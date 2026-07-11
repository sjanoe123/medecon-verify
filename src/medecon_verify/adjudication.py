"""Claims adjudication awareness — final-action dedup, allowed/paid reconciliation,
reversal-pair detection, header/line consistency, ICN/DCN linkage.

The single highest-impact silent-failure mode for an analyst agent is silently
miscounting reversed claims. This module makes that mistake structurally hard.

v1.1 Phase D:
- `reversal_pairs` was O(K^2) per claim_id group — pathological at 100k+
  lines per claim. Replaced with O(K) bucket-by-amount within group, so
  the function is O(N) overall regardless of group skew.
- New `iter_reversal_pairs` streams record iterators, yielding pairs as
  they're identified. Memory bounded by the largest in-flight claim_id.

Public contract (pinned):

    final_action_signal(records, *, claim_id, claim_version, claim_line=None)
            -> dict
        Returns {"total_lines", "duplicates", "pct_dup", "needs_dedup", ...}.
        The dedup key is (claim_id, claim_line) when a claim_line column is
        present (else claim_id alone), so distinct lines of a multi-line claim
        are NOT counted as supersessions. Does NOT actually dedup. Pure signal
        — the analyst decides next steps.

    final_action_dedup(records, *, claim_id, claim_version, claim_line=None)
            -> list[dict]
        Returns deduped records. The dedup key is (claim_id, claim_line) when a
        claim_line column is present (else claim_id alone), so distinct lines of
        a multi-line claim are each kept. Keeps the highest claim_version per
        key; rows tied at the winning version are kept as conflicts, not
        collapsed. For non-versioned claims (claim_version is None across the
        record set) only EXACT full-row duplicates are dropped — distinct rows
        sharing a claim_id are never merged.

    reversal_pairs(records, *, claim_id, paid_amount) -> list[dict]
        Returns records that look like reversal pairs (offsetting paid amounts
        on the same claim_id). Used for visibility; caller decides what to do.
        Result preserves input order (per row, both members of each pair).

    iter_reversal_pairs(records_iter, *, claim_id, paid_amount) -> Iterator[dict]
        Streaming variant. Consumes any iterable of dicts and yields reversal
        pair members incrementally. Memory: O(max in-flight claim_id group).

    allowed_paid_distribution(records, *, allowed_field, paid_field) -> dict
        Histogram + flags for paid > allowed and allowed = 0 with paid > 0.

    header_line_consistency(headers, lines, *, claim_id, claim_amount,
                            line_amount) -> dict
        Verify that line-level amounts roll up to header-level amounts.

    blended_total(records, *, amount_field, date_field="service_date",
                  context="blended paid total") -> dict
        Sum an amount across records and, if the service-date span crosses an
        ICD-10-CM fiscal-year boundary, attach a non-empty hard-warning naming
        the two FYs. This makes the VINT-1 discipline structural: a total cannot
        ship without the vintage check — there is no "inline query" carve-out.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

try:  # package import and script-path import both work
    from . import codeset as _codeset_version
except ImportError:  # pragma: no cover - fallback when run as a bare module
    import codeset as _codeset_version  # type: ignore


def _row_signature(record: dict) -> tuple:
    """Hashable, order-independent signature of a full row for exact-dup detection."""
    return tuple(sorted((str(k), str(v)) for k, v in record.items()))


def _count_exact_duplicates(records: list[dict]) -> int:
    """Count rows that are exact full-row duplicates of an earlier row (keep-first)."""
    seen: set[tuple] = set()
    exact = 0
    for r in records:
        sig = _row_signature(r)
        if sig in seen:
            exact += 1
        else:
            seen.add(sig)
    return exact


def final_action_signal(
    records: list[dict],
    *,
    claim_id: str,
    claim_version: str | None,
    claim_line: str | None = None,
) -> dict[str, Any]:
    """Signal: how much of this dataset would final-action dedup remove?

    The dedup key is ``(claim_id, claim_line)``. A row is a *duplicate* only if:

      - it is superseded by a higher ``claim_version`` for the **same** dedup
        key (an earlier adjudication of the same claim line), **or**
      - it is an exact full-row duplicate of another row.

    Extra lines of a multi-line claim (same ``claim_id``, different
    ``claim_line``) are **not** duplicates — they are expected. Counting them as
    dups is the 38.8%-artifact bug this signal exists to prevent.

    When no ``claim_version`` field is present, only **exact full-row
    duplicates** are counted — never rows that merely share a ``claim_id``.

    Returns a dict with:
      total_lines: int — input row count
      claim_count: int — distinct claim_id count
      claim_lines: int — distinct (claim_id, claim_line) dedup keys
      multi_line_rows: int — rows belonging to a claim with >1 distinct line
        (expected; NOT duplicates)
      duplicates: int — rows that would be dropped under final-action dedup
        (superseded versions + exact full-row dups)
      superseded: int — duplicates attributable to a higher-version supersession
        (only rows STRICTLY below the group-max version, never ties at the max)
      exact_duplicates: int — duplicates attributable to exact full-row equality
      version_conflicts: int — distinct, non-identical rows tied at the winning
        (max) version within a dedup key. These are NOT supersessions/dups —
        they are true final-action conflicts (two competing adjudications at the
        same version) and a caveat the analyst must resolve, not silently drop.
      pct_dup: float — duplicates / total_lines
      needs_dedup: bool — True if duplicates > 0
      missing_version_field: bool — True if claim_version is absent

    Does NOT mutate or dedup. Pure signal.
    """
    if not records:
        return {
            "total_lines": 0,
            "claim_count": 0,
            "claim_lines": 0,
            "multi_line_rows": 0,
            "duplicates": 0,
            "superseded": 0,
            "exact_duplicates": 0,
            "version_conflicts": 0,
            "pct_dup": 0.0,
            "needs_dedup": False,
            "missing_version_field": False,
        }

    has_line = claim_line is not None and any(claim_line in r for r in records)

    # Distinct claim_ids and dedup-key cardinality, plus multi-line accounting.
    ids = {r.get(claim_id) for r in records}
    if has_line:
        keys = {(r.get(claim_id), r.get(claim_line)) for r in records}
        lines_per_id: dict[Any, set] = defaultdict(set)
        for r in records:
            lines_per_id[r.get(claim_id)].add(r.get(claim_line))
        multi_line_rows = sum(
            len([r for r in records if r.get(claim_id) == cid])
            for cid, lns in lines_per_id.items()
            if len(lns) > 1
        )
    else:
        keys = set(ids)
        multi_line_rows = 0

    exact_dups = _count_exact_duplicates(records)

    has_version = _has_usable_version(records, claim_version)
    if not has_version:
        # No version field: a row is a duplicate ONLY if it is an exact full-row
        # duplicate. Sharing a claim_id (or even a dedup key, absent versions)
        # is NOT enough — those are legitimately distinct adjudications/lines.
        duplicates = exact_dups
        return {
            "total_lines": len(records),
            "claim_count": len(ids),
            "claim_lines": len(keys),
            "multi_line_rows": multi_line_rows,
            "duplicates": duplicates,
            "superseded": 0,
            "exact_duplicates": exact_dups,
            "version_conflicts": 0,
            "pct_dup": duplicates / len(records),
            "needs_dedup": duplicates > 0,
            "missing_version_field": True,
        }

    # Versioned: within each dedup key, every row except the highest-version one
    # is superseded. Without a claim_line we fall back to keying on claim_id
    # alone (best available), but extra lines then look like supersessions — so
    # a claim_line column is strongly preferred and recorded as a caveat upstream.
    def _key(r: dict) -> tuple:
        return (r.get(claim_id), r.get(claim_line)) if has_line else (r.get(claim_id),)

    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        by_key[_key(r)].append(r)

    superseded = 0
    superseded_sigs: list[tuple] = []
    version_conflicts = 0
    for _k, rs in by_key.items():
        if len(rs) <= 1:
            continue
        # The highest *version* survives; only rows STRICTLY below the group-max
        # version are superseded. Rows tied at the max version but not identical
        # are a true version conflict (two final-action candidates), NOT a
        # supersession — counting them as superseded overstates dedup and hides
        # the conflict. They are counted separately and surfaced as a caveat.
        keyed = [(_version_sort_key(r.get(claim_version)), r) for r in rs]
        max_vkey = max(k for k, _r in keyed)
        below = [r for k, r in keyed if k < max_vkey]
        at_max = [r for k, r in keyed if k == max_vkey]
        for r in below:
            superseded += 1
            superseded_sigs.append(_row_signature(r))
        if len(at_max) > 1:
            # Distinct rows tied at the winning version: conflict, not dedup. An
            # exact full-row duplicate among them is still a dup (handled below by
            # the exact-dup pass); only non-identical ties are conflicts.
            distinct_at_max = {_row_signature(r) for r in at_max}
            if len(distinct_at_max) > 1:
                version_conflicts += len(distinct_at_max) - 1

    # Exact full-row duplicates that are NOT already counted as superseded rows
    # (a supersession with identical-everything would otherwise double-count).
    superseded_sig_set = set(superseded_sigs)
    extra_exact = 0
    seen: set[tuple] = set()
    for r in records:
        sig = _row_signature(r)
        if sig in seen and sig not in superseded_sig_set:
            extra_exact += 1
        seen.add(sig)

    duplicates = superseded + extra_exact
    return {
        "total_lines": len(records),
        "claim_count": len(ids),
        "claim_lines": len(keys),
        "multi_line_rows": multi_line_rows,
        "duplicates": duplicates,
        "superseded": superseded,
        "exact_duplicates": extra_exact,
        "version_conflicts": version_conflicts,
        "pct_dup": duplicates / len(records),
        "needs_dedup": duplicates > 0,
        "missing_version_field": False,
    }


def _version_sort_key(v: Any) -> tuple:
    """Sort key for claim_version: numeric when possible, else lexicographic.

    Tuples are compared by ``max()`` to pick the final-action winner, so the
    tier index MUST put real numeric versions ABOVE missing/unparseable ones.
    Returns ``(0, str(v))`` for ``None``/non-numeric versions and ``(1, float)``
    for numerics. This guarantees any parseable numeric version outranks a
    missing or junk version: a row whose ``claim_version`` is ``None`` or
    garbage can never be selected as the final action over a legitimately
    numbered row. (The previous ``(0, float)`` / ``(1, str)`` ordering inverted
    this — a ``None`` version sorted above numerics and silently won, the exact
    'miscount superseded claims' failure this module exists to prevent.)
    Among themselves, missing/non-numeric versions order deterministically by
    their string form.
    """
    f = _to_float_or_none(v)
    if f is not None:
        return (1, f)
    return (0, str(v))


def _has_usable_version(records: list[dict], claim_version: str | None) -> bool:
    """True only when the version column carries at least one usable value.

    Key PRESENCE is not enough: an all-null/blank ``claim_version`` column means
    the data is effectively un-versioned and must take the missing-version path.
    Routing an all-null column through the versioned path makes every row tie at
    ``_version_sort_key(None)``, so distinct same-claim rows are miscounted as
    version_conflicts instead of being reported as exact-dup-only (round-6
    finding 4). A value counts as usable when it parses to a number OR is a
    non-blank string.
    """
    if claim_version is None:
        return False
    for r in records:
        if claim_version not in r:
            continue
        val = r.get(claim_version)
        if _to_float_or_none(val) is not None:
            return True
        if str(val).strip() if val is not None else "":
            return True
    return False


def final_action_dedup(
    records: list[dict],
    *,
    claim_id: str,
    claim_version: str | None,
    claim_line: str | None = None,
) -> list[dict]:
    """Final-action dedup: keep the highest claim_version per dedup key.

    The dedup key is ``(claim_id, claim_line)`` when a ``claim_line`` column is
    present, mirroring ``final_action_signal``. Distinct lines of the same claim
    (same ``claim_id``, different ``claim_line``) are SEPARATE records and are
    each kept — collapsing them to one row per ``claim_id`` understates every
    downstream total. Without a ``claim_line`` the key falls back to
    ``claim_id`` alone (best available), the same caveat the signal records.

    Versioned data: within each key, the highest parseable ``claim_version``
    survives; lower versions are dropped. When two rows tie at the winning
    version they are a true conflict, not a supersession — both are kept (the
    caller resolves the conflict; we never silently pick one). Exact full-row
    duplicates are still collapsed.

    Unversioned data (no ``claim_version`` field): we drop only EXACT full-row
    duplicates and keep every otherwise-distinct row. The old last-wins-per-
    ``claim_id`` behavior silently merged legitimately distinct adjudications and
    lines into one row; that is the collapse this function exists to avoid.

    Returns the deduped record list in first-seen order.
    """
    if not records:
        return records

    has_line = claim_line is not None and any(claim_line in r for r in records)

    def _key(r: dict) -> tuple:
        return (r.get(claim_id), r.get(claim_line)) if has_line else (r.get(claim_id),)

    has_version = _has_usable_version(records, claim_version)

    if not has_version:
        # Drop only exact full-row duplicates (keep-first); never merge distinct
        # rows that merely share a claim_id / dedup key.
        seen_sigs: set[tuple] = set()
        out: list[dict] = []
        for r in records:
            sig = _row_signature(r)
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            out.append(r)
        return out

    # Versioned: bucket by dedup key, keep rows tied at the max version (drop the
    # strictly-lower ones). Then collapse exact full-row duplicates among the
    # survivors. Output preserves first-seen order.
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    order: list[tuple] = []
    for r in records:
        k = _key(r)
        if k not in by_key:
            order.append(k)
        by_key[k].append(r)

    survivors: list[dict] = []
    for k in order:
        rs = by_key[k]
        keyed = [(_version_sort_key(r.get(claim_version)), r) for r in rs]
        max_vkey = max(vk for vk, _r in keyed)
        survivors.extend(r for vk, r in keyed if vk == max_vkey)

    # Collapse exact full-row duplicates (e.g. two identical rows tied at the max
    # version) while keeping non-identical version conflicts.
    seen_sigs = set()
    out = []
    for r in survivors:
        sig = _row_signature(r)
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        out.append(r)
    return out


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_AMOUNT_TOLERANCE = 0.005  # rounding to cents (within ±0.005)


def _match_pairs_within_group(
    rows: list[dict], paid_amount: str,
) -> list[dict]:
    """Match offsetting paid amounts within a single claim_id group.

    Algorithm: bucket positive amounts by rounded magnitude; walk negatives
    once, popping the matching positive bucket. O(K) per group.
    """
    if len(rows) < 2:
        return []

    pos_buckets: dict[int, list[int]] = defaultdict(list)  # mag-cents → row indices
    for i, r in enumerate(rows):
        v = _to_float_or_none(r.get(paid_amount))
        if v is None or v <= 0:
            continue
        pos_buckets[round(v * 100)].append(i)

    paired: list[tuple[int, int]] = []
    matched_indices: set[int] = set()
    for j, r in enumerate(rows):
        v = _to_float_or_none(r.get(paid_amount))
        if v is None or v >= 0:
            continue
        mag_cents = round(-v * 100)
        bucket = pos_buckets.get(mag_cents)
        if not bucket:
            continue
        # Pop the earliest available positive that hasn't been matched.
        partner = None
        while bucket:
            candidate = bucket.pop(0)
            if candidate not in matched_indices and candidate != j:
                partner = candidate
                break
        if partner is None:
            continue
        matched_indices.add(j)
        matched_indices.add(partner)
        paired.append((min(j, partner), max(j, partner)))

    paired.sort()
    out: list[dict] = []
    for a, b in paired:
        out.append(rows[a])
        out.append(rows[b])
    return out


def reversal_pairs(
    records: list[dict],
    *,
    claim_id: str,
    paid_amount: str,
) -> list[dict]:
    """Find apparent reversal pairs: same claim_id with offsetting paid amounts.

    Returns the list of records that participate in a reversal pair. Output
    is grouped by pair: each pair contributes its two members in
    earlier-index-first order, then all pairs in earlier-pair-first order.

    Complexity O(N): linear bucket-by-claim-id, then linear bucket-by-amount
    within group. Replaces the v1.0 O(K^2)-per-group inner loop.
    """
    if not records:
        return []

    by_id: dict[Any, list[dict]] = defaultdict(list)
    for r in records:
        by_id[r.get(claim_id)].append(r)

    out: list[dict] = []
    for _cid, rs in by_id.items():
        out.extend(_match_pairs_within_group(rs, paid_amount))
    return out


def iter_reversal_pairs(
    records_iter: Iterable[dict],
    *,
    claim_id: str,
    paid_amount: str,
) -> Iterator[dict]:
    """Streaming variant of `reversal_pairs`.

    Consumes any iterable of records and yields pair-member dicts incrementally.
    Records are buffered until the claim_id group is closed (i.e., we see a
    different claim_id, then come back later — but we keep all pending groups
    in memory to handle interleaved input). For strictly-grouped inputs (sorted
    by claim_id), memory is bounded by the largest single group. For
    arbitrary order, memory is O(distinct claim_ids).

    Use this when consuming claims from a generator (e.g., row-by-row CSV /
    parquet streaming) where materializing the full list of records is
    impractical. Yields pair members in pair order: each pair's two members
    appear adjacent in the output stream, in input-order within the pair.
    """
    pending: dict[Any, list[dict]] = defaultdict(list)
    for r in records_iter:
        pending[r.get(claim_id)].append(r)
    for _cid, rs in pending.items():
        for member in _match_pairs_within_group(rs, paid_amount):
            yield member


def allowed_paid_distribution(
    records: list[dict],
    *,
    allowed_field: str,
    paid_field: str,
) -> dict[str, Any]:
    """Distribution stats + flags for the allowed/paid relationship.

    Flags:
      paid_gt_allowed: count where paid > allowed (suspicious)
      allowed_zero_paid_nonzero: count where allowed = 0 and paid > 0 (suspicious)
      both_null_capitation: count where both null (capitation-style; not flagged)
      negative_paid: count where paid < 0 (likely reversal claims)
    """
    paid_gt_allowed = 0
    allowed_zero_paid_nonzero = 0
    both_null_capitation = 0
    negative_paid = 0
    valid_pairs = 0

    paid_values: list[float] = []
    allowed_values: list[float] = []

    for r in records:
        a = r.get(allowed_field)
        p = r.get(paid_field)

        if a is None and p is None:
            both_null_capitation += 1
            continue

        try:
            af = float(a) if a is not None else None
            pf = float(p) if p is not None else None
        except (TypeError, ValueError):
            continue

        if pf is not None and pf < 0:
            negative_paid += 1
        if pf is not None:
            paid_values.append(pf)
        if af is not None:
            allowed_values.append(af)

        if af is not None and pf is not None:
            valid_pairs += 1
            if pf > af:
                paid_gt_allowed += 1
            if af == 0 and pf > 0:
                allowed_zero_paid_nonzero += 1

    def _summary(values: list[float]) -> dict[str, float]:
        if not values:
            return {"n": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0}
        s = sorted(values)
        mid = len(s) // 2
        median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return {
            "n": len(s),
            "min": s[0],
            "max": s[-1],
            "mean": sum(s) / len(s),
            "median": median,
        }

    return {
        "valid_pairs": valid_pairs,
        "paid_gt_allowed": paid_gt_allowed,
        "allowed_zero_paid_nonzero": allowed_zero_paid_nonzero,
        "both_null_capitation": both_null_capitation,
        "negative_paid": negative_paid,
        "paid_summary": _summary(paid_values),
        "allowed_summary": _summary(allowed_values),
    }


def header_line_consistency(
    headers: list[dict],
    lines: list[dict],
    *,
    claim_id: str,
    claim_amount: str,
    line_amount: str,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    """Verify that line-level amounts roll up to header-level amounts.

    Returns:
      checked: int — claim_ids with both header and line records
      mismatched: int — claim_ids where line_sum differs from claim_amount > tolerance
      orphan_lines: int — line records with no matching header
      orphan_headers: int — header records with no line records
      mismatch_examples: list — up to 5 example mismatches for debugging
    """
    line_sums: dict[Any, float] = defaultdict(float)
    line_ids: set[Any] = set()
    for ln in lines:
        cid = ln.get(claim_id)
        amt = ln.get(line_amount)
        line_ids.add(cid)
        try:
            if amt is not None:
                line_sums[cid] += float(amt)
        except (TypeError, ValueError):
            continue

    header_ids = {h.get(claim_id) for h in headers}

    checked = 0
    mismatched = 0
    examples: list[dict] = []
    for h in headers:
        cid = h.get(claim_id)
        amt = h.get(claim_amount)
        if cid not in line_ids:
            continue
        try:
            target = float(amt) if amt is not None else None
        except (TypeError, ValueError):
            continue
        if target is None:
            continue
        checked += 1
        line_sum = line_sums[cid]
        if abs(line_sum - target) > tolerance:
            mismatched += 1
            if len(examples) < 5:
                examples.append({
                    "claim_id": cid,
                    "header_amount": target,
                    "line_sum": line_sum,
                    "delta": line_sum - target,
                })

    return {
        "checked": checked,
        "mismatched": mismatched,
        "orphan_lines": len(line_ids - header_ids),
        "orphan_headers": len(header_ids - line_ids),
        "mismatch_examples": examples,
    }


def blended_total(
    records: list[dict],
    *,
    amount_field: str = "paid_amount",
    date_field: str = "service_date",
    context: str = "blended paid total",
) -> dict[str, Any]:
    """Sum `amount_field` across records, surfacing a code-set vintage warning.

    The total is computed over the service-date span implied by `date_field`
    (a single field, or a pair like ``"service_from"``/``"service_to"`` — pass
    either; both ends are read when present). If that span crosses an Oct-01
    ICD-10-CM fiscal-year boundary, ``vintage_warning`` is a NON-EMPTY hard
    warning naming the two FYs (e.g. FY2024→FY2025) and ``crosses_fy_boundary``
    is True.

    This is the VINT-1 guardrail: producing a blended total across a service-date
    span that crosses the FY boundary must raise a hard warning. There is no
    "inline query" carve-out — the warning rides with the number.

    Returns:
      total: float — sum of parseable amounts
      n_records: int — input row count
      n_amounts: int — rows with a parseable amount
      fiscal_years: list[str] — sorted ICD-10-CM FYs touched by the span
      crosses_fy_boundary: bool — True when fiscal_years has >1 entry
      vintage_warning: str — non-empty hard warning when crossing, else ""
    """
    total = 0.0
    n_amounts = 0
    dates: list[Any] = []
    for r in records:
        v = _to_float_or_none(r.get(amount_field))
        if v is not None:
            total += v
            n_amounts += 1
        # Collect every present service-date end (handles single field or a
        # service_from/service_to pair without the caller having to flatten).
        for fld in (date_field, "service_from", "service_to"):
            if fld in r and r.get(fld):
                dates.append(r.get(fld))

    fiscal_years = _codeset_version.detect_fy_span(dates)
    warning = _codeset_version.vintage_mismatch_warning(dates, context=context)
    return {
        "total": total,
        "n_records": len(records),
        "n_amounts": n_amounts,
        "fiscal_years": fiscal_years,
        "crosses_fy_boundary": len(fiscal_years) > 1,
        "vintage_warning": warning,
    }


__all__ = [
    "final_action_signal",
    "final_action_dedup",
    "reversal_pairs",
    "iter_reversal_pairs",
    "allowed_paid_distribution",
    "header_line_consistency",
    "blended_total",
]
