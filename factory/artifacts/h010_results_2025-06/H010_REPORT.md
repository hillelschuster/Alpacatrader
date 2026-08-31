# H010 — Overnight Gap / Opening-Range Hold vs Fail

**Data**: `data/clean_ohlcv_2025-06.parquet` (June-only, certified; 21,996,803 bars, 20 sessions, 14,213 tickers, 0 dup `(ticker,timestamp)`).
**Dates**: signals span **2025-06-03 .. 2025-06-30** (19 signal days). First session **2025-06-02 dropped** (no prior_close in June-only).

## Definition (as implemented)

Bar timestamps are stamped at **bar START** (verified: a bar labelled `09:30` carries the OHLC of 09:30:00–09:30:59).

- `prior_close` = previous session's last RTH close per `(ticker, et_date)`; first session 2025-06-02 dropped (10,919 tickers).
- `gap_pct` = `(open_0930 − prior_close) / prior_close`, where `open_0930` = open of the bar stamped 09:30. Universe: `|gap_pct| > 5%`.
- `opening_range_high_5m` = `max(high)` over bars stamped **09:30–09:34** (5 bars) — the first 5 minutes.
- `holds_opening_range_flag` = **no** bar stamped 09:35–09:44 (10 bars) has `low <= or_high`. First such bar = FAIL (fail_time recorded).
- Entry reference = **09:45 bar close** (strictly after hold/fail window ends). Forward returns 15/30/60m via exact `timestamp + h` join.
- **Liquidity gate**: `dollar_open` = Σ(close·volume) over 09:30–09:45 bars, floor **$1M**. See *Critical nuance* below.

Cost convention (sibling H006/H009): `roundtrip = cost_bps × 2`. Primary `--cost-bps 10` = **20bps RT**; sensitivity `--cost-bps 20` = **40bps RT**.

## Segment tables (20bps RT — primary, $1M liquidity gate)

| segment | h | n | wr | avg gross | exp_net |
|---|---|---|---|---|---|
| ALL >5% gaps | 15m | 928 | 0.491 | 0.1268% | **−0.0732%** |
| ALL >5% gaps | 30m | 906 | 0.480 | −0.0145% | **−0.2145%** |
| ALL >5% gaps | 60m | 851 | 0.489 | 0.0906% | **−0.1094%** |
| gap-up-hold | 15m | 8 | 0.500 | 1.1721% | **+0.9721%** |
| gap-up-hold | 30m | 6 | 0.500 | 0.1958% | **−0.0042%** |
| gap-up-hold | 60m | 6 | 0.167 | −3.5519% | **−3.7519%** |
| gap-up-fail | 15m | 595 | 0.484 | 0.1681% | **−0.0319%** |
| gap-up-fail | 30m | 579 | 0.487 | 0.1236% | **−0.0764%** |
| gap-up-fail | 60m | 547 | 0.537 | 0.4014% | **+0.2014%** |
| gap-down-hold | 15m | 1 | 0.000 | −4.4937% | **−4.6937%** |
| gap-down-hold | 30m | 2 | 0.000 | −11.5788% | **−11.7788%** |
| gap-down-hold | 60m | 2 | 0.000 | −17.6120% | **−17.8120%** |
| gap-down-fail | 15m | 324 | 0.506 | 0.0394% | **−0.1606%** |
| gap-down-fail | 30m | 319 | 0.470 | −0.1968% | **−0.3968%** |
| gap-down-fail | 60m | 296 | 0.409 | −0.2904% | **−0.4904%** |

**Hypothesis direction (20bps RT):**
- **gap-up continuation (hold − fail)**: 15m **+1.0040%** · 30m **+0.0722%** · 60m **−3.9533%**. Hold beats fail at 15m (on n=8) but the edge inverts violently at 60m (hold is net −3.75% vs fail +0.20%). **Not consistent across horizons.**
- **gap-down fade (fail − hold)**: 15m **+4.5331%** · 30m **+11.3820%** · 60m **+17.3217%**. Fail looks "better" than hold — but only because gap-down-**hold** is catastrophically negative (n=1–2; both legs deeply negative, fail only "less bad"). **Both legs net-negative; no executable fade.**

### 40bps RT (sensitivity)

| segment | h | n | wr | avg gross | exp_net |
|---|---|---|---|---|---|
| gap-up-hold | 15m | 8 | 0.500 | 1.1721% | **+0.7721%** |
| gap-up-hold | 30m | 6 | 0.500 | 0.1958% | **−0.2042%** |
| gap-up-hold | 60m | 6 | 0.167 | −3.5519% | **−3.9519%** |
| gap-up-fail | 60m | 547 | 0.537 | 0.4014% | **+0.0014%** |
| gap-down-hold | 60m | 2 | 0.000 | −17.6120% | **−18.0120%** |
| gap-down-fail | 60m | 296 | 0.409 | −0.2904% | **−0.6904%** |

## Critical nuance: the certified June data is dominated by thin/OTC names

Every |gap|>5% gap-signal in raw June data is over-populated by illiquid tickers. **Without a liquidity floor**, 62% of candidate days have an opening-range window with fewer than 5 bars and/or fewer than 10 hold-window bars (some with **zero** hold bars), so the "hold through 09:45" condition is undefined or trivially satisfied by missing data.

- Raw |gap|>5% candidate days: **3,075**; after $1M `dollar_open` floor: **982**.
- Among the 982: only **10 hold** vs **972 fail** (1.0% hold rate).
- Well-covered (or=5 & hold=10 bars): **767 / 982 (78.1%)** at the $1M gate; **only 4** in the raw universe.
- Forward-return nulls from exact-bar join (thin liquidity): h15=54, h30=76, h60=131 out of 982.

**Well-covered subset (or=5 & hold=10 bars, 20bps RT), n=767:**

| segment | h | n | wr | avg gross | exp_net |
|---|---|---|---|---|---|
| gap-up-hold | 15m | 4 | 0.250 | 0.4713% | **+0.2713%** |
| gap-up-hold | 30m | 3 | 0.333 | 0.7849% | **+0.5849%** |
| gap-up-hold | 60m | 4 | 0.000 | −3.3765% | **−3.5765%** |
| gap-up-fail | 15m | 490 | 0.488 | 0.1912% | **−0.0088%** |
| gap-up-fail | 30m | 478 | 0.508 | 0.2968% | **+0.0968%** |
| gap-up-fail | 60m | 456 | 0.557 | 0.7123% | **+0.5123%** |
| gap-down-fail | 15m | 266 | 0.515 | 0.1763% | **−0.0237%** |
| gap-down-fail | 30m | 259 | 0.490 | −0.0380% | **−0.2380%** |
| gap-down-fail | 60m | 250 | 0.416 | −0.1982% | **−0.3982%** |

This is the honest/clean beam, and it does **not** support the hypothesis:
- **gap-up-hold** is net positive at 15m/30m but on **n=4 and n=3** — statistically meaningless — and flips to net **−3.58%** at 60m.
- The **gap-up-fail** segment (baseline, n=456–490) is the only *stable* positive group, at 60m **+0.5123%** net 20bps RT. That is the **opposite** of "hold → continuation"; in June the failing gaps kept drifting, while the handful of holding gaps faded.
- **gap-down-hold** = 0 well-covered signals; gap-down direction shows **no** fade/hold separation (fail and hold both negative).

## Lookahead audit

- OR high uses only bars stamped **09:30–09:34** (5 bars, the first 5 minutes). No later bar influences it.
- Hold/fail resolves strictly within bars stamped **09:35–09:44** (10 bars); the flag uses only that window. `fail_time` is the first such bar.
- Entry uses the **09:45** bar close, strictly after the resolution window ends. Forward returns are measured from that same 09:45 close to the close at `09:45 + h`, via exact `(ticker, timestamp)` join — no bar from before the entry reference can leak in.
- `open_0930` uses only the 09:30-stamped bar's open; `prior_close` is the prior session's final RTH close; `gap` is computed before any intraday use.
- `dollar_open` (liquidity gate) sums only 09:30–09:45 bars — observable at/past entry, no forward-return lookahead.
- **No lookahead found.** All forward returns compute from the 09:45 close; the exact-timestamp join means a bar only contributes if it is exactly `entry_ts + h`.

**Spot-check (hand-verified): AAL 2025-06-13**, gap −5.138%, `or_high=10.51`. OR window highs {10.425, 10.48, 10.45, **10.51**, 10.505} → max **10.51** ✓. Hold window 09:35–09:44 first bar (09:35) low **10.37 ≤ 10.51** → **FAIL at 09:35** ✓. Entry 09:45 close **10.415** ✓. Algorithm flags correctly.

## Selftest (pre-filter output-identity)

On an `A*` ticker slice of 11,473 ticker-days, the candidate-day set derived via the [group_by-max → filter → join-back] pre-filter is **exactly identical** to brute-force `filter(|gap|>5%)` over the same frame: **267 = 267 rows, exact set equality True**. This confirms the output-identical candidate pre-filter (keeps all bars of candidate days, preserving OR/hold windows and forward lookups) matches a naive scan.

## Verdict: **REJECTED**

The hypothesis — *gaps that hold above the opening 5m high continue, gaps that fail fade* — has no evidence on certified 2025-06:

1. **The hold cohort barely exists.** Only **10 of 982** (>5% gap, $1M-gated) signals hold the opening range through 09:45; only **4** are well-covered gap-up-holds. A continuation thesis built on n=4 is unverifiable.
2. **Direction is non-monotonic and unstable.** gap-up-hold beats gap-up-fail by +1.00% at 15m (n=8), collapses to −3.95% at 60m (n=6). The only *stable* net-positive group is **gap-up-fail** (baseline gap-ups that failed, +0.51% 60m net) — the **inverse** of the hypothesis's continuation claim.
3. **gap-down "fade" is a mirage.** fail − hold is large only because gap-down-**hold** (n=1–2) is catastrophically negative (both legs deeply net-negative). No executable fade.
4. **No survivor**: no segment is stably net-positive across 15/30/60m at both 20bps and 40bps. The lone positive (gap-up-fail 60m +0.51% net 20bps, +0.31% net 40bps) is a *failure* signal, not the hypothesis's continuation, and it does not survive the hold-comparison framing.

## OOS status (honest)

- **Degenerate.** June 2025 = 20 sessions, signals span 19 days (2025-06-02 dropped, no prior close). Single month, no held-out period separate from the signal days. **This is a June-only in-sample evaluation, NOT a validated OOS result.**
- No train/test split was used — the entire certified month is the test and there is no out-of-sample month available in the staged data. Any numeric edge here (n=4 holds) is far below statistical significance and must not be treated as an OOS finding.
- Requisite next step: replicate on an OOS month (e.g. certified 2025-07) with the same liquidity gate and window semantics before any confidence.

## Files written

- `factory/artifacts/h010_results_2025-06/results_c10.parquet` (primary 20bps RT, 982 signals)
- `factory/artifacts/h010_results_2025-06/results_c20.parquet` (sensitivity 40bps RT, 982 signals)
- `factory/artifacts/h010_results_2025-06/results_c10_summary.json`
- `factory/artifacts/h010_results_2025-06/results_c20_summary.json`
- `factory/artifacts/h010_results_2025-06/run_c10.log`
- `factory/artifacts/h010_results_2025-06/run_c20.log`
- `factory/artifacts/h010_results_2025-06/H010_REPORT.md`
- `factory/scripts/experiment_h010.py`

*(Ledgers `HYPOTHESES.jsonl` / `EXPERIMENTS.jsonl` / `STATE.md` / certification artifacts NOT touched — orchestrator writes them.)*
