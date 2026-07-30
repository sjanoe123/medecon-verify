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

Also in 2.0.0: the branch matcher accepts partial coverage for hypothesis
branches of 8+ content tokens (threshold 0.40 *and* a 2x margin over the
next-best branch). Short branch labels are unaffected and still require full
token coverage. Previously, a `/scope` tree written as sentences rather than
labels could not be matched at all — every finding on such a tree was reported
as scope drift.

## License

MIT.
