"""Task 0.2 CI gate — the riskadj engine ships its HCC reference tables as
package data and loads the FULL tables, never the sparse in-code fallback.

Written to pass identically from a source checkout (PYTHONPATH=src) and from a
pip-installed wheel: it asserts on loaded state and on importlib.resources
discovery, not on any on-disk path layout.
"""
from __future__ import annotations

from importlib import resources

from medecon_verify.riskadj import engine as ra


def test_v28_not_using_fallback():
    """Import-time load of the committed V28 tables must NOT have fallen back to
    the sparse in-code seed. This is the packaging gate: a missing/omitted data
    file flips this True.
    """
    assert ra._V28_USING_FALLBACK is False


def test_full_v28_tables_loaded():
    """Full CMS-HCC v28 crosswalk + CNA coefficient table are present, not the
    six-entry seed maps.
    """
    # Full crosswalk is thousands of codes; the fallback seed is 4.
    assert len(ra._V28_ICD_TO_HCCS) > 1000
    assert ra._V28_ICD_TO_HCCS is not ra._V28_ICD_TO_HCC_FALLBACK
    # All 115 payment HCCs in the CNA segment, not the 6-HCC seed.
    assert len(ra._V28_CNA_HCC_COEFS) == 115


def test_full_hhs_tables_loaded():
    """HHS-HCC 2026 grid is metal- and demographic-complete (all five metals),
    not the silver-only in-code seed.
    """
    assert set(ra._HHS_HCC_BY_METAL) == {
        "platinum",
        "gold",
        "silver",
        "bronze",
        "catastrophic",
    }
    assert ra._HHS_DEMOGRAPHICS  # non-empty demographic grid


def test_reference_data_is_package_resource():
    """The reference tables + provenance doc are discoverable as package data via
    importlib.resources — the mechanism that works inside an installed wheel, not
    just a source tree.
    """
    data = resources.files(ra._DATA_PACKAGE)
    for name in (
        "cms_hcc_v28_icd10_map.csv",
        "cms_hcc_v28_py2026_coefficients.csv",
        "hhs_hcc_2026_coefficients.csv",
        "hhs_hcc_v08_2026_icd10_map.csv",
        "coefficient-sources.md",
    ):
        assert data.joinpath(name).is_file(), name


def test_load_boundary_helper_resolves():
    """The single load boundary (`_data_file`) resolves a bundled file that opens
    and reads — task 0.3 replaces only this function's body.
    """
    res = ra._data_file("coefficient-sources.md")
    assert res.is_file()
    with res.open(encoding="utf-8") as f:
        assert f.read(1)
