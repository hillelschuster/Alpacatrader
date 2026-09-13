# PRE-REG-BACKBONE-01 — Frozen-mechanism regime replication on 2021–2023

Draft frozen 2026-09-13. **Requires explicit user sign-off on download/staging
before any 2021–2023 data is examined.** No computation on those years until
then; 2024 (already burned) is the only debug surface.

## Purpose
Ask whether the frozen H025 mechanism — and the census event process — survive
three independent market regimes (2021 mania, 2022 bear, 2023) that are outside
the 2024–2026 examination window. This is a **regime-replication test of a
frozen mechanism**, not a discovery pass, and not an optimization opportunity.

## Prerequisites (must be certified BEFORE any H025 result on 2021–2023 is seen)
1. **PIT universe.** Local `data/pit/pit_symbols.parquet` covers vintages
   2023-11-01..2026-08-20 only. 2021–2023 membership must be staged from
   `yolo22/stock-pit-archives` (2021+) preserving vintage semantics. Ranking
   without correct PIT membership is survivorship-biased and invalid — top-3
   identity is highly sensitive to omitted later-delisted names.
2. **Split certification.** The leaderboard pipeline (`lb18.py`) shows no split
   filter; `clean_month.py` split exclusion is an optional flag;
   `certify_month.py` implements an overnight-ratio `split_suspect` check. With
   raw/unadjusted OHLCV, reverse splits manufacture fake +100% leader states.
   Certify split handling on 2021–2023 **and** audit the 2024–2026 tape for
   split artifacts (separate small study, reported first).
3. **Tape build.** Derive the same top-3 causal tape for 2021–2023 with the
   identical builder semantics (PIT, prev-close gain, last completed bar,
   top-3, minute grid).
4. **No pilot leak.** Any machinery debugging uses 2024 (already burned). The
   validation protocol below is frozen, then 2021–2023 are run once and
   reported as a whole.

## Frozen outputs (replication evidence; no adoption gates)
- H025 frozen engine per year: all + pf2 (n, mean, median, months+, worst
  month, fills/day), with the 2024-01..2025-02 OOS block as reference.
- Census event process per year: vacuum events, rank gradient, prior_flush
  profile, AM/PM split, halt-adjacent share.
- Split-suspect share of leaderboard names per year.

## Frozen interpretation rule
- **REPLICATES** iff pf2 mean > 0 in each year AND months+ >= 60% of months in
  each year AND the rank gradient direction holds (rank1 recovery > rank3).
- **FAILS** iff pf2 mean <= 0 in any year with >= 100 pf2 fills.
- Otherwise **MIXED**, reported as such. No narrative spin; no parameter
  change to H025 in any outcome; no adoption path.

## Honest labeling / contamination
Applying a mechanism designed on 2024–2026 to earlier years is
backward-in-time generalization: replication evidence, not OOS of the original
claim. The value is regime independence, not a fresh OOS pass.

## Cost
~36 months × ~450 MB raw download (~16 GB) + PIT staging; compute ~1–2 h;
135 GB disk headroom available.
