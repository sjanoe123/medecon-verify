# FY2027 ICD-10-CM Release Files — Acquisition Manifest

Source page: https://www.cms.gov/medicare/coding-billing/icd-10-codes
(fetched via `curl -L`, HTTP 200; page title confirms current CMS ICD-10 landing
page, section listing FY 2027 files alongside FY 2026 / April 1, 2026 updates)

Retrieval timestamp (UTC, `date -u`): **2026-07-19 12:28:22 UTC**
(directories created / download run: 2026-07-19 07:25:19 local / 12:25:19 UTC)

Downloader: `curl -s -L -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)
AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" -o <file>
"https://www.cms.gov/files/zip/<file>"`

All six files returned HTTP 200, are valid ZIP archives per `file(1)`, and
unzip cleanly. Inner file names confirm FY2027 vintage (e.g.
`icd10cm_order_2027.txt`, `icd10cm_codes_2027.txt`,
`...FY2027-October 1 2026 - FINAL-.csv`, `POAexemptCodesFY27.txt`) — no
FY2026 substitution.

| File | Source URL | SHA-256 | Size (bytes) |
|---|---|---|---|
| 2027-code-descriptions-tabular-order.zip | https://www.cms.gov/files/zip/2027-code-descriptions-tabular-order.zip | `91c6c9d1117764ce72375a0f3a5493b1725dafbc1ab283b55799076c9e194965` | 2,208,022 |
| 2027-code-tables-tabular-index.zip | https://www.cms.gov/files/zip/2027-code-tables-tabular-index.zip | `37baa476323714be16f95c9b2c96812bf6f3623d9f15188866e584dc529b0298` | 22,059,093 |
| 2027-conversion-table.zip | https://www.cms.gov/files/zip/2027-conversion-table.zip | `9478b8f95e137177e18be3df906c35566674b16313b12740d9e8e3778cb0e67a` | 175,330 |
| 2027-icd-10-addendum.zip | https://www.cms.gov/files/zip/2027-icd-10-addendum.zip | `16986a34d1458e549217229686f1a487cf6419ee6acb1043fbc770f8fabdd026` | 671,906 |
| 2027-poa-exempt-codes.zip | https://www.cms.gov/files/zip/2027-poa-exempt-codes.zip | `03f55091045024d07c99860470b9069ef2db4c19242d4c2ba0e9f30905bea904` | 1,654,940 |
| 2027-version-update-summary.zip | https://www.cms.gov/files/zip/2027-version-update-summary.zip | `c26b19c9246f24ecaccf056eb21640bf1cdded07527910e24bb1d006494f2860` | 211,531 |

Checksums generated with: `shasum -a 256 *.zip` (run from this directory,
2026-07-19 12:28:22 UTC).

## Unzip verification + key inner files

Extraction command used for each: `unzip -o -q <file>.zip -d <tmpdir>`.
All six extracted with exit status 0 (no CRC/corruption errors).

**2027-code-descriptions-tabular-order.zip** → `Code Descriptions/`
- `icd10cm_order_2027.txt` (14,724,229 bytes) — the FY2027 ICD-10-CM order file
- `icd10cm_codes_2027.txt` (6,425,201 bytes) — FY2027 code descriptions (short titles)
- `icd10cm_order_addenda_2027.txt`, `icd10cm_codes_addenda_2027.txt` — mid-year addenda variants
- `icd10OrderFiles.pdf`, `icd10cmCodesFile.pdf` — documentation PDFs

**2027-code-tables-tabular-index.zip** → `Table and Index/`
- `icd10cm_tabular_2027.xml` / `.pdf` — full tabular list
- `icd10cm_index_2027.xml` / `.pdf` — alphabetic index
- `icd10cm_drug_2027.xml` / `.pdf` — Table of Drugs and Chemicals
- `icd10cm_neoplasm_2027.xml` / `.pdf` — Table of Neoplasms
- `icd10cm_eindex_2027.xml` / `.pdf` — External Cause of Injuries Index
- `icd10cm_tabular.xsd`, `icd10cm_index.xsd`, `icd10cm_drug_neoplasm.xsd` — XML schemas

**2027-conversion-table.zip**
- `ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - FINAL-.xlsx`
- `508-VERSION-ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - FINAL-.csv`
- Filenames self-identify as FY2027, effective October 1, 2026, FINAL (not proposed).

**2027-icd-10-addendum.zip** → `Addendum/`
- `icd10cm_tabular_addenda_2027.pdf`, `icd10cm_index_addenda_2027.pdf`,
  `icd10cm_neoplasm_addenda_2027.pdf`, `icd10cm_drug_addenda_2027.pdf`,
  `icd10cm_eindex_addenda_2027.pdf`

**2027-poa-exempt-codes.zip**
- `POAexemptCodesFY27.txt` / `.xlsx` (4,077,325 / 1,302,538 bytes) — full FY2027 POA-exempt list
- `POAexemptAddFY27.txt` / `.xlsx`, `POAexemptDeleteCodesFY27.txt` / `.xlsx`,
  `POAexemptReviseCodesFY27.txt` / `.xlsx` — year-over-year deltas
- `ReadMePOAexemptFY27.txt`

**2027-version-update-summary.zip**
- `pcs_update_summary_2027.pdf` — note: this file is ICD-10-**PCS** (procedure)
  update summary, not CM. Downloaded for completeness since it was listed
  alongside the CM zips on the same page section; CM-specific version-update
  summary content is otherwise embedded in the order/codes files above. Not
  required for ICD-10-CM diagnosis code-set stamping.

## Not downloaded (out of scope for ICD-10-CM)

The CMS ICD-10 page also lists FY2027 ICD-10-**PCS** (procedure code) files
(`2027-icd-10-pcs-addendum.zip`, `2027-icd-10-pcs-code-tables-index.zip`,
`2027-icd-10-pcs-codes-file.zip`, `2027-icd-10-pcs-conversion-table.zip`,
`2027-icd-10-pcs-order-file-long-abbreviated-titles.zip`,
`2027-official-icd-10-pcs-coding-guidelines.pdf`). These were intentionally
skipped — this manifest covers ICD-10-**CM** (diagnosis codes) per the task
scope. No FY2027 ICD-10-CM-specific "Coding Guidelines" PDF was found posted
yet (latest posted CM guidelines PDF on the page is still `fy-2026-icd-10-cm-coding-guidelines.pdf`);
this is not required by the task and is not treated as a blocker.

## Status: not blocked

All requested FY2027 ICD-10-CM code-descriptions / order-file / addenda /
conversion-table / POA-exempt files were located, downloaded, checksummed,
and verified to unzip with FY2027-vintage inner filenames.
