"""P2 money-map pooled measurement (PRE-REG-P2, frozen 2026-09-08).

Single per-month pass producing BOTH row types (assembled from the proven
gate_decompose.py + halt_temporal.py + capture_variants.py code paths;
definitions identical to those scripts):
  gate rows: gate cohort (>=15% & <30% by 10:30 + >=1 halt-gap, $1-50, >=120 bars,
             >=30 pre bars) with 10:30-entry fwd/mfe/mae, hourly cumulative path
             from 10:30, 13:30 ref + 13:30->close, green flag.
  halt rows: every >=5min hole in names up >=10% at hole time, with state
             (gain_cum, g5/g10, prior_holes, hour, hole_len) + anchors
             (pre_close, ro_open, ro_close, next_open, c3/c10/c30, v5/vro).
Incremental per-day JSONL, resumable. Read-only market data.
Covers M1-M6 of PRE-REG-P2. Dev pool: 2025-03..12 + 2026-01..03 (13 months).
NEVER run on 2026-04..08 (sealed holdout).
"""
import sys, json
from pathlib import Path
sys.path.insert(0, 'factory')
from scripts.replay_watchlist import load_day
import pandas as pd, numpy as np

HOURS = [(630, 660), (660, 720), (720, 780), (780, 840), (840, 900), (900, 960)]


def scan_month(month, out_gate, out_halt):
    base = Path('data/backfill') if month >= '2026-03' else Path('data')
    probe = pd.read_parquet(base / f'clean_ohlcv_{month}.parquet', columns=['timestamp'])
    probe['timestamp'] = pd.to_datetime(probe['timestamp'], utc=True)
    dates = sorted(probe['timestamp'].dt.floor('D').unique())
    done = set()
    for p in (out_gate, out_halt):  # union: a day may have halt rows but no gate rows
        if Path(p).exists():
            done |= {json.loads(l)['day'] for l in open(p) if l.strip()}
    has_vol = None
    for d in dates:
        day_key = str(pd.Timestamp(d).date())
        if day_key in done:
            continue
        try:
            sess = load_day(month, d)
            if sess.empty:
                continue
            if has_vol is None:
                has_vol = 'volume' in sess.columns
            for sym, b in sess.groupby('ticker'):
                b = b.sort_values('timestamp')
                if len(b) < 120:
                    continue
                et = b['et'].to_numpy(int); o = b['open'].to_numpy(float)
                c = b['close'].to_numpy(float); h = b['high'].to_numpy(float)
                l = b['low'].to_numpy(float)
                if o[0] < 1 or o[0] > 50:
                    continue
                vv = b['volume'].to_numpy(float) if has_vol else None
                # ---- gate row
                pre = et <= 630
                if pre.sum() >= 30:
                    g_pre = float(c[pre][-1] / o[0] - 1)
                    if 0.15 <= g_pre < 0.30:
                        halts_pre = int((np.diff(et[pre]) >= 5).sum())
                        if halts_pre >= 1:
                            post = et >= 630
                            p0i = int(np.searchsorted(et, 630))
                            p0 = float(o[p0i])
                            fwd = float(c[-1] / p0 - 1)
                            mfe = float(np.max(h[post] / p0 - 1))
                            mae = float(np.min(l[post] / p0 - 1))
                            path = {}
                            run = 1.0
                            for lo, hi in HOURS:
                                m = (et >= lo) & (et < hi)
                                if m.any():
                                    lo_i = int(np.searchsorted(et, lo))
                                    op = float(o[lo_i]) if lo_i < len(b) else float(c[m][-1])
                                    run *= float(c[m][-1]) / op
                                path[f"{lo}-{hi}"] = round(run - 1, 4)
                            pm_i = int(np.searchsorted(et, 810, 'right') - 1)
                            p1330 = float(c[pm_i]) if pm_i > p0i else None
                            row = dict(day=day_key, sym=sym, month=month,
                                       g_pre=round(g_pre, 3), halts=halts_pre,
                                       fwd=round(fwd, 3), mfe=round(mfe, 3),
                                       mae=round(mae, 3), path=path,
                                       ret_pm=(round(float(c[-1] / p1330 - 1), 4)
                                               if p1330 else None),
                                       green1330=(int(p1330 > p0) if p1330 else None))
                            with open(out_gate, 'a') as f:
                                f.write(json.dumps(row) + '\n')
                # ---- halt rows
                idx_of = {int(x): i for i, x in enumerate(et)}
                holes = np.where(np.diff(et) >= 5)[0]
                prior = 0
                for i in holes:
                    j = i + 1
                    if et[j] > 930 or j + 1 >= len(b):
                        prior += 1
                        continue
                    gain_cum = float(c[i] / o[0] - 1)
                    if gain_cum < 0.10:
                        prior += 1
                        continue
                    g5 = float(c[i] / c[max(0, i - 5)] - 1) if i >= 5 else None
                    g10 = float(c[i] / c[max(0, i - 10)] - 1) if i >= 10 else None
                    t = int(et[j])
                    def close_at(k):
                        q = idx_of.get(t + k)
                        return float(c[q]) if (q is not None and q > j) else None
                    row = dict(day=day_key, sym=sym, month=month, t=t,
                               hole=int(et[j] - et[i]), prior_holes=prior,
                               gain_cum=round(gain_cum, 3),
                               g5=(round(g5, 3) if g5 is not None else None),
                               g10=(round(g10, 3) if g10 is not None else None),
                               pre_close=float(c[i]), ro_open=float(o[j]),
                               ro_close=float(c[j]), next_open=float(o[j + 1]),
                               c3=close_at(3), c10=close_at(10), c30=close_at(30))
                    if vv is not None:
                        row['v5'] = float(np.sum(vv[max(0, i - 5):i + 1]))
                        row['vro'] = float(vv[j])
                    with open(out_halt, 'a') as f:
                        f.write(json.dumps(row) + '\n')
                    prior += 1
        except Exception as ex:
            print(f'  {month} {day_key} SKIP {str(ex)[:60]}', flush=True)


if __name__ == '__main__':
    scan_month(sys.argv[1], sys.argv[2], sys.argv[3])
    print('done', sys.argv[1], flush=True)
