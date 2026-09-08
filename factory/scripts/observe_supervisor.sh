#!/bin/bash
# Forward-observer supervisor: relaunches the observer immediately if it dies.
# Idles outside the session window are handled by the observer itself; this
# wrapper only guarantees the process exists. Log: logs/forward_supervisor.log
cd "$(dirname "$0")/../.." || exit 1
mkdir -p logs
while true; do
  echo "[supervisor] start observer $(date -u +%FT%TZ)" >> logs/forward_supervisor.log
  python factory/scripts/forward_observe.py --live >> logs/forward_observe.log 2>&1
  code=$?
  echo "[supervisor] observer exited code=$code; restart in 5s" >> logs/forward_supervisor.log
  [ $code -eq 0 ] && sleep 5 || sleep 5
done
