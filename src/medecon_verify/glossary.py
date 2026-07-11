"""Semantic layer — the canonical metric/term definitions, consulted first.

Anthropic's self-service-analytics playbook puts a semantic layer at the top of
the trust hierarchy: compiled metric and dimension definitions the agent is
"structurally required to consult" before computing or narrating a number,
because the same word ("active users", "PMPM") can mean different things across
tables. medecon's canonical definitions live in the 245-term
`skills/methodology/stakeholder-translation/references/glossary.yaml`.

This module is the one loader for that glossary. `/explain` renders single
lookups; the `metric-terms-resolve-to-glossary` eval checker uses `resolve()` to
confirm a deliverable's metric jargon is pinned to definitions rather than left
ambiguous. A small fallback set keeps lookups working when the YAML or PyYAML
isn't reachable.

Public API:

    define(term) -> dict | None        # canonical entry for a term
    known_terms() -> set[str]          # all glossary keys (upper-cased)
    metric_terms() -> set[str]         # the computational metrics subset
    resolve(text) -> dict[str, dict]   # glossary terms appearing in `text`
    load_glossary() -> dict            # full merged glossary
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Built-in fallback glossary — used when the canonical YAML or PyYAML isn't
# reachable (skill-only / offline environments). Covers the most-asked terms so
# lookups stay useful; the full ~245-term set lives in the YAML. Keep this in
# sync with what the explain-skill tests require to resolve offline.
_FALLBACK_GLOSSARY: dict[str, dict[str, Any]] = {
    "PMPM": {"definition": "Per-Member-Per-Month. The standard cost normalization "
             "in healthcare: total dollars divided by member-months. Lets you "
             "compare a small employer with a large one without scaling apples "
             "to oranges.", "see_also": ["PMPY", "member-months", "MLR"]},
    "PMPY": {"definition": "Per-Member-Per-Year. The same idea as PMPM but on an "
             "annual denominator (member-years). MSSP reports use PMPY; commercial "
             "actuarial reports often use PMPM.", "see_also": ["PMPM", "member-months"]},
    "MLR": {"definition": "Medical Loss Ratio. Medical claim spend divided by "
            "premium. ACA-regulated minimums: 80% for individual/small group, 85% "
            "for large group. Below the floor → rebates owed.",
            "see_also": ["premium", "PMPM", "rebate"]},
    "HCC": {"definition": "Hierarchical Condition Categories. CMS's risk-adjustment "
            "grouper that maps ICD-10 diagnoses into clinical categories with "
            "weights. v24 covers PY2017–PY2023; v28 phases in PY2024-PY2026.",
            "see_also": ["RAF", "v24", "v28", "risk-adjustment"]},
    "RAF": {"definition": "Risk Adjustment Factor. The HCC-derived score that "
            "scales the MA benchmark / MSSP target for a beneficiary's health "
            "risk. RAF 1.0 = average; >1.0 → sicker, larger payment.",
            "see_also": ["HCC", "MA", "MSSP"]},
    "BPCI": {"definition": "Bundled Payments for Care Improvement. CMS bundle "
             "program where providers take risk on a 90-day episode of care. BPCI "
             "Advanced (BPCI-A) is the current iteration; covers 33 inpatient + 4 "
             "outpatient clinical episodes.",
             "see_also": ["episode", "target_price", "MS-DRG"]},
    "MSSP": {"definition": "Medicare Shared Savings Program. The standing CMS ACO "
             "program. ACOs benchmark against historical regional spend; meet "
             "quality bar AND save money → share savings; some tracks have downside "
             "risk.", "see_also": ["ACO", "benchmark", "quality_gate"]},
    "ACO": {"definition": "Accountable Care Organization. A group of providers "
            "jointly responsible for cost + quality of an attributed beneficiary "
            "panel.", "see_also": ["MSSP", "ACO REACH", "attribution"]},
    "ACPT": {"definition": "ACO Performance Tier. Used in ACO REACH and MSSP for "
             "tiering ACOs by historical performance. Affects benchmarks.",
             "see_also": ["ACO", "MSSP", "REACH"]},
    "MIPS": {"definition": "Merit-based Incentive Payment System. CMS clinician-level "
             "quality program; positive/negative payment adjustment based on score "
             "across quality, cost, improvement-activities, and "
             "promoting-interoperability categories.",
             "see_also": ["MACRA", "QPP", "value-based"]},
    "HEBA": {"definition": "Health Equity Benchmark Adjustment. ACO REACH adjustment "
             "that raises benchmarks for ACOs serving underserved populations.",
             "see_also": ["ACO REACH", "benchmark"]},
    "MAGI": {"definition": "Modified Adjusted Gross Income. ACA + Medicaid income "
             "test (federal AGI + tax-exempt interest + foreign earned income + "
             "non-taxable Social Security). Determines eligibility for premium tax "
             "credits and most non-disabled Medicaid groups.",
             "see_also": ["FPL", "AGI", "APTC"]},
    "FPL": {"definition": "Federal Poverty Level. HHS-published annual income "
            "threshold by household size used to determine Medicaid, CHIP, and ACA "
            "subsidy eligibility.", "see_also": ["MAGI", "Medicaid", "APTC"]},
    "PBM": {"definition": "Pharmacy Benefit Manager. Third-party administrator for "
            "prescription benefits. Negotiates rebates, manages formulary, processes "
            "pharmacy claims through NCPDP.", "see_also": ["formulary", "rebate", "NCPDP"]},
    "DRG": {"definition": "Diagnosis-Related Group. Inpatient case-mix classification "
            "grouping discharges by clinical and resource-use similarity. Used as "
            "the payment unit in IPPS.", "see_also": ["MS-DRG", "APR-DRG", "IPPS"]},
    "PHI": {"definition": "Protected Health Information. Individually identifiable "
            "health information held by a HIPAA-covered entity or business "
            "associate. The 18 Safe Harbor identifiers must be removed for "
            "de-identification.", "see_also": ["HIPAA", "Safe-Harbor", "de-identification"]},
    "HIPAA": {"definition": "Health Insurance Portability and Accountability Act of "
              "1996. Privacy Rule governs PHI use/disclosure; Security Rule sets "
              "safeguards; Transactions Rule mandates X12 EDI standards.",
              "see_also": ["PHI", "HITECH", "BAA"]},
}

# The computational metrics whose ambiguity produces confidently-wrong numbers —
# the subset the `resolve()`-based checker watches. Intersected with the live
# glossary at runtime, so adding a glossary entry is enough to cover it.
_METRIC_TERMS = {"PMPM", "PMPY", "MLR", "RAF", "HCC", "ACPT", "HEBA", "PEPM", "MER"}


def _glossary_path() -> Path:
    try:
        import importlib.resources as ir
        return Path(str(ir.files("medecon_verify") / "data" / "glossary.yaml"))
    except (ModuleNotFoundError, ValueError, TypeError):
        return Path(__file__).resolve().parent / "data" / "glossary.yaml"


# Parse cache: {path -> (mtime, parsed)}. The 245-term glossary is ~1700 lines;
# without this it was re-read + re-parsed on every define/resolve/metric_terms
# call (2+ full parses per metric-terms eval). Keyed by file mtime so an edit
# busts the cache; no wall-clock dependency.
_PARSE_CACHE: dict[str, tuple[float, dict]] = {}


def _load_external() -> dict[str, dict[str, Any]]:
    path = _glossary_path()
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        # PyYAML genuinely absent (skill-only/offline env) → degrade to fallback.
        return {}

    mtime = path.stat().st_mtime
    cached = _PARSE_CACHE.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]

    # A malformed glossary must fail LOUDLY (a YAMLError here), not silently drop
    # ~230 canonical terms to the offline fallback — that would be a confidently
    # wrong /explain with no signal. So we do NOT swallow yaml.YAMLError.
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        result: dict[str, dict[str, Any]] = {}
    else:
        # YAML coerces bare numeric keys (837, 835) to ints; normalize to str.
        result = {str(k): v for k, v in data.items() if isinstance(v, dict)}
    _PARSE_CACHE[str(path)] = (mtime, result)
    return result


def load_glossary() -> dict[str, dict[str, Any]]:
    """Full glossary: canonical YAML merged over the offline fallback."""
    return {**_FALLBACK_GLOSSARY, **_load_external()}


def define(term: str) -> dict[str, Any] | None:
    """Canonical entry for `term` (case-insensitive), or None if unknown."""
    if not term:
        return None
    upper = term.upper().strip()
    for key, val in load_glossary().items():
        if key.upper() == upper:
            return val
    return None


def known_terms() -> set[str]:
    """All glossary keys, upper-cased."""
    return {k.upper() for k in load_glossary()}


def metric_terms() -> set[str]:
    """The computational-metric subset actually present in the glossary."""
    return {t for t in _METRIC_TERMS if t in known_terms()}


def resolve(text: str) -> dict[str, dict[str, Any]]:
    """Return the glossary terms that appear as whole words in `text`.

    Whole-word, case-insensitive match on each glossary key so "PMPM" matches
    but "compmpm" does not. Used to find which defined terms a deliverable uses.
    """
    if not text:
        return {}
    glossary = load_glossary()
    found: dict[str, dict[str, Any]] = {}
    for key, val in glossary.items():
        if not key or not key.replace("-", "").replace(" ", "").isalnum():
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            found[key.upper()] = val
    return found


__all__ = [
    "define", "known_terms", "metric_terms", "resolve", "load_glossary",
]
