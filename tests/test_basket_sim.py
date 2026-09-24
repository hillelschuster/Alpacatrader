from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from factory.scripts import basket_sim


DAY1 = "2026-01-02"
DAY2 = "2026-01-05"


def _config(tmp_path: Path, run_id: str, days: list[str]) -> basket_sim.RunConfig:
    return basket_sim.RunConfig(
        family_id="test",
        run_id=run_id,
        spec=basket_sim.StrategySpec(name="resume-test"),
        out_root=tmp_path,
        days=days,
        workers=1,
    )


def _controlled_simulation(strat, day, rec, bars, session_end, bps_total, **kwargs):
    key = (DAY1, "AAA")
    if key not in strat.open_tickets:
        strat.open_tickets[key] = basket_sim.Ticket(
            ticker="AAA", sleeve_day=DAY1, entry_et=570, entry_px=10.0,
            unit_notional=1.0, shares=0.1, cost_open=1.0, cash_in=1.0,
            shares_entry=0.1, peak=10.0,
        )
    tk = strat.open_tickets[key]
    tk.mark = 0.75 if day == DAY1 else 0.8
    tk.last_close = tk.mark / tk.shares
    strat.day_rows.append({
        "date": day, "r_day": 0.0, "pnl": 0.0, "deployed_end": 1.0,
        "deployed_avg": 1.0, "n_actions": 1, "n_open_end": 1, "flags": [],
    })
    return strat.day_rows[-1]


def _install_controlled_inputs(monkeypatch):
    monkeypatch.setattr(basket_sim, "session_end_map", lambda: {})
    monkeypatch.setattr(
        basket_sim, "_read_day_pair",
        lambda day, *args: ({"date": day}, object()),
    )
    monkeypatch.setattr(basket_sim, "simulate_day", _controlled_simulation)


def test_partial_resume_matches_uninterrupted_parquet_with_one_terminal_ticket(
    tmp_path, monkeypatch
):
    _install_controlled_inputs(monkeypatch)
    partial_cfg = _config(tmp_path, "resumed", [DAY1])
    full_cfg = _config(tmp_path, "resumed", [DAY1, DAY2])
    baseline_cfg = _config(tmp_path, "baseline", [DAY1, DAY2])

    basket_sim.run(partial_cfg, progress=False)
    basket_sim.run(full_cfg, progress=False)
    basket_sim.run(baseline_cfg, progress=False)

    resumed_daily = pl.read_parquet(full_cfg.run_dir / "daily.parquet")
    baseline_daily = pl.read_parquet(baseline_cfg.run_dir / "daily.parquet")
    resumed_tickets = pl.read_parquet(full_cfg.run_dir / "tickets.parquet")
    baseline_tickets = pl.read_parquet(baseline_cfg.run_dir / "tickets.parquet")
    assert resumed_daily.equals(baseline_daily)
    assert resumed_tickets.equals(baseline_tickets)
    assert resumed_tickets.height == 1
    assert resumed_tickets.filter(pl.col("ticker") == "AAA").height == 1
    assert resumed_tickets["open_end"].to_list() == [True]


@pytest.mark.parametrize("progress_state", ["missing", "malformed"])
def test_parts_ahead_of_progress_rebuild_to_uninterrupted_outputs(
    tmp_path, monkeypatch, progress_state
):
    _install_controlled_inputs(monkeypatch)
    resumed_cfg = _config(tmp_path, f"interrupted-{progress_state}", [DAY1, DAY2])
    baseline_cfg = _config(tmp_path, "baseline-after-interrupt", [DAY1, DAY2])
    write_json = basket_sim._atomic_write_json

    def interrupt_before_progress(path, obj):
        if path.name == "_progress.json":
            raise RuntimeError("simulated crash after part flush")
        write_json(path, obj)

    monkeypatch.setattr(basket_sim, "_atomic_write_json", interrupt_before_progress)
    with pytest.raises(RuntimeError, match="simulated crash after part flush"):
        basket_sim.run(resumed_cfg, progress=False)

    progress_path = resumed_cfg.run_dir / "_progress.json"
    if progress_state == "malformed":
        progress_path.write_text("{")
    else:
        assert not progress_path.exists()
    monkeypatch.setattr(basket_sim, "_atomic_write_json", write_json)

    summary = basket_sim.run(resumed_cfg, progress=False)
    baseline_summary = basket_sim.run(baseline_cfg, progress=False)
    resumed_daily = pl.read_parquet(resumed_cfg.run_dir / "daily.parquet")
    baseline_daily = pl.read_parquet(baseline_cfg.run_dir / "daily.parquet")
    resumed_tickets = pl.read_parquet(resumed_cfg.run_dir / "tickets.parquet")
    baseline_tickets = pl.read_parquet(baseline_cfg.run_dir / "tickets.parquet")

    assert resumed_daily.equals(baseline_daily)
    assert resumed_tickets.equals(baseline_tickets)
    assert resumed_daily["date"].n_unique() == resumed_daily.height == 2
    assert resumed_tickets.filter(pl.col("ticker") == "AAA").height == 1
    assert resumed_tickets["open_end"].to_list() == [True]
    assert summary["daily_rows"] == resumed_daily.height
    assert summary["ticket_rows"] == resumed_tickets.height
    assert baseline_summary["daily_rows"] == baseline_daily.height
    assert baseline_summary["ticket_rows"] == baseline_tickets.height


def _progress_doc(cfg: basket_sim.RunConfig, days: list[str]) -> dict:
    return {"done": days,
            "fingerprint": basket_sim._run_fingerprint(cfg, days),
            "contract_version": basket_sim.CONTRACT_VERSION}


def _write_complete_outputs(run_dir: Path, cfg: basket_sim.RunConfig,
                            days: list[str]) -> dict:
    daily = pl.DataFrame([{
        "date": DAY1, "r_day": 0.0, "pnl": 0.0, "deployed_end": 0.0,
        "deployed_avg": 0.0, "n_actions": 0, "n_open_end": 0, "flags": "",
    }], schema=basket_sim._DAILY_SCHEMA)
    tickets = pl.DataFrame(schema=basket_sim._TICKET_SCHEMA)
    daily_path = run_dir / "daily.parquet"
    ticket_path = run_dir / "tickets.parquet"
    daily.write_parquet(daily_path)
    tickets.write_parquet(ticket_path)
    return {
        "days_run": 1,
        "daily_rows": daily.height,
        "ticket_rows": tickets.height,
        "daily_sha256": basket_sim._sha256_bytes(daily_path.read_bytes()),
        "tickets_sha256": basket_sim._sha256_bytes(ticket_path.read_bytes()),
        "fingerprint": basket_sim._run_fingerprint(cfg, days),
        "contract_version": basket_sim.CONTRACT_VERSION,
    }


def test_completed_run_returns_only_when_both_final_outputs_match_summary(
    tmp_path, monkeypatch
):
    days = [DAY1]
    cfg = _config(tmp_path, "complete", days)
    cfg.run_dir.mkdir(parents=True)
    (cfg.run_dir / "_progress.json").write_text(json.dumps(_progress_doc(cfg, days)))
    expected = _write_complete_outputs(cfg.run_dir, cfg, days)
    (cfg.run_dir / "parts" / "daily").mkdir(parents=True)
    (cfg.run_dir / "parts" / "tickets").mkdir(parents=True)
    pl.read_parquet(cfg.run_dir / "daily.parquet").write_parquet(
        cfg.run_dir / "parts" / "daily" / "2026-01.parquet"
    )
    (cfg.run_dir / "run_summary.json").write_text(json.dumps(expected))
    monkeypatch.setattr(
        basket_sim, "_read_day_pair",
        lambda *args: (_ for _ in ()).throw(AssertionError("trusted outputs reopened inputs")),
    )

    assert basket_sim.run(cfg, progress=False) == expected


def test_pre_c1_progress_or_summary_is_never_trusted(tmp_path, monkeypatch):
    """The same run id must not return pre-C1 semantics: a stale contract version
    or run fingerprint invalidates both the resume and the completed-run paths."""
    days = [DAY1]
    cfg = _config(tmp_path, "stale-contract", days)
    run_dir = cfg.run_dir
    (run_dir / "parts" / "daily").mkdir(parents=True)
    (run_dir / "parts" / "tickets").mkdir(parents=True)
    pl.DataFrame([{
        "date": DAY1, "r_day": 0.0, "pnl": 0.0, "deployed_end": 0.0,
        "deployed_avg": 0.0, "n_actions": 0, "n_open_end": 0, "flags": "",
    }], schema=basket_sim._DAILY_SCHEMA).write_parquet(
        run_dir / "parts" / "daily" / "2026-01.parquet")
    pl.DataFrame(schema=basket_sim._TICKET_SCHEMA).write_parquet(
        run_dir / "parts" / "tickets" / "2026-01.parquet")
    (run_dir / "_progress.json").write_text(json.dumps(
        {"done": days, "fingerprint": "deadbeef",
         "contract_version": "FROZEN-2026-09-22"}))
    stale = _write_complete_outputs(run_dir, cfg, days)
    stale["fingerprint"] = "deadbeef"
    stale["contract_version"] = "FROZEN-2026-09-22"
    (run_dir / "run_summary.json").write_text(json.dumps(stale))
    _install_controlled_inputs(monkeypatch)

    rebuilt = basket_sim.run(cfg, progress=False)

    assert rebuilt != stale
    assert rebuilt["fingerprint"] == basket_sim._run_fingerprint(cfg, days)
    assert rebuilt["contract_version"] == basket_sim.CONTRACT_VERSION
    assert pl.read_parquet(run_dir / "daily.parquet").height == 1


@pytest.mark.parametrize("corruption", ["missing_tickets", "mismatched_daily"])
def test_completed_run_rebuilds_when_final_output_integrity_fails(
    tmp_path, monkeypatch, corruption
):
    days = [DAY1]
    cfg = _config(tmp_path, f"invalid-{corruption}", days)
    run_dir = cfg.run_dir
    (run_dir / "parts" / "daily").mkdir(parents=True)
    (run_dir / "parts" / "tickets").mkdir(parents=True)
    daily = pl.DataFrame([{
        "date": DAY1, "r_day": 0.0, "pnl": 0.0, "deployed_end": 0.0,
        "deployed_avg": 0.0, "n_actions": 0, "n_open_end": 0, "flags": "",
    }], schema=basket_sim._DAILY_SCHEMA)
    tickets = pl.DataFrame(schema=basket_sim._TICKET_SCHEMA)
    daily.write_parquet(run_dir / "parts" / "daily" / "2026-01.parquet")
    tickets.write_parquet(run_dir / "parts" / "tickets" / "2026-01.parquet")
    (run_dir / "_progress.json").write_text(json.dumps(_progress_doc(cfg, days)))
    summary = _write_complete_outputs(run_dir, cfg, days)
    if corruption == "missing_tickets":
        (run_dir / "tickets.parquet").unlink()
    else:
        summary["daily_sha256"] = "wrong"
    (run_dir / "run_summary.json").write_text(json.dumps(summary))
    _install_controlled_inputs(monkeypatch)

    rebuilt = basket_sim.run(cfg, progress=False)

    assert rebuilt != summary
    assert (run_dir / "tickets.parquet").exists()


def test_duplicate_ticket_part_invalidates_resume_state(tmp_path, monkeypatch):
    _install_controlled_inputs(monkeypatch)
    cfg = _config(tmp_path, "duplicate-ticket-part", [DAY1])
    run_dir = cfg.run_dir
    daily_dir = run_dir / "parts" / "daily"
    ticket_dir = run_dir / "parts" / "tickets"
    daily_dir.mkdir(parents=True)
    ticket_dir.mkdir(parents=True)
    pl.DataFrame([{
        "date": DAY1, "r_day": 0.0, "pnl": 0.0, "deployed_end": 1.0,
        "deployed_avg": 1.0, "n_actions": 1, "n_open_end": 1, "flags": "",
    }], schema=basket_sim._DAILY_SCHEMA).write_parquet(daily_dir / "2026-01.parquet")
    ticket = basket_sim._normalize_row({
        "ticker": "AAA", "sleeve_day": DAY1, "entry_et": 570,
        "entry_px": 10.0, "unit_notional": 1.0, "shares_entry": 0.1,
        "n_adds": 0, "n_reduces": 0, "exit_et": None, "exit_px": None,
        "exit_reason": None, "open_end": True, "net": 0.0, "net_return": 0.0,
        "mfe_raw": 0.0, "mae_raw": 0.0, "peak": 10.0, "flags": "",
    }, basket_sim._TICKET_SCHEMA)
    pl.DataFrame([ticket, ticket], schema=basket_sim._TICKET_SCHEMA).write_parquet(
        ticket_dir / "2026-01.parquet"
    )
    (run_dir / "_progress.json").write_text(json.dumps(_progress_doc(cfg, [DAY1])))

    rebuilt = basket_sim.run(cfg, progress=False)

    tickets = pl.read_parquet(run_dir / "tickets.parquet")
    assert rebuilt["ticket_rows"] == tickets.height == 1
    assert tickets.filter(pl.col("ticker") == "AAA").height == 1


# --------------------------------------------------------------------------- #
# 2026-09-24 substrate-correction regressions
# --------------------------------------------------------------------------- #


def _entry_rec(day: str, ticker: str, px: float, et: int = 570) -> dict:
    return {"date": day, "snapshots": [{"pop": "A_pm", "T": 570, "names": [
        {"ticker": ticker, "rank": 1, "fill": {"et": et, "px": px, "blocked": False}}]}]}


def _strategy(**kw) -> basket_sim.Strategy:
    spec = basket_sim.StrategySpec(
        family_id="test", name="regression", entry_pop="A_pm", entry_T=570,
        top_n=1, n_slots=1, **kw)
    return basket_sim.Strategy(spec)


def _write_carry_substrate(root: Path, day: str, ticker: str,
                           rows: list[tuple[int, float]]) -> None:
    """Minimal declared carry overlay (see factory/BASKET-SIM-CONTRACT.md §13).

    Test-only: stamped ``production=false``, so the engine loads it only with
    ``require_production=False``.
    """
    root.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [day] * len(rows),
            "ticker": [ticker] * len(rows),
            "et": [int(r[0]) for r in rows],
            "open": [float(r[1]) for r in rows],
            "high": [float(r[1]) for r in rows],
            "low": [float(r[1]) for r in rows],
            "close": [float(r[1]) for r in rows],
            "volume": [100.0] * len(rows),
        },
        schema={"date": pl.Utf8, "ticker": pl.Utf8, "et": pl.Int64, "open": pl.Float64,
                "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                "volume": pl.Float64},
    ).write_parquet(root / f"{day}.parquet")
    (root / "manifest.json").write_text(json.dumps({
        "contract": basket_sim.CONTRACT_VERSION,
        "days": {day: {
            "source": {"production": False, "certification": "fixture_test_only"},
            "tickers": {ticker: {"status": "bars", "rows": len(rows),
                                 "first_et": rows[0][0], "last_et": rows[-1][0]}},
        }}}))
    assert not list(root.glob("*.tmp"))


def _fixture_substrate(root: Path) -> basket_sim.CarrySubstrate:
    return basket_sim.CarrySubstrate(root=root, require_production=False)


def _certify_only(root: Path, day: str, ticker: str, status: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    entry = {"status": status}
    if status == "no_bars":
        entry["rows"] = 0
    (root / "manifest.json").write_text(json.dumps({
        "contract": basket_sim.CONTRACT_VERSION,
        "days": {day: {
            "source": {"production": False, "certification": "fixture_test_only"},
            "tickers": {ticker: entry},
        }}}))
    # drop any stale day file so "bars" certification cannot be satisfied by rows
    (root / f"{day}.parquet").unlink(missing_ok=True)


def test_hsdt_carry_resolves_on_next_market_session_from_carry_substrate():
    """HSDT halted 2022-03-23 (last candidate bar et=942); the pending exit must
    resolve on 2022-03-24 at the first real market bar (et=570, open 3.32) --
    not on 2022-04-26, the next candidate-neighborhood appearance."""
    day0, day1 = "2022-03-23", "2022-03-24"
    sem = basket_sim.session_end_map()
    rec0 = basket_sim.load_anatomy(day0)
    bars0 = basket_sim.load_bars(day0, sem[day0])
    strat = basket_sim.Strategy(basket_sim.StrategySpec(
        family_id="test", name="hsdt-carry", entry_pop="A_pm", entry_T=570,
        top_n=10, n_slots=10, release=[basket_sim.R1(-8)]))

    basket_sim.simulate_day(strat, day0, rec0, bars0, sem[day0], 100.0)
    tk = strat.open_tickets[(day0, "HSDT")]
    assert tk.open and tk.pending is not None and tk.pending["carry"] is True
    assert tk.pending["reason"].startswith("R1")
    assert tk.entry_px == 3.5 and tk.entry_et == 570
    assert [t for t in strat.open_tickets.values()] == [tk]   # only HSDT carries

    bars1 = basket_sim.load_bars(day1, sem[day1])
    assert bars1.ticker("HSDT") is None                       # not a candidate that day

    carry = basket_sim.CarrySubstrate()
    row1 = basket_sim.simulate_day(strat, day1, basket_sim.load_anatomy(day1), bars1,
                                   sem[day1], 100.0, do_entries=False, carry=carry)

    assert not tk.open
    assert tk.exit_day == day1 and tk.exit_et == 570          # 2022-03-24 09:30 ET
    assert tk.exit_px == pytest.approx(min(3.32, tk.entry_px * 0.92))
    assert tk.exit_reason.startswith("R1")
    assert row1["n_actions"] == 1 and row1["n_open_end"] == 0
    # total-order chronology: the exit is dated on the resolving session
    assert (tk.exit_day, tk.exit_et) == (day1, 570)
    assert all(a["day"] == day0 for a in tk.actions[:-1])
    assert tk.actions[-1]["day"] == day1


def test_same_ticker_tickets_are_independent_across_sleeves(tmp_path):
    """Two basket-day sleeves may hold the same ticker at once: the carried
    day-1 ticket must not suppress the day-2 entry, and closing one must not
    remove the other from open_tickets."""
    day1, day2 = "2021-03-02", "2021-03-03"
    substrate_root = tmp_path / "carry_bars"
    bars1 = basket_sim._synthetic_bars(day1, {"XYZ": [
        (570, 10.0, 10.1, 9.95, 10.0, 100),
        (571, 10.0, 10.2, 9.90, 9.50, 100),      # tape ends: forced-flat carries
    ]})
    bars2 = basket_sim._synthetic_bars(day2, {"XYZ": [
        (570, 12.0, 12.5, 11.9, 12.2, 100),
        (571, 12.2, 12.6, 12.0, 12.4, 100),
        (572, 12.4, 12.8, 12.3, 12.6, 100),
    ]})
    strat = _strategy(release=[basket_sim.R0()])
    basket_sim.simulate_day(strat, day1, _entry_rec(day1, "XYZ", 10.0), bars1, 959, 100.0)
    t1 = strat.open_tickets[(day1, "XYZ")]
    assert t1.pending is not None and t1.pending["carry"] is True

    # a conflicting overlay must be ignored: candidate bars are canonical today
    _write_carry_substrate(substrate_root, day2, "XYZ", [(570, 99.0), (600, 99.0)])
    row2 = basket_sim.simulate_day(
        strat, day2, _entry_rec(day2, "XYZ", 12.0), bars2, 572, 100.0,
        carry=_fixture_substrate(substrate_root))

    t2 = [t for t in strat.closed if t.sleeve_day == day2][0]
    assert len(strat.closed) == 2
    assert (day1, "XYZ") not in strat.open_tickets
    assert (day2, "XYZ") not in strat.open_tickets
    assert t1.exit_day == day2 and t1.exit_et == 570 and t1.exit_px == pytest.approx(12.0)
    assert t2.exit_day == day2 and t2.exit_et == 572 and t2.exit_px == pytest.approx(12.4)
    assert t2.entry_et == 570 and t2.entry_px == pytest.approx(12.0)

    side = 100.0 / 2 / 10000
    sh1 = 1.0 / (10.0 * (1 + side))
    sh2 = 1.0 / (12.0 * (1 + side))
    assert t1.proceeds == pytest.approx(sh1 * 12.0 * (1 - side))
    assert t2.proceeds == pytest.approx(sh2 * 12.4 * (1 - side))
    assert strat.sleeve_cash[day1] == pytest.approx(1.0 + t1.net())
    assert strat.sleeve_cash[day2] == pytest.approx(1.0 + t2.net())
    assert strat.deployed[day1] == pytest.approx(0.0)
    assert strat.deployed[day2] == pytest.approx(0.0)
    assert row2["n_open_end"] == 0 and row2["deployed_end"] == pytest.approx(0.0)


def test_carry_coverage_absence_is_never_silently_no_trade(tmp_path):
    day1, day2 = "2021-03-05", "2021-03-08"
    bars1 = basket_sim._synthetic_bars(day1, {"XYZ": [
        (570, 10.0, 10.1, 9.95, 10.0, 100),
        (571, 10.0, 10.2, 9.90, 9.50, 100),      # tape ends -> carry
    ]})
    bars2 = basket_sim._synthetic_bars(day2, {"ABC": [(570, 5.0, 5.1, 4.9, 5.0, 100)]})
    strat = _strategy(release=[basket_sim.R0()])
    basket_sim.simulate_day(strat, day1, _entry_rec(day1, "XYZ", 10.0), bars1, 959, 100.0)
    tk = strat.open_tickets[(day1, "XYZ")]
    assert tk.pending is not None

    # 1) no certification at all -> hard failure, ticket untouched
    with pytest.raises(basket_sim.MissingCarrySubstrate) as exc:
        basket_sim.simulate_day(strat, day2, _entry_rec(day2, "ABC", 5.0), bars2, 572,
                                100.0, do_entries=False,
                                carry=_fixture_substrate(tmp_path))
    assert exc.value.requests == [(day2, "XYZ")]
    assert tk.open and tk.pending is not None
    assert [a["action"] for a in tk.actions] == ["ENTER"]   # nothing executed

    # 2) certified no_bars -> the carry genuinely continues, flagged
    _certify_only(tmp_path, day2, "XYZ", "no_bars")
    row = basket_sim.simulate_day(strat, day2, _entry_rec(day2, "ABC", 5.0), bars2, 572,
                                  100.0, do_entries=False,
                                  carry=_fixture_substrate(tmp_path))
    assert tk.open and "no_resumption" in tk.flags
    assert row["n_open_end"] == 1 and tk.pending is not None

    # 3) certified unavailable -> hard failure, not a silent carry
    _certify_only(tmp_path, day2, "XYZ", "unavailable")
    with pytest.raises(basket_sim.MissingCarrySubstrate):
        basket_sim.simulate_day(strat, day2, _entry_rec(day2, "ABC", 5.0), bars2, 572,
                                100.0, do_entries=False,
                                carry=_fixture_substrate(tmp_path))
    assert tk.open

    # 4) test-only fixture coverage can never certify a production run
    _certify_only(tmp_path, day2, "XYZ", "no_bars")
    strict = basket_sim.CarrySubstrate(root=tmp_path)
    with pytest.raises(basket_sim.MissingCarrySubstrate, match="production-certified"):
        strict.bars(day2, "XYZ", 572)
    assert tk.open


def test_half_release_rate_requires_cumulative_pre_touch_reduction():
    """half_release_rate[H] counts only tickets whose cumulative pre-touch
    REDUCE quantity is >= 50% of the pre-touch peak share count, with
    (day, et) chronology across sessions."""
    d1, d2 = "2021-01-04", "2021-01-05"

    def ticket(ticker, sleeve_day, reduces, touch):
        local = _strategy(release=[basket_sim.R0()])
        tk = basket_sim.Ticket(ticker=ticker, sleeve_day=sleeve_day, entry_et=570,
                               entry_px=10.0, unit_notional=1.0)
        basket_sim._execute(local, tk, "ENTER", 10.0, 570, "ENTRY", 0.0,
                            day=sleeve_day)
        for day, et, frac in reduces:
            basket_sim._execute(local, tk, "REDUCE", 11.0, et, "R-half", 0.0,
                                frac=frac, day=day)
        tk.h_touch_et[50] = touch[1]
        tk.h_touch_day[50] = touch[0]
        tk.mfe = 0.60
        return tk
    tickets = [
        # two 33%-of-current reduces remove 55.1% cumulatively -> yes
        ticket("A", d1, [(d1, 580, 0.33), (d1, 585, 0.33)], (d1, 590)),
        ticket("B", d1, [(d1, 580, 0.25)], (d1, 590)),                    # 25% -> no
        ticket("C", d1, [(d1, 580, 0.50)], (d1, 590)),                    # 50% -> yes
        ticket("D", d1, [(d1, 580, 0.25), (d1, 595, 0.50)], (d1, 590)),   # pre-touch 25% -> no
        ticket("E", d1, [(d1, 600, 0.50)], (d2, 570)),                    # prior session -> yes
        ticket("F", d2, [(d2, 600, 0.50)], (d2, 570)),                    # after touch -> no
    ]
    days = [{"date": d1, "r_day": 0.0, "pnl": 0.0, "deployed_avg": 0.0,
             "n_actions": 0, "n_open_end": 0, "flags": []}]
    m = basket_sim.compute_metrics(days, tickets, 1, 0, 0, 0, draws=10)

    assert m["half_release_rate"]["50"] == pytest.approx(3 / 6)
    assert m["false_release_rate"]["50"] == pytest.approx(0.0)


def test_deployed_end_tracks_open_cost_basis_after_reduce():
    """deployed_end is the current open cost basis: a 50% REDUCE halves it,
    while cash_in (lifetime spend) stays at the full unit notional."""
    day = "2021-03-04"

    class HalfReduce(basket_sim.ReleaseRule):
        name = "R-half"

        def evaluate(self, tk, bar, idx):
            st = self.state(tk)
            if st.get("done"):
                return None
            if bar["low"] <= tk.entry_px * 0.95:
                st["done"] = True
                return {"action": "REDUCE", "reason": self.name, "level": None, "frac": 0.5}
            return None

    bars = basket_sim._synthetic_bars(day, {"XYZ": [
        (570, 10.0, 10.1, 10.0, 10.05, 100),
        (571, 10.05, 10.1, 9.40, 9.60, 100),     # low <= 9.5 -> REDUCE scheduled
        (572, 9.60, 9.80, 9.50, 9.70, 100),      # executes at this open; tape ends
    ]})
    strat = _strategy(release=[HalfReduce()])
    row = basket_sim.simulate_day(strat, day, _entry_rec(day, "XYZ", 10.0), bars, 960, 100.0)

    tk = strat.open_tickets[(day, "XYZ")]
    assert tk.n_reduces == 1 and tk.open
    assert tk.cost_open == pytest.approx(0.5)
    assert tk.cash_in == pytest.approx(1.0)
    assert tk.shares == pytest.approx(tk.shares_entry * 0.5)
    assert row["deployed_end"] == pytest.approx(tk.cost_open) == pytest.approx(0.5)
    # Duration-weighted (minutes) deployed capital: 1.0 for two minutes
    # (entry bar 570 through the REDUCE execution at 572), then 0.5 cost basis
    # for the remaining 388 session minutes (to the 960 close) => 196/390.
    assert row["deployed_avg"] == pytest.approx(196.0 / 390.0)
    assert row["n_open_end"] == 1


def test_carry_never_bridges_a_dev_block_gap(tmp_path, monkeypatch):
    """A ticket still open on the last day of a dev block is terminally marked
    at that boundary and retained in tickets/metrics -- it is never carried
    into the next block (2023-12 -> 2025-02 in the real calendar)."""
    day_a, day_b = "2021-03-01", "2021-04-15"        # 45-day gap > DEV_BLOCK_GAP_DAYS
    monkeypatch.setattr(basket_sim, "session_end_map", lambda: {})
    monkeypatch.setattr(basket_sim, "_read_day_pair", lambda day, *a: ({"date": day}, object()))

    def keep_open(strat, day, rec, bars, session_end, bps_total, **kw):
        if day == day_a and not strat.open_tickets:
            strat.open_tickets[(day, "AAA")] = basket_sim.Ticket(
                ticker="AAA", sleeve_day=day, entry_et=570, entry_px=10.0,
                unit_notional=1.0, shares=0.1, cost_open=1.0, cash_in=1.0,
                shares_entry=0.1, peak=10.0, last_close=10.5, mark=1.05)
        strat.day_rows.append({
            "date": day, "r_day": 0.0, "pnl": 0.0, "deployed_end": 1.0,
            "deployed_avg": 1.0, "n_actions": 1,
            "n_open_end": sum(1 for tk in strat.open_tickets.values() if tk.open),
            "flags": [],
        })
        return strat.day_rows[-1]

    monkeypatch.setattr(basket_sim, "simulate_day", keep_open)
    cfg = _config(tmp_path, "block-boundary", [day_a, day_b])
    summary = basket_sim.run(cfg, progress=False)

    tickets = pl.read_parquet(cfg.run_dir / "tickets.parquet")
    assert tickets.height == summary["ticket_rows"] == 1
    row = tickets.row(0, named=True)
    assert row["sleeve_day"] == day_a and row["open_end"] is False
    assert row["exit_reason"] == "BLOCK_BOUNDARY_MARK"
    assert row["exit_day"] is None and row["exit_et"] is None
    assert row["exit_px"] == pytest.approx(10.5)
    assert "block_boundary_carry" in row["flags"]
    assert row["net"] == pytest.approx(1.05 - 1.0)    # terminal mark recognized once
    assert summary["metrics"]["n_entries"] == 1
    assert summary["metrics"]["n_exits"] == 0            # a boundary mark is not a trade
    assert summary["metrics"]["n_terminal_marks"] == 1
    assert pl.read_parquet(cfg.run_dir / "daily.parquet").height == 2


def test_final_bar_pending_carries_a_forced_exit():
    """A pending action that executes on the forced-flat execution bar and
    leaves shares open must still flatten: the remainder carries a FORCED_FLAT
    exit to the next session's first executable bar (2026-09-24 fix)."""
    day1, day2 = "2021-04-01", "2021-04-05"

    class HalfReduceOnce(basket_sim.ReleaseRule):
        name = "R-half"

        def evaluate(self, tk, bar, idx):
            st = self.state(tk)
            if st.get("done"):
                return None
            if bar["low"] <= 9.5:
                st["done"] = True
                return {"action": "REDUCE", "reason": self.name, "level": None, "frac": 0.5}
            return None

    bars1 = basket_sim._synthetic_bars(day1, {"XYZ": [
        (570, 10.0, 10.1, 10.0, 10.0, 100),
        (571, 10.0, 10.1, 10.0, 10.0, 100),
        (574, 9.6, 9.7, 9.0, 9.6, 100),      # breach -> REDUCE scheduled (after 574)
        (576, 9.8, 9.9, 9.7, 9.8, 100),      # session_end: 575 has no bar, so the
    ]})                                      # pending first executes here
    strat = _strategy(release=[HalfReduceOnce()])
    basket_sim.simulate_day(strat, day1, _entry_rec(day1, "XYZ", 10.0), bars1, 576, 100.0)
    tk = strat.open_tickets[(day1, "XYZ")]
    assert tk.n_reduces == 1
    assert tk.shares == pytest.approx(tk.shares_entry * 0.5)
    assert tk.pending is not None and tk.pending["reason"] == "FORCED_FLAT"
    assert tk.pending["carry"] is True

    bars2 = basket_sim._synthetic_bars(day2, {"XYZ": [
        (570, 12.0, 12.2, 11.9, 12.1, 100),
        (571, 12.1, 12.2, 12.0, 12.1, 100),
    ]})
    basket_sim.simulate_day(strat, day2, _entry_rec(day2, "XYZ", 12.0), bars2, 571, 100.0,
                            do_entries=False)
    assert not tk.open
    assert (tk.exit_day, tk.exit_et) == (day2, 570)
    assert tk.exit_reason == "FORCED_FLAT"
    assert tk.exit_px == pytest.approx(12.0)


def test_production_no_bars_still_verifies_overlay_integrity(tmp_path):
    """Fail-closed carry coverage: a production-certified ``no_bars`` entry is
    only usable while its day parquet matches the manifest hash and the manifest
    declares its source/window; tampering or a stripped manifest is a hard
    failure, never a silent 'no trade' (2026-09-24 fix)."""
    import json as _json

    day, ticker = "2021-03-08", "XYZ"
    root = tmp_path / "carry_bars"
    root.mkdir()
    frame = pl.DataFrame({
        "date": [day], "ticker": ["OTHER"], "et": [570], "open": [1.0],
        "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0],
    }, schema=basket_sim.CARRY_BARS_SCHEMA)
    path = root / f"{day}.parquet"
    frame.write_parquet(path)
    sha = basket_sim._sha256_bytes(path.read_bytes())
    production_source = {
        "production": True, "certification": "alpaca_sip_raw", "kind": "alpaca_sip_raw",
        "feed": "sip", "adjustment": "raw", "timeframe": "1Min",
        "overlay_sha256": sha, "requested": 1, "returned": 0, "no_bars": 1, "unavailable": 0,
    }
    (root / "manifest.json").write_text(_json.dumps({
        "contract": basket_sim.CONTRACT_VERSION,
        "days": {day: {"session_end": 959, "source": production_source,
                       "tickers": {ticker: {"status": "no_bars", "rows": 0}}}},
    }))
    substrate = basket_sim.CarrySubstrate(root=root, require_production=True)
    assert substrate.bars(day, ticker, 959) is None           # verified no_bars

    path.write_bytes(path.read_bytes() + b"\x00")             # tamper the day file
    with pytest.raises(basket_sim.MissingCarrySubstrate, match="sha256"):
        # a fresh substrate (new run) re-verifies integrity on first use
        basket_sim.CarrySubstrate(root=root, require_production=True).bars(day, ticker, 959)

    stripped = _json.loads((root / "manifest.json").read_text())
    del stripped["days"][day]["source"]["feed"]
    (root / "manifest.json").write_text(_json.dumps(stripped))
    with pytest.raises(basket_sim.MissingCarrySubstrate, match="missing"):
        basket_sim.CarrySubstrate(root=root, require_production=True).bars(day, ticker, 959)


def test_post_exit_path_touches_are_tracked():
    """Raw path classes are properties of the tape, not of the holding period: a
    ticket released early must still register the session's later H-touches, so
    false_release_rate is not truncated at the exit bar (2026-09-24 fix)."""
    day = "2021-04-06"
    bars = basket_sim._synthetic_bars(day, {"XYZ": [
        (570, 10.0, 10.1, 10.0, 10.0, 100),
        (571, 10.0, 10.1, 10.0, 10.0, 100),
        (572, 10.0, 10.1, 10.0, 10.0, 100),
        (573, 10.0, 10.1, 9.4, 9.5, 100),     # R1(-5) breach -> exit at 574
        (574, 9.5, 9.6, 9.4, 9.5, 100),
        (575, 12.0, 12.5, 11.9, 12.2, 100),
        (576, 15.0, 15.4, 14.8, 15.0, 100),   # +54% from the 10.0 fill
    ]})
    strat = _strategy(release=[basket_sim.R1(-5)])
    basket_sim.simulate_day(strat, day, _entry_rec(day, "XYZ", 10.0), bars, 576, 100.0)
    tk = strat.closed[0]
    assert tk.exit_et == 574 and tk.shares == 0.0          # flat before the rally
    assert tk.h_touch_et[50] == 576 and tk.h_touch_day[50] == day
    assert tk.mfe == pytest.approx(15.4 / 10.0 - 1.0)      # full post-fill path

    metrics = basket_sim.compute_metrics(strat.day_rows, [tk], 1, 0, 0, 0, draws=10)
    assert metrics["false_release_rate"]["50"] == pytest.approx(1.0)
