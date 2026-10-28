#!/usr/bin/env bash
# box - work with Alpacatrader data on the Hetzner Storage Box (pull/verify/link/evict/push).
# Thin launcher: the implementation is factory/scripts/box.py (python3, stdlib only).
#   box --help | box ls [dest] | box pull <dest> [--match GLOB] | box set <name>
#   box status | box evict <dest|--lru N> | box push <dir> <dest> | box link
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$here/box.py" "$@"
