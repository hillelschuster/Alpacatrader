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

et_hm=$(TZ=America/New_York date +%H%M)
et_dow=$(TZ=America/New_York date +%u)
if [ "${WD_FORCE_WINDOW:-}" = "1" ]; then et_hm=1000; et_dow=2; fi
DAY=$(TZ=America/New_York date +%F)
log() { echo "$(date -Is) $*" >> "$LOG"; }
say() { [ "$MODE" = "--check" ] && echo "$*"; log "$([ "$MODE" = "--check" ] && echo 'check:' || echo 'run:') $*"; }

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
J="$REPO/data/forward/bot/$DAY/journal.jsonl"
stale=1
if [ -f "$J" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$J") ))
  [ "$age" -le 720 ] && stale=0
fi
if [ "$stale" = "1" ]; then
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
  oage=$(( $(date +%s) - $(stat -c %Y "$O" 2>/dev/null || echo 0) ))
  if [ "$oage" -gt 1200 ]; then
    n=$(count_proc 'forward_observe.py')
    if [ "${n:-0}" = "0" ]; then relaunch "$REPO/factory/scripts/observe_supervisor.sh" "observer(absent)"
    else log "observer present ($n) but log stale -> kill+relaunch"; [ "$MODE" = "--check" ] || powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*forward_observe.py*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null
         relaunch "$REPO/factory/scripts/observe_supervisor.sh" "observer(hung)"; fi
  else
    say "observer fresh (age ${oage}s)"
  fi
fi
exit 0
