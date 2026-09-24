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
6. `contract_version` is unchanged; the run fingerprint (`_run_fingerprint`) does not
   include the policy, so a run id can never silently change meaning: family scripts bind
   the policy into their own `run_config.json`/cell identity (see `basket_f6.py`).

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
