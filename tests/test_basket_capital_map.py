from __future__ import annotations


def test_checkpoint_outcomes_are_strictly_forward_and_date_guarded() -> None:
    from factory.scripts import basket_capital_map as mapping

    assert mapping.CHECKPOINTS == (585, 600)
    assert mapping.block_for("2021-02-01") == "block1"
    assert mapping.block_for("2025-02-03") == "block2"
    for day in ("2024-01-02", "2025-01-02", "2026-06-01"):
        try:
            mapping.block_for(day)
        except ValueError:
            pass
        else:
            raise AssertionError(f"date guard accepted {day}")

    assert mapping.outcome_indices([584, 585, 586, 600], 585) == [2, 3]
    try:
        mapping.outcome_indices([584, 585, 586], 586)
    except ValueError:
        pass
    else:
        raise AssertionError("checkpoint with no future outcome was accepted")


def test_input_alignment_and_complete_dimensions_are_enforced() -> None:
    from factory.scripts import basket_capital_map as mapping

    panel_rows = [{"date": "2021-02-01", "family": "A_pm", "et": 585}]
    joint_rows = [{"date": "2021-02-01", "entry": "A_pm", "N": 2}]
    assert mapping.validate_alignment(panel_rows, joint_rows) is None
    try:
        mapping.validate_alignment(panel_rows, [])
    except ValueError:
        pass
    else:
        raise AssertionError("misaligned inputs were accepted")

    assert len(mapping.expected_dimensions()) == 18
    try:
        mapping.validate_dimensions([{**panel_rows[0], "block": "block1"}])
    except ValueError:
        pass
    else:
        raise AssertionError("partial family/block/checkpoint tables were accepted")


def test_map_contract_is_descriptive_not_execution_pnl() -> None:
    import polars as pl

    from factory.scripts import basket_capital_map as mapping

    assert mapping.OUTCOME_FIELDS.isdisjoint(mapping.EXECUTION_PNL_FIELDS)
    assert mapping.EXECUTION_PNL_FIELDS == frozenset({"net", "net_return", "r_day", "pnl_c0"})
    rows = pl.DataFrame({"entry": ["A_pm"], "N": [2], "date": ["2021-02-01"],
                         "ge2_reach_20": [True], "ge2_reach_30": [False]})
    context = mapping.build_joint_context(rows)
    assert all(not (mapping.EXECUTION_PNL_FIELDS & row.keys()) for row in context)
    assert len(context) == 30
