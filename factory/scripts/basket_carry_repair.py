"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Fetch chain coverage for specific (day, ticker) pairs and merge it in.

Usage: carry_repair.py DAY:TICKER [DAY:TICKER ...]
For each pair, the ticker is requested from that day until the first later dev
session with a complete candidate tape (the engine force-flats there) or the end
of its dev block, skipping already-certified pairs.  Fetches go to a scratch
part root and are union-merged into the canonical carry substrate.
"""
import json
import subprocess
import sys
from pathlib import Path

import polars as pl

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_sim as sim  # noqa: E402

PY = "/home/hillel/projects/Alpacatrader/.venv/bin/python"
ENV = "/home/hillel/projects/Alpacatrader/.env"
root = WT / "factory/artifacts/basket/sip/carry_bars"

manifest = json.loads((root / "manifest.json").read_text())
certified = set()
for day, entry in manifest["days"].items():
    for ticker, info in entry.get("tickers", {}).items():
        if info.get("status") in ("bars", "no_bars"):
            certified.add((day, ticker))

days = sim.dev_days()
sem = sim.session_end_map()
index = {d: i for i, d in enumerate(days)}
gap = sim.DEV_BLOCK_GAP_DAYS

last_et_cache: dict[str, dict[str, int]] = {}


def last_et(day: str, ticker: str):
    if day not in last_et_cache:
        frame = pl.read_parquet(sim.BARS_DIR / f"{day}.parquet", columns=["ticker", "et"])
        frame = frame.filter(pl.col("et") <= sem[day])
        last_et_cache[day] = {
            row["ticker"]: int(row["last_et"])
            for row in frame.group_by("ticker").agg(pl.col("et").max().alias("last_et"))
            .iter_rows(named=True)
        }
    return last_et_cache[day].get(ticker)


requests: set[tuple[str, str]] = set()
for text in sys.argv[1:]:
    day, _, ticker = text.partition(":")
    i = index[day]
    first = True
    while i < len(days):
        if not first and sim._gap_days(days[i - 1], days[i]) > gap:
            break
        if not first:
            value = last_et(days[i], ticker)
            if value is not None and value >= sem[days[i]] - 1:
                break                               # full session: engine force-flats
        if (days[i], ticker) not in certified:
            requests.add((days[i], ticker))
        first = False
        i += 1

if not requests:
    print(json.dumps({"added": 0, "note": "nothing missing"}))
    raise SystemExit(0)

req_path = Path("/tmp/carry_repair_requests.json")
req_path.write_text(json.dumps({"requests": [f"{d}:{t}" for d, t in sorted(requests)]}, indent=1))
part_root = Path("/tmp/carry_repair_part")
proc = subprocess.run(
    [PY, str(WT / "factory/scripts/basket_carry_bars.py"), "--env-file", ENV,
     "--requests", str(req_path), "--out-root", str(part_root)],
    cwd=str(WT), capture_output=True, text=True)
if proc.returncode != 0:
    print(json.dumps({"error": proc.stdout[-500:] + proc.stderr[-800:]}))
    raise SystemExit(1)
merge = subprocess.run([PY, "/tmp/carry_merge.py", str(root), str(part_root)],
                       capture_output=True, text=True)
print(json.dumps({"requested": len(requests),
                  "fetch_tail": proc.stdout.strip().splitlines()[-1:] if proc.stdout else [],
                  "merge": merge.stdout.strip() if merge.returncode == 0 else merge.stderr[-300:]}))
