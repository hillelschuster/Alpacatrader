#!/bin/bash
# Flush-bot supervisor: relaunches flush_bot.py immediately if it dies.
# Dry-run by default; paper orders activate when data/forward/bot/LIVE exists.
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
mkdir -p logs
while true; do
  echo "[supervisor] start flush_bot $(date -u +%FT%TZ)" >> logs/flush_bot_supervisor.log
  PYBIN="/c/Users/הלל/AppData/Local/hermes/hermes-agent/venv/Scripts/python"
  [ -x "$PYBIN" ] || PYBIN="python"
  "$PYBIN" -c "import loguru,pandas,dotenv,pyarrow,alpaca.data.historical,alpaca.trading.client" 2>/dev/null || { echo "[supervisor] MISSING DEPS for $PYBIN; retry in 60s" >> logs/flush_bot_supervisor.log; sleep 60; continue; }
  ARGS=""
  [ -f data/forward/bot/LIVE ] && ARGS="--live"
  "$PYBIN" factory/scripts/flush_bot.py $ARGS >> logs/flush_bot.log 2>&1
  echo "[supervisor] flush_bot exited code=$?; restart in 5s" >> logs/flush_bot_supervisor.log
  sleep 5
done
