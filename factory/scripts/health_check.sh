#!/usr/bin/env bash
# Stack health: journal freshness + ALERT file, evaluated only inside the US session.
# Exits non-zero on failure so `systemctl --failed` / journalctl surface it.
# ET clock comes from python zoneinfo, so the host timezone is irrelevant.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO" || exit 2
LOGDIR="$REPO/logs"; mkdir -p "$LOGDIR"; LOG="$LOGDIR/watchdog.log"
say() { printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG"; echo "$*"; }

read -r ET_HM ET_DOW DAY <<<"$(python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
n = datetime.now(ZoneInfo("America/New_York"))
print(n.hour * 100 + n.minute, n.weekday(), n.date())
PY
)" || { say "cannot determine ET clock - no action"; exit 0; }

[ "$ET_DOW" -ge 5 ] && { say "weekend - skip"; exit 0; }
if [ "$ET_HM" -lt 0910 ] || [ "$ET_HM" -gt 1620 ]; then
  say "outside 09:10-16:20 ET ($ET_HM) - skip"; exit 0
fi

fail=0
J="$REPO/data/forward/bot/$DAY/journal.jsonl"
if [ -f "$J" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$J") ))
  if [ "$age" -le 720 ]; then say "bot fresh (age ${age}s)"; else say "FAIL bot journal stale (age ${age}s)"; fail=1; fi
else
  say "FAIL bot journal missing ($J)"; fail=1
fi

if [ -f "$REPO/data/forward/bot/ALERT" ]; then say "FAIL ALERT file present"; fail=1; fi

if [ "$ET_HM" -ge 0925 ]; then
  L="$LOGDIR/forward_observe.log"
  if [ -f "$L" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$L") ))
    if [ "$age" -le 1200 ]; then say "observer fresh (age ${age}s)"; else say "FAIL observer log stale (age ${age}s)"; fail=1; fi
  else
    say "FAIL observer log missing"; fail=1
  fi
fi
exit $fail
