# PRE-REG-MICRO-01 — IEX feed fidelity (A) + post-fill microstructure (B)

Status: FROZEN 2026-09-12 before any computation. Measurement studies, NOT alpha
claims. Seen data (all 32 months seen). No live-bot change follows from either
study without a separate pre-registration.

Data:
- Frozen tape: `data/leaderboard/{path,lb}_YYYY-MM-DD.parquet` (667 usable days,
  2024-01-02..2026-08-31, 7015 candidate (date,ticker) pairs).
- Alpaca IEX 1-min historical bars, cached to `data/iex_tape/` (resumable).
- Alpaca historical SIP trades/quotes (≥15-min rule; Basic plan) for Study B.

Integrity: no lookahead; IEX tape mirrors `lb18.py:81-129` (`MIN_GRID=570..959`,
ffill OHLCV, decision at t uses completed bar `et<=t-1`, `n_bars` counts real
IEX bars only). Pairs with zero IEX bars are omitted (= invisible to live feed).

## Study A — IEX-feed replay of the frozen entry mechanism

Question: with the same candidate gate (frozen `lb`: rank≤3, gain≥1, px) and the
same rolling-bid engine (`lb18_oos.run_engine`), how does the IEX bar tape
change states, entries, fills, anchors and exits vs the frozen consolidated tape?

Design (single substitution, everything else frozen):
- IEX arm: `run_engine(iex_paths, lb_frozen)`. `lb` unchanged (live scanner is
  consolidated Alpaca movers, so frozen rank/gain is the scanner proxy).
  `prior_flush`, `pullback`, `r15`, fills and exits all ride the IEX tape.
- Reference: frozen fills `lb18_oos_oos.parquet` (OOS) + `lb18_oos_dev.parquet`.
- Parity guard in-script: engine on frozen paths must reproduce frozen stats
  exactly (OOS all n=541 +0.91%; pf2 n=381 +1.14%) before any IEX number is read.
- Matching: same (date,ticker) and `|tf_iex - tf_frozen| <= 5` min = matched.
  Frozen fill with no IEX match = missed. IEX fill with no frozen match = spurious.
- Drift on matched: ΔB in bps (`B = c0*(1-0.10)`), Δtf, Δt0.
- Economics: IEX-arm EV (frozen engine, 100bps) all/pf2/months+; spurious fills'
  own outcome distribution; missed fills' frozen outcomes (opportunity lost).
- Exit-nuance sensitivity: IEX arm re-run with clock-minute tl30 (live
  translation) to separate entry effects from exit-horizon effects.
- Universe scope: OOS primary; dev replication. Same 667 days for tape.
- Cross-check with production overlay: feed effect (A) × policy effect
  (`lb18_overlay.json`) compose; report both, do not multiply blindly.

Gate A (frozen now, decision-relevant, not tuned): IEX "preserves entry
expression" iff missed_pf2 <= 10% of frozen pf2 fills, spurious <= 10% of frozen
fills, pf2 EV within ±0.30pp of frozen, months+ >= 12/14. Otherwise the IEX tape
materially changes the strategy → paid real-time SIP has a direct entry-fidelity
case (quantify with the missed/spurious reconstructed paths).

## Study B — post-fill microstructure, decision-anchored

Question: can causal microstructure right after the assumed fill separate the
catastrophic tail from snapbacks, and is any of it actionable at latencies this
system can achieve?

Population: OOS 541 fills (primary) + dev fills (replication); pf≥2 split is
phenomenology only.

Anchoring and features:
- `t_anchor` = first SIP trade at/below B. This is NOT a fill-latency
  measurement (queue position is unobservable historically).
- Features at Δ ∈ {5,15,30}s after `t_anchor`, frozen set, no Δ tuning: dwell
  time at/below B so far; volume and trade count at/below B so far; max depth
  below B; snapped back to >= B by Δ. IEX arm: same trade features and whether
  the best IEX bid is still >= B (deployability check on the live feed).
- Outcomes strictly after `t_anchor+Δ`, on new bars: stop-first / target-first /
  unresolved (path return net 100bps). Exit-slippage proxy: first trade price
  after Δ when a stop-side exit triggers.
- Latency ladder: capability to act at `t_anchor+Δ` AND after detection lag
  (~current 60s poll). Report rescuable share at each rung.
- Rescue 2×2 at each Δ: early-stop (resolved before Δ; unrescuable), early-target
  (must not sacrifice), alive→later-stop (the prize), alive→later-target,
  unresolved. Primary output is this decomposition, not classification accuracy.
- Baselines to beat (already computed, reuse, do not re-derive): unconditional
  tighter stops (`lb18_exec.json` stop08/12/15), pf2 clean-touch-only, baseline
  10% stop.
- Paging prerequisite: `lb18_fills_micro.fetch()` must page via
  `next_page_token` (SDK stops at `limit`); current truncation 3.3% overall /
  7.1% strong group biases exactly the interesting cells.

Gate/deprioritize B (frozen now): if no Δ/latency rung yields an alive→later-stop
subset whose reconstructed intervention improves net P&L after slippage without
materially eroding alive→later-target economics (vs the tighter-stop baselines),
deprioritize the simple post-fill management approach and keep the baseline
stop. The broader microstructure lane stays open.

Artifacts: `factory/artifacts/lb18_iex_feed.json` + `.parquet`;
`factory/artifacts/lb18_subminute.json` + `.parquet`. Ledgers: EXPERIMENTS.jsonl,
HYPOTHESES.jsonl, factory/STATE.md.

Commitments: entry rule untouched; no rule adopted from these studies without a
new pre-reg + OOS; subscription cost (~$99/mo, if ever) is a separate decision
justified by the reconstructed path economics, not by fill counts alone.

---

## Amendment A1 — Study A decomposition (pre-registered 2026-09-12, BEFORE running)

Study A (A2: IEX tape substituted everywhere) returned **Gate A FAIL**: OOS all
n=456 −2.54%, pf2 n=256 −1.56%, 3/14; dev pf2 −1.25%. The failure signature is
asymmetric: the 174 missed frozen fills were *winners* (median frozen ret
+10.11% = target hits), the 89 spurious IEX fills were *losers* (median IEX ret
−11.00% = stop hits). IEX-only fill detection is adversely selected, so A2
conflates two effects:
- STATE: which strict-state minutes / anchors / B the feed produces.
- FILLS: whether the feed's prints touch B. Live fills are CONSOLIDATED (the
  bot's limit order rests at the broker, not on IEX), so A2 is stricter than live.

Decision-relevant question: is the live state feed implicated, or is A2's FAIL a
fill-venue artifact?

**2×2 decomposition** (frozen candidate gate `lb` unchanged) over
(state/anchor source) × (execution/fill source), with B/c0 derived from the
state source:
- **A3 = (IEX state, frozen exec)** — the deployable hybrid: IEX-observable
  state + B, consolidated fills/exits.
- **A4 = (frozen state, IEX exec)** — pure fill-venue control.
- **A3a = A3 with B/c0 from the frozen close at the IEX anchor minute** —
  sub-variant splitting anchor/cadence drift from B-level drift.
- (IEX, IEX) = A2 (already run); (frozen, frozen) = frozen baseline (parity).

Also report descriptive, no gate: paired matched-subset ret delta (frozen − IEX)
on the 367 matched fills.

**Frozen gates (evaluated on OOS 2024-01..2025-02):**
- A3 PASS iff pf2 EV within ±0.30pp of frozen (+1.14%) AND months+ >= 12/14 AND
  frozen pf2 fills lost to IEX anchor/B drift <= 10%.
- A4 PASS iff pf2 EV within ±0.30pp AND missed <= 10% — expected to FAIL; a pass
  would falsify the fill-venue story.

**Decision rule (frozen now):**
- A3 PASS -> Study A FAIL is attributable to IEX-only fill detection; the live
  IEX state feed is NOT implicated; keep IEX state in `flush_bot.py`; Study B
  proceeds on the frozen fill population.
- A3 FAIL -> the live state feed is implicated. Use A3a vs A3 to split
  anchor/cadence drift from B drift; candidate remedies (no adoption without a
  new pre-reg): consolidated B/c0 source while keeping IEX state; real-time SIP
  (Algo Trader Plus, ~$99/mo, separate priced decision); or a reduced rule.

Status: seen-data measurement, not an alpha claim. Artifacts:
`factory/artifacts/lb18_iex_hybrid.json` + `.parquet` (new script
`lb18_iex_hybrid.py`; existing scripts are not edited).
