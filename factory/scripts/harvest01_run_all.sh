#!/usr/bin/env bash
# HARVEST01 — full dev pipeline, resume-safe. Gates on the broad PM snapshot
# acquisition, then fills PM selection gaps, bars, unmanaged cells, readouts,
# containment and the management families. Every stage is idempotent; each stage's
# exit code is logged and the chain continues (partial data is still usable).
set -u
W=/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1
PY=/home/hillel/projects/Alpacatrader/.venv/bin/python
D=/home/hillel/projects/Alpacatrader/data
L=$D/harvest01/logs
mkdir -p "$L"
cd "$W" || exit 1

echo "== chain start $(date -Is)"
for i in $(seq 1 200); do
  n=$(ls "$D"/sip/pm_snapshots/*.parquet 2>/dev/null | wc -l)
  if [ "$n" -ge 1066 ]; then break; fi
  echo "waiting for PM acquisition: $n/1066"
  sleep 60
done
echo "== PM snapshots: $(ls "$D"/sip/pm_snapshots/*.parquet 2>/dev/null | wc -l)"

run () {
  name=$1; shift
  echo "== $name start $(date -Is)"
  "$@" >> "$L/$name.log" 2>&1
  echo "== $name exit=$? $(date -Is)"
}

run select_final   $PY factory/scripts/basket_harvest_select.py --dev-days --force-missing-pm --workers 3
run bars_full      $PY factory/scripts/basket_harvest_bars.py --dev-days --workers 3
run sim_full       $PY factory/scripts/basket_harvest_sim.py --dev-days --workers 3
run report         $PY factory/scripts/basket_harvest_report.py
run containment    $PY factory/scripts/basket_harvest_containment.py
run mgmt_full      $PY factory/scripts/basket_harvest_mgmt.py --dev-days --workers 3
run mgmt_report    $PY factory/scripts/basket_harvest_mgmt_report.py
echo "== counts: selected=$(ls "$D"/harvest01/base/selected/*.parquet 2>/dev/null | wc -l) champs=$(ls "$D"/harvest01/base/champs/*.parquet 2>/dev/null | wc -l) bars=$(ls "$D"/harvest01/base/bars/*.parquet 2>/dev/null | wc -l) sim=$(ls "$D"/harvest01/sim/cells/*.parquet 2>/dev/null | wc -l) mgmt=$(ls "$D"/harvest01/mgmt/*.parquet 2>/dev/null | wc -l)"
echo "== chain done $(date -Is)"
