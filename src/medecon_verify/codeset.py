"""Code-set version pinning — every deliverable carries the version stamps that produced it.

Without this, year-over-year comparisons silently use mismatched code sets
and produce wrong answers with high confidence. This is a cross-cutting
discipline; every Outputs-layer skill stamps deliverables via this module.

Public contract (pinned):

    stamp(deliverable: dict, *, asof: date | None = None) -> dict
        Add the code-set version stamps that apply to a deliverable.
        Returns the deliverable dict with a `code_set_versions` field.

        v1.1 breaking change: if `deliverable['analysis_period']` indicates
        a backward-looking analysis (end date > 90 days before today) and
        `asof` is not provided, this raises CodesetVersionError. The fix is
        to pass `asof` explicitly (typically `analysis_period['end']`).

    detect_icd10_fy(records: list[dict], date_field: str = "service_date") -> str
        Detect the ICD-10-CM fiscal year vintage from a sample of records.
        Returns "FYxxxx" or "UNKNOWN" if the vintage cannot be determined.

    detect_fy_span(dates) -> list[str]
        Return the sorted list of ICD-10-CM fiscal years touched by a span of
        service dates. A length > 1 means the span crosses an Oct-01 FY boundary.

    vintage_mismatch_warning(dates, *, context=None) -> str
        Return a non-empty hard-warning string naming the two (or more) FYs when
        a service-date span crosses the ICD-10-CM FY boundary, else "".
        There is NO "inline query" carve-out: any blended total spanning a
        boundary must surface this warning before it ships.

    icd10_registry() -> dict
        Returns the full registry of known ICD-10-CM fiscal-year vintages.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

try:  # package import and script-path import both work
    from .dateparse import parse_date as _parse_date
except ImportError:  # pragma: no cover - fallback when run as a bare module
    from dateparse import parse_date as _parse_date  # type: ignore


class CodesetVersionError(ValueError):
    """Raised when a deliverable cannot be safely stamped without explicit asof."""


_BACKWARD_LOOKING_THRESHOLD_DAYS = 90

# ---------------------------------------------------------------------------
# Registries — extend these to add new vintages
# ---------------------------------------------------------------------------

ICD10CM_FY_REGISTRY = {
    "FY2022": {"effective": "2021-10-01", "obsolete": "2022-09-30"},
    "FY2023": {"effective": "2022-10-01", "obsolete": "2023-09-30"},
    "FY2024": {"effective": "2023-10-01", "obsolete": "2024-09-30"},
    "FY2025": {"effective": "2024-10-01", "obsolete": "2025-09-30"},
    "FY2026": {"effective": "2025-10-01", "obsolete": "2026-09-30"},
}

HCPCS_QUARTER_REGISTRY = {
    "2025Q1": {"effective": "2025-01-01", "obsolete": "2025-03-31"},
    "2025Q2": {"effective": "2025-04-01", "obsolete": "2025-06-30"},
    "2025Q3": {"effective": "2025-07-01", "obsolete": "2025-09-30"},
    "2025Q4": {"effective": "2025-10-01", "obsolete": "2025-12-31"},
    "2026Q1": {"effective": "2026-01-01", "obsolete": "2026-03-31"},
    "2026Q2": {"effective": "2026-04-01", "obsolete": "2026-06-30"},
}

MS_DRG_REGISTRY = {
    "v40": {"fy": "FY2023", "effective": "2022-10-01"},
    "v41": {"fy": "FY2024", "effective": "2023-10-01"},
    "v42": {"fy": "FY2025", "effective": "2024-10-01"},
    "v43": {"fy": "FY2026", "effective": "2025-10-01"},
}

CMS_HCC_REGISTRY = {
    "v24": {"applies_to": "PY2017–PY2023", "blend_to_v28": False},
    "v28": {"applies_to": "PY2024+", "blend_to_v28": True,
            "phase_in": {"PY2024": 0.33, "PY2025": 0.67, "PY2026": 1.0}},
}

HEDIS_REGISTRY = {
    "MY2024": {"reporting_year": 2025},
    "MY2025": {"reporting_year": 2026},
    "MY2026": {"reporting_year": 2027},
}

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_icd10_fy(records: list[dict], date_field: str = "service_date") -> str:
    """Detect the ICD-10-CM fiscal-year vintage from a sample of service dates.

    Picks the dominant FY across the records. Returns 'UNKNOWN' if no parseable
    dates or multiple FYs spanning a transition (>5% in a different FY).
    """
    if not records:
        return "UNKNOWN"

    fy_counts: dict[str, int] = {}
    parsed = 0
    for rec in records:
        d = _parse_date(rec.get(date_field))
        if d is None:
            continue
        parsed += 1
        for fy, bounds in ICD10CM_FY_REGISTRY.items():
            eff = _parse_date(bounds["effective"])
            obs = _parse_date(bounds["obsolete"])
            if eff and obs and eff <= d <= obs:
                fy_counts[fy] = fy_counts.get(fy, 0) + 1
                break

    if parsed == 0 or not fy_counts:
        return "UNKNOWN"

    dominant_fy = max(fy_counts, key=lambda k: fy_counts[k])
    dominant_share = fy_counts[dominant_fy] / parsed
    if dominant_share < 0.95:
        # Cross-FY data — caller should be warned and stamp explicitly
        return f"MIXED({','.join(sorted(fy_counts.keys()))})"
    return dominant_fy


def _fy_for_date(d: date) -> str | None:
    """Return the ICD-10-CM fiscal-year label containing date `d`, or None.

    The FY boundary is Oct 01: FY{Y+1} runs from {Y}-10-01 through {Y+1}-09-30.
    Computed from the boundary rather than the registry so dates outside the
    registry's explicit window still resolve to the correct FY.
    """
    if d is None:
        return None
    fy = d.year + 1 if (d.month, d.day) >= (10, 1) else d.year
    return f"FY{fy}"


def _coerce_dates(dates) -> list[date]:
    """Parse an iterable of date-likes (str / date / None) into a list of dates.

    Accepts the same inputs as the rest of the module: ISO strings, `date`
    objects, or anything `dateparse.parse_date` understands. Unparseable
    or empty entries are dropped.
    """
    out: list[date] = []
    for item in dates:
        if isinstance(item, date):
            # datetime (and pandas.Timestamp, a datetime subclass) are also
            # `date` instances. Normalize them to a plain `date` so a span that
            # mixes ISO strings (parsed to date), date objects, and datetime/
            # Timestamp objects can be sorted together — `sorted()` over a mix
            # of date and datetime raises TypeError.
            out.append(item.date() if isinstance(item, datetime) else item)
            continue
        d = _parse_date(item)
        if d is not None:
            out.append(d)
    return out


def detect_fy_span(dates) -> list[str]:
    """Return the sorted ICD-10-CM fiscal years touched by a span of dates.

    `dates` is any iterable of service dates (ISO strings or `date` objects).
    A return length > 1 means the span crosses at least one Oct-01 ICD-10-CM
    fiscal-year boundary — blending totals across it mixes code-set vintages.

    Returns an empty list if no dates parse.
    """
    parsed = _coerce_dates(dates)
    fys = {_fy_for_date(d) for d in parsed}
    fys.discard(None)
    return sorted(fys)


def vintage_mismatch_warning(dates, *, context: str | None = None) -> str:
    """Hard-warning string when a date span crosses an ICD-10-CM FY boundary.

    Returns a non-empty warning naming the fiscal years involved when the span
    of `dates` straddles an Oct-01 boundary (e.g. service dates 2024-09 …
    2025-02 touch both FY2024 and FY2025). Returns "" when all dates fall in a
    single FY (or none parse) — i.e. no mismatch.

    There is deliberately NO "inline query" carve-out: any blended total whose
    service-date span crosses a boundary must surface this before it ships. The
    optional `context` (e.g. "blended paid total") is folded into the message.
    """
    fys = detect_fy_span(dates)
    if len(fys) < 2:
        return ""
    parsed = sorted(_coerce_dates(dates))
    span = ""
    if parsed:
        span = f" (service dates {parsed[0].isoformat()} … {parsed[-1].isoformat()})"
    what = f"{context} " if context else ""
    fy_list = "→".join(fys)
    return (
        f"VINTAGE MISMATCH (hard warning): {what}spans {len(fys)} ICD-10-CM "
        f"fiscal years {fy_list}{span}, crossing the Oct-01 FY boundary. "
        f"Diagnosis/grouper code sets differ across {fy_list}; a blended total "
        "mixes vintages. Stamp both vintages and split the total by FY, or state "
        "explicitly that the blend is intended. No inline-query carve-out applies."
    )


def _analysis_period_end(deliverable: dict) -> date | None:
    """Return the end date of `deliverable['analysis_period']`, if any.

    Accepts:
      {"start": "...", "end": "2024-12-31"}     — explicit dates
      "PY2024"                                   — payment year string
      "2024-Q4"                                  — calendar-quarter string
      "FY2024"                                   — fiscal year (Oct–Sept)
    Returns None if no analysis_period is set or the format is unrecognized.
    """
    period = deliverable.get("analysis_period")
    if period is None:
        return None
    if isinstance(period, dict):
        end_val = period.get("end")
        return _parse_date(end_val)
    if isinstance(period, str):
        s = period.strip()
        if s.startswith("PY") and s[2:].isdigit():
            return date(int(s[2:]), 12, 31)
        if s.startswith("FY") and s[2:].isdigit():
            return date(int(s[2:]), 9, 30)
        if "-Q" in s:
            try:
                year_str, q_str = s.split("-Q", 1)
                year = int(year_str)
                quarter = int(q_str)
                end_month = quarter * 3
                if end_month == 12:
                    return date(year, 12, 31)
                last_day = (date(year, end_month + 1, 1) - timedelta(days=1)).day
                return date(year, end_month, last_day)
            except (ValueError, IndexError):
                return None
    return None


def stamp(deliverable: dict, *, asof: date | None = None) -> dict:
    """Add code-set version stamps to a deliverable.

    The stamps are based on `asof`. If `asof` is None, today is used unless
    the deliverable carries an `analysis_period` whose end is more than
    90 days in the past — in which case CodesetVersionError is raised so the
    caller doesn't silently stamp old data with a current vintage.

    Callers can override individual stamps by setting them in
    `deliverable['code_set_versions']` before calling stamp.
    """
    if asof is None:
        period_end = _analysis_period_end(deliverable)
        if period_end is not None:
            today = date.today()
            if (today - period_end).days > _BACKWARD_LOOKING_THRESHOLD_DAYS:
                raise CodesetVersionError(
                    f"deliverable analysis_period ends {period_end.isoformat()}, "
                    f"more than {_BACKWARD_LOOKING_THRESHOLD_DAYS} days before "
                    f"today ({today.isoformat()}). Pass asof explicitly "
                    "(typically asof=analysis_period['end']) so the deliverable "
                    "is not stamped with a current code-set vintage that did "
                    "not apply during the analysis window."
                )
            asof = period_end
        else:
            asof = date.today()
    existing = deliverable.get("code_set_versions", {})

    stamps = {
        "icd10cm_fy": existing.get("icd10cm_fy")
            or _vintage_for(asof, ICD10CM_FY_REGISTRY) or "UNKNOWN",
        "hcpcs_quarter": existing.get("hcpcs_quarter")
            or _vintage_for(asof, HCPCS_QUARTER_REGISTRY) or "UNKNOWN",
        "ms_drg": existing.get("ms_drg")
            or _vintage_for_drg(asof) or "UNKNOWN",
        "cms_hcc": existing.get("cms_hcc")
            or _vintage_for_hcc(asof.year) or "UNKNOWN",
        "hedis_my": existing.get("hedis_my")
            or _vintage_for_hedis(asof.year) or "UNKNOWN",
        "stamped_at": asof.isoformat(),
    }
    deliverable["code_set_versions"] = stamps
    return deliverable


def _vintage_for(d: date, registry: dict) -> str | None:
    for vintage, bounds in registry.items():
        eff = _parse_date(bounds["effective"])
        obs = _parse_date(bounds["obsolete"])
        if eff and obs and eff <= d <= obs:
            return vintage
    return None


def _vintage_for_drg(d: date) -> str | None:
    fy = _vintage_for(d, ICD10CM_FY_REGISTRY)
    if fy is None:
        return None
    for v, info in MS_DRG_REGISTRY.items():
        if info["fy"] == fy:
            return v
    return None


def _vintage_for_hcc(year: int) -> str | None:
    if year >= 2024:
        return "v28"
    return "v24"


def _vintage_for_hedis(year: int) -> str | None:
    # MY{Year-1} reports during {Year}; MY{Year} starts at year-end
    return f"MY{year - 1}"


def icd10_registry() -> dict:
    """Return the full ICD-10-CM fiscal-year registry."""
    return dict(ICD10CM_FY_REGISTRY)


__all__ = [
    "stamp",
    "detect_icd10_fy",
    "detect_fy_span",
    "vintage_mismatch_warning",
    "icd10_registry",
    "CodesetVersionError",
    "ICD10CM_FY_REGISTRY",
    "HCPCS_QUARTER_REGISTRY",
    "MS_DRG_REGISTRY",
    "CMS_HCC_REGISTRY",
    "HEDIS_REGISTRY",
]
