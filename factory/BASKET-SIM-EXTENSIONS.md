# BASKET-01 Phase-2 — declared engine extensions

**Status: DECLARED 2026-09-24.** This file records additive, opt-in engine extensions to
`factory/scripts/basket_sim.py`. It does **not** modify `factory/BASKET-SIM-CONTRACT.md`
(frozen) and does **not** change `contract_version`
(`FROZEN-2026-09-22+SUBSTRATE-CORRECTION-2026-09-24`), which stays owned by the contract
and by correction C1.

Rule for this file: an extension may only *add* a call site, a data field, or a policy
object. It may not reorder, weaken, or re-define any existing engine rule (§5–§8 of the
contract). Every extension below states exactly what it adds and how it is inert when it
is not requested.

---

## EXT-1 — `BATCH-CAPITAL-CHECKPOINT-2026-09-24`

### Why

`reserve_frac` (§8 capital family) only shrinks the entry budget: it parks
`(1 - reserve_frac) * C0` as sleeve cash and never deploys it. The per-ticket
`Strategy.on_checkpoint(et, ticket, bar)` callback cannot see the sleeve's survivor set and
therefore cannot size one sleeve reserve across several recipients. Families F6/F7 need a
*whole-sleeve* decision at a completed golden-window bar, with normal next-bar execution.

### What is added

1. `BatchAllocationPolicy` — a policy object with `checkpoint_ets` and
   `plan(ctx) -> list[BatchAllocationIntent]`.
2. `BatchCheckpointContext` — the read-only per-bar context handed to `plan`.
3. `BatchAllocationIntent` — one requested `ADD` for one ticket.
4. `StrategySpec.batch_policy: BatchAllocationPolicy | None = None` — declarative opt-in.
5. `Ticket.entry_rank: int = -1` — the canonical snapshot rank of the ticket's entry
   (`nm["rank"]`, falling back to the snapshot enumeration index). Purely descriptive;
   no engine rule reads it.
6. One engine call site in `simulate_day`, plus its scheduling helper.

`plan` is invoked **once per completed checkpoint bar**, after every ticket decision for
that bar and before the §7 step-4 state updates of that bar. The policy returns per-ticket
`ADD` intents; the engine schedules each one with `_schedule(..., decision_et=et, ...)`,
so it executes at that ticket's **first eligible later bar open** through the ordinary
§7 step-0 pending path. Nothing else in the engine changes.

### API

```python
@dataclass
class BatchAllocationIntent:
    sleeve_day: str        # sleeve whose cash funds the ADD
    ticker: str            # ticket identity is (sleeve_day, ticker)
    frac: float            # ADD notional / that ticket's unit_notional
    reason: str            # recorded on the pending action and on the executed action
    alloc_id: str = ""     # policy-side identity for evidence tables


@dataclass
class BatchCheckpointContext:
    day: str                       # session date of the completed bar
    et: int                        # the completed checkpoint bar (decision minute)
    session_end: int               # session end for `day` (calendar)
    strategy: "Strategy"           # the live sleeve owner (read-only inspection)
    spec: "StrategySpec"
    rec: dict                      # anatomy record for `day` (canonical ranking source)
    bars: "Bars"                   # candidate tape for `day`
    side: float                    # per-side friction fraction (bps/2/10000)
    eligible: list["Ticket"]       # ordered open survivors, see below
    excluded: list[tuple["Ticket", str]]   # (ticket, reason), see below


class BatchAllocationPolicy:
    name = "batch_policy"
    checkpoint_ets: tuple[int, ...] = ()

    def plan(self, ctx: BatchCheckpointContext) -> list[BatchAllocationIntent]:
        raise NotImplementedError
```

* `checkpoint_ets` must be a non-empty subset of the contract's golden-window points
  `sim.CHECKPOINTS = (580, 585, 590, 600, 615)`; `Strategy.__init__` raises `ValueError`
  otherwise. `plan` is called only for `t in CHECKPOINTS` and `t in checkpoint_ets`.
* `eligible` is ordered by `(ticket.entry_rank, ticket.ticker)` — canonical rank ascending,
  ticker ascending as the deterministic tie-break. A policy may re-sort, but the engine's
  order is the declared one.
* `eligible` membership: the ticket is open, `ticket.sleeve_day == ctx.day`, it has **no
  pending action**, and it has a completed bar at `ctx.et`. "No pending action" is
  re-checked *after* that ticket's own §7 decisions for the checkpoint bar, so a
  release/reduce/scale-in scheduled **on** the checkpoint bar also excludes the ticket —
  that bar is already occupied and a pending action is never overwritten. Reserve
  decisions are sleeve-local, so tickets carried in from an earlier sleeve are never in
  `eligible` (their cash is another sleeve's cash).
* `excluded` membership: open tickets of `ctx.day` that failed those tests, with reason
  `"pending_action"` (a §7 step-0/1/2/3 action is pending on the checkpoint bar, whether
  it arrived earlier or was scheduled on that bar) or `"halted_at_checkpoint"` (the ticker
  has no completed bar at `ctx.et`). Decisions are made from completed bars only (§3), so
  a halted-at-checkpoint ticket is not a decision recipient.
* Both lists are plain lists of the live `Ticket` objects. The engine does not retain them.

### Scheduling semantics (what the engine does with the intents)

For each returned intent, in returned order:

1. resolve the ticket by `(intent.sleeve_day, intent.ticker)` in
   `strategy.open_tickets`; skip the intent if it is absent, closed, or already has a
   pending action (a pending action is never overwritten, §7 step 0);
2. skip intents with `frac <= 0`;
3. otherwise `_schedule(strategy, ticket, "ADD", intent.reason, ctx.et, level=None,
   frac=intent.frac, decision_day=ctx.day)`.

Consequences, all of which are ordinary engine behaviour and not new rules:

* execution is at the ticket's first bar with `et > ctx.et` — **never the checkpoint bar
  itself** (§3, §7 step 0); `min(open, level)` degenerates to the bar open because
  `level is None`;
* the ADD is funded from `strategy.sleeve_cash[intent.sleeve_day]` and must satisfy the
  existing cash/no-leverage invariants and the existing ADD cap
  (`total ADD notional per ticket <= unit_notional`). The engine **skips** an ADD that
  exceeds the cap (`add_cap_exceeded`) or cannot be funded (`add_unfunded`), flags it, and
  never partially fills it. The extension adds no bypass and no truncation: sizing,
  truncation and retention decisions belong to the policy;
* a scheduled ADD that cannot execute in-session becomes an involuntary carry exactly like
  any other pending (§6/§13.3) and resolves from the declared carry substrate;
* `_execute` records the action with `reason = intent.reason`, so a policy can reconcile
  intent against execution without touching engine internals.

### Inertness guarantees

A strategy that declares no policy is **bit-identical** to the pre-extension engine:

1. `StrategySpec.batch_policy` defaults to `None`.
2. The checkpoint call site is guarded by `policy is not None` and is the only added
   statement inside `simulate_day`'s bar loop.
3. The decision loop of §7 was split into a decisions pass and a state-update pass so the
   batch call can sit *between* them. The split is behaviour-preserving because every
   per-ticket rule (`ReleaseRule.evaluate`, `ScaleInRule.evaluate`,
   `Strategy.on_checkpoint`) reads only the state of the ticket it is given; no rule reads
   another ticket's `peak`/`mfe`/`mae`/`last_close`/`h_touch_*`. The state-update pass
   writes exactly the same fields, in the same per-ticket order, and the deployed
   time-weight sample is still taken once per completed bar.
4. The checkpoint et is **not** forced into the bar set: the engine still iterates the
   union of the active tickets' completed bars. A policy therefore observes exactly the
   checkpoint bars the tape produced, and a sleeve whose own tickets have no bar at the
   checkpoint cannot be handed a synthetic one.
5. `Ticket.entry_rank` is written at entry creation and read by nobody in the engine.
6. `contract_version` is unchanged. **Superseded by EXT-2 (2026-09-25):** the run
   fingerprint (`_run_fingerprint`) now *does* bind the policy — `batch_policy`,
   `entry_veto`, `mid_entry`, `reentry` and `fingerprint_extra` are all part of the
   payload, and a policy with behaviour-carrying parameters is expected to expose them
   through `signature()`. Until then a run id could silently change meaning when only the
   policy changed; family scripts also bind the policy into their own
   `run_config.json`/cell identity (e.g. `basket_f6.py`).

### Not changed

* §7 order for existing strategies: pending execution → forced flat → release → scale-in →
  per-ticket checkpoint → state update.
* §5 cap and cash rules, §6 carries/forced flat, §8 accounting and friction, §9 metrics,
  §10 outputs, §11 canaries, `CHECKPOINTS`, `C0`, `CONTRACT_VERSION`.
* `ReleaseRule`/`ScaleInRule`/`Strategy.on_checkpoint` signatures.

### Consumers

* `factory/scripts/basket_f6.py` — staged reserve capital (one checkpoint per sleeve,
  `equal` deployment across eligible open originals, or `cash` = no deployment).
* F7 (released-capital redeployment) may reuse the same hook; it is not implemented here.

### Verification hooks

`tests/test_basket_f6.py` encodes: policy-free inertness, exact per-survivor amounts,
blocked-slot cash never used as reserve, `B585` deploying no earlier than ET586 and `B600`
no earlier than ET601, `B600`-at-585 inapplicability, pending-action exclusion, cap
truncation leaving cash, `p=1` identity with the primitive baseline, two sleeves holding
the same ticker remaining independent, and fail-closed carry behaviour.

---

## EXT-2 — `BUY-SIDE-ENTRY-HOOKS-2026-09-25`

### Why

The engine's buy side was one-shot (verified 2026-09-24): entries happen once at
`entry_T`, the EXT-1 batch hook emits `ADD` intents only, and there is no per-name entry
veto, no mid-session entry and no re-entry after an exit. Any handling policy that wants
to *reinforce* a name after it proves itself mid-session, or to *skip* a name at the entry
moment and buy it later only if it holds up, is structurally untestable. ATLAS stage 5
needs exactly those three actions.

### What is added

1. `EntryCausal` — the entry-moment **causal view** the engine builds itself and hands to a
   veto rule (the raw anatomy name record is never handed over; see §"Causality").
2. `EntryVetoRule` (base), `V0` (never veto), `VGate(field, op, value, on_missing)`.
3. `EntryTrigger` (base), `TAlways`, `TCross(ref, pct, side)`.
4. `BuySidePolicy` (base), `MidEntryPolicy`, `ReentryPolicy`.
5. `StrategySpec.entry_veto: list[EntryVetoRule]` (default `[]`),
   `StrategySpec.mid_entry: MidEntryPolicy | None` (default `None`),
   `StrategySpec.reentry: ReentryPolicy | None` (default `None`),
   `StrategySpec.fingerprint_extra: dict` (default `{}`).
6. `Strategy.n_vetoed_slots`, `.n_entry_unfunded`, `.n_entry_unfilled`,
   `.n_entry_capped`, `.buy_side_day_flags` (diagnostics; no §7 rule reads them).
7. `Ticket` flags `mid_entry` / `reentry` (no schema change: they ride the existing
   `flags` column), and the per-day flags `veto_skip`, `entry_unfunded`, `entry_capped`,
   `entry_unfilled_<why>` in the existing `daily.parquet` `flags` column.
8. Metrics key `buy_side` in `metrics.json`.
9. Three engine call sites in `simulate_day` (the batch veto; a pending-entry execution
   pass in step A; a decision pass after the EXT-1 checkpoint hook) plus the fingerprint
   binding.

### Causality (the entry moment)

A bar with `et=t` is complete at `t+1`; a decision from completed bars executes at the
next bar's open (§3). Every hook obeys it:

* **Veto** — evaluated in the entry batch, before any ticket exists, on an `EntryCausal`
  built with `cutoff_et = fill.et - 1`. For `A_pm`/`A_pm31` (fill at ET 570) that is
  premarket state only: `open0930`, `bar_*` and `n_bars` are `None`/`0`. For `A_open`
  (fill 571) the completed 09:30 bar is visible; for the `B` families the completed bars
  up to `T-1`. The rule can read `gap_pct`, `pre_run_pct`, `sel`, `decision_px`,
  `prev_close`, `pre_high`, `open0930`, `bar_close`, `rank` — and nothing else: `close`,
  `eod_ret`, `mfe`, `mae`, `day_high`, `ladders`, `gap_post` never reach it. A field the
  engine cannot fill causally is `None` (never fabricated), and `VGate.on_missing`
  decides what that means (`"veto"`, the default = fail closed, or `"pass"`).
  *Consequence to know before you configure one*: `VGate("open0930", "above", x)` is a
  veto-*everything* rule for `A_pm`/`A_pm31`; that is deliberate and loud.
* **Mid-session entry** — the trigger is evaluated on the candidate's **own completed
  bar** inside the declared window; the entry executes at the open of that ticker's first
  bar strictly after the decision minute.
* **Re-entry** — same, with the reference price resolved from the closed ticket
  (`exit_px`, `entry_px`) or from the name's premarket state. A ticket must already be
  closed to be a candidate, so the exit is known before the decision bar.

### Veto

* Evaluated once per candidate in the entry batch, in canonical snapshot rank order, for
  names in `snap["names"][:top_n]` that have a non-blocked anatomy fill. Blocked names keep
  their existing treatment (`n_blocked_slots`, no veto evaluation).
* `veto(ctx) -> True` skips the slot: no ticket, no substitute, no backfill — exactly like
  a blocked fill. Counted in `n_vetoed_slots` and the day flag `veto_skip`.
* `StrategySpec.entry_veto` is a **list**: several rules are OR-ed (any veto skips).
* A rule whose behaviour depends on parameters MUST implement `signature()`; the base
  class raises `NotImplementedError`, so an unbound custom rule fails at run start rather
  than silently changing what a stored run id means.

### Mid-session entry (`MidEntryPolicy`)

* Candidates: snapshot names in `snap["names"][:top_n]`, canonical rank order, that have
  **no ticket today** and no pending entry — i.e. a slot that stayed in cash (blocked
  fill, vetoed name, or no fill at all). `include_blocked_fills=False` excludes the
  blocked-fill case.
* `window=[lo, hi]` (ET minutes, either end `null`) is intersected with the engine's hard
  limit `[FIRST_ET, session_end - 2]`: a new entry is never *scheduled* on the last two
  completed session bars, so an execution bar can never be the forced-flat bar.
* `max_per_day` / `max_per_bar` cap the number of new entries (default 1 / 1).
* Size: the ordinary slot budget `C0 * reserve_frac / n_slots`. Funding is the unchanged
  cash rule: the sleeve must be able to pay the **whole** budget from cash (net of entries
  already scheduled) and must stay inside its deployed limit — `C0 * reserve_frac` by
  default, or `C0` with `allow_reserve=True`. An unfunded entry is skipped, never
  partially filled, never levered, and counted once per day (`n_entry_unfunded`, day flag
  `entry_unfunded`).
* Execution is an ordinary `ENTER`: `shares = budget / (px * (1 + side))` with
  `side = bps_total/2/10000`, recorded in `tk.actions` and in the day's `actions` JSON.

### Re-entry (`ReentryPolicy`)

* Candidates: tickets of the **same sleeve day** that already exited, ordered by
  `(exit_et, ticker)`, that are not currently open and have no pending entry.
* Declared budget, not implicit: `max_per_ticker` (per `(day, ticker)`), `max_per_day`,
  `max_notional_per_day_frac` of `C0` as the ceiling on notional committed by re-entries
  that day, `max_per_bar`, and `cooldown_bars` (completed bars since that ticket's exit).
* A fired trigger that a **budget** refuses is counted once per day
  (`n_entry_capped`, day flag `entry_capped`), so a policy read can never confuse "the cap
  said no" with "the rule never fired".
* Same funding, size, friction and no-leverage rules as mid-entry. A name that stopped out
  cannot refill a full slot from a shrunken sleeve: the entry is skipped
  (`entry_unfunded`). Declaring `allow_reserve=True` is the only way to reach the parked
  reserve, and `C0` is still the hard ceiling.
* The re-entered ticket is a **new ticket** for the same `(sleeve_day, ticker)` — the same
  key the closed one used, because the old one is closed. One position per name per sleeve
  still holds; the day's ticket count grows.

### Deterministic order and what the engine does with a scheduled entry

A scheduled entry carries no order-book state; it is one record
`{ticker, after_et, rank, budget, flag, reason}` in the day's bookkeeping.

* **Placement in the bar**: step A executes the bar's ordinary pendings, then the EXT-2
  entries whose execution bar has arrived (so capital released on this bar can fund them);
  the bar's decisions include the newly opened ticket, exactly as for a batch entry.
  The decision pass runs after the per-ticket decisions and the EXT-1 checkpoint hook.
* **Execution price/et**: the open of the ticker's first bar strictly after the decision
  minute, recorded at that minute. When a hook is declared, the day's candidate bars join
  the event grid, so that minute is itself a scheduled minute and the fill resolves
  exactly; a `>=` test remains as a robustness net for callers that pass a custom bar set.
* **Order among same-sleeve executions on one bar** is the engine's existing one
  (ticker ascending) and is not changed by this extension.
* **Nothing carries**: a scheduled entry that cannot execute in the session (the ticker's
  next bar is the session's last bar or later, or there is no later bar) is cancelled —
  no ticket, no cash committed, counted (`n_entry_unfilled`) and flagged
  (`entry_unfilled_no_later_bar` / `entry_unfilled_too_late` / `entry_unfilled_session_end`).
  This is a deliberate, declared difference from §6, which is about *exits*: an entry
  intent belongs to the day's selection and would not be a decision if it executed in a
  later session.

### Fingerprint (no silent rule parameters)

`_run_fingerprint` now binds `entry_veto[*].signature()`, `mid_entry`, `reentry`,
`fingerprint_extra`, and (closing the EXT-1 gap identified in the audit) `batch_policy`.
A rule whose behaviour depends on constructor parameters MUST expose them through
`signature()`; the EXT-2 base classes raise instead of returning a constant, and
`fingerprint_extra` carries identity that is not expressible as a declared field (it must
be deterministic — no ids, no addresses, no timestamps). Two specs that differ only in a
hook parameter get different fingerprints **even when the tape never exercises the
difference**; an unchanged spec keeps its fingerprint.

### Inertness guarantees

A strategy that declares no hook is bit-identical to the pre-EXT-2 engine:

1. The three defaults (`entry_veto=[]`, `mid_entry=None`, `reentry=None`) are the
   pre-extension behaviour; every new call site is guarded (`if veto_rules`,
   `if do_entries and bs.pending`, `_buy_side_decide` returns immediately).
2. The veto block sits inside the entry batch and is skipped entirely when the list is
   empty, so the batch's iteration, counting and ticket construction are unchanged.
3. `_BuySideDay` is empty (and its pending list always empty) without a hook; the day-end
   sweep and the day-flag union are no-ops producing the same `flags` string.
4. The event grid is extended only when a hook is declared.
5. No `Ticket`, `daily.parquet` or `tickets.parquet` schema changes.

Evidence (`factory/artifacts/basket/phase2/ATLAS/engine_buyside_canary.json`): identical
`daily.parquet` / `tickets.parquet` sha256 for a 20-day `A_pm N=2 R0 100bps` run on the
pre-change engine (`git show HEAD:factory/scripts/basket_sim.py`) and on this engine, plus
two further 20-day windows (one containing the 2021-11-26 early close, one from block 2),
plus a byte-diff of the exercising configuration showing that only the intended tickets
move.

### Limits and invariants that had to be stated (not weakened)

* **No engine invariant was relaxed.** `cash >= 0`, `deployed <= C0`, the per-side
  friction convention, the ADD cap, the forced flat, the carry rules for exits, and the
  §7 order for existing strategies are all untouched.
* `deployed_avg` for an *extension* run is sampled on the extended event grid (candidate
  bars are decision minutes). It is not directly comparable with a non-extension run of
  the same positions; no existing run's metric moves.
* The unfunded/capped counters count *(day, hook, kind)* once, not once per scanned bar;
  they are "days on which the policy wanted in and was refused".
* New entries are always full slot budgets or nothing. A policy that wants graded sizing
  must declare a smaller `reserve_frac`/more slots, or use EXT-1 ADDs on a real ticket.
* Mid-entry candidates come from the entry snapshot only (`entry_pop`/`entry_T`); the
  extension never re-ranks and never looks outside `top_n`.
* Re-entry is same-session only (no cross-session re-entry), and the mid/re-entry ticket
  inherits the same sleeve as the batch entries, so a new entry can never be funded by
  another day's cash.
* Resume validation had to be widened: the parts' ticket-uniqueness key is now
  `(sleeve_day, ticker, entry_et)` instead of `(sleeve_day, ticker)`, because a sleeve day
  may legitimately hold two tickets for one name (entry + re-entry). Without this, every
  resumed run of a re-entry cell would silently rebuild from scratch.

### Not changed

* §7 order for existing strategies (pending → forced flat → release → scale-in →
  per-ticket checkpoint → EXT-1 batch → EXT-2 buy side → state updates), the ADD/EXIT
  vocabulary, §5 caps, §6 carries for exits, §8 accounting and friction, §9 metric
  definitions, §10 output schemas, §11 canaries, `CHECKPOINTS`, `C0`, `CONTRACT_VERSION`.
* `ReleaseRule` / `ScaleInRule` / `Strategy.on_checkpoint` / `BatchAllocationPolicy`
  signatures and the `daily.parquet` / `tickets.parquet` columns.

### Declarative JSON (what a policy can now express)

```json
{
  "name": "cell", "entry_pop": "A_pm", "entry_T": 570, "top_n": 3, "n_slots": 3,
  "release": [{"rule": "R3", "g": 15}],
  "entry_veto": [
    {"rule": "VGate", "field": "gap_pct", "op": "above", "value": 40.0, "on_missing": "veto"},
    {"rule": "VGate", "field": "rank", "op": "above", "value": 2}
  ],
  "mid_entry": {
    "label": "MID_ENTRY", "window": [600, 660], "max_per_day": 1, "max_per_bar": 1,
    "include_blocked_fills": true, "allow_reserve": false,
    "trigger": {"rule": "TCross", "ref": "px_decision", "pct": -2.0, "side": "above"}
  },
  "reentry": {
    "label": "REENTRY", "window": null, "max_per_ticker": 1, "max_per_day": 1,
    "max_per_bar": 1, "max_notional_per_day_frac": 1.0, "cooldown_bars": 5,
    "allow_reserve": false,
    "trigger": {"rule": "TCross", "ref": "exit_px", "pct": 0.0, "side": "above"}
  },
  "fingerprint_extra": {"predicate": "atlas-window-v1"}
}
```

Semantics in words: *veto a name at the entry moment on causal entry-moment state; enter a
slot that stayed in cash mid-session once that name's own completed bar crosses a declared
reference price; re-enter a name that exited today once it reclaims a declared level,
under a declared per-name/per-day budget, with friction and full-slot funding on the
ordinary next-bar-open convention.*

### Verification hooks

`factory/scripts/basket_sim.py --self-test` (EXT-2 block): inertness without hooks;
veto counting/day flag and the untouched survivor; causal view for `A_pm` vs `A_open`;
fail-closed `on_missing`; mid entry firing on the candidate's own bar and filling the next
open with the slot budget; unfunded skip (no leverage); window clamping; cancellation of
an entry that cannot execute before the flat bar; re-entry after a stop-out with cooldown
and per-ticker cap; a can't-refill-after-a-loss case; cap refusals counted and flagged;
and fingerprint stability/binding including the `NotImplementedError` for an unbound rule.
`factory/artifacts/basket/phase2/ATLAS/engine_buyside_canary.json`: the byte-identity
canary and the exercising-run evidence.
