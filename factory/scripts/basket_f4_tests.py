#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))

from basket_f4_analysis import exact_fraction, replay_ticket  # noqa: E402
from basket_f4_scaleout import PatternRule, TrigR1  # noqa: E402


class TicketStub:
    def __init__(self) -> None:
        self.entry_px = 10.0
        self.rule_state: dict = {}


def test_exact_third() -> None:
    assert exact_fraction("R1_10:REDUCE:0.3333:1") == 1.0 / 3.0


def test_terminal_mark_reconciles() -> None:
    row = {
        "shares_entry": 1.0,
        "unit_notional": 10.0,
        "open_end": True,
        "net": 2.0,
        "actions_json": '[{"et":570,"action":"ENTER","px":10,"reason":"ENTRY"}]',
    }
    tranches, total = replay_ticket(row, 0.005)
    assert tranches == [{"label": "terminal_mark", "pnl": 2.0, "notional": 10.0}]
    assert total == row["net"]


def test_reclaim_separates_stages() -> None:
    ticket = TicketStub()
    rule = PatternRule(
        TrigR1(), [("REDUCE", 0.25), ("EXIT", None)], name="R1_10"
    )
    breach = {"low": 8.9, "close": 8.9}
    reclaim = {"low": 9.1, "close": 9.1}

    assert rule.evaluate(ticket, breach, 0)["action"] == "REDUCE"
    assert rule.evaluate(ticket, breach, 1) is None
    assert rule.evaluate(ticket, reclaim, 2) is None
    assert rule.evaluate(ticket, breach, 3)["action"] == "EXIT"


def main() -> None:
    test_exact_third()
    test_terminal_mark_reconciles()
    test_reclaim_separates_stages()
    print("basket_f4_tests: PASS")


if __name__ == "__main__":
    main()
