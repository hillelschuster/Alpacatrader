#!/usr/bin/env python3

"""Replay stopping-value decisions on the same original roster, exact dollars/shares.

Predictions and event kinds are the ONLY learned action inputs. Future execution and
label_status stay inside accounting. Entry costs are paid once; retained shares incur
no fresh round-trip cost. Settled sale cash is reusable only after exec_et+1.
Three action diagnostics: stopping only; one repair re-entry; cash redeployment into
freshly observed positive-value original-roster claims (rotation/reinforcement).
N3 and N5 are separate original ownership/candidate universes, never combined.

``simulate`` also carries a second ADMISSION axis, keyword-only and defaulting to the
original behaviour so every pre-existing caller is unchanged:

* ``admission='upfront'`` -- the original roster's virtual fills are executed once each
  for a reserved ``1/n`` slot.  This is the whole historical engine.
* ``admission='cash'``   -- the SAME roster starts as pure cash: no initial order, no
  reservation, no entry fee.  Each claim owns its own ``1/n`` slot and buys only when the
  forecast clears the round-trip hurdle, because entry is not yet sunk.  A held position
  is still sold on the sign of the forecast alone (cost hysteresis).  ``cycles`` bounds
  how many fresh cycles a claim may take.  Reached only by the ``<view>:cash`` policy.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import lifecycle_study as ls
import polars as pl

CLOCKS = (540, 560, 569, 571)
SIDES = (0.005, 0.0075)
MODES = ("stop", "reentry", "allocate")
REPAIRS = ("repair", "rank_recovery", "new_high", "reclaim", "resurrection")
BUY_DEPLOY_FRACTION = 0.90  # fixed execution headroom, not an optimized sizing rule


def finite(x):
    return x is not None and math.isfinite(float(x))


def simulate(
    rows,
    roster,
    predictions,
    side,
    n,
    policy,
    notional=10000.0,
    *,
    admission="upfront",
    cycles="once",
):
    """One day/clock book. A pending sell never manufactures spendable cash.

    ``admission='upfront'`` (the default) is the ORIGINAL roster admission and is
    byte-identical to the historical engine: every filled claim is bought once at its
    own ``fill_et``/``fill_px`` for a reserved ``1/n`` cash slot.

    ``admission='cash'`` starts the SAME original roster with NO initial order, NO
    reservation and NO entry fee: every claim simply owns its ``1/n`` cash slot, so the
    account holds all cash until a fresh gate actually says buy. Because the roster can
    hold fewer than ``n`` claims, the slots sum to ``len(roster)/n <= 1`` and the unused
    remainder stays unassigned global cash -- it is never borrowed across claims and
    never resized onto one.

    The two thresholds differ because entry is SUNK once held: an owned position is sold
    on the SIGN of the forecast (core's ``pred < 0`` rule), while a FRESH entry must clear
    the round-trip hurdle ``2*s/(1-s)`` on the forecast's GROSS magnitude. That is cost
    hysteresis, not a time or return-count heuristic.

    ``cycles`` is the predeclared entry-count control and counts ACTUAL executed buys,
    never attempts: ``'once'`` (default) allows each claim exactly one fresh cycle ever;
    ``'repeat'`` lets a claim re-enter after a sale has actually settled, each re-entry
    clearing the same hurdle. The first fresh buy is an ENTRY, not a re-entry.
    """
    if admission not in ("upfront", "cash"):
        raise SystemExit(
            f"unknown admission {admission!r}; only 'upfront' or 'cash' is an admitted mode"
        )
    if cycles not in ("once", "repeat"):
        raise SystemExit(
            f"unknown cycle control {cycles!r}; only 'once' or 'repeat' is an admitted mode"
        )
    cash_mode = admission == "cash"
    ros = {r["ticker"]: r for r in roster if r["rank"] <= n}
    if not ros:
        raise ValueError("empty roster without a known-empty selection contract")
    clock = int(next(iter(ros.values()))["clock"])
    se = int(next(iter(ros.values()))["session_end"])
    day = next(iter(ros.values()))["day"]
    by_t = defaultdict(dict)
    for row in rows:
        if row["ticker"] in ros:
            by_t[int(row["t"])][row["ticker"]] = row
    # Available cash excludes reserved entries and unsettled/recent sale receipts.
    cash = 1.0
    ready_cash = 1.0
    receipts = []
    # In CASH admission each original claim simply owns 1/n of the account from t=0.
    # With fewer than n claims the slots sum to len(ros)/n < 1 and the remainder stays
    # unassigned global cash; no claim may borrow it and nothing is ever resized onto one.
    claim_cash = dict.fromkeys(ros, 1 / n if cash_mode else 0.0)
    shares = dict.fromkeys(ros, 0.0)
    flow = dict.fromkeys(ros, 0.0)
    gross_flow = dict.fromkeys(ros, 0.0)
    fees = dict.fromkeys(ros, 0.0)
    pending = {}
    # Nothing is reserved in CASH mode: no initial order exists, so there is no future
    # commitment holding cash hostage, and reserving would silently forbid every fresh buy.
    entry_reserve = dict.fromkeys(ros, 0.0 if cash_mode else 1 / n)
    last_release = dict.fromkeys(ros, None)
    reentries = dict.fromkeys(ros, 0)
    fresh_entries = dict.fromkeys(ros, 0)
    marks = {tk: r["decision_px"] for tk, r in ros.items()}
    # A roster row with no observable market data is a KNOWN cash fact in cash mode: no
    # order was attempted, so nothing about the outcome is unresolved.  In UPFRONT mode
    # the same row is an unresolved INITIAL admission, exactly as before.
    missing = {tk for tk, r in ros.items() if r["status"] == "missing"} if not cash_mode else set()
    # The original virtual fill is a shadow reference only once it has become PAST.
    shadow_known = {
        tk: bool(finite(r.get("fill_et")) and finite(r.get("fill_px"))) for tk, r in ros.items()
    }
    attempted_unknown = set()
    affordability_unknown = set()
    unresolved_initial = False
    fills = []
    snapshots = {}
    max_positions = 0
    exit_times = []
    settled_receipt = dict.fromkeys(ros, 0.0)

    def reserve_total():
        return sum(entry_reserve.values()) + sum(
            order["amount"]
            for order in pending.values()
            if order["side"] == "buy" and not order["initial"]
        )

    timeline = None
    scheduled = set()

    def execute(t):
        nonlocal cash, ready_cash, max_positions
        matured = [x for x in receipts if x[0] <= t]
        for _, owner, amount in matured:
            ready_cash += amount
            # The ACTUAL net receipt lands in this claim's own slot and nowhere else.
            claim_cash[owner] += amount
            settled_receipt[owner] += amount
        receipts[:] = [x for x in receipts if x[0] > t]
        for tk in sorted(pending, key=lambda x: pending[x]["side"] == "buy"):
            order = pending[tk]
            if (
                order["et"] is None
                or order["et"] > t
                or order["et"] > se
                or not finite(order["px"])
            ):
                continue
            px = order["px"]
            if order["side"] == "buy":
                if order["initial"]:
                    cost = order["amount"]
                    q = cost / (px * (1 + side))  # predefined equal-dollar admission ruler
                else:
                    q = order["quantity"]  # fixed from completed decision price
                    cost = q * px * (1 + side)
                    if (
                        cost > order["amount"] + 1e-10
                        or cost > ready_cash + 1e-10
                        or ((mode == "reentry" or cash_mode) and cost > claim_cash[tk] + 1e-10)
                    ):
                        attempted_unknown.add(tk)
                        affordability_unknown.add(tk)
                        order["et"] = None  # unresolved; no hindsight resizing or leverage
                        continue
                if cost > cash + 1e-10:
                    raise AssertionError("unfunded buy")
                gross = q * px
                fee = gross * side
                cash -= cost
                ready_cash -= cost
                shares[tk] += q
                flow[tk] -= cost
                gross_flow[tk] -= gross
                if order["initial"]:
                    entry_reserve[tk] = 0.0
                else:
                    if mode == "reentry" or cash_mode:
                        # Segregated: the buy is funded from THIS claim's own settled slot
                        # only.  A claim never borrows another claim's money.
                        claim_cash[tk] -= cost
                    else:
                        # Redeployed receipts lose their old segregated owner.
                        left = cost
                        for owner in sorted(claim_cash):
                            take = min(left, claim_cash[owner])
                            claim_cash[owner] -= take
                            left -= take
                    reentries[tk] += 1
                    fresh_entries[tk] += 1
            else:
                q = min(shares[tk], order["amount"])
                gross = q * px
                fee = gross * side
                net = gross - fee
                cash += net
                # Cash is real, but cannot fund another order at this same open.
                receipts.append((int(order["et"]) + 1, tk, net))
                shares[tk] -= q
                flow[tk] += net
                gross_flow[tk] += gross
                last_release[tk] = int(order["et"])
                exit_times.append(int(order["et"]))
            fees[tk] += fee
            if q > 1e-12:
                vol = order.get("volume")
                fill = {
                    "day": day,
                    "clock": clock,
                    "n": n,
                    "side_cost": side,
                    "policy": policy,
                    "ticker": tk,
                    "rank": ros[tk]["rank"],
                    "side": order["side"],
                    "decision_et": order["decision"],
                    "exec_et": order["et"],
                    "px": px,
                    "shares_per_capital": q,
                    "gross_fraction": gross,
                    "fee_fraction": fee,
                    "reason": order["reason"],
                    "participation": q * notional / vol if finite(vol) and vol > 0 else None,
                }
                if cash_mode:
                    # The two predeclared cycle controls share every other fill key, so
                    # without this an execution leg cannot be attributed to the book that
                    # produced it and a per-book fee/order reconciliation cannot join the
                    # journal to the separately reported cycles cells.  Cash mode only:
                    # upfront fills keep their historical schema.
                    fill["admission"] = admission
                    fill["cycles"] = cycles
                fills.append(fill)
            del pending[tk]
        max_positions = max(max_positions, sum(q > 1e-12 for q in shares.values()))
        if cash < -1e-9 or ready_cash < -1e-9 or ready_cash > cash + 1e-9:
            raise AssertionError("cash or receipt conservation")

    def submit(t, tk, side_kind, amount, row, reason, initial=False):
        if tk in pending or amount <= 1e-12:
            return
        et, px = row.get("sell_et"), row.get("sell_px")
        quantity = None
        if side_kind == "buy" and not initial:
            mark = marks.get(tk)
            if not finite(mark) or mark <= 0:
                attempted_unknown.add(tk)
                return
            quantity = BUY_DEPLOY_FRACTION * amount / (mark * (1 + side))
        if not finite(px) or px <= 0 or et is None:
            attempted_unknown.add(tk)
            if side_kind == "buy":
                pending[tk] = {
                    "side": "buy",
                    "et": None,
                    "px": None,
                    "amount": amount,
                    "quantity": quantity,
                    "decision": t,
                    "reason": reason,
                    "initial": initial,
                }
            return
        if et < t:
            raise AssertionError("anticipatory fill")
        pending[tk] = {
            "side": side_kind,
            "et": int(et),
            "px": float(px),
            "amount": amount,
            "quantity": quantity,
            "decision": t,
            "reason": reason,
            "volume": row.get("sell_volume"),
            "initial": initial,
        }
        if timeline is not None and t < int(et) <= se and int(et) not in scheduled:
            heapq.heappush(timeline, int(et))
            scheduled.add(int(et))

    if not cash_mode:
        # UPFRONT admission only: the original roster's own virtual fills are executed.
        # In CASH mode there is no initial order at all, hence no reserve and no fee.
        for tk, r in ros.items():
            if r["status"] == "filled":
                pending[tk] = {
                    "side": "buy",
                    "et": r["fill_et"],
                    "px": r["fill_px"],
                    "amount": 1 / n,
                    "decision": clock,
                    "reason": "initial",
                    "volume": r.get("fill_volume"),
                    "initial": True,
                }
    parts = policy.split(":")
    is_model = len(parts) == 2
    view, mode = parts if is_model else (None, None)
    if cash_mode and not (is_model and mode == "cash"):
        raise SystemExit(
            "cash admission is reached only through the '<view>:cash' policy; refusing to "
            f"reinterpret {policy!r} as a cash-mode policy"
        )
    if not cash_mode and is_model and mode == "cash":
        raise SystemExit(
            "policy '<view>:cash' requires admission='cash'; it is not an upfront mode"
        )
    # Stop/re-entry decisions exist only at causal events. Hold needs only fills,
    # snapshots and terminal. Fade retains its full-minute ruler clock.
    points = {clock, se, clock + 5, 585, 630, 690, 780}
    if not cash_mode:
        points.update(int(r["fill_et"]) for r in ros.values() if r["status"] == "filled")
    if is_model:
        points.update(t for tk, t in predictions if tk in ros)
    elif policy == "fade":
        points.update(range(clock, se + 1))
    timeline = [t for t in points if clock <= t <= se]
    # The fresh-entry hurdle is the round-trip cost of buying AND selling one account
    # dollar at the modeled per-leg side cost: (1-s)/(1+s) > 1/(1+h)  =>  h = 2s/(1-s).
    # It is derived from the fee alone -- not tuned, not a target return.
    hurdle = 2 * side / (1 - side)
    heapq.heapify(timeline)
    scheduled = set(timeline)
    while timeline:
        t = heapq.heappop(timeline)
        # Blocked initial slots become reusable only once their causal timeout expires.
        for tk, r in ros.items():
            if r["status"] == "blocked" and t >= clock + 5:
                entry_reserve[tk] = 0.0
        execute(t)
        current = by_t.get(t, {})
        for tk, row in current.items():
            if finite(row.get("px")):
                marks[tk] = row["px"]
        if t in (585, 630, 690, 780):
            snapshots[f"exposure_{t}"] = sum(shares[tk] * marks.get(tk, 0) for tk in ros)
        if t == se:
            # Terminal is the accounting boundary, not an assumed harvest time.
            unresolved_initial = any(o["side"] == "buy" and o["initial"] for o in pending.values())
            # In CASH mode the only admission is a FRESH buy, so an unresolved fresh buy is
            # the equivalent of the upfront engine's unresolved INITIAL order: a funding
            # attempt whose outcome is unknown.  It must not be laundered into a known 0
            # merely because nothing settled by the terminal boundary.
            unresolved_fresh_tickers = (
                {
                    tk
                    for tk, o in pending.items()
                    if o["side"] == "buy" and not o["initial"] and finite(o["px"])
                }
                if cash_mode
                else set()
            )
            unresolved_fresh = bool(unresolved_fresh_tickers)
            if unresolved_fresh_tickers:
                # A finite fresh buy whose execution falls outside the session is a real
                # funding attempt with an unresolved outcome.  Naming the affected claims
                # here -- BEFORE the orders are discarded -- is what stops that attempt
                # being reported as known member cash: their net is null and they appear
                # in unknown_tickers.  Claims that never traded stay known.
                attempted_unknown |= unresolved_fresh_tickers
            pending = {tk: o for tk, o in pending.items() if o["side"] == "sell"}
            for tk, q in shares.items():
                if q > 1e-12 and tk not in pending:
                    submit(t, tk, "sell", q, current.get(tk, {}), "terminal")
            execute(t)
            break
        event_values = {}
        for tk, row in current.items():
            q = shares[tk]
            pred = predictions.get((tk, t), {})
            # label_status and sell_* never decide whether the claim deserves capital.
            value = pred.get(f"pred_{view}") if is_model else None
            kinds = pred.get("event_kind", "").split("|")
            # The original virtual fill price is a SHADOW reference: it exists only for a
            # claim whose virtual fill actually happened, and it is readable only once that
            # fill is PAST.  Without it there is no entry anchor, so the magnitude is
            # undefined and no fresh capital may be committed on this forecast.  (For the
            # historical upfront paths every predicted claim is a filled claim, so this
            # guard never changes an action they could previously take.)
            if (
                finite(value)
                and shadow_known[tk]
                and t > int(ros[tk]["fill_et"])
                and finite(marks.get(tk))
                and marks[tk] > 0
            ):
                # Model predicts original-share dollar increment; new capital
                # compares return per CURRENT causal marked dollar.
                relative = value * ros[tk]["fill_px"] / marks[tk]
                event_values[tk] = (float(relative), kinds)
            release = False
            if policy == "fade" and q > 1e-12 and finite(row.get("fill_et")):
                values = [
                    row.get(k) for k in ("ret_fill", "dd_from_high", "minutes_since_high", "ret5")
                ]
                release = (
                    t - row["fill_et"] >= 10
                    and all(finite(x) for x in values)
                    and values[0] < 0
                    and values[1] <= -0.05
                    and values[2] >= 15
                    and values[3] < 0
                )
            elif is_model and finite(value):
                release = value < 0
            if q > 1e-12 and release:
                submit(t, tk, "sell", q, row, "state_release" if is_model else "fade")
        # Allocation quantities are based only on cash already settled before this decision.
        free = max(0.0, ready_cash - reserve_total())
        if is_model and mode == "reentry":
            for tk, (value, kinds) in sorted(event_values.items(), key=lambda item: -item[1][0]):
                if (
                    value > hurdle
                    and any(k in REPAIRS for k in kinds)
                    and shares[tk] <= 1e-12
                    and last_release[tk] is not None
                    and t > last_release[tk]
                    and reentries[tk] == 0
                    and tk not in pending
                ):
                    # Segregated original-claim proceeds; no borrowing from another name.
                    budget = min(free, max(0.0, claim_cash[tk]))
                    if budget > 1e-12:
                        submit(t, tk, "buy", budget, current[tk], "repair_reentry")
                        free = max(0.0, ready_cash - reserve_total())
        elif is_model and mode == "allocate":
            candidates = [
                (tk, value)
                for tk, (value, kinds) in event_values.items()
                if value > hurdle
                and tk not in pending
                and ros[tk]["status"] == "filled"
                and (last_release[tk] is None or t > last_release[tk])
                and (shares[tk] > 1e-12 or any(k in REPAIRS for k in kinds))
            ]
            if candidates and free > 1e-12:
                tk, _ = max(candidates, key=lambda item: (item[1], -ros[item[0]]["rank"]))
                submit(
                    t,
                    tk,
                    "buy",
                    free,
                    current[tk],
                    "reinforce" if shares[tk] > 1e-12 else "rotate_reentry",
                )
        if cash_mode:
            # COST HYSTERESIS, not a time or return-count rule: an owned position is sold
            # above on the SIGN of the forecast, because its entry is already SUNK.  A
            # FRESH entry has not paid anything yet, so it must additionally clear the
            # round-trip hurdle on the forecast's GROSS magnitude.  The hurdle is the
            # round-trip fee 2*s/(1-s) on a full account dollar, not a tuned threshold.
            for tk, (value, _kinds) in sorted(event_values.items(), key=lambda item: -item[1][0]):
                if (
                    value > hurdle
                    and shadow_known[tk]
                    and t > int(ros[tk]["fill_et"])  # the virtual fill must be PAST
                    and shares[tk] <= 1e-12
                    and tk not in pending
                    and (last_release[tk] is None or t > last_release[tk])
                    and (cycles == "repeat" or fresh_entries[tk] == 0)
                ):
                    # Segregated: the buy can only spend THIS claim's own settled slot.
                    # Its ACTUAL net receipt, never a lifetime-profit figure, and never
                    # another claim's money.
                    budget = max(0.0, claim_cash[tk])
                    if budget > 1e-12:
                        submit(t, tk, "buy", budget, current[tk], "fresh_entry")
        execute(t)
    unknown = bool(
        missing
        or attempted_unknown
        or unresolved_initial
        or unresolved_fresh
        or any(q > 1e-10 for q in shares.values())
    )
    if not unknown and abs(sum(flow.values()) - (cash - 1)) > 1e-8:
        raise AssertionError("portfolio cashflow conservation")
    if not unknown and abs(sum(gross_flow.values()) - sum(fees.values()) - (cash - 1)) > 1e-8:
        raise AssertionError("gross/fee conservation")
    daily = {
        "day": day,
        "clock": clock,
        "n": n,
        "side": side,
        "policy": policy,
        "ret": None if unknown else cash - 1,
        "unknown": unknown,
        "orders": len(fills),
        "fees": sum(fees.values()),
        "gross_pnl": sum(gross_flow.values()),
        "affordability_unknown": len(affordability_unknown),
        "max_positions": max_positions,
        "exit_times": exit_times,
        "unknown_tickers": sorted(
            missing | attempted_unknown | {tk for tk, q in shares.items() if q > 1e-10}
        ),
        **snapshots,
    }
    members = [
        {
            "day": day,
            "clock": clock,
            "n": n,
            "side": side,
            "policy": policy,
            "ticker": tk,
            "rank": r["rank"],
            "net_pnl": None
            if tk in missing | attempted_unknown or shares[tk] > 1e-10
            else flow[tk],
            "gross_pnl": gross_flow[tk],
            "fees": fees[tk],
            "remaining_shares": shares[tk],
            "reentries": reentries[tk],
        }
        for tk, r in ros.items()
    ]
    if cash_mode:
        # Cash-ledger audit columns.  They are emitted ONLY in cash mode so that the
        # upfront frames stay byte-identical to the historical engine's output.
        # ``slot_total`` is the STRUCTURAL assignment at t=0: len(roster)/n, which is < 1
        # whenever the roster holds fewer than n claims.  That remainder is
        # ``unassigned_at_start``: unassigned global cash that is never borrowed across
        # claims and never resized onto one.  ``slots_idle_end`` is what is still sitting
        # undeployed in the per-claim slots when the book closes.
        slot_total = len(ros) / n
        daily.update(
            {
                "admission": admission,
                "cycles": cycles,
                "hurdle": hurdle,
                "fresh_entries": sum(fresh_entries.values()),
                "claims_with_entry": sum(1 for v in fresh_entries.values() if v > 0),
                "roster_claims": len(ros),
                "slot_total": slot_total,
                "unassigned_at_start": 1.0 - slot_total,
                "slots_idle_end": sum(claim_cash.values()),
                "settled_receipts_total": sum(settled_receipt.values()),
                "unresolved_fresh": bool(unresolved_fresh),
                "unresolved_fresh_tickers": sorted(unresolved_fresh_tickers),
            }
        )
        # ``fresh_entries`` counts EXECUTED fresh buys including the first one; the
        # historical ``reentries`` column keeps its meaning, which is cycles AFTER the
        # first.  In cash mode the two are DISTINCT metrics, not aliases: the first fresh
        # buy is an ENTRY, so it must not inflate a re-entry count.  Upfront rows are
        # untouched.
        for member, tk in zip(members, ros, strict=True):
            member["reentries"] = max(fresh_entries[tk] - 1, 0)
            member.update(
                {
                    "admission": admission,
                    "cycles": cycles,
                    "fresh_entries": fresh_entries[tk],
                    "settled_receipt": settled_receipt[tk],
                    "cash_slot_end": claim_cash[tk],
                }
            )
    return daily, members, fills


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--events-root", type=Path, required=True)
    ap.add_argument("--scores-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    a = ap.parse_args()
    meta = json.loads((a.scores_root / "metadata.json").read_text())
    if meta.get("status") != "complete":
        raise SystemExit("stopping-value model is not complete")
    days = sorted({day for fold in meta["folds"] for day in fold["test_days"]})
    if not set(days) <= set(ls.discovery_days(a.data_root)):
        raise SystemExit("non-discovery score days")
    policies = ["hold", "fade"] + [f"{view}:{mode}" for view in meta["views_run"] for mode in MODES]
    pin = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (a.out / "days").mkdir(parents=True, exist_ok=True)
    for i, day in enumerate(days):
        roster = ls.load_roster([day], a.data_root, clocks=CLOCKS)
        panel = ls.load_panel([day], a.data_root, clocks=CLOCKS, with_tape=False)
        event = pl.read_parquet(a.events_root / "events" / f"{day}.parquet")
        score = pl.read_parquet(a.scores_root / "scores" / f"{day}.parquet")
        joined = score.drop("label_status").join(
            event.select(["day", "clock", "rank", "ticker", "t", "event_kind"]),
            on=["day", "clock", "rank", "ticker", "t"],
            how="left",
        )
        ds, ms, fs = [], [], []
        for clock in CLOCKS:
            r = roster.filter(pl.col("clock") == clock).to_dicts()
            p = panel.filter(pl.col("clock") == clock).to_dicts()
            scores = {
                (row["ticker"], row["t"]): row
                for row in joined.filter(pl.col("clock") == clock).to_dicts()
            }
            for n in (3, 5):
                for side in SIDES:
                    for policy in policies:
                        d, m, f = simulate(p, r, scores, side, n, policy)
                        ds.append(d)
                        ms.extend(m)
                        fs.extend(f)
        for label, values in (("daily", ds), ("members", ms), ("fills", fs)):
            path = a.out / "days" / f"{day}.{label}.parquet"
            tmp = path.with_suffix(".tmp.parquet")
            frame = pl.DataFrame(values, infer_schema_length=None)
            if label == "daily":
                frame = frame.with_columns(
                    pl.col("unknown_tickers").cast(pl.List(pl.String)),
                    pl.col("exit_times").cast(pl.List(pl.Int64)),
                )
            frame.write_parquet(tmp)
            tmp.replace(path)
        if i % 25 == 0:
            print(f"{i + 1}/{len(days)} {day}", flush=True)
    daily = pl.concat(
        [pl.read_parquet(a.out / "days" / f"{day}.daily.parquet") for day in days],
        how="diagonal_relaxed",
    )
    daily.write_parquet(a.out / "daily.parquet")
    summary = daily.group_by(["clock", "n", "side", "policy"]).agg(
        pl.len().alias("days"),
        pl.col("ret").count().alias("known_days"),
        pl.col("unknown").sum().alias("unknown_days"),
        pl.col("ret").mean().alias("ev"),
        pl.col("ret").median().alias("median"),
        (pl.col("ret").std() / pl.col("ret").count().sqrt()).alias("day_se"),
        pl.col("fees").filter(pl.col("ret").is_not_null()).mean().alias("fees"),
        pl.col("gross_pnl").filter(pl.col("ret").is_not_null()).mean().alias("gross_pnl"),
        pl.col("affordability_unknown").sum().alias("affordability_unknown"),
        pl.col("orders").mean().alias("orders"),
    )
    summary.write_parquet(a.out / "summary.parquet")
    per_fold = daily.with_columns(
        pl.col("day")
        .replace_strict({day: fold["fold"] for fold in meta["folds"] for day in fold["test_days"]})
        .alias("fold")
    )
    folds = per_fold.group_by(["fold", "clock", "n", "side", "policy"]).agg(
        pl.col("ret").count().alias("known_days"), pl.col("ret").mean().alias("ev")
    )
    paired = []
    for view in meta["views_run"]:
        reference = daily.filter(pl.col("policy") == f"{view}:stop").select(
            ["day", "clock", "n", "side", pl.col("ret").alias("stop_ret")]
        )
        compare = daily.join(reference, on=["day", "clock", "n", "side"]).filter(
            pl.col("ret").is_not_null() & pl.col("stop_ret").is_not_null()
        )
        pp = compare.group_by(["clock", "n", "side", "policy"]).agg(
            pl.len().alias("paired_days"),
            (pl.col("ret") - pl.col("stop_ret")).mean().alias("delta_stop"),
        )
        paired.extend(dict(r, reference=f"{view}:stop") for r in pp.to_dicts())
    timing = (
        daily.explode("exit_times", empty_as_null=False)
        .filter(pl.col("exit_times").is_not_null())
        .group_by(["clock", "n", "side", "policy"])
        .agg(
            pl.col("exit_times").median().alias("median_exit_et"),
            (pl.col("exit_times") < 780).mean().alias("sales_before_13_share"),
        )
    )
    artifact = {
        "kind": "DISCOVERY-OOF-PORTFOLIO-PROXY-NOT-VALIDATED-EDGE",
        "source_sha256": pin,
        "model_source_sha256": meta.get("source_sha256", meta.get("script_sha256")),
        "test_days": days,
        "model_evidence_flag": meta["evidence"],
        "summary": summary.to_dicts(),
        "folds": folds.to_dicts(),
        "paired": paired,
        "timing": timing.to_dicts(),
        "model_metadata_path": str(a.scores_root / "metadata.json"),
        "new_buy_quantity": (
            "90% of settled causal budget at completed decision mark; "
            "actual cost beyond reserved cash UNKNOWN, never resized"
        ),
        "caveats": [
            "100/150bps per actual traded leg pair is modeled, not quote/queue-certified.",
            "Partial dollar actions are linear interpolation absent impact/risk "
            "constraints; not separately optimized.",
            "Initial fixed3/5 claims and terminal session boundary are disclosed "
            "rulers; decisions are state-dependent.",
            "Re-entry bounded to one cycle; allocation is a separate diagnostic "
            "without a dwell/threshold grid.",
            "UNKNOWN cashflows counted/excluded, paired comparisons use joint "
            "known days; no protected-half read.",
        ],
    }
    a.evidence.parent.mkdir(parents=True, exist_ok=True)
    a.evidence.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    print(
        summary.filter((pl.col("side") == 0.005) & (pl.col("n") == 3)).sort(
            ["clock", "ev"], descending=[False, True]
        )
    )


if __name__ == "__main__":
    main()
