#!/usr/bin/env python3
"""BASKET-01 sealed 2024 / January-2025 acquisition certification (mechanical only).

Verifies the sealed SIP universe tables (RTH + premarket) for 2024-01..2025-01:
parquet present; manifest status ok; sha256(parquet) == index/manifest sha256
(every file, streamed); row count == manifest rows; parquet schema == manifest
schema; no *.tmp leftovers; index entries == files on disk for those months.

MECHANICAL ONLY. No BASKET ranking, anatomy, read packet or outcome computation is
performed on these days. They are sealed outside the frozen dev span and may not be
inspected until the owner opens them.

Outputs: <root>/SEALED_2024_CERT.json + SEALED_2024_CERT.md

Usage:
  .venv/bin/python factory/scripts/sip_sealed_certify.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/sip_sealed_certify.py --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
UNI = ROOT / "data" / "sip" / "universe"
MONTHS = [f"2024-{m:02d}" for m in range(1, 13)] + ["2025-01"]
STATEMENT = ("Mechanical fetch + manifests + index only. No BASKET rankings, anatomy, "
             "read packet, or outcome computation has been performed on these days. "
             "These months are sealed outside the frozen dev span.")
DEV_ROOT = ROOT / "factory" / "artifacts" / "basket" / "sip"


def art_root(arg=None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("BASKET_ART_ROOT")
    return Path(env) if env else DEV_ROOT


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_index(mode: str) -> dict:
    p = UNI / f"index_{mode}.jsonl"
    out = {}
    if not p.exists():
        return out
    for line in open(p):
        line = line.strip()
        if line:
            rec = json.loads(line)
            out[rec["day"]] = rec
    return out


def check_mode(mode: str) -> dict:
    d = UNI / mode
    idx = load_index(mode)
    rows, anomalies = [], []
    per_month: dict = {}
    for day in sorted(idx):
        if day[:7] not in MONTHS:
            continue
        rec = idx[day]
        pq, mf = d / f"{day}.parquet", d / f"{day}.manifest.json"
        row = {"day": day, "status": rec.get("status"), "pq": pq.exists(), "mf": mf.exists()}
        if not pq.exists():
            anomalies.append(f"{mode} {day}: parquet missing")
            rows.append(row)
            continue
        if not mf.exists():
            anomalies.append(f"{mode} {day}: manifest missing")
            rows.append(row)
            continue
        man = json.load(open(mf))
        row["manifest_status"] = man.get("status")
        if man.get("status") != "ok":
            anomalies.append(f"{mode} {day}: manifest status {man.get('status')}")
        got = sha256(pq)
        want = man.get("sha256") or rec.get("sha256")
        row["sha_ok"] = (got == want) if want else None
        if want is None:
            anomalies.append(f"{mode} {day}: no sha256 in manifest/index")
        elif got != want:
            anomalies.append(f"{mode} {day}: sha256 mismatch")
        n = pl.read_parquet(pq).height
        row["rows"] = n
        exp_rows = man.get("rows") or rec.get("rows")
        row["rows_ok"] = (n == exp_rows)
        if not row["rows_ok"]:
            anomalies.append(f"{mode} {day}: rows {n} != manifest {exp_rows}")
        schema = {k: str(v) for k, v in (man.get("schema") or {}).items()}
        if schema:
            actual = {k: str(v) for k, v in pl.read_parquet(pq, n_rows=0).schema.items()}
            row["schema_ok"] = (actual == schema)
            if not row["schema_ok"]:
                anomalies.append(f"{mode} {day}: schema differs")
        else:
            row["schema_ok"] = None
        rows.append(row)
        m = per_month.setdefault(day[:7], {"days": 0, "sha_ok": 0, "rows_ok": 0, "schema_ok": 0})
        m["days"] += 1
        m["sha_ok"] += int(bool(row["sha_ok"]))
        m["rows_ok"] += int(bool(row["rows_ok"]))
        m["schema_ok"] += int(bool(row["schema_ok"]))
    on_disk = {p.name[:10] for p in d.glob("*.parquet") if p.name[:10] in
               {x for x in idx if x[:7] in MONTHS}}
    in_idx = {x for x in idx if x[:7] in MONTHS}
    extra = sorted(on_disk - in_idx)
    missing = sorted(in_idx - on_disk)
    if extra:
        anomalies.append(f"{mode}: on disk but not in index: {extra[:5]}")
    if missing:
        anomalies.append(f"{mode}: in index but not on disk: {missing[:5]}")
    tmp = sorted(p.name for p in d.glob("*.tmp"))
    if tmp:
        anomalies.append(f"{mode}: tmp leftovers {tmp[:5]}")
    return {"mode": mode, "per_month": per_month, "rows": rows, "anomalies": anomalies,
            "days_verified": len(rows), "index_days": len(in_idx)}


def run(root: Path, write: bool):
    out = {"statement": STATEMENT, "months": MONTHS, "feed": "sip",
           "provider": "alpaca", "art_root": str(root),
           "checks": ["parquet present", "manifest status ok", "sha256 match (all files)",
                      "rows == manifest", "schema == manifest", "index == disk", "no *.tmp"],
           "modes": {}, "anomalies": []}
    for mode in ("rth", "premarket"):
        res = check_mode(mode)
        out["modes"][mode] = res
        out["anomalies"] += res["anomalies"]
    ok = not out["anomalies"]
    out["verdict"] = "CERTIFIED" if ok else "ANOMALIES"
    print(f"sealed cert: rth days={out['modes']['rth']['days_verified']} "
          f"premarket days={out['modes']['premarket']['days_verified']} "
          f"anomalies={len(out['anomalies'])} -> {out['verdict']}")
    for m in ("rth", "premarket"):
        for mon, v in sorted(out["modes"][m]["per_month"].items()):
            print(f"  {m} {mon}: {v['days']} days sha_ok={v['sha_ok']} "
                  f"rows_ok={v['rows_ok']} schema_ok={v['schema_ok']}")
    if write:
        root.mkdir(parents=True, exist_ok=True)
        with open(root / "SEALED_2024_CERT.json", "w") as fh:
            json.dump(out, fh, indent=1, default=str)
        lines = [f"# Sealed SIP acquisition certification — {out['verdict']}", "",
                 STATEMENT, "", f"- feed: sip / provider alpaca / months: {MONTHS[0]}..{MONTHS[-1]}",
                 f"- days verified: rth={out['modes']['rth']['days_verified']}, "
                 f"premarket={out['modes']['premarket']['days_verified']}",
                 f"- anomalies: {len(out['anomalies'])}", ""]
        for m in ("rth", "premarket"):
            lines.append(f"## {m}")
            lines.append("| month | days | sha_ok | rows_ok | schema_ok |")
            lines.append("|---|---|---|---|---|")
            for mon, v in sorted(out["modes"][m]["per_month"].items()):
                lines.append(f"| {mon} | {v['days']} | {v['sha_ok']} | {v['rows_ok']} | {v['schema_ok']} |")
            lines.append("")
        if out["anomalies"]:
            lines.append("## anomalies")
            lines += [f"- {a}" for a in out["anomalies"]]
        (root / "SEALED_2024_CERT.md").write_text("\n".join(lines) + "\n")
        print(f"[wrote] {root/'SEALED_2024_CERT.json'} + .md")
    return out


def selftest():
    d = Path(tempfile.mkdtemp())
    p = d / "x.parquet"
    pl.DataFrame({"a": [1, 2, 3]}).write_parquet(p)
    want = sha256(p)
    assert want == sha256(p) and len(want) == 64
    assert pl.read_parquet(p).height == 3
    with open(p, "ab") as fh:
        fh.write(b"x")
    assert sha256(p) != want, "modified file must change sha"
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    run(art_root(args.root), args.write)


if __name__ == "__main__":
    main()
