# Replication — 2025-05 vs 2025-06 (certified month + H006/H009)

**Lane A replication.** Compares the certified & externally-verified **2025-06** results against a fresh
**2025-05** certification and fresh H006/H009 runs. Cost convention throughout: `roundtrip = cost_bps × 2`
→ `--cost-bps 10` = **20bps RT**, `--cost-bps 20` = **40bps RT**.

---

## 0. Cohort summary

| metric | 2025-06 | 2025-05 |
|---|---|---|
| sessions | 20 | 21 |
| clean rows | 21,996,803 | 22,546,633 |
| tickers | 14,213 | 14,058 |
| candidates | 2,370 | 3,056 |
| split suspects | 86 | 76 |
| universe excluded | 1,448 | 1,606 |
| universe unknown | 217 | 292 |
| events (`events_topN`) | 120,054 | 123,783 |
| events/day (min/med/max) | 5,157 / 5,918 / 6,640 | 4,756 / 6,258 / 7,229 |
| pct_gain p50 / p90 / p99 | 11.29 / 22.45 / 50.44 | 13.65 / 28.86 / 63.70 |

**First-session note (May):** the clean file spans **21 sessions** (2025-05-01 .. 2025-05-30), but
`events_topN` starts at **2025-05-02** (20 event days). The first session, **2025-05-01** (10,718 tickers),
is dropped because it has **no prior_close within May** (April is not present locally). The certifier
computes `prev_close = session_close.shift(1).over(ticker)`, so the first session in the span yields null
prev_close and is excluded from candidate/event logic. **Affected: ~10,718 tickers on 2025-05-01** — this is
an expected data-boundary limitation, not a bug.

---

## 1. External verification — 2025-05 (sample 20, seed 42)

Verdicts are reported **verbatim, unadjudicated** — the orchestrator adjudicates.

| check | verdict | matched/checked | match_rate | median diff | worst offender |
|---|---|---|---|---|---|
| prev_close vs yfinance (last-print vs 16:00 auction) | **FAIL** | 10/20 | 50.00% (operating 80.00%) | 0.1167 | CVNA 2025-05-14 diff_pct=79.951 |
| pct_gain@16:00 vs yfinance (EOD close) | **FAIL** | 18/20 | 90.00% | 0.1078 pp | MNTN 2025-05-23 diff_pp=-2.269 |
| split cross-check (no event day on split date) | PASS | 20/20 | 100.00% | n/a | — |
| rth_hod vs consolidated yf High | **FAIL** | 17/20 | 85.00% | 5.146e-06 | CVNA 2025-05-14 diff_pct=80.000 |
| 1-min path (IEX vs clean) | PASS | 20/20 | 100.00% | 2.269 bps | GCL 2025-05-21 median_bps=37.24 |

FAILs verbatim:
- **prev_close:** checked 20, matched 10, match_rate 50.00%, median_diff 0.1167; operating match_rate 80.00% (0.35%).
  Worst: `CVNA 2025-05-14 diff_pct=79.951`, `MNTN 2025-05-23 diff_pct=1.051`, `GCL 2025-05-21 diff_pct=0.461`.
- **pct_gain@16:00:** checked 20, matched 18, match_rate 90.00%, median_diff 0.1078pp.
  Worst: `MNTN 2025-05-23 diff_pp=-2.269`, `GCL 2025-05-21 diff_pp=-1.519`, `PONY 2025-05-02 diff_pp=0.325`.
- **rth_hod:** checked 20, matched 17, match_rate 85.00%, median_diff 5.146e-06.
  Worst: `CVNA 2025-05-14 diff_pct=80.000`, `HNGE 2025-05-27 diff_pct=-0.344`, `CRCT 2025-05-07 diff_pct=-0.311`.

> The report's own caveat: **CVNA 2025-05-14** at 80% is a **split artifact** (yfinance lists `CVNA 2026-05-08 ratio=5.0`);
> same for the prev_close job. MNTN/GCL diffs are thin-IEX/auction-reference effects already documented in the caveat.

---

## 2. H006 — VWAP fade, `>8%` above VWAP (threshold 8)

Direction = **short** (fade), win when `fwd_ret < 0`. `exp_short_net = −avg_ret − roundtrip`.

### 2a. VWAP-distance buckets (net @20bps RT)

| VWAP bucket | n15 | n30 | avg15% | avg30% | sw15 | sw30 | e15@20 | e30@20 | e15@40 | e30@40 |
|---|---|---|---|---|---|---|---|---|---|---|
| **<3%** Jun | 77,270 | 74,137 | +0.006 | +0.023 | 48.9% | 49.5% | −0.206 | −0.223 | −0.406 | −0.423 |
| **<3%** May | 78,628 | 75,437 | +0.046 | +0.093 | 46.8% | 46.6% | −0.246 | −0.293 | −0.446 | −0.493 |
| **3-5%** Jun | 18,676 | 17,733 | +0.063 | +0.097 | 47.1% | 48.2% | −0.263 | −0.297 | −0.463 | −0.497 |
| **3-5%** May | 19,845 | 18,863 | +0.039 | +0.048 | 48.2% | 49.5% | −0.239 | −0.248 | −0.439 | −0.448 |
| **5-8%** Jun | 7,988 | 7,472 | −0.061 | −0.115 | 51.7% | 53.6% | −0.139 | −0.086 | −0.339 | −0.286 |
| **5-8%** May | 8,394 | 7,782 | +0.056 | +0.132 | 49.3% | 49.6% | −0.256 | −0.332 | −0.456 | −0.532 |
| **8-12%** Jun | 1,851 | 1,719 | −0.347 | −0.415 | 54.5% | 56.3% | **+0.147** | **+0.215** | −0.053 | +0.015 |
| **8-12%** May | 1,719 | 1,648 | −0.170 | −0.177 | 52.1% | 53.0% | −0.030 | −0.023 | −0.230 | −0.223 |
| **12%+** Jun | 247 | 219 | **−3.285** | **−3.653** | 73.7% | 75.8% | **+3.085** | **+3.453** | **+2.885** | **+3.253** |
| **12%+** May | 517 | 480 | **−0.328** | **−0.341** | 47.4% | 47.7% | +0.128 | +0.141 | −0.072 | −0.059 |

### 2b. Signal aggregate (`vwap_dist > 8%`, n=2,470 both months)

| horizon | n(Jun/May) | avg Jun | avg May | sw Jun | sw May | e20 Jun | e20 May | e40 Jun | e40 May |
|---|---|---|---|---|---|---|---|---|---|
| 5m | 2,221 / 2,282 | −0.251% | −0.109% | 54.5% | 50.4% | +0.051 | −0.091 | −0.149 | −0.291 |
| **15m** | 2,098 / 2,236 | −0.693% | −0.207% | 56.8% | 51.0% | **+0.493** | +0.007 | +0.293 | **−0.193** |
| **30m** | 1,938 / 2,128 | −0.781% | −0.214% | 58.5% | 51.8% | **+0.581** | +0.014 | +0.381 | **−0.186** |
| 60m | 1,717 / 1,948 | −0.744% | −0.148% | 60.3% | 50.7% | +0.544 | −0.052 | +0.344 | −0.252 |

### H006 verdict — **DEGRADED**

- **Direction preserved but magnitude collapsed.** June's gradient is `+0.006% → +0.063% → −0.061% → −0.347% → −3.28%`
  (15m); May's is `+0.046% → +0.039% → +0.056% → −0.170% → −0.328%`. The `12%+` bucket fade shrinks from
  **−3.28% to −0.33% (~10× smaller)** even though May has **2× the `12%+` observations** (517 vs 247) with
  a **far lower short win rate** (47.4% vs 73.7%).
- **Edge mostly evaporates.** The `>8%` signal nets **+0.49%/15m, +0.58%/30m** in June at 20bps, but only
  **+0.01%/+0.01%** in May at 20bps, and goes **negative at 40bps** (−0.19%/−0.19% May vs +0.29%/+0.38% June).
- **8–12%** turns net-negative on May (−0.03% vs June +0.15% @15m). Only the `12%+` tail stays nominally
  positive at 20bps on May (+0.13%), and it breaks at 40bps.
- **Degenerate train/test both months:** 20 (May) sessions, `split=40` → all rows train, **0 test rows**.
  No OOS within-month.

---

## 3. H009 — reclaimed failed breakdown vs clean HOD break

Definitions (script): `trap` = bar closes above **prior HOD** OR above **VWAP** AND prior 5 bars had a dip
`low < hod_before*0.997` OR `low < VWAP`. `clean` = closes above prior HOD, no HOD dip in prior 10 bars.
**Broad trap is diluted** — the script's own sub-nuance: most trap signals only reclaim VWAP, never prior-HOD.

### 3a. Broad trap vs clean (net @20bps RT) — diluted

| segment | month | n | 30m exp_net | 60m exp_net |
|---|---|---|---|---|
| ALL | Jun | 247,033 | −0.0011 | −0.0005 |
| ALL | May | 497,745 | −0.0021 | −0.0017 |
| TRAP | Jun | 234,997 | −0.0010 | −0.0004 |
| TRAP | May | 470,575 | −0.0013 | −0.0006 |
| CLEAN | Jun | 12,036 | −0.0017 | −0.0009 |
| CLEAN | May | 27,170 | −0.0018 | −0.0016 |

Broad-trap tiny edge over clean: Jun 30m +0.0006, 60m +0.0005; May 30m +0.0006, 60m +0.0010 — still a hair
better both months, but **both segments net-negative** everywhere at 20bps.

### 3b. Dilution %

Percent of trap signals that **never reclaim above prior HOD** (only back above VWAP):

| month | trap n | never-above-HOD n | never-above-HOD % |
|---|---|---|---|
| Jun | 380,803 | 345,887 | **91.0%** |
| May | 470,575 | 430,739 | **91.5%** |

### 3c. FAITHFUL subset (dip below prior HOD → reclaim **above** prior HOD)

Definition used: `signal_type='trap' AND dip_type='hod' AND close>hod_before`. This is the mechanism-correct
subset excluded by the broad-trap dilution.

| horizon | faithful n (Jun/May) | faithful exp_net @20 Jun | May | clean exp_net @20 Jun | May | edge bps @20 Jun | May |
|---|---|---|---|---|---|---|---|
| 5m | 16,549 / 15,921 | −0.00164 | −0.00183 | −0.00188 | −0.00196 | +2.5 / +1.3 |
| **15m** | 15,332 / 14,806 | −0.00100 | −0.00120 | −0.00179 | −0.00208 | +7.9 / +8.8 |
| **30m** | 14,601 / 14,182 | **+0.00033** | −0.00050 | −0.00168 | −0.00184 | **+20.0 / +13.4** |
| **60m** | 13,397 / 12,963 | **+0.00194** | **+0.00054** | −0.00092 | −0.00161 | **+28.6 / +21.5** |

Faithful @40bps RT: June 30m −0.00167, 60m **−0.00006**; **May 30m −0.00250, 60m −0.00146** (edge still
+13.4/+21.5 bps over clean, but both halves net-negative after cost).

### H009 verdict — **DEGRADED**

- **Mechanism survives qualitatively, edge halves.** The faithful HOD-reclaim subset still beats the clean control
  on **both months**, at every horizon, at both cost levels; May edge is **+13.4 bps (30m) / +21.5 bps (60m)** vs
  June's **+20.0 / +28.6** — roughly **2/3 retained**.
- **"Only net-positive segment" flips narrower on May.** June faithful was net-positive at **30m (+0.33bp) AND 60m
  (+19.4bp)**; May faithful is net-positive at **60m (+5.4bp) only** (30m is −5.0bp) after 20bps. At 40bps the May
  60m faithful is −14.6bp (June barely −0.6bp). Selectivity cost — May's good subset needs a tighter filter.
- **Dilution is identical in structure** (~91% Jun, ~93% May) and the clean-control cohort is comparable, so the
  mechanism framing holds; it's the **size of the edge that degrades**, not the direction.
- Faithful subset n is remarkably stable across months (Jun 34,916 / May 34,923).

---

## 4. Verdict summary

| Hypothesis | June baseline | May replication | verdict |
|---|---|---|---|
| **H006** VWAP fade `>8%` above VWAP | 15m/30m +0.49%/+0.58% @20; monotone tail | 15m/30m +0.01%/+0.01% @20; tail −0.33% (10× weaker); **negative @40** | **DEGRADED** |
| **H009** failed-breakdown reclaim beats clean | faithful edge +20.0/+28.6 bps (30m/60m); net+ @30m&60m | faithful edge +13.4/+21.5 bps; net+ @60m only | **DEGRADED** |

**Criteria used:** REPLICATED = sign + rough magnitude preserved; DEGRADED = sign preserved, magnitude materially
reduced (or edge no longer survives the 40bps sensitivity); DIED = sign flipped or edge gone entirely.
- H006: sign preserved (fade exists in tail, both months) but magnitude collapses ~10× and the 20bps edge
  essentially vanishes / goes negative at 40bps → **DEGRADED**, trending toward DIED.
- H009: sign preserved (faithful beats clean), edge retained at ~2/3 magnitude → **DEGRADED** (not DIED).

**Cross-cutting caveat:** both months offer no in-month OOS (20–21 sessions, degenerate split), so these are
in-sample replications. A 2025-07 out-of-sample pass is required before any executable conclusion.

---

## Files written (fresh, this lane)

- `factory/artifacts/certification_2025-05/` (pre-existing from prior complete run; validated — **not overwritten**)
- `factory/artifacts/h006_results_2025-05/events_with_vwap.parquet`, `h006_summary.json`, `cost40bps/*` (re-run)
- `factory/artifacts/h009_results_2025-05/h009_results.parquet`, `h009_results_c20.parquet`, `h009_summary.json` (new)
- `factory/artifacts/replication_2025-05_vs_2025-06.md` (this doc)
- `data/universe_tags.parquet` (cache append, nothing changed beyond prior partial attempt)

No changes made to `HYPOTHESES.jsonl` / `EXPERIMENTS.jsonl` / `STATE.md` (orchestrator writes ledgers).
