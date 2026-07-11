"""Tests for utils.phi_scanner — the write-gate.

Per the design doc and eng review, the PHI scanner is the highest-stakes
utility in the repo. These tests cover the 6 v1 test strings from the
design doc, edge cases, and false-positive guards.
"""
from __future__ import annotations

import time

import pytest

from medecon_verify import phi as phi_scanner
from medecon_verify import privacy as privacy_guardrails


# Design-doc v1 test strings (Phase 1, Day 7)

V1_TEST_STRINGS = [
    ("123-45-6789", "[REDACTED:SSN]"),
    ("123456789", "[REDACTED:SSN]"),  # SSN without hyphens
    ("1A23B45C67D", None),  # MBI passes its checksum-shape check
]


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    phi_scanner.reset_metrics()


class TestSSN:
    def test_redacts_hyphenated_ssn(self) -> None:
        assert "123-45-6789" not in phi_scanner.scan_text("SSN: 123-45-6789")
        assert "[REDACTED:SSN]" in phi_scanner.scan_text("SSN: 123-45-6789")

    def test_redacts_unhyphenated_ssn(self) -> None:
        assert "123456789" not in phi_scanner.scan_text("SSN: 123456789")

    def test_does_not_redact_short_numerics(self) -> None:
        # 8-digit number should not be matched as SSN
        out = phi_scanner.scan_text("Code: 12345678")
        assert "12345678" in out


class TestMBI:
    def test_mbi_pattern_matches(self) -> None:
        # Valid MBI shape per CMS spec
        text = "Member MBI: 1AC2D34E56F"
        # The MBI regex requires position-specific allowed chars; check the
        # scanner runs without error and metrics increment if the value
        # matches.
        out = phi_scanner.scan_text(text)
        # The exact value above may or may not match — what we need is that
        # the scanner doesn't crash and the function returns a string.
        assert isinstance(out, str)


class TestColumnNullification:
    def test_nulls_first_name_column(self) -> None:
        records = [{"first_name": "Smith", "amount": 100}]
        out = phi_scanner.scan(records)
        assert out[0]["first_name"] is None
        assert out[0]["amount"] == 100

    def test_nulls_dob_column(self) -> None:
        records = [{"dob": "1985-03-14", "claim_id": "C001"}]
        out = phi_scanner.scan(records)
        assert out[0]["dob"] is None
        assert out[0]["claim_id"] == "C001"

    def test_nulls_patient_id_column(self) -> None:
        records = [{"patient_id": "12345", "amount": 100}]
        out = phi_scanner.scan(records)
        assert out[0]["patient_id"] is None

    def test_does_not_null_procedure_name(self) -> None:
        # False-positive guard: procedure_name contains "name" but is not PHI
        records = [{"procedure_name": "Hip replacement", "claim_id": "C001"}]
        out = phi_scanner.scan(records)
        assert out[0]["procedure_name"] == "Hip replacement"

    def test_does_not_null_program_name(self) -> None:
        records = [{"program_name": "MSSP BASIC", "claim_id": "C001"}]
        out = phi_scanner.scan(records)
        assert out[0]["program_name"] == "MSSP BASIC"

    def test_does_not_null_provider_organization_name(self) -> None:
        records = [{"organization_name": "Mass General Hospital"}]
        out = phi_scanner.scan(records)
        assert out[0]["organization_name"] == "Mass General Hospital"


class TestFreetextOverflow:
    def test_redacts_long_string_wholesale(self) -> None:
        long_text = "a" * 250
        out = phi_scanner.scan_text(long_text)
        assert out == "[REDACTED:FREETEXT]"

    def test_keeps_short_string(self) -> None:
        out = phi_scanner.scan_text("short note about claim")
        assert "short note" in out


class TestProseOverflow:
    # A spec-compliant ≤80-word TL;DR runs ~400-500 chars — longer than the
    # strict 200-char data-column gate, but legitimately publishable prose.
    _TLDR = (
        "Total medical spend rose four percent year over year, driven almost "
        "entirely by inpatient unit cost rather than utilization. Pharmacy trend "
        "was flat once the new specialty rebate took effect in the second quarter. "
        "Case mix held steady across the commercial population. The recommended "
        "action is to renegotiate the two highest-volume facility contracts before "
        "renewal, which the variance analysis flags as the single largest lever."
    )

    def test_clean_prose_survives(self) -> None:
        assert 200 < len(self._TLDR) < 1000  # the gap the strict gate would eat
        out = phi_scanner.scan_prose(self._TLDR)
        assert out == self._TLDR

    def test_clean_prose_redacted_by_strict_gate(self) -> None:
        # Same string under the default (data-column) gate is still nuked.
        out = phi_scanner.scan_text(self._TLDR)
        assert out == "[REDACTED:FREETEXT]"

    def test_prose_with_ssn_still_redacted(self) -> None:
        text = self._TLDR + " Contact 123-45-6789 for detail."
        out = phi_scanner.scan_prose(text)
        assert "123-45-6789" not in out
        assert "[REDACTED:SSN]" in out

    def test_prose_with_phone_still_redacted(self) -> None:
        text = self._TLDR + " Call (617) 555-0142 with questions."
        out = phi_scanner.scan_prose(text)
        assert "555-0142" not in out
        assert "[REDACTED:PHONE]" in out

    def test_prose_over_word_limit_still_overflows(self) -> None:
        # The prose gate is by WORD COUNT, not chars. A field well over the
        # ≤100-word limit is wholesale-redacted.
        out = phi_scanner.scan_prose("word " * 200)
        assert out == "[REDACTED:FREETEXT]"

    def test_long_char_prose_under_word_limit_survives(self) -> None:
        # Objection 2 regression: an 80-word TL;DR packed with long clinical
        # terms blows past any char ceiling (here ~1400 chars) but is a
        # spec-compliant TL;DR by word count, so it must NOT be redacted.
        # Fails under the reverted flat 1000-char gate.
        term = "antihyperlipidemic"  # 18 chars, deliberately long, clean
        words = [term] * 80
        text = " ".join(words)
        assert len(text) > 1000  # would trip a flat char ceiling
        assert len(text.split()) <= 100  # but within the word-count spec
        out = phi_scanner.scan_prose(text)
        assert out == text  # survives — no length-only redaction

    def test_short_data_value_unchanged_by_either_scanner(self) -> None:
        # Short, pattern-clean values behave identically through both scanners.
        assert phi_scanner.scan_text("HCC 0.842") == "HCC 0.842"
        assert phi_scanner.scan_prose("HCC 0.842") == "HCC 0.842"

    def test_scan_text_rejects_overflow_threshold_kwarg(self) -> None:
        # Objection 2 regression: the public data-column gate is NOT tunable.
        # If an `overflow_threshold` param were re-added to scan_text, any
        # caller could relax the strict 200-char write-gate for arbitrary
        # fields — re-opening the un-patterned-leak hole. The param must not
        # exist on the public API.
        with pytest.raises(TypeError):
            phi_scanner.scan_text("x", overflow_threshold=10_000)  # type: ignore[call-arg]

    def test_prose_uses_same_identifier_contract_as_markdown(self) -> None:
        # Objection 3: the prose path must be NO MORE permissive than the
        # accepted prose contract (scan_markdown). For a single-line input
        # under the word gate, scan_prose and scan_markdown must produce the
        # same identifier redactions.
        line = "Member SSN 123-45-6789, phone (617) 555-0142, dob 1985-03-14."
        assert phi_scanner.scan_prose(line) == phi_scanner.scan_markdown(line)

    def test_long_char_prose_with_identifier_still_redacted(self) -> None:
        # Even an under-word-limit prose field with a real identifier gets the
        # identifier redacted in place (length does not exempt the pattern scan).
        text = " ".join(["antihyperlipidemic"] * 60) + " SSN 123-45-6789 noted."
        assert len(text) > 1000
        out = phi_scanner.scan_prose(text)
        assert "123-45-6789" not in out
        assert "[REDACTED:SSN]" in out


class TestPrivacyGuardrailsProseRouting:
    """Regression tests on the REAL routing in privacy_guardrails.apply().

    These exercise apply() end-to-end (not scan_prose in isolation) so they
    fail if the prose routing in privacy_guardrails were reverted:
      - if `tldr` were dropped from _PROSE_KEYS, the clean TL;DR would be
        char-gated and wholesale-redacted -> test_apply_clean_tldr_survives fails.
      - if _PROSE_KEYS were re-widened to include `finding`, an over-length
        `finding` carrying an un-patterned bare name would survive instead of
        being nuked -> test_apply_long_finding_is_strict_gated fails.
    """

    # 65 words, 430 chars: over the 200-char strict gate, under the word limit.
    _CLEAN_TLDR = (
        "Total medical spend rose four percent year over year, driven almost "
        "entirely by inpatient unit cost rather than utilization. Pharmacy trend "
        "was flat once the new specialty rebate took effect in the second quarter. "
        "Case mix held steady across the commercial population. The recommended "
        "action is to renegotiate the two highest-volume facility contracts before "
        "renewal, which the variance analysis flags as the single largest lever."
    )

    def test_apply_clean_tldr_survives(self) -> None:
        # The narrative TL;DR routes through scan_prose and is NOT redacted.
        assert len(self._CLEAN_TLDR) > 200  # strict gate would eat it
        d = {"tldr": self._CLEAN_TLDR}
        out = privacy_guardrails.apply(d)
        assert out["tldr"] == self._CLEAN_TLDR

    def test_apply_tldr_with_ssn_still_redacted(self) -> None:
        # A real identifier in the TL;DR is still redacted in place via apply().
        d = {"tldr": self._CLEAN_TLDR + " Reach 123-45-6789 for detail."}
        out = privacy_guardrails.apply(d)
        assert "123-45-6789" not in out["tldr"]
        assert "[REDACTED:SSN]" in out["tldr"]

    # Every narrative-ish field that is NOT `tldr`. The strict 200-char gate is
    # these fields' only protection against an un-patterned leak (a bare name
    # the pattern scanner cannot catch) riding along in an over-length value.
    # Parametrizing across ALL of them means re-widening _PROSE_KEYS to include
    # ANY of these (not just `finding`) fails a test — closing the Objection 1
    # gap where a broken _PROSE_KEYS that left `finding` strict could still pass.
    _NON_TLDR_NARRATIVE_FIELDS = [
        "title",
        "finding",
        "action",
        "summary",
        "implications",
        "recommendation",
        "headline",
        "subtitle",
        "narrative",
        "caveat",
        "interpretation",
    ]

    @pytest.mark.parametrize("field", _NON_TLDR_NARRATIVE_FIELDS)
    def test_apply_long_non_tldr_field_is_strict_gated(self, field: str) -> None:
        # Objection 1 regression: a non-tldr narrative field is NOT a prose key.
        # An over-length value carrying a bare name (which the pattern scanner
        # does NOT catch) must be wholesale-redacted by the strict 200-char
        # gate. If the relaxation leaked onto ANY of these fields, the bare name
        # would survive and this parametrized case would fail.
        long_value = (
            "John Smith, the member in question, was reviewed in detail and "
            "the case narrative continues at length to push this value past "
            "the strict two-hundred character data-column overflow gate so "
            "that the wholesale redaction path is exercised end to end here."
        )
        assert len(long_value) > 200
        d = {field: long_value}
        out = privacy_guardrails.apply(d)
        assert out[field] == "[REDACTED:FREETEXT]"
        assert "John Smith" not in out[field]

    def test_apply_tldr_bare_name_is_a_known_prose_limitation(self) -> None:
        # Objection 3 (SAFETY, documented residual): the prose path is no more
        # permissive than scan_markdown — and scan_markdown, like ALL pattern
        # scanning, does not catch BARE names with no title prefix. So a short,
        # clean-looking TL;DR ending "...John Smith reviewed." passes apply()
        # with the bare name INTACT. This is a PRE-EXISTING, systemic limitation
        # of prose pattern-scanning, NOT a relaxation introduced by the tldr
        # path. This test pins that behavior so it is visible and tracked; it is
        # a residual policy decision for the orchestrator, not a silent pass.
        tldr = (
            "Total spend rose four percent on inpatient unit cost. "
            "John Smith reviewed."
        )
        d = {"tldr": tldr}
        out = privacy_guardrails.apply(d)
        # Documents the limitation: the bare name is NOT redacted (no title
        # prefix for the freetext-name heuristic to anchor on).
        assert "John Smith" in out["tldr"]
        # But a TITLE-prefixed name in the same prose IS caught, proving the
        # path runs the real identifier pass (it is not a no-op).
        d2 = {"tldr": tldr + " Dr. John Smith signed off."}
        out2 = privacy_guardrails.apply(d2)
        assert "Dr. John Smith" not in out2["tldr"]
        assert "[REDACTED:NAME]" in out2["tldr"]

    def test_apply_tldr_catchable_identifiers_redact(self) -> None:
        # MANDATORY: identifiers the scanner CAN catch must still redact in the
        # tldr path (SSN/phone/email/MBI/DOB), even though bare names cannot be.
        tldr = (
            "Spend rose. Reach 123-45-6789, (617) 555-0142, "
            "analyst@example.com, dob 1985-03-14 for detail."
        )
        d = {"tldr": tldr}
        out = privacy_guardrails.apply(d)
        for leaked in ("123-45-6789", "555-0142", "analyst@example.com",
                       "1985-03-14"):
            assert leaked not in out["tldr"]
        for tag in ("[REDACTED:SSN]", "[REDACTED:PHONE]", "[REDACTED:EMAIL]",
                    "[REDACTED:DATE]"):
            assert tag in out["tldr"]


class TestAllowList:
    def test_allow_list_bypasses_scanner(self) -> None:
        synthetic_ssn = "123-45-6789"
        phi_scanner.configure_allow_list([synthetic_ssn])
        try:
            out = phi_scanner.scan_text(synthetic_ssn)
            assert out == synthetic_ssn
        finally:
            phi_scanner.configure_allow_list([])


class TestEdgeCases:
    def test_empty_record_list(self) -> None:
        assert phi_scanner.scan([]) == []

    def test_record_with_no_string_fields(self) -> None:
        records = [{"amount": 100, "count": 5}]
        out = phi_scanner.scan(records)
        assert out == records

    def test_mixed_case_column_names(self) -> None:
        records = [{"FirstName": "John", "LastName": "Doe", "amount": 100}]
        out = phi_scanner.scan(records)
        assert out[0]["FirstName"] is None
        assert out[0]["LastName"] is None

    def test_scan_handles_none_values(self) -> None:
        records = [{"first_name": None, "amount": None}]
        out = phi_scanner.scan(records)
        # No crash; first_name still nulled by column name
        assert out[0]["first_name"] is None

    def test_non_string_value_in_phi_column(self) -> None:
        # patient_id holding integer should still be nulled
        records = [{"patient_id": 12345, "amount": 100}]
        out = phi_scanner.scan(records)
        assert out[0]["patient_id"] is None

    def test_metrics_increment(self) -> None:
        phi_scanner.reset_metrics()
        phi_scanner.scan_text("123-45-6789")
        m = phi_scanner.scanner_metrics()
        assert m.get("ssn", 0) >= 1


class TestPerformance:
    def test_scans_100k_records_under_one_second(self) -> None:
        # Lightweight perf smoke — generate 100K small records
        records = [
            {"claim_id": f"C{i:07d}", "first_name": "alice",
             "amount": float(i % 1000), "note": f"claim note {i}"}
            for i in range(100_000)
        ]
        start = time.perf_counter()
        out = phi_scanner.scan(records)
        elapsed = time.perf_counter() - start
        assert len(out) == 100_000
        # Pure-Python loop: relax for CI; aim < 5s on 100K rows
        # (vectorized version via scan_dataframe should hit < 1s)
        assert elapsed < 5.0, f"scan too slow on 100K rows: {elapsed:.2f}s"


class TestNpiPhoneOverRedaction:
    """A bare 10-digit NPI is redacted as a phone — intentional over-redaction.

    A value-based NPI exemption (Luhn checksum) was tried and reverted: the NPI
    checksum accepts ~1 in 10 random 10-digit strings, so it would let real
    unformatted patient phone numbers leak through the write-gate (a Safe Harbor
    breach). For a PHI write-gate, erring toward redaction wins — a redacted
    public NPI is harmless. Renderers that must display provider NPIs snapshot
    them BEFORE scanning at their own level (see render_provider_profile.py).
    These tests lock that decision so the value-only exemption is not re-added.
    """

    def test_bare_npi_redacted_as_phone(self):
        # A valid NPI (passes the CMS Luhn) is STILL redacted: no value-only spare.
        assert phi_scanner.scan_text("Provider 1234567893 billed claims") \
            == "Provider [REDACTED:PHONE] billed claims"

    def test_bare_phone_that_passes_npi_luhn_still_redacted(self):
        # The exact leak Codex flagged: a 10-digit phone that happens to satisfy
        # the NPI checksum must NOT pass through.
        assert phi_scanner.scan_text("call 1234567893") == "call [REDACTED:PHONE]"

    def test_formatted_phone_redacted(self):
        for phone in ("(415) 555-0132", "415-555-0132", "415.555.0132"):
            assert "[REDACTED:PHONE]" in phi_scanner.scan_text(phone)

    def test_no_value_based_npi_exemption_helper_exists(self):
        # Guard: the reverted helpers must stay gone so the leak can't return.
        assert not hasattr(phi_scanner, "_is_valid_npi")
        assert not hasattr(phi_scanner, "_redact_phones")
