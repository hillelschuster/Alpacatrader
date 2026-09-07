"""H11 COLLISION TEST — frozen rule, unseen months.

RULE (frozen 2026-09-07, never tuned on collision months):
  watchlist by 10:30 ET = RTH names with open in [1,50] whose 10:30-close/9:30-open
  gain >= 15% AND >= 1 halt-gap (>=5-min hole in RTH minute bars) by 10:30,
  AND 15% crossed BEFORE the first counted halt is not required (gate as defined).
  Sweet spot only: gain < 30% by 10:30 (exhaustion zone excluded).
  ENTRY: first bar with et >= 810 (13:30 ET), at that bar's OPEN (next-touch fill).
  EXIT: last session bar close (15:59), market close.
  COSTS: 100 bps round trip (50 entry + 50 exit) against gross.
  NAME cap: none (population test). Fill validity: entry bar volume > 0.

Out: per name-day row + per-month summary. Incremental JSONL per day, resumable.
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
                c = b['close'].to_numpy(float); v = b['volume'].to_numpy(float)
                if o[0] < 1 or o[0] > 50:
                    continue
                # GATE at 10:30 (et <= 630), RTH-only holes
                pre = et <= 630
                if pre.sum() < 30:
                    continue
                g_pre = c[pre][-1] / o[0] - 1
                if not (0.15 <= g_pre < 0.30):
                    continue
                rth_pre = np.diff(et[pre])
                halts = int((rth_pre >= 5).sum())
                if halts < 1:
                    continue
                # ENTRY: first bar et >= 810
                ei = int(np.searchsorted(et, 810))
                if ei >= len(b) or v[ei] <= 0:
                    continue  # no tradable fill
                entry = o[ei]
                exitp = c[-1]
                gross = exitp / entry - 1
                net = gross - 0.0100
                # honesty fields: worst 13:30->close MAE (low-based)
                mae = float(np.min(b['low'].to_numpy(float)[ei:] ) / entry - 1)
                mfe = float(np.max(b['high'].to_numpy(float)[ei:]) / entry - 1)
                row = dict(day=day_key, sym=sym, month=month, g_pre=round(float(g_pre), 3),
                           halts=halts, entry=round(float(entry), 3),
                           gross=round(float(gross), 4), net=round(float(net), 4),
                           mae=round(mae, 4), mfe=round(mfe, 4))
                with open(outp, 'a') as f:
                    f.write(json.dumps(row) + '\n')
        except Exception as ex:
            print(f'  {month} {day_key} SKIP {str(ex)[:70]}', flush=True)


if __name__ == '__main__':
    scan_month(sys.argv[1], sys.argv[2])
    print('done', sys.argv[1], flush=True)
