"""Cross-cutting eval-coverage gates: every Outputs-layer skill ships an
evals.json with the mandatory anti-fabrication eval, and every methodology/domain
(judgment) skill ships an anti-confabulation fidelity blocker. These are
structural checks, not behavioral ones.

Corpus root
-----------
These gates run against a *corpus root* that contains a ``skills/{outputs,
methodology, domain}/<skill>/evals/evals.json`` tree. In medecon-verify the
corpus defaults to the self-contained fixture tree under
``tests/evals_fixture/skills/`` — a minimal but real positive for every gate, so
the well-formedness, anti-fabrication, and fidelity-severity assertions actually
execute here rather than being skipped.

The full medecon-stack-wide sweep — the same gates over the entire live
``skills/**`` tree — continues to run *in the medecon-stack repo*, whose
``tests/test_eval_coverage.py`` roots at its own repo. Point ``MEDECON_EVAL_CORPUS_ROOT``
at any corpus root to override the default (that is how medecon-stack aims these
identical gates at its skills tree). The enumeration and every assertion below
are identical to that original — only the root is parameterized.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# The corpus root these gates enumerate. Defaults to the self-contained fixture
# corpus that travels with medecon-verify; overridable via env so the identical
# gates can be aimed at the medecon-stack-wide skills/ tree.
_DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parent / "evals_fixture"
CORPUS_ROOT = Path(os.environ.get("MEDECON_EVAL_CORPUS_ROOT", _DEFAULT_CORPUS_ROOT))
OUTPUTS_DIR = CORPUS_ROOT / "skills" / "outputs"


def _outputs_skills() -> list[Path]:
    return sorted(p.parent for p in OUTPUTS_DIR.glob("*/SKILL.md"))


@pytest.mark.parametrize("skill", _outputs_skills(), ids=lambda p: p.name)
def test_outputs_skill_has_evals_file(skill: Path):
    evals = skill / "evals" / "evals.json"
    assert evals.exists(), (
        f"{skill.name}: missing evals/evals.json — every Outputs-layer skill "
        "must ship at least one eval"
    )


@pytest.mark.parametrize("skill", _outputs_skills(), ids=lambda p: p.name)
def test_outputs_skill_evals_well_formed(skill: Path):
    evals = json.loads((skill / "evals" / "evals.json").read_text())
    assert isinstance(evals.get("evals"), list) and evals["evals"], (
        f"{skill.name}: evals.json must have an 'evals' array with ≥1 entry"
    )
    for e in evals["evals"]:
        assert e.get("id"), f"{skill.name}: eval missing id: {e}"
        assert e.get("description"), f"{skill.name}: eval {e['id']} missing description"


# Anti-fabrication is mandatory for skills that produce prose with numbers in it.
# Pure infrastructure/policy skills (privacy-output-guardrails) are exempt.
ANTI_FAB_EXEMPT = {
    "privacy-output-guardrails",
    "methodology-disclosure-and-reproducibility",
}


@pytest.mark.parametrize("skill", _outputs_skills(), ids=lambda p: p.name)
def test_outputs_skill_has_anti_fabrication_eval(skill: Path):
    if skill.name in ANTI_FAB_EXEMPT:
        pytest.skip(f"{skill.name} is exempt from anti-fabrication (no prose with numbers)")
    evals = json.loads((skill / "evals" / "evals.json").read_text())
    ids = {e.get("id", "") for e in evals["evals"]}
    assert "anti-fabrication-mandatory" in ids, (
        f"{skill.name}: missing the 'anti-fabrication-mandatory' eval — "
        "this is required per the cross-cutting constraint"
    )


# ---------------------------------------------------------------------------
# Knowledge-layer coverage: methodology/ and domain/ skills encode JUDGMENT,
# not numeric renderers, so their highest-impact failure isn't a fabricated
# total in a sidecar — it's confidently-wrong guidance: an invented statutory
# threshold, a hallucinated estimator, a misremembered code-set semantic. Per
# the repo's #1 failure-mode discipline, every judgment skill must ship at
# least one anti-confabulation / fidelity blocker that pins what it must never
# get wrong. (This extends the Outputs-only gate above to the latent layers,
# per "every fat skill needs an eval", not just the renderers.)
# ---------------------------------------------------------------------------

KNOWLEDGE_DIRS = ("methodology", "domain")

# ids (or id substrings) that count as an anti-confabulation / fidelity gate.
_FIDELITY_ID_PATTERNS = ("fabricat", "confabulat", "fidelity", "no-fabricated")

# Deterministic infrastructure skills that emit no analytical guidance/numbers,
# so confident-fabrication isn't their failure mode — their blocker evals are
# structural invariants instead. (The knowledge-layer analog of the Outputs
# ANTI_FAB_EXEMPT set.)
KNOWLEDGE_FIDELITY_EXEMPT = {
    "resolver-integrity",  # deterministic routing audit; blockers are structural
}


def _knowledge_skills() -> list[Path]:
    out: list[Path] = []
    for cap in KNOWLEDGE_DIRS:
        out.extend(sorted(p.parent for p in (CORPUS_ROOT / "skills" / cap).glob("*/SKILL.md")))
    return out


@pytest.mark.parametrize("skill", _knowledge_skills(), ids=lambda p: p.name)
def test_knowledge_skill_has_evals_file(skill: Path):
    evals = skill / "evals" / "evals.json"
    assert evals.exists(), (
        f"{skill.name}: missing evals/evals.json — every methodology/ and domain/ "
        "skill must ship evals (judgment skills need contracts too, not just renderers)"
    )


@pytest.mark.parametrize("skill", _knowledge_skills(), ids=lambda p: p.name)
def test_knowledge_skill_evals_well_formed(skill: Path):
    payload = json.loads((skill / "evals" / "evals.json").read_text())
    evals = payload.get("evals")
    assert isinstance(evals, list) and len(evals) >= 2, (
        f"{skill.name}: evals.json must have an 'evals' array with ≥2 entries"
    )
    for e in evals:
        assert e.get("id"), f"{skill.name}: eval missing id: {e}"
        assert e.get("description"), f"{skill.name}: eval {e.get('id')} missing description"


# A fidelity blocker must carry a real contract, not just a matching id — guard
# against a stub like {id:'no-fabricated-parameters', description:'TODO'} that
# would satisfy a name-only check while encoding nothing.
_PLACEHOLDER_MARKERS = ("todo", "tbd", "fixme", "xxx", "placeholder", "...")
_MIN_CONTRACT_LEN = 40  # chars of substantive assert/description text


def _is_substantive(eval_obj: dict) -> bool:
    """A real contract: has an `assert` (or description) of meaningful length
    that isn't a placeholder."""
    contract = (eval_obj.get("assert") or eval_obj.get("description") or "").strip()
    if len(contract) < _MIN_CONTRACT_LEN:
        return False
    lowered = contract.lower()
    return not any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


@pytest.mark.parametrize("skill", _knowledge_skills(), ids=lambda p: p.name)
def test_knowledge_skill_has_fidelity_blocker(skill: Path):
    """The #1 failure mode for a judgment skill is confident fabrication. Every
    methodology/ and domain/ skill must carry at least one blocker-severity eval
    that guards against inventing parameters, citations, thresholds, or findings —
    and that eval must carry a real contract, not just a matching id (a stub with
    a `no-fabricated-*` id and a 'TODO' body does not count)."""
    if skill.name in KNOWLEDGE_FIDELITY_EXEMPT:
        pytest.skip(f"{skill.name} is deterministic infra (no analytical guidance to fabricate)")
    payload = json.loads((skill / "evals" / "evals.json").read_text())
    named = [
        e
        for e in payload["evals"]
        if e.get("severity") == "blocker"
        and any(pat in e.get("id", "").lower() for pat in _FIDELITY_ID_PATTERNS)
    ]
    assert named, (
        f"{skill.name}: no anti-confabulation/fidelity blocker eval found. Add a "
        "severity:'blocker' eval whose id signals it (e.g. 'no-fabricated-parameters' "
        "or 'no-fabricated-findings') pinning what this skill must never invent."
    )
    substantive = [e for e in named if _is_substantive(e)]
    assert substantive, (
        f"{skill.name}: the fidelity blocker(s) {[e.get('id') for e in named]} have no "
        f"substantive contract (need an `assert`/`description` ≥{_MIN_CONTRACT_LEN} chars "
        "and not a TODO/placeholder). A fidelity gate satisfied by id-naming alone is theater."
    )
