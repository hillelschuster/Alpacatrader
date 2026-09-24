from __future__ import annotations

from datetime import date, timedelta

import pytest

from factory.scripts.f9_weighting import SCHEMES, validate_dev_days, weights_for


def test_fixed_gross_schemes_assign_one_c0_without_leverage() -> None:
    ranks = [1, 2, 3]
    scores = [0.60, 0.30, 0.10]
    for scheme in SCHEMES:
        weights = weights_for(scheme, ranks, scores, [True, False, True])
        assert sum(weights) == pytest.approx(1.0)
        assert min(weights) >= 0
        assert max(weights) <= 1


def test_rank_linear_grid_is_exactly_frozen_rank_order() -> None:
    assert weights_for("rank_linear", [1, 2, 3], [0, 0, 0], [False] * 3) == pytest.approx(
        [0.5, 1 / 3, 1 / 6]
    )


def test_gate_and_survival_weights_require_causal_flags() -> None:
    with pytest.raises(ValueError, match="causal flags"):
        weights_for("golden_gate_1p5x", [1, 2], [1.0, 0.5])


def test_dev_provenance_rejects_substituted_day() -> None:
    start, end = date(2021, 2, 1), date(2023, 12, 29)
    first_block = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    days = first_block + [f"2025-02-0{i}" for i in range(3, 7)]
    expected = days.copy()
    expected[0] = "2021-02-02"
    with pytest.raises(ValueError, match="canonical dev calendar"):
        validate_dev_days(days, expected)


def test_rank_linear_rejects_noncanonical_selected_ranks() -> None:
    with pytest.raises(ValueError, match="ranks must be 1..N"):
        weights_for("rank_linear", [1, 3, 4], [0.6, 0.3, 0.1])
