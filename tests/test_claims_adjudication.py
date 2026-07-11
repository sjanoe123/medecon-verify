"""Tests for utils.claims_adjudication.

The single highest-impact silent-failure mode for an analyst agent is silently
miscounting reversed claims. These tests lock the structural correctness in.
"""
from __future__ import annotations

import csv
from pathlib import Path

from medecon_verify import adjudication as ca

_MOCK_CLAIMS = (
    Path(__file__).resolve().parents[1]
    / "analyst-workspace" / "examples" / "mock-claims"
    / "medical_claims_2024H2_2025H1.csv"
)


class TestFinalActionSignal:
    def test_no_duplicates_returns_zero(self) -> None:
        records = [
            {"claim_id": "C001", "claim_version": 1, "paid": 100},
            {"claim_id": "C002", "claim_version": 1, "paid": 200},
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["duplicates"] == 0
        assert sig["needs_dedup"] is False
        assert sig["claim_count"] == 2

    def test_duplicates_detected(self) -> None:
        records = [
            {"claim_id": "C001", "claim_version": 1, "paid": 100},
            {"claim_id": "C001", "claim_version": 2, "paid": 100},  # version-2
            {"claim_id": "C002", "claim_version": 1, "paid": 200},
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["duplicates"] == 1
        assert sig["needs_dedup"] is True
        assert sig["claim_count"] == 2

    def test_missing_claim_version_field(self) -> None:
        records = [
            {"claim_id": "C001", "paid": 100},
            {"claim_id": "C001", "paid": 100},
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["missing_version_field"] is True
        assert sig["duplicates"] == 1

    def test_empty_input(self) -> None:
        sig = ca.final_action_signal([], claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["total_lines"] == 0
        assert sig["needs_dedup"] is False

    def test_multi_line_claim_not_counted_as_duplicate(self) -> None:
        """PROF-1: extra LINES of one claim are expected, not duplicates.

        No version field + distinct claim_line values + no exact dup = 0 dups.
        This is the bug that produced the 38.8% (97/250) artifact.
        """
        records = [
            {"claim_id": "C001", "claim_line": 1, "paid": 100},
            {"claim_id": "C001", "claim_line": 2, "paid": 50},
            {"claim_id": "C001", "claim_line": 3, "paid": 25},
        ]
        sig = ca.final_action_signal(
            records, claim_id="claim_id", claim_version="claim_version",
            claim_line="claim_line",
        )
        assert sig["duplicates"] == 0
        assert sig["needs_dedup"] is False
        assert sig["multi_line_rows"] == 3
        assert sig["claim_lines"] == 3
        assert sig["claim_count"] == 1
        assert sig["missing_version_field"] is True

    def test_no_version_counts_only_exact_full_row_dups(self) -> None:
        """No version field: shared claim_id is NOT a dup; exact full row is."""
        records = [
            {"claim_id": "C001", "claim_line": 1, "paid": 100},
            {"claim_id": "C001", "claim_line": 1, "paid": 100},  # exact dup
            {"claim_id": "C001", "claim_line": 2, "paid": 50},   # multi-line, not dup
        ]
        sig = ca.final_action_signal(
            records, claim_id="claim_id", claim_version="claim_version",
            claim_line="claim_line",
        )
        assert sig["duplicates"] == 1
        assert sig["exact_duplicates"] == 1
        assert sig["superseded"] == 0
        assert sig["multi_line_rows"] == 3

    def test_versioned_supersession_keyed_on_claim_line(self) -> None:
        """A higher version for the SAME (claim_id, claim_line) supersedes; a
        different line is not a dup."""
        records = [
            {"claim_id": "C001", "claim_line": 1, "claim_version": 1, "paid": 100},
            {"claim_id": "C001", "claim_line": 1, "claim_version": 2, "paid": 90},
            {"claim_id": "C001", "claim_line": 2, "claim_version": 1, "paid": 40},
        ]
        sig = ca.final_action_signal(
            records, claim_id="claim_id", claim_version="claim_version",
            claim_line="claim_line",
        )
        assert sig["superseded"] == 1
        assert sig["duplicates"] == 1
        assert sig["claim_lines"] == 2
        assert sig["multi_line_rows"] == 3

    def test_equal_version_nonidentical_is_conflict_not_superseded(self) -> None:
        """Two non-identical rows tied at the SAME (winning) version are a true
        version conflict, not a supersession — they must not inflate `superseded`
        or `duplicates`, and must be surfaced as `version_conflicts`."""
        records = [
            {"claim_id": "C001", "claim_version": 2, "paid": 100},
            {"claim_id": "C001", "claim_version": 2, "paid": 80},  # same version, differs
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["superseded"] == 0
        assert sig["duplicates"] == 0
        assert sig["version_conflicts"] == 1
        assert sig["needs_dedup"] is False

    def test_all_null_version_column_routes_through_missing_path(self) -> None:
        """An all-null claim_version column is effectively un-versioned.

        Round-6 finding 4: key PRESENCE with all-null values took the versioned
        path, where every row tied at _version_sort_key(None) and distinct
        same-claim rows were miscounted as version_conflicts. An all-null column
        must behave like an ABSENT one: distinct non-identical rows are NOT
        duplicates, version_conflicts == 0, missing_version_field is True.
        """
        records = [
            {"claim_id": "C001", "claim_version": None, "paid": 100},
            {"claim_id": "C001", "claim_version": None, "paid": 80},  # distinct
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["missing_version_field"] is True
        assert sig["version_conflicts"] == 0
        assert sig["duplicates"] == 0
        assert sig["superseded"] == 0

    def test_blank_string_version_column_routes_through_missing_path(self) -> None:
        """An all-blank ('' / whitespace) version column is also un-versioned."""
        records = [
            {"claim_id": "C001", "claim_version": "", "paid": 100},
            {"claim_id": "C001", "claim_version": "  ", "paid": 80},
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["missing_version_field"] is True
        assert sig["version_conflicts"] == 0

    def test_partially_null_version_column_stays_versioned(self) -> None:
        """One parseable value is enough to keep the versioned path."""
        records = [
            {"claim_id": "C001", "claim_version": 1, "paid": 100},
            {"claim_id": "C001", "claim_version": 2, "paid": 90},
            {"claim_id": "C001", "claim_version": None, "paid": 70},
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig.get("missing_version_field") is not True
        # version 2 wins; v1 and null-version rows are superseded.
        assert sig["superseded"] == 2

    def test_all_null_version_dedup_drops_only_exact_dups(self) -> None:
        """final_action_dedup on an all-null column keeps distinct rows."""
        records = [
            {"claim_id": "C001", "claim_version": None, "paid": 100},
            {"claim_id": "C001", "claim_version": None, "paid": 80},  # distinct, keep
            {"claim_id": "C001", "claim_version": None, "paid": 100},  # exact dup, drop
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                    claim_version="claim_version")
        assert len(out) == 2

    def test_equal_version_exact_dup_still_counts_as_dup(self) -> None:
        """An EXACT full-row duplicate tied at the max version is still a dup (not
        a conflict) — exact equality is collapsible."""
        records = [
            {"claim_id": "C001", "claim_version": 2, "paid": 100},
            {"claim_id": "C001", "claim_version": 2, "paid": 100},  # exact dup
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["duplicates"] == 1
        assert sig["exact_duplicates"] == 1
        assert sig["superseded"] == 0
        assert sig["version_conflicts"] == 0

    def test_strictly_lower_version_superseded_with_tie_at_max(self) -> None:
        """Mixed: one strictly-lower version (superseded) plus two non-identical
        rows tied at the max (conflict)."""
        records = [
            {"claim_id": "C001", "claim_version": 1, "paid": 100},  # superseded
            {"claim_id": "C001", "claim_version": 3, "paid": 90},   # max
            {"claim_id": "C001", "claim_version": 3, "paid": 95},   # max, differs → conflict
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["superseded"] == 1
        assert sig["version_conflicts"] == 1
        assert sig["duplicates"] == 1  # only the strictly-lower one


class TestFinalActionDedup:
    def test_keeps_highest_version(self) -> None:
        records = [
            {"claim_id": "C001", "claim_version": 1, "paid": 100},
            {"claim_id": "C001", "claim_version": 2, "paid": 80},
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                     claim_version="claim_version")
        assert len(out) == 1
        assert out[0]["claim_version"] == 2
        assert out[0]["paid"] == 80

    def test_no_version_field_keeps_distinct_rows(self) -> None:
        """Unversioned data: distinct rows sharing a claim_id are NOT collapsed.

        The old last-wins-per-claim_id behavior silently merged two legitimately
        distinct adjudications (different paid amounts) into one, understating
        totals. With no version field, only EXACT full-row duplicates are dropped.
        """
        records = [
            {"claim_id": "C001", "paid": 100},
            {"claim_id": "C001", "paid": 80},
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                     claim_version="claim_version")
        assert len(out) == 2  # distinct rows, not last-wins

    def test_no_version_field_drops_only_exact_duplicates(self) -> None:
        records = [
            {"claim_id": "C001", "paid": 100},
            {"claim_id": "C001", "paid": 100},  # exact dup of the first
            {"claim_id": "C001", "paid": 80},
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                     claim_version="claim_version")
        assert len(out) == 2

    def test_keeps_distinct_claim_lines(self) -> None:
        """Multi-line claim: each (claim_id, claim_line) survives dedup."""
        records = [
            {"claim_id": "C001", "claim_line": 1, "claim_version": 1, "paid": 100},
            {"claim_id": "C001", "claim_line": 1, "claim_version": 2, "paid": 90},
            {"claim_id": "C001", "claim_line": 2, "claim_version": 1, "paid": 40},
        ]
        out = ca.final_action_dedup(
            records, claim_id="claim_id", claim_version="claim_version",
            claim_line="claim_line",
        )
        # Line 1 collapses to its v2; line 2 survives. Two distinct lines remain.
        assert len(out) == 2
        keys = {(r["claim_id"], r["claim_line"]) for r in out}
        assert keys == {("C001", 1), ("C001", 2)}
        line1 = next(r for r in out if r["claim_line"] == 1)
        assert line1["claim_version"] == 2

    def test_equal_version_conflict_keeps_both(self) -> None:
        """Two non-identical rows tied at the winning version are a conflict, not
        a supersession — both survive (caller resolves)."""
        records = [
            {"claim_id": "C001", "claim_version": 2, "paid": 100},
            {"claim_id": "C001", "claim_version": 2, "paid": 80},
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                     claim_version="claim_version")
        assert len(out) == 2

    def test_missing_version_does_not_outrank_numeric(self) -> None:
        """Regression: a None (or junk) claim_version must NOT win over a real
        numeric version. The higher numeric version is the final action; the
        missing-version row is superseded and dropped."""
        records = [
            {"claim_id": "C001", "claim_version": 2, "paid": 100},
            {"claim_id": "C001", "claim_version": None, "paid": 999},
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                     claim_version="claim_version")
        assert len(out) == 1
        assert out[0]["claim_version"] == 2
        assert out[0]["paid"] == 100  # the None/999 row was correctly dropped

    def test_junk_version_does_not_outrank_numeric(self) -> None:
        """Unparseable (non-numeric) versions also sort below real numerics."""
        records = [
            {"claim_id": "C001", "claim_version": "PENDING", "paid": 999},
            {"claim_id": "C001", "claim_version": 3, "paid": 50},
        ]
        out = ca.final_action_dedup(records, claim_id="claim_id",
                                     claim_version="claim_version")
        assert len(out) == 1
        assert out[0]["claim_version"] == 3
        assert out[0]["paid"] == 50


class TestVersionSortKey:
    def test_numeric_outranks_missing_and_junk(self) -> None:
        """max() over sort keys must pick a real numeric over None/junk."""
        keys = [
            ca._version_sort_key(None),
            ca._version_sort_key("PENDING"),
            ca._version_sort_key(2),
        ]
        assert max(keys) == ca._version_sort_key(2)

    def test_signal_supersedes_missing_version_row(self) -> None:
        """The signal counts the missing-version row as superseded, not the
        numeric one — duplicates attribute to the right row."""
        records = [
            {"claim_id": "C001", "claim_version": 2, "paid": 100},
            {"claim_id": "C001", "claim_version": None, "paid": 999},
        ]
        sig = ca.final_action_signal(records, claim_id="claim_id",
                                      claim_version="claim_version")
        assert sig["superseded"] == 1
        assert sig["duplicates"] == 1
        assert sig["version_conflicts"] == 0


class TestReversalPairs:
    def test_detects_offsetting_pair(self) -> None:
        records = [
            {"claim_id": "C001", "paid": 100.0},
            {"claim_id": "C001", "paid": -100.0},
        ]
        pairs = ca.reversal_pairs(records, claim_id="claim_id",
                                   paid_amount="paid")
        assert len(pairs) == 2

    def test_does_not_flag_non_pair(self) -> None:
        records = [
            {"claim_id": "C001", "paid": 100.0},
            {"claim_id": "C002", "paid": -100.0},
        ]
        pairs = ca.reversal_pairs(records, claim_id="claim_id",
                                   paid_amount="paid")
        assert pairs == []

    def test_handles_zero_amounts(self) -> None:
        records = [
            {"claim_id": "C001", "paid": 0},
            {"claim_id": "C001", "paid": 0},
        ]
        pairs = ca.reversal_pairs(records, claim_id="claim_id",
                                   paid_amount="paid")
        # 0 + 0 = 0 but 0 is not flagged (paid_i must != 0)
        assert pairs == []


class TestAllowedPaidDistribution:
    def test_paid_gt_allowed_flagged(self) -> None:
        records = [
            {"allowed": 100, "paid": 80},
            {"allowed": 100, "paid": 120},  # suspicious
        ]
        d = ca.allowed_paid_distribution(records, allowed_field="allowed",
                                          paid_field="paid")
        assert d["paid_gt_allowed"] == 1
        assert d["valid_pairs"] == 2

    def test_capitation_both_null(self) -> None:
        records = [
            {"allowed": None, "paid": None},
            {"allowed": 100, "paid": 80},
        ]
        d = ca.allowed_paid_distribution(records, allowed_field="allowed",
                                          paid_field="paid")
        assert d["both_null_capitation"] == 1
        assert d["valid_pairs"] == 1

    def test_negative_paid_for_reversal(self) -> None:
        records = [
            {"allowed": 100, "paid": -50},
            {"allowed": 100, "paid": 50},
        ]
        d = ca.allowed_paid_distribution(records, allowed_field="allowed",
                                          paid_field="paid")
        assert d["negative_paid"] == 1

    def test_empty_input(self) -> None:
        d = ca.allowed_paid_distribution([], allowed_field="a", paid_field="p")
        assert d["valid_pairs"] == 0


class TestHeaderLineConsistency:
    def test_consistent_headers_and_lines(self) -> None:
        headers = [{"claim_id": "C001", "amt": 100.0}]
        lines = [
            {"claim_id": "C001", "line_amt": 60.0},
            {"claim_id": "C001", "line_amt": 40.0},
        ]
        result = ca.header_line_consistency(
            headers, lines, claim_id="claim_id",
            claim_amount="amt", line_amount="line_amt"
        )
        assert result["checked"] == 1
        assert result["mismatched"] == 0

    def test_mismatch_detected(self) -> None:
        headers = [{"claim_id": "C001", "amt": 100.0}]
        lines = [{"claim_id": "C001", "line_amt": 90.0}]
        result = ca.header_line_consistency(
            headers, lines, claim_id="claim_id",
            claim_amount="amt", line_amount="line_amt"
        )
        assert result["mismatched"] == 1
        assert len(result["mismatch_examples"]) == 1


class TestBlendedTotal:
    """VINT-1: a blended total whose service-date span crosses the Oct-01
    ICD-10-CM FY boundary must carry a non-empty hard vintage warning."""

    def test_single_fy_no_warning(self) -> None:
        records = [
            {"paid_amount": "100.00", "service_date": "2025-01-15"},
            {"paid_amount": "50.00", "service_date": "2025-06-30"},
        ]
        out = ca.blended_total(records, amount_field="paid_amount")
        assert out["total"] == 150.0
        assert out["crosses_fy_boundary"] is False
        assert out["vintage_warning"] == ""
        assert out["fiscal_years"] == ["FY2025"]

    def test_crossing_boundary_warns(self) -> None:
        records = [
            {"paid_amount": "100.00", "service_date": "2024-09-02"},  # FY2024
            {"paid_amount": "200.00", "service_date": "2025-02-28"},  # FY2025
        ]
        out = ca.blended_total(records, amount_field="paid_amount")
        assert out["total"] == 300.0
        assert out["crosses_fy_boundary"] is True
        assert out["fiscal_years"] == ["FY2024", "FY2025"]
        assert out["vintage_warning"] != ""
        assert "FY2024" in out["vintage_warning"]
        assert "FY2025" in out["vintage_warning"]

    def test_reads_service_from_to_pair(self) -> None:
        # No service_date column; the from/to pair still drives the span.
        records = [
            {"paid_amount": "1", "service_from": "2024-09-02", "service_to": "2024-09-03"},
            {"paid_amount": "1", "service_from": "2025-02-27", "service_to": "2025-02-28"},
        ]
        out = ca.blended_total(records, amount_field="paid_amount")
        assert out["crosses_fy_boundary"] is True
        assert out["vintage_warning"] != ""

    def test_mock_claims_fixture_span_warns(self) -> None:
        # The committed mock-claims fixture spans 2024-09-02 … 2025-02-28; a
        # blended paid total over it must surface the FY2024→FY2025 warning.
        if not _MOCK_CLAIMS.exists():
            import pytest
            pytest.skip(f"fixture not present: {_MOCK_CLAIMS}")
        with _MOCK_CLAIMS.open() as f:
            records = list(csv.DictReader(f))
        out = ca.blended_total(
            records, amount_field="paid_amount", date_field="service_from",
        )
        assert out["crosses_fy_boundary"] is True
        assert out["fiscal_years"] == ["FY2024", "FY2025"]
        assert "FY2024→FY2025" in out["vintage_warning"]
