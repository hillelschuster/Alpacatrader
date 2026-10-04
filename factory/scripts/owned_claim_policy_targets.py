#!/usr/bin/env python3
"""Chronological whole-policy WAIT targets for the owned-claim policy_return method.

The learned stopping policy is re-evaluated at every *emitted* event in claim
`sequence` order, exactly like ``owned_claim_replay.simulate``: at the first event
whose previous-round value is negative the sale executes at THAT event's own
``sell_et``/``sell_px``; if no event releases, the calendar terminal is a forced
liquidation at its own execution. The label for a decision is
``(chosen_policy_liquidation_price - current_liquidation_price) / original_fill_px``,
on the same fixed original-share basis as the inherited value iteration.

Why this replaces the strict next-liquidation walk
--------------------------------------------------
* ``next_sequence`` deliberately points only at a strictly later *executable* open,
  so it skips emitted events whose open does not move later. A decided release at
  such an event was invisible and the walk continued to a later print. Audit
  scenario: rows A(t600,open600,px10) -> B(t605,open620,px10) positive ->
  C(t614,open620,px10) negative -> E(t625,open625) -> terminal(px12). Chronological
  replay releases at C, reprinting the same opening as B, so the correct incremental
  is 0; the strict pointer walk gives A->B->E->terminal = +0.20.
* Outcome ``status`` was frozen into the fit mask before the policy iterations, so a
  learned early stop with a finite sale *before* an unknown/bad terminal was censored.
  Availability here depends only on the chosen actual liquidation, never on an unused
  suffix.

Guarantees
----------
* Current liquidation and chosen-policy liquidation MUST both be finite, positive and
  in-session. An UNKNOWN chosen exit excludes the row; it is never turned into 0.
* The full-session hold (iteration 0) is an initialization ruler only and is produced
  by the all-zero first-round prediction; it is never the resulting policy.
* Fixed-horizon V15/V30/V60/V120 labels are untouched by this module.
* TRAIN-only: this module takes arrays and performs no I/O and no data access.
"""

from __future__ import annotations

import numpy as np

__all__ = ["chronological_successor", "policy_wait_targets", "target_support"]


def chronological_successor(sequence: np.ndarray, group: np.ndarray) -> np.ndarray:
    """Row index of each row's next emitted event (same claim, ``sequence+1``), else -1.

    ``group`` holds an equal value for every row of one claim; rows MUST be sorted so a
    claim is contiguous and ``sequence`` is 0-based monotone (the event producer
    guarantees this). This is the causal observation order used by the replay, not the
    strict ``next_sequence`` liquidation pointer.
    """
    sequence = np.asarray(sequence, dtype=np.int64)
    group = np.asarray(group, dtype=np.int64)
    if sequence.ndim != 1 or group.shape != sequence.shape:
        raise ValueError("chronological_successor expects 1-D sequence/group of equal length")
    n = sequence.size
    successor = np.full(n, -1, dtype=np.int64)
    if n > 1:
        same_claim = group[1:] == group[:-1]
        next_seq = sequence[1:] == sequence[:-1] + 1
        idx = np.flatnonzero(same_claim & next_seq)
        successor[idx] = idx + 1
    return successor


def policy_wait_targets(
    price: np.ndarray,
    entry: np.ndarray,
    executable: np.ndarray,
    successor: np.ndarray,
    prediction: np.ndarray,
    iteration: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Realized liquidation of the previous round's policy one event after each row.

    Parameters (all aligned per row, TRAIN only):
      price       current executable liquidation ``sell_px`` for this event.
      entry       original ``fill_px`` (original gross-entry notional basis).
      executable  True iff this event's own sale is finite, positive and in-session.
      successor   next emitted event index (``chronological_successor``); -1 = last
                  event of the claim (the calendar terminal).
      prediction  previous round's per-row value; ``< 0`` releases.
      iteration   round index; 0 is the full-session hold ruler (never releases).

    Returns ``(destination, known, exit_index)``:
      destination  row index of the realized sale for the WAIT action, or -1 when the
                   chosen liquidation is UNKNOWN.
      known        True iff the row may be fit: it has a later emitted observation, its
                   current print is executable and the chosen exit is executable.
      exit_index   policy liquidation per row (used only for diagnostics).
    """
    price = np.asarray(price, dtype=np.float64)
    entry = np.asarray(entry, dtype=np.float64)
    executable = np.asarray(executable, dtype=bool)
    successor = np.asarray(successor, dtype=np.int64)
    prediction = np.asarray(prediction, dtype=np.float64)
    n = price.size
    if not (entry.shape == executable.shape == successor.shape == prediction.shape == price.shape):
        raise ValueError("policy_wait_targets expects equal-length aligned arrays")
    if not (successor < n).all() or (successor < -1).any():
        raise ValueError("successor out of range")

    # A row exits when the calendar terminal is reached (no successor) or the previous
    # round releases. UNKNOWN execution at that point propagates as UNKNOWN (-1) rather
    # than being silently zeroed; that is exactly the incomplete-cashflow case.
    exit_index = np.full(n, -1, dtype=np.int64)
    for i in range(n - 1, -1, -1):
        s = successor[i]
        release = iteration > 0 and np.isfinite(prediction[i]) and prediction[i] < 0
        if s < 0 or release:
            exit_index[i] = i if executable[i] else -1
        else:
            exit_index[i] = exit_index[s]

    current_ok = executable & np.isfinite(entry) & (entry > 0)
    has_successor = successor >= 0
    destination = np.full(n, -1, dtype=np.int64)
    destination[has_successor] = exit_index[successor[has_successor]]
    known = has_successor & current_ok & (destination >= 0)
    return destination, known, exit_index


def target_support(
    successor: np.ndarray,
    executable: np.ndarray,
    entry: np.ndarray,
    destination: np.ndarray,
    known: np.ndarray,
) -> dict:
    """Counts of rows by availability reason, for per-iteration support diagnostics."""
    successor = np.asarray(successor, dtype=np.int64)
    executable = np.asarray(executable, dtype=bool)
    entry = np.asarray(entry, dtype=np.float64)
    known = np.asarray(known, dtype=bool)
    has_successor = successor >= 0
    entry_ok = np.isfinite(entry) & (entry > 0)
    current_ok = executable & entry_ok
    return {
        "rows": int(successor.size),
        "known_fit": int(known.sum()),
        "no_successor_terminal": int((~has_successor).sum()),
        "current_not_executable": int((has_successor & ~executable).sum()),
        "entry_not_positive": int((has_successor & executable & ~entry_ok).sum()),
        "chosen_exit_unknown": int((has_successor & current_ok & (destination < 0)).sum()),
    }
