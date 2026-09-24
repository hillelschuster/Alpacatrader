from __future__ import annotations

from factory.scripts import basket_f10_f14 as lane


def test_descriptive_grid_has_fixed_non_outcome_cutpoints() -> None:
    assert lane.BUCKETS == {
        "pm_leader_strength": (0.10, 0.30),
        "pm_top10_dispersion": (0.10, 0.50),
        "pm_names_ge_10pct": (2, 4),
        "b600_cohort_trend": (-0.01, 0.01),
        "prior_b600_cohort_trend": (-0.01, 0.01),
        "b600_realized_volatility": (0.005, 0.015),
        "b600_names_ge_10pct": (2, 4),
        "b600_top10_dollar_volume": (1_000_000, 10_000_000),
    }
    assert all(len(cutpoints) == 2 for cutpoints in lane.BUCKETS.values())


def test_bar_environment_ignores_rows_after_fixed_1000_et_cutoff() -> None:
    before = [
        {"ticker": "AAA", "et": 570, "open": 10.0, "close": 10.0, "volume": 100},
        {"ticker": "AAA", "et": 571, "open": 10.0, "close": 10.1, "volume": 200},
        {"ticker": "AAA", "et": 600, "open": 10.1, "close": 99.0, "volume": 9_000_000},
        {"ticker": "BBB", "et": 570, "open": 20.0, "close": 20.0, "volume": 100},
        {"ticker": "BBB", "et": 571, "open": 20.0, "close": 19.9, "volume": 200},
    ]
    future = before + [
        {"ticker": "AAA", "et": 601, "open": 10.1, "close": 100.0, "volume": 10_000_000}
    ]

    assert lane.bar_environment(before, cutoff_et=600) == lane.bar_environment(
        future, cutoff_et=600
    )


def test_bucket_edges_are_left_closed_and_have_no_outcome_input() -> None:
    assert lane.bucket_index(0.10, (0.10, 0.30)) == 1
    assert lane.bucket_index(0.30, (0.10, 0.30)) == 2
    assert lane.bucket_index(-0.01, (-0.01, 0.01)) == 1


def test_bar_environment_excludes_tickers_outside_asof_snapshot() -> None:
    rows = [
        {"ticker": "KNOWN", "et": 570, "open": 10.0, "close": 10.1, "volume": 100},
        {"ticker": "LATER", "et": 570, "open": 10.0, "close": 90.0, "volume": 1_000_000},
    ]

    result = lane.bar_environment(rows, cutoff_et=600, tickers={"KNOWN"})

    assert result["b600_bar_tickers"] == 1
    assert result["b600_top10_dollar_volume"] == 1_010.0


def test_maps_preserve_empty_fixed_buckets_and_both_block_rows() -> None:
    labels = {name: bucket_labels[-1] for name, bucket_labels in lane.BUCKET_LABELS.items()}
    environment = [
        {
            "date": "2021-02-01",
            "year": 2021,
            "quarter": "2021-Q1",
            "month": "2021-02",
            "weekday": "Monday",
            "labels": labels,
        }
    ]
    outcomes = {"2021-02-01": {"cell": -0.02}}
    cells = [{"run_id": "cell", "entry": "A_pm", "N": 2, "exit": "R0", "bps": 100}]

    result = lane.build_maps(environment, outcomes, cells)

    assert set(result["pm_leader_strength"]) == set(lane.BUCKET_LABELS["pm_leader_strength"])
    empty = result["pm_leader_strength"]["lt10pct"]
    assert empty["days_n"] == 0
    assert empty["cells"]["cell"]["blocks"]["block1_2021-02_to_2023-12"]["n_days"] == 0
    assert empty["cells"]["cell"]["blocks"]["block2_2025-02_to_2026-05"]["n_days"] == 0


def test_cohort_trend_uses_canonical_b600_scores() -> None:
    record = {
        "snapshots": [
            {"pop": "A_pm", "T": 570, "names": [{"ticker": "AAA", "sel": 0.2}]},
            {
                "pop": "B",
                "T": 600,
                "names": [{"ticker": "AAA", "sel": 0.2}, {"ticker": "BBB", "sel": 0.4}],
            },
        ]
    }
    bars = [{"ticker": "AAA", "et": 570, "open": 10.0, "close": 10.0, "volume": 1}]

    result = lane.environment_for_day("2021-02-01", record, bars, None)

    assert abs(result["b600_cohort_trend"] - 0.3) < 1e-12
