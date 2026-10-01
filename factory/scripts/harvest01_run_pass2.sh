#!/usr/bin/env bash
# HARVEST01 pass 2 — regenerate every lane uniformly AFTER pass 1 completes, now that
# the selection lane also emits the compact-anchored variant (previous-session rth
# compact c_last, split-normalized — the anchor the legacy A_pm lane used). Every
# stage runs with --force so all variants (raw/primary/listed/compact) are covered on
# all 1,066 dev days, and the reports are rebuilt from the uniform surface.
set -u
W=/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1
PY=/home/hillel/projects/Alpacatrader/.venv/bin/python
D=/home/hillel/projects/Alpacatrader/data
L=$D/harvest01/logs
mkdir -p "$L"
cd "$W" || exit 1

echo "== pass2 waiting for pass1 chain done $(date -Is)"
for i in $(seq 1 300); do
  if grep -q "== chain done" "$L/chain.log" 2>/dev/null; then break; fi
  sleep 60
done
echo "== pass2 start $(date -Is)"

run () {
  name=$1; shift
  echo "== $name start $(date -Is)"
  "$@" >> "$L/$name.log" 2>&1
  echo "== $name exit=$? $(date -Is)"
}

run p2_select   $PY factory/scripts/basket_harvest_select.py --dev-days --workers 3 --force
run p2_bars     $PY factory/scripts/basket_harvest_bars.py --dev-days --workers 3 --force
run p2_sim      $PY factory/scripts/basket_harvest_sim.py --dev-days --workers 3 --force
run p2_report   $PY factory/scripts/basket_harvest_report.py
run p2_contain  $PY factory/scripts/basket_harvest_containment.py
run p2_mgmt     $PY factory/scripts/basket_harvest_mgmt.py --dev-days --workers 3 --force
run p2_mgmt_rep $PY factory/scripts/basket_harvest_mgmt_report.py
run p2_pol      $PY factory/scripts/basket_harvest_policies.py --dev-days --workers 3 --force
run p2_pol_rep  $PY factory/scripts/basket_harvest_policies_report.py
run p2_basket   $PY factory/scripts/basket_harvest_basket.py --dev-days --workers 3 --force
run p2_basket_rep $PY factory/scripts/basket_harvest_basket_report.py
run p2_window   $PY factory/scripts/basket_harvest_window.py --dev-days --workers 3 --force
run p2_window_rep $PY factory/scripts/basket_harvest_window_report.py
run p2_anatomy  $PY factory/scripts/basket_harvest_anatomy.py
run p2_submin   $PY factory/scripts/basket_harvest_subminute.py --sample 40
run p2_assemble $PY factory/scripts/basket_harvest_assemble.py
echo "== pass2 done $(date -Is)"
