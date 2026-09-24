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


def _controlled_simulation(strat, day, rec, bars, session_end, bps_total):
    if "AAA" not in strat.open_tickets:
        strat.open_tickets["AAA"] = basket_sim.Ticket(
            ticker="AAA", sleeve_day=DAY1, entry_et=570, entry_px=10.0,
            unit_notional=1.0, shares=0.1, cost_open=1.0, cash_in=1.0,
            shares_entry=0.1, peak=10.0,
        )
    tk = strat.open_tickets["AAA"]
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


def _write_complete_outputs(run_dir: Path) -> dict:
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
    }


def test_completed_run_returns_only_when_both_final_outputs_match_summary(
    tmp_path, monkeypatch
):
    days = [DAY1]
    cfg = _config(tmp_path, "complete", days)
    cfg.run_dir.mkdir(parents=True)
    (cfg.run_dir / "_progress.json").write_text(json.dumps({"done": days}))
    expected = _write_complete_outputs(cfg.run_dir)
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
    (run_dir / "_progress.json").write_text(json.dumps({"done": days}))
    summary = _write_complete_outputs(run_dir)
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
    (run_dir / "_progress.json").write_text(json.dumps({"done": [DAY1]}))

    rebuilt = basket_sim.run(cfg, progress=False)

    tickets = pl.read_parquet(run_dir / "tickets.parquet")
    assert rebuilt["ticket_rows"] == tickets.height == 1
    assert tickets.filter(pl.col("ticker") == "AAA").height == 1
