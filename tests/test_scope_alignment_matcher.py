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


class TestExplicitBranchReference:
    """The contract that removes the matching problem: an id or an index."""

    ID_TREE = [
        {"id": "H1", "hypothesis": "MLR drift"},
        {"id": "H2", "hypothesis": "RAF lag"},
    ]

    def _sc(self, answers, tree=None):
        return {
            "hypotheses": tree if tree is not None else self.ID_TREE,
            "findings": [{"finding": "Margin moved.", "method": "decomposition",
                          "answers": answers}],
        }

    def test_branch_id_maps(self) -> None:
        assert er.check_analysis_answers_scope("", self._sc("H2")).status == "pass"

    def test_branch_id_is_case_and_space_insensitive(self) -> None:
        assert er.check_analysis_answers_scope("", self._sc(" h2 ")).status == "pass"

    def test_zero_index_maps(self) -> None:
        # `answers: 0` is a valid reference to the FIRST branch. Resolving the
        # declaration field by truthiness would drop it silently.
        assert er.check_analysis_answers_scope("", self._sc(0)).status == "pass"

    def test_last_index_maps(self) -> None:
        assert er.check_analysis_answers_scope("", self._sc(1)).status == "pass"

    def test_out_of_range_index_is_reported_not_resolved(self) -> None:
        # A dangling reference must surface rather than resolve to something
        # approximate.
        r = er.check_analysis_answers_scope("", self._sc(7))
        assert r.status == "warn"

    def test_unknown_id_is_reported(self) -> None:
        r = er.check_analysis_answers_scope("", self._sc("H9"))
        assert r.status == "warn"

    def test_true_is_not_a_branch_selector(self) -> None:
        # bool is an int in Python; `answers: true` must not select branch 1.
        r = er.check_analysis_answers_scope("", self._sc(True))
        assert r.status == "warn"

    def test_id_reference_survives_a_branch_rewording(self) -> None:
        # The whole point of the contract: rewording the branch text does not
        # break a mapping expressed as an id. The free-text equivalent
        # ("MLR drift") would no longer match this reworded branch.
        reworded = [
            {"id": "H1", "hypothesis": "medical loss ratio deterioration "
                                       "year over year in the retained book"},
            {"id": "H2", "hypothesis": "RAF lag"},
        ]
        r = er.check_analysis_answers_scope("", self._sc("H1", tree=reworded))
        assert r.status == "pass"

    def test_index_is_positional_in_the_DECLARED_tree(self) -> None:
        # An index counts branches as the producer DECLARED them, including one
        # whose label carries no content token ("ED" is stripped by the <3-char
        # rule). Compacting the list first renumbered every later index, so
        # `answers: 1` silently resolved to a branch nobody named — a wrong
        # mapping reported as success.
        tree = [
            {"id": "H1", "hypothesis": "ED"},           # tokenizes to nothing
            {"id": "H2", "hypothesis": "coding intensity"},
        ]
        # 1 is "coding intensity" as declared, and must resolve.
        assert er.check_analysis_answers_scope(
            "", self._sc(1, tree=tree)).status == "pass"
        # 2 is past the end of the declared tree.
        assert er.check_analysis_answers_scope(
            "", self._sc(2, tree=tree)).status == "warn"

    def test_duplicate_ids_do_not_resolve(self) -> None:
        # Two branches claiming the same id is a producer error. Taking the
        # first would map the finding to an arbitrary one of them.
        tree = [
            {"id": "H1", "hypothesis": "coding intensity"},
            {"id": "H1", "hypothesis": "network access"},
        ]
        r = er.check_analysis_answers_scope("", self._sc("H1", tree=tree))
        assert r.status == "warn"

    def test_numeric_string_and_float_are_not_selectors(self) -> None:
        # Only a real int indexes. "0" and 0.0 fall through to the text paths
        # and are reported rather than guessed at.
        for bad in ("0", 0.0):
            assert er.check_analysis_answers_scope(
                "", self._sc(bad)).status == "warn", bad

    def test_negative_index_does_not_wrap(self) -> None:
        # Python would read -1 as the last branch; that would silently select a
        # branch the producer did not name.
        assert er.check_analysis_answers_scope(
            "", self._sc(-1)).status == "warn"


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

    def test_claim_contained_in_two_branches_does_not_map(self) -> None:
        # The uniqueness guard. This claim is fully contained in BOTH branches
        # (precision 1.00 against each), differing only in the phrase it omits.
        # A claim that cannot distinguish two branches maps to neither.
        tree = [
            {"branch": "inpatient admissions rose because of expanded access "
                       "across the commercial population in the northern region"},
            {"branch": "inpatient admissions rose because of coding intensity "
                       "across the commercial population in the northern region"},
        ]
        sidecar = {
            "hypotheses": tree,
            "findings": [{
                "finding": "A quantitative result.", "method": "groupby",
                "answers": "inpatient admissions rose across the commercial "
                           "population in the northern region",
            }],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "warn"

    def test_generic_fragment_of_a_branch_does_not_map(self) -> None:
        # Precision 1.00 (every word is lifted from branch 1) but coverage far
        # under 0.30 - a claim assembled from a branch's filler has not engaged
        # with the hypothesis. This is what the coverage floor is for.
        r = er.check_analysis_answers_scope("", _sidecar(
            "the hospital organizations they report", finding="A result."
        ))
        assert r.status == "warn"

    def test_short_claim_contained_in_a_branch_does_not_map(self) -> None:
        # "financial assistance" is contained in all three branches and
        # identifies none. Below the 4-token floor the containment path is
        # unavailable, so this cannot map on precision alone.
        r = er.check_analysis_answers_scope("", _sidecar(
            "financial assistance", finding="A result."
        ))
        assert r.status == "warn"

    def test_branches_that_tokenize_identically_are_ambiguous(self) -> None:
        # "ED"/"IP" are stripped by the <3-char rule, leaving two branches with
        # IDENTICAL token sets. A claim about OP utilization answers neither
        # and must not map to whichever was declared first. The full-coverage
        # path needs its own ambiguity guard for this - containment's
        # uniqueness check never runs, because full coverage matches first.
        sidecar = {
            "hypotheses": ["ED utilization increased in rural counties",
                           "IP utilization increased in rural counties"],
            "findings": [{
                "finding": "OP utilization increased in rural counties.",
                "method": "decomposition",
                "answers": "OP utilization increased in rural counties",
            }],
        }
        assert er.check_analysis_answers_scope("", sidecar).status == "warn"

    def test_more_specific_branch_wins_when_nested(self) -> None:
        # The flip side of the ambiguity guard: when one covered branch's
        # tokens are a strict subset of another's, that is not a tie - the
        # more specific branch is the better answer and must still map.
        sidecar = {
            "hypotheses": ["coding intensity",
                           "coding intensity rose in the commercial book"],
            "findings": [{
                "finding": "A result.", "method": "decomposition",
                "answers": "coding intensity rose in the commercial book",
            }],
        }
        assert er.check_analysis_answers_scope("", sidecar).status == "pass"

    def test_tree_that_tokenizes_to_nothing_still_reports(self) -> None:
        # Every branch label strips to an empty token set. Previously the
        # branch list went empty and failure mode 2 stopped running entirely,
        # so an off-tree finding came back "pass" - the gate went quiet
        # instead of reporting that it could not match.
        sidecar = {
            "hypotheses": [{"id": "H1", "hypothesis": "ED"},
                           {"id": "H2", "hypothesis": "IP"}],
            "findings": [{"finding": "Pharmacy spend rose.", "method": "sum",
                          "answers": "weather"}],
        }
        assert er.check_analysis_answers_scope("", sidecar).status == "warn"

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


class TestMethodNegation:
    """The escape hatch must not be openable by a method that denies itself.

    `is_decomposition` excuses a driver claim. A plain substring test opened it
    for any string merely CONTAINING "decomposition" -- including one saying no
    decomposition was run. These pin both directions: the escape is closed, and
    a legitimate decomposition is not turned into a false blocker.
    """

    def _sc(self, method):
        return {
            "scope": {"hypothesis_tree": ["access expansion"]},
            "findings": [{
                "finding": "ED visits rose, driven by the ESRD cohort",
                "method": method, "answers": "access expansion",
            }],
        }

    @pytest.mark.parametrize("method", [
        "composition groupby only; decomposition not performed",
        "groupby cohort; regression not run",
        "composition groupby; a decomposition was NOT run",
        "no decomposition was performed, only a share-of-total cut",
        "composition, not decomposition",
        "share of total; no causal claim",
        "composition (groupby cohort, share of total)",
        "groupby",
    ])
    def test_composition_driver_still_blocks(self, method) -> None:
        r = er.check_analysis_answers_scope("", self._sc(method))
        assert r.status == "fail", f"{method!r} -> {r.status}: {r.detail}"
        assert "composition" in r.detail.lower()

    @pytest.mark.parametrize("method", [
        "trend decomposition (util x unit cost x case mix waterfall)",
        "trend decomposition, not a groupby",     # must not negate ITSELF
        "decomposition, not composition",
        "waterfall decomposition",
        "shapley variance decomposition",
    ])
    def test_real_decomposition_is_not_a_false_blocker(self, method) -> None:
        # The other direction: loosening until nothing blocks is one failure;
        # tightening until honest work blocks is the other.
        r = er.check_analysis_answers_scope("", self._sc(method))
        assert r.status == "pass", f"{method!r} -> {r.status}: {r.detail}"

    def test_composition_substring_of_decomposition_is_not_a_match(self) -> None:
        # "composition" is a substring of "decomposition". Unbounded matching
        # read every decomposition as a composition too, and let a negated
        # decomposition clause cancel a real composition flag.
        assert er._method_flags("decomposition") == (False, True)
        assert er._method_flags("composition") == (True, False)


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


class TestGaapAttributionExemption:
    """GAAP allocation boilerplate must not read as a driver claim.

    "Net income (loss) attributable to non-controlling interests" is the
    standard income-statement line name in essentially every 10-Q/10-K. It
    allocates WHO keeps the income; it asserts nothing about WHY a number
    moved. A draft quoting an income statement verbatim tripped the orphan
    driver-claim scan on 2026-08-02 (healthnews daily run) because
    "attributable to" sits in both driver-phrase lists.

    The exemption is deliberately narrow: BOTH sides of the phrase must look
    like the idiom (net income/loss before it, an ownership party after it).
    A genuine causal claim that borrows the words — including one about a net
    loss — must keep flagging. All fixtures synthetic; the rendered fixture
    mirrors the real flagged sentence's shape.
    """

    # The real flagged shape: a section quoting income-statement lines, bound
    # to a finding whose method is a quotation (neither composition nor
    # decomposition), alongside a separate composition finding that arms the
    # orphan scan.
    GAAP_RENDERED = (
        "# Article\n\n"
        "## The same building is worth more to some owners than others\n\n"
        "Start with what gets kept. Operating income was $65.8 million. Net "
        "interest was $69.1 million, leaving a $3.3 million pretax loss. Then "
        "$33.8 million of net income was attributable to non-controlling "
        "interests, the minority owners of individual facilities, and the "
        "parent company booked a $35.9 million net loss.\n\n"
        "## Payer mix section\n\n"
        "Medicare share of cases was 41 percent, a share-of-total cut by "
        "payer.\n"
    )

    GAAP_SIDECAR = {
        "hypotheses": [
            {"id": "H2", "hypothesis": "Payer mix shifted toward Medicare"},
            {"id": "H3", "hypothesis": "Parent-level profitability lags "
                                       "facility-level operating income"},
        ],
        "findings": [
            {"finding": "Medicare share of cases was 41 percent",
             "answers": "H2",
             "method": "composition groupby by payer; share-of-total cut"},
            {"finding": "The company reported $65.8M of operating income "
                        "against $69.1M of net interest expense; $33.8M of "
                        "net income was attributable to non-controlling "
                        "interests and the parent booked a $35.9M net loss.",
             "answers": "H3",
             "method": "direct quotation of reported financial statement "
                       "lines; no model, no attribution analysis"},
        ],
    }

    def test_quoted_income_statement_does_not_block(self) -> None:
        # Failure mode 1b (orphan scan): the GAAP sentence is the only
        # strong-phrase hit in the artifact and must not fire the blocker.
        r = er.check_analysis_answers_scope(self.GAAP_RENDERED, self.GAAP_SIDECAR)
        assert r.status == "pass", f"{r.status}: {r.detail}"

    def test_gaap_idiom_in_finding_text_does_not_block(self) -> None:
        # Failure mode 1 (finding-text path): a composition finding that
        # QUOTES the allocation line makes no causal claim either.
        sidecar = {
            "scope": {"hypothesis_tree": ["parent-level profitability"]},
            "findings": [{
                "finding": "Net income attributable to non-controlling "
                           "interests was $33.8M; parent-level profitability "
                           "was negative",
                "method": "composition (groupby owner, share of total)",
                "answers": "parent-level profitability",
            }],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "pass", f"{r.status}: {r.detail}"

    @pytest.mark.parametrize("line", [
        "Net income attributable to non-controlling interests was $33.8M.",
        "Net loss attributable to Surgery Partners, Inc. was $35.9M.",
        "Net income (loss) attributable to controlling interests widened.",
        "Net income attributable to noncontrolling interests fell.",
    ])
    def test_gaap_variants_are_exempt(self, line) -> None:
        rendered = (
            "# Article\n\n## Filings section\n\n" + line + "\n\n"
            "## Payer mix section\n\nMedicare share of cases was 41 percent.\n"
        )
        sidecar = {
            "hypotheses": [{"id": "H2",
                            "hypothesis": "Payer mix shifted toward Medicare"}],
            "findings": [{"finding": "Medicare share of cases was 41 percent",
                          "answers": "H2",
                          "method": "composition groupby by payer"}],
        }
        r = er.check_analysis_answers_scope(rendered, sidecar)
        assert r.status == "pass", f"{line!r} -> {r.status}: {r.detail}"

    @pytest.mark.parametrize("decoy", [
        # Real driver claims wearing the same words. Object is a CAUSE, not an
        # ownership party — the exemption must not cover them, including the
        # over-widening trap where "net loss" alone would exempt.
        "The margin decline is attributable to payer mix.",
        "Most of the loss is attributable to the new facility ramp.",
        "The net loss is attributable to the new facility ramp.",
    ])
    def test_causal_attributable_to_still_blocks_in_rendered(self, decoy) -> None:
        # Orphan-scan path: the decoy section shares no content words with any
        # finding, so it binds to nothing and must flag as an orphan claim.
        rendered = "# Article\n\n## Outlook\n\n" + decoy + "\n"
        sidecar = {
            "scope": {"hypothesis_tree": ["access expansion"]},
            "findings": [{"finding": "ED visits rose in the ESRD cohort",
                          "method": "composition groupby cohort",
                          "answers": "access expansion"}],
        }
        r = er.check_analysis_answers_scope(rendered, sidecar)
        assert r.status == "fail", f"{decoy!r} -> {r.status}: {r.detail}"

    def test_causal_attributable_to_still_blocks_in_finding_text(self) -> None:
        sidecar = {
            "scope": {"hypothesis_tree": ["margin pressure"]},
            "findings": [{
                "finding": "The margin decline is attributable to payer mix",
                "method": "composition (groupby payer, share of total)",
                "answers": "margin pressure",
            }],
        }
        r = er.check_analysis_answers_scope("", sidecar)
        assert r.status == "fail", f"{r.status}: {r.detail}"

    def test_other_strong_phrases_are_untouched(self) -> None:
        # The exemption is specific to "attributable to"; "driven by" in the
        # same GAAP-ish clause still flags.
        rendered = (
            "# Article\n\n## Outlook\n\n"
            "Net income was driven by non-controlling interests.\n"
        )
        sidecar = {
            "scope": {"hypothesis_tree": ["access expansion"]},
            "findings": [{"finding": "ED visits rose in the ESRD cohort",
                          "method": "composition groupby cohort",
                          "answers": "access expansion"}],
        }
        r = er.check_analysis_answers_scope(rendered, sidecar)
        assert r.status == "fail", f"{r.status}: {r.detail}"
