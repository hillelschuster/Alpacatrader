#!/usr/bin/env bash
# Archive a local folder to the Hetzner Storage Box, tidily (see storagebox:alpacatrader/README.md).
#
#   factory/scripts/archive_to_storagebox.sh <local_dir> "<why it is archived>" [--delete-local]
#
# - Destination mirrors the path relative to $HOME: storagebox:alpacatrader/archive/<rel path>/
# - Subfolders named raw/ (or any folder whose entries start with YYYY-MM) are batched per month;
#   everything else goes into other.tar.gz. Streams tar | gzip -1 | rclone rcat (no local temp).
# - Each batch is verified: sha256 of the uploaded stream == `ssh storagebox sha256sum` on the box.
# - Writes MANIFEST.md next to the batches and appends a row to the box README index.
# - Deletes the local folder ONLY with --delete-local and ONLY if every batch verified.
# Re-runnable: verified batches are skipped (markers in ~/.cache/storagebox_archive/<rel>/).
set -uo pipefail

SRC=$(realpath "${1:?local dir}")
WHY=${2:?reason}
DELETE=${3:-}
REL=${SRC#"$HOME"/}
[ "$REL" = "$SRC" ] && { echo "source must live under \$HOME" >&2; exit 2; }
DST="alpacatrader/archive/$REL"
STATE="$HOME/.cache/storagebox_archive/$REL"
mkdir -p "$STATE"
LOG="$STATE/log"
cd "$SRC" || exit 1

push() {  # $1 = batch name, rest = paths relative to SRC
  local name=$1; shift
  [ -f "$STATE/$name.ok" ] && return 0
  tar -cf - "$@" | gzip -1 | tee >(sha256sum | cut -d' ' -f1 >"$STATE/$name.sha") \
    | rclone rcat "storagebox:$DST/$name.tar.gz" || { echo "FAIL_UPLOAD $name" >>"$LOG"; return 1; }
  sleep 1
  local remote
  remote=$(ssh -o BatchMode=yes storagebox sha256sum "$DST/$name.tar.gz" | cut -d' ' -f1)
  if [ -n "$remote" ] && [ "$remote" = "$(cat "$STATE/$name.sha")" ]; then
    touch "$STATE/$name.ok"; echo "OK $name $remote" >>"$LOG"
  else
    echo "FAIL_VERIFY $name" >>"$LOG"; return 1
  fi
}
export -f push; export SRC DST STATE LOG

rclone mkdir "storagebox:$DST"   # create once up front (parallel mkdir races fail on the box)

# Month-batched folders: any top-level dir whose entries look like YYYY-MM...
batched=(); others=()
for d in */ .[!.]*; do
  [ -e "$d" ] || continue
  d=${d%/}
  if [ -d "$d" ] && ls "$d" | head -50 | grep -qE '^[0-9]{4}-[0-9]{2}'; then batched+=("$d"); else others+=("$d"); fi
done
jobs=()
for d in "${batched[@]}"; do
  for m in $(ls "$d" | grep -oE '^[0-9]{4}-[0-9]{2}' | sort -u); do jobs+=("${d//\//_}_$m|$d/$m"); done
done
printf '%s\n' "${jobs[@]}" | xargs -P 3 -I{} bash -c \
  'n=${0%%|*}; p=${0#*|}; cd "$SRC" && push "$n" $p*' {}
[ ${#others[@]} -gt 0 ] && push other "${others[@]}"

total=$(( ${#jobs[@]} + (${#others[@]} > 0 ? 1 : 0) ))
ok=$(ls "$STATE"/*.ok 2>/dev/null | wc -l)
echo "verified $ok / $total batches"

# MANIFEST.md + README index row
size=$(rclone size "storagebox:$DST" --json | python3 -c 'import json,sys;print(round(json.load(sys.stdin)["bytes"]/2**30,2))')
local_gb=$(du -sb "$SRC" | awk '{printf "%.1f", $1/2**30}')
{
  echo "# $(basename "$SRC") - archive manifest"; echo
  echo "Archived $(date -u +%F) from \`~/$REL\`. Why: $WHY"
  echo "Local size ${local_gb} GiB -> ${size} GiB on the box (tar + gzip -1)."; echo
  echo "## Restore"; echo '```'
  echo "mkdir -p <dest> && cd <dest>"
  echo "for f in \$(rclone lsf storagebox:$DST --include '*.tar.gz'); do rclone cat storagebox:$DST/\$f | tar -xzf -; done"
  echo '```'; echo; echo "## Batches (sha256: local stream == box sha256sum)"; echo
  echo "| batch | sha256 |"; echo "|---|---|"
  for s in $(ls "$STATE"/*.sha | sort); do echo "| $(basename "$s" .sha).tar.gz | $(cat "$s") |"; done
} >"$STATE/MANIFEST.md"
rclone copyto "$STATE/MANIFEST.md" "storagebox:$DST/MANIFEST.md"

if [ "$ok" -eq "$total" ]; then
  status="$ok/$total batches sha256-verified"
  if [ "$DELETE" = "--delete-local" ]; then rm -rf "$SRC"; status="$status; local copy deleted $(date -u +%F)"; fi
  rclone cat storagebox:alpacatrader/README.md >"$STATE/README.md"
  grep -qF "| $DST/ |" "$STATE/README.md" || \
    echo "| ${DST#alpacatrader/}/ | $WHY | ${size} GiB (from ${local_gb} GiB) | $(date -u +%F) | $status |" \
      >>"$STATE/README.md"
  rclone copyto "$STATE/README.md" storagebox:alpacatrader/README.md
  echo "DONE: $status"
else
  echo "INCOMPLETE: re-run the same command to retry failed batches (see $LOG); nothing deleted" >&2
  exit 1
fi
