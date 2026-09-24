from __future__ import annotations

import polars as pl


def max_drawdown(values: list[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def block_report(daily: pl.DataFrame, tickets: list[dict]) -> dict:  # noqa: DICT_OK — JSON metrics schema
    values = daily["r_day"].to_list()
    months: dict[str, list[float]] = {}
    for day, value in zip(daily["date"].to_list(), values):
        months.setdefault(str(day)[:7], []).append(value)
    return {
        "days_n": len(values), "months_n": len(months),
        "mean_basket_day": sum(values) / len(values) if values else None,
        "median_basket_day": float(daily["r_day"].median()) if values else None,
        "worst_day": min(values) if values else None,
        "compounded_max_dd": max_drawdown(values),
        "monthly": {month: {"days_n": len(month_values),
                             "mean": sum(month_values) / len(month_values),
                             "sum": sum(month_values), "worst_day": min(month_values)}
                    for month, month_values in sorted(months.items())},
        "realized_ticket_net": sum(ticket["net"] for ticket in tickets if not ticket["open_end"]),
        "marked_ticket_net": sum(ticket["net"] for ticket in tickets if ticket["open_end"]),
        "ticket_n": len(tickets),
        "tail_mfe_ge_50_n": sum(ticket["mfe_raw"] >= 0.5 for ticket in tickets),
        "tail_retained_mean_mfe_ge_50": mean(
            [ticket["net_return"] / ticket["mfe_raw"] for ticket in tickets
             if ticket["mfe_raw"] >= .5]),
    }
