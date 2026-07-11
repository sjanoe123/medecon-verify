"""Canonical best-effort date parser — one home for the format list.

Several skill scripts and ``codeset_version`` independently grew their own
``_parse_date`` helpers. They drifted: some accepted only ``%Y-%m-%d`` and
``%m/%d/%Y`` while claims data routinely arrives as ``%Y%m%d`` (CMS RIF) or
``%m-%d-%Y``. A date the narrow parsers could not read was silently dropped
(``None``) — exactly the "confidently wrong number" failure the repo guards
against. This module is the single source of truth for that format list.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Order matters only for ambiguous strings; these formats do not overlap.
_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y", "%Y%m%d")


def parse_date(value: Any) -> date | None:
    """Best-effort parse a value into a ``date``; ``None`` if unparseable.

    Accepts ``date``/``datetime`` objects directly and the common claims-data
    string formats. Returns ``None`` for ``None`` or any unrecognized input.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in _FORMATS:
            # %Y%m%d is delimiter-free, so strptime parses it greedily and would
            # coerce short numeric missing-date sentinels ("999999" -> 9999-09-09)
            # into far-future dates. Require exactly 8 digits so sentinels stay
            # unparsed (None) rather than silently becoming valid-looking dates.
            if fmt == "%Y%m%d" and not (len(value) == 8 and value.isdigit()):
                continue
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None
