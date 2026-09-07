"""One pass per MONTH (loads each day once), not per name-day.

For every name >=5% by 10:30: 13:30 reference price and rest-of-day return,
split green/red at 13:30. Answers: is the pm-green conditioning gate-independent?
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
    for d in dates:
        day_key = str(pd.Timestamp(d).date())
        if day_key in done:
            continue
        try:
            sess = load_day(month, d)
            if sess.empty:
                continue
            for sym, b in sess.groupby('ticker'):
                b = b.sort_values('timestamp')
                if len(b) < 120:
                    continue
                et = b['et'].to_numpy(int); o = b['open'].to_numpy(float)
                c = b['close'].to_numpy(float)
                if o[0] < 1 or o[0] > 50:
                    continue
                ei = int(np.searchsorted(et, 630))
                if ei == 0 or ei >= len(b):
                    continue
                g_pre = c[et < 630][-1] / o[0] - 1
                if g_pre < 0.05:
                    continue
                entry = o[ei]
                pm_i = int(np.searchsorted(et, 810, 'right') - 1)
                if pm_i <= ei:
                    continue
                p1330 = c[pm_i]
                row = dict(day=day_key, sym=sym, month=month, g_pre=round(float(g_pre), 3),
                           ref1330=round(float(p1330 / entry - 1), 4),
                           ret_pm=round(float(c[-1] / p1330 - 1), 4))
                with open(outp, 'a') as f:
                    f.write(json.dumps(row) + '\n')
        except Exception as ex:
            print(f'  {month} {day_key} SKIP {str(ex)[:60]}', flush=True)

if __name__ == '__main__':
    scan_month(sys.argv[1], sys.argv[2])
    print('done', sys.argv[1], flush=True)
