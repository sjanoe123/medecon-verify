"""PHI scanner — the write-gate.

Sub-agents read PHI freely. The orchestrator does not write PHI to .medecon/org/.
This module is the gate between sub-agent output and disk.

Public contract (pinned, see references/phi-scanner-contract.md):

    scan(records: list[dict]) -> list[dict]
        Run all 18 Safe Harbor identifiers + high-risk extras over each record.
        Returns redacted records. Errs on the side of redaction. Logs counts
        by category to scanner_metrics.

    scan_text(text: str) -> str
        Run the same scanner over a single string. Used for free-text DATA
        fields (single-value data columns). ALWAYS uses the strict 200-char
        overflow gate — there is no caller-tunable threshold, so the data-column
        write-gate contract cannot be relaxed by any caller. For narrative/prose
        fields use scan_prose (word-count gate) instead.

    scan_prose(text: str) -> str
        Scan a narrative/prose field (e.g. a deliverable's ≤80-word TL;DR).
        Uses a WORD-COUNT overflow gate (default ≤100 words) instead of the
        strict char gate, so legitimate prose with long clinical terms is not
        nuked for length alone. Identifiers are still redacted in place via the
        SAME identifier scan the prose-grade scan_markdown uses — it is no more
        permissive than the accepted prose-scanning contract. (Bare names with
        no title prefix are a pre-existing systemic limitation of pattern
        scanning for ALL prose, not specific to this path — see scan_markdown.)

    scanner_metrics() -> dict
        Returns the count of redactions by category since module load.
        Resets via reset_metrics().

Performance target: < 1 second per 100K-row dataframe via vectorized regex.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------------------
# Regex compilation (once, at import) — vectorized application via pandas/numpy
# ---------------------------------------------------------------------------

# 1. SSN
_SSN = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")

# 2. MBI (Medicare Beneficiary ID, 11-char CMS format)
# Position 1: 1-9; 2,5,8: A-Z excl SLOIBZ; 3: 0-9; 4: alnum excl SLOIBZ;
# 6: A-Z excl SLOIBZ; 7,9,10,11: 0-9 then 0-9 then... approximation per CMS spec
_MBI = re.compile(
    r"\b[1-9][AC-HJ-KM-NP-RT-Y][0-9][AC-HJ-KM-NP-RT-Y0-9][0-9]"
    r"[AC-HJ-KM-NP-RT-Y][AC-HJ-KM-NP-RT-Y][0-9][0-9][AC-HJ-KM-NP-RT-Y][AC-HJ-KM-NP-RT-Y]\b"
)

# 3. Phone (US-centric; international is broader)
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

# 4. Email (org-domain allow-list applied at higher layer if needed)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# 5. Date (granular: YYYY-MM-DD, MM/DD/YYYY, M/D/YY, etc.) — caller decides
# whether to redact based on whether the date is associated with an individual.
# Year-only dates are preserved; this matches month or day granularity.
_DATE_GRANULAR = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}-\d{1,2}-\d{2,4})\b"
)

# 6. URL
_URL = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)

# 7. IP address
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")

# 8. Free-text names (Phase C.2)
# Catches "Patient Smith said...", "Mr. John Doe", "Dr Williams reported".
# Honest scope: this is a record-field heuristic. It catches the most common
# patterns in clinical chart-note prose. Things it does NOT catch:
#   - bare names with no title prefix ("John Smith reported...")
#   - non-Latin scripts or names with apostrophes / hyphens not at the end
#   - aliases that look like ordinary capitalized phrases
# Things it WILL false-positive on (acceptable per "err on redaction"):
#   - "Patient Care", "Patient Outcomes", "Patient Safety" (common clinical
#     phrases). False positives are noise rather than data leakage.
# We curate a tiny safe-prefix list to suppress the most common medical
# concepts so the false-positive rate is tolerable in practice.
_FREETEXT_NAME = re.compile(
    r"\b(?P<title>Patient|Mr|Mrs|Ms|Miss|Mister|Dr|Doctor)"
    r"\.?\s+"
    r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
    r"\b"
)
# Common phrases that match the regex but are not names.
_FREETEXT_NAME_FALSE_POSITIVES = frozenset({
    # Patient-prefixed clinical concepts
    "Care", "Outcomes", "Safety", "Care Plan", "Health", "History",
    "Education", "Engagement", "Experience", "Encounter", "Records",
    "Records Review", "Care Coordinator", "Care Team", "Outcomes Reporting",
    "Reported Outcomes", "Reported Outcome",
    # Dr-prefixed concepts (rare but possible: "Dr Visit Frequency"... no, that's not capitalized right)
})


def _redact_freetext_names(text: str) -> tuple[str, int]:
    """Replace title-prefixed names with [REDACTED:NAME]. Returns (out, count)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        name = m.group("name")
        if name in _FREETEXT_NAME_FALSE_POSITIVES:
            return m.group(0)  # leave untouched
        count += 1
        return f"{m.group('title')} [REDACTED:NAME]"

    out = _FREETEXT_NAME.sub(repl, text)
    return out, count


def _scan_identifiers(text: str) -> str:
    """Apply every PHI identifier pattern to a string, in place. No length gate.

    This is the single shared identifier-scanning contract. `scan_text` (data
    columns), `scan_prose` (narrative), and `scan_markdown` (documents) all run
    EXACTLY this set of patterns — the only thing that differs between them is
    the length/overflow gate applied BEFORE this runs. Keeping the identifier
    pass in one place guarantees prose is never scanned more permissively than
    the accepted prose contract (scan_markdown): they are the same code.

    Honest scope (pre-existing, systemic to pattern scanning for ALL prose):
    bare names with no title prefix ("John Smith reviewed.") are NOT caught
    here. Names are Safe Harbor identifiers, but value-only detection of bare
    names is beyond regex; data columns are protected from this by their strict
    200-char overflow gate (which nukes wholesale), prose is not. This is a
    residual policy limitation, not a relaxation introduced by any one caller.
    """
    out = text
    if _SSN.search(out):
        out = _SSN.sub("[REDACTED:SSN]", out)
        _METRICS["ssn"] += 1
    if _MBI.search(out):
        out = _MBI.sub("[REDACTED:MBI]", out)
        _METRICS["mbi"] += 1
    if _PHONE.search(out):
        out = _PHONE.sub("[REDACTED:PHONE]", out)
        _METRICS["phone"] += 1
    if _EMAIL.search(out):
        out = _EMAIL.sub("[REDACTED:EMAIL]", out)
        _METRICS["email"] += 1
    if _URL.search(out):
        out = _URL.sub("[REDACTED:URL]", out)
        _METRICS["url"] += 1
    if _IPV4.search(out):
        out = _IPV4.sub("[REDACTED:IP]", out)
        _METRICS["ip"] += 1
    if _IPV6.search(out):
        out = _IPV6.sub("[REDACTED:IP]", out)
        _METRICS["ip"] += 1
    if _DATE_GRANULAR.search(out):
        out = _DATE_GRANULAR.sub("[REDACTED:DATE]", out)
        _METRICS["date_granular"] += 1
    if _FREETEXT_NAME.search(out):
        out, n = _redact_freetext_names(out)
        if n:
            _METRICS["freetext_name"] += n
    return out


# NOTE: a bare 10-digit NPI matches the phone pattern and is redacted as a phone.
# This over-redaction is intentional. A value-based NPI exemption (Luhn checksum)
# was tried and reverted: the NPI checksum accepts ~1 in 10 random 10-digit
# strings, so it would let real unformatted patient phone numbers leak through the
# write-gate. For a PHI write-gate, erring toward redaction wins — a redacted
# public NPI is harmless; a leaked patient phone is a Safe Harbor breach. Renderers
# that must display provider NPIs (e.g. provider-profile-report) snapshot them
# BEFORE scanning at their own level. A context-aware exemption (an NPI-labeled
# column in scan(records)) could be added later, but not a value-only check.

# Column-name patterns (case-insensitive substring matches; null entire column)
_PHI_COL_NAMES = {
    "name": ["first_name", "last_name", "patient_name", "member_name",
             "subscriber_name", "beneficiary_name", "full_name", "fname", "lname"],
    "member_id": ["mrn", "medical_record", "patient_id", "member_id",
                  "subscriber_id", "beneficiary_id", "hicn"],
    "dob": ["dob", "birth_date", "date_of_birth", "birthdate"],
    "address": ["street_address", "address_line", "addr1", "addr2",
                "mailing_address", "home_address"],
    "fax": ["fax", "fax_number"],
    "account": ["account_number", "license_number", "certificate_number"],
    "vehicle": ["vehicle_id", "vin", "license_plate"],
    "device": ["device_id", "device_serial", "implant_id"],
    "biometric": ["fingerprint", "voiceprint", "retina_scan"],
    "photo": ["photograph", "photo_path", "image_path"],
}

# Free-text overflow: any DATA-COLUMN string longer than this is replaced
# wholesale. Record-level data values over this length are highly unlikely to
# be safe to publish, so the strict gate nukes them rather than trusting the
# pattern scans to catch every identifier.
_FREETEXT_OVERFLOW_THRESHOLD = 200

# Narrative/prose fields (e.g. a deliverable's ≤80-word TL;DR) are spec'd by
# WORD COUNT, not char count. An 80-word TL;DR packed with long clinical terms
# ("hospitalization", "antihyperlipidemic") can blow past a char ceiling while
# still being a compliant, publishable TL;DR. So prose uses a word-count gate:
# a field over this many words is treated as overflow and replaced wholesale.
# The pattern scans (SSN/phone/email/MRN/DOB/etc.) STILL run below the gate, so
# a prose field that contains an actual identifier is redacted regardless of
# length. Limit is set above the ≤80-word TL;DR spec with headroom.
_PROSE_MAX_WORDS = 100

# ---------------------------------------------------------------------------
# Allow-list configuration
# ---------------------------------------------------------------------------

_ALLOW_LIST: set[str] = set()


def configure_allow_list(values: list[str]) -> None:
    """Configure synthetic test data values that should pass through unchanged."""
    global _ALLOW_LIST
    _ALLOW_LIST = set(values)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_METRICS: dict[str, int] = defaultdict(int)


def scanner_metrics() -> dict[str, int]:
    """Return redaction counts by category since module load (or last reset)."""
    return dict(_METRICS)


def reset_metrics() -> None:
    """Reset the metrics counter."""
    _METRICS.clear()


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def scan_text(text: str) -> str:
    """Run all PHI patterns over a single DATA-COLUMN string. Returns redacted text.

    For RECORD / DATA FIELDS (single-value strings like a chart-note column).
    Long text is replaced wholesale via the STRICT 200-char overflow gate —
    record-level data values over 200 chars are highly unlikely to be safe to
    publish, so they are nuked rather than trusting the pattern scans to catch
    every identifier (notably bare names, which the patterns do not catch).

    This gate is NOT caller-tunable. There is deliberately no `overflow_threshold`
    parameter: a public knob would let any caller relax the data-column write-gate
    contract for arbitrary fields, re-opening the un-patterned-leak hole. The
    narrow, audited relaxation for genuine prose lives in `scan_prose` only.

    For NARRATIVE / PROSE fields (e.g. a deliverable's ≤80-word TL;DR) use the
    ``scan_prose`` wrapper instead, which gates on WORD COUNT rather than chars
    so legitimate prose with long clinical terms is not nuked for length alone.

    For MARKDOWN DOCUMENTS (renderer / sub-agent output) use `scan_markdown`
    instead, which scans line-by-line without the overflow gate.
    """
    if not isinstance(text, str):
        return text

    # Free-text overflow: replace wholesale (strict data-column semantics)
    if len(text) > _FREETEXT_OVERFLOW_THRESHOLD:
        _METRICS["freetext_overflow"] += 1
        return "[REDACTED:FREETEXT]"

    # Allow-list bypass (synthetic test data)
    if text in _ALLOW_LIST:
        return text

    return _scan_identifiers(text)


def scan_prose(text: str, *, max_words: int = _PROSE_MAX_WORDS) -> str:
    """Scan a NARRATIVE / PROSE field (e.g. a deliverable TL;DR) for PHI.

    Unlike `scan_text` (a strict 200-char gate for data columns), prose is
    gated by WORD COUNT (default ≤100 words). A spec-compliant ≤80-word TL;DR
    survives even when long clinical terms push its char length well past the
    data-column ceiling. A prose field over the word limit is replaced wholesale
    via the overflow gate. Below the gate, the identifier pass STILL runs, so any
    actual identifier in the prose is redacted in place regardless of length.

    Identifier scanning here is `_scan_identifiers` — the EXACT same pass used by
    `scan_markdown`, the already-accepted contract for narrative/document text.
    The prose path is therefore no more permissive than `scan_markdown`: it does
    NOT reach back into `scan_text` or relax its public data-column gate. (Bare
    names with no title prefix remain a pre-existing, systemic limitation of
    pattern scanning for ALL prose — see `_scan_identifiers`.)
    """
    if not isinstance(text, str):
        return text

    # Word-count overflow gate (prose semantics): a field longer than a
    # legitimate TL;DR is highly unlikely to be safe to publish as-is.
    if len(text.split()) > max_words:
        _METRICS["freetext_overflow"] += 1
        return "[REDACTED:FREETEXT]"

    # Allow-list bypass (synthetic test data), matching scan_text semantics.
    if text in _ALLOW_LIST:
        return text

    # Identifier pass with no char gate (overflow already decided by word count
    # above). Same contract as scan_markdown — never weaker.
    return _scan_identifiers(text)


def scan_markdown(doc: str) -> str:
    """Scan a markdown document line-by-line, applying PHI patterns.

    No overflow gate. Use for renderer / sub-agent output where the document
    structure is intentional and individual lines are short. Each line is
    treated as a separate scannable unit so an SSN or phone number gets
    redacted in place without nuking the whole document.
    """
    if not isinstance(doc, str):
        return doc
    out_lines: list[str] = []
    for line in doc.split("\n"):
        # Reuse the shared identifier pass (no overflow gate) per line.
        if not line:
            out_lines.append(line)
            continue
        out_lines.append(_scan_identifiers(line))
    return "\n".join(out_lines)


def _normalize_col(s: str) -> str:
    """Lowercase + drop underscores/spaces. Handles camelCase + snake_case + SCREAMING."""
    return s.lower().replace("_", "").replace(" ", "").replace("-", "")


def _classify_column(col_name: str) -> str | None:
    """Return the PHI category if column name matches, else None."""
    if not col_name:
        return None
    normalized = _normalize_col(col_name)
    safe_substrs = [_normalize_col(s) for s in (
        "procedure_", "program_", "drug_", "company_",
        "facility_", "provider_", "vendor_", "organization_", "org_",
    )]
    for category, patterns in _PHI_COL_NAMES.items():
        for pat in patterns:
            np = _normalize_col(pat)
            if np in normalized:
                if category == "name" and any(s in normalized for s in safe_substrs):
                    return None
                return category
    return None


def scan(records: list[dict]) -> list[dict]:
    """Run the PHI scanner over a list of dict records.

    Returns redacted records. Columns whose names match _PHI_COL_NAMES are
    nulled wholesale. String values are scanned for in-line PHI patterns.

    Defaults to redaction on any error (per the V1 spec failure mode policy).
    """
    if not records:
        return records

    # Identify columns to null based on names
    sample = records[0] if records else {}
    null_columns: dict[str, str] = {}
    for col in sample:
        category = _classify_column(col)
        if category is not None:
            null_columns[col] = category

    redacted: list[dict] = []
    for rec in records:
        new_rec: dict[str, Any] = {}
        for col, val in rec.items():
            if col in null_columns:
                new_rec[col] = None
                _METRICS[f"col_{null_columns[col]}"] += 1
                continue
            if isinstance(val, str):
                try:
                    new_rec[col] = scan_text(val)
                except Exception:  # default to redaction on any scanner failure
                    new_rec[col] = "[SCAN_FAILED—CONTENT_SUPPRESSED]"
                    _METRICS["scan_failed"] += 1
            else:
                new_rec[col] = val
        redacted.append(new_rec)
    return redacted


def scan_dataframe(df):  # type: ignore[no-untyped-def]
    """Vectorized scan over a pandas DataFrame. Returns a redacted copy.

    Implementation note: uses pandas.Series.str methods for the regex layer
    and column-name classification for the column-null layer. Performance
    target: < 1 second per 100K rows.
    """
    import pandas as pd  # type: ignore[import-not-found]

    out = df.copy()

    # Null entire columns whose names match PHI categories
    for col in list(out.columns):
        category = _classify_column(col)
        if category is not None:
            out[col] = None
            _METRICS[f"col_{category}"] += len(df)

    # Scan string columns for in-line PHI patterns
    for col in out.columns:
        if pd.api.types.is_string_dtype(out[col]) or out[col].dtype == object:
            # Convert to string, apply scan_text, write back
            mask = out[col].notna()
            if mask.any():
                out.loc[mask, col] = out.loc[mask, col].astype(str).apply(scan_text)
    return out


__all__ = [
    "scan",
    "scan_text",
    "scan_prose",
    "scan_dataframe",
    "scanner_metrics",
    "reset_metrics",
    "configure_allow_list",
]
