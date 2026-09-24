# BASKET-01 Phase-2 — canonical event-driven simulation contract

**Status: FROZEN 2026-09-22** with the PRE-REG-BASKET-02 family contract. Normative for
every Phase-2 strategy run. Any change to this file is a **new degree of freedom**: it must
be dated, declared in the run report, and invalidates comparability with prior runs unless
the affected families are re-run on the new contract.

> **Correction C1 (2026-09-24) — cross-session carry substrate, sleeve-scoped ticket
> identity, `deployed_end`, `half_release_rate`, execution chronology. See §13.**
> C1 is explicit and dated, not a silent semantic change. Runs whose tickets never carry,
> never share a ticker across sleeves, never reduce before a touch, and are not read via
> `deployed_end` are numerically unaffected; every other prior Phase-2 result was produced
> under pre-C1 semantics and must be re-run (or explicitly marked non-comparable) before it
> is used for a decision. `contract_version` for all post-C1 runs is
> `FROZEN-2026-09-22+SUBSTRATE-CORRECTION-2026-09-24`.

Companion: `researches/PRE-REG-BASKET-02.md` (families, grids, validation, DOF register).
Engine implementation: `factory/scripts/basket_sim.py` (must follow this file literally).

---

## 1. Why this exists

Ten workers must not invent ten meanings of "return". Every strategy family in Phase 2 runs
on this one engine: identical causal selection, identical fill semantics, identical
accounting, identical friction, identical metrics. Family modules may only compose the
action/rule vocabulary declared here and in PRE-REG-BASKET-02.

## 2. Substrate (inputs; read-only)

Canonical root: `factory/artifacts/basket/sip/` (`BASKET_ART_ROOT` override).

- `anatomy/YYYY-MM-DD.jsonl` — one JSON record per day; 15 snapshots
  (`A_open`, `A_pm`, `A_pm31` at T=570; `B` at T in {575,580,585,590,595,600,615,630,645,660,690,720}).
  Each snapshot: `{pop, T, names:[{ticker, rank, sel, sel_alt, px_decision, open0930,
  prev_close, split_flag, fill:{et,px,gap_min,blocked,alt_px}, pre_high, day_high, close,
  eod_ret, mfe, mae, i_mfe, i_mae, last_et, bars, gap_pre, gap_post, ladders:{up,dn},
  state, ratio_anchor}]}`. Top-10 names per snapshot; ranking tie-break = score desc,
  ticker asc (canonical).
- `bars/YYYY-MM-DD.parquet` — per-candidate 1-minute bars, schema
  `(date, ticker, et, open, high, low, close, volume)`; `et` = ET minute-of-day int;
  RTH range [570, 960).
- Raw SIP layer (`data/sip/`, `/home/hillel/sip/`) is available for verification and
  execution-realism work only; never a substitute for the artifacts above.
- **Carry substrate (C1, mandatory for carries):** `carry_bars/YYYY-MM-DD.parquet` +
  `carry_bars/manifest.json` — the declared full-market RTH overlay consulted only for
  cross-session carry resolution (§6, §13). Producer:
  `factory/scripts/basket_carry_bars.py`.
- **Session calendar (mandatory, committed):** `factory/artifacts/basket/sip/phase2_session_calendar.json`
  maps every dev day to `session_end` (last *regular-session* minute): 959 normally,
  779 on NYSE early-close days (13:00 ET close). Early closes in dev must be enumerated
  mechanically (volume falloff check: after-hours prints exist past the close) and verified;
  the file records the method and the day list. No Phase-2 run may trade bars with
  `et > session_end`. Half-day candidate list to verify: 2021-11-26, 2022-11-25, 2023-07-03,
  2023-11-24, 2025-07-03, 2025-11-28, 2025-12-24 (build + verify, do not trust the list).

**Evidence boundary (hard):** dev days = the 1,066 canonical days (2021-02..2023-12,
2025-02..2026-05). Sealed 2024 + 2025-01 SIP: never computed on. Reserved 2026-06/07/08:
never fetched, never read. The loader must refuse any day outside the dev set.

## 3. Clock, causality, decisions

- ET integer minutes (`et`). A bar with `et=t` is **completed** at minute `t+1`; every
  decision is made from completed bars only (1-bar lag, frozen doctrine).
- Every decision executes at the **open of the first bar with `et' > t`** for that ticker
  (within the session; see §6 for pending/halts). No same-bar execution anywhere.
- Entry fills are **not recomputed**: they are read from the anatomy snapshot `fill`
  (`et`, `px`, `gap_min`, `blocked`). The engine asserts `fill.px == bars.open[fill.et]`
  (1e-9) and flags any mismatch; anatomy is canonical.
- Ranking and selection for all families come from the anatomy snapshots (top-10 names),
  preserving the Phase-1 selection contract exactly. Family modules choose *within* the
  snapshot (e.g. top-N by the stored rank); they never re-rank on their own prices.
- **Ticket identity (C1):** an independent ticket is `(sleeve_day, ticker)`. The same ticker
  may be held simultaneously by two basket-day sleeves; a new sleeve's entry is never
  suppressed by another sleeve's open ticket, and closing one ticket never removes another.
  Within the entry session all execution stays on the canonical candidate bars.

## 4. Entry families (mapping; frozen in PRE-REG-BASKET-02)

| Family id | Snapshot (`pop`,`T`) | Selection known | Fill |
|---|---|---|---|
| `A_pm` | (`A_pm`, 570) | pre-bell premarket print | 09:30 bar open (anatomy fill) |
| `A_pm31` | (`A_pm31`, 570) | same selection | 09:31 bar open |
| `A_open` | (`A_open`, 570) | completed 09:30 bar | 09:31 bar open |
| `B585` | (`B`, 585) | completed 09:44 bar | first open `et>=585` |
| `B600` | (`B`, 600) | completed 09:59 bar | first open `et>=600` |
| `B575`, `B615` | (`B`, 575/615) | neighborhood stability points only | first open `et>=T` |

- A blocked fill (`blocked:true` or no bar) = **cash slot**, retained in the denominator,
  reported (`n_blocked_slots`). No backfill, no replacement, no close substitution.
- Fewer than N qualifiers = fewer slots; empty days are retained in all denominators.
- Slot i takes an equal budget (see §5); one position per ticker; duplicates across
  snapshots are irrelevant (a run uses one entry family at a time).

## 5. Position & action model

Ticket state: `{ticker, entry(fill et,px), shares, unit_notional, cash_in, realized, peak,
mfe, mae, actions[]}`. Fractional shares are allowed in the simulator (notional accounting);
rounding for live execution is an execution-realism question (family F11), not a core concern.

| Action | Size vocabulary | Trigger (completed bar t) | Execution |
|---|---|---|---|
| `ENTER` | full slot budget | entry family | anatomy fill |
| `ADD` | +25% / +50% / +100% of **original unit notional** | strategy-declared strengthening module | next bar open |
| `REDUCE` | 25% / 33% / 50% of **current shares** | strategy-declared deterioration module | next bar open |
| `EXIT` | 100% of current shares | release module fired / forced flat | next bar open |
| `HOLD` | — | default | — |

- **No leverage, ever.** Engine invariant: `cash >= 0` at every event; total deployed
  notional ≤ C0; an ADD that cannot be fully funded from available cash is **skipped**
  (flagged), never partially filled.
- **Add cap:** total ADD notional per ticket ≤ 2.0× unit notional (i.e. ≤ +100% total adds).
  Exceeding the cap = strategy bug; engine flags and skips.
- An add may only be justified by the candidate's **own state** (thesis lock); the engine
  cannot enforce semantics, but family reports must cite the causal justification.

## 6. Session end, forced flat, carries

- `session_end(day)` from the committed calendar. Session bars = `et <= session_end`.
- **Forced flat:** decision at the last completed session bar (`et = session_end - 1`);
  exit at the `open` of the last session bar (`et = session_end`). If a release also
  fires that bar, the exit price is the adverse convention: `min(open, level)` for
  level-type exits (§7).
- **Pending through halt (C1):** a triggered action with no later session bar stays pending
  and executes at the **first genuinely executable bar of the next session in which the
  ticker trades** — resolved from the declared full-market carry substrate (§13), never
  from the next candidate-neighborhood appearance of that ticker. Price is `min(px, level)`
  for level-type exits; the position is an involuntary carry, keeps its slot (no leverage),
  and is flagged (`n_carries`). A later session in which the substrate certifies `no_bars`
  for the ticker is a genuine no-trade day: the carry continues (flagged `no_resumption`)
  until a bar appears or the dev data ends (terminal mark at last close, flagged). An
  uncertified or `unavailable` (day, ticker) is a **hard error**
  (`basket_sim.MissingCarrySubstrate`), never a silent "no trade" and never a candidate-bar
  fallback. Carry resolution never inspects sealed 2024 / 2025-01 or reserved
  2026-06..08; it walks the declared dev-day sequence only and stops at dev-block boundaries.
- Carried P&L is attributed to the originating basket-day in ticket rows, but the
  basket-day return is only recognized when realized (exit); marked value is reported
  separately (`realized` vs `marked`). Daily return uses realized P&L of the day plus the
  day's marked change for open tickets (mark-to-market), so carry days are not
  zero-return days.
- **Dev-block boundary (C1):** a carry may not bridge a declared dev-block gap (the
  2023-12 -> 2025-02 gap is a data boundary, not a tradable overnight). A ticket still open
  on the last dev day before a gap is terminally marked at its last close
  (`exit_reason=BLOCK_BOUNDARY_MARK`, `exit_day`/`exit_et` null, flagged
  `block_boundary_carry`) and retained in closed tickets, so it stays in the final ticket
  table and metrics. The mark was already part of that day's P&L; nothing is double-counted.

## 7. Deterministic event order (engine must implement exactly)

Per completed bar `t`, per ticket, in this order:

0. **Pending execution:** an action already pending from an earlier completed bar (same
   session or a carried cross-session action) executes at the open of the first eligible bar
   `et' > decision_et` (carries: first bar of the new session's tape), price
   `min(open, level)` for level-type exits. A pending action is never overwritten.
1. **Forced-flat check** (`t == session_end - 1`): emit `EXIT` **only for tickets with no
   pending action**; skip rules below for those tickets. A ticket whose action is already
   pending keeps it: that action executes at the `session_end` bar open when the ticker has
   one, otherwise it carries into the next session (a pending REDUCE/ADD therefore takes
   precedence over, and replaces, the forced-flat exit for that bar).
2. **Release modules** in the strategy's declared order (e.g. `R1` then `R3`): the first
   firing module emits its action (`EXIT`, or `REDUCE` for scale-out variants). One rule
   firing consumes the bar for that ticket (no double action from two release modules).
3. **Scale-in modules** (only if no release/reduce fired this bar): may emit `ADD`.
4. **State updates:** update running `peak` with this bar's high; update `mfe/mae`;
   a bar's own high can never trigger a peak-relative rule on the same bar.
5. Ambiguity inside a bar (both +H and −L touched) is resolved by the module's declared
   completed-bar definition (close/low/high fields); the engine never invents intra-bar
   ordering. Same-bar `min(open, level)` is the only adverse price convention.

Cross-ticket: all tickets evaluate the same bar independently; executions are applied in
order `(execution et, ticker asc, action priority ENTER < ADD < REDUCE < EXIT)`; cash
deductions happen in that order. Any ties in selection are already settled by the canonical
ranking (score desc, ticker asc).

## 8. Accounting & friction

- Primary convention: **one independent sleeve per basket-day**, capital `C0 = 1.0` unit.
  Slot budget = `C0 / N` (times reserve fraction on entry, see capital family). Returns are
  basket-day returns `r_day = P&L_day / C0`. Secondary (supplementary) convention:
  compounded equity across days (`E_{d+1} = E_d (1 + r_day)`), reported with max drawdown.
- Friction: configurable total round-trip bps, applied **per side** as
  `side = bps_total / 2 / 10000`. ENTER/ADD: `shares = budget / (px * (1 + side))`;
  EXIT/REDUCE proceeds: `shares * px * (1 - side)`. Minimum base = 100 bps (adversary
  150); gross (0) and the sensitivity ladder {0, 50, 100, 150, 200} are reportable runs.
  Blocked/unfilled/cash slots pay nothing.
- Realized vs marked always separated; released capital ≠ economic risk removed; report
  capital released by released peers and locked profits where the family uses them.

## 9. Standard metrics (every run emits all of these)

`days_n`, `filled_days`, `mean_basket_day`, `median_basket_day`, `std_basket_day`,
month-blocked **day-clustered bootstrap CI** (resample months with replacement, 10,000
draws, seed 20260922), `compounded_growth` + `compounded_max_dd` (secondary convention),
`worst_day`, `worst_week` (calendar-week sums), `worst_month`, `positive_day_share`,
daily percentiles `p1/p5/p10/p25/p50/p75/p90/p95/p99`, `avg_deployed_capital`
(time-weighted deployed notional / C0), `turnover_per_day` (executed notional / C0) and
`turnover_annualized` (×252), `n_entries/n_adds/n_reduces/n_exits/n_blocked_slots/
n_pending/n_carries`, `avg_failed_ticket_cost` (mean net return of tickets with net < 0),
`avg_survivor_contribution` (mean net return of tickets with net > 0 and their share of
positive P&L), `top1/top5/top10_day_share` of total net P&L (NA if total ≤ 0),
`path_contrib`: net P&L share of tickets whose raw post-fill MFE ≥ {50,100,200} (touch),
`false_release_rate[H]` for H ∈ {30, 50, 100}: among tickets with raw MFE ≥ H, the share
fully flat before the first H-touch bar; `half_release_rate[H]` (C1): among tickets with raw
MFE ≥ H, the share whose **cumulative REDUCE share quantity executed strictly before the
first H-touch bar is ≥ 50% of the pre-touch peak share count** (entry shares plus pre-touch
ADDs); tickets with no pre-touch REDUCE are not counted (a single token reduction does not
count). Chronology for both rates is the total order `(session_day, et)`; `tail_retained`:
mean/median of `captured_net / MFE_raw` among MFE ≥ 50 tickets
(`captured_net` = total net P&L / unit notional); per-year and per-quarter versions of the
headline block. All money numbers are unit-normalized (C0 = 1).

Gross and net@100/net@150 are mandatory columns; every family must also report the full
parameter surface it explored — never only the best cell.

## 10. Outputs (per run) & hygiene

`factory/artifacts/basket/phase2/<family_id>/<run_id>/`:
`config.json` (family, cell params, friction, days, seed, git HEAD, sim contract hash),
`daily.parquet` (date, r_day, deployed, actions, flags; `deployed_end` = **current open cost
basis** of open tickets, not lifetime `cash_in`, C1), `tickets.parquet` (ticker, sleeve_day,
entry, exits incl. `exit_day`, net, mfe_raw, flags), `metrics.json` (section 9),
`surface.json` (family-level grid summary), plus a worker-authored `README.md` (mechanism
read + stability). Incremental
month-keyed part writes; `--merge-only` to finalize; runs must be resumable and never leave
`.tmp` artifacts. Workers do not commit; the orchestrator commits.

## 11. Mandatory canaries (before any family result is trusted)

1. `fill.px == bars.open[fill.et]` for every filled snapshot name; count mismatches (must be 0).
2. Recomputed MFE/MAE from bars == stored `mfe/mae` (1e-9) for all names in a ≥30-day sample.
3. Recomputed first-touch et for the up/dn ladders == stored `ladders` for a ≥30-day sample.
4. Engine `R1(-10)` exit et/px reproduced by an independent scan on ≥20 hand-checked tickets,
   including at least one gap-through (`min(open, level)`) and one pending/halt case (C1: the
   pending case resolves from the declared carry substrate, so canaries require the overlay
   for the days/tickers they touch; the canary report records `contract_version`).
5. Forced-flat plumbing: engine all-hold exit price == `bars.open[session_end]` for every held
   ticket, and the engine's mean of `open[session_end]/fill - 1` over all filled `A_pm` names
   equals an independent direct scan of the same quantity from bars+anatomy (exact). The
   anatomy `eod_ret` (close-based) is reported beside it as a documented, different convention.
6. Determinism: two identical runs → identical `daily.parquet` content hashes.
7. Cash/no-leverage invariant assertions across a full 1,066-day baseline run.

## 12. Prohibited

Leverage; trading past `session_end`; recomputing fills from bars (except canary checks);
using sealed 2024/2025-01 or reserved 2026-06..08 in any path; modifying Phase-1 artifacts,
the canonical packet, or other lanes' files; live bot edits; commit by workers; strategy
runs before the PRE-REG-BASKET-02 freeze commit.

## 13. Correction C1 — 2026-09-24 (explicit and dated)

C1 corrects four coupled simulator defects found during the F1/Phase-2 restart audit. It is a
declared new degree of freedom, not a silent semantic change; `contract_version` becomes
`FROZEN-2026-09-22+SUBSTRATE-CORRECTION-2026-09-24` and prior carry-affected results are not
comparable. Normative details:

### 13.1 Sleeve-scoped ticket identity

An independent ticket is `(sleeve_day, ticker)` (`Strategy.open_tickets`). Entry suppression,
exit bookkeeping and carry bookkeeping use that key. Two basket-day sleeves may hold the same
ticker simultaneously with independent cash, cost basis and P&L; closing one ticket never
removes or mutates another. Within the entry session all execution stays on the canonical
candidate bars.

### 13.2 Declared full-market carry substrate

Cross-session carry resolution consults `carry_bars/` (under the canonical SIP artifact root;
`BASKET_CARRY_BARS_ROOT` overrides):

- `carry_bars/YYYY-MM-DD.parquet` — schema `(date, ticker, et, open, high, low, close,
  volume)`, regular session only, sorted by `(ticker, et)`. Only the tickers requested for
  that day are required.
- `carry_bars/manifest.json` — `{"contract": <contract_version>,
  "days": {day: {"session_end": int, "source": {"production": bool, "certification": str,
  "kind": "alpaca_sip_raw", "feed": "sip", "adjustment": "raw", "timeframe": "1Min",
  "window_et": [..], "overlay_sha256": str, "requested/returned/no_bars/unavailable": int},
  "tickers": {ticker: {"status": "bars"|"no_bars"|"unavailable", "certification",
  "rows", "first_et", "last_et"}}}}}`.

**Source validity (only a production source may certify a production run).** The overlay is
produced by `factory/scripts/basket_carry_bars.py` from **targeted Alpaca historical
StockBars with `DataFeed.SIP` and `Adjustment.RAW`** (`TimeFrame.Minute`, regular-session
window `09:25 ET .. session_end+5min`, provider `end` inclusive). Credentials come from an
explicit `--env-file` (`ALPACA_API_KEY`/`ALPACA_SECRET_KEY`) or the process environment;
nothing is auto-loaded from the CWD. Filtered, provider-substituted, or candidate-scoped
layers (e.g. `clean_ohlcv_*`, the `net/` candidate bars) are **not** valid carry sources: a
`no_bars` certification from such a layer would confuse "not a candidate" with "did not
trade". The producer retries transient failures with backoff, aborts on auth failures, and
re-requests every symbol omitted from a batch response individually so that
success-with-zero-rows (`no_bars`) is never confused with a failed request (`unavailable`).

`--fixture-root` reads a local parquet layer and is stamped `source.production=false` /
`certification=fixture_test_only`; the engine refuses it in production mode
(`CarrySubstrate(require_production=True)`, the default) and only test code may relax that
explicitly. Fixture coverage can never certify a production carry.

**Producer commands:** `--request DAY:TICKER`, `--requests FILE`, or `--scan` (candidate-tape
halt census -> next `--scan-window` dev days, stopping at dev-block boundaries). The producer
refuses sealed/reserved days and days absent from the committed calendar, and writes
atomically.

**Load-time validation (engine):** the manifest `contract` must equal the engine
`contract_version`; the recorded `session_end` must equal the run's session end; in
production mode `source.production` must be true and `source.overlay_sha256` must match the
day parquet bytes. Any mismatch is `MissingCarrySubstrate`, never a fallback.

### 13.3 Carry resolution rule

For an active ticket whose `sleeve_day != today`:

1. If the ticker is a candidate today, the canonical candidate bars are today's tape (entry
   convention unchanged); the substrate is not consulted.
2. Otherwise today's tape is the substrate's certified `bars` entry; the pending action
   executes at the **first bar of that tape** (same `min(open, level)` price convention), and
   the remaining position is managed through that session (release rules, forced flat, marks).
3. A certified `no_bars` entry means the ticker genuinely had no RTH trade that day: the
   carry continues, flagged `no_resumption`, marked at last known close.
4. Missing, uncertified or `unavailable` coverage raises `MissingCarrySubstrate` listing every
   missing `(day, ticker)` for the day. **Absence of candidate bars never means "no trade".**
5. Resolution walks the declared dev-day sequence only; it never inspects sealed
   2024 / 2025-01 or reserved 2026-06..08. A carry may not bridge a dev-block gap: tickets
   still open on the last dev day before a gap are terminally marked at their last close
   (§6, `BLOCK_BOUNDARY_MARK`) and retained in tickets/metrics. A carry that never resolves
   before the dev data ends is terminally marked at its last close in the final ticket table.

### 13.4 `deployed_end`

`daily.parquet.deployed_end` is the **current open cost basis** of tickets open at the session
end (`sum(cost_open)`), not lifetime `cash_in`; a REDUCE releases capital and lowers it.

### 13.5 `half_release_rate`

See §9. Cumulative pre-touch REDUCE shares must be ≥ 50% of the pre-touch peak share count
(entry shares plus pre-touch ADDs); chronology is `(session_day, et)`.

### 13.6 Execution chronology (total order)

Every action record carries `day` + `et`; `Ticket.exit_day`/`exit_et`,
`h_touch_day[H]`/`h_touch_et[H]` (the pair is the total order) and
`pending.decision_day` make cross-session chronology a total order.
`tickets.parquet` includes `exit_day`. A later session's 09:30 (`et=570`) is
never "before" an earlier session's 10:00 (`et=600`).

### 13.7 Resume/complete trust

`_progress.json` and `run_summary.json` carry the run `fingerprint` (contract version + hash
+ run identity + spec + friction + day span) and `contract_version`. Both the resume path and
the "already complete" path reject anything written under a different contract/fingerprint and
rebuild from scratch; parts/`_progress` from before C1 can therefore never silently return
pre-C1 semantics under the same run id.

### 13.8 Evidence and remaining prerequisite

- Negative evidence: F1 canary `pending_halt_case` resolved HSDT's 2022-03-23 halt pending on
  2022-04-26, the ticker's next *candidate* appearance, while the ticker actually traded on
  2022-03-24 (first bar `et=570`, open 3.32; last bar `et=940`).
- `carry_bars/2022-03-24.parquet` + manifest entry (HSDT, 25 rows, `first_et=570`,
  `last_et=940`, `production=true`, `certification=alpaca_sip_raw`, Alpaca SIP/RAW fetch
  window 09:25..16:00 ET, `overlay_sha256` recorded) is the production overlay shipped with
  C1. The non-SIP (Finnhub-derived `clean_ohlcv`) 16-row overlay produced earlier the same day
  is quarantined under `carry_bars/_quarantine/` with its provenance note; it must never be
  used as a carry source. The regression
  `tests/test_basket_sim.py::test_hsdt_carry_resolves_on_next_market_session_from_carry_substrate`
  encodes the corrected resolution against the production overlay.
- Remaining data prerequisite: a candidate-tape census finds **4,593 halt cases on 1,015 of
  the 1,066 dev days** (in-session tape ending before `session_end`, i.e. no bar for the
  forced-flat exit); `--scan --window 1` enumerates 4,583 adjacent ticker-day requests
  (13,324 at window 3). The overlay must be extended (`--scan --env-file <env>`) before any
  full-span run whose carries extend beyond already-certified ticker-days. Until then such
  runs hard-fail with the exact missing requests rather than silently mis-trading.

### 13.9 Implementation fixes inside C1 (2026-09-24, same correction; no new degree of freedom)

An independent hostile audit of the first C1 implementation pass found conformance defects,
fixed before any C1 result was produced. They change no declared semantics — they make the
engine do what §6/§7/§9/§10/§13 already specify:

- **Fail-closed carry integrity.** Production coverage is validated *before* the
  `bars`/`no_bars` branch: the manifest must carry `session_end`, `feed`, `adjustment`,
  `timeframe` and `overlay_sha256`, and the day parquet's hash must match, for **both**
  certified `bars` and certified `no_bars` entries. A missing or tampered day file can no
  longer be accepted as "genuine no trade".
- **Final-bar pending actions.** If a pending action executes on the forced-flat execution bar
  (`et = session_end`) and leaves shares open, a `FORCED_FLAT` exit is carried to the next
  session's first executable bar. An open ticket whose tape ended before the forced-flat
  decision bar is flagged `open_carry_no_pending` and counted in `n_carries`.
- **Same-et execution order.** Pending executions iterate in `(ticker asc)` order, matching the
  §7 cross-ticket rule (one pending per ticket, so ticker ordering is the complete tie-break).
- **Strategy-independent raw path tracking (§9).** `mfe`/`mae` and the first `(day, et)` touch
  of +30/+50/+100/+200 are updated from the ticket's whole session tape after the event loop,
  so bars printed *after* an exit still count; entry-day tracking starts at the entry bar.
  Without this, `false_release_rate`, `half_release_rate`, `path_contrib` and `tail_retained`
  were truncated at the exit bar.
- **Terminal marks are not trades (§13.3).** A data-boundary/data-end mark books a
  `terminal_value` (+ `terminal_kind`: `BLOCK_BOUNDARY_MARK`, `DATA_END_MARK`) instead of
  realized proceeds; it does not count in `n_exits` (new `n_terminal_marks`) and does not enter
  turnover. A run that spans the last canonical day terminally marks its unresolved carries
  with `DATA_END_MARK`.
- **Duration-weighted deployed capital (§9).** `deployed_avg` weights each deployed level by the
  minutes until the next observation point, including the tail to `session_end`.
- **Daily action records (§10).** `daily.parquet` gains an `actions` column (JSON list of
  executed actions with day/et/ticker/action/px/reason) beside `n_actions`; `tickets.parquet`
  gains `terminal_value`/`terminal_kind`.
