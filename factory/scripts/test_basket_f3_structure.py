import numpy as np

from factory.scripts.basket_f3_structure import describe_path


def test_describe_path_maps_entry_and_peak_deterioration_without_thresholds():
    et = np.arange(570, 576)
    high = np.array([100.0, 110.0, 108.0, 106.0, 107.0, 111.0])
    low = np.array([99.0, 105.0, 94.0, 96.0, 99.0, 108.0])
    close = np.array([100.0, 109.0, 96.0, 100.0, 106.0, 110.0])

    row = describe_path(et, high, low, close, 100.0)

    assert row["entry_max_adverse_pct"] == 6.0
    assert row["entry_max_adverse_duration_min"] == 1
    assert row["entry_recovery_min"] == 1
    assert row["entry_recovery_speed_pct_per_min"] == 6.0
    assert row["peak_drawdown_pct"] == 14.545455
    assert row["peak_mfe_surrendered_pct"] == 145.454545
    assert row["peak_time_since_peak_min"] == 1
    assert row["peak_failed_reclaim"] is False
    assert row["entry_max_adverse_pct"] * row["entry_max_adverse_duration_min"] == 6.0
