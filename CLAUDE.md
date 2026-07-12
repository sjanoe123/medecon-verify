# CLAUDE.md — agent operating instructions for medecon-verify

This repo is a **commercial product**: the correctness-and-compliance layer for
healthcare AI, extracted from the medecon-stack plugin (the sibling repo at
`~/medecon-stack`, github.com/sjanoe123/medecon-stack). Treat every change as a
change customers will `pip install`.

## What this package promises (do not break these)

1. **Strict SemVer from 1.0.0.** The public signatures in the README/plan are a
   contract. Additive = minor. Behavior or signature change = major, and majors
   need explicit owner sign-off. When in doubt, it's a major.
2. **stdlib-only core.** `dependencies = []` stays empty. `click` lives only
   under the `[cli]` extra. A new third-party import in a core module is a
   product-posture regression, not a convenience.
3. **Fail closed.** PHI scan errors redact (`[SCAN_FAILED—CONTENT_SUPPRESSED]`);
   strict mode raises `CodesetVersionError` on unknown vintages; never soften
   either to make a test pass.
4. **No invented data.** Registry JSONs (`src/medecon_verify/data/registries/`)
   and HCC CSVs (`src/medecon_verify/riskadj/data/`) carry official CMS values
   with provenance (`coefficient-sources.md`). Never hand-edit a coefficient;
   data flows in through medecon-stack's SeedDataLoop (sourced + adversarially
   verified) and is copied here as a release.
5. **No PHI, ever.** All fixtures are synthetic and labeled as such. Never pull
   real claims data into this repo.

## Known consumers (check before changing anything they touch)

- **medecon-stack plugin** — imports every module; re-runs the traveling tests
  as consumer contract tests; pins this repo in its `requirements.txt` (git tag
  `vX.Y.Z` until PyPI publish). Its bridge playbook:
  `~/medecon-stack/docs/verify-bridge.md`.
- **healthnews pipeline** — indirectly, through medecon-stack's
  `utils/cli.py` (`peer-review|stamp|scan`), daily.
- **Future paying customers** — the reason strictness above is not negotiable.

## Release flow (every change ships this way)

1. `PYTHONPATH=src python3 -m pytest tests/ -q` — zero failures.
2. Wheel gate: `python3 -m build --wheel`; install into a fresh venv; re-run the
   full suite from the installed wheel (empty cwd, PYTHONPATH unset).
3. Bump `version` in `pyproject.toml` per SemVer; update README if API changed.
4. Commit, `git tag vX.Y.Z`, `git push --tags`.
5. Bridge to the plugin: bump the pin in medecon-stack's `requirements.txt`,
   reinstall in its envs, run its full suite (~2,776 tests), bump its
   `plugin.json` + CHANGELOG. A traveling-test failure over there is the
   contract gate working — reconcile deliberately, never by weakening a test.

## Structure

- `src/medecon_verify/` — core modules (adjudication, codeset, privacy, phi,
  dateparse, glossary), `certify/runner.py` (eval checkers),
  `riskadj/engine.py` + bundled data, `_data_discovery.py` (installed
  `medecon-verify-data` entry-point → bundled tier → strict error precedence).
- `tests/` — traveling tests from medecon-stack + the self-contained fixture
  corpus (`tests/fixtures/`, all synthetic) + packaging/strict-mode tests.
- Review history: three adversarial Codex passes at extraction (findings
  X-1/X-2/Y-1, all fixed); the extraction plan with full provenance is
  `~/medecon-stack/docs/planning/medecon-verify-product-plan.md`.

## Explicit non-goals for v1 (from the plan — do not build these here)

No hosted API/SaaS, no UI/dashboard, no PHI ingestion, no clinical decision
support, no CPT/AMA-licensed content, no relicensing of MIT code.
