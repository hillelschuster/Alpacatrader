from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import basket_sim as bs
from basket_f67_capital import CapitalCell, CapitalStrategy, build_tier1_cells


def _record(day: str, tickers: tuple[str, ...]) -> dict:
    return {
        "date": day,
        "snapshots": [{
            "pop": "A_pm",
            "T": 570,
            "names": [
                {"ticker": ticker, "rank": rank, "fill": {"et": 570, "px": 10.0, "blocked": False}}
                for rank, ticker in enumerate(tickers, start=1)
            ],
        }],
    }


def test_tier1_registry_matches_frozen_plan() -> None:
    cells = build_tier1_cells()
    assert len(cells) == 156
    assert len({cell.run_id for cell in cells}) == 156
    assert sum(cell.study == "reserve" for cell in cells) == 114
    assert sum(cell.study == "recycle" for cell in cells) == 42


def test_reserve_routes_equally_at_next_bar_without_creating_cash() -> None:
    day = "2021-03-01"
    tickers = ("AAA", "BBB", "CCC")
    series = {
        ticker: [
            (570, 10.0, 10.1, 9.9, 10.0, 100),
            (584, 10.0, 10.1, 9.9, 10.0, 100),
            (585, 10.0, 10.1, 9.9, 10.0, 100),
            (586, 10.0, 10.1, 9.9, 10.0, 100),
            (587, 10.0, 10.1, 9.9, 10.0, 100),
            (588, 10.0, 10.1, 9.9, 10.0, 100),
        ]
        for ticker in tickers
    }
    bars = bs._synthetic_bars(day, series)
    cell = CapitalCell.reserve("A_pm", "R0", 0.5, 585, "equal")
    strategy = CapitalStrategy(cell.spec(), cell, {})
    strategy.begin_day(day, bars)

    bs.simulate_day(strategy, day, _record(day, tickers), bars, 588, 100.0)

    adds = [action for ticket in strategy.closed for action in ticket.actions if action["action"] == "ADD"]
    assert len(adds) == 3
    assert {action["et"] for action in adds} == {586}
    assert abs(strategy.sleeve_cash[day]) < 1e-12
    assert strategy.max_deployed <= 1.0 + 1e-12
    assert strategy.cash_created_abs <= 1e-12


def test_r2_recycling_uses_exit_proceeds_only_after_exit_fill() -> None:
    day = "2021-03-01"
    series = {
        "EXIT": [
            (570, 10.0, 10.1, 9.9, 10.0, 100),
            (571, 10.0, 10.0, 8.9, 9.0, 100),
            (572, 9.0, 9.1, 8.8, 8.9, 100),
            (573, 8.9, 9.0, 8.8, 8.9, 100),
            (574, 8.9, 9.0, 8.8, 8.9, 100),
            (575, 8.9, 9.0, 8.8, 8.9, 100),
            (576, 8.9, 9.0, 8.8, 8.9, 100),
            (577, 8.9, 9.0, 8.8, 8.9, 100),
            (578, 8.9, 9.0, 8.8, 8.9, 100),
            (579, 8.9, 9.0, 8.8, 8.9, 100),
            (580, 8.9, 9.0, 8.8, 8.9, 100),
        ],
        "HOLD": [
            (et, 10.0, 10.2, 9.9, 10.1, 100) for et in (570, 571, 572, 573, 574, 575, 576, 577, 578, 579, 580)
        ],
    }
    bars = bs._synthetic_bars(day, series)
    cell = CapitalCell.recycle("A_pm", 1.0, None, "equal")
    strategy = CapitalStrategy(cell.spec(n_slots=2), cell, {})
    strategy.begin_day(day, bars)

    bs.simulate_day(strategy, day, _record(day, ("EXIT", "HOLD")), bars, 580, 100.0)

    exit_ticket = next(ticket for ticket in strategy.closed if ticket.ticker == "EXIT")
    hold_ticket = next(ticket for ticket in strategy.closed if ticket.ticker == "HOLD")
    add = next(action for action in hold_ticket.actions if action["action"] == "ADD")
    assert exit_ticket.exit_et == 577
    assert add["et"] > exit_ticket.exit_et
    assert strategy.recycled_executed > 0.0
    assert strategy.recycled_executed <= exit_ticket.proceeds + 1e-12
    assert strategy.max_deployed <= 1.0 + 1e-12
    assert strategy.cash_created_abs <= 1e-12
