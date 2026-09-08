#!/bin/bash
# Forward-observer supervisor: relaunches the observer immediately if it dies.
# Observer handles idle windows itself; this wrapper guarantees process existence.
REPO="/c/Users/הלל/Desktop/algo projects/Alpacatrader"
cd "$REPO" || { echo "[supervisor] cannot cd $REPO" >> "$REPO/logs/forward_supervisor.log"; exit 1; }
mkdir -p logs
while true; do
  echo "[supervisor] start observer $(date -u +%FT%TZ)" >> logs/forward_supervisor.log
  PYBIN="/c/Users/הלל/AppData/Local/hermes/hermes-agent/venv/Scripts/python"
  [ -x "$PYBIN" ] || PYBIN="python"
  "$PYBIN" -c "import loguru,pandas,dotenv,alpaca.data.historical" 2>/dev/null || { echo "[supervisor] MISSING DEPS for $PYBIN; retry in 60s" >> logs/forward_supervisor.log; sleep 60; continue; }
  "$PYBIN" factory/scripts/forward_observe.py --live >> logs/forward_observe.log 2>&1
  echo "[supervisor] observer exited code=$?; restart in 5s" >> logs/forward_supervisor.log
  sleep 5
done
