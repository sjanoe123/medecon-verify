"""medecon-verify — the correctness-and-compliance layer for healthcare AI agents.

A no-third-party-dependency core library extracted from the MIT-licensed
medecon-stack plugin. Public API modules:

    adjudication  final-action dedup, O(N) reversal-pair detection, FY-boundary warnings
    codeset       ICD-10-CM / HCPCS / MS-DRG / CMS-HCC vintage stamping
    privacy       n<=10 cell suppression, age-90+ aggregation, 42 CFR Part 2 flag
    phi           heuristic, pattern-based Safe Harbor identifier redaction
    dateparse     tolerant date parsing shared by the above
    glossary      healthcare-economics term glossary (PMPM, MLR, HCC, RAF, ...)

The ``[cli]`` extra adds the ``medecon-verify`` command-line entry point.
"""
from __future__ import annotations

from . import adjudication, codeset, dateparse, glossary, phi, privacy

try:  # installed distribution metadata
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("medecon-verify")
except Exception:  # pragma: no cover - source checkout without installed metadata
    __version__ = "1.0.0"

__all__ = [
    "adjudication",
    "codeset",
    "dateparse",
    "glossary",
    "phi",
    "privacy",
    "__version__",
]
