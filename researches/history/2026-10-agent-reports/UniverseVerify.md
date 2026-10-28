# UniverseVerify — independent method verification of PRE-REG-DEPTH-UNIVERSE-01

Scope: validation claim (K stays 10; K=20/30 extras add 4 candidate states, 2 funded, 1 known
fill −$2.445; nothing in late; additive arm fails (a) and (c)). Read-only on code/outputs;
raw tarballs extracted under /tmp/uv1 (removed after).

## Verdict

**ARTIFACT.** The rarity of extra-name candidate states is GENUINE (a property of the frozen
head at rank_snapshot=11), but the reported *contribution* funnel and criterion (a) are an
artifact of a quote read-path defect: quotes acquired for exactly these extras are written to
`<stage>/quotes/quotes/<day>.parquet` and never read back, so their intents are recorded as
`quote_not_acquired` and dropped before funding. Fixed path: INAB (+$4.399) and HUDI
(+$127.891) are funded and known on validation, CNCK (+$46.004) late.

## The defect

`factory/scripts/alpha_depth_universe.py:1594`

```python
supp = tuple(frozen.SUPPLEMENT_ROOTS) + ((quotes_root,) if quotes_root else ())
qf = quote_day_frame(DATA, day, need, supp) if need else None
```

`ensure_quotes` -> `alpha_quote_universe_acquire.merge_day` writes to
`quotes_root/"quotes"/<day>.parquet` (`alpha_quote_universe_acquire.py:541`), and
`alpha_depth_universe.py:1683` checks that exact path for readiness. But
`quote_day_frame`/`supplement_day_path` resolves a supplement root as `root/<day>.parquet`,
so passing `quotes_root` (`<stage>/quotes`) looks for `<stage>/quotes/<day>.parquet`, which
does not exist. Every newly acquired quote is therefore invisible to the same run (and to any
rebuild, because the restored tarball keeps the deep layout). `intent_facts` then returns
`quote_not_acquired` (no symbol stream) and `replay_day` never funds the intent.

Evidence:
- Manifest of 2023-04-24: `candidates:["INAB"], statuses complete, merge.path
  .../_staging/2023-04-24/quotes/quotes/2023-04-24.parquet` — the file the run should use.
- k10_control has 0 `quote_not_acquired` intents; k20_add_A has exactly 2 — INAB (2023-04-24)
  and HUDI (2023-04-28), the only extras whose quotes were acquired in-run. The other two
  candidates (SDA, SNES) already sit in `data/sip/net/quotes` (primary cache), which is why
  they were visible.
- Reproduced `build_day_cells` on the extracted tarball: as coded, `quote_not_acquired=1`,
  0 funded, no trade; with `quotes_root/"quotes"`, INAB funds 343 sh, known +4.398975 @25
  (+1.93795 @50). 2023-04-28: HUDI funds 50 sh at 7.58 (exit bid 10.16 at 19:21Z, bar path
  7.50->10.20, +36%), known +127.89125 @25 (+126.7825 @50).
- Late: 2025-12-01 CNCK (quote manifest candidates ["CNCK"], complete, deep path). As coded
  `quote_not_acquired`, unfunded; correct path known +46.00375 @25 (+45.0075 @50).

The report's "equivalence verified on days acquired both ways" is vacuous under this bug:
both runs read only primary/frozen quote stores, so they agree trivially. The "quote volume
cut to what the replay can consume" is moot — none of the in-run acquisitions is consumed.

## Funnel — validation 2023 (250 sessions)

Extra names = admitted[K] \ admitted[10] (additive arm), source `derived/*.cov.json`,
`daily.parquet`, `plan/*.json`; preds independently recomputed from net tape for the 2,837
extra pairs whose bars are in the net tape (`symbol_rows` + `day_context(panel day)` + frozen
head), reproducing the 4 candidate states exactly.

| stage | K=20 extras | K=10 top-10 |
|---|---|---|
| (day,ticker) pairs | 5,169 (20.7/session) | 6,257 (25.0/session) |
| with minute bars | 5,018 (97.1%) | 6,257 |
| panel rows | 153,813 | 222,542 |
| passing liquidity gate | 89,288 (58.1%) | 149,284 (67.1%) |
| pred >= 0.030 (states) | **4** (0.0045% of qualified) | 107 |
| quotes at intent | 2 (SDA, SNES; both pre-existing) | 107 |
| funded intents | 2 recorded -> **4** corrected | 62 |
| known fills | 1 (−$2.445) -> **3** (mean **+$43.28**) | 53 |
| $/session @25 | 1.35375 -> **1.88291** | 1.36353 |

The 4 candidate states: INAB 2023-04-24 .03209, HUDI 2023-04-28 .03381, SDA 2023-05-31
.03708 (unknown exit; bars end 14:49, no 15:55 print), SNES 2023-12-28 .03240 (known −2.445).
K=30 (9,416 pairs) adds 0 further candidates: k20_add_A and k30_add_A daily cells are identical
on 250/250 days; late K=20 adds 0, K=30 adds CNCK (1).

Pred distribution, qualified extra rows (net-tape subset, 53,299): min −0.0562, median
+0.00037, p90 0.0016, p99 0.0044, max 0.0371, mean −0.00014. K10 panel (149,284 qualified):
median +0.0001, p99 0.0114, max 0.0595. The mechanism is rank, not features: within the
frozen K10 panel itself, rank_snapshot=11 rows (72,239) have mean −0.00066, max 0.0427 and
**6** states >=0.030, versus rank 1 (10,850 rows, 67 states). Extras are 99.9% rank-11 rows
(53,263 of 53,299; 4/53,263 >= 0.03). So "the head rarely scores rank-11 gainers >= 0.030"
is genuine in-sample behaviour of the frozen head, not an encoding/feature artifact.

## Registered construction checks

- Additive top-10 rows bit-identical: states = stored ∪ extra candidates; replay only changed
  the two days with a funded extra (2023-05-31, 2023-12-28). Per-ticker net/status/qty/limit
  for the control's own trades are identical between k10_control and k20_add_A on all 250
  days. (2023-12-28 adds SNES alongside SIDU; SIDU's fields unchanged.)
- rank_snapshot encoding: `encode_rank(..., "A")` caps at 11 with `min_horizontal`; observed
  additive values {1:2, 5:4, 7:4, 11:17,526}; the 4 candidates all 11.0; admission ranks
  11/12/19/11.
- Peer context: extras join `day_context(<panel day file>)`, i.e. the same pre-liquidity
  (day,t) window `load_panel` uses for the stored rows — not a context over the stored
  post-liquidity rows. `k10_pooled_rebuild.control_state_match.bit_identical` is true on
  250/250 days (max abs pred diff 0.0), so the context function and encoding reproduce the
  frozen states exactly. context_null_rows = 0 on every day.
- Data guard: outputs contain only 2023-01..12 and 2025-02..2026-05; no sealed prefix present.

## Hand-checks (raw bars, 6 extra name-days)

All recomputed with the same `symbol_rows` path. Net-tape source unless noted; top-10 peers =
frozen states at the same t (max pred among them).

| day | ticker | admit t/rank | bars | features at candidate t | pred | peers |
|---|---|---|---|---|---|---|
| 2023-04-24 | INAB | 575 / 11 | net 390 | t680 gain_open .1265, log_dv 19.04, bars15 15, peer_gain .1180 | .03209 | .00586 |
| 2023-04-28 | HUDI | 575 / 12 | net 387 | t860 gain_open 1.2435, log_dv 18.92, bars15 15, peer_gain .2220 | .03381 | .00129 |
| 2023-05-31 | SDA | 600 / 19 | net 306 | t895 gain_open .5191, log_dv 18.50, bars15 15, peer_gain .0682 | .03708 | .00375 |
| 2023-12-28 | SNES | 720 / 11 | net 382 | t750 gain_open .1477, log_dv 17.90, bars15 15, peer_gain .2870 | .03240 | .01220 |
| 2023-05-15 | ACET | 585 / 30 | provider 209 | 28 rows, bars15 4-12, log_dv 12.62-14.40 | 0 qualified | — |
| 2023-05-15 | SANW | 585 / 29 | provider 52 | 5 rows, bars15 2-3, log_dv 10.18-11.07 | 0 qualified | — |

Spot values match the raw minute tape (e.g. INAB close 2.855 vs open 2.535 = +12.6%; HUDI
7.50 vs 3.344 = +124%; SDA 27.58 vs 18.15 = +52%; SNES 1.785 vs 1.555 = +14.8%). Bars
present, features internally consistent, ranks/peer vectors as registered, and preds plausible
(extras score with the panel's rank-11 rows, not with the rank-1 rows the control trades).
ACET/SANW are genuine liquidity-gate rejections (sparse minutes), the same gate applied to
top-10 names.

## Secondary observations (not the verdict)

- 151 extra pairs (2.9%) never got bars: 79 on the 53 days built by the earlier producer
  version (pre-stage1 tarballs restored, no `bars/` file), 72 on 7 new-version days whose
  tarballs lack `bars/`. At the observed 4/5,018 hit rate they would add ~0.1 candidate state.
- 53 validation days carry an older cov schema (`combined_rows = stored + qualified`, no
  `candidate_rows`); replay filters pred>=0.030 anyway, and their daily cells match the
  report, so this is not a source of wrong results.
- Sensitivity of the frozen rule to the fix: (a) flips FAIL->PASS (1.8829 >= 1.363527),
  (b) still PASS (~1.50 @50), (c) still FAIL (3 < 10 known fills), (d) FAIL (additive
  increment +0.519 vs pooled −2.879: signs disagree). Decision is still K=10, but the
  published reasons and the "measured negative / inert / late bit-identical" narrative are
  not correct.
