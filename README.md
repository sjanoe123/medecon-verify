# medecon-verify

The correctness-and-compliance layer for healthcare AI agents, extracted from the
MIT-licensed [medecon-stack](https://github.com/sjanoe123/medecon-stack) plugin.

A **no-third-party-dependency core library** that drops in between any LLM and its
output:

- `medecon_verify.adjudication` — final-action claim dedup, O(N) reversal-pair
  detection, FY-boundary blended-total warnings.
- `medecon_verify.codeset` — ICD-10-CM / HCPCS / MS-DRG / CMS-HCC vintage stamping.
- `medecon_verify.privacy` — n≤10 cell suppression, age-90+ aggregation, 42 CFR
  Part 2 redisclosure flag.
- `medecon_verify.phi` — heuristic, pattern-based Safe Harbor identifier redaction.
  **Scope & limitations:** this is deterministic, pattern-based identifier
  redaction — fail-closed and reproducible. It is *not* a substitute for Expert
  Determination, and bare names without a title prefix in free prose are out of
  scope for the regex tier.
- `medecon_verify.dateparse` — tolerant date parsing shared by the above.
- `medecon_verify.glossary` — healthcare-economics term glossary.

## Install

```bash
pip install medecon-verify          # core library, stdlib-only
pip install "medecon-verify[cli]"   # adds the `medecon-verify` command (click)
```

The console script installs unconditionally; without the `[cli]` extra it exits
with a clear message telling you to install it.

## License

MIT.
