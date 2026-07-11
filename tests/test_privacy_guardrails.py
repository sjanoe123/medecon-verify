"""Tests for utils.privacy_guardrails."""
from __future__ import annotations

from medecon_verify import privacy as pg


class TestSuppressSmallCells:
    def test_suppresses_below_threshold(self) -> None:
        out = pg.suppress_small_cells([5, 10, 11, 100], threshold=11)
        assert out == ["*", "*", 11, 100]

    def test_passes_strings_through(self) -> None:
        out = pg.suppress_small_cells(["already-suppressed", 5, 100])
        assert out[0] == "already-suppressed"
        assert out[1] == "*"
        assert out[2] == 100


class TestAggregateAge90Plus:
    def test_replaces_ages_90_and_above(self) -> None:
        records = [
            {"age": 89, "id": 1},
            {"age": 90, "id": 2},
            {"age": 102, "id": 3},
        ]
        out = pg.aggregate_age_90_plus(records, age_field="age")
        assert out[0]["age"] == 89
        assert out[1]["age"] == "90+"
        assert out[2]["age"] == "90+"

    def test_handles_none_age(self) -> None:
        records = [{"age": None, "id": 1}]
        out = pg.aggregate_age_90_plus(records, age_field="age")
        assert out[0]["age"] is None


class TestPart2Flag:
    def test_detects_substance_use_keyword(self) -> None:
        d = {"summary": "Cohort includes patients with substance use disorder."}
        out = pg.flag_part_2_redisclosure(d)
        assert out["privacy_audit"]["part_2_flag"] is True
        assert "substance use disorder" in out["privacy_audit"]["part_2_keywords"]

    def test_no_keyword_no_flag(self) -> None:
        d = {"summary": "Standard MSSP variance analysis."}
        out = pg.flag_part_2_redisclosure(d)
        assert out["privacy_audit"]["part_2_flag"] is False


class TestApplyEndToEnd:
    def test_apply_runs_all_guardrails(self) -> None:
        d = {
            "summary": "Cohort analysis on 5 members.",  # contains no PHI
            "member_counts": [3, 12, 100, 2],
            "narrative": "Standard variance analysis on FY2026 data.",
        }
        out = pg.apply(d)
        # member_counts suppressed below 11
        assert out["member_counts"][0] == "*"
        assert out["member_counts"][1] == 12
        assert out["member_counts"][2] == 100
        # privacy_audit section exists
        assert "privacy_audit" in out
        assert "phi_scan_metrics" in out["privacy_audit"]
