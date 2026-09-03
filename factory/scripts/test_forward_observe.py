"""Unit tests for the forward observer's pure selection rules (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.forward_observe import (  # noqa: E402
    rule_top_gain, rule_gain_x_vol, rule_sep_shortlist, select_watchlist,
)


def rows():
    return [
        {"symbol": "A", "percent_gain": 40.0, "dollar_volume": 1e6},
        {"symbol": "B", "percent_gain": 25.0, "dollar_volume": 50e6},
        {"symbol": "C", "percent_gain": 20.0, "dollar_volume": 5e6},
        {"symbol": "D", "percent_gain": 15.0, "dollar_volume": 100e6},
        {"symbol": "E", "percent_gain": None, "dollar_volume": 1e6},
    ]


def test_top_gain_order_and_none_last():
    assert rule_top_gain(rows(), k=2) == ["A", "B"]


def test_gain_x_vol_reorders():
    top = rule_gain_x_vol(rows(), k=4)
    assert "D" in top  # huge $-vol keeps lower-gain D in a 4-list


def test_sep_shortlist_diff():
    out = dict(rule_sep_shortlist(rows(), k=3))
    assert abs(out["A"]["diff_pp"] - 15.0) < 1e-9
    assert abs(out["B"]["diff_pp"] - 5.0) < 1e-9


def test_select_watchlist_union_capped():
    promoted, debug = select_watchlist(rows())
    assert len(promoted) <= 8
    assert set(promoted) >= {"A", "B"}
    assert all("top_gain" in debug[s] for s in promoted)


def test_observer_module_has_no_order_imports():
    lines = Path("factory/scripts/forward_observe.py").read_text().splitlines()
    imports = [ln for ln in lines if ln.startswith(("import ", "from "))]
    blob = "\n".join(imports)
    for forbidden in ("TradingClient", "paper_execution"):
        assert forbidden not in blob
