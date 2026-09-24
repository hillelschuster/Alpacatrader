from factory.scripts import basket_f11


def test_f11_comparisons_are_frozen_and_missing_surfaces_are_blocked():
    assert basket_f11.FRICTION_BPS == (0, 50, 100, 150, 200)
    assert basket_f11.BLOCKS == {
        "2021-02_2023-12": ("2021-02", "2023-12"),
        "2025-02_2026-05": ("2025-02", "2026-05"),
    }
    rows = basket_f11.comparison_registry(quote_files=0)
    by_comparison = {row["comparison"]: row for row in rows}
    assert by_comparison["stored_open_friction_ladder"]["status"] == "RUN"
    assert by_comparison["conservative_minute_execution"]["status"] == "BLOCKED"
    assert "fill rule" in by_comparison["conservative_minute_execution"]["reason"]
    assert by_comparison["quote_aware_execution"]["status"] == "BLOCKED"
    assert "quote" in by_comparison["quote_aware_execution"]["reason"]
    assert by_comparison["capacity_curve"]["status"] == "BLOCKED"
    assert "account size" in by_comparison["capacity_curve"]["reason"]


def test_f11_does_not_encode_strategy_or_account_sizing_choices():
    assert basket_f11.strategy_constraints() == {
        "strategy_semantics_changed": False,
        "account_size_selected": False,
        "deployment_claim": False,
        "profitability_claim": False,
    }


def test_f11_sweeps_unique_f1_strategy_configs_not_friction_duplicate_rows():
    cells = basket_f11.frozen_cells()
    assert len(cells) == 60
    assert len({(c.entry_id, c.n, c.exit_id) for c in cells}) == 60
