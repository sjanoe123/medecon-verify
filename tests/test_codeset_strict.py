"""Tests for codeset.stamp(..., strict=True) — fail-closed renewal enforcement.

Default mode (strict=False) must be byte-for-byte the historical behavior:
an out-of-registry vintage yields a visible "UNKNOWN" stamp. Strict mode turns
that same coverage gap into a loud CodesetVersionError naming the missing
registry and the current reference-data package.
"""
from __future__ import annotations

import types
from datetime import date

import pytest

from medecon_verify import _data_discovery as dd
from medecon_verify import codeset as cv


# A vintage no bundled registry covers (bundled ICD-10-CM tops out at FY2026).
_OUT_OF_REGISTRY = date(2027, 5, 1)
# A vintage fully covered by the bundled tier.
_COVERED = date(2026, 5, 1)


# --------------------------------------------------------------------------- #
# Default mode is unchanged (SemVer-safe)
# --------------------------------------------------------------------------- #
def test_default_mode_yields_unknown_for_out_of_registry():
    out = cv.stamp({}, asof=_OUT_OF_REGISTRY)
    assert out["code_set_versions"]["icd10cm_fy"] == "UNKNOWN"
    # cms_hcc / hedis are rule-derived, never UNKNOWN.
    assert out["code_set_versions"]["cms_hcc"] == "v28"


def test_default_mode_strict_false_is_the_documented_default():
    # Calling without strict= must behave exactly like strict=False.
    a = cv.stamp({}, asof=_OUT_OF_REGISTRY)
    b = cv.stamp({}, asof=_OUT_OF_REGISTRY, strict=False)
    assert a["code_set_versions"] == b["code_set_versions"]


# --------------------------------------------------------------------------- #
# Strict mode raises on the coverage gap
# --------------------------------------------------------------------------- #
def test_strict_raises_on_out_of_registry_vintage():
    with pytest.raises(cv.CodesetVersionError) as exc:
        cv.stamp({}, asof=_OUT_OF_REGISTRY, strict=True)
    msg = str(exc.value)
    assert "icd10cm_fy" in msg          # names the missing registry
    assert "2027-05-01" in msg          # names the uncovered date
    assert "medecon-verify-data" in msg  # names the package to renew


def test_strict_message_names_bundled_tier_when_no_feed():
    with pytest.raises(cv.CodesetVersionError) as exc:
        cv.stamp({}, asof=_OUT_OF_REGISTRY, strict=True)
    assert "bundled lagged free tier" in str(exc.value)


def test_strict_passes_on_covered_vintage():
    out = cv.stamp({}, asof=_COVERED, strict=True)
    assert out["code_set_versions"]["icd10cm_fy"] == "FY2026"
    assert out["code_set_versions"]["hcpcs_quarter"] == "2026Q2"
    assert out["code_set_versions"]["ms_drg"] == "v43"


def test_strict_override_escape_hatch_all_fields():
    # Explicit overrides for every date-windowed field satisfy strict even for
    # an out-of-registry asof — the caller has taken responsibility.
    deliv = {
        "code_set_versions": {
            "icd10cm_fy": "FY2027",
            "hcpcs_quarter": "2027Q2",
            "ms_drg": "v44",
        }
    }
    out = cv.stamp(deliv, asof=_OUT_OF_REGISTRY, strict=True)
    assert out["code_set_versions"]["icd10cm_fy"] == "FY2027"


def test_strict_partial_override_still_raises_on_next_gap():
    # Overriding only icd10cm_fy leaves hcpcs_quarter uncovered -> strict raises
    # naming that next field, not icd10cm_fy.
    deliv = {"code_set_versions": {"icd10cm_fy": "FY2027"}}
    with pytest.raises(cv.CodesetVersionError) as exc:
        cv.stamp(deliv, asof=_OUT_OF_REGISTRY, strict=True)
    assert "hcpcs_quarter" in str(exc.value)


def test_strict_names_installed_feed_version_when_present(monkeypatch):
    # An installed (compatible) feed that still lacks the vintage -> the strict
    # message names that feed's version, not the bundled tier.
    class _Provider:
        DATA_API_VERSION = 1
        DATA_PACKAGE_VERSION = "2026Q4"

        def get_registry(self, name):
            return None  # carries nothing for this test -> bundled falls short

    class _EP:
        dist = None

        def load(self):
            return _Provider()

    monkeypatch.setattr(dd, "_iter_data_entry_points", lambda: [_EP()])
    with pytest.raises(cv.CodesetVersionError) as exc:
        cv.stamp({}, asof=_OUT_OF_REGISTRY, strict=True)
    assert "medecon-verify-data 2026Q4" in str(exc.value)


# --------------------------------------------------------------------------- #
# Public-contract signature is preserved
# --------------------------------------------------------------------------- #
def test_stamp_signature_has_strict_keyword_only():
    import inspect

    sig = inspect.signature(cv.stamp)
    assert "strict" in sig.parameters
    assert sig.parameters["strict"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["strict"].default is False
    assert sig.parameters["asof"].default is None
