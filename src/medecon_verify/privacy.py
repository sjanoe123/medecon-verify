"""Privacy guardrails — runs on every deliverable before it ships.

HIPAA Safe Harbor 18-identifier check (delegated to phi_scanner), n≤10 cell
suppression, age 90+ aggregation, 42 CFR Part 2 SUD redisclosure flag.

This module is the public contract every Outputs-layer skill calls before
writing a deliverable to disk or to a stakeholder-facing surface.

Public contract (pinned):

    apply(deliverable: dict, *, suppress_below: int = 11) -> dict
        Run all guardrails over a deliverable. Returns the deliverable with
        a `privacy_audit` section appended.

    suppress_small_cells(values: list[int], threshold: int = 11) -> list
        Return a parallel list with values < threshold replaced by '*'.
        threshold=11 means n≤10 is suppressed (the standard small-cell rule).

    aggregate_age_90_plus(records, *, age_field) -> list
        Replace age values ≥ 90 with the string "90+" (HIPAA Safe Harbor).

    flag_part_2_redisclosure(deliverable: dict) -> dict
        Detect and flag 42 CFR Part 2 SUD content. Adds a flag to the
        deliverable's privacy_audit section.
"""
from __future__ import annotations

from typing import Any

from . import phi as phi_scanner

# 42 CFR Part 2 SUD-related keywords (very conservative match)
_PART_2_KEYWORDS = (
    "substance use disorder",
    "substance abuse",
    "alcohol abuse",
    "drug abuse",
    "addiction treatment",
    "methadone",
    "buprenorphine",
    "naltrexone",
    "opioid use disorder",
    "alcohol use disorder",
    "sud treatment",
    "rehab",
    "detox",
)


def suppress_small_cells(values: list[Any], threshold: int = 11) -> list[Any]:
    """Replace counts below threshold with '*'. threshold=11 means n≤10 suppressed."""
    out: list[Any] = []
    for v in values:
        try:
            if isinstance(v, str):
                out.append(v)
                continue
            n = int(v)
            out.append(n if n >= threshold else "*")
        except (TypeError, ValueError):
            out.append(v)
    return out


def aggregate_age_90_plus(records: list[dict], *, age_field: str) -> list[dict]:
    """Replace age values ≥ 90 with '90+'. Mutates copies, returns new list."""
    out: list[dict] = []
    for r in records:
        new_r = dict(r)
        a = new_r.get(age_field)
        try:
            if a is not None and int(a) >= 90:
                new_r[age_field] = "90+"
        except (TypeError, ValueError):
            pass
        out.append(new_r)
    return out


def flag_part_2_redisclosure(deliverable: dict) -> dict:
    """Detect and flag 42 CFR Part 2 SUD content in a deliverable."""
    text_blob = _walk_text(deliverable).lower()
    matched = [k for k in _PART_2_KEYWORDS if k in text_blob]
    audit = deliverable.setdefault("privacy_audit", {})
    audit["part_2_flag"] = bool(matched)
    audit["part_2_keywords"] = matched
    if matched:
        audit["part_2_warning"] = (
            "42 CFR Part 2 substance-use-disorder content detected. Verify "
            "redisclosure consent or aggregation before sharing externally."
        )
    return deliverable


def _walk_text(obj: Any) -> str:
    """Concatenate all string values in a nested structure."""
    if isinstance(obj, str):
        return obj + " "
    if isinstance(obj, dict):
        return "".join(_walk_text(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return "".join(_walk_text(v) for v in obj)
    return ""


def apply(deliverable: dict, *, suppress_below: int = 11) -> dict:
    """Run every guardrail. Returns the deliverable with a `privacy_audit` section.

    Steps:
    1. Apply n≤10 suppression to any list-of-counts in the deliverable
       (heuristic: keys ending in `_counts` or `_n`).
    2. Detect 42 CFR Part 2 keywords.
    3. Run the PHI scanner over all string values to confirm no Safe Harbor
       identifiers leaked through.
    4. Stamp the audit metadata.
    """
    audit = deliverable.setdefault("privacy_audit", {})

    # Step 1: small-cell suppression
    suppressed_keys: list[str] = []
    for key in list(deliverable.keys()):
        if (key.endswith("_counts") or key.endswith("_n")) and isinstance(
            deliverable[key], list
        ):
            deliverable[key] = suppress_small_cells(deliverable[key], suppress_below)
            suppressed_keys.append(key)
    audit["small_cell_suppression_threshold"] = suppress_below
    audit["small_cell_suppressed_keys"] = suppressed_keys

    # Step 2: Part 2 detection
    flag_part_2_redisclosure(deliverable)

    # Step 3: PHI scan over all string values
    phi_scanner.reset_metrics()
    _scan_in_place(deliverable)
    audit["phi_scan_metrics"] = phi_scanner.scanner_metrics()

    # Step 4: stamp
    audit["guardrails_applied"] = [
        "suppress_small_cells",
        "flag_part_2_redisclosure",
        "phi_scan",
    ]
    return deliverable


# Metadata subtrees that are produced by guardrails / stamps themselves and
# never carry PHI. Skipping them avoids redacting the very vintage strings
# that the deliverable needs to publish.
_METADATA_KEYS_SKIP = {"code_set_versions", "privacy_audit"}

# Genuinely-narrative deliverable field(s). A TL;DR is spec'd by word count
# (≤80 words) and can run longer than the strict 200-char data-column gate
# while still being compliant prose, so it gets the word-count prose gate via
# `scan_prose`. The SSN/phone/email/MRN/DOB pattern scans STILL run on it, so
# any structured identifier is redacted regardless of length.
#
# This set is deliberately NARROW. Every OTHER field (title, finding, action,
# summary, etc.) keeps the strict char gate: a long value in those fields is
# wholesale-redacted, which is the only protection against an un-patterned leak
# (e.g. a bare name like "John Smith", which the pattern scanner does not catch)
# riding along in an over-length value. Widening this set would re-open that
# hole — do not add a field here unless it is, like a TL;DR, authored prose
# whose length is governed by an explicit word-count spec.
_PROSE_KEYS = {"tldr"}


def _scan_in_place(obj: Any) -> Any:
    """Walk a nested dict/list and PHI-scan every string value in place."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in _METADATA_KEYS_SKIP:
                continue
            if isinstance(v, str):
                if k in _PROSE_KEYS:
                    obj[k] = phi_scanner.scan_prose(v)
                else:
                    obj[k] = phi_scanner.scan_text(v)
            else:
                _scan_in_place(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = phi_scanner.scan_text(v)
            else:
                _scan_in_place(v)
    return obj


__all__ = [
    "apply",
    "suppress_small_cells",
    "aggregate_age_90_plus",
    "flag_part_2_redisclosure",
]
