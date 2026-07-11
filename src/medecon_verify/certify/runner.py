"""Programmatic eval runner for medecon-stack skills.

Loads each skill's `evals/evals.json` and executes the assertions it can
evaluate programmatically. Eval definitions are JSON; the runner has built-in
checkers keyed by `id` (e.g. `anti-fabrication-mandatory`,
`code-set-stamp-present`, `small-cell-suppression-applied`). Evals without a
matching checker are reported as `not_runnable` rather than failing — the
runner is honest about what it can and can't verify.

Public API:

    discover_evals(repo_root: Path) -> list[EvalSpec]
    run(spec: EvalSpec, *, rendered: str, sidecar: dict) -> EvalResult
    run_all(repo_root: Path, samples: dict[str, dict]) -> EvalReport

The anti-fabrication checker compares numbers in `rendered` (markdown text)
against numbers in the `sidecar` payload. Numbers in rendered must appear in
the sidecar (verbatim or as float-equal). New numbers in rendered = fabrication.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class EvalSpec:
    skill_name: str
    skill_path: Path
    eval_id: str
    description: str
    severity: str = "warn"  # "blocker" or "warn"
    extra: dict = field(default_factory=dict)


@dataclass
class EvalResult:
    eval_id: str
    skill_name: str
    status: str  # pass | fail | not_runnable | skipped
    detail: str = ""


@dataclass
class EvalReport:
    results: list[EvalResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "fail")

    @property
    def not_runnable(self) -> int:
        return sum(1 for r in self.results if r.status == "not_runnable")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def summary(self) -> dict:
        return {
            "passed": self.passed, "failed": self.failed,
            "not_runnable": self.not_runnable, "skipped": self.skipped,
            "total": len(self.results),
        }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_evals(repo_root: Path) -> list[EvalSpec]:
    """Walk skills/**/evals/evals.json and yield one EvalSpec per eval entry."""
    out: list[EvalSpec] = []
    for evals_file in (repo_root / "skills").glob("**/evals/evals.json"):
        skill_dir = evals_file.parent.parent
        skill_name = skill_dir.name
        payload = json.loads(evals_file.read_text())
        for entry in payload.get("evals", []):
            out.append(EvalSpec(
                skill_name=skill_name,
                skill_path=skill_dir,
                eval_id=entry.get("id", ""),
                description=entry.get("description", ""),
                severity=entry.get("severity", "warn"),
                extra=entry,
            ))
    return out


# ---------------------------------------------------------------------------
# Built-in checkers
# ---------------------------------------------------------------------------


_NUMBER_PATTERN = re.compile(
    # Numeric tokens not adjacent to alphanumerics, dashes, or periods, so
    # "ICD-10-CM", "FY2025", and "-3.74%" (would-be "74%") don't get sliced
    # into spurious tokens.
    r"(?<![A-Za-z0-9_.\-])"
    r"(?:"
    r"\$?\d+(?:,\d{3})+(?:\.\d+)?%?"   # comma-grouped: $1,234,567.89
    r"|\$?\d+\.\d+%?"                    # decimals: 0.0180, 12.5%
    r"|\$?\d+%?"                         # plain integer: 100, 25%
    r")"
)


def extract_number_tokens(text: str) -> list[str]:
    """Ordered, normalized numeric tokens from text (the canonical extractor).

    Shared so callers that need to detect/extract numbers (e.g.
    `active_correction`) use the same notion of "a number" the anti-fabrication
    and oracle checkers use — one regex, no drift. Returns tokens in
    left-to-right order (unlike `_extract_numbers`, which dedups into a set).
    """
    return [
        m.group(0).replace(",", "").replace("$", "")
        for m in _NUMBER_PATTERN.finditer(text)
    ]


def _extract_numbers(text: str) -> set[str]:
    """Extract numeric tokens from text, normalized (set; order-independent).

    "1,234" → "1234"; "$1,234.50" → "1234.50"; "12.5%" → "12.5%"
    """
    return set(extract_number_tokens(text))


def _walk_payload_numbers(obj) -> set[str]:
    """Walk a payload dict/list and extract every numeric token."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _walk_payload_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _walk_payload_numbers(v)
    elif isinstance(obj, (int, float)):
        out.add(str(obj))
        out.add(f"{obj:.2f}".rstrip("0").rstrip("."))
    elif isinstance(obj, str):
        out |= _extract_numbers(obj)
    return out


# ---------------------------------------------------------------------------
# Checkers (id → callable)
# ---------------------------------------------------------------------------


def _to_float(token: str) -> float | None:
    """Parse a numeric token to float. Percent tokens divide by 100 so
    "1.20%" and 0.012 are treated as equivalent."""
    is_pct = token.endswith("%")
    cleaned = token.replace(",", "").replace("$", "").rstrip("%")
    try:
        v = float(cleaned)
        return v / 100 if is_pct else v
    except ValueError:
        return None


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Template-literal numbers we should ignore (e.g. "TL;DR (≤80 words)",
# "(1500-2200 word target)"). These describe the template, not the data.
_TEMPLATE_NUMERIC_GUIDANCE = re.compile(
    r"\(\s*[≤<>=]?\s*\d+(?:-\d+)?\s*(?:words?|chars?|sections?|pages?|slides?)[^)]*\)",
    re.IGNORECASE,
)
# Regulatory citations whose digits are legal references, not data (e.g.
# "42 CFR Part 2", "45 CFR 164.514"). Stripped before number extraction so a
# citation in deliverable prose (e.g. the Part-2 redisclosure warning) never
# reads as a fabricated number. Generalized to the "<n> CFR [Part] <n>" shape
# rather than a denylist of one specific citation string.
_REGULATORY_CITATIONS = re.compile(
    r"\b\d+\s*CFR(?:\s*Part)?\s*\d+(?:\.\d+)?", re.IGNORECASE
)


def check_anti_fabrication(rendered: str, sidecar: dict) -> EvalResult:
    """Every number in rendered prose must appear in the sidecar.

    Numbers compare *numerically* (e.g., "0.018" matches "0.0180"). Trivial
    integers (years 1900-2100, list indices 1-20) are whitelisted. HTML
    comments (validator warnings) and template-literal numeric guidance
    like "TL;DR (≤80 words)" are stripped before comparison.
    """
    cleaned = _HTML_COMMENT.sub("", rendered)
    cleaned = _TEMPLATE_NUMERIC_GUIDANCE.sub("", cleaned)
    cleaned = _REGULATORY_CITATIONS.sub("", cleaned)
    rendered_tokens = _extract_numbers(cleaned)
    sidecar_tokens = _walk_payload_numbers(sidecar)

    sidecar_floats: set[float] = set()
    for tok in sidecar_tokens:
        f = _to_float(tok)
        if f is not None:
            # Store rounded at multiple precisions so a sidecar 0.01095
            # matches a rendered 1.10% (0.0110) or 1.09% (0.0109).
            sidecar_floats.add(round(f, 6))
            sidecar_floats.add(round(f, 4))
            sidecar_floats.add(round(f, 3))
            sidecar_floats.add(round(f, 2))

    fabricated: set[str] = set()
    for tok in rendered_tokens:
        f = _to_float(tok)
        if f is None:
            continue
        # Whitelist: years 1900-2100, list indices 1-20
        if 1900 <= f <= 2100 and f == int(f):
            continue
        if 1 <= f <= 20 and f == int(f):
            continue
        # Try matching at increasing precision tolerance
        if (round(f, 6) in sidecar_floats
            or round(f, 4) in sidecar_floats
            or round(f, 3) in sidecar_floats
            or round(f, 2) in sidecar_floats):
            continue
        fabricated.add(tok)

    if fabricated:
        return EvalResult(
            eval_id="anti-fabrication-mandatory",
            skill_name="",
            status="fail",
            detail=f"fabricated numbers in rendered output: {sorted(fabricated)[:8]}",
        )
    return EvalResult(eval_id="anti-fabrication-mandatory", skill_name="",
                      status="pass", detail="every rendered number traces to sidecar")


# RAF-context cue words: a number adjacent to one of these reads as a
# risk-adjustment *result* (the thing the scorer must have produced) rather than
# a statutory parameter or an incidental figure. Matched case-insensitively in a
# small window around each numeric token in the rendered prose.
_RAF_CONTEXT_CUES = (
    "raf",
    "risk score",
    "risk-adjustment factor",
    "normalized pmpm",
    "risk-normalized",
    "o/e",
    "observed/expected",
    "observed-to-expected",
    # A coefficient / normalization-factor figure is a risk-adjustment *result*
    # the scorer produced (HCC weight, age-sex factor, MA normalization factor),
    # not a statutory parameter an analyst may type freely. Treat it as RAF-shaped
    # so a fabricated "coefficient 0.166" / "normalization factor 1.05" is judged.
    "coefficient",
    "normalization factor",
)

# A RiskScore-shaped dict is recognised as scorer provenance only when it looks
# like real `risk_adjustment.py` output, not merely because it carries a `raf`
# key. The signature /risk-adjust fabrication is an analyst who types
# "RAF 9.999" and *also* hand-adds {"raf": 9.999} to the sidecar; a bare {raf: x}
# dict must NOT launder that. We require the dict to carry at least one OTHER
# scorer-emitted field alongside `raf` — the RiskScore dataclass always emits
# member_id, model, the three components and triggered_hccs together, so a
# genuine scorer record trivially satisfies this while a hand-forged {raf: x}
# does not.
# Structural component fields the RiskScore dataclass ALWAYS co-emits with `raf`
# (member_id, model, demographic_component, hcc_component, interaction_component,
# triggered_hccs). These are the hard-to-forge signature: a genuine scorer record
# carries the full set, whereas a hand-forged sidecar value typically does not.
_RISKSCORE_STRUCTURAL_FIELDS = frozenset({
    "demographic_component", "hcc_component", "interaction_component",
    "triggered_hccs",
})

# Soft companion fields a scorer record also carries but which an analyst can
# trivially type alongside a forged `raf` (model="cms-hcc-v28", a notes string).
# These NO LONGER count as provenance on their own — see _is_riskscore_shaped.
_RISKSCORE_SOFT_FIELDS = frozenset({
    "model", "notes", "payment_year", "phase_in", "phase_in_year",
})

# Named aggregate provenance keys: scorer/aggregate-helper output values
# (population_average_raf / normalized_pmpm / oe_ratio …). These are bare numbers
# the helpers emit, so they are honoured ONLY when they live inside a recognised
# scorer-provenance object (see _is_provenance_object) — a bare top-level
# {"population_average_raf": 9.999} an analyst typed is NOT accepted, closing the
# mirror-the-figure forgery path.
_RISKSCORE_AGGREGATE_FIELDS = frozenset({
    "normalized_pmpm", "oe_ratio", "o_e_ratio", "average_raf",
    "population_average_raf", "mean_raf",
})

# Keys that mark a dict as a deliberate scorer-provenance wrapper. A renderer
# that embeds the scorer's aggregate output parks it under one of these (or
# emits the per-member RiskScore records themselves). Requiring the wrapper means
# a bare mirrored aggregate number cannot launder a typed figure.
_PROVENANCE_OBJECT_KEYS = frozenset({
    "risk_score_provenance", "riskscore_provenance", "scorer_provenance",
    "risk_adjustment_provenance", "raf_provenance",
})


def _is_riskscore_shaped(obj: dict) -> bool:
    """True when a dict looks like a genuine `risk_adjustment.py` RiskScore record.

    Requires a `raf` AND a *structural* scorer signature that an analyst cannot
    reproduce by typing trivial strings: at least one of the component fields the
    dataclass always co-emits (demographic_/hcc_/interaction_component or
    triggered_hccs). A bare ``{"raf": 9.999}`` — or one carrying only typeable
    identity/soft companions (``member_id``, ``model``, ``notes``) — is NOT
    scorer provenance and is rejected. member_id+model are three trivially
    typeable strings, so that pair no longer launders a forged RAF past the gate
    (round-6 finding 1); a real scorer record always carries the components, so
    this rejects forgeries without rejecting genuine output.
    """
    if "raf" not in obj:
        return False
    return any(k in obj for k in _RISKSCORE_STRUCTURAL_FIELDS)


def _is_provenance_object(obj: dict) -> bool:
    """True when a dict is a deliberate scorer-provenance wrapper.

    Either it is itself RiskScore-shaped, or it carries the scorer's identity
    markers (a `model` plus a payment-year / source / run marker), so a bare
    aggregate number parked inside it traces to an actual scorer run rather than
    a figure an analyst mirrored into the sidecar (round-5 finding 3).
    """
    if _is_riskscore_shaped(obj):
        return True
    has_model = "model" in obj
    has_run_marker = any(
        k in obj for k in (
            "payment_year", "source", "source_function", "run_id",
            "scorer", "scorer_version", "generated_by", "n_members",
            "member_count", "scores",
        )
    )
    return has_model and has_run_marker


def _risk_score_provenance_numbers(sidecar: dict) -> set[float]:
    """Collect RAF / component figures that came from an actual scorer run.

    Recognises the provenance the `risk_adjustment.py` scorer emits — RiskScore
    objects (``raf`` PLUS the structural component fields the dataclass always
    co-emits) and the aggregate helpers (``population_average_raf`` /
    ``normalized_pmpm`` / ``oe_ratio``). We walk RiskScore-shaped dicts anywhere
    in the payload, so a sidecar that simply embeds the scorer's JSON output is
    accepted without a wrapper.

    A bare ``{"raf": x}`` dict, or one carrying only a soft companion field
    (``model`` / ``notes``), is deliberately NOT accepted — that is the
    hand-forged-sidecar fabrication mode (round-5 finding 2). A bare aggregate
    number (``{"population_average_raf": 9.999}``) is likewise NOT honoured
    unless it lives inside a recognised scorer-provenance object, so a mirrored
    aggregate figure cannot launder a typed RAF (round-5 finding 3). Returns the
    set of float values (rounded to several precisions) that a rendered RAF
    figure may match.
    """
    floats: set[float] = set()

    def _add(v) -> None:
        f = _to_float(str(v)) if not isinstance(v, (int, float)) else float(v)
        if f is None:
            return
        for p in (6, 4, 3, 2):
            floats.add(round(f, p))

    def _walk(obj, in_provenance: bool = False) -> None:
        if isinstance(obj, dict):
            # A RiskScore-shaped dict (raf + structural scorer fields) is scorer
            # provenance: pull every numeric field from it.
            shaped = _is_riskscore_shaped(obj)
            if shaped:
                for val in obj.values():
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        _add(val)
            # Aggregate keys are honoured only inside a provenance context: the
            # enclosing dict is itself a provenance object, the dict was reached
            # under a *_provenance wrapper key, or it is the RiskScore record
            # itself. A bare top-level aggregate number is rejected.
            here_is_provenance = (
                in_provenance or shaped or _is_provenance_object(obj)
            )
            if here_is_provenance:
                for k, val in obj.items():
                    if (k in _RISKSCORE_AGGREGATE_FIELDS
                            and isinstance(val, (int, float))
                            and not isinstance(val, bool)):
                        _add(val)
            # Recurse, marking children of an explicit *_provenance wrapper key
            # (or of a provenance object) as in-provenance.
            for k, val in obj.items():
                child_in_provenance = (
                    here_is_provenance or k in _PROVENANCE_OBJECT_KEYS
                )
                _walk(val, child_in_provenance)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v, in_provenance)

    _walk(sidecar)
    return floats


def _risk_score_notes(sidecar: dict) -> list[str]:
    """Collect the non-empty `notes` strings off every RiskScore-shaped dict.

    The `risk_adjustment.py` scorer attaches a `notes` list to each RiskScore it
    emits — a V28 fallback-data warning, an "originally_disabled not modeled"
    note, a "no demographic coefficient for …" gap, a "scoring failed: …"
    record. The eval contract makes these BLOCKER-severity: a sidecar that
    carries a scorer warning must surface it in the deliverable, never present
    the RAF as clean. We pull `notes` only from dicts that are genuine scorer
    provenance (raf + companion fields), so a hand-typed `{"notes": [...]}` blob
    elsewhere in the sidecar is not mistaken for a scorer record. Returns the
    deduped, order-preserving list of note strings.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _walk(obj) -> None:
        if isinstance(obj, dict):
            if _is_riskscore_shaped(obj):
                notes = obj.get("notes")
                if isinstance(notes, str):
                    notes = [notes]
                if isinstance(notes, (list, tuple)):
                    for note in notes:
                        s = str(note).strip()
                        if s and s not in seen:
                            seen.add(s)
                            out.append(s)
            for val in obj.values():
                _walk(val)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(sidecar)
    return out


# An explicit "the scorer could not produce a figure, and here is why" caveat is
# an acceptable substitute for surfacing a specific scorer note verbatim: the
# eval contract says when the scorer cannot produce a figure the answer is
# "not-available-and-why", never an invented estimate. A rendered output that
# carries such a caveat has satisfied the disclosure intent even if it phrases
# the warning differently from the raw note string.
_NOT_AVAILABLE_CAVEAT = re.compile(
    r"\bnot[\s-]+available\b|\bn/?a\b|\bunavailable\b|\bcannot\s+(?:be\s+)?"
    r"(?:produce|comput|scor|determin)|\bcould\s+not\s+(?:be\s+)?"
    r"(?:produce|comput|scor|determin)|\bunable\s+to\s+(?:produce|comput|scor)",
    re.IGNORECASE,
)

# How far (characters) a not-available caveat may sit from a RAF-context cue and
# still count as qualifying the RAF claim. A caveat further away is treated as
# belonging to something else.
_CAVEAT_BIND_WINDOW = 160


def _has_raf_bound_caveat(rendered: str) -> bool:
    """True when a not-available caveat is bound to a RAF claim, not just present.

    The caveat must fall within ``_CAVEAT_BIND_WINDOW`` characters of a
    RAF-context cue (raf / risk score / O-E / normalization factor …) so an
    unrelated "n/a" elsewhere in the artifact cannot suppress the scorer-notes
    gate while the RAF figure still reads as clean (round-6 finding 2).
    """
    lowered = rendered.lower()
    cue_spans = [
        (m.start(), m.end())
        for cue in _RAF_CONTEXT_CUES
        for m in re.finditer(re.escape(cue), lowered)
    ]
    if not cue_spans:
        return False
    for cm in _NOT_AVAILABLE_CAVEAT.finditer(rendered):
        c_start, c_end = cm.start(), cm.end()
        for s_start, s_end in cue_spans:
            if c_start <= s_end + _CAVEAT_BIND_WINDOW and s_start <= c_end + _CAVEAT_BIND_WINDOW:
                return True
    return False


# A small integer is exempt from the RAF gate only when it is an explicit
# figure/exhibit/slide/list label or a structural index — i.e. it labels a thing,
# it is not itself a risk-adjustment result. Matched against the text immediately
# preceding the numeric token.
_FIGURE_LABEL_PREFIX = re.compile(
    r"(?:fig(?:ure)?|exhibit|slide|table|chart|appendix|hcc|step|item|panel|"
    r"section|line|row|col(?:umn)?|tier|phase|#|no\.?)\s*$",
    re.IGNORECASE,
)


def _is_figure_or_list_labeled(match: "re.Match[str]", text: str) -> bool:
    """True when a small-integer token is an explicit figure/list/index label.

    Looks at the ≤16 chars immediately before the token for a label word
    ("Fig", "Exhibit", "Slide", "HCC", "#", "no.", a markdown list bullet, …).
    Used so "Fig 1" / "HCC 18" / "3." stay exempt while a bare "RAF 2" does not.
    """
    start = match.start()
    prefix = text[max(0, start - 16):start]
    if _FIGURE_LABEL_PREFIX.search(prefix):
        return True
    # Markdown ordered-list item at line start: "1. ", "2) ".
    line_start = text.rfind("\n", 0, start) + 1
    before_on_line = text[line_start:start]
    if before_on_line.strip() == "" and text[match.end():match.end() + 2] in (". ", ") "):
        return True
    return False


def check_no_fabricated_raf_scores(rendered: str, sidecar: dict) -> EvalResult:
    """RAF / O-E / normalized-PMPM results must trace to a scorer run.

    The signature fabrication failure for `/risk-adjust` is a typed RAF table
    (member → 0.413, cohort mean → 1.04) with no `risk_adjustment.py` output
    behind it. This checker finds every number in the rendered prose that sits in
    a RAF/risk-score/normalized-PMPM/O-E context and requires it to match a value
    produced by the scorer and parked in the sidecar (a RiskScore-shaped object
    or a named aggregate like ``population_average_raf``). A RAF-context number
    with no scorer provenance — or with provenance that doesn't contain it — is a
    fabrication and fails the gate.

    Years (1900-2100) are always whitelisted ("PY2025"). Small integers 1-20 are
    whitelisted ONLY when they are explicitly figure/list-labeled ("Fig 1",
    "Slide 3", "HCC 18", "#2"); a small integer that sits in a RAF/risk-score
    context with no such label ("Cohort RAF 2", "O/E ratio is 1") is judged like
    any other RAF figure, so a fabricated small-integer RAF cannot hide behind the
    list-index exemption.
    """
    eid = "no-fabricated-raf-scores"
    cleaned = _HTML_COMMENT.sub("", rendered)
    cleaned = _TEMPLATE_NUMERIC_GUIDANCE.sub("", cleaned)
    cleaned = _REGULATORY_CITATIONS.sub("", cleaned)
    lowered = cleaned.lower()

    provenance = _risk_score_provenance_numbers(sidecar)

    fabricated: set[str] = set()
    for m in _NUMBER_PATTERN.finditer(cleaned):
        tok = m.group(0).replace(",", "").replace("$", "")
        f = _to_float(tok)
        if f is None:
            continue
        # Years are always exempt (e.g. "PY2025").
        if 1900 <= f <= 2100 and f == int(f):
            continue
        # The RAF-context test runs BEFORE the small-index whitelist: a small
        # integer adjacent to a RAF cue ("Cohort RAF 2", "O/E ratio is 1",
        # "coefficient 0.166") must be judged, not silently exempted. Scan a
        # window around the token for a cue word.
        start = max(0, m.start() - 40)
        end = min(len(lowered), m.end() + 40)
        window = lowered[start:end]
        in_raf_context = any(cue in window for cue in _RAF_CONTEXT_CUES)
        if not in_raf_context:
            continue
        # Small integers 1-20 are exempt ONLY when explicitly figure/list-labeled
        # (Fig 1, Slide 3, HCC 18, #2, etc.) — never merely because they are small
        # and happen to be near an RAF cue.
        if 1 <= f <= 20 and f == int(f) and _is_figure_or_list_labeled(m, cleaned):
            continue
        if (round(f, 6) in provenance
                or round(f, 4) in provenance
                or round(f, 3) in provenance
                or round(f, 2) in provenance):
            continue
        fabricated.add(tok)

    if fabricated:
        return EvalResult(
            eval_id=eid, skill_name="", status="fail",
            detail=(
                "RAF / O-E / normalized-PMPM figures with no risk_adjustment.py "
                f"scorer provenance in sidecar: {sorted(fabricated)[:8]}. Run the "
                "scorer and attach its RiskScore output (raf/component fields or "
                "population_average_raf) — never type RAF results."
            ),
        )

    # Notes-surfacing gate. The eval contract is BLOCKER-severity: a scorer
    # warning (V28 fallback-data, out-of-domain member, unmodeled medicaid-dual
    # segment, "scoring failed: …") must reach the deliverable, or the RAF is
    # presented as clean when it is not. Each note must appear verbatim in the
    # rendered output, OR the output must carry an explicit not-available-and-why
    # caveat that stands in for the omitted warning. `cleaned` is the rendered
    # text minus HTML comments / template guidance / regulatory citations; we
    # match notes against the original `rendered` so a note that legitimately
    # lives in a footnote/comment still counts — the obligation is "the human
    # sees it", not "it is in body prose".
    notes = _risk_score_notes(sidecar)
    if notes:
        # The caveat substitute must be bound to the RAF claim it qualifies — an
        # unrelated "n/a" elsewhere in the artifact (a missing benchmark, a blank
        # table cell) must NOT suppress a scorer warning while the RAF still reads
        # as clean (round-6 finding 2). Only a not-available caveat located within
        # the RAF context counts.
        has_caveat = _has_raf_bound_caveat(rendered)
        unsurfaced = [n for n in notes if n not in rendered]
        if unsurfaced and not has_caveat:
            return EvalResult(
                eval_id=eid, skill_name="", status="fail",
                detail=(
                    "scorer RiskScore notes not surfaced in rendered output: "
                    f"{unsurfaced[:3]}. A sidecar carrying a scorer warning "
                    "(V28 fallback-data, out-of-domain member, unmodeled segment) "
                    "must surface each note verbatim, or state not-available-and-why "
                    "adjacent to the RAF figure — never present the RAF as clean."
                ),
            )

    return EvalResult(
        eval_id=eid, skill_name="", status="pass",
        detail="every RAF/risk-score figure traces to scorer provenance in sidecar",
    )


def check_code_set_stamp(rendered: str, sidecar: dict) -> EvalResult:
    has_pinning = "Code-set pinning" in rendered or "code-set pinning" in rendered.lower()
    has_icd = "ICD-10-CM" in rendered or "icd-10-cm" in rendered.lower()
    if has_pinning and has_icd:
        return EvalResult(eval_id="code-set-stamp-present", skill_name="",
                          status="pass", detail="ICD-10-CM stamp present")
    return EvalResult(eval_id="code-set-stamp-present", skill_name="",
                      status="fail",
                      detail="missing 'Code-set pinning' footer or ICD-10-CM stamp")


def check_render_split(rendered: str, sidecar: dict) -> EvalResult:
    has_marker = "[AGENT:" in rendered
    if has_marker:
        return EvalResult(eval_id="render-split-discipline", skill_name="",
                          status="pass", detail="[AGENT:] marker present")
    # If sidecar declares no missing prose, the absence of markers is correct
    return EvalResult(eval_id="render-split-discipline", skill_name="",
                      status="pass",
                      detail="no [AGENT:] markers — caller filled all prose sections")


def check_small_cell_suppression(rendered: str, sidecar: dict) -> EvalResult:
    """Reject numeric counts in [1, 10] inline as standalone numbers."""
    bad_counts = []
    for m in re.finditer(r"\bn=(\d+)\b", rendered, re.IGNORECASE):
        n = int(m.group(1))
        if 1 <= n <= 10:
            bad_counts.append(n)
    if bad_counts:
        return EvalResult(eval_id="small-cell-suppression-applied", skill_name="",
                          status="fail",
                          detail=f"unsuppressed small cells: n={bad_counts[:5]}")
    return EvalResult(eval_id="small-cell-suppression-applied", skill_name="",
                      status="pass", detail="no n in [1,10] cells in rendered output")


def check_age_90_plus(rendered: str, sidecar: dict) -> EvalResult:
    """Catch literal ages 90-130 displayed without aggregation.

    Allow "90+" (the aggregated band) by requiring the digit not be followed
    by '+'.
    """
    bad = []
    for m in re.finditer(r"\bage[:\s]+(\d{2,3})(?!\+)\b", rendered, re.IGNORECASE):
        n = int(m.group(1))
        if 90 <= n <= 130:
            bad.append(n)
    if bad:
        return EvalResult(eval_id="age-90-plus-aggregated", skill_name="",
                          status="fail", detail=f"un-aggregated ages: {bad[:5]}")
    return EvalResult(eval_id="age-90-plus-aggregated", skill_name="",
                      status="pass", detail="no individual ages 90+ in output")


# ---------------------------------------------------------------------------
# v1.1 Phase C.1 — five new structural checkers.
# Each maps to an eval_id already declared by an Outputs-layer skill.
# ---------------------------------------------------------------------------


_EXHIBIT_REF = re.compile(r"\bExhibit\s+(\d+)\b")
_EXHIBIT_HEADER = re.compile(r"^#+\s+Exhibit\s+(\d+)\b", re.MULTILINE)


def check_numbered_exhibits(rendered: str, sidecar: dict) -> EvalResult:
    """Milliman white papers: every Exhibit N reference must have a header.

    Also: exhibit headers must be sequential starting at 1 (no gaps).
    """
    refs = [int(m.group(1)) for m in _EXHIBIT_REF.finditer(rendered)]
    headers = [int(m.group(1)) for m in _EXHIBIT_HEADER.finditer(rendered)]

    if not refs and not headers:
        return EvalResult(
            eval_id="numbered-exhibits", skill_name="",
            status="pass",
            detail="no exhibits in rendered output (vacuous pass)",
        )
    if not headers:
        return EvalResult(
            eval_id="numbered-exhibits", skill_name="",
            status="fail",
            detail=f"prose references Exhibit {sorted(set(refs))} but no Exhibit headers found",
        )

    missing = sorted(set(refs) - set(headers))
    if missing:
        return EvalResult(
            eval_id="numbered-exhibits", skill_name="",
            status="fail",
            detail=f"prose references Exhibit {missing} which has no header",
        )

    seq = sorted(set(headers))
    expected = list(range(1, max(seq) + 1))
    if seq != expected:
        return EvalResult(
            eval_id="numbered-exhibits", skill_name="",
            status="fail",
            detail=f"non-sequential exhibits: {seq} (expected {expected})",
        )
    return EvalResult(
        eval_id="numbered-exhibits", skill_name="",
        status="pass",
        detail=f"{len(seq)} exhibits, sequential 1..{max(seq)}, all referenced exhibits have headers",
    )


_KFF_REQUIRED_HEADINGS = (
    ("Lead", re.compile(r"^#+\s+Lead\b", re.MULTILINE | re.IGNORECASE)),
    ("Key findings", re.compile(r"^#+\s+Key\s+findings\b", re.MULTILINE | re.IGNORECASE)),
    ("Body", re.compile(r"^#+\s+Body\b|^#+\s+Q&A\b", re.MULTILINE | re.IGNORECASE)),
    ("Methodology", re.compile(r"^#+\s+Methodology\b|^#+\s+Methods\b", re.MULTILINE | re.IGNORECASE)),
    ("Disclosure", re.compile(r"^#+\s+Disclosure\b", re.MULTILINE | re.IGNORECASE)),
)


def check_kff_sections_present(rendered: str, sidecar: dict) -> EvalResult:
    """KFF brief must have Lead, Key findings, Body Q&A, Methodology, Disclosure."""
    missing = [name for name, pattern in _KFF_REQUIRED_HEADINGS if not pattern.search(rendered)]
    if missing:
        return EvalResult(
            eval_id="structural-sections-present", skill_name="",
            status="fail",
            detail=f"missing required KFF sections: {missing}",
        )
    return EvalResult(
        eval_id="structural-sections-present", skill_name="",
        status="pass",
        detail="all 5 KFF sections present",
    )


_SLIDE_TITLE = re.compile(r"^#+\s+Slide\s+\d+:\s+(.+?)\s*$", re.MULTILINE)


def _violates_action_title(title: str) -> bool:
    """Action-title rule: ≥5 words OR contains at least one digit."""
    words = title.split()
    has_digit = any(any(c.isdigit() for c in w) for w in words)
    return len(words) < 5 and not has_digit


def check_action_title_rule(rendered: str, sidecar: dict) -> EvalResult:
    """Every slide title in the rendered output must satisfy the action-title rule."""
    failures: list[str] = []
    matches = list(_SLIDE_TITLE.finditer(rendered))
    if not matches:
        return EvalResult(
            eval_id="action-title-rule-slides", skill_name="",
            status="pass",
            detail="no slide titles in rendered output (vacuous pass)",
        )
    for m in matches:
        title = m.group(1).strip()
        if _violates_action_title(title):
            failures.append(title)
    if failures:
        return EvalResult(
            eval_id="action-title-rule-slides", skill_name="",
            status="fail",
            detail=f"{len(failures)} slide titles violate action-title rule: {failures[:3]}",
        )
    return EvalResult(
        eval_id="action-title-rule-slides", skill_name="",
        status="pass",
        detail=f"{len(matches)} slide titles all satisfy action-title rule (≥5 words OR has number)",
    )


_DENOMINATOR_PATTERNS = (
    re.compile(r"\bn\s*=\s*\d", re.IGNORECASE),
    re.compile(r"\bout\s+of\s+\d", re.IGNORECASE),
    re.compile(r"\bcohort_n\b", re.IGNORECASE),
    re.compile(r"\bdenominator\b", re.IGNORECASE),
    re.compile(r"\bcohort\s+(?:of\s+)?\d", re.IGNORECASE),
    re.compile(r"\bsample\s+size\b", re.IGNORECASE),
    re.compile(r"\bN\s*=\s*\d"),
    re.compile(r"\bmembers?\s*[:=]\s*\d", re.IGNORECASE),
)


def check_denominator_explicit(rendered: str, sidecar: dict) -> EvalResult:
    """Member-journey + general denominator-explicit rule.

    The rendered output must declare a cohort/denominator anchor in plain
    text (n=N, out of N, cohort_n, denominator, sample size, etc.).
    """
    for pattern in _DENOMINATOR_PATTERNS:
        if pattern.search(rendered):
            return EvalResult(
                eval_id="denominator-explicit", skill_name="",
                status="pass",
                detail=f"denominator marker found via {pattern.pattern!r}",
            )
    return EvalResult(
        eval_id="denominator-explicit", skill_name="",
        status="fail",
        detail=("no denominator marker found in rendered output. "
                "Expected one of: n=N, out of N, cohort_n, denominator, "
                "sample size, members: N"),
    )


_CONTRACT_KEYWORDS = (
    ("contract type", re.compile(r"\bcontract[_\s-]?type\b", re.IGNORECASE)),
    ("benchmark method", re.compile(
        r"\bbenchmark[_\s-]?method(?:ology)?\b", re.IGNORECASE)),
    ("attribution method", re.compile(
        r"\battribution[_\s-]?method(?:ology)?\b|\battribution\b", re.IGNORECASE)),
    ("quality gate", re.compile(r"\bquality[_\s-]?gate\b", re.IGNORECASE)),
    ("risk corridor", re.compile(r"\brisk[_\s-]?corridor\b", re.IGNORECASE)),
    ("minimum savings rate", re.compile(
        r"\bminimum[_\s-]?savings[_\s-]?rate\b|\bMSR\b", re.IGNORECASE)),
)


def check_contract_mechanics_pinned(rendered: str, sidecar: dict) -> EvalResult:
    """VBC scorecard must declare contract_type, benchmark_method, attribution_method.

    Plus the v1.1 broadened set: quality_gate, risk_corridor, minimum_savings_rate.
    Pass requires all 3 core mechanics; the 3 broader ones are reported as
    "extras present" in the detail but not required for pass (some VBC
    contracts genuinely lack a corridor or MSR).
    """
    core = ["contract type", "benchmark method", "attribution method"]
    optional = ["quality gate", "risk corridor", "minimum savings rate"]
    found_core = [name for name, p in _CONTRACT_KEYWORDS if name in core and p.search(rendered)]
    found_optional = [name for name, p in _CONTRACT_KEYWORDS if name in optional and p.search(rendered)]
    missing_core = [name for name in core if name not in found_core]
    if missing_core:
        return EvalResult(
            eval_id="contract-mechanics-pinned", skill_name="",
            status="fail",
            detail=f"missing core VBC mechanics: {missing_core}",
        )
    return EvalResult(
        eval_id="contract-mechanics-pinned", skill_name="",
        status="pass",
        detail=(f"all 3 core mechanics present; {len(found_optional)}/3 optional "
                f"(quality_gate / risk_corridor / MSR) also present: {found_optional}"),
    )


# ---------------------------------------------------------------------------
# v1.4 capability-matrix — three privacy-guardrail checkers.
# privacy-output-guardrails/evals.json declared these ids since v1.0 but they
# had no checker, so the strict capability gate would have reported them as
# not_runnable (a silent pass). These close that hole.
# ---------------------------------------------------------------------------

# 42 CFR Part 2 warning text comes from medecon_verify.privacy; the checker
# only needs a stable substring that proves the banner is present.
_PART_2_BANNER_MARK = "42 CFR Part 2"
_SCAN_FAILED_SENTINEL = "[SCAN_FAILED—CONTENT_SUPPRESSED]"


# Safe Harbor identifiers that must never survive into a rendered deliverable,
# keyed by phi_scanner.scan_markdown() metric name:
#   ssn, mbi, phone, email, ip   — unambiguous structured identifiers
#   url                          — Safe Harbor #14; a URL in a deliverable is far
#                                  likelier a leaked member/claim link than a
#                                  public citation (no renderer emits URLs)
#   date_granular                — Safe Harbor #3; a YYYY-MM-DD in output is a
#                                  likely DOB/service-date leak (renderers emit
#                                  no granular dates — as-of dates render coarser)
#   freetext_name                — Safe Harbor #1; "Patient John Smith" style
#                                  names. The scanner heuristic is context-aware
#                                  (decision-maker names like "J. Smith" do NOT
#                                  trip it); provider names ("Dr. Jane Doe") do —
#                                  allow-list those via phi_scanner if/when the
#                                  provider-profile renderer is wired.
# All eight verified to produce zero hits across every currently-wired renderer,
# so enabling them costs no false positives today while closing the gap where an
# explicit name or DOB in rendered output passed the guardrail.
_HARD_IDENTIFIER_METRICS = (
    "ssn", "mbi", "phone", "email", "ip", "url", "date_granular", "freetext_name",
)


def check_safe_harbor_18_clean(rendered: str, sidecar: dict) -> EvalResult:
    """No hard Safe Harbor identifier may survive into rendered output.

    Reuses phi.scan_markdown (line-by-line, no overflow gate — the
    correct entry point for renderer output) and fails only on unambiguous direct
    identifiers (SSN, MBI, phone, email, IP).
    """
    try:
        from .. import phi as phi_scanner  # lazy: never break the runner on import
    except Exception as exc:  # pragma: no cover - defensive
        return EvalResult(
            eval_id="safe-harbor-18-clean", skill_name="", status="not_runnable",
            detail=f"phi_scanner unavailable: {exc}",
        )
    try:
        phi_scanner.reset_metrics()
        phi_scanner.scan_markdown(rendered)
        metrics = phi_scanner.scanner_metrics()
    except Exception as exc:
        # Fail-closed: a scanner crash during the eval is a failure, not a pass.
        return EvalResult(
            eval_id="safe-harbor-18-clean", skill_name="", status="fail",
            detail=f"phi_scanner crashed scanning rendered output: {exc}",
        )
    hits = {k: metrics[k] for k in _HARD_IDENTIFIER_METRICS if metrics.get(k)}
    if hits:
        return EvalResult(
            eval_id="safe-harbor-18-clean", skill_name="", status="fail",
            detail=f"Safe Harbor identifier(s) in rendered output: {hits}",
        )
    return EvalResult(
        eval_id="safe-harbor-18-clean", skill_name="", status="pass",
        detail="no Safe Harbor identifiers (SSN/MBI/phone/email/IP/URL/date/name) in output",
    )


def check_part_2_redisclosure_flag(rendered: str, sidecar: dict) -> EvalResult:
    """If 42 CFR Part 2 SUD content is present, the warning banner must be too.

    Reuses the conservative keyword list from medecon_verify.privacy. No SUD
    content → vacuous pass.
    """
    try:
        from ..privacy import _PART_2_KEYWORDS  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive
        return EvalResult(
            eval_id="part-2-redisclosure-flag", skill_name="", status="not_runnable",
            detail=f"privacy_guardrails unavailable: {exc}",
        )
    low = rendered.lower()
    matched = [k for k in _PART_2_KEYWORDS if k in low]
    if not matched:
        return EvalResult(
            eval_id="part-2-redisclosure-flag", skill_name="", status="pass",
            detail="no 42 CFR Part 2 SUD content detected (vacuous pass)",
        )
    if _PART_2_BANNER_MARK in rendered:
        return EvalResult(
            eval_id="part-2-redisclosure-flag", skill_name="", status="pass",
            detail=f"SUD content ({matched[:3]}) carries the Part 2 warning banner",
        )
    return EvalResult(
        eval_id="part-2-redisclosure-flag", skill_name="", status="fail",
        detail=f"SUD content present ({matched[:3]}) but no '{_PART_2_BANNER_MARK}' banner",
    )


def check_scanner_crash_defaults_to_redact(rendered: str, sidecar: dict) -> EvalResult:
    """A signalled scan failure must surface the redaction sentinel, never pass through.

    CANONICAL shape (the one real producers emit): phi_scanner.scan(records)
    increments a `scan_failed` count on a crashed chunk; a pipeline stamps
    scanner_metrics() into the sidecar under privacy_audit.phi_scan_metrics
    (matching privacy_guardrails.apply()). So privacy_audit.phi_scan_metrics
    .scan_failed > 0 is the shape that matters in production — pinned by
    test_scanner_crash_contract_matches_real_phi_scanner_shape.

    DEFENSIVE tolerances (no current producer, but cheap to accept so an
    alternate assembly can't silently bypass the gate): flat `scan_failed` /
    `phi_scan_failed` / positive `scan_errors` / non-empty `failed_chunks`, at
    either the sidecar root OR inside privacy_audit. Both scopes are checked so a
    mixed sidecar can't drop a flat flag.

    If a failure is signalled in any of those, the rendered output MUST contain
    the sentinel. No signalled failure → vacuous pass.
    """
    # A crash signal can live at the sidecar root (flat shape) OR inside
    # privacy_audit (canonical shape, where privacy_guardrails.apply() stores
    # phi_scanner.scanner_metrics() under phi_scan_metrics). Check BOTH scopes —
    # a mixed sidecar ({"scan_failed": True, "privacy_audit": {}}) must not let
    # the flat flag get dropped just because a privacy_audit object exists.
    root = sidecar if isinstance(sidecar, dict) else {}
    audit = root.get("privacy_audit")
    if not isinstance(audit, dict):
        audit = {}

    def _positive(d: dict, key: str) -> bool:
        v = d.get(key)
        return isinstance(v, (int, float)) and v > 0

    def _signals_failure(d: dict) -> bool:
        if not isinstance(d, dict):
            return False
        metrics = d.get("phi_scan_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        return bool(
            d.get("scan_failed")
            or d.get("phi_scan_failed")
            or _positive(d, "scan_errors")
            or d.get("failed_chunks")
            or _positive(metrics, "scan_failed")
            or _positive(metrics, "scan_errors")
        )

    failure_signalled = _signals_failure(root) or _signals_failure(audit)
    if not failure_signalled:
        return EvalResult(
            eval_id="scanner-crash-defaults-to-redact", skill_name="", status="pass",
            detail="no scan failure signalled in sidecar (vacuous pass)",
        )
    if _SCAN_FAILED_SENTINEL in rendered:
        return EvalResult(
            eval_id="scanner-crash-defaults-to-redact", skill_name="", status="pass",
            detail="scan failure signalled and redaction sentinel present",
        )
    return EvalResult(
        eval_id="scanner-crash-defaults-to-redact", skill_name="", status="fail",
        detail="scan failure signalled but redaction sentinel absent — content passed through unscanned",
    )


ORACLE_EVAL_ID = "expected-value-match"

_MISSING = object()


def _walk_path(obj, path: str):
    """Walk a dotted path with list-index support: 'findings.0.number'."""
    cur = obj
    for part in path.split("."):
        try:
            if isinstance(cur, list):
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                if part not in cur:
                    return _MISSING
                cur = cur[part]
            else:
                return _MISSING
        except (ValueError, IndexError, KeyError, TypeError):
            return _MISSING
    return cur


def check_expected_value_match(rendered: str, sidecar: dict, extra: dict) -> EvalResult:
    """Ground-truth oracle: assert sidecar values equal known-correct answers.

    The first checker that verifies a number is *correct*, not merely present.
    Anti-fabrication proves a rendered number traces to the sidecar; this proves
    the sidecar number itself matches a blessed answer, anchored to a snapshot.

    Eval entry carries:
        "oracle": [{"path": "findings.0.number", "expected": 1234, "tol": 0.01}]
    `tol` is optional (absolute tolerance for floats; default exact). Non-numeric
    expected values compare by equality.
    """
    eid = ORACLE_EVAL_ID
    oracle = extra.get("oracle")
    if not oracle:
        return EvalResult(
            eval_id=eid, skill_name="", status="not_runnable",
            detail="eval declares no `oracle` block; nothing to verify",
        )
    if isinstance(oracle, dict):
        oracle = [oracle]

    mismatches: list[str] = []
    for item in oracle:
        path = item.get("path", "")
        expected = item.get("expected")
        actual = _walk_path(sidecar, path)
        if actual is _MISSING:
            mismatches.append(f"{path}: missing from sidecar")
            continue
        tol = item.get("tol")
        # Only compare numerically when BOTH sides are genuine numbers, or the
        # eval explicitly opts in with a tolerance. Otherwise compare strictly —
        # so a string code like "0042" does not float-match "42", and a string
        # "5%" is not silently divided to 0.05.
        def _num(x) -> bool:
            return isinstance(x, (int, float)) and not isinstance(x, bool)
        if _num(expected) and _num(actual) or tol is not None:
            ef, af = _to_float(str(expected)), _to_float(str(actual))
            if ef is None or af is None:
                mismatches.append(f"{path}: cannot numerically compare {expected!r} vs {actual!r}")
            elif abs(ef - af) > (tol or 0.0):
                mismatches.append(f"{path}: expected {expected}, got {actual} (tol {tol or 0})")
        elif actual != expected:
            mismatches.append(f"{path}: expected {expected!r}, got {actual!r}")

    if mismatches:
        return EvalResult(
            eval_id=eid, skill_name="", status="fail",
            detail="oracle mismatch — " + "; ".join(mismatches[:5]),
        )
    return EvalResult(
        eval_id=eid, skill_name="", status="pass",
        detail=f"all {len(oracle)} oracle value(s) match the blessed answer",
    )


def check_adversarial_review_present(rendered: str, sidecar: dict) -> EvalResult:
    """Leadership-bound analyses must carry a completed adversarial review.

    The renderer hard-fails without one, so by the time we have rendered output
    the block exists; this checker confirms it surfaced in the artifact (not
    skipped) when the sidecar marks the analysis leadership-bound.
    """
    eid = "adversarial-review-present-for-leadership"
    scope = sidecar.get("scope") if isinstance(sidecar, dict) else {}
    leadership = bool(sidecar.get("leadership_bound")) or (
        isinstance(scope, dict) and scope.get("audience") == "leadership"
    )
    if not leadership:
        return EvalResult(
            eval_id=eid, skill_name="", status="pass",
            detail="not leadership-bound — adversarial review optional (vacuous pass)",
        )
    has_review = bool(sidecar.get("adversarial_review")) and "## Adversarial review" in rendered
    skipped = "_Skipped —" in rendered
    if has_review and not skipped:
        return EvalResult(
            eval_id=eid, skill_name="", status="pass",
            detail="leadership-bound analysis carries a completed adversarial review",
        )
    return EvalResult(
        eval_id=eid, skill_name="", status="fail",
        detail="leadership-bound analysis is missing a completed adversarial review",
    )


_DRIVER_WORDS = ("driver", "driven by", "caused by", "cause of", "explains",
                 "attributable to", "because", "due to", "root cause")

# A STRICTER set used only by the orphan/sibling-section scan (failure mode 1b).
# `_DRIVER_WORDS` includes bare connectives ("because", "due to", "explains")
# that pervade legitimate Caveats/Limitations/Notes prose ("figures may shift
# because the warehouse refresh was delayed"). Bare connectives caused the
# orphan scan to OVER-FIRE on clean descriptive-composition artifacts that make
# NO causal/driver claim. The orphan scan therefore requires a phrase that
# actually ASSERTS a driver/cause of a change — not a bare connective. The
# broader `_DRIVER_WORDS` is kept for failure mode 1 (where the finding's OWN
# method is composition and its OWN bound text claims a driver); only the
# section-wide orphan scan uses this strict set.
_STRONG_DRIVER_PHRASES = (
    "driven by", "driver of", "drivers of", "the driver",
    "attributable to", "caused by", "the cause of", "cause of",
    "root cause", "root-cause", "the reason", "reason is", "reason for",
    "explained by",
)

_COMPOSITION_METHODS =("composition", "groupby", "group-by", "group by",
                        "breakdown", "mix", "share", "distribution", "cross-tab",
                        "crosstab", "pivot", "count by", "top-n", "top n")
_DECOMPOSITION_METHODS = ("decomposition", "decompose", "waterfall",
                          "trend decomposition", "regression", "causal",
                          "counterfactual", "fixed-effect", "fixed effect",
                          "difference-in-difference", "diff-in-diff",
                          "shapley", "variance decomposition", "attribution model")

# Stopwords stripped before token-matching a finding's branch claim against a
# hypothesis-tree branch label. These are the connective words that make a
# 1-token substring like "a" or "data" spuriously "match" a JSON blob.
#
# IMPORTANT: this list deliberately does NOT contain branch-distinguishing
# content words like "drift", "gap", or "lag". Those are exactly what tells
# "MLR drift" apart from "RAF lag"; stripping them let answers="RAF drift" map
# to the "RAF lag" branch via the single shared token "raf". Only true
# connectives and the scaffolding nouns of a hypothesis JSON blob
# ("data"/"source"/"test"/"hypothesis"/"branch") belong here.
_BRANCH_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "by",
    "with", "vs", "is", "was", "rose", "fell", "up", "down",
    "data", "source", "test", "hypothesis", "branch",
})


def _branch_tokens(label) -> set[str]:
    """Significant word tokens of a branch/claim label, lowercased.

    Used to match a finding's `answers`/`hypothesis`/`branch` against the
    actual hypothesis-tree branch identifiers by *token overlap*, not by a
    substring search over a JSON blob (which let junk like answers="a" or
    answers="data_source" pass). Stopwords and 1-2 char tokens are dropped so
    only content words count.
    """
    text = str(label).lower()
    toks = re.findall(r"[a-z0-9]+", text)
    return {t for t in toks if len(t) >= 3 and t not in _BRANCH_STOPWORDS}


def _stem(tok: str) -> str:
    """Cheap singular/plural normalizer so 'admissions' matches 'admission'.

    Deliberately conservative: strips a trailing 'ies'->'y', 'ses'->'s', or a
    bare plural 's' (keeping 'ss' words like 'access' intact). Not a real
    stemmer — just enough that a branch labelled 'admissions' and a finding
    that says 'admission' map to each other instead of reading as scope drift.
    """
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 4 and tok.endswith("ses"):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _stemmed_tokens(label) -> set[str]:
    """`_branch_tokens` with each token singular-normalized for matching."""
    return {_stem(t) for t in _branch_tokens(label)}


def _split_rendered_blocks(rendered: str) -> list[str]:
    """Split a rendered artifact into section blocks for per-finding binding.

    A block is a markdown header and the body up to the next header. When there
    are no headers, the whole artifact is a single block. Used so a driver-word
    scan can be bound to the *one* section a finding owns, instead of poisoning
    every finding because the word "driven by" appears somewhere in the artifact.
    """
    if not rendered:
        return []
    parts: list[str] = []
    buf: list[str] = []
    for line in rendered.splitlines():
        if re.match(r"^#+\s", line) and buf:
            parts.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        parts.append("\n".join(buf))
    return parts


def _rendered_text_for_finding(finding_tokens: set[str], blocks: list[str]) -> str:
    """Return the rendered block that best matches a finding's content words.

    Binds a finding to the section that actually describes it (highest
    content-word overlap), so a driver claim in *another* finding's section is
    not attributed to this one. Returns "" when no block shares any content word
    with the finding — an unbound finding is judged on its sidecar text alone,
    never on global artifact text.
    """
    if not finding_tokens or not blocks:
        return ""
    best_text = ""
    best_overlap = 0
    for block in blocks:
        overlap = len(finding_tokens & _branch_tokens(block))
        if overlap > best_overlap:
            best_overlap = overlap
            best_text = block
    return best_text


def _best_finding_index_for_block(block: str, finding_token_sets: list[set[str]]) -> int | None:
    """Return the index of the finding whose content best matches a block.

    The inverse binding of `_rendered_text_for_finding`: given a rendered
    section, which finding (if any) owns it? Used to decide whether a driver
    claim that appears in a section is *explained* by a real decomposition
    finding, or is an orphan/laundered claim that belongs to no decomposition.
    Returns None when the block shares no content word with any finding.
    """
    best_idx: int | None = None
    best_overlap = 0
    block_tokens = _branch_tokens(block)
    for idx, ftoks in enumerate(finding_token_sets):
        overlap = len(block_tokens & ftoks)
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = idx
    return best_idx


def _hypothesis_branches(*sources) -> list[str]:
    """Normalize hypothesis-tree branch labels from any of the real shapes.

    The canonical /scope payload carries `hypotheses` as a list of dicts
    ({"hypothesis": str, "disconfirming_test": str, "data_source": str}); other
    producers carry a flat list of label strings, or a dict keyed by branch
    name. Accept all of them, from whichever location they really live in, and
    return the branch *labels* (the `hypothesis` text for dict entries).
    """
    branches: list[str] = []
    for tree in sources:
        if not tree:
            continue
        if isinstance(tree, dict):
            tree = list(tree.keys())
        if not isinstance(tree, list):
            tree = [tree]
        for item in tree:
            if isinstance(item, dict):
                label = item.get("hypothesis") or item.get("branch") or item.get("name")
                if label:
                    branches.append(str(label))
            elif item:
                branches.append(str(item))
    return branches


def check_analysis_answers_scope(rendered: str, sidecar: dict) -> EvalResult:
    """Does the analytical DESIGN answer the scoped question? (Scope alignment.)

    The method-vs-question gate. Two failure modes a denominator/code-set pass
    does not catch:

    1. *Composition presented as driver.* A finding asserts a cause ("ED visits
       rose, driven by the ESRD cohort") but the method behind it is a
       composition/groupby cut — a share-of-total, not a decomposition. A cut
       tells you *where* volume sits, never *why* it changed. Claiming a driver
       from a composition is the single most common altitude error in an
       analyst's draft. The driver claim is read from BOTH the sidecar finding
       text AND the rendered artifact (title/TLDR/finding line) — a neutral
       sidecar finding cannot launder a "driven by" claim that ships in prose.
    2. *Findings off the hypothesis tree.* When /scope declared a hypothesis
       tree, every finding should map to one of its branches; a finding with no
       home is scope drift (or an unscoped fishing result). The tree is read
       from wherever it really lives — `sidecar["hypotheses"]` (the canonical
       /scope payload shape), `sidecar["scope"]["hypotheses"]` /
       `["hypothesis_tree"]` (the carried-scope shape) — and branches are
       matched by content-word overlap, not a substring-in-JSON test (so
       answers="a" or answers="data_source" no longer pass spuriously).

    Each finding may carry `method` / `analysis_type` and `answers` /
    `hypothesis` (the branch it addresses). When neither structure is present we
    don't enforce — a vacuous pass, consistent with the other checkers, so an
    exec summary that legitimately omits method metadata is not penalised.
    """
    eid = "analysis-design-answers-scope"
    if not isinstance(sidecar, dict):
        return EvalResult(eid, "", "pass", "no sidecar to enforce against (vacuous pass)")
    scope = sidecar.get("scope") if isinstance(sidecar.get("scope"), dict) else {}
    findings = sidecar.get("findings")
    if not isinstance(findings, list) or not findings:
        return EvalResult(eid, "", "pass", "no findings to check (vacuous pass)")

    # The hypothesis tree, read from every location it actually lives in: the
    # canonical /scope payload puts `hypotheses` at top level (list of dicts);
    # a carried-scope /analyze sidecar may nest it under `scope`. Both the
    # legacy `hypothesis_tree` key and the canonical `hypotheses` key are read.
    branches = _hypothesis_branches(
        sidecar.get("hypotheses"),
        sidecar.get("hypothesis_tree"),
        scope.get("hypotheses"),
        scope.get("hypothesis_tree"),
    )
    # Stem branch tokens (singular/plural) so a branch "admissions" matches a
    # finding that says "admission" and is not mis-read as scope drift.
    branch_token_sets = [_stemmed_tokens(b) for b in branches]
    branch_token_sets = [s for s in branch_token_sets if s]

    have_method = any(
        isinstance(f, dict) and (f.get("method") or f.get("analysis_type"))
        for f in findings
    )
    if not have_method and not branches:
        return EvalResult(
            eid, "", "pass",
            "findings carry no method/analysis_type and /scope declared no "
            "hypothesis tree — nothing to align against (vacuous pass)",
        )

    # Split the rendered artifact into section blocks once, so each finding's
    # driver-claim scan can be bound to the section it owns rather than the whole
    # artifact. A global scan poisons every composition finding the moment the
    # word "driven by" appears anywhere (e.g. in a legitimate, separate
    # decomposition finding's section).
    rendered_blocks = _split_rendered_blocks(rendered or "")

    # Per-finding metadata, indexed parallel to `findings`, used both by the
    # per-finding loop and by the orphan-driver scan below.
    finding_token_sets: list[set[str]] = []
    finding_is_decomposition: list[bool] = []
    finding_is_composition: list[bool] = []
    for f in findings:
        if not isinstance(f, dict):
            finding_token_sets.append(set())
            finding_is_decomposition.append(False)
            finding_is_composition.append(False)
            continue
        ftext = str(f.get("finding", "")).lower()
        fmethod = str(f.get("method") or f.get("analysis_type") or "").lower()
        finding_token_sets.append(_branch_tokens(ftext))
        finding_is_decomposition.append(
            any(m in fmethod for m in _DECOMPOSITION_METHODS)
        )
        finding_is_composition.append(
            any(m in fmethod for m in _COMPOSITION_METHODS)
            and not any(m in fmethod for m in _DECOMPOSITION_METHODS)
        )

    problems: list[str] = []
    for i, f in enumerate(findings, 1):
        if not isinstance(f, dict):
            continue
        finding_text = str(f.get("finding", "")).lower()
        method = str(f.get("method") or f.get("analysis_type") or "").lower()

        # Failure mode 1: a driver claim sourced from a composition method. The
        # driver claim may live in the sidecar finding text OR in the shipped
        # artifact — but only the artifact section that BELONGS to this finding,
        # bound by content-word overlap. A "driven by" headline in some other
        # finding's section no longer leaks onto this one (the global-scan
        # poison bug); a neutral sidecar still cannot launder a driver claim
        # that ships in this finding's own section.
        bound_rendered = _rendered_text_for_finding(
            _branch_tokens(finding_text), rendered_blocks
        ).lower()
        claims_driver = (
            any(w in finding_text for w in _DRIVER_WORDS)
            or any(w in bound_rendered for w in _DRIVER_WORDS)
        )
        is_composition = any(m in method for m in _COMPOSITION_METHODS)
        is_decomposition = any(m in method for m in _DECOMPOSITION_METHODS)
        if claims_driver and is_composition and not is_decomposition:
            problems.append(
                f"finding {i} claims a driver/cause but its method is "
                f"'{method}' — a composition/groupby cut, not a decomposition"
            )

        # Failure mode 2: a finding that maps to no hypothesis-tree branch.
        # Match by *coverage of a branch's distinguishing content*, not a single
        # shared token. A claim maps to a branch only when it carries every one
        # of that branch's key terms (the branch's token set is a subset of the
        # claim's token set). A single shared token is not enough: with branches
        # "MLR drift" {mlr, drift} and "RAF lag" {raf, lag}, answers="RAF drift"
        # {raf, drift} covers neither branch in full, so it correctly maps to no
        # declared branch. (Distinguishing words like drift/lag/gap are no longer
        # stripped, so they count toward coverage.)
        if branch_token_sets:
            branch = f.get("answers") or f.get("hypothesis") or f.get("branch")
            # The branch a finding addresses comes from an explicit field when
            # present; otherwise fall back to the finding's own text. A finding
            # that PLAINLY names a branch in its `finding` text (but omits the
            # `answers` field) should not be auto-flagged as drift. Tokens are
            # stemmed so singular/plural ("admission"/"admissions") still cover.
            claim_tokens = _stemmed_tokens(branch) if branch else set()
            claim_tokens |= _stemmed_tokens(finding_text)
            mapped = bool(claim_tokens) and any(
                bt and bt <= claim_tokens for bt in branch_token_sets
            )
            if not mapped:
                problems.append(
                    f"finding {i} maps to no /scope hypothesis branch "
                    f"(answers={branch!r}; finding={f.get('finding')!r}; "
                    f"branches={branches!r})"
                )

    # Failure mode 1b: a LAUNDERED driver claim in a sibling section. Per-finding
    # binding (above) only scans the section that best-matches each finding's
    # content words. That over-corrected the round-2 global scan: a "driven by"
    # claim placed in a section whose header lexically matches NO finding (and so
    # belongs to no real decomposition) escapes detection entirely — the laundered
    # driver claim ships unflagged. Close that hole: scan EVERY rendered section
    # for a driver word, find the finding it binds to, and decide if the claim is
    # legitimately *explained*:
    #   - binds to a decomposition finding -> explained (a real "why" analysis);
    #   - binds to a composition finding    -> already caught by failure mode 1;
    #   - binds to nothing / a non-method finding -> ORPHAN. A causal claim with
    #     no decomposition behind it is exactly the altitude error the gate
    #     exists to catch. When the sidecar carries any composition finding (the
    #     usual source of a laundered driver claim), flag those composition
    #     findings rather than let the claim escape.
    #
    # Tradeoff (documented per the round-4 directive): a fully-robust attribution
    # of an arbitrary prose claim to a specific method is not achievable from
    # text alone, so this ERRS TOWARD DETECTION. An orphan driver claim alongside
    # a composition finding is flagged for the analyst to confirm. A false
    # positive that asks "is this composition cut really a driver?" is far safer
    # than a laundered driver claim shipping silently. The legitimate mixed case
    # (one real decomposition-driver section + one descriptive composition
    # section) stays green because that driver claim binds to the decomposition
    # finding and is therefore explained.
    if rendered_blocks:
        any_composition = any(finding_is_composition)
        already_flagged_composition = any(
            "composition/groupby cut" in p for p in problems
        )
        if any_composition and not already_flagged_composition:
            for block in rendered_blocks:
                # Require a STRONG driver-attribution phrase, not a bare
                # connective. "because"/"due to"/"explains" pervade legitimate
                # prose (incl. caveats/limitations), so only phrases that assert
                # a driver/cause of a change ("driven by", "attributable to",
                # "root cause", ...) count here. We deliberately do NOT skip
                # "non-analytical" sections by header: a strong driver claim
                # laundered under a "Notes"/"Appendix" heading must still flag,
                # else the header becomes a trivial evasion of the gate.
                if not any(p in block.lower() for p in _STRONG_DRIVER_PHRASES):
                    continue
                owner = _best_finding_index_for_block(block, finding_token_sets)
                explained = owner is not None and finding_is_decomposition[owner]
                if explained:
                    continue
                if owner is not None and finding_is_composition[owner]:
                    # Bound to a composition finding — failure mode 1 owns this
                    # case (it scans the finding's own bound section). Skip here
                    # to avoid a duplicate problem line.
                    continue
                # Orphan driver claim: no decomposition explains it. Flag the
                # composition finding(s) so the laundered claim cannot escape.
                comp_idxs = [
                    j + 1 for j, is_comp in enumerate(finding_is_composition)
                    if is_comp
                ]
                problems.append(
                    f"a driver/cause claim appears in a rendered section that "
                    f"maps to no decomposition finding; the only causal-capable "
                    f"method in scope is composition (finding(s) {comp_idxs}) — "
                    f"a composition/groupby cut cannot establish a driver. "
                    f"Confirm the cause is backed by a decomposition, not a "
                    f"share-of-total cut."
                )
                break

    if problems:
        return EvalResult(
            eid, "", "fail",
            "analysis design does not answer the scoped question — "
            + "; ".join(problems[:5]),
        )
    return EvalResult(
        eid, "", "pass",
        "every finding's method matches its claim and maps to the scoped question",
    )


def check_metric_terms_resolve(rendered: str, sidecar: dict) -> EvalResult:
    """Metric jargon used in a deliverable must be pinned to a definition.

    The semantic layer is the top of the trust hierarchy; the "active users has
    no single definition" ambiguity is what produces confidently-wrong numbers.
    When a deliverable carries a measure-definitions structure (a "Measure
    definitions" section or a `measures` sidecar block), every computational
    metric term it uses (PMPM, MLR, RAF, …) must appear there. If there is no
    such structure, we don't enforce — avoids false-positives on an exec summary
    that legitimately references a metric defined elsewhere.
    """
    eid = "metric-terms-resolve-to-glossary"
    try:
        from .. import glossary as semantic_layer
    except Exception:  # noqa: BLE001
        return EvalResult(eid, "", "not_runnable", "semantic_layer unavailable")

    used = set(semantic_layer.resolve(rendered)) & semantic_layer.metric_terms()
    if not used:
        return EvalResult(eid, "", "pass", "no metric terms used (vacuous pass)")

    # Locate a definitions structure.
    section = ""
    for header in ("Measure definitions", "Measure Definitions", "## Measures"):
        idx = rendered.find(header)
        if idx != -1:
            rest = rendered[idx:]
            nxt = rest.find("\n## ", len(header))
            section = rest if nxt == -1 else rest[:nxt]
            break
    measures = sidecar.get("measures") if isinstance(sidecar, dict) else None
    if not section and not measures:
        return EvalResult(
            eid, "", "pass",
            f"metric terms {sorted(used)} used; no measure-definitions structure "
            "to enforce against (vacuous pass)",
        )

    blob = (section + " " + json.dumps(measures, default=str)).upper()
    missing = sorted(t for t in used if t not in blob)
    if missing:
        return EvalResult(
            eid, "", "fail",
            f"metric terms used but absent from measure definitions: {missing}",
        )
    return EvalResult(
        eid, "", "pass",
        f"all metric terms {sorted(used)} are pinned in measure definitions",
    )


def check_provenance_footer(rendered: str, sidecar: dict) -> EvalResult:
    """The provenance footer must carry source tier, freshness, owner, privacy.

    Anthropic's playbook puts a provenance footer on every analytic output so a
    reader sees how much to trust a number and whom to ask. We assert the
    rendered markdown carries the source line (tier/freshness/owner), the
    privacy summary, and the reproducibility line (git/model/snapshot).
    """
    eid = "provenance-footer-complete"
    needles = {
        "source line": "Source — tier:",
        "privacy summary": "Privacy —",
        "provenance line": "Provenance — git",
    }
    missing = [label for label, needle in needles.items() if needle not in rendered]
    if missing:
        return EvalResult(
            eval_id=eid, skill_name="", status="fail",
            detail=f"provenance footer missing: {', '.join(missing)}",
        )
    return EvalResult(
        eval_id=eid, skill_name="", status="pass",
        detail="provenance footer carries source tier, freshness, owner, privacy, repro stamps",
    )


CHECKERS: dict[str, Callable[[str, dict], EvalResult]] = {
    "anti-fabrication-mandatory": check_anti_fabrication,
    "no-fabricated-raf-scores": check_no_fabricated_raf_scores,
    "code-set-stamp-present": check_code_set_stamp,
    "provenance-footer-complete": check_provenance_footer,
    "adversarial-review-present-for-leadership": check_adversarial_review_present,
    "analysis-design-answers-scope": check_analysis_answers_scope,
    "metric-terms-resolve-to-glossary": check_metric_terms_resolve,
    "render-split-discipline": check_render_split,
    "small-cell-suppression-applied": check_small_cell_suppression,
    "age-90-plus-aggregated": check_age_90_plus,
    # Phase C.1 — five new structural checkers.
    "numbered-exhibits": check_numbered_exhibits,
    "structural-sections-present": check_kff_sections_present,
    "action-title-rule-slides": check_action_title_rule,
    "action-title-on-every-analytic-slide": check_action_title_rule,
    "denominator-explicit": check_denominator_explicit,
    "contract-mechanics-pinned": check_contract_mechanics_pinned,
    # v1.4 capability-matrix — three privacy-guardrail checkers.
    "safe-harbor-18-clean": check_safe_harbor_18_clean,
    "part-2-redisclosure-flag": check_part_2_redisclosure_flag,
    "scanner-crash-defaults-to-redact": check_scanner_crash_defaults_to_redact,
}


def run(spec: EvalSpec, *, rendered: str, sidecar: dict) -> EvalResult:
    # The oracle checker needs the eval's `extra` (its `oracle` block), so it is
    # dispatched explicitly rather than through the 2-arg CHECKERS table.
    if spec.eval_id == ORACLE_EVAL_ID:
        result = check_expected_value_match(rendered, sidecar, spec.extra)
        result.skill_name = spec.skill_name
        return result
    checker = CHECKERS.get(spec.eval_id)
    if checker is None:
        return EvalResult(
            eval_id=spec.eval_id, skill_name=spec.skill_name,
            status="not_runnable",
            detail=f"no built-in checker for eval id {spec.eval_id!r}; "
            "this eval is documented but not yet programmatically verified",
        )
    result = checker(rendered, sidecar)
    result.skill_name = spec.skill_name
    return result


def run_all(
    repo_root: Path,
    samples: dict[str, dict] | None = None,
    *,
    telemetry_sink: Path | None = None,
    meta: dict | None = None,
) -> EvalReport:
    """Run every discoverable eval against the provided per-skill samples.

    `samples` maps skill_name → {"rendered": str, "sidecar": dict}. Skills
    without a sample are reported as `skipped`.

    If `telemetry_sink` is given, write one `EvalRunRecord` (git SHA, model id,
    skill versions, token counts, snapshot date, summary) so eval accuracy is
    tracked over time per Anthropic's "store results as telemetry" practice.
    `meta` supplies run identity the runtime can't infer here (this environment
    has no wall clock): `run_id`, `timestamp`, `git_sha`, `model_id`,
    `skill_versions`, `token_counts`.
    """
    samples = samples or {}
    meta = meta or {}
    report = EvalReport()
    for spec in discover_evals(repo_root):
        sample = samples.get(spec.skill_name)
        if sample is None:
            report.results.append(EvalResult(
                eval_id=spec.eval_id, skill_name=spec.skill_name,
                status="skipped",
                detail=f"no sample provided for skill {spec.skill_name!r}",
            ))
            continue
        report.results.append(run(
            spec, rendered=sample["rendered"], sidecar=sample["sidecar"],
        ))

    if telemetry_sink is not None:
        record = EvalRunRecord.from_report(report, samples=samples, meta=meta)
        record.write(telemetry_sink)
    return report


@dataclass
class EvalRunRecord:
    """One eval-run telemetry record. Persisted as JSON for accuracy tracking."""

    run_id: str
    timestamp: str
    git_sha: str
    model_id: str
    skill_versions: dict
    token_counts: dict
    snapshot_date: str
    summary: dict
    results: list[dict] = field(default_factory=list)

    @classmethod
    def from_report(
        cls, report: EvalReport, *, samples: dict, meta: dict
    ) -> "EvalRunRecord":
        # Snapshot date: the code-set stamp shared by the sampled sidecars, if
        # consistent. Anchors ground truth to a date rather than live data.
        stamps = {
            (s.get("sidecar", {}).get("code_set_versions", {}) or {}).get("stamped_at")
            for s in samples.values()
        }
        stamps.discard(None)
        snapshot = stamps.pop() if len(stamps) == 1 else (meta.get("snapshot_date") or "unknown")
        return cls(
            run_id=str(meta.get("run_id") or meta.get("timestamp") or "run"),
            timestamp=str(meta.get("timestamp") or "unknown"),
            git_sha=str(meta.get("git_sha") or "unknown"),
            model_id=str(meta.get("model_id") or "unknown"),
            skill_versions=dict(meta.get("skill_versions") or {}),
            token_counts=dict(meta.get("token_counts") or {}),
            snapshot_date=str(snapshot),
            summary=report.summary,
            results=[
                {"eval_id": r.eval_id, "skill_name": r.skill_name,
                 "status": r.status, "detail": r.detail}
                for r in report.results
            ],
        )

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "timestamp": self.timestamp,
            "git_sha": self.git_sha, "model_id": self.model_id,
            "skill_versions": self.skill_versions, "token_counts": self.token_counts,
            "snapshot_date": self.snapshot_date, "summary": self.summary,
            "results": self.results,
        }

    def write(self, sink: Path) -> Path:
        """Write the record as JSON. `sink` may be a file or a directory."""
        sink = Path(sink)
        if sink.suffix == ".json":
            path = sink
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            sink.mkdir(parents=True, exist_ok=True)
            path = sink / f"{self.run_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path


def gate(report: EvalReport, *, threshold: float = 0.9, by: str = "skill") -> dict:
    """Per-domain accuracy gate. Anthropic gates launches per domain at ~90%.

    Accuracy = passed / (passed + failed); not_runnable and skipped are ignored.
    `by="skill"` groups by skill_name. Returns one entry per domain plus
    `_overall`, each `{accuracy, passed, failed, cleared}`.
    """
    groups: dict[str, list[EvalResult]] = {}
    for r in report.results:
        key = r.skill_name if by == "skill" else r.eval_id
        groups.setdefault(key, []).append(r)

    def _score(results: list[EvalResult]) -> dict:
        passed = sum(1 for r in results if r.status == "pass")
        failed = sum(1 for r in results if r.status == "fail")
        denom = passed + failed
        acc = (passed / denom) if denom else 1.0
        return {"accuracy": acc, "passed": passed, "failed": failed,
                "cleared": acc >= threshold}

    out = {domain: _score(results) for domain, results in groups.items()}
    out["_overall"] = _score(report.results)
    return out


__all__ = [
    "EvalSpec", "EvalResult", "EvalReport", "EvalRunRecord",
    "discover_evals", "run", "run_all", "gate",
    "check_expected_value_match", "ORACLE_EVAL_ID",
    "extract_number_tokens",
    "CHECKERS",
]
