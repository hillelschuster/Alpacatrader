# PHASE1-RECAP — Phase-1 containment / joint-tail / capture-funnel evidence

Read-only recovery of the frozen Phase-1 SIP-packet numbers behind the owner's recollection
"PM top-3 captured ~30-40% of the top gainers." Every figure below is a **measurement** read
out of an existing artifact; nothing is re-derived from new data and nothing is a profitability
claim.

Pointer legend (all paths repo-relative):

- `PKT` = `factory/artifacts/basket/sip/READ_PACKET.md` (embeds the agg JSON; `L<n>` = line)
- `T2` = `factory/artifacts/basket/sip/agg/T2.json`
- `T7b` = `factory/artifacts/basket/sip/agg/T7b.json`
- `CF` = `factory/artifacts/basket/sip/agg/capture_funnel.json`
- `PR2` = `researches/PRE-REG-BASKET-02.md` (worktree copy byte-identical to main at L25-27)

Dev days = 1,066 (2021-02-01..2026-05-29 minus 2024 and 2025-01; `PKT` coverage `"days": 1068`
at L5 is the raw anatomy count, not the dev set). Every containment/joint-tail cell below pools
51 monthly rows (B1 2021-02..2023-12 = 35 months, B2 2025-02..2026-05 = 16 months).

---

## (a) Containment — eventual session gainers inside our selected top-N

`top{wr}_in` = the day's **wr-th largest hi_open gainer** (session max, RTH-open anchored) was
one of our top-N selected names. The winner list is `winners_open` = top-10 by `hi/o570-1`
(`factory/scripts/sip_candidates.py:14`); the aggregate keeps `[:3]`
(`factory/scripts/basket_aggregate.py:140-153`). The three counters are **disjoint by rank**
(Name 1 / Name 2 / Name 3 of the day's top gainers), not cumulative.

**Denominator `days` = 1,066** for every cell (day-level snapshot count; `PKT` note L87270-87273).
Pooled share = Σ(monthly integer) / Σ(monthly `days`); the per-month share columns are rounded
to 5 dp in `T2`, so the pooled share is taken from the integers.

| view (pop/T) | N | days | top1_in | share | top2_in | share | top3_in | share | PKT row |
|---|---|---|---|---|---|---|---|---|---|
| A_pm/570  | 1 | 1066 | 27  | 0.0253 | 33  | 0.0310 | 14  | 0.0131 | L211 |
| A_pm/570  | 3 | 1066 | 93  | 0.0872 | 76  | 0.0713 | 43  | 0.0403 | L233 |
| A_pm/570  | 10| 1066 | 191 | 0.1792 | 158 | 0.1482 | 127 | 0.1191 | L277 |
| A_pm31/570| 1 | 1066 | 27  | 0.0253 | 33  | 0.0310 | 14  | 0.0131 | L299 |
| A_pm31/570| 3 | 1066 | 93  | 0.0872 | 76  | 0.0713 | 43  | 0.0403 | L321 |
| A_pm31/570| 10| 1066 | 191 | 0.1792 | 158 | 0.1482 | 127 | 0.1191 | L365 |
| A_open/570| 1 | 1066 | 25  | 0.0235 | 27  | 0.0253 | 12  | 0.0113 | L123 |
| A_open/570| 3 | 1066 | 89  | 0.0835 | 67  | 0.0629 | 44  | 0.0413 | L145 |
| A_open/570| 10| 1066 | 188 | 0.1764 | 154 | 0.1445 | 133 | 0.1248 | L189 |
| B/600     | 1 | 1066 | 212 | 0.1989 | 146 | 0.1370 | 116 | 0.1088 | L827 |
| B/600     | 3 | 1066 | 304 | 0.2852 | 249 | 0.2336 | 222 | 0.2083 | L849 |
| B/600     | 10| 1066 | 377 | 0.3537 | 331 | 0.3105 | 306 | 0.2871 | L893 |

Field offsets inside each `PKT` row block: `days` = key L+2, `blocked_in` = L+3,
`top1_in` = L+4, `top2_in` = L+5, `top3_in` = L+6 (and the `*_share` fields follow each
integer). Machine source: `T2` rows `pop∈{A_pm,A_pm31,A_open,B}` (`T=570` for the A_* views,
`T=600` for B), `N∈{1,3,10}` — 51 rows per cell.

Notes:
- A_pm and A_pm31 coincide at N=1/3/10 in these four counters; they differ only in fill
  bookkeeping (`blocked_in` N=3: A_pm 0 vs A_pm31 5).
- `blocked_in` is the complement of `filled_in` among contained primary (open-anchored)
  leaders; blocked slots are cash, not participation (L87271).
- `PKT`/`T2` also carry `prev_`, `eodopen_`, `eodprev_`, `flr_`, `flreod_` variants for other
  leader objects; not reproduced here (this recap is the open-anchored, unfloored definition).

## (b) Joint tail, touch lens, N=3 (filled top-3)

`T7b` `set=main` = our top-3 (`PRIMARY_N = 3`, `factory/scripts/basket_aggregate.py:56`),
**filled-only** members; `k_touch_H` counts how many of those members' minute paths touched
+H% over their own fill price; `hist[i]` = number of days with exactly `i` touchers
(`factory/scripts/basket_aggregate.py:199-206`). `set=adj` (selection ranks 4-6) is the
rank-adjacent control. Denominators: `Σhist = days = 1,066` per cell.

Formulas used: `k>=1 = (Σhist − hist[0])/Σhist`; `k>=2 = (hist[2]+hist[3])/Σhist`;
`all3 = hist[3]/Σhist`. Pooled from the 51 monthly `touch_k_H` arrays in `T7b`.

| view | ruler | hist (k=0/1/2/3) | k>=1 | k>=2 | all3 | PKT row |
|---|---|---|---|---|---|---|
| A_pm/570 main | +30  | [596, 367, 93, 10] | 0.4409 | 0.0966 | 0.0094 | key L2052, bin L2153 |
| A_pm/570 main | +50  | [798, 240, 26, 2]  | 0.2514 | 0.0263 | 0.0019 | L2186 |
| A_pm/570 main | +100 | [963, 100, 3, 0]   | 0.0966 | 0.0028 | 0.0000 | L2219 |
| A_pm/570 adj  | +30  | [657, 341, 60, 8]  | 0.3837 | 0.0638 | 0.0075 | key L1851, bin L1952 |
| A_pm/570 adj  | +50  | [847, 200, 16, 3]  | 0.2054 | 0.0178 | 0.0028 | L1985 |
| A_pm/570 adj  | +100 | [995, 68, 3, 0]    | 0.0666 | 0.0028 | 0.0000 | L2018 |
| B/600 main    | +30  | [644, 355, 61, 6]  | 0.3959 | 0.0629 | 0.0056 | key L4866, bin L4967 |
| B/600 main    | +50  | [828, 220, 18, 0]  | 0.2233 | 0.0169 | 0.0000 | L5000 |
| B/600 main    | +100 | [981, 84, 1, 0]    | 0.0797 | 0.0009 | 0.0000 | L5033 |
| B/600 adj     | +30  | [855, 192, 18, 1]  | 0.1979 | 0.0178 | 0.0009 | key L4665, bin L4766 |
| B/600 adj     | +50  | [961, 104, 1, 0]   | 0.0985 | 0.0009 | 0.0000 | L4799 |
| B/600 adj     | +100 | [1035, 31, 0, 0]   | 0.0291 | 0.0000 | 0.0000 | L4832 |

Each bin block holds the sub-keys `k>=1`, `k>=2`, `all3`, `hist` in that order. The `exec_*`
(accessible-next-bar) lens is within ~0.1 pp of `touch_*` on the main set (e.g. A_pm +30
k>=1: exec 0.4400 vs touch 0.4409, `PKT` L2164-2165), so the executable adjustment is immaterial
at these rulers.

Whole-frozen-surface `touch_30 k>=1` at N=3/main, for the range cited in `PR2`:
A_open/570 0.3893, A_pm/570 0.4409, A_pm31/570 0.4146, B/595 0.3912, B/600 0.3959
(pooled from `T7b`; per-family keys at `PKT` L1449/1650 A_open, L1851/2052 A_pm,
L2253/2454 A_pm31, L4263/4464 B/595, L4665/4866 B/600; `adj`/`main`).

## (c) Capture funnel — top-3 "contains" a +H monster day

Exact JSON keys (`CF` `summary` -> anchor -> `views` -> view):
`contains_share`, `monster_ahead_share_q` (`{n, p50, p90}`), plus `monster_above_H_days`.
`contains_share` = share of offered days on which our top-3 included at least one full-PIT name
that reached +H over the anchor — **not** the singular champion (`PKT` L87269; `CF` definitions
`PKT` L84083). Denominator = `days` = `days_offered` = dev days on which ≥1 eligible name
existed for that anchor/H (`PKT` L39207; open +H50 = 959 days, prev-close +H50 = 1052 days,
`PKT` L39234-39235, L39263-39264). `monster_ahead_share` is `(post-fill high − fill)/(day high
− anchor)` on accessible fills only.

| anchor | view | days_offered | contains_days | `contains_share` | `monster_ahead_share_q` | `monster_above_H_days` | PKT view |
|---|---|---|---|---|---|---|---|
| hi_open/H50 | A_pm/570   | 959  | 268  | 0.2795 | {n:268, p50:1.0000, p90:1.0000} | 169 | L84088 (`contains_share` L84103) |
| hi_open/H50 | A_pm31/570 | 959  | 268  | 0.2795 | {n:260, p50:0.9885, p90:1.0405} | 133 | L84128 |
| hi_open/H50 | A_open/570 | 959  | 249  | 0.2596 | {n:243, p50:0.9889, p90:1.0456} | 126 | L84168 |
| hi_open/H50 | B/600      | 959  | 646  | 0.6736 | {n:600, p50:0.5445, p90:0.8129} | 93  | L84208 (`contains_share` L84222) |
| hi_prev/H50 | A_pm/570   | 1052 | 1005 | 0.9553 | {n:1005, p50:0.1478, p90:0.5363} | 66  | L84581 (`contains_share` L84596) |
| hi_prev/H50 | A_pm31/570 | 1052 | 1005 | 0.9553 | {n:991, p50:0.1371, p90:0.5235}  | 52  | L84621 |
| hi_prev/H50 | A_open/570 | 1052 | 1009 | 0.9591 | {n:990, p50:0.1279, p90:0.4962}  | 48  | L84661 |
| hi_prev/H50 | B/600      | 1052 | 771  | 0.7329 | {n:728, p50:0.3455, p90:0.7481}  | 83  | L84700 (`contains_share` L84714) |
| hi_open/H100| A_pm/570   | 511  | 103  | 0.2016 | {n:103, p50:1.0000, p90:1.0000}  | 55  | L84252 (`contains_share` L84267) |
| hi_open/H100| A_pm31/570 | 511  | 103  | 0.2016 | {n:100, p50:0.9922, p90:1.0258}  | 48  | L84292 |
| hi_open/H100| A_open/570 | 511  | 92   | 0.1800 | {n:91, p50:0.9950, p90:1.0262}   | 45  | L84332 |
| hi_open/H100| B/600      | 511  | 255  | 0.4990 | {n:229, p50:0.7342, p90:0.8846}  | 28  | L84372 (`contains_share` L84387) |
| hi_prev/H100| A_pm/570   | 922  | 834  | 0.9046 | {n:834, p50:0.1451, p90:0.5838}  | 28  | L84744 (`contains_share` L84759) |
| hi_prev/H100| A_pm31/570 | 922  | 834  | 0.9046 | {n:822, p50:0.1372, p90:0.5892}  | 25  | L84784 |
| hi_prev/H100| A_open/570 | 922  | 850  | 0.9219 | {n:832, p50:0.1268, p90:0.5519}  | 21  | L84824 |
| hi_prev/H100| B/600      | 922  | 423  | 0.4588 | {n:396, p50:0.4024, p90:0.8093}  | 24  | L84863 (`contains_share` L84878) |

`CF` also carries `hi_open/H200` and `hi_prev/H200` (not requested). `contains_accessible_days`
< `contains_days` where fills were blocked (e.g. B/600 hi_open/H50: 600 accessible / 46 blocked);
`monster_ahead_share_q`'s `n` is the accessible complement.

---

## (d) Verbatim Phase-1 recital in the Phase-2 pre-registration

`PR2` L25-27 (`researches/PRE-REG-BASKET-02.md`, lines 25-27; quoted exactly):

> 1. Phase 1 established a reproducible, strongly right-tailed top-gainer population on 1,066
>    SIP development days (packet: containment B/600 top-3 20.8%, joint touch +30 k≥1 ≈ 39–44%
>    by entry family, matched-random +30 ≈ 1.65%). Phase 2 is **implementation discovery**:
>    find the highest-EV causal way to harvest the measured phenomenon.

The "containment B/600 top-3 20.8%" is §(a) B/600 N=3 `top3_in = 0.2083`; the
"joint touch +30 k≥1 ≈ 39–44% by entry family" is §(b)'s N=3/main `touch_30 k>=1` across
A_open/A_pm/A_pm31/B(T) families.

## (e) Denominators (verbatim from the packet)

- `PKT` L87269: "capture_funnel 'contains' = the day's top-3 included at least one +H-qualified
  name; it is NOT the singular eventual leader object (that is the containment table's top1_in)."
- `PKT` L87270: "Containment: top{k}_in = hi_open leaders (session max, RTH-open anchored);
  prev_/eodopen_/eodprev_ are the hi_prev, EOD/open and EOD/prev leader objects — never
  conflated with the intraday-high definition."
- `PKT` L87271: "Containment `blocked_in` = contained leaders whose fill was not accessible
  (blocked slot or missing fill); `filled_in` is its complement among contained primary
  (open-anchored) leaders."
- `PKT` L87272: "Containment `flr_`/`flreod_` counters use the $1-floored leader objects: an
  independent top-10 re-rank restricted to o570 >= $1 (the basket-eligible anchor), NOT a subset
  of the unfloored list."
- `PKT` L87273: "Pooling = day-weighted sums across months; quantities are provisional until the
  artifact root's QA gate reports PASS."
- `PKT` L84083 (`capture_funnel` `definitions`): "… contains = the day's anatomy top-3 included at
  least one such +H-qualified name for that (pop,T) … monster_ahead_share = (post-fill high −
  fill) / (day high − anchor); miss_* = on days no +H-qualified name was in our top-3 …"
- `PKT` L39207 (`market_base_rates` `method`): the offered-day universe behind `days_offered`
  ("open anchor = hi/o570-1 with o570>=$1.0; prev_close anchor = hi/prev-1 with o570>=$1 …").
- `PKT` L87267 (lenses): "touch = the excursion exists on the minute path; exec = touch AND
  a next bar existed … above = the next bar's open is at/above the threshold … Never read exec as
  'we could have sold there'."

## (f) What the "~30-40%" memory most plausibly refers to, and what it does NOT claim

**Most plausible referent.** The owner's "PM top-3 captured ~30-40% of the top gainers" matches
the Phase-1 **joint-tail touch stat**: at N=3/main, `touch_30 k>=1` is 44.1% (A_pm/570), 41.5%
(A_pm31/570), 38.9% (A_open/570) and 39.6% (B/600) — i.e. the pre-registration's own recital
"joint touch +30 k≥1 ≈ 39–44% by entry family" (`PR2` L26). Read plainly: *on ~40% of the 1,066
dev days, the three-name basket contained at least one name whose minute path touched +30% over
its fill at some point in the session.* If instead the memory means the pre-registration's
"containment B/600 top-3 20.8%", that is §(a) `top3_in` at B/600/N=3, which does not reach
30-40%; the containment counters only enter that band at N=10 (B/600 `top1_in` 35.4%,
`top2_in` 31.1%).

**What it does NOT claim.**
- Not net EV and not a P&L. The packet is explicitly "Measurement only. No release rule, no
  survivor rule, no policy P&L" (`factory/scripts/basket_aggregate.py:9`); `PR2` L26-28
  presents it as a phenomenon to be *harvested*, not a result.
- Not accessible/saleable capture. `touch` is a minute-path excursion lens; `exec` is only
  "touch AND a next bar existed" and is explicitly "NOT a claim of sellability" (`PKT` L87267).
- No friction. Nothing in §(a)-(c) deducts the 100 bps round-trip (or the fill/gap/blocked-slot
  mechanics); blocked and unfilled slots are counted separately and never treated as
  participation (`PKT` L87271).
- Not "a share of the gain" and not cumulative in rank. `top1_in`/`top2_in`/`top3_in` are
  disjoint per-rank day indicators for the day's #1/#2/#3 gainers, and the joint-tail histograms
  count *days* by number of touching members, not dollars or fractions of move.
- Not the matched-random baseline. The relevant control is "matched-random +30 ≈ 1.65%"
  (`PR2` L27); the raw "+30 happens somewhere" day rate is ~99.5% (`PKT` L39229-39230), so
  neither the 20.8% containment nor the 39-44% touch figure should be read without its control.
- Not the $1-floored / basket-eligible ranking (`flr_`) and not the hi_prev/EOD leader objects;
  those are different, separately-keyed definitions (`PKT` L87270, L87272).
