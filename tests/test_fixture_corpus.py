"""Self-contained fixture corpus — end-to-end gate exercise (medecon-verify task 0.6).

Runs the four guardrail gate modules over a fully-synthetic corpus that has ZERO
coupling to the medecon-stack skills tree or to `.medecon/org/` real data:

    adjudication  ->  codeset.stamp  ->  privacy.apply  ->  phi.scan_markdown

Every fixture is fabricated (see tests/fixtures/README.md). The suite asserts the
documented behavior of each gate on records engineered to trip it:

  * reversal pairs           (adjudication.reversal_pairs)
  * final-action supersession(adjudication.final_action_dedup)
  * FY-boundary blended total(adjudication.blended_total / codeset)
  * unknown-vintage stamp    (codeset.stamp -> "UNKNOWN")
  * n<=10 small-cell + SUD   (privacy.apply)
  * age 90+ aggregation      (privacy.aggregate_age_90_plus)
  * PHI redaction + near-miss(phi.scan_markdown)
  * fail-closed sentinel      (phi.scan under a monkeypatched scanner error)
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from medecon_verify import adjudication, codeset, phi, privacy

FIXTURES = Path(__file__).parent / "fixtures"
CLAIMS_CSV = FIXTURES / "claims" / "synthetic_claims_fy_boundary.csv"
DELIVERABLE_JSON = FIXTURES / "rendered" / "deliverable_smallcell_sud.json"
NARRATIVE_PHI = FIXTURES / "rendered" / "narrative_phi.md"
NARRATIVE_NEARMISS = FIXTURES / "rendered" / "narrative_nearmiss.md"

REDACTION_SENTINEL = "[SCAN_FAILED—CONTENT_SUPPRESSED]"


def _load_claims(path: Path) -> list[dict]:
    """Load a synthetic claims CSV, skipping the leading '#' synthetic-header line."""
    with path.open(newline="") as fh:
        rows = [ln for ln in fh if not ln.startswith("#")]
    return list(csv.DictReader(rows))


# ---------------------------------------------------------------------------
# Corpus hygiene
# ---------------------------------------------------------------------------

def test_every_fixture_declares_itself_synthetic() -> None:
    """No fixture ships without a synthetic disclaimer in its header."""
    files = [p for p in FIXTURES.rglob("*") if p.is_file()]
    assert files, "fixture corpus is empty"
    for p in files:
        head = p.read_text(errors="replace")[:400].upper()
        assert "SYNTHETIC" in head, f"{p} has no synthetic declaration in its header"


def test_claims_fixture_loads() -> None:
    records = _load_claims(CLAIMS_CSV)
    assert len(records) == 6
    assert {r["clm_id"] for r in records} == {"C1001", "C1002", "C1003", "C1004"}


# ---------------------------------------------------------------------------
# Gate 1 — adjudication
# ---------------------------------------------------------------------------

def test_reversal_pair_is_detected() -> None:
    records = _load_claims(CLAIMS_CSV)
    pairs = adjudication.reversal_pairs(records, claim_id="clm_id", paid_amount="paid_amt")
    # C1001 carries the engineered +500 / -500 offset; it is the only reversal.
    assert len(pairs) == 2
    assert {r["clm_id"] for r in pairs} == {"C1001"}
    assert sorted(r["paid_amt"] for r in pairs) == ["-500.00", "500.00"]


def test_final_action_dedup_keeps_highest_version() -> None:
    records = _load_claims(CLAIMS_CSV)
    deduped = adjudication.final_action_dedup(
        records, claim_id="clm_id", claim_version="clm_ver", claim_line="clm_line"
    )
    c1003 = [r for r in deduped if r["clm_id"] == "C1003"]
    # ver 1 (340.00) is superseded by ver 2 (360.00).
    assert len(c1003) == 1
    assert c1003[0]["clm_ver"] == "2"
    assert c1003[0]["paid_amt"] == "360.00"


def test_blended_total_flags_fy_boundary_crossing() -> None:
    records = _load_claims(CLAIMS_CSV)
    result = adjudication.blended_total(
        records, amount_field="paid_amt", date_field="svc_dt"
    )
    assert result["crosses_fy_boundary"] is True
    assert result["fiscal_years"] == ["FY2026", "FY2027"]
    assert result["vintage_warning"]  # non-empty hard warning
    assert "FY2026→FY2027" in result["vintage_warning"]


# ---------------------------------------------------------------------------
# Gate 2 — codeset.stamp
# ---------------------------------------------------------------------------

def test_stamp_known_vintage_in_registry() -> None:
    deliverable = codeset.stamp({"title": "synthetic"}, asof=date(2026, 9, 15))
    assert deliverable["code_set_versions"]["icd10cm_fy"] == "FY2026"


def test_stamp_unknown_vintage_past_registry() -> None:
    # FY2028 (on/after 2027-10-01) is not yet in the bundled registry -> UNKNOWN.
    # (FY2027 is now bundled via tools/extract_icd10cm_fy2027.py.)
    deliverable = codeset.stamp({"title": "synthetic"}, asof=date(2027, 11, 1))
    assert deliverable["code_set_versions"]["icd10cm_fy"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Gate 3 — privacy.apply
# ---------------------------------------------------------------------------

def test_privacy_apply_suppresses_small_cells_and_flags_sud() -> None:
    deliverable = json.loads(DELIVERABLE_JSON.read_text())
    out = privacy.apply(deliverable, suppress_below=11)
    # n<=10 cells replaced with '*'; n>=11 pass through.
    assert out["subgroup_counts"] == ["*", "*", 45, 210]
    assert out["attributed_n"] == ["*", 60, 12]  # 5<=10 suppressed; 60,12 pass
    audit = out["privacy_audit"]
    assert set(audit["small_cell_suppressed_keys"]) == {"subgroup_counts", "attributed_n"}
    # SUD content trips the 42 CFR Part 2 flag.
    assert audit["part_2_flag"] is True
    assert "opioid use disorder" in audit["part_2_keywords"]


def test_age_90_plus_aggregation() -> None:
    records = _load_claims(CLAIMS_CSV)
    out = privacy.aggregate_age_90_plus(records, age_field="member_age")
    ages = [r["member_age"] for r in out]
    assert "90+" in ages           # ages 94 and 90 collapse
    assert "94" not in ages
    assert "72" in ages            # sub-90 ages untouched


# ---------------------------------------------------------------------------
# Gate 4 — phi.scan_markdown (+ end-to-end pipeline)
# ---------------------------------------------------------------------------

def test_phi_scan_markdown_redacts_seeded_identifiers() -> None:
    doc = NARRATIVE_PHI.read_text()
    scanned = phi.scan_markdown(doc)
    for leaked in (
        "123-45-6789",                       # SSN
        "1W9A4KM27CD",                       # MBI
        "testpatient@example.com",           # email
        "415-555-0142",                      # phone
        "2026-11-15",                        # granular date
        "https://portal.example.com/member/8675309",  # URL
    ):
        assert leaked not in scanned, f"{leaked!r} leaked through scan_markdown"
    assert "[REDACTED" in scanned
    # Title-prefixed name redacts, bare title token survives.
    assert "Testpatient" not in scanned
    assert "[REDACTED:NAME]" in scanned


def test_phi_scan_markdown_leaves_near_misses_intact() -> None:
    doc = NARRATIVE_NEARMISS.read_text()
    scanned = phi.scan_markdown(doc)
    assert scanned == doc, "near-miss narrative was altered — a false-positive redaction"
    assert "[REDACTED" not in scanned
    assert "1S9A4KM27CD" in scanned   # invalid MBI (excluded letter) preserved
    assert "Smith reviewed" in scanned  # bare surname preserved


def test_end_to_end_pipeline_over_corpus() -> None:
    """adjudication -> codeset.stamp -> privacy.apply -> phi.scan_markdown."""
    records = _load_claims(CLAIMS_CSV)

    # 1. adjudication: dedup, then blended total with FY-boundary warning.
    records = adjudication.final_action_dedup(
        records, claim_id="clm_id", claim_version="clm_ver", claim_line="clm_line"
    )
    total = adjudication.blended_total(
        records, amount_field="paid_amt", date_field="svc_dt"
    )
    caveats: list[str] = []
    if total["vintage_warning"]:
        caveats.append(total["vintage_warning"])
    assert caveats  # the FY2026->FY2027 span must surface a caveat

    deliverable = {
        "_synthetic": "fabricated",
        "title": "Synthetic blended total",
        "blended_total": total["total"],
        "caveats": caveats,
        "subgroup_counts": [3, 45],
        "narrative": NARRATIVE_PHI.read_text(),
    }

    # 2. codeset.stamp (asof past the bundled registry -> UNKNOWN, honestly
    #    stamped; FY2028 is not yet bundled, whereas FY2026/FY2027 now are).
    deliverable = codeset.stamp(deliverable, asof=date(2027, 11, 1))
    assert deliverable["code_set_versions"]["icd10cm_fy"] == "UNKNOWN"

    # 3. privacy.apply: small-cell suppression + audit section.
    deliverable = privacy.apply(deliverable, suppress_below=11)
    assert deliverable["subgroup_counts"] == ["*", 45]
    assert "privacy_audit" in deliverable

    # 4. phi.scan_markdown over the narrative.
    deliverable["narrative"] = phi.scan_markdown(deliverable["narrative"])
    assert "123-45-6789" not in deliverable["narrative"]
    assert "[REDACTED" in deliverable["narrative"]


# ---------------------------------------------------------------------------
# Fail-closed: a scanner crash must default to redaction, never pass-through.
# ---------------------------------------------------------------------------

def test_phi_scan_fails_closed_on_scanner_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_text: str) -> str:
        raise RuntimeError("synthetic scanner failure")

    monkeypatch.setattr(phi, "scan_text", _boom)
    out = phi.scan([{"note": "row that would otherwise pass through"}])
    # The record content is suppressed with the fail-closed sentinel, not leaked.
    assert out == [{"note": REDACTION_SENTINEL}]
    assert phi.scanner_metrics().get("scan_failed", 0) >= 1
