"""v1.4 capability-matrix — three privacy-guardrail eval-runner checkers.

Tests for: safe-harbor-18-clean, part-2-redisclosure-flag,
scanner-crash-defaults-to-redact. These eval ids were declared by
privacy-output-guardrails since v1.0 but had no checker (silent not_runnable);
these tests lock the behavior that closes that hole.
"""
from __future__ import annotations

from medecon_verify.certify import runner as er


# ---------- safe-harbor-18-clean ----------


def test_safe_harbor_pass_on_clean_output() -> None:
    md = "Total spend was $1,234 PMPM across the 90+ age band."
    r = er.check_safe_harbor_18_clean(md, {})
    assert r.status == "pass", r.detail


def test_safe_harbor_fail_on_ssn() -> None:
    r = er.check_safe_harbor_18_clean("Member SSN 123-45-6789 was flagged.", {})
    assert r.status == "fail"


def test_safe_harbor_fail_on_email() -> None:
    r = er.check_safe_harbor_18_clean("Contact jane.doe@example.com for follow-up.", {})
    assert r.status == "fail"


def test_safe_harbor_fail_on_phone() -> None:
    r = er.check_safe_harbor_18_clean("Call (415) 555-0132 to verify.", {})
    assert r.status == "fail"


def test_safe_harbor_fail_on_url() -> None:
    # Safe Harbor identifier #14 — a member-portal link leaking into a deliverable.
    r = er.check_safe_harbor_18_clean(
        "See member record at https://portal.example.com/m/8675309", {}
    )
    assert r.status == "fail"


def test_safe_harbor_fail_on_patient_name() -> None:
    # Safe Harbor identifier #1 — an explicit patient name in rendered output.
    r = er.check_safe_harbor_18_clean("Patient John Smith was admitted in Q1.", {})
    assert r.status == "fail"


def test_safe_harbor_fail_on_granular_date() -> None:
    # Safe Harbor identifier #3 — a granular DOB/service date.
    r = er.check_safe_harbor_18_clean("DOB 1950-01-01 on file.", {})
    assert r.status == "fail"


def test_safe_harbor_pass_on_decision_maker_name() -> None:
    # Named decision-makers are required by /scope and are NOT patient PHI; the
    # scanner's name heuristic is context-aware and must not flag them.
    r = er.check_safe_harbor_18_clean("Decision-maker: J. Smith, VP Finance.", {})
    assert r.status == "pass", r.detail


# ---------- part-2-redisclosure-flag ----------


def test_part_2_vacuous_pass_without_sud_content() -> None:
    r = er.check_part_2_redisclosure_flag("Routine MA margin analysis.", {})
    assert r.status == "pass"
    assert "vacuous" in r.detail


def test_part_2_fail_when_sud_without_banner() -> None:
    r = er.check_part_2_redisclosure_flag("Opioid use disorder cohort trend.", {})
    assert r.status == "fail"


def test_part_2_pass_when_sud_with_banner() -> None:
    md = "Opioid use disorder cohort. 42 CFR Part 2 redisclosure consent verified."
    r = er.check_part_2_redisclosure_flag(md, {})
    assert r.status == "pass"


def test_part_2_detects_multiple_keywords() -> None:
    # methadone + detox both in the conservative keyword list
    r = er.check_part_2_redisclosure_flag("methadone and detox program counts.", {})
    assert r.status == "fail"


# ---------- scanner-crash-defaults-to-redact ----------


def test_scanner_crash_vacuous_pass_without_signal() -> None:
    r = er.check_scanner_crash_defaults_to_redact("clean output", {})
    assert r.status == "pass"


def test_scanner_crash_fail_when_signalled_but_no_sentinel() -> None:
    r = er.check_scanner_crash_defaults_to_redact(
        "content here", {"privacy_audit": {"scan_failed": True}}
    )
    assert r.status == "fail"


def test_scanner_crash_pass_when_sentinel_present() -> None:
    md = "row 1 ok\n[SCAN_FAILED—CONTENT_SUPPRESSED]\nrow 3 ok"
    r = er.check_scanner_crash_defaults_to_redact(md, {"scan_failed": True})
    assert r.status == "pass"


def test_scanner_crash_signal_via_scan_errors_count() -> None:
    r = er.check_scanner_crash_defaults_to_redact("x", {"scan_errors": 2})
    assert r.status == "fail"


def test_scanner_crash_signal_via_failed_chunks_list() -> None:
    r = er.check_scanner_crash_defaults_to_redact(
        "x", {"privacy_audit": {"failed_chunks": ["chunk_7"]}}
    )
    assert r.status == "fail"


def test_scanner_crash_signal_via_canonical_phi_scan_metrics() -> None:
    """Regression: the canonical guardrails sidecar nests scan_failed under
    privacy_audit.phi_scan_metrics (phi_scanner.scanner_metrics()), not as a
    flat key. A real crash there must NOT be treated as a vacuous pass.
    """
    sidecar = {"privacy_audit": {"phi_scan_metrics": {"scan_failed": 2, "ssn": 0}}}
    r = er.check_scanner_crash_defaults_to_redact("content, no sentinel", sidecar)
    assert r.status == "fail", r.detail


def test_scanner_crash_canonical_metrics_pass_when_sentinel_present() -> None:
    sidecar = {"privacy_audit": {"phi_scan_metrics": {"scan_failed": 1}}}
    md = "row 1\n[SCAN_FAILED—CONTENT_SUPPRESSED]\nrow 3"
    r = er.check_scanner_crash_defaults_to_redact(md, sidecar)
    assert r.status == "pass", r.detail


def test_scanner_crash_canonical_metrics_clean_is_vacuous_pass() -> None:
    """phi_scan_metrics present but scan_failed == 0 → no failure signalled."""
    sidecar = {"privacy_audit": {"phi_scan_metrics": {"scan_failed": 0, "ssn": 0}}}
    r = er.check_scanner_crash_defaults_to_redact("clean output", sidecar)
    assert r.status == "pass"


def test_scanner_crash_mixed_shape_flat_flag_not_dropped() -> None:
    """Regression: a flat scan_failed alongside a privacy_audit object must NOT be
    discarded. Earlier the checker resolved to privacy_audit-or-root (not both),
    so a flat flag next to an empty privacy_audit silently passed.
    """
    sidecar = {"scan_failed": True, "privacy_audit": {}}
    r = er.check_scanner_crash_defaults_to_redact("no sentinel here", sidecar)
    assert r.status == "fail", r.detail


def test_scanner_crash_mixed_shape_passes_with_sentinel() -> None:
    sidecar = {"scan_failed": True, "privacy_audit": {}}
    md = "row 1\n[SCAN_FAILED—CONTENT_SUPPRESSED]\nrow 3"
    r = er.check_scanner_crash_defaults_to_redact(md, sidecar)
    assert r.status == "pass", r.detail


def test_scanner_crash_nested_signal_with_flat_clean() -> None:
    """Signal only in privacy_audit, root clean → still caught."""
    sidecar = {"other_meta": 1, "privacy_audit": {"failed_chunks": ["c1"]}}
    r = er.check_scanner_crash_defaults_to_redact("no sentinel", sidecar)
    assert r.status == "fail", r.detail


# ---------- scanner-crash CONTRACT (real phi_scanner shape) ----------
#
# The shape tests above are hand-built. This contract test pins the checker
# against the ACTUAL sidecar a real scanner crash produces, so the checker's
# tolerated shapes trace to real behavior instead of guesswork. The canonical
# producer is phi_scanner.scan(records): on a field-scan exception it writes the
# sentinel into the record AND increments the `scan_failed` metric; a real
# pipeline then stamps scanner_metrics() under privacy_audit.phi_scan_metrics
# (mirroring utils.privacy_guardrails.apply()). apply() itself scans via
# scan_text, which has no scan_failed path — so scan(records) is the only origin
# of this signal, and privacy_audit.phi_scan_metrics.scan_failed is THE real shape.


def test_scanner_crash_contract_matches_real_phi_scanner_shape(monkeypatch) -> None:
    from medecon_verify import phi as phi_scanner

    real_scan_text = phi_scanner.scan_text

    def crashing_scan_text(text):
        if text == "BOOM":
            raise RuntimeError("simulated scanner crash")
        return real_scan_text(text)

    monkeypatch.setattr(phi_scanner, "scan_text", crashing_scan_text)

    phi_scanner.reset_metrics()
    redacted = phi_scanner.scan([{"value": "BOOM", "ok": "clean text"}])

    # Real behavior: the crashed field is replaced with the sentinel and the
    # scan_failed metric is incremented. If either assertion breaks, the checker's
    # assumptions about the real producer are stale.
    assert redacted[0]["value"] == "[SCAN_FAILED—CONTENT_SUPPRESSED]"
    metrics = phi_scanner.scanner_metrics()
    assert metrics.get("scan_failed", 0) >= 1

    # The sidecar shape a real pipeline stamps (matches privacy_guardrails.apply()).
    sidecar = {"privacy_audit": {"phi_scan_metrics": metrics}}

    # Compliant deliverable: scanned content carries the sentinel → pass.
    rendered_ok = "\n".join(str(v) for v in redacted[0].values())
    assert "[SCAN_FAILED—CONTENT_SUPPRESSED]" in rendered_ok
    assert er.check_scanner_crash_defaults_to_redact(rendered_ok, sidecar).status == "pass"

    # Violation: same real crash signal, but the sentinel was stripped from output
    # (content passed through unscanned) → must fail.
    rendered_leaked = rendered_ok.replace("[SCAN_FAILED—CONTENT_SUPPRESSED]", "BOOM")
    assert er.check_scanner_crash_defaults_to_redact(rendered_leaked, sidecar).status == "fail"


# ---------- registry wiring ----------


def test_all_three_checkers_registered() -> None:
    for eid in (
        "safe-harbor-18-clean",
        "part-2-redisclosure-flag",
        "scanner-crash-defaults-to-redact",
    ):
        assert eid in er.CHECKERS, f"{eid} not registered in CHECKERS"
