# STATE.md — Top-Gainer Research Factory

> **Current status (2026-10-05)**: append-only chronicle — read the tail first. The latest lane is
> cash-first fee-aware ownership (H042 / EXP-87; COMPLETE-DISCOVERY-POSITIVE-MEANS-NOT-VALIDATED);
> the direct-minute ownership work (H041 / EXP-86) finished COMPLETE-NEGATIVE; the 2026-10-03 lane is
> the two-method owned-claim discovery. Its first model numbers (the 8-event fitted value
> iteration, "FVI8", and the first `policy_return` pass) were **PROVISIONAL / INVALID-AS-CAUSAL**
> (future-status peer/scanner leakage, strict-pointer skipping of same-open releases,
> over-censoring of the fit mask, misapplied strict-control entry gate). The corrected 533-event
> rebuild and 383 test-day two-method replays are now **COMPLETE**: 0/176 net-positive cells in
> each method (352 combined) at 100/150bps, cash reconciliation 4.53e-14, **no edge**.
> Declared machine `source_correction_status: NOT_VERIFIED` (`owned_claim_findings.json`) — no
> structured source-correction verification record published; separately observed parent
> source/runtime checks passed (source pins, 8 financial regressions, review PASS). Three
> exploratory diagnostics (push/probe/retrieval) also complete — anatomy only, no policy/P&L. See
> the 2026-10-03 / 2026-10-05 tail entries.

**Last updated**: 2026-08-28 (initial snapshot; chronicle continues to 2026-10-05)
**Phase**: 2025-06 AND 2025-05 CERTIFIED (both with microstructure adjudications),
on Drive. H006 tail + H009 faithful replicated on May: NOT ROBUST (H006 edge →~0,
H009 edge halved). H007/H010 killed. Next: download+certify 2025-07 as the true
OOS month for H006/H009; run H008.

## ML Phase — Overnight Mission (2026-09-01, in progress)

Pivot approved by user: systematic ML pattern discovery on the certified event
stream, replacing hypothesis-by-hypothesis testing (all ten H001–H010 now tested;
H008/H009 live on as features).

- **Spec**: `factory/artifacts/ml/FEATURE_SPEC.md` v1 + oracle review (design
  critic subagent, ADOPT/MODIFY verdicts) + reviewer leakage audit (10 findings,
  3 must-fix code items — all implemented).
- **Target**: raw `fwd_ret_60m` winsorized ±10% (constant cost shift moved to
  evaluation). Cost scenarios 20/40bps RT at eval only.
- **Sampling**: `sample_weight = 1/n_events(episode)`, cap 120 (episode-equal
  influence). Evaluation always on FULL population.
- **Splits**: train 2025-05+06, dev 2025-07 (burned: early stop + threshold only),
  final OOS 2025-08..12 certifying via `cert_chain.sh`. Nov–Dec frozen as re-OOS
  if August is ever peeked. Rolling-origin added later if first result is alive.
- **Feature build** `factory/scripts/build_features.py`: minute grid per event
  (ticker,et_date), close/high ffill, low/volume NULL on missing bars (real bars
  only for dips + vol stats); H008 RVOL baselines (null at 09:30 — expdv=0 trap);
  H009-faithful trap/reclaim on grid (`hod_before = hod.shift(1)`); range_pos via
  running cum extremes (reviewer HIGH fix); gain_vel time-based; market_ret_5m
  within-session; t+1m-entry label variants (executable-entry proxy; edge must
  survive both entry timings). Outputs `data/ml_features/features_YYYY-MM.parquet`
  (gitignored) + `coverage_YYYY-MM.json`.
- **Coverage 2025-07** (139,400 events): rvol .896, ret_30m .942, vwap_dist 1.0,
  trap_reclaim 1.0, market_ret_5m .993, fwd_ret_60m .738, fwd60_t1entry .683.
- **Training** `factory/scripts/train_ml.py`: LightGBM (single config, depth 6,
  min_data 200) + ElasticNet baseline; decile tables day-ranked; episode-best
  (one trade per ticker-day); day-clustered bootstrap CIs; daily-PnL drawdown;
  Spearman IC; t+1-entry variant; long+short; @20/40bps. Aug–Dec evaluated once,
  frozen.
- **Accepted biases (documented, not fixed)**: non-PIT universe tags (yfinance
  2026 fetch; PIT archive = future option); certify `n_bars>=30` full-day gate
  conditions on the future (survivor-flavored population; uniform across months);
  H009 dip window 5 grid-min (tighter than H009's 5 bars post-halt).
- **2025-03 downloaded late** (May 1 baselines reach into March); May features
  rebuilt after March clean.

## ML v1 OOS — VERDICT: GO (2026-09-01)

Single frozen pass (protocol pre-registered in `factory/artifacts/ml/OOS_PROTOCOL.md`):
LightGBM depth-6 (57 iters), train May+Jun, dev Jul, OOS Aug..Dec (293,687 events).
**5/5 gates PASS.** Details: `factory/artifacts/ml/REPORT_OOS_v1.md`.

- D10 (top-10% score/day) all-events net @20bps: +10/+49/+23/+20/+6 bps monthly,
  pooled +22.1bps, n=49,200. t+1m-entry pooled +22.4bps (entry-timing robust).
- wr gradient D1→D10 monotone in all 5 OOS months; D1 = −39..−92bps (avoid zone).
- @40bps D10 dead (−25..−37bps) → edge requires ≤30bps RT execution.
- ElasticNet reproduces ordering 5/5 (structure not a tree artifact).
- Phase 6 composite rv>4 & vwap_dist>3% & tod<270, frozen on Nov–Dec: **+89.4bps
  @20bps, wr .605 (n=3,847 full set)**; survives 40bps in label-complete subset.
  Adversarial review: D10 GO claim audited CLEAN; composite PARTIALLY validated —
  the design grid was informed by an earlier slice peek at Nov–Dec (disclosed in
  REPORT_OOS_v1.md). Definitive test = exact frozen rule on next fresh month.
  Script: factory/scripts/composite_ml.py. Sizing research next.
- Dead at: D10@40bps; episode-best picking adds nothing (breadth portfolio).
- Next: pre-register composite rule (rv>3 & vwap+2% & tod<240 built from Aug–Oct,
  validate Nov–Dec untouched); bot integration is a recommendation only (bot off-limits).

## 2025-06 Certification — DONE (2026-08-28)

Internal gates (certify_month.py, `factory/artifacts/certification_2025-06/`):
20 sessions, 21.99M rows, 14,213 tickers → 2,370 candidates (86 split suspects,
1,448 universe-excluded, 217 universe-unknown), **120,054 events**; per-day
5157/5918/6640; pct_gain p50 11.29% / p90 22.45% / p99 50.44% / max 148.51%.
All internal gates PASS (checks.json). Last clean bar ~15:59 (no exact 16:00 bar).

External verification (verify_external.py, 20 ticker-days, seed 42):
pct_gain@EOD PASS (95%, 0.042pp) · splits PASS (100%) · rth_hod vs yf High PASS
(100%, 0.0083%) · 1-min path PASS (4.14bps) · prev_close PASS at operating
tolerance (95% within 0.35%). The only median-gate breaker (UAMY 2025-06-06
+0.662%) was adjudicated as a genuine 15:59 print (56,586 shares) vs yf's 16:00
auction close 3.00 — microstructure, not error; the 0.05% median gate is
miscalibrated for microcaps. Details: external_verification_report.md.
Survivorship caveat stands (current-state exchange tags, not PIT snapshots).

## 2025-05 Certification — DONE (2026-08-28, adjudicated)

Internal gates (`factory/artifacts/certification_2025-05/`): 21 sessions, 22.5M
rows, 14,058 tickers → 3,056 candidates, **123,783 events**; pct_gain p50 13.65%
/ p90 28.86% / p99 63.70%. First session 2025-05-01 dropped (no April locally).

External verify (seed 42): after **split-normalization of the yf reference**
(yf serves split-adjusted OHLC in both auto_adjust modes; ref = yf_price ×
product of split ratios with ex-date AFTER the sampled session — e.g. CVNA
5:1 split 2026-05-08 turned a false 79.95% diff into −0.243%), residual
prev_close/pct_gain/rth_hod offenders (MNTN 05-23, GCL 05-21, HNGE 05-27)
were adjudicated from bars as genuine print-vs-16:00-auction microstructure
(hyper-volatile IPO / dead-tape-then-hot / healthy-tape print). Splits + 1-min
path PASS. Verdict: **ADJUDICATED PASS — 2025-05 CERTIFIED**. Same 0.05%
median-gate miscalibration note as June. Full evidence in
certification_2025-05/external_verification_report.md.

## May Replication of Provisional Positives — NOT ROBUST (2026-08-28)

- **H006 (>8% VWAP fade)**: May n=2,470, exp_net ~0 at 20bps RT (June +0.49%/15m);
  40bps RT negative (−0.19%). 12%+ tail −0.33% vs June −3.29%. **DOWNGRADED
  survivor-provisional → not robust.**
- **H009 faithful subset**: direction preserved, edge halved (+21.5bps @60m vs
  +28.6; 30m +13.4bps not net-positive); 40bps negative. Dilution 91.5% stable.
  **Weak replication only.**
- Comparison: `factory/artifacts/replication_2025-05_vs_2025-06.md`.
- Implication: no hypothesis currently justifies building. The decisive test is
  a forward OOS month: download + certify 2025-07, run H006/H009 unchanged.

## 2025-07 OOS — COMPLETE (2026-08-31)

- **Certification 2025-07 DONE** (`certification_2025-07/`): 22 sessions, 24.36M rows, 14,611 tickers,
  2,657 candidates, 67 split suspects (< June's 86), 139,400 events (4,958–7,360/day).
  Certified with **BOTH** clean_ohlcv_2025-06 + 2025-07 files so 2025-07-01 sessions get the
  2025-06-30 prior-session close (user-corrected design; certified clean_2025-07 predates this fix).
- **External verification**: 1-min path PASS 20/20 (independent Alpaca IEX tape), split cross-check PASS 20/20;
  prev_close/pct_gain/rth_hod FAIL raw gates at 55%/85%/90% — statistically identical to May's
  adjudicated-and-accepted profile (50%/90%/90%; medians slightly better than May). Adjudicated ACCEPT
  (same reference artifacts: last-print vs 16:00 auction, consolidated-vs-feed highs). Run-note: first
  attempt had IEX checked=0 (missing python-dotenv in ephemeral uv env); re-run with --with python-dotenv.
- **H006 KILLED by July OOS** (EXP-H006-2025-07): tail 12%+ n=1,070 (4.3× June) exp −0.065% @20bps
  (June: +3.08% on n=247); monotone gradient absent; 8–12% bucket flipped positive.
- **H009 not executable standalone** (EXP-H009-2025-07): relative edge vs clean replicates 3/3 months
  (+13.7bps @60m July-only, halving each month: 28.6→21.5→13.7) but absolute net ≈0 @20bps, negative @40bps;
  June's 10–11am concentration absent in July. Faithful-subset filters reconciled (May used strict,
  June used loose — both now reported for all months).
- Comparison: `factory/artifacts/replication_2025-05_06_07.md`.

## Where We Are (summary)

H001–H005 (continuation-family hypotheses) were tested on 2025-07 and all killed
(net negative after 20bps roundtrip, universal OOS decay). Results are recorded but
carry a **data-quality caveat**: they ran on a pipeline whose `prior_close`,
split-handling, and universe filtering are not yet trustworthy (details below).
The verdicts are probably still correct (edges were negative, not falsely positive),
but no further conclusions may be built on this foundation until one month is
certified end-to-end.

**Current objective**: certify 2025-06 (chosen because July 2025 is burned by
H001–H005 experiments; May 2025 remains available as trailing baseline history).
Then set up the bounded Google Drive artifact workflow, then resume hypotheses
(H006–H010) on certified data.

## Certification Gates (2025-06 must pass all)

1. **Previous-session close**: `prior_close` must be the previous regular-session
   close per ticker (not "last bar before target date"). Spot-check vs yfinance
   raw daily closes: ≥99% of sampled ticker-days match within 0.1%.
2. **Splits**: detect overnight ratios far from 1 (e.g. <0.5 or >2) per ticker;
   exclude or adjust; cross-check sampled events against yfinance splits.
   No top-N event may span an unhandled split boundary.
3. **Universe**: NYSE/NASDAQ/AMEX common-stock filtering (exclude OTC/pinks,
   warrants, units, preferreds). PIT snapshots not yet downloaded — current-state
   exchange tags (yfinance/Alpaca assets) accepted for 2025-06 with noted
   survivorship caveat.
4. **Liquidity/participation**: events require price ≥ $2, cumulative dollar
   volume ≥ threshold (default $5M), and recent active volume.
5. **Intraday rank + true HOD**: recompute per-minute rank and RTH HOD; verify
   sampled minutes against independent recomputation.
6. **Independent path verification**: sample top-gainer ticker-days; compare 1-min
   bars and HOD against Alpaca IEX historical bars (free tier). IEX ≠ consolidated
   tape — use as sanity check with tolerance, not absolute truth. yfinance 1m is
   unavailable for 2025-06 (30-day window).
7. **Labels**: forward returns (1/3/5/15/30/60m), MFE/MAE spot-checked against bars.

Deliverable: `factory/artifacts/certification_2025-06/{report.md, checks.json,
events_topN.parquet}` — report states PASS/FAIL per gate with numbers.

## Known Data-Integrity Issues (verified in code, 2026-08-27)

- `clean_month.py`: PIT universe join and split exclusion are unimplemented stubs (`pass`).
- `rank_day.py` prior_close = last close before target date **within the monthly
  file** → (a) wrong when a ticker skipped sessions, (b) undefined on month's first
  trading day (ticker dropped), (c) raw unadjusted prices across split dates create
  fake huge % gains that win the rank.
- mito0o852/OHLCV-1m is **raw/unadjusted** (Finnhub). Splits MUST be handled
  explicitly. Audit found split signals (HMBL 890 jumps >20%, HCTI, OPEN, GIBO).
- ~25% of tickers sub-$1 OTC noise; price floor $2 only partially filters;
  no exchange/asset-type filter exists yet.
- Known duplicates (~(timestamp,ticker) dedup exists in both clean and rank steps).

## Datasets

| Source | Role | Status |
|---|---|---|
| `mito0o852/OHLCV-1m` | 1-min OHLCV backbone (raw, unadjusted) | 2025-05/06/07 downloaded+cleaned |
| `yolo22/stock-pit-archives` | PIT universe snapshots 2021+ | NOT downloaded |
| `speb/financial-data` stock_split_events | split dates | NOT downloaded |
| yfinance (installed 1.4.1) | external verification: daily raw closes, splits | available |
| Alpaca data API (IEX feed) | external verification: 1-min bars | keys present |

Data philosophy: sources are not sacred. Replace/supplement if materially better
reconstruction exists. Requirement = best practical data + correct methodology +
point-in-time integrity. Do not overengineer beyond what affects validity.

## Hypothesis Ledger (summary — canonical: HYPOTHESES.jsonl)

- H001 pullback-reclaim continuation — KILLED (2025-07)
- H002 HOD breakout — KILLED (2025-07)
- H003 gain×volume×TOD — KILLED (2025-07; inverted volume finding: high DV underperforms)
- H004 micro-pullback above VWAP — KILLED (2025-07; VWAP filter adds nothing net)
- H005 rank persistence — KILLED (2025-07; persistence less bad, still negative)
- Common failure: mean reversion dominates; 20%+ extremes worst; nothing cleared 20bps cost.
- CAVEAT: all on uncertified pipeline (prior_close/splits/universe gaps above).
  Treat as strong prior, not final truth.
- H006 VWAP-distance fade — **TESTED 2025-06 + replicated 2025-05: NOT ROBUST.**
  June (n=2,470): exp_net +0.493%/15m +0.581%/30m after 20bps RT; 12%+ tail
  (n=247, +3.09%/15m). May replication: exp_net ~0 at 20bps, −0.19% at 40bps;
  tail −0.33%. Edges concentrated in rare June tail; no cross-month stability.
  Artifacts: h006_results_2025-06/, h006_results_2025-05/.
- H007 consolidation-then-break — **TESTED 2025-06 certified: KILLED.** Contraction
  (15m range <1%) WORSE than grind control (≥1.5%): exp_net 60m −0.179% vs
  +0.241% (spread −0.420pp) net 20bps RT; all sub-segments negative; rolling-ATR
  arm untested. Artifacts: h007_results_2025-06/.
- H008 RVOL vs TOD baseline — queued (needs May 20-day baseline → May certification first)
- H009 failed-breakdown reclaim — **TESTED 2025-06 + replicated 2025-05: WEAK.**
  June faithful subset (dip below prior HOD → reclaim above, n=34,916): +20bps
  @30m, +29bps @60m net 20bps RT; broad trap bucket negative (91% VWAP-only
  dilution). May replication: direction preserved, edge halved (+21.5bps @60m),
  30m not net-positive, 40bps negative. Dilution 91.5% stable. Artifacts:
  h009_results_2025-06/, h009_results_2025-05/.
- H010 overnight gap / opening-range hold — **TESTED 2025-06 certified: KILLED.**
  Gap-up-hold continuation positive only at n=8/n=6 (collapses −3.75% @60m);
  stable positive is gap-up-FAIL (+0.51% @60m net 20bps — inverse of claim);
  gap-down segments n≤2 unusable. Required added $1M open-DV gate (62% of
  |gap|>5% universe thin/OTC). Degenerate OOS (19 signal days). Note: bars
  stamped at bar START. Artifacts: h010_results_2025-06/.
- RULE: single-month June results have degenerate OOS — provisional positives
  (H006 tail, H009 faithful) must replicate on a second month before further building.
- COST CONVENTION: experiment scripts compute roundtrip = cost_bps × 2 →
  `--cost-bps 10` = 20bps RT (H001–H005 standard).

## Storage / Google Drive Workflow — LIVE (2026-08-28)

rclone v1.75.0 at `~/.local/bin/rclone`, remote `gdrive:` configured (user's own
GCP OAuth client; shared client_id retiring 2026). Remote root:
`gdrive:algo-research/alpaca-top-gainers/`. Uploaded + MD5-verified
(`rclone check --download`, 0 diffs): `certification/2025-06/` (5 files incl
events_topN.parquet 8.3MB), `ledgers/`, `docs/`, `data/universe_tags.parquet`.

Gotcha: `rclone copy FILE DIR/FILE` creates a DIR named FILE — use `rclone copyto`
for single files. Upload valuable certified/derived artifacts only (no raw public
datasets; Drive is not a database). Don't let storage work delay research.

## Next Actions (ordered)

1. ~~Download + clean + certify 2025-07~~ DONE (2026-08-31, see above).
2. ~~Run H006 + H009-faithful on certified 2025-07~~ DONE: H006 killed; H009 direction-persistent
   but not executable standalone (ledgers updated).
3. ~~H008 RVOL vs 20-day TOD baseline~~ DONE (2026-09-01, EXP-H008-2025-06_07): relative edge real
   (+7..+28bps @60m, 6/6 volume-matched cells, 2/2 months) but absolute ≈0 to negative; stack with
   H009 dead in July. All ten hypotheses now tested on certified data; no standalone executable edge
   survives 20bps RT. Next: synthesis decision (see STATE.md Where We Are).
4. Upload new artifacts (July cert, May/June re-verified outputs, May
   replication artifacts, ledgers, STATE) to Drive + MD5 verify; prune local if
   disk >90%.

## Important Commands

```bash
# Download/audit/clean a month (HF source)
python factory/scripts/download_month.py --year 2025 --month 06
python factory/scripts/audit_month.py --file data/ohlcv_2025-06.parquet
python factory/scripts/clean_month.py --file data/ohlcv_2025-06.parquet

# Certification (new)
python factory/scripts/certify_month.py --file data/clean_ohlcv_2025-06.parquet --month 2025-06
python factory/scripts/verify_external.py --month 2025-06 --sample 20

# Legacy (used for H001-H005, superseded by certify path)
python factory/scripts/rank_day.py --file data/clean_ohlcv_2025-07.parquet --date 2025-07-02
```

## Key Paths

- Research root: `factory/`
- Canonical: `factory/RESEARCH_GOAL.md`, `factory/STATE.md`, `factory/AGENTS.md`
- Ledgers: `factory/HYPOTHESES.jsonl`, `factory/EXPERIMENTS.jsonl`
- Scripts: `factory/scripts/` (13 scripts: download/audit/clean/rank/extract +
  experiment_h001…h009)
- Data: `data/` (raw+clean 2025-05/06/07 parquet; ~6.5GB incl. ~3.8GB ranked
  intermediates — ranked files are reproducible, delete after artifact upload)
- Artifacts: `factory/artifacts/` (h001–h005 summaries/results,
  h001_h005_synthesis.md; ranked_*.parquet)
- Note: `factory/artifacts/decisions.jsonl*` and `data/journal/` are live-trading
  leftovers, not research products — exclude from research uploads.

## ML Phase 2 — live translation (2026-09-01 session)

Mission: convert D10 ranking into executable admission, understand archetypes, sequence
real trades, exploit composite, execution costs, fresh 2026 evidence, paper-bot spec.

### Live-causal admission (live_admission.py, frozen theta from May-Jul only)
| rule | n | net @20bps | @40 | t1 | months |
|---|---|---|---|---|---|
| REF whole-day D10 (non-executable) | 49,210 | +22.1bps | +0.2 | +26.3 | 5/5 |
| M1 fixed theta | 44,221 | +22.6 | +0.3 | +26.2 | 5/5 |
| M2 rolling-threshold | 51,137 | +19.2 | -0.8 | +22.4 | 4/5 |
| M3 vis-rank<=2 + theta | 35,186 | **+29.0** | **+9.0** | +32.8 | 5/5 |
| M4 first-crossing | 1,100 | +26.5 | +6.5 | +33.7 | 4/5 |
| M5 persist 2of3 | 42,822 | +24.9 | +4.9 | +28.2 | 5/5 |
| **M3 x composite** | 8,170 | **+90.5** | **+70.5** | wr .570 | 5/5 |
theta_fixed=0.00115 (May-Jul p90), theta_hi=0.00340 (p97). M3 = score-rank<=2 within
current minute's visible candidate set (et_date,tod_min) — fully causal, beats D10.
Flow: 332 events/day (M3), 83/day (composite).

### Sequencing (sequencing.py, t1-entry fills, OOS Aug-Dec)
First-signal entries are below stream average. S4 scale-in (unit1 first qualifying minute,
unit2/3 at later score>=theta_hi crossings, +5m/+20m spacing) best: composite stream
+77.4bps/unit, +130bps/episode, 5/5 months, 1.7 units/ep. Re-entry-after-cooldown WORST
(late-stage contamination). Chasing first-crossing per episode (M4) degrades vs M3.

### Archetypes (archetypes.py, OOS, post-peek descriptive)
- Money map (M3 stream): rvol>8 & vwap 3-8% +84bps; rvol>8 & vwap>8% +201bps;
  rvol 2-8 rows NEGATIVE on the M3 stream -> extreme volume is the separator, not the model score alone.
- Composite-internal gradients monotone: vwap_dist 3-5% +73 / 5-8% +89 / 8-12% +147 /
  >12% +419 (n=159, small); tod later = better within composite.
- KMeans k=5 inside composite: all 4 real clusters profitable +55..+183bps (broad region,
  not knife-edge). Best: parabolic monster-RVOL (rvol~364, vwap +8.7%, ret15m +5%).
- Blowoff trap: vwap_dist>10% on ALL events loses (-61bps) but WINS inside high RVOL
  -> extension is safe only with volume behind it.
- Execution: price>$20 loses at all costs; pocket = close<=20 & cum_dv 5-100M
  (5-20M best +220bps @20). <$5 survives 100bps. >$100M dv diluted.

### Fresh 2026 evidence (download->clean->certify->features, frozen eval)
2026-01/02/03 certified: 386,178 events / 61 days. eval_2026.py = frozen stack
(model_v1 + theta 0.00115 + M3 + composite + S4 + pocket) first OOS look. Results: see
factory/artifacts/ml/eval_2026.log. PAPER_BOT_SPEC.md = v0 live reproduction spec.

### Adversarial lanes — verdicts integrated
- oracle: decay ranking = (1) regime dependence of extreme-RVOL continuation, (2) execution-
  universe mismatch (~20-40bps haircut: 24.5% of composite rows lack a next bar; non-PIT tags),
  (3) layered post-peek selection. vis_rank sensitivity TESTED LOW (plateau <=1..<=4 within
  ~10bps; random 25% list drop: M3 29->24bps). rvol>8 = 17% of tape, capacity OK ($100-300K
  positions at 0.5-1% of day volume). vwap>12% cell = don't-cap-extension signal, not a cell.
  price<=20: trust directionally (stable sign at 4 cost levels). Recommended = pre-registered
  single 2026 pass. ADOPTED (PRE_REG_2026.md, written before any 2026 peek).
- reviewer: 1 MATERIAL — sequencing picks were conditioned on fwd60_t1entry non-null
  (60m-future tradability, unknowable live). FIXED: score-complete pool + decision-time
  tod<=328 cap; PnL on label-complete picks; null share reported (11.5%). S4 composite
  survived the fix (+77.5bps/unit vs +77.4 before). 5 MINORs fixed/noted (dead code,
  pool-definition documented, cut label cosmetic).

### 2026 FROZEN VERDICT (eval_2026.py, gates in PRE_REG_2026.md, written pre-peek)
2026-01/02/03 = 386,178 events / 61 days, model+theta+rules 100% frozen from 2025.
ALL 3 GATES PASS: composite net20 avg +77.3bps (gate >= +30), 3/3 months >= 0 (gate 2/3),
net40 avg +62.4bps (gate >= 0). Monthly @20: Jan +186 / Feb +5 / Mar +41. n=4,177, 70/day.
Score drift real: only 7.7% of 2026 events >= frozen theta (2025 OOS: 44%) -> strict regime
filter; DIAG month-own rel-p90 confirms attenuation is NOT a threshold artifact.
WHAT TRANSFERRED vs NOT:
- EVENT-LEVEL composite stream: 2025 +91 -> 2026 +87bps @20 (+62 @40). ROBUST. This is the product.
- Pocket (close<=20 & cumdv 5-100M, post-peek 2025 selection): first OOS look = +129.5bps
  @20 (n=1,973), monthly +345/-39/-4. Transferred (volatile Feb).
- Base M3 ranking: 2025 +29 -> 2026 +9.5bps; D10 ref -3bps; score edge now only in >t97
  tail (+39bps; t90-97 band -8.5bps). Attenuated, tail-only.
- ENTRY-TIMING refinements: S1 first-crossing 2025 +56 -> 2026 -2.5bps; S4 scale-ins
  +77 -> +18. DO NOT time within episode in v0; enter on stream events with per-name
  concurrency cap (expectancy ~= stream average, no timing skill needed).
Regime telemetry: rvol>8 share and composite flow did NOT collapse -> oracle mechanism #1
did not fire in H1-2026; mechanism #3 (layered selection) largely dead too.
RECOMMENDATION: forward paper bot = composite stream, event-level admission, 1 position
per ticker, 60m hold, 20-40bps budget, vwap extension unbounded. Sizing next.

### Exposure design (2026-09-01, exposure_design.py) — the conversion layer, SOLVED
Question: 17 qualifying minutes/episode -> how many real positions? Answer from evidence
(derivation 2025 Aug-Dec -> frozen replay 2026, PRE_REG_EXPOSURE.md written pre-peek):
- EXIT: fixed 60m hold dominates BOTH years. State-death exit destroys the edge
  (2025 +5.4 vs +54.6bps; 2026 +1.0 vs +34.4) — continuation persists PAST model-state
  lapse; do not exit when the state dies. 30m hold = capital-efficiency alternative
  (1.23 vs 0.91 bps/capital-minute 2025); 15m dead.
- EXPOSURE: persistence harvest = re-enter after each 60m hold at next qualified minute
  (E3/E6), ~1.6 entries/episode. Cycle-2 keeps full edge (+59.2), cycle-3 dead weight
  (+9.2) but harmless. First-entry-only (E1) is BELOW stream average in 2026 (-2.5).
- ENTRY GATE: rvol>8 at the entry minute (2025: +70.7 vs -49.7 for rvol 4-8).
- FROZEN E6 (rvol>8 gate + unlimited 60m re-entry cycles): 2025 +66.4bps/unit wr .548
  5/5 months, +113.9/episode; 2026 +34.4bps/unit wr .498 3/3 months (+78/+3/+27),
  +54.1/episode, 5.4 entries/day. ALL 3 pre-registered gates PASS on 2026.
  Survives 40bps RT at +14.4 (2026) / +46.4 (2025).
- ECONOMICS (cap 10 names x $10k units): 2026 mean +$194/day (p10 -1,262/p90 +1,400),
  57/61 trade-days, ~4 positions avg, zero entries skipped -> not concurrency-bound at
  small size. 2025 mean +$415/day.
- Feature parity: PAPER_BOT_SPEC.md v0 was WRONG on rvol (real def: cum $vol / 20-session
  bucket-interpolated expected cum $vol), market_ret_5m (real: cross-sectional MEDIAN over
  entire clean universe), excess_gain (gain minus that median). Spec rewritten with exact
  build_features.py definitions. Advisor suspicion confirmed and fixed.
Bot spec updated (PAPER_BOT_SPEC.md): composite gate + E6 exposure + fixed 60m exit.
Next: implement v0 paper bot per spec; live rvol baseline table needs 20 sessions warmup.

## 2026-09-02 (session 3) — rvol corruption + corrected verdicts + frozen v2 + fresh-window prep
- **BUG (fixed)**: build_features.rvol_attach cum_sum over unsorted rows → all 11 feature parquets had row-order-partial-sum rvol. Fixed (sort before cumsum). tests/test_feature_parity.py caught it (live engine == intended math).
- **Rebuilt**: features 2025-05..2026-03 (v2), model_v2.pkl (best_iter 56), theta re-derived dev-only = 0.00098 (theta_hi 0.00259).
- **Corrected verdicts**: composite FROZEN Nov+Dec +86.1bps@20 (was +102.6); E6 2025 +45.0bps/unit (was +66.4); **E6 2026 −62.8bps/unit (was +34.4) — 2026 GO REVERSED**; eval_2026 all cuts negative. RVOL_CORRUPTION_REPORT.md.
- **Trust audit**: tests/audit_features_independent.py — hand math from raw chronological bars == research parquet == live engine; 1253 event rows, 0 mismatches (train/OOS/2026 samples). Sortedness audit of all window ops: only rvol_attach was unsorted; labels/rank/m5 order-independent (self-join/median/rank).
- **FROZEN_V2.md committed BEFORE fresh window** (model_v2 + theta 0.00098 + M3/composite/E6_rvol8/X_60m/cap10/$10k; eval protocol pre-registered; no tuning between months).
- **Fresh window blocked on data**: HF dataset (mito0o852/OHLCV-1m + all mirrors) ends 2026-03; updated 2026-05-03. 2026-04..08 exist nowhere local. FIX: backfill from Alpaca SIP historical (entitlement VERIFIED via test pull). factory/scripts/backfill_alpaca.py → HF-schema raw parquets; clean/certify/featurize chain unchanged; validate_backfill.py (bar-level vs HF 2026-03) gates the run. eval_frozen.py written+committed BEFORE fresh data lands.
- Decision rule (pre-registered): paper-deploy iff pooled net/unit > 0 @20bps AND all five months ≥ −20bps/unit; else no-go + mechanism investigation, no re-tune.

## 2026-09-03 — fresh window built (Alpaca SIP backfill) + NO-GO verdict
- **Data fix**: HF dataset ends 2026-03 → backfilled 2026-03..08 from Alpaca SIP
  (backfill_alpaca.py, resumable, ~36-39M rows/month). Validation on 2026-03 vs HF:
  100% listed-ticker bar coverage, 99.5% closes <0.5%, 99.1% vols <1% → PASS.
  Clean/certify/featurize chain unchanged; features 2026-04..08 coverage healthy
  (rvol .94-.97). NOTE: first featurize pass wrote all-null features (clean files in
  data/backfill/, build_features reads data/) — caught by coverage check, re-run fixed.
- **FROZEN one-shot eval (2026-04..08)**: **NO-GO.** Pooled −41.6bps/unit @20 (−61.6
  @40), wr 0.480, 215 entries (2.9/day), −$115/day, all 5 months negative (−16..−153),
  top-5 = 13.2% |gross| (broad loss). Both pre-registered deploy conditions failed.
- **Post-hoc decomposition** (REPORT_FRESH_WINDOW_2026.md): model IC fresh +0.018 (weak
  but alive, ~1/3 of 2025 val +0.059); M3 flat; the 2025-selected composite gates
  (rvol>4 & vwap>0.03) FLIPPED negative (+0.86% 2025 → −0.13% fresh) — the
  extension/volume conditional is what failed to transfer, not primarily the model.
  E6 within-episode timing −42bps vs pool +9bps (noise-dominated, n=215).
- **Status**: no validated edge in this family as of 2026-08. Paper bot = measurement
  instrument (if built). New hypotheses require new data (2026-09+). No re-tuning on
  04-08.

## 2026-09-03 — forensic investigation (advisor brief + "something is off" intuition)
- **Implementation/data/parity: CLEAN.** 11 checks: hand-math audit, sortedness audit,
  bar-level xvendor (100%/99.5%), FULL-STACK March replication (Alpaca −87.7 vs HF
  −74.6bps, same sign/magnitude), eval_frozen replays 2025 at +45.0 EXACTLY, no April
  vendor cliff (Q1 worse than fresh), checks.json/coverage stable, nulls incident
  fixed pre-eval. Reviewer subagent NOT completed (2x provider 429s) — disclosed.
- **What broke**: 60-min continuation lift fired 2/5 OOS months 2025 (Sep/Dec), ~0/8
  in 2026. L/S quintile spread negative 11/13 months. Only Dec-2025 significant
  (t=+2.27, n=41); 2025 pooled t=+1.40 (ns); 13-month arc −10bps t=−0.53.
- **2025 anatomy**: weak signal + mild selection-adjacency + one lucky month (Dec;
  cycle-3 n=20 +222bps, vwap-extreme n=39 +190bps tails). Repeatable core (cycle-1)
  was +30bps t≈1.2. Market-regime proxies do NOT separate winners/losers.
- **Execution exonerated**: t+0/t+1, holds 15/30/60, cycles, timing — nothing flips
  2026; gross negative so costs aren't it. Negative pool, no rule rescues it.
- **Mechanism dead (long)**: no conditioning works in 2026 (12+ variants); multi-day
  continuation NEVER existed (gainer fade both years); short side ≈ breakeven.
- **Candidate**: NONE. rvol 8-12 same-sign both periods but t≈1.0 post-hoc — not a
  recommendation. Next: forward paper MEASUREMENT only (Sep-Dec 2026, zero capital);
  new hypotheses need new pre-reg. NO paper deployment of this stack.

## 2026-09-03 — Cameron/discretionary-momentum research lane (design only, no implementation)
- Researcher brief obtained (DIRECT/INFERRED labeled; no web fetch available — cutoffs need primary-source confirmation, but our tests grid our own numbers anyway).
- Data availability VERIFIED: yfinance float+short% (~0.7s/ticker); Alpaca NewsClient historical news (timing rule: catalyst window ends at 9:30 ET; movers-roundups are coincident, excluded); premarket bars exist in raw backfill files; trade_count NOT retained (design change: keep it in future backfills).
- Key mechanism probes (16-month panel): HINDSIGHT leader minutes +30/+51bps (2025/2026, incl. mega-leaders +25/+67bps) vs tail −41/−43bps — regime correlates with sign in BOTH years. CAUSAL minute-rank-1: negative every month both years (chasing rotates into tops). Gap = early leader identification = the lane (Stage A prediction + Stage B within-leader patterns).
- RESEARCH_CAMERON_LANE.md: translations table, 7 pattern event specs, 8 design changes (D1 regime-conditioning first ... D8 catalyst A/B), hypotheses H-C1..H-C7 with dev/validation split (2025+2026 = dev; forward paper = validation). Score gate retired to covariate; 60-min fixed hold retired.

## 2026-09-04 — Leader program pass 2 (6 lanes + local; research only, no implementation)
- Web (High): rechecks — gap-fade CONFIRMED (FILTRIX n=66,906); RVOL 2x/5x + 100k PM-vol UNCERTAIN as alpha cutoffs (convention/execution only); float tiers directional, rotation-multiple the real variable; $2-20 tradability compromise (incl. $5-10 worst-fade tier); morning-only + EOD-reversal STRONGLY CONFIRMED. Tradeoff: gross edge first-15-min (unchaseable), net window 09:45-11:00, midday/EOD headwinds.
- Patterns publicly ~0 unconditional; leader-vs-tail test exists nowhere — only our panel can run it. ML verdict: profit-weighted learning-to-rank + calibrated meta-gate, 3-stage funnel; binary leader classification rejected.
- Empirical (21-22 sessions, causal): 10-20% true-gap pocket both years; tod gradient 81/37/18/14 (2025), 46/24/18/5 (2026); top-3-by-15:00 leaders +~48% to close (9/10 beat costs); snapshot #1 holds <=41%, top-5 50-64%; separation@15:00 85.7% vs 12.5% (n=22); share non-monotonic; PM-high distance (~0) + scarcity (~0) DEMOTED. 2025 clean files lack premarket; 2026 backfill has it.
- Doc: researches/07-dominant-leader-program.md (framework v1). No production bot. Next: Stage-A tests 1-7 in doc.

## 2026-09-04 — Leader program pass 3 (advisor consolidation; research only)
- REGIME CAVEAT: causal top-3 fwd flips sign across day-samples (Mar sessions +3.4% both yrs vs spread-year negative vs second 20d +5.7%). Opportunity is regime-clustered, not uniform. No threshold ships without month-blocked proof.
- Representations: diff_pp separation wins (rho .54/.47, 85.7% high-tercile); HHI/share dead; $-vol-only dead (-0.39%, 0% recall). Top-4 gain recall 60% eventual / 95% any-big-mover.
- Stateful: flash-tops below VWAP/off-high vs leaders holding structure; drawdown-from-high corr +.76 with forward; archetypes 7 gap / 0 pure-emergent / 9 other + ELAB late-launch outlier.
- Premarket-backfill GO: 2025 ext-hours ~538MB total, month phases, entitlement OK. Awaiting run approval.
- Target verdict: excursion-gated MFE + recall-first funnel. Doc researches/07 (pass 3). No implementation.

## 2026-09-04 — 2025 premarket repair RUNNING + forward observer BUILT
- Backfill: factory/scripts/backfill_premarket_2025.py (premarket-only <09:30 ET, own
  outputs data/backfill/premarket_ohlcv_2025-MM + premarket_parts/, resume per part).
  API proven, Jan pilot ok (~3-5s/batch, ~40min/mo, ~5GB/yr total, 143GB free).
  Running all 12 months in background (logs/premarket_backfill.log). Existing data untouched.
- Observer: factory/scripts/forward_observe.py (ORDER-FREE by construction + assert +
  test). Session loop 13:25-20:10 UTC, 2-min polls: scans.jsonl (top-30 raw),
  promotions.jsonl (union of top4-gain / gain-x-vol / sep-shortlist + SIP bars/ADV/float/news),
  state.jsonl (rank/sep/$-vol per promoted). Scoring offline — nothing frozen.
  tests: factory/scripts/test_forward_observe.py (5 pass). Dry run ok.
  Live loop started 2026-09-03 22:06 UTC, idles to Fri 13:25 UTC (logs/forward_observe.log).
- Judgment (advisor Q): observer-only first, NO live shadow entries — forward 1-min bars
  make any entry/exit replayable offline with identical SIP data; shadow engine = strategy
  creep. Watch recall-first (2-8 names), precision downstream offline.

## 2026-09-04 — backfill bug postmortem + replay harness + regime wave
- BUG: polars dt.hour()/minute() are Int8 -> `h*60+m` overflowed (10:00 -> 88), so the
  premarket filter kept the COMPLEMENT (evening bars). Caught by hour-dist validation.
  Fix: cast to Int32 + assert zero rows >=14:30 UTC per merged month. Lesson: validate
  distributions, not just row counts. Deleted polluted files, rerunning all 12 months.
- Ops lesson: git-bash `ps` misses Windows python; duplicate background runs raced on
  part files. Use tasklist/wmic + check log coherence (no dup batch lines) for singletons.
- Harness: factory/scripts/replay_watchlist.py — snapshot t -> rules -> E0 (buy-hold,
  selection alpha) vs E1 (first-pullback, timing alpha). Pilot 2d: E0 neg/flat, E1
  positive 70-79% hit (tiny n). One code path for historical + (later) forward-backfilled days.
- Regime cover (web lane lost tooling; ran in main): VIX-gate precedents (Concretum,
  AlgoKing VIX<16/16-24/>24, AIBROKER ERM trend/breadth/dispersion/vol), Daniel-Moskowitz
  panic states, internals (TICK/ADD/VOLD), SmallCapLab next-day, Vortex gap map,
  TradeTheMatrix cycles, FOMO-momentum GH (10yr/23k-ticker negative-results program).
- Observer widened 30->50 live snapshots for future-rule replay; full-market depth via
  next-day historical backfill (DO NOT run backfill_alpaca for 2026-09 until month ends —
  partial final would poison resume-skip).

## 2026-09-04 — pass 4: opportunity-state (n=36d) + leader-state (n=150)
- Day base: top-4 fwd +1.4% mean/-0.1% med; max-fwd +39.8% mean (daily continuation exists).
- HOT TAPE replaces scarcity: n10>=4 -> 61% vs 39% (+2.6 vs +0.2); top1-share NEGATIVE
  (<0.5 -> 67% vs 33%); HHI negative; churn negative. Price-separation good, vol-diffusion good.
- Morning window only: 16:00 emergence not still-tradable. Structure persists, behavior re-dices.
- Names: mid-gain@15 0.58 hit (inverted-U); high-accel fades (0.29); rising>stable;
  2+ pullbacks = left-tail (-7.7% mean, hit holds). Health = mid-gain+decel+rising+near-high+VWAP.
- Regime-web lane failed on tooling; NO-WEB priors filed + main-thread web cover (VIX-gate,
  ERM, panic states, internals). Doc 07 pass 4. No implementation.

## 2026-09-04 — Stage-A harness + block regime map (research only)
- Harness: factory/scripts/stagea_eval.py (baselines cur1/top3-20, sep3, opp gates,
  health lite/full, emerging lane; selected-vs-rejected; multi-block). Fast (~25s/day).
- BLOCKS (causal top-N fwd-to-close, first-sessions/mo + mid-Jun check):
  Mar25 +983 | Apr-May -431 | Jun-Jul cur1 -341 | Aug-Sep top3 -194 | Oct-Nov +274 |
  Dec+Mar26 top3 +1086/cur1 +1903 | mid-Jun -1267 (not month-start artifact).
  Summer = cold regime, fall/winter+Mar = hot. Same rules, opposite signs.
- Early: health-lite/full >> none on hot blocks; emerging lane ~noise so far
  (Aug-Sep -403, Oct-Nov -19, Dec-Mar -5); gates untested (hot days pass all).
- Pending: parity-audit + emerging lanes; backfill Dec; Friday forward session live.

## 2026-09-04 — milestone: Stage-A NOT mature (48d fixed harness)
- Pooled: top3 +17 / top5 +46 / sep3 -39 bps, meds neg, hit ~.45; gates/health move
  nothing pooled; selected-vs-rejected coin flip (20-24/48). Raw watchlist != edge.
- Old block map void (clock artifact); new blocks -507..+831, no seasonal form.
- Survives: ET gradient recheck (4d), hindsight effect (recheck queued), heterogeneity,
  emerging-(a) capped, halt-tolerant gating.
- Assets: 2025 PM repaired+validated (325MB); Friday forward captured (1525 rows/28 syms).
- Next: PM retests, forward scoring, month-blocked state/health proof. No Stage B.

## 2026-09-04 — advisories reconciled; per-name logging; canonical state; orphans moved
- Adopted: target-econ (MFE-gated primary + FP-per-opportunity; mean-fwd retired as ranker);
  inference-audit 15-claim table (5 supported directional, 1 disproven, rest pending);
  repo-debt triage (leave-alone + 3 orphan moves only).
- Built: per-name {fwd,mfe,mae,obp} in stagea --out (was the binding constraint).
- Moved: data/stage_*{md5-verified identical} -> data/_superseded/ + receipt;
  zz_leader_health.csv -> factory/artifacts/; deprecation headers rank_day/extract_events.
- Wrote researches/CANONICAL_STATE.md (fresh-context inheritance contract).
- CodeGraph tested live: 98 files/2.9k nodes, blast-radius + dup-map instant. Adopted as
  structural navigator; no reorg. Note: indexes src/, not factory/scripts/.
- /tmp reaped aggressively — write harness outputs to data/_scratch/, not /tmp.
- Observer redeploy (gain_open_anchored) after Friday close ~20:10 UTC.

## 2026-09-04 — MFE-before-DD confirmed; PM retests; mfe lane timed out (partials kept)
- MFE-before-100bps (33d/165 names, salvaged data/_scratch/mfe_curves.json): pooled
  711/158, P(>200)=0.46; ALL 9 month-blocks positive (298-1678); day-hit 31/33;
  FP/cap 0.8-2.2. Terminal fwd flips sign on same blocks. Target adopted.
- PM retests (20d, n=14,379): echo-70% contradicted blanket (0.56; narrow-PM 0.71 ->
  wide 0.36 monotonic); PM-high distance dead (r=-0.010, permanent demote);
  true gaps weak-negative tilt, tiny-n; PM-$-vol leadership contradicted (0/20).
- mfe-curves lane timed out at 30min despite steer; partial JSON kept. Lesson: cap
  lane scopes to <=24 days or split halves explicitly.

## 2026-09-05 — first forward session scored (n=31 promoted, 26 scored)
- Scorer bugs fixed: str-vs-datetime compare (zeroed all outcomes), month-boundary end,
  empty-stats guard, warrant-symbology variants (5 names genuinely bar-less: BNCWZ,
  EONR.WS, EUDAW, GFAIW, OGGWZ — screener phantoms, 16% of promotions).
- Friday tape: mean fwd -1025 / med -1175 / hit 7/26 / mean MFE +1338. Violent warrant
  tape (BIAFW +9743, TMCWW -7889). MFE>>fwd signature live-confirmed; terminal negative.
- Logging apparatus verified: 178 scans x50, 0 gaps>5min, 2647 state rows, promos
  13:25-19:56, none pre-window. Observer redeployed w/ gain_open_anchored (idle to Mon).

## 2026-09-05 — verification program: rank-1-chase DOWNGRADED, PM retests confirmed
- verify_core.py (new, permanent): A=hindsight (BTAI +17.2/EGG +25.4 fresh, holds);
  B=rank-1 chase h60, 11 monthly points MIXED (means -579..+411) — 'negative every
  month' NOT reproduced; claim downgraded to provisional/horizon-specific. Vectorized
  merge_asof verified exactly vs row-loop (n=328/303, identical means).
- C=PM via harness path (6 fresh days): echo narrow>wide monotonic every day
  (0.54-0.84 vs 0.27-0.39); pmcorr ~0 (one +0.14 day); PM-lead 1/6 (combined 1/26).
- Trust map written to CANONICAL_STATE.md. MFE-magnitude provenance gap noted
  (lane-only code — needs harness-path rerun next).

## 2026-09-05 — interaction scale-up started (per-name E1 + MB detail in harness)
- stagea --out now stores per-name {fwd,mfe,mae,obp,mb100,mb200,e1}. outcome_MB uses
  conservative same-bar rule (DD-first). Convention note: entry-bar included, low-based;
  salvaged lane used looser convention (MSTZ mb100 106 vs 0.0 here) — mine is stricter
  and matches market-order economics. Discrepancy logged, not hidden.
- 3 lanes: rerun-A (03-08), rerun-B (09-12+2026-03, capped scopes), reviewer (audit MB/E1
  wiring + independent mb100 repro spec). Outputs to data/_scratch/sae1_*.json.

## 2026-09-05 — interaction verdict: E1 shows no conditionality; P1s fixed+verified
- E1-first-pullback selected vs rejected: no separation either slice (A +18/0.46 vs
  +9/0.44; B -45/0.32 vs -2/0.42). Pattern #1 fails the attention-conditionality test.
- Fixed: E1 next-bar-open fill, dead-bar ref (et>=t), vectorized MB == loop exactly,
  snapshot cdv vectorized, E1 cached. Post-fix E1 lower (close-fill was optimistic).
- Next: HOD-break + VWAP-reclaim probes before any mechanism rethink.

## 2026-09-05 — E2 (HOD-break) + E3 (VWAP-reclaim) built, scaling
- entries_E2/E3 share _run_trade exit engine with E1 conventions (next-bar fill,
  stop-first, 2R, 15-bar stop, 15:30 ET flatten). Wired e2/e3 into stagea names.
- 3 lanes: rerun-C (03-08), rerun-D (09-12+2026-03), reviewer2 (E2/E3 audit).

## 2026-09-05 — health-conditioned interaction: E3 separates, E1 weak, E2 no
- 44d stored files: E1 kept +48/.51 vs dropped -5/.43; E2 kept -20/.35 vs -56/.37 (no);
  E3 kept +24/.43 vs dropped -48/.26 (separates on both metrics, n=60/77).
- Caveat: files predate session-VWAP fix — E3 verdict needs rerun with true VWAP.
- Fixed: E3 session-open VWAP cumsum; replay_day E2/E3 branches + unknown-name guard;
  duplicate return removed.

## 2026-09-05 — E3-fixed rerun: no consistent separation; E1-health weakly consistent
- E3 true-VWAP kept-vs-dropped: E-lane pooled favors DROPPED (+64 vs +41, 2 reversal
  months May/Aug dominate); F-lane pooled favors kept (+34 vs -151, n=27, 1 reversal +
  2 degenerate months). Verdict: NO consistent separation. Old +24/-48 was VWAP-def artifact.
- E1-health across THREE slices same direction: 44d files +48/-5 (.51/.43); E-lane
  +99/-24 (.60/.39, kept wins 4/6mo); F-lane +37/-9 (.54/.38). Modest (+50-120bps,
  +8-20pp hit) but unanimous. Best-supported claim in program; still small-n.
- Reframing: health value may be FP-avoidance (kept names skip bad setups; trigger-rate
  gaps 0.45/0.28, Oct kept 0/3 E3 fires vs dropped 6/7 losers), not trade improvement.
- Harness gap: no --day-offset (F-lane stuck on first-2-days). Add before full-month runs.

## 2026-09-06 — E1-health DISSOLVED at full-month scale (146d, ~290 triggers)
- Full-A (Mar-Jun 83d): kept-vs-dropped +27.6bps mean, +0.036 hit; capture medians
  negative both; verdict: no separation.
- Full-C (Nov/Dec/Mar26 63d): pooled means indistinguishable (-15 vs -11); capture
  medians ~0 both; verdict: no. Full-B (Jul-Oct) timed out; gap noted, non-blocking.
- E1-first-pullback x health-full: earlier 3-slice weak signal was small-n mirage.
  Per pre-registered tree: mechanism-rethink branch for THIS formulation (gain-rank and
  health-state selection x pullback timing). Opening-shape conditioning UNTESTED.
- Standing: MFE-before-DD target (month-blocked); E1 trigger-suppression asymmetry;
  Friday forward session; repaired PM data; verified harness.

## 2026-09-06 — top-3 path→opportunity program ADOPTED as central lane (design frozen)
- 3 advisory lanes reconciled (ml-formulation / data-inventory / red-team). Verdict: GO
  as bounded falsification probe with pre-registered kill criteria; prior for success low.
- Formulation: clustering K=3-6 + Ridge <=10-15 vars; stock-day unit (episodes as features
  only, day-clustered SEs); mb200_capped primary (+mb100 comparability); support gates
  (>=15-20 obs, 3+ months, applicability check); month-blocked validation; 2 unseen months
  required (1 month = pilot only). Sequence models rejected; GBM capped challenger; kNN diagnostic.
- Dev = 2025-05+06+07 (63 contiguous days, mixed regimes, PM-capable); collision = 2025-08
  then 2025-10. Reserve 2025-03/04 + all 2026. Arms A (raw) / B ($2-20+liq) separate models.
- Preconditions (wiring): premarket branch fix (dead code today) + prev-close/split join
  into snapshot + RVOL baselines for dev months. Turnover dropped (no PIT float); halt proxy
  = bar absence; late-starters flagged.
- Kill criteria (binding): DEAD if d_hit<=+5pp or median-capture<=0 or degenerate rates;
  WEAK if +5-15pp both arms CI-incl-0 -> expand 2 months frozen; PROMISING only if
  d_hit>=+15-20pp AND capture>=+100-150bps AND FP<1.0, both arms, n>=60, search disclosed.

## 2026-09-06 — reviewer P0s fixed before any learning (in_B peek, DTW inf)
- in_B now snapshot-time price (was full-day close = 6h peek). ret_last30 (dup of
  ret_open_T) replaced by range_position; z_len explicit (halt proxy, not hidden);
  DTW full-window + length-normalized (unequal lengths finite, order-sensitive);
  PM frame guarded et<570 inside builder. Tests 10 pass incl. new DTW/uniqueness.
- Dev panels (May/Jun/Jul) + Aug holdout must be REBUILT (in_B + 29-feature schema).

## 2026-09-06 — August collision: Ridge dead, clustering weakly alive
- Freeze (May-Jul dev): Ridge LOMO rank-IC ~0 both arms (alpha maxed = shrunk to nothing).
- Collision Aug (63 territ): rankIC -0.13/-0.23, top-1 model loses to gain baseline
  (393 vs 444; 359 vs 402). Supervised linear carries nothing. Dead as specified.
- DTW-medoid cluster ORDER roughly replicates: A dev C0<C2<C1<C3 -> test C2<C0<C1<C3
  (best 602, n=8); B dev C1<C2<C3<C0 -> test C2<C1<C3<C0 (best 606, n=5). Weak,
  underpowered, same direction. Alive-but-weak: October collision decides.
- All pre-registered: K=4, support gates, mb200_capped, both arms, disclosed search.

## 2026-09-06 — October collision PRE-REGISTERED (before seeing October)
- Verdict metric (frozen): Spearman(dev cluster MEANS vs October cluster MEDIANS),
  plus dev-best cluster finishes top-two in October. Computed once, blind.
- PASS: rho >= +0.6 with dev-best top-two. FAIL: below, or ordering scrambles.
- August reference: rho=+0.80 both arms (means AND medians). October decides whether
  shape recurrence is real (2 months) or an small-n alignment.

## 2026-09-06 — October collision: FAIL. Representation retired.
- Pre-registered bar (rho>=0.6 + dev-best top-two): arm A rho=-0.40, dev-best C3
  finishes 4/4 (med 15, worst); arm B rho=+0.20, dev-best 3/4. FAIL both arms.
- Ridge dead again (rankIC +0.04/-0.14). August 0.80 = small-n alignment, retired
  with E1-health into the mirage file.
- Note: Oct arm-B only 39 rows (top-3s often outside $2-20 — penny-heavy month).
- Standing per tree: gain-rank/health/E1-E3/shape-medoid formulations ALL failed.
  Surviving: MFE target, harness, PM data, Friday forward loop, apparatus.
  Next: rethink branch — finer representations, turnover-gating, regime-conditioning,
  or thesis revision. No new learning runs until the rethink lands.

## 2026-09-06 — hypothesis round: 5 thinking lanes -> HYPOTHESES.md (living doc)
- H1 scanner-lag rental, H2 participation/narrow-base, H3 turnover-clock, H4 veto,
  H5 halt-resolution, H6 fade, H7 transitions/legs. Anomaly ledger (7). Kill criteria
  binding. Adversarial standing kept open with flip conditions both sides.
- Test queue ordered zero-new-data-first: lag buckets, frozen participation, veto.

## 2026-09-07 — runner phenomenology + thrust+halt gate (first positive month-blocked result)
- Built runner_phenom.py; scanned 172 days (2025-05..12, 2026-03): 141 runners ≥60%.
  Phenomenology: move takes all day (med 322min open→high, 55% of move after noon),
  halts monotone with gain (84% halt; 11+ halts → mean +177%), retrace doesn't kill.
- FP check: gate = ≥15% by 10:30 + ≥1 halt-gap → rest-of-day from 10:30 next-bar-open:
  n=281, fwd mean +2.8% vs ctrl +0.3%, 7/9 months positive, MFE med +11% after entry.
  First positive month-blocked cohort result in project history. Payoff is tail-shaped
  (win 48%); extraction = stay-rules problem (H2) on a visible gate (H5 fingerprint).
- Artifacts: factory/artifacts/runner_phenom_2025.json, sig_halt_1030.json.
- Misdiagnosis log (mine): month scans died to `timeout 280` before writing; I blamed
  session-reaping for 2 cycles. Fixed: per-day incremental JSONL + resume. Lesson: write
  incrementally, verify the artifact exists before reasoning.

## 2026-09-07b — gate decomposition + halt-reopen mechanics
- Decomposed the +2.8% gate: thrust alone dead (+0.3%), interaction carries it
  (15-30%x1halt +5.3%). >=30%+1halt is NEGATIVE (-6.8%) — exhaustion zone.
- 22,739 live halt events: average reopen ≈ 0 (scalp dead), but velocity≥30% &
  hole≥11min → ret3 +3.9% win 57%, 8/9 months. Halt-ordinal decays; morning only.
- New H9: event-clock participation machine (state coords, not wall clock).
  Artifacts: gate_decomp_2025.json, halt_reopen_all.json (committed).

## 2026-09-07c — temporal decomposition: anchor illusion resolved
- The +3.9% post-halt continuation was pre-halt->reopen GAP (anchor illusion); causal
  next-open entry after reopen is NEGATIVE (D30 -1.5% morning). Reopen buying dead.
- Already-long is where gap money accrues: morning qualifying halt gaps +6.3% med +4.4%.
- Gate cohort accrual schedule: +1.2% by 11:00, dead 11-14, +2.0% in 14-16 wave.
  H10a hold-through-halt / H10b afternoon rental pre-registered. Artifacts committed.

## 2026-09-07d — capture pricing → H11 afternoon rental
- Priced dumb hold (+1.24%), abort-7% (worse: +0.43%), 13:30 rental (+2.39% best).
  Green@13:30 dead on general pop (+0.24%); gate interaction is load-bearing.
- Tail-concentration honest: excl top-5 mean +1.07%; 2nd half (Nov-Mar) +0.67%
  (excl top-3 −0.38%), 2026-03 alone +2.19%. Lottery-book anatomy.
- H11 = stupid-simple afternoon rental of gate names, pre-registered kill on two
  flat collision months. Artifacts: gate_pm_full.json, pm_green_all.json.

## 2026-09-08 — H11 collision test: FAILED, hypothesis retired
- Ran frozen rule on 4 unseen months (2025-03/04, 2026-01/02): pooled net -2.13%
  (gross -1.13% — sign flip pre-friction), 2025-04 -6.27%, 2026-01 -3.51%.
- Pre-registered kill met (two consecutive negative collision months). H11 retired.
- Meta-finding (4th dev-positive to die on collision): month-blocked dev positives
  with n~20-30/month do not replicate; power is the binding constraint.
- Artifact: factory/artifacts/h11_collision_results.json. Halt/runner phenomenology
  stands as description; tradability unproven for every formulation tried so far.

## 2026-09-08 — project reorganization (organization session)
- 6-lane recon (history/data/scripts/srcdiff/contradicts/git) + fresh-eyes review.
- Built brain: researches/INTENT.md (stable objective) + researches/STATE.md (current
  truth snapshot) + entry AGENTS.md (read-order); HYPOTHESES.md archived A-E layers.
- Audit corrections applied: runner freq 0.82/day (was 1.2-1.5, inflated by day-#1
  fallback), halt-gap mean/median disentangled (+6.3 mean/+4.4 med), gate-cohort
  qualifier on money-map, sig(281 rows/118d) vs gd(245/96d) day-coverage resolution,
  281-vs-245 not-a-trend, halt_reopen 1.06M rows, rank-1 chasing conflict recorded
  (minute-claim vs h60-mixed — unresolved), CANONICAL_STATE + 07 frozen with headers.
- Provenance doctrine: producer scripts committed; day-coverage reported per cohort;
  friction annotated per claim. Ledgers backfilled H011-022, EXP-20..34.
- Git: 55 commits ahead of origin (clean FF push, awaiting user); untracked
  certification dirs + ml coverage still uncommitted (next: commit evidence);
  src/ workstream (~3.1k lines, Phase A/B/C paper-bot) uncommitted, reviewed, split
  recommended before commit; data/ gitignored by policy.

## 2026-09-08b — full file mapping round (6 lanes) + repairs
- Verdicts recorded in researches/STATE.md file-map doctrine. June audits 00-06 ->
  researches/history/. Clean 2026-04..08 un-orphaned (moved to backfill/, load_day
  verified 2026-04+2026-08 OK) — P2 pooled re-measure now unblocked. Stale
  data/clean_2026-03 dup deleted. h007/h010 c10/c20 dup parquets pruned; _scratch
  dom studies pruned. Bot workstream committed (Phase A/B/C, SPEC 11.19); ml logs
  gitignored. Working tree clean; 70 commits ahead of origin pending push.

## 2026-09-08 — forward observer running supervised
- Observer live via factory/scripts/observe_supervisor.sh (auto-restart loop, 5s);
  single tree, failure-safe. Window 13:25-20:10 UTC today. Scans -> data/forward/.
- Infra lesson: inspect Windows processes with WMI filters excluding own commands;
  Stop-Process must be tree-kill (taskkill /T), else supervisors resurrect children.

## 2026-09-08c — PRE-REG-P2 frozen (sequential step 1)
- Wrote researches/PRE-REG-P2.md: 13-month dev pool (2025-03..2026-03), sealed
  2026-04..08 holdout (with ML-lineage overlap disclosed), frozen gate cohort,
  fixed M1-M6 metrics, three decision gates. No edits allowed; new file for changes.
- Next sequential: check/score 2026-09-07 forward day.

## 2026-09-08d — forward-day checks + scorer verified
- 2026-09-07 (Mon) NOT captured (observer only launched today) — coverage starts 2026-09-08. P1 ops gap noted.
- score_forward_day.py dry-run on 2026-09-04: reproduces scores.json byte-identically (31 rows). Tonight's pipeline green. Needed: pip install alpaca-py (done) + .env keys present.

## 2026-09-08e — observer outage + recovery (honest ops log)
- 13:25-15:05 UTC: observer alive but every poll crashed (No module named 'loguru').
  Detached supervisor's python != shell python env. ~100 min of today's tape LOST.
- Fixed by pip install loguru (+yfinance for float enrichment) into hermes venv;
  running process self-healed (failed imports aren't cached). First good poll
  15:05:17 UTC (cands=50, watch=5). Supervisor hardened: pinned interpreter path
  + dep preflight check (loud MISSING DEPS instead of silent poll-error loop).
- Running loop still holds old script body (bash parses while-loop upfront); new
  body activates on next supervisor restart. No mid-session restart (would duplicate
  today's promotion rows). SIP snapshot fields null on scans (same as 09-04 — known;
  deep snapshots pull bars separately).

## 2026-09-08f — P2 verification closed (reviewer + verdict script)
- Reviewer audit (static): producer faithful to pre-reg cohort; halt counting,
  RTH-only holes, entry anchors all match. No blocking bugs.
- Closed provenance gap: factory/scripts/moneymap_verdict.py committed — reproduces
  every M1-M6 number from the artifacts exactly. Morning-D30 "-1.54% exact" flag
  resolved: rounding coincidence across different day-coverage samples (dev subset
  in-scan -1.73% n=37; pooled -1.54% n=50). Honest note in HYPOTHESES.
- Known P3-relevant caveats (cosmetic for P2 description): halt spanning 10:30 ->
  p0 measured post-gap; halt spanning 13:30 -> p1330 pre-halt close (upward bias on
  gap days); late-day (et>930) halt events dropped from M1; hourly path drops
  inter-bucket 1-min moves. P3 entry design must handle all four explicitly.

## 2026-09-09a — H12 hot-window pattern-mining: BUILT, RUN, DEAD (pre-registered)
- New lane from user directive: causal top-5 gainers (prev-close gain, scanner
  semantics), sampled 9:45-10:30 ET every 5 min, 22 raw 1-min OHLCV features,
  LightGBM target = first-touch +4% before -2% in 30 min, next-bar-open entry.
- Pre-registered BEFORE any run: researches/PRE-REG-H12.md (gates: dev LOMO AUC
  >=0.55 AND top-decile net >= +30bps; collision AUC >=0.53 AND net >= +30bps AND
  positive in >=2/3 months; 100bps friction).
- Producer: factory/scripts/hot_window_ml.py (imports harness snapshot semantics;
  sample cache data/cache_h12/ per-day parquet, resumable; H12_STAGE=/tmp staging
  because /mnt/c parquet reads are ~10x slower on ext4). Data: 273 days,
  13 months (2025-03..2026-03), n=9074 dev + 2793 collision samples.
- Verdict: DEV GATE FAIL (AUC 0.60 pass, top-decile net -50bps fail — rank info
  without tradable drift), COLLISION FAIL (AUC 0.565 but deciles flat/inverted,
  top decile worst at -285bps, 3/3 months negative, dedup -187bps). H12 RETIRED.
  2026-04..08 still sealed. Artifact: factory/artifacts/hot_window_ml_H12_dev.json.
- Env notes for future long jobs: WSL OOM kills came from et_minute's dt.date
  object-array on 23M-row months (fixed via tz_convert path in hot_window_ml);
  bash-tool detached processes get reaped between calls — run long caches in
  ~14-min chunks with per-day incremental writes (resume by design) instead.

## 2026-09-09b — H12 SCOPE CORRECTION + post-mortem probe (lane open, probe dead)
- User directive: H12-as-tested (one probe) may be dead; the thesis "top gainers,
  hot window, price action -> recurring money-patterns" is NOT refuted by it.
  Discovery mode stays primary; validation hardens only when something looks
  monetizable. Recorded in HYPOTHESES.md H12 SCOPE CORRECTION.
- Probe (hot_window_ml.py probe, artifact hot_window_ml_H12_probe.json):
  (1) T1-matched bracket exit (+400/-200/30min) dead every month, dev AND
  collision (-156..-175 net dec8+); excursion-not-terminal story refuted —
  model's top scores = dn-first states (60-64%). (2) Surviving OOS AUC 0.565 =
  vol-classification (volrank control 0.572); dev up-specific lift gone in 2026.
  (3) Within model's top picks no raw scalar separates up_first (max 0.561).
- Consequence: scalar-summary representation is exhausted on this population.
  Next probe must change REPRESENTATION (raw path) or ECONOMIC QUESTION, not
  re-tune scalars. No new lane opened yet — decision with user.

## 2026-09-09c — bracket bug FIXED, probe numbers corrected (verdict unchanged)
- User caught real ordering bug in bracket_ret: mae-first check charged -200 to
  up-first-then-later-break trades. Fixed to use T1 ordering (T1=1 -> +400 flat).
- Corrected numbers: dev dec9 bracket gross +33 (was -55), dedup -80 (n=851);
  collision dec8 gross +18 vs T2 -97 (partial rescue of terminal fade is real),
  dec9 gross -2, dedup -89 (n=245). Dev dec8+ months net -46..-102, collision
  -87..-96. Short mirror no longer computed (bug made it meaningless).
- Broad verdict unchanged: matched-exit dead at 100bps everywhere, no month
  clears +30 net. But "model picks = dn-first states" was overstated — top picks
  carry REAL up-first-then-fade rescue relative to terminal, gross magnitude
  just 1/3 of friction. Vol-classification finding (#2) and scalar-exhaustion
  finding (#3) unchanged. Artifacts + H12 SCOPE CORRECTION revised in place.

## 2026-09-09d — H12 PATH PROBE (the literal question): raw path adds nothing OOS
- Representation: last 30 bars right-aligned, 4 scale-free channels/bar (bar
  ret, range, vol-ratio, close-position) = 120 features; scalars / path / both,
  same fixed LightGBM, same LOMO dev 2025-03..12 + collision 2026-01..03.
- Result: dev AUC 0.601 / 0.599 / 0.610 — path alone matches scalars, +path adds
  ~1pt in dev. COLLISION: 0.565 / 0.550 / 0.557 — path does NOT add OOS; path-
  only WORSE than scalars. Bracket economics still negative everywhere (dev
  dec9 -56..-67, collision -102..-129). Early-window (t<=600) dev path lift
  (0.584 vs scalars 0.568) did not survive collision.
- Combined with EXP-38 (relative within-snapshot vol-explained OOS): on this
  population at 30-min first-touch horizon, neither scalar summaries NOR raw
  bar-path of the last 30 bars carry OOS direction information beyond
  volatility. The lane's remaining untested directions: different horizons
  (shorter than 30 min / multi-leg), conditioning objects (halt events, catalyst
  context), or finer tape. Sequence-shape per se on 1-min bars: tested, dead.
- Producer: factory/scripts/hot_window_path.py; artifact:
  factory/artifacts/hot_window_ml_H12_path.json (+ relative probe
  hot_window_ml_H12_relative.json).

## 2026-09-09e — leaderboard (TV-equivalent) rebuilt, contract enforced
- User stopped hist runs mid-way: three defects fixed in tv_leaderboard.py.
  (1) prev_close_map ranked mid-month days vs the PRIOR MONTH's last close —
  now searches back by session date for the IMMEDIATELY prior session (April+
  caches were garbage; purged, 52 files). (2) Lag was double-staled
  (shift(1)+et<=t-1 => bar t-2); now causal px = close of last bar et<=t-1
  directly. (3) Grid no longer baked: --ts arbitrary; default 585..630/5 only
  when caller passes nothing.
- Verified with explicit traces (verify cmd): 2025-04-07 t=585/600/615
  (BJDX +138/+122/+159% vs prev_close 3.53 — cross-checked vs raw 2025-04-04
  bars) and 2025-07-08 t=592/603 (NDRA +171/+159%, arbitrary t works).
- Contract written into researches/STATE.md (Leaderboard contract section).
  Live source = scanner API (no key). Leaderboard ONLY — no sampling/targets/
  horizons chosen. Research design deferred to user.

## 2026-09-09f — leaderboard preprocessing FINISHED (grid gone, PIT universe)
- --ts mandatory everywhere (hist/verify error without it; the 585..630/5
  H12 grid is deleted from the codebase, not just defaulted).
- PIT check: universe_tags.parquet = single CURRENT yfinance snapshot, NOT
  point-in-time (no date dimension). Real PIT source per factory/AGENTS.md:
  yolo22/stock-pit-archives. Downloaded the 492MB US-Stock-Symbols bundle,
  extracted 369 daily vintages x (nasdaq|nyse|amex) symbol lists with junk-name
  exclusion (warrant/right/unit/preferred/note) at extract time ->
  data/pit/pit_symbols.parquet (2.1M rows, 2.5MB). Survivorship verified:
  845 delistings + 940 new listings across 2025-03..2026-08.
- eligible_set(day) now = PIT symbols as of that date. First catch: JNVR,
  the TRUE #1 gainer (+249% at 9:45) on 2025-04-07, was invisible to the old
  yfinance tags (NaN exchange / NONE quote type). Old tag filter also
  survivorship-biased in reverse (kept later-delisted names as eligible via
  stale rows). Leaderboard cache purged again; rebuilds use PIT.
- Live fetcher unchanged (scanner API is inherently current-date PIT).

## 2026-09-09g — 18-month 1-minute leaderboard + paths COMPLETE (EXP-42)
- factory/scripts/lb18.py vectorized: per day, per RTH minute t=571..959,
  causal top-3 by gain vs immediately prior session close, PIT universe;
  top-3 rows -> lb_YYYY-MM-DD.parquet; union-of-top3 causal paths (o/h/l/c/v,
  gain_c, n_bars at last completed bar) -> path_YYYY-MM-DD.parquet.
- Built 2025-03..2026-08: 377 days, 756 files, 41MB, 146k minute-samples.
- Correctness: vectorized version verified byte-equal vs earlier per-ticker
  implementation on all of April + JNVR(2025-04-07)/SLBT(2026-06-16) traces.
  Found + fixed a 1-bar lookahead in the first script version affecting only
  2025-03-04..07 (rebuilt). Causality contract: bar t-1 completes at t.
- Storage well under budget (41MB vs 30GB constraint).

## 2026-09-09h — 18-month causal top-3 discovery arc (raw path mining)
- Built 2025-03..2026-08 leaderboards+paths at every RTH minute (377 days,
  756 files, 41MB) via factory/scripts/lb18.py (vectorized per-month wide
  matrices; verified byte-equal against independent tv_leaderboard.py traces).
- Leak rule established: path files include union-of-top3 names; analyses must
  exclude future_hot rows (names whose first top-3 appearance is after t).
- Discovery scripts (all causal, month-blocked): lb18_clusters/events/stability/
  moments/leakcheck/causal/state/dip.py; artifacts lb18_*.json.
- HEADLINE PHENOMENON: flush-recovery in extreme states. In g100+/fresh/thrust
  top-3 minutes: 70.5% see >=10% flush within 2h; of those 56.2% recover to
  breakeven (median 4 min to recovery), 52.3% recover before deeper, 21.3% get
  a new +20% leg off the low; 18/18 months positive recovery rate.
- Emerging formulation (NOT yet pre-registered): bid the flush in extreme
  fresh+thrust leaders, target breakeven recovery — weakness, not strength.
- Next: mechanical pre-registered test of that formulation with costs, plus
  flush-depth/timing conditioning. Memory: researches/HYPOTHESES.md 2026-09-09g.

## 2026-09-09i — flush conditioning (lb18_flush2)
- 1079 flush events: fast (1078/1079 <=3 bars), morning best (rec 0.688),
  rank1 0.587, 300%+ 0.597, -15..-25% depth newx 0.296. Ultra-deep (<-25%)
  rec 0.30. Artifact lb18_flush2.json. Formulation crystallizing:
  resting bid into fast flushes of extreme fresh+thrust leaders, hot-window,
  rank-1 bias, flush depth -10..-25%, target breakeven-recovery + runner tail.

## 2026-09-09j — flush context + fill realism (lb18_flush3)
- 1084 flushes of 1537 state moments (70.5% incidence). Fill realism: 17.4% gap
  through the -10% bid; median overshoot past bid -2.3% (q75 -0.85%); 55.5% of
  flush bars close back above the bid.
- Race (win = recover to c0, stop = -10% from fill): p_win 0.484 (clean 0.480),
  mean win +12.4%, descriptive EV +0.87% gross (~-0.13% at 100bps). Aggregate race
  is a coin flip, net-negative at doctrine friction.
- Conditioning that helps rec_first: morning rank-1 0.592 (6/8 months), thrust15
  20%+ 0.577 (newx 0.283), gain 300-600% 0.553, nbar<20 0.552 (newx 0.315, also
  deepest 0.776). First flush of day NOT bought (0.136, n=44). Depth 15-25% recovers
  worse (0.407) but fatter new leg (0.294). rank2 weakest (0.380).
- Descriptive net-of-100bps on best cells ~+1.9..+2.3% (small n) — needs a frozen
  pre-registered test, not belief.
- Artifact lb18_flush3.json; producer lb18_flush3.py.

## 2026-09-09k — flush measurement repair (reviewer audit) + corrected episode read
- lb18_episodes.py (new producer, lean): touches/volume/speed use real NEW bars
  (n_bars increments) only; native 1-min state minutes (no t%5); flush events
  deduped by (date,ticker,fill-minute) with multiplicity; speed index bug fixed;
  prior_flush counts distinct episodes; per-event race with fill-bar ordering
  bounds (pess/opt), EOD censoring flag, gap-through sensitivity; labels fixed
  (x20 vs pre-flush c0; bars_so_far session count).
- Corrected: 7840 native state minutes -> 5294 filled -> 1893 UNIQUE flush events
  (multiplicity mean 2.8/max 21). Fill realism: gap-through 6.97% (not 17.4%),
  overshoot med -1.35%. Old flush3 numbers were snapshot-inflated + ffill-fake.
- Race per event (censored excluded, n=1663): win pess 0.428/opt 0.521; pay
  pess_net -0.75%/opt +1.18%; abort-on-weak-close variant pess -0.06%/opt +0.88%.
- Survivors: fill-bar close>bid (pess +1.67%/opt +3.28%, x20 19.8%); gain 300-600%
  & depth<=15% (n=267, pess +1.04%/opt +3.22%, 13/18 months pess+, fmax q90 +60%);
  repeat-flush > first-flush. Morning edge shrank (pess negative) — revised down.
- Artifact lb18_episodes.json + lb18_episodes_events.parquet; EXP-46 recorded.

## 2026-09-09l — lifecycle + runner capture (scale-out discovery)
- Built lb18_lifecycle.py: one-order-at-a-time lifecycle (bid -10% under decision
  close, rests 120m, no mid-rest replace; fill on first NEW bar; stay/abort at
  fill-bar close; re-arm at next state minute after resolution). 744 orders,
  541 fills, 203 no-fills. Strong fills (close>=bid) 271; weak 270.
- Exit study (lb18_runners.py, lb18_scaleout.py) across all 1663 unique events:
  plain trail20 mean +0.71%; **scale-out (sell half at pre-flush c0, trail
  remainder 15%, floor -10%) mean +2.56%, med +3.06%, 15/18 months positive**.
  Strong subset (fill-close>=+2%): mean +5.86%, med +4.40%, 77% pos, 17-18/18
  months; c0 hit rate 77%. Weak subset: ~breakeven (was -3.8%).
- Tradeable lifecycle population (n=489): so0.50_t0.15 mean +1.04%, med +2.55%,
  11/18 months positive. Positive but thin — needs freeze + fresh months.
- L=0.15 bid depth variant worse on aggregate; keep L=0.10.
- Artifacts: lb18_lifecycle.json/.parquet, lb18_runners.parquet,
  lb18_scaleout.parquet (+grid), lb18_absorb_all.parquet, lb18_runner_variants.parquet.
- Discovery-grade only. Next: freeze the rule + pre-register on untouched months.

## 2026-09-09p — canonical reconciliation + rolling lifecycle (discovery executed)
- Rebuilt all exits in ONE engine (lb18_canon.py) after finding S/L sim mismatch.
  CORRECTION: sell-all-at-c0 (f100) beat partial scale-out; scale-out headline
  (+2.56%) was an artifact of inconsistent sims. Strong fills +5.29%/18of18 (f100);
  tl30 hold-30min improves to +5.38%/18of18. LOCK variants destroy edge. Cutting
  weak fills loses to holding all.
- Lifecycle re-arm diagnosed (lb18_relax.py): blocking re-arm (strict) misses
  +6.73% seq-only fills; rolling refresh (lb18_roll.py: -10% bid refreshed every
  state minute while flat) is the correct executable version: all +0.39%/11of18,
  pf>=2 +1.20%/15of18 worst -1.17%, n=658/18mo (~35 fills/mo).
- Stay/abort by fill-bar close loses under honest exits; abort@bid assumption
  rejected. Seq upper bound (no pyramid): +2.23%/16of18.
- Artifacts: lb18_canon*, canon_*.parquet, lb18_relax*, lb18_roll*, plus prior
  runnerpath/runnermanage. Scripts: lb18_canon.py, lb18_relax.py, lb18_roll.py.
- Status: discovery-grade on all seen months; freeze + forward test is the next step.

## 2026-09-11 — FLUSH RULE OOS PASS (pre-registered; first true OOS pass)
- PRE-REG-FLUSH-01 frozen/committed (33e2943) before any OOS computation; OOS
  runner parity-verified exact on dev (8c8f3d2) before touching OOS months.
- Built 2024-01..2025-02 (14 months) through the unchanged pipeline; PIT
  vintages extended 2023-11..2025-02 from yolo22/stock-pit-archives mirror
  (data/pit/pit_symbols.parquet now 3.94M rows, 2023-11..2026-08).
- OOS result: pf>=2 n=381 +1.14%/trade net, 12/14 months positive, worst month
  -2.03% -> PRE-REG GATE PASS (dev +1.20%/15of18). all +0.91%/11of14.
- Caveats: paper sim; fills-at-bid assumption; halt/gap tails (worst OOS fill
  -22.9%); capacity/execution unproven. Next: forward observer + execution design.
- Artifacts: lb18_oos_oos.json/.parquet (OOS), lb18_oos_dev.json (parity).

## 2026-09-11b — execution realism + live confirmation wiring
- lb18_exec.py on pooled dev+OOS frozen-rule fills (n=1478): clean-touch +1.27%
  (n=1363) vs gap-through -7.64% (n=115, 7.8%); halt-gap share 0.0% (tail is
  collapse, not missing bars); stop breaches 9.1% (worst -35.9%); capacity
  median $3.45M fill-bar volume (5% participation -> $173k); decay all-fills
  half1 +1.08% -> half2 -0.05%, pf2 +1.31% -> +0.96%; tl30 and stop -10..-15
  on broad plateaus (robustness only; rule unchanged).
- Live confirmation wired: forward_observe.py logs session bars for promoted
  names (log_new_bars); flush_forward_score.py replays the frozen engine
  (lb18_oos.run_engine) on those logs. lb18_oos refactored; dev parity
  re-verified exact. Smoke-tested via Windows venv; activates at next observer
  restart (no mid-session restart performed).
- Artifacts: lb18_exec.json/.parquet.

## 2026-09-11c — live confirmation pipeline + first replay
- forward_backfill_bars.py: Alpaca SIP 1-min session bars for live scan days
  (09-04: 158 names/23.7k bars; 09-08: 87/14.4k; 09-09: 231/34k).
- flush_forward_score.py unit bug fixed (percent vs fraction gain); first
  corrected live replay = 1 qualifying fill (FTFT +10.1%), 0 on the other two
  sessions. n far too small; live candidate net is 50-name alpaca_movers.
- Continuous bars logging starts on next observer/supervisor restart.
- NOTE: Alpaca paper API keys appeared in a tool output during recon — rotate.

## 2026-09-11d — fill-realism microstructure evidence (SIP)
- lb18_fills_micro.py: per frozen-rule fill, SIP trades at/below resting bid +
  NBBO snapshot. OOS n=541: touch 98.9%, through 98.3%, at-bid vol med 52.6k sh,
  dwell med 15.3s (strong fills 4.5s/18.9k). Gap fills 2.6x volume (adverse
  selection real). Fill assumption evidence-backed at small size; queue position
  unmeasured until paper/live.

## 2026-09-11e — flush bot + observer supervisors LIVE (dry-run)
- flush_bot.py committed 45acbde; flush_bot_supervisor.sh launched from WSL via
  cmd.exe /c start "flush bot" /min git-bash -lc "bash .../flush_bot_supervisor.sh"
  (the start title MUST contain a space or WSL interop strips quotes and cmd errors).
- Running since 07:56 UTC; polls every 60s; dry-run default (no orders); scans/bids from
  09:30 ET; enable paper orders by creating data/forward/bot/LIVE
  (supervisor relaunches with --live within 5s).
- Observer supervisor restarted too (07:59 UTC) -> bars logging + scans resume; previous
  observer had been down since 2026-09-08.
- Bot: TV scanner candidates; Alpaca SIP bars; broker-truth reconciliation; journal at
  data/forward/bot/<ET-day>/journal.jsonl; guards POS_MAX=3, $2k qty, 15:30 cutoff,
  15:55 flatten, KILL file.

## 2026-09-11f — flush-bot parity tooling + restart procedure
- flush_bot.py now journals scan_row (rank/symbol/close/change) for the top-5 each poll;
  required to replay the bot's own decisions.
- flush_bot_parity.py: reads data/forward/bot/<day>/journal.jsonl, rebuilds
  observer-schema scans/bars in a temp dir, runs the frozen engine (lb18_oos.run_engine),
  diffs expected vs journal fills (symbol +-5 min). Verified on a fabricated 2026-09-09
  journal built from observer scans: engine reproduced the known live fill exactly
  (FTFT tf=678 = 11:18 ET, +10.11%).
- Restart semantics: the supervisor restarts the bot ONLY on process exit; the bot loops
  while True. To reload code or flip --live: touch data/KILL (bot exits within ~60s),
  rm data/KILL, supervisor relaunches within 5s. Used to load scan_row journaling; bot
  now runs the new code, dry-run, market closed.

## 2026-09-11g — flush-bot pre-open hardening (exit tracking + bid concurrency)
Audit of the live order paths found four defects, all fixed before market open:
1. Exits were never detected: when the OCO sold, meta kept entry_bars -> no exit journalled,
   re-arm gate never armed, and a later re-entry on the same symbol SKIPPED its protective
   OCO (fill branch required entry_bars is None). Fix: each poll, entry_bars set but no
   broker position -> journal 'exit', set last_exit_bars, clear entry/oco/order state.
2. Buy-order concurrency: POS_MAX capped positions but not resting bids (up to 10 bids for 3
   slots). Fix: len(positions)+len(buys) >= POS_MAX.
3. tl30 exit and EOD flatten never cleared/reset state (last_exit_bars, entry_*, oco_id).
   Fix: both paths clear state and set last_exit_bars.
4. OCO id was logged but not stored; micro guard for missing filled_at.
Bot restarted via KILL switch to run the hardened code (dry-run, live:false).

## 2026-09-11h — first live paper session armed
- Pre-open: bot on hardened code; LIVE flag set (start 04:23:51 ET, live:true) -> it will
  paper-trade unattended from 09:30 ET. Revert to dry-run: rm data/forward/bot/LIVE then
  KILL-restart (touch data/KILL, rm, supervisor relaunches within 5s).
- Observer supervisor running; paper account clean (0 positions/orders) at arm time.
- After the session: run flush_bot_parity.py --day <day> and inspect the journal
  (scans, bids, fills, OCO hits, tl30 exits, micro stats).

## 2026-09-11i — paper-fill ledger built (account hygiene catch)
- flush_bot_ledger.py: per-trade P&L from Alpaca paper orders (get_orders;
  get_account_activities does not exist in this alpaca-py) joined to the bot
  journal's intended B/c0; artifact factory/artifacts/flush_bot_ledger.json.
- Hygiene catch: the paper account holds 54 foreign fills / 33 trades from
  2026-06-09, 06-11 and 07-14..17 (RNAC/BMNG/BMNU/BATL; mean -4.07%) — not ours
  (bot armed 04:23 ET today, market closed). Ledger filters fills to symbols
  present in the journal on the same date and reports n_foreign_ignored.
  Pre-session truth for our bot: fills=0, trades=0.

## 2026-09-11j — flush bot hardening v2 (audit fixes) + restart
Independent audit (16 blocker + 8 major). Triage: fixed the order/lifecycle
correctness set; deferred 8 parity/ledger (analysis-side) findings.
Fixes: lifecycle managed for all tracked symbols every poll (independent of
scanner membership); ownership via client_order_id prefix (flushbot-);
fills detected by order-id lookup (filled orders leave the open book) with
partial-fill cancel + protect; OCO protection retried until accepted; anchor
and 120-min expiry refreshed at every strict-state minute, broker order
replaced only on a tick change; tl30 counted from filled_at; 15:30 cancels
resting buys; EOD cancels owned OCOs before closing; ET day-roll resets meta
and cancels owned leftovers; $2 price floor + qty guard; terminal-status
classification; strict state required at the latest completed bar so the live
rank applies to the same minute.
Verified: py_compile + 5 mock-broker lifecycle tests (refresh-on-tick, expiry,
protect, tl30, exit bookkeeping) pass; live bot restarted (start live:true
05:12:29 ET, no error events).
Deliberate fidelity choices (not bugs): tick-only replacement; current-rank
alignment via latest-completed-bar state; cancel owned resting buys on restart.
Deferred: parity/ledger semantics (findings 17-24) to fix before trusting
reconciliation numbers.

## 2026-09-11k — parity/ledger semantics fixed (audit findings 17-24, partial)
Parity: reconstructed scans gated to rank<=3 (live gate); engine fills matched to
journal fills by nearest minute (<=5) instead of greedy first-match; artifact
records dmin plus an explicit "approx" list (one-bar scan timing and
POS_MAX/15:30/anchor guards NOT replicated).
Ledger: attribution now uses ET days on both sides (was UTC prefix -> late-day
fills would have been dropped as foreign); get_orders paginated; friction charged
once per round-trip on the closing leg; bids keyed by (ET day, symbol). Entry
cost remains weighted-average (not FIFO) — acceptable for v0 single-lot trades.
Regression: parity on the fabricated 2026-09-09 journal still reproduces the known
FTFT fill (tf=678, +10.11%); ledger baseline fills=0 / 54 foreign ignored.

## 2026-09-11l — LIVE BUG: SIP intraday sparse -> zero bids; IEX fix
- First live session check (09:44 ET): bot scanning every 60s, zero errors, but NO
  bids and no orders. Cause: broker.bars() returned on the first non-empty feed;
  SIP intraday on this plan returns only ~2 stale bars (13:30-13:31Z at 09:44)
  while IEX returns the full live session (16 bars by 09:45). With <16 bars the
  strict-state evaluator never fires -> zero state minutes -> zero bids.
- Fixed: flush_bot.bars() now tries IEX first (SIP fallback for gaps);
  forward_backfill_bars.fetch_day() uses IEX for the current ET day, SIP for
  history (tonight's parity depends on it).
- Restart 09:50:28 ET (live:true); 09:51 candidates all <+100% so no bids yet
  (TNON's brief +117% at 09:42 was missed during the SIP window; its scanner
  change later read +57.9% — TV-side reference shift, not ours).
- Watch midday for +100%-gain qualifying candidates; parity tonight uses IEX
  bars for today's session.

## 2026-09-11m — observer feed fix staged (IEX-first intraday)
forward_observe.py log_new_bars + deep_snapshot now try IEX before SIP (SIP
intraday returns ~2 stale rows on this plan). The edit is dormant: the running
observer keeps the old code until its next restart, deliberately deferred to
post-close to avoid duplicate promotion rows. The bot's equivalent fix is
already live (5f6dcc5).

## 2026-09-11n — live candidate source: Alpaca movers (TV scanner stale) + PIT/$2 filter
- Found live: the TV scanner returns stale/frozen rows for microcaps intraday
  (SWRD frozen at 3.56/+59.6% for 6+ min while the IEX last trade was 4.92 =>
  +120.6% vs the 2.23 prior close). The bot's change-based gain was therefore
  blind to a qualifying >=+100% name.
- Fix: scan_candidates() uses Alpaca's screener movers (live) as the primary
  source, TV as fallback; rows filtered to the latest PIT universe and the $2
  floor BEFORE ranks are assigned (raw movers are full of warrants/rights/units:
  APURR 0.39, CHPGR 0.15, AENTW 0.43, BRLSW 0.05, crowding the rank gate).
  Restarted live:true 10:10:26 ET; zero errors; 10:11 rows are clean PIT names
  (FTFT +45.8 r1, TNON +45.7 r2, ACVA +44.4 r3).
- No bids yet: no name has held causal gain >= +100% with fresh+thrust; SWRD's
  >=100% window (~09:5x-10:0x) was missed during the TV-blind period.

## 2026-09-11n — day-1 live verification: missed window accounted; IEX coverage caveat
- Verified end-to-end at ~10:15 ET: scan_candidates() sources Alpaca movers (PIT/$2
  filtered); BENF +123.65% rank1 but only 2 IEX bars (fresh listing/halt) -> cannot
  qualify (16-bar minimum, consistent with the r15 requirement).
- SWRD DID trade >=+100% intraday (33 IEX bars; max +103.8% session-relative,
  +120.6% vs its prior close at 09:54) — the first genuine qualifying opportunity
  was MISSED because the two feed bugs overlapped it: SIP-sparsity until 09:50
  (no bars) and stale-TV candidates until ~10:10 (SWRD showed +59.6%).
- IEX intraday coverage gaps observed (COLA/MKDW: 0 bars despite being top movers)
  => the live paper tape differs from the SIP historical tape; today's parity must
  use the IEX-based replay (forward_backfill_bars already patched).
- Journal place_bid count = 0; zero error events. Day-1 remains a no-trade session,
  which is rule-correct given the candidates and the tape available.

## 2026-09-11n — Alpaca /clock outage hardening (live)
- 12:16-13:02 ET: Alpaca Trading API returned 500 on /clock for ~46 consecutive
  polls -> the bot logged 44 error tracebacks and aborted each poll (it was flat,
  so no risk, but a live position would have been unmanaged during the outage).
- Fix: Broker.clock() retries once (1s) then falls back to the local ET session
  clock (weekday + 570<=minute<960); a single API hiccup no longer blinds the
  poll cycle. Restarted live:true via KILL after the edit.
- Also noted: one error event at 15:22 was an offline test invocation missing
  pyarrow (my shell), not the bot (the bot venv has pyarrow 25.0.1).

## 2026-09-11o — DAY-1 LIVE SESSION RECORD (zero fills)
- Bot live:true from 04:23 ET; the first session produced 0 bids/fills (no PIT
  name reached causal +100% with fresh+thrust while the fixed pipeline was live).
- Parity 2026-09-11 (IEX replay): 1369 bars, 14/14 syms, expected 0/actual 0.
  Ledger: fills=0, closed_trades=0. First live day closed flat.
- Live bugs found/fixed on day 1: SIP intraday sparse -> IEX-first (5f6dcc5);
  TV scanner stale -> Alpaca movers + PIT/$2 pre-rank filter (c45b07f); observer
  IEX fix activated post-close (bcd9b02; observer restarted 20:04Z); Alpaca
  /clock 500s -> retry + local-ET fallback (32dd271).
- Windows missed while feeds were broken: TNON +117% (09:42), SWRD ~+120%
  (09:54). Post-fix max mover ~+44%. Day 1 was pipeline validation, not edge
  evidence; accumulation continues next session.

## 2026-09-11p — offline verification: day-roll + clock fallback (pre-Monday)
Two live-critical paths had never been exercised: the ET day-roll (the bot
crosses midnight tonight and over the weekend) and the new /clock fallback.
Added T6 (day-roll: stale meta + owned resting buy + open position -> cancel,
close, meta reset to today, day_roll logged) and T7 (clock: retry once then
local-ET fallback; a transient failure retries without fallback) to the mock
harness. All 7 tests pass; harness preserved at factory/scripts/test_flush_bot.py.

## 2026-09-11p — post-close scan gating (cosmetic noise fix)
poll() scanned before the market-open gate, so movers/bars calls and scan_rows
continued after the close (125 rows after 15:55 on day 1; no orders possible —
entry cutoff 15:30 + EOD return). Scans now run only when the broker clock
reports open (or --probe); closed sessions emit market_closed and stay quiet.
Restarted live:true for the weekend.

## 2026-09-11q — HANDOFF.md written (project root)
Comprehensive prompt-style handoff for the next agent at HANDOFF.md: frozen-rule
spec, evidence base (OOS +1.14%/trade pf>=2, 12/14; execution realism; decay
watch), live systems inventory, environment commands (KILL restart, cmd.exe
quoting, uv patterns), Alpaca/WSL landmines, research discipline, immediate
roadmap (Monday session -> 30+ fills -> tiny sizing), do-not list, and explicit
context-discipline instructions: compress big and often.

## 2026-09-12 — execution-semantics audit + v2.1 fixes (market closed)
Advisor's 8-item audit triaged against code; 5 material fixes applied to
`factory/scripts/flush_bot.py`, all mock-tested (T1-T10, 11/11 pass). Frozen
alpha untouched; only the fidelity of the live translation changed.

Fixed:
- Parity r15: `state_minutes()` now evaluates r15 on a forward-filled 1-minute
  grid, matching the research tape (`lb18.py:97-103`). Previous `shift(15)` on
  raw IEX rows meant 15 *printed* bars, which on sparse IEX spans far longer
  than 15 clock minutes and mislabels thrust.
- Parity tl30: time-stop is now 30 clock minutes from `filled_at` (30 new bars
  on the near-complete tape); a bare IEX bar count over-held.
- Partial fills: `sync_fills()` inspects working orders each poll (previously
  skipped while still in the open book), cancels the remainder, books the
  broker-reported fill, and protects held qty; `manage_symbol()` resizes the
  OCO if the remainder filled before the cancel landed (protect_resize).
- Restart: `startup_reconcile()` cancels owned resting entry buys, re-adopts
  broker positions, and rehydrates entry_B/entry_c0/entry_ts from today's
  journal (+broker filled_at) so protection/tl30 keep the original anchor.
- KILL: now intentional — cancel owned entry buys, leave protective OCO sells
  in place, warn on unprotected positions, exit. No auto-flatten. Idempotent
  on supervisor relaunch.
- pf_est: journal tag on `place_bid`/`fill` only (causal prior-flush count,
  IEX-undercounted). Not an entry gate: paper deliberately trades the broad
  population; fills partition ex-post into pf0/pf1/pf>=2. Frozen result
  preserved; a corrected pf definition stays a separate research variant.

Not changed (assessed, intentional): fill detection = broker truth (no sim);
scanner already Alpaca-movers + PIT/$2; ownership = dedicated paper account
(now stated in the module docstring); micro `fc<0` label is "filled at/below
bid", a different concept from the execution study's "gap-through" (see
HANDOFF §13 terminology note) — a research-labeling clarification, not a bot bug.

Pending: production-overlay measurement (fill cutoff 15:30 / flatten 15:55 /
POS_MAX=3 on historical fills -> retained EV + frequency) delegated as a
measurement (seen data, not an alpha claim); artifact `lb18_overlay.json`.
Next: Monday pre-open health check, then accumulate paper fills tagged pf_est.

## 2026-09-12r — Study A (IEX feed fidelity) + Amendment A1
PRE-REG-MICRO-01 Study A: replayed the frozen rolling-bid engine on an IEX-only
tape built for all candidate symbol-days (667 days cached, 170 zero-bar pairs
logged in `data/iex_tape/_missing.jsonl`). Parity reproduced the frozen numbers
exactly (all 541/+0.91%; pf2 381/+1.14%; dev 937/+0.39%, 658/+1.20%), so the
substitution machinery is sound. The IEX-everything arm FAILED Gate A: OOS all
n=456 −2.54%, pf2 n=256 −1.56% (3/14); dev pf2 −1.25%. Failure signature is
asymmetric: the 174 missed frozen fills (118 pf2) were target winners (median
frozen ret +10.11%) and the 89 spurious IEX fills were stop losers (median
−11.00%). IEX-only fill detection is adversely selected; live fills are
consolidated (real broker order), so this arm is stricter than live. Amendment
A1 (pre-registered before running) decomposes into (IEX state, frozen exec) =
deployable hybrid, (frozen state, IEX exec) = fill-venue control, plus an
IEX-anchor/frozen-B sub-variant, each with frozen gates and a decision rule
(A3 PASS ⇒ state feed not implicated). Files: `factory/scripts/lb18_iex_tape.py`,
`lb18_iex_feed.py`, `factory/artifacts/lb18_iex_feed.json`/.parquet. Verdict
pending the hybrid run.

## 2026-09-12u — Study B (post-fill tail rescue) + independent verification
Study B's gate technically PASSES; my replication of the raw rows sharpens what is
real and what is selection noise.

Artifacts: `factory/scripts/lb18_subminute.py`, `factory/artifacts/lb18_subminute.json`
/`.parquet` (541 fills x 6 configs = 3246 rows; SIP anchors 499/541; 2 truncated
trade windows, 0 errors). Counterfactual exits are friction-consistent
(`exit = action_price/B - 1 - 0.01`, same 1% as the baseline).

Agent's best seen-data rules: all Δ=5s+60s EV +0.91% -> +1.34%; pf2 Δ=30s+60s
+1.14% -> +1.73% vs clean-touch +1.39%. But 25,272 rules were searched on the
same 14 OOS months; winning-rule identity reshuffles completely across adjacent
Δ/lag (unrelated features and directions) and the all-population winner improves
only 5/14 months.

Independent verification (mine, from the per-fill parquet):
- 7-month train / 7-month test re-selection: lag=0 transfers positive in only
  1/12 configs; lag=60 in 11/12.
- leave-one-month-out (select on 13 months, apply to the held-out month):
  lag=0 negative/weak (−0.41..+0.26pp); lag=60 positive in 8/8 configs
  (all +0.09..+0.50pp; pf2 +0.47..+1.00pp). pf2 Δ=15s+60s = +0.997pp, 12/14
  months. Rule identity stays unstable (9-14 distinct rules/fold) ⇒ the value is
  in the feature FAMILY (participation size/volume at B, dwell/below-span, max
  depth below B, snapback at ~60s), not one rule.
- unconditional action on EVERY fill: strongly negative at lag=0 (−1.2..−1.8pp),
  POSITIVE at lag=60 (all +0.44..+0.66pp; pf2 +0.74..+0.82pp, 10/14 months,
  worst month −0.55% vs −2.03%, std 7.2 vs ~10). The simple "exit ~60s after
  first touch" knob captures most of the gate; the 25k-rule search adds little.
- Mechanism: among fills still alive at the action point, later-stops outnumber
  later-targets ~1.8:1, so a flat early exit wins on base rates. Instant action
  is noise; ~60s is where the conditional odds turn.

Verdict: measurement only, NOT adoptable. Blockers: (1) exit execution is
optimistic — it fills at the last SIP print with the same flat 1% friction as
the baseline, while real market-exit slippage on these names is the known killer
(gap-through −7.64%); (2) the 14 OOS months are now seen for this exit question,
so any 60s-exit rule needs a NEW pre-reg + fresh/forward OOS. Next experiment
(pre-reg first): unconditional exit-time sweep with a conservative exit-slippage
model and a small candidate set (flat time-stop; "exit if depth below B < −1%"),
then forward paper validation.

## 2026-09-12v — live-observable data surface (deployability inventory)
Checked what `flush_bot.py` can actually see live (from code, not assumption):
- `Broker.bars()` = `DataFeed.IEX` first, SIP fallback (lines 307-319) → 1-min IEX
  OHLCV, polled every `POLL_S=60`.
- `Broker.trades_at_bid()` = `DataFeed.SIP` (lines 331-337) → returns nothing live
  (SIP intraday blocked on this key); harmless, used for post-fill micro only.
- No quotes/NBBO fetch anywhere in the bot.
Consequence for rescue rules: every Study B sub-minute feature
(touch_trade_count/volume/size_median, below_span_s, touch_episodes,
max_depth_below, snapback at 5-30s) is NOT live-observable; only fill-time and
IEX 1-min bar features (e.g. `iex_above_B`) are.
- R1 (flat time-stop at H) is deployable today with zero new data.
- Any feature-based rescue needs either an IEX websocket trades/quotes consumer
  (code only, no purchase) or real-time SIP (Algo Trader Plus, ~$99/mo).
- `trades_at_bid` still passes `limit=10000` (the old total-cap pattern); moot
  live, but fix it if it is ever pointed at historical fetches.

## 2026-09-12w — PRE-REG-EXIT-01: flat time-stop DEAD; keep the frozen exit
Conservative exit test on the 541 frozen OOS fills: exit priced at the NBBO
**bid** (not the last print) plus 50bps extra slippage. Result: every flat or
conditional time-stop fails — the Study B "gate PASS" was exit-pricing optimism
(last SIP trade + flat 1%).
- At the touch instant the bid is already below B: mean `bid_H/B` 0.967, median
  0.992, p05 0.866. Filling a flush and then selling at the market means hitting
  a bid below entry, worst on exactly the names that keep falling.
- EV (SLIP=50bps): R1 flat H=0 −3.77%, H=60 −3.93%, H=1800 −4.12%; R2 conditional
  (exit iff price<B*0.99) −3.77%..−4.90%. Months+ 0-1/14. Frozen baseline on the
  same fills: +0.914% (all) / +1.136% (pf2).
- Mechanism: the edge lives in the RESTING limit target at c0 (48% of fills exit
  at +10.11% net). A time-stop sells the winners into a falling bid; it sacrifices
  ~112% of aggregate target P&L. Survivors are stop-heavy (~1.8:1) yet exiting
  them costs more than the stops saved once priced at the bid.
- Coverage: 499/541 anchored, 0 quote errors/truncations; 42 anchor-missing; 88
  trade fallbacks (mostly H=1800). A3b secondary not run (cache 361/507).
VERDICT: post-fill time-stop lane **CLOSED** (the Study B deprioritization rule
fires — no rung beats the baseline). Keep the frozen resting exit unchanged. No
SIP needed for this question. Files: `factory/scripts/lb18_exit.py`,
`factory/artifacts/lb18_exit.json`/.parquet, `researches/PRE-REG-EXIT-01.md`.

## 2026-09-12s — Study A decomposition (Amendment A1): IEX state costs frequency, not edge
2×2 over (state/anchor source) × (exec source), frozen `lb` gate, `lb18_iex_hybrid.py`:
parity (frozen,frozen) reproduced 541/+0.91%, 381/+1.14% exactly.

| variant | pf2 n | pf2 EV | months+ | missed_pf2 | note |
|---|---|---|---|---|---|
| frozen | 381 | +1.14% | 12/14 | 0 | reference |
| **A3b IEX state+B, frozen exec** | **282** | **+1.13%** | **10/14** | **77 (20.2%)** | deployable live model |
| A3a IEX state, frozen B, frozen exec | 267 | +1.59% | 9/14 | 64 (16.8%) | anchor/state only |
| A4 frozen state+B, IEX exec | 335 | −3.03% | 1/14 | 89 (23.4%) | fill-venue control |

Verdict: A4 confirms IEX-only *fill detection* drove Study A's FAIL (catastrophic,
and not the live model — live fills are consolidated). For the deployable model
A3b, **per-trade EV is preserved (+1.13% vs +1.14%)** but ~20% of frozen pf2
fills are not captured, and month stability drops to 10/14 (worst −3.09%).
The loss is dominated by the IEX state/anchor schedule (anchor_lost 31 +
lifecycle_lost 36 + fill_lost 10 pf2), not by B: A3a (frozen B) recovers 13 pf2
fills (EV +1.59%) but is *less* month-stable (9/14). So the IEX B/c0 drift is
minor; the sparser IEX tape simply arms fewer anchors. IEX `prior_flush` also
undercounts: variant pf2 n=282 vs 304 frozen-pf2 matched pairs ⇒ the live pf_est
tag undercounts true-pf2 fills by ~7%. Matched-fill paired delta (frozen−IEX
ret) n=367 mean +2.30% p95 +21.1% ⇒ same-fill exit paths differ enough to flip
target→stop in a chunk of cases.

Consequence: judge the live paper bot against the A3b baseline (**pf2 ≈ +1.13%,
10/14, ~19 fills/mo post-overlay, worst month ≈ −3.1%**), NOT the frozen
reference. Real-time SIP (Algo Trader Plus ~$99/mo) is the only way to restore
the frozen schedule; a separate priced decision, not adopted. Study B (tail
management on the frozen population) remains valid and needs no subscription.

## 2026-09-12t — fragility profile of the live flush edge (descriptive, seen data)
A3b pf2 (the deployable live model), OOS 2024-01..2025-02, from lb18_iex_hybrid.parquet:
win 55.0%, mean win +9.24%, mean loss −8.77%, payoff 1.05, breakeven win-rate
48.7% => cushion ~6.3pp. Exit mix: 48% exactly target (+10.1% net), 28% exactly
stop (−11%), 23% tl30/other. Bootstrap 95% CI of mean [+0.01%, +2.21%] (frozen
[+0.18%, +2.09%]) — lower bound touches zero; ~14 months of forward fills needed
to exclude zero at ~19/mo. Worst 5 fills −75.9pp of +318pp total; ex-worst5 mean
+0.97%. H1 +2.18% (n=130) -> H2 +0.23% (n=152): recent half flat. $/mo ≈ $422 at
$2k/trade, $2.1k at $10k, $5.3k at $25k (5%-participation capacity p25 ≈ $84k).
Implication: thin, fat-tailed, decaying edge; Study B (tail) is the highest-
leverage lever; paper fills validate mechanics, not the edge; no more untouched
data remains for the frozen rule (forward is the only true OOS).

## 2026-09-12x — Pattern digest phase 1 (PRE-REG-PATTERN-01 v1)
Unsupervised top-3 digest (user directive: top-3 only, every minute, no label).
Corpus: data/leaderboard minute grid, 2,133,120 60-min windows over 667 days /
7015 symbol-days. Frozen config L=60 k=16: silhouette 0.0058, occupancy entropy
0.886, one 95-window cluster -> the flattened-Euclidean representation does NOT
separate path shapes; clusters degenerate into time-of-day and level slices
(`minute_index` was a clustering channel). Only C12 shows a recognizable
ramp-then-plateau (51% rank-1, median 60-min +38.8%). Month occupancy TV 0.062.
IEX coverage (informs the SIP decision): 172/7015 top-3 pairs have zero IEX bars
all day; median per-pair IEX session-minute share 24.6% (pooled 33.9%) -> an
IEX-only live feed is materially sparse. No outcomes or targets used anywhere.
Amendment A1 freezes v2 (time-of-day excluded, per-window z-scored shape) with a
stopping rule: best silhouette < 0.05 => representation class exhausted.
Artifacts: factory/scripts/pattern_digest.py,
factory/artifacts/pattern_digest.json/.parquet/_cards.png. EXP-67.

## 2026-09-12y — Pattern digest v2 (Amendment A1): EXHAUSTED
Per-window z-scored five-channel shape clustering (time-of-day removed from the
vector). Silhouettes: k=8 0.0206 (best/selected), k=16 -0.0358, k=24 -0.1452.
Best < 0.05 => the frozen stopping rule declares the flattened-Euclidean shape
class EXHAUSTED; no further variants/k were run. The z-scored envelopes render
as interpretable archetypes (C0 spike-and-fade, C1 broad-V, C5
ramp-to-plateau, C7 plateau-decline; C4/C6 near-duplicate late ramps), but the
partition does not separate: silhouette ~0.02 means within-cluster variance
dominates 300-D flattened distance. Interpretable k-means medians are NOT
evidence of recoverable structure — do not read these as patterns unless the
Scoring pass (still unrun, user-gated) says otherwise. Month stability TV
0.0168; 2,133,120 assignments; one degenerate cluster (n=3). No outcomes or
labels used. Artifacts: pattern_digest_v2.json/.parquet/_cards.png (producer
pattern_digest.py --variant v2). EXP-68. Lane status: unsupervised shape
discovery at 1-min/60-min granularity on the top-3 population is a tested
negative; continuation needs a separate pre-reg with a different representation
(learned embedding) or unit of analysis (event-anchored windows).

## 2026-09-12z — live-path audit: pyarrow dependency gap in the supervisor gate
Journal audit of day 1: 46 error events = 45 `/clock` 500s (the known Alpaca
outage 12:16-13:02 ET, handled by retry + local-ET fallback) + 1 real live-path
failure at 15:22:17 ET: `alpaca_movers` raised "pyarrow is required for parquet
support" while reading `data/pit/pit_symbols.parquet` (flush_bot.py:136-137), so
that movers poll produced no candidates. pyarrow (25.0.1) is present in the
hermes venv now and the read works (3,944,234 rows / 703 vintages / 1.72s); the
failure window was environment drift, not code. The supervisor's dependency gate
checked loguru/pandas/dotenv/alpaca but NOT pyarrow, so a missing parquet engine
would let the bot start and silently degrade candidate scanning. Gate now
includes pyarrow (activates on the next supervisor start; the running process is
untouched). Bot otherwise healthy: LIVE armed, no KILL, startup_reconcile ran
(canceled 0 / rehydrated 0), weekend-idle on market_closed.

## 2026-09-13 — PRE-REG-DAYTYPE-01: day-context candidate (OOS pass, dev caveat)
Five pre-declared causal day/session cuts over 1478 frozen fills (OOS 541 +
dev 937), day aggregates built from data/leaderboard (strict-state names,
flush names; producer `factory/scripts/lb18_daytype.py`). Result: the day-breadth
cut `n_strict_names_sofar >= 2` (distinct strict-state top-3 names already seen
strictly before the fill minute) formally PASSES the frozen 5-condition gate on
OOS: mean +0.46% (n=207) vs +1.20% (n=334) for <=1; rank1-only -0.69% vs
+1.33%; rank1&pf2 -1.41% vs +1.57%; below population in 3/3 OOS blocks
(5/5/4 months); dose-response monotone (rank1 OOS: 1 → +1.33%, 2 → -0.49%,
3 → -0.04%, 4+ → -5.79% n=7); session-independent. Caveat: NOT replicated
within rank1 on dev (1:+0.98%, 2:+1.22%, 3:+0.26%) although pooled dev agrees
(+0.93% vs -0.16%). Other four cuts fail the gate (n_flush quiet days n=19;
rank2-3 negative OOS but dev opposite; prior_stop_today ~flat; session
direction agrees but gate fails). Falsified the convenient composition story:
share2+ H1 0.422 vs H2 0.457; corr(monthly share2+, monthly mean) -0.08 dev,
+0.01 OOS — the H2 fade is NOT day-composition. No adoption: H029 = CANDIDATE,
needs forward paper validation. Next: journal `n_strict_est` in flush_bot.py
(IEX-estimated day breadth at bid/fill, behavior-neutral tag) so Monday+ fills
test the candidate in the only uncontaminated arena. EXP-69.

## 2026-09-13b — H029 robustness (Amendment A1): downgraded to OOS-only
Six frozen robustness checks on the day-breadth candidate. R1 leave-one-month-out:
14/14 OOS folds positive (+0.50..+1.22pp). R5: the difference survives removing
the top-3 (date,ticker) contributors (+0.44pp). R3: the A3b deployable population
agrees in direction and larger (quiet +1.29% n=315 vs busy -1.30% n=192).
BUT R2: day-clustered bootstrap 95% CI = [-1.14%, +2.42%] pooled and
[-0.06%, +4.02%] rank1 — both include zero; R6 permutation p=0.194 pooled
(0.026 rank1); R4's alternative breadth definition has almost no variation
(OOS quiet n=13) and is inconclusive. Verdict per the frozen rule: H029 is an
OOS-only association, NOT robust — no gating, no adoption. The live bot's
behavior-neutral `n_strict_est` tag stays for forward paper comparison.
Artifacts: lb18_daytype_robust.py/.json. EXP-70, H029r.

## 2026-09-13c — Pattern digest phase 2 (event-anchored): shape family closed
PRE-REG-PATTERN-02 frozen agent-side under the standing goal directive; producer
factory/scripts/pattern_digest_events.py; 668 day caches under
data/scratch_pattern_events (26MB, gitignored). Results:
- E1 flush touch: 14,116 / 9,988 / 6,586 windows (L 15/30/60) over 4,560
  symbol-days; best silhouette 0.0411 (L=15,k=8) < 0.05 => EXHAUSTED.
- E2 thrust: 1,783 / 1,155 / 750 windows over 714 symbol-days; largest < 2,000
  power floor => INCONCLUSIVE_POWER (not tested).
- E3 volume spike: 3,479 / 2,908 / 2,107 windows over 1,232 symbol-days; best
  0.0433 (L=15,k=8) < 0.05 => EXHAUSTED; occupancy drifts across months (TV 0.123).
Cards (E1, E3) show a few different medians (hump-fade, fade, hump-reversal) but
heavy interquartile overlap; as with v1/v2, interpretable medians are not
recoverable structure. No outcomes or labels anywhere; the Sec.6 scoring pass
remains unrun and user-gated. Lane status: all shape-clustering units
(all-minutes v1/v2, event-anchored E1/E3) are tested negatives; the only
remaining branch is a learned sequence embedding under its own pre-reg.
Artifacts: pattern_digest_events.json/_assignments.parquet/
_cards_E1_flush_touch.png/_cards_E3_volume_spike.png. EXP-71, H030.

## 2026-09-13d — ledger tag partitions for the forward-paper judge
flush_bot_ledger.py now attaches `pf_est` / `n_strict_est` to each round trip
(read from place_bid/fill journal events) and prints `summary_by_tag`
partitions (pf 0-1 vs 2+, n_strict 0-1 vs 2+) plus the A3b baseline
(+1.13% pf2 mean net) for the forward comparison. Smoke test on the flat
account: fills=0 closed_trades=0, artifact written.

## 2026-09-13e — Vacuum-event census (PRE-REG-CENSUS-01): rank gradient + frequency headroom
Measurement only, no trading rule. 24,655 vacuum events over 667 days (36.9/day).
Key numbers: rank1 7.47 events/day, recovery-to-session-max in 30m 41.4%, median
3m; rank2 5.74/29.3%/5m; rank3 4.37/24.0%/5m; off-top-3 19.33/13.0%/11m.
Event-level prior_flush is INVERSE (pf0 32.0%, pf1 23.4%, pf2+ 19.4%) and the
pf2+ pool is dominated by off-rank declining names (off|2+ n=7466, 8.8%) — so
H025's pf>=2 edge is a name-level survivor/state conjunction, not an
event-sequence effect. Halt-adjacent vacuums (pre-gap>=5m) recover more often
(36.2%) with fatter tails (p05 -23.8%) but only 2.48/day. AM strongly better
than PM (29.9% vs 13.7%). Frequency headroom: H025 executes ~1.9 fills/day vs
7.5 rank1 vacuum events/day — the entry qualification, not the phenomenon, is
the constraint on dollars. Frozen gate licenses (candidate only): rank1/2/3,
pf0/1, seq1/2, AM, rank1|pf2+ (5.52/day, 38.0%), rank2|pf2+, off|0, pf0|seq1,
pf1|seq2. No rule adopted; H025 and the live bot untouched.
Artifacts: factory/scripts/event_census.py, factory/artifacts/event_census.json/.parquet
(cache data/scratch_census/). EXP-72, H031.

## 2026-09-13f — PRE-REG-LEADER-01: the frequency axis is tested and negative
Four frozen variants of the flush mechanics, parity exact (541/+0.91%, 381/+1.14%).
Baseline OOS = 1.86 fills/day. V1 rank1: n=445 +0.72%, pf2 n=311 +0.69% (8/14) —
rank1 REDUCES the pf2 edge, and 1.53/day fails the frequency gate. V2 AM(<12:00):
n=313 +1.25% (11/14), worst month −0.98% vs baseline −2.51%, pf2 n=205 +1.75%
(10/14) — the best per-trade quality and tail of anything tested, but only
1.08/day (FAIL freq) and dev disagrees (+0.21%, 7/18). V3 rank1+AM: n=266 +0.96%
(8/14) FAIL. V4 continuous bid at 0.9×running session max: n=9635, 33.1/day,
−15.48%/trade, win 7%, 0/14 months → RETIRED.
Lesson: the census's raw vacuum supply is unqualified and toxic when harvested
(V4 = selling into crashes); the event-level rank/recovery gradient does NOT map
to rule-level P&L (rank1 lowers pf2); tightening improves quality but halves
frequency. H025's ~1.9 qualified fills/day is near the frontier of this mechanism
on this data. Artifacts: lb18_leader.py/.json/.parquet. EXP-73, H032/H033 RETIRED.

## 2026-09-13g — Outside-review corrections + bonehead fixes + backbone pre-reg
Corrections owned after review (verified against artifacts): (1) the "1.9/day
frontier" mixed populations — forward-relevant rates are baseline all 1.859/day,
frozen pf2 1.309/day, deployable A3b pf2 0.969/day; use ~1/day. (2) V2-AM's dev
verdict: the ALL-fill dev population failed (+0.21%, 7/18) but dev pf2 is
solidly positive (n=332, +1.35%, 11/18) — the dev gate was written for adoption,
not for characterizing quality. (3) V1–V3 are restrictions of H025's anchors and
cannot add fills by construction: they tested selectivity, not frequency
existence; only V4 tested frequency expansion and is the strong negative.
Free, behavior-neutral action taken: `flush_bot_ledger.py` now partitions
forward trades by session (AM/PM) alongside pf_est / n_strict_est; compile + flat
smoke test pass. Backbone prerequisites verified: local PIT covers vintages only
2023-11-01..2026-08-20 (2021–2023 needs yolo22 staging); the leaderboard
pipeline has no split filter in evidence (clean_month's split exclusion is an
optional flag; certify_month.py has the overnight-ratio split_suspect check), so
split certification is required before trusting older-year +100% states — and
worth auditing on 2024–2026. Draft `researches/PRE-REG-BACKBONE-01.md` freezes
the blinded protocol: debug on 2024 (burned), freeze, then reveal 2021–2023 once
as a regime-replication test (REPLICATES/FAILS/MIXED rule; no adoption path).
Needs user sign-off on the multi-GB staging. H034 (latent survivor-state) opened
as research workstream 2, separate from the protected H025.

## 2026-09-13h — Split-artifact audit: the tape (and Alpaca live) contain split-fake leaders
Independent audit of the raw leaderboard tape: 110/7015 pairs (1.6%) are
reverse-split-like (huge overnight ratio, flat intraday), 42.7% at exact split
factors — SIRI 1:10 (2024-09-10), LCID 1:10 (2025-09-02), GDEV/JDZG/REAX 10x.
These enter the tape as fake +900% "top gainers". 37/541 frozen OOS fills
(6.8%) sit on flagged days and are ABOVE average (+2.26%, pf2 +2.45%), so they
FLATTER the frozen result: split-clean baselines are all n=504 +0.815%, pf2
n=354 +1.035%, 11/14 months, worst month −3.82% (vs −2.03% with flags). A3b:
flagged +1.21% vs +0.24%; clean pf2 +1.00%. Alpaca data certified RAW
(adjustment=None -> raw default; SIRI 2.67→27.38, LCID 1.985→17.655, IEX+SIP),
so the live bot sees the same fake split-gainers — a population-definition issue,
not a live/backtest mismatch. Flag list: data/split_flags.parquet (gitignored,
reproducible from the artifact). The certification pipeline's split_suspect
counts (51–91/month) cluster on month starts (boundary artifacts); this
exact-factor audit is the operative check and is a prerequisite for 2021–2023.
Artifacts: audit_splits.py/.json/.parquet. EXP-74.

## 2026-09-13i — BACKBONE REPLICATION: H025 pf>=2 travels across 2021-2023
PRE-REG-BACKBONE-01 run (frozen engine, PIT-built tape, split-certified,
reference parity exact 541/+0.91%, 381/+1.14%). VERDICT: REPLICATES.
pf2: 2021 +1.08%/trade n=243 (8/11 months, worst -0.23%); 2022 +1.08% n=128
(8/12, worst -3.94%); 2023 +1.95% n=200 (11/12, worst -2.09%). Pooled
n=571, mean +1.39%, median +4.65%, months+ 27/35, worst -3.94% — comparable to
or better than the original OOS (+1.14%/12-14) and dev (+1.20%/15-18).
Split-clean pf2 is similar or stronger (2021 +1.44%, 2022 +1.15%, 2023 +1.97%).
BUT the broad all-fills rule does not travel: +0.14% (5/11), +0.34% (7/12),
−0.56% (5/12, worst month −8.19%); rank1 is flat in every year. Census per
year: monotone rank gradient (1>2>3>>off) in 2021/2022/2023; AM>PM every year;
event-level prior_flush inverse every year; halt-adjacent recovery higher
(42.5%/23.9%/26.5%). Read: the pf>=2 SURVIVOR CONDITIONING is the load-bearing
element of H025 — it is what survives regime change, while the broad form and
the rank-1 form do not. Replication is backward-in-time, not a fresh OOS; no
adoption change; H025 stays frozen and the live bot untouched.
Artifacts: factory/scripts/lb18_backbone.py, factory/artifacts/lb18_backbone.json,
lb18_backbone_fills.parquet; audit_splits rerun now covers 2021-2023 (167 flags).
EXP-75, H035.

## 2026-09-13j — PRE-REG-DRIFT-01 (H034 probe 1): post-recovery drift NEGATIVE
Hold-based drift after a recovered vacuum, measured on 28,164 census events
(2021-02..2026-08, 1,401 days). Absolute net means (the valid read):
all/30m +0.22%, all/60m −0.51%; rank1/30m +0.23% (median −3.74%, 39.9% win);
rank2 −1.49%; rank3 −2.09%; rank1&seq2+ +0.72% (12 of 14 OOS-scale half-months
positive only 32/67). Only rank=off has a positive mean (+2.22% at 30m, +2.43%
at 60m, 79% months+, both eras) but its median is −0.94%, win 46.3%, 30% of
events >+5% against 32% <−5%, worst −43% → a tail-driven lottery, not an edge.
Descriptive monotonicity: post-recovery drift is negative and ordered by rank
(median 30m: rank1 −3.7%, rank2 −4.3%, rank3 −4.9%, off −0.9%), i.e. the more
attention, the harder the fade after recovery — which independently justifies
H025's c0 target exit and matches EXIT-01 (extended holds are worse).
Method note: the pre-registered paired control is invalid (sample conditioned on
recovered_30m, so the event-minute control benefits from the known recovery:
+6.7% at 30m); the gate's diff term is therefore not meaningful and the absolute
means carry the verdict. Artifacts: drift_survivor.py, lb18_drift.json/.parquet.
EXP-76, H034a (RETIRED). H025 and the live bot untouched.

## 2026-09-13k — PRE-REG-PERSIST-01 (H034 probe 2): cross-day survivor selection NEGATIVE
1,020 consecutive-day (date,ticker) pairs from the census tape; 1,392 frozen
fills joined (851 in 2021–2023 + 541 in 2024-01..2025-02).
Persistence: survivor-day (>=2 vacuums) base rate 85.7%; P(surv | prev surv)
87.5% vs P(surv | prev non-surv) 73.9% → lift +13.6pp, both eras. So the
survivor state *is* partially predictable across days.
But it does not pay: fills on prev-survivor names n=105 mean −1.43% (pf2 n=72
+0.66%, median −0.07%, 16/30 months) versus fills on names ABSENT from
yesterday's tape n=1272 +0.47% (pf2 n=869 +1.37%, median +5.09%, 58% win,
40/49 months, both eras positive). 91% of frozen fills are already fresh names.
Frozen gate = NEGATIVE (pf2 prev-survivor n=72 < 200) and the direction is
inverted versus the hypothesis: cross-day recurrence underperforms fresh
attention, while in-session repeated vacuums (the pf>=2 condition already
inside H025) remain what pays. Everything is measurement; H025 untouched.
Artifacts: survivor_persist.py, lb18_persist.json/_fills.parquet. EXP-77,
H034b (RETIRED).

## 2026-09-13l — Monday judging hazard recorded (raw vs split-clean baseline)
Live Alpaca bars are raw/unadjusted (verified empirically on SIRI/LCID split
days), so the live bot's movers and the frozen/backtest population both include
split-fake gainers. Therefore the live paper fills must be judged against the
**raw** A3b pf2 baseline (+1.13%, 10/14) — NOT the split-clean variant (+1.00%) —
and ~6.8% of historical frozen fills sat on split-flagged days (audit_splits:
37/541), where A3b reads +1.21% flagged vs +0.24% clean. `flush_bot_ledger.py`
baseline block now records a3b_pf2_mean_net_split_clean=0.010 and
expected_split_flagged_share=0.068 with that instruction. No strategy change;
documentation only, so Monday's read is not mis-specified.

## 2026-09-13m — PRE-REG-SIZE-01: the edge is a small-order edge (sizing input)
Measured on the existing SIP per-fill artifact (541 frozen OOS fills; flush_vol_at
= shares traded at/below B in the fill minute). Findings: pf2 retained mean stays
+1.01..+1.10% with months+ 12/14 for sizes up to $50k per fill (retention 92-99%);
it degrades to +0.81%/11-14 at $100k and +0.45%/9-14 at $200k, as larger orders
can no longer fill the low-volume (better) micro-vacuum fills and retain the
high-volume (worse) crash fills. Volume-at-bid quartiles: Q1 +2.68% / Q2 +1.10% /
Q3 +2.13% / Q4 -1.39%, breach 21.9% vs 45.3%; Spearman(vol,ret) pf2 -0.141. The
gradient is concentrated in cheap names: B in [4,10) rho -0.200, Q4-Q1 -5.5pp;
B>=$10 rho +0.031, +1.0pp. The frozen gate fails at every N on the cliff term —
no "size-safe" badge; practical read: current $2k is deep in the flat zone, and
sizing to ~$25-50k costs ~0.1pp. Exit proxy (next-minute volume >= size) 89-92%
throughout. No adoption, no bot change; sizing input for the real-money decision.
H037 opened as an untested lead (pre-anchor activity state as toxicity
conditioner). Artifacts: researches/PRE-REG-SIZE-01.md, factory/scripts/size_curve.py,
factory/artifacts/lb18_size.json/.parquet. EXP-78, H036.

## 2026-09-13n — PRE-REG-TOXICITY-01 + A1: first surviving conditioner (range5)
Can causal pre-anchor state mark toxic flush fills? 1,392 fills (541 OOS +
851 backbone), features from grid bars <= t0. vol5_ratio (trailing 5-min volume
vs session median) passed the initial gate but DIED on permutation (p=0.3965)
-> RETIRED. range5 (trailing 5-min high-low range / close at t0) SURVIVED every
frozen check: extreme-quartile diff +3.76pp (Q4 mean +2.36%/median +10.11%
target/36-of-46 months vs Q2 -1.40%), day-clustered bootstrap 95% CI
[+2.15,+5.38]pp, permutation p=0.0000, both eras (+4.50pp 2024-25, +3.16pp
2021-23), both price bands, >=2 of 3 rank strata. Direction: HIGH pre-anchor
activity = better fills (attention/battle state), LOW = dead-tape fills.
Live-computable at arm time from the bot's existing IEX bars. Candidate only;
TOXICITY-02 (pf2 combination + practical cut + frequency tradeoff) and a
forward test still required. No adoption, H025/bot untouched. Artifacts:
researches/PRE-REG-TOXICITY-01.md (+A1), factory/scripts/toxicity_state.py,
factory/artifacts/lb18_toxicity.json/.parquet/_robust.json. EXP-79, H037a.

## 2026-09-13o — PRE-REG-TOXICITY-02: range5 filter passes on pf2 (+24% dollars/day, shallower tail)
The surviving conditioner (range5 = trailing 5-min h-l range / close at the
anchor; threshold = median of all 1,392 fills = 0.1321) applied to the
deployable pf2 population. Pooled pf2 n=952: base +1.29% (39/49 months) vs
high-range n=544 +2.21% (40/48) vs low n=408 +0.06% (24/49); retention 57.1%;
day-clustered bootstrap of high-low [+0.94,+3.25]pp; all four frozen checks
pass. OOS 2024-25: base 381/+1.14%/12-14/worst -2.03% -> high
243/+2.20%/13-14/worst -0.72%; low 138/-0.73%/6-14/-5.32%. 2021-23: base
571/+1.39%/27-35 -> high 301/+2.21%/27-34 (worst month -11%: the bear-era tail
is unchanged); low 270/+0.46%. Dollars/day OOS: base 1.31 x 1.14 = 1.49pp vs
high 0.84 x 2.20 = 1.85pp/day (+24%) with a much shallower worst month.
CANDIDATE FILTER ONLY: no adoption, no bot change. Forward validation path:
reconstruct range5 for live fills from Alpaca IEX historical bars at t0
(no bot edit needed) and partition vs the A3b high/low expectation; adoption
would need its own pre-reg. Caveats: fixed 0.1321 threshold is research-tape
specific (live IEX bars are sparser); AM/time-of-day confound untested.
Artifacts: researches/PRE-REG-TOXICITY-02.md, factory/scripts/toxicity_state.py,
factory/artifacts/lb18_toxicity_pf2.json. EXP-80, H037b.

## 2026-09-13p — PRE-REG-TOXICITY-03: range5 is a deployable live proxy
Checked whether the range5 conditioner survives the live feed (IEX-only, sparse)
and whether it is just a time-of-day proxy. Population = OOS 541 fills.
- Coverage 97.8% (the IEX tape has the anchor minute), hi/lo agreement at the
  frozen threshold 0.13214 = 84.3%.
- pf2: full-tape gap +2.99pp (hi +2.24% n=241 / lo -0.75% n=135); IEX-computed
  gap +2.36pp (hi +2.35% n=187 / lo -0.01% n=189) → 79% of the gap retained
  (gate required ≥50%). The IEX-median (0.111) split also separates: +1.79% vs
  +0.26%.
- Not a session artifact: AM gap +2.85pp (n 415/322) and PM gap +1.88pp
  (n 281/374); Spearman(range5, t0) = −0.087.
- IEX range5 is compressed vs the full tape (median 0.111 vs 0.132) because IEX
  bars are sparse → the live rule must use a live quantile, not the frozen
  constant. Verdict DEPLOYABLE-PROXY. No adoption; H025 and the bot untouched.
Artifacts: researches/PRE-REG-TOXICITY-03.md, factory/scripts/range5_live_check.py,
factory/artifacts/lb18_range5_live.json/.parquet. EXP-81, H037c.

## 2026-09-13q — PRE-REG-ROBUST-RANGE5-01: range5 is FRAGILE (2022 inversion)
Five frozen stability tests on 1,392 fills (pf2 952). Verdict FRAGILE, failing
only R1 (time stability): per-year pf2 gaps at the pooled threshold 0.13214 are
2021 +1.91pp, **2022 -0.87pp (inverted, n=60/68)**, 2023 +3.17pp, 2024 +3.03pp,
2025 +2.36pp but n_lo=26 < the frozen 30 floor so 2025 cannot count; counted 4,
positive 3 -> fail. Everything else passes: window sensitivity (win 3/10/20:
+1.97/+2.24/+1.95pp), month-blocked LOMO (22/32 months positive = 68.8%; pooled
LOMO gap +2.18pp, day-clustered bootstrap [+0.93,+3.29]pp), half-year sign (8/9),
threshold profile (monotone +1.12/+2.15/+2.78/+3.03pp at q .33-.75, no flips).
Interpretation: the conditioner is not a knife-edge artifact, but it is
regime-conditional - it worked in 2021, 2023, 2024, 2025 and inverted in the
2022 bear. Adoption implication: no; a fair-weather filter with unknown
next-regime probability is not a safe H025 change. Forward paper remains the
only test; H037d FRAGILE. Artifacts: range5_robust.py, lb18_range5_robust.json/.parquet.
EXP-82.

## 2026-09-15a — Monday session: crash-loop bug (fixed) + hardening
The Monday session placed zero orders because of a bug I introduced with the
n_strict_est tag (commit d551a47): `note_strict` stores `meta["_strict_seen"]`
as a set, and `sync_fills` (flush_bot.py:638) assumed every meta value except
"_day" is a per-symbol dict -> AttributeError every poll. It fired the moment a
symbol first reached strict state (13:17:14 ET, FTFT) and ran to the close: 246
crashes, bid placement impossible during exactly the window when FTFT was a
qualifying leader (+102% at 13:21 -> +205% at 15:17). Account never at risk (0
orders/positions, ledger fills=0). Latent since 2026-09-12 because no prior
session produced a strict signal.
Fixes: (1) structural guard (isinstance dict) in sync_fills and in the tracked
set - replaces the name-based "_day" special case; (2) T12 regression test;
(3) T13 poll-level integration test driving the whole path (strict signal ->
note_strict -> sync_fills -> bid placement); (4) crash alarm - 3 consecutive
poll errors write `data/forward/bot/ALERT` + an `alert` journal event, cleared
on recovery. 14/14 mock tests pass. Bot restarted with the fix at 17:40:45 ET
(`start live:true` + `startup_reconcile`), zero errors since. Commits: 5ec316c,
this one.

## 2026-09-15b — Dry-run replay tool + "what did the crash cost?" answer
Committed `factory/scripts/replay_decisions.py`: replays a day's journal scan
history + Alpaca IEX bars through the bot's own strict gate and the frozen
bid/fill/exit semantics, modelling the live entry cutoff (resting bids cancelled
at 15:30, flush_bot.py:510). Verdict for 2026-09-14: FTFT was the only strict
leader (18 strict minutes 13:17-15:17, prev_close 2.88, rank<=3); the rolling bid
was never touched before the cutoff and the only touch came at 15:31 - after the
cancel - so **the day was a zero-trade day with or without the bug**. All other
scanned names never reached +100% (BMGL max ~+72%, ELMT ~+51%). Side effect: the
replay doubles as a pre-open rehearsal and a post-close "did we miss anything"
check.

## 2026-09-15c — Pre-open certainty battery (for the Tue 2026-09-15 session)
Three-layer verification that the Monday crash cannot recur:
1. meta-consumer audit: every access to `meta` in flush_bot.py was enumerated;
   only sync_fills and the tracked comprehension dereference values (both now
   isinstance-guarded); the day-roll loop uses membership tests only (safe by
   construction). No remaining shape assumption anywhere.
2. Poll rehearsal (`factory/scripts/rehearse_poll.py`, new): ran the REAL poll()
   in dry-run on the REAL 2026-09-14 tape at the first strict minute - 13:17
   FTFT, prev 2.88 - reproducing the exact fatal conditions (note_strict creates
   _strict_seen). Result: zero errors, _strict_seen={'FTFT'}, and place_bid
   logged B 5.19 / c0 5.77 / qty 385 / rank 1 / pf_est 8. The path that killed
   Monday now completes and places the intended bid.
3. Alarm tripwire: 4 injected poll errors produced alert events at 3 and 4 plus
   the ALERT file (cleared on the next successful poll). The "silent crash
   loop" failure mode now leaves evidence.
Suite 15/15 (T12 meta guard, T13 strict-state poll, T14 day-roll with set).
Running process started 17:40:41 ET 2026-09-14, after the last flush_bot.py
write; zero journal errors since. Next session: Tue 2026-09-15 09:30 ET.

## 2026-09-15d — Liveness heartbeat (silent death now leaves evidence)
Post-close polls returned silently by design (the FLAT_ET return precedes the
market_closed log), so the journal gave no proof the loop was running. Added a
heartbeat: every 5th poll logs `alive {polls}` (~5 min). Verified live after the
restart at 18:08:16 ET: `alive polls=5` at 18:12:19 ET, 0 errors, no ALERT.
Liveness is now observable from the journal alone, and with the ALERT tripwire
both silent failure modes (crash loop, silent hang) leave visible evidence.
Interpreter liveness also confirmed independently: PID 7708 (venv python running
flush_bot) CPU delta 0.02s over 70s = one cheap post-close poll cycle.

## 2026-09-15e — Overnight death + stack restart + watchdog
All processes died overnight (Windows session went down; no supervisor exit
lines; last bot heartbeat 2026-09-14 20:28:20 ET). Power settings were already
never-sleep on AC, so the cause was external (reboot/lid/power/user action).
Stack restarted: bot supervisor 09:05:27Z, observer supervisor 09:13:53Z (after
fixing the launch chain: ASCII wrapper C:\Users\Public\observe_sup.sh + `start
""` empty-title form). Live path verified pre-open (scan src=alpaca, FTFT +179%
rank1, PIT 5633 symbols) and heartbeats flow (`alive polls=10` at 05:14 ET).
New `factory/scripts/watchdog_stack.sh` + Windows task `algo-stack-watchdog`
(every 10 min, verified by force-run at 12:19:55 IDT): during 09:10-16:15 ET it
checks the bot journal (<12 min) and the observer log (<20 min); relaunches the
supervisor when the process is absent, kills+relaunches when the process exists
but the journal is stale; duplicate guard via CIM process count; `--check` mode
prints decisions only. Log: logs/watchdog.log. Not registered: a logon trigger
(schtasks /sc onlogon requires elevation) — one elevated command if wanted.

## 2026-09-15f — One-command daily close-out (waiting-period work)
Added `factory/scripts/daily_close.sh`: runs the three post-close steps I have
been doing by hand - `flush_bot_ledger.py` (fills + pf_est / n_strict_est / AM-PM
partitions), `replay_decisions.py` (what the bot would have done, live
15:30-cancel semantics), and a journal event summary - then appends a marker to
`logs/daily_close.log`. Verified end to end on 2026-09-14 (rc=0; ledger fills=0;
replay: FTFT armed 18x 13:17-15:17, 0 fills; journal key counters incl. error 258
from the crash loop, alert 0, alive 28). Removes the manual step from the daily
loop so the close-out cannot be missed.

## 2026-09-15f — FIRST LIVE ROUND TRIP (milestone)
VEEA: place_bid 09:53:13 ET (rank1, B 4.72, c0 5.25, qty 423, pf_est 0, n_strict_est 1)
-> 5 refresh cycles on rising ticks (B 4.72 -> 4.87 -> 5.61 -> 5.57 -> 5.60 -> 5.75,
qty adapting 423 -> 347 to hold ~$2k notional; each refresh cancels + replaces)
-> fill 10:41:39 ET (347 @ 5.703228, BELOW the 5.75 limit = price improvement;
pf_est 2 at the fill, n_strict_est 1) -> OCO in the same poll (stop 5.17 /
target 6.39, cid flushbot-s-VEEA-...) -> tl30 11:11:33 cancel_oco / 11:12:35
tl30_exit / 11:13:37 exit bookkeeping -> SELL MARKET filled 5.77 -> flat.
Ledger: closed_trades=1, ret_net +0.17% (+$23.17); equity 99,285.71 -> 99,308.88.
Tags: pf_est 2plus n=1, n_strict_est 0_1 n=1, session AM n=1.
One trade carries no inference (55%-win / +9.2% avg-win / -8.8% avg-loss
population; a near-scratch is ordinary). What IS established: the entire live
path is exercised end-to-end against the broker - scan -> strict gate on IEX
bars -> rolling arm at 0.9 x close -> refresh-on-tick -> fill detection below
limit -> OCO protection -> tl30 time-stop -> market exit -> ledger. Judge live
fills vs RAW A3b pf2 +1.13% only at n >= 30.

## 2026-09-16b — LIVE FILL SEMANTICS: the resting bid is adversely selected
`factory/scripts/live_fill_quality.py` (new, read-only) compared each live fill
against the consolidated SIP tape. 9 of 10 fills analysable (1 too recent for
SIP; the >=15-min rule):
- **mean fill = 259 bps BELOW our own bid** (8/9 fills below B; worst -830bps),
- **the SIP tape had already traded at/below our B before 9/9 fills**, median
  **203s earlier** (two clusters: fast sweeps 13-38s before, deep -170..-830bps;
  and long grinds 814-899s before, filled at ~the limit),
- live resolutions (ledger, authoritative): **6 stops, 1 target, 1 tl30, 1 -1.6%,
  1 -4.4%** vs the frozen expectation of ~48% target exits.
Mechanism: the frozen backtest fills on the FIRST new bar whose low <= B and
credits the fill at exactly B; the live order is not first in line at B, so it
fills only once the tape has already broken through - i.e. in the middle/end of a
collapse. Entry is then deeper (2.6% below B), the stop (0.9B) sits only ~7.4%
under the fill instead of 10%, the first post-fill prints are often already at or
under the stop, and the snapback that the frozen rule harvests tends to arrive
AFTER the stop (median max favourable excursion from the fill is +10.2%, but the
stop is hit in a median 22s in the raw tape read).
Consequences: (1) live fills are NOT the A3b population - A3b modelled
consolidated first-touch fills, so the -5.54%/trade over 10 trades cannot be
compared to +1.13% as if it were the same experiment; (2) this is an
IMPLEMENTATION-level (fill-semantics) finding, not a verdict on the edge; (3) any
remedy (deeper bid, wider stop, entry only after the vacuum has stopped printing)
is a strategy change -> new pre-reg. Artifacts: live_fill_quality.json/.parquet.

## 2026-09-16c — Stack shut down cleanly (user order) + day result
User ordered the scripts stopped. Executed: KILL placed -> bot logged
`kill_file {stopping, canceled: 0}` 11:51:43 ET and exited; supervisor relaunch
attempts each exited immediately via the startup-KILL check (idempotent);
watchdog task DISABLED; supervisor bash chains + observer python killed;
verification "none remaining"; broker after shutdown 0 positions / 0 open orders
(nothing orphaned), equity 98,637.10. KILL left in place and the task left
disabled until restart.
Day (09-16, stopped at 11:52 ET): 7 bids -> 19 refreshes -> 7 fills -> 7 OCOs ->
7 exits, 1 partial, 0 errors, 0 alerts. Ledger cumulative: 14 round trips,
mean_net -3.17%/trade, median -5.52%, pos 0.286 (AM n=8 -1.43%, PM n=6 -5.49%),
all tagged pf_est>=2. Equity 99,285.71 -> 98,637.10 (-$648.61 paper, -0.65%).
Not a verdict on the edge: live fills are a queue-adverse population (fills
-259bps below our own bid; the tape was already through B before 9/9 fills;
6 stops / 1 target in the first 10) - the LIVE_FILL_QUALITY finding
(996668e). Judge remains n>=30 of the *validated* population, which live
resting bids do not currently produce.
Restart: rm data/KILL; re-enable algo-stack-watchdog; relaunch both supervisors.

## 2026-09-16d — H019 reconciliation: one canonical runner-frequency truth (+ basket-thesis planning)
- Contradiction closed. researches/STATE.md carried "~1.2-1.5 runner days/day"; ledger H019 and
  INTENT carried 0.82. Root cause: runner_phenom.py selects >=60% open->close names and falls
  back to the day's #1 when none exist; the artifact (223 rows) mixes 141 genuine runners + 82
  fallback rows. Genuine = 0.82/day over 172 days; 90/172 days (52%) hold >=1; mixed = 1.30/day
  (where "1.2-1.5" came from; monthly mixed range 1.14-1.50).
- All headline shape stats (322min open->high, 15% half-done by 10:00, 55% afternoon, 84% halt,
  +83%/+177% dose-response) were computed on the genuine 141 - verified, no contamination.
  Retrace claim clarified: 24/141 strict >30% (+244%), 25/141 inclusive (+239%).
- Evidence: factory/artifacts/runner_phenom_reconcile.json (counts, per-month table, per-subset
  stats, raw spot-checks 2025-05-01/02/06); producer factory/scripts/reconcile_runner_phenom.py;
  EXP-83 appended.
- Machinery note: the committed artifact is a single-line JSON *array*, while runner_phenom.py
  appends JSONL with a resume-by-day reader - rerunning the producer against this path would
  misparse/corrupt it; write to a fresh path if ever rerun.
- Docs corrected: researches/STATE.md (phenomenon block), researches/HYPOTHESES.md (2026-09-07
  phenomenology block), researches/INTENT.md (frequency parenthetical). H019 ledger entry already
  said 0.82 (REPLICATED-DESCRIPTIVE) - untouched. Entries 671/719 kept as history; this entry
  supersedes their ambiguity.
- Context: basket/participation thesis moved from concept to prototype plan (planning only, no
  code, no runs; H025 stack stopped, untouched). Next: Phase 0 thesis + pre-reg docs on user go.

## 2026-09-16e — BASKET-01 Phase 0 artifacts written (planning only)
- Created researches/THESIS-BASKET-01.md (creed; formal statement; state semantics
  CANDIDATE/ACTIVE SURVIVOR/LAST SURVIVOR/RELEASED/FORCED FLAT; four label objects;
  identification-vs-economics split; ex-post vs policy pay-for-team; LS hypothesis with
  attrition-timing measures; evidence hierarchy; collisions incl. H2/H12/H025/ML boundaries)
  and researches/PRE-REG-BASKET-01.md (Phase-1 descriptive anatomy contract: broadest
  defensible PIT universe + reported strata; A/B coequal; A_open + 2025-only A_pm; B grid
  09:45/10:00/10:30/11:00; rulers only; tables T1-T10 incl. containment, controls with
  rank-adjacent primary, LS columns; NO release-rule constants).
- Registered H038 (OPEN-PLANNING) in HYPOTHESES.jsonl; pointers added to researches/STATE.md
  and researches/HYPOTHESES.md. No code, no runs, no Phase-2 constants; H025 untouched.
- Sequence locked: measure the race -> survival/death geometry -> ONE simple implementation
  frozen in PRE-REG-BASKET-02 -> economics test.

2026-09-17 (BASKET-01 first read, descriptive only): extraction complete — 1065 days, QA PASS (1 declared skip 2025-02-03; 0 corrupt/structure/bars/tmp; 140,740 candidate rows). Passes: T5 paths (75,005 filled members), T7 overnight shadow (25,609 ok / 262 no_data flagged), T9b matched-random (239 sampled days). Aggregates T1-T10 + T4b frontier committed. Headline descriptive numbers (population B, main top-3, T=600, pooled months unless noted): F(+30% before own -10%) = 0.255; F(+50% before -15%) = 0.162; F(+10% before -3%) = 0.438; member-level Q(+30 first | +30 touched) ~ 0.81; T7b touch lens P(basket has >=1 +30% member) ~ 0.306 (T=585/600); rank-adjacent control (ranks 4-6) F(+30,-10) = 0.127 (top-3 ~2x); matched-random +30% touch base rate ~0.008-0.012. T5 paths (MFE>=30 members): median retracement-before-high -15.9%, time-to-high 134 min, post-high drawdown -27.2%, EOD-vs-high -21.8%. Overnight shadow median gap EOD->next open: -0.7% (all), -4.2% (MFE>=30), -5.7% (MFE>=50). No interpretation registered yet — these are PRE-REG-BASKET-02 inputs only.

2026-09-17 (BASKET-01 PRE-REG-02 drafted — NOT frozen): Phase-2 primitive contract written as
researches/PRE-REG-BASKET-02.md, derived from the committed anatomy by pre-registered
mechanical mappings (not by P&L, not by visual choice): population B T*=600 (plateau; LULD
opening-window past; development-formulated narrowing, not a pre-registered rule) + A_open
co-primary; N=3; release rule R1 = -10% from fill (pre-declared set {8,10,12,15}; shallowest
with pessimistic tail-preservation Q>=0.80 — measured L=8 Q=0.743 vs L=10 Q=0.807; note Q is
H-dependent and B/600-calibrated: A_open Q_30(10)=0.762); permissive survivor rule S1 = 50%
giveback of post-fill running high; arms Arm-0 (all-hold comparator) / Arm-1 (R1 only, primary
confirmatory) / Arm-2 (R1+S1); forced flat relative to the session's last bar with involuntary
halt carry; cash!=risk accounting; decision rules D1-D5 (implementation diagnostic / rule
adoption / thesis-level conditions / parameter re-point requires fresh unseen months /
kill-vs-refine). Derived descriptive inputs (pooled, B main, 1065 days, T=600): F(+30,-8)=0.237,
F(+30,-10)=0.255, F(+30,-15)=0.292; marginal retention per pp falls monotonically 0.0221
(L3->5) / 0.0191 (L5->8) / 0.0094 (L8->10) / 0.0073 (L10->15); rank-adjacent F(+30,-10)=0.127;
F(30,-10) by T: 09:35 0.186 -> 09:45 0.256 -> 10:00 0.255 -> 10:30 0.232 -> 12:00 0.199
(plateau 09:45-10:15, then decay). No Phase-2 P&L computed; LS event study still blocked;
H025 and reserved months untouched.

2026-09-17 (BASKET-01 PRE-REG-02 adversarial audit, pre-freeze — corrections applied): an
independent adversarial review of the draft found 12 fatal / 7 material / 3 minor issues; all
were addressed in the document before any freeze. Corrections of record: (1) arms renamed
Arm-0/1/2 (the old A/B/C names collided with populations A_open/B and made D1-D5 ambiguous);
(2) one exact per-population estimand B_{d,p,arm} with carry-slot occupancy (a carried position
keeps its slot; total exposure never exceeds 3 slots per population — no implicit leverage) and
no combined-portfolio verdict input; (3) complete event-order precedence (R1 evaluated first;
S1 against the prior peak; peak updated only after evaluation; R1/S1 collision on the
forced-flat decision bar priced at min(open, level)); (4) forced flat redefined relative to the
session's actual last bar using a mandatory committed early-close calendar (this dataset carries
bars to 16:00 even on known early-close dates, so the calendar cannot be inferred silently;
truncated sessions 2022-09-30 / 2023-03-10 / 2023-03-13 flagged); (5) pending-release-through-halt
and no-resumption terminal-value rules defined; (6) friction made computable (100 bps round trip
per filled slot, charged once including carries, 150 adversary, cash pays nothing, full-fill
small-notional assumption with capacity explicitly not claimed); (7) D1 denominator fixed to ALL
dev days, per-population benchmarks (B 25.54%, A_open 29.20%), plus the stricter
at-or-above-next-open +30% sale rate (B 18.50%; the 30.5% exec lens is an access ceiling, not an
exit); (8) D2 rewritten as a binary comparison (mean, p10, top-5% P&L share) with no tolerance
constants; (9) D3 split into dev / reserved one-shot / combined-supplemental verdict roles with
computable negative conditions; (10) S1's "clips <=10% of tail" justification WITHDRAWN
(T5_paths.json has no population/T dimension; -0.459/-0.492 are medians of 51 monthly p10s, and
20/51 resp. 22/51 months have monthly p10 < -0.50) — g=50 retained as an owner-chosen permissive
rule, not a proven bound; (11) numeric corrections: 600->615 overlap is 1.75/3 (not 2.1), RTH
remaining at 10:00 ~92% (not two-thirds), LULD clustering marked external and not mechanically
verified. T*=600, L=10, g=50 unchanged and explicitly labelled owner-decisions whose confirmation
is reserved/forward only. Still NOT frozen; awaiting user sign-off.

2026-09-18 (BASKET-01 SIP certification layer — pilot day 1): factory/scripts/sip_certify.py
(+ --why review printer) compares SIP-derived facts with the committed anatomy per day:
re-rank of stored top-10 by SIP prices; fill/MFE/MAE deltas; non-ambiguous order flips +
resolution of stored AMBIGUOUS cases via trade sequencing (raw and price-updating lenses);
quote/spread at fill; RTH price-updating max vs stored day high. Day 1 (2021-02-01):
34 rank-flip positions / 13 snapshots; fill delta max 469bps; MFE delta max 492bps; 21 order
flips on 4 members; spreads p50 107bps / p90 358bps; 10 tail diffs, max +9.3%
(CDE 12.58 -> 13.75). Verified mechanisms (not hypotheses): the legacy tape loses whole minutes
(DCOM et=571 absent), stores single-print minutes (DCOM et=570 range 24.48-26.0 collapsed to
25.11; et=572 lone print 24.835 absent from SIP) and understates ranges (YGMZ et=592 low 41.321
vs 41.811 -> crosses the -3% ruler = ordering flip). Notes:
factory/artifacts/basket/sip/CERTIFICATION_NOTES.md. Pilot scope limits: candidate-union only,
prev_close not re-derived, A_pm skipped, quoted spreads not fill models. PRE-REG-BASKET-02
freeze remains PAUSED; no parameters or rules changed.

2026-09-17 (BASKET-01 re-centering pass, pre-freeze — owner directive): read layer re-centered
away from ruler-as-ontology. New measurement artifacts: T11_dist.json (full daily basket
anatomy: continuous member / day-max excursion distributions incl. extremes, 0/1/2/3-mover
frequencies across the full ladder, ordinary days, pay-for-participation arithmetic —
explicitly ex-post illustration) and T12_splitaudit.json (artifact audit). Headline readings
(pooled, B T=600, 1065 days, main top-3): member MFE p50 +7.7% / p90 +36.9% / p99 +142.6% /
max +1585% (QMMM 2025-09-09; genuine); day-max p50 +18.7% / p90 +74.6% / p99 +227%; 2nd ticket
p50 +6.8% / p90 +20.1% / max +92.7%; 3rd p50 +2.3% / p90 +8.2% / max +37.7%; k>=1 movers
90.9 / 75.0 / 47.5 / 30.6 / 17.3 / 6.0% for +5/+10/+20/+30/+50/+100; k>=2 60.9 / 32.5 / 9.9 /
4.1 / 1.3 / 0%; all-3 19.3 / 5.1 / 0.9 / 0.3 / 0 / 0%; ordinary days (day-max < 10%) 24.7%,
on which member p50 +3.6% vs adverse p50 -9.6%. Ladder-wide tail preservation Q(H, L=10):
0.858 / 0.818 / 0.807 / 0.783 / 0.703 for H=10/20/30/50/100 (at L=15: 0.958 / 0.945 / 0.946 /
0.934 / 0.891) — the far tail wants more room than -10%; the final choice is the owner's at
freeze. Containment (session-max leader inside our top-N): top-1 in top-1 2.6% -> 14.4%
(09:35 -> 12:00); in top-3 4.9% -> 18.7%; in top-10 8.0% -> 22.4%. ARTIFACT AUDIT (T12): of
38,331 member-days exactly 1 bad-print glitch (BRP 2022-03-10: et=587 close 1092.71 and et=629
open 785.47 -> close 25.14 corrupted both the causal rank and the recorded MFE; glitch-free
member MFE +3.7%), plus 273 susp_expost split-like member-days (0.7%; raw >= +100% tail 697 ->
691 when excluded, max unchanged apart from BRP). No strategy code; PRE-REG-BASKET-02 remains
NOT FROZEN.

2026-09-17 (BASKET-01 T11 pairing-bug fix; SIP certification phase begins): basket_dist.py
sorted members' MFE but kept MAE in original member order, so "others_adverse" could exclude
the wrong member's adverse excursion. Fixed by preserving per-member pairing (pairs[1:] now
excludes the best-MFE member's own MAE); self-test extended with a case where the best-MFE
member is rank 2 (fails under the old code). T11 recomputed: B/600 cover 0.628 -> 0.553
(669 -> 589 of 1065 days), ratio p50 1.34 -> 1.09; A_open 0.551 -> 0.496; A_pm 0.472 -> 0.520.
Alpaca SIP historical trades+quotes verified reachable with the .env keys (probe: BRP
2022-03-10 09:40-10:35 ET window max SIP trade price 25.76 vs the corrupted 1092.71/785 print
in the clean tape). PRE-REG-BASKET-02 parameter freeze remains PAUSED pending SIP certification.

2026-09-17 (BASKET-01 data-provenance audit): every BASKET input family documented
(provider/feed/resolution/transforms/breaks) -> factory/artifacts/basket/data_provenance.json +
DATA_PROVENANCE.md. Key facts: HF mito0o852/Finnhub bars for 2021-2023 + 2025-02..2026-02 (feed,
trade conditions, cancellations unknown); Alpaca SIP bars from 2026-03 (provenance break; HF
ohlcv_2026-03 also local for cross-feed check); clean_month bakes RTH/dedup/$2 floor/volume>=100
BEFORE BASKET logic; 2024 raw+clean absent while 2024 leaderboard exists (non-regenerable);
2025-02 clean without raw sibling; 2025 premarket = SIP; leaderboard inherits clean mix; IEX and
subminute-quote lanes are separate/experimental. Next: SIP pilot ingestion + certification panel.

2026-09-18 (BASKET-01 SIP upgrade — raw ingestion layer live): per user directive, Alpaca SIP
is now the high-fidelity measuring instrument (PRE-REG-BASKET-02 parameter freeze remains
PAUSED). Committed sip_ingest.py (resumable/atomic, zero cleaning): data/sip/{trades,quotes}/
YYYY-MM-DD.parquet + per-artifact manifests (sha256, schema, window, counts, per-symbol errors)
+ append-only manifest.jsonl; window 09:25-16:05 ET (DST-correct); pilot panel of 20 era-spread
days launched detached (BRP 2022-03-10 bad-print day, QMMM 2025-09-09 +1585%, 4 extremes,
3 multi-survivor, 3 ordinary, 3 halt, 3 ambiguity). Day 1 (2021-02-01) verified: 3,318,734 trade
rows / 54 syms (29.5MB) + 959,117 quote rows / 15 syms (5.1MB), ts 09:25:00.205851-16:04:59.974
ET, 0 nulls, 16 exchanges, raw condition codes preserved (['@'], ['@','I'], ['@','F'], [' ']).
Sample manifests + layer README committed under factory/artifacts/basket/sip/. Next: SIP
trade->1-min bar builder with explicit condition policy (calibrated against provider bars),
quote/execution-truth layer, then certification comparison -> regeneration scope. Context: T11
pairing bug fixed (1703ebc); provenance audit committed (d157237); no BASKET rule frozen.

2026-09-18 (BASKET-01 SIP upgrade — bars layer + first provider cross-check): sip_bars.py
rebuilds 1-min bars from raw SIP trades with Alpaca's documented condition-update
table (tape-aware, strictest-rule-wins; auction codes Q/M/O/5/6 excluded from bars and preserved
as a separate artifact: 221 prints day 1). Self-test + live run: 2021-02-01 3.32M trades ->
16,613 bars / 54 syms (~20s). Provider cross-check: 16,613/16,613 matched, 0 ours-only,
0 provider-only inside 09:25-16:05 (all 6,675 provider-only bars out of window), exact-cell
99.982% (16,610/16,613). Only mismatches: 3 volume-only bars (STPK 11:28 +4,000sh [' ','B']
avg-price print provider excludes; ALYA 11:46 +1 trade/50sh; STPK 09:30 +3/120sh
late/superseded prints), OHLC identical. bars + auction prints under data/sip/derived/
(gitignored). Next: quote/execution-truth layer; certification panel across the 20 pilot days.

2026-09-18 (BASKET-01 SIP certification panel COMPLETE — regeneration decision note): 20-day
SIP pilot fully ingested (40 artifacts, manifests + sha256 in data/sip/), own bars per Alpaca's
documented condition table, event-level certification vs the legacy anatomy + a triage layer.
Panel A-E (260 snapshots / 695 member-days): top-3 SET changed 15.4% of snapshots; 763
rank-flip positions; decision-price deltas p50 ~1bp; fill deltas p90 77bps (7.5% >100bps);
498 non-ambiguous first-passage order flips (all on healthy-coverage symbols; 27/27 stored
AMBIGUOUS resolved by trades); quoted spread at fill p50 86bps / p90 472bps; stored >=+100%
member-days 50 -> 49 confirmed (the 1 = BRP fabricated spike). All large tail revisions
decoded: BRP = legacy bad print (SIP true max ~26.4); BNY x10.6 and GOLD x1.49 = scale/adj
offsets (SIP ~106-109 / ~27-29 all day; percent-safe, absolute levels not comparable); 14
diffs = SIP archive gaps (auction-print-only symbols e.g. BKKT/BE/RDW 2021-10-25; live probes
confirm 0 trades; SIP is NOT truth there). Control: 2026 panel days (same Alpaca provenance)
show 0 rank flips / 0 set changes. Verdict in factory/artifacts/basket/sip/
SIP_REGENERATION_DECISION.md: REGENERATE Phase-1 from SIP-derived bars (guardrails: per
symbol-day coverage QC; auction side channel; widened candidate net beyond legacy union;
quotes for candidates only; reserved months raw-only). PRE-REG-BASKET-02 remains PAUSED; no
strategy/parameter changes; owner review next. Artifacts: SIP_DECISION_NOTE.md, SIP_TRIAGE.md,
SIP_REGENERATION_DECISION.md, sip_decision.json, sip_triage.json, certification_<day>.json
(x20); scripts sip_decision.py, sip_triage.py (sip_decision.py bugfix: skip A_pm 'skipped'
entries + <200-trade coverage classification).

2026-09-18 (BASKET-01 SIP guardrail refinement — measured net sizes): guardrail #3's
"gain-floor superset" acquisition net is measured and rejected as impractical: on 8
era-spread days a +2% floor reaches 1,587-3,162 names/day (30-70x the panel) while the
per-T cutoff-margin net (within 1% of the legacy 10th-ranked decision score; anchor =
max(gain vs RTH open, gain vs previous close)) is 11-23 names/day, only 4-11 beyond the
legacy union. Adopted: legacy union + winners + cutoff-margin(1%) + A_open margin at
09:30 ≈ 55-70/day (≈1.1-1.3x panel cost → 1,065 dev days ≈ 4-5 days single-process, ~1
day with 4-6 processes). Alpaca rate probes: 1.3 req/s sequential, 2.8 req/s at 6
workers, 2.7 at 12 workers, zero 429s (≤164 req/min, under Basic's 200/min). Artifacts:
factory/scripts/sip_net_size.py (self-tested) + factory/artifacts/basket/sip/net_size.json
+ addendum in SIP_REGENERATION_DECISION.md. Still awaiting owner review before any
backfill; PRE-REG-BASKET-02 freeze paused; no thesis/ruler/parameter change; H025 and
reserved months untouched.

2026-09-18 (SIP owner-approved regeneration — architecture amendments + measurement fixes):
owner approved Phase-1 regeneration under an amended two-layer discovery architecture:
(a) full PIT-universe SIP minute bars reconstruct top-1/3/5/10 ranking independently of the
legacy tape; (b) raw trades/quotes only for the discovered SIP candidate neighborhood;
legacy union demoted to audit; the 1% legacy-cutoff-margin net is NOT canonical. Also:
SIP-derived previous-session close (stored legacy prev_close retired for ranking), A_pm
premarket acquisition, per-symbol-day coverage classes (healthy / provider-bar-only /
unresolved; gaps reported, never silently dropped), 4-6 disjoint-day workers with per-day
manifests canonical. Measurement fixes applied + self-tested: sip_bars.ts_min_utc now true
UTC truncation (was ET wall time relabeled as UTC; ET minute/OHLC unaffected); sip_certify
ambiguity aggregated per (H,L) cell with resolved_raw / resolved_price_updating / unresolved
(was: rows with ambiguity counted and effectively always resolved); \"order flips\" relabeled
bar-based (legacy bars vs SIP-derived bars) in cert files, decision note and docs; the 15.4%
top-3 set-change declared a lower bound (stored top-10 rerank only). PRE-REG-02 banner and
SIP_REGENERATION_DECISION.md owner-amendments section updated. Measurement-only: no
thesis/ruler/parameter change; PRE-REG-BASKET-02 still NOT FROZEN; reserved months untouched.

2026-09-18 (SIP Layer-1 candidate snapshots): new factory/scripts/sip_candidates.py
(self-tested; smoke on 2021-02-01 -> net=67, prev_day=2021-01-29) reconstructs the frozen
BASKET ranking snapshots DIRECTLY FROM SIP compact tables: A_open = o570 / SIP previous-
session close - 1 (previous close chained from the previous available SIP table; A_open
reported, never silently gated, when no SIP prev session exists); B(T) = px_T / o570 - 1
(prev-close independent); winners_open/winners_prev diagnostics; per-snapshot top-10 plus
a 1pp SIP boundary margin; candidate net = union of all snapshot top-10s + margins +
winners. The legacy union is not consulted. Outputs: data/sip/candidates/YYYY-MM-DD.json
+ per-day manifest (source sha256, counts, elapsed) + index builder (--index). Atomic,
resumable. Measurement-only; no thesis/ruler/parameter change; PRE-REG-02 not frozen.

2026-09-18 (SIP Layer-2 net ingestion mode): sip_ingest.py extended with --net (trades =
SIP-discovered candidate net; quotes = top-3 per SIP snapshot; legacy union not consulted),
--all (every day with a candidates file), --workers N (disjoint-day workers; per-day
manifests canonical; shared global log disabled via --no-global-log), --index (rebuild
merged manifest_index.jsonl from per-day manifests after worker runs), plus append_global
parameterization and a selftest for net symbol derivation + index rebuild. Output root for
regeneration: data/sip/net/{trades,quotes}. Live probe 2021-02-01 (3 symbols):
trades 75,238 rows / 9.2s; quotes 174,294 rows / 17.4s. Measurement-only; no
thesis/ruler/parameter change; PRE-REG-BASKET-02 not frozen; reserved months untouched.

2026-09-18 (read-packet builder): factory/scripts/basket_read.py — consolidated NON-SELECTIVE
read over the agg tables for any artifact root (--root / BASKET_ART_ROOT): composition,
containment, joint k-tail across the whole ruler ladder (5..100) for touch/exec/above,
competing-risk counters, the F/Q frontier over every (H,L) cell with zero-month counts,
runner path anatomy, overnight shadow, matched-random control, the continuous T11
distribution and extremes. `--write` emits READ_PACKET.md + read_packet.json beside the
tables. Self-tested; validated against the committed legacy anchors (B/600/N3 top1_in
0.1243; F(30,-10) 0.2554 / Q 0.807; touch k>=1@30 0.3061; random touch k>=1@30 0.0083;
F(100,-10) 0.0423 / Q 0.7031 with 22 zero months of 51). The reader selects nothing;
rulers remain rulers. Measurement-only; no thesis/ruler/parameter change; PRE-REG-02 not
frozen; reserved months untouched.

2026-09-18 (SIP substrate chain: netbars + anatomy driver, smoke-verified): new
factory/scripts/sip_netbars.py builds the Layer-2 substrate per day: derived bars from raw
net trades (sip_bars policy='alpaca'), provider SIP bars as fallback/cross-check,
per-symbol-day coverage class (healthy_raw / provider_only / unresolved, rule documented in
code), merged RTH frame data/sip/net/bars/<day>.parquet (+ coverage/<day>.json + manifests).
Smoke 2021-02-01: 67 symbols, 19,837 merged rows, classes 65 healthy_raw / 2 provider_only /
0 unresolved, 74.7s. new factory/scripts/sip_anatomy.py reuses basket_anatomy.process_day on
the SIP substrate with SIP prev close (previous available universe day c_last), A_pm from the
SIP premarket compact table when present, winners + audit patched from the FULL PIT-universe
compact table (containment uses all PIT symbols). Smoke 2021-02-01: 13 snapshots, audit
n_elig 5,290 / n_open0930 5,090 / missing 200, B600 top3 LODE +55.3% / KSPN +20.2% /
GSM +18.8%, winners_open LODE +85.3% / LACQ +67.4% / KIQ +63.2%. Both self-tested.
Measurement-only; no thesis/ruler/parameter change; PRE-REG-BASKET-02 not frozen; reserved
months untouched.

2026-09-18 (SIP A_pm snapshot + pipeline runner): sip_candidates.py gained the A_pm
population from SIP premarket compact tables (last print <= 09:29 ET, freshness <= 15 min,
scored vs SIP prev close; a missing premarket table is recorded as skipped, never silently
omitted) with a pm_top self-test (stale-print exclusion asserted). New sip_pipeline.py runs
the per-day chain netbars -> anatomy with disjoint-day workers and per-worker logs
(resumable; self-test OK; smoke 2021-02-01: classes healthy_raw 65 / provider_only 2 /
unresolved 0). Measurement-only; no thesis/ruler/parameter change; PRE-REG-BASKET-02 not
frozen; reserved months untouched.

2026-09-18 (SIP read-layer parametrization): the six read/QA scripts (basket_aggregate,
basket_dist, basket_t5_bars, basket_shadow_overnight, basket_random_control, basket_qa) now
honor BASKET_ART_ROOT (default = legacy factory/artifacts/basket) so the identical frozen
tables can be regenerated over the SIP anatomy tree via
BASKET_ART_ROOT=factory/artifacts/basket/sip. All six self-tests pass. Measurement-only; no
thesis/ruler/parameter change; PRE-REG-BASKET-02 not frozen; reserved months untouched.

2026-09-18 (SIP Layer-1 discovery — full PIT-universe SIP minute bars): new
factory/scripts/sip_universe.py fetches Alpaca SIP 1-min bars across the complete PIT-eligible
universe per day (feed=sip, Adjustment.RAW, batches of 500, per-day atomic parquet + manifest,
resume; --premarket mode = A_pm window 04:00-09:29 ET; --workers N runs disjoint-day
subprocesses; --index rebuilds the merged index from per-day manifests, never concurrent
appends). Compact per-symbol row: o570 + delayed-open flag, px at every frozen T (close of the
last bar with et<=T-1, with the et actually used), day hi/lo/c_last/vol/n_bars. Three
handling fixes: '^' and '/' symbols are pre-filtered (Alpaca rejects those preferred/class
spellings and fails a WHOLE batch on one bad symbol), API-invalid symbols are removed
individually with a loop guard (the raw removal previously could not match whitespace-padded
names and spun forever), and PIT symbols are whitespace-stripped (the bundle is fixed-width
padded); seed days before the PIT archive start fall back to the earliest vintage.
Benchmarks: 2021-02-01 5,290/5,366 syms 55.8s; 2022-03-10 6,054/6,289 56.5s; 2025-09-09 and
2025-10-30 ~47-60s each. Full dev-span backfill launched: 1,068 days (1,066 dev + 2
prev-session seeds 2021-01-29 & 2025-01-31), 5 workers, ETA ~4h; run validated to include May
2026 (an earlier full-date string filter had silently excluded it). Raw stays under
data/sip/universe/ (gitignored); a compact index/diagnostics copy will be committed under
factory/artifacts/basket/sip/ when the backfill completes. No thesis/ruler/parameter change;
PRE-REG-02 unfrozen; reserved months untouched.

2026-09-18 (Layer-2 net fetch relaunched memory-safe after the OOM + 9p incident):
sip_ingest.py hardened — per-symbol part flushes every 5 symbols (parts merged to the day
parquet then deleted), zstd level 3 on all parquet writes, C:-free-space floor
(SPACE_FLOOR_GB=52) that stops cleanly before the user's disk floor. Bounded memory verified
on the two heaviest early days (2021-02-01/02: 4.37M/3.55M trade rows; workers ~380-420MB
RSS, was 1.5-3.4GB pre-fix). Relaunched with 2 disjoint-day workers over 1,068 days
(skip-existing honors the valid 2021-01-29 leftover); pace ~8-9.5 min/day/worker => ETA
~3.2 days. Raw trees on WSL ext4 (/home/hillel/sip/net via symlink), C: >= 52GB floor.
Measurement-only; PRE-REG-BASKET-02 unfrozen; H025 and reserved months untouched.

2026-09-18 (SIP regeneration chain, autonomous): Layer-2 fetch (sip_ingest --net --all --workers 2,
memory-bounded part flushes, zstd, disk floor now 50GB) running from 2021-02; drain loop
(/tmp/opencode/drain_loop.sh) runs sip_pipeline --all --workers 2 every 25 min and on fetch exit
runs a final drain, net index, sip_coverage --write, then writes ORCH2_DONE; final read chain
(/tmp/opencode/final_read.sh) waits for ORCH2_DONE, then runs basket_aggregate, basket_dist,
basket_t5_bars, basket_shadow_sip --write, basket_qa, basket_read --write under
BASKET_ART_ROOT=factory/artifacts/basket/sip and writes FINAL_READ_DONE. New
factory/scripts/basket_shadow_sip.py: SIP-consistent overnight shadow (T7) computed from the universe
tables (next-day o570 vs stored close; reasons counters; reserved months refused) - no legacy tape
read in SIP mode. Matched-random control (T9b) is NOT regenerated on SIP in this pass (needs raw
fetches for randomly drawn names) - the read packet notes it explicitly. Measurement-only;
PRE-REG-BASKET-02 unfrozen; H025 and reserved months untouched.

2026-09-20 (SIP Phase-1 regeneration COMPLETE — anatomy regenerated from the SIP substrate; QA PASS):
Layer-1 full-PIT SIP universe (1,068 days: 1,066 dev + 2 seeds), Layer-2 raw SIP trades+quotes for the
SIP-discovered nets (1,066/1,066 days, zero missing), netbars (derived-from-raw bars with provider
fallback; coverage classes healthy_raw 63,815 / provider_only 6,121 / unresolved 8 symbol-days listed
explicitly in COVERAGE.md), SIP anatomy 1,066 days (basket_qa PASS: 0 missing/corrupt/structure/bars/tmp;
149,201 candidate rows). Fixes of record during the run: REST v2 page streaming (flat memory; monster
2021-05-27 day 8.3M rows at ~150MB RSS), provider-bar transient-error retries with derive-only fallback
(manifest field provider_unavailable), 50GB disk floor, per-symbol part flushes + response release,
worker-safe per-day manifests, and a prev-close adjacency guard (a seed day had resolved prev_close 13
months back via 2023-12-29; such days are now skipped as prev_session_outside_coverage). Also: the two
dev days the candidates step had missed (2026-05-21, 2026-05-29 - they were still being written when the
sweep's completion guard fired) were re-built and folded in. Regenerated tables: T1-T10 + T4b frontier +
T11 dist + T5 paths (82,853 members) + T7 SIP overnight shadow, plus READ_PACKET.md/json and
READ_COMPARE.md vs the legacy root. Headline legacy->SIP shifts (B/600, main top-3): joint +30% touch
k>=1 30.6% -> 39.1% (exec 39.0%), k>=2 4.1% -> 6.3%, all-3 0.28% -> 0.56%; +100% k>=1 6.0% -> 7.8%;
frontier F(+30,-10) 25.5% -> 31.2% with Q 80.7% -> 77.1% (L=10 no longer clears the previously declared
Q>=0.80 mapping under SIP data; L=15 gives F 36.5% / Q 92.5%); containment top1_in 12.4% -> 9.8%;
member MFE p50 7.2% -> 8.4%, MAE p50 -8.3% -> -10.5%; ordinary days 24.7% -> 19.3%; gap-blocked slots
3,691 -> 2,678. Continuous (SIP, B/600): member MFE p90 44.9% / p99 168.1% / max 727%; day-max p50 23.6%
/ p90 86.3% / p99 239.9%. All numbers are descriptive; no ruler/rule/parameter was selected from them.
PRE-REG-BASKET-02 remains UNFROZEN (its constants are provisional and now carry SIP-vs-legacy deltas);
H025 and reserved months untouched.

2026-09-20 (evening) — BASKET-01 SIP Phase-1 REPAIR PASS + canonical read layer (all numbers [ART]/[RUN],
descriptive only, no parameter selected, PRE-REG-BASKET-02 still unfrozen):
Repairs of record (work order §7): (1) T2 containment reported at day level — the earlier printed shares
divided by a 3x event denominator (per-winner increments); all containment shares in the previous packet
were 1/3 of the day-level value. (2) T2 remaining opportunity now from the post-fill high, paired on the
same rows with the completed/ahead shares; blocked fills excluded from fill-based shares and counted
(blocked_in). (3) T5 pre-high retracement / post-high giveback rebuilt from raw SIP prints: peak-minute
low/high ordering resolved from trade timestamps, peak bar excluded; rows keyed by population and T
(82,853 members; order lo_first 44,514 / hi_first 33,683 / unresolved 4,656 = 5.6%, dropped from those
stats and reported). (4) T7 policy-free pay-for-team added (all-hold EOD gross/net100; stylized
failed-ticket costs -3/-5/-8/-10; P(best pays peers); surplus/deficit; break-even map r*=k*c, k in {1,2}).
(5) Layer-1-vs-anatomy selection audit: 14,924 snapshots, agree 96.1%; after repair, promoted names are
all top-10-internal (rank>10 promotions 0, margin 0); the 584 dropped/584-only cases are the documented
B-population difference (no prev session in the previous day's universe table → anatomy requires
prev_close>0), 1 outside-net, 2 no-decision-bar. (6) exec lens legend added to the packet (touch/exec/
above; exec is NOT sellability). (7) overnight shadow compounds (was adding). (8) T9b matched-random
control regenerated ON SIP (244 sampled days; B/600 touch30 k>=1 1.65% vs treated 39.2% — the control
never overlaps the treated top-10). (9) T8 consolidated month/quarter stability artifact (345 rows).
(10) stale 2025-02-03 QA skip now declared only on trees lacking the 2025-01-31 seed (the SIP root
declares none). (11) market base-rate funnel added: full PIT universe, both anchors — open-anchored
day-shares +50 90.0% / +100 47.9% / +200 13.7%; prev-close-anchored +100 74.7% / +200 36.2% (explicitly
separate from the basket's post-entry +100). (12) sip_candidates missing-universe-table skip is now
reported; sip_ingest catches per-day exceptions and continues. INCOMPLETE-NET FINDING during the audit:
10 days had Layer-2 nets far below the candidates net (worst 7/57 on 2021-04-06) because ingest raced
the candidates step; nets re-fetched, coverage re-certified (symbol-days 70,176; healthy_raw 64,041 /
provider_only 6,126 / unresolved 9), the 10 anatomy days re-extracted, full read chain re-run (QA PASS).
Sealed acquisition: 2024 + 2025-01 SIP RTH+premarket (272 days each) fetched mechanically, per-file
sha256/rows/schema certified (SEALED_2024_CERT.md); no BASKET computation touched those days; reserved
2026-06..08 neither fetched nor read. Canonical packet regenerated: containment day-level B/575 10.1% /
B/600 29.3% / B/720 52.4% / A_open 8.7% / A_pm 9.1%; joint touch B/600 k>=1 39.2% / k>=2 6.3% / all-3
0.56% at +30; above30 k>=1 22.5%; F(30,-10) 31.3% Q 77.2%; F(30,-15) 36.6% Q 92.5%; T7 B/600 pays_net
30.4%, c3 49.5%. All artifacts under factory/artifacts/basket/sip/; scripts self-test via --self-test;
integrated read pending owner gate. H025 / flush lane untouched.

## 2026-09-21 (later) — advisor-directed final measurement pass completed; canonical packet regenerated

All of the following is [ART]/[RUN] under `factory/artifacts/basket/sip/` (QA PASS: 1,066 days, 15
snapshots/day, 159,900 candidate rows, 0 missing/corrupt/structure/bars/tmp). No parameter selected;
PRE-REG-BASKET-02 remains unfrozen.

Repairs that changed the numbers: (1) B(T) admission no longer requires a previous-session close (the
gate was never part of B's ranking rule) — the `dropped:no_prev_close` audit class (584) is gone and B
candidates now carry prev_close as nullable metadata; (2) A_pm31 added (coequal conservative 09:31
bound, identical premarket selection); (3) T5 rebuilt trade-by-trade from raw SIP prints under the
Alpaca condition policy (1,066 days, 89,122 members; `peak_recon_vs_stored_mfe` share_within_1e-4 =
1.0; 24 sparse(<5 prints) members); (4) T7 emits true monthly rollups + daily rows (the old "monthly"
key held day rows, which had made T8 monthly pays shares invalid); (5) EOD/prev-anchored leader objects
recorded and reported as separate containment counters; (6) Layer-2 provider fetch now retries symbols
the bulk request silently dropped — unresolved symbol-days 9 -> 2 (PMN 2023-02-13, MGLD 2023-09-19);
(7) capture funnel + race_by_view added; market base rates re-run with canonical eligibility ($1 floor
on open tradeability; `prev_close_prevfloor` sensitivity retained); (8) funnel `stage_day` deletes a
stale file when a re-staged day yields no rows (this stale-file case caused a 3-day mismatch:
2021-09-14, 2022-01-13, 2022-02-01).

Headline canonical numbers after the repairs (day-level, 1,066 days; hi_open leader object unless
stated): containment top1/top2/top3 — A_pm 8.7/7.1/4.0%, A_pm31 same selection (blocked 5), A_open
8.4/6.3/4.1%, B/575 10.1/9.4/8.0%, B/585 19.3/16.3/13.9%, B/600 28.5/23.4/20.8% (blocked 54), B/720
51.7/38.0/28.2%. EOD-close-anchored leaders are a separate object (EOD/open anchor top1: A_pm 7.0%,
A_open 6.5%, B600 22.7%, B720 46.3%). Joint tail (main, touch) — A_pm: +30 44.1% k>=1 / 9.7% k>=2,
+50 25.1/2.6, +100 9.7/0.3; A_pm31 +30 41.5/9.1, +50 23.4/2.4, +100 9.1/0.3; A_open +30 38.9/7.7, +50
21.5/2.1, +100 8.3/0.4; B/600 +30 39.6/6.3, +50 22.3/1.7, +100 8.0/0.1. Strict saleable (above lens)
A_pm +30 25.1% / +50 14.7% / +100 5.3%. Frontier: A_pm F(30,-10) 36.1% Q 75.8%, F(30,-15) 39.8% Q
87.3%, F(100,-10) 7.0% Q 71.7% (10 zero months); A_pm31 32.6/72.4, 37.1/86.1, 6.8/73.0 (10); A_open
31.1/74.1, 34.9/86.5, 6.3/75.0 (11); B/600 31.6/77.0, 36.9/92.3, 5.6/70.9 (19). Pay-for-team (all-hold
EOD net of 100bps): A_pm pays 28.1% (stylized -3% cap 50.8%, -10% 37.2%), A_pm31 27.3% (51.3/34.6),
A_open 26.8% (50.9/34.2), B/600 30.2% (49.4/36.9). Break-even coverage (best member MFE reaches r*) A_pm
k1c3 98.0% / k2c10 62.0%; B/600 97.8% / 57.6%. Tail destruction (race_by_view, dn-before-up share): a
-10 release aborts ~24-28% of A_pm/A_open tickets that later touch +50 (B/600 23.0%); a -15 release
~6-15%. Market base rates: +100 from RTH open on 47.9% of days (511), +200 13.7% (146); prev-close
anchor +100 86.5% (922), +200 66.8% (712). Capture funnel (hi_open/H100, 511 days): A_pm ranks the
monster in the top-3 on 103 days (20.2%), A_open 92 (18.0%), B/600 255 (49.9%, 229 with an accessible
fill); on the miss days another basket member still touched +30 on 159/408 (A_pm) and 68/256 (B/600)
days. Selection audit: 15,990 snapshots, agree 15,990 (100.0%, 0 differ once the score-desc /
ticker-asc tie-break contract was made explicit); promotions all top-10-internal;
CSLR 2023-11-13
B/575 rank 3 is now selected with its own fill (the one true slot-substitution case resolved); the 7
recovered provider symbol-days (+2 genuinely unresolved) are recorded with status recovered_provider. Verification: containment
0.2852, joint touch30 0.3959, T8 2025-06 B/600 pays_net 0.2 / c3 0.55, T5 trade-level stats (LODE
2021-02-01) and the funnel day-level sample all reproduced independently from raw data; funnel vs base
rates agree on all six (anchor, ruler) counts. Subagents remain unusable here (two further 30-min
zero-output timeouts) — direct verification used instead.

## 2026-09-21 (audit pass — measurement layer closed out)

Five scoped audits (population/selection, T5/race chronology, aggregation/denominators, stale state,
independent headline reproduction) ran to completion. Findings and dispositions: (1) ranking tie-break
was implicit — polars sort is unstable, and the 11 selection-audit diffs were exactly the 10 tie days;
the frozen ranking rule is now explicit everywhere (`score desc, ticker asc`) in basket_anatomy.topk,
the four winners sorts, sip_anatomy.winners_from_universe/union_leaders and sip_candidates.top_list →
selection audit is now 15,990/15,990 agree, 0 differ, 0 promotions. (2) T8 containment read N=10 rows
instead of N=3 (250/306 month blocks were wrong: B/600 pooled 0.3537 → 0.2852) — fixed; T8 quarterly
pays weighted by calendar days instead of `pays_days` (one quarter affected by 0.0004) — fixed.
(3) T5 now initializes the path at the actual fill (state zero = fill price, time origin = fill minute):
verified 12/12 + 8/8 members against raw prints, 0/89,124 members with peak_vs_fill < 0. (4) race_by_view
rebuilt on raw chronological prints (labels reproduced exactly; verified 11/11 pooled quantities and
10/10 microsecond-level labels). (5) Sub-$1 leader objects added (`winners_open_floored`,
`winners_close_open_floored`, independent $1-subset re-rank — NOT a subset of the unfloored list) with
`flr_`/`flreod_` containment counters: B/600 0.3865 vs 0.2852 unfloored, A_open 0.1126, A_pm 0.1201.
(6) T4/T7b `unfilled_slots` was degenerate (always 0) — now counts slots with no fill; `blocked_slots`
added (pooled B/600: unfilled 4 / blocked 487). (7) Stale-generation defects fixed (T2 missing flr
counters, T11/mbr missing `_producer`, funnel stale-file-on-empty, PRE-REG '8 of 9 recovered' →
'7 of 9'). (8) Packet coverage section restored (`unresolved_n` 2). Canonical numbers were otherwise
unchanged by the audit. Independent verification: all headline numbers reproduced from lower-level
inputs by separate implementations (containment, joint tail, T5, race, funnel, base rates, T7, T8,
random control). Selection audit: 1,066 days / 15,990 snapshots / 100% agreement / 2 unresolved
symbol-days (PMN 2023-02-13, MGLD 2023-09-19). No parameter selected; PRE-REG-BASKET-02 unfrozen.
Subagents were usable for this pass (5 audits + 2 verifiers completed; two earlier attempts hung).

## 2026-09-22 — Phase 2 opened (owner directive); PRE-REG-02 frozen at family level; sim contract frozen

Phase 1 formally CLOSED (measurement layer signed off; all headline numbers independently
reproduced). Owner directive: implementation/EV-discovery mode — find the highest-EV causal
harvest of the measured top-gainer phenomenon; freeze the family-level PRE-REG-BASKET-02
BEFORE any strategy outcome is inspected; establish ONE canonical event-driven simulation
contract; run bounded parallel worker families (scope: no generic thesis-defeat research, no
handicapping of strong results). Frozen this date: `researches/PRE-REG-BASKET-02.md`
(family registry, finite grids, DOF register, dual-block validation, holdout status) and
`factory/BASKET-SIM-CONTRACT.md` (fills/actions/accounting/friction/metrics/canaries);
`researches/THESIS-BASKET-01.md` gains the Phase-2 working-mode note; PRE-REG-01 §7 sequence
amended (single-strategy freeze moves to Phase 3). Sealed 2024/2025-01 outcomes and reserved
2026-06..08 remain untouched; no BASKET computation has touched them. Sim-core engine build
+ leaderboard-truth worker launched; family runs start only after the engine passes its
canaries and this freeze commit exists.

2026-09-23 — F1 closure [RUN]: validated the 120 frozen cells against PRE-REG-BASKET-02 §3.1 and
the FROZEN-2026-09-22 simulator contract; all had the exact 1,066 permitted unique dev dates,
required outputs, matching mapping/parameters/seed/contract hash/version, and consistent daily,
metrics, and run-summary evidence. Existing canary recovery report: 7/7 PASS; c7 = 1,066 days,
10,528 entries, cash/deployment invariants enforced. Atomically rebuilt only the shared F1
`surface.json` (sorted grid, neutral `validated_frozen` statuses; no reruns). Full-surface mean
basket-day ranges: 100bps -4.60%..-1.99%, 150bps -5.07%..-2.44%; dual-block per-cell means:
2021-02..2023-12 -4.90%..-2.16% / -5.37%..-2.59%, 2025-02..2026-05 -5.29%..-0.94% /
-5.73%..-1.42% (100/150bps respectively). Descriptive dev surface only: no OOS, selection,
profitability, or live inference; see `factory/artifacts/basket/phase2/F1/README.md`.


2026-09-24 — SIMULATOR SUBSTRATE CORRECTION C1 [code/RUN in progress]: the Phase-2 shared
simulator was quarantined after the restart audit found that later sessions were resolved from
`factory/artifacts/basket/sip/bars/YYYY-MM-DD.parquet` (a candidate-neighborhood slice), so a
held ticker absent from the next day's candidate net was treated as "did not trade". Concrete
negative evidence: HSDT halted 2022-03-23 (candidate tape ends et=942) actually traded
2022-03-24 (full-market first bar et=570, open 3.32, last et=940), yet the pre-C1 engine carried
the pending exit to 2022-04-26, the next candidate appearance (recorded in
`factory/artifacts/basket/phase2/F1/canary_recheck/canary_report.json`). Coupled defects:
`Strategy.open_tickets` keyed by bare ticker (a carried name could suppress a new same-ticker
sleeve), `deployed_end` reported lifetime `cash_in` instead of current cost basis, and
`half_release_rate` counted any single REDUCE as a >=50% reduction.
Correction C1 (declared and dated in `factory/BASKET-SIM-CONTRACT.md` §13): ticket identity is
`(sleeve_day, ticker)`; cross-session actions resolve from a declared full-market carry overlay
(`sip/carry_bars/`, per-day parquet + manifest with `bars`/`no_bars`/`unavailable`, Alpaca SIP
RAW 1-minute bars, sha256-verified before use, fail-closed when uncertified); actions, exits and
touches carry their session date so chronology is a total order; `deployed_end` = open cost
basis; `half_release_rate` = cumulative pre-touch REDUCE shares >= 50% of pre-touch peak shares.
Implementation fixes folded into the same correction (contract §13.9): terminal marks
(`BLOCK_BOUNDARY_MARK`, `DATA_END_MARK`) no longer count as exits/turnover; raw MFE/MAE and
+30/+50/+100/+200 first touches are tracked from the ticket's whole session tape after the event
loop (a release no longer truncates tail cohorts); same-et executions apply in ticker order;
`deployed_avg` is minute-weighted; daily output carries action records; resume/complete trust is
bound to contract + engine-source hashes (pre-C1 progress is refused).
Substrate provenance: `factory/scripts/basket_carry_bars.py` fetches targeted Alpaca SIP/RAW
minute bars (window 09:25 ET..session_end+5), retries, resolves every omitted symbol
individually (empty success = `no_bars`, failed request = `unavailable`), refuses sealed/reserved
days, and stops at dev-block boundaries. Coverage currently 1,063 days / 44,000+ certified
ticker-days, internally audited (0 `bars` entries without rows, 0 rows without certification,
all day-file sha256s matching the manifest). The Finnhub-derived first attempt is quarantined
under `carry_bars/_quarantine/` and must never be used. F1/F3/F4/F5 results produced before C1
are not comparable and are being re-run; the first corrected cells (B600 N2 R0/R1m8) show only
+11..+19 bps mean basket-day versus the old values, i.e. the qualitative negative conclusion is
unchanged so far. New families on the corrected engine: F6 staged reserve capital
(`basket_f6.py`, EXT-1 batch hook declared in `factory/BASKET-SIM-EXTENSIONS.md`) and F7
release recycling (`basket_f7.py`); neither has been run yet.


2026-09-24 (later) — PHASE-2 DIAGNOSTIC PASS [RUN]: corrected-engine results for the capital-
sequencing hypothesis. All numbers are development evidence on the 1,066 canonical days with the
C1 simulator (canaries 7/7 PASS), 100/150 bps round trip, month-blocked blocks reported.
Corrected F1 (120 cells, `phase2/F1_C1`): every cell still negative, means -4.70%..-2.04%
basket-day at 100/150 bps; C1 moved cells by -9..+51 bps (mean +16 bps) vs the quarantined pre-C1
surface (`F1_C1/comparison_vs_pre_c1.json`) - the carry defect changed levels, not the verdict.
Corrected F3 (20 cells, `phase2/F3_C1`): all still negative (-4.41%..-2.84% at 100 bps); the
release-rule ordering survives (R2(L10,w5) best at -2.84%/day with failed-ticket cost -11.7% vs
R0 -3.94%/-15.7%); C1 shift +7.5..+18 bps (`F3_C1/comparison_vs_pre_c1.json`).
F5 scale-in diagnostic (48 cells: A_pm/B600, N 2/3, R0/R1m10, schedules []/[50]/[50,25];
`phase2/F5_DIAG`): 0 of 32 add cells are positive in both blocks. Structural finding: in a fully
deployed sleeve (R0) most add signals are skipped as `add_unfunded` (e.g. 2,024 of 3,677 signals
in one cell), because the sleeve holds no free cash; executed tranches are few and their EV per
deployed dollar is ~0 to -2%. A strict own-ticket new-high ADD is not a promising marginal-dollar
mechanism as formulated.
F6 mechanism decomposition (A_pm, p=0.50, ET585/600, R0; `phase2/F6_DIAG2` + `F6_COMBO`), which
separates the three questions: (1) WITHHOLDING capital alone is pure de-leveraging - per-deployed-
dollar EV is unchanged (p=1.00 -3.94%/day on 1.0 deployed; p=0.50 cash -1.97%/day on ~0.5
deployed); (2) INDISCRIMINATE redeployment is negative in both blocks: -2.2..-3.2% per dollar
deployed (equal split across eligible survivors, 462-469 notional units deployed); (3) INFORMED
redeployment (reserve only to survivors still up on the day and still holding >=2/3 of their
running MFE, `state67`) recovers almost all of that loss: -1.5% per dollar at N=2, +0.3% at N=3,
with block 2 positive (+1.2%, +6.0%) and block 1 negative (-2.7%, -2.1%) - i.e. the state
conditioning carries real information about where the next dollar should NOT go, but no stable
positive incremental EV. Combining an F3 release rule (R2 L=10,w=5) with the reserve arm
(`F6_COMBO`) gives +2.6% per dollar (N=2, 35 adds) and -2.5% (N=3, 52 adds) with opposite block
signs - not a demonstrated edge. Two structural limits stand out: the ADD cap (+100% of unit
notional) binds for most reserve allocations, and only 9-57 notional units are deployable over
1,066 days at p=0.50, so even a positive rule would move the strategy by a few bps per day.
F7 (recycling) is implemented (`basket_f7.py`, EXT-1 hook) but NOT run: the precondition it
needs - positive continuation EV of a surviving original at the moment another ticket fails - is
the same quantity that the F6 arms measured as approximately zero, so recycling is not yet
justified. F4 corrected rerun (74 cells) is in flight; no F4 read yet.


2026-09-24 (evening) — WHERE THE MONEY IS NOT, AND THE FIRST ALPHA-SHAPED LEAD [RUN]: three
small corrected-engine diagnostics locate the day's economics for the A_pm top-N population.
(1) SEGMENT (`phase2/SEGMENT_DIAG`): exiting everything at the first bar after ET580 (09:40)
returns -1.48%/day vs -3.94% for holding to the close at N=2/100bps (+246 bps), ET600 -2.49%
(+145), ET630 -2.68% (+126); N=3 shows the same ordering. Dividing by time-weighted deployed
capital shows the loss per unit of exposure is WORST in the first 30 minutes (09:30-10:00) and
slowly negative afterwards - there is no positive intraday segment to hold for; the sleeve bleeds
from the fill.
(2) DE-RISK (`phase2/DERISK_DIAG`, full deployment): cutting 50% of every position at the
completed ET600 bar gains +73 bps/day at N=2 (+83 block1 / +49 block2) and +56 at N=3, both
frictions; cutting only the "damaged" subset (ret_from_fill <= 0 or retained MFE <= 1/3) gains
+49/+42 bps, and a tighter subset +31/+30. The unconditional cut beats the state-conditioned one,
so the gain is exposure removal, not state information.
(3) HARVEST (`phase2/HARVEST_DIAG`): selling 100% at the first +30% MFE touch (next-bar-open
execution) gains +89 bps/day at N=2 (+34 block1 / +211 block2) and +66 at N=3; 50% scale-outs at
+30/+50/+100 gain +45/+20/+11. Earlier selling is monotonically better.
The decisive measurement behind all three (`HARVEST_DIAG/touch_fade_paths.json`, direct path scan,
A_pm top-3 anatomy fills over 1,066 days): after a first +30% touch (582 tickets, 470 days) the
median ticket gives back -10.8% from the touch-bar close to the session close (mean -2.2%,
positive share 30.6%); after +50% (297 tickets) median -12.4%, positive 29.6%; after +100% (105)
median -13.6%, positive 36.2%. The excursion is a local maximum that fades: the right tail is a
TOUCH phenomenon, not a HOLD phenomenon, and the median toucher surrenders ~11% of it.
Consequences: the measured "right tail" cannot be monetized by holding; it must be SOLD INTO
(ideally with a resting limit sell at the level, the same execution shape the live H025 lane
uses). The non-touching majority (about 82% of fills never reach +30%) is where the remaining loss
lives, and it is incurred at entry and in the first 30 minutes. Neither the F6 staged-capital
decomposition (withholding = de-leveraging; equal redeploy -2.2..-3.2% per dollar; state-conditioned
redeploy -1.5%..+0.3% per dollar with block sign flips) nor the F5 new-high add lane (+0 of 32 cells
positive in both blocks) survives as a marginal-dollar mechanism. F7 recycling stays unrun: its
precondition (positive continuation EV of a survivor) is exactly what the fade measurement refutes
for the post-touch path. Next branch, in order: (a) resting-limit sell-into-strength harvest with
executable level pricing (needs a small favourable-price exit convention, EXT-2) and staged
levels; (b) entry-side cost reduction (later entry / limit entry into the morning flush), since
the 09:30 fill is the worst price of the day for this population; (c) only then re-test capital
sequencing on top of a non-bleeding base.

## 2026-09-24 (C1 cycle, swarm session) — placebo decomposition, engine fingerprint fix, panel defects

(1) TIME-MATCHED IDENTITY-SHUFFLED PLACEBO (`phase2/PLACEBO_DIAG`, producer
`factory/scripts/basket_diag_placebo.py`, three shuffle seeds per N). The harvest arm's own realized
exit-time multiset is shuffled within day across that day's exiting tickets, so time-in-market is
matched (held minutes within 2%; 2,122-2,123 of 2,124 scheduled exits fire) and only touch identity
is removed. Placebo delta vs hold: +0.159/+0.233/+0.370 %/day at N=2 (harvest +0.894) and
-0.144/+0.158/+0.243 at N=3 (harvest +0.661). So the "be in the market less" channel is worth
roughly 0-40% of the harvest gain with sign instability across seeds and N; the bulk is the IDENTITY
of the tickets that exit (selling at a locally high print), i.e. the state information the harvest
claim adds. This kills "exposure removal" as the primary mechanism and keeps "sell into the
touch" alive as a state mechanism - but note the harvest delta itself is fragile
(month-blocked CI95 [-20.3, +183.9] bps/day; block1 t=0.48 vs block2 t=2.31).
(2) `avg_deployed_capital` is NOT a pure same-day exposure measure: placebo vs harvest differ 18%
(1.015 vs 0.857) while held minutes differ 2%, because closing tickets also removes their basis from
later days' carry accounting. Every per-deployed-dollar ratio must use the held-minutes integral
instead, or state the carry composition.
(3) ENGINE FIX (truth-critical, `factory/scripts/basket_sim.py`): the run fingerprint bound only
`rule.name`, so two runs sharing a run id but differing in rule PARAMETERS shared a fingerprint and
the second silently returned the first's cached summary (three "different seeds" produced
byte-identical numbers in ~1 s each). `ReleaseRule`/`ScaleInRule` now expose `signature()`
(default `name`); the fingerprint uses it and a parameterized rule must override it. Verified: same
run id + different seed now recomputes; no change for existing frozen rules (default is the name).
(4) PANEL DEFECTS (verified): `basket_score_disp` is NaN in all 182,094 F2_F12 rows
(`basket_f2_f12.py:119` reads `x["sel"]` from the `{"name","bars"}` wrappers instead of
`x["name"]["sel"]`) and `spread_state` is hard-coded NaN; the capital map lists both as features, so
their "no relationship" readings are vacuous. `rank_change` is not an adjacent migration and
`rel_strength` is a return residual, not a peer-relative MFE.
(5) INTERPRETATION GUARD: a carried ticket certified `no_bars` can never execute its forced-flat
pending, so its cost basis keeps counting in `deployed_avg` (phantom exposure; ASPA 2023-10-25,
GATE 2025-04-01 in the A_pm hold arm). Smallest fix: terminalize after N certified `no_bars`
sessions as NO_RESUMPTION_MARK, or report deployed excluding frozen carries.
(6) The engine's next-bar-open exit is ABOVE a resting limit at the level (mean +85 bps on the 582
+30 touchers; 53.4% of fills above the level) - the convention is favourable, not conservative;
`exit_convention_comparison.json` framing corrected in place.

## 2026-09-24 (same cycle) — swarm complete: F8 stale-input defect fixed, joint surface regenerated

(7) TRUTH-CRITICAL (found by the swarm, fixed): F8 and the capital-allocation map were built from
the **pre-C1** F1 tree. F8's input configs say `FROZEN-2026-09-22` while the engine is
`FROZEN-2026-09-22+SUBSTRATE-CORRECTION-2026-09-24`, and F8's A_pm_N4_R0_bps100 mean was exactly the
pre-C1 value (-0.0308568487 vs C1 -0.0293282347). `basket_f8_joint.py` validated family/dates/entry/
N/bps/release but NOT `contract_version`, so the stale tree was accepted silently. Fix: the per-cell
check now refuses a contract mismatch (verified it raises on the old tree); the loader accepts the
corrected tree's per-entry `surface_*.json`; provenance hashes the actual sources; `--f1/--out` added
so one producer serves both generations. `F8_C1` regenerated from `F1_C1` (120 cells, 1,066
days/cell, 127,920 rows) and its C0 mean matches C1 exactly. Joint rates moved <=0.5pp
(p_ge2_reach_30: A_pm N2 3.94%->3.94%, N4 15.76%->16.23%, B600 N3 6.29%->6.47%), so the
multi-survivor reading is unchanged but the provenance is now correct. The capital map still hashes
the pre-C1 F8 context and must be regenerated from `F8_C1` before its F8 columns are quoted.
(8) Multi-survivor geometry (canonical SIP, main top-3, 1,066 days): days with >=2 touches at +5%:
A_pm 801, A_open 737, B600 720; +10%: 531/480/448; +30%: 103/82/67; +100%: 3/4/1. Conditional on
>=1 +30 touch the >=2 share is 21.9%/19.8%/15.9%. Breadth is real at +5/+10 and rare at the
harvesting ruler: the +30/+100 tail is predominantly one giant plus co-members. Rank-1 concentration
is hindsight (T5 MFE ranks and T7 best-member economics sort outcomes after the fact; no causal
100%-rank-1 arm exists); handoffs are unmeasured (T1 top-3 overlap is set churn, `race_by_view` is
per-ticket).
(9) Intraday shape ruler (A_pm top-3, n=3,187): mean return -1.24% at 10:00 -> -2.62% at 15:00,
median -2.41% -> -5.32%; non-touchers bleed all day (-5.06% -> -9.29%), touchers gain all day
(+15.88% -> +27.36%). False-cut accounting: of 1,928 early-down fills, 123 touchers (6.4%) end at
+35.4% mean (median +22.5%, 77.2% positive, aggregate +43.5 return units), so a blanket 10:00 cut is
worth ~+1.27% per ticket and a state-conditioned (early-down only) cut only ~+0.73% - reproducing
DERISK's ordering from raw paths.
(10) Giant-runner anatomy (swarm): a fixed +30/+50 exit eliminates 100% of every MFE>=100% band
(A_open 92/92, A_pm 106/106, B600 86/86, B615 73/73); named giants peak 179-234 minutes after entry
with -19..-25% pre-high retracement and -28..-38% post-peak giveback; 8/10 named >=100% tickets were
materially better held (exceptions WNW, KELYB). Two shapes exist (smooth vs interrupted reopening),
so there is no single halt signature. Preserved as a disagreement with the "sell into strength"
framing: the +30 arm harvests the fade majority's mean while forfeiting the convex tail.
(11) Thesis reconstruction (swarm): all 120 corrected F1 cells, all 20 corrected F3 cells and all 54
corrected F4 partials are net-negative; twelve rulers drifted into apparent constants (+30%, 10:00,
N=3, T=600, L=10/g=50, staged capital, hold/sell conclusions); the load-bearing claims never tested
in their stated form are listed in `researches/SWARM-SYNTHESIS-20260924.md` section 2.9; the T10
"LS materials" contract is unimplemented (aggregate counters only). Stale BASKET headers in
`researches/HYPOTHESES.md` / `researches/STATE.md` corrected this cycle.

## 2026-09-24 (same cycle) — doctrine correction: THE WINDOW; and an existing learned BASKET component found

(12) DOCTRINE (owner directive, written into the injected context files): **EOD is not the value
horizon.** The phenomenon is an early-session explosive-attention event; the climb and climax are
concentrated in the morning-to-midday hours and the afternoon is the relaxation phase where these
names die or return to base camp. Any forward value defined "to the session close" measures the fade
and is biased against the thesis by construction. Written into `researches/INTENT.md` (new section
"THE WINDOW"), `AGENTS.md` (operating rules) and `factory/AGENTS.md` (core claim), and reflected in
`researches/PLAN-ATLAS-01.md`, which now estimates continuation value over a horizon grid
(5/15/30/60/120 bars) and makes the *time profile of when marginal continuation EV dies* the first
object of study. Grounding: ≥60% runners complete half their open-to-high move by 11:25 (H019);
corrected T5 puts the peak of MFE≥100 main tickets at ~180–234 minutes after entry with −28…−38%
post-peak giveback; the A_pm sleeve's mean is flat after 11:30 while its median keeps decaying.
(13) EXISTING LEARNED COMPONENT (found during the audit; lives in the MAIN checkout, not on this
branch): `factory/scripts/basket_f2_predict.py` + `factory/artifacts/basket/phase2/F2_golden/`
(both UNTRACKED on main, i.e. another session's uncommitted work). It is a hand-rolled numpy
logistic regression on 6 causal features at checkpoints 585/600, time-blocked validation only,
targets future +50%/+100% from the checkpoint price. Out-of-block AUC **0.658–0.751** for +50%
(base rate ~0.067), Brier skill +0.017…+0.044 vs train base rate, monotone calibration deciles; the
carriers are `mfe_so_far`, `ret_from_prevclose`, `cum_volume`/`dollar_volume`, `basket_breadth_*`
and `rel_strength`. So causal path state *does* carry out-of-block information about the tail — but
it was never converted into an action policy, and its label is a barrier (opportunity), not
executable dollars per committed dollar. This is the closest existing object to the state→action
atlas and it should be read before building a new one.
(14) ENGINE ACTION SPACE (verified): entries happen once at `entry_T` (`basket_sim.py:1111`, budget
`C0*reserve_frac/N`); available actions are ENTER/ADD/REDUCE/EXIT; the batch hook can request **ADD
intents only** (`basket_sim.py:844-858`). There is no mid-session entry, no re-entry after exit, and
no per-name entry veto. The buy side of any learned handling policy is therefore structurally
untestable today — the sell side and adds are expressible, nothing else.

## 2026-09-27 — ATLAS Phase 1 CLOSED (measurement); Phase 2 open on E1
Frozen this cycle (all committed on basket-phase2-f1): panel v2 (SIP, 1,066 dev days, 25,788
runner-days, 1,900,432 state rows, 100% SI coverage 2017+, IDs zero, reserved 2026-06..08
untouched); ledger v2 (same-bar forward violation; primary next-bar PATH_RETURN on
complete-path runner-days; hold/giveback:10/peak_pct:0.03-0.10/timestop; dual-block,
top-5-day-removed, day-clustered); window v2 (09:30-11:30 sleeve + dedup); fall v2 final sha
19e98a19...; pairs v2 final sha ac85cb72...; readable conclusion
researches/ATLAS-PHASE1-SYNTHESIS.md.
Key results: state strongly predicts forward dispersion (within-clock AUC 0.67-0.74); the
exhaustion barrier is a real, weaker, dispersion-independent score-level signal (like-for-like
+0.038/+0.044/+0.037/+0.009 over hazard; within-decile 0.675-0.702); per-decile coefficient
allocation is UNIDENTIFIED (mediation claim withdrawn); no directional separator; the honest
two-sided matched-pair test is adverse (higher range -> lower executable value later).
Independent audits: panel/ledger/window/fall/pairs all audited; text-level defects fixed;
remaining limits published in each artifact (shares, thin cells, idempotency).
Ruler honesty: the in-sample price-perfect table is unattainable (q10 rows too), so execution
tests are judged against the in-sample estate table as an oracle-CONTAMINATED ruler, never a bar.
Open: E1a cross-fitted exhaustion-score release test (builder running); F4 microstructure plan
drafted; SIP 2022-04-01..19 stub (19 days) + fold-2 vs post-2022 family split explained;
blocks 0-3 = 2017/2021/2022-2026 (2022-03-25+ folded into block 2 by design).

## 2026-09-27 (audit detail, fall v2) — independent delta audit of the fall rewrite
Diff (d33e321 -> 3071b65): all pre-existing published numbers bit-identical except the intended
fixes: 10 thin flags 0->1 (5 decile cells), 4 flat-share values, and the 16 deleted
attenuation_calmest_over_wildest numbers. Two semantic notes worth keeping: (a) the key
direction_label_exactly_flat_share_of_the_nonpositive_class previously held the ALL-finite-rows
share; the rewrite moved those values verbatim to ..._of_all_finite_rows and published the true
class-denominator value under the original name; (b) d33e321's committed artifact was c1ad6f04,
not the final-run cc100d (which was still uncommitted when audited) - the committed final is
19e98a19 (3071b65); the rewrite path's refuse-to-write guard plus this diff bound the change.

## 2026-09-28 — E1 CLOSED: no score-based release increment; the E1b niche was a look-ahead artifact
E1a (corrected every-bar score, inner-cross-fit thresholds): primary + all three quantile rulers
SURVIVE the pre-registered kill (positive vs hold) but are beaten on dollars by giveback:10 in both
folds; the event-hazard ablation family is KILLED in fold B by the giant-tail clause. E1b's apparent
conditional niche (top predicted-dispersion tercile) was produced by a member-median regime
statistic that reads post-release bars: 87-99% of t2 members are t2 only because of post-decision
bars. With a decision-time (trigger-bar) tercile, t2 dedup increments are NEGATIVE in all 16
arm x fold combinations (dollar -3.498 A / -16.454 B; q70 -1.022 / -14.981; q80 -16.271 / -26.130;
q90 -31.581 / -38.052). Independent debugger (E1bDebug) found this; artifact is being republished
with the causal statistic and closure statement. Standing state: management-by-score adds nothing
over the trivial peak-relative ruler; the trivial ruler remains the strongest release policy tested.

## 2026-09-28 (E1b verification, corrected artifact) — residual boxes: not promotable
Independent re-verification of the corrected artifact (672/672 compared fields match; control cells
exact). Calm-tercile box (primary arm t0 +9.31 A / +21.19 B) is numerically real and stable to
tercile edges, dedup, friction, and day removals - but the arm's own net inside the box is NEGATIVE
in both folds (the entire increment is the ruler losing in calm states) and the pre-registered tail
clause fails (both ratios 0.0 vs 0.0); the three clock-quantile arms' calm boxes are negative in
both folds. The ablation family: cell-level fold-B tail kill confirmed; per-tercile, two boxes
(q80/q90 B t2) have the arm below control with the biggest positive increments (56.4/55.7), but that
is a post-hoc slice of an arm that already failed its pre-registered cell-level tail test. Decision:
E1 stays CLOSED; no rescue pre-registration, no look at unused months. The look-ahead guard is a
real regression lock (old member-median statistic fails it) but is specific to the single
decision-bar read, not a general causality proof.

## 2026-09-28 — ATLAS Phase 2 CLOSED (E1 management + E3 sub-minute); synthesis file added
E1 closed: every-bar exhaustion score (AUC 0.82-0.84) is profitable vs hold but never beats the
trivial giveback:10 ruler; the conditional niche was a look-ahead artifact (member-median
conditioning read post-release bars; decision-time statistic negative in 16/16 cells); the
calm-tercile box nets <= 0 and fails the tail clause. E3 closed null: 20,937 frozen windows, I-EV
no family positive out-of-block in both blocks for CS-1..CS-4; CS-5 weak-only (<=0.0055, inside the
0.054 band); nothing promoted; L-EV blocked by precondition. Readable closure:
researches/ATLAS-PHASE2-CLOSURE.md. Every verdict-carrying artifact has an independent debug pass;
all runs byte-reproducible. Standing: only validated executable mechanism remains the H025 flush
rule (separate workstream); the peak-relative ruler is the strongest management policy tested.

## 2026-09-30 — Tape Atlas resumed; SIP net index repaired
Research center: `researches/PLAN-TAPE-ATLAS.md`; observation before geometry, geometry before
future anatomy, economics only afterward. Prior E1/E3 negatives constrain their tested
formulations, not raw sequence/retrieval/race discovery.
Actual local canary was a first run, not a completed final deterministic chain; no `verify.json`
survived. Partial gate work recovered. Core and quote-channel readiness are now separate.
Verified B6 repair: existing raw day files/manifests for 2026-05-21/29 were intact; the derived
net index omitted six entries and contained forty stale entries. Rebuilt atomically from
per-day manifests: 3,198 -> 3,204 entries, sha256 `446090fd9f60524934a500504a1f73fa2a92d01daf68292f634dd8a171be42b6`.
Real `atlas_net_index_reconcile.py --stage repair` and `--stage verify` both exited 0;
no fetch, no market-source mutation. B1/B2 acquisition and final canary closure remain in progress.

## 2026-09-30 — Real SIP repair scopes complete; cross-feed witnesses preserved
Actual API acquisition and full-scope verification completed: B1 19/19 February-2025 days in
`data/atlas/acquisition/v4`, B2 41/41 April/May-2026 days in `v3` (reused, not refetched).
Published tracked joint admission under `TAPE/OBSERVATION/v0/acquisition/evidence`; zero
missing/stale scopes and zero unresolved same-feed gaps. Origin producer/contract snapshots,
per-day raw payloads, manifests and rosters are SHA-bound. Market rows remain gitignored.
Real defects caught before admission: inclusive endpoint mismatch; one-day subsets wrongly
claiming full scope; missing historical `asof` losing BK/ARMN; failed batches mislabeled zeros;
month-wide planning OOM; and conflating third-party proxy bars with SIP bar obligations.
DXR on 2025-02-03: SIP has 24 trades / 501 shares but no minute bar, while HF/Finnhub has one
unreproduced 12:55 bar (7.78, 124 shares). Absence of a bar is not absence of trading.
General feed-aware policy preserves four cross-feed disagreements (BYNO, DXR, FTII, VISL) as
original proxy witness rows under the acquisition root; none is invented or relabeled as SIP.
The fresh API canaries and entire scopes verify; final observation canary and full corpus remain
pending. No retrieval/discovery result or alpha claim has been produced.

## 2026-09-30 (later) — observation corpus: repairs SOLID, canary NOT verified, aborts = kernel OOM

Tape Atlas observation lane (centre `researches/PLAN-TAPE-ATLAS.md`; producer
`basket_tape_atlas_observation.py`; evidence `TAPE/OBSERVATION/v0`). Commands: PLAN §10.1.

**Verified and committed.** `8f2ff07` rebuilt the net manifest index from the per-day manifests
(3,198 → 3,204; 6 restored, 40 stale dropped; sha256 `446090fd…`), repair+verify exit 0, no source
mutated. `dcefbd5` published the joint acquisition admission (B1 19/19 Feb-2025 `v4`; B2 41/41
Apr/May-2026 `v3`; sha256 `eaf08243…`): `all_scopes_ready`, both `state: ready`, zero missing/stale
scopes, zero `residual_confirmed_trading_gaps`, rehash `mismatches: []`. Blockers B1/B2/B5/B6 bind to
real evidence **files** (B5 = measured storage `ok: true`, 112,609,935,360 free bytes via 9p; B6 = net
reconciliation `a2e77e13…`). B3 is a sibling quote lane; no quote feature ready.

**Provisional / NOT verified.** No `verify.json`; the tracked `canary/summary.json` is the pre-repair
artefact (old id `B6_missing_net_manifest_…`, `full_v0_ready` false, determinism null); bookkeeping is
117/120 layers with 2025-03-03 not re-hashing, so a `--force` rebuild is required. Run 1 built all 20
days then exited 1: `day_registry` built its 1,066 day dicts under polars' default 100-row inference,
so acquisition strings inferred Null for the first ~1,006 dev days and the first acquisition day
raised `ComputeError`; fixed with full-length inference (the registry builds and 60 acquisition days
resolve). No signal/OOM (peak RSS 4.71 GiB, pre-pin ÷10⁶ conversion), no new
manifest/summary/costs/selftest, and the source changed mid-run ⇒ **run 1 is smoke-only**. Producer
re-frozen at `32a1d082…`; owner on hold. Next: lock re-freeze, then a fresh A/B run, `verify-canary`
and audit. *(All superseded by the two sections below: the canary went GREEN at pin `f9346e6b` and the
full 1,066-day corpus went GREEN, `core_full_ready: true`; this paragraph records the pre-closure state.)*

**Corrections this cycle (source-only).** PIT fixed-width padding of `ECC`/`ETX`/`SAND` corrected
injectively (5,535 → 5,535 distinct, collision-refused) → Feb-3 closes at 0 unresolved (17
roster-unavailable + 50 API zeros + 1 DXR witness); `entry_et` `agg(min)` emitted `entry_et` while the
comprehension read a non-existent `first_entry_et` (green pure checks hid it) → explicit alias, proved
by a real single-day build of six layers; B6 id is now `B6_unreconciled_net_manifest`; evidence must
resolve under the tracked root, re-hash, and be the consumed file. **Lock NOT valid**: of its five
hashed files only `schema.json` is stale (disk `990542110104…` vs locked `868fd1df…`); one
`--stage contract --force` re-freeze is authorized. The schema edit adds exactly two `day_registry`
coverage fields (`raw_n_pit_symbols_full_window`, `raw_n_pit_offhours_only`) and changes no market
payload — only `day_registry.parquet`/`coverage.parquet` differ, so runs A/B must be identical.
**`RTH_LO` CLOSED**: the board stays RTH-trimmed and no clock moved, but coverage now derives from the
untrimmed declared `[565, 965]` window, after the real NTZ counterexample (2025-02-03: one bar at
16:01, 137 shares, counted as missing; 54 B1 / 173 B2 off-hours-only name-days).

**Open.** No full 1,066-day corpus; no outcome-blind inspection (PREPARED-NOT-RUN); Freeze R not frozen
(DRAFT-NOT-RUN); no nearest-100 proof, no SSL run, no race tiers; geometry/sequence/race producers
prepared-only. Nothing here is a retrieval, discovery or alpha result, and there is **no old
`HistoricalBarsRepair` blocker** (that lane is closed). *(Also superseded: blind Stage-0C inspection, Freeze R binding, all three race tiers and EV-01 have since landed — see below and the 2026-10-01 commits.)*

**Aborts = kernel global OOM** — three kernel kills of `python` at 11:01:07 / 12:02:46 / 13:25:03
(anon RSS 9.05 / 10.35 / 7.58 GiB), no agent-initiated termination, victim command lines
unrecoverable; forensics `local://wsl-interruption-investigation.md`. Child cgroup cap unproven/not
adopted; `.wslconfig` now 16 GiB / 32 GiB swap.

**Ops rule.** One heavy owner, strict serialization, cooperative stop only. Ops holds the `>= 10 GiB`
cold start; the script gate is one shared calc — `cap = floor(min(8.0 − current parent VmRSS,
MemAvailable − 2.0 GiB) / 5.0 GiB child)`, and cap 0 refuses (the 6.0 GiB figure is contract text
only; the parent term is current VmRSS, not lifetime peak). Peak RSS now uses exact ÷2²⁰ GiB (was
÷10⁶, a +4.86% over-report) ⇒ peak figures must not be differenced across that pin. Nothing committed
this cycle; the H025 paper lane is untouched.

## 2026-09-30 (night) — Freeze-O observation canary GREEN at adopted pin f9346e6b
Chain closed after four full 20-day canary runs (strictly serial, no signals; A3 run was smoke-only
against a superseded predecessor). B3 vs A3 (same generation 58bbbf0c): 50/50, 125/125 payloads
byte-identical. Throughput patch adopted (UTC physical-timestamp row-group pruning conjunct inside
read_raw_days; admitted-row set provably identical): C and D both 50/50 at f9346e6b, 0 differing
payloads of 125 vs B3 (CROSS-generation identity) and C vs D (two same-generation runs); `--stage
verify-canary` exit 0, 43/43, core_hash ffd513ce…; source pin constant 21/21 samples in C and D.
Measured effect: wall per 20-day canary 35:00 → 17:15–18:45 (~2x), peak child RSS ~5.07 → ~3.2 GiB.
Coverage defects closed on real data (NTZ 2025-02-03 etm 961/16:01 observed; 54 B1 + 173 B2
off-hours-only name-days → 0 unresolved; no spurious B2 append). Independent audit: coverage,
negative controls, lock PASS; provenance items closed by C+D+verify (documented gap: verify.json
carries core_hash but no explicit code pin). Full 1,066-day corpus launched after commit.

## 2026-10-01 (early) — FULL 1,066-day observation corpus GREEN (pin f9346e6b)
`--stage full --workers 1` on producer f9346e6b finished 20:37→05:32 local (8h54m47s): 1,066/1,066 days
(20 canary days resumed, 1,046 newly built; mean 27.65 s/day), payloads 50,127,241,317 B, manifest
core_hash 1bda734b…, `full_v0_ready: true`, `core_full_ready: true`, selftest 50/50 (`all_ok true`),
source pin constant in 824/824 boundary samples and re-hash-identical at the end. Peak worker RSS
4.364 GiB (under the 4.5 line); MemAvailable min 8.64 GiB; swap untouched; physical free 59.58 GB
after (10 GiB operating reserve held). Independent closure at full scale: B1 off-hours-only = 54,
B2 = 248, `unresolved = 0` on all 60 acquisition days, zero B2 blocker appends, conservation exact.
Emitter gaps to fix later (no source touch tonight): full manifest `status=canary_built` and
`canary_days` listing all days; verify.json carries core_hash but no code pin.

## 2026-10-01 (late) — CV01: two dev-only A/B diagnostics AUTHORIZED (after EV-01; A/B DONE, audit PASS)

User authorized TWO dev-only diagnostics runs A/B (not TestC, not a model); parent contract `factory/artifacts/basket/phase2/ATLAS/CV01/contract.json`. The authorization came **after EV-01**, never before it. Readable readout: `factory/artifacts/basket/phase2/ATLAS/CV01/report.md`.
**Results (dev anatomy, not a policy; h = ET minutes):** A day × family balanced CV h60 −0.438% / h120 −0.560% / h240 −0.805% (occupancy −0.449 / −0.708 / −1.138%), h0 exact 0, 1,888,885 clock-eligible decisions + 185 unknown liquidations, worst bucket 09:30–10:00 (h60 −1.27%, easing to −0.25% at 11:00–11:30), no EOD primary horizon. B: 5,165 GB10 sales (4,946 complete + 219 censored), 4,793 re-entries, 1 unresolved; retaining beats this re-entry rule over the reported horizons h1–h240 (B−A −0.74 / −0.81 / −0.73 / −0.66pp at h1/30/60/120, ≈5–16 t) and **both branches fall below cash from ~15 minutes on**. No claim is made beyond the reported horizons (thin support late).
New primitive CV = W_hold/W_exit − 1 over the current next-open liquidation-$ baseline (entry cost sunk; sell fees common, cancelling); arm B = first giveback-10 sell → completed close ≥ GROSS actual exit price → STRICTLY later next-open buy.
One cycle only: no outsiders, no sizing, no reserved months, no sub-minute, no models; states past-only, incomplete partial horizons kept UNKNOWN (never 0), giant attribution never state.
**EV01 is RETROSPECTIVE ANATOMY, not causal alpha** — it reads 30/60/120 objects later than the +1/+5/+30 decisions plus the whole future-N universe, so its earlier PASS is statistical separation, not actionability.
Owners: SharedValuationBuilder / ContinuationAnatomy / OwnershipContinuity / CurrentDollarAudit. No profitable claim; a failing management family does NOT kill Atlas or race admission.

## 2026-10-02 (night) — HARVEST01: multi-clock top-gainer basket grid + handling families (dev discovery)

Owner directive: back to the original basket thesis — actual top 2/3/4 gainers at fixed PM/RTH clocks, equal initial dollars, next executable price; understand the paths and the golden window; run many handling variants (release/partial/re-entry/rotation/redistribute/reserve/scale-in/dip-buy); use dev data freely, keep reserved months untouched.
Substrate built and validated this cycle (contract `factory/artifacts/basket/phase2/HARVEST01/contract.json`; ledger `.../HARVEST01/LEDGER.md`; Phase-1 recap `.../HARVEST01/PHASE1-RECAP.md`):
- New lane `basket_pm_snapshots.py`: broad PIT-universe PM minute fetch compacted to causal decision px + first-open at 08:30/09:00/09:10/09:15/09:20/09:25/09:29; canary reconciled EXACTLY vs the legacy premarket compact (8,818/8,818 rows); full 1,066-day acquisition running with resume + `--index`.
- RTH decisions reuse the local full-market race panel (`race.minute_full`; decision row t=C, px = close of last bar ≤ C-1) — no new acquisition.
- Corporate-action normalization (`basket_split_events.py`, 3,630 reverse splits in span): without it a sampled reverse split showed a fake +468% vs true ≈ −5%; displayed-change ranking now split-normalized.
- Selection lane (`basket_harvest_select.py`): 19 clocks × top-4 × variants raw/primary/listed, split-normalized, quality flags carried, day champions written per day; independent self-check recomputes top-4 naively and matches.
- Bars lane (`basket_harvest_bars.py`): per-day minute bars for selected + leader tickers; PM fill cross-check 0 mismatches.
- Unmanaged sim (`basket_harvest_sim.py`): entry = first bar open ≥ clock, gap ≥ 5 min ⇒ blocked slot = cash; exit = first bar open ≥ max(E, fill+1); EOD requires a print ≥ session_end else UNKNOWN; 50 bps/side (150 stress). Independently verified EXACT (45 cell fields + 12 fill rows + 81-cell sweep; max delta 2.2e-16).
- Handling engines: `basket_harvest_mgmt.py` (12 causal member rules incl. failed-recovery, damage+participation-decay, time-stop, partial, re-entry, replacement), `basket_harvest_policies.py` (36-policy grid), `basket_harvest_basket.py` (release→cash/equal survivors/best survivor/market leader + 1/3-reserve scale-in: strength/time/dip-buy; base_hold reproduces sim ret_100 exactly, max diff 4.4e-16), `basket_harvest_window.py` (birds-eye: peak ET, capture ratios, per-member top-out).
- Ops fixes: multiprocessing pools switched to `spawn` (fork+polars deadlock diagnosed via futex_wasait on hung workers); workers read the splits frame with its `factor` column.
Full-grid numbers are pending the gated chain (PM repair → selection → leaders → bars → sim → readouts → containment → mgmt → policies → basket → window → sub-minute probe → assembled REPORT.md). Canary-only textures (NOT conclusions): release-to-cash and dip-buys looked least-bad; redeployment into survivors/leader worst; re-entry/rotation underperform plain sell; basket peaks clustered 10:00–11:00 on the sample days. No promoted claim; no reserved-month use.

## 2026-10-02 — HARVEST01 discovery: basket-mark bracket is a friction-determined coin flip

New lane: rules on the BASKET's own mark path (equal slots, last close) instead of member
rules. Best causal strategy found: buy the funnel's rank-1 PM gainer at the 09:29 print,
take +3-4% (basket mark), market-stop -6%, flat by noon (`tp3_s6_t10_c720`).

* Hold bleeds -7.5%/day; the bracket realizes -0.29 (100 bps round trip) / +0.12 (measured
  ~50 bps round trip) at 569/N1, n=1066. Gross ~+0.7-0.8%/day; friction decides the sign.
* Exit decomposition (569/N1): tp 52% x +5.88 realized, stop 39% x -8.32, cap 8% x -1.15.
* Dead in this lane: stop-limits (no-fill risk), flush-bounce release of the basket,
  scale-out ladders (fade beats tail), pop/dvol re-ranking of the roster, day-volume
  allocators, stop arming delays.
* Regime split (100 bps): 2021 -0.80, 2022 -0.24, 2023 -0.82, 2025 +0.02, 2026 +1.39;
  filters on the true news gap (decision_gain >= 150%) give +0.42 overall but B1 -0.40 /
  B2 +1.17 -> no cell positive in both blocks at the conservative ruler.
* Friction measured: rank-1 fills print at the ask; quoted spread median ~50 bps (rank1),
  77-83 (rank2/3); inside size $27-126 for a $10k order; 09:30 auction = 09:29 print.

## 2026-10-02 — LIFECYCLE-01 causal rebuild + owner framing correction (window, not hold)

Owner asks behavior-first PM/near-open top-gainer life cycles, then a frozen chronological
evaluation. Corrected corpus: `data/harvest01/lifecycle/v2`, clocks 09:00/09:20/09:29/09:31,
fixed top-5; top-3 economic primary, ranks4-5 comparisons (rank-4 produced monsters too:
AUVI +152%, NRSN +208%, KALA +91% captured on the discovery half). Split locked: 533
discovery days 2021-02-01..2023-03-14, then 533 evaluation days 2023-03-15..2026-05-29;
2024/2025-01/2026-06..08 excluded. Panel 8,490,400 rows; tape (1-min + 5/10s) complete
for 1,066 days; quotes/sizes are round lots x100 before 2025-11-03.

The first lifecycle builder and its readouts are quarantined (wrong selection anchors,
clean-bar dropouts, repeated stale volume, later PM summaries in early states, mis-signed
drawdown, same-open observations, incomplete-horizon labels). Corrected producer
`lifecycle_build.py` v2.7 SHA `30c32bb2...`; tape `lifecycle_tape.py` 2.0.1 SHA `b839de39...`.

Discovery-half behavior map (533 days, corrected): monsters ~42-47 per clock at
09:00..09:31 (E[captured|monster] +109..+119%), duds ~50-55% of names (-11..-13%).
Continuation rulers are NEGATIVE beyond ~3 minutes at every clock (-0.3% at 30m, -0.5% at
60m, -0.8/-0.9% at 120m; occupancy = member-balanced). The positive cells are states, not
exposure: deep drawdown still actively repairing on expanding flow (+0.44/+0.57% at 30/60m,
112 days, P(+30% within 60m)=22.5%), and shallow pullback with a fresh high (+0.18-0.20% at
30m, 500+ days). Separation for a FUTURE +30% leg: recovery_from_low AUC 0.865, race_gain
0.839, rank inverse 0.220; chronological LightGBM rank-IC 0.043 price / 0.050 full /
0.078 tape (matched coverage).

**Owner framing correction (applied in code and readouts):** unconditional holding of top
gainers is a RULER, never a strategy; a session-long hold nearly contradicts the thesis.
The asset is the early-session WINDOW; the close is the opposite side of the phenomenon.
Action space is RELEASE / RETAIN-while-the-window-pays / RE-ENTER / CASH. All unconditional
baselines are labelled `benchmark_unconditional_hold*`; leg-map column is `continuation_ev`.
No second-half outcome evaluation until the full pipeline freeze; no profitable claim yet.

## 2026-10-02 (later) — LIFECYCLE-01 REANCHORED by owner; old policy pipeline cancelled

Owner reset the mental model (full text: `researches/LIFECYCLE-REANCHOR.md`, commit 87c205e).
The inefficiency is the early **option on the day's extreme winners**, not the average
member. The top-5 is an **optionality portfolio of competing claims** (rank-4 monsters are
real: AUVI +152%, NRSN +208%, KALA +91% captured). Unconditional hold is a **ruler, never a
strategy**; a session-long hold nearly contradicts the thesis; the asset is a finite
**window**. The decision object is **"where does the next dollar go right now"** across this
name / a sibling / an emerging outsider / partial cash / full cash / a resurrection — and
the objective is executable EV through the window with the **tail preserved** and the **dud
tax measured**. Ownership architecture (slots, weights, release, rotation, re-entry, window
exit) is a **discovery target to be proven**, not a simulator default.

Cancelled under the old framing: the `lifecycle_policy` discovery selection, the freeze, and
any second-half evaluation (no freeze exists; no second-half outcome has been read; no policy
number is a result). Kept: the corrected causal substrate, the tape layer, the behavior
anatomy, the leg/continuation rulers, the chronological model rank-ICs, measured friction,
strongest-path evidence, and the money/causal audits.

Redesigned discovery (first 533 days only): (A) per-member **personality trajectories** with
route types and **time-resolved separability** curves — when, and through which combination,
routes become distinguishable; (B) a **next-dollar map** including siblings, outsiders and
re-entry; (C) **ownership-architecture search** judged on EV + tail preservation + dud tax at
measured friction; (D) freeze and spend the clean half once.

## 2026-10-03 — LIFECYCLE-01 reanchored discovery: first honest results (second half untouched)

Substrate complete and audited: 1,066 days, panel 8,490,400 member-minutes, tape 1-min + genuine
5/10 s, split locked 533/533, reserved months excluded. Full reanchor text and the standing
anti-drift checklist: `researches/LIFECYCLE-REANCHOR.md`, `researches/LIFECYCLE-REANCHOR-CHECK.md`.

Discovery-half findings:
* Route census (569): monster 2.4% (+77% captured median), second-leg 3.8% (+40%), sustained 2.0%
  (+25%), recoverable flush 0.7% (+18%), resurrection 0.8%; fake_recovery 34.2% at −17.9% and
  immediate_dud 21.2% = the tax. ~9.6% of members carry the money.
* Route separability is high early but largely tautological (monster AUC 0.80 at +10 m vs best
  single observable 0.84) — descriptive only, no classifier deployed.
* Next-dollar ranking (leak-free two-segment chronological OOF, 383 days): real cross-sectional
  skill (daily rank-IC +0.12/+0.10) but negative absolute level of fresh deployment; net of
  100 bps the top-1 is −0.63% (540) / −0.77% (560) at +120 m.
* RETRACTED (leakage): an earlier fold trained and tested on the same days, manufacturing
  "+20.8% top-1%", "+1.5% top-1 net" and a "+1.16%/day architecture". Corrected OOF voids them.
* Structural anatomy rules on clean data: deep-drawdown-repairing ≈ breakeven (+0.007%/+0.045%,
  0.5 orders/day); shallow-pullback negative. Behavioural overrides add nothing.
* Resting bid into a flush: fill rates 30–81%, every cell negative net (−0.57% to −4.84%).
* Day level: entry gain correlates NEGATIVELY with outcome (−0.18); PM clocks carry no
  market-wide breadth/rank (board starts 09:30).
* Architecture: hysteresis and cost margins are decisive (no-hysteresis thrash −17.6%/day at
  127 orders/day); 3 competing claims beat 1 in the leaked run — to be re-proven leak-free.

Binding constraint: the 100 bps round trip versus edges of the same order. No positive-EV
executable mechanism on the discovery half yet; no freeze; the second 533 days remain untouched.
Next candidates: (a) near-open 571 with full-market race features and leak-free folds;
(b) outsiders/rotation (emerging leader outside the roster); (c) tail-riding formulations that
pay one round trip for a monster held through the window.

## 2026-10-03 — takeover: same-roster harvesting map; entry/selection drift rejected

Current worktree `basket-phase2-f1`; three read-only audits (economics, leakage,
friction). Stage F/G negative endpoints do not prove “past the pop” or justify
replacing top-gainer selection. New producers `lifecycle_harvestability.py` and
`lifecycle_harvestability_read.py`; artifact
`factory/artifacts/lifecycle_harvestability_discovery.json`. Discovery ONLY:
533 days, four clocks, all five ranks, 11 policies, 3 endpoint rulers, 100/150bps,
3/5-slot reporting = 528 correlated cells, all negative. Next-open +5% first-push
signals on 48–51% of selected slots before 13:00, mean sale +6.9–7.5% gross.
At 09:00 fade releases 157/310 later +30%-signal claims (50.6%); other clocks
18.5–21.8%. Best top-5 13:00 static-bank proxy −1.18..−1.74%/day; not an edge.
Exact-share accounting avoids the shared replay's reproduced 5%-capital deadband;
paired-day comparisons and window-only MFE attribution retained. Smoke proof:
delayed partials/costs/UNKNOWN, 3-day ×4-clock fade parity, label mutation,
all 281,424 portfolio-day cashflows conserve; source pin matches full run.
Next discovery object: joint trajectories at release/profit decisions, dollar
value of repair versus terminal decay and monetization versus retained optionality.
No lifecycle freeze or protected-half outcome read; H025/bot unchanged.

## 2026-10-03 — owned-claim decision dollars: first chronological pass complete

New producers owned_claim_events/value/replay/attribution/surfaces; discovery only
533 days, 673,035 causal events, 156 past-state/history/tape features. Tape is gated
by causal acquisition admission: the old raw net included future winners/later
leaders, so availability could leak. No whole-day quality feature. Owned value
is dollars on a fixed original-share basis; entry sunk, exit fee common. New buys
use causal fixed quantities, 90% cash headroom and settled receipts, not lifetime
profit; price-gap funding failures remain UNKNOWN.
First model = eight-event fitted value iteration, three expanding chronological
folds (383 test days), 3 feature views; stop / one repair re-entry / free-cash
rotation-reinforcement, N3/N5 independently, 100/150bps. All176 reported cells
negative. Best N3/569 tape:stop gross+0.477% fees0.985% net-0.508%; best N5 byclock
net-1.33..-0.79%. Full533 ruler decomposition reconciles to <2.8e-13.
First +5% clean pushes (540/N3, no prior damage) add +1.78% original-claim dollars
over30m vs-0.27% after damage; both fade on longer horizons. These are descriptive
surfaces, not rules. Next controlled correction: full future learned-policy
cashflow targets, not an eight-event planning horizon (540 stop exits median09:10).
Artifacts owned_claim_contract/attribution/policy_discovery/surfaces.json.
No protected-half outcomes, freeze, bot change, or promoted executable edge.

## 2026-10-03 (later) — owned-claim status correction: FVI8 / first policy_return are PROVISIONAL

The FVI8 (eight-event fitted value iteration) and first `policy_return` model numbers in the
entry above are **not a valid causal EV assessment** and must not be read as evidence about the
roster or the thesis. Four defects invalidate them AS CAUSAL:

1. **Future-status peer/scanner leakage** — the inherited peer/N3/N5 scanner predictors were
   built from a peer's FINAL fill/block/missing status, so availability could move on future
   information. Rebuilt as-of from observed `decision_px`/`px` only
   (`owned_claim_observable_peers.py`).
2. **Strict-pointer skipped chronological release** — the value-iteration walk used the strict
   `next_sequence` liquidation pointer, which skips a release that reprints the same opening and
   reports a later print instead of the correct zero incremental price.
3. **Excessive unused-suffix censoring** — outcome `status` was frozen into the fit mask before
   iteration, so an early finite stop ahead of an unused unknown/bad terminal was censored.
4. **Adaptive entry-point control gate** — `policy_return` was forced through the strict-control
   `classify`/`fold_weights` gate; it must enter on its own per-iteration chosen-exit support.

Corrections are landed in source (`owned_claim_events/value/replay/attribution/surfaces.py`,
`owned_claim_observable_peers.py`, `owned_claim_policy_targets.py`) with **8 passing regression
proofs** in `factory/scripts/test_owned_claim_causality.py`: as-of context ignores future fill/
status; a same-open reprint release is a known zero, not the strict later print; an early release
stays known when an unused terminal is broken; an unavailable chosen exit is excluded, not zeroed;
one settled-receipt-funded re-entry at the causal decision mark; an unaffordable execution gap is
UNKNOWN, not resized; proceeds are spendable only after the execution minute; and `policy_return`
enters without the strict-control gate.

The **full corrected 533-event rebuild and the 383 test-day two-method (FVI8 + `policy_return`)
replays are PENDING** — no corrected artifact exists, so no corrected result may be called
"passed" yet. The old numbers (all-176 cells negative, best N3/569 tape net −0.508%, etc.) are
preserved as **historical/provisional observations only**, not erased and not a roster
falsification. Standing qualifications: N3 and N5 are independent economic views (neither repairs
the other); original-claim entry is sunk while fresh re-entry carries a separate cost hurdle;
causal quantities are fixed original-share/fixed-buy and price-gap funding is UNKNOWN; no
protected-half read, no freeze, no deployment. Descriptively useful regardless of the causal
verdict: the clean first+5% push/dollar-horizon anatomy and the before-13:00 next-open sale-proxy
surface. The user authorizes the data-driven exploratory push-legs / recovery-retrieval /
probe-reserve scripts as they are being implemented — that authorizes the implementation, not any
outcome from it. No new IDs, no freeze, no protected-half outcome read.

## 2026-10-03 (later still) — owned-claim corrected rebuild + two-method replays COMPLETE (no edge)

The corrected full-ruler rebuild and both 383 test-day replays the entry above called PENDING are
now **COMPLETE**; the FVI8 / first-`policy_return` numbers are **historical INVALID-AS-CAUSAL**
only. This entry supersedes the "PENDING" wording above.

Corrected evidence (discovery half only, 2021-02-01..2023-03-14): 533 days, 673,035 causal events,
160 tape-view feature columns (state90 / history134 / tape160), 9,895 claims, 638,198 valid rows,
24,942 unknown rows, 0 beyond-session; three expanding chronological folds = 383 test days. Two
methods completed on 100/150bps: `value_iteration` (control) and `policy_return` (full
learned-policy cashflows), each 176 cells — **0/176 net-positive in EACH method (352 cells
combined), no positive-EV executable cell.**

* Control (`owned_claim_policy_discovery.json`) least-negative cell: 571/N3/50bps `state:stop` net
  −0.5140% (gross +0.4475%, modeled fees 0.9615%; 380/383 known days); best N5 control 571
  `state:stop` −0.6631%.
* Full policy (`owned_claim_policy_full_discovery.json`) is materially worse: N3 least-negative 540
  `fade` −2.4535% (gross −1.5395%); best N3 stop 540 `tape:stop` net −3.6058%; best N5 stop 560
  `tape:stop` net −2.9706%.
* Cash/fee reconciliation over 704 books: max |Σ member net − Σ daily ret| =
  4.529709940470639e-14 (common-day subset 4.263256414560601e-14).
* Eight financial regressions passed (parent-exercised) in
  `factory/scripts/test_owned_claim_causality.py`.

Verdict unchanged and conservative: no positive-EV mechanism; a failed harvesting instrument, NOT
a roster falsification. `source_correction_status: NOT_VERIFIED` **[binding status; an interim
'VERIFIED' relabel in the 2026-10-03 (final) entry below was an overclaim and has been withdrawn —
the declared status stays NOT_VERIFIED]** — the corrected artifacts exist
and reconcile, but no structured source-correction verification record exists, so this is
completed-run evidence, not a certified edge.

**QUARANTINE [SUPERSEDED by the 2026-10-03 (final) entry below — loss reader reran; attribution now
publishable, final pins/group-invariant assertions pending]:** the pre-rerun `owned_claim_loss_sources.json`'s `concentration` and `release_before`
attribution were not publishable
— parent fixed two reader grouping bugs after the full loss run (concentration merged N3/N5;
release_before joined both N views inside each N loop → duplicate N3 keys / incorrect
denominators); reader rerun pending. Standing qualifications: N3/N5 independent; original entry
sunk vs fresh re-entry cost hurdle; fixed-share causal quantities and price-gap funding UNKNOWN; no
protected-half read, freeze, deployment or quote certification. The exploratory push-legs /
recovery-retrieval / probe-reserve scripts passed three actual canaries; their full outcomes remain
PENDING (no outcome claimed). Bot offline: earlier fixes are NOT closed — latest review found six
additional defects; no live code/flags touched.

## 2026-10-03 (final) — exploratory diagnostics landed; loss-reader rerun closes quarantine [status correction: declared source_correction_status stays NOT_VERIFIED]

Compact follow-up to the entry above (same EXP-85; no new hypothesis ID).

* **Provenance [CORRECTED — original 'VERIFIED' phrasing was an overclaim, withdrawn].** The
  declared machine `source_correction_status` remains **NOT_VERIFIED** (`owned_claim_findings.json`
  actual value); no structured source-correction verification record was published into the reader
  inputs. SEPARATELY OBSERVED (not the same claim): parent source/runtime checks passed — bg_523
  source pins observed (push `provenance.script_sha256` matches; top `source_sha256` composite), 8
  financial regressions passed, EconomicAssumptionReview review PASS. No alpha validated; still no
  quote certification, freeze, deployment or protected-half read.
* **Loss-reader quarantine superseded (asserted).** Both reader grouping bugs fixed; the final
  formatted full rerun bg_531 completed 320.57s, its source pin and the 2,112 unique release keys
  were asserted in Eval, and `concentration` shows 352 N-separated cells matching the decomposition
  (release bounds re-asserted in the same cell next pass); max cash residual 4.529709940470639e-14.
  Attribution may now be published; historical warning retained. Core policy results and cash
  reconciliation were never affected.
* **Three exploratory diagnostics complete (bg_523, 705.56s), discovery-only — none a validated
  policy or edge; UNITS DIFFER.** `owned_claim_push_legs.json` (NOT a portfolio-P&L surface —
  causal first-push dollar anatomy): 533 dates / 9,895
  claims / 5,575 first pushes (2,791 clean, 2,784 damaged) / 63,424 declared-leg events;
  pooled-clock N3 clean hold increment $/100 +0.4887 at 15m [CI −0.3885,+1.3673], −0.5939 at 30m,
  −2.3122 at 60m, −3.8442 at 120m. `owned_claim_probe_reserve.json` (IS a modeled portfolio
  cash/wealth endpoint diagnostic): 48 frontier cells, early
  endpoint 0 positive; late endpoint 2 positive only (540/N3/100bps 15m +0.019334% SE 0.120457%,
  30m +0.102710% SE 0.200719%; break-even α 0.008657 / 0.044336); all 150bps and all N5 late cells
  non-positive. `owned_claim_retrieval.json` (NOT a portfolio-P&L surface — held-out
  retrieval-scalar evidence): evidence true, 3 fixed folds, 32,514 anchors, 75,076
  queries; pooled-focus expected-positive realised $/100 negative every horizon/N/mode; scalar
  TRAIN baseline lower/equal RMSE in 77/80 cells (all 16 focus); no classifier. Paired parent calc
  (EXACT same clock/N/cost/policy/date; no cross-clock causal comparison): 540/N3 `tape:stop` 367
  dates control −1.2992% vs full −3.6058% (delta −2.3066pp); 571/N3 `state:stop` 359 dates
  −0.4241% vs −3.7461% (delta −3.3220pp).
* **Producer lineage note:** `owned_claim_attribution.json` producer_sha `bd6e1741` EXACT-matches
  archived git blob `76db4a1` (historical `owned_claim_attribution.py`), NOT the current formatted
  script `8942b8e7` — ARCHIVED lineage, not current-producer output (no rerun needed, format only);
  other current pins (events/helper/model/replay/surfaces/loss/probe/retrieval) verified.
* **Bot:** at that time NOT closed — pending restart-to-existing OCO integration and final gates
  (obsolete T5 failure counts, not the latest boundary) — SUPERSEDED by the 2026-10-05
  parent-verified offline paper closure below. No live code/flags touched then.

## 2026-10-05 — owned-claim clock windows + direct-minute ownership (bg36 + bg45 COMPLETE-NEGATIVE)

New discovery-only lane on the first533 days (2021-02-01..2023-03-14); no protected-half read,
FREEZE or deploy. Clock/anatomy is NOT strategy; no edge promoted.

* **Per-clock episode windows (bg36, COMPLETE):** `owned_claim_clock_windows.json` (producer
  `owned_claim_clock_windows.py`, source pin verified). 533 dates, 892.99s, 5,575 pushes, 63,424
  legs; 24 first-push / 16 leg profiles / 720 contrasts / 896 contexts, 0 pooled-clock cells.
  Units: `inherited_*` = $ per $100 original budget on the fixed inherited share count
  100/(fill_px·1.005) with entry fee SUNK; `fresh_*` = a separate new $100 cash at the episode's own
  entry open, a conditional ruler that sizes nothing; fee share-count math proved at 100/150bps.
  N3 clean 540 (401 episodes, 315 dates): h1 +0.699/−0.375; h15 +1.545/+0.415 CI[−0.590,1.668];
  h30 +1.756/+0.628 CI[−0.897,2.349]; h60 −0.060/−1.047. NO portfolio alpha / universal-exit claim;
  conditional positive owned EV does not erase the initial dud tax.
* **Direct-minute ownership (EXP-86 / H041) — FULL COMPLETE, NEGATIVE:** `owned_claim_minute_value.py`
  + `owned_claim_minute_replay.py` (test `test_owned_claim_minute_causality.py`); 533 dates /
  3,935,543 minute rows / 164 causal features (h1/3/5/10/15/30/60/120), separate N3/N5 models, 3
  fixed chronological folds / 48 fits / 80 rounds baseline LGB, original-CASH@50 forecast SIGN-only
  stop; raw GROSS equiv = cash/0.990049 (not an identity). Parent removed a future `feature_sellable`
  leakage + a manifest missing horizons/Ns/clocks (pre-fix agent smokes INVALID); corrected bg43
  smoke 208.28s pin `71fa3b67`; parent 25 financial regressions PASS (8 baseline + 9 minute + 8 cash
  levers); Ruff/syntax PASS. FULL bg45 7779.73s →
  `factory/artifacts/owned_claim_minute_discovery.json` (`evidence: true`): **0/176 net-positive
  cells** — no edge promoted, no protected-half read. Best N3 `569 max:stop` net −0.510461% (gross
  +0.474685%, fees 0.985146%, 379/383, day SE 0.307470%); best N5 `571 h1:stop` net −0.962846%
  (gross −0.023450%, fees 0.939396%, 380/383, day SE 0.108141%). Fine cadence alone FAILED as a
  priced implementation — NOT a roster/window falsification.
* **Money-gap (`owned_claim_money_gap.py`):** fixed a NET dud+fee double count (now GROSS
  price-loss with fees disjoint); baseline other-outcome TAIL requirement = deficit; worst/best DATE
  sensitivities on the same population primary per BOOK DATE; cross-case Σ not portfolio. FULL
  money-gap (383 dates, 352 cells) actual reconciliation 4.263256414560601e-14, source pin match;
  old double-fee/denominator counterfactual bugs corrected; parent 25 fiscal regressions passed.
* **NEXT (now ACTUAL — superseded by the 2026-10-05 (evening) entry below):** the cash-first
  fee-aware admission hypothesis on the SAME original roster is now H042 / EXP-87 with completed FULL
  evidence. Existing baseline `460b1d1` unchanged.
* **Bot offline paper closure — parent-verified final:** 34 safety tests PASS + legacy T1–T14 module
  collection PASS; new Safety file Ruff PASS / 3-file syntax / F-E9 PASS / LSP diagnostics request OK;
  real alpaca-py nested-Order temp-journal restart smoke PASS (original B/ts retained, stop child fill
  100 + 2 stale held snapshots, no OCO/close, true flat books). ProtectionRestartAudit bounded all
  source findings closed incl lag-position post-fill quantity via the shared handler `pos` (source
  Qty/liveness + family IDs preserved). OFFLINE fix/test/source-review **CLOSED** — NOT deploy or
  broker-cert: broker exposure UNOBSERVED, intrinsic OCO double-fill/cancel race NOT certified; known
  ordinary multiple over-covering families not consolidated (pre-existing, out of scope, not hidden).
  Source-only WT3 files `flush_bot.py`, `test_flush_bot.py`, new Safety file (parent commits +records/
  HANDOFF; no DATA). Fiscal 25 PASS earlier; source-budget new core cash mode is a separate phase. No
  runtime/gates/git/source changes; main/flags/orders unchanged.

## 2026-10-05 (evening) — cash-first fee-aware ownership (H042 / EXP-87) + corrected minute-attribution reader

New discovery-only lane on the first533 days; no protected-half read, FREEZE or deploy.

* **Cash-first ownership (H042 / EXP-87), COMPLETE-DISCOVERY-POSITIVE-MEANS-NOT-VALIDATED:**
  `factory/artifacts/owned_claim_cash_first_discovery.json` (bg71, 3788.04s, final 3-day smoke +
  383-day full). Start in CASH on the SAME original roster; pay the fresh entry fee only where the
  predicted marginal money clears the ACTUAL round-trip hurdle (held threshold = forecast sign; fresh
  threshold = derived fee hurdle). 304 cells = 288 active + 16 cash0; **61 active mean-positive**
  (N3/100 19, N3/150 11, N5/100 20, N5/150 11); same roster / OOF 48 fits / 3 folds / 9 views / 4
  clocks / N3,N5 separate / 100,150bps / once,repeat. Best N3 540 `h3:cash` once @100 **+0.268540%**
  (SE 0.144797%, gross +0.469931%, fees 0.201391%, 381/2 UNKNOWN) and @150 **+0.051910%**; folds @100
  [+0.105451, +0.294920, +0.521704], @150 [+0.000914, −0.047625, +0.327270]. Best N5 569 `h60:cash`
  once @100 **+0.138254%**, @150 **+0.109992%**, 3/3 fold-positive both costs. Remove-best-DATES:
  N3@100 best10 −0.054881%, N5@100 best3 −0.013006% — conditional, NOT a kill-tail strategy.
  **INTERPRETATION QUALIFICATION:** new cash consumer enters at 90% owner-slot headroom vs
  forced-upfront history 100% with new timing, so admission/SIZE/exposure all differ — NO one-factor
  pure entry-gate causal claim (only roster/scores/fees preserved); the forecast hurdle uses the
  current completed MARK vs a next-open expectation approx, so next-open proxy + 100/150bps fees are
  NOT actionable quote proof; the upstream unit artifact's SIGN-only text is the H041/old-stop
  contract while the new cash consumer uses cash/F magnitude with a fill-reference/current-mark
  gate. Not validated: 288 dev alternatives, uncertainty/tails; no profit/FREEZE/collision-read/bot-
  deploy; cash0 known zero (6,128 books/0 nonzero/0 orders); **ONCE invariant (parent actual): 55,152
  active once books / 220,608 members, no original claim bought twice and all reentries 0 — so the
  exec+1-minute proceeds-REUSE assumption is NOT a funding blocker for ONCE (broker price/order/qty
  still NOT certified);** settlement exec+1 minute is a SIMULATOR,
  not broker T+1; cash-stability/execution producers pending. Ruff PASS; 38 fiscal PASS (13 cash+25
  old); real 3-day UPFRONT parity 528/2,112/4,158/0 mismatch; pins match.
* **Corrected minute-attribution reader (bg76→bg86; `factory/artifacts/owned_claim_minute_attribution.json`),**
  **FINAL CURRENT MATCH:** bg86 COMPLETE 277.18s, Ruff PASS, 383 days / 176 cells, `evidence: true`,
  producer SHA `4276fa56c9e27490abd18b8a1053a324f772de152e9698a2208b4c98a00c9807` current and actual
  consumed FVI/`policy_return` vintage SHAs MATCH. Own paired
  5,524 keys, cross 5,418; max member-book residual 4.440892098500626e-14; all 176 cells vs bg76 show
  no structural change (floating-reduction diffs ≤4.440892098500626e-16); ownership all partitions clean
  (never-owned blocked slots separate from held); 352 own-ruler pairs, 3,872 prior-book pairs, 352
  reference distributions; joins on (day,clock,N,side) ONLY with separate
  `minute_policy`/`reference_policy` — never a policy-name inner join. N3 `569 max:stop` @100
  all-11-policy common 345 dates net −0.538677%/paired DAY; median hold 2 min, 421/569 later-push
  claims released before the push (ex post). No pure-cadence-effect claim. The N5 best COMMON
  all-policy-date cell 560 `h30:stop` differs from the official unpaired best 571 `h1:stop` — official
  best NOT overwritten. A bounded source review closed one relocated prior-ledger defect (declared-root
  vs consumed data-root copy; an 11-line guard now asserts the actual consumed daily SHA vs declared and
  rejects on mismatch; permanent `test_owned_claim_minute_attribution.py` 2 lineage cases PASS) with no
  open findings and unchanged money. This corrected reader is the valid money reader.

## 2026-10-05 (night) — cash-first stability (bg96) complete; execution reader pending

* **Cash-stability DONE [superseded by the 2026-10-05 (late) FINAL — see below]:**
  `factory/artifacts/owned_claim_cash_stability.json` (bg96, 28.29s; Ruff +
  3 financial regressions PASS 1.16s + full reader), evidence true / `full_discovery_evidence`. 383
  dates, 288 active + 16 cash0, 144 matched-cost + 144 cycle contrasts, 19 months, 3 folds;
  pins_verified true (766 score partitions + 1,149 execution inputs rehashed), producer SHA
  `5ecd323edd966afcbd78c098aaba08e765f75cd4b39bfa2b0fc0b353f285ba08` current MATCH, EV reconciliation
  max 0.0. All 61 positive; 8 all-3-fold positive; 42 ≥ half-month positive; remove best 1/3/5/10
  DATES → 30/11/4/0 positive cells (alternative cell counts NOT independent samples/portfolio). N3
  best 540 `h3` once 10/19 positive months @100 / 7/19 @150; N5 best 569 `h60` once 12/19 @100 / 11/19
  @150. Deterministic 200 MONTH-BLOCK draws are DESCRIPTIVE only (N3 @100 p05 −0.008536% / p95
  +0.610194% / 5.5% ≤0; @150 −0.198343% / +0.280384% / 38% ≤0; N5 @100 −0.058269% / +0.334986% / 15%
  ≤0; @150 −0.032536% / +0.265328% / 18.5% ≤0) — no p-value/significance. Ledger unit = original
  PORTFOLIO CAPITAL; forecasts original CLAIM-cash F units (distinct; source-traced unit bug fixed
  pre-run). Top fold/month source-lexicographic bug fixed pre-run; 3 permanent regressions correct
  money-block rank / UNKNOWN-fee profit denominators / date-removal boundary. UNKNOWN hypothetical
  per-date loss erases the known sum (N3 @100 51.156863% 2 dates, @150 19.829472% 1; N5 @100 52.812984%
  1, @150 20.953457% 2). Reader bounded review CLOSED; execution DONE (bg101) — no validated
  edge / protected read / FREEZE.

## 2026-10-05 (late) — stability bg102 FINAL; execution reader (bg101) actual; 4-cost ladder PENDING

* **Stability FINAL (bg102, 28.53s; Ruff + 5 regressions PASS 0.78s).** Two diagnostic bugs fixed:
  independent denominator recovery correct (summary EV / fees / gross on the KNOWN SAME set, orders ALL;
  affordability counts UNKNOWN DATES separately from affected CLAIMS). Corrected orders-denominator diff
  = 0.15186886527694377 / all 0 ⇒ all-date-mean; **all 288 return means EXACT unchanged vs the old
  reader**; pins_verified TRUE, EV diff 0.0. Reviewer bounded findings ALL CLOSED correct (no alpha / no
  broker cert). FINAL union 59 fiscal tests PASS (6.56s); all 8 NEW source/test Ruff PASS + core F/E9
  PASS. Executed SHA MATCH (all 5): cash adapter `84bc4365`, core `52205b03`, minute-attr `4276fa56`,
  stability `5ecd323edd966afcbd78c098aaba08e765f75cd4b39bfa2b0fc0b353f285ba08`, execution
  `7b0071919d4a7f95e6c7c4dea9f5a06ab10c5c01323695a7be07ecc9c28a0f47`. RECORDS READY IMMUTABLE for the
  focused ARCHIVE commit BEFORE the cash-driver source cost extension (old producer `84bc`
  git-preserved); no further changes until the parent 4-cost result.
* **Execution FULL (bg101) actual — reader only, NOT validated.**
  `factory/artifacts/owned_claim_cash_execution.json` (reader `owned_claim_cash_execution.py`, evidence
  true, kind `CASH-FIRST-OWNED-CLAIM-OBSERVED-EXECUTION-READER-NOT-VALIDATED-EDGE`; validates nothing):
  COMPLETE 234.13s, Ruff + 14 tests PASS 1.11s + full source-panel read; 304 cells / 116,432 books /
  465,728 members / 208,544 legs / 1,245 unknown books / 1,270 unknown members / 94 held terminal; max
  recon 7.60503e-15; all known support unique/complete/finite; pairs closed; qty PASS. Units = ORIGINAL
  PORTFOLIO cash (correct). Actual N3 best 540 `h3:cash` once @100: median entry 09:37, exit 09:45, hold
  3 min; bar-volume proxy 10k median 0.343367%, p95 24.76345%, p99 ~1206%, max 4382% (!). N5 best 569
  `h60:cash` once @100: median entry 10:24, exit 11:29, hold 11 min; median 0.322671%, p95 63.7478%, max
  6061%. Bar-volume-proxy price-taking / capacity tails — NOT certified / NOT an automatic profit kill
  (PnL dependence unmeasured). Successful entry mark→next-open: N3 p95 +2.41549%, N5 +1.75241%; known
  funding-failed attempts remain UNKNOWN. ONCE zero re-entry → no intraday proceeds-reuse blocker (REPEAT
  caveat unchanged). No alpha / no broker cert.
* **Cost-ladder extension (user priority; 4-cost PENDING).** Run total round-trip bps 25/50/100/150
  (per-leg 0.00125 / 0.0025 / 0.005 / 0.0075) on the SAME H042 / EXP-87 (no new hypothesis ID). The
  completed two-cost (100/150) proof is archived and must NOT be overwritten as the 4-cost outcome; the
  4-cost run (576 active + 32 cash0 = 608 total; all controls unchanged) is PENDING. Preserve the frozen
  publisher F = 0.995/1.005 forecast-unit conversion SEPARATE from actual leg cost. NO core SIDES /
  upfront model source changes.

**ARCHIVAL (parent):** old two-cost producer/core/cash-test/
  artifact ARCHIVED at commit `016056a`; all diagnostics/readers/tests/records/HANDOFF ARCHIVED at
  `7b48fa6`, pushed `origin/basket-phase2-f1`. Old artifacts pin to `016056a`/`7b48fa6` — do NOT require
  them to equal the new cash-driver HEAD. **4-cost run started:** new cash adapter SIDES =
  (0.00125,0.0025,0.005,0.0075) explicit TOTAL 25/50/100/150, CORE unchanged `52205b03`, frozen
  forecast F unchanged; new data root `owned_claim/replay_cash_first_four_costs`, NEW artifact
  `owned_claim_cash_first_four_costs.json` (old 2-cost data/artifact NOT overwritten). Parent 17
  admission tests + 3-real-day four-cost smoke `bg104` were superseded by the FULL results; the new
  latest curve awaits explicit user instruction (not a review/gate).
* **Capacity two-cost diagnostic DONE (bg113, 24.81s; Ruff + 5 financial regressions PASS 1.13s before a
  wording-only fix):** `factory/artifacts/owned_claim_cash_capacity_two_costs.json` (producer
  `owned_claim_cash_capacity.py`, evidence true): 288 active + 16 cash0, 116,432 books, 208,544 legs,
  104,225 closed, 6,910 legs > historical full bar volume, max identity 3.5083047578154947e-14, failures
  []. Bugfixes pre-run: missing ONE-leg volume cycles UNKNOWN (never the other leg's max); all/part-
  missing executed day ⇒ `missing_volume` (NOT `no_leg`); NULL-member finite count + unique-Ticker roster
  checks; whole unknown-book exclusion tests. Source bounded review later CLOSED (final FourCostContractReview BOUNDED CORRECT; all 6 areas). Per-pair (initial-C0 units,
  within ONE alternative book across its KNOWN dates — never added as a portfolio): N3 540 `h3:cash`
  once @100 381 known +1.0231372677 (≤1% 169 pairs +1.3703922045; >full-hist-vol 10 pairs −0.1304510902;
  all other measured bands negative); @150 382 known +0.1982947198 (≤1% 116 pairs +0.5736070229; >full 8
  pairs −0.1241966380). N5 569 `h60:cash` once @100 382 known +0.5281298357 (≤1% 145 pairs +0.9026049593;
  >full 13 pairs +0.0300011292); @150 381 known +0.4190691316 (≤1% 124 pairs +0.6866690068; >full 8 pairs
  +0.0311025497). Empirical positive PnL is NOT driven by the large-printed-volume-share cohort; future
  volume is DIAGNOSTIC only — cannot become an entry gate or retro-filter strategy EV. PriceSizeRatio > 1
  is NOT a physical impossibility (historical prints are NOT market depth; an additional order could alter
  counterfactual volume; unpriced risk) — not a fillability certificate. 4-cost consumer smoke: 576 active
  + 32 zero + 432 adjacent-cost + 288 cycle = 608 books/combos over 3 days (1,824 books, 3,859 legs, 35
  unknown, panel-gap read, max 4.09828e-16); parent 63 fiscal union PASS 6.09s (17 cash + 17 math/gates +
  3 real-day 608 proof + old 100/150 exact 912 daily / 3,648 members / 1,066 fills), BEFORE the 5 new
  capacity tests. Full four-cost `bg105` COMPLETE (see the 4-cost block).
* **Four-cost FULL COMPLETE (bg105, 6050.60s; evidence true; current adapter SHA / engine `52205b03`
  MATCH; frozen OOF unchanged):** `factory/artifacts/owned_claim_cash_first_four_costs.json` (new data
  root `owned_claim/replay_cash_first_four_costs`; old 2-cost data/artifact preserved). 383 dates, 608
  cells = 576 active + 32 cash0; 257/576 active mean-positive; costs TOTAL 25/50/100/150 (equal half-leg).
  Best by cost & N (per KNOWN BOOK DATE %, exploratory — NOT selected/frozen): @25 N3 540 `h3:cash`
  REPEAT +0.9053613% (SE 0.3265139, 377 known / 6 UNKNOWN, gross 1.3425845%, fees 0.4372232%, 11.13577
  orders/day, ALL), N5 560 `max:cash` REPEAT +0.6521188% (SE 0.3147229, 367/16 UNKNOWN, 24.43081 orders);
  @50 N3 540 `h3` REPEAT +0.5299096% (SE 0.2504682, 376/7 UNKNOWN, 5.527415 orders), N5 560 `max` ONCE
  +0.2267669% (SE 0.2000620, 379/4 UNKNOWN); @100 N3 540 `h3` ONCE +0.2685400% (381/2), N5 569 `h60` ONCE
  +0.1382539% (382/1); @150 N3 569 `h5` ONCE +0.0797915% (378/5), N5 569 `h60` ONCE +0.1099919% (381/2).
  Per-cost ONCE curves: N3 540 `h3` 25/50/100/150 = +0.2143284/+0.2414981/+0.2685400/+0.0519096%; N5 569
  `h60` = +0.2219027/+0.1641858/+0.1382539/+0.1099919% — lower cost CHANGES the entry hurdle/trades, NOT
  simple fee subtraction, NOT monotone. Low-cost REPEAT leaders are NEW simulator intraday proceeds-reuse
  / turnover quote uncertainty — NOT broker T+1 cert; funding/execution proof still needed; NO deployment.
  ChildFourCostContractReview: cost source CORRECT closed no findings; EconomicAssumptionReview failed
  model callback; the replacement FourCost/capacity review later CLOSED (BOUNDED CORRECT; all 6 areas — see the FINAL entry). Parent FULL new stability/
  exec/capacity four chain `bg114` is now COMPLETE (372.74s) — see the correction entry below. Records now current = FOUR-COST_FULL
  complete + diagnostics COMPLETE + FINAL capacity label rerun (bg115) complete; READY IMMUTABLE for the parent's final artifacts/docs commit (hash not yet known).

## 2026-10-05 (final) — record correction: test-union breakdown + four-cost FULL complete

Correction to the 'late' entry above (append-only; the pre-correction text is retained as history).

* **Test-union breakdown corrected.** The latest UNION is **68 tests PASS 5.67s** (final, after the fractional display fix) = 63 (25 original
  financial + 17 cash + 2 lineage + 5 stability + 14 execution) + 5 new capacity; Ruff PASS on all 8 new
  files. The earlier "63 = 17 cash + 17 math/gates + 3 real-day 608 proof" phrasing was WRONG. Old
  100/150 exact 912 daily / 3,648 members / 1,066 fills unchanged. The 5 new capacity tests verify:
  whole-UNKNOWN excluded; partial/all-missing liquidity is `missing_volume` not class-safe/`no_leg`;
  open-buy-only pivot; band boundaries.
* **Four-cost FULL `bg105` COMPLETE** (6050.60s; 383 dates; 608 cells = 576 active + 32 cash0; 257/576
  active mean-positive; evidence true; engine `52205b03` MATCH; frozen OOF unchanged) — see the 4-cost
  FULL COMPLETE block above. The stale "bg105 RUNNING / NO outcomes yet / HOLD" wording is withdrawn.
* **Current status:** full REPLAY COMPLETE for the four-cost lane; ONLY the 3-diagnostic FULL chain
  `bg114` is COMPLETE (see the final-2 entry), and the FINAL capacity label rerun `bg115` then closed the
  capacity review.
* **Code vintages:** old two-cost `016056a`; all diagnostics/readers/tests/records/HANDOFF `7b48fa6`; the
  new commit `5bc7518` holds the 4 executed current sources. Capacity-review replacement later CLOSED.
Records READY IMMUTABLE for the parent's final artifacts/docs commit (hash not yet known).

## 2026-10-05 (final-2) — 3-diagnostic FULL chain bg114 COMPLETE

`bg114` COMPLETE 372.74s; all 3 current producer SHAs matched. Stability 383 dates / 576 active + 32 cash0
/ 432 adjacent-cost + 288 cycle / 3,068 unknown alternative-book dates, pins_verified TRUE, EV diff 0.0;
execution 608 cells / 232,864 books / 802,985 legs / 3,068 unknown / max recon 1.90958e-14 / panel-gap
read / evidence true; capacity 576 + 32 riders @25/50/100/150 (same 232,864 books / 802,985 legs /
401,318 closed / 29,303 legs > full historical volume / max ident 3.5083e-14, all True, evidence true).
Stability breadth 257 positive, 77 all-3-fold positive, 201 ≥ half-month positive; remove best 1/3/5/10
DATES → 179/108/73/31 positive cells (correlated, NOT independent edges). ROUT-CASE caveat: @25 N3 540
`h3` REPEAT folds −0.1968909 / +0.8916134 / +2.9444727 ⇒ NOT 3-fold-positive (@50 −0.0875390 / +0.6615214
/ +1.4287076); N5 560 `max` REPEAT @25 folds +0.5686249 / +0.4365793 / +1.2118038 (three+) but same @50
pooled +0.058044% (2 neg folds), @100 −0.106433%, @150 −0.213691%; N5 569 `h60` ONCE 3-fold+ at ALL 4
costs; N3 540 `h3` ONCE 3-fold+ at 50/100 (@25 first fold neg, @150 middle fold neg). No top mean is a
validated strategy. Capacity-review replacement: all 6 source areas CLOSED; ONE P3 numeric display bug
(per-leg cost 25 rounded to 12; money UNCHANGED) fixed at both sites to exact 12.5 and the 2-/4-cost
capacity is rerunning for a final current SHA — the FINAL label rerun `bg115` matched the capacity producer
SHA at both 2-/4-cost and confirmed all cell monetary results EXACT UNCHANGED (see the FINAL entry at
EOF). All generic 4-diagnostic artifact paths are `*_four_costs.json`; no more RUNNING scaffold. Evidence commit `5bc7518` pushed source + full replay; the FINAL label rerun `bg115` then completed the parent's
fractional-proof + economic/capacity summary (see the FINAL entry below).

## 2026-10-05 (final-3) — FINAL capacity label rerun bg115; all four-cost FULL artifacts complete; docs READY IMMUTABLE

`bg115` COMPLETE 81.55s (both 2-/4-cost capacity). Producer SHA
`c12fa8fd92d57e6bf62cb3727731bdfa76d2c758b99908e159eeb01c6d20997c` MATCH both; all 576 cells 2×per-leg
== declared total with per-leg [12.5, 25, 50, 75] (earlier 12 rounding fixed); all cell monetary results
EXACT UNCHANGED. FourCostContractReview final BOUNDED CORRECT, no findings, all capacity areas closed;
all 4 FULL artifacts complete TRUE / current pins match; NO RUNNING/PENDING review remaining. No alpha /
no quote cert. Final source 68-test union PASS **5.67s** (after the fractional display fix), all 8
source/test Ruff PASS.

**FINAL economic money/stability.** @25 N3 540 `h3` REPEAT +0.905361% folds
−0.196891/+0.891613/+2.944473% (NOT 3-fold+), 12/19 months+, drop best 5 DATES +0.428531%, best 10
+0.219960%, 200 month-block descriptive p05 +0.192244% (NOT confidence); @50 same +0.529910% folds
−0.087539/+0.661521/+1.428708%, 11/19 months+, drop5 +0.195499%, drop10 −0.017743%, block p05
+0.042439%. @25 N5 560 `max` REPEAT +0.652119%, folds +0.568625/+0.436579/+1.211804%, 13/19 months+,
drop5 +0.255395%, drop10 +0.020482%, block p05 +0.170350%; SAME case @50 +0.058044%, @100 −0.106433%,
@150 −0.213691%. N5 569 `h60` ONCE 3-fold+ ALL 4 costs (weak SE/tail); N3 540 `h3` ONCE 50/100 3-fold+,
25/150 one fold negative. A lower-cost repeated wave-harvesting candidate appears, but high
turnover / REUSE / actual 25bps fees are NOT certified. Candidates kept OPEN DISCOVERY positive; NO
validated edge / FREEZE / new protected collision / deploy.

**FINAL four-cost price/PnL capacity (per-pair, initial-C0 units — never added as a portfolio).** @25
N3 REPEAT ≤1% 1,247 pairs net +3.017917, 1–5% 438 pairs +0.626299, >full-historical 65 pairs −0.082112
(total known net +3.413212); @50 N3 ≤1% 654 pairs +1.865544, >full 30 pairs −0.090607 (total
+1.992460); @25 N5 `max` REPEAT ≤1% 2,403 pairs +4.296626, >full 260 pairs −0.197502 (other bands
negative except 25–100% +0.260105, total +2.393276). Historical printed volume is NOT market depth;
future volume is EX POST no-filter diagnostic. Positive profit is mostly small-volume-share trades;
beyond-full volume is NOT the source of profits in the low-cost leaders (measured, NOT certified).
UNKNOWN books fully excluded in all cohorts; the 3,068 alternative-book dates are NOT independent
samples. NEW artifacts: `owned_claim_cash_stability_four_costs.json` (stability SHA
`9df772a693f159a0bbd9e4d3b5889daef07beecdb2dff0c48a82c98261c6464a`),
`owned_claim_cash_execution_four_costs.json` (reader SHA
`8bbda7fd975f4718de66d9554ef31b91aca986c1bc00f55be08a62285924d1c1`),
`owned_claim_cash_capacity_four_costs.json` (reader SHA `c12fa8fd…`, per-leg 12.5/25/50/75 confirmed in
the artifact); the existing two-cost snapshots stay separate. Full runtimes: cash 6050s / 3-diag
372.74s / final capacity 81.55s; source/model/raw roster/N-separation unchanged except friction.
**Docs READY IMMUTABLE** — no further edits until asked; commit `5bc7518` already pushed source + full
replay; the parent's final artifact/docs commit follows this READY (hash not yet known).

* **Publication (parent):** producers/artifacts committed+pushed at `95d425b` (final `c12` capacity source +
  refreshed two-cost capacity + 3 FULL four-cost diagnostics); `5bc7518` holds the cash four driver/replay
  and the 4-cost consumer adaptation. Engine core `52205b03` unchanged. Declared/current digests match
  (all 5): cash driver `4901fa99f80de3f16d21295ebcfd65e132b1b6fa509b66b6210c008b61eabda0`, stability
  `9df772a693f159a0bbd9e4d3b5889daef07beecdb2dff0c48a82c98261c6464a`, execution
  `8bbda7fd975f4718de66d9554ef31b91aca986c1bc00f55be08a62285924d1c1`, capacity
  `c12fa8fd92d57e6bf62cb3727731bdfa76d2c758b99908e159eeb01c6d20997c` — per-leg [12.5, 25, 50, 75]
  exact (2×per-leg == declared total, 576 cells). stability/execution/capacity `evidence: true`; all
  status COMPLETE, bounded review CLOSED, **no RUNNING/PENDING**. Final source 68-union PASS 5.67s +
  Ruff PASS. Only the 6 doc/ledger paths + HANDOFF remain; the parent commits them after this READY
  (hash unknown, not invented).

## 2026-10-06 — H042/EXP-87 independent verification wave (parent-orchestrated; read-only agents; discovery-only)

Six independent read-only agents + parent analyses over the four-cost cash-first discovery
(worktree HEAD 59f3f10; no protected-half read; no FREEZE; no deploy; no repo mutation beyond
these records). Findings (full detail: `factory/artifacts/h042_verification_2026-10-06.json`):

* **Leak audit: 0 channels.** 11 channels CLEARED via code read + future-perturbation attack
  (NaN-ing every non-key panel column for t>cutoff leaves all 164 features byte-identical over
  3,900 prefix rows × 3 cutoffs × 2 days); labels/OOF/sizing/proceeds/cash0 all verified; the
  tightest timing edge (decision at t filling at the open of a bar in minute t) is safe because
  fill price never enters sizing (max sizing error 0.0; px/mark p50 1.0000). Bounded caveats, not
  leaks: UNKNOWN drop (3,068/232,864 = 1.32%) is non-random but ~self-cancelling (net shadow
  ~+0.15%/day; headline's own dropped days −0.30%/−1.27%); the headline is best-of-576 with no
  selection adjustment in the artifact; frozen panel not re-derivable from current raw files
  (AMST 2023-02-14), though every downstream hop is sha-pinned.
* **Selection statistics (parent; `factory/scripts/verify_h042_selection.py`,
  `factory/artifacts/h042_verification_selection_folds.json`).** Family-wise Reality Check
  (max over 144 active cells/rung, day-resampled null, 10k draws): p = 0.0771/0.3317/0.7671/0.9852
  at 25/50/100/150. Internal-forward (select cells by folds0+1 mean>0 → fold2): 25bps +0.289%/day
  (p=0.003 vs random subsets), 50bps +0.027%, 100bps **−0.023%**, 150bps **−0.059%**; random-subset
  p 0.003/0.042/0.015/0.085. Cell-mean Spearman f0~f1 = −0.006..+0.118 (no persistence);
  top-k-by-folds01 → fold2 at 100bps: top5 +0.128% (SE 0.199), top10 +0.006%, top20 −0.044%,
  top40 −0.019%. Family means/day (SE): +0.163% (0.094) / +0.037% (0.085) / −0.080% (0.071) /
  −0.126% (0.061).
* **Tail/power (agent).** ONCE A/B/C: top-1 date = 21.3%/61.9%/78.1% of net; top-10 = 120%/188%/
  223%; remove-best-3: A +0.147% (still), B **−0.013%**, C **−0.025%**; bootstrap CIs include zero;
  ~413/1,328/1,897 independent dates needed to exclude zero at observed dispersion. D/E survive
  remove-best-10 (+0.220%/+0.196%) and month-block CIs exclude zero, but fold0 negative and
  fold2-driven (D fold2 +2.94%/day on 81 dates; 2023 alone +3.70%/day on 48 dates).
* **Execution re-cost (agent; SIP quotes ATLAS-top-3 union, covered legs).** Measured full quoted
  spread 46–58bps per leg (round trip 92–116bps); px is the SIP tape (|first_print/px−1| median
  2.8e-4; 42% exact) sitting ~0.19% from mid. Re-cost: D +0.905% → **−0.147%** (covered) /
  **−0.669%** extrapolated; E +0.530% → +0.110% / −0.060%; A/B/C/F survive cost re-pricing
  (+0.233/+0.079/+0.105/+0.087%/day) but are the statistically weak ones. $500/$1000 orders exceed
  displayed top-of-book depth (median 5–7 sh) on 73–94% of covered legs; 2.5% of legs participation
  >1× bar volume; top-5 round trips = 37–81% of net.
* **Symbol/data quality (agent).** Silent NaN prev_close path: `basket_tape_atlas_observation.py`
  requires ~isnan(ratio_pc), so a name absent from the prior-day universe file skips the
  discrepancy flag entirely. Verified case: AMV 2022-09-28 clock540/n3 (prev_close 52.01 vs true
  82.12 → bogus +438% gain; member nets +0.115/+0.066/−0.019 across 25/50/100bps). 309/997 traded
  claims in the audited cells take the same path; ~15 more with gain_adj>1.93 flagged UNKNOWN.
  Excluding structural/suspect names does not flip any cell sign (deltas ≤ 0.06pp/day).
* **Simple form (agent).** Horizon-averaged importance stable core: pm_range_asof ~9–10%,
  race_n_known ~9%, pm_cum_vol ~6–7%, scan3_mean_ret, pm_n_bars, recovery_from_low, race_gain,
  event_count_new_high, event_count_push, peak_gain. No entry-state coordinate separates winners
  from losers (IQR overlaps; best rank statistic 0.63–0.65 vs SE 0.05; signs flip across cells);
  the gating forecast does not order outcomes (P(win>loss) 0.421/0.521); `sel_h` ≠ gated view
  43%/57%; `pred_max` optimism premium 48–68% and the max view loses (−0.069%/−0.227%/day).
* **Data blocks (agent).** Discovery 533d 2021-02-01..2023-03-14 (H042 replayed the 383-day
  3-fold test union; first 150 days train-only). Validation 533d = 2023-03-15..2023-12-29 (201)
  + 2025-02-03..2026-05-29 (332), inputs present, zero fetches needed, locked. Sealed 2024+2025-01
  certified, unseen. Reserved 2026-06..08: SIP lanes stop 2026-05-29; non-SIP ML/scratch artifacts
  exist physically — exclude by path allowlist, not month assumption.

**Verdict:** H042 stands as description, not as an extraction candidate. No read of the
validation half; no FREEZE; bot/flags untouched. Next: PRE-REG-ENTRY-EV-01 (conditional
executable continuation surface + entry-location families on the first533; internally split;
protected block sealed).

## 2026-10-06 (later) — ENTRY-EV-01 Stage A COMPLETE (discovery block; measurement only; negative, scoped)

Pre-registered Stage A ran end-to-end on the full discovery block: 533/533 days built, top-10
causal minute board, guard = verified non-null prev_close ≥ $1.00 (dropped 468,146 rows, ALL
`prev_close_lt_1`; zero null/stale/floor/discrepancy), 1,601,807 kept events; gross next-open →
next-open at h ∈ {1,3,5,10,15,30,60}; forward coverage 99.35% (h1) → 84.16% (h60, EOD censoring
= UNKNOWN). Lane verified across all 26 months (`factory/scripts/entry_ev/verify_lane_months.py`,
52/52 board digests matched, px exact 1.000000, 390k sampled rows). Producers
`factory/scripts/entry_ev/stage_a_build.py` (FE-0 parity exact; 15 tests) + `stage_a_read.py`;
artifacts `factory/artifacts/entry_ev/{lane_verification,stage_a_readout}.json` + `READ_PACKET.md`.
FE-0/FE-0b feasibility: board lane pinned (`data/ohlcv_<month>.parquet`), quotes ~34% / SIP trades
~81% of top-10 symbol-days (Stage A independent of both); resting-limit mechanism deferred to its
own registration (gain-correlated gated download).

Results: pooled continuation negative gross at every horizon (h1 −0.011% → h60 −0.411%; halves
agree). No conditional pocket clears cost: best eligible region +3.7bps (median 0.0) vs the
92–116bps measured round trip; momentum spikes, volume spikes, near-boundary rank state,
at-day-high all ≈0/negative. The one large positive region — `promo_age ∈ [−5,0)` (first top-5
crossing 1–5 minutes AHEAD; n=18,904, 527 days): h1 +0.994%, h3 +2.379%, h5 +3.086%, h15 +2.620% —
is **future-conditioned**: the crossing is (mostly) the price move itself, and every causal proxy
observable at t (rank 6–10 + momentum/boundary/volume/HOD) is ≈0 or negative. Post-promotion
(0–5 min) fades (h5 −0.206%, h30 −1.032%). Stage-B gate NOT triggered (best eligible +3.7bps ≪
120bps criterion; hindsight excluded by construction); Stage B(c) stays deferred. Sub-$1 cohort
recorded as labeled sensitivity (not silently dropped). Scope: falsifies marketable next-open
minute-level entries on the top-10 board in this coordinate set at h≤60; does NOT falsify passive
entries, other populations/states, longer holds, the funded/owned mechanisms, or H025. No
protected read; no FREEZE; no deploy; bot/flags untouched. EXP-89.
