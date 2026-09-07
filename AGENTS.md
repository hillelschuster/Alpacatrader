# AGENTS.md — read this first (kept short on purpose)

You are in a money-making trading research project. Sole objective: find and exploit
a profitable top-gainer/momentum trading mechanism. No template worship; follow
evidence. (Full principle in the parent folder AGENTS.md.)

## Read order (small context, current truth first)
1. `researches/INTENT.md` — objective, stance, discipline (3 min).
2. `researches/STATE.md` — CURRENT truth snapshot: what's solid, what died, what's open.
3. `researches/HYPOTHESES.md` — living hypothesis ranking + falsifiers (top header is
   current; sections A-E below it are archived history).
4. `factory/STATE.md` — append-only operational chronicle (read the tail, not all).

## Everything else is history/evidence, clearly marked
- `researches/CANONICAL_STATE.md`, `researches/07-dominant-leader-program.md` — FROZEN
  historical snapshots. Do not cite as current.
- `factory/artifacts/*.json` — committed evidence for every number in STATE/HYPOTHESES.
- `factory/HYPOTHESES.jsonl` / `factory/EXPERIMENTS.jsonl` — experiment ledgers.

## Working rules (learned expensively)
- Measurement contract: ET clocks (et_minute in factory/scripts/replay_watchlist.py),
  causal-only, 1-bar lag, next-bar-open fills, >=100bps friction, pre-registered kills.
- Power doctrine (post-2026-09-08): any selection/timing test needs >=6 months pooled
  dev + >=2 pre-registered unseen collision months. Small-n dev positives are 0-for-4.
- Never resurrect a collision-failed formulation. New formulation + more power only.
- Forward observer (factory/scripts/forward_observe.py) = primary live evidence engine.
- Long compute: run detached (nohup) with incremental per-day writes + resume; never
  write only at the end; verify artifacts exist before reasoning from them.
- Research memory updates: append a few lines to factory/STATE.md after meaningful
  work; keep researches/HYPOTHESES.md pruned and current; both before finishing.
- Producer scripts for all artifacts must be committed under factory/scripts/.
- Two workstreams: research (factory/, researches/) and the paper bot (src/, tests/).
  Keep their states separate; the bot implements only validated research.

## Repo state notes (2026-09-08)
- Main branch is AHEAD of origin (local-only commits; push is clean fast-forward —
  ask user before pushing). data/ is gitignored by policy (no remote backup of
  market data — handle with care).
- Uncommitted src/ workstream exists (paper-bot Phase A/B/C); see git status and
  SPEC.md §11.19 before touching src/.
