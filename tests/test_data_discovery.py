"""Tests for the reference-data discovery / loading precedence chain.

Covers `_data_discovery`: bundled free-tier loading, installed-feed precedence
via the entry-point seam, version-compat skipping, and the RegistryNotFound
error path. The entry-point group is exercised through the `_iter_data_entry_points`
seam so no real `medecon-verify-data` distribution needs to be installed.
"""
from __future__ import annotations

import types

import pytest

from medecon_verify import _data_discovery as dd


# --------------------------------------------------------------------------- #
# Fakes for the entry-point seam
# --------------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self, registries, *, api=1, version="2026Q4"):
        self._registries = registries
        self.DATA_API_VERSION = api
        self.DATA_PACKAGE_VERSION = version

    def get_registry(self, name):
        return self._registries.get(name)


class _FakeEntryPoint:
    def __init__(self, provider, *, dist_version=None):
        self._provider = provider
        self.dist = (
            types.SimpleNamespace(version=dist_version) if dist_version else None
        )

    def load(self):
        return self._provider


@pytest.fixture
def install_feed(monkeypatch):
    """Return a helper that installs fake providers on the discovery seam."""

    def _install(*entry_points):
        monkeypatch.setattr(dd, "_iter_data_entry_points", lambda: list(entry_points))

    return _install


# --------------------------------------------------------------------------- #
# Bundled free tier
# --------------------------------------------------------------------------- #
def test_bundled_tier_loads_each_registry():
    for name in ("icd10cm_fy", "hcpcs_quarter", "ms_drg", "cms_hcc", "hedis"):
        reg = dd.load_registry(name)
        assert isinstance(reg, dict) and reg, f"{name} bundled registry is empty"


def test_bundled_icd10_values_are_faithful():
    reg = dd.load_registry("icd10cm_fy")
    assert reg["FY2026"] == {"effective": "2025-10-01", "obsolete": "2026-09-30"}
    # FY2027 added by tools/extract_icd10cm_fy2027.py from the verified CMS
    # FY2027 ICD-10-CM release ZIPs (see docs/workpapers/fy2027-registry-sources.md).
    assert reg["FY2027"] == {"effective": "2026-10-01", "obsolete": "2027-09-30"}
    assert set(reg) == {"FY2022", "FY2023", "FY2024", "FY2025", "FY2026", "FY2027"}


def test_bundled_cms_hcc_phase_in_faithful():
    reg = dd.load_registry("cms_hcc")
    assert reg["v28"]["phase_in"] == {"PY2024": 0.33, "PY2025": 0.67, "PY2026": 1.0}
    assert reg["v24"]["blend_to_v28"] is False
    assert reg["v28"]["blend_to_v28"] is True


def test_load_registry_returns_a_fresh_copy():
    a = dd.load_registry("icd10cm_fy")
    a["FY2026"]["effective"] = "MUTATED"
    b = dd.load_registry("icd10cm_fy")
    assert b["FY2026"]["effective"] == "2025-10-01"


def test_no_feed_means_bundled_source():
    assert dd.registry_source("icd10cm_fy") == "bundled"
    assert dd.data_package_version() is None
    assert dd.has_data_package() is False


def test_unknown_registry_raises_registry_not_found():
    with pytest.raises(dd.RegistryNotFound):
        dd.load_registry("no_such_registry")


# --------------------------------------------------------------------------- #
# Installed feed precedence
# --------------------------------------------------------------------------- #
def test_installed_feed_supersedes_bundled(install_feed):
    fy2027 = {
        "FY2026": {"effective": "2025-10-01", "obsolete": "2026-09-30"},
        "FY2027": {"effective": "2026-10-01", "obsolete": "2027-09-30"},
    }
    install_feed(_FakeEntryPoint(_FakeProvider({"icd10cm_fy": fy2027})))
    reg = dd.load_registry("icd10cm_fy")
    assert "FY2027" in reg
    assert dd.registry_source("icd10cm_fy") == "installed"
    assert dd.has_data_package() is True
    assert dd.data_package_version() == "2026Q4"


def test_installed_feed_falls_through_when_registry_absent(install_feed):
    # Provider carries icd10cm_fy but NOT hedis -> hedis comes from bundled.
    install_feed(_FakeEntryPoint(_FakeProvider({"icd10cm_fy": {"FY2027": {}}})))
    assert dd.registry_source("hedis") == "bundled"
    assert "MY2026" in dd.load_registry("hedis")


def test_dist_version_used_when_provider_lacks_explicit_version(install_feed):
    provider = _FakeProvider({"icd10cm_fy": {"FY2027": {}}}, version=None)
    # DATA_PACKAGE_VERSION=None -> fall back to the distribution version.
    provider.DATA_PACKAGE_VERSION = None
    install_feed(_FakeEntryPoint(provider, dist_version="9.9.9"))
    assert dd.data_package_version() == "9.9.9"


# --------------------------------------------------------------------------- #
# Version-compat
# --------------------------------------------------------------------------- #
def test_incompatible_provider_is_skipped_and_falls_back(install_feed):
    install_feed(
        _FakeEntryPoint(_FakeProvider({"icd10cm_fy": {"FY2999": {}}}, api=2))
    )
    # Incompatible API -> skipped -> bundled tier serves the registry.
    reg = dd.load_registry("icd10cm_fy")
    assert "FY2999" not in reg
    assert "FY2026" in reg
    assert dd.has_data_package() is False
    assert dd.data_package_version() is None


def test_require_compatible_raises_on_incompatible_only(install_feed):
    install_feed(
        _FakeEntryPoint(
            _FakeProvider({"icd10cm_fy": {"FY2999": {}}}, api=2, version="2027Q1")
        )
    )
    with pytest.raises(dd.DataPackageIncompatible):
        dd.require_compatible_data_package()


def test_require_compatible_noop_without_feed():
    dd.require_compatible_data_package()  # no feed installed -> no raise


def test_require_compatible_noop_with_compatible_feed(install_feed):
    install_feed(_FakeEntryPoint(_FakeProvider({"icd10cm_fy": {"FY2027": {}}})))
    dd.require_compatible_data_package()  # compatible -> no raise


def test_first_compatible_provider_wins(install_feed):
    incompat = _FakeEntryPoint(_FakeProvider({"icd10cm_fy": {"X": {}}}, api=99))
    good = _FakeEntryPoint(
        _FakeProvider({"icd10cm_fy": {"FY2027": {}}}, version="2026Q4")
    )
    install_feed(incompat, good)
    assert dd.data_package_version() == "2026Q4"
    assert "FY2027" in dd.load_registry("icd10cm_fy")
