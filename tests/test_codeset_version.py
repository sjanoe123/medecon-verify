"""Tests for utils.codeset_version."""
from __future__ import annotations

from datetime import date, datetime

from medecon_verify import codeset as cv


class TestStamp:
    def test_stamp_adds_versions(self) -> None:
        d: dict = {}
        out = cv.stamp(d, asof=date(2026, 5, 1))
        assert "code_set_versions" in out
        assert out["code_set_versions"]["stamped_at"] == "2026-05-01"
        assert out["code_set_versions"]["icd10cm_fy"] == "FY2026"
        assert out["code_set_versions"]["cms_hcc"] == "v28"

    def test_stamp_preserves_existing_overrides(self) -> None:
        d = {"code_set_versions": {"icd10cm_fy": "FY2024"}}
        out = cv.stamp(d, asof=date(2026, 5, 1))
        assert out["code_set_versions"]["icd10cm_fy"] == "FY2024"


class TestDetectIcd10Fy:
    def test_detects_dominant_fy(self) -> None:
        records = [
            {"service_date": "2025-11-15"},
            {"service_date": "2026-01-10"},
            {"service_date": "2026-03-22"},
        ]
        # All within FY2026 (Oct 2025–Sep 2026)
        result = cv.detect_icd10_fy(records)
        assert result == "FY2026"

    def test_returns_unknown_for_no_dates(self) -> None:
        assert cv.detect_icd10_fy([]) == "UNKNOWN"
        assert cv.detect_icd10_fy([{"x": "y"}]) == "UNKNOWN"

    def test_detects_mixed_when_crossing_fy_boundary(self) -> None:
        records = [
            {"service_date": "2024-09-15"},  # FY2024
            {"service_date": "2024-10-15"},  # FY2025
        ]
        result = cv.detect_icd10_fy(records)
        assert result.startswith("MIXED")
        assert "FY2024" in result
        assert "FY2025" in result


class TestDetectFySpan:
    def test_single_fy_returns_one(self) -> None:
        # All within FY2025 (Oct 2024 – Sep 2025)
        assert cv.detect_fy_span(["2024-10-01", "2025-01-15", "2025-09-30"]) == ["FY2025"]

    def test_span_crossing_boundary_returns_both(self) -> None:
        # 2024-09-30 is FY2024; 2024-10-01 is FY2025
        assert cv.detect_fy_span(["2024-09-30", "2024-10-01"]) == ["FY2024", "FY2025"]

    def test_boundary_is_oct_01(self) -> None:
        assert cv._fy_for_date(date(2024, 9, 30)) == "FY2024"
        assert cv._fy_for_date(date(2024, 10, 1)) == "FY2025"

    def test_accepts_date_objects(self) -> None:
        span = cv.detect_fy_span([date(2024, 9, 2), date(2025, 2, 28)])
        assert span == ["FY2024", "FY2025"]

    def test_empty_and_unparseable_return_empty(self) -> None:
        assert cv.detect_fy_span([]) == []
        assert cv.detect_fy_span([None, "", "not-a-date"]) == []

    def test_outside_registry_window_still_resolves(self) -> None:
        # Dates beyond the explicit registry rows must still resolve by boundary.
        assert cv.detect_fy_span(["2030-11-01"]) == ["FY2031"]

    def test_mixed_string_date_datetime_does_not_crash(self) -> None:
        # A real claims/profiler call can mix an ISO string (parsed to date), a
        # bare date object, and a datetime/Timestamp (e.g. a parsed Excel/CSV
        # date column). datetime is a `date` subclass, so an un-normalized span
        # of [date, datetime] crashes `sorted()` with TypeError. Normalizing
        # datetimes to plain dates keeps the span sortable.
        span = cv.detect_fy_span(
            ["2024-09-02", date(2024, 12, 31), datetime(2025, 2, 28, 12, 0)]
        )
        assert span == ["FY2024", "FY2025"]


class TestVintageMismatchWarning:
    def test_no_warning_within_single_fy(self) -> None:
        assert cv.vintage_mismatch_warning(["2025-01-01", "2025-06-30"]) == ""

    def test_no_warning_when_no_dates(self) -> None:
        assert cv.vintage_mismatch_warning([]) == ""
        assert cv.vintage_mismatch_warning([None, "bad"]) == ""

    def test_mock_claims_service_date_range_warns_fy2024_to_fy2025(self) -> None:
        # The mock-claims fixture spans service dates 2024-09-02 … 2025-02-28,
        # which crosses the Oct-01 ICD-10-CM FY boundary (VINT-1 acceptance case).
        warning = cv.vintage_mismatch_warning(
            ["2024-09-02", "2025-02-28"], context="blended paid total",
        )
        assert warning != ""
        assert "FY2024" in warning
        assert "FY2025" in warning
        assert "FY2024→FY2025" in warning

    def test_warning_names_the_span(self) -> None:
        warning = cv.vintage_mismatch_warning(["2024-09-02", "2025-02-28"])
        assert "2024-09-02" in warning
        assert "2025-02-28" in warning

    def test_warning_has_no_inline_carveout(self) -> None:
        warning = cv.vintage_mismatch_warning(["2024-09-02", "2025-02-28"])
        assert "carve-out" in warning.lower()

    def test_mixed_string_date_datetime_does_not_crash(self) -> None:
        # The warning path sorts the coerced dates to print the span. A mix of
        # an ISO string, a date, and a datetime must not raise TypeError.
        warning = cv.vintage_mismatch_warning(
            ["2024-09-02", date(2024, 12, 31), datetime(2025, 2, 28, 12, 0)],
            context="blended paid total",
        )
        assert warning != ""
        assert "FY2024→FY2025" in warning
        # The span endpoints render as plain ISO dates (datetime normalized).
        assert "2024-09-02" in warning
        assert "2025-02-28" in warning
