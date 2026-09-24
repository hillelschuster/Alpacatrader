from __future__ import annotations

from pathlib import Path


def test_frozen_checkpoints_and_entry_family_grid() -> None:
    from factory.scripts import basket_f2_f12 as panel

    assert panel.CHECKPOINTS == (580, 585, 590, 600, 615)
    assert set(panel.FAMILIES) == {"A_pm", "A_pm31", "A_open", "B585", "B600"}


def test_checkpoint_features_exclude_checkpoint_and_later_bars() -> None:
    from factory.scripts import basket_f2_f12 as panel

    bars = {
        "et": [570, 571, 580, 581, 582, 583],
        "open": [10, 10, 11, 12, 13, 14],
        "high": [11, 12, 13, 100, 100, 100],
        "low": [9, 10, 10, 1, 1, 1],
        "close": [10, 11, 12, 13, 14, 15],
        "volume": [1, 2, 3, 1000, 1000, 1000],
    }
    name = {"ticker": "XYZ", "rank": 1, "open0930": 10, "prev_close": 5,
            "sel": 1.0, "fill": {"et": 571, "px": 10, "blocked": False}}

    row = panel.checkpoint_row("2021-02-01", "A_open", name, bars, 580, 583)

    assert row["px_tp"] == 12
    assert abs(row["ret_from_fill"] - 0.2) < 1e-12
    assert abs(row["mfe_so_far"] - 0.3) < 1e-12
    assert row["cum_volume"] == 5
    assert abs(row["rem_mfe"] - (100 / 12 - 1)) < 1e-12
    assert abs(row["rem_mae"] - (1 / 12 - 1)) < 1e-12
    assert row["touch30"] is True
    assert row["touch100"] is True
    assert row["nhba"] is False
    assert row["adverse_first"] is True

    changed_future = {**bars, "high": [11, 12, 13, 14, 15, 16],
                      "low": [9, 10, 10, 11, 12, 13],
                      "volume": [1, 2, 3, 1000, 1000, 1000]}
    changed = panel.checkpoint_row("2021-02-01", "A_open", name, changed_future, 580, 583)
    for field in panel.STATE_FEATURES:
        same = row[field] == changed[field]
        both_nan = row[field] != row[field] and changed[field] != changed[field]
        assert same or both_nan
    assert row["rem_mfe"] != changed["rem_mfe"]


def test_output_root_and_resume_are_isolated_and_require_complete_month(tmp_path: Path) -> None:
    from factory.scripts import basket_f2_f12 as panel

    assert panel.DEFAULT_OUT == panel.ROOT / "factory/artifacts/basket/phase2/F2_F12"
    month_dir = tmp_path / "month=2021-02"
    month_dir.mkdir()
    (month_dir / "panel.parquet").write_bytes(b"ready")
    (month_dir / "_done").write_text('{"rows": 1}')
    assert panel.month_complete(month_dir)
    (month_dir / "_done").unlink()
    assert not panel.month_complete(month_dir)


def test_day_builder_emits_only_frozen_post_fill_family_checkpoints() -> None:
    import polars as pl

    from factory.scripts import basket_f2_f12 as panel
    from factory.scripts import basket_sim as sim

    ticker = "XYZ"
    bars = sim.Bars("2021-02-01", pl.DataFrame({
        "ticker": [ticker] * 9,
        "et": [570, 571, 580, 585, 590, 600, 615, 616, 617],
        "open": [10.0] * 9, "high": [11.0] * 9, "low": [9.0] * 9,
        "close": [10.0] * 9, "volume": [1.0] * 9,
    }))
    name = {"ticker": ticker, "rank": 1, "sel": 1.0, "open0930": 10.0,
            "prev_close": 10.0, "fill": {"et": 570, "px": 10.0, "blocked": False}}
    snapshots = []
    for pop, checkpoint in (("A_pm", 570), ("A_pm31", 570), ("A_open", 570),
                            ("B", 585), ("B", 600), ("B", 615)):
        fill_et = 571 if pop in {"A_pm31", "A_open"} else checkpoint
        row_name = {**name, "fill": {"et": fill_et, "px": 10.0, "blocked": False}}
        snapshots.append({"pop": pop, "T": checkpoint, "names": [row_name]})
    rec = {"date": "2021-02-01", "snapshots": snapshots}

    rows, _ = panel.build_day(rec, bars, 617)

    assert {(row["family"], row["et"]) for row in rows} == {
        (family, checkpoint)
        for family, checkpoints in {
            "A_pm": (580, 585, 590, 600, 615),
            "A_pm31": (580, 585, 590, 600, 615),
            "A_open": (580, 585, 590, 600, 615),
            "B585": (585, 590, 600, 615),
            "B600": (600, 615),
        }.items()
        for checkpoint in checkpoints
    }
