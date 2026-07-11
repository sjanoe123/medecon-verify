"""Risk-adjustment scorer — CMS-HCC v24/v28, HHS-HCC, CDPS.

Pure-Python pluggable scorer. Coefficient registries are seed values that
match published CMS coefficients in shape and order; production users should
override with the latest official tables (see references/coefficient-sources.md).

Public API:

    score_member(member: dict, model: str = "cms-hcc-v28") -> RiskScore
    score_population(members: list[dict], **kwargs) -> list[RiskScore]
    apply_v28_phase_in(raw_v28: float, raw_v24: float, year: int) -> float
    register_coefficients(model: str, coefficients: CoefficientTable) -> None

A `member` dict has at minimum:
    {
        "member_id": str,
        "age": int,
        "sex": "M" | "F",
        "dx_codes": list[str],         # ICD-10-CM codes for the condition window
        "medicaid_dual": bool,         # CMS-HCC eligibility flag
        "originally_disabled": bool,   # CMS-HCC eligibility flag
    }
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


def _norm_icd(code: str) -> str:
    """Normalize an ICD-10-CM code for lookup: strip dots/spaces, uppercase.

    Makes scoring robust to dotted ("E11.22") vs undotted ("E1122") forms, which
    differ between the dotted in-code seed maps and the undotted official CSVs /
    claims feeds.
    """
    return code.replace(".", "").replace(" ", "").strip().upper()


# ---------------------------------------------------------------------------
# Coefficient registry
# ---------------------------------------------------------------------------


@dataclass
class CoefficientTable:
    model: str
    description: str
    # --- modeled input domain (the boundary contract) -----------------------
    # The age range and subpops this table actually models. When set, score_member
    # validates inputs against them and RAISES on anything outside — rather than a
    # silent partial RAF from a missing-key default. Leave None for an
    # intentionally illustrative/sparse table (permissive: missing demo -> 0.0).
    valid_age_range: tuple[int, int] | None = None
    valid_subpops: frozenset[str] | None = None
    # demo: keyed by ("age_bucket", "sex", "subpop") → coefficient
    demographics: dict[tuple[str, str, str], float] = field(default_factory=dict)
    # hcc_coefs: keyed by HCC label → coefficient
    hcc_coefs: dict[str, float] = field(default_factory=dict)
    # icd_to_hcc: ICD-10 → HCC label (a small seed; production tables are 70k+)
    icd_to_hcc: dict[str, str] = field(default_factory=dict)
    # icd_to_hccs: ICD-10 → list of HCC labels, for the FULL crosswalks where one
    # code can map to several payment HCCs (e.g. V28 C50.911 → HCC22 + HCC23).
    # When non-empty this takes precedence over the 1:1 ``icd_to_hcc`` seed in
    # scoring. Populated from the committed reference CSV; the in-code 1:1 seed is
    # kept only as a clearly-labeled fallback used when the CSV is absent.
    icd_to_hccs: dict[str, list[str]] = field(default_factory=dict)
    # interactions: list of (exact-set-of-HCCs, coefficient). Fires when the
    # member has ALL listed HCCs (issubset). Use for true HCC-pair interactions.
    interactions: list[tuple[frozenset, float]] = field(default_factory=list)
    # group_interactions: list of ((group1, group2, ...), coefficient). Fires
    # `coefficient` ONCE when the member has >=1 triggered HCC in EACH group.
    # Models V28-style disease-GROUP interactions (e.g. any diabetes HCC + any
    # heart-failure HCC) without double-counting when a member carries more than
    # one HCC from the same group.
    group_interactions: list[tuple[tuple[frozenset, ...], float]] = field(
        default_factory=list
    )
    # hierarchies: HCC label -> the set of lower-severity HCCs it SUPPRESSES.
    # Applied before summing so a member coded for several HCCs in one disease
    # hierarchy counts the coefficient once (the highest-ranked HCC wins).
    hierarchies: dict[str, frozenset] = field(default_factory=dict)
    # originally_disabled_factors: sex -> additive demographic-interaction
    # coefficient for community-AGED members originally entitled by disability
    # (CMS-HCC "OriginallyDisabled"). Empty when the model/segment does not
    # define it.
    originally_disabled_factors: dict[str, float] = field(default_factory=dict)
    # hcc_coefs_by_subpop: subpop/segment -> {HCC label -> coefficient}. When
    # present, score_member selects the HCC coefficients for the member's subpop
    # (e.g. HHS metal level) instead of the flat `hcc_coefs`. Lets one table hold
    # metal-varying diagnosis factors without silently applying one level's coefs
    # to another.
    hcc_coefs_by_subpop: dict[str, dict[str, float]] = field(default_factory=dict)
    # _norm_crosswalk_cache: lazily-built normalized {ICD → [HCC,...]} map, so
    # score_member does not rebuild and list-copy the full ~8k-code crosswalk on
    # every member (population scoring would otherwise do O(members * map_size)
    # work). Not part of the public contract; populated by ``norm_crosswalk``.
    _norm_crosswalk_cache: dict[str, list[str]] | None = field(
        default=None, repr=False, compare=False
    )

    def norm_crosswalk(self) -> dict[str, list[str]]:
        """Normalized ICD → [HCC, ...] crosswalk, built once and cached.

        Prefers the full multi-valued ``icd_to_hccs`` when present; otherwise the
        1:1 ``icd_to_hcc`` seed. Keys are normalized via ``_norm_icd`` so dotted
        ("E11.22") and undotted ("E1122") codes both resolve. Cached because the
        full V28 crosswalk is ~8k codes and rebuilding it per member is wasted work
        at population scale.
        """
        if self._norm_crosswalk_cache is None:
            if self.icd_to_hccs:
                self._norm_crosswalk_cache = {
                    _norm_icd(k): list(v) for k, v in self.icd_to_hccs.items()
                }
            else:
                self._norm_crosswalk_cache = {
                    _norm_icd(k): [v] for k, v in self.icd_to_hcc.items()
                }
        return self._norm_crosswalk_cache


CMS_HCC_V24 = CoefficientTable(
    model="cms-hcc-v24",
    description="CMS-HCC v24 (the 2020 model; 75% in PY2021, 100% for PY2022-2023). Seed coefficients.",
    # Community-aged segment only: ages 65+, community subpop.
    valid_age_range=(65, 200),
    valid_subpops=frozenset({"community"}),
    demographics={
        ("65-69", "M", "community"): 0.331,
        ("65-69", "F", "community"): 0.323,
        ("70-74", "M", "community"): 0.391,
        ("70-74", "F", "community"): 0.371,
        ("75-79", "M", "community"): 0.479,
        ("75-79", "F", "community"): 0.452,
        ("80-84", "M", "community"): 0.602,
        ("80-84", "F", "community"): 0.566,
        ("85-89", "M", "community"): 0.741,
        ("85-89", "F", "community"): 0.704,
        ("90+",   "M", "community"): 0.883,
        ("90+",   "F", "community"): 0.831,
    },
    hcc_coefs={
        "HCC1": 0.473,    # HIV/AIDS
        "HCC2": 0.514,    # Septicemia, Sepsis
        "HCC8": 2.659,    # Metastatic Cancer
        "HCC18": 0.302,  # Diabetes with chronic complications
        "HCC85": 0.323,  # Congestive Heart Failure
        "HCC96": 0.265,  # Specified Heart Arrhythmias
        "HCC108": 0.346, # Vascular Disease
        "HCC111": 0.335, # COPD
    },
    icd_to_hcc={
        "B20": "HCC1",
        "A41.9": "HCC2",
        "C77.9": "HCC8",
        "E11.22": "HCC18",
        "I50.9": "HCC85",
        "I48.91": "HCC96",
        "I73.9": "HCC108",
        "J44.9": "HCC111",
    },
    interactions=[
        (frozenset({"HCC85", "HCC111"}), 0.156),  # HF + COPD
        (frozenset({"HCC18", "HCC85"}), 0.121),   # DM with complications + HF
    ],
)


# ---------------------------------------------------------------------------
# CMS-HCC V28 (the "2024 CMS-HCC model"; 100% of PY2026 risk scores) — SOURCED.
#
# Provenance (retrieved 2026-06-12):
#   * Coefficients + ICD-10 mapping: CMS "2026 Midyear/Final Model Software /
#     ICD-10 Mappings", https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/2026-model-software-icd-10-mappings
#       - Python package: https://www.cms.gov/files/zip/2026-midyear-final-model-software-python.zip
#         -> CMS_HCC_v28_2026_T_package_v3.zip -> V28_CE_Relative_Factors.csv,
#            ICD10_CC_mappings_CMS_HCC_2026_v28.csv
#       - SAS package (cross-check): https://www.cms.gov/files/zip/2026-midyear-final-model-software.zip
#         -> "CMS-HCC software V2826.115.T1.zip" -> C2824T2N.csv
#   * Printed coefficient table: "Announcement of Calendar Year (CY) 2024
#     Medicare Advantage (MA) Capitation Rates..." (Mar 31, 2023), Attachment
#     VIII, Table VIII-1 "2024 CMS-HCC Model Relative Factors for Continuing
#     Enrollees", https://www.cms.gov/files/document/2024-announcement-pdf.pdf
#   * PY2026 applicability: CY2026 Rate Announcement (Apr 7, 2025) completes
#     the phase-in — 100% of CY2026 risk scores use the 2024 CMS-HCC model.
#     https://www.cms.gov/files/document/2026-announcement.pdf
#
# Segment: this scorer models the COMMUNITY NON-DUAL AGED (CNA) segment, keyed
# with subpop="community". The DEFAULT diagnosis coefficients are the FULL CNA
# `hcc` rows (all 115 payment HCCs) loaded at import from the committed multi-
# segment table references/cms_hcc_v28_py2026_coefficients.csv (CNA, CPA, CFA,
# CND, CPD, CFD, INS, NE, SNPNE — 1,237 coefficients). The six-HCC in-code map
# survives only as a labeled fallback (_V28_HCC_COEF_FALLBACK) used when that CSV
# is absent. Loading the full CNA table is what lets every triggered HCC from the
# full crosswalk contribute its real RAF instead of summing to 0.0.
#
# ICD->HCC crosswalk: the DEFAULT is the full FY2025/FY2026 ICD-10-CM -> V28
# payment-HCC mapping (8,933 rows, 8,019 distinct codes, all 115 payment HCCs) at
# references/cms_hcc_v28_icd10_map.csv, loaded once at import into icd_to_hccs.
# The old 4-code in-code map survives only as a labeled fallback
# (_V28_ICD_TO_HCC_FALLBACK) used when that CSV is absent. Using the full table
# is what prevents the RAF under-capture the sparse seed caused.
#
# Constraining: V28 constrains coefficients within disease families. All
# diabetes HCCs except HCC35 Pancreas Transplant Status share one coefficient
# per segment (CNA = 0.166 for HCC36/37/38).
# ---------------------------------------------------------------------------
_V28_ICD_CSV = (
    Path(__file__).resolve().parent.parent
    / "references" / "cms_hcc_v28_icd10_map.csv"
)
_V28_COEF_CSV = (
    Path(__file__).resolve().parent.parent
    / "references" / "cms_hcc_v28_py2026_coefficients.csv"
)

#: Module-level flag set True when EITHER committed V28 reference CSV is absent and
#: the loader fell back to the sparse in-code seed. ``score_member`` reads it so a
#: packaging/data omission surfaces as a RiskScore note rather than masquerading as
#: a normal full-table default (the "confidently wrong number" failure mode).
_V28_USING_FALLBACK = False

#: Clearly-labeled sparse FALLBACK used only when the committed full crosswalk CSV
#: is absent. These are the original illustrative V28 seed assignments; the full
#: 8,933-row mapping (loaded by ``_load_v28_icd_to_hccs``) is the default.
_V28_ICD_TO_HCC_FALLBACK = {
    "E11.65": "HCC38",
    "E11.22": "HCC37",
    "I50.9": "HCC226",
    "J44.9": "HCC280",
}

#: Clearly-labeled sparse FALLBACK CNA HCC coefficients, used only when the
#: committed full coefficient CSV is absent. These match the published CNA values
#: for the handful of HCCs the original seed carried; the full 115-HCC CNA table
#: (loaded by ``_load_v28_cna_hcc_coefs``) is the default. Without the full table,
#: triggered HCCs outside this handful add ZERO diagnosis RAF (the under-capture
#: this fix removes).
_V28_HCC_COEF_FALLBACK = {
    "HCC35": 0.949,   # Pancreas Transplant Status
    "HCC36": 0.166,   # Diabetes with Severe Acute Complications
    "HCC37": 0.166,   # Diabetes with Chronic Complications
    "HCC38": 0.166,   # Diabetes with No, Glycemic, or Unspecified Complications
    "HCC226": 0.360,  # Heart Failure, Except End Stage and Acute
    "HCC280": 0.319,  # COPD
}


def _load_v28_icd_to_hccs() -> dict[str, list[str]]:
    """Load the committed full V28 ICD-10-CM → payment-HCC crosswalk.

    Parses ``references/cms_hcc_v28_icd10_map.csv`` (8,933 rows, undotted codes)
    once into a normalized {ICD → [HCC, ...]} map so the default scoring path uses
    the full CMS crosswalk, not the sparse in-code seed (which under-captured RAF).
    One code can map to several payment HCCs (e.g. C50.911 → HCC22 + HCC23), so
    values are lists, preserving CSV order and de-duplicating.

    Falls back to the labeled in-code sparse map only when the CSV is absent, so
    the scorer still runs in a stripped-down checkout. Sets ``_V28_USING_FALLBACK``
    so the fallback is surfaced, not silent.
    """
    global _V28_USING_FALLBACK
    if not _V28_ICD_CSV.exists():
        _V28_USING_FALLBACK = True
        return {
            _norm_icd(k): [v] for k, v in _V28_ICD_TO_HCC_FALLBACK.items()
        }
    mapping: dict[str, list[str]] = {}
    with open(_V28_ICD_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = _norm_icd(r["icd10_code"])
            hcc = r["v28_hcc"].strip()
            if not code or not hcc:
                continue
            hccs = mapping.setdefault(code, [])
            if hcc not in hccs:
                hccs.append(hcc)
    return mapping


def _load_v28_cna_hcc_coefs() -> dict[str, float]:
    """Load the committed full CNA HCC coefficient table for V28.

    Parses ``references/cms_hcc_v28_py2026_coefficients.csv`` (the official
    multi-segment relative-factor table) and returns the COMMUNITY NON-DUAL AGED
    (CNA) segment's ``hcc`` rows as {HCC label → coefficient} — all 115 payment
    HCCs, not the six-HCC seed. This is what lets a triggered HCC from the full
    crosswalk (e.g. C50.911 → HCC22, G35 → HCC198) contribute its REAL diagnosis
    RAF instead of summing to 0.0 against a sparse seed.

    Falls back to the labeled in-code sparse coefficient map only when the CSV is
    absent, so the scorer still runs in a stripped-down checkout. Sets
    ``_V28_USING_FALLBACK`` so the fallback is surfaced, not silent.
    """
    global _V28_USING_FALLBACK
    if not _V28_COEF_CSV.exists():
        _V28_USING_FALLBACK = True
        return dict(_V28_HCC_COEF_FALLBACK)
    coefs: dict[str, float] = {}
    with open(_V28_COEF_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["segment"] != "CNA" or r["factor_type"] != "hcc":
                continue
            key = r["factor_key"].strip()
            if not key:
                continue
            coefs[key] = float(r["coefficient"])
    # Defensive: an empty/garbled CSV must not silently zero every diagnosis RAF.
    if not coefs:
        _V28_USING_FALLBACK = True
        return dict(_V28_HCC_COEF_FALLBACK)
    return coefs


_V28_ICD_TO_HCCS = _load_v28_icd_to_hccs()
_V28_CNA_HCC_COEFS = _load_v28_cna_hcc_coefs()


CMS_HCC_V28 = CoefficientTable(
    model="cms-hcc-v28",
    description=(
        "CMS-HCC v28 (the 2024 model; 33% PY2024, 67% PY2025, 100% PY2026). "
        "Community Non-Dual Aged (CNA) segment, official PY2026 software values."
    ),
    # Community Non-Dual Aged segment only: ages 65+, community subpop.
    valid_age_range=(65, 200),
    valid_subpops=frozenset({"community"}),
    demographics={
        # CNA age-sex factors (V28_CE_Relative_Factors.csv, COMMUNITY_NA column;
        # C2824T2N.csv CNA_F65_69..CNA_M95_GT). V28 bands end at 90-94 then 95+.
        ("65-69", "M", "community"): 0.332,
        ("65-69", "F", "community"): 0.330,
        ("70-74", "M", "community"): 0.396,
        ("70-74", "F", "community"): 0.395,
        ("75-79", "M", "community"): 0.502,
        ("75-79", "F", "community"): 0.465,
        ("80-84", "M", "community"): 0.571,
        ("80-84", "F", "community"): 0.524,
        ("85-89", "M", "community"): 0.664,
        ("85-89", "F", "community"): 0.624,
        ("90-94", "M", "community"): 0.800,
        ("90-94", "F", "community"): 0.737,
        ("95+",   "M", "community"): 0.896,
        ("95+",   "F", "community"): 0.742,
    },
    # Full CNA HCC coefficient table (all 115 payment HCCs), loaded once at import
    # from references/cms_hcc_v28_py2026_coefficients.csv. This is what lets a
    # triggered HCC from the full crosswalk contribute its REAL diagnosis RAF
    # (e.g. C50.911 → HCC22 = 0.363, G35 → HCC198 = 0.647) instead of summing to
    # 0.0 against a six-HCC seed. The diabetes family (HCC36/37/38) is constrained
    # to a single 0.166 value in the source table; HCC35 Pancreas Transplant Status
    # (0.949) is the documented exception at the top of the diabetes hierarchy. The
    # six-HCC in-code seed survives only as a labeled fallback
    # (_V28_HCC_COEF_FALLBACK) used when the coefficient CSV is absent.
    hcc_coefs=_V28_CNA_HCC_COEFS,
    # Default crosswalk: the FULL committed CMS V28 ICD-10-CM → payment-HCC table
    # (references/cms_hcc_v28_icd10_map.csv, 8,933 rows, 8,019 codes, all 115
    # payment HCCs), loaded once at import. Using the full table is what prevents
    # the RAF under-capture the sparse in-code seed caused. One code can map to
    # several payment HCCs (e.g. C50.911 → HCC22 + HCC23), so this is a
    # multi-valued map. The 1:1 in-code seed survives only as a labeled fallback
    # (_V28_ICD_TO_HCC_FALLBACK) used when the CSV is absent.
    # (I73.9, unspecified PVD, maps to NO V28 payment HCC and is correctly absent
    # from the committed crosswalk — the V28 vascular-group reconfiguration.)
    icd_to_hccs=_V28_ICD_TO_HCCS,
    # Published V28 interactions are disease-GROUP level (V28_Interactions.csv:
    # DIABETES_V28 x HF_V28, HF_V28 x CHR_LUNG_V28; CNA coefficients from
    # V28_CE_Relative_Factors.csv). Modeled as group_interactions so any diabetes
    # HCC (36/37/38) paired with HF fires 0.112 ONCE — the exact-pair form missed
    # E11.65->HCC38 + HF and double-counted members with multiple diabetes HCCs.
    # Full group memberships are in references/cms_hcc_v28_py2026_coefficients.csv.
    group_interactions=[
        (
            (frozenset({"HCC36", "HCC37", "HCC38"}), frozenset({"HCC226"})),
            0.112,
        ),  # DIABETES_HF_V28 (CNA)
        (
            (frozenset({"HCC226"}), frozenset({"HCC280"})),
            0.078,
        ),  # HF_CHR_LUNG_V28 (CNA)
    ],
    # CMS-HCC V28 within-disease-group hierarchies (V28_HCC_Hierarchies.csv): a
    # higher-severity HCC suppresses its lower-ranked siblings so the group is paid
    # once. Two families are modeled here because the committed crosswalk routes the
    # tested codes into both:
    #   * Diabetes: HCC35 (pancreas transplant) > HCC36 (severe acute) > HCC37
    #     (chronic complications) > HCC38 (no/unspecified).
    #   * Malignant neoplasm: HCC17 > HCC18 > HCC19 > HCC20 > HCC21 > HCC22 > HCC23,
    #     monotone in severity (and in the CNA coefficient: HCC22=0.363 > HCC23=
    #     0.186). The committed crosswalk maps an active primary breast malignancy
    #     C50.911 → BOTH HCC22 and HCC23; the hierarchy keeps the higher-ranked
    #     HCC22 and suppresses HCC23 so the neoplasm group is paid once, not twice.
    # Each HCC suppresses ALL lower-ranked siblings in its chain.
    hierarchies={
        # diabetes
        "HCC35": frozenset({"HCC36", "HCC37", "HCC38"}),
        "HCC36": frozenset({"HCC37", "HCC38"}),
        "HCC37": frozenset({"HCC38"}),
        # malignant neoplasm
        "HCC17": frozenset({"HCC18", "HCC19", "HCC20", "HCC21", "HCC22", "HCC23"}),
        "HCC18": frozenset({"HCC19", "HCC20", "HCC21", "HCC22", "HCC23"}),
        "HCC19": frozenset({"HCC20", "HCC21", "HCC22", "HCC23"}),
        "HCC20": frozenset({"HCC21", "HCC22", "HCC23"}),
        "HCC21": frozenset({"HCC22", "HCC23"}),
        "HCC22": frozenset({"HCC23"}),
    },
    # CNA OriginallyDisabled demographic-interaction factors
    # (cms_hcc_v28_py2026_coefficients.csv): Female 0.228, Male 0.135. Applies to
    # community-aged members originally entitled by disability.
    originally_disabled_factors={"F": 0.228, "M": 0.135},
)


# ---------------------------------------------------------------------------
# HHS-HCC (ACA marketplace), 2026 benefit year FINAL coefficients — SOURCED.
#
# Provenance (retrieved 2026-06-12):
#   * Coefficients: CMS/CCIIO, "2026 Benefit Year Final HHS Risk Adjustment
#     Model Coefficients" (Jan 13, 2025),
#     https://www.cms.gov/files/document/2026-benefit-year-final-hhs-risk-adjustment-model-coefficients2025-01-13.pdf
#     Published per 45 CFR 153.320(b)(1)(i); annual recalibration blends
#     coefficients separately solved on 2020/2021/2022 enrollee-level EDGE
#     data (Final 2026 Payment Notice). The published numbers already bake in
#     the partially-phased-out Hepatitis C drug market pricing adjustment, the
#     new PrEP affiliated cost factor (ACF), and $1M high-cost-pool truncation.
#   * Classification + ICD-10 crosswalk: V08 HHS-HCC classification, "HHS-
#     Developed Risk Adjustment Model Algorithm 'Do It Yourself (DIY)'
#     Software" tables (final, Mar 30, 2026), Table 3 "ICD-10 to V08 HHS-
#     Condition Categories (CC) Crosswalk" (FY2025 + FY2026 ICD-10 codes),
#     https://www.cms.gov/files/document/cy2025-diy-tables-03-30-2026.xlsx
#
# Canonical in-code level: HHS adult-model HCC coefficients vary by metal
# level; the hcc_coefs below are the SILVER ADULT model. The demographics dict
# keeps the seed's (age_bucket, sex, metal-level-in-the-subpop-slot) keys with
# the published adult age-sex factors. The COMPLETE 2026 final coefficient set
# (adult/child/infant x platinum/gold/silver/bronze/catastrophic, including
# RXC, RXC-HCC interaction, enrollment-duration, interacted-HCC-counts, and
# ACF factors) is committed at references/hhs_hcc_2026_coefficients.csv; the
# full V08 ICD-10 -> CC crosswalk (11,513 codes) at
# references/hhs_hcc_v08_2026_icd10_map.csv.
#
# NOTE: HHS HCC numbering is NOT CMS-HCC numbering. A41.9 -> HHS HCC002
# (Septicemia/Sepsis/SIRS/Shock), E11.22 -> HHS HCC020 (Diabetes with Chronic
# Complications), I50.9 -> HHS HCC130 (Heart Failure), per DIY Table 3.
# ---------------------------------------------------------------------------
_HHS_2026_CSV = (
    Path(__file__).resolve().parent.parent
    / "references" / "hhs_hcc_2026_coefficients.csv"
)
_HHS_SEX = {"Male": "M", "Female": "F"}


def _load_hhs_2026_adult():
    """Load the full adult HHS-HCC 2026 grid from the committed CSV.

    Returns (demographics, hcc_coefs_by_metal) where demographics is keyed
    (age_bucket, sex, metal_level) and hcc_coefs_by_metal is metal -> {HCC ->
    coef}. This keeps HHS scoring metal- and age-complete (the in-code seed only
    had silver + 6 demographic cells) so a non-silver call is not scored with
    silver coefficients.
    """
    demographics: dict[tuple[str, str, str], float] = {}
    by_metal: dict[str, dict[str, float]] = {}
    with open(_HHS_2026_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["model"] != "adult":
                continue
            metal = r["metal_level"]
            coef = float(r["coefficient"])
            ft = r["factor_type"]
            key = r["factor_key"]
            if ft == "demographic" and key.startswith("Age "):
                # "Age 21-24, Female" -> ("21-24", "F", metal)
                band, sex_word = key[len("Age "):].split(", ")
                sex = _HHS_SEX.get(sex_word.strip())
                if sex:
                    demographics[(band.strip(), sex, metal)] = coef
            elif ft == "diagnosis" and key.startswith("HCC"):
                by_metal.setdefault(metal, {})[key] = coef
    return demographics, by_metal


_HHS_DEMOGRAPHICS, _HHS_HCC_BY_METAL = _load_hhs_2026_adult()

#: HHS-HCC demographics are keyed by metal level (not the CMS "community" subpop),
#: so an HHS call must name a metal level; the domain contract enforces it.
HHS_METAL_LEVELS = frozenset(
    {"platinum", "gold", "silver", "bronze", "catastrophic"}
)

HHS_HCC_2026 = CoefficientTable(
    model="hhs-hcc-2026",
    description=(
        "HHS-HCC for the ACA marketplace, 2026 benefit year final coefficients "
        "(CMS/CCIIO, Jan 13, 2025; V08 classification, 2020-2022 EDGE blend). "
        "Full adult age-sex demographics and metal-keyed diagnosis factors are "
        "loaded from references/hhs_hcc_2026_coefficients.csv so every metal "
        "level and age band scores with its own sourced coefficients."
    ),
    # ACA marketplace adult model: ages 21-64, keyed by metal level.
    valid_age_range=(21, 64),
    valid_subpops=HHS_METAL_LEVELS,
    demographics=_HHS_DEMOGRAPHICS,
    # Flat hcc_coefs kept as the silver subset for back-compat / direct reads;
    # score_member selects from hcc_coefs_by_subpop by metal level.
    hcc_coefs={
        "HCC002": _HHS_HCC_BY_METAL["silver"]["HCC002"],
        "HCC020": _HHS_HCC_BY_METAL["silver"]["HCC020"],
        "HCC130": _HHS_HCC_BY_METAL["silver"]["HCC130"],
    },
    hcc_coefs_by_subpop=_HHS_HCC_BY_METAL,
    icd_to_hcc={
        # V08 assignments, DIY Table 3 (FY2025 + FY2026 code valid = Y).
        "A41.9": "HCC002",
        "E11.22": "HCC020",
        "I50.9": "HCC130",
    },
)


CDPS_2025 = CoefficientTable(
    model="cdps-2025",
    description="Chronic Illness and Disability Payment System (Medicaid). Seed.",
    demographics={
        ("0-4",   "M", "tanf"): 0.45,
        ("5-14",  "M", "tanf"): 0.40,
        ("15-44", "F", "tanf"): 0.62,
        ("45-64", "F", "tanf"): 0.78,
    },
    hcc_coefs={
        "CDPS-CARDIOVASCULAR-VERYHIGH": 1.450,
        "CDPS-DIABETES-MEDIUM": 0.560,
        "CDPS-PSYCH-HIGH": 1.220,
    },
    icd_to_hcc={
        "I50.9": "CDPS-CARDIOVASCULAR-VERYHIGH",
        "E11.22": "CDPS-DIABETES-MEDIUM",
        "F20.9": "CDPS-PSYCH-HIGH",
    },
)


REGISTRY: dict[str, CoefficientTable] = {
    "cms-hcc-v24": CMS_HCC_V24,
    "cms-hcc-v28": CMS_HCC_V28,
    "hhs-hcc-2026": HHS_HCC_2026,
    "cdps-2025": CDPS_2025,
}


def register_coefficients(model: str, coefficients: CoefficientTable) -> None:
    REGISTRY[model] = coefficients


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class RiskScore:
    member_id: str
    model: str
    raf: float
    demographic_component: float
    hcc_component: float
    interaction_component: float
    triggered_hccs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _age_bucket(age: int, model: str) -> str:
    if model.startswith("cms-hcc"):
        # CMS uses 5-year bands plus disabled <65. The official V28 tables run
        # 65-69 ... 90-94 then 95+ (V28_CE_Relative_Factors.csv); the older
        # v24 seed keeps its original 90+ terminal band.
        if age < 65:
            return "<65"
        top = 95 if model == "cms-hcc-v28" else 90
        for low in range(65, top, 5):
            if low <= age < low + 5:
                return f"{low}-{low + 4}"
        return f"{top}+"
    if model.startswith("hhs-hcc"):
        # HHS adult model age-sex bands (2026 BY final coefficients): a 4-year
        # 21-24 band, then 5-year bands 25-29, 30-34, ... 60-64. (The child and
        # infant models use their own bands; this scorer keys the adult demos.)
        if age < 21:
            return "0-20"
        if age < 25:
            return "21-24"
        for low in range(25, 65, 5):
            if low <= age < low + 5:
                return f"{low}-{low + 4}"
        # Age >= 65 is outside the HHS marketplace adult model (Medicare territory);
        # return an out-of-model sentinel rather than silently folding into 60-64.
        return "65+"
    if model.startswith("cdps"):
        if age < 5:
            return "0-4"
        if age < 15:
            return "5-14"
        if age < 45:
            return "15-44"
        if age < 65:
            return "45-64"
        return "65+"
    return "unknown"


def _resolve_table(model: str, subpop: str) -> "CoefficientTable":
    """Validate the population-wide config (model + subpop) and return the table.

    These two checks depend ONLY on the kwargs, not on any member, so they are
    identical for every row of a roster. ``score_member`` calls this so a single
    bad call fails loud; ``score_population`` calls it ONCE before the loop so a
    population-wide misconfiguration (misspelled model, or an HHS model without a
    metal subpop) fails fast here instead of raising on every row and being
    caught by the per-member handler into an all-zero population RAF — the
    "confidently wrong number" this repo guards against.
    """
    if model not in REGISTRY:
        raise ValueError(
            f"unknown model {model!r}; registered: {sorted(REGISTRY)}"
        )
    table = REGISTRY[model]
    if table.valid_subpops is not None and subpop not in table.valid_subpops:
        raise ValueError(
            f"subpop {subpop!r} is not modeled by {model}; expected one of "
            f"{sorted(table.valid_subpops)}."
        )
    return table


def score_member(
    member: dict,
    *,
    model: str = "cms-hcc-v28",
    subpop: str = "community",
) -> RiskScore:
    """Score a single member's RAF for the chosen model.

    INPUT-DOMAIN CONTRACT: a table that declares ``valid_age_range`` /
    ``valid_subpops`` models only that domain. An age or subpop outside it RAISES
    (e.g. HHS is ages 21-64 keyed by metal level; CMS-HCC is community-aged 65+),
    rather than returning a silent partial RAF from a missing-key default — the
    "confidently wrong number" failure this repo guards against. Within the
    domain the demographic factor is mandatory (the CMS/HHS tables are complete);
    an absent demographic is therefore an error, not a zero. A genuinely-zero
    contribution (an ICD that maps to no HCC) is still fine.
    """
    # Population-wide config (model + subpop) is validated here so a single
    # score_member call still fails loud; score_population reuses _resolve_table
    # before its loop so the same config error is not swallowed per-row.
    table = _resolve_table(model, subpop)

    if member.get("medicaid_dual") and model.startswith("cms-hcc"):
        # Dual status selects a different CMS-HCC segment (CND/CPD/CFD). The
        # in-code seed exposes only the non-dual community (CNA) segment, so
        # honoring the flag would require segment routing the seed does not model.
        # Raise rather than silently score it as non-dual (a wrong RAF).
        raise NotImplementedError(
            "medicaid_dual segment routing (CND/CPD/CFD) is not modeled in this "
            "seed; the in-code CMS-HCC table is the non-dual community (CNA) "
            "segment. Supply a dual-segment coefficient table to score duals."
        )

    age_raw = member.get("age")
    if age_raw is None:
        raise ValueError(
            f"{member.get('member_id', 'member')}: age is required and must be an "
            "integer (got None — check the eligibility/claims join)."
        )
    try:
        age = int(age_raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"{member.get('member_id', 'member')}: age must be an integer, "
            f"got {age_raw!r}."
        ) from None
    sex = member.get("sex", "M")

    notes: list[str] = []

    # Surface a V28 reference-data omission rather than letting it masquerade as a
    # normal full-table default: if either committed V28 CSV was absent at import,
    # the scorer is running on the sparse in-code fallback (most diagnoses score
    # 0.0 RAF), so every V28 score carries a diagnostic note.
    if model == "cms-hcc-v28" and _V28_USING_FALLBACK:
        notes.append(
            "V28 committed reference CSV(s) absent; scoring on the sparse in-code "
            "fallback — diagnosis RAF is under-captured."
        )

    # --- input-domain validation (the single boundary contract) -------------
    if table.valid_age_range is not None:
        lo, hi = table.valid_age_range
        if not lo <= age <= hi:
            raise ValueError(
                f"age {age} is outside the {model} modeled domain (ages {lo}-{hi}); "
                "score with the model that covers this age, not a partial RAF."
            )
    # NOTE: the subpop-domain check now lives in _resolve_table (called at the
    # top), so a population-wide bad subpop fails fast in score_population too.

    bucket = _age_bucket(age, model)
    demo_key = (bucket, sex, subpop)
    if table.valid_age_range is not None or table.valid_subpops is not None:
        # Domain-constrained table: the grid is complete within its domain, so a
        # missing demographic key is a real gap, not a silent zero.
        if demo_key not in table.demographics:
            raise ValueError(
                f"no demographic coefficient for {demo_key} in {model} "
                "(unexpected gap in a domain-complete table)"
            )
        demo = table.demographics[demo_key]
    else:
        # Illustrative/sparse table (no declared domain): permissive default.
        demo = table.demographics.get(demo_key, 0.0)

    # OriginallyDisabled demographic-interaction factor for community-aged members
    # originally entitled by disability (CMS-HCC). Additive; applies only to aged
    # members (>=65) of a model that defines the factor. The flag is a documented
    # standard member field, so a model that does not model it (v24/CDPS seeds)
    # records a note and ignores it — it must NOT abort scoring of an otherwise
    # in-domain member.
    if member.get("originally_disabled"):
        if table.originally_disabled_factors:
            if age >= 65:
                demo += table.originally_disabled_factors.get(sex, 0.0)
        else:
            notes.append(
                f"originally_disabled not modeled for {model}; flag ignored"
            )

    # Normalize ICD-10 codes so dotted ("E11.22") and undotted ("E1122") forms
    # both score. The in-code seed maps are dotted, but the committed official
    # mapping CSVs and most claims feeds store codes undotted; without this a
    # valid undotted code silently yields no HCC and zero diagnosis RAF. The
    # normalized crosswalk is built once per table and cached (the full V28 map is
    # ~8k codes; rebuilding it per member is wasted work at population scale).
    norm_map = table.norm_crosswalk()
    triggered_hccs: set[str] = set()
    for code in member.get("dx_codes", []) or []:
        for hcc in norm_map.get(_norm_icd(code), ()):
            triggered_hccs.add(hcc)

    # Apply CMS-HCC hierarchies BEFORE summing: within a hierarchy, a
    # higher-severity HCC suppresses (zeroes) its lower siblings, so a member
    # coded for several HCCs in one disease hierarchy counts the coefficient
    # once, not once per code. Without this, a member with both HCC37 and HCC38
    # (chronic + unspecified diabetes) would be paid the diabetes coefficient
    # twice and RAF would be inflated.
    suppressed: set[str] = set()
    for hcc in triggered_hccs:
        suppressed |= table.hierarchies.get(hcc, frozenset()) & triggered_hccs
    triggered_hccs -= suppressed

    # Select subpop-specific HCC coefficients when the table provides them (e.g.
    # HHS metal levels), so a platinum member is not scored with silver coefs.
    coefs = table.hcc_coefs_by_subpop.get(subpop, table.hcc_coefs)
    hcc_total = sum(coefs.get(h, 0.0) for h in triggered_hccs)
    # NOTE: the V28 payment-HCC-count factors (D1..D10P) are 0.0 for 1-4 HCCs and
    # only positive at >=5. They are intentionally NOT summed here — the count
    # component is a known, deliberate omission. The diagnosis coefficients above
    # are now the FULL committed CNA table (all 115 payment HCCs), so triggered HCCs
    # contribute their real RAF; only the >=5-HCC count add-on remains unmodeled.
    # Load the payment_hcc_count factors from
    # references/cms_hcc_v28_py2026_coefficients.csv before relying on it.

    interaction_total = 0.0
    for required_set, coef in table.interactions:
        if required_set.issubset(triggered_hccs):
            interaction_total += coef
    # Disease-group interactions fire once if every group is represented.
    for groups, coef in table.group_interactions:
        if all(group & triggered_hccs for group in groups):
            interaction_total += coef

    raf = demo + hcc_total + interaction_total
    # Domain-constrained models validate the demographic up front (raise on a
    # gap). For an illustrative/sparse table a zero demo is expected, not noted.
    if (demo == 0.0 and bucket != "unknown"
            and table.valid_age_range is None and table.valid_subpops is None):
        notes.append(f"no demographic coefficient for {demo_key}")
    return RiskScore(
        member_id=member.get("member_id", "unknown"),
        model=model,
        raf=raf,
        demographic_component=demo,
        hcc_component=hcc_total,
        interaction_component=interaction_total,
        triggered_hccs=sorted(triggered_hccs),
        notes=notes,
    )


def score_population(members: list[dict], **kwargs) -> list[RiskScore]:
    """Score a roster, isolating per-member failures.

    score_member fails loud on a single out-of-domain / missing-age / dual record
    (correct for one call), but at population scale one anomalous row must not
    discard the whole batch. A member that raises yields a RiskScore with raf=0.0
    and a diagnostic note, so the failure is surfaced (not silently zeroed) while
    the rest of the roster still scores.

    Population-wide config errors are the exception: model and subpop are kwargs
    applied identically to every row, so a misspelled model or an HHS model
    without a metal subpop would raise on EVERY member and the per-row handler
    would silently zero the entire roster (an all-zero population RAF a caller
    would aggregate as real). _resolve_table is called once up front so that
    misconfiguration fails fast instead of being swallowed per-row.
    """
    # Fail fast on population-wide misconfiguration BEFORE the per-row loop.
    _resolve_table(kwargs.get("model", "cms-hcc-v28"),
                   kwargs.get("subpop", "community"))
    scores: list[RiskScore] = []
    for m in members:
        try:
            scores.append(score_member(m, **kwargs))
        except (ValueError, NotImplementedError) as e:
            scores.append(RiskScore(
                member_id=m.get("member_id", "unknown"),
                model=kwargs.get("model", "cms-hcc-v28"),
                raf=0.0,
                demographic_component=0.0,
                hcc_component=0.0,
                interaction_component=0.0,
                notes=[f"scoring failed: {e}"],
            ))
    return scores


def apply_v28_phase_in(raw_v28: float, raw_v24: float, year: int) -> float:
    """Apply the CMS-HCC v28 phase-in blending.

    2024 = 33% v28 + 67% v24
    2025 = 67% v28 + 33% v24
    2026 = 100% v28
    """
    if year <= 2023:
        return raw_v24
    if year == 2024:
        return 0.33 * raw_v28 + 0.67 * raw_v24
    if year == 2025:
        return 0.67 * raw_v28 + 0.33 * raw_v24
    return raw_v28


def population_average_raf(scores: list[RiskScore]) -> float:
    if not scores:
        return 0.0
    return sum(s.raf for s in scores) / len(scores)
