# $90 daily realised-loss stop — quantification against the frozen rule

Source: frozen `thr300bps|p50` replay trades (`adaptive_depth_sizing/replay/{validation,confirmation}`),
both allowed blocks only (2023 validation, 2025-02..2026-05 confirmation). Read-only.

## Method

Chronological replay of the bot's daily stop on the frozen no-stop trade sequence:

* realised per trade = the bot's metric: `qty * (exit_bid - entry_ask)` for known fills; UNKNOWN
  exits / no-match / unknown entries contribute 0 (the bot never books a charge for them).
  The trigger days are identical when `net_usd_25` is used instead, so the table below applies
  to both; the blocked-attempt effect is reported in `net_usd_25`.
* close events are processed before intents at the same clock (the bot's `_poll_orders` runs
  before `_drain_tasks`); an intent at/after the first close with cumulative <= -limit is blocked.
* this is a direct attribution on the frozen no-stop sequence, not a re-simulation of the
  replacement trades a blocked slot could fund. It is exact for "which funded attempts the stop
  refuses"; the author's 3 divergent replay days match the 3 days below exactly.

Sessions: validation 250; confirmation/late 332 (calendar spans 361 and 481 days).

## $90 result

| block | sessions | attempts | trigger days (stop fired) | blocked attempts | known net blocked | known P&L effect | $/session | top-10 blocked |
|---|---|---|---|---|---|---|---|---|
| validation | 250 | 62 | 6 | 0 | $0.00 | $0.00 | $0.000 | 0 |
| late | 332 | 142 | 22 | 3 | +$120.81 | **-$120.81** | **-$0.364** | 0 |
| total | 582 | 204 | 28 | 3 | +$120.81 | -$120.81 | -$0.208 | 0 |

The 3 blocked attempts (all late; the 6 validation trigger days had no later funded attempt):

| day | ticker | t | fill status | net_usd_25 |
|---|---|---|---|---|
| 2025-07-15 | CYCC | 720 | conditional_fill | **+$120.81** |
| 2025-08-18 | SNGX | 810 | unknown_exit_execution | None |
| 2025-11-25 | CETY | 785 | unknown_exit_execution | None |

* The one known blocked fill is a **winner**; it is outside the late top-10 (the late 10th is
  $259.88). The stop blocks 0 of the top-10 winners in either block; the top-5 tail is untouched.
* The two unknown attempts have no realised net. Under the research lower-bound convention
  (each UNKNOWN charged $1,000), blocking them avoids $2,000 of lower-bound loss
  (+$6.02/session late), but that is a bookkeeping convention, not realised P&L.
* The other 25 trigger days fire after the day's last funded attempt (no behavioural effect).
* The stop is a one-bad-fill cap, not a daily catastrophe cap: the worst observed daily
  realised loss is -$263.22 gross / -$265.32 net (2025-08-19); $90 is 0.34x that.

## Level alternatives (both blocks)

`fire` = days the stop triggers; `blocked` = funded attempts refused; `known` = known net effect
of refusing them (negative = the stop costs P&L); `unk` = unknown attempts refused.

| level | val fire | late fire | blocked known+unk | known net effect | top-10 blocked |
|---|---|---|---|---|---|
| $90 | 6 | 22 | 1 + 2 | -$120.81 late | 0 |
| $120 | 5 | 17 | 0 + 2 | $0 | 0 |
| $150 | 5 | 12 | 0 + 1 | $0 | 0 |
| $200 | 2 | 8 | 0 + 1 | $0 | 0 |
| $250 | 0 | 3 | 0 + 1 | $0 | 0 |
| $270 | 0 | 0 | 0 | $0 | 0 |
| $300 | 0 | 0 | 0 | $0 | 0 |
| $400 | 0 | 0 | 0 | $0 | 0 |

($150 keeps only the 2025-11-25 unknown block; $200 and above keep no known block; $270+ never
fires on the 582-session frozen sample.)

## Recommendation

**Do not keep $90.** It pays -6.1% of the late known edge (-$0.364/session) to block a
+$120.81 mid-tier winner, and it is far below the observed daily-loss distribution (fires on
22/332 late days). It does not cut the *top* tail in-sample (0 top-10 blocks), but it is a
one-fill cap that can cut a recovery winner.

Two defensible replacements:

1. **$150 — stop-digging rule.** Lowest level with zero known-P&L cost: it preserves the CYCC
   +$120.81 and the SNGX unknown, still fires on 12 late / 5 validation days, and still halts
   after the worst -$261 day (blocking only the CETY unknown). Evidence-based and it binds.
2. **$270-300 (≈1.0-1.1x the worst observed day) — catastrophe brake.** Never fires on the
   frozen sample, so it preserves every observed attempt including the unknowns and the winner;
   it only halts after a day worse than any of the 582 sessions. If the owner's intent is "do
   not cut the tail", this is the consistent level; if the owner wants a stop that actually
   binds, use $150.

Dropping it is also defensible: the frozen rule has no stop, the strategy is fat-tailed, and
the 15:55 flatten + $1,100 per-order sanity guard remain. A $300 brake costs nothing in-sample;
the $90 level is the only option the evidence clearly rejects.
