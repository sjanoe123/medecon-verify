"""Tests for the FY2027 extractor's *parsing and cross-check logic*.

Finding (codex GPT-5.6, task 1.1): the FY2027 registry tests only re-asserted the
already-committed JSON literals — none exercised `tools/extract_icd10cm_fy2027.py`'s
actual parsers. A wrong column offset, a changed ZIP layout, a malformed
conversion-file member, a broken cross-check, or a write-path regression would
have left the suite green because nothing drove the code that produces the value.

These tests close that gap. They build **synthetic** ZIP fixtures with
independently-known ground truth (never real PHI or real CMS bytes) and drive the
real functions: the fixed-column order-file parser, the codes-file counter, the
ZIP-member reader, the conversion-filename parser, every cross-check inside
`derive_fy2027`, and the registry write path `apply_to_registry`.

A separate `optional`-marked test drives the extractor end-to-end over the real
downloaded CMS sources when they are present, tying the committed registry row and
the workpaper's counts back to the actual artifacts. It skips (never fails) in an
environment without the ~/.gstack sources, so CI stays green while local runs get
the real-artifact check.
"""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Load the tools/ extractor by path (it is a script, not an installed package).
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXTRACTOR = _REPO_ROOT / "tools" / "extract_icd10cm_fy2027.py"


def _load_extractor():
    spec = importlib.util.spec_from_file_location("extract_icd10cm_fy2027", _EXTRACTOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ex = _load_extractor()


# --------------------------------------------------------------------------- #
# Synthetic fixture builders (ground truth is whatever we put in).
# --------------------------------------------------------------------------- #
def _order_line(order_no: int, code: str, flag: str, desc: str) -> str:
    """A CMS-shaped fixed-column order line with the billable flag at offset 14.

    Layout: 5-char order number, space, 7-char code, space, flag (index 14),
    space, description. Mirrors the real icd10cm_order_YYYY.txt column geometry
    the parser depends on — so a wrong offset here would be caught downstream.
    """
    line = f"{order_no:05d} {code:<7} {flag} {desc}"
    assert line[ex._ORDER_FLAG_OFFSET] == flag  # guard the fixture itself
    return line


def _order_bytes(n_billable: int, n_header: int) -> bytes:
    lines = []
    i = 0
    for _ in range(n_billable):
        i += 1
        lines.append(_order_line(i, f"A{i:02d}", "1", "billable code"))
    for _ in range(n_header):
        i += 1
        lines.append(_order_line(i, f"B{i:02d}", "0", "category header"))
    return ("\n".join(lines) + "\n").encode("latin-1")


def _codes_bytes(n: int) -> bytes:
    return ("\n".join(f"A{j:02d} short title" for j in range(1, n + 1)) + "\n").encode(
        "latin-1"
    )


def _write_desc_zip(path: Path, n_billable: int, n_header: int, codes_lines: int) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Code Descriptions/icd10cm_order_2027.txt", _order_bytes(n_billable, n_header))
        zf.writestr("Code Descriptions/icd10cm_codes_2027.txt", _codes_bytes(codes_lines))


def _write_conversion_zip(path: Path, member: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, b"synthetic conversion table body")


_FINAL_MEMBER = "ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - FINAL-.xlsx"


# --------------------------------------------------------------------------- #
# Unit: fixed-column order-file parser
# --------------------------------------------------------------------------- #
class TestCountOrderLines:
    def test_counts_billable_and_header_by_offset_14(self):
        raw = _order_bytes(n_billable=3, n_header=2)
        total, billable, header = ex._count_order_lines(raw)
        assert (total, billable, header) == (5, 3, 2)

    def test_blank_lines_are_ignored(self):
        raw = _order_bytes(2, 1) + b"\n   \n"
        total, billable, header = ex._count_order_lines(raw)
        assert (total, billable, header) == (3, 2, 1)

    def test_line_too_short_to_carry_flag_raises(self):
        with pytest.raises(ex.ExtractionError, match="too short"):
            ex._count_order_lines(b"0001 A00\n")  # no char at offset 14

    def test_unexpected_flag_value_raises(self):
        bad = _order_line(1, "A00", "9", "bad flag").encode("latin-1") + b"\n"
        with pytest.raises(ex.ExtractionError, match="unexpected billable flag"):
            ex._count_order_lines(bad)


class TestCountCodesLines:
    def test_counts_nonblank_lines(self):
        assert ex._count_codes_lines(_codes_bytes(7)) == 7

    def test_ignores_trailing_blank(self):
        assert ex._count_codes_lines(b"A00 x\nA01 y\n\n") == 2


# --------------------------------------------------------------------------- #
# Unit: ZIP member reader
# --------------------------------------------------------------------------- #
class TestReadMember:
    def test_returns_the_single_match(self, tmp_path):
        z = tmp_path / "desc.zip"
        _write_desc_zip(z, 1, 0, 1)
        name, raw = ex._read_member(z, ex.ORDER_FILE_RE)
        assert name.endswith("icd10cm_order_2027.txt")
        assert raw

    def test_zero_matches_raises(self, tmp_path):
        z = tmp_path / "empty.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("unrelated.txt", b"x")
        with pytest.raises(ex.ExtractionError, match="found 0"):
            ex._read_member(z, ex.ORDER_FILE_RE)

    def test_multiple_matches_raises(self, tmp_path):
        z = tmp_path / "dup.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a/icd10cm_order_2027.txt", b"x")
            zf.writestr("b/icd10cm_order_2027.txt", b"y")
        with pytest.raises(ex.ExtractionError, match="found 2"):
            ex._read_member(z, ex.ORDER_FILE_RE)


# --------------------------------------------------------------------------- #
# Unit: conversion-filename parser
# --------------------------------------------------------------------------- #
class TestDeriveEffectiveFromConversion:
    def test_parses_fy_date_and_final_status(self, tmp_path):
        z = tmp_path / "conv.zip"
        _write_conversion_zip(z, _FINAL_MEMBER)
        from datetime import date

        fy, eff, status = ex._derive_effective_from_conversion(z)
        assert fy == 2027
        assert eff == date(2026, 10, 1)
        assert status == "FINAL"

    def test_parses_proposed_status(self, tmp_path):
        z = tmp_path / "conv.zip"
        _write_conversion_zip(
            z, "ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - PROPOSED-.xlsx"
        )
        _, _, status = ex._derive_effective_from_conversion(z)
        assert status == "PROPOSED"

    def test_no_conversion_member_raises(self, tmp_path):
        z = tmp_path / "conv.zip"
        _write_conversion_zip(z, "SomethingElse.xlsx")
        with pytest.raises(ex.ExtractionError, match="no CONVERSION-TABLE"):
            ex._derive_effective_from_conversion(z)

    def test_multiple_agreeing_members_are_accepted(self, tmp_path):
        # The real CMS ZIP ships the table as both a 508 CSV and an XLSX; both
        # encode the same FY/date/status, so all matching members agree and the
        # parser returns that shared value.
        z = tmp_path / "conv.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(
                "508-VERSION-ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - FINAL-.csv",
                b"csv body",
            )
            zf.writestr(_FINAL_MEMBER, b"xlsx body")
        from datetime import date

        fy, eff, status = ex._derive_effective_from_conversion(z)
        assert (fy, eff, status) == (2027, date(2026, 10, 1), "FINAL")

    def test_members_disagreeing_on_status_fail_closed(self, tmp_path):
        # One FINAL + one PROPOSED member. Accepting the first by archive order
        # would silently pick a status on member ordering alone; all matching
        # members must agree or the parser fails closed.
        z = tmp_path / "conv.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(_FINAL_MEMBER, b"xlsx body")
            zf.writestr(
                "508-VERSION-ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - PROPOSED-.csv",
                b"csv body",
            )
        with pytest.raises(ex.ExtractionError, match="disagree"):
            ex._derive_effective_from_conversion(z)

    def test_members_disagreeing_on_effective_date_fail_closed(self, tmp_path):
        # Same status, conflicting effective dates across members -> fail closed.
        z = tmp_path / "conv.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(_FINAL_MEMBER, b"xlsx body")
            zf.writestr(
                "508-VERSION-ICD-10-CM-CONVERSION-TABLE-FY2027-October 2 2026 - FINAL-.csv",
                b"csv body",
            )
        with pytest.raises(ex.ExtractionError, match="disagree"):
            ex._derive_effective_from_conversion(z)


# --------------------------------------------------------------------------- #
# Integration: derive_fy2027 over synthetic sources (checksums monkeypatched).
# --------------------------------------------------------------------------- #
def _stage_sources(tmp_path: Path, *, desc_counts=(3, 2, 3), conv_member=_FINAL_MEMBER):
    """Create a source dir with the two parsed ZIPs and a checksum manifest.

    Returns (source_dir, sources_manifest) where sources_manifest is a drop-in
    replacement for ex.SOURCES whose SHA-256 literals match the files on disk.
    """
    n_bill, n_head, codes = desc_counts
    desc = tmp_path / "2027-code-descriptions-tabular-order.zip"
    conv = tmp_path / "2027-conversion-table.zip"
    _write_desc_zip(desc, n_bill, n_head, codes)
    _write_conversion_zip(conv, conv_member)
    sources = {
        "2027-code-descriptions-tabular-order.zip": (ex._sha256(desc), "https://x/desc.zip", True),
        "2027-conversion-table.zip": (ex._sha256(conv), "https://x/conv.zip", True),
    }
    return tmp_path, sources


class TestDeriveFy2027Integration:
    def test_happy_path_derives_expected_row_and_counts(self, tmp_path, monkeypatch):
        src, sources = _stage_sources(tmp_path, desc_counts=(3, 2, 3))
        monkeypatch.setattr(ex, "SOURCES", sources)
        derived = ex.derive_fy2027(src)
        assert derived["fy_key"] == "FY2027"
        assert derived["effective"] == "2026-10-01"
        assert derived["obsolete"] == "2027-09-30"
        assert derived["counts"] == {
            "billable": 3,
            "header_non_billable": 2,
            "total_order_entries": 5,
            "codes_file_lines": 3,
        }
        assert derived["conversion_table_status"] == "FINAL"

    def test_checksum_mismatch_fails_closed(self, tmp_path, monkeypatch):
        src, sources = _stage_sources(tmp_path)
        # Corrupt the recorded checksum for the desc ZIP.
        bad = dict(sources)
        name = "2027-code-descriptions-tabular-order.zip"
        bad[name] = ("0" * 64, sources[name][1], True)
        monkeypatch.setattr(ex, "SOURCES", bad)
        with pytest.raises(ex.ExtractionError, match="SHA-256 mismatch"):
            ex.derive_fy2027(src)

    def test_missing_source_fails_closed(self, tmp_path, monkeypatch):
        src, sources = _stage_sources(tmp_path)
        (src / "2027-conversion-table.zip").unlink()
        monkeypatch.setattr(ex, "SOURCES", sources)
        with pytest.raises(ex.ExtractionError, match="source file missing"):
            ex.derive_fy2027(src)

    def test_proposed_conversion_table_is_refused(self, tmp_path, monkeypatch):
        src, sources = _stage_sources(
            tmp_path,
            conv_member="ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - PROPOSED-.xlsx",
        )
        monkeypatch.setattr(ex, "SOURCES", sources)
        with pytest.raises(ex.ExtractionError, match="not FINAL"):
            ex.derive_fy2027(src)

    def test_billable_vs_codes_count_disagreement_is_refused(self, tmp_path, monkeypatch):
        # 3 billable in the order file but 4 lines in the codes file -> cross-check fails.
        src, sources = _stage_sources(tmp_path, desc_counts=(3, 2, 4))
        monkeypatch.setattr(ex, "SOURCES", sources)
        with pytest.raises(ex.ExtractionError, match="billable count"):
            ex.derive_fy2027(src)

    def test_non_oct01_effective_date_is_refused(self, tmp_path, monkeypatch):
        # Conversion filename says October 2 2026 -> off the statutory Oct-01 boundary.
        src, sources = _stage_sources(
            tmp_path,
            conv_member="ICD-10-CM-CONVERSION-TABLE-FY2027-October 2 2026 - FINAL-.xlsx",
        )
        monkeypatch.setattr(ex, "SOURCES", sources)
        with pytest.raises(ex.ExtractionError, match="statutory Oct-01 boundary"):
            ex.derive_fy2027(src)


# --------------------------------------------------------------------------- #
# Unit: registry write path.
# --------------------------------------------------------------------------- #
class TestApplyToRegistry:
    def test_writes_row_sorted_and_is_idempotent(self, tmp_path):
        reg = tmp_path / "icd10cm_fy.json"
        reg.write_text(
            json.dumps(
                {
                    "FY2026": {"effective": "2025-10-01", "obsolete": "2026-09-30"},
                    "FY2025": {"effective": "2024-10-01", "obsolete": "2025-09-30"},
                }
            ),
            encoding="utf-8",
        )
        derived = {
            "fy_key": "FY2027",
            "effective": "2026-10-01",
            "obsolete": "2027-09-30",
        }
        changed = ex.apply_to_registry(derived, reg)
        assert changed is True
        after = json.loads(reg.read_text(encoding="utf-8"))
        assert after["FY2027"] == {"effective": "2026-10-01", "obsolete": "2027-09-30"}
        # Pre-existing rows are preserved untouched.
        assert after["FY2026"] == {"effective": "2025-10-01", "obsolete": "2026-09-30"}
        # FY-sorted output.
        assert list(after.keys()) == ["FY2025", "FY2026", "FY2027"]
        # Re-running is a no-op.
        assert ex.apply_to_registry(derived, reg) is False


# --------------------------------------------------------------------------- #
# Deferral guard: the v44 stub must fail loudly, never silently emit a row.
# --------------------------------------------------------------------------- #
def test_extract_ms_drg_v44_is_deferred_and_raises(tmp_path):
    with pytest.raises(NotImplementedError, match="DEFERRED"):
        ex.extract_ms_drg_v44(tmp_path)


# --------------------------------------------------------------------------- #
# Real-source check: drive the real extractor over the real downloaded CMS
# sources and pin BOTH the parser output and the committed registry row to
# independent static literals (workpaper section 1 + the statutory Oct-01
# boundary) — never to each other. Comparing the committed JSON to the parser's
# own fresh output is circular: a shared wrong derivation (both written by the
# same buggy parser) would agree and pass. Checking each side against a constant
# catches that.
#
# Not `optional`-marked: the sources are local files (no network, no
# credentials), so this is collected by the default suite and SKIPS cleanly when
# the ~/.gstack sources are absent (hermetic CI stays green) while the
# release-gate run on a machine that has them actually verifies the committed
# artifacts against the real CMS files.
# --------------------------------------------------------------------------- #

# Independently-recorded expectations. These constants are the static source of
# truth; both the parser output and the committed JSON are checked against them.
_EXPECTED_FY2027_ROW = {"effective": "2026-10-01", "obsolete": "2027-09-30"}
_EXPECTED_FY2027_COUNTS = {
    "billable": 74879,
    "header_non_billable": 23524,
    "total_order_entries": 98403,
    "codes_file_lines": 74879,
}


def test_real_sources_reproduce_committed_registry_and_workpaper_counts():
    src = ex.DEFAULT_SOURCE_DIR
    if not src.is_dir() or not all((src / n).is_file() for n in ex.SOURCES):
        pytest.skip(f"real CMS FY2027 sources not present at {src}")
    derived = ex.derive_fy2027(src)  # verifies real SHA-256s, then parses
    # (1) The parser's fresh derivation must equal the independent static row ...
    assert {
        "effective": derived["effective"],
        "obsolete": derived["obsolete"],
    } == _EXPECTED_FY2027_ROW
    # (2) ... and the committed registry row must equal the SAME static row —
    # checked against the constant, not against `derived`, so a shared wrong
    # derivation cannot make both sides agree on a wrong value.
    committed = json.loads(ex.REGISTRY_PATH.read_text(encoding="utf-8"))
    assert committed["FY2027"] == _EXPECTED_FY2027_ROW
    # (3) The workpaper's provenance counts must equal what the real files derive.
    assert derived["counts"] == _EXPECTED_FY2027_COUNTS
