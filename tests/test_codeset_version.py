"""Tests for utils.codeset_version."""
from __future__ import annotations

from datetime import date, datetime

import pytest

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


class TestFy2027:
    """FY2027 ICD-10-CM vintage — derived from verified CMS FY2027 release ZIPs
    by tools/extract_icd10cm_fy2027.py (docs/workpapers/fy2027-registry-sources.md).
    """

    def test_registry_has_fy2027_bounds(self) -> None:
        reg = cv.icd10_registry()
        assert reg["FY2027"] == {
            "effective": "2026-10-01",
            "obsolete": "2027-09-30",
        }

    def test_stamp_recognizes_fy2027(self) -> None:
        out = cv.stamp({}, asof=date(2027, 5, 1))
        assert out["code_set_versions"]["icd10cm_fy"] == "FY2027"

    def test_stamp_at_oct01_2026_is_fy2027(self) -> None:
        # First day of the FY2027 window.
        out = cv.stamp({}, asof=date(2026, 10, 1))
        assert out["code_set_versions"]["icd10cm_fy"] == "FY2027"

    def test_stamp_at_sep30_2026_is_still_fy2026(self) -> None:
        # Last day of FY2026 — the boundary is exclusive on the FY2026 side.
        out = cv.stamp({}, asof=date(2026, 9, 30))
        assert out["code_set_versions"]["icd10cm_fy"] == "FY2026"

    def test_detect_icd10_fy_returns_fy2027(self) -> None:
        records = [
            {"service_date": "2026-11-15"},
            {"service_date": "2027-02-10"},
            {"service_date": "2027-08-22"},
        ]
        assert cv.detect_icd10_fy(records) == "FY2027"

    def test_strict_mode_fy2027_icd10_covered_but_still_fails_closed(self) -> None:
        # Proves FY2027 ICD-10-CM coverage is REAL without fabricating overrides.
        #
        # We must NOT stamp an invented 2027Q3 HCPCS quarter or an unfinalized
        # ms_drg=v44 just to force strict=True green — that would mask the real
        # fail-closed result and normalize stamping a proposed-rule grouper as
        # valid. Instead:
        #   1. icd10cm_fy alone resolves to FY2027 (the registry row is real), and
        #   2. strict mode STILL fails closed for a 2027-05-01 date, because the
        #      bundled HCPCS registry (tops out 2026Q2) and ms_drg.json (v43/
        #      FY2026) genuinely do not cover it. The strict error must name
        #      hcpcs_quarter — the first still-uncovered date-windowed field —
        #      and must NOT name icd10cm_fy, which is now covered. If FY2027
        #      coverage regressed, icd10cm_fy would become the blocker again and
        #      this assertion would flip.
        assert cv._vintage_for(date(2027, 5, 1), cv.ICD10CM_FY_REGISTRY) == "FY2027"
        with pytest.raises(cv.CodesetVersionError) as exc:
            cv.stamp({}, asof=date(2027, 5, 1), strict=True)
        msg = str(exc.value)
        assert "hcpcs_quarter" in msg  # the genuinely-uncovered field is the blocker
        assert "icd10cm_fy" not in msg  # FY2027 is covered; it is NOT the blocker

    def test_ms_drg_v44_deferred_stamps_unknown_for_fy2027(self) -> None:
        # DEFERRAL CONSEQUENCE: FY2027 diagnosis codes exist, but no final MS-DRG
        # grouper does (FY2027 IPPS Final Rule unpublished; v44 is proposed-rule
        # Test GROUPER only). So a FY2027 date stamps icd10cm_fy=FY2027 while
        # ms_drg degrades to UNKNOWN — exactly the real-world state. When the
        # final rule posts and v44 lands in ms_drg.json, this becomes "v44".
        out = cv.stamp({}, asof=date(2027, 5, 1))
        assert out["code_set_versions"]["icd10cm_fy"] == "FY2027"
        assert out["code_set_versions"]["ms_drg"] == "UNKNOWN"

    @pytest.mark.skip(
        reason="MS-DRG v44 DEFERRED: FY2027 IPPS Final Rule unpublished as of "
        "2026-07-19; v44 exists only as a proposed-rule Test GROUPER. Un-skip and "
        "assert v44 once tools/extract_icd10cm_fy2027.extract_ms_drg_v44 ingests "
        "the FINAL rule and ms_drg.json carries {'v44': {'fy': 'FY2027', "
        "'effective': '2026-10-01'}}. Re-check: https://www.cms.gov/medicare/"
        "payment/prospective-payment-systems/acute-inpatient-pps/"
        "fy-2027-ipps-final-rule-home-page"
    )
    def test_ms_drg_v44_final_rule(self) -> None:
        out = cv.stamp({}, asof=date(2027, 5, 1))
        assert out["code_set_versions"]["ms_drg"] == "v44"


class TestFy2026Fy2027BoundaryWarning:
    """The Oct-01 2026 FY2026->FY2027 boundary must still warn on a blended span."""

    def test_span_crossing_oct01_2026_lists_both_fys(self) -> None:
        assert cv.detect_fy_span(["2026-09-15", "2026-11-15"]) == ["FY2026", "FY2027"]

    def test_boundary_is_oct_01_2026(self) -> None:
        assert cv._fy_for_date(date(2026, 9, 30)) == "FY2026"
        assert cv._fy_for_date(date(2026, 10, 1)) == "FY2027"

    def test_warning_names_fy2026_to_fy2027(self) -> None:
        warning = cv.vintage_mismatch_warning(
            ["2026-09-15", "2026-11-15"], context="blended paid total"
        )
        assert warning != ""
        assert "FY2026→FY2027" in warning
        assert "2026-09-15" in warning
        assert "2026-11-15" in warning


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
