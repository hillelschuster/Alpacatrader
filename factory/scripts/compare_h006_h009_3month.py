"""3-month honest comparison for H006 + faithful H009 — May / June / July 2025, after costs.

Faithful H009 subset definition (validated: reproduces June report exactly):
    signal_type == "trap" AND close > hod_before   (reclaim closes back above prior running RTH HOD)
Cost convention: roundtrip = cost_bps x 2 / 10000. Primary 20bps RT, sensitivity 40bps RT.
July run stages June+July clean (July 1 needs June 30 prior-session close); July-only slice = et_date >= 2025-07-01.
"""
import json
from pathlib import Path
import polars as pl

RT = {"20bps": 0.0020, "40bps": 0.0040}
MONTHS = ["2025-05", "2025-06", "2025-07"]


def h006_row(base: Path, month: str, rt: float) -> dict:
    s = json.load(open(base / f"h006_results_{month}" / ("cost40bps/h006_summary.json" if rt == 0.004 else "h006_summary.json")))
    sm, t12 = s["signal_metrics"], s["by_bucket"]["12%+"]

    def g(d, h):
        e = d.get(f"h{h}")
        return (e["n"], None if e.get("exp_short_net") is None else round(e["exp_short_net"] * 100, 3))
    return {"n15": g(sm, 15)[0], "e15": g(sm, 15)[1], "e30": g(sm, 30)[1], "e60": g(sm, 60)[1],
            "tail_n": t12["h15"]["n"], "tail_e15": round(t12["h15"]["exp_short_net"] * 100, 3),
            "tail_avg15": round(t12["h15"]["avg"] * 100, 3)}


def h009_month(base: Path, month: str, rt: float, july_only: bool = False) -> dict:
    fn = "h009_results_c20.parquet" if rt == 0.004 else "h009_results.parquet"
    p = base / f"h009_results_{month}" / fn
    if not p.exists():
        return {}
    df = pl.read_parquet(p)
    if july_only:
        df = df.filter(pl.col("et_date") >= pl.date(2025, 7, 1))
    c = df.filter(pl.col("signal_type") == "clean")
    out = {}
    # two faithful variants (prior work mixed them across months: May ledger=strict, June report=loose)
    variants = {
        "loose": (pl.col("signal_type") == "trap") & (pl.col("close") > pl.col("hod_before")),
        "strict": (pl.col("signal_type") == "trap") & (pl.col("dip_type") == "hod") & (pl.col("close") > pl.col("hod_before")),
    }
    for vname, flt in variants.items():
        f = df.filter(flt)
        out[vname] = {"n": f.height}
        for h in (15, 30, 60):
            m = f.select(pl.col(f"fwd_ret_{h}m").drop_nulls()).to_series()
            mc = c.select(pl.col(f"fwd_ret_{h}m").drop_nulls()).to_series()
            if m.len() == 0:
                continue
            out[vname][f"n{h}"] = m.len()
            out[vname][f"e{h}"] = round((m.mean() - rt) * 100, 3)
            out[vname][f"edge{h}"] = round((m.mean() - mc.mean()) * 10000, 1) if mc.len() else None
            out[vname][f"wr{h}"] = round((m > 0).sum() / m.len(), 3)
    return out


def main():
    base = Path("factory/artifacts")
    lines = ["# May / June / July 2025 — H006 + faithful H009 after-cost comparison", "",
             "Faithful H009 = trap reclaim closing above prior running RTH HOD (validated vs June report: n=34,916, edges +20.0/+28.6bps @30/60m).",
             "All numbers net of roundtrip cost; exp in %, edge vs clean-control in bps.", ""]

    lines += ["## H006 — short fade when vwap_dist > 8% (exp_short_net %)", "",
              "| month | RT | n(15m) | 15m | 30m | 60m | 12%+ n | 12%+ exp 15m | 12%+ avg 15m |", "|---|---|---|---|---|---|---|---|---|"]
    for m in MONTHS:
        for tag, rt in RT.items():
            try:
                r = h006_row(base, m, rt)
                lines.append(f"| {m} | {tag} | {r['n15']} | {r['e15']} | {r['e30']} | {r['e60']} | {r['tail_n']} | {r['tail_e15']} | {r['tail_avg15']} |")
            except FileNotFoundError:
                lines.append(f"| {m} | {tag} | MISSING | | | | | | |")

    lines += ["", "## H009 faithful subsets — long reclaim above prior HOD (exp_net %, edge vs clean bps)", "",
              "loose = trap & close>prior_HOD (June-report convention); strict = trap & HOD-dip & close>prior_HOD (May-ledger convention).", "",
              "| month | slice | RT | variant | n | 15m e | 15m edge | 30m e | 30m edge | 60m e | 60m edge | wr60 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in MONTHS:
        slices = [False, True] if m == "2025-07" else [False]
        for jo in slices:
            for tag, rt in RT.items():
                r = h009_month(base, m, rt, july_only=jo)
                if not r:
                    lines.append(f"| {m} | {'July-only' if jo else 'full'} | {tag} | - | MISSING | | | | | | | |")
                    continue
                sl = "July-only" if jo else "full"
                for vname in ("loose", "strict"):
                    v = r[vname]
                    lines.append(f"| {m} | {sl} | {tag} | {vname} | {v.get('n')} | {v.get('e15')} | {v.get('edge15')} | {v.get('e30')} | {v.get('edge30')} | {v.get('e60')} | {v.get('edge60')} | {v.get('wr60')} |")

    txt = "\n".join(lines) + "\n"
    print(txt)
    (base / "replication_2025-05_06_07.md").write_text(txt)
    print("wrote factory/artifacts/replication_2025-05_06_07.md")


if __name__ == "__main__":
    main()
