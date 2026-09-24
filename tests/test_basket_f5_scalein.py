from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from factory.scripts import basket_f5_scalein as f5
from factory.scripts import basket_sim as sim

DAYS = ["2021-02-01", "2021-02-02"]
SESSION_END = 959
_TICKET_SCHEMA = {"sleeve_day": pl.Utf8, "ticker": pl.Utf8, "open_end": pl.Boolean, "net": pl.Float64,
                  "flags": pl.Utf8, "unit_notional": pl.Float64, "shares_entry": pl.Float64,
                  "n_reduces": pl.Int64, "exit_px": pl.Float64, "exit_day": pl.Utf8,
                  "exit_et": pl.Int64, "exit_reason": pl.Utf8}
_BAR_SCHEMA = {"ticker": pl.Utf8, "et": pl.Int64, "open": pl.Float64, "high": pl.Float64,
               "close": pl.Float64}


# --------------------------------------------------------------------------- #
# Rule behavior (unchanged semantics)
# --------------------------------------------------------------------------- #


def test_rule_triggers_only_strict_new_peak_per_ticket() -> None:
    rule = f5.ScaleInRule((0.25, 0.25))
    first = sim.Ticket("AAA", "2021-02-01", 570, 10.0, 0.5, peak=10.0)
    second = sim.Ticket("BBB", "2021-02-01", 570, 10.0, 0.5, peak=10.0)

    assert rule.evaluate(first, {"et": 571, "high": 10.0, "close": 10.0}, 1) is None
    assert rule.evaluate(first, {"et": 572, "high": 10.1, "close": 10.0}, 2)["frac"] == 0.25
    assert rule.evaluate(first, {"et": 573, "high": 10.1, "close": 10.0}, 3) is None
    assert rule.evaluate(first, {"et": 574, "high": 10.2, "close": 10.0}, 4)["frac"] == 0.25
    assert rule.evaluate(first, {"et": 575, "high": 10.3, "close": 10.0}, 5) is None
    assert rule.evaluate(second, {"et": 572, "high": 10.0, "close": 10.0}, 2) is None


def test_reserved_cash_signal_does_not_consume_next_staged_add() -> None:
    day = "2021-02-01"
    decisions: list[dict] = []
    rule = f5.ScaleInRule((0.25, 0.50), decisions, {day: 575})
    ticket = sim.Ticket("AAA", day, 570, 10.0, 0.5, peak=10.0)

    reserved = rule.evaluate(ticket, {"et": 571, "high": 10.1, "close": 10.0}, 1)
    assert rule.state(ticket).get("next_add", 0) == 0
    first_eligible = rule.evaluate(ticket, {"et": 575, "high": 10.2, "close": 10.1}, 2)
    second_eligible = rule.evaluate(ticket, {"et": 576, "high": 10.3, "close": 10.2}, 3)

    assert reserved is None
    assert decisions[0]["skip_reason"] == "pending_entry_cash_reserved"
    assert decisions[0]["size_frac"] == 0.25
    assert first_eligible["frac"] == 0.25
    assert second_eligible["frac"] == 0.50


def test_pending_selected_entry_reservation_does_not_create_executed_add() -> None:
    day = "2021-02-01"
    decisions: list[dict] = []
    rule = f5.ScaleInRule((0.25, 0.50), decisions, {day: 573})
    strategy = sim.Strategy(sim.StrategySpec(
        n_slots=2, release=[sim.R0()], scale_in=[rule]))
    first = sim.Ticket("AAA", day, 570, 10.0, 0.25, shares=0.25 / 10.05,
                       cost_open=0.25, cash_in=0.25, shares_entry=0.25 / 10.05,
                       peak=10.0)
    later_entry = sim.Ticket("BBB", day, 573, 10.0, 0.25,
                             pending={"action": "ENTER", "reason": "ENTRY",
                                      "after_et": 572, "level": None,
                                      "frac": None, "carry": False})
    strategy.open_tickets.update({
        (first.sleeve_day, first.ticker): first,
        (later_entry.sleeve_day, later_entry.ticker): later_entry,
    })
    strategy.sleeve_cash[day] = 0.75
    strategy.deployed[day] = 0.25
    cash_before_reserved_signal = strategy._cash(day)
    reserved = rule.evaluate(first, {"et": 571, "high": 10.1, "close": 10.0}, 1)
    assert reserved is None
    assert rule.state(first).get("next_add", 0) == 0
    assert strategy._cash(day) == cash_before_reserved_signal
    decisions.clear()

    ets = list(range(570, 577))
    bars = sim.Bars(day, pl.DataFrame({
        "ticker": ["AAA"] * len(ets) + ["BBB"] * len(ets),
        "et": ets * 2,
        "open": [10.0, 10.0, 10.0, 10.0, 12.0, 12.0, 12.0] + [10.0] * len(ets),
        "high": [10.0, 10.1, 10.2, 10.3, 10.3, 10.3, 10.3] + [10.0] * len(ets),
        "low": [10.0] * (2 * len(ets)),
        "close": [10.0] * (2 * len(ets)),
        "volume": [1.0] * (2 * len(ets)),
    }))
    sim.simulate_day(strategy, day, {"date": day, "snapshots": []}, bars, 576, 100.0, False)

    adds = [action for action in first.actions if action["action"] == "ADD"]
    assert len(adds) == first.n_adds == 1
    assert adds[0]["et"] == 574
    assert adds[0]["px"] == 12.0
    assert [event["skip_reason"] for event in decisions if "skip_reason" in event] == [
        "pending_entry_cash_reserved"]
    assert decisions[-1]["size_frac"] == 0.25
    assert rule.state(first)["next_add"] == 1
    assert first.add_notional == 0.25 * first.unit_notional


def test_scalein_cell_grid_and_blocks_are_exact() -> None:
    cells = f5.build_cells()
    assert len(cells) == 840
    assert {cell.add_sizes for cell in cells} == {
        (), (0.25,), (0.5,), (1.0,), (0.25, 0.25), (0.25, 0.5), (0.5, 0.25)
    }
    assert {cell.bps for cell in cells} == {100, 150}
    assert len({cell.run_id for cell in cells}) == len(cells)
    assert all(sum(cell.add_sizes) <= 1.0 + 1e-12 for cell in cells)

    blocks = f5.dev_blocks(["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"])
    assert blocks == {"pooled": ["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"],
                      "block1": ["2021-02-01", "2023-12-29"],
                      "block2": ["2025-02-03", "2026-05-29"]}


# --------------------------------------------------------------------------- #
# Fixtures for manifest-driven reconciliation
# --------------------------------------------------------------------------- #


def _cell(entry_id: str = "A_open", n: int = 2, exit_id: str = "R0", sizes: tuple = (), bps: int = 100) -> f5.Cell:
    return next(cell for cell in f5.build_cells()
                if cell.entry_id == entry_id and cell.n == n and cell.exit_id == exit_id
                and cell.add_sizes == tuple(sizes) and cell.bps == bps)


def _ticket(sleeve_day: str, ticker: str, *, open_end: bool = False, net: float = 0.0,
            unit_notional: float = 0.5, shares_entry: float = 0.5, n_reduces: int = 0,
            exit_px: float | None = None, exit_day: str | None = None, exit_et: int | None = None,
            exit_reason: str | None = None, flags: str = "") -> dict:
    return {"sleeve_day": sleeve_day, "ticker": ticker, "open_end": open_end, "net": net,
            "flags": flags, "unit_notional": unit_notional, "shares_entry": shares_entry,
            "n_reduces": n_reduces, "exit_px": exit_px, "exit_day": exit_day, "exit_et": exit_et,
            "exit_reason": exit_reason}


def _decision(sleeve_day: str, ticker: str, et: int, size: float, *, skip_reason: str | None = None) -> dict:
    decision = {"sleeve_day": sleeve_day, "ticker": ticker, "decision_et": et, "prior_peak": 10.0,
                "decision_high": 10.1, "decision_close": 10.0, "size_frac": size}
    if skip_reason:
        decision["skip_reason"] = skip_reason
    return decision


def _trace(sleeve_day: str, ticker: str, et: int, size: float, *, executed: bool = True,
           open_px: float = 12.0, execution_day: str | None = None, bps: int = 100,
           unit_notional: float = 0.5, skip_cause: str | None = None) -> dict:
    return {"sleeve_day": sleeve_day, "ticker": ticker, "execution_day": execution_day or sleeve_day,
            "execution_et": et, "execution_open": open_px, "size_frac": size,
            "allocated_original_unit_notional": size * unit_notional, "executed": executed,
            "skip_cause": skip_cause, "friction_bps": bps}


def _write_bars(bars_dir: Path, day: str, series: dict[str, list[tuple]]) -> None:
    rows = [{"ticker": ticker, "et": et, "open": open_px, "high": high, "close": close}
            for ticker, bars in series.items() for (et, open_px, high, close) in bars]
    pl.DataFrame(rows, schema=_BAR_SCHEMA).write_parquet(bars_dir / f"{day}.parquet")


def _metrics(days_n: int) -> dict:
    return {"days_n": days_n, "mean_basket_day": 0.0, "avg_deployed_capital": 1.0,
            "turnover_per_day": 0.0, "turnover_annualized": 0.0, "n_entries": 1, "n_adds": 0,
            "n_exits": 1, "n_blocked_slots": 0, "n_pending": 0, "n_carries": 0, "worst_day": 0.0,
            "worst_week": 0.0, "worst_month": 0.0, "compounded_max_dd": 0.0}


def _manifest_for(cells: list[f5.Cell], days: list[str], *, contract_version: str | None = None,
                  contract_hash: str | None = None) -> dict:
    entries, releases, schedules = [], [], []
    for cell in cells:
        entry = {"entry": cell.entry_id, "pop": cell.entry_pop, "T": cell.entry_T}
        release = {"id": cell.exit_id, "L": cell.loss_pct}
        schedule = list(cell.add_sizes)
        if entry not in entries:
            entries.append(entry)
        if release not in releases:
            releases.append(release)
        if schedule not in schedules:
            schedules.append(schedule)
    return {
        "family_id": "F5", "evidence_label": "RUN", "days": list(days), "days_n": len(days),
        "blocks": {name: {"days_n": len(ds), "first": ds[0] if ds else None, "last": ds[-1] if ds else None}
                   for name, ds in f5.dev_blocks(list(days)).items()},
        "entry_configs": entries, "N": sorted({cell.n for cell in cells}), "release": releases,
        "add_schedules_original_unit_notional": schedules,
        "bps_round_trip": sorted({cell.bps for cell in cells}), "cell_count": len(cells),
        "sim_contract_hash": contract_hash or sim._contract_hash(),
        "contract_version": contract_version or sim.CONTRACT_VERSION,
    }


def _write_manifest(out_root: Path, manifest: dict) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "run_config.json").write_text(json.dumps(manifest))


def _seed_cell(out_root: Path, cell: f5.Cell, days: list[str], *, tickets: list[dict] | None = None,
               decisions: list[dict] | None = None, trace: list[dict] | None = None,
               daily: list[dict] | None = None, metrics: dict | None = None,
               contract_version: str | None = None, contract_hash: str | None = None,
               fingerprint: str | None = None) -> Path:
    """Write a complete, self-consistent C1 core cell (everything preflight checks)."""
    run_dir = out_root / "F5" / cell.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("daily", "tickets"):
        (run_dir / "parts" / kind).mkdir(parents=True, exist_ok=True)
        for month in sorted({day[:7] for day in days}):
            (run_dir / "parts" / kind / f"{month}.parquet").touch()
    daily_rows = daily if daily is not None else [{"date": day, "pnl": 0.0} for day in days]
    daily_df = pl.DataFrame(daily_rows, schema={"date": pl.Utf8, "pnl": pl.Float64})
    tickets_df = pl.DataFrame(tickets or [], schema=_TICKET_SCHEMA)
    daily_df.write_parquet(run_dir / "daily.parquet")
    tickets_df.write_parquet(run_dir / "tickets.parquet")
    metrics = dict(metrics or _metrics(len(days)))
    (run_dir / "metrics.json").write_text(json.dumps(metrics))
    (run_dir / "add_decisions.json").write_text(json.dumps(list(decisions or [])))
    (run_dir / "add_execution_trace.json").write_text(json.dumps(list(trace or [])))
    contract_version = contract_version or sim.CONTRACT_VERSION
    contract_hash = contract_hash or sim._contract_hash()
    (run_dir / "f5_config.json").write_text(json.dumps({
        "family_id": "F5", "run_id": cell.run_id, "entry": cell.entry_id, "entry_pop": cell.entry_pop,
        "entry_T": cell.entry_T, "N": cell.n, "release": cell.exit_id,
        "add_sizes_original_unit_notional": list(cell.add_sizes), "bps_round_trip": cell.bps,
        "days": [days[0], days[-1]], "n_days": len(days),
        "sim_contract_hash": contract_hash, "contract_version": contract_version}))
    (run_dir / "config.json").write_text(json.dumps({
        "family_id": "F5", "run_id": cell.run_id, "strategy": cell.run_id,
        "entry_pop": cell.entry_pop, "entry_T": cell.entry_T, "top_n": cell.n, "n_slots": cell.n,
        "bps_total": float(cell.bps), "release": [cell.exit_id],
        "scale_in": ["F5_own_strict_new_high"] if cell.add_sizes else [],
        "days": [days[0], days[-1]], "n_days": len(days),
        "sim_contract_hash": contract_hash, "contract_version": contract_version}))
    if fingerprint is None:
        fingerprint = sim._run_fingerprint(
            sim.RunConfig("F5", cell.run_id, cell.strategy([], {}), float(cell.bps), out_root,
                          days=days), days)
    (run_dir / "run_summary.json").write_text(json.dumps({
        "days_run": len(days), "daily_rows": daily_df.height, "ticket_rows": tickets_df.height,
        "daily_sha256": hashlib.sha256((run_dir / "daily.parquet").read_bytes()).hexdigest(),
        "tickets_sha256": hashlib.sha256((run_dir / "tickets.parquet").read_bytes()).hexdigest(),
        "metrics": metrics, "fingerprint": fingerprint, "contract_version": contract_version}))
    return run_dir


@pytest.fixture
def f5_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Synthetic canonical bars + a session calendar for the two test days."""
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    monkeypatch.setattr(sim, "BARS_DIR", bars_dir)
    monkeypatch.setattr(sim, "session_end_map", lambda: {day: SESSION_END for day in DAYS})
    return tmp_path, bars_dir


def _add_cell_tree(out_root: Path, bars_dir: Path, *, sizes: tuple = (0.25,)) -> tuple[f5.Cell, f5.Cell]:
    """One ADD cell plus its matched no-add control, with one executed ADD on day 1."""
    day = DAYS[0]
    add_cell, control_cell = _cell(sizes=sizes), _cell(sizes=())
    _write_manifest(out_root, _manifest_for([add_cell, control_cell], DAYS))
    for index, ticker in enumerate(("AAA", "BBB")):
        _write_bars(bars_dir, DAYS[index], {ticker: [(571, 10.0, 10.1, 10.0),
                                                     (572, 12.0, 12.5, 12.4),
                                                     (575, 13.0, 13.2, 13.1)]})
    decisions = [_decision(day, "AAA", 571, sizes[0])]
    trace = [_trace(day, "AAA", 572, sizes[0])]
    _seed_cell(out_root, add_cell, DAYS,
               tickets=[_ticket(day, "AAA", net=9.0, exit_px=13.0, exit_day=day, exit_et=575,
                                exit_reason="R0")],
               decisions=decisions, trace=trace)
    _seed_cell(out_root, control_cell, DAYS,
               tickets=[_ticket(day, "AAA", net=-99.0, exit_px=1.0, exit_day=day, exit_et=575,
                                exit_reason="R0")])
    return add_cell, control_cell


# --------------------------------------------------------------------------- #
# RED 1 — every cell uses its own tickets (no cross-cell leak)
# --------------------------------------------------------------------------- #


def test_reconcile_surface_uses_each_cell_ticket_rows(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    add_cell, control_cell = _add_cell_tree(out_root, bars_dir)
    assert add_cell.run_id < control_cell.run_id  # sorted surface ends on the control

    rows = f5.reconcile_surface(out_root)

    events = json.loads((out_root / "F5" / add_cell.run_id / "add_events.json").read_text())
    assert len(events) == 1
    executed = events[0]
    assert executed["executed_status"] == "executed"
    assert executed["terminal_px"] == 13.0  # the ADD cell's own ticket, never the control's 1.0
    expected = 0.125 / (12.0 * 1.005) * 13.0 * 0.995 - 0.125
    assert executed["executed_tranche_net_pnl"] == pytest.approx(expected)
    assert json.loads((out_root / "F5" / control_cell.run_id / "add_events.json").read_text()) == []
    assert json.loads((out_root / "F5" / control_cell.run_id / "executed_tranches.json").read_text()) == []
    assert [row["run_id"] for row in rows] == sorted([add_cell.run_id, control_cell.run_id])
    control_row = next(row for row in rows if row["run_id"] == control_cell.run_id)
    assert control_row["add_events"]["executed_tranches"] == 0
    assert control_row["add_decision_count"] == 0


# --------------------------------------------------------------------------- #
# RED 2 — bounded, reused, immutable bar batches + trace-authoritative execution
# --------------------------------------------------------------------------- #


def test_reconcile_surface_reuses_one_immutable_day_bar_across_cell_batch(
        f5_env, monkeypatch: pytest.MonkeyPatch) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    first, second = _cell(sizes=(0.25,)), _cell(sizes=(0.5,))
    _write_manifest(out_root, _manifest_for([first, second], DAYS))
    for day in DAYS:
        _write_bars(bars_dir, day, {"AAA": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4)]})
    for cell in (first, second):
        _seed_cell(out_root, cell, DAYS,
                   tickets=[_ticket(day, "AAA", exit_px=13.0, exit_day=day, exit_et=575,
                                    exit_reason="R0") for day in DAYS],
                   decisions=[_decision(day, "AAA", 571, cell.add_sizes[0]) for day in DAYS],
                   trace=[_trace(day, "AAA", 572, cell.add_sizes[0]) for day in DAYS])
    loads: list[tuple[list[str], dict]] = []
    original_load = f5._load_bar_batch

    def spy_load(days, session_end):
        result = original_load(days, session_end)
        loads.append((list(days), result))
        return result

    seen: list[dict] = []
    original_events = f5._add_event_outcomes

    def spy_events(decisions, execution_trace, ticket_rows, bps, bars_by_day, session_end, **kwargs):
        seen.append(dict(bars_by_day))
        return original_events(decisions, execution_trace, ticket_rows, bps, bars_by_day,
                               session_end, **kwargs)

    monkeypatch.setattr(f5, "_load_bar_batch", spy_load)
    monkeypatch.setattr(f5, "_add_event_outcomes", spy_events)
    f5.reconcile_surface(out_root, cell_batch_size=2, day_batch_size=1)

    assert all(len(days) <= 1 for days, _ in loads)
    assert [days for days, _ in loads] == [[DAYS[0]], [DAYS[1]]]
    for day in DAYS:
        identities = {id(mapping[day]) for mapping in seen if day in mapping}
        assert len(identities) == 1, f"day {day} frame was not shared across the cell batch"
        assert sum(1 for mapping in seen if day in mapping) == 2  # both cells, same object
    batch_frames = {day: next(mapping[day] for mapping in seen if day in mapping) for day in DAYS}
    snapshots = {day: {ticker: tuple(array.copy() for array in arrays)
                       for ticker, arrays in batch_frames[day].by_ticker.items()} for day in DAYS}
    f5.reconcile_surface(out_root, cell_batch_size=2, day_batch_size=1)
    for day in DAYS:
        for ticker, arrays in batch_frames[day].by_ticker.items():
            for before, after in zip(snapshots[day][ticker], arrays):
                assert np.array_equal(before, after), "bar batch was mutated by reconciliation"


def test_execution_trace_remains_executed_when_first_observed_bar_differs(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    cell = _cell(sizes=(0.25,))
    _write_manifest(out_root, _manifest_for([cell], DAYS))
    _write_bars(bars_dir, DAYS[0], {"AAA": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4),
                                            (574, 12.0, 12.2, 12.1)]})
    _write_bars(bars_dir, DAYS[1], {"AAA": [(571, 12.0, 12.0, 12.0)]})
    _seed_cell(out_root, cell, DAYS,
               tickets=[_ticket(DAYS[0], "AAA", exit_px=13.0, exit_day=DAYS[0], exit_et=575,
                                exit_reason="R0")],
               decisions=[_decision(DAYS[0], "AAA", 571, 0.25)],
               trace=[_trace(DAYS[0], "AAA", 574, 0.25, open_px=12.0)])

    f5.reconcile_surface(out_root)

    events = json.loads((out_root / "F5" / cell.run_id / "add_events.json").read_text())
    executed = events[0]
    assert executed["executed_status"] == "executed"
    assert executed["execution_et"] == 574  # the trace, not the first observed later bar
    assert executed["first_observed_later_bar_et"] == 572
    assert executed["bar_evidence"] == "recorded_sleeve_day"
    assert executed["allocated_original_unit_notional"] == 0.125


# --------------------------------------------------------------------------- #
# RED 3 — reconciliation cannot execute or clean core cells
# --------------------------------------------------------------------------- #


def test_reconcile_cli_never_calls_core_execution(f5_env, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    _add_cell_tree(out_root, bars_dir)

    def forbidden(*args, **kwargs):
        raise AssertionError("reconcile mode reached core execution")

    monkeypatch.setattr(f5, "_clean_cell_outputs", forbidden)
    monkeypatch.setattr(f5, "_execute_cell", forbidden)
    monkeypatch.setattr(f5, "run_cells", forbidden)
    monkeypatch.setattr(f5, "_write_run_config", forbidden)
    monkeypatch.setattr(sim, "run", forbidden)

    assert f5.main(["--reconcile", "--out-root", str(out_root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "reconcile"
    assert report["cells"] == 2 and report["add_cells"] == 1 and report["controls"] == 1


def test_reconcile_cli_rejects_run_only_flags(f5_env) -> None:
    root, _ = f5_env
    out_root = root / "out"
    with pytest.raises(SystemExit):
        f5.main(["--reconcile", "--full"])
    with pytest.raises(SystemExit):
        f5.main(["--reconcile", "--out-root", str(out_root), "--workers", "2"])
    with pytest.raises(SystemExit):
        f5.main(["--reconcile", "--out-root", str(out_root), "--cell-workers", "2"])


# --------------------------------------------------------------------------- #
# RED 4 — atomic, idempotent publication that never touches core artifacts
# --------------------------------------------------------------------------- #


def _core_snapshot(out_root: Path) -> dict[str, tuple[str, int]]:
    """sha256 + mtime of every core input (everything reconciliation must never touch)."""
    published = set(f5.DERIVED_OUTPUTS) | {"README.md", "surface.json"}
    return {str(path.relative_to(out_root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                              path.stat().st_mtime_ns)
            for path in out_root.rglob("*")
            if path.is_file() and path.name not in published}


def _published_hashes(out_root: Path) -> dict[str, str]:
    """sha256 of every file reconciliation publishes (idempotence is byte equality)."""
    published = set(f5.DERIVED_OUTPUTS) | {"README.md", "surface.json"}
    return {str(path.relative_to(out_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in out_root.rglob("*")
            if path.is_file() and path.name in published}


def test_reconcile_is_idempotent_and_never_rewrites_core_artifacts(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    add_cell, control_cell = _add_cell_tree(out_root, bars_dir)
    for cell in (add_cell, control_cell):
        for name in f5.DERIVED_OUTPUTS:
            (out_root / "F5" / cell.run_id / name).write_text('["stale sentinel"]')
    core_before = _core_snapshot(out_root)

    f5.reconcile_surface(out_root)
    published_before = _published_hashes(out_root)
    f5.reconcile_surface(out_root)
    published_after = _published_hashes(out_root)
    core_after = _core_snapshot(out_root)

    assert core_after == core_before, "reconciliation changed core artifacts (hash or timestamp)"
    assert published_after == published_before, "reconciliation is not byte-idempotent"
    assert '["stale sentinel"]' not in (out_root / "F5" / add_cell.run_id / "add_events.json").read_text()
    assert not [path for path in out_root.rglob("*") if path.name.endswith(".tmp")]
    surface = json.loads((out_root / "surface.json").read_text())
    assert surface["phase"] == "reconciled"
    assert surface["source"]["derived_sha256"] == f5._derived_digest(
        {row["run_id"]: row["derived_sha256"] for row in surface["cells"]})


def test_atomic_text_preserves_existing_target_when_replace_fails(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "add_events.json"
    target.write_text("old")

    def boom(source, destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(f5.os, "replace", boom)
    with pytest.raises(OSError):
        f5._atomic_text(target, "new")
    assert target.read_text() == "old"
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".tmp")]


# --------------------------------------------------------------------------- #
# RED 5 — executed-only tranche accounting and duplicate identity
# --------------------------------------------------------------------------- #


def test_executed_tranche_accounting_uses_only_executed_traces(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    day = DAYS[0]
    add_cell, control_cell = _cell(sizes=(0.25,)), _cell(sizes=())
    _write_manifest(out_root, _manifest_for([add_cell, control_cell], DAYS))
    for index, day_id in enumerate(DAYS):
        _write_bars(bars_dir, day_id, {
            "AAA": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4), (575, 13.0, 13.2, 13.1)],
            "BBB": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4), (575, 13.0, 13.2, 13.1)],
            "CCC": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4), (575, 13.0, 13.2, 13.1)],
            "DDD": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4)],
            "EEE": [(571, 10.0, 10.1, 10.0), (572, 12.0, 12.5, 12.4)],
        })
    decisions = [
        _decision(day, "AAA", 571, 0.25, skip_reason="pending_entry_cash_reserved"),
        _decision(day, "BBB", 571, 0.25),
        _decision(day, "CCC", 571, 0.25),
        _decision(day, "DDD", 571, 0.25),
        _decision(day, "EEE", 571, 0.25),
        _decision(day, "FFF", 571, 0.25),  # scheduled, ticker never trades again: no trace
    ]
    trace = [
        _trace(day, "BBB", 572, 0.25, executed=False, skip_cause="add_unfunded"),
        _trace(day, "CCC", 572, 0.25, open_px=12.0),
        _trace(day, "DDD", 572, 0.25, open_px=12.0),
        _trace(day, "EEE", 572, 0.25, open_px=12.0),
    ]
    _seed_cell(out_root, add_cell, DAYS, decisions=decisions, trace=trace, tickets=[
        # open-end marks come from these rows, never from another session's bars
        _ticket(day, "CCC", exit_px=13.0, exit_day=day, exit_et=575, exit_reason="R1(-10)"),
        _ticket(day, "DDD", open_end=True, net=5.0),
        _ticket(day, "EEE", open_end=True, net=2.0, shares_entry=1.0),
    ])
    _seed_cell(out_root, control_cell, DAYS,
               tickets=[_ticket(day, "CCC", net=-99.0, exit_px=1.0, exit_day=day, exit_et=575,
                                exit_reason="R0")])

    rows = f5.reconcile_surface(out_root)
    row = next(row for row in rows if row["run_id"] == add_cell.run_id)
    events = json.loads((out_root / "F5" / add_cell.run_id / "add_events.json").read_text())
    by_ticker = {event["ticker"]: event for event in events}
    assert len(events) == len(decisions) == row["add_decision_count"] == 6

    reservation, unfunded = by_ticker["AAA"], by_ticker["BBB"]
    for skipped in (reservation, unfunded):
        assert skipped["executed_status"] == "skipped"
        assert skipped["allocated_original_unit_notional"] is None
        assert skipped["executed_tranche_net_pnl"] is None
        assert skipped["execution_et"] is None
    assert reservation["skip_cause"] == "pending_entry_cash_reserved"
    assert unfunded["skip_cause"] == "add_unfunded"

    shares = 0.125 / (12.0 * 1.005)
    closed = by_ticker["CCC"]
    assert closed["execution_open"] == 12.0 and closed["execution_et"] == 572
    assert closed["applicable_friction_bps"] == 100
    assert closed["allocated_original_unit_notional"] == 0.125
    assert closed["terminal_value_kind"] == "exit" and closed["terminal_px"] == 13.0
    assert closed["executed_tranche_net_pnl"] == pytest.approx(shares * 13.0 * 0.995 - 0.125)

    marked = by_ticker["DDD"]
    ddd_mark = (5.0 + 0.5 + 0.125) / (0.5 + shares)  # mark = net + unit_notional + allocations
    assert marked["terminal_value_kind"] == "open_end_mark" and marked["terminal_day"] is None
    assert marked["terminal_px"] == pytest.approx(ddd_mark)
    assert marked["executed_tranche_net_pnl"] == pytest.approx(shares * ddd_mark - 0.125)

    marked_other = by_ticker["EEE"]  # per-ticket decomposition, not a shared/global mark
    eee_mark = (2.0 + 0.5 + 0.125) / (1.0 + shares)
    assert marked_other["terminal_px"] == pytest.approx(eee_mark)
    assert marked_other["executed_tranche_net_pnl"] == pytest.approx(shares * eee_mark - 0.125)

    never_filled = by_ticker["FFF"]  # scheduled ADD with no execution trace is never a tranche
    assert never_filled["executed_status"] == "not_executed"
    assert never_filled["skip_cause"] == "no_execution_trace"
    assert never_filled["allocated_original_unit_notional"] is None
    assert never_filled["executed_tranche_net_pnl"] is None
    assert never_filled["bar_evidence"] == "unresolved"  # no bars for this ticker either

    for event in events:
        assert "matched_no_add_ticket_net" not in event
        assert "matched_no_add_tranche_contribution" not in event
        assert "incremental_net_pnl_contribution" not in event

    summary = row["add_events"]
    expected_net = ((shares * 13.0 * 0.995 - 0.125) + (shares * ddd_mark - 0.125)
                    + (shares * eee_mark - 0.125))
    assert summary["triggered"] == 6
    assert summary["executed_tranches"] == 3
    assert summary["skipped_tranches"] == 2
    assert summary["not_executed_tranches"] == 1
    assert summary["bar_evidence_unresolved"] == 1
    assert summary["pending_entry_cash_reserved_skips"] == 1
    assert summary["executed_tranche_net_pnl"] == pytest.approx(expected_net)
    assert summary["allocated_original_unit_notional"] == pytest.approx(0.375)
    assert summary["net_ev_per_allocated_notional"] == pytest.approx(expected_net / 0.375)
    block_ev = f5._executed_tranche_ev_by_block(events, {"pooled": DAYS})
    assert block_ev["pooled"]["n_executed_tranches"] == 3
    assert block_ev["pooled"]["executed_tranche_net_pnl"] == pytest.approx(expected_net)
    tranches = json.loads((out_root / "F5" / add_cell.run_id / "executed_tranches.json").read_text())
    assert {event["ticker"] for event in tranches} == {"CCC", "DDD", "EEE"}
    assert all(event["executed_status"] == "executed" for event in tranches)


def test_ticket_identity_rejects_duplicate_sleeve_day_ticker(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    add_cell, control_cell = _add_cell_tree(out_root, bars_dir)
    run_dir = out_root / "F5" / add_cell.run_id
    duplicate = [_ticket(DAYS[0], "AAA", exit_px=13.0, exit_day=DAYS[0], exit_et=575,
                         exit_reason="R0"),
                 _ticket(DAYS[0], "AAA", exit_px=1.0, exit_day=DAYS[0], exit_et=576,
                         exit_reason="R0")]
    _seed_cell(out_root, add_cell, DAYS, tickets=duplicate,
               decisions=[_decision(DAYS[0], "AAA", 571, 0.25)],
               trace=[_trace(DAYS[0], "AAA", 572, 0.25)])

    with pytest.raises(f5.ReconcileError, match="duplicate"):
        f5.reconcile_surface(out_root)
    assert not (run_dir / "add_events.json").exists()  # refused before the first derived write


def test_reconcile_refuses_uncorrected_core_contract(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    add_cell, control_cell = _add_cell_tree(out_root, bars_dir)
    manifest = _manifest_for([add_cell, control_cell], DAYS,
                             contract_version="FROZEN-2026-09-22", contract_hash="0" * 16)
    _write_manifest(out_root, manifest)
    with pytest.raises(f5.ReconcileError, match="contract_version|sim_contract_hash"):
        f5.reconcile_surface(out_root)

    _write_manifest(out_root, _manifest_for([add_cell, control_cell], DAYS))
    _seed_cell(out_root, add_cell, DAYS,
               tickets=[_ticket(DAYS[0], "AAA", exit_px=13.0, exit_day=DAYS[0], exit_et=575,
                                exit_reason="R0")],
               decisions=[_decision(DAYS[0], "AAA", 571, 0.25)],
               trace=[_trace(DAYS[0], "AAA", 572, 0.25)],
               contract_version="FROZEN-2026-09-22", contract_hash="0" * 16)
    with pytest.raises(f5.ReconcileError, match="corrected contract"):
        f5.reconcile_surface(out_root)


def test_reconcile_uses_manifest_days_and_grid(f5_env) -> None:
    root, bars_dir = f5_env
    out_root = root / "out"
    add_cell, control_cell = _add_cell_tree(out_root, bars_dir)
    manifest = _manifest_for([add_cell, control_cell], DAYS)

    assert f5._manifest_days(manifest, out_root) == DAYS
    selected = f5._cells_from_manifest(manifest, out_root)
    assert {cell.run_id for cell in selected} == {add_cell.run_id, control_cell.run_id}
    canonical = _manifest_for(f5.build_cells(), DAYS)
    assert [cell.run_id for cell in f5._cells_from_manifest(canonical, out_root)] == [
        cell.run_id for cell in f5.build_cells()]
    assert canonical["cell_count"] == 840

    (out_root / "F5" / "stray_cell").mkdir()
    with pytest.raises(f5.ReconcileError, match="cell tree does not match"):
        f5.reconcile_surface(out_root)


# --------------------------------------------------------------------------- #
# Core vs derived completeness
# --------------------------------------------------------------------------- #


def test_core_and_derived_completeness_are_separate(tmp_path: Path) -> None:
    cell = next(cell for cell in f5.build_cells() if cell.add_sizes)
    days = ["2021-02-01", "2021-02-02"]
    run_dir = tmp_path / "F5" / cell.run_id
    run_dir.mkdir(parents=True)
    for name in f5.CORE_OUTPUTS:
        (run_dir / name).write_text("[]" if name.endswith(".json") else "{}")
    for kind in ("daily", "tickets"):
        part_dir = run_dir / "parts" / kind
        part_dir.mkdir(parents=True)
        for month in ("2021-02",):
            (part_dir / f"{month}.parquet").touch()

    assert f5.is_core_complete(run_dir, days, cell)
    assert not f5.is_complete(run_dir, days, cell)
    assert f5.is_complete(run_dir, days, cell, require_derived=False)
    for name in f5.DERIVED_OUTPUTS:
        (run_dir / name).write_text("[]")
    assert f5.is_complete(run_dir, days, cell)

    (run_dir / "add_decisions.json").unlink()
    assert not f5.is_core_complete(run_dir, days, cell)
    assert not f5.is_complete(run_dir, days, cell)
    (run_dir / "add_decisions.json").write_text(json.dumps([]))
    assert f5.is_complete(run_dir, days, cell)


def test_add_executes_next_open_and_release_consumes_trigger_bar() -> None:
    day = "2021-02-01"

    def run(highs: list[float], lows: list[float], release: list) -> sim.Ticket:
        strat = sim.Strategy(sim.StrategySpec(n_slots=2, release=release,
                                              scale_in=[f5.ScaleInRule((0.25,))]))
        tk = sim.Ticket("AAA", day, 570, 10.0, 0.5, shares=0.05, cost_open=0.5,
                        cash_in=0.5, shares_entry=0.05, peak=10.0)
        strat.open_tickets[(tk.sleeve_day, tk.ticker)] = tk
        strat.sleeve_cash[day] = 0.5
        strat.deployed[day] = 0.5
        bars = sim.Bars(day, pl.DataFrame({
            "ticker": ["AAA"] * 5, "et": np.array([570, 571, 572, 573, 574]),
            "open": [10.0, 10.5, 12.0, 12.0, 12.0], "high": highs,
            "low": lows, "close": [10.0] * 5, "volume": [1.0] * 5,
        }))
        sim.simulate_day(strat, day, {"date": day, "snapshots": []}, bars, 574, 0.0, False)
        return tk

    added = run([10.0, 11.0, 12.5, 12.5, 12.5], [10.0] * 5, [])
    add_action = next(action for action in added.actions if action["action"] == "ADD")
    assert add_action["et"] == 572
    assert add_action["px"] == 12.0

    released = run([10.0, 11.0, 12.5, 12.5, 12.5], [10.0, 8.0, 10.0, 10.0, 10.0],
                   [sim.R1(-10)])
    assert released.n_adds == 0
    assert released.exit_reason == "R1(-10)"


def test_unfunded_and_over_cap_adds_are_skipped_and_flagged() -> None:
    for cash, deployed, already_added, expected_flag in (
        (0.1, 0.9, 0.0, "add_unfunded"),
        (0.5, 0.5, 0.9, "add_cap_exceeded"),
    ):
        strat = sim.Strategy(sim.StrategySpec(n_slots=1))
        tk = sim.Ticket("AAA", "2021-02-01", 570, 1.0, 0.5, shares=0.5,
                        cost_open=0.5, cash_in=0.5, add_notional=already_added)
        strat.sleeve_cash[tk.sleeve_day] = cash
        strat.deployed[tk.sleeve_day] = deployed
        sim._execute(strat, tk, "ADD", 1.0, 572, "test", 0.0, frac=0.5)
        assert expected_flag in tk.flags
        assert tk.n_adds == 0
        assert strat._cash(tk.sleeve_day) == cash


def test_runner_script_exposes_full_grid_cli() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "factory/scripts/basket_f5_scalein.py", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--full" in result.stdout
    assert "--max-days" in result.stdout
    assert "--reconcile" in result.stdout
    assert "--cell-batch-size" in result.stdout
    assert "--day-batch-size" in result.stdout
