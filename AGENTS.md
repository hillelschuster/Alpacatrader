# AGENTS.md — read this first (kept short on purpose)

You are in a money-making trading research project. Sole objective: find and exploit
a profitable top-gainer/momentum trading mechanism. No template worship; follow
evidence. (Full principle in the parent folder AGENTS.md.)

## Read order (small context, current truth first)

1. `researches/INTENT.md` — objective, stance, discipline (3 min).
2. `researches/STATE.md` — CURRENT truth snapshot: what's solid, what died, what's open.
3. `researches/HYPOTHESES.md` — living hypothesis ranking + falsifiers (top header is
   current; older sections are archived history).
4. `factory/STATE.md` — append-only operational chronicle (read the tail, not all).
5. `HANDOFF.md` — full cold-start handoff; **§16 = latest state / entry point**.

## Operating rules

* **Profitability is the priority.** Research, code, tooling and process exist only to help find, validate or execute an edge.
* **The user is the decision maker.** Follow explicit instructions strictly.
* Deviate only when there is a clearly better or more practical route toward profitability; state why briefly.
* Work directly. **Zero overengineering. Zero unnecessary frameworks, architecture, abstractions or process.**
* Use the smallest sufficient script, experiment, test or tool.
* Tasks requiring deep reasoning: **think deeply.**
* Tasks requiring hypothesis generation: **generate and investigate hypotheses.**
* Tasks requiring concise execution: **be concise and execute.**
* Use Context7 when current library/API/documentation context is needed.
* Do not inherit old assumptions, targets, models or experimental structures merely because code already exists.
* Discovery can be exploratory. Once something looks monetizable, validate it hard enough to distinguish edge from luck.
* Negative evidence applies to what was actually tested. Do not overgeneralize it or endlessly protect dead ideas.
* Prefer raw evidence and simple experiments over elaborate methodology.
* Before adding complexity, ask: **does this materially help profitability, evidence quality, execution correctness or capital protection?** If not, skip it.

## Working rules (learned expensively)

- Measurement contract: ET clocks, causal-only, 1-bar lag, next-bar-open fills,
  >=100bps friction, pre-registered kills.
- Power doctrine (post-2026-09-08): any selection/timing test needs >=6 months pooled
  dev + >=2 pre-registered unseen collision months. Small-n dev positives are 0-for-4.
- Never resurrect a closed/failed formulation. New formulation + more power only.
- Live path: `factory/scripts/flush_bot.py` (paper trader; `data/forward/bot/LIVE`
  arms it, `data/KILL` stops it, a bot edit needs the KILL-toggle restart —
  supervisor restarts only on exit). `factory/scripts/forward_observe.py` = evidence
  observer. Judge live fills against the **A3b baseline** (`HANDOFF.md` §14), never
  the frozen backtest; `flush_bot_ledger.py` partitions fills by `pf_est` / `n_strict_est`.
- Long compute: incremental per-day writes + resume; never write only at the end;
  verify artifacts exist before reasoning from them.
- Subagent runs have stalled in this setup; prefer direct execution for research
  scripts (user directive 2026-09-12).
- Research memory: append a few lines to `factory/STATE.md` after meaningful work;
  keep `researches/HYPOTHESES.md` and the ledgers current; commit producer scripts
  under `factory/scripts/`.
- Two workstreams: research (`factory/`, `researches/`) and the paper bot (`src/`,
  `tests/`). Keep their states separate; the bot implements only validated research.

## Everything else is history/evidence, clearly marked

- `researches/CANONICAL_STATE.md`, `researches/07-dominant-leader-program.md` — FROZEN
  historical snapshots. Do not cite as current.
- `researches/history/` — June bot-era audits (00-06) + archived research docs. Zero
  constraint on active research.
- `factory/artifacts/*.json` — committed evidence for every number in STATE/HYPOTHESES.
- `factory/HYPOTHESES.jsonl` / `factory/EXPERIMENTS.jsonl` — experiment ledgers.

## Repo state notes (2026-09-13)

- `origin/main` is current; pushes are expected after each work cycle. `data/` is
  gitignored by policy (no remote backup of market data — handle with care).
- Live paper bot: armed (`LIVE`), account flat, 0 fills; next session Mon 2026-09-14.
  Real-time SIP not purchased (not needed for the frozen strategy).
- Uncommitted working-tree dirt from other workstreams (ml/* logs, certification
  artifacts, `PRE-REG-H12R.md`, CRLF churn) is NOT research state — do not commit it
  blindly.
