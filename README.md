# medecon-verify

The correctness-and-compliance layer for healthcare AI agents, extracted from the
MIT-licensed [medecon-stack](https://github.com/sjanoe123/medecon-stack) plugin.

A **no-third-party-dependency core library** that drops in between any LLM and its
output:

- `medecon_verify.adjudication` — final-action claim dedup, O(N) reversal-pair
  detection, FY-boundary blended-total warnings.
- `medecon_verify.codeset` — ICD-10-CM / HCPCS / MS-DRG / CMS-HCC vintage stamping.
- `medecon_verify.privacy` — n≤10 cell suppression, age-90+ aggregation, 42 CFR
  Part 2 redisclosure flag.
- `medecon_verify.phi` — heuristic, pattern-based Safe Harbor identifier redaction.
  **Scope & limitations:** this is deterministic, pattern-based identifier
  redaction — fail-closed and reproducible. It is *not* a substitute for Expert
  Determination, and bare names without a title prefix in free prose are out of
  scope for the regex tier.
- `medecon_verify.dateparse` — tolerant date parsing shared by the above.
- `medecon_verify.glossary` — healthcare-economics term glossary.

## Install

```bash
pip install medecon-verify          # core library, stdlib-only
pip install "medecon-verify[cli]"   # adds the `medecon-verify` command (click)
```

The console script installs unconditionally; without the `[cli]` extra it exits
with a clear message telling you to install it.

## 2.1.0 — declaring which hypothesis a finding answers

A finding names its branch in `answers`. **Prefer an explicit reference** — a
branch id or a 0-based index:

```jsonc
"hypotheses": [{"id": "H1", "hypothesis": "coding intensity rose ..."}],
"findings":   [{"finding": "...", "method": "...", "answers": "H1"}]
//                                                 "answers": 0   also works
```

An explicit reference is exact: no tokenizing, no thresholds, and rewording the
branch text cannot silently break the mapping.

Free text remains supported and is matched by **containment** — a claim that is
a prefix, a suffix, or a faithful paraphrase of a branch resolves to it.
Previously the rule required the claim to cover every content token of the
branch, which is right for short labels (`"MLR drift"` vs `"RAF lag"`) but
unsatisfiable for a branch written as a sentence, where the claim would have to
reproduce even editorial filler. Every finding on such a tree was reported as
scope drift.

Containment measures **precision** — what share of the claim's own words appear
in the branch — so branch verbosity is irrelevant. On the tree that exposed the
bug it scored 1.00 for every intended branch and at most 0.22 for any other; the
threshold sits at 0.80, inside that gap. Guards: claims under 4 content tokens
fall through to the full-coverage rule, a claim must cover at least 30% of the
branch, and a claim contained in two branches maps to neither.

### 2.2.1 — GAAP "attributable to" boilerplate is not a driver claim

`attributable to` sits in both driver-phrase lists, and it is also the standard
income-statement line name — "Net income (loss) attributable to
non-controlling interests" / "... attributable to *Company*, Inc." — appearing
in essentially every 10-Q/10-K. A rendered section quoting an income statement
verbatim tripped the orphan driver-claim scan (2026-08-02, consumer daily run):
an accounting allocation label was read as a causal assertion.

The exemption is the minimum that clears documented accounting idiom, and BOTH
sides of the phrase must look like the line: preceded in-clause by `net income`
/ `net loss` / `net income (loss)`, **and** followed by an ownership party
(`controlling` / `non-controlling interests`, or an Inc./Corp./LLC-shaped
entity). A genuine causal claim that borrows the words — `"the net loss is
attributable to the new facility ramp"` — still blocks: its object is a cause,
not an owner. Applied identically to the finding-text path and the
rendered-block scan; three decoy fixtures pin the narrowness.

### 2.2.0 — the composition-as-driver blocker closes its escape hatch

**This can newly BLOCK deliverables that previously passed** — deliberately.

A driver claim is excused when a decomposition backs it, so `is_decomposition`
is the gate's escape hatch. It was a plain substring test, which meant any
`method` string merely *containing* "decomposition" opened it — including one
that denies the method outright:

```
"composition groupby only; decomposition not performed"   # read as BOTH
"groupby cohort; regression not run"                      # blocker suppressed
```

The method that said in words that no decomposition was run was the method that
excused the driver claim.

Classification is now **word-bounded** (`composition` is a substring of
`decomposition`, so unbounded matching read every decomposition as a
composition too) and **clause-level negation-aware**. Each clause votes, and an
explicit denial beats a positive, so a method cannot both claim and disclaim a
decomposition to get past the gate.

Clause splitting is what keeps a negation bound to the method it actually
denies — `"trend decomposition, not a groupby"` does not negate its own
decomposition, because the negation lands in the second clause with the groupby
it refers to. Both directions are pinned by tests: eight method strings that
must still block, five that must not become false blockers.

If a deliverable starts blocking after this upgrade, the finding is claiming a
cause its method cannot support. That is the gate working.

### 2.1.1 — matcher correctness fixes

Found by an adversarial review of 2.1.0. Depend on **>= 2.1.1**, not a bare
`>= 2`:

- **A 0-based index counts branches as DECLARED.** 2.1.0 compacted away
  branches whose label carried no content token (short codes like `ED` are
  stripped by the <3-char rule), silently renumbering every later index — so a
  reference could resolve to a branch the producer never named and report
  success.
- **A tree that tokenizes to nothing now reports instead of passing.** When
  every branch label stripped to empty, the branch list went empty and the
  off-tree check stopped running altogether.
- **Full coverage gained an ambiguity guard.** When several branches are fully
  covered, the most specific (largest token set) wins and a tie maps to
  nothing. Two branches that tokenize identically no longer resolve to whichever
  was declared first.
- **Duplicate branch ids no longer resolve.** Taking the first silently mapped
  a finding to an arbitrary one of them.
- Negative indices, floats, and numeric strings (`"0"`) are explicitly not
  selectors — they fall through to the text paths and are reported rather than
  guessed at.

> **Upgrading from 2.0.0.** 2.0.0 matched free text by branch-side *coverage*
> (0.40 with a 2x margin over the next-best branch). 2.1.0 replaces that with
> containment, so a small number of free-text claims may resolve differently —
> high-coverage-but-low-precision claims no longer map, and faithful fragments
> now do. Explicit references are unaffected. 2.0.0 was tagged the same day and
> superseded within hours; if you pinned it, move to 2.1.0.

## Breaking changes in 2.0.0

`medecon_verify.certify.runner` — the eval/checker layer. **Read this before
upgrading if you gate a deliverable on `check_analysis_answers_scope`.**

1. **`EvalResult.status` gains a `warn` value** (was `pass | fail |
   not_runnable | skipped`). A `warn` is a real finding that is not
   ship-blocking. Code shaped like `if status == "fail": block() else: ship()`
   will now ship a warned result — audit any such branch. `gate()` excludes
   `warn` from the accuracy denominator, so launch gates do not move.
2. **`EvalReport.summary` gains a `warned` key.** Exact-dict assertions on
   `summary` will fail.
3. **`check_analysis_answers_scope` returns `warn`, not `fail`, when a finding
   maps to no declared `/scope` hypothesis branch.** This is the softening that
   forces the major: a gate that used to block now reports. Composition
   presented as a driver — the checker's other failure mode, and the one that
   catches an unsupportable claim — still returns `fail` and still blocks.

   The rationale: an off-tree finding is not a wrong claim, it is a claim
   outside the declared scope. That covers both a legitimate external
   cross-check and an unscoped fishing result, and no text-level checker can
   tell them apart. Blocking both gated honest work on a bookkeeping omission.
   The condition is still detected, still reported, and still recorded in the
   telemetry record — the judgement moves to the human.

## License

MIT.
