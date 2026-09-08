#!/usr/bin/env python3
"""H12 hot-window pattern mining. Pre-registered: researches/PRE-REG-H12.md.

cache MONTHS... : build per-day sample cache (causal top-5 leaderboard,
                  raw 1-min features, first-touch targets), resumable.
eval --dev M ... [--collision M ...] : LOMO dev eval + optional collision,
                  gates per pre-reg, artifact JSON.

Universe/eligibility/lag semantics imported from replay_watchlist (harness
contract). Run from repo root via uv (see factory/STATE.md).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_watchlist import et_minute, snapshot  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache_h12"
ART = ROOT / "factory" / "artifacts"

SNAP_TS = list(range(585, 631, 5))   # 9:45..10:30 ET, every 5 min (pre-reg)
TOPK = 5
WINDOW = 30                          # forward bars for targets
FRICTION = 100.0                     # bps (current doctrine)
DEV_AUC_GATE, DEV_NET_GATE = 0.55, 30.0
COL_AUC_GATE, COL_NET_GATE, COL_MONTH_POS = 0.53, 30.0, 2

FEATURES = ["n", "t_open", "log_pc", "gain_open", "gain_pc", "gap", "runup",
            "pb", "dvwap", "ret1", "ret5", "ret15", "ret30", "rng10",
            "upfrac10", "consec_up", "volr_last", "volr10", "log_dv",
            "last_rng", "mfe_so", "mae_so"]


def month_path(month: str) -> Path:
    # /mnt/c (9P) parquet reads are ~10x slower than ext4: stage months to
    # H12_STAGE (default /tmp/opencode/h12stage) before processing.
    import os
    stage = Path(os.environ.get("H12_STAGE", "/tmp/opencode/h12stage"))
    stage.mkdir(parents=True, exist_ok=True)
    sp = stage / f"clean_ohlcv_{month}.parquet"
    if sp.exists():
        return sp
    base = ROOT / "data" / "backfill" if month >= "2026-03" else ROOT / "data"
    src = base / f"clean_ohlcv_{month}.parquet"
    if src.exists():
        import shutil
        shutil.copy2(src, sp)
        return sp
    return src  # missing month: caller checks .exists() and falls back


def prev_month(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:])
    return f"{y - (mm == 1):04d}-{(mm - 2) % 12 + 1:02d}"


_maps: dict = {}


def close_map(month: str) -> dict:
    """{utc_date: {ticker: last session close}} from one month file."""
    if month in _maps:
        return _maps[month]
    df = pd.read_parquet(month_path(month), columns=["timestamp", "ticker", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["et"] = et_minute(df["timestamp"])
    df = df[(df["et"] >= 570) & (df["et"] < 960)].sort_values("timestamp")
    df["date"] = df["timestamp"].dt.floor("D")
    last = df.groupby(["date", "ticker"], sort=False)["close"].last().reset_index()
    out = {}
    for d, g in last.groupby("date"):
        out[d] = dict(zip(g["ticker"], g["close"]))
    _maps[month] = out
    if len(_maps) > 3:
        _maps.pop(next(iter(_maps)))
    return out


def prev_close_dict(day, month: str) -> dict:
    """Closes from latest trading date strictly before day (harness semantics)."""
    day = pd.Timestamp(day)
    cand = {}
    for m in {month, prev_month(month)}:
        if month_path(m).exists():
            for d, mp in close_map(m).items():
                if d < day:
                    cand[d] = mp
    return cand[max(cand)] if cand else {}


def features(g: pd.DataFrame, t: int, srow) -> dict | None:
    """Raw 1-min features from session bars with et <= t-1 only."""
    gg = g[g["et"] <= t - 1]
    if len(gg) < 10:
        return None
    o = gg["open"].to_numpy(float); h = gg["high"].to_numpy(float)
    l = gg["low"].to_numpy(float);  c = gg["close"].to_numpy(float)
    v = gg["volume"].to_numpy(float)
    n = len(c); pc = c[-1]; po = o[0]
    with np.errstate(all="ignore"):
        up = (c > o).astype(float)
        cu = 0
        for x in up[::-1]:
            if x > 0:
                cu += 1
            else:
                break
        vmean = v.mean()
        ret = lambda k: float(pc / c[-1 - k] - 1) if n > k else np.nan
        f = {
            "n": n, "t_open": t - 570, "log_pc": float(np.log(pc)),
            "gain_open": float(pc / po - 1),
            "gain_pc": float(srow["gain_pc"]), "gap": float(srow["gap_pct"]),
            "runup": float(h.max() / po - 1), "pb": float(pc / h.max() - 1),
            "dvwap": float(pc / srow["vwap"] - 1),
            "ret1": ret(1), "ret5": ret(5), "ret15": ret(15), "ret30": ret(30),
            "rng10": float((h[-10:] / l[-10:] - 1).mean()),
            "upfrac10": float(up[-10:].mean()), "consec_up": cu,
            "volr_last": float(v[-1] / vmean) if vmean > 0 else np.nan,
            "volr10": float(v[-10:].mean() / vmean) if vmean > 0 else np.nan,
            "log_dv": float(np.log(srow["cdv"])),
            "last_rng": float(h[-1] / l[-1] - 1),
            "mfe_so": float(h.max() / po - 1), "mae_so": float(l.min() / po - 1),
        }
    return f


def target(g: pd.DataFrame, t: int) -> dict | None:
    """T1 first-touch +4% before -2% within 30 bars (same-bar both -> 0),
    T2 fwd ret to last window close, entry = open of bar et==t."""
    w = g[(g["et"] >= t) & (g["et"] <= t + WINDOW)]
    if len(w) < 10 or not (w["et"] == t).any():
        return None
    o = w["open"].to_numpy(float); h = w["high"].to_numpy(float)
    l = w["low"].to_numpy(float);  c = w["close"].to_numpy(float)
    entry = o[0]
    if entry <= 0:
        return None
    iu = np.where(h >= entry * 1.04)[0]
    idn = np.where(l <= entry * 0.98)[0]
    t1 = 1 if (len(iu) and (not len(idn) or iu[0] < idn[0])) else 0
    return {"T1": t1, "T2": round(float(c[-1] / entry - 1) * 10000, 1),
            "mfe30": round(float(h.max() / entry - 1) * 10000, 1),
            "mae30": round(float(l.min() / entry - 1) * 10000, 1)}


def day_samples(sess: pd.DataFrame, day, prev: dict, month: str):
    if len(sess) == 0:
        return None
    # hot-window truncation: features use et<=629, targets et<=t+30<=660
    sess = sess[(sess["et"] >= 570) & (sess["et"] <= 660)]
    groups = {tk: g.sort_values("timestamp")
              for tk, g in sess.groupby("ticker", sort=False)}
    rows = []
    for t in SNAP_TS:
        snap = snapshot(sess, t, prev=prev)
        if len(snap) == 0 or "prev_close" not in snap.columns:
            continue
        e = snap[snap["prev_close"].notna() & ~snap["split_suspect"]].copy()
        if len(e) == 0:
            continue
        e["gain_pc"] = e["pc"] / e["prev_close"] - 1
        e = e.sort_values("gain_pc", ascending=False).head(TOPK)
        for tk, srow in e.iterrows():
            g = groups.get(tk)
            if g is None:
                continue
            f = features(g, t, srow)
            tgt = target(g, t) if f is not None else None
            if f is None or tgt is None:
                continue
            rows.append({"date": str(day)[:10], "month": month, "t": t,
                         "ticker": tk, "gain_open": float(srow["gain"]),
                         **f, **tgt})
    return pd.DataFrame(rows) if rows else None


def cmd_cache(months):
    # ET via tz_convert: identical values to harness et_minute but avoids the
    # O(rows) python-date object array that OOMs on ~500MB months.
    for month in months:
        df = pd.read_parquet(month_path(month))
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        et = df["timestamp"].dt.tz_convert("America/New_York")
        df["et"] = et.dt.hour * 60 + et.dt.minute
        del et
        df = df[(df["et"] >= 570) & (df["et"] <= 660)]  # hot window bounds: features <=629, targets <=660
        df["date"] = df["timestamp"].dt.floor("D")
        for day, s in df.groupby("date", sort=True):
            key = CACHE / f"h12_{str(day)[:10]}.parquet"
            if key.exists():
                continue
            prev = prev_close_dict(day, month)
            s = day_samples(s, day, prev, month)
            (s if s is not None else pd.DataFrame()).to_parquet(key)
            print(f"{month} {str(day)[:10]}: {0 if s is None else len(s)} samples",
                  flush=True)


# ── eval ───────────────────────────────────────────────────────────────────
def load_cache(months):
    dfs = []
    for p in sorted(CACHE.glob("h12_*.parquet")):
        d = pd.read_parquet(p)
        if len(d) and d["month"].iloc[0] in months:
            dfs.append(d)
    if not dfs:
        sys.exit(f"no cached samples for {months}")
    return pd.concat(dfs, ignore_index=True)


def fit_predict(train, test):
    import lightgbm as lgb
    params = dict(objective="binary", num_leaves=31, learning_rate=0.05,
                  n_estimators=300, min_child_samples=50, feature_fraction=0.9,
                  bagging_fraction=0.8, bagging_freq=1, seed=7, verbose=-1,
                  force_col_wise=True)
    m = lgb.LGBMClassifier(**params)
    m.fit(train[FEATURES], train["T1"])
    return m, m.predict_proba(test[FEATURES])[:, 1]


def with_deciles(o):
    o = o.sort_values("score").copy()
    o["dec"] = np.minimum((o["score"].rank(pct=True) * 10).astype(int), 9)
    return o


def auc(o):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(o["T1"], o["score"]))


def decile_table(o):
    g = o.groupby("dec").agg(n=("T2", "size"), t1_rate=("T1", "mean"),
                             mean_t2=("T2", "mean"))
    g["net_bps"] = g["mean_t2"] - FRICTION
    return g


def topdec_month(o):
    top = o[o["dec"] == 9]
    g = top.groupby("month").agg(n=("T2", "size"), mean_t2=("T2", "mean"))
    g["net_bps"] = g["mean_t2"] - FRICTION
    return g


def dedup_net(o):
    """Per ticker-day earliest top-decile snapshot, mean net (diagnostic)."""
    top = o[o["dec"] == 9].sort_values("t")
    d = top.drop_duplicates(subset=["date", "ticker"])
    return round(float(d["T2"].mean() - FRICTION), 1), len(d)


def cmd_eval(dev, collision):
    devdf = load_cache(dev)
    base = {"n": int(len(devdf)), "t1_rate": round(float(devdf["T1"].mean()), 4),
            "mean_t2": round(float(devdf["T2"].mean()), 1)}
    print("dev base:", base)

    # LOMO over dev months
    parts = []
    importances = None
    for m in dev:
        tr, te = devdf[devdf["month"] != m], devdf[devdf["month"] == m]
        if len(tr) == 0 or len(te) == 0:
            continue
        _, p = fit_predict(tr, te)
        o = te[["month", "date", "t", "ticker", "T1", "T2"]].copy()
        o["score"] = p
        parts.append(o)
    lomo = with_deciles(pd.concat(parts, ignore_index=True))
    d_auc = auc(lomo)
    d_tab = decile_table(lomo)
    d_tm = topdec_month(lomo)
    d_dedup = dedup_net(lomo)
    print(f"\nLOMO pooled AUC: {d_auc:.4f}\n")
    print(d_tab.round(2).to_string())
    print("\ntop-decile by month:")
    print(d_tm.round(1).to_string())
    print(f"\ntop-decile deduped net: {d_dedup[0]} bps (n={d_dedup[1]})")

    dev_gate = bool(d_auc >= DEV_AUC_GATE
                    and d_tab.loc[9, "net_bps"] >= DEV_NET_GATE)

    full_model, _ = fit_predict(devdf, devdf)
    importances = dict(sorted(zip(FEATURES,
                                 full_model.feature_importances_.round(1).tolist()),
                              key=lambda kv: -kv[1]))

    col = None
    if collision:
        coldf = load_cache(collision)
        _, p = fit_predict(devdf, coldf)
        co = coldf[["month", "date", "t", "ticker", "T1", "T2"]].copy()
        co["score"] = p
        col_o = with_deciles(co)
        c_auc = auc(col_o)
        c_tab = decile_table(col_o)
        c_tm = topdec_month(col_o)
        print(f"\ncollision pooled AUC: {c_auc:.4f}")
        print(c_tab.round(2).to_string())
        print("\ncollision top-decile by month:")
        print(c_tm.round(1).to_string())
        col_gate = bool(c_auc >= COL_AUC_GATE
                        and c_tab.loc[9, "net_bps"] >= COL_NET_GATE
                        and (c_tm["net_bps"] > 0).sum() >= COL_MONTH_POS)
        col = {"auc": round(c_auc, 4), "base_t1": round(float(coldf["T1"].mean()), 4),
               "base_t2": round(float(coldf["T2"].mean()), 1),
               "deciles": c_tab.round(2).to_dict("index"),
               "topdec_month": c_tm.round(1).to_dict("index"),
               "dedup_net_bps": dedup_net(col_o)[0], "gate_pass": col_gate}

    art = {
        "hypothesis": "H12 hot-window pattern mining (PRE-REG-H12.md)",
        "friction_bps": FRICTION, "gates": {"dev_auc": DEV_AUC_GATE,
        "dev_net": DEV_NET_GATE, "col_auc": COL_AUC_GATE, "col_net": COL_NET_GATE},
        "dev": {"months": dev, "base": base, "lomo_auc": round(d_auc, 4),
                "deciles": d_tab.round(2).to_dict("index"),
                "topdec_month": d_tm.round(1).to_dict("index"),
                "dedup_net_bps": d_dedup[0], "gate_pass": dev_gate},
        "collision": col, "importances": importances,
    }
    out = ART / "hot_window_ml_H12_dev.json"
    out.write_text(json.dumps(art, indent=1))
    print(f"\ndev gate: {'PASS' if dev_gate else 'FAIL'} -> {out}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["cache", "eval"])
    p.add_argument("months", nargs="*")
    p.add_argument("--dev", nargs="*")
    p.add_argument("--collision", nargs="*")
    a = p.parse_args()
    if a.cmd == "cache":
        cmd_cache(a.months)
    else:
        if not a.dev:
            sys.exit("eval needs --dev MONTHS")
        cmd_eval(a.dev, a.collision)


if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    main()
