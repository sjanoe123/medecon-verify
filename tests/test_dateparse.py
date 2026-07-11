"""Tests for utils.dateparse — the canonical best-effort date parser.

These lock in the format list that the per-skill ``_parse_date`` copies had
drifted away from. The four skill copies previously accepted only a subset
(missing ``%Y%m%d`` and ``%m-%d-%Y``), so a date in those formats was silently
dropped to ``None``. Consolidating onto this module fixed that; these tests
guard the fix.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from medecon_verify.dateparse import parse_date


class TestStringFormats:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("2026-03-15", date(2026, 3, 15)),   # ISO
            ("2026/03/15", date(2026, 3, 15)),   # ISO slash
            ("03/15/2026", date(2026, 3, 15)),   # US slash
            ("03-15-2026", date(2026, 3, 15)),   # US dash — dropped by old skill copies
            ("20260315", date(2026, 3, 15)),     # compact (CMS RIF) — dropped by old skill copies
        ],
    )
    def test_supported_formats(self, value: str, expected: date) -> None:
        assert parse_date(value) == expected

    def test_unparseable_string_returns_none(self) -> None:
        assert parse_date("not a date") is None
        assert parse_date("") is None

    @pytest.mark.parametrize("sentinel", ["999999", "9999", "0", "20240", "1234567"])
    def test_short_numeric_sentinels_are_not_coerced(self, sentinel: str) -> None:
        # %Y%m%d is delimiter-free; strptime would greedily coerce "999999" to
        # 9999-09-09. Only an exact 8-digit string may use that format, so
        # missing-date sentinels stay None instead of becoming far-future dates.
        assert parse_date(sentinel) is None

    def test_eight_digit_garbage_stays_none(self) -> None:
        assert parse_date("99999999") is None  # month 99 — strptime rejects


class TestObjectAndNone:
    def test_date_passthrough(self) -> None:
        d = date(2026, 1, 2)
        assert parse_date(d) is d

    def test_datetime_narrows_to_date(self) -> None:
        assert parse_date(datetime(2026, 1, 2, 13, 45)) == date(2026, 1, 2)

    def test_none_returns_none(self) -> None:
        assert parse_date(None) is None

    def test_unrecognized_type_returns_none(self) -> None:
        assert parse_date(12345) is None
