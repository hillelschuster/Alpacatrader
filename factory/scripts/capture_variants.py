"""Price the three simplest captures on the gate cohort (>=15% by 10:30 + >=1 halt).

Variants per gate name-day:
  dumb hold:  entry next_open@10:30 -> session close
  abort-7%:   same, but if low_after hits -7% -> exit at -7% (approx; halt gaps can be worse)
  pm rental:  price@13:30 -> close (unconditional)
  pm green:   same, only if 13:30 price > entry (still green)
Resumable per name-day. Read-only market data.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, 'factory')
from scripts.replay_watchlist import load_day
import pandas as pd, numpy as np

OUT = 'data/_scratch/capture3.jsonl'
gd = json.load(open('factory/artifacts/gate_decomp_2025.json'))
gate = sorted([r for r in gd if r['g_pre'] >= 0.15 and r['halts'] >= 1], key=lambda r: r['day'])
done = set()
if Path(OUT).exists():
    done = {(json.loads(l)['day'], json.loads(l)['sym']) for l in open(OUT) if l.strip()}
n = 0
for g in gate:
    key = (g['day'], g['sym'])
    if key in done:
        continue
    day, sym = key
    month = f"{day[:4]}-{day[5:7]}"
    try:
        sess = load_day(month, pd.Timestamp(day, tz='UTC'))
        b = sess[sess['ticker'] == sym].sort_values('timestamp')
        if len(b) < 120:
            continue
        et = b['et'].to_numpy(int); o = b['open'].to_numpy(float)
        c = b['close'].to_numpy(float); l = b['low'].to_numpy(float)
        ei = int(np.searchsorted(et, 630))
        if ei == 0 or ei >= len(b):
            continue
        entry = o[ei]
        ret_close = c[-1] / entry - 1
        low_after = float(np.min(l[ei:]))
        pm_i = int(np.searchsorted(et, 810, 'right') - 1)  # last bar before 13:30
        if pm_i <= ei:
            continue
        p1330 = c[pm_i]
        ret_pm = c[-1] / p1330 - 1
        row = dict(day=day, sym=sym, month=month, entry=float(entry),
                   ret_close=round(float(ret_close), 4),
                   low_after=round(low_after / entry - 1, 4),
                   ret_pm=round(float(ret_pm), 4),
                   green1330=int(p1330 > entry),
                   ret_pm_green=(round(float(ret_pm), 4) if p1330 > entry else None))
        with open(OUT, 'a') as f:
            f.write(json.dumps(row) + '\n')
        n += 1
        if n >= 200:
            break
    except Exception:
        pass
print('added', n)
