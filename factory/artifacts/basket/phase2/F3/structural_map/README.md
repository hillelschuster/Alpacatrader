# F3 own-path structural map [RUN]

This is a descriptive prerequisite to the separately frozen F3 release-module surface. It maps
the canonical A_pm/T570 top-2 filled own paths for the complete permitted development calendar.
Use `run_config.json`, `provenance.json`, and `relationship_tables.json` for the executable
scope, input hashes, output counts, and all block-level relationship strata. Month-keyed
`parts/month=YYYY-MM.parquet` are resumable producer outputs; `structural_daily.parquet` is their
deterministic merge.

Each row is state through one completed bar `t`. Entry-relative drawdown is decision close /
anatomy fill price − 1; breach duration counts consecutive completed closes below fill. The
running peak is the maximum own-path high from fill through `t`; peak drawdown is decision close
/ running peak − 1. MFE-to-date uses highs only through `t`; MFE surrendered is
`max(0, MFE-to-date − entry-relative close return) / MFE-to-date`, or zero where MFE is zero.
Time since high counts bars from the latest running-high bar through `t`. Failed reclaim means
a close below the running high after at least one post-high close below it. Recovery latency is
bars from the lowest low of the current continuous below-entry episode; completed recovery is
unavailable until close reclaims entry.

Forward max/min and final-close returns are relative to the decision close and consume only bars
whose `et` is strictly greater than decision `et`; no same-bar high is treated as future. The
last available bar therefore carries null outcome measures. `relationship_tables.json` reports
the registered magnitude × duration × recovery cross-products independently by block, retaining
zero-count strata as `n=0`; these bins are descriptive summaries only, not rule thresholds.

The producer rejects non-development dates, requires exactly 1,066 anatomy days split 734/332,
and checks calendar coverage before reading the per-day data. No sealed/reserved dates, outcome
fitting, strategy selection, profitability claim, or promotion is part of this artifact.
