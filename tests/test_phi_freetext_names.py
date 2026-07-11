"""Phase C.2 — PHI scanner free-text name detection.

Catches title-prefixed names ("Patient Smith", "Dr Williams", "Mr. John Doe")
in record-field text. Honest about residual gaps:
- bare names without a title prefix still slip ("John Smith said")
- non-Latin scripts not handled
- common clinical concepts (Patient Care, Patient Outcomes) are intentionally
  whitelisted so they don't redact-storm.
"""
from __future__ import annotations

from medecon_verify import phi as phi_scanner


def setup_function() -> None:
    phi_scanner.reset_metrics()


def test_patient_lastname_redacted() -> None:
    out = phi_scanner.scan_text("Patient Smith reported chest pain at intake.")
    assert "Smith" not in out
    assert "[REDACTED:NAME]" in out
    assert phi_scanner.scanner_metrics()["freetext_name"] == 1


def test_mr_firstname_lastname_redacted() -> None:
    out = phi_scanner.scan_text("Mr. John Doe scheduled for follow-up.")
    assert "John" not in out and "Doe" not in out
    assert "[REDACTED:NAME]" in out


def test_dr_lastname_redacted() -> None:
    out = phi_scanner.scan_text("Dr Williams ordered the panel.")
    assert "Williams" not in out
    assert "[REDACTED:NAME]" in out


def test_mrs_lastname_redacted() -> None:
    out = phi_scanner.scan_text("Mrs Garcia left AMA.")
    assert "Garcia" not in out


def test_doctor_full_name_redacted() -> None:
    out = phi_scanner.scan_text("Doctor Anita Patel signed the order.")
    assert "Anita" not in out and "Patel" not in out


def test_patient_care_not_redacted() -> None:
    """'Patient Care' is a clinical concept, not a person."""
    text = "Patient Care Coordinator scheduled the appointment."
    out = phi_scanner.scan_text(text)
    assert out == text  # unchanged
    assert phi_scanner.scanner_metrics().get("freetext_name", 0) == 0


def test_patient_outcomes_not_redacted() -> None:
    text = "Patient Outcomes reporting standard."
    out = phi_scanner.scan_text(text)
    assert out == text


def test_bare_name_without_title_still_passes_through() -> None:
    """Honest gap: free-form 'John Smith reported' has no title prefix and
    is NOT caught by this regex. Documented; future work to add NER."""
    text = "John Smith reported the issue at 9am."
    out = phi_scanner.scan_text(text)
    assert "John Smith" in out  # gap is real
    assert phi_scanner.scanner_metrics().get("freetext_name", 0) == 0


def test_freetext_name_in_markdown_document() -> None:
    md = "# Visit notes\n\nPatient Johnson seen at 2pm.\n\nDr Lee evaluated.\n"
    out = phi_scanner.scan_markdown(md)
    assert "Johnson" not in out
    assert "Lee" not in out
    assert out.count("[REDACTED:NAME]") == 2


def test_metric_increments_per_redaction() -> None:
    phi_scanner.scan_text("Mr. Adams arrived. Mrs Adams accompanied him.")
    assert phi_scanner.scanner_metrics()["freetext_name"] == 2


def test_safe_harbor_coverage_count_message() -> None:
    """Documents the v1.1 coverage shift.

    v1.0: 8/18 Safe Harbor identifiers had code coverage in the scanner.
    v1.1: + free-text name detection moves names from 'policy-only' to
    'code-covered (with documented residual gap for bare names)'.
    """
    # Just exercises the metric to confirm the category exists.
    phi_scanner.scan_text("Patient Test reported.")
    assert "freetext_name" in phi_scanner.scanner_metrics()


def test_freetext_name_does_not_redact_when_inside_overflow() -> None:
    """Long text hits overflow gate first, replaced wholesale — name handler not reached."""
    long_text = "Patient Smith " + ("a" * 250)
    out = phi_scanner.scan_text(long_text)
    assert out == "[REDACTED:FREETEXT]"
