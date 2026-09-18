#!/usr/bin/env python3
"""SIP coverage certification aggregator (owner amendment #4).

Reads the per-day coverage files produced by sip_netbars.py
(data/sip/net/coverage/YYYY-MM-DD.json) and reports, WITHOUT any thesis content:

  * symbol-day class incidence: healthy_raw / provider_only / unresolved;
  * unresolved cases explicitly listed (day, symbol, reason) so economically
    interesting days are never silently dropped;
  * provider_only reasons histogram (raw missing / gappy / low trade count);
  * per-month shares for stability.

Coverage classes (documented in sip_netbars.py):
  healthy_raw   = raw trades good enough to be the canonical bar source
  provider_only = raw trades missing/insufficient; Alpaca provider SIP bars used
  unresolved    = neither representation credible -> NOT used, must be reported

Output (committed evidence): factory/artifacts/basket/sip/coverage_summary.json
                             factory/artifacts/basket/sip/COVERAGE.md

Usage:
  .venv/bin/python factory/scripts/sip_coverage.py --self-test
  .venv/bin/python factory/scripts/sip_coverage.py                 # one-line summary
  .venv/bin/python factory/scripts/sip_coverage.py --write         # artifacts
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COV = ROOT / "data" / "sip" / "net" / "coverage"
OUT = ROOT / "factory" / "artifacts" / "basket" / "sip"
CLASSES = ("healthy_raw", "provider_only", "unresolved")


def load_days(cov_dir: Path) -> list:
    out = []
    for f in sorted(glob.glob(str(cov_dir / "*.json"))):
        if f.endswith(".manifest.json"):
            continue
        d = json.load(open(f))
        out.append(d)
    return out


def summarize(days: list) -> dict:
    total = Counter()
    per_month: dict = defaultdict(Counter)
    per_day = []
    unresolved_rows = []
    prov_reasons = Counter()
    unres_reasons = Counter()
    symbols_total = 0
    for d in days:
        day = d["day"]
        classes = d.get("classes", {})
        n = int(d.get("symbols", sum(classes.values())))
        symbols_total += n
        for c in CLASSES:
            total[c] += int(classes.get(c, 0))
        per_month[day[:7]].update({c: int(classes.get(c, 0)) for c in CLASSES})
        per_day.append({"day": day, "symbols": n,
                        **{c: int(classes.get(c, 0)) for c in CLASSES}})
        for sym, rec in (d.get("per_symbol") or {}).items():
            cls = rec.get("cls")
            if cls == "provider_only":
                prov_reasons[str(rec.get("reason", "?"))[:80]] += 1
            elif cls == "unresolved":
                unres_reasons[str(rec.get("reason", "?"))[:80]] += 1
                unresolved_rows.append({"day": day, "symbol": sym,
                                        "n_trades": rec.get("n_trades"),
                                        "derived_bars": rec.get("derived_bars"),
                                        "provider_bars": rec.get("provider_bars"),
                                        "reason": rec.get("reason")})
    days_n = len(days)
    return {
        "days": days_n, "symbol_days": symbols_total,
        "classes": {c: total[c] for c in CLASSES},
        "shares": {c: (round(total[c] / symbols_total, 6) if symbols_total else None)
                   for c in CLASSES},
        "per_month": {m: dict(v) for m, v in sorted(per_month.items())},
        "per_day": per_day,
        "provider_only_reasons": dict(prov_reasons.most_common()),
        "unresolved_reasons": dict(unres_reasons.most_common()),
        "unresolved_days": len({r["day"] for r in unresolved_rows}),
        "unresolved_rows": unresolved_rows,
    }


def render_md(s: dict) -> str:
    lines = ["# SIP coverage certification (net generation)", "",
             f"Days: **{s['days']}** · symbol-days: **{s['symbol_days']}**", "",
             "| class | symbol-days | share |", "|---|---:|---:|"]
    for c in CLASSES:
        lines.append(f"| {c} | {s['classes'][c]} | {s['shares'][c]} |")
    lines += ["", f"Days containing unresolved symbol-days: **{s['unresolved_days']}**", "",
              "## provider_only reasons", ""]
    for r, n in s["provider_only_reasons"].items():
        lines.append(f"- {n}x {r}")
    if not s["provider_only_reasons"]:
        lines.append("- (none)")
    lines += ["", "## unresolved reasons", ""]
    for r, n in s["unresolved_reasons"].items():
        lines.append(f"- {n}x {r}")
    if not s["unresolved_reasons"]:
        lines.append("- (none)")
    lines += ["", "## unresolved symbol-days (explicit, never silently dropped)", "",
              "| day | symbol | n_trades | derived_bars | provider_bars | reason |",
              "|---|---|---:|---:|---:|---|"]
    for r in s["unresolved_rows"][:200]:
        lines.append(f"| {r['day']} | {r['symbol']} | {r['n_trades']} | "
                     f"{r['derived_bars']} | {r['provider_bars']} | {r['reason']} |")
    if not s["unresolved_rows"]:
        lines.append("| — | — | — | — | — | no unresolved symbol-days |")
    lines += ["", "## per-month class counts", "",
              "| month | healthy_raw | provider_only | unresolved |", "|---|---:|---:|---:|"]
    for m, v in s["per_month"].items():
        lines.append(f"| {m} | {v.get('healthy_raw', 0)} | {v.get('provider_only', 0)} | "
                     f"{v.get('unresolved', 0)} |")
    lines += ["", "Measurement-only certificate. No thesis, ruler or parameter content.",
              "Reserved months are not present by construction (dev span only).", ""]
    return "\n".join(lines)


def selftest():
    days = [
        {"day": "2021-02-01", "symbols": 4, "classes": {"healthy_raw": 3, "provider_only": 1,
                                                        "unresolved": 0},
         "per_symbol": {"AAA": {"cls": "healthy_raw", "reason": "ok"},
                        "BBB": {"cls": "provider_only", "reason": "raw missing"}}},
        {"day": "2021-02-02", "symbols": 2, "classes": {"healthy_raw": 1, "provider_only": 0,
                                                        "unresolved": 1},
         "per_symbol": {"CCC": {"cls": "healthy_raw", "reason": "ok"},
                        "DDD": {"cls": "unresolved", "reason": "no provider bars",
                                "n_trades": 0, "derived_bars": 0, "provider_bars": 0}}},
    ]
    s = summarize(days)
    assert s["days"] == 2 and s["symbol_days"] == 6, s
    assert s["classes"] == {"healthy_raw": 4, "provider_only": 1, "unresolved": 1}, s["classes"]
    assert s["unresolved_days"] == 1 and s["unresolved_rows"][0]["symbol"] == "DDD"
    assert s["per_month"]["2021-02"] == {"healthy_raw": 4, "provider_only": 1, "unresolved": 1}
    assert s["provider_only_reasons"] == {"raw missing": 1}
    md = render_md(s)
    assert "unresolved symbol-days" in md and "DDD" in md
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(COV))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    days = load_days(Path(args.dir))
    if not days:
        raise SystemExit(f"no coverage files under {args.dir}")
    s = summarize(days)
    print(f"coverage: days={s['days']} symbol_days={s['symbol_days']} "
          f"classes={s['classes']} unresolved_days={s['unresolved_days']}")
    if args.write:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "coverage_summary.json", "w") as fh:
            json.dump(s, fh, indent=1, default=str)
        with open(out / "COVERAGE.md", "w") as fh:
            fh.write(render_md(s))
        print(f"wrote {out/'coverage_summary.json'} + COVERAGE.md")


if __name__ == "__main__":
    main()
