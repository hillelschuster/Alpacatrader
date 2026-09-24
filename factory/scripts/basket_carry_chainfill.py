"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Carry coverage for whole halt chains, not just the next few sessions.

A held ticket stays open until it sees a *complete* session (the engine force-flats
at that session's last bar).  So for every candidate-tape halt (a day whose tape
ends before ``session_end - 1``), the overlay must cover that ticker until the
first later dev session with a full tape — or the end of its dev block.

Writes /tmp/carry_chain.json with the still-uncertified (day, ticker) pairs.
"""
import json
import sys
from pathlib import Path

import polars as pl

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_sim as sim  # noqa: E402

root = WT / "factory/artifacts/basket/sip/carry_bars"
manifest = json.loads((root / "manifest.json").read_text())
days = sim.dev_days()
sem = sim.session_end_map()
index = {d: i for i, d in enumerate(days)}
gap = sim.DEV_BLOCK_GAP_DAYS

certified: set[tuple[str, str]] = set()
for day, entry in manifest["days"].items():
    for ticker, info in entry.get("tickers", {}).items():
        if info.get("status") in ("bars", "no_bars"):
            certified.add((day, ticker))

last_et: dict[tuple[str, str], int | None] = {}
for day in days:
    frame = pl.read_parquet(sim.BARS_DIR / f"{day}.parquet", columns=["ticker", "et"])
    frame = frame.filter(pl.col("et") <= sem[day])
    grouped = frame.group_by("ticker").agg(pl.col("et").max().alias("last_et"))
    for row in grouped.iter_rows(named=True):
        last_et[(day, row["ticker"])] = int(row["last_et"])

def full_session(day: str, ticker: str) -> bool:
    value = last_et.get((day, ticker))
    return value is not None and value >= sem[day] - 1

# Only tickers that the registered families can actually hold: a name whose tape
# is short *and* that appears in an anatomy snapshot top-10 that day.
selected: dict[str, set[str]] = {}
for day in days:
    rec = sim.load_anatomy(day)
    names: set[str] = set()
    for snap in rec["snapshots"]:
        names.update(nm["ticker"] for nm in snap["names"][:10])
    selected[day] = names

todo: set[tuple[str, str]] = set()
halt_days = 0
for (day, ticker), value in last_et.items():
    if value >= sem[day] - 1:
        continue
    if ticker not in selected.get(day, ()):
        continue
    halt_days += 1
    i = index[day]
    j = i
    while True:
        j += 1
        if j >= len(days):
            break
        if sim._gap_days(days[j - 1], days[j]) > gap:
            break
        if full_session(days[j], ticker):
            break
        if (days[j], ticker) not in certified:
            todo.add((days[j], ticker))
    if j >= len(days) or (j < len(days) and sim._gap_days(days[j - 1], days[j]) > gap):
        continue

todo = sorted(todo)
out = Path("/tmp/carry_chain.json")
out.write_text(json.dumps({"requests": [f"{d}:{t}" for d, t in todo]}, indent=1))
print(json.dumps({"halt_pairs": halt_days, "todo": len(todo),
                  "todo_days": len({d for d, _ in todo}),
                  "todo_tickers": len({t for _, t in todo}), "out": str(out)}))
