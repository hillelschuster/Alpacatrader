# BASKET-01 — Runbook (operations only; the thesis lives in THESIS-BASKET-01.md)

Ground rules learned expensively on 2026-09-17. Follow them literally.

## 1. Liveness check (all three signals, one command)

```bash
pgrep -af "[b]asket"                                  # supervisor + watchdog + current month's python
stat -c 'log mtime: %y' factory/artifacts/basket/run_gaps.log
ls factory/artifacts/basket/anatomy | wc -l           # must be rising mid-run
```

- A bare `echo $!` or an empty output from an unverified command is NOT evidence.
- **Never use `rtk ps`** — it returns empty output silently. Use plain `pgrep`/`ps` with the `[b]` bracket so the check does not match itself.
- During a month transition there is a ~1–2s window with no python child; that is normal. Judge by log mtime (< 60s) + file count, not by a single process snapshot.

## 2. Memory (this box: 11GB, shared with opencode)

- The extractor uses 2-day chunk collects (~1.0–1.5GB RSS). Check after launch:
  `ps -eo pid,rss,args | grep "[b]asket_anatomy"` → if RSS > 2.5GB, reduce chunk size further.
- OOM kills are only visible in `dmesg | grep -iE "killed process|out of memory"`. After any surprise death, check dmesg before theorizing.
- A killed python mid-month does NOT stop the supervisor loop — it prints `month M FAILED` and moves on. Always grep the log for `FAILED` after any run, and run the QA gate.

## 3. Launch pattern

```bash
echo "=== RESUME<candidate> $(date '+%F %T') ===" >> factory/artifacts/basket/run_gaps.log
setsid nohup bash /tmp/opencode/basket_run_resumeN.sh >> factory/artifacts/basket/run_gaps.log 2>&1 < /dev/null &
setsid nohup bash /tmp/opencode/basket_watchdog.sh >> /dev/null 2>&1 < /dev/null &
```

Watchdog: 120s checks; relights the loop if no python process exists and the log has not said ALL_DONE; logs to `run_watchdog.log`. It does NOT detect per-month python failures (loop is still alive) — that is what the log grep + QA are for.

## 4. QA gate (mandatory before reading anything)

```bash
.venv/bin/python factory/scripts/basket_qa.py     # must print QA: PASS
```

Checks: JSON parse of every day file; snapshot count 13/14; per-filled-member fill/ladders/mfe/mae/states; bars column set + presence; zero `.tmp` leftovers; calendar completeness vs leaderboard dates with declared known-skips only. The single declared skip (first 2025-02 session) is now conditional: it is declared only on trees that lack the 2025-01-31 prev-close seed (the legacy root); SIP trees carry the seed and 2025-02-03, so no skip is declared there.

## 5. Failure protocol (after any `month M FAILED`)

1. Read the traceback in `run_gaps.log` — fix the ROOT CAUSE in code, never skip the month.
2. Prove output-equivalence on a day that already exists:
   `cp` the day's JSONL + bars to /tmp → `--months M --max-days 1 --force` → `cmp` JSONL (must be byte-identical) + polars frame-equal bars.
3. Rebuild the pending-months list (QA logic) → `/tmp/opencode/basket_months_resumeN.txt`.
4. Relaunch (section 3) with a new resume script; keep old logs.

## 6. Commits of record (basket lane)

- `5170f70` split-flag causal fix · `5fbfd8f` timing surface · `525f7f5` multi-survivor lock · `9ebd95a` aggregation · `ff2d78c` posture · `3fc02c4` bounded memory · `1022a91` T5/T7/T9b passes · `8be27db` OOM-safe extraction (2-day chunks, atomic writes) · `ae9e2ae` QA gate · `<pending>` A_pm no-RTH-bars fix + this runbook.

## 7. Current state (update after each cycle)

- 2026-09-17: resume2 DONE; QA PASS (1065/1066, 1 declared skip). All passes + aggregates complete; first descriptive read done; evidence committed. Next: PRE-REG-BASKET-02 (release rule + survivor rule) from the anatomy, frozen before any Phase-2 P&L. Reserved months 2026-06..08 untouched.
- 2026-09-21 (advisor-directed final measurement pass): B(T) prev-close admission gate removed (584 audit
  cases); EOD leader objects + separate containment counters added; A_pm31 population added; T5 rebuilt
  trade-by-trade from raw prints (no minute ordering assumed anywhere; `peak_recon_vs_stored_mfe` in the
  artifact shows exact agreement with the bar-based MFE); T7 emits true monthly rollups (T8 pays_net shares
  corrected); full anatomy regeneration (1,066 days, 15 snapshots/day); provider fetch now retries symbols
  the bulk request silently dropped — netbars re-run recovered 7 of the 9 audited symbol-days
  (2 remain unresolved: PMN 2023-02-13 and MGLD 2023-09-19 are genuinely provider-empty); CSLR 2023-11-13
  B/575 rank 3 is now selected with its own fill (the one true slot-substitution case is resolved).
  No parameter selected; PRE-REG-BASKET-02 remains unfrozen.
- 2026-09-21 (continuation): market base rates re-run with canonical eligibility (the $1
  floor on open tradeability for both anchors; prev-close anchor additionally needs
  prev_close > 0; the old prev-basis floor is kept as `prev_close_prevfloor`); capture
  funnel re-staged (A_pm31 was missing from the rank table -> mis-bucketed as absent; fixed)
  and cross-checked against the base-rate artifact — all six (anchor, ruler) day counts are
  identical. `basket_capture_funnel.stage_day` now DELETES a stale staged file when a
  re-staged day yields no rows (without this, a pre-floor staging survived re-runs and the
  merge kept reading it — this caused a 3-day mismatch: 2021-09-14, 2022-01-13, 2022-02-01).
  `race_by_view` added (dn-before-up shares per view/cell). T8 quarter rows carry
  `pays_days`. T7 emits true monthly rollups (key `monthly`) + day rows (`daily`).
- 2026-09-21 (audit pass — measurement layer close-out): five scoped audits + two verifiers. Ranking
  tie-break made explicit everywhere (`score desc, ticker asc`; polars sort is unstable) → selection
  audit 15,990/15,990 agree, 0 promotions. T8 read N=10 containment rows instead of N=3 (250/306 month
  blocks wrong; B/600 pooled 0.3537 → 0.2852) — fixed; T8 quarterly pays now weighted by `pays_days`.
  T5 path now starts at the actual fill (state zero = fill price/time; 0/89,124 members with
  peak_vs_fill < 0). `race_by_view` rebuilt on raw chronological prints (labels verified to the
  microsecond). Sub-$1 leader objects added (`winners_open_floored` / `winners_close_open_floored` =
  independent $1-subset re-rank, NOT a subset) with `flr_`/`flreod_` containment counters (B/600 0.3865).
  T4/T7b `unfilled_slots` de-degenerated + `blocked_slots` added. Packet coverage section restored
  (`unresolved_n` 2). All headline numbers reproduced independently from lower-level inputs.
  No parameter selected; PRE-REG-BASKET-02 remains unfrozen.
  Verification: T5 trade-level spot check reproduces staged stats exactly (LODE
  2021-02-01: retr_pre_hi -0.217169, retr_after_hi -0.398082, time_to_hi 33.7, peak = stored
  mfe 0.853333); containment/joint-tail/T8 pays recomputed from day files independently
  match. Subagents remain unusable in this environment (two more 30-min zero-output
  timeouts) — verify directly.

## 8. Canonical read chain + repair recipes (2026-09-20)

Canonical root: `BASKET_ART_ROOT=factory/artifacts/basket/sip`. Full chain (each step resumable; run under the env var):

```bash
.venv/bin/python factory/scripts/basket_aggregate.py          # T1-T11 core
.venv/bin/python factory/scripts/basket_dist.py               # T11 continuous
.venv/bin/python factory/scripts/basket_t5_rawpaths.py --all --workers 4 --force && \
  .venv/bin/python factory/scripts/basket_t5_rawpaths.py --merge-only   # T5 raw-print paths
.venv/bin/python factory/scripts/basket_shadow_sip.py --write # T7 overnight (next-day o570)
.venv/bin/python factory/scripts/basket_t7_econ.py            # T7 pay-for-team (daily + monthly rollups)
.venv/bin/python factory/scripts/basket_t8_stability.py       # T8 month/quarter stability
.venv/bin/python factory/scripts/basket_qa.py                 # must print QA: PASS
.venv/bin/python factory/scripts/basket_capture_funnel.py --all --workers 3 --force && \
  .venv/bin/python factory/scripts/basket_capture_funnel.py --merge-only  # monster -> capture funnel
.venv/bin/python factory/scripts/basket_read.py --write       # READ_PACKET.md + read_packet.json
.venv/bin/python factory/scripts/sip_selection_audit.py --write       # Layer-1 vs anatomy selections
.venv/bin/python factory/scripts/basket_market_base_rates.py          # full-universe base rates
```

Anatomy shape since 2026-09-21: 15 snapshots/day (A_open + A_pm + A_pm31 + 12 B(T)); A_pm31 is the
coequal 09:31-bound A_pm read (same premarket selection, fill = first bar open with et >= 571).
B(T) admission no longer requires a previous-session close. Day files also carry
`winners_close_open` / `winners_close_prev` (EOD leader objects) beside `winners_open` / `winners_prev`.

Long variants: `basket_t5_rawpaths.py --days ...` stages raw-trade paths per day then `--merge-only`; `basket_random_control_sip.py --all --workers 4` stages T9b draws per sampled day (data/sip/t9b_raw/) then `--merge-only`.

**Incomplete-net detection + repair** (found 2026-09-20: 10 days had Layer-2 nets far below `net_n` because ingest raced the candidates step). Detection uses `data/sip/net/coverage/<day>.json` (`symbols` count vs candidates `net_n`) — **never** `net/manifest_index.jsonl`, which holds duplicate stale entries:

```bash
.venv/bin/python factory/scripts/sip_ingest.py --days <days> --net --force --workers 3
.venv/bin/python factory/scripts/sip_ingest.py --index
.venv/bin/python factory/scripts/sip_netbars.py --days <days> --force
.venv/bin/python factory/scripts/sip_coverage.py --write
# then re-extract the affected anatomy days and re-run the read chain:
.venv/bin/python factory/scripts/sip_anatomy.py --days <days> --force
.venv/bin/python factory/scripts/basket_t5_rawpaths.py --days <days> --force
```

**Subagent note (environment)**: background subagents are usable but flaky here — several attempts hung with zero output (30-min inactivity kills), while others completed normally (five scoped audits + two verifiers finished on 2026-09-21). A hung agent's session is NOT resumable (`Task not found`); respawn it fresh with the same work order. Prefer direct execution for anything on the critical path.
