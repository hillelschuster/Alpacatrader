#!/usr/bin/env python3
"""ENTRY-EV-01 Stage-A readout (pre-reg researches/PRE-REG-ENTRY-EV-01.md §2, §8).

Read-only over data/entry_ev/stage_a/<day>.parquet. Prints JSON, or writes it to
--out PATH. The only optional arguments are --input DIR (default: the frozen Stage-A
directory) and --out PATH (default: stdout).

Sections actually emitted (nothing else is claimed):
  1. `measurement`   -- what the numbers ARE: row weighting, horizon anchoring,
                        retrospective promotion label, unvalidated upstream ranks,
                        >= $1 previous-close population, and the explicit NOT_TESTED
                        list (sparse A+ conjunction events, qualified-name-conditioned
                        economics, delayed-entry / passive mechanisms).
  2. `pooled`        -- gross next-open -> next-open mean/median per horizon over ALL
                        guarded rows, with the DISCOVER/CONFIRM split. Rows are
                        minute-state rows, so these are minute-weighted, NOT
                        day-balanced and NOT portfolio PnL.
  3. `hindsight`     -- RETROSPECTIVE promotion anatomy. The defining coordinate
                        (promo_age < 0) resolves only after the full day is known, so
                        this namespace is descriptive anatomy, never a tradable region.
  4. `causal`        -- 8 t-computable proxy conditions on rank 6-10 rows. A proxy is
                        a proxy: nothing here shows the condition is itself tradable.
  5. `gate`          -- RETIRED_CONCEPTUALLY_INVALID. The old immediate-continuation
                        hurdle priced a marketable next-open continuation and said
                        nothing about delayed-entry / passive mechanisms. It is retired
                        and NO replacement hurdle is imposed here.

Every per-horizon table carries BOTH scales explicitly:
  * event-level keys (`top5_event_sum_share`, `mean_excl_top5_events`) -- the top five
    are five individual EVENTS, the largest by return, NOT five days;
  * a `day_balanced` block (`mean_day_balanced`, `top5_day_sum_share`,
    `mean_excl_top5_days`) -- an unweighted mean over per-day means, computed AFTER
    null filtering with the day key travelling with its own value. This is a
    sensitivity on measurement weighting. It is NOT portfolio PnL: no sizing, no
    costs, no overlap handling, no capital constraint.
"""
import argparse
import json
from pathlib import Path

import polars as pl

ROOT = Path("/home/hillel/projects/Alpacatrader/data/entry_ev/stage_a")
DISC_END = "2022-04-07"
HS = [1, 3, 5, 10, 15, 30, 60]
TABLE_HS = (1, 3, 5, 15, 30)

COLS = ["day", "t", "ticker", "rank_known", "gain", "px", "ret1", "ret3", "ret5",
        "ret15", "dd_from_high", "promo_age", "vol30_ratio"] + [f"fwd_ret_{h}" for h in HS]

SCHEMA = "entry_ev/stage_a_readout/v2"

MEASUREMENT = {
    "schema": SCHEMA,
    "source_table": "data/entry_ev/stage_a/<day>.parquet (frozen, discovery block only)",
    "days_allowed": "2021-02-01..2023-03-14; no protected / sealed / reserved read",
    "unit_of_observation": "one row = one (day, ET minute t, ticker) minute state that the "
                           "upstream board had already placed at rank_known <= 10",
    "row_weighting": "all means/medians outside the `day_balanced` blocks are ROW-weighted "
                     "(equivalently minute-state weighted): a minute carrying more names on "
                     "the board contributes more rows and therefore more weight, and a "
                     "ticker contributes one weight per minute it holds a top-10 rank. "
                     "There is no per-minute, per-ticker or per-day equalization.",
    "horizon_anchoring": {
        "decision_time": "t (the board stamp) is the DECISION time, not a fill time",
        "entry": "first lane bar whose ET stamp is >= t",
        "exit": "first lane bar whose ET stamp is >= t+h",
        "same_bar_collapse": "when the lane has a long gap, entry and exit can resolve to "
                             "the SAME bar, producing 0.0. `exact_zero_return_share` counts "
                             "ALL zero returns, including genuine unchanged prices; it "
                             "does NOT identify or quantify same-bar collapse. Exit stamps "
                             "are absent, so that cause cannot be measured from this table.",
        "return_definition": "gross open-to-open price ratio; fees, spread, slippage and "
                             "market impact are NOT included at any point",
    },
    "promotion_label_is_retrospective": {
        "definition": "promo_age = t minus the first ET minute of the FULL board day at "
                      "which that ticker held rank_known <= 5 anywhere on the board",
        "signs": "negative = before that ticker's first top-5 minute of the day; "
                 "0 = the promotion minute; null = the ticker never held a top-5 rank at "
                 "any minute of the full day",
        "consequence": "the pre-promotion window depends on a crossing after decision t; "
                       "the null distinguishes names that never promote during the full "
                       "day. These are retrospective labels, not decision-time features.",
        "no_impossibility_claim": "the limited t-computable proxies did not reproduce the "
                                  "retrospective value. Sparse events and small conjunctions "
                                  "were not tested; predictability in other representations "
                                  "remains open.",
    },
    "upstream_ranks_unvalidated": "rank_known is copied from the frozen upstream board. The "
                                  "builder only FILTERS rows whose published rank is already "
                                  "<= 10; it does not re-derive, re-sort or validate the "
                                  "ranking, and it does not validate prev_close. Whatever "
                                  "'rank 6-10' means upstream is carried through unchanged.",
    "population": "rows kept by the build-time guard: published prev_close non-null, "
                  ">= $1.00, not stale, not floor-qualified, no prevclose-discrepancy flag. "
                  "The sub-$1 cohort is excluded from the table entirely and is not "
                  "measured here (UNKNOWN for this readout).",
    "not_tested": {
        "sparse_a_plus_2_to_4_condition_conjunction_events": "NOT_TESTED",
        "qualified_name_conditioned_economics": "NOT_TESTED",
        "delayed_entry_mechanisms": "NOT_TESTED",
        "passive_limit_entry_mechanisms": "NOT_TESTED",
        "note": "this readout measures generic immediate next-open continuation on the "
                "top-10 minute board plus a handful of single-coordinate proxies. It makes "
                "no claim about any of the above; they are untested, not falsified.",
    },
}


def event_stats(sub: pl.DataFrame, h: int) -> dict:
    """Event-level (row-weighted) stats for horizon `h` plus a day-balanced sensitivity.

    The day-balanced block filters nulls on a FRAME (so each surviving row keeps its own
    day key) before grouping; a series-level drop_nulls() would leave `sub["day"]`
    unfiltered and mis-pair day keys with values.
    """
    col = f"fwd_ret_{h}"
    d = sub.filter(pl.col(col).is_not_null())
    v = d[col]
    n = len(v)
    if n == 0:
        return {"n_events": 0, "n_days": 0, "day_balanced": {"n_days": 0}}
    tot = float(v.sum())
    top5_events = float(v.sort(descending=True).head(5).sum())
    ndays = int(d["day"].n_unique())

    day_means = d.group_by("day").agg(pl.col(col).mean().alias("m")).sort("m", descending=True)["m"]
    nd = len(day_means)
    db: dict = {"n_days": nd, "mean_day_balanced": float(day_means.mean()),
               "median_day_mean": float(day_means.median()),
               "sum_of_day_means": float(day_means.sum()),
               "top5_day_sum": float(day_means.head(5).sum()),
               "best_day_mean": float(day_means.head(1).item()),
               "worst_day_mean": float(day_means.tail(1).item()),
               "top5_days_are": "the five DAYS with the largest per-day mean, ranked after "
                                "null filtering; NOT the five largest events",
               "is_portfolio_pnl": False}
    day_sum = db["sum_of_day_means"]
    db["top5_day_sum_share"] = (db["top5_day_sum"] / day_sum) if day_sum != 0.0 else None
    db["mean_excl_top5_days"] = (float((day_sum - db["top5_day_sum"]) / (nd - min(5, nd)))
                                 if nd > 5 else None)
    db["top5_day_sum_share_reading"] = (
        "share of the SIGNED sum of day means. With a negative sum the denominator's sign "
        "flips the ratio's sign, so judge the level from mean_day_balanced / best_day_mean "
        "/ mean_excl_top5_days, not from this ratio alone.")

    return {
        "n_events": n,
        "n_days": ndays,
        "mean": float(v.mean()),
        "median": float(v.median()),
        "exact_zero_return_share": float((v == 0.0).mean()),
        "sum_of_events": tot,
        "top5_event_sum": top5_events,
        "top5_events_are": "the five individual EVENTS with the largest return at this "
                           "horizon, NOT five days",
        "top5_event_sum_share": (top5_events / tot) if tot != 0.0 else None,
        "mean_excl_top5_events": (float((tot - top5_events) / (n - min(5, n)))
                                  if n > 5 else None),
        "day_balanced": db,
    }


def table(sub: pl.DataFrame, hs=TABLE_HS) -> dict:
    r = {"n_rows": len(sub), "n_days": int(sub["day"].n_unique())}
    for h in hs:
        r[str(h)] = event_stats(sub, h)
    return r


def evaluate_gate(causal: dict) -> dict:
    """The Stage-A immediate-continuation hurdle, retired.

    Kept for provenance as `retired_criterion`; deliberately emits NO eligibility
    boolean and NO replacement hurdle. A delayed-entry or passive mechanism earns its
    edge from the path AFTER entry, so an immediate-continuation shortfall says nothing
    about it -- the old gate converted an untested mechanism into a veto.
    """
    best = None
    for name, r in causal.items():
        for h in TABLE_HS:
            e = r.get(str(h))
            if not e or not e.get("n_events"):
                continue
            if best is None or e["mean"] > best["mean"]:
                best = {"region": name, "h": str(h), "n_events": e["n_events"],
                        "n_days": e.get("n_days"), "mean": e["mean"], "median": e["median"],
                        "mean_day_balanced": (e.get("day_balanced") or {}).get("mean_day_balanced")}
    return {
        "status": "RETIRED_CONCEPTUALLY_INVALID",
        "informational_only": True,
        "is_a_verdict": False,
        "retired_criterion": "Stage-B runs only where a Stage-A region gross mean AND median "
                             "exceed a stated margin (~120bps) over the measured mechanism "
                             "cost floor (~92-116bps round trip), at h<=30 next-open "
                             "continuation.",
        "why_retired": "that criterion prices an IMMEDIATE marketable next-open "
                       "continuation. Delayed-entry / passive mechanisms are not measured "
                       "by it at all, and Stage A measured no sparse A+ conjunction event, "
                       "no qualified-name conditioning and no delayed-entry economics. "
                       "Using it against them converted 'untested' into 'vetoed'.",
        "replacement_hurdle": None,
        "replacement_hurdle_note": "NONE imposed by this readout. Any such threshold is a "
                                   "separate registration with its own measured cost floor.",
        "mechanisms_status": {
            "delayed_entry": "NOT_TESTED",
            "passive_limit_entry": "NOT_TESTED",
            "sparse_a_plus_conjunction": "NOT_TESTED",
            "qualified_name_conditioned": "NOT_TESTED",
        },
        "hindsight_region_status": "retrospective anatomy (promo_age < 0 resolves only after "
                                   "the day closes); descriptive, not an enterable state and "
                                   "not evidence that the state is unpredictable",
        "best_causal_proxy_cell": best,
        "best_causal_proxy_cell_reading": "max over 8 single-coordinate proxies x 5 horizons "
                                          "= 40 cells, chosen post hoc. Reported so the "
                                          "pooled/proxy numbers stay checkable. It is NOT a "
                                          "gate, NOT a selection, and NOT an edge claim.",
    }


def build_readout(df: pl.DataFrame) -> dict:
    out = {"measurement": MEASUREMENT, "days": int(df["day"].n_unique()), "rows": len(df),
           "pooled": {}, "hindsight": {}, "causal": {}, "gate": {}}

    for h in HS:
        v = df[f"fwd_ret_{h}"].drop_nulls()
        d = df.filter(pl.col(f"fwd_ret_{h}").is_not_null())
        disc = d.filter(pl.col("day") <= DISC_END)[f"fwd_ret_{h}"]
        conf = d.filter(pl.col("day") > DISC_END)[f"fwd_ret_{h}"]
        out["pooled"][str(h)] = {"n": len(v), "days": int(d["day"].n_unique()),
                                 "mean": float(v.mean()), "median": float(v.median()),
                                 "exact_zero_return_share": float((v == 0.0).mean()),
                                 "disc": {"n": len(disc),
                                          "mean": float(disc.mean()) if len(disc) else None},
                                 "confirm": {"n": len(conf),
                                             "mean": float(conf.mean()) if len(conf) else None}}

    # ---- retrospective promotion anatomy vs t-computable proxies (rank 6-10) ----
    # `max`, not `first`: when two names sit at rank 5 in the same minute the gain5 pick was
    # arbitrary. On the frozen table this changes nothing (all 50 tied minutes carry an
    # identical gain), but the coordinate is now defined instead of accidental.
    r5 = df.filter(pl.col("rank_known") == 5).group_by(["day", "t"]).agg(pl.col("gain").max().alias("gain5"))
    dfj = df.join(r5, on=["day", "t"], how="left")
    mid = dfj.filter((pl.col("rank_known") >= 6) & (pl.col("rank_known") <= 10))
    hind = dfj.filter((pl.col("promo_age") >= -5) & (pl.col("promo_age") < 0))
    post = dfj.filter((pl.col("promo_age") >= 0) & (pl.col("promo_age") < 5))

    out["hindsight"] = {
        "namespace": "RETROSPECTIVE_LABEL_ANATOMY",
        "coordinate": "promo_age = t minus the ticker's FIRST top-5 minute of the day; "
                      "[-5,0) uses a crossing 1-5 minutes AFTER t, while a full-day null "
                      "uses never-promoted membership. Neither is known at decision t.",
        "not_a_tradable_state": True,
        "not_a_predictability_refutation": "limited causal proxies were tested, not a "
                                           "sparse-event/conjunction search",
        "promo_age_-5..0": table(hind),
        "promo_age_0..5_post": table(post),
        "note": "promo_age<0 conditions on the ticker's first top-5 entry occurring 1-5 "
                "minutes AFTER t: future information; not tradable as conditioned.",
    }
    causal = {
        "namespace": "T_COMPUTABLE_PROXIES_ON_RANK_6_10",
        "population_note": "rank 6-10 rows only, including the promo_age null rows (names "
                           "that never reached the top-5 that day); ranks are upstream-"
                           "published and unvalidated here",
        "rank6-10_all": table(mid),
        "rank6-10_ret5>0": table(mid.filter(pl.col("ret5") > 0)),
        "rank6-10_ret5>1pct": table(mid.filter(pl.col("ret5") > 0.01)),
        "rank6-10_ret1>2pct": table(mid.filter(pl.col("ret1") > 0.02)),
        "rank6-10_gap5>-1pct": table(mid.filter((pl.col("gain") >= pl.col("gain5") - 0.01) & (pl.col("gain") != pl.col("gain5")))),
        "rank6-10_vol30ratio>1.5": table(mid.filter(pl.col("vol30_ratio") > 1.5)),
        "rank6-10_vol30ratio<0.5": table(mid.filter(pl.col("vol30_ratio") < 0.5)),
        "rank6-10_at_dayhigh": table(mid.filter(pl.col("dd_from_high") > -0.005)),
    }
    out["causal"] = causal
    out["gate"] = evaluate_gate({k: v for k, v in causal.items()
                                 if not k.startswith("namespace")
                                 and not k.endswith("population_note")})
    return out


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ENTRY-EV-01 Stage-A readout")
    p.add_argument("--input", type=Path, default=ROOT,
                   help="directory of <day>.parquet Stage-A event tables "
                        "(default: the frozen data/entry_ev/stage_a)")
    p.add_argument("--out", type=Path, default=None,
                   help="write JSON here (default: stdout)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    df = pl.scan_parquet(str(a.input / "*.parquet")).select(COLS).collect()
    txt = json.dumps(build_readout(df), indent=1)
    if a.out is not None:
        a.out.write_text(txt)
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())