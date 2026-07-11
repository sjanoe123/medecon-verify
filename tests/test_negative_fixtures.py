"""Adversarial negative fixtures — prove every guardrail checker actually bites.

Golden (positive) fixtures only prove a checker passes on clean output, which is
near-tautological: refresh a golden after a renderer regresses and the bug gets
blessed (Codex outside-voice finding #5). These fixtures do the opposite — feed
each guardrail checker a deliberately POISONED rendered+sidecar and assert it
returns `fail`. A checker that silently passes poisoned input is worthless; this
suite is what proves it isn't.

One poisoned case per required-guardrail checker. Mirrors
REQUIRED_GUARDRAIL_EVAL_IDS in test_capability_matrix.py.
"""
from __future__ import annotations

import pytest

from medecon_verify.certify import runner as er

# (eval_id, poisoned_rendered, poisoned_sidecar, why-it-must-fail)
NEGATIVE_FIXTURES = [
    (
        "anti-fabrication-mandatory",
        "The savings rate was 47.3% this year.",  # 47.3 absent from sidecar
        {"analysis": {"savings_rate": 0.051}},
        "rendered number 47.3 has no sidecar source",
    ),
    (
        "code-set-stamp-present",
        "# Deck\nSome findings without any pinning footer.",
        {},
        "no Code-set pinning / ICD-10-CM stamp",
    ),
    (
        "small-cell-suppression-applied",
        "The flagged subgroup had n=3 members.",
        {},
        "unsuppressed small cell n=3 (<=10)",
    ),
    (
        "age-90-plus-aggregated",
        "Oldest attributed member: age: 94.",
        {},
        "individual age 94 not aggregated to 90+",
    ),
    (
        "safe-harbor-18-clean",
        "Beneficiary SSN 123-45-6789 appears in the export.",
        {},
        "Safe Harbor identifier (SSN) in rendered output",
    ),
    (
        "safe-harbor-18-clean",
        "Member detail: https://portal.example.com/member/8675309",
        {},
        "Safe Harbor identifier #14 (member-specific URL) in rendered output",
    ),
    (
        "part-2-redisclosure-flag",
        "Opioid use disorder cohort grew 12%.",  # SUD content, no banner
        {},
        "SUD content without the 42 CFR Part 2 banner",
    ),
    (
        "scanner-crash-defaults-to-redact",
        "row 1\nrow 2 passed through\nrow 3",  # no sentinel despite signalled crash
        # canonical guardrails shape: scan_failed nested under phi_scan_metrics
        {"privacy_audit": {"phi_scan_metrics": {"scan_failed": 1}}},
        "scan failure signalled but no redaction sentinel",
    ),
]


@pytest.mark.parametrize(
    "eval_id,rendered,sidecar,why",
    NEGATIVE_FIXTURES,
    ids=[f[0] for f in NEGATIVE_FIXTURES],
)
def test_guardrail_checker_fails_on_poisoned_input(
    eval_id: str, rendered: str, sidecar: dict, why: str
) -> None:
    checker = er.CHECKERS[eval_id]
    result = checker(rendered, sidecar)
    assert result.status == "fail", (
        f"{eval_id} should have FAILED on poisoned input ({why}) "
        f"but returned {result.status!r}: {result.detail}"
    )


def test_every_negative_fixture_targets_a_real_checker() -> None:
    """No negative fixture may reference an eval id that has no checker."""
    for eval_id, *_ in NEGATIVE_FIXTURES:
        assert eval_id in er.CHECKERS, f"{eval_id} is not a registered checker"


@pytest.mark.xfail(
    reason="needs-0.6-fixtures: imports test_capability_matrix, which stays in medecon-stack",
    strict=False,
)
def test_every_required_guardrail_has_a_biting_negative_fixture() -> None:
    """Meta-gate: every REQUIRED guardrail must have a negative fixture on which
    its checker actually returns `fail`.

    This is the structural close for the "guardrail that can't bite" class
    (render-split-discipline was in the required set but its checker could only
    ever return pass). If a required guardrail has no poisoned fixture, or its
    checker doesn't fail on the one it has, this test fails — so a non-biting
    guardrail can never silently claim required status again.
    """
    from test_capability_matrix import REQUIRED_GUARDRAIL_EVAL_IDS

    fixtures_by_id: dict[str, list] = {}
    for eval_id, rendered, sidecar, _why in NEGATIVE_FIXTURES:
        fixtures_by_id.setdefault(eval_id, []).append((rendered, sidecar))

    missing = sorted(REQUIRED_GUARDRAIL_EVAL_IDS - fixtures_by_id.keys())
    assert not missing, (
        f"required guardrails with no negative fixture (can't prove they bite): {missing}"
    )

    non_biting = []
    for eval_id in REQUIRED_GUARDRAIL_EVAL_IDS:
        checker = er.CHECKERS[eval_id]
        if not any(checker(r, s).status == "fail" for r, s in fixtures_by_id[eval_id]):
            non_biting.append(eval_id)
    assert not non_biting, (
        f"required guardrails whose checker never failed on its negative fixture: {non_biting}"
    )
