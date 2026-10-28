#!/usr/bin/env bash
# Archive a local folder to the Hetzner Storage Box, tidily (see storagebox:alpacatrader/README.md).
#
#   factory/scripts/archive_to_storagebox.sh <local_dir> "<why it is archived>" [options]
#
# Options
#   --dest-name NAME        destination under storagebox:alpacatrader/archive/ (default: path relative
#                          to $HOME; required when <local_dir> is not under $HOME, e.g. /mnt/c/...).
#   --max-batch-bytes SIZE  cap a batch at SIZE (K/M/G/T suffixes, default 6G).
#   --jobs N                parallel uploads (default 3).
#   --exclude PATTERN       repeatable; basename glob, or path glob when it contains a "/".
#   --dry-run               print the batch plan, totals and estimated hours; upload nothing.
#   --delete-local          delete the archived regular files (then now-empty dirs) after ALL batches verify.
#
# Always excluded: .env, .env.*, *.pem, id_rsa*, id_ed25519* and every .git, __pycache__, .pytest_cache,
# .ruff_cache, node_modules, .venv directory.
#
# Guarantees
# - Content-aware, deterministic batches: entries whose extension is already compressed
#   (parquet npz pt zip gz zst feather joblib) go into a PLAIN .tar (gzip would burn CPU for ~0%);
#   everything else goes into .tar.gz (gzip -1). Kinds are never mixed and a directory yields at most
#   one stream per kind per size cap. Batch names are <dir>__<kind>_<NNN>, so re-runs resume exactly.
# - Folders holding YYYY-MM* entries are still batched per month (<dir>__<YYYY-MM>__<kind>_<NNN>);
#   anything else inside such a folder lands in <dir>__other__<kind>_<NNN> (nothing is silently dropped).
# - Each batch is streamed (tar | [gzip -1] | rclone rcat, no local temp copy) and verified: the sha256
#   and the size of the uploaded stream must match `ssh storagebox sha256sum` / `stat` on the box.
# - MANIFEST.md + RESTORE.sh are written into the archive dir and a row is appended to the box README
#   index, only once every batch is verified.
# - --delete-local never uses rm -rf: it refuses to run when the tree holds symlinks pointing outside
#   the source, deletes only regular files that were archived, removes only dirs left empty, and
#   reports what remains (excluded/unarchived files).
# - Verified batches are skipped on re-run (markers in ~/.cache/storagebox_archive/<dest-name>/),
#   cross-checked against the actual size on the box.
# shellcheck disable=SC2016  # deliberate: single-quoted strings are emitted into RESTORE.sh or read by the child bash -c
set -uo pipefail

PROG=$(basename "$0")
COMPRESSED_EXTS=" parquet npz pt zip gz zst feather joblib "
ALWAYS_PRUNE=(.git __pycache__ .pytest_cache .ruff_cache node_modules .venv)
ALWAYS_EXCLUDE=(.env '.env.*' '*.pem' 'id_rsa*' 'id_ed25519*')
REMOTE_BYTES_PER_SEC=$((8 * 1024 * 1024))   # measured uplink ceiling, used for --dry-run estimates
GZ_ESTIMATE_DIV=2.5                         # conservative gzip ratio for --dry-run time estimates

usage() {  # print the leading comment block (everything after the shebang, before the first command)
  awk 'NR==1{next} /^#[[:space:]]*shellcheck/{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "$0"
}

die() { echo "$PROG: $*" >&2; exit 2; }

parse_size() {  # 6G / 500M / 1024 -> bytes
  local s=${1^^}
  case $s in
    *K) echo $(( ${s%K} * 1024 ));;
    *M) echo $(( ${s%M} * 1024 * 1024 ));;
    *G) echo $(( ${s%G} * 1024 * 1024 * 1024 ));;
    *T) echo $(( ${s%T} * 1024 * 1024 * 1024 * 1024 ));;
    ''|*[!0-9]*) die "bad size: $1";;
    *) echo "$s";;
  esac
}

gib() { awk -v b="${1:-0}" 'BEGIN{printf "%.2f", b / 1073741824}'; }
mib() { awk -v b="${1:-0}" 'BEGIN{printf "%.1f", b / 1048576}'; }

sanitize() {  # batch-name safe
  local s=${1#./}
  s=${s//\//_}
  s=${s//[^A-Za-z0-9._-]/_}
  printf '%s' "$s"
}

is_compressed() {
  local base=${1##*/} ext=""
  case $base in *.*) ext=${base##*.};; esac
  ext=${ext,,}
  case " $COMPRESSED_EXTS " in *" $ext "*) return 0;; esac
  return 1
}

# ---------------------------------------------------------------- arguments
MAX_BATCH_BYTES=$((6 * 1024 * 1024 * 1024))
JOBS=3
DRY=0
DELETE=0
DEST_NAME=""
SRC_ARG=""
WHY=""
EXCLUDES=()

while [ $# -gt 0 ]; do
  case $1 in
    --dest-name)   (($# >= 2)) || die "--dest-name needs a value"; DEST_NAME=$2; shift 2;;
    --dest-name=*) DEST_NAME=${1#*=}; shift;;
    --max-batch-bytes)   (($# >= 2)) || die "--max-batch-bytes needs a value"; MAX_BATCH_BYTES=$(parse_size "$2"); shift 2;;
    --max-batch-bytes=*) MAX_BATCH_BYTES=$(parse_size "${1#*=}"); shift;;
    --jobs)   (($# >= 2)) || die "--jobs needs a value"; JOBS=$2; shift 2;;
    --jobs=*) JOBS=${1#*=}; shift;;
    --exclude)   (($# >= 2)) || die "--exclude needs a value"; EXCLUDES+=("$2"); shift 2;;
    --exclude=*) EXCLUDES+=("${1#*=}"); shift;;
    --dry-run) DRY=1; shift;;
    --delete-local) DELETE=1; shift;;
    -h|--help) usage; exit 0;;
    --) shift; break;;
    -*) die "unknown option: $1 (see --help)";;
    *)
      if   [ -z "$SRC_ARG" ]; then SRC_ARG=$1
      elif [ -z "$WHY" ];     then WHY=$1
      else die "unexpected argument: $1"
      fi
      shift;;
  esac
done
while [ $# -gt 0 ]; do
  if   [ -z "$SRC_ARG" ]; then SRC_ARG=$1
  elif [ -z "$WHY" ];     then WHY=$1
  else die "unexpected argument: $1"
  fi
  shift
done

[ -n "$SRC_ARG" ] || { usage; exit 2; }
[ -n "$WHY" ] || die "missing <why it is archived> (it becomes the README index column)"
case $JOBS in ''|*[!0-9]*|0) die "--jobs must be a positive integer";; esac
case $MAX_BATCH_BYTES in ''|*[!0-9]*|0) die "bad --max-batch-bytes";; esac

SRC=$(realpath -e -- "$SRC_ARG" 2>/dev/null) || die "no such directory: $SRC_ARG"
[ -d "$SRC" ] || die "not a directory: $SRC"
[ "$SRC" != "/" ] || die "refusing to archive /"

if [ -z "$DEST_NAME" ]; then
  case $SRC in
    "$HOME"/*) DEST_NAME=${SRC#"$HOME"/};;
    *) die "source is not under \$HOME; pass --dest-name NAME (destination under storagebox:alpacatrader/archive/)" ;;
  esac
fi
DEST_NAME=${DEST_NAME#/}
DEST_NAME=${DEST_NAME%/}
[ -n "$DEST_NAME" ] || die "--dest-name resolved to an empty path"
case "/$DEST_NAME/" in *"/../"*|*"//"*) die "invalid --dest-name: $DEST_NAME";; esac
case $DEST_NAME in backup|backup/*) die "storagebox:backup/ is off limits";; esac

DST="alpacatrader/archive/$DEST_NAME"
DEST_REL=${DST#alpacatrader/}
STATE="$HOME/.cache/storagebox_archive/$DEST_NAME"
LOG="$STATE/log"
rm -rf "$STATE/lists" "$STATE/tmp"
mkdir -p "$STATE/lists" "$STATE/tmp" || die "cannot create state dir $STATE"
rm -f "$STATE/plan.tsv" "$STATE/uploaded.txt" "$STATE"/*.meta

cd "$SRC" || die "cannot cd to $SRC"

# ---------------------------------------------------------------- batch plan
prune_conds=()
add_prune() {
  local pat=$1
  [ ${#prune_conds[@]} -gt 0 ] && prune_conds+=(-o)
  if [[ $pat == */* ]]; then prune_conds+=(-path "./$pat"); else prune_conds+=(-name "$pat"); fi
}
for p in "${ALWAYS_PRUNE[@]}" "${ALWAYS_EXCLUDE[@]}" ${EXCLUDES[@]+"${EXCLUDES[@]}"}; do add_prune "$p"; done

excluded_name() {  # $1 = basename; true when a name-based pattern excludes it (path patterns are left to find)
  local n=$1 pat
  for pat in "${ALWAYS_PRUNE[@]}" "${ALWAYS_EXCLUDE[@]}" ${EXCLUDES[@]+"${EXCLUDES[@]}"}; do
    [[ $pat == */* ]] && continue
    # shellcheck disable=SC2254  # deliberate: $pat is a basename glob, matched as a pattern
    case $n in $pat) return 0;; esac
  done
  return 1
}

list_files() {  # $1 = dir to walk -> "size\t./path\0" records on stdout (paths always ./-prefixed)
  find "$1" -mindepth 1 \( "${prune_conds[@]}" \) -prune -o -type f -printf '%s\t./%p\0'
}
record_of() {  # $1 = single entry -> record if it is an archivable regular file (path always ./prefixed)
  local p=${1#./} size
  size=$(find "$p" -maxdepth 0 \( "${prune_conds[@]}" \) -prune -o -type f -printf '%s' 2>/dev/null)
  [ -n "$size" ] || return 0
  printf '%s\t./%s\0' "$size" "$p"
}

emit_batches() {  # $1=safe group name, $2=kind, $3=sizes array name, $4=paths array name
  local safe=$1 kind=$2
  local -n _sz=$3 _pa=$4
  local i n=${#_pa[@]} bytes=0 count=0 seq=0 name="" list=""
  [ "$n" -eq 0 ] && return 0
  for i in "${!_pa[@]}"; do
    local s=${_sz[i]}
    if [ "$count" -eq 0 ] || [ $(( bytes + s )) -gt "$MAX_BATCH_BYTES" ]; then
      if [ "$count" -gt 0 ]; then
        printf '%s\t%s\t%s\t%s\n' "$name" "$kind" "$count" "$bytes" >>"$STATE/plan.tsv"
        printf '%s\n' "$kind" >"$STATE/$name.meta"
      fi
      seq=$((seq + 1))
      name="$safe"__"$kind"_$(printf '%03d' "$seq")
      list="$STATE/lists/$name.list"
      : >"$list"
      bytes=0
      count=0
    fi
    printf '%s\0' "${_pa[i]}" >>"$list"
    bytes=$(( bytes + s ))
    count=$(( count + 1 ))
  done
  if [ "$count" -gt 0 ]; then
    printf '%s\t%s\t%s\t%s\n' "$name" "$kind" "$count" "$bytes" >>"$STATE/plan.tsv"
    printf '%s\n' "$kind" >"$STATE/$name.meta"
  fi
}

pack_group() {  # $1 = group label, $2 = record stream (file, NUL records, sorted)
  local group=$1 rec=$2 safe
  safe=$(sanitize "$group")
  local -a p_sz=() p_pa=() g_sz=() g_pa=()
  local size path
  while IFS= read -r -d '' line; do
    size=${line%%$'\t'*}
    path=${line#*$'\t'}
    if is_compressed "$path"; then g_sz+=("$size"); g_pa+=("$path")
    else                        p_sz+=("$size"); p_pa+=("$path")
    fi
  done <"$rec"
  # already-compressed files -> plain .tar; everything else -> .tar.gz (gzip -1)
  emit_batches "$safe" plain g_sz g_pa
  emit_batches "$safe" gz p_sz p_pa
}

# ---------------------------------------------------------------- symlink guard (fail fast, before uploading)
if [ "$DELETE" = 1 ]; then
  bad=0
  while IFS= read -r -d '' l; do
    tgt=$(readlink -f -- "$l" 2>/dev/null || true)
    case $tgt in
      "$SRC"|"$SRC"/*) ;;
      *) echo "  symlink escapes the source: $l -> ${tgt:-<dangling>}" >&2; bad=$(( bad + 1 ));;
    esac
  done < <(find . -mindepth 1 \( "${prune_conds[@]}" \) -prune -o -type l -print0)
  if [ "$bad" -gt 0 ]; then
    echo "REFUSING --delete-local: $bad archivable symlink(s) point outside $SRC; nothing will be deleted." >&2
    DELETE=0
  fi
fi

# Group the source: per-month batches for YYYY-MM* folders (as before), per-directory otherwise.
mapfile -t tops < <(find . -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)
for t in ${tops[@]+"${tops[@]}"}; do
  excluded_name "$t" && continue
  if [ -d "$t" ] && [ ! -L "$t" ]; then
    rec="$STATE/tmp/$(sanitize "$t").rec"
    months=$(find "$t" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null \
             | grep -oE '^[0-9]{4}-[0-9]{2}' | LC_ALL=C sort -u)
    if [ -n "$months" ]; then
      mapfile -t entries < <(find "$t" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)
      for e in "${entries[@]}"; do
        excluded_name "$e" && continue
        m=$(printf '%s' "$e" | grep -oE '^[0-9]{4}-[0-9]{2}' || true)
        if [ -n "$m" ]; then grec="$STATE/tmp/$(sanitize "$t")_$m.rec"
        else                 grec="$STATE/tmp/$(sanitize "$t")_other.rec"
        fi
        if [ -d "$t/$e" ] && [ ! -L "$t/$e" ]; then list_files "$t/$e" >>"$grec"
        else record_of "$t/$e" >>"$grec"
        fi
      done
    else
      list_files "$t" >"$rec"
    fi
  else
    record_of "$t" >>"$STATE/tmp/root.rec"
  fi
done

for rec in "$STATE"/tmp/*.rec; do
  [ -s "$rec" ] || continue
  label=$(basename "$rec" .rec)
  pack_group "$label" <(LC_ALL=C sort -z -t$'\t' -k2,2 <"$rec")
done

LC_ALL=C sort -o "$STATE/plan.tsv" "$STATE/plan.tsv"
dups=$(cut -f1 "$STATE/plan.tsv" | uniq -d)
[ -z "$dups" ] || die "batch name collision: $dups"
total=$(wc -l <"$STATE/plan.tsv")
src_bytes=$(awk -F'\t' '{s+=$4} END{print s+0}' "$STATE/plan.tsv")
[ "$total" -gt 0 ] || die "no archivable files under $SRC (all excluded or empty)"

# ---------------------------------------------------------------- dry run
if [ "$DRY" = 1 ]; then
  plain_bytes=0
  gz_bytes=0
  printf '%-46s %-6s %8s %12s\n' BATCH KIND FILES BYTES
  while IFS=$'\t' read -r name kind nfiles nbytes; do
    ext=tar
    [ "$kind" = gz ] && ext=tar.gz
    printf '%-46s %-6s %8s %12s\n' "$name.$ext" "$kind" "$nfiles" "$nbytes"
    if [ "$kind" = gz ]; then gz_bytes=$(( gz_bytes + nbytes )); else plain_bytes=$(( plain_bytes + nbytes )); fi
  done <"$STATE/plan.tsv"
  est_upload=$(awk -v p="$plain_bytes" -v g="$gz_bytes" -v d="$GZ_ESTIMATE_DIV" 'BEGIN{printf "%.0f", p + g / d}')
  printf '%-46s %-6s %8s %12s\n' TOTAL "-" "$total" "$src_bytes"
  printf 'source %s GiB (plain %s GiB + gzip-input %s GiB)\n' \
    "$(gib "$src_bytes")" "$(gib "$plain_bytes")" "$(gib "$gz_bytes")"
  printf 'estimated upload %s GiB (gzip assumed /%s, and it was not measured) at 8 MiB/s => %s h\n' \
    "$(gib "$est_upload")" "$GZ_ESTIMATE_DIV" \
    "$(awk -v b="$est_upload" -v r="$REMOTE_BYTES_PER_SEC" 'BEGIN{printf "%.2f", b / r / 3600}')"
  printf 'dry run: nothing uploaded; destination would be storagebox:%s\n' "$DST"
  exit 0
fi

# ---------------------------------------------------------------- upload
now() { date +%s; }

stream_batch() {  # $1 = batch name, $2 = kind; returns 0 only when the box sha256 AND size match
  local name=$1 kind=$2 ext=tar
  [ "$kind" = gz ] && ext=tar.gz
  local list="$STATE/lists/$name.list"
  local remote="$DST/$name.$ext"
  local sha_tmp="$STATE/$name.sha.tmp" size_tmp="$STATE/$name.size.tmp"
  local -a rc=()
  local t0 t1 local_sha local_size remote_sha remote_size
  rm -f "$sha_tmp" "$size_tmp"
  t0=$(now)
  if [ "$kind" = gz ]; then
    tar --null -T "$list" -cf - 2>>"$STATE/tar.err" \
      | gzip -1 \
      | tee >(sha256sum | cut -d' ' -f1 >"$sha_tmp") >(wc -c | tr -d ' \n' >"$size_tmp") \
      | rclone rcat "storagebox:$remote" 2>>"$STATE/rclone.err"
  else
    tar --null -T "$list" -cf - 2>>"$STATE/tar.err" \
      | tee >(sha256sum | cut -d' ' -f1 >"$sha_tmp") >(wc -c | tr -d ' \n' >"$size_tmp") \
      | rclone rcat "storagebox:$remote" 2>>"$STATE/rclone.err"
  fi
  rc=( "${PIPESTATUS[@]}" )
  wait   # let the two tee writers (sha256sum, wc -c) finish before reading their files
  t1=$(now)
  local i
  for i in "${rc[@]}"; do [ "$i" = 0 ] || { echo "FAIL_STREAM $name (pipe status ${rc[*]})" >>"$LOG"; return 1; }; done
  local_sha=$(tr -d ' \n' <"$sha_tmp" 2>/dev/null)
  local_size=$(tr -d ' \n' <"$size_tmp" 2>/dev/null)
  case "$local_sha" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) [ ${#local_sha} -eq 64 ] || { echo "FAIL_SHA $name" >>"$LOG"; return 1; };;
    *) echo "FAIL_SHA $name (empty)" >>"$LOG"; return 1;;
  esac
  case $local_size in ''|*[!0-9]*) echo "FAIL_SIZE $name (no byte count)" >>"$LOG"; return 1;; esac
  remote_sha=$(ssh -o BatchMode=yes storagebox sha256sum "$remote" 2>/dev/null | awk 'NR==1{print $1}')
  remote_size=$(ssh -o BatchMode=yes storagebox stat "$remote" 2>/dev/null | awk '/Size:/{print $2; exit}')
  if [ "$remote_sha" != "$local_sha" ]; then
    echo "FAIL_VERIFY $name sha local=$local_sha remote=${remote_sha:-<none>}" >>"$LOG"; return 1
  fi
  if [ "$remote_size" != "$local_size" ]; then
    echo "FAIL_VERIFY $name size local=$local_size remote=${remote_size:-<none>}" >>"$LOG"; return 1
  fi
  printf '%s' "$local_sha" >"$STATE/$name.sha"
  printf '%s' "$local_size" >"$STATE/$name.size"
  touch "$STATE/$name.ok"
  echo "$name $local_size" >>"$STATE/uploaded.txt"
  echo "OK $name $local_sha $local_size $(( t1 - t0 ))s" >>"$LOG"
  printf 'OK   %-40s %8s MiB %6s s %6s MiB/s\n' "$name.$ext" "$(mib "$local_size")" "$(( t1 - t0 ))" \
    "$(awk -v b="$local_size" -v s="$(( t1 - t0 > 0 ? t1 - t0 : 1 ))" 'BEGIN{printf "%.1f", b / s / 1048576}')"
  return 0
}

upload_batch() {  # $1 = batch name; retries once, verifies each attempt
  local name=$1 kind
  kind=$(tr -d ' \n' <"$STATE/$name.meta" 2>/dev/null)
  [ -n "$kind" ] || { echo "FAIL_META $name" >>"$LOG"; return 1; }
  if stream_batch "$name" "$kind"; then return 0; fi
  echo "RETRY $name" >>"$LOG"
  if stream_batch "$name" "$kind"; then return 0; fi
  echo "FAIL $name" >>"$LOG"
  printf 'FAIL %-40s (see %s)\n' "$name" "$LOG" >&2
  return 1
}
export -f upload_batch stream_batch gib mib now
export STATE DST LOG

rclone mkdir "storagebox:$DST" || die "cannot create storagebox:$DST (box unreachable?)"

# Resume: a marker only counts when the file is still on the box with the recorded size.
declare -A remote_size=()
while IFS=';' read -r sz path; do
  case ${sz:-} in ''|-1|*[!0-9]*) continue;; esac
  remote_size[$path]=$sz
done < <(rclone lsf "storagebox:$DST" --files-only --format sp 2>/dev/null)

to_run=()
skipped=0
while IFS=$'\t' read -r name kind nfiles nbytes; do
  ext=tar
  [ "$kind" = gz ] && ext=tar.gz
  f="$name.$ext"
  if [ -f "$STATE/$name.ok" ] && [ -f "$STATE/$name.size" ] \
     && [ "${remote_size[$f]:-}" = "$(tr -d ' \n' <"$STATE/$name.size")" ]; then
    skipped=$(( skipped + 1 ))
  else
    rm -f "$STATE/$name.ok"
    to_run+=("$name")
  fi
done <"$STATE/plan.tsv"

echo "plan: $total batches, $src_bytes source bytes; $skipped already verified, $((${#to_run[@]})) to upload, jobs=$JOBS"
if [ ${#to_run[@]} -gt 0 ]; then
  start=$(now)
  printf '%s\n' "${to_run[@]}" | xargs -P "$JOBS" -n1 -r bash -c 'upload_batch "$0"' || true
  elapsed=$(( $(now) - start ))
else
  elapsed=0
fi

ok=0
while IFS=$'\t' read -r name kind nfiles nbytes; do
  [ -f "$STATE/$name.ok" ] && ok=$(( ok + 1 ))
done <"$STATE/plan.tsv"
echo "verified $ok / $total batches"

if [ "$ok" -ne "$total" ]; then
  echo "INCOMPLETE: re-run the same command to retry failed batches (see $LOG); nothing deleted" >&2
  exit 1
fi

# ---------------------------------------------------------------- manifest / restore / readme
arc_bytes=0
while IFS=$'\t' read -r name kind nfiles nbytes; do
  arc_bytes=$(( arc_bytes + $(tr -d ' \n' <"$STATE/$name.size") ))
done <"$STATE/plan.tsv"

archive_name=$(basename "$SRC")
{
  echo "# $archive_name - archive manifest"; echo
  echo "Archived $(date -u +%F) from \`$SRC\` into \`storagebox:$DST\`. Why: $WHY"
  echo "Local $(gib "$src_bytes") GiB -> $(gib "$arc_bytes") GiB on the box (content-aware tar / tar + gzip -1)."; echo
  echo "## Restore"; echo '```'
  echo "rclone copy storagebox:$DST ./restored && cd ./restored && bash RESTORE.sh ."
  echo '```'
  echo "Every batch below was streamed once (no local temp copy) and verified: the sha256 and the byte"
  echo "size of the stream match \`ssh storagebox sha256sum\` / \`stat\` on the box."; echo
  echo "## Batches"; echo
  echo "| batch | kind | files | source GiB | archive GiB | sha256 |"
  echo "|---|---|---:|---:|---:|---|"
  while IFS=$'\t' read -r name kind nfiles nbytes; do
    ext=tar
    [ "$kind" = gz ] && ext=tar.gz
    echo "| $name.$ext | $kind | $nfiles | $(gib "$nbytes") | $(gib "$(tr -d ' \n' <"$STATE/$name.size")") | $(tr -d ' \n' <"$STATE/$name.sha") |"
  done <"$STATE/plan.tsv"
} >"$STATE/MANIFEST.md"
rclone copyto "$STATE/MANIFEST.md" "storagebox:$DST/MANIFEST.md" || echo "WARN: could not upload MANIFEST.md" >&2

{
  echo '#!/usr/bin/env bash'
  echo "# Restore $archive_name from storagebox:$DST"
  echo '# Usage: bash RESTORE.sh <destination-dir>'
  echo 'set -euo pipefail'
  echo 'DEST=${1:?usage: RESTORE.sh <destination-dir>}'
  echo 'mkdir -p "$DEST"'
  echo 'cd "$DEST"'
  echo "BASE=storagebox:$DST"
  while IFS=$'\t' read -r name kind nfiles nbytes; do
    if [ "$kind" = gz ]; then
      echo "rclone cat \"\$BASE/$name.tar.gz\" | tar -xzf -"
    else
      echo "rclone cat \"\$BASE/$name.tar\" | tar -xf -"
    fi
  done <"$STATE/plan.tsv"
  echo 'echo "restore done in $DEST"'
} >"$STATE/RESTORE.sh"
chmod +x "$STATE/RESTORE.sh"
rclone copyto "$STATE/RESTORE.sh" "storagebox:$DST/RESTORE.sh" || echo "WARN: could not upload RESTORE.sh" >&2

status="$ok/$total batches sha256+size-verified"
if [ "$DELETE" = 1 ]; then
  removed=0
  while IFS= read -r -d '' l; do
    while IFS= read -r -d '' p; do
      case $p in ./*) ;; *) continue;; esac
      if [ -f "$p" ] && [ ! -L "$p" ]; then rm -f -- "$p" && removed=$(( removed + 1 )); fi
    done <"$l"
  done < <(find "$STATE/lists" -name '*.list' -print0 | LC_ALL=C sort -z)
  find "$SRC" -mindepth 1 -depth -type d -empty -delete 2>/dev/null
  if [ -d "$SRC" ]; then
    left_files=$(find "$SRC" -mindepth 1 -type f | wc -l)
    left_bytes=$(du -sb "$SRC" 2>/dev/null | awk '{print $1}')
    left_bytes=${left_bytes:-0}
    if [ "$left_files" -eq 0 ]; then
      rmdir "$SRC" 2>/dev/null && echo "deleted local copy: $SRC" \
        || echo "left in place (could not remove): $SRC"
    else
      echo "left in place: $left_files file(s), $(mib "$left_bytes") MiB of ${archive_name} (excluded/unarchived);"
      echo "  kept these files (--delete-local removes only archived regular files):"
      find "$SRC" -mindepth 1 \( -type f -o -type l \) -printf '    %p\n' | LC_ALL=C sort | head -40
    fi
  else
    echo "deleted local copy: $SRC"
  fi
  status="$status; removed $removed local file(s) $(date -u +%F)"
fi

rclone cat storagebox:alpacatrader/README.md >"$STATE/README.md" 2>/dev/null
if [ -s "$STATE/README.md" ]; then
  grep -qF "| $DEST_REL/ |" "$STATE/README.md" || \
    echo "| $DEST_REL/ | $WHY | $(gib "$arc_bytes") GiB (from $(gib "$src_bytes") GiB) | $(date -u +%F) | $status |" \
      >>"$STATE/README.md"
  rclone copyto "$STATE/README.md" storagebox:alpacatrader/README.md || echo "WARN: could not update README index" >&2
fi

rate=0
up_bytes=$(awk '{s+=$2} END{print s+0}' "$STATE/uploaded.txt" 2>/dev/null)
if [ "${elapsed:-0}" -gt 0 ] && [ -n "$up_bytes" ]; then
  rate=$(awk -v b="$up_bytes" -v t="$elapsed" 'BEGIN{printf "%.1f", b / t / 1048576}')
fi
echo "DONE: $status; source $(gib "$src_bytes") GiB -> archive $(gib "$arc_bytes") GiB; ${elapsed:-0}s wall for $(( ${#to_run[@]} )) uploaded batch(es), ${rate} MiB/s aggregate"
echo "manifest: storagebox:$DST/MANIFEST.md  restore: storagebox:$DST/RESTORE.sh"
