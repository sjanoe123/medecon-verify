"""Tests for phi_scanner.scan_markdown — markdown-document scanner.

scan_text was designed for record fields and nukes anything > 200 chars
with a wholesale [REDACTED:FREETEXT] sentinel. scan_markdown applies the
same patterns line-by-line, no overflow gate, suitable for sub-agent
markdown outputs.
"""
from __future__ import annotations


from medecon_verify import phi as phi_scanner


def test_scan_markdown_does_not_nuke_long_documents():
    """A 3000-char markdown doc must survive intact (modulo PHI redactions)."""
    body = "## Heading\n\n" + ("This is a normal sentence. " * 500)
    out = phi_scanner.scan_markdown(body)
    assert "Heading" in out
    assert "[REDACTED:FREETEXT]" not in out
    assert "normal sentence" in out


def test_scan_markdown_redacts_ssn_in_place():
    body = "Member identified by SSN 123-45-6789 had high spend."
    out = phi_scanner.scan_markdown(body)
    assert "123-45-6789" not in out
    assert "[REDACTED:SSN]" in out


def test_scan_markdown_redacts_email_phone_url_per_line():
    body = (
        "Contact: jane.doe@hospital.example\n"
        "Phone: 555-123-4567\n"
        "URL: https://example.org/path\n"
        "Date: 2025-01-15\n"
        "All other content remains intact.\n"
    )
    out = phi_scanner.scan_markdown(body)
    assert "jane.doe@hospital.example" not in out
    assert "555-123-4567" not in out
    assert "https://example.org/path" not in out
    assert "2025-01-15" not in out
    assert "All other content remains intact." in out


def test_scan_markdown_handles_empty_string():
    assert phi_scanner.scan_markdown("") == ""


def test_scan_markdown_handles_non_string():
    assert phi_scanner.scan_markdown(None) is None  # type: ignore[arg-type]


def test_scan_markdown_returns_unchanged_when_no_phi():
    body = "## Top of file\n\nFindings:\n- Total = 100\n- Mean = 25.5\n"
    out = phi_scanner.scan_markdown(body)
    assert out == body


def test_scan_text_still_nukes_long_field_values():
    """Backward compatibility: scan_text retains its overflow gate."""
    long_field = "x" * 500
    out = phi_scanner.scan_text(long_field)
    assert out == "[REDACTED:FREETEXT]"
