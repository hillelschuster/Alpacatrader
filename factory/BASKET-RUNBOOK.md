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
- 2026-09-20 (evening): SIP regeneration + repair pass done. Canonical root = `factory/artifacts/basket/sip` (QA PASS, 1,066 days). Repairs 1-12 applied; T5 rebuilt from raw SIP prints (82,853 members; peak-minute ordering resolved); T7 pay-for-team added; T8 stability + T9b SIP random control + market base rates + selection audit produced. 10 days with incomplete Layer-2 nets found and repaired (net-repair recipe in §8); those 10 anatomy days re-extracted and the read chain re-run. Sealed 2024 + 2025-01 acquired (mechanics only, certified in `SEALED_2024_CERT.md`); reserved months untouched. No parameter selected; PRE-REG-BASKET-02 remains unfrozen. Next: owner gate on the integrated read.

## 8. Canonical read chain + repair recipes (2026-09-20)

Canonical root: `BASKET_ART_ROOT=factory/artifacts/basket/sip`. Full chain (each step resumable; run under the env var):

```bash
.venv/bin/python factory/scripts/basket_aggregate.py          # T1-T11 core
.venv/bin/python factory/scripts/basket_dist.py               # T11 continuous
.venv/bin/python factory/scripts/basket_t5_rawpaths.py --merge-only   # T5 from raw prints (per-day staging data/sip/t5_raw/)
.venv/bin/python factory/scripts/basket_shadow_sip.py --write # T7 overnight (next-day o570)
.venv/bin/python factory/scripts/basket_t7_econ.py            # T7 policy-free pay-for-team + break-even
.venv/bin/python factory/scripts/basket_t8_stability.py       # T8 month/quarter stability
.venv/bin/python factory/scripts/basket_qa.py                 # must print QA: PASS
.venv/bin/python factory/scripts/basket_read.py --write       # READ_PACKET.md + read_packet.json
.venv/bin/python factory/scripts/sip_selection_audit.py --write       # Layer-1 vs anatomy selections
.venv/bin/python factory/scripts/basket_market_base_rates.py          # full-universe base rates + funnel
```

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

**Subagent note (environment)**: as of 2026-09-20 background subagents are unusable here — one ran 31 min with no output, others were wiped by an environment restart. Execute research scripts directly (matches the repo AGENTS.md guidance).
