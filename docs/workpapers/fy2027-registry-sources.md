# FY2027 ICD-10-CM registry — extraction sources & provenance

**Task:** Plan Phase 1a, Task 1.1 — add the FY2027 ICD-10-CM fiscal-year vintage
to the bundled reference registry
`src/medecon_verify/data/registries/icd10cm_fy.json`, and evaluate MS-DRG v44.

**Extractor:** `tools/extract_icd10cm_fy2027.py` (deterministic, stdlib-only,
re-runnable, fail-closed). Every value below is derived by that script from a
downloaded, checksum-verified CMS file — none is hand-typed. Re-run to reproduce:

```
python3 tools/extract_icd10cm_fy2027.py --check   # derive + print, no write
python3 tools/extract_icd10cm_fy2027.py           # derive + write registry
```

Derivation date: 2026-07-19. Extractor exit status 0; all cross-checks passed.

---

## 1. ICD-10-CM FY2027 — SOURCED, verified 2026-07-19

### Source files (all six manifested ZIPs verified before any read)

Landing page: https://www.cms.gov/medicare/coding-billing/icd-10-codes
Acquisition manifest (upstream): `~/.gstack/projects/medecon-stack/cms-sources/icd10cm-fy2027/MANIFEST.md`
(files fetched 2026-07-19 12:25–12:28 UTC; shasums re-verified 2026-07-19).

| File | Source URL | SHA-256 | Read for derivation |
|---|---|---|---|
| 2027-code-descriptions-tabular-order.zip | https://www.cms.gov/files/zip/2027-code-descriptions-tabular-order.zip | `91c6c9d1117764ce72375a0f3a5493b1725dafbc1ab283b55799076c9e194965` | **yes** (order + codes files) |
| 2027-conversion-table.zip | https://www.cms.gov/files/zip/2027-conversion-table.zip | `9478b8f95e137177e18be3df906c35566674b16313b12740d9e8e3778cb0e67a` | **yes** (effective-date cross-check) |
| 2027-code-tables-tabular-index.zip | https://www.cms.gov/files/zip/2027-code-tables-tabular-index.zip | `37baa476323714be16f95c9b2c96812bf6f3623d9f15188866e584dc529b0298` | integrity-verified only |
| 2027-icd-10-addendum.zip | https://www.cms.gov/files/zip/2027-icd-10-addendum.zip | `16986a34d1458e549217229686f1a487cf6419ee6acb1043fbc770f8fabdd026` | integrity-verified only |
| 2027-poa-exempt-codes.zip | https://www.cms.gov/files/zip/2027-poa-exempt-codes.zip | `03f55091045024d07c99860470b9069ef2db4c19242d4c2ba0e9f30905bea904` | integrity-verified only |
| 2027-version-update-summary.zip | https://www.cms.gov/files/zip/2027-version-update-summary.zip | `c26b19c9246f24ecaccf056eb21640bf1cdded07527910e24bb1d006494f2860` | integrity-verified only (ICD-10-**PCS** summary, per manifest caveat) |

The extractor recomputes `sha256` for each file with `hashlib.sha256` and refuses
to derive anything unless every file matches the value above (equivalent to
`shasum -a 256 <file>`). A missing file or mismatch → `ExtractionError`, nothing
written.

### Derived registry row (what landed in `icd10cm_fy.json`)

```json
"FY2027": {"effective": "2026-10-01", "obsolete": "2027-09-30"}
```

Derivation and cross-checks (all inside `derive_fy2027`):

- **Fiscal year = 2027** — parsed from the inner member names, which must agree:
  `Code Descriptions/icd10cm_order_2027.txt`,
  `Code Descriptions/icd10cm_codes_2027.txt`, and the conversion-table member
  `ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - FINAL-.xlsx`. Disagreement → raise.
- **effective = 2026-10-01** — the statutory Oct-01 boundary date for FY2027,
  `date(FY-1, 10, 1)`, **cross-checked** against the effective date parsed out of
  the conversion-table filename (`...FY2027-October 1 2026 - FINAL...` →
  `2026-10-01`). Mismatch → raise.
- **obsolete = 2027-09-30** — `date(FY, 9, 30)`, the day before FY2028 begins.
- **FINAL, not proposed** — the conversion-table member name carries the literal
  ` - FINAL-`; the extractor refuses a `PROPOSED` conversion table.

### Derived code counts (recorded here; not stored in the registry schema)

The registry schema stores only `effective`/`obsolete` per row (uniform with the
existing FY2022–FY2026 rows). The code counts below are derived provenance,
recorded in this workpaper rather than the JSON:

| Metric | Value | Source of truth |
|---|---|---|
| Billable/valid ICD-10-CM codes (order-file col-15 flag = `1`) | **74,879** | `icd10cm_order_2027.txt` |
| Header / non-billable category rows (flag = `0`) | **23,524** | `icd10cm_order_2027.txt` |
| Total order-file entries | **98,403** | `icd10cm_order_2027.txt` |
| `icd10cm_codes_2027.txt` line count (billable codes only) | **74,879** | `icd10cm_codes_2027.txt` |

Cross-check enforced by the extractor: billable count (74,879) **equals** the
codes-file line count (74,879), and 74,879 + 23,524 = 98,403. Any disagreement →
raise (the script will not emit a count its own sources contradict).

Manual reproduction of the counts:

```
unzip -p 2027-code-descriptions-tabular-order.zip \
  "Code Descriptions/icd10cm_order_2027.txt" | awk '{print substr($0,15,1)}' \
  | sort | uniq -c
#   23524 0
#   74879 1
unzip -p 2027-code-descriptions-tabular-order.zip \
  "Code Descriptions/icd10cm_codes_2027.txt" | grep -c .
#   74879
```

---

## 2. MS-DRG v44 — DEFERRED (not added)

**No v44 row was added to `ms_drg.json`.** As of 2026-07-19 the **FY2027 IPPS
Final Rule is unpublished**; MS-DRG v44 exists only as a proposed-rule **"Test
GROUPER"** reflecting *proposed* logic. Proposed-to-final DRG reclassifications
and relative-weight recalibrations are common, so ingesting v44 from proposed
content would risk stamping non-final grouper logic as final — the exact silent
vintage substitution this repo exists to prevent.

Evidence (from `~/.gstack/projects/medecon-stack/cms-sources/ipps-fy2027/MANIFEST.md`,
checks at 2026-07-19 12:29 and 12:33 UTC):

- Expected final-rule page →
  `.../acute-inpatient-pps/fy-2027-ipps-final-rule-home-page` → **HTTP 404**
  (the equivalent FY2026 page resolves, confirming the naming convention).
- Only `fy-2027-ipps-proposed-rule-home-page` is linked from the Acute Inpatient
  PPS index; the MS-DRG Classifications & Software page explicitly labels
  Version 44 a *"test version ... reflect[ing] the proposed GROUPER logic for
  FY 2027."*
- Federal Register: proposed rule `CMS-1849-P` published 2026-04-14; no
  `CMS-1849-F` (final) document exists yet. (FY2026's final rule, `2025-14681`,
  published 2025-08-04 — CMS's typical late-July/early-August timing, so a
  2026-07-19 non-publication is expected, not anomalous.)

### Deferral is wired, not forgotten

- `tools/extract_icd10cm_fy2027.py` carries `extract_ms_drg_v44(final_rule_dir)`,
  a ready-to-implement stub that raises `NotImplementedError` with the
  re-check URLs until the FINAL rule posts, plus the `MS_DRG_V44_DEFERRAL` note.
- `tests/test_codeset_version.py::TestFy2027::test_ms_drg_v44_final_rule` is a
  **clearly-marked skipped test** (`@pytest.mark.skip`) whose reason names the
  deferral and the un-skip condition.
- `tests/test_codeset_version.py::TestFy2027::test_ms_drg_v44_deferred_stamps_unknown_for_fy2027`
  is a **live** test pinning the correct deferral consequence: a FY2027 date
  stamps `icd10cm_fy=FY2027` but `ms_drg=UNKNOWN` (diagnosis codes exist; no
  final grouper does).

### CMS re-check URLs (run the acquisition again once these show FINAL/Version 44)

- https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2027-ipps-final-rule-home-page
- https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software

When published, implement `extract_ms_drg_v44` to derive the row from the final
Definitions Manual / Table 5, add
`"v44": {"fy": "FY2027", "effective": "2026-10-01"}` to `ms_drg.json`, un-skip the
test above, and flip `test_ms_drg_v44_deferred_stamps_unknown_for_fy2027` to
expect `v44`.

---

## 3. Test impact

`PYTHONPATH=src python3 -m pytest tests/` → **391 passed, 2 skipped, 1 deselected**
(the deselected item is a `slow`/`optional` network-gated test excluded by the
default `-m 'not slow and not optional'`; the 2 skips are the v44 deferral test
and one pre-existing skip).

Test expectations updated (each because FY2027 is now a *covered* vintage, so
dates previously used as "out-of-registry" sentinels had to move to the next
uncovered FY — FY2028):

| Test | Old sentinel | New sentinel | Reason |
|---|---|---|---|
| `test_data_discovery.test_bundled_icd10_values_are_faithful` | set of 5 FYs | +FY2027 (+value assert) | FY2027 now bundled |
| `test_packaging_strict.test_icd10cm_fy_faithful` | 5 rows | +FY2027 row | FY2027 now bundled |
| `test_packaging_strict.test_no_data_package_default_yields_unknown` | asof 2027-05-01 | asof 2028-05-01 | 2027-05-01 now → FY2027 |
| `test_packaging_strict.test_no_data_package_strict_raises_loudly` | asof 2027-05-01 | asof 2028-05-01 | keep out-of-registry intent |
| `test_codeset_strict` `_OUT_OF_REGISTRY` (+2 asserts) | 2027-05-01 | 2028-05-01 | 2027-05-01 now covered; strict must name the first truly-uncovered field |
| `test_fixture_corpus.test_stamp_unknown_vintage_past_registry` | asof 2026-11-01 | asof 2027-11-01 | 2026-11-01 now → FY2027 |
| `test_fixture_corpus.test_end_to_end_pipeline_over_corpus` | asof 2026-11-01 | asof 2027-11-01 | same |

New coverage added in `test_codeset_version.py`: `TestFy2027` (registry bounds,
`stamp()` recognition incl. the Oct-01 2026 boundary, `detect_icd10_fy`, strict
pass, the v44 deferral consequence + skipped v44 test) and
`TestFy2026Fy2027BoundaryWarning` (the FY2026→FY2027 span still warns).
