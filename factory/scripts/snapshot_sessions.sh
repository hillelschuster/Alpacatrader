#!/usr/bin/env bash
# Point-in-time snapshot of the agent conversation store for this project -> storage box (sha256-verified).
# Run it when you close a conversation:  bash factory/scripts/snapshot_sessions.sh
set -euo pipefail
BOX=alpacatrader/archive/omp-sessions; n=$(date -u +%F_%H%M); tmp=$(mktemp)
cd ~/.omp/agent/sessions
tar -cf - --warning=no-file-changed -- *lpacatrader* 2>/dev/null | tee >(sha256sum | cut -d' ' -f1 >"$tmp") \
  | rclone rcat "storagebox:$BOX/alpacatrader_sessions_$n.tar"
sleep 2
r=$(ssh -o BatchMode=yes storagebox sha256sum "$BOX/alpacatrader_sessions_$n.tar" | cut -d' ' -f1)
[ -n "$r" ] && [ "$r" = "$(cat "$tmp")" ] && echo "VERIFIED $BOX/alpacatrader_sessions_$n.tar" || { echo "FAIL_VERIFY" >&2; exit 1; }
