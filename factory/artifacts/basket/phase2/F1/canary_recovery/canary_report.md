# BASKET-01 Phase-2 canary report

Contract: FROZEN-2026-09-22  |  bootstrap seed: 20260922
Generated: 2026-09-23T21:32:40

## Verdicts

- Canary 1: **PASS**
- Canary 2: **PASS**
- Canary 3: **PASS**
- Canary 4: **PASS**
- Canary 5: **PASS**
- Canary 6: **PASS**
- Canary 7: **PASS**
- **All pass: True**

## Numbers

1. fill.px==open[fill.et]: 159785 names, 0 mismatches, 19.4s
2. MFE/MAE recompute: 31 days, 4649 names, 0 mismatches
3. ladder first-touch: 51139 cells, 0 mismatches
4. R1(-10) independent scan: 20 tickets, 0 mismatches, gap_through=True, pending=True
5. forced flat: 10193 exits, 0 mismatches, engine_mean=-0.016305032038205425, direct_mean=-0.016305032038205425
6. determinism: run1=68d561765ad6306e run2=68d561765ad6306e ok=True
7. full baseline invariants: 1066 days, 104.6s, entries=10528, load/day=0.0009s

## Formulas matched

- canary 2: mfe=max(high[fi:])/fill-1; mae=min(low[fi:])/fill-1; i=argmax/argmin offset from fi; full bar array (no session filter), fi=first et>=target
- canary 3: first i>=fi with high>=fill*(1+H/100) or low<=fill*(1-L/100); touch=high/low at i; exec=open[i+1]
