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


def _action_execution_dates(ticket: sim.Ticket, rule: ScaleOutRule | FullExitRule,
                            days: list[str], raw_paths: dict) -> list[str]:
    start = days.index(ticket.sleeve_day)
    date_cursor = start
    prior_et = -1
    dates = []
    session_ends = sim.session_end_map()
    for action in ticket.actions:
        if action["action"] not in ("REDUCE", "EXIT"):
            continue
        found = None
        for day_index in range(date_cursor, len(days)):
            day = days[day_index]
            if day_index == start:
                bars = raw_paths[(ticket.sleeve_day, ticket.ticker)]
                ets, opens = bars["_ets"], bars["_opens"]
            else:
                ticker_bars = sim.load_bars(day, session_ends[day]).ticker(ticket.ticker)
                if ticker_bars is None:
                    continue
                ets, opens = ticker_bars["et"].tolist(), ticker_bars["open"].tolist()
            for et, open_px in zip(ets, opens):
                if et != action["et"] or (day_index == date_cursor and et <= prior_et):
                    continue
                level = (ticket.entry_px * (1 + rule.trigger.L)
                         if isinstance(rule.trigger, sim.R2) and
                         action["reason"] != "FORCED_FLAT" else None)
                expected_px = min(open_px, level) if level is not None else open_px
                if abs(expected_px - action["px"]) <= 1e-9:
                    found = (day_index, day, int(et))
                    break
            if found:
                break
        if found is None:
            raise F4ContractError(f"could not map executed {action['action']} to canonical bar: "
                                  f"{ticket.sleeve_day} {ticket.ticker} {action}")
        date_cursor, day, prior_et = found
        dates.append(day)
    return dates


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
        tail_fraction_at_touch = {}
        reduced_before_touch = {}
        released_before_touch = {}
        for level, touch_text in path["first_touch_et"].items():
            touch_et = int(touch_text)
            remaining = ticket.shares_entry
            reduced = False
            exited = False
            for event in sequence:
                if (event["execution_date"] == day and event["et"] <= touch_et
                        and event["action"] in ("REDUCE", "EXIT")):
                    remaining = event["shares_remaining"]
                    if event["et"] < touch_et:
                        reduced |= event["action"] == "REDUCE"
                        exited |= event["action"] == "EXIT"
            tail_fraction_at_touch[level] = remaining / ticket.shares_entry
            reduced_before_touch[level] = reduced
            released_before_touch[level] = exited
        records.append({
            "sleeve_day": day, "ticker": ticker, "entry_et": ticket.entry_et,
            "shares_entry": ticket.shares_entry, "entry_px": ticket.entry_px,
            "actions": sequence, "first_touch_et": path["first_touch_et"],
            "tail_fraction_at_touch": tail_fraction_at_touch,
            "reduced_before_touch": reduced_before_touch,
            "released_before_touch": released_before_touch,
            "mfe_raw": path["mfe_raw"], "mae_raw": path["mae_raw"],
            "net": ticket.net(), "net_return": ticket.net_return(),
            "friction_cost": ticket.unit_notional - ticket.shares_entry * ticket.entry_px + exit_friction,
            "open_end": ticket.open, "remaining_shares": ticket.shares,
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
    for record in records:
        remaining_by_level = {}
        reduced_by_level = {}
        released_by_level = {}
        for level, touch_et in record["first_touch_et"].items():
            remaining = record["shares_entry"]
            reduced = False
            released = False
            for action in record["actions"]:
                if (action["execution_date"] != record["sleeve_day"]
                        or action["et"] > touch_et
                        or action["action"] not in ("REDUCE", "EXIT")):
                    continue
                remaining = action["shares_remaining"]
                if action["et"] < touch_et:
                    reduced |= action["action"] == "REDUCE"
                    released |= action["action"] == "EXIT"
            remaining_by_level[level] = remaining / record["shares_entry"]
            reduced_by_level[level] = reduced
            released_by_level[level] = released
        record["tail_fraction_at_touch"] = remaining_by_level
        record["reduced_before_touch"] = reduced_by_level
        record["released_before_touch"] = released_by_level
    return records
