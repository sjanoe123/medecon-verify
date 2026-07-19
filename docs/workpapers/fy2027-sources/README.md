# FY2027 registry — committed provenance chain

This directory makes the FY2027 ICD-10-CM extraction **auditable from artifacts
committed to this repo**, not just from an external `~/.gstack` path. It answers
the codex GPT-5.6 finding that the workpaper asserted a successful, checksum-
verified CMS extraction that nothing in the diff could substantiate.

## The chain (each link is committed here or in the repo)

1. **Cited CMS artifacts → SHA-256.** `icd10cm-fy2027-acquisition-manifest.md`
   records, for every downloaded FY2027 ICD-10-CM release ZIP: the exact CMS
   source URL, the `shasum -a 256` hash, the byte size, and the retrieval
   timestamp. This is a verbatim copy of the acquisition manifest that sits
   beside the downloaded ZIPs under
   `~/.gstack/projects/medecon-stack/cms-sources/icd10cm-fy2027/`.

2. **SHA-256 → extractor.** `tools/extract_icd10cm_fy2027.py`'s `SOURCES` dict
   carries those same SHA-256 literals. `verify_sources()` recomputes the hash
   of each local file and refuses to derive anything unless it matches. The
   literals in the extractor and the hashes in the manifest (link 1) are the
   same strings — grep either to confirm.

3. **Extractor → derived values (captured run).** `extract-run-log.json` is the
   captured stdout of `python3 tools/extract_icd10cm_fy2027.py --check`, run over
   the real downloaded sources. It records the recomputed `source_checksums`
   (which match link 1), the derived `effective`/`obsolete` row that landed in
   `src/medecon_verify/data/registries/icd10cm_fy.json`, and the derived
   provenance `counts` (billable / header / total / codes-file lines). Re-running
   `--check` reproduces this file byte-for-byte from the same sources.

4. **Derived values → committed registry + tests, reproduced from ground truth.**
   `tests/test_extract_icd10cm_fy2027.py` drives the extractor's real parsing and
   cross-check logic against synthetic fixtures with independently-known ground
   truth (not the pre-baked JSON). Its `optional`-marked
   `test_real_sources_reproduce_committed_registry_and_workpaper_counts` re-runs
   the real extractor over the real sources (when present) and asserts the
   committed registry row and the workpaper counts equal what the files derive —
   closing the loop from CMS artifact back to committed literal. Run it with:

   ```
   PYTHONPATH=src python3 -m pytest tests/test_extract_icd10cm_fy2027.py -m optional
   ```

## MS-DRG v44 deferral evidence

`ipps-fy2027-deferral-manifest.md` is the committed copy of the IPPS FY2027
acquisition check that documents why MS-DRG v44 was **not** added: the FY2027
IPPS Final Rule was unpublished (final-rule page HTTP 404; only a proposed-rule
"Test GROUPER" v44 existed) as of 2026-07-19. See section 2 of
`../fy2027-registry-sources.md`.

## Why the CMS ZIPs themselves are not committed

The six release ZIPs total ~27 MB of binary reference data and are not source for
this stdlib-only library; committing them would bloat the repo. The manifest
(link 1) + the extractor's checksum literals (link 2) + the captured run log
(link 3) + the reproduce-from-real-sources test (link 4) make the extraction
auditable without vendoring the binaries. Anyone can re-download from the URLs in
link 1, confirm the hashes, and re-run the extractor to reproduce every value.
