---
name: exec-summary-fixture
description: Fixture Outputs-layer skill for the eval-coverage gate. Not a real skill.
---

# exec-summary-fixture

Synthetic fixture standing in for a real Outputs-layer renderer so the
`test_eval_coverage` gates have a positive corpus to run against. It ships a
well-formed `evals/evals.json` carrying the mandatory `anti-fabrication-mandatory`
eval.
