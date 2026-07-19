# FY2027 IPPS Final Rule / MS-DRG v44 Documentation — Acquisition Manifest

## Status: BLOCKED

**What is missing:** The **FY 2027 IPPS Final Rule** has not been published by
CMS as of this check. Only the **FY 2027 IPPS Proposed Rule** exists. MS-DRG
v44 is currently posted only as a **"Test GROUPER"** reflecting *proposed*
logic — not the final, effective grouper. Per the task's anti-fabrication
instruction, FY2026 final-rule files were NOT substituted, and the FY2027
proposed-rule / test-grouper files were NOT downloaded into this directory
labeled as final, since doing so would risk exactly the kind of vintage
mismatch this repo's `medecon_verify.codeset` stamping exists to prevent
(proposed MS-DRG logic can and does change before the final rule — DRG
reclassifications, weight recalibrations, and relative-weight changes are
common between proposed and final).

## Pages checked (retrieval timestamp UTC, `date -u`: **2026-07-19 12:29:47 UTC**)

1. **Expected final-rule URL (does not exist):**
   `https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2027-ipps-final-rule-home-page`
   → `curl -o /dev/null -w "%{http_code}"` → **HTTP 404**
   (contrast: the equivalent FY2026 URL,
   `.../fy-2026-ipps-final-rule-home-page`, resolves fine and is linked from
   the main Acute Inpatient PPS page — confirming the naming convention is
   right and the FY2027 final-rule page simply does not exist yet, rather
   than having moved).

2. **FY 2027 IPPS Proposed Rule Home Page (exists, is the current authoritative page):**
   `https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2027-ipps-proposed-rule-home-page`
   → HTTP 200. Page `<title>` = "FY 2027 IPPS Proposed Rule Home Page | CMS".
   Body text explicitly self-describes as "FY 2027 Hospital Inpatient PPS
   proposed rule" — no final-rule language anywhere on the page.

3. **Main Acute Inpatient PPS index page** (to confirm no final-rule link has
   been added elsewhere and no page was missed):
   `https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps`
   → HTTP 200. Only FY2027 link present is `fy-2027-ipps-proposed-rule-home-page`
   (plus the FY2027 proposed-rule newsroom fact sheet). No
   `fy-2027-ipps-final-rule-home-page` link exists on this page. FY2026 has
   both `fy-2026-ipps-proposed-rule-home-page` and
   `fy-2026-ipps-final-rule-home-page` listed, for comparison.

4. **MS-DRG Classifications and Software page** (where CMS posts the
   GROUPER/Definitions Manual/Table 5 documentation directly):
   `https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software`
   → HTTP 200. Page explicitly states: *"CMS is providing a test version of
   the ICD-10 MS-DRG GROUPER Software, Version 44, so that the public can
   better analyze and understand the impact of the proposals included in
   the FY 2027 IPPS/LTCH PPS proposed rule[.] This test software reflect[s]
   the proposed GROUPER logic for FY 2027."* — i.e., v44 as currently posted
   is explicitly labeled a proposed/test artifact, not the final grouper.

5. **Federal Register** confirms the proposed rule's publication and
   timeline: "Medicare Program; Hospital Inpatient Prospective Payment
   Systems for Acute Care Hospitals (IPPS) ... FY 2027 Rates..." published
   2026-04-14 at
   `https://www.federalregister.gov/documents/2026/04/14/2026-07203/...`
   (docket CMS-1849-P). Comment period closed 2026-06-09 per AHA coverage
   (`https://www.aha.org/2026-06-09-aha-comments-cms-fy-2027-inpatient-proposed-payment-rule`).
   No CMS-1849-**F** (final rule) document was found via web search as of
   this check.

## What exists but was NOT downloaded (and why)

- `CMS-1849-P` proposed-rule Table 5 (Proposed MS-DRGs, relative weights,
  mean LOS), Table 6P.1a-6P.1b, the draft ICD-10 MS-DRG Definitions Manual
  v44, the draft Definition of Medicare Code Edits v44, and the v44 Test
  GROUPER software package are all posted on the FY 2027 IPPS Proposed Rule
  Home Page and the MS-DRG Classifications and Software page. These are
  real, downloadable files — but they are proposed/test-vintage, not final,
  and the task asked specifically for what the **Final Rule** page documents
  MS-DRG v44 with. Downloading and filing the proposed-rule artifacts in
  this `ipps-fy2027/` directory without a final-rule counterpart risks a
  future reader treating test-grouper weights/logic as final — silently
  substituting proposed data for final data, which this task's instructions
  explicitly prohibit for FY2026-vs-FY2027 and which applies with equal
  force to proposed-vs-final.

## Recommended next step

Re-run this acquisition once CMS publishes the FY 2027 IPPS Final Rule
(historically published in late July/early August for an October 1 effective
date — e.g., FY2026's final rule followed the same pattern). Check
`https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2027-ipps-final-rule-home-page`
and the MS-DRG Classifications and Software page for language confirming
"final" (not "test"/"proposed") Version 44 GROUPER logic before downloading.

No files were placed in this directory. No sha256/URL/timestamp table is
included because no in-scope file was retrieved.

## Re-check addendum — 2026-07-19 12:33 UTC

Re-ran the same checks (single re-check per task scope, not a repeat cadence):

- `curl -o /dev/null -w "%{http_code}"` on
  `.../acute-inpatient-pps/fy-2027-ipps-final-rule-home-page` → still **HTTP 404**.
- Main Acute Inpatient PPS index page (`.../acute-inpatient-pps`) → only
  `fy-2027-ipps-proposed-rule-home-page` appears among FY2027 links; no
  `fy-2027-ipps-final-rule-home-page` link present.
- MS-DRG Classifications and Software page → still contains the "test
  version ... Version 44 ... proposed GROUPER logic" language quoted above;
  no "final" GROUPER language for FY2027 has appeared.
- Federal Register API: search for docket `CMS-1849-F` → 0 results. No
  FY2027 IPPS final rule document exists yet (compare: the FY2026 final rule,
  `2025-14681`, published 2025-08-04 — consistent with CMS's typical
  late-July/early-August final-rule timing, so a 2026-07-19 non-publication
  is not anomalous).

**Conclusion: still unposted.** No change from the original finding. This is
an **approved deferral, not a task blocker** — the acquisition-verification
task this addendum belongs to treats overall status as `ok` with this item
carried as a noted blocker/deferral, per its explicit instructions. Re-run
once CMS posts the FY2027 IPPS Final Rule (watch the two URLs above).
