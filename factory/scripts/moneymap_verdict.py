"""P2 verdict aggregation (PRE-REG-P2 M1-M6). Reads the two committed P2 artifacts,
prints the verdict table. This is the ONLY sanctioned producer of the numbers in
researches/HYPOTHESES.md section 2026-09-08e. Re-run to verify:
  python factory/scripts/moneymap_verdict.py
"""
import json, statistics as st
from collections import defaultdict
from pathlib import Path

ART = Path('factory/artifacts')


def rep(v):
    if not v:
        return 'n=0'
    return 'n={:<5d} {:+.2f}% med {:+.2f}% win {:.0f}%'.format(
        len(v), 100 * st.mean(v), 100 * st.median(v), 100 * sum(1 for x in v if x > 0) / len(v))


def main():
    gate = json.load(open(ART / 'moneymap_pooled_gate.json'))
    halt = json.load(open(ART / 'moneymap_pooled_halt.json'))
    print('gate n=%d name-days on %d days | halt events n=%d'
          % (len(gate), len(set(r['day'] for r in gate)), len(halt)))
    # M4
    m4 = [r['ret_pm'] for r in gate if r['ret_pm'] is not None]
    print('M4 13:30->close (gross):', rep(m4))
    mm = defaultdict(list)
    for r in gate:
        if r['ret_pm'] is not None:
            mm[r['day'][:7]].append(r['ret_pm'])
    for k in sorted(mm):
        print('  %s: n=%d %+.0f bps' % (k, len(mm[k]), 100 * st.mean(mm[k]) * 100))
    # M6
    print('M6 green:', rep([r['ret_pm'] for r in gate if r['green1330'] == 1 and r['ret_pm'] is not None]))
    print('M6 red:  ', rep([r['ret_pm'] for r in gate if r['green1330'] == 0 and r['ret_pm'] is not None]))
    # M3
    print('M3 hourly accrual from 10:30 (cumulative):')
    for k in ('630-660', '660-720', '720-780', '780-840', '840-900', '900-960'):
        v = [r['path'][k] for r in gate]
        print('  %s: mean %+.2f%% med %+.2f%%' % (k, 100 * st.mean(v), 100 * st.median(v)))
    print('M3 fwd 10:30->close:', rep([r['fwd'] for r in gate]))
    # M1 / M2
    Q = [r for r in halt if r['gain_cum'] >= 0.30 and r['hole'] >= 11]
    QM = [r for r in Q if r['t'] < 750]
    print('M1 gap A qualifying:', rep([r['ro_open'] / r['pre_close'] - 1 for r in Q]))
    print('M1 gap A morning:', rep([r['ro_open'] / r['pre_close'] - 1 for r in QM]))
    print('M2 D10:', rep([r['c10'] / r['next_open'] - 1 for r in Q if r.get('c10') is not None]))
    print('M2 D30:', rep([r['c30'] / r['next_open'] - 1 for r in Q if r.get('c30') is not None]))
    print('M2 D30 morning:', rep([r['c30'] / r['next_open'] - 1 for r in QM if r.get('c30') is not None]))
    # M5
    v = sorted(m4)
    print('M5 excl top-5: %+.2f%% | excl top-10: %+.2f%%'
          % (100 * st.mean(v[:-5]), 100 * st.mean(v[:-10])))
    w = [x for x in v if x > 0]
    l = [x for x in v if x <= 0]
    print('M5 winners n=%d mean %+.2f%% | losers n=%d mean %+.2f%%'
          % (len(w), 100 * st.mean(w), len(l), 100 * st.mean(l)))


if __name__ == '__main__':
    main()
