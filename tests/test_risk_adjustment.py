"""Tests for the risk-adjustment scorer."""
from __future__ import annotations

import pytest

try:
    from medecon_verify.riskadj import engine as ra
except FileNotFoundError:  # riskadj HCC reference CSVs are bundled by task 0.2
    pytest.skip(
        "needs-0.2: riskadj HCC reference CSVs not yet bundled under riskadj/data/",
        allow_module_level=True,
    )


def test_score_member_returns_demographic_only_when_no_dx():
    member = {"member_id": "M1", "age": 72, "sex": "M", "dx_codes": []}
    score = ra.score_member(member, model="cms-hcc-v24")
    assert score.demographic_component == pytest.approx(0.391)
    assert score.hcc_component == 0.0
    assert score.raf == pytest.approx(0.391)
    assert score.triggered_hccs == []


def test_score_member_adds_hcc_coefficients():
    member = {
        "member_id": "M2", "age": 75, "sex": "F",
        "dx_codes": ["I50.9", "J44.9"],  # CHF + COPD
    }
    score = ra.score_member(member, model="cms-hcc-v24")
    assert "HCC85" in score.triggered_hccs
    assert "HCC111" in score.triggered_hccs
    # demo + HCC85 + HCC111 + (HF+COPD interaction)
    expected = 0.452 + 0.323 + 0.335 + 0.156
    assert score.raf == pytest.approx(expected)


def test_score_member_v28_uses_v28_coefficients():
    member = {
        "member_id": "M3", "age": 70, "sex": "F",
        "dx_codes": ["I50.9"],
    }
    score = ra.score_member(member, model="cms-hcc-v28")
    # Official V28 CNA values (PY2026 software): Female 70-74 = 0.395;
    # I50.9 -> HCC226 (Heart Failure, Except End Stage and Acute) = 0.360.
    assert score.triggered_hccs == ["HCC226"]
    assert score.raf == pytest.approx(0.395 + 0.360)


def test_score_member_unknown_dx_codes_dont_trigger_hccs():
    member = {
        "member_id": "M4", "age": 65, "sex": "M",
        "dx_codes": ["Z99.99", "X00.0"],  # not in seed icd_to_hcc
    }
    score = ra.score_member(member, model="cms-hcc-v24")
    assert score.triggered_hccs == []
    assert score.hcc_component == 0.0


def test_score_member_unknown_model_raises():
    with pytest.raises(ValueError, match="unknown model"):
        ra.score_member({"member_id": "x"}, model="some-future-model")


def test_v28_phase_in_blends_correctly():
    raw_v28 = 1.0
    raw_v24 = 1.2
    assert ra.apply_v28_phase_in(raw_v28, raw_v24, 2023) == 1.2
    # 2024: 33% v28 + 67% v24
    assert ra.apply_v28_phase_in(raw_v28, raw_v24, 2024) == pytest.approx(
        0.33 * 1.0 + 0.67 * 1.2
    )
    # 2025: 67% v28 + 33% v24
    assert ra.apply_v28_phase_in(raw_v28, raw_v24, 2025) == pytest.approx(
        0.67 * 1.0 + 0.33 * 1.2
    )
    # 2026+: 100% v28
    assert ra.apply_v28_phase_in(raw_v28, raw_v24, 2026) == 1.0


def test_register_coefficients_extends_registry():
    # Use cms-hcc prefix so the age bucket logic recognizes the bucketing.
    custom = ra.CoefficientTable(
        model="cms-hcc-custom-test", description="x",
        demographics={("65-69", "M", "community"): 0.500},
    )
    ra.register_coefficients("cms-hcc-custom-test", custom)
    score = ra.score_member(
        {"member_id": "M5", "age": 67, "sex": "M", "dx_codes": []},
        model="cms-hcc-custom-test",
    )
    assert score.demographic_component == 0.500


def test_score_population_runs_over_list():
    members = [
        {"member_id": "M1", "age": 70, "sex": "M", "dx_codes": []},
        {"member_id": "M2", "age": 80, "sex": "F", "dx_codes": ["I50.9"]},
    ]
    scores = ra.score_population(members, model="cms-hcc-v24")
    assert len(scores) == 2
    assert all(s.raf > 0 for s in scores)


def test_population_average_raf():
    members = [
        {"member_id": "M1", "age": 70, "sex": "M", "dx_codes": []},
        {"member_id": "M2", "age": 75, "sex": "M", "dx_codes": []},
    ]
    scores = ra.score_population(members, model="cms-hcc-v24")
    expected = (0.391 + 0.479) / 2
    assert ra.population_average_raf(scores) == pytest.approx(expected)


def test_hhs_hcc_uses_different_age_buckets():
    member = {
        "member_id": "M6", "age": 32, "sex": "F",
        "dx_codes": ["I50.9"],
    }
    score = ra.score_member(member, model="hhs-hcc-2026", subpop="silver")
    assert score.model == "hhs-hcc-2026"
    # age 32 -> HHS adult band "30-34" (NOT a 4-year "29-32"); the silver-adult
    # demographic factor for Age 30-34 Female is 0.198 (2026 BY final HHS RA
    # coefficients), so the demographic component must be picked up, not zeroed.
    assert score.demographic_component == pytest.approx(0.198)
    # I50.9 → V08 HHS HCC130 (Heart Failure); silver adult coefficient 1.647.
    assert score.triggered_hccs == ["HCC130"]
    assert score.hcc_component == pytest.approx(1.647)
    assert score.raf == pytest.approx(0.198 + 1.647)


def test_score_member_accepts_undotted_icd_codes():
    # Codes as stored in the committed official maps / claims feeds (undotted)
    # must score identically to the dotted form. cms-hcc-v28: E1122->HCC37,
    # I509->HCC226, J449->HCC280.
    dotted = ra.score_member(
        {"member_id": "U1", "age": 75, "sex": "F",
         "dx_codes": ["E11.22", "I50.9", "J44.9"]},
        model="cms-hcc-v28")
    undotted = ra.score_member(
        {"member_id": "U2", "age": 75, "sex": "F",
         "dx_codes": ["E1122", "I509", "J449"]},
        model="cms-hcc-v28")
    assert undotted.triggered_hccs == dotted.triggered_hccs
    assert undotted.raf == pytest.approx(dotted.raf)
    assert undotted.hcc_component > 0


def test_score_member_undotted_icd_codes_hhs():
    # hhs-hcc-2026: A419->HCC002, E1122->HCC020, I509->HCC130 (undotted).
    out = ra.score_member(
        {"member_id": "U3", "age": 32, "sex": "F",
         "dx_codes": ["A419", "E1122", "I509"]},
        model="hhs-hcc-2026", subpop="silver")
    assert set(out.triggered_hccs) == {"HCC002", "HCC020", "HCC130"}
    assert out.hcc_component > 0


def test_hhs_hcc_coefs_are_metal_keyed():
    # Diagnosis factors vary by metal level; a platinum member must NOT be scored
    # with the silver coefficient. HCC130 (HF): platinum 1.769 vs silver 1.647.
    plat = ra.score_member(
        {"member_id": "p", "age": 45, "sex": "M", "dx_codes": ["I50.9"]},
        model="hhs-hcc-2026", subpop="platinum")
    silv = ra.score_member(
        {"member_id": "s", "age": 45, "sex": "M", "dx_codes": ["I50.9"]},
        model="hhs-hcc-2026", subpop="silver")
    assert plat.hcc_component == pytest.approx(1.769)
    assert silv.hcc_component == pytest.approx(1.647)
    assert plat.hcc_component != silv.hcc_component


def test_hhs_full_demographic_grid_loaded():
    # The full adult age-sex grid (all metals/bands) is loaded, so a common cell
    # like silver Female 50-54 is no longer a silent demographic_component=0.
    s = ra.score_member(
        {"member_id": "g", "age": 50, "sex": "F", "dx_codes": []},
        model="hhs-hcc-2026", subpop="silver")
    assert s.demographic_component == pytest.approx(0.305)


def test_hhs_requires_metal_subpop():
    # HHS demographics are metal-keyed; a non-metal subpop (e.g. the CMS default
    # "community") is outside the modeled domain and must raise.
    with pytest.raises(ValueError, match="not modeled by hhs-hcc-2026"):
        ra.score_member(
            {"member_id": "H", "age": 32, "sex": "F", "dx_codes": ["E11.22"]},
            model="hhs-hcc-2026",  # subpop defaults to "community"
        )


def test_hhs_age_out_of_adult_model_both_bounds_raise():
    # The HHS adult model covers ages 21-64; BOTH boundaries raise (the domain
    # contract), rather than returning a partial RAF. (Round 10 fixed >=65;
    # round 11/this domain contract also covers <21.)
    for bad_age in (18, 10, 1, 65, 80):
        with pytest.raises(ValueError, match="outside the hhs-hcc-2026 modeled domain"):
            ra.score_member(
                {"member_id": "O", "age": bad_age, "sex": "F", "dx_codes": ["I50.9"]},
                model="hhs-hcc-2026", subpop="silver")


def test_v28_originally_disabled_factor_applied():
    # An originally-disabled aged member gets the sourced OriginallyDisabled
    # demographic-interaction factor (CNA Female 0.228) added; previously the
    # documented flag was silently ignored.
    base = ra.score_member(
        {"member_id": "d0", "age": 67, "sex": "F", "dx_codes": []},
        model="cms-hcc-v28")
    dis = ra.score_member(
        {"member_id": "d1", "age": 67, "sex": "F", "originally_disabled": True,
         "dx_codes": []},
        model="cms-hcc-v28")
    assert base.demographic_component == pytest.approx(0.330)
    assert dis.demographic_component == pytest.approx(0.330 + 0.228)


def test_cms_hcc_under_65_out_of_domain_raises():
    # The CMS-HCC seed models the community-AGED segment (65+); a <65 member is
    # outside the domain and raises, rather than returning a partial RAF from the
    # aged table (the same fail-loud contract as HHS's age bounds).
    with pytest.raises(ValueError, match="outside the cms-hcc-v28 modeled domain"):
        ra.score_member(
            {"member_id": "d2", "age": 50, "sex": "M", "dx_codes": []},
            model="cms-hcc-v28")


def test_originally_disabled_ignored_with_note_when_model_lacks_factor():
    # originally_disabled is a documented standard field. A model that does not
    # model it (v24 seed) must score normally and note the ignored flag, NOT crash.
    base = ra.score_member(
        {"member_id": "v0", "age": 70, "sex": "F", "dx_codes": []},
        model="cms-hcc-v24")
    flagged = ra.score_member(
        {"member_id": "v1", "age": 70, "sex": "F", "originally_disabled": True,
         "dx_codes": []},
        model="cms-hcc-v24")
    assert flagged.demographic_component == base.demographic_component  # no crash, no change
    assert any("originally_disabled not modeled" in n for n in flagged.notes)


def test_age_none_raises_clean_value_error():
    # age explicitly None (a left-join artifact) must raise a clear ValueError,
    # not a raw TypeError from int(None).
    with pytest.raises(ValueError, match="age is required"):
        ra.score_member(
            {"member_id": "x", "age": None, "sex": "M", "dx_codes": []},
            model="cms-hcc-v28")


def test_medicaid_dual_raises_rather_than_silently_misscoring():
    with pytest.raises(NotImplementedError, match="medicaid_dual"):
        ra.score_member(
            {"member_id": "x", "age": 70, "sex": "M", "medicaid_dual": True,
             "dx_codes": []},
            model="cms-hcc-v28")


def test_score_population_isolates_per_member_failures():
    # One out-of-domain member must not abort the whole batch: it yields a raf=0
    # RiskScore with a diagnostic note, and the valid member still scores.
    members = [
        {"member_id": "ok", "age": 70, "sex": "M", "dx_codes": ["I50.9"]},
        {"member_id": "bad", "age": 54, "sex": "M", "dx_codes": []},  # <65, out of domain
    ]
    scores = ra.score_population(members, model="cms-hcc-v28")
    assert len(scores) == 2
    by_id = {s.member_id: s for s in scores}
    assert by_id["ok"].raf > 0
    assert by_id["bad"].raf == 0.0
    assert any("scoring failed" in n for n in by_id["bad"].notes)


def test_score_population_fails_fast_on_unknown_model():
    # A population-wide misconfiguration (misspelled model) must RAISE, not be
    # caught per-row and turned into an all-zero population RAF. Every row would
    # otherwise fail identically and a caller aggregating the scores would read
    # 0.0 as a real population RAF.
    members = [
        {"member_id": "a", "age": 70, "sex": "M", "dx_codes": ["I50.9"]},
        {"member_id": "b", "age": 72, "sex": "F", "dx_codes": []},
    ]
    with pytest.raises(ValueError, match="unknown model"):
        ra.score_population(members, model="cms-hcc-v280")  # typo'd model


def test_score_population_fails_fast_on_missing_metal_subpop():
    # model="hhs-hcc-2026" with the default "community" subpop is a population-wide
    # config error: HHS is metal-keyed, so every row would raise. score_population
    # must fail fast rather than zero the whole roster.
    members = [
        {"member_id": "a", "age": 32, "sex": "F", "dx_codes": ["E11.22"]},
        {"member_id": "b", "age": 40, "sex": "M", "dx_codes": []},
    ]
    with pytest.raises(ValueError, match="not modeled by hhs-hcc-2026"):
        ra.score_population(members, model="hhs-hcc-2026")  # subpop defaults to community


def test_score_population_valid_hhs_metal_subpop_scores_whole_roster():
    # The fail-fast guard must NOT trip for a correctly-configured HHS population:
    # a valid metal subpop scores every row normally.
    members = [
        {"member_id": "a", "age": 32, "sex": "F", "dx_codes": ["E11.22"]},
        {"member_id": "b", "age": 40, "sex": "M", "dx_codes": []},
    ]
    scores = ra.score_population(members, model="hhs-hcc-2026", subpop="silver")
    assert len(scores) == 2
    assert all(not any("scoring failed" in n for n in s.notes) for s in scores)


def test_cdps_uses_pediatric_bands():
    member = {
        "member_id": "M7", "age": 8, "sex": "M",
        "dx_codes": [],
    }
    score = ra.score_member(member, model="cdps-2025", subpop="tanf")
    assert score.demographic_component == pytest.approx(0.40)


def test_member_with_subset_interaction_does_not_trigger_full_interaction():
    # CHF alone: interaction (HF+COPD) does NOT fire
    member = {
        "member_id": "M8", "age": 75, "sex": "F",
        "dx_codes": ["I50.9"],
    }
    score = ra.score_member(member, model="cms-hcc-v24")
    assert score.interaction_component == 0.0


def test_member_with_full_interaction_set_fires_interaction():
    member = {
        "member_id": "M9", "age": 75, "sex": "F",
        "dx_codes": ["I50.9", "J44.9"],
    }
    score = ra.score_member(member, model="cms-hcc-v24")
    assert score.interaction_component == 0.156


def test_v28_diabetes_hf_group_interaction_fires_for_any_diabetes_hcc():
    # E11.65 -> HCC38 (diabetes) + I50.9 -> HCC226 (HF) must fire DIABETES_HF_V28
    # (0.112) even though HCC38, not HCC37, is the diabetes HCC. The old exact
    # {HCC37, HCC226} pair missed this.
    member = {"member_id": "D1", "age": 75, "sex": "F",
              "dx_codes": ["E11.65", "I50.9"]}
    score = ra.score_member(member, model="cms-hcc-v28")
    assert score.interaction_component == pytest.approx(0.112)


def test_v28_diabetes_hf_group_interaction_fires_once_not_per_hcc():
    # A member with TWO diabetes HCCs (HCC37 via E11.22, HCC38 via E11.65) plus
    # HF must fire the group interaction exactly once (0.112), not twice (0.224).
    member = {"member_id": "D2", "age": 75, "sex": "F",
              "dx_codes": ["E11.22", "E11.65", "I50.9"]}
    score = ra.score_member(member, model="cms-hcc-v28")
    assert score.interaction_component == pytest.approx(0.112)


def test_v28_diabetes_hf_group_interaction_does_not_fire_without_hf():
    # Diabetes alone (no HF HCC) must NOT fire the group interaction.
    member = {"member_id": "D3", "age": 75, "sex": "F",
              "dx_codes": ["E11.65"]}
    score = ra.score_member(member, model="cms-hcc-v28")
    assert score.interaction_component == 0.0


def test_v28_default_path_resolves_full_committed_crosswalk():
    # The V28 default crosswalk is the FULL committed CSV (8,933 rows), not the
    # sparse in-code seed. Codes that exist only in the full table must resolve
    # via the default scoring path: C50.911 -> HCC22, E11.9 -> HCC38, G35 -> HCC198.
    c50 = ra.score_member(
        {"member_id": "X1", "age": 70, "sex": "F", "dx_codes": ["C50.911"]},
        model="cms-hcc-v28")
    assert "HCC22" in c50.triggered_hccs  # breast cancer; absent from the old seed
    e119 = ra.score_member(
        {"member_id": "X2", "age": 70, "sex": "F", "dx_codes": ["E11.9"]},
        model="cms-hcc-v28")
    assert "HCC38" in e119.triggered_hccs  # diabetes, no/unspecified complications
    g35 = ra.score_member(
        {"member_id": "X3", "age": 70, "sex": "F", "dx_codes": ["G35"]},
        model="cms-hcc-v28")
    assert "HCC198" in g35.triggered_hccs  # multiple sclerosis


def test_v28_full_map_hccs_contribute_real_diagnosis_raf():
    # REGRESSION GUARD (objection A5.1): full-map-only diagnoses must add their
    # REAL CNA coefficient, not 0.0. These coefficients come from the committed
    # references/cms_hcc_v28_py2026_coefficients.csv (CNA `hcc` rows):
    #   G35  -> HCC198 = 0.647
    #   E11.9 -> HCC38 = 0.166 (diabetes family, constrained)
    #   C50.911 -> HCC22 = 0.363 (HCC23 = 0.186 is suppressed by the hierarchy)
    # If the full coefficient table is reverted to the six-HCC seed, HCC198/HCC22
    # are absent from hcc_coefs and these assertions fail (component -> 0.0).
    g35 = ra.score_member(
        {"member_id": "G", "age": 70, "sex": "F", "dx_codes": ["G35"]},
        model="cms-hcc-v28")
    assert g35.hcc_component == pytest.approx(0.647)

    e119 = ra.score_member(
        {"member_id": "E", "age": 70, "sex": "F", "dx_codes": ["E11.9"]},
        model="cms-hcc-v28")
    assert e119.hcc_component == pytest.approx(0.166)

    c50 = ra.score_member(
        {"member_id": "C", "age": 70, "sex": "F", "dx_codes": ["C50.911"]},
        model="cms-hcc-v28")
    assert c50.hcc_component == pytest.approx(0.363)

    # A member carrying ALL THREE conditions: the diagnosis RAF is the sum of the
    # three post-hierarchy coefficients, demonstrably non-zero and exact.
    combo = ra.score_member(
        {"member_id": "M", "age": 70, "sex": "F",
         "dx_codes": ["G35", "C50.911", "E11.9"]},
        model="cms-hcc-v28")
    assert combo.hcc_component == pytest.approx(0.647 + 0.363 + 0.166)
    assert combo.hcc_component > 0


def test_v28_loaded_cna_coefs_are_the_full_table_not_the_seed():
    # Proves the diagnosis coefficients are the full committed CNA table (115
    # payment HCCs), not the six-HCC fallback, and that HCCs unique to the full
    # table carry their real value. Fails if the coefficient loading is reverted.
    assert len(ra._V28_CNA_HCC_COEFS) >= 100
    assert len(ra._V28_HCC_COEF_FALLBACK) < 10
    # HCCs that exist ONLY in the full table (not the fallback) carry real coefs.
    for missing_from_seed in ("HCC198", "HCC22", "HCC23"):
        assert missing_from_seed not in ra._V28_HCC_COEF_FALLBACK
        assert ra._V28_CNA_HCC_COEFS[missing_from_seed] > 0
    assert ra._V28_CNA_HCC_COEFS["HCC198"] == pytest.approx(0.647)
    assert ra._V28_CNA_HCC_COEFS["HCC22"] == pytest.approx(0.363)
    assert ra._V28_CNA_HCC_COEFS["HCC23"] == pytest.approx(0.186)


def test_v28_default_crosswalk_is_the_full_table_not_the_seed():
    # Proves the default is the committed full crosswalk, not the 4-code fallback:
    # the loaded map carries thousands of codes and the labeled fallback is small.
    assert len(ra._V28_ICD_TO_HCCS) > 5000
    assert len(ra._V28_ICD_TO_HCC_FALLBACK) < 10
    # A multi-HCC code (C50.911 -> HCC22 + HCC23) keeps BOTH HCCs from the CSV.
    assert ra._V28_ICD_TO_HCCS[ra._norm_icd("C50.911")] == ["HCC22", "HCC23"]


def test_v28_neoplasm_hierarchy_suppresses_lower_ranked_hcc():
    # REGRESSION GUARD (objection A5.2): the committed crosswalk maps C50.911 to
    # BOTH HCC22 and HCC23, but the V28 neoplasm hierarchy ranks HCC22 above HCC23,
    # so HCC23 must be SUPPRESSED before scoring — paid once, not twice.
    c50 = ra.score_member(
        {"member_id": "BC", "age": 70, "sex": "F", "dx_codes": ["C50.911"]},
        model="cms-hcc-v28")
    assert "HCC22" in c50.triggered_hccs
    assert "HCC23" not in c50.triggered_hccs  # suppressed by HCC22
    # Paid the HCC22 coefficient ONCE (0.363), NOT HCC22+HCC23 (0.363 + 0.186).
    assert c50.hcc_component == pytest.approx(0.363)
    assert c50.hcc_component != pytest.approx(0.363 + 0.186)


def test_v28_csv_absent_falls_back_to_labeled_seed(monkeypatch, tmp_path):
    # When the committed CSV is absent, the loader falls back to the clearly-labeled
    # in-code sparse seed so the scorer still runs in a stripped-down checkout.
    missing = tmp_path / "no_such_map.csv"
    monkeypatch.setattr(ra, "_V28_ICD_CSV", missing)
    # Patch the flag too so the loader's global write is auto-restored after the
    # test (the loader now also sets _V28_USING_FALLBACK).
    monkeypatch.setattr(ra, "_V28_USING_FALLBACK", False)
    fb = ra._load_v28_icd_to_hccs()
    assert fb == {
        ra._norm_icd("E11.65"): ["HCC38"],
        ra._norm_icd("E11.22"): ["HCC37"],
        ra._norm_icd("I50.9"): ["HCC226"],
        ra._norm_icd("J44.9"): ["HCC280"],
    }


def test_v28_coef_csv_absent_falls_back_to_labeled_seed(monkeypatch, tmp_path):
    # When the committed coefficient CSV is absent, the loader falls back to the
    # clearly-labeled six-HCC in-code seed and flags the fallback, so the scorer
    # still runs in a stripped-down checkout (rather than zeroing every coef).
    missing = tmp_path / "no_such_coefs.csv"
    monkeypatch.setattr(ra, "_V28_COEF_CSV", missing)
    # Also patch the flag so the loader's global write is auto-restored after the
    # test and does not leak into tests asserting the normal (non-fallback) state.
    monkeypatch.setattr(ra, "_V28_USING_FALLBACK", False)
    fb = ra._load_v28_cna_hcc_coefs()
    assert fb == ra._V28_HCC_COEF_FALLBACK
    assert ra._V28_USING_FALLBACK is True


def test_v28_fallback_surfaces_diagnostic_note(monkeypatch):
    # When running on the sparse fallback, every V28 score must carry a diagnostic
    # note so a packaging/data omission is surfaced, not silently treated as a
    # normal full-table default (the "confidently wrong number" failure mode).
    monkeypatch.setattr(ra, "_V28_USING_FALLBACK", True)
    score = ra.score_member(
        {"member_id": "fb", "age": 70, "sex": "F", "dx_codes": ["I50.9"]},
        model="cms-hcc-v28")
    assert any("committed reference CSV" in n for n in score.notes)


def test_v28_full_table_default_has_no_fallback_note():
    # In a normal checkout (both CSVs present) the flag is False and the score
    # carries NO fallback note — the note only appears on a real data omission.
    assert ra._V28_USING_FALLBACK is False
    score = ra.score_member(
        {"member_id": "ok", "age": 70, "sex": "F", "dx_codes": ["I50.9"]},
        model="cms-hcc-v28")
    assert not any("committed reference CSV" in n for n in score.notes)


def test_v28_diabetes_hierarchy_counts_coefficient_once():
    # E11.22 -> HCC37 (chronic) and E11.65 -> HCC38 (unspecified) are the same
    # CMS-HCC diabetes hierarchy; HCC37 suppresses HCC38, so the constrained
    # 0.166 diabetes coefficient is paid ONCE, not 0.166 + 0.166 = 0.332.
    member = {"member_id": "DH", "age": 75, "sex": "F",
              "dx_codes": ["E11.22", "E11.65"]}
    score = ra.score_member(member, model="cms-hcc-v28")
    assert score.hcc_component == pytest.approx(0.166)
    assert "HCC37" in score.triggered_hccs
    assert "HCC38" not in score.triggered_hccs  # suppressed by HCC37
