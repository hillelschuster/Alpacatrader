#!/usr/bin/env python3
"""Side-by-side comparison of two BASKET read packets (legacy tape vs SIP substrate).

Reads read_packet.json from two artifact roots (produced by basket_read.py) and emits
READ_COMPARE.md: every listed headline metric with both values and the delta. Nothing is
selected, ranked or thresholded here — missing paths render as '—' and are listed in a note
so absences are visible instead of silent.

Usage:
  .venv/bin/python factory/scripts/basket_compare.py --self-test
  .venv/bin/python factory/scripts/basket_compare.py [--a ROOT] [--b ROOT] [--write]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "factory" / "artifacts" / "basket"
SIP = LEGACY / "sip"


def load(root: Path):
    p = root / "read_packet.json"
    return json.loads(p.read_text()) if p.exists() else None


def g(obj, *path, default=None):
    cur = obj
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def k_ge(hist, k, days):
    if not isinstance(hist, list) or not hist or not days:
        return None
    ks = hist[1:] if k == 1 else hist[k:]
    tot = sum(hist)
    if not tot:
        return None
    return sum(ks) / float(tot)


def fmt(x, pct=False):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x * 100:.2f}%" if pct else f"{x:.4f}"
    return str(x)


def delta(a, b, pct=False):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        d = b - a
        return (f"{d * 100:+.2f}pp" if pct else f"{d:+.4f}")
    return "—"


def metric_rows(d):
    s = (d or {}).get("sections", {})
    rows = []
    add = lambda sec, label, a, b, pct=False: rows.append(
        {"section": sec, "metric": label, "a": a, "b": b, "pct": pct})

    # composition
    for key in ("A_open/570", "A_pm/570", "B/600"):
        add("composition", f"{key}/days", g(s, "composition", key, "days"),
            None)
    # containment
    for key in ("B/600/N3", "B/600/N10", "A_open/570/N3"):
        for f in ("top1_in", "top3_in"):
            add("containment", f"{key}/{f}", g(s, "containment", key, f), None, True)
    # joint tail: k>=1 / k>=2 / all-3 for H=30, H=100 (main set)
    for popT in ("B/600", "B/585"):
        for H in (30, 100):
            for kind in ("touch", "exec"):
                days = g(s, "joint_tail", f"{popT}/main", "days")
                hist = g(s, "joint_tail", f"{popT}/main", f"{kind}_{H}")
                add("joint_tail", f"{popT}/main/{kind}{H}/k>=1", k_ge(hist, 1, days), None, True)
                add("joint_tail", f"{popT}/main/{kind}{H}/k>=2", k_ge(hist, 2, days), None, True)
                add("joint_tail", f"{popT}/main/{kind}{H}/all3", k_ge(hist, 3, days), None, True)
    # frontier
    for popT in ("B/600", "A_open/570"):
        for hl in ("H30/L10", "H100/L10", "H30/L15", "H10/L5"):
            e = g(s, "frontier", f"{popT}/main", hl, default={})
            add("frontier", f"{popT}/main/{hl}/F",
                e.get("F_pess", e.get("F")), None, True)
            add("frontier", f"{popT}/main/{hl}/Q",
                e.get("Q_pess", e.get("Q")), None, True)
    # runner paths
    for st in ("retr_pre_hi", "retr_after_hi", "eod_vs_hi", "time_to_hi"):
        key = f"main/mfe>=30/{st}"
        add("runner_paths", key, g(s, "runner_paths", key, "p50_monthly_median"), None)
    # member MFE/MAE
    for k in ("mfe", "mae"):
        add("member_mfe_mae", f"B/main/{k}/p50",
            g(s, "member_mfe_mae", "B/main", k, "p50"), None)
    # mfe ranks
    for r in ("rank1", "rank2", "rank3"):
        add("mfe_ranks", f"main/{r}/p50", g(s, "mfe_ranks", "main", r, "p50"), None)
    # overnight
    for stratum in ("main/all", "main/mfe>=30"):
        add("overnight", f"{stratum}/p50", g(s, "overnight", stratum, "p50_monthly_median"), None)
    # random control
    for T in ("600",):
        for H in (10, 30, 50):
            add("random_control", f"T{T}/touch{H}/k>=1",
                g(s, "random_control", T, f"touch_{H}_k>=1"), None, True)
    # continuous (T11)
    for popT in ("B/600", "B/720", "A_open/570"):
        for q in ("p50", "p90", "p99"):
            add("continuous", f"{popT}/member_mfe/{q}",
                g(s, "continuous", popT, "member_mfe_q", q), None)
        for q in ("p90", "p99"):
            add("continuous", f"{popT}/day_max/{q}",
                g(s, "continuous", popT, "day_max_q", q), None)
        add("continuous", f"{popT}/ordinary_share",
            g(s, "continuous", popT, "ordinary", "share"), None)
    # buckets
    for key in ("B/main/filled", "B/main/gap_blocked", "B/main/unfilled"):
        add("buckets", key, g(s, "buckets", key), None)
    return rows


def render(a_pack, b_pack, a_name="legacy", b_name="sip"):
    rows = metric_rows(a_pack)
    lines = [f"# BASKET read comparison — {a_name} vs {b_name}", ""]
    cur = None
    missing_a, missing_b = [], []
    for r in rows:
        if r["section"] != cur:
            lines += ["", f"## {r['section']}", "",
                      f"| metric | {a_name} | {b_name} | delta |",
                      "|---|---|---|---|"]
            cur = r["section"]
        # resolve values by dotted lookup into each packet
        a = _lookup(a_pack, r["section"], r["metric"])
        b = _lookup(b_pack, r["section"], r["metric"])
        if a is None:
            missing_a.append(r["metric"])
        if b is None:
            missing_b.append(r["metric"])
        lines.append(f"| {r['metric']} | {fmt(a, r['pct'])} | {fmt(b, r['pct'])} | "
                     f"{delta(a, b, r['pct'])} |")
    if missing_a or missing_b:
        lines += ["", "## notes", "",
                  f"- absent in {a_name}: {len(missing_a)} paths",
                  f"- absent in {b_name}: {len(missing_b)} paths"]
    return "\n".join(lines) + "\n"


def _lookup(pack, section, metric):
    """Resolve a metric path: prefer the packet's own precomputed rows when present."""
    if pack is None:
        return None
    s = pack.get("sections", {})
    # metric keys are of the form family-specific paths; re-derive from the section
    for r in metric_rows(pack):
        if r["section"] == section and r["metric"] == metric:
            return r["a"]
    return None


def selftest():
    fake_a = {"sections": {
        "composition": {"B/600": {"days": 1000, "empty": 0, "lt3": 5}},
        "containment": {"B/600/N3": {"days": 1065, "top1_in": 0.12, "top2_in": 0.09, "top3_in": 0.07}},
        "joint_tail": {"B/600/main": {"days": 100, "touch_30": [70, 25, 5, 0]}},
        "frontier": {"B/600/main": {"H30/L10": {"F_pess": 0.25, "Q_pess": 0.8}}},
        "random_control": {"600": {"touch_30_k>=1": 0.01}},
        "buckets": {"B/main/filled": 10, "B/main/gap_blocked": 2, "B/main/unfilled": 0},
    }}
    fake_b = json.loads(json.dumps(fake_a))
    fake_b["sections"]["containment"]["B/600/N3"]["top1_in"] = 0.15
    fake_b["sections"]["joint_tail"]["B/600/main"]["touch_30"] = [60, 30, 10, 0]
    out = render(fake_a, fake_b)
    assert "top1_in" in out and "12.00%" in out and "15.00%" in out, out[:400]
    assert "touch30/k>=1" in out and "30.00%" in out and "40.00%" in out, out[:800]
    assert "| 25.00% | 25.00% | +0.00pp |" in out, out[:800]
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=str(LEGACY))
    ap.add_argument("--b", default=str(SIP))
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    a_pack, b_pack = load(Path(args.a)), load(Path(args.b))
    if a_pack is None and b_pack is None:
        raise SystemExit("no read_packet.json in either root")
    md = render(a_pack, b_pack, a_name=Path(args.a).name or "a", b_name=Path(args.b).name or "b")
    out = Path(args.out) if args.out else (Path(args.b) / "READ_COMPARE.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"wrote {out} ({len(md.splitlines())} lines)")


if __name__ == "__main__":
    main()
