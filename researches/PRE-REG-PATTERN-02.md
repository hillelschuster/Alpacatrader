# PRE-REG-PATTERN-02 — Event-Anchored Attention Digest

**FROZEN v1** (agent-encoded 2026-09-13 under the user's standing goal directive:
"only goal is profitability... work as much as needed and continue with tasks";
the user may amend). The Sec.6 scoring pass remains **user-gated and unrun**.
Results: E1 flush-touch EXHAUSTED (best silhouette 0.0411 < 0.05), E3
volume-spike EXHAUSTED (0.0433), E2 thrust INCONCLUSIVE_POWER (1,783 windows <
2,000 floor) — see `factory/artifacts/pattern_digest_events.json`, EXP-71, H030.
This does not replace PRE-REG-PATTERN-01: v1/v2 remain the all-minutes record.

## Why a new pre-reg
PATTERN-01 proved the all-minutes unit does not separate: v1 silhouette 0.0058,
v2 (Amendment A1, z-scored shape) best 0.0206 < the frozen 0.05 stopping rule,
declared EXHAUSTED. Amendment A1 requires a new pre-reg for either a different
unit of analysis or a learned embedding. This is the **different unit** branch,
chosen because the project's only OOS-validated conditional structure is the
flush event on a top-3 gainer (`PRE-REG-FLUSH-01`), not arbitrary minutes.

## 1. Unit of analysis (the change)
Windows anchored on **events** in the top-3 candidate population, all causal,
all on the 09:30–16:00 minute grid of `data/leaderboard`:
- **E1 flush touch** — a minute where `low <= 0.9 * running session max close`
  (the prior_flush episode start, per `lb18_episodes.py:50-71`).
- **E2 thrust** — a strict-state minute (`gain>=1.0 & pullback>=-0.01 &
  r15>=0.03`, per `lb18_oos.py:91-92`).
- **E3 volume spike** — a minute whose volume >= 5x the trailing 20-minute
  median (causal), for the rank-1 name at that minute.
Frozen window lengths L ∈ {15, 30, 60} minutes, right-aligned at the event
minute; each event is one sample (dedupe consecutive same-type events within L
minutes, first occurrence wins, frozen).
Each event type is analyzed and reported separately; no pooling across types.

## 2. Event census (deliverable, and a power gate)
Count events per type, per month, per symbol-day. If a type yields fewer than
2,000 windows, report the power limitation and mark its digest inconclusive
rather than interpreting it.

## 3. Channels (identical to PATTERN-01 v2)
bar return, range as % of close, close-position-in-range, volume vs trailing
20-minute median, cumulative gain from session open; per-window z-scored;
`minute_index` excluded from the clustering vector (post-hoc descriptor only).
No engineered stacks, no labels, no outcomes.

## 4. Representation (frozen)
Same machinery as v2: MiniBatchKMeans on flattened z-scored windows; k chosen
by silhouette over k ∈ {8, 16, 24}, single configuration frozen before
illustrations; seed 20260912. Plus, because the event count is far smaller than
2.13M, a DTW-medoid view per cluster for illustration only (DTW remains a
descriptive tool, not a predictor — H018 stands).

**Stopping rule (carried over):** if the best silhouette < 0.05 for an event
type, declare that type's shape-clustering EXHAUSTED and run no further variants
for it. If all three types are exhausted, the entire shape-clustering family is
closed and the only remaining branch is a learned sequence embedding — which
requires its own separate pre-reg.

## 5. Deliverables
Per event type: cluster envelopes (raw + z-scored), occupancy by time-of-day and
rank, example (date,ticker) instances, month stability of occupancy, cluster
sizes. Plus the census. These are descriptions; nothing here is a signal.

## 6. Scoring pass (separate, gated)
Not run until the user explicitly confirms. Same ruler as PATTERN-01 §5:
per cluster, forward MFE/MAE and bracket EV from the window end, month-blocked,
100bps friction, replication >= 2 of 3 chronological blocks; multiple-comparison
risk reported. No adoption without a new pre-reg + OOS.

## 7. Data
`data/leaderboard` (have). `data/iex_tape` as the live-compatible arm for the
coverage question (already measured: median 24.6% of session minutes per pair).
Sub-minute (SIP) only if a cluster survives scoring AND the user buys SIP.

## 8. Anti-patterns
No rerun of the all-minutes unit; no supervised target in the representation; no
k-shopping beyond the frozen rule; no interpreting a low-silhouette partition as
structure; honest power reporting per event type.

## 9. Artifacts
`factory/artifacts/pattern_digest_events_*.json` / `.parquet` / `_cards.png` from
producer `factory/scripts/pattern_digest_events.py`. Ledgers after the cycle.
