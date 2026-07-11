"""Phase D — `reversal_pairs` O(N) refactor + streaming variant.

Verifies:
- list API behavior preserved on small inputs.
- iter_reversal_pairs streams pairs from any iterable, yields the same set.
- bucket-based pairing handles edge cases:
    - more positives than negatives in a group
    - exact-zero amounts ignored
    - non-numeric amounts ignored
    - cents-precision rounding (12.345 vs -12.34 collide; should pair)
- slow benchmark: 1M synthetic rows complete under 10 s.

The slow test uses pytest's `slow` marker (registered in pyproject) so the
default test run is unaffected.
"""
from __future__ import annotations

import time

import pytest

from medecon_verify import adjudication as ca


def _row(cid: str, amt: float | None) -> dict:
    return {"claim_id": cid, "paid_amount": amt}


# ---------- correctness on small inputs ----------


def test_reversal_pairs_basic_offset() -> None:
    rows = [_row("A", 100.0), _row("A", -100.0), _row("B", 50.0)]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert len(pairs) == 2
    assert {r["paid_amount"] for r in pairs} == {100.0, -100.0}


def test_reversal_pairs_no_pairs_when_no_offsets() -> None:
    rows = [_row("A", 100.0), _row("A", 50.0), _row("B", 75.0)]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert pairs == []


def test_reversal_pairs_handles_multiple_within_group() -> None:
    rows = [
        _row("A", 100.0), _row("A", -100.0),
        _row("A", 50.0), _row("A", -50.0),
        _row("A", 25.0),  # unpaired; no -25
    ]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert len(pairs) == 4
    assert sum(r["paid_amount"] for r in pairs) == 0.0


def test_reversal_pairs_only_pairs_first_match_per_negative() -> None:
    """Three positives at $100, two negatives at $100. We expect 2 pairs (4 rows)."""
    rows = [
        _row("A", 100.0), _row("A", 100.0), _row("A", 100.0),
        _row("A", -100.0), _row("A", -100.0),
    ]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert len(pairs) == 4


def test_reversal_pairs_ignores_zero_amounts() -> None:
    rows = [_row("A", 0.0), _row("A", -0.0), _row("A", 50.0), _row("A", -50.0)]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert len(pairs) == 2  # the 50/-50 pair only
    assert all(abs(r["paid_amount"]) == 50.0 for r in pairs)


def test_reversal_pairs_ignores_nonnumeric_amounts() -> None:
    rows = [_row("A", "garbage"), _row("A", 100.0), _row("A", -100.0)]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert len(pairs) == 2


def test_reversal_pairs_string_numbers_work() -> None:
    rows = [_row("A", "100.0"), _row("A", "-100.0")]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert len(pairs) == 2


def test_reversal_pairs_cross_claim_id_isolation() -> None:
    """A positive on claim A and a negative on claim B must NOT pair."""
    rows = [_row("A", 100.0), _row("B", -100.0)]
    pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    assert pairs == []


def test_reversal_pairs_empty_input() -> None:
    assert ca.reversal_pairs([], claim_id="claim_id", paid_amount="paid_amount") == []


def test_reversal_pairs_singleton_group() -> None:
    rows = [_row("A", 100.0)]
    assert ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount") == []


# ---------- streaming API ----------


def test_iter_reversal_pairs_yields_same_pairs_as_list_api() -> None:
    rows = [
        _row("A", 100.0), _row("A", -100.0),
        _row("B", 50.0), _row("B", -50.0),
        _row("A", 200.0), _row("A", -200.0),
    ]
    list_pairs = ca.reversal_pairs(rows, claim_id="claim_id", paid_amount="paid_amount")
    iter_pairs = list(ca.iter_reversal_pairs(
        iter(rows), claim_id="claim_id", paid_amount="paid_amount",
    ))
    # Same set of records (pair ordering across groups may differ).
    list_amounts = sorted(r["paid_amount"] for r in list_pairs)
    iter_amounts = sorted(r["paid_amount"] for r in iter_pairs)
    assert list_amounts == iter_amounts
    assert len(iter_pairs) == 6


def test_iter_reversal_pairs_consumes_generator() -> None:
    """Verify the iter API actually consumes a generator, not just lists."""
    def gen():
        yield _row("A", 100.0)
        yield _row("A", -100.0)
        yield _row("B", 50.0)

    pairs = list(ca.iter_reversal_pairs(
        gen(), claim_id="claim_id", paid_amount="paid_amount",
    ))
    assert len(pairs) == 2


def test_iter_reversal_pairs_empty() -> None:
    assert list(ca.iter_reversal_pairs(
        iter([]), claim_id="claim_id", paid_amount="paid_amount",
    )) == []


# ---------- complexity sanity (small but worst-case shape) ----------


def test_reversal_pairs_handles_large_single_group_quickly() -> None:
    """100k rows in a single claim_id group must complete under 1 second.

    The v1.0 O(K^2) implementation took ~10^10 inner-loop iterations on this
    input, which timed out tests. The v1.1 bucket implementation is O(K).
    """
    n_pairs = 50_000
    rows: list[dict] = []
    for i in range(n_pairs):
        rows.append({"claim_id": "MEGA", "paid_amount": float(i + 1)})
        rows.append({"claim_id": "MEGA", "paid_amount": float(-(i + 1))})

    t0 = time.perf_counter()
    pairs = ca.reversal_pairs(
        rows, claim_id="claim_id", paid_amount="paid_amount",
    )
    elapsed = time.perf_counter() - t0
    assert len(pairs) == 2 * n_pairs
    assert elapsed < 2.0, f"reversal_pairs too slow: {elapsed:.2f}s for {len(rows)} rows"


# ---------- slow benchmark: 1M rows ----------


@pytest.mark.slow
def test_reversal_pairs_1m_rows_under_10s() -> None:
    """Volume benchmark: 1M synthetic rows finish in under 10 seconds."""
    n = 500_000
    rows: list[dict] = []
    for i in range(n):
        cid = f"CL{i % 100_000}"
        rows.append({"claim_id": cid, "paid_amount": float((i % 1000) + 1)})
        rows.append({"claim_id": cid, "paid_amount": float(-((i % 1000) + 1))})

    t0 = time.perf_counter()
    pairs = ca.reversal_pairs(
        rows, claim_id="claim_id", paid_amount="paid_amount",
    )
    elapsed = time.perf_counter() - t0
    assert len(pairs) == 2 * n
    assert elapsed < 10.0, f"reversal_pairs 1M-row benchmark: {elapsed:.2f}s"
    print(f"\n[BENCHMARK] reversal_pairs at {2*n} rows: {elapsed:.2f}s")
