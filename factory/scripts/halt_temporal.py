"""Temporal decomposition of live halt events (per-month, resumable JSONL).

For every >=5min bar-hole in a name up >=10% at hole time:
  state: gain_cum (extension), gain_recent_5/10 (velocity), prior_holes, hour, hole_len
  prices: pre_close, ro_open, ro_close, next_open, closes at t+3/+10/+30
  volumes: v5 (5 bars pre), vro (reopen bar) if volume column exists

Anchors (computed in analysis):
  A  = ro_open/pre_close  (halt gap — untradable post-event)
  B1 = ro_close/ro_open   (reopen bar body)
  D  = close(t+k)/next_open (fully causal: see reopen print, enter next bar open)
  E  = close(t+k)/ro_open  (watch-and-hit at reopen)
Skips holes after 15:30 ET. Read-only on market data.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, 'factory')
from scripts.replay_watchlist import load_day
import pandas as pd, numpy as np


def scan_month(month, outp):
    base = Path('data/backfill') if month >= '2026-03' else Path('data')
    probe = pd.read_parquet(base / f'clean_ohlcv_{month}.parquet', columns=['timestamp'])
    probe['timestamp'] = pd.to_datetime(probe['timestamp'], utc=True)
    dates = sorted(probe['timestamp'].dt.floor('D').unique())
    done = set()
    if Path(outp).exists():
        done = {json.loads(l)['day'] for l in open(outp) if l.strip()}
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
                c = b['close'].to_numpy(float)
                if o[0] < 1 or o[0] > 50:
                    continue
                if has_vol:
                    v = b['volume'].to_numpy(float)
                idx_of = {int(x): i for i, x in enumerate(et)}
                holes = np.where(np.diff(et) >= 5)[0]
                prior = 0
                for i in holes:
                    j = i + 1
                    if et[j] > 930 or j + 1 >= len(b):
                        prior += 1
                        continue
                    gain_cum = c[i] / o[0] - 1
                    if gain_cum < 0.10:
                        prior += 1 if et[j] <= 930 else 0
                        continue
                    g5 = (c[i] / c[max(0, i - 5)] - 1) if i >= 5 else None
                    g10 = (c[i] / c[max(0, i - 10)] - 1) if i >= 10 else None
                    t = int(et[j])
                    def close_at(k):
                        q = idx_of.get(t + k)
                        return float(c[q]) if q is not None and q > j else None
                    row = dict(day=day_key, sym=sym, month=month, t=t,
                               hole=int(et[j] - et[i]), prior_holes=prior,
                               gain_cum=round(float(gain_cum), 3),
                               g5=round(float(g5), 3) if g5 is not None else None,
                               g10=round(float(g10), 3) if g10 is not None else None,
                               pre_close=float(c[i]), ro_open=float(o[j]),
                               ro_close=float(c[j]), next_open=float(o[j + 1]),
                               c3=close_at(3), c10=close_at(10), c30=close_at(30))
                    if has_vol:
                        row['v5'] = float(np.sum(v[max(0, i - 5):i + 1]))
                        row['vro'] = float(v[j])
                    with open(outp, 'a') as f:
                        f.write(json.dumps(row) + '\n')
                    prior += 1
        except Exception as ex:
            print(f'  {month} {day_key} SKIP {str(ex)[:60]}', flush=True)


if __name__ == '__main__':
    scan_month(sys.argv[1], sys.argv[2])
    print('done', sys.argv[1], flush=True)
