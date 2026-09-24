# F3 — causal survival / release surface

**Status: RUN; development-only description, no selection or edge claim.** This artifact
evaluates the complete registered F3 R2/R3 surface through the unchanged canonical simulator.
The exact ten controls/modules × two frictions are recorded in `configs.json` and
`surface.json`; each cell directory under `F3/` contains the engine's native `config.json`,
incremental month parts, merged `daily.parquet` and `tickets.parquet`, `metrics.json`, and
`run_summary.json`. `provenance.json` hashes the contract, calendar, day manifest, and every
canonical input file. `surface.json` retains every cell, including null/negative results, and
includes pooled and block means/deltas against same-friction R0.

## Frozen method

- Entry/control path: canonical `A_pm` anatomy at T=570, stored anatomy fill, top 2 by
  canonical rank, two equal slots, one sleeve with C0=1, no leverage. A_pm/N=2 is a registered
  F1 configuration and is held fixed to isolate release behavior; no F2/F12 state or result
  gates a cell. Each release cell is compared with this exact same-friction registered R0
  control path.
- Release grid: R0 hold control; R2 with loss depth L ∈ {10,15}% and grace window w ∈
  {3,5,10} completed bars; R3 with prior-running-peak giveback g ∈ {40,50,60}%. Friction is
  total round trip 100 or 150 bps. There are no composites, additional thresholds, or
  outcome-derived parameters.
- Timing: canonical simulator rules unchanged. Decisions use completed bars, R2/R3 actions
  execute at the first later available bar open, and session/calendar, adverse level price,
  forced-flat, halt/carry, cash-slot, and friction behavior come from
  `factory/BASKET-SIM-CONTRACT.md` and `basket_sim.py`.
- Permitted data only: canonical SIP anatomy, bars, and committed Phase-2 calendar for dates
  2021-02–2023-12 and 2025-02–2026-05. Sealed 2024 / 2025-01 and reserved 2026-06–08 are
  excluded. Both frozen blocks are reported independently: block1 (734 days / 35 months),
  block2 (332 days / 16 months); pooled total is 1,066 days / 51 months.

## Results [RUN]

All 20 cells completed on all 1,066 dates. Every pooled cell mean was negative. Across the
complete control+release surface, pooled means were **−4.12% to −2.99% at 100 bps** and
**−4.59% to −3.47% at 150 bps**. Block 1 means were **−4.09% to −2.70%** and
**−4.56% to −3.18%**, respectively; block 2 means were **−4.19% to −3.27%** and
**−4.67% to −3.75%**. These ranges span all parameter cells at the same friction and are
descriptive only.

Against the same-friction R0 A_pm/N=2 comparator, all nine release settings had positive
mean deltas within each block in this development run: block 1 deltas span **+0.06 to +1.39
percentage points at 100 bps** and **+0.06 to +1.38 points at 150 bps**; block 2 deltas
span **+0.26 to +0.92 points** and **+0.26 to +0.92 points**. Pooled release-cell deltas
span **+0.12 to +1.13 points at 100 bps** and **+0.12 to +1.12 points at 150 bps**. This
is a shared-development comparison, not a causal inference or profitability result; all net
means remain negative and there is no selection or confirmation claim.

Across cells, the simulator reports 2,115–2,118 ticket entries, 7 blocked slots, and
2,102–2,109 exits; open-end carries/pending range from 4,366 to 5,921 day-counted events.
Across each 1,066-day cell, summed realized closed-ticket net ranges from −43.95 to −32.00
C0 units at 100 bps and −48.97 to −37.09 at 150 bps; open-end marked ticket net ranges from
+0.02 to +0.16 and +0.001 to +0.15, respectively. Estimated explicit entry/exit friction
charges sum to 10.32–10.40 C0 units at 100 bps and 15.44–15.57 at 150 bps. These sums are
across ticket rows, not daily returns. `ticket_gross_net_before_friction` is the net of
fee-adjusted share quantities before subtracting the estimated explicit charges; it is not a
separate zero-friction simulation or a re-sized gross strategy.
Mean failed-ticket net return ranges from −16.54% to −12.04% at 100 bps and −16.68% to
−12.32% at 150 bps. Mean retained-net/raw-MFE for tickets with raw MFE ≥50% ranges from
0.336–0.382 at 100 bps and 0.329–0.374 at 150 bps (164–195 such tickets per cell).
Contract-defined pooled false-release rates are 3.62–4.44% for H=30, 5.08–6.67% for
H=50, and 4.29–5.00% for H=100; path-contribution shares are null where required by the
contract because total net P&L is nonpositive. Cell-level realized closed-ticket net, marked
open-end ticket net, tail/action counts, turnover, deployment, drawdown, monthly summaries,
and year/quarter metrics remain in `surface.json` / each `metrics.json`. Half-release rates
are 0: these cells never issue REDUCE actions. The contract's average deployed-capital metric
aggregates concurrent independent basket-day sleeves and can exceed 1 while each sleeve still
obeys its own C0/cash invariant; this is not leverage.

R0 and all release rules are reported with equal prominence. Net returns include the specified
friction; these ranges are not an invitation to choose the maximum-delta cell. The tested
cells share dates, names, and one entry path and are not independent replications.

See `surface.json` for every cell's pooled mean, block means/deltas, monthly summaries, exit
counts, realized/open-end ticket net, and block tail measures. Per-cell `metrics.json` contains
simulator bootstrap, daily distribution, drawdown, turnover, blocked-slot/action counts,
failure/tail metrics, and year/quarter summaries. Month-keyed daily/ticket parts are preserved
under each cell's `parts/` for incremental recovery and audit.

## Measurement limits

No tested result is promoted, selected, called profitable, generalized out of sample, or
recommended for live use. Reported ticket-level marked totals are the net values of open-end
tickets (F3 has no partial reductions); closed-ticket totals are realized. Daily simulator
returns combine realized cashflow and changes in open marks. False/half-release and retained-
tail quantities are engine-defined pooled metrics; the native ticket parquet does not retain
intraday first-touch/action chronology for reconstructing those metrics separately by block.
The raw MFE and release action outcomes must not be conflated: MFE is not an executable fill.
