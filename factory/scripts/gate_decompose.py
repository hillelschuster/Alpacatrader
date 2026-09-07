"""Gate decomposition + halt-reopen mechanics (one pass per month).

Rows 1 (gate file): every name with >=5% RTH gain by 10:30 — records exact halt count
by 10:30, premarket gap, rest-of-day fwd/MFE/MAE. Answers: thrust vs halt vs interaction.

Rows 2 (reopen file): every >=5min bar-hole during the day for names up >=10% at hole
time (live process) AND a flat-name control (0..5%). Records hole length, post-hole
1/3-bar returns. Answers: does the halt queue mechanically reprice?

Incremental per-day JSONL, resumable. Read-only on market data.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, 'factory')
from scripts.replay_watchlist import load_day, prior_closes
import pandas as pd, numpy as np


def scan_month(month, out_gate, out_reopen):
    base = Path('data/backfill') if month >= '2026-03' else Path('data')
    probe = pd.read_parquet(base / f'clean_ohlcv_{month}.parquet', columns=['timestamp'])
    probe['timestamp'] = pd.to_datetime(probe['timestamp'], utc=True)
    dates = sorted(probe['timestamp'].dt.floor('D').unique())
    done = set()
    if Path(out_gate).exists():
        done = {json.loads(l)['day'] for l in open(out_gate) if l.strip()}
    prev = prior_closes(month, None) if False else None
    for d in dates:
        if str(d.date()) in done:
            continue
        try:
            sess = load_day(month, d)
            if sess.empty:
                continue
            day_key = str(pd.Timestamp(d).date())
            pc = prior_closes(month, d)
            for sym, b in sess.groupby('ticker'):
                b = b.sort_values('timestamp')
                if len(b) < 120:
                    continue
                et = b['et'].to_numpy(int); o = b['open'].to_numpy(float)
                c = b['close'].to_numpy(float); h = b['high'].to_numpy(float)
                l = b['low'].to_numpy(float)
                if o[0] < 1 or o[0] > 50:
                    continue
                # ---- gate row
                pre = et <= 630
                if pre.sum() >= 30:
                    g_pre = c[pre][-1] / o[0] - 1
                    if g_pre >= 0.05:
                        post = et >= 630
                        p0 = o[post][0] if post.any() else None
                        if p0:
                            fwd = c[-1] / p0 - 1
                            mfe = float(np.max(h[post] / p0 - 1))
                            mae = float(np.min(l[post] / p0 - 1))
                            halts_pre = int((np.diff(et[pre]) >= 5).sum())
                            gap = (o[0] / pc[sym] - 1) if pc.get(sym) else None
                            row = dict(day=day_key, sym=sym, month=month,
                                       g_pre=round(float(g_pre), 3), halts=halts_pre,
                                       gap=round(float(gap), 3) if gap is not None else None,
                                       fwd=round(float(fwd), 3), mfe=round(mfe, 3),
                                       mae=round(mae, 3))
                            with open(out_gate, 'a') as f:
                                f.write(json.dumps(row) + '\n')
                # ---- reopen rows (any hole in session, name up >=10% or flat control)
                gaps_idx = np.where(np.diff(et) >= 5)[0]
                for i in gaps_idx:
                    j = i + 1
                    if et[j] > 930 or i + 3 >= len(b):  # skip late-day; need 3 post bars
                        continue
                    gain_at = c[i] / o[0] - 1
                    live = gain_at >= 0.10
                    ctrl = 0.0 <= gain_at <= 0.05
                    if not (live or ctrl):
                        continue
                    r = dict(day=day_key, sym=sym, month=month,
                             t=int(et[j]), hole=int(et[j] - et[i]),
                             live=int(live), gain_at=round(float(gain_at), 3),
                             ret1=round(float(c[j] / c[i] - 1), 4),
                             ret3=round(float(c[min(i + 3, len(b) - 1)] / c[i] - 1), 4))
                    with open(out_reopen, 'a') as f:
                        f.write(json.dumps(r) + '\n')
        except Exception as ex:
            print(f'  {month} {d} SKIP {str(ex)[:60]}', flush=True)


if __name__ == '__main__':
    scan_month(sys.argv[1], sys.argv[2], sys.argv[3])
    print('done', sys.argv[1], flush=True)
