# SYNTHETIC TEST FIXTURES — medecon-verify task 0.6

**Every file in this directory tree is fully fabricated. Nothing here is derived
from, or joined against, any real patient, claim, member, provider, or PHI.**

These fixtures exist so the four guardrail gate modules
(`adjudication` → `codeset` → `privacy` → `phi`) can be exercised end-to-end
without any coupling to the medecon-stack skills tree or to `.medecon/org/` data.

Deliberately-synthetic conventions used throughout:

- **Names** are all-caps `TESTPATIENT <GREEK>` tokens — obviously fake, never a
  real person's name.
- **MBIs** match the CMS Medicare Beneficiary Identifier charset (so the PHI
  scanner's `_MBI` pattern recognizes them and redacts) but are randomly
  assembled placeholders flagged synthetic here — they are not issued MBIs.
- **Service dates** are chosen to straddle the **2026-10-01 ICD-10-CM FY2026→
  FY2027 boundary** so the vintage-mismatch guardrail has something to bite on.
- **PHI-shaped strings** (SSN `123-45-6789`, phone `415-555-0142`, email/URL)
  are canonical test literals, not live identifiers.

Sub-directories:

- `claims/` — synthetic claims CSVs (reversal pairs, FY-boundary span, versioned
  supersessions, age 90+, synthetic MBIs/names). First line is a `#` comment
  declaring the file synthetic; loaders skip `#` lines.
- `rendered/` — rendered-output fixtures: a deliverable JSON (small cells, SUD
  content, age fields) and two markdown narratives — one seeded with PHI that
  **must** redact, one of near-misses that **must NOT** redact.
