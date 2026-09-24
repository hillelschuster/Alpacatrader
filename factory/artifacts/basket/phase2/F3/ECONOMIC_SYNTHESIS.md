# F3 economic mechanism synthesis

**Evidence: [RUN]. Development-only, full frozen surface; no cell selected.**

## Direct answer

Release rules reduce losses relative to same-friction R0 broadly across the tested surface and both blocks, but they do not make the tested mechanism profitable: all absolute mean daily C0 returns remain negative. The economic trade is real, not settled in favor of release: F3 must be judged by ordinary failed-ticket loss removed versus positive raw-MFE tail ticket contribution foregone, not classifier accuracy. Raw MFE is a path touch, not an executable fill.

## Measurement boundaries

- One C0 sleeve per basket-day; daily mean is C0 EV. Ticket-net sums are not daily return sums. Closed-ticket P&L is realized; open-end ticket net is marked.
- Friction is tested at 100/150bps round trip. `friction_cost_estimate` is the explicit ticket charge estimate, not a separate gross simulation.
- Exit ET is the next-available-bar execution time. False-release values are pooled engine metrics. Trigger ET and per-block false-release/first-touch chronology are absent; do not reinterpret execution timing as signal timing. Half-release is 0 for every cell (no REDUCE actions).
- F8 concurrent survivor metrics share dates and ticket members across cells; correlation/overlap are descriptive, not independent observations.

## Complete 20-cell economic surface

All quantities in returns use fractions (e.g., -0.04 = -4%). Tail columns show ticket-net P&L summed for tickets whose raw post-fill MFE touched the stated level; these are not executable proceeds.

| Rule | bps | C0 EV | Δ vs R0 | Failed mean / n | Failed net Δ vs R0 | Release / forced-flat exits | Median release exec ET | False release 30/50/100 | Realized ticket net | Marked ticket net | MFE≥50 net (Δ vs R0) | ≥100 net (Δ) | ≥200 net (Δ) | Capture ratio ≥50 | DD | Friction |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| R0 | 100 | -0.0412 | +0.0000 | -0.1654 / 1452 | +0.000 | 0 / 2102 | None | 0.044/0.067/0.043 | -43.951 | 0.017 | 44.538 (+0.000) | 31.911 (+0.000) | 19.422 (+0.000) | 0.347 (195) | -1.000 | 10.322 |
| R0 | 150 | -0.0459 | +0.0000 | -0.1668 / 1476 | +0.000 | 0 / 2102 | None | 0.044/0.067/0.043 | -48.973 | 0.001 | 43.831 (+0.000) | 31.577 (+0.000) | 19.276 (+0.000) | 0.339 (195) | -1.000 | 15.444 |
| R2_L10_w10 | 100 | -0.0324 | +0.0088 | -0.1266 / 1544 | +22.323 | 1125 / 982 | 614 | 0.038/0.053/0.048 | -34.751 | 0.164 | 36.707 (-7.831) | 25.909 (-6.002) | 14.924 (-4.498) | 0.342 (169) | -1.000 | 10.386 |
| R2_L10_w10 | 150 | -0.0372 | +0.0087 | -0.1292 / 1565 | +21.982 | 1125 / 982 | 614 | 0.038/0.053/0.048 | -39.832 | 0.151 | 36.103 (-7.727) | 25.625 (-5.952) | 14.808 (-4.468) | 0.334 (169) | -1.000 | 15.540 |
| R2_L10_w3 | 100 | -0.0313 | +0.0099 | -0.1204 / 1565 | +25.817 | 1192 / 917 | 604.0 | 0.039/0.055/0.050 | -33.494 | 0.120 | 35.324 (-9.214) | 24.773 (-7.138) | 14.731 (-4.692) | 0.337 (164) | -1.000 | 10.399 |
| R2_L10_w3 | 150 | -0.0361 | +0.0098 | -0.1232 / 1586 | +25.406 | 1192 / 917 | 604.0 | 0.039/0.055/0.050 | -38.587 | 0.109 | 34.738 (-9.093) | 24.500 (-7.078) | 14.615 (-4.661) | 0.329 (164) | -1.000 | 15.560 |
| R2_L10_w5 | 100 | -0.0299 | +0.0113 | -0.1219 / 1556 | +25.227 | 1164 / 944 | 608.0 | 0.039/0.055/0.048 | -31.995 | 0.112 | 37.300 (-7.238) | 26.787 (-5.124) | 16.102 (-3.321) | 0.346 (165) | -1.000 | 10.404 |
| R2_L10_w5 | 150 | -0.0347 | +0.0112 | -0.1246 / 1577 | +24.843 | 1164 / 944 | 608.0 | 0.039/0.055/0.048 | -37.093 | 0.099 | 36.703 (-7.128) | 26.499 (-5.079) | 15.976 (-3.299) | 0.338 (165) | -1.000 | 15.568 |
| R2_L15_w10 | 100 | -0.0346 | +0.0066 | -0.1425 / 1492 | +13.765 | 820 / 1284 | 636.0 | 0.039/0.056/0.044 | -36.987 | 0.100 | 39.964 (-4.574) | 29.480 (-2.431) | 16.908 (-2.514) | 0.336 (179) | -1.000 | 10.364 |
| R2_L15_w10 | 150 | -0.0394 | +0.0066 | -0.1445 / 1516 | +13.595 | 820 / 1284 | 636.0 | 0.039/0.056/0.044 | -42.050 | 0.085 | 39.320 (-4.511) | 29.164 (-2.414) | 16.777 (-2.499) | 0.329 (179) | -1.000 | 15.508 |
| R2_L15_w3 | 100 | -0.0338 | +0.0074 | -0.1387 / 1501 | +15.981 | 873 / 1232 | 623 | 0.036/0.051/0.045 | -36.173 | 0.156 | 39.095 (-5.443) | 28.339 (-3.573) | 16.102 (-3.320) | 0.340 (177) | -1.000 | 10.371 |
| R2_L15_w3 | 150 | -0.0386 | +0.0074 | -0.1409 / 1524 | +15.777 | 873 / 1232 | 623 | 0.036/0.051/0.045 | -41.242 | 0.142 | 38.460 (-5.371) | 28.030 (-3.547) | 15.977 (-3.299) | 0.332 (177) | -1.000 | 15.518 |
| R2_L15_w5 | 100 | -0.0341 | +0.0071 | -0.1402 / 1498 | +15.074 | 866 / 1239 | 627.0 | 0.036/0.051/0.044 | -36.556 | 0.156 | 39.522 (-5.016) | 28.878 (-3.033) | 16.140 (-3.282) | 0.341 (177) | -1.000 | 10.369 |
| R2_L15_w5 | 150 | -0.0389 | +0.0070 | -0.1423 / 1521 | +14.882 | 866 / 1239 | 627.0 | 0.036/0.051/0.044 | -41.623 | 0.142 | 38.885 (-4.946) | 28.564 (-3.013) | 16.015 (-3.261) | 0.334 (177) | -1.000 | 15.515 |
| R3_g40 | 100 | -0.0361 | +0.0051 | -0.1579 / 1443 | +6.159 | 272 / 1831 | 721.5 | 0.039/0.057/0.043 | -38.490 | 0.017 | 46.502 (+1.964) | 32.832 (+0.921) | 17.834 (-1.588) | 0.382 (194) | -1.000 | 10.354 |
| R3_g40 | 150 | -0.0408 | +0.0051 | -0.1592 / 1469 | +6.149 | 272 / 1831 | 721.5 | 0.039/0.057/0.043 | -43.542 | 0.001 | 45.788 (+1.957) | 32.494 (+0.916) | 17.698 (-1.578) | 0.374 (194) | -1.000 | 15.493 |
| R3_g50 | 100 | -0.0380 | +0.0032 | -0.1625 / 1446 | +2.590 | 106 / 1996 | 789.0 | 0.042/0.062/0.043 | -40.508 | 0.017 | 46.837 (+2.299) | 33.812 (+1.900) | 20.033 (+0.610) | 0.367 (194) | -1.000 | 10.339 |
| R3_g50 | 150 | -0.0427 | +0.0032 | -0.1640 / 1470 | +2.592 | 106 / 1996 | 789.0 | 0.042/0.062/0.043 | -45.548 | 0.001 | 46.121 (+2.290) | 33.468 (+1.891) | 19.883 (+0.607) | 0.359 (194) | -1.000 | 15.470 |
| R3_g60 | 100 | -0.0400 | +0.0012 | -0.1637 / 1452 | +1.242 | 43 / 2059 | 759 | 0.044/0.067/0.043 | -42.633 | 0.017 | 45.371 (+0.833) | 32.605 (+0.693) | 19.579 (+0.157) | 0.354 (195) | -1.000 | 10.328 |
| R3_g60 | 150 | -0.0447 | +0.0012 | -0.1651 / 1476 | +1.236 | 43 / 2059 | 759 | 0.044/0.067/0.043 | -47.662 | 0.001 | 44.660 (+0.829) | 32.267 (+0.690) | 19.431 (+0.156) | 0.346 (195) | -1.000 | 15.454 |

## Block, month, year and joint evidence

Tail deltas in the table are ticket-net outcome differences versus same-friction R0 among all tickets that ultimately touched each raw-MFE threshold, not cash captured at release. R2 cells reduce mean failed-ticket return by about 2.0–4.5 percentage points and lower the failed-ticket net-loss sum, while their MFE≥50 ticket-net sum is 4.5–9.2 ticket-net units lower than R0 (all tickets remain in the full-ticket run; these tails are cohort contrasts, not observed premature-exit losses). R3 provides the contrasting state/action shape: ≥50 ticket-net sum is about +0.8 to +2.3 versus R0, but mean failed loss improves by only about 0.1–0.8 points. Both changes persist at 150bps with near-identical rankings/magnitudes. Mean C0 improvement is not confined to one event: positive release-vs-R0 months range 20–38 of 51 across tested release cells; pooled MFE≥50 ticket-net's largest single-month share spans 7.7%–9.9% across cells. Per-threshold month detail is retained in JSON.
Mean daily C0 Δ ranges by block (fraction; 18 release cells / friction): block1 100bps +0.0006 to +0.0139, 150bps +0.0006 to +0.0138; block2 100bps +0.0026 to +0.0092, 150bps +0.0026 to +0.0092. Absolute annual/quarterly per-cell results and the full 51-month table are in JSON.
Per-cell JSON retains pooled and both block failed-ticket, release, realized/marked, MFE contribution, capture, C0 EV, drawdown, friction, plus month-by-month C0 and ticket/action metrics; annual and quarterly metric tables are copied from each native metrics artifact. Each block is compared with same-friction R0; complete values are in `economic_synthesis.json`.

F8 does not contain F3 release-rule runs. For context only, each F3 cell carries the matching A_pm/N=2 R0 F8 concurrent baseline (100/150bps): all/≥2 profitable and raw-MFE threshold frequencies, member net/MFE distributions, pairwise correlation and pair counts, pooled and by block. This does not estimate joint behavior under R2/R3. Same date/member overlap remains, and raw-MFE survivor counts do not establish executable capture.

## Mechanism read and bounded handoff

**Established:** relative C0 mean improvement is broad over the frozen release surface and appears in both development blocks; the absolute C0 mean is negative, so loss reduction alone is insufficient. Release exit ET/count and survivor tail ticket-net contribution are reported together in every cell.
**Unresolved:** native outputs cannot locate causal trigger ET or determine whether the path reaches H after release before any hypothetical executable reacquisition. Pooled false-release rates exist; block-level rates do not. Therefore early exits cannot be declared avoidable or beneficial from raw-path hindsight.
**Ranked F4 (bounded, not selected):** first test R2 entry-relative depth-after-grace scaleout because it removes the most failed-ticket loss but also has the largest tail cohort cost; contrast with R3 peak-relative giveback scaleout, which preserves tail ticket net better but improves failed losses less. Keep each tested rule/parameter frozen and report all declared tranches. Require positive incremental net C0 EV at 100/150bps and both blocks/months, while separating realized/marked and raw-MFE opportunity from actual tranche executions.
**F6/F7 (bounded next):** compare idle released cash and controlled redeployment for R2 and R3 actions into already-existing concurrent survivors, preserving C0 and no leverage. Require positive incremental redeployment contribution net of friction in both blocks, without tail concentration or overlap accounting errors. F3 supplies no evidence that released capital is profitably reusable.
**Falsified here:** the proposition that tested release makes this A_pm/N2 implementation profitable, and any claim that raw-MFE ≥50/100/200 is captured executable profit. No release threshold is selected or recommended.

## Provenance and limits

Inputs: F3 `surface.json`, structural map (`provenance.json`, `run_config.json`, `relationship_tables.json`), every cell's `daily.parquet`, `tickets.parquet`, `metrics.json`, and F8 `joint_surface.json`. Source SHA-256 values and the entire map are in the JSON artifact. Sealed and reserved periods remain excluded. No F3 runs/artifacts were changed.

The raw structural map records causal completed-bar state and strictly later path outcomes; those conditional future high/low/close paths are descriptive, not fills or a simulated counterfactual release. Cell comparisons share data and are not independent evidence.
