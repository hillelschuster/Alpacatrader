"""CV01 Experiment B — ownership optionality: retain-through vs sell-then-reclaim-reenter.

Dev-only anatomy of the *existing* first causal give-back:10 release (v2 semantics, exactly
``basket_atlas_ledger.Giveback.locate_exit``): the first completed bar at/after the entry bar whose
close is >=10% below the running high *as of that bar*, scanned from bar index 1, preempted by the
engine's forced flat on any bar with ``et >= session_end - 1``.  The sale executes at the open of
the next printed bar (which may be the session_end bar itself when the tape gaps: a genuine firing,
just not reenterable).

Paired branches, over every subsequent observed executable minute endpoint:

    A (retain)   : NEVER executes the GB10 sale.  Hold the already-owned q=1 from entry through the
                   endpoint: W_A(e) = P_open(e) * (1-s).  W_exit = P_exit*(1-s) is the shared
                   numeraire (q=1), not an A trade.
    B (own-again): execute the GB10 sale at P_exit, wait in cash (0% carry), buy back at the open of
                   the bar after the first completed post-sale bar with close >= gross P_exit
                   (decision bar et <= session_end-2, buy bar et < session_end, strictly later than
                   the sale), q_new = W_exit / (P_reentry * (1+s)), then hold to the same endpoint
                                                              W_B(e) = q_new * P_open(e) * (1-s)

Relative to A, B is exactly ONE extra sell/buy round trip k = (1-s)/(1+s) = 0.995/1.005; the
original entry fee is common/sunk and is never recharged or double counted.  Both branches are
normalised by W_exit so A == B == 1 at the sale endpoint and B is never given a fabricated terminal
return.  One sale and at most one reentry: not a recurring policy, no peak/horizon/stop/size is
optimised.

Censoring: the causal risk set is never filtered on future labels.  A member whose sale executed is
kept with its observed partial tape; its later wealth is *unknown*, not zero.  The 273 censored
members enter curves only through observed minutes, and every aggregate is published for both
denominators (all observed tapes / complete tapes only) plus the whole-basket census.

Reported as anatomy only: no optimal value, no alpha pass, no policy claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
from pathlib import Path

import numpy as np
import polars as pl

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

# The shared (mounted) data root is the canonical checkout's data dir; the worktree's own data/ is a
# local stub.  Resolve the base there first, then in the worktree, then fall back to the ticket path.
CANON_BT = Path("/home/hillel/projects/Alpacatrader/data/atlas/continuation/cv01")
WT_BT = WT / "data/atlas/continuation/cv01"
DEFAULT_EVIDENCE = WT / "factory/artifacts/basket/phase2/ATLAS/CV01/B"
DEFAULT_LEDGER = WT / "factory/artifacts/basket/phase2/ATLAS/ledger_selftest.json"
CONTRACT = WT / "factory/artifacts/basket/phase2/ATLAS/CV01/contract.json"

SIDE = 0.005                 # 50bp per side friction (contract side_friction_bps=50)
GIVEBACK_PCT = 10.0          # GB10
GIVEBACK_TOL_REL = 1e-9      # producer's exact tolerance
CASH_FRAC = 1.0 - SIDE
FRIC_K = (1.0 - SIDE) / (1.0 + SIDE)

NEED_ST = ("member_id", "bar_index", "et", "bar_close", "running_high", "entry_et", "session_end",
           "block", "month", "family", "sleeve_day", "entry_rank", "ticker")
NEED_EX = ("member_id", "bar_index", "bar_open", "next_open", "next_et", "has_next_open")
NEED_MB = ("member_id", "n_bars", "observed_last_et", "past_entry_px", "terminal_censored",
           "path_complete_to_session_end", "future_member_last_et", "future_forced_flat_px",
           "session_peak_et", "session_peak_ret_from_entry", "session_close_ret_from_entry",
           "tail_class_50", "tail_class_100", "tail_class_300", "giveback_fired_10")

S_HOLD = "no_trigger_hold_to_forced_flat"
S_PREEMPT = "no_trigger_preempted_by_forced_flat"
S_EVENT = "event_sale_executed"
S_UNRESOLVED = "event_unresolved_no_next_open"
S_SINGLE_BAR = "single_bar_tape"

HEADLINE_H = (0, 1, 3, 5, 15, 30, 60, 120, 240)
CLOCKS = ("h_from_decision", "h_from_exit")
BRANCHES = ("A_retain", "B_sell_reenter")


def _resolve_base(explicit: "Path | None") -> Path:
    cands = ([explicit] if explicit is not None else []) + [CANON_BT / "base", WT_BT / "base"]
    for c in cands:
        if c is not None and (c / "states.parquet").exists():
            return c
    return cands[0]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gb_mask(closes: np.ndarray, runhigh: np.ndarray, pct: float = GIVEBACK_PCT) -> np.ndarray:
    """Producer-exact GB hit mask: close <= running_high*(1-g) + 1e-9*running_high."""
    c = np.asarray(closes, dtype=float)
    rh = np.asarray(runhigh, dtype=float)
    g = pct / 100.0
    ok = np.isfinite(c) & np.isfinite(rh)
    hit = np.zeros(c.shape, dtype=bool)
    hit[ok] = c[ok] <= rh[ok] * (1.0 - g) + GIVEBACK_TOL_REL * rh[ok]
    return hit


def locate_exit(et: np.ndarray, closes: np.ndarray, runhigh: np.ndarray, session_end: int,
                pct: float = GIVEBACK_PCT) -> "tuple[int, int]":
    """(trigger_row, exit_row) under the ledger's ``Giveback.locate_exit``; (-1, -1) otherwise."""
    n = len(et)
    if n < 2:
        return -1, -1
    hits = np.flatnonzero(gb_mask(closes, runhigh, pct)[1:])
    if hits.size == 0:
        return -1, -1
    t = int(hits[0]) + 1
    if int(et[t]) >= int(session_end) - 1:
        return -1, -1
    j = t + 1
    if j >= n:
        return -1, -1
    return t, j


def classify(et: np.ndarray, closes: np.ndarray, runhigh: np.ndarray, session_end: int) -> dict:
    """The 4-way causal GB10 census read: hold / preempted / event / unresolved.

    Past-clock only: the first completed bar after the fill row whose close is >=10% below its own
    running high.  Forced flat preempts any trigger at ``et >= session_end - 1``; a trigger on a bar
    with no next observed open is an *unresolved* exit (detected, not a no-event).  The trigger row
    is reported for every non-hold status, including preempted/unresolved ones.
    """
    n = len(et)
    if n < 2:
        return {"status": S_SINGLE_BAR, "trigger": -1, "exit": -1}
    hits = np.flatnonzero(gb_mask(closes, runhigh)[1:])
    if hits.size == 0:
        return {"status": S_HOLD, "trigger": -1, "exit": -1}
    t = int(hits[0]) + 1
    if int(et[t]) >= int(session_end) - 1:
        return {"status": S_PREEMPT, "trigger": t, "exit": -1}
    if t + 1 >= n:
        return {"status": S_UNRESOLVED, "trigger": t, "exit": -1}
    return {"status": S_EVENT, "trigger": t, "exit": t + 1}


def detect_member(et: np.ndarray, closes: np.ndarray, runhigh: np.ndarray, opens: np.ndarray,
                  session_end: int) -> dict:
    """Causal GB10 read + paired-branch resolution for one member's observed tape."""
    n = len(et)
    out: dict = {"n_bars": n, "tape_last_et": int(et[-1]) if n else None,
                 "trigger_bar_index": None, "trigger_et": None, "trigger_decision_et": None,
                 "exit_bar_index": None, "exit_et": None, "exit_px": None,
                 "exit_is_session_end_bar": None, "reclaim_bar_index": None, "reclaim_et": None,
                 "reclaim_close": None, "reentry_bar_index": None, "reentry_et": None,
                 "reentry_px": None, "recovery_delay_min": None, "missed_gross_move": None,
                 "has_reentry": False, "endpoint_bar_index": None, "endpoint_et": None,
                 "w_exit": None, "a_final_norm": None, "b_final_norm": None, "delta_norm": None,
                 "reclaim_blocked_reason": None}
    cls = classify(et, closes, runhigh, session_end)
    out["gb_status"] = cls["status"]
    if cls["status"] != S_EVENT:
        out["trigger_bar_index"] = cls["trigger"] if cls["trigger"] >= 0 else None
        if cls["trigger"] >= 0:
            out["trigger_et"] = int(et[cls["trigger"]])
            out["trigger_decision_et"] = int(et[cls["trigger"]]) + 1
            out["exit_px"] = None
        return out
    t, j = cls["trigger"], cls["exit"]
    se = int(session_end)
    exit_px = float(opens[j])
    out.update({"trigger_bar_index": t, "trigger_et": int(et[t]),
                "trigger_decision_et": int(et[t]) + 1, "exit_bar_index": j,
                "exit_et": int(et[j]), "exit_px": exit_px,
                "exit_is_session_end_bar": bool(int(et[j]) >= se),
                "w_exit": exit_px * CASH_FRAC})

    # ---- reclaim: first completed bar at/after the sale whose close >= GROSS exit px ---------- #
    k = r = None
    blocked = None
    for idx in range(j, n):
        if int(et[idx]) >= se - 1:
            blocked = "session_boundary_preempts_decision"
            break
        if closes[idx] >= exit_px:
            r = idx
            break
    if r is not None:
        nxt = r + 1
        if nxt >= n:
            blocked = "no_next_open_after_reclaim"
        elif int(et[nxt]) >= se:
            blocked = "buy_would_be_at_or_after_session_end"
        else:
            k = nxt
    elif blocked is None:
        blocked = "no_reclaim_before_session_boundary"
    out["reclaim_blocked_reason"] = blocked
    if r is not None:
        out.update({"reclaim_bar_index": r, "reclaim_et": int(et[r]),
                    "reclaim_close": float(closes[r])})
    if k is not None:
        reentry_px = float(opens[k])
        out.update({"reentry_bar_index": k, "reentry_et": int(et[k]), "reentry_px": reentry_px,
                    "has_reentry": True, "recovery_delay_min": int(et[k]) - int(et[j]),
                    "missed_gross_move": reentry_px / exit_px - 1.0})

    # ---- common-endpoint paired curves over every subsequent observed minute ------------------ #
    idx = np.arange(j, n)
    e_et = et[idx].astype(np.int64)
    p = opens[idx].astype(float)
    a_norm = p / exit_px
    b_norm = np.ones(idx.shape, dtype=float) if k is None else np.where(
        idx < k, 1.0, FRIC_K * p / float(opens[k]))
    out["curve"] = {"h_from_decision": (e_et - (int(et[t]) + 1)).astype(np.int32),
                    "h_from_exit": (e_et - int(et[j])).astype(np.int32),
                    "a_norm": a_norm, "b_norm": b_norm}
    last = n - 1
    out.update({"endpoint_bar_index": last, "endpoint_et": int(et[last]),
                "a_final_norm": float(p[-1] / exit_px),
                "b_final_norm": float(1.0 if k is None or last < k
                                      else FRIC_K * p[-1] / float(opens[k]))})
    out["delta_norm"] = out["b_final_norm"] - out["a_final_norm"]
    return out


def support_by_horizon(anchors: np.ndarray, ses: np.ndarray, lasts: np.ndarray,
                       priced_mi: np.ndarray, priced_h: np.ndarray) -> pl.DataFrame:
    """Horizon support disclosure for the sale anchor (decision clock) on the event set.

    For every integer minute h after the anchor label:
      n_at_risk_within_session   : anchor+h is still inside the member's session window.
      n_priced                   : an observed executable bar exists at exactly that label.
      n_missing_inside_tape      : inside the session AND inside the observed tape, but no print
                                   (a gap) -> the price is unknown, never zero.
      n_unknown_after_tape_tail  : inside the session but past the member's observed tape end
                                   (censored tape) -> unknown, never zero.
      n_outside_session          : anchor+h is past session_end: NOT at risk and NOT unknown.
    `invalid` flags a row where the three inside-session buckets do not reconcile with the risk set.
    """
    if anchors.size == 0:
        return pl.DataFrame(schema={"clock": pl.String, "h": pl.Int32,
                                    "n_at_risk_within_session": pl.Int64, "n_priced": pl.Int64,
                                    "n_missing_inside_observed_tape": pl.Int64,
                                    "n_unknown_after_observed_tail": pl.Int64,
                                    "n_outside_session": pl.Int64, "invalid": pl.Boolean}), \
            {"members_checked": 0, "members_priced_set_mismatch": 0,
             "partition_recon_failures": 0, "hmax": 0, "priced_pairs": 0,
             "note": "no event members"}
    hmax = int(max(0, (ses - anchors).max()))
    h = np.arange(hmax + 1, dtype=np.int64)
    tgt = anchors[:, None] + h[None, :]
    within = tgt <= ses[:, None]
    inside_tape = tgt <= lasts[:, None]
    n_members = anchors.size
    priced = np.zeros((n_members, hmax + 1), dtype=bool)
    if priced_mi.size:
        keep = (priced_h >= 0) & (priced_h <= hmax)
        priced[priced_mi[keep], priced_h[keep]] = True
    priced &= within
    missing = within & inside_tape & ~priced
    unknown = within & ~inside_tape & ~priced
    outside = ~within
    at_risk = within.sum(axis=0)
    n_priced = priced.sum(axis=0)
    n_missing = missing.sum(axis=0)
    n_unknown = unknown.sum(axis=0)
    # non-tautological audit: each member's priced h-set must equal that member's actual observed
    # endpoint offsets from its own anchor (catches any lattice/member mis-assignment)
    bad_member = np.zeros(n_members, dtype=bool)
    for k in range(n_members):
        exp = np.sort(priced_h[priced_mi == k].astype(np.int64))
        got = np.flatnonzero(priced[k])
        if exp.shape != got.shape or not np.array_equal(exp, got):
            bad_member[k] = True
    bad_h = np.zeros(hmax + 1, dtype=bool)
    if bad_member.any():
        for k in np.flatnonzero(bad_member):
            exp = np.sort(priced_h[priced_mi == k].astype(np.int64))
            exp = exp[(exp >= 0) & (exp <= hmax)]
            bad_h[exp] = True
    recon = at_risk == (n_priced + n_missing + n_unknown)
    return pl.DataFrame({
        "clock": ["h_from_decision"] * (hmax + 1), "h": h.astype(np.int32),
        "n_at_risk_within_session": at_risk.astype(np.int64),
        "n_priced": n_priced.astype(np.int64),
        "n_missing_inside_observed_tape": n_missing.astype(np.int64),
        "n_unknown_after_observed_tail": n_unknown.astype(np.int64),
        "n_outside_session": outside.sum(axis=0).astype(np.int64),
        "invalid": (~recon) | bad_h}), {
        "members_checked": int(n_members), "members_priced_set_mismatch": int(bad_member.sum()),
        "partition_recon_failures": int((~recon).sum()),
        "hmax": int(hmax), "priced_pairs": int(priced.sum()),
        "note": ("each event member's priced h-set is compared against its own observed endpoint "
                 "offsets; mismatch 0 is required and makes `invalid` non-tautological")}


# --------------------------------------------------------------------------- #
def _schema(path: Path) -> set:
    return set(pl.read_parquet_schema(path))


def load_base(base: Path):
    st = pl.read_parquet(base / "states.parquet",
                         columns=[c for c in NEED_ST if c in _schema(base / "states.parquet")])
    ex = pl.read_parquet(base / "executions.parquet",
                         columns=[c for c in NEED_EX if c in _schema(base / "executions.parquet")])
    mb = pl.read_parquet(base / "members.parquet",
                         columns=[c for c in NEED_MB if c in _schema(base / "members.parquet")])
    return st, ex, mb


def run(base: Path):
    st, ex, mb = load_base(base)
    j = st.join(ex, on=["member_id", "bar_index"], how="inner").sort(["member_id", "bar_index"])
    linkage = {"states_rows": st.height, "executions_rows": ex.height, "joined_rows": j.height,
               "states_rows_without_price": st.height - j.height}
    del st, ex

    # member boundaries from the sorted frame (one contiguous run per member)
    starts = j.select(pl.col("member_id").ne(pl.col("member_id").shift(1)).fill_null(True)
                      .alias("s"))["s"].to_numpy()
    bounds = np.flatnonzero(starts)
    ends = np.append(bounds[1:], j.height)
    bl = bounds.tolist()
    ids = j["member_id"].gather(bl).to_list()

    def first(col):
        return j[col].gather(bl).to_list()
    block_l, month_l, family_l = first("block"), first("month"), first("family")
    sleeve_l, rank_l, ticker_l = first("sleeve_day"), first("entry_rank"), first("ticker")
    entry_et_l, se_l = first("entry_et"), first("session_end")
    arr = {c: j[c].to_numpy() for c in ("et", "bar_close", "running_high", "bar_open",
                                        "next_open", "next_et", "has_next_open")}
    del j

    try:
        from factory.scripts.basket_atlas_ledger import Giveback
        ledger_rule = Giveback(name="giveback:10", kind="giveback", params={"pct": GIVEBACK_PCT})
        ledger_rec = {"available": True, "members": 0, "locator_agree": 0, "locator_disagree": 0,
                      "locator_fired_members": 0, "preempted_members": 0, "unresolved_members": 0}
    except Exception as exc:                                        # pragma: no cover
        ledger_rule, ledger_rec = None, {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    rows, cv = [], {"member_idx": [], "h_from_decision": [], "h_from_exit": [], "a_norm": [],
                    "b_norm": []}
    anchors, ses_e, lasts, priced_idx, priced_h = [], [], [], [], []
    giant_code_l = np.full(len(bounds), -1, dtype=np.int16)
    mb_map = {r["member_id"]: r for r in mb.iter_rows(named=True)}
    monster_code_l = np.full(len(bounds), -1, dtype=np.int16)
    for mi in range(len(bounds)):
        row = mb_map.get(ids[mi], {})
        t50, t100, t300 = (row.get("tail_class_50"), row.get("tail_class_100"),
                           row.get("tail_class_300"))
        if t300 is True:
            monster_code_l[mi] = 3
        elif t100 is True:
            monster_code_l[mi] = 2
        elif t50 is True:
            monster_code_l[mi] = 1
        elif t50 is False:
            monster_code_l[mi] = 0
    audit = {"next_open_null_mismatch": 0, "next_open_px_mismatch": 0, "next_et_mismatch": 0}
    for mi in range(len(bounds)):
        s, e = int(bounds[mi]), int(ends[mi])
        et = arr["et"][s:e].astype(np.int64)
        closes = arr["bar_close"][s:e].astype(float)
        rh = arr["running_high"][s:e].astype(float)
        op = arr["bar_open"][s:e].astype(float)
        se = int(se_l[mi])
        # execution audit: next_open[t] == bar_open[t+1] on the observed tape
        no = arr["next_open"][s:e]
        ne = arr["next_et"][s:e]
        hn = arr["has_next_open"][s:e]
        if e - s > 1:
            audit["next_open_null_mismatch"] += int(
                (np.isnan(no[:-1]) != np.isnan(op[1:])).sum())
            audit["next_open_px_mismatch"] += int(
                (np.abs(np.nan_to_num(no[:-1]) - op[1:]) > 1e-12).sum())
            audit["next_et_mismatch"] += int((np.nan_to_num(ne[:-1], nan=-1)
                                              != et[1:]).sum())
        audit.setdefault("has_next_flag_mismatch", 0)
        if e - s > 1:
            audit["has_next_flag_mismatch"] += int((hn[:-1] != True).sum())  # noqa: E712

        d = detect_member(et, closes, rh, op, se)
        if ledger_rule is not None:
            lj = ledger_rule.locate_exit({"et": et, "bar_close": closes, "running_high": rh,
                                          "session_end": np.array([se], dtype=np.int64)})
            t_l, j_l = locate_exit(et, closes, rh, se)
            ledger_rec["members"] += 1
            ledger_rec["locator_fired_members"] += int(lj >= 0)
            if d["gb_status"] == S_PREEMPT:
                ledger_rec["preempted_members"] += 1
            if d["gb_status"] == S_UNRESOLVED:
                ledger_rec["unresolved_members"] += 1
            if (lj >= 0) == (j_l >= 0) and (lj < 0 or lj == j_l):
                ledger_rec["locator_agree"] += 1
        c = d.pop("curve", None)
        if c is not None:
            cv["member_idx"].append(np.full(c["a_norm"].shape, mi, dtype=np.int32))
            cv["h_from_decision"].append(c["h_from_decision"])
            cv["h_from_exit"].append(c["h_from_exit"])
            cv["a_norm"].append(c["a_norm"])
            cv["b_norm"].append(c["b_norm"])
            pos = len(anchors)
            anchors.append(d["trigger_decision_et"])
            ses_e.append(se)
            lasts.append(int(et[-1]))
            priced_idx.append(np.full(c["h_from_decision"].shape, pos, dtype=np.int32))
            priced_h.append(c["h_from_decision"].astype(np.int64))
        d.update({"member_id": ids[mi], "block": block_l[mi], "month": month_l[mi],
                  "family": family_l[mi], "sleeve_day": sleeve_l[mi],
                  "entry_rank": int(rank_l[mi]), "ticker": ticker_l[mi],
                  "entry_et": int(entry_et_l[mi]), "session_end": se})
        # attribution-only class: the member's own OBSERVED endpoint move from the exit, computed
        # only on complete tapes (a truncated tape cannot classify a giant; code -1 = unknown)
        if (d["gb_status"] == S_EVENT and d["a_final_norm"] is not None
                and bool(mb_map[ids[mi]].get("path_complete_to_session_end"))):
            amv = float(d["a_final_norm"]) - 1.0
            giant_code_l[mi] = 3 if amv >= 3.0 else (2 if amv >= 1.0 else (1 if amv >= 0.5 else 0))
        d["giant_from_exit_code"] = int(giant_code_l[mi])
        rows.append(d)
    if ledger_rule is not None:
        ledger_rec["locator_disagree"] = ledger_rec["members"] - ledger_rec["locator_agree"]
    linkage.update(audit)

    ev = pl.DataFrame(rows)
    lab = mb.select(["member_id"] + [c for c in NEED_MB[1:] if c in mb.columns])
    ev = ev.join(lab, on=["member_id"], how="left")
    ev = ev.with_columns((pl.col("gb_status") == S_EVENT).alias("gb_fired_mine"))
    if "giveback_fired_10" in ev.columns:
        ev = ev.with_columns((pl.col("gb_fired_mine") == pl.col("giveback_fired_10"))
                             .alias("label_reconciled"))
    if cv["member_idx"]:
        curves = pl.DataFrame({
            "member_idx": np.concatenate(cv["member_idx"]),
            "h_from_decision": np.concatenate(cv["h_from_decision"]),
            "h_from_exit": np.concatenate(cv["h_from_exit"]),
            "a_norm": np.concatenate(cv["a_norm"]),
            "b_norm": np.concatenate(cv["b_norm"])})
    else:
        curves = pl.DataFrame(schema={"member_idx": pl.Int32, "h_from_decision": pl.Int32,
                                     "h_from_exit": pl.Int32, "a_norm": pl.Float64,
                                     "b_norm": pl.Float64})
    fam_names = sorted(set(family_l))
    block_names = sorted(set(block_l))
    day_names = sorted(set(sleeve_l))
    month_names = sorted(set(month_l))
    fam_code = {f: i for i, f in enumerate(fam_names)}
    block_code = {b: i for i, b in enumerate(block_names)}
    day_code = {d: i for i, d in enumerate(day_names)}
    month_code = {m: i for i, m in enumerate(month_names)}
    lookup = pl.DataFrame({
        "member_idx": np.arange(len(bounds), dtype=np.int32),
        "family_code": np.array([fam_code[f] for f in family_l], dtype=np.int16),
        "block_code": np.array([block_code[b] for b in block_l], dtype=np.int16),
        "day_code": np.array([day_code[d] for d in sleeve_l], dtype=np.int32),
        "month_code": np.array([month_code[m] for m in month_l], dtype=np.int16),
        "giant_code": giant_code_l,
        "monster_code": monster_code_l,
        "tape_complete": ev["path_complete_to_session_end"].fill_null(False).to_list()})
    curves = curves.join(lookup, on="member_idx", how="left")
    support, support_audit = support_by_horizon(
        np.asarray(anchors, dtype=np.int64), np.asarray(ses_e, dtype=np.int64),
        np.asarray(lasts, dtype=np.int64),
        np.concatenate(priced_idx) if priced_idx else np.array([], dtype=np.int32),
        np.concatenate(priced_h) if priced_h else np.array([], dtype=np.int64))
    return ev, curves, (fam_names, block_names), support, support_audit, linkage, ledger_rec


def legacy_reconcile(ev: pl.DataFrame, path: Path) -> dict:
    """Reconcile this read's GB10 trigger/exit census against the legacy ledger artifact.

    Legacy basis: ``ledger_selftest.json -> real_panel.blocks['giveback:10']``, i.e. the per-member
    early-exit census of the original v2 ledger on *complete* tapes.  The ledger's EOD forced-flat
    primary is a holding baseline, never the event definition, so it is not used here.
    """
    if not path.exists():
        return {"available": False, "path": str(path)}
    blob = json.loads(path.read_text())
    try:
        g = blob["real_panel"]["blocks"]["giveback:10"]
    except (KeyError, TypeError):
        return {"available": False, "path": str(path), "reason": "no giveback:10 real-panel block"}
    evt = ev.filter(pl.col("gb_status") == S_EVENT)
    complete = evt.filter(pl.col("path_complete_to_session_end").fill_null(False))
    by_fam = {str(k): int(v["n_early_exits"]) for k, v in
              (g.get("by_family_total") or {}).items() if "n_early_exits" in v}
    by_blk = {str(k): int(v["n_early_exits"]) for k, v in (g.get("blocks") or {}).items()
              if "n_early_exits" in v}
    views = g.get("accounting_views") or {}
    total_exp = (views.get("sleeve_accounting") or {}).get("n_early_exits")
    if total_exp is None:
        total_exp = sum(by_fam.values())
    act_fam = {str(r["family"]): r["n"] for r in
               complete.group_by("family").agg(pl.len().alias("n")).iter_rows(named=True)}
    act_blk = {str(r["block"]): r["n"] for r in
               complete.group_by("block").agg(pl.len().alias("n")).iter_rows(named=True)}
    judged_actual = int(ev["label_reconciled"].is_not_null().sum()) \
        if "label_reconciled" in ev.columns else None
    cens_actual = int((ev["terminal_censored"] == True).sum())  # noqa: E712
    checks = {
        "n_early_exits_complete_tapes": {"expected": int(total_exp), "actual": complete.height},
        "n_members_judged": {"expected": int(g.get("n_members_judged", -1)),
                             "actual": judged_actual},
        "n_members_unresolved_censored": {
            "expected": int(g.get("n_members_unresolved_censored", -1)), "actual": cens_actual},
        "by_family_early_exits": {"expected": by_fam, "actual": act_fam},
        "by_block_early_exits": {"expected": by_blk, "actual": act_blk},
    }
    ok = (checks["n_early_exits_complete_tapes"]["expected"]
          == checks["n_early_exits_complete_tapes"]["actual"]
          and checks["n_members_judged"]["expected"] == judged_actual
          and checks["n_members_unresolved_censored"]["expected"] == cens_actual
          and by_fam == act_fam and by_blk == act_blk)
    return {"available": True, "path": str(path.relative_to(WT)), "sha256": sha256_file(path),
            "basis": ("legacy ledger v2 giveback:10 early exits on complete tapes; the EOD "
                      "forced-flat primary is not used as the event definition"),
            "checks": checks, "clean": bool(ok)}


# --------------------------------------------------------------------------- #
def _stats(col: str) -> list:
    c = pl.col(col)
    return [("median", c.median()), ("q10", c.quantile(0.10)), ("q25", c.quantile(0.25)),
            ("q75", c.quantile(0.75)), ("q90", c.quantile(0.90)), ("q99", c.quantile(0.99)),
            ("min", c.min()), ("max", c.max())]


_METRICS = ["mean", "median", "q10", "q25", "q75", "q90", "q99", "min", "max", "se", "se_month",
            "se_diff", "se_diff_month"]


def _cell_stats(c: str, pre: str) -> list:
    """Day x family-cell statistics of one branch's cell means (point estimates)."""
    col = pl.col(c)
    return [col.mean().alias(f"mean_{pre}"), col.median().alias(f"median_{pre}"),
            col.quantile(0.10).alias(f"q10_{pre}"), col.quantile(0.25).alias(f"q25_{pre}"),
            col.quantile(0.75).alias(f"q75_{pre}"), col.quantile(0.90).alias(f"q90_{pre}"),
            col.quantile(0.99).alias(f"q99_{pre}"), col.min().alias(f"min_{pre}"),
            col.max().alias(f"max_{pre}")]


def _cluster_se(level: str) -> list:  # replaced by cluster_se(); kept for call-site clarity
    raise NotImplementedError


def cluster_se(values: np.ndarray, cluster_codes: np.ndarray):
    """Cluster-robust SE OF THE PUBLISHED POINT MEAN (unweighted mean over cells).

    ``values`` are the cell-level values (one per day x family cell), ``cluster_codes`` the cluster
    each cell belongs to (day or month).  With N cells, published mean Ybar and G clusters:

        score_g = SUM_{cells in g} (y_cell - Ybar) / N
        se      = sqrt( G/(G-1) * SUM_g score_g**2 )

    which targets the same estimand as the published point mean (cells weighted equally, unequal
    cluster sizes handled) and reduces to sd(cluster means)/sqrt(G) when cluster sizes are equal.
    Returns (published_mean, se, n_clusters); se is None when G <= 1.
    """
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return None, None, 0
    ybar = float(v.mean())
    codes = np.unique(np.asarray(cluster_codes), return_inverse=True)[1]
    ctr = (v - ybar) / v.size
    sums = np.bincount(codes, weights=ctr)
    g = sums.size
    if g <= 1:
        return ybar, None, int(g)
    return ybar, float(np.sqrt(g / (g - 1.0) * np.sum(sums ** 2))), int(g)


# attribution-only class labels.  The endpoint classes use the member's OWN observed last-minute
# return above the gross exit price; the audit classes are the members file's entry-to-session MFE
# tags (tail_class_50/100/300, measured from the fill row).  Neither is a state or a CV denominator.
ENDPOINT_CLASS = {-1: "exit_ret_unknown_censored_tape",
                  0: "exit_ret_lt_50pct", 1: "exit_ret_50_100pct",
                  2: "exit_ret_100_300pct", 3: "exit_ret_ge_300pct"}
AUDIT_MFE_CLASS = {-1: "entry_mfe_unknown_censored",
                   0: "entry_mfe_lt_50pct", 1: "entry_mfe_50_100pct",
                   2: "entry_mfe_100_300pct", 3: "entry_mfe_ge_300pct"}


def _split_branch(frame: pl.DataFrame, keys: list, keep: list, extra: list) -> pl.DataFrame:
    """Long (keys, branch, counts, metrics) view of a frame carrying both *_A and *_B statistics."""
    out = []
    for branch in BRANCHES:
        sfx = "_A" if branch == BRANCHES[0] else "_B"
        mets = [m for m in _METRICS if f"{m}{sfx}" in frame.columns]
        out.append(frame.select(keys + [pl.lit(branch).alias("branch")] +
                                [pl.col(k) for k in keep] +
                                [pl.col(f"{m}{sfx}").alias(m) for m in mets] + extra))
    return pl.concat(out, how="vertical")


def curves_table(curves: pl.DataFrame, fam_names, block_names) -> pl.DataFrame:
    """Compact event-conditioned common-endpoint curves: scopes x denominators x 2 clocks x 2
    branches, occupancy- and equal-member-within-day/family-weighted, untrimmed quantiles.

    Both branches are aggregated in the same pass and only the *small* result is unpivoted, so the
    member-minute table is never multiplied by branch or clock.
    """
    def branch_exprs(a: str, b: str) -> list:
        e = []
        for col, pre in ((a, "A"), (b, "B")):
            e.append(pl.col(col).mean().alias(f"mean_{pre}"))
            e.extend(expr.alias(f"{m}_{pre}") for m, expr in _stats(col))
        return e

    frames = []
    for scope_kind, key, names in (
            ("all", None, None), ("family", "family_code", None),
            ("block", "block_code", None),
            ("exit_ret_class", "giant_code", ENDPOINT_CLASS),
            ("audit_entry_mfe_class", "monster_code", AUDIT_MFE_CLASS)):
        for denom, flag in (("all_observed_tape", None), ("complete_tape_only", True)):
            frame = curves if flag is None else curves.filter(pl.col("tape_complete") == flag)
            for clock in CLOCKS:
                keys = [clock] + ([key] if key else [])
                occ = frame.group_by(keys).agg(
                    pl.len().alias("n_obs"), pl.col("tape_complete").sum().alias("n_obs_complete"),
                    *branch_exprs("a_norm", "b_norm"))
                # equal-member-within-day/family: member mean, then the cell (day x family) mean,
                # then an unweighted mean over cells -> a day with more members is one unit
                m1 = frame.group_by(list(dict.fromkeys(
                    keys + ["day_code", "family_code", "member_idx"]))).agg(
                    pl.col("a_norm").mean().alias("a_norm"),
                    pl.col("b_norm").mean().alias("b_norm"),
                    pl.col("tape_complete").max().alias("tape_complete"),
                    pl.col("month_code").first().alias("month_code"))
                cell = m1.group_by(list(dict.fromkeys(
                    keys + ["day_code", "family_code", "month_code"]))).agg(
                    pl.len().alias("n_members"),
                    pl.col("tape_complete").sum().alias("n_members_complete"),
                    pl.col("a_norm").mean().alias("mean_A"),
                    pl.col("b_norm").mean().alias("mean_B"))
                eqm = cell.group_by(keys).agg(
                    pl.len().alias("n_cells"), pl.col("n_members").sum().alias("n_members"),
                    pl.col("n_members_complete").sum().alias("n_members_complete"),
                    *_cell_stats("mean_A", "A"), *_cell_stats("mean_B", "B"))
                # cluster-robust SEs whose estimand is the PUBLISHED cell mean (cells weighted
                # equally); scores are formed around that published mean, so unequal day sizes are
                # handled and equal-sized clusters reduce to sd(cluster means)/sqrt(G)
                se_rows = []
                for kvals, g in cell.group_by(keys, maintain_order=True):
                    kv = kvals if isinstance(kvals, tuple) else (kvals,)
                    a = g["mean_A"].to_numpy()
                    b = g["mean_B"].to_numpy()
                    dv = b - a
                    dayc = g["day_code"].to_numpy()
                    monc = g["month_code"].to_numpy()
                    _, se_a, n_days = cluster_se(a, dayc)
                    _, se_b, _ = cluster_se(b, dayc)
                    _, se_d, _ = cluster_se(dv, dayc)
                    _, se_am, n_mon = cluster_se(a, monc)
                    _, se_bm, _ = cluster_se(b, monc)
                    _, se_dm, _ = cluster_se(dv, monc)
                    se_rows.append({**{k: v for k, v in zip(keys, kv)},
                                    "n_days": n_days, "n_months": n_mon,
                                    "se_A": se_a, "se_B": se_b, "se_diff": se_d,
                                    "se_month_A": se_am, "se_month_B": se_bm,
                                    "se_diff_month": se_dm})
                eqm = eqm.join(pl.DataFrame(se_rows), on=keys, how="left")
                eqm = eqm.with_columns(
                    pl.col("se_diff").alias("se_diff_A"), pl.col("se_diff").alias("se_diff_B"),
                    pl.col("se_diff_month").alias("se_diff_month_A"),
                    pl.col("se_diff_month").alias("se_diff_month_B"))
                eqm = eqm.with_columns(
                    (pl.col("n_members") - pl.col("n_members_complete"))
                    .alias("n_members_censored_partial"))
                occ = occ.with_columns(
                    (pl.col("n_obs") - pl.col("n_obs_complete"))
                    .alias("n_obs_censored_partial"))
                long = pl.concat([
                    _split_branch(occ, keys,
                                  ["n_obs", "n_obs_complete", "n_obs_censored_partial"],
                                  [pl.lit("occupancy").alias("weighting")]),
                    _split_branch(eqm, keys,
                                  ["n_members", "n_members_complete",
                                   "n_members_censored_partial", "n_cells", "n_days",
                                   "n_months"],
                                  [pl.lit("equal_member_day_family").alias("weighting")])],
                    how="diagonal_relaxed")
                long = long.with_columns(
                    pl.lit(scope_kind).alias("scope_kind"),
                    pl.lit(denom).alias("denominator"),
                    pl.lit(clock).alias("clock_f"))
                if key == "family_code":
                    long = long.with_columns(pl.col("family_code")
                                             .replace_strict({i: f for i, f in
                                                              enumerate(fam_names)},
                                                             return_dtype=pl.String)
                                             .alias("scope_value"))
                elif key == "block_code":
                    long = long.with_columns(pl.col("block_code")
                                             .replace_strict({i: b for i, b in
                                                              enumerate(block_names)},
                                                             return_dtype=pl.String)
                                             .alias("scope_value"))
                elif names is not None:
                    long = long.with_columns(pl.col(key)
                                             .replace_strict(names, return_dtype=pl.String)
                                             .alias("scope_value"))
                else:
                    long = long.with_columns(pl.lit("ALL").alias("scope_value"))
                long = long.rename({clock: "h"})
                frames.append(long.select(["scope_kind", "scope_value", "denominator", "weighting",
                                           "branch", "clock_f", "h", "n_obs", "n_obs_complete",
                                           "n_obs_censored_partial", "n_members",
                                           "n_members_complete", "n_members_censored_partial",
                                           "n_cells", "n_days", "n_months", "mean", "median",
                                           "q10", "q25", "q75", "q90", "q99", "min", "max",
                                           "se", "se_month", "se_diff", "se_diff_month"]))
    return pl.concat(frames, how="vertical").rename({"clock_f": "clock"}).sort(
        ["scope_kind", "scope_value", "denominator", "weighting", "branch", "clock", "h"])


def _dist_rows(frame: pl.DataFrame, condition: str, metrics) -> list:
    rows = []
    for scope, key in (("all", None), ("family", "family"), ("block", "block"),
                       ("month", "month")):
        groups = ([("ALL", frame)] if key is None
                  else [(str(k[0] if isinstance(k, tuple) else k), g)
                        for k, g in frame.group_by([key], maintain_order=True)])
        for val, g in groups:
            if g.height == 0:
                continue
            for metric in metrics:
                s = g[metric].drop_nulls()
                if s.len() == 0:
                    continue
                rows.append({
                    "condition": condition, "scope": scope, "scope_value": val, "metric": metric,
                    "n": s.len(), "mean": float(s.mean()), "median": float(s.median()),
                    "q10": float(s.quantile(0.10)), "q25": float(s.quantile(0.25)),
                    "q75": float(s.quantile(0.75)), "q90": float(s.quantile(0.90)),
                    "q99": float(s.quantile(0.99)), "min": float(s.min()), "max": float(s.max()),
                    "sum": float(s.sum()), "sum_pos": float(s.filter(s > 0).sum()),
                    "sum_neg": float(s.filter(s < 0).sum())})
    return rows


def _econ(comp: pl.DataFrame) -> dict:
    """Compact descriptive headline of the paired branches on the member's own last observed
    executable minute (dollars are per $1 of exit value, W_exit normaliser)."""
    d = comp["delta_norm"].drop_nulls()
    rd = comp["recovery_delay_min"].drop_nulls()
    mm = comp["missed_gross_move"].drop_nulls()
    cls = {r["outcome_class"]: r["n"] for r in
           comp.group_by("outcome_class").agg(pl.len().alias("n")).iter_rows(named=True)}
    fam = {}
    famg = comp.group_by("family").agg(
        pl.len().alias("n"), pl.col("delta_norm").mean().alias("dmean"),
        pl.col("delta_norm").median().alias("dmed"), pl.col("delta_norm").sum().alias("dsum"))
    for r in famg.iter_rows(named=True):
        fam[str(r["family"])] = {"n": r["n"], "mean_delta": r["dmean"],
                                 "median_delta": r["dmed"], "sum_delta": r["dsum"]}
    return {
        "endpoint": ("each member's OWN last observed executable minute open (terminal endpoint; "
                     "not EOD, not a peak, not a selected horizon) - the pointwise wealth curves "
                     "in curves.parquet are the primary published object"),
        "n_events": comp.height,
        "n_reentry": int(comp["has_reentry"].fill_null(False).sum()),
        "reentry_share": (float(comp["has_reentry"].fill_null(False).mean())
                          if comp.height else None),
        "delta_norm": {"mean": float(d.mean()), "median": float(d.median()),
                       "q01": float(d.quantile(0.01)), "q05": float(d.quantile(0.05)),
                       "q25": float(d.quantile(0.25)), "q75": float(d.quantile(0.75)),
                       "q95": float(d.quantile(0.95)), "q99": float(d.quantile(0.99)),
                       "min": float(d.min()), "max": float(d.max()), "sum": float(d.sum()),
                       "sum_pos": float(d.filter(d > 0).sum()),
                       "sum_neg": float(d.filter(d < 0).sum()),
                       "share_negative": float((d < 0).mean() if d.len() else None)},
        "recovery_delay_min": {"n": rd.len(), "mean": float(rd.mean()) if rd.len() else None,
                               "median": float(rd.median()) if rd.len() else None,
                               "q90": float(rd.quantile(0.90)) if rd.len() else None,
                               "max": float(rd.max()) if rd.len() else None},
        "missed_gross_move": {"n": mm.len(), "mean": float(mm.mean()) if mm.len() else None,
                             "median": float(mm.median()) if mm.len() else None,
                             "q10": float(mm.quantile(0.10)) if mm.len() else None,
                             "q90": float(mm.quantile(0.90)) if mm.len() else None,
                             "max": float(mm.max()) if mm.len() else None},
        "outcome_class_counts": cls, "by_family": fam,
        "prices_are": "gross open prices; both branches net of the same 50bp side friction"}


def aggregate(ev: pl.DataFrame, curves: pl.DataFrame, names, out: Path) -> dict:
    curve_table = curves_table(curves, names[0], names[1])
    curve_table.write_parquet(out / "curves.parquet")

    final = ev.select(["member_id", "block", "month", "family", "gb_status", "has_reentry",
                       "recovery_delay_min", "missed_gross_move", "a_final_norm", "b_final_norm",
                       "delta_norm", "endpoint_et", "exit_et", "reclaim_et", "reentry_et",
                       "path_complete_to_session_end"] +
                      [c for c in ("terminal_censored", "tail_class_50", "tail_class_100",
                                   "tail_class_300", "future_member_last_et", "past_entry_px")
                       if c in ev.columns])
    evt = final.filter(pl.col("gb_status") == S_EVENT)
    comp = evt.with_columns((pl.col("a_final_norm") - 1.0).alias("a_move_from_exit"))
    comp = comp.with_columns(
        pl.when(pl.col("delta_norm") == 0).then(pl.lit("flat"))
        .when(pl.col("has_reentry") & (pl.col("delta_norm") > 0)).then(pl.lit("restored"))
        .when(pl.col("has_reentry")).then(pl.lit("destroyed"))
        .when(pl.col("delta_norm") > 0).then(pl.lit("saved_protection"))
        .otherwise(pl.lit("missed_upside")).alias("outcome_class"),
        pl.when(pl.col("a_move_from_exit") >= 3.0).then(pl.lit(ENDPOINT_CLASS[3]))
        .when(pl.col("a_move_from_exit") >= 1.0).then(pl.lit(ENDPOINT_CLASS[2]))
        .when(pl.col("a_move_from_exit") >= 0.5).then(pl.lit(ENDPOINT_CLASS[1]))
        .otherwise(pl.lit(ENDPOINT_CLASS[0])).alias("giant_class"))
    noev = (final.filter(pl.col("gb_status").is_in([S_HOLD, S_PREEMPT, S_SINGLE_BAR]))
            .with_columns(pl.col("delta_norm").fill_null(0.0).alias("delta_norm"),
                          pl.lit(None, pl.Float64).alias("a_move_from_exit"),
                          pl.lit("no_event").alias("outcome_class"),
                          pl.lit("not_applicable_no_exit").alias("giant_class")))
    # the unresolved member stays in the risk set with a NULL delta and an "unresolved" class:
    # its later wealth is unknown and may never be read as a zero or as a never-recovery
    unres = final.filter(pl.col("gb_status") == S_UNRESOLVED).with_columns(
        pl.lit(None, pl.Float64).alias("delta_norm"),
        pl.lit(None, pl.Float64).alias("a_move_from_exit"),
        pl.lit("unresolved").alias("outcome_class"),
        pl.lit("not_applicable_unresolved_exit").alias("giant_class"))
    whole = pl.concat([comp, noev, unres], how="diagonal_relaxed")
    conds = {
        "event_observed_tape": comp,
        "event_complete_tape": comp.filter(pl.col("path_complete_to_session_end").fill_null(False)),
        "whole_basket_observed": whole,
        "whole_basket_complete": whole.filter(
            pl.col("path_complete_to_session_end").fill_null(False))}

    metrics = ("recovery_delay_min", "missed_gross_move", "delta_norm", "a_move_from_exit")
    dist_rows = []
    for name, frame in conds.items():
        dist_rows.extend(_dist_rows(frame, name, metrics))
    pl.DataFrame(dist_rows).write_parquet(out / "distributions.parquet")

    decomp_rows = []
    for name, frame in conds.items():
        for scope, key in (("all", None), ("family", "family"), ("block", "block"),
                           ("month", "month")):
            groups = ([("ALL", frame)] if key is None
                      else [(str(k[0] if isinstance(k, tuple) else k), g)
                            for k, g in frame.group_by([key], maintain_order=True)])
            for val, g in groups:
                if g.height == 0:
                    continue
                d = g["delta_norm"]
                absd = d.abs()
                tot = float(absd.sum())
                cnt = {r["outcome_class"]: r["n"] for r in
                       g.group_by("outcome_class").agg(pl.len().alias("n")).iter_rows(named=True)}
                gcnt = {r["giant_class"]: r["n"] for r in
                        g.group_by("giant_class").agg(pl.len().alias("n")).iter_rows(named=True)}
                decomp_rows.append({
                    "condition": name, "scope": scope, "scope_value": val,
                    "n_members": g.height, "n_events": int((g["gb_status"] == S_EVENT).sum()),
                    "n_reentry": int(g["has_reentry"].fill_null(False).sum()),
                    "n_delta_null_unresolved": int(d.is_null().sum()),
                    "sum_delta": float(d.sum()), "sum_delta_pos": float(d.filter(d > 0).sum()),
                    "sum_delta_neg": float(d.filter(d < 0).sum()), "mean_delta": float(d.mean()),
                    "median_delta": float(d.median()),
                    "n_saved_protection": cnt.get("saved_protection", 0),
                    "n_missed_upside": cnt.get("missed_upside", 0),
                    "n_restored": cnt.get("restored", 0), "n_destroyed": cnt.get("destroyed", 0),
                    "n_flat": cnt.get("flat", 0), "n_no_event": cnt.get("no_event", 0),
                    "n_giant50": gcnt.get("giant50", 0), "n_giant100": gcnt.get("giant100", 0),
                    "n_giant300": gcnt.get("giant300", 0),
                    "top_contributor_share": float(absd.max() / tot) if tot > 0 else 0.0,
                    "hhi_abs_delta": float(((absd / tot) ** 2).sum()) if tot > 0 else 0.0})
    decomp_df = pl.DataFrame(decomp_rows)
    decomp_df.write_parquet(out / "decomposition.parquet")
    witness_rows = decomp_df.filter((pl.col("condition") == "whole_basket_observed") &
                                    (pl.col("scope") == "all")).to_dicts()

    # ---- compact headline at declared marks (equal-member, all observed tapes) ---------------- #
    headline: dict = {}
    occ_n = {}
    for r in curve_table.filter((pl.col("weighting") == "occupancy")).iter_rows(named=True):
        occ_n[(r["scope_kind"], r["scope_value"], r["denominator"], r["clock"], r["h"],
               r["branch"])] = (r["n_obs"], r["n_obs_censored_partial"])
    for scope_val in ("ALL", "A_pm", "B600"):
        kind = "all" if scope_val == "ALL" else "family"
        for denom in ("all_observed_tape", "complete_tape_only"):
            for clock in CLOCKS:
                sel = curve_table.filter((pl.col("scope_kind") == kind) &
                                         (pl.col("scope_value") == scope_val) &
                                         (pl.col("denominator") == denom) &
                                         (pl.col("weighting") == "equal_member_day_family") &
                                         (pl.col("clock") == clock) & pl.col("h").is_in(HEADLINE_H))
                for r in sel.iter_rows(named=True):
                    n_obs, n_part = occ_n.get((kind, scope_val, denom, clock, r["h"],
                                               r["branch"]), (None, None))
                    headline.setdefault(scope_val, {}).setdefault(denom, {}).setdefault(
                        clock, {})[f"h{r['h']}_{r['branch']}"] = {
                        "mean": r["mean"], "median": r["median"], "q10": r["q10"],
                        "q90": r["q90"], "se_day_clustered": r["se"],
                        "se_month_clustered": r["se_month"],
                        "se_diff_day_clustered_paired": r["se_diff"],
                        "se_diff_month_clustered_paired": r["se_diff_month"],
                        "n_cells": r["n_cells"],
                        "n_days": r["n_days"], "n_months": r["n_months"],
                        "n_members": r["n_members"],
                        "n_members_censored_partial": r["n_members_censored_partial"],
                        "n_obs_occupancy": n_obs, "n_obs_censored_partial": n_part}
    return {"curve_rows": curve_table.height, "headline": headline,
            "whole_basket_witness": witness_rows,
            "econ": {"event_observed_tape": _econ(comp),
                     "event_complete_tape": _econ(
                         comp.filter(pl.col("path_complete_to_session_end").fill_null(False))),
                     "whole_basket_complete": _econ(conds["whole_basket_complete"])}}


def census(ev: pl.DataFrame, mb: pl.DataFrame) -> dict:
    st = ev.group_by("gb_status").agg(pl.len().alias("n")).sort("gb_status")
    evt = ev.filter(pl.col("gb_status") == S_EVENT)
    complete = evt.filter(pl.col("path_complete_to_session_end").fill_null(False))
    out = {"members_total": ev.height, "members_base_members": mb.height,
           "by_status": {r["gb_status"]: r["n"] for r in st.iter_rows(named=True)},
           "event_sale_executed": evt.height,
           "event_complete_tape": complete.height,
           "event_censored_tape": evt.height - complete.height,
           "unresolved_no_next_open": int((ev["gb_status"] == S_UNRESOLVED).sum()),
           "reentry": int(ev["has_reentry"].fill_null(False).sum()),
           "no_reentry_complete_tape": int((~complete["has_reentry"].fill_null(False)).sum()),
           "censored_sales_with_observed_reclaim": int(
               ev.filter((pl.col("gb_status") == S_EVENT) &
                         ~pl.col("path_complete_to_session_end").fill_null(False))
               ["has_reentry"].fill_null(False).sum()),
           "censored_sales_without_observed_reclaim": int(
               ev.filter((pl.col("gb_status") == S_EVENT) &
                         ~pl.col("path_complete_to_session_end").fill_null(False) &
                         ~pl.col("has_reentry").fill_null(False)).height),
           "denominator_note": ("re-entry counts use all sales with an executed exit "
                                "(complete + censored observed tapes); the "
                                "never-reclaimed label is only applied on complete tapes"),
           "reentry_blocked_at_session_boundary": int(
               (ev["reclaim_blocked_reason"] == "buy_would_be_at_or_after_session_end")
               .fill_null(False).sum())}
    if "terminal_censored" in ev.columns:
        out["terminal_censored_members"] = int(ev["terminal_censored"].fill_null(False).sum())
    if "label_reconciled" in ev.columns:
        rec = ev.filter(pl.col("label_reconciled").is_not_null())
        out["panel_label_reconcile_members"] = rec.height
        out["panel_label_reconcile_ok"] = int((rec["label_reconciled"] == True).sum())  # noqa: E712
        out["panel_label_reconcile_mismatch"] = rec.height - out["panel_label_reconcile_ok"]
        out["panel_label_fired_true"] = int((rec["giveback_fired_10"] == True).sum())  # noqa: E712
    return out


# --------------------------------------------------------------------------- #
def selftest() -> dict:
    """Genuine financial-boundary smoke on synthetic member tapes (no base needed)."""
    chk = {}

    def tape(et, o, c, rh, se=1000):
        et = np.asarray(et, np.int64)
        return (et, np.asarray(c, float), np.asarray(rh, float), np.asarray(o, float), se)

    # 1) flat after the event: B loses exactly the reentry round trip, A is unchanged
    d = detect_member(*tape([940, 941, 942, 943, 944], [10, 10, 10, 10, 10],
                            [10, 10, 9.0, 10, 10], [10, 10, 10, 10, 10]))
    chk["flat_B_loses_roundtrip_A_unchanged"] = bool(
        d["gb_status"] == S_EVENT and d["has_reentry"] and abs(d["a_final_norm"] - 1.0) < 1e-12
        and abs(d["b_final_norm"] - FRIC_K) < 1e-12)
    chk["flat_delta_is_fric_k_minus_1"] = bool(abs(d["delta_norm"] - (FRIC_K - 1.0)) < 1e-12)
    chk["branches_start_at_one"] = bool(abs(d["curve"]["a_norm"][0] - 1.0) < 1e-12
                                        and abs(d["curve"]["b_norm"][0] - 1.0) < 1e-12)

    # 2) the reclaim closing bar cannot buy at its own open: it closes at exactly the gross sale
    #    price, but the buy is the NEXT open, which gaps to 100 -> B must be priced off 100
    d2 = detect_member(*tape([940, 941, 942, 943, 944, 945], [10, 10, 10, 10, 100, 100],
                             [10, 10, 9.0, 10, 100, 100], [10, 10, 10, 10, 100, 100]))
    chk["reclaim_bar_is_not_buy_bar"] = bool(d2["reclaim_bar_index"] == 3
                                             and d2["reentry_bar_index"] == 4
                                             and abs(d2["reentry_px"] - 100.0) < 1e-12
                                             and abs(d2["b_final_norm"] - FRIC_K) < 1e-12)

    # 3) no reclaim -> B stays cash at exactly the exit value while A loses (protection saved)
    d3 = detect_member(*tape([940, 941, 942, 943, 944], [10, 10, 10, 8, 6],
                             [10, 10, 9.0, 7, 5], [10, 10, 10, 10, 10]))
    chk["no_reclaim_B_cash"] = bool(not d3["has_reentry"]
                                    and abs(d3["b_final_norm"] - 1.0) < 1e-12
                                    and d3["delta_norm"] > 0
                                    and abs(d3["a_final_norm"] - 0.75) < 1e-12)

    # 4) forced flat preempts a trigger on the completed bar session_end-1 -> no event
    d4 = detect_member(*tape([940, 941, 999, 1000], [10, 10, 10, 10],
                             [10, 10, 9.0, 9.0], [10, 10, 10, 10]))
    chk["session_boundary_preempts"] = bool(d4["gb_status"] == S_PREEMPT)

    # 5) a pre-flat trigger whose next print is the session_end bar (gap) is a genuine sale at that
    #    open, but no reentry can be bought at/after session_end; A == B == 1
    d5 = detect_member(*tape([940, 941, 942, 1000], [10, 10, 10, 11],
                             [10, 10, 9.0, 11], [10, 10, 10, 11]))
    chk["gap_sale_at_session_end_no_reentry"] = bool(
        d5["gb_status"] == S_EVENT and d5["exit_et"] == 1000 and d5["exit_is_session_end_bar"]
        and not d5["has_reentry"] and abs(d5["delta_norm"]) < 1e-12)

    # 6) exit price is the open of the next PRINTED bar (gap crossing), never an invented bar
    d6 = detect_member(*tape([940, 941, 942, 947, 948], [10, 10, 10, 12, 13],
                             [10, 10, 9.0, 12, 13], [10, 10, 10, 12, 13]))
    chk["gap_crossing_uses_next_observed_open"] = bool(d6["exit_et"] == 947
                                                       and abs(d6["exit_px"] - 12.0) < 1e-12)

    # 7) censored: trigger on the last observed bar with no next open -> unresolved, never zero
    d7 = detect_member(*tape([940, 941, 942], [10, 10, 10], [10, 10, 9.0], [10, 10, 10]))
    chk["censored_last_bar_unresolved"] = bool(d7["gb_status"] == S_UNRESOLVED
                                               and d7["b_final_norm"] is None)
    # 8) a censored tape with an early executable sale keeps the observed paired branches: the
    #    sale is real, the wait is observed, and later wealth is simply unknown (never zero)
    d8 = detect_member(*tape([940, 941, 942, 943, 944], [10, 10, 10, 12, 11],
                             [10, 10, 9.0, 11, 10], [10, 10, 10, 12, 12]))
    chk["censored_early_sale_kept_observed"] = bool(
        d8["gb_status"] == S_EVENT and not d8["has_reentry"]
        and abs(d8["a_final_norm"] - 11.0 / 12.0) < 1e-12 and abs(d8["b_final_norm"] - 1.0) < 1e-12)

    # 9) horizon support lattice: two events, anchors 900/950, sessions 959/959, tapes complete.
    #    h=59 is inside only the first member's session (priced 1); h=9 is inside both (priced 2);
    #    the member->row assignment must not drift (auditor witness for the off-by-one).
    sup, sup_audit = support_by_horizon(
        np.array([900, 950]), np.array([959, 959]), np.array([959, 959]),
        np.concatenate([np.zeros(60, dtype=np.int32), np.ones(10, dtype=np.int32)]),
        np.concatenate([np.arange(60, dtype=np.int64), np.arange(10, dtype=np.int64)]))
    row = {int(r["h"]): r for r in sup.iter_rows(named=True)}
    chk["support_lattice_member_assignment"] = bool(
        row[59]["n_at_risk_within_session"] == 1 and row[59]["n_priced"] == 1
        and row[59]["n_missing_inside_observed_tape"] == 0
        and row[9]["n_at_risk_within_session"] == 2 and row[9]["n_priced"] == 2
        and row[10]["n_at_risk_within_session"] == 1
        and sup["invalid"].sum() == 0
        and sup_audit["members_priced_set_mismatch"] == 0
        and sup_audit["partition_recon_failures"] == 0)

    # 10) cluster-robust SE of the PUBLISHED point mean (unweighted over cells):
    #     unequal day sizes must NOT be re-weighted to a day mean.  day1=[2], day2=[0,0] gives
    #     published mean 2/3 (not the day mean 1) and score-cluster SE 8/9 (not 1.0, not the naive
    #     cell-level 0.5774); equal-sized clusters reduce to sd(cluster means)/sqrt(G).
    m_uneq, se_uneq, g_uneq = cluster_se(np.array([2.0, 0.0, 0.0]), np.array([0, 1, 1]))
    m_uneq2, se_uneq2, _ = cluster_se(np.array([1.0, 1.0, -1.0]), np.array([0, 0, 1]))
    m_eq, se_eq, g_eq = cluster_se(np.array([1.0, 1.0, 3.0, 3.0]), np.array([0, 0, 1, 1]))
    chk["cluster_se_same_estimand"] = bool(
        abs(m_uneq - 2.0 / 3.0) < 1e-12 and abs(se_uneq - 8.0 / 9.0) < 1e-12 and g_uneq == 2
        and abs(m_uneq2 - 1.0 / 3.0) < 1e-12 and abs(se_uneq2 - 8.0 / 9.0) < 1e-12
        and abs(m_eq - 2.0) < 1e-12 and abs(se_eq - 1.0) < 1e-12 and g_eq == 2)
    return {k: bool(v) for k, v in chk.items()}


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    ap.add_argument("--ledger-selftest", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    smoke = selftest()
    if args.selftest:
        print(json.dumps(smoke, indent=2))
        return 0 if all(smoke.values()) else 1
    if not all(smoke.values()):
        raise SystemExit(f"smoke FAILED: {smoke}")

    args.base = _resolve_base(args.base)
    args.out = args.out or (args.base.parent / "B")
    args.out.mkdir(parents=True, exist_ok=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    ev, curves, names, support, support_audit, linkage, ledger_rec = run(args.base)
    ev.write_parquet(args.out / "events.parquet")
    support.write_parquet(args.out / "support_by_horizon.parquet")
    agg = aggregate(ev, curves, names, args.out)
    cen = census(ev, pl.read_parquet(args.base / "members.parquet", columns=["member_id"]))
    legacy = legacy_reconcile(ev, args.ledger_selftest)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    manifest = {
        "version": "basket-ownership-continuity-v0",
        "contract": {"path": str(CONTRACT.relative_to(WT)), "sha256": sha256_file(CONTRACT)},
        "code": {"path": str(Path(__file__).resolve().relative_to(WT)),
                 "sha256": sha256_file(Path(__file__).resolve())},
        "base_root": str(args.base),
        "inputs": {p.name: {"sha256": sha256_file(args.base / p.name)}
                   for p in (args.base / "states.parquet", args.base / "executions.parquet",
                             args.base / "members.parquet")},
        "outputs": {p.name: {"sha256": sha256_file(args.out / p.name)}
                    for p in (args.out / "events.parquet", args.out / "curves.parquet",
                              args.out / "distributions.parquet",
                              args.out / "decomposition.parquet",
                              args.out / "support_by_horizon.parquet")},
        "census": cen, "linkage_audit": linkage, "ledger_reconcile": ledger_rec,
        "legacy_reconcile": legacy, "smoke": smoke,
        "curve_rows": agg["curve_rows"], "headline_curves": agg["headline"],
        "headline_economics": agg["econ"],
        "support_by_horizon": {
            "clock": "h_from_decision (the sale anchor = trigger completed bar + 1)",
            "file": "support_by_horizon.parquet",
            "note": ("horizon-specific disclosure on the sale-event set: a horizon inside the "
                     "member's session but past its observed tape end is UNKNOWN (never 0), a "
                     "horizon inside the tape with no print is a gap, and a horizon past "
                     "session_end is NOT at risk and NOT unknown; B's cash is known at any horizon "
                     "but the paired delta is unknown whenever A cannot be valued"),
            "rows": support.to_dicts(),
            "audit": support_audit},
        "whole_basket_witness": agg["whole_basket_witness"],
        "peak_mib": round(peak, 1),
        "side_friction_bps": SIDE * 10000, "fric_k": FRIC_K, "cash_return": 0.0,
        "labels": ("anatomy only: no optimal horizon/stop/size, no alpha pass, no policy claim; "
                   "future labels are attribution only"),
        "definitions": {
            "occupancy": "each member-minute endpoint counts once (occupancy weighting)",
            "equal_member_day_family": ("member mean, then day x family cell mean, then unweighted "
                                        "mean over cells (day-clustered); median/quantiles and "
                                        "min/max are over cell means"),
            "se": ("day-cluster-robust SE of the level mean: cells collapsed to their day mean, "
                   "then std(day means)/sqrt(n_days); occupancy rows carry no clustered SE (null)"),
            "se_month": ("month-cluster-robust SE: day means collapsed to their month mean, then "
                         "std(month means)/sqrt(n_months)"),
            "se_diff": ("cluster-robust SE of the PAIRED level difference (mean_B - mean_A) at day "
                        "level; se_diff_month at month level; duplicated on both branch rows"),
            "exit_ret_class": ("attribution-only scope from the member's OWN observed last-minute "
                               "return above the gross exit price (labels exit_ret_lt_50pct / "
                               "50_100pct / 100_300pct / ge_300pct; censored tapes = "
                               "exit_ret_unknown_censored_tape). NOT the Phase-1 entry-to-session "
                               "monster definition and never a CV denominator"),
            "audit_entry_mfe_class": ("audit-only scope from the members file's entry-to-session "
                                      "MFE tags tail_class_50/100/300 (max high from the fill "
                                      "row's next open); labels entry_mfe_* , unknown for censored. "
                                      "Attribution only - never a state, filter or denominator"),
            "primary_object": ("the pointwise common-endpoint wealth curves in curves.parquet; "
                               "headline_economics is one endpoint (each member's own last "
                               "observed executable minute), labelled as such"),
            "giant_from_exit": ("attribution-only label: the member's own observed endpoint return "
                                "above the gross exit price (see exit_ret_class); never a state, "
                                "filter or executable-peak claim"),
            "branch_transactions": ("A (retain) NEVER executes the GB10 sale: it holds the "
                                    "already-owned q=1 from entry through the common endpoint, and "
                                    "W_exit = P_exit*(1-s) is only the shared numeraire, not an A "
                                    "trade. B executes the GB10 sale at P_exit, waits in cash, then "
                                    "buys back once at the next open after the reclaim close; "
                                    "relative to A that is exactly ONE extra sell/buy round trip "
                                    "k=(1-s)/(1+s)=0.995/1.005. The original entry fee is common/"
                                    "sunk and is never recharged or double counted")},
        "caveats": [
            "events are conditioned on realised entry-to-trigger tenure, so tenure confounds any "
            "comparison against whole-basket members",
            "future tail/giant labels are audit-only attribution, never states or filters",
            "a censored member with no next open is unresolved; its held wealth is unknown, not 0",
            "curves are published for the all-observed-tape and complete-tape-only denominators "
            "separately; no completeness flag ever filters the causal risk set",
            "the never-reclaimed label is applied on complete tapes only; censored sales without "
            "an observed reclaim are unresolved, not never-recovery"],
    }
    blob = json.dumps(manifest, indent=2)
    (args.evidence / "manifest.json").write_text(blob)
    (args.out / "manifest.json").write_text(blob)
    print(json.dumps({"census": cen, "smoke_ok": all(smoke.values()), "peak_MiB": round(peak, 1),
                      "ledger_reconcile": ledger_rec, "legacy_reconcile": legacy,
                      "linkage_audit": linkage}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
