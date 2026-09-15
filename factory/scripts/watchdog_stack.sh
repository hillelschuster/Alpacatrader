#!/usr/bin/env bash
# Stack watchdog: during market hours, relaunch a dead/stale bot or observer.
# Runs from Windows Task Scheduler (ASCII wrapper copy at C:\Users\Public\algo_watchdog.sh).
# Modes: --check (print decisions, act on nothing) | normal (act).
# Duplicate guard: relaunches only when the corresponding process is absent;
# a process that exists but whose journal is stale is killed and relaunched.
set -u
REPO="/c/Users/הלל/Desktop/algo projects/Alpacatrader"
[ -d "$REPO" ] || REPO="/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader"
LOG="$REPO/logs/watchdog.log"
mkdir -p "$REPO/logs"
BASH_WIN='C:\Program Files\Git\bin\bash.exe'
MODE="${1:-}"

log() { echo "$(date -Is) $*" >> "$LOG"; }
say() { [ "$MODE" = "--check" ] && echo "$*"; log "$([ "$MODE" = "--check" ] && echo 'check:' || echo 'run:') $*"; }

# ET clock via the venv python. git-bash ignores TZ, which made this watchdog act
# on local time and kill the healthy out-of-window observer every ~30 minutes.
PYBIN_WD="/c/Users/הלל/AppData/Local/hermes/hermes-agent/venv/Scripts/python"
read_et() { "$1" -c "from datetime import datetime; from zoneinfo import ZoneInfo; n=datetime.now(ZoneInfo('America/New_York')); print(n.strftime('%H%M'), n.strftime('%u'), n.strftime('%F'))" 2>/dev/null | tr -d '\r'; }
et_all=$(read_et "$PYBIN_WD")
if [ -z "$et_all" ]; then   # WSL / Linux context: git-bash style path does not exist
  PYBIN_WD="/mnt/c/Users/הלל/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
  et_all=$(read_et "$PYBIN_WD")
fi
et_hm=$(printf '%s' "$et_all" | awk '{print $1}')
et_dow=$(printf '%s' "$et_all" | awk '{print $2}')
DAY=$(printf '%s' "$et_all" | awk '{print $3}')
if [ "${WD_FORCE_WINDOW:-}" = "1" ]; then et_hm=1000; et_dow=2; fi
if [ -z "$DAY" ]; then DAY=$(date +%F); fi
if [ -z "$et_hm" ] || [ -z "$et_dow" ]; then say "cannot determine ET clock - no action"; exit 0; fi

# weekends: Fri(5) after 17:00 ET through Sat/Sun -> nothing to watch
if [ "$et_dow" -ge 6 ]; then say "weekend - skip"; exit 0; fi
if [ "$et_hm" -lt 0910 ] || [ "$et_hm" -gt 1615 ]; then say "outside 09:10-16:15 ET - skip"; exit 0; fi

count_proc() { # $1 = substring
  powershell.exe -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*$1*' }).Count" 2>/dev/null | tr -d '\r' | tail -1
}
relaunch() { # $1 = script path (windows form), $2 = label
  if [ "$MODE" = "--check" ]; then say "WOULD relaunch $2"; return; fi
  log "relaunching $2"
  cmd.exe /c start "" /min "$BASH_WIN" -lc "bash '$1' >/dev/null 2>&1"
}

# --- bot: journal heartbeat must be < 12 min old ---
# Fail-safe asymmetry: killing a healthy bot mid-session is far worse than
# missing a restart, so a MISSING journal is acted on only when no process runs
# (process present + no journal = watchdog path problem, not a hang).
J="$REPO/data/forward/bot/$DAY/journal.jsonl"
age=-1; stale=0; missing=0
if [ -f "$J" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$J") ))
  [ "$age" -gt 720 ] && stale=1
else
  missing=1
fi
if [ "$missing" = "1" ]; then
  n=$(count_proc 'flush_bot.py')
  if [ "${n:-0}" = "0" ]; then relaunch "$REPO/factory/scripts/flush_bot_supervisor.sh" "bot(journal missing, no process)"
  else say "bot journal missing ($J) but process present ($n) - no action"; fi
elif [ "$stale" = "1" ]; then
  n=$(count_proc 'flush_bot.py')
  if [ "${n:-0}" = "0" ]; then relaunch "$REPO/factory/scripts/flush_bot_supervisor.sh" "bot(supervisor absent)"
  else log "bot process present ($n) but journal stale -> kill+relaunch"; [ "$MODE" = "--check" ] || powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*flush_bot.py*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null
       relaunch "$REPO/factory/scripts/flush_bot_supervisor.sh" "bot(hung)"; fi
else
  say "bot fresh (age ${age}s)"
fi

# --- observer: only during its window 09:25-16:10 ET; log < 20 min old ---
if [ "$et_hm" -ge 0925 ] && [ "$et_hm" -le 1610 ]; then
  O="$REPO/logs/forward_observe.log"
  oage=-1
  [ -f "$O" ] && oage=$(( $(date +%s) - $(stat -c %Y "$O") ))
  if [ "$oage" -lt 0 ]; then
    n=$(count_proc 'forward_observe.py')
    if [ "${n:-0}" = "0" ]; then relaunch "$REPO/factory/scripts/observe_supervisor.sh" "observer(log missing, no process)"
    else say "observer log missing but process present ($n) - no action"; fi
  elif [ "$oage" -gt 1200 ]; then
    n=$(count_proc 'forward_observe.py')
    if [ "${n:-0}" = "0" ]; then relaunch "$REPO/factory/scripts/observe_supervisor.sh" "observer(absent)"
    else log "observer present ($n) but log stale -> kill+relaunch"; [ "$MODE" = "--check" ] || powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*forward_observe.py*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null
         relaunch "$REPO/factory/scripts/observe_supervisor.sh" "observer(hung)"; fi
  else
    say "observer fresh (age ${oage}s)"
  fi
fi
exit 0
