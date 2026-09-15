# VPS deployment — flush-bot stack (systemd)

Why: the current chain is 4 hops (Windows Task Scheduler → git-bash → bash
supervisor → venv python) through a Hebrew path and WSL interop. Three bugs on
2026-09-15 came from that layer alone: git-bash ignores `TZ` (the watchdog ran on
local IDT time), a `\r` from Windows python broke the journal path, and the
`cmd start` title parsing ate the observer's path. systemd replaces all of it:
`Restart=always` instead of the bash supervisor, ET-aware timers instead of
shell date math, and `journalctl` instead of log scraping.

## What runs on the VPS

| unit | replaces | what it does |
|---|---|---|
| `flush-bot.service` | `flush_bot_supervisor.sh` (+ supervisor loop) | paper trader, `--live`, `POLL_S=60`, `Restart=always` |
| `forward-observe.service` | `observe_supervisor.sh` | evidence observer, `--live` |
| `daily-close.timer` | manual `daily_close.sh` | 16:05 ET Mon–Fri: ledger + replay + journal summary |
| `health-check.timer` + `.service` | `watchdog_stack.sh` (bash) | every 10 min: journal freshness + ALERT file; exits non-zero on failure so `systemctl --failed` shows it |

## Files to copy

- the repo (git clone; `data/` and `logs/` are in `.gitignore`)
- `.env` → `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER=true` (paper keys only)
- `data/pit/pit_symbols.parquet` (8,120,639 rows — the bot reads the latest vintage on first scan)
- `data/forward/bot/LIVE` (arm flag; **the bot is a no-op without it**)
- optional: `data/forward/**` (journals, for continuity from the Windows run)
- research only: `data/leaderboard/**`, `data/iex_tape/**` (the live path does not need them)

## Prerequisites

`python3.11+` with `alpaca-py pandas pyarrow python-dotenv loguru`, plus `tzdata`
(the code resolves clocks via `zoneinfo("America/New_York")`; there is no `TZ`
env dependency anywhere).

## Install

```bash
sudo cp factory/deploy/*.service factory/deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now flush-bot forward-observe daily-close.timer health-check.timer
```

## Verify (must all be true)

```bash
systemctl status flush-bot --no-pager
journalctl -u flush-bot -n 50 --no-pager | tail
tail -n 20 logs/flush_bot.log
```
- `{"event": "start", "live": true}` then `startup_reconcile` on boot
- `{"event": "alive", "polls": N}` every ~5 min (proof of life)
- `scan_row` events every ~60 s during 09:30–16:00 ET
- `place_bid` only when a top-3 name is in strict state
- **no `error` events**, and no `data/forward/bot/ALERT` file

## Operations

- **Stop trading, keep the stack up:** `touch data/KILL` → cancels owned entry
  buys, leaves protective OCO sells, exits; systemd restarts it and the startup
  KILL check makes it a quiet no-op. Remove the file to resume.
- **Alert:** `data/forward/bot/ALERT` exists after 3 consecutive poll errors (cleared
  on the next good poll). `health-check` also fails loudly.
- **Post-close:** `daily-close.timer` writes `logs/daily_close.log`; the judge is
  `flush_bot_ledger.py` (partitions by `pf_est` / `n_strict_est` / AM-PM) compared
  against the RAW A3b pf2 baseline (+1.13%/trade) once n ≥ 30 fills.
- **Never** run research `uv` commands concurrently with each other on this box
  (cache lock); the live services do not use `uv`.

## Timezone

All timers carry `Timezone=America/New_York`. The bot's own clocks
(`ENTRY_CUTOFF` 15:30 ET, `FLAT_ET` 15:55 ET, day-roll at ET midnight) are
computed in code, so the host TZ is irrelevant — which is exactly the property
the Windows setup lacked.
