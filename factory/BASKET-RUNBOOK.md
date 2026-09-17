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

Checks: JSON parse of every day file; snapshot count 13/14; per-filled-member fill/ladders/mfe/mae/states; bars column set + presence; zero `.tmp` leftovers; calendar completeness vs leaderboard dates with declared known-skips only (2024/2025-01: no raw data; 2025-02-03: no prev-close seed).

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
