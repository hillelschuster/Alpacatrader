#!/usr/bin/env python3
"""As-of observable peer / scanner context for the owned-claim event producer.

Every predictor produced here is a pure function of information observable at grid
minute ``t`` for a claim admitted at causal clock ``T``:

  * ``decision_px``  — the causal decision anchor, known at ``T`` (PM snapshot close of
    the last completed bar et <= T-1, or the race ``minute_full`` row known_by_t for the
    near-open clock).  This is the ONLY admission anchor used; it never reads a final
    whole-day status.
  * ``px`` / ``px_age`` — close of the last COMPLETED bar with et <= t-1 and its staleness
    (``t - px_et``), both available at ``t``.

Return semantics (declared, unambiguous)
----------------------------------------
``obs_ret = px / decision_px - 1``  is a SCANNER / OBSERVATION return: "how far this name
has moved since its causal admission price".  It is deliberately NOT the policy's
fill-based holdings return (that is ``ret_fill = px / fill_px - 1``, only defined once
``filled_asof``).  The two are kept separate on purpose: the peer/scanner features answer
"what is the observable roster context at t", while the policy's own dollar cashflows use
the execution/fill columns elsewhere.

Because only the observable price enters, a name's FUTURE fill/block/missing outcome can
never change any prefix feature: two rosters that differ only in a peer's future first open
(``futurelateopen`` vs ``no futureopen``) are byte-identical through every t before the
difference is observable.  No final ``filled``/``blocked``/``missing`` status and no
whole-day quality flag is read anywhere in this module.

Support / UNKNOWN discipline
----------------------------
  * A member is OBSERVED at t when ``decision_px`` and ``px`` are finite and strictly
    positive.  Otherwise its return is UNKNOWN (null) and it is excluded from means and
    percentiles but still counted so downstream can see the support.
  * A price is STALE (flag only, never censored) when its last completed bar is older than
    ``STALE_MAX_MIN`` clock minutes.  The last trade is still the best observable price, so
    it is used, but the staleness count is exposed.
  * Scanner (N3/N5) means and positive counts require FULL support (every present member of
    the rank<=N cohort is observed).  Partial support yields UNKNOWN for the scan mean while
    ``scan{n}_known``/``scan{n}_members`` still report the exact counts, so a model never
    silently averages a self-selected cohort.
  * Self-excluded peer aggregates (``sib_*``/``peer_ret_p*``/``basket_ret``) average the
    OBSERVED members (a partially known peer set is the honest as-of state) and report the
    supporting count in ``n_peers``; a null mean means no peer had an observable price.
  * Empty peer sets (single-member roster) report ``n_peers = 0`` with a null mean rather
    than inventing a value.

The peer fields keep the historical column names for source-hash-driven schema
compatibility but are REDEFINED here (see ``PEER_COLS``): every ``PEER_FEAT`` exposed by
the event producer now comes from this module, not from the panel's final-status aggregates.
"""

from __future__ import annotations

import polars as pl

# A last completed bar older than this many clock minutes is flagged stale (context only).
STALE_MAX_MIN = 5
# Scanner contexts preserved from the producer (top-N of the clock roster).
SCAN_NS = (3, 5)
# Historical peer column names, redefined as-of (schema compatibility).
PEER_COLS = (
    "n_peers",
    "sib_ret_mean",
    "sib_above_fill",
    "peer_ret_p25",
    "peer_ret_p50",
    "peer_ret_p75",
    "basket_ret",
    "peer_dvol5_mean",
    "rel_dvol5",
)
SCAN_COLS = tuple(
    f"scan{n}_{s}" for n in SCAN_NS for s in ("mean_ret", "positive", "known", "members", "stale")
)
_GROUP = ("day", "clock", "t")


def _fin(name: str) -> pl.Expr:
    return pl.col(name).cast(pl.Float64).is_finite().fill_null(False)


def _observable_exprs(has_age: bool) -> list[pl.Expr]:
    px = pl.col("px").cast(pl.Float64)
    dp = pl.col("decision_px").cast(pl.Float64)
    known = px.is_finite().fill_null(False) & dp.is_finite().fill_null(False) & (px > 0) & (dp > 0)
    if has_age:
        age = pl.col("px_age").cast(pl.Float64)
        stale = known & age.is_finite().fill_null(False) & (age > STALE_MAX_MIN)
    else:
        stale = pl.lit(False, dtype=pl.Boolean)
    return [
        known.alias("_obs_known"),
        pl.when(known).then(px / dp - 1.0).otherwise(None).alias("_obs_ret"),
        stale.fill_null(False).alias("_obs_stale"),
    ]


def add_observable_context(panel: pl.DataFrame, ns=SCAN_NS) -> pl.DataFrame:
    """Return ``panel`` with inherited peer columns replaced by as-of versions plus the
    N3/N5 scanner columns.  Only ``decision_px``, ``px``, ``px_age``, ``dvol5``, ``rank``
    and the (day, clock, t, ticker) grid are read — never status/fill/terminal columns."""
    missing = [c for c in ("px", "decision_px") if c not in panel.columns]
    if missing:
        raise ValueError(
            f"observable peer context needs causal columns {missing}; "
            f"panel has {sorted(panel.columns)}"
        )
    group = list(_GROUP)
    # Drop any inherited (final-status-derived) peer columns so the as-of rebuild cannot
    # collide with them and silently leave the leaked versions in place.
    stale_cols = [c for c in tuple(PEER_COLS) + tuple(SCAN_COLS) if c in panel.columns]
    p = panel.drop(stale_cols).with_columns(_observable_exprs("px_age" in panel.columns))

    # Basket context (includes self), per (day, clock, t).
    tot = p.group_by(group).agg(
        pl.col("_obs_ret").is_not_null().sum().alias("_tot_known"),
        pl.col("_obs_ret").fill_null(0.0).sum().alias("_tot_sum"),
    )
    p = p.join(tot, on=group, how="left")

    # Peer context (excludes self) via a self-join on the same minute grid.
    q = p.select(group + ["ticker", "_obs_ret", "dvol5"])
    peer = (
        q.join(q, on=group, how="inner", suffix="_p")
        .filter(pl.col("ticker") != pl.col("ticker_p"))
        .group_by(group + ["ticker"])
        .agg(
            pl.col("_obs_ret_p").is_not_null().sum().alias("n_peers"),
            pl.col("_obs_ret_p").fill_null(0.0).sum().alias("_peer_sum"),
            (pl.col("_obs_ret_p").is_not_null() & (pl.col("_obs_ret_p") > 0))
            .sum()
            .alias("sib_above_fill"),
            pl.col("_obs_ret_p")
            .drop_nulls()
            .quantile(0.25, interpolation="linear")
            .alias("peer_ret_p25"),
            pl.col("_obs_ret_p")
            .drop_nulls()
            .quantile(0.50, interpolation="linear")
            .alias("peer_ret_p50"),
            pl.col("_obs_ret_p")
            .drop_nulls()
            .quantile(0.75, interpolation="linear")
            .alias("peer_ret_p75"),
            pl.col("dvol5_p").filter(_fin("dvol5_p")).mean().alias("peer_dvol5_mean"),
        )
    )
    p = p.join(peer, on=group + ["ticker"], how="left")

    # Scanner context: top-N of the clock roster, applied to every member of that clock.
    for n in ns:
        s = (
            p.filter(pl.col("rank").is_not_null() & (pl.col("rank") <= n))
            .group_by(group)
            .agg(
                pl.col("_obs_ret").is_not_null().sum().alias(f"_k{n}"),
                pl.col("rank").count().alias(f"_m{n}"),
                pl.col("_obs_ret").fill_null(0.0).sum().alias(f"_s{n}"),
                (pl.col("_obs_ret").is_not_null() & (pl.col("_obs_ret") > 0)).sum().alias(f"_p{n}"),
                (pl.col("_obs_stale") & pl.col("_obs_known")).sum().alias(f"_st{n}"),
            )
        )
        p = p.join(s, on=group, how="left")

    exprs: list[pl.Expr] = []
    for n in ns:
        m = pl.col(f"_m{n}").fill_null(0)
        k = pl.col(f"_k{n}").fill_null(0)
        full = (m > 0) & (k == m)
        exprs += [
            pl.when(full)
            .then(pl.col(f"_s{n}") / pl.col(f"_m{n}"))
            .otherwise(None)
            .alias(f"scan{n}_mean_ret"),
            pl.when(full)
            .then(pl.col(f"_p{n}").cast(pl.Float64))
            .otherwise(None)
            .alias(f"scan{n}_positive"),
            k.cast(pl.Float64).alias(f"scan{n}_known"),
            m.cast(pl.Float64).alias(f"scan{n}_members"),
            pl.col(f"_st{n}").cast(pl.Float64).alias(f"scan{n}_stale"),
        ]
    exprs += [
        pl.when(pl.col("n_peers").fill_null(0) > 0)
        .then(pl.col("_peer_sum") / pl.col("n_peers"))
        .otherwise(None)
        .alias("sib_ret_mean"),
        pl.when(pl.col("_tot_known").fill_null(0) > 0)
        .then(pl.col("_tot_sum") / pl.col("_tot_known"))
        .otherwise(None)
        .alias("basket_ret"),
        pl.when(_fin("dvol5") & _fin("peer_dvol5_mean") & (pl.col("peer_dvol5_mean") > 0))
        .then(pl.col("dvol5") / pl.col("peer_dvol5_mean"))
        .otherwise(None)
        .alias("rel_dvol5"),
        pl.col("n_peers").fill_null(0).cast(pl.Float64).alias("n_peers"),
        pl.col("sib_above_fill").fill_null(0).cast(pl.Float64).alias("sib_above_fill"),
        pl.col("peer_ret_p25").cast(pl.Float64).alias("peer_ret_p25"),
        pl.col("peer_ret_p50").cast(pl.Float64).alias("peer_ret_p50"),
        pl.col("peer_ret_p75").cast(pl.Float64).alias("peer_ret_p75"),
        pl.col("peer_dvol5_mean").cast(pl.Float64).alias("peer_dvol5_mean"),
    ]
    p = p.with_columns(exprs)

    drop = ["_obs_known", "_obs_ret", "_obs_stale", "_tot_known", "_tot_sum", "_peer_sum"]
    drop += [f"_{t}{n}" for n in ns for t in ("k", "m", "s", "p", "st")]
    return p.drop([c for c in drop if c in p.columns])
