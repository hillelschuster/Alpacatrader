#!/usr/bin/env bash
# Daily close-out for the live paper stack: fills ledger + judgement partitions
# + "what would the bot have done" replay, appended as one summary line.
# Run after the US close (~23:05 IDT). Read-only: no orders, no writes outside logs/.
# Usage: bash factory/scripts/daily_close.sh [YYYY-MM-DD]   (default: today ET)
set -u
REPO="/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader"
cd "$REPO" || exit 1
export PATH="$HOME/.local/bin:$PATH"
UV="uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py --with python-dotenv"
DAY="${1:-$(TZ=America/New_York date +%F)}"
LOG="logs/daily_close.log"
mkdir -p logs

echo "=== $DAY ledger ==="
$UV python factory/scripts/flush_bot_ledger.py 2>&1 | tail -n 20
echo
echo "=== $DAY replay (live semantics: 15:30 cancel, IEX bars) ==="
$UV python factory/scripts/replay_decisions.py --day "$DAY" 2>&1 | tail -n 30
echo
echo "=== $DAY journal summary ==="
python3 - "$DAY" <<'PY'
import collections, json, sys
day = sys.argv[1]
try:
    rows = [json.loads(l) for l in open(f"data/forward/bot/{day}/journal.jsonl",
                                        encoding="utf-8", errors="replace") if l.strip()]
except FileNotFoundError:
    print("no journal for", day); raise SystemExit
c = collections.Counter(r.get("event") for r in rows)
key = {k: c.get(k, 0) for k in ("place_bid", "fill", "oco", "exit", "tl30_exit", "error", "alert", "alive")}
print("events:", dict(c))
print("key:", key)
last = rows[-1]
print("last:", str(last.get("ts"))[11:19], last.get("event"))
PY
{
  echo "$(date -Is) $DAY close-out run"
} >> "$LOG"
echo
echo "appended to $LOG"
