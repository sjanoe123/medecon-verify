"""Branch-matching + severity contract for `check_analysis_answers_scope`.

The gate has two failure modes with two different severities:

- composition presented as a driver -> "fail" (ship-blocking wrong claim)
- finding maps to no declared branch -> "warn" (reported, never gating)

and a branch matcher that must tolerate the shapes an `answers` field really
takes against a SENTENCE-shaped hypothesis tree (prefix / suffix / paraphrase)
without becoming so loose that a genuinely off-tree finding matches anyway.

All fixtures here are synthetic.
"""

import pytest

from medecon_verify.certify import runner as er


# A sentence-shaped hypothesis tree, the shape that broke the original
# full-coverage-subset matcher. Branch token counts: 22 / 18 / 17.
SENTENCE_TREE = [
    {"branch": "the forgone federal income tax of the largest tax-exempt "
               "hospital organizations is of the same order of magnitude as "
               "the financial assistance they report, so the exemption is not "
               "obviously earned on that one line"},
    {"branch": "the variation in financial assistance across organizations "
               "holding the identical exemption is wide enough that a single "
               "national label conceals materially different behavior"},
    {"branch": "total community benefit is several times financial "
               "assistance, so the hospital industry's Medicaid-shortfall "
               "defense is materially correct and must be carried in the piece"},
]


def _sidecar(answers, finding="A neutral quantitative finding.", method="sum"):
    return {
        "hypotheses": SENTENCE_TREE,
        "findings": [{"finding": finding, "method": method, "answers": answers}],
    }


class TestBranchMatchShapes:
    """The three real `answers` shapes that must map to a sentence branch."""

    def test_exact_prefix_of_branch_maps(self) -> None:
        # answers stops before the branch's trailing ", so ..." clause.
        r = er.check_analysis_answers_scope("", _sidecar(
            "the forgone federal income tax of the largest tax-exempt hospital "
            "organizations is of the same order of magnitude as the financial "
            "assistance they report"
        ))
        assert r.status == "pass"

    def test_exact_suffix_of_branch_maps(self) -> None:
        # answers drops the branch's leading "total community benefit ..., so".
        r = er.check_analysis_answers_scope("", _sidecar(
            "the hospital industry's Medicaid-shortfall defense is materially "
            "correct and must be carried in the piece"
        ))
        assert r.status == "pass"

    def test_paraphrase_of_branch_maps(self) -> None:
        # answers drops two qualifying phrases ("of the largest tax-exempt
        # hospital organizations", "they report") - 0.45 coverage, 2.7x margin.
        r = er.check_analysis_answers_scope("", _sidecar(
            "the forgone federal income tax is of the same order of magnitude "
            "as the financial assistance"
        ))
        assert r.status == "pass"

    def test_single_missing_word_still_maps(self) -> None:
        # The original rule rejected a finding for missing one word ("several").
        r = er.check_analysis_answers_scope("", _sidecar(
            "total community benefit is times financial assistance, so the "
            "hospital industry's Medicaid-shortfall defense is materially "
            "correct and must be carried in the piece"
        ))
        assert r.status == "pass"

    def test_full_coverage_still_maps(self) -> None:
        r = er.check_analysis_answers_scope("", _sidecar(
            SENTENCE_TREE[1]["branch"]
        ))
        assert r.status == "pass"


class TestTrueNegatives:
    """A matcher loosened until nothing fails is worse than the bug."""

    def test_short_branch_partial_overlap_does_not_map(self) -> None:
        # THE key true negative. Branches "MLR drift"/"RAF lag" are 2 tokens
        # each; "RAF drift" covers 50% of each but neither in full. Short
        # branches never reach the ratio path, so this must stay unmapped.
        sidecar = {
            "scope": {"hypothesis_tree": ["MLR drift", "RAF lag"]},
            "findings": [{"finding": "A quantitative result.",
                          "method": "decomposition", "answers": "RAF drift"}],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "warn"
        assert "maps to no /scope hypothesis branch" in r.detail

    def test_off_tree_finding_does_not_map(self) -> None:
        # An external cross-check: ~0.27 coverage of its nearest branch.
        r = er.check_analysis_answers_scope("", _sidecar(
            None,
            finding="External cross-check: a published national study "
                    "estimated a federal benefit of $11.5 billion in 2021, "
                    "and found 7 percent of institutions below threshold.",
            method="comparison against an independently published estimate",
        ))
        assert r.status == "warn"
        assert "finding 1 maps to no /scope hypothesis branch" in r.detail

    def test_claim_spread_evenly_over_two_branches_does_not_map(self) -> None:
        # Coverage may clear 0.40 against a long branch, but without a 2x
        # margin over the runner-up it is not a match to either. This is the
        # margin rule doing the discriminating, not the threshold.
        tree = [
            {"branch": "inpatient admissions rose because of expanded access "
                       "across the commercial population in the northern region"},
            {"branch": "inpatient admissions rose because of coding intensity "
                       "across the commercial population in the northern region"},
        ]
        sidecar = {
            "hypotheses": tree,
            "findings": [{
                "finding": "Inpatient admissions rose across the commercial "
                           "population in the northern region.",
                "method": "groupby", "answers": None,
            }],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "warn"

    def test_junk_answers_does_not_map(self) -> None:
        sidecar = {
            "hypotheses": SENTENCE_TREE,
            "findings": [{"finding": "x", "method": "sum", "answers": "a"}],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "warn"


class TestSeveritySplit:
    """Unmapped warns; a wrong claim still blocks; a blocker never hides it."""

    def test_unmapped_is_warn_not_fail(self) -> None:
        r = er.check_analysis_answers_scope("", _sidecar("weather seasonality"))
        assert r.status == "warn"
        assert r.status != "fail"

    def test_composition_as_driver_still_blocks(self) -> None:
        sidecar = {
            "scope": {"hypothesis_tree": ["access expansion"]},
            "findings": [{
                "finding": "ED visits rose, driven by the ESRD cohort",
                "method": "composition (groupby cohort, share of total)",
                "answers": "access expansion",
            }],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "fail"
        assert "composition" in r.detail.lower()

    def test_blocker_does_not_suppress_the_unmapped_warning(self) -> None:
        # Both conditions at once: status is fail, but the off-tree finding
        # must still be named in the detail rather than swallowed.
        sidecar = {
            "scope": {"hypothesis_tree": ["access expansion"]},
            "findings": [
                {"finding": "ED visits rose, driven by the ESRD cohort",
                 "method": "composition (groupby cohort, share of total)",
                 "answers": "access expansion"},
                {"finding": "Pharmacy spend rose.", "method": "sum",
                 "answers": "weather seasonality"},
            ],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "fail"
        assert "non-blocking" in r.detail
        assert "finding 2 maps to no /scope hypothesis branch" in r.detail

    def test_clean_sidecar_passes(self) -> None:
        sidecar = {
            "scope": {"hypothesis_tree": ["access expansion", "coding intensity"]},
            "findings": [{"finding": "Coding intensity rose",
                          "method": "decomposition", "answers": "coding intensity"}],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "pass"


class TestWarnStatusPlumbing:
    """`warn` must be countable and must not move the launch gate."""

    def test_report_counts_warned(self) -> None:
        rep = er.EvalReport(results=[
            er.EvalResult("a", "s", "pass"),
            er.EvalResult("b", "s", "warn"),
            er.EvalResult("c", "s", "fail"),
        ])
        assert rep.warned == 1
        assert rep.summary["warned"] == 1
        assert rep.summary["total"] == 3

    def test_warn_excluded_from_gate_accuracy(self) -> None:
        # A warn is neither a correct answer nor an incorrect one; folding it
        # into either bucket would silently move a launch gate.
        rep = er.EvalReport(results=[
            er.EvalResult("a", "s", "pass"),
            er.EvalResult("b", "s", "warn"),
        ])
        assert er.gate(rep)["_overall"]["accuracy"] == pytest.approx(1.0)
        assert er.gate(rep)["_overall"]["passed"] == 1
        assert er.gate(rep)["_overall"]["failed"] == 0


class TestVacuousPasses:
    """Unchanged contract: nothing to align against is not a finding."""

    def test_no_findings(self) -> None:
        assert er.check_analysis_answers_scope("", {"scope": {}}).status == "pass"

    def test_no_method_and_no_tree(self) -> None:
        r = er.check_analysis_answers_scope(
            "", {"scope": {}, "findings": [{"finding": "ED visits up"}]}
        )
        assert r.status == "pass"
