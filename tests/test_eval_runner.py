"""Tests for the programmatic eval runner."""
from __future__ import annotations

from pathlib import Path

import pytest

from medecon_verify.certify import runner as er

REPO_ROOT = Path(__file__).resolve().parents[1]

# discover_evals/run_all now take an evals_root Path and glob it directly (no
# hard-coded "skills" segment — task 0.5). The full medecon-stack skills corpus
# doesn't travel here (that loader is Phase 3); a minimal synthetic fixture tree
# exercises discovery instead.
EVALS_FIXTURE_ROOT = Path(__file__).resolve().parent / "evals_fixture"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_evals_finds_at_least_one_per_fixture_skill():
    specs = er.discover_evals(EVALS_FIXTURE_ROOT)
    assert specs, "no evals discovered"
    skills = {s.skill_name for s in specs}
    for required in ["skill-alpha", "skill-beta", "skill-gamma"]:
        assert required in skills, f"missing evals for {required}"


def test_discover_evals_carries_severity():
    specs = er.discover_evals(EVALS_FIXTURE_ROOT)
    blockers = [s for s in specs if s.severity == "blocker"]
    assert blockers, "expected at least one 'blocker'-severity eval"


def test_discover_evals_roots_directly_no_skills_segment():
    """The glob roots at evals_root itself — no hard-coded 'skills' path
    segment (source eval_runner.py:84). Passing the fixture's `nested/`
    subtree directly (which has no literal 'skills' dir anywhere in its
    path) must still discover its eval."""
    specs = er.discover_evals(EVALS_FIXTURE_ROOT / "nested")
    assert {s.skill_name for s in specs} == {"skill-gamma"}


def test_discover_evals_empty_root_returns_empty():
    specs = er.discover_evals(EVALS_FIXTURE_ROOT / "skill-alpha" / "evals")
    assert specs == []


# ---------------------------------------------------------------------------
# Anti-fabrication checker
# ---------------------------------------------------------------------------


def test_anti_fabrication_passes_when_numbers_match():
    sidecar = {"findings": [{"value": 0.0180}, {"value": 0.0044}]}
    rendered = "PPO margin compressed 0.0180; MLR gap 0.0044."
    r = er.check_anti_fabrication(rendered, sidecar)
    assert r.status == "pass"


def test_anti_fabrication_fails_when_rendered_introduces_new_number():
    sidecar = {"findings": [{"value": 0.0180}]}
    rendered = "PPO margin compressed 0.0180 — but actually 0.0250 in my view."
    r = er.check_anti_fabrication(rendered, sidecar)
    assert r.status == "fail"
    assert "0.025" in r.detail or "0.0250" in r.detail


def test_anti_fabrication_handles_dollar_and_comma_formatting():
    sidecar = {"recommendations": [{"impact": 1234567}]}
    rendered = "Recommended bid raise impact: $1,234,567 — see Exhibit 1."
    r = er.check_anti_fabrication(rendered, sidecar)
    assert r.status == "pass"


def test_anti_fabrication_whitelists_year_and_list_indices():
    sidecar = {"finding": "MLR drift"}
    rendered = "## 1. Finding\n\nIn 2025, MLR drift was the dominant driver. See item 3."
    r = er.check_anti_fabrication(rendered, sidecar)
    assert r.status == "pass"


# ---------------------------------------------------------------------------
# Code-set stamp checker
# ---------------------------------------------------------------------------


def test_code_set_stamp_passes_when_present():
    rendered = "_Code-set pinning: ICD-10-CM FY2025, MS-DRG v42_"
    r = er.check_code_set_stamp(rendered, {})
    assert r.status == "pass"


def test_code_set_stamp_fails_when_missing():
    rendered = "Some output with no pinning footer."
    r = er.check_code_set_stamp(rendered, {})
    assert r.status == "fail"


# ---------------------------------------------------------------------------
# Small-cell suppression checker
# ---------------------------------------------------------------------------


def test_small_cell_suppression_fails_on_small_n():
    rendered = "ACO-A: n=5 episodes. ACO-B: n=200 episodes."
    r = er.check_small_cell_suppression(rendered, {})
    assert r.status == "fail"
    assert "5" in r.detail


def test_small_cell_suppression_passes_when_above_threshold():
    rendered = "ACO-A: n=11 episodes. ACO-B: n=200 episodes."
    r = er.check_small_cell_suppression(rendered, {})
    assert r.status == "pass"


# ---------------------------------------------------------------------------
# Age 90+ checker
# ---------------------------------------------------------------------------


def test_age_90_plus_fails_on_individual_age():
    rendered = "Member: age: 94, presented with..."
    r = er.check_age_90_plus(rendered, {})
    assert r.status == "fail"


def test_age_90_plus_passes_on_aggregated_band():
    rendered = "Cohort: age 90+ aggregated per Safe Harbor."
    r = er.check_age_90_plus(rendered, {})
    assert r.status == "pass"


# ---------------------------------------------------------------------------
# run() + run_all()
# ---------------------------------------------------------------------------


def test_run_returns_not_runnable_for_unknown_eval_id():
    spec = er.EvalSpec(
        skill_name="x", skill_path=Path("/tmp"),
        eval_id="some-future-eval-id", description="x",
    )
    r = er.run(spec, rendered="", sidecar={})
    assert r.status == "not_runnable"


def test_run_all_with_no_samples_skips_everything():
    report = er.run_all(EVALS_FIXTURE_ROOT, samples={})
    assert report.skipped == len(report.results) - report.not_runnable - report.failed - report.passed


def test_run_all_with_one_sample_runs_that_skills_evals():
    # Sidecar includes the code-set vintages so the renderer's pinning footer
    # doesn't trip the anti-fabrication check.
    samples = {
        "skill-alpha": {
            "rendered": (
                "# Margin compressed\n\n"
                "0.0180 margin gap.\n\n"
                "_Code-set pinning: ICD-10-CM FY2025, MS-DRG v42, CMS-HCC v28_\n\n"
                "_Source — tier: curated, freshness: prior-year, owner: Actuarial_\n\n"
                "_Privacy — n≤10 suppression on 0 field(s); 42 CFR Part 2: none; "
                "PHI scan: 0 finding(s)_\n\n"
                "_Provenance — git abc1234, model claude-opus-4-8, snapshot prior-period_"
            ),
            "sidecar": {
                "findings": [{"value": 0.0180}],
                "code_set_versions": {
                    "icd10cm_fy": "FY2025", "ms_drg": "v42", "cms_hcc": "v28",
                },
                "privacy_audit": {"phi_scan_metrics": {"redactions": 0}},
            },
        }
    }
    report = er.run_all(EVALS_FIXTURE_ROOT, samples=samples)
    relevant = [r for r in report.results if r.skill_name == "skill-alpha"]
    # At least anti-fabrication + code-set-stamp should have run
    runnable = [r for r in relevant if r.status in {"pass", "fail"}]
    assert runnable, "expected runnable evals for skill-alpha"
    # The provided sample should pass the checkers we built
    assert all(r.status == "pass" for r in runnable), (
        f"some runnable evals failed: {[(r.eval_id, r.detail) for r in runnable if r.status == 'fail']}"
    )


def test_eval_report_summary_counts():
    report = er.EvalReport()
    report.results = [
        er.EvalResult(eval_id="a", skill_name="x", status="pass"),
        er.EvalResult(eval_id="b", skill_name="x", status="fail"),
        er.EvalResult(eval_id="c", skill_name="x", status="not_runnable"),
        er.EvalResult(eval_id="d", skill_name="x", status="skipped"),
    ]
    s = report.summary
    assert s == {"passed": 1, "failed": 1, "not_runnable": 1, "skipped": 1, "total": 4}


# ---------------------------------------------------------------------------
# RAF anti-fabrication checker (no-fabricated-raf-scores)
# ---------------------------------------------------------------------------


def test_raf_small_integer_in_context_is_not_whitelisted():
    """A fabricated small-integer RAF figure must fail even though it is 1-20.

    Round-3 finding 1: the 1-20 small-index whitelist ran before the RAF-context
    test, so 'Cohort RAF 2' / 'O/E ratio is 1' slipped through. The context test
    now runs first; only figure/list-labeled small ints stay exempt.
    """
    for rendered in ("Cohort RAF 2 across the panel", "O/E ratio is 1"):
        r = er.check_no_fabricated_raf_scores(rendered, {})
        assert r.status == "fail", f"{rendered!r} should fail: {r.detail}"


def test_raf_coefficient_and_normalization_factor_are_judged():
    """'coefficient 0.166' and 'normalization factor 1.05' are RAF-shaped results.

    Round-3 finding 1: these cues were missing from _RAF_CONTEXT_CUES, so a
    fabricated coefficient/normalization figure was never judged.
    """
    for rendered in ("Applying coefficient 0.166", "normalization factor 1.05"):
        r = er.check_no_fabricated_raf_scores(rendered, {})
        assert r.status == "fail", f"{rendered!r} should fail: {r.detail}"


def test_raf_figure_labeled_small_int_stays_exempt():
    """Genuine figure/list labels near an RAF cue do not trip the gate."""
    for rendered in (
        "See Fig 1 for the RAF distribution",
        "HCC 18 contributes to the cohort RAF",
        "Per PY2025 the RAF model is v28",
    ):
        r = er.check_no_fabricated_raf_scores(rendered, {})
        assert r.status == "pass", f"{rendered!r} should pass: {r.detail}"


def test_raf_forged_bare_sidecar_value_fails():
    """A hand-forged {raf: x} sidecar value must NOT launder a typed RAF figure.

    Round-3 finding 2: provenance was a pure numeric match against any dict
    carrying a 'raf' key, so an analyst could type 'RAF 9.999' and add
    {"raf": 9.999} to the sidecar to pass. A bare {raf: x} dict with no companion
    scorer fields is now rejected as provenance.
    """
    forged = {"raf": 9.999}
    r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", forged)
    assert r.status == "fail", "forged bare {raf:x} sidecar must not pass the gate"


def test_raf_real_scorer_shaped_provenance_passes():
    """A RiskScore-shaped record (raf + companion scorer fields) is valid provenance."""
    real = {"raf": 9.999, "member_id": "M1", "model": "cms-hcc-v28",
            "hcc_component": 0.5, "demographic_component": 0.4,
            "interaction_component": 0.0}
    r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", real)
    assert r.status == "pass", r.detail


def test_raf_named_aggregate_provenance_still_accepted():
    """A named aggregate (population_average_raf) remains valid provenance."""
    sidecar = {"risk_score_provenance": {"population_average_raf": 1.234}}
    r = er.check_no_fabricated_raf_scores("Cohort mean RAF 1.234", sidecar)
    assert r.status == "pass", r.detail


def test_raf_single_soft_companion_field_fails():
    """raf + one trivial soft companion (model / notes) is NOT provenance.

    Round-5 finding 2: the gate accepted any dict with `raf` plus ANY companion
    field, including `model`/`notes`. A forger who adds one string defeated it.
    Provenance now demands a structural scorer signature, not a soft field.
    """
    forged_model = {"raf": 9.999, "model": "cms-hcc-v28"}
    r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", forged_model)
    assert r.status == "fail", "raf+model alone must not pass the gate"

    forged_notes = {"raf": 9.999, "notes": ["looks fine"]}
    r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", forged_notes)
    assert r.status == "fail", "raf+notes alone must not pass the gate"


def test_raf_member_id_plus_model_pair_fails():
    """member_id + model are three trivially-typeable strings, NOT provenance.

    Round-6 finding 1: the gate previously accepted {raf, member_id, model} as
    the dataclass identity pair. But all three are strings an analyst can forge,
    with no structural scorer signature behind them. Provenance now requires at
    least one component field (demographic_/hcc_/interaction_component or
    triggered_hccs), which a genuine scorer record always carries.
    """
    forged = {"raf": 9.999, "member_id": "M1", "model": "cms-hcc-v28"}
    r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", forged)
    assert r.status == "fail", "raf+member_id+model alone must not pass the gate"

    # Adding one structural component field makes it genuine scorer provenance.
    real = {
        "raf": 9.999, "member_id": "M1", "model": "cms-hcc-v28",
        "hcc_component": 9.999,
    }
    r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", real)
    assert r.status == "pass", r.detail


def test_raf_bare_aggregate_number_fails():
    """A bare top-level aggregate number is NOT provenance.

    Round-5 finding 3: aggregate keys were honoured anywhere, so a forger could
    mirror a typed RAF into {"population_average_raf": 9.999} and pass. An
    aggregate value is now honoured only inside a scorer-provenance object.
    """
    for key in ("population_average_raf", "mean_raf", "normalized_pmpm", "oe_ratio"):
        forged = {key: 9.999}
        r = er.check_no_fabricated_raf_scores("Cohort RAF 9.999", forged)
        assert r.status == "fail", f"bare {{{key}: x}} must not pass the gate"


def test_raf_aggregate_in_provenance_object_passes():
    """An aggregate value inside a scorer-provenance object (model + run marker)
    is honoured even without the explicit *_provenance wrapper key."""
    sidecar = {
        "population_average_raf": 1.234,
        "model": "cms-hcc-v28",
        "payment_year": 2026,
        "n_members": 5000,
    }
    r = er.check_no_fabricated_raf_scores("Cohort mean RAF 1.234", sidecar)
    assert r.status == "pass", r.detail


# --- notes-surfacing gate (round-4 finding 2) ------------------------------
# The eval assertion is BLOCKER-severity: scorer RiskScore notes (V28
# fallback-data warning, out-of-domain member, unmodeled segment) must be
# surfaced verbatim, or the answer must say not-available-and-why. The numeric
# gate alone let a sidecar warning be dropped while the RAF read as clean.

_SCORER_RECORD_WITH_NOTE = {
    "raf": 1.234, "member_id": "M1", "model": "cms-hcc-v28",
    "demographic_component": 0.4, "hcc_component": 0.834,
    "interaction_component": 0.0,
    "notes": [
        "V28 committed reference CSV(s) absent; scoring on the sparse in-code "
        "fallback — diagnosis RAF is under-captured."
    ],
}


def test_raf_notes_must_be_surfaced_or_checker_fails():
    """A scorer note in the sidecar that the deliverable omits is a failure.

    The RAF number itself traces to provenance (clean numeric gate), so this
    fails ONLY because the fallback-data warning never reached the rendered
    output — the gap the numeric-only checker missed.
    """
    rendered = "Cohort mean RAF 1.234 — population looks healthy."
    r = er.check_no_fabricated_raf_scores(rendered, _SCORER_RECORD_WITH_NOTE)
    assert r.status == "fail", r.detail
    assert "not surfaced" in r.detail


def test_raf_notes_surfaced_verbatim_passes():
    """Surfacing the note verbatim alongside the RAF passes the gate."""
    rendered = (
        "Cohort mean RAF 1.234. CAVEAT: V28 committed reference CSV(s) absent; "
        "scoring on the sparse in-code fallback — diagnosis RAF is "
        "under-captured."
    )
    r = er.check_no_fabricated_raf_scores(rendered, _SCORER_RECORD_WITH_NOTE)
    assert r.status == "pass", r.detail


def test_raf_not_available_caveat_substitutes_for_note():
    """An explicit not-available-and-why caveat satisfies the disclosure intent.

    When the scorer could not produce a figure the contract permits
    not-available-and-why in place of the raw note string.
    """
    rendered = (
        "Cohort RAF is not available for this segment — the V28 reference data "
        "was absent at scoring time."
    )
    r = er.check_no_fabricated_raf_scores(rendered, _SCORER_RECORD_WITH_NOTE)
    assert r.status == "pass", r.detail


def test_raf_empty_notes_do_not_trip_the_gate():
    """A scorer record with no notes is clean — surfacing nothing is correct."""
    clean = {"raf": 1.234, "member_id": "M1", "model": "cms-hcc-v28",
             "demographic_component": 0.4, "hcc_component": 0.834,
             "interaction_component": 0.0, "notes": []}
    r = er.check_no_fabricated_raf_scores("Cohort mean RAF 1.234", clean)
    assert r.status == "pass", r.detail


def test_raf_unrelated_caveat_does_not_suppress_note():
    """An 'n/a' bound to something else must NOT suppress the scorer-note gate.

    Round-6 finding 2: the caveat was a single global flag — any 'not available'
    anywhere (a missing benchmark, a blank table cell) suppressed the entire
    notes-surfacing gate while the RAF still read as clean. The caveat must be
    bound to the RAF claim; an unrelated one far from any RAF cue does not count.
    """
    filler = (
        "The cohort skews older with a high chronic-condition burden, and the "
        "attribution model assigns members on a prospective basis. Utilization "
        "ran above the regional median across inpatient and outpatient settings. "
    )
    rendered = (
        "Cohort mean RAF 1.234 — population looks healthy.\n\n"
        + filler
        + "Prior-year benchmark spend was not available for this region."
    )
    r = er.check_no_fabricated_raf_scores(rendered, _SCORER_RECORD_WITH_NOTE)
    assert r.status == "fail", r.detail
    assert "not surfaced" in r.detail


def test_raf_caveat_adjacent_to_claim_still_passes():
    """A not-available caveat next to the RAF figure still substitutes for the note."""
    rendered = (
        "Cohort mean RAF could not be computed cleanly — the V28 reference data "
        "was unavailable at scoring time."
    )
    r = er.check_no_fabricated_raf_scores(rendered, _SCORER_RECORD_WITH_NOTE)
    assert r.status == "pass", r.detail


def test_raf_notes_only_pulled_from_scorer_shaped_dicts():
    """A bare {"notes": [...]} blob (not scorer-shaped) is not enforced.

    Only genuine RiskScore provenance (raf + companion fields) carries the
    surfacing obligation; an unrelated notes list elsewhere must not fail a
    deliverable that never claimed a scorer warning.
    """
    sidecar = {"misc": {"notes": ["some unrelated annotation"]},
               "risk_score_provenance": {"population_average_raf": 1.234}}
    r = er.check_no_fabricated_raf_scores("Cohort mean RAF 1.234", sidecar)
    assert r.status == "pass", r.detail
