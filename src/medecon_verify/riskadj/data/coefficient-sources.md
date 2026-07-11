# Coefficient sources

## CMS-HCC V28 (PY2026) — SOURCED, verified 2026-06-12

The `CMS_HCC_V28` seed in `scripts/risk_adjustment.py` and the two committed
data files in this directory carry **official CMS values**, not illustrations:

| File | Contents | Source |
|---|---|---|
| `cms_hcc_v28_py2026_coefficients.csv` | Full multi-segment V28 relative-factor table: 1,237 coefficients across segments CNA, CPA, CFA, CND, CPD, CFD, INS (continuing enrollees) and NE, SNPNE (new enrollees). Columns: `segment,factor_type,factor_key,coefficient`. | `V28_CE_Relative_Factors.csv` + `V28_NE_Relative_Factors.csv` from `CMS_HCC_v28_2026_T_package_v3.zip` inside [2026-midyear-final-model-software-python.zip](https://www.cms.gov/files/zip/2026-midyear-final-model-software-python.zip) |
| `cms_hcc_v28_icd10_map.csv` | Full FY2025/FY2026 ICD-10-CM -> V28 payment-HCC mapping: 8,933 rows, 8,019 distinct ICD-10-CM codes, all 115 payment HCCs. Columns: `icd10_code,v28_hcc` (ICD codes undotted, as published by CMS). | `ICD10_CC_mappings_CMS_HCC_2026_v28.csv`, same package |

Landing page: [2026 Model Software/ICD-10 Mappings](https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/2026-model-software-icd-10-mappings)
(the CMS "Risk Adjustment" / risk-adjustors page).

Cross-validation performed at extraction (2026-06-12):

- Every one of the 1,237 cells matches the SAS-package coefficient file
  `C2824T2N.csv` (from `CMS-HCC software V2826.115.T1.zip` inside
  [2026-midyear-final-model-software.zip](https://www.cms.gov/files/zip/2026-midyear-final-model-software.zip))
  exactly; the single naming variant is `ORIGDIS` (Python package) =
  `INS_ORIGDS` (SAS package), both 0.000 in the institutional segment.
- Spot-checked against the printed table: CY2024 Rate Announcement
  (Mar 31, 2023), Attachment VIII, **Table VIII-1 "2024 CMS-HCC Model Relative
  Factors for Continuing Enrollees"**,
  [2024-announcement-pdf.pdf](https://www.cms.gov/files/document/2024-announcement-pdf.pdf).
- PY2026 applicability: the CY2026 Rate Announcement (Apr 7, 2025),
  [2026-announcement.pdf](https://www.cms.gov/files/document/2026-announcement.pdf),
  completes the three-year phase-in — 100% of CY2026 risk scores use the 2024
  CMS-HCC model (V28).

Structural facts (CY2024 Rate Announcement Table III-3; CY2024 Advance Notice
Table II-4):

- 115 payment HCCs (266 total CCs; 151 non-payment).
- Organized into 26 disease groups in Table II-4 (the Complications group has
  0 payment HCCs in V28, so 25 groups are non-empty).
- 7,770 FY22/FY23 ICD-10-CM codes mapped to a payment HCC at publication; the
  FY2025/FY2026 mapping shipped with the PY2026 software maps 8,019 codes
  (annual ICD-10-CM updates).
- Coefficients are constrained within disease families: e.g. HCC36/37/38
  (diabetes) all carry 0.166 in the CNA segment; HCC35 Pancreas Transplant
  Status (0.949 CNA) is the exception at the top of the diabetes hierarchy.

The in-code `CMS_HCC_V28` seed is the **Community Non-Dual Aged (CNA)**
segment only.

## HHS-HCC 2026 benefit year — SOURCED, verified 2026-06-12

The `HHS_HCC_2026` seed in `scripts/risk_adjustment.py` and two committed data
files carry **official CMS/CCIIO values**, not illustrations:

| File | Contents | Source |
|---|---|---|
| `hhs_hcc_2026_coefficients.csv` | Complete 2026 benefit year FINAL coefficient set: 368 factors x 5 metal levels (1,840 values). Adult model (age-sex, HCCs, ACF, interacted-HCC-counts, enrollment-duration, RXCs, RXC-HCC interactions), child model (age-sex, HCCs, interacted-HCC-counts, ACF), infant model (maturity x severity groups + age-sex). Columns: `model,metal_level,factor_type,factor_key,factor_label,coefficient`. | [2026 Benefit Year Final HHS Risk Adjustment Model Coefficients](https://www.cms.gov/files/document/2026-benefit-year-final-hhs-risk-adjustment-model-coefficients2025-01-13.pdf) (CMS/CCIIO, Jan 13, 2025), Tables 1, 2, 4 |
| `hhs_hcc_v08_2026_icd10_map.csv` | Full V08 ICD-10 -> HHS-CC crosswalk: 11,513 ICD-10 codes with FY2025/FY2026 validity flags, CC age/sex splits, and up to three CC assignments (codes undotted, as published). | DIY Table 3, [cy2025-diy-tables-03-30-2026.xlsx](https://www.cms.gov/files/document/cy2025-diy-tables-03-30-2026.xlsx) (final, Mar 30, 2026; "Includes Fiscal Year (FY) 2025 and FY 2026 list of ICD-10 codes") |

Notes from extraction (2026-06-12):

- Coefficients were published outside the Final 2026 Payment Notice per
  45 CFR 153.320(b)(1)(i); the recalibration blends models separately solved
  on 2020, 2021, and 2022 enrollee-level EDGE data.
- The published numbers already reflect (as published, not recomputed): the
  partially-phased-out Hepatitis C drug market pricing adjustment, the new
  HIV PrEP affiliated cost factor (ACF01), and high-cost risk pool truncation
  (60% of costs above $1M removed). RXC03 / RXC04 are constrained to average
  plan liability and their RXC-HCC interactions constrained to zero (PDF
  footnote b) — e.g. `RXC03xHCC142` = 0.000 in all five metal levels.
- Classification is **V08** ("HHS-Developed Risk Adjustment Model Algorithm
  DIY Software" instructions, final Mar 30, 2026,
  [cy2025-diy-instructions-03-30-2026.pdf](https://www.cms.gov/files/document/cy2025-diy-instructions-03-30-2026.pdf)).
  A 2026-benefit-year DIY package had not been published as of 2026-06-12;
  Table 3 of the final CY2025 DIY tables is the current official V08 crosswalk
  and explicitly covers FY2026 ICD-10 codes (use the `valid_fy2026` column).
- Child-model rows in the PDF print labels only; HCC numbers in the CSV were
  joined from the adult table's printed codes and DIY Table 4 (V08 HCC
  hierarchies) by label.
- Extraction was verified by multiset comparison: every coefficient quintuple
  in the CSV matches the `pdftotext -layout` dump of the PDF (adult 194,
  child 147, infant 27 factors).
- The in-code `HHS_HCC_2026.hcc_coefs` are the **silver adult** model;
  `demographics` carries the published adult age-sex factors for the metal
  levels in the seed structure (platinum 21-24/25-29, silver 30-34).

## Still illustrative (override before production use)

- `CMS_HCC_V24` — shape-correct seed, NOT the published v24 table.
- `CDPS_2025` — illustrative; CDPS is licensed by UCSD.
