"""Packaging-level tests for the externalized registries + strict mode.

The plan's exit criterion (1.1.1): "a built wheel with no data package raises
loudly in strict mode; with the data package it loads full tables." These tests
exercise the same contract against the installed/importable package:

  * the bundled free-tier JSON is present and byte-for-byte faithful to the
    values that used to be hard-coded in codeset.py;
  * with no data package installed, strict mode on an out-of-registry vintage
    raises loudly, while default mode still yields UNKNOWN;
  * swapping in a feed (via _reload_registries against the discovery seam) makes
    the previously-uncovered vintage resolve and strict pass — proving the
    installed tier supersedes the bundled one end to end.
"""
from __future__ import annotations

import json
from datetime import date
from importlib import resources

import pytest

from medecon_verify import _data_discovery as dd
from medecon_verify import codeset as cv


@pytest.fixture(autouse=True)
def _restore_registries():
    """Reset codeset's module-level registries to the bundled tier after each test."""
    yield
    cv._reload_registries()


# --------------------------------------------------------------------------- #
# Bundled data files ship and are faithful
# --------------------------------------------------------------------------- #
def _bundled(name):
    res = resources.files("medecon_verify") / "data" / "registries" / f"{name}.json"
    with res.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def test_all_registry_files_are_packaged():
    for name in ("icd10cm_fy", "hcpcs_quarter", "ms_drg", "cms_hcc", "hedis"):
        res = resources.files("medecon_verify") / "data" / "registries" / f"{name}.json"
        assert res.is_file(), f"{name}.json not packaged as data"


def test_icd10cm_fy_faithful():
    assert _bundled("icd10cm_fy") == {
        "FY2022": {"effective": "2021-10-01", "obsolete": "2022-09-30"},
        "FY2023": {"effective": "2022-10-01", "obsolete": "2023-09-30"},
        "FY2024": {"effective": "2023-10-01", "obsolete": "2024-09-30"},
        "FY2025": {"effective": "2024-10-01", "obsolete": "2025-09-30"},
        "FY2026": {"effective": "2025-10-01", "obsolete": "2026-09-30"},
        # FY2027 derived from verified CMS FY2027 ICD-10-CM release ZIPs by
        # tools/extract_icd10cm_fy2027.py (docs/workpapers/fy2027-registry-sources.md).
        "FY2027": {"effective": "2026-10-01", "obsolete": "2027-09-30"},
    }


def test_hcpcs_quarter_faithful():
    assert _bundled("hcpcs_quarter") == {
        "2025Q1": {"effective": "2025-01-01", "obsolete": "2025-03-31"},
        "2025Q2": {"effective": "2025-04-01", "obsolete": "2025-06-30"},
        "2025Q3": {"effective": "2025-07-01", "obsolete": "2025-09-30"},
        "2025Q4": {"effective": "2025-10-01", "obsolete": "2025-12-31"},
        "2026Q1": {"effective": "2026-01-01", "obsolete": "2026-03-31"},
        "2026Q2": {"effective": "2026-04-01", "obsolete": "2026-06-30"},
    }


def test_ms_drg_faithful():
    assert _bundled("ms_drg") == {
        "v40": {"fy": "FY2023", "effective": "2022-10-01"},
        "v41": {"fy": "FY2024", "effective": "2023-10-01"},
        "v42": {"fy": "FY2025", "effective": "2024-10-01"},
        "v43": {"fy": "FY2026", "effective": "2025-10-01"},
    }


def test_cms_hcc_faithful():
    assert _bundled("cms_hcc") == {
        "v24": {"applies_to": "PY2017–PY2023", "blend_to_v28": False},
        "v28": {"applies_to": "PY2024+", "blend_to_v28": True,
                "phase_in": {"PY2024": 0.33, "PY2025": 0.67, "PY2026": 1.0}},
    }


def test_hedis_faithful():
    assert _bundled("hedis") == {
        "MY2024": {"reporting_year": 2025},
        "MY2025": {"reporting_year": 2026},
        "MY2026": {"reporting_year": 2027},
    }


def test_codeset_module_globals_match_bundled_files():
    # The module loaded its globals from the bundled files at import — they must
    # equal the on-disk data (no divergent copy left in code).
    assert cv.ICD10CM_FY_REGISTRY == _bundled("icd10cm_fy")
    assert cv.HCPCS_QUARTER_REGISTRY == _bundled("hcpcs_quarter")
    assert cv.MS_DRG_REGISTRY == _bundled("ms_drg")
    assert cv.CMS_HCC_REGISTRY == _bundled("cms_hcc")
    assert cv.HEDIS_REGISTRY == _bundled("hedis")


# --------------------------------------------------------------------------- #
# No data package: strict raises loudly, default yields UNKNOWN
# --------------------------------------------------------------------------- #
def test_no_data_package_default_yields_unknown():
    # FY2027 is now bundled (tools/extract_icd10cm_fy2027.py); the next
    # out-of-registry ICD-10-CM vintage is FY2028 (asof 2028-05-01).
    assert dd.has_data_package() is False
    out = cv.stamp({}, asof=date(2028, 5, 1))
    assert out["code_set_versions"]["icd10cm_fy"] == "UNKNOWN"


def test_no_data_package_strict_raises_loudly():
    assert dd.has_data_package() is False
    with pytest.raises(cv.CodesetVersionError):
        cv.stamp({}, asof=date(2028, 5, 1), strict=True)


# --------------------------------------------------------------------------- #
# Installed feed: full tables load, strict passes end to end
# --------------------------------------------------------------------------- #
class _Feed:
    DATA_API_VERSION = 1
    DATA_PACKAGE_VERSION = "2026Q4"

    def __init__(self, registries):
        self._registries = registries

    def get_registry(self, name):
        return self._registries.get(name)


class _EP:
    def __init__(self, feed):
        self._feed = feed
        self.dist = None

    def load(self):
        return self._feed


def test_installed_feed_makes_2027_resolve_and_strict_pass(monkeypatch):
    feed = _Feed({
        "icd10cm_fy": {
            "FY2026": {"effective": "2025-10-01", "obsolete": "2026-09-30"},
            "FY2027": {"effective": "2026-10-01", "obsolete": "2027-09-30"},
        },
        "hcpcs_quarter": {
            "2027Q2": {"effective": "2027-04-01", "obsolete": "2027-06-30"},
        },
        "ms_drg": {"v44": {"fy": "FY2027", "effective": "2026-10-01"}},
    })
    monkeypatch.setattr(dd, "_iter_data_entry_points", lambda: [_EP(feed)])
    cv._reload_registries()

    out = cv.stamp({}, asof=date(2027, 5, 1), strict=True)
    stamps = out["code_set_versions"]
    assert stamps["icd10cm_fy"] == "FY2027"
    assert stamps["hcpcs_quarter"] == "2027Q2"
    assert stamps["ms_drg"] == "v44"
