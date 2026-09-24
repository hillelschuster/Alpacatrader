from __future__ import annotations

import polars as pl

try:
    from factory.scripts import basket_sim as sim
    from factory.scripts.basket_f4_rules import F4ContractError, FullExitRule, ScaleOutRule
except ImportError:
    import basket_sim as sim
    from basket_f4_rules import F4ContractError, FullExitRule, ScaleOutRule


def path_metrics(  # noqa: DICT_OK — JSON-compatible measured path record
        fill_px: float, et: list[int], high: list[float], low: list[float],
        fill_et: int) -> dict:
    indices = [i for i, minute in enumerate(et) if minute >= fill_et]
    if not indices:
        return {"mfe_raw": 0.0, "mae_raw": 0.0, "first_touch_et": {}}
    highs = [high[i] for i in indices]
    lows = [low[i] for i in indices]
    return {
        "mfe_raw": max(0.0, max(highs) / fill_px - 1),
        "mae_raw": min(0.0, min(lows) / fill_px - 1),
        "first_touch_et": {
            str(level): next(et[i] for i in indices if high[i] >= fill_px * (1 + level / 100))
            for level in (30, 50, 100)
            if any(high[i] >= fill_px * (1 + level / 100) for i in indices)
        },
    }


def raw_path_map(days: list[str]) -> dict[tuple[str, str], dict]:
    paths = {}
    session_ends = sim.session_end_map()
    for day in days:
        record = sim.load_anatomy(day)
        snapshot = sim.snapshot_of(record, "A_pm", 570)
        bars = sim.load_bars(day, session_ends[day])
        for name in snapshot["names"][:2]:
            fill = name.get("fill")
            if not fill or fill.get("blocked"):
                continue
            ticker = name["ticker"]
            ticker_bars = bars.ticker(ticker)
            if ticker_bars is None:
                raise F4ContractError(f"filled F4 candidate lacks bars: {day} {ticker}")
            paths[(day, ticker)] = path_metrics(float(fill["px"]),
                                                ticker_bars["et"].tolist(),
                                                ticker_bars["high"].tolist(),
                                                ticker_bars["low"].tolist(),
                                                int(fill["et"]))
            paths[(day, ticker)]["_ets"] = ticker_bars["et"].tolist()
            paths[(day, ticker)]["_opens"] = ticker_bars["open"].tolist()
    return paths


def _tape_for(day: str, ticker: str, session_ends: dict, carry,
              raw_paths: Optional[dict] = None) -> Optional[tuple]:
    """(ets, opens) for ``ticker`` on ``day``: the entry-session path map when
    available, else canonical candidate bars, else the certified carry overlay
    (C1).  Uncertified coverage is a hard error, never a silent skip."""
    entry = raw_paths.get((day, ticker)) if raw_paths else None
    if entry is not None:
        return entry["_ets"], entry["_opens"]
    end = session_ends.get(day, sim.SESSION_END_NORMAL)
    ticker_bars = sim.load_bars(day, end).ticker(ticker)
    if ticker_bars is None and carry is not None:
        try:
            ticker_bars = carry.bars(day, ticker, end)
        except sim.MissingCarrySubstrate as exc:
            raise F4ContractError(str(exc)) from exc
    if ticker_bars is None:
        return None
    return ticker_bars["et"].tolist(), ticker_bars["open"].tolist()


def _action_execution_dates(ticket: sim.Ticket, rule: ScaleOutRule | FullExitRule,
                            days: list[str], raw_paths: dict) -> list[str]:
    """Execution dates for a ticket's REDUCE/EXIT actions.

    C1: the engine records the execution session on every action, so the dates
    are authoritative and only *validated* here against that session's tape
    (candidate bars, or the carry overlay when the ticker is not a candidate);
    the pre-C1 version re-derived them by scanning candidate bars and could not
    see a carried execution at all.
    """
    session_ends = sim.session_end_map()
    carry = sim.CarrySubstrate()
    dates = []
    for action in ticket.actions:
        if action["action"] not in ("REDUCE", "EXIT"):
            continue
        day = action.get("day") or ticket.sleeve_day
        tape = _tape_for(day, ticket.ticker, session_ends, carry, raw_paths)
        if tape is None:
            raise F4ContractError(
                f"no tape for executed {action['action']} on {day}: "
                f"{ticket.sleeve_day} {ticket.ticker}")
        ets, opens = tape
        hit = [open_px for et, open_px in zip(ets, opens) if int(et) == int(action["et"])]
        if not hit:
            raise F4ContractError(
                f"execution bar {action['et']} missing from {day} tape: "
                f"{ticket.sleeve_day} {ticket.ticker}")
        level = (ticket.entry_px * (1 + rule.trigger.L)
                 if isinstance(rule.trigger, sim.R2) and action["reason"] != "FORCED_FLAT"
                 else None)
        expected_px = min(hit[0], level) if level is not None else hit[0]
        if abs(expected_px - action["px"]) > 1e-9:
            raise F4ContractError(
                f"execution price mismatch on {day} et={action['et']}: "
                f"{ticket.sleeve_day} {ticket.ticker} engine={action['px']} tape={expected_px}")
        dates.append(day)
    return dates


def _touch_records(ticket: sim.Ticket, path: dict) -> dict[str, dict]:
    """First-touch (day, et) per level: the engine's own post-fill record when
    present, else the entry-session path map."""
    touches: dict[str, dict] = {}
    levels = {int(lv) for lv in path["first_touch_et"]} | set(ticket.h_touch_et)
    for level in sorted(levels):
        day = ticket.h_touch_day.get(level)
        et = ticket.h_touch_et.get(level)
        if day is not None and et is not None:
            touches[str(level)] = {"day": day, "et": int(et)}
        elif str(level) in path["first_touch_et"]:
            touches[str(level)] = {"day": ticket.sleeve_day,
                                   "et": int(path["first_touch_et"][str(level)])}
    return touches


def _path_positions(ticket: sim.Ticket, touches: dict[str, dict],
                    sequence: list[dict]) -> tuple[dict, dict, dict]:
    """Per-level (tail fraction at touch, cumulative >=50% reduction before the
    touch, fully flat before the touch) using the (day, et) total order and the
    contract's cumulative-share definition of an early reduction (C1)."""
    tail, reduced_half, released = {}, {}, {}
    for level_text, touch in touches.items():
        key = (touch["day"], touch["et"])
        remaining = ticket.shares_entry
        peak = ticket.shares_entry
        reduced_shares = 0.0
        flat_before = False
        for event in sequence:
            # shares held at the touch bar (events on the touch bar included)
            if (event["execution_date"], event["et"]) <= key:
                remaining = event["shares_remaining"]
            if (event["execution_date"], event["et"]) >= key:
                continue
            if event["action"] == "REDUCE":
                reduced_shares += event["shares_sold"]
            after = event["shares_remaining"]
            if after > peak:
                peak = after
            if event["action"] == "EXIT":
                flat_before = True
        tail[level_text] = remaining / ticket.shares_entry
        reduced_half[level_text] = bool(peak > 0 and reduced_shares >= 0.5 * peak - 1e-12)
        released[level_text] = flat_before
    return tail, reduced_half, released


def ticket_chronology(rule: ScaleOutRule | FullExitRule, bps: int,
                      raw_paths: dict[tuple[str, str], dict], days: list[str]) -> list[dict]:
    side = bps / 20_000
    records = []
    for (day, ticker), ticket in sorted(rule._tickets.items()):
        path = raw_paths[(day, ticker)]
        action_dates = _action_execution_dates(ticket, rule, days, raw_paths)
        shares_left = ticket.shares_entry
        cost_left = ticket.unit_notional
        sequence = []
        exit_friction = 0.0
        action_date_iter = iter(action_dates)
        for action in ticket.actions:
            if action["action"] not in ("REDUCE", "EXIT"):
                continue
            execution_date = next(action_date_iter)
            if action["action"] == "REDUCE":
                stage = int(action["reason"].rsplit(":stage", 1)[-1]) - 1
                fraction = rule.fractions[stage]
            else:
                fraction = 1.0
            sold = shares_left * fraction
            cost = cost_left * fraction
            proceeds = sold * action["px"] * (1 - side)
            exit_friction += sold * action["px"] * side
            sequence.append({**action, "execution_date": execution_date,
                             "shares_sold": sold, "fraction_of_current": fraction,
                             "cost_basis_removed": cost, "net_tranche_pnl": proceeds - cost,
                             "shares_remaining": shares_left - sold})
            shares_left -= sold
            cost_left -= cost
        touches = _touch_records(ticket, path)
        tail_fraction_at_touch, reduced_before_touch, released_before_touch = (
            _path_positions(ticket, touches, sequence))
        records.append({
            "sleeve_day": day, "ticker": ticker, "entry_et": ticket.entry_et,
            "shares_entry": ticket.shares_entry, "entry_px": ticket.entry_px,
            "actions": sequence, "first_touch": touches,
            # raw path metrics are the engine's post-fill, post-exit record (C1)
            "mfe_raw": ticket.mfe, "mae_raw": ticket.mae,
            "tail_fraction_at_touch": tail_fraction_at_touch,
            "reduced_before_touch": reduced_before_touch,
            "released_before_touch": released_before_touch,
            "net": ticket.net(), "net_return": ticket.net_return(),
            "friction_cost": ticket.unit_notional - ticket.shares_entry * ticket.entry_px + exit_friction,
            "open_end": ticket.open, "remaining_shares": ticket.shares,
            "terminal_kind": ticket.terminal_kind,
            "pending": ticket.pending, "flags": list(ticket.flags),
        })
    return records


def ticket_actions(rule, bps: int, raw_paths: dict[tuple[str, str], dict],
                   days: list[str]) -> list[dict]:
    match rule:
        case ScaleOutRule() | FullExitRule():
            return ticket_chronology(rule, bps, raw_paths, days)
        case sim.R0() | sim.R1() | sim.R2() | sim.R3():
            return []
        case unreachable:
            from typing import assert_never
            assert_never(unreachable)


def refresh_touch_chronology(records: list[dict]) -> list[dict]:
    """Recompute per-level tail/reduction fields from the stored sequence and
    the stored first-touch (day, et) record (idempotent; used on resume)."""
    for record in records:
        touches = record.get("first_touch")
        if not touches:
            touches = {level: {"day": record["sleeve_day"], "et": int(et)}
                       for level, et in record.get("first_touch_et", {}).items()}
        sequence = record["actions"]
        shares_entry = record["shares_entry"]
        tail, reduced_half, released = {}, {}, {}
        for level_text, touch in touches.items():
            key = (touch["day"], touch["et"])
            remaining = shares_entry
            peak = shares_entry
            reduced_shares = 0.0
            flat_before = False
            for event in sequence:
                if (event["execution_date"], event["et"]) <= key:
                    remaining = event["shares_remaining"]
                if (event["execution_date"], event["et"]) >= key:
                    continue
                if event["action"] == "REDUCE":
                    reduced_shares += event["shares_sold"]
                if event["shares_remaining"] > peak:
                    peak = event["shares_remaining"]
                if event["action"] == "EXIT":
                    flat_before = True
            tail[level_text] = remaining / shares_entry
            reduced_half[level_text] = bool(peak > 0 and reduced_shares >= 0.5 * peak - 1e-12)
            released[level_text] = flat_before
        record["tail_fraction_at_touch"] = tail
        record["reduced_before_touch"] = reduced_half
        record["released_before_touch"] = released
    return records
