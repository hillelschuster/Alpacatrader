# Cost & Fill Reality — Alpaca small-cap marketable round trips

As-of 2026-10-26. Primary sources cited inline; repo evidence quoted with file/line anchors.
Scope: $1,000 marketable round trip in a $2–$20 NMS small-cap, entry at ask, exit at bid.

---

## 1. Exact all-in broker/regulatory cost for a $1,000 RT

Alpaca retail equities commission is **$0**; only three pass-through fees exist, and no
equities exchange/venue fee is listed for regular retail accounts.

| Component | Rule | Side |
|---|---|---|
| Commission | $0 (regular retail; Elite Smart Router / non-retail / business-partner excluded) | both |
| SEC Section 31 | $0.0000206 × sell trade value (= $20.60 per $1M, effective 2026-04-04) | sell |
| FINRA TAF | $0.000195/share, cap $9.79/trade — **$0.00 from 2026-10-01 to 2026-12-31**, resumes 2027-01-01 | sell |
| FINRA CAT | $0.000003 × executed equivalent share | both |
| Exchange/venue pass-through | none listed for regular retail equities | — |

Sources: Alpaca Brokerage Fee Schedule rev. 2026-10-12
(https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf) and Broker Partner
Exhibit B rev. 2026-10-01
(https://files.alpaca.markets/disclosures/library/BrokerAPIExhibitB.pdf) — "SEC Transaction
Fee $0.0000206 * Trade Value When sells only", "TAF $0 per share When sells only", "CAT
$0.000003 per executed equivalent share", "Each fee type is aggregated separately at the
daily, per-account level. After aggregation, each fee total is rounded up to the nearest
cent." SEC rate: FY2026 Rate Advisory ($20.60/$M, effective 2026-04-04) via LTSE RIC-2026-01
(https://cdn.prod.website-files.com/6462417e8db99f8baa06952c/...RIC-2026-01...pdf). TAF:
SR-FINRA-2026-021 Exhibit 5A — "$[0.000195]0.00 per share ... maximum charge of $[9.79]0.00
per trade" for 2026-10-01..2026-12-31, previous rates resume with January 2027 transactions;
rates held to 2028-12-31 by SR-FINRA-2026-020
(https://www.finra.org/sites/default/files/2026-09/SR-FINRA-2026-021.pdf).

Exact arithmetic, q = floor(1000/P) shares, buy ≈ $1,000, sell ≈ $1,000:

| Price | Shares | SEC $ | TAF $ (2027 basis) | CAT $ | Total $ | Total bps of $1,000 | **Now (TAF $0)** |
|---|---|---|---|---|---|---|---|
| $2 | 500 | 0.0206 | 0.0975 | 0.0030 | 0.1211 | **1.21 bps** | 0.24 bps |
| $5 | 200 | 0.0206 | 0.0390 | 0.0012 | 0.0608 | **0.61 bps** | 0.22 bps |
| $10 | 100 | 0.0206 | 0.0195 | 0.0006 | 0.0407 | **0.41 bps** | 0.21 bps |
| $20 | 50 | 0.0206 | 0.00975 | 0.0003 | 0.0307 | **0.31 bps** | 0.21 bps |

- **Answer: 0.2–1.2 bps of entry notional ($0.02–$0.12 on $1,000).** Today (Q4-2026 TAF
  holiday) 0.21–0.24 bps; from 2027-01-01, 0.31–1.21 bps (price-driven, per-share TAF).
- EOD up-cent rounding adds ≤ $0.03 per active day (3 fee types × up to a cent) ≈ ≤0.3 bps
  per trade at 1 RT/day, ≤0.06 bps at 2 RT/week. Not a per-trade floor.
- Margin interest 6.50%/yr (fee schedule) on a 60-min $1,000 debit = $0.0074 ≈ 0.07 bps;
  zero if cash-funded. **Excluded and much larger: data opex (§5).**
- Repo cross-check (independent, same conclusion): `provider_fee_ledger/summary.json`,
  base_143 cohort, Alpaca current profile — 79 supported fills, raw fee $1.8351, posted
  $2.71, **fee_rt_bps_of_entry_notional 0.344**; TAF-Jan-2027 profile **1.108 bps**
  (entry notional $78,679.69). Producer: `factory/scripts/alpha_provider_fee_ledger.py`
  (fee profiles `_alpaca`, `PROVIDERS`, posting helper `up_cent`, RT basis
  `round_trip_fee_bps`).

---

## 2. What the "25 bps rung" actually is

The rung is **not a fee**. It is an *additional* round-trip residual charged on top of
**actual ask/bid prices that already contain the spread**:

- `factory/scripts/alpha_adaptive_depth_sizing.py:103-109, 177-184, 733-736` — "the entry
  rule charges the OBSERVED spread plus a reasonable 25bps residual"; `net_usd` uses
  `side = cost_bps / 20000`, i.e. **25 bps RT = 12.5 bps per leg, both legs, at real
  notional** (audited: `factory/STATE.md:2776`).
- The spread itself is already paid once by the sim: fee ledger rung-0 touch
  `mean_price_effect_bps = 248.45` (base_143, $1,000 budget) — ~**$25.10 per $1,000 RT**.
- So total modelled friction at rung 25 ≈ 248 + 25 ≈ 273 bps, while measured
  Alpaca fees are 0.2–1.2 bps. **The fee is ~1/20th to ~1/125th of the rung.**
- Cross-family trap already on the record (`researches/STATE.md:274-275`,
  `factory/STATE.md:2776-2778`): in quote-touch lanes the rung is a *residual*; in
  minute-proxy lanes the same number is *total* friction. Never compare across families.

Per-fill dollar weight, chosen adaptive arm (`adaptive_depth_sizing/results.json`,
`thr300bps|p50`): mean net per known fill at 0/25/50 bps = $17.97 / $16.19 / $14.41 late
→ **~$1.78 per 25 bps per fill**, versus a ~$25 touch. The rung is a ~7% add-on to true cost.

---

## 3. Which rung is honest as PRIMARY

**PRIMARY = 25 bps RT; co-primary stress = 50 bps; floor = 0 bps (shown, never a decision
basis); 100 bps = outer display column only.**

Chosen arm's own ladder ($/panel session, 250 val / 332 late sessions):

| rung RT | 0 | 10 | 25 | 50 | 100 | 125 | 150 |
|---|---|---|---|---|---|---|---|
| VALIDATION | 1.729 | 1.583 | **1.364** | 0.998 | 0.267 | −0.099 | −0.464 |
| LATE | 6.602 | 6.341 | **5.949** | 5.296 | 3.991 | 3.338 | 2.685 |

Reasoning, in order of weight:
1. The rung must price what the touch cannot see — 250 ms latency drift, queue/partial
   fills, adverse selection on the exit bid. 25 bps RT is a conservative allowance for
   that bucket; it is a genuine plateau (positive at 25 **and** 50 on both blocks).
2. It is not fee-driven: fees are 0.2–1.2 bps. Anyone quoting "25 bps cost" as broker cost
   is overstating by 20–100×.
3. 100 bps is **over-conservative** and decision-distorting: it charges ~100× measured
   fees and flips the validation cell to +0.27 (from +1.36). Keep it as a stress column.
4. 0 bps is not "free": it omits the ~0.5–1.2 bps fee and assumes zero execution error.
   Use only as a mechanical lower bound.
5. Honesty requirement when reporting: always publish the rung **and** the ~248 bps touch
   beside it, plus fills, median fill, top-5-fill share, months positive, $/session and
   $/calendar-day-span (2023 = 361 d, late = 481 d).

---

## 4. How Alpaca PAPER fills marketable orders (and why paper ≠ our sim)

Documented behavior (https://docs.alpaca.markets/us/docs/paper-trading):
- Orders "are filled only when they become marketable"; "all orders ... will be matched
  against the best available current market price (NBBO)"; "Your order quantity is **not
  checked** against the NBBO quantities ... you can submit and receive a fill for an order
  that is much larger than the actual available liquidity"; "when orders are eligible to be
  filled, they will receive partial fills for a random size **10% of the time**", remainder
  re-evaluated if still marketable.
- Paper explicitly does **not** model: market impact, information leakage, latency
  slippage, queue position (resting limits), price improvement, **regulatory fees**,
  dividends.
- Paper-only accounts are entitled to **IEX market data only**; Basic is the default plan
  "for both Paper and Live trading accounts". `[INFERENCE]` with an IEX-only reference
  quote, paper's "NBBO" is the IEX top-of-book, not the consolidated NBBO our sims priced.

Consequences for judging a paper run of this lane:
1. **Over-fill bias.** Our sim funds only when the intent+250 ms quote is firm+fresh, ask ≤
   limit, *and* displayed ask depth ≥ q, with q ≤ 50% × min(ask size, bid size); exits with
   displayed-bid shortfall become UNKNOWN and are charged a full $1,000. Paper fills 100% of
   size at the touch and can never produce an UNKNOWN. Expect paper fill rate ≈ attempts,
   sim ≈ 86% (122/142 late).
2. **Price optimism.** Paper has no adverse selection and no latency: its entry will look
   equal-or-better than the sim's `entry_ask`, and its exit will always price.
3. **Zero fees.** Paper charges nothing; subtract 0.2–1.2 bps yourself (or reconcile with
   `alpha_provider_fee_ledger`).
4. **Different tape.** If paper runs on free IEX data, decisions and fills are taken on a
   different quote stream than the SIP corpus the model was fit on; the measured
   IEX/free-feed path was negative (`delayed_sip_iex`: val +4.73 $/day, late −1.76 $/day,
   all 12 views negative late).
5. **Therefore:** judge paper on *relative* diagnostics — fill rate vs sim's conditional
   fill rate, count of exits the sim would have called UNKNOWN, realized entry price vs sim
   `entry_ask`, and P&L net of 0.2–1.2 bps fees. Paper P&L is an **upper bound**, never the
   live expectation.

---

## 5. Data plan needed for real-time SIP quotes + trades

- **Algo Trader Plus, $99/month ($1,188/yr)**: equities real-time coverage "All US Stock
  Exchanges" (CTA + UTP), unlimited websocket symbols, no 15-min historical restriction,
  10,000 API calls/min. Basic ($0) = IEX only, 30 websocket symbols, 15-min-delayed
  historical SIP (https://docs.alpaca.markets/us/docs/about-market-data-api,
  https://alpaca.markets/data, https://docs.alpaca.markets/us/docs/market-data-faq).
- No cheaper Alpaca equities tier exists for the Trading API (Basic/ATP only; the
  "Standard*" plans are Broker API, i.e. for running a brokerage).
- Whether a *paper* account inherits the live account's ATP subscription is **not
  documented**; paper-only accounts are documented as IEX-only. Verify empirically
  (`feed=sip` on a paper key) before arming.
- **This is the dominant true cost.** The lane's own cadence: 53 fills/250 val sessions
  (0.1468/calendar day) and 122/332 late (0.2537/calendar day) → 53.6 / 92.6 round trips
  per year → **$22.2/fill (≈2,220 bps of a $1,000 ticket) at validation cadence,
  $12.8/fill (≈1,283 bps) at late cadence.** Annual contribution at 25 bps: val
  252 × 1.364 = $344/yr, late 252 × 5.949 = $1,499/yr. **After the $1,188/yr data bill:
  −$844/yr (validation cadence), +$311/yr (late cadence).** Data opex, not fees, decides
  whether this is worth arming.

---

## 6. PDT / margin constraints for a ~$3,000 account, ~2 RT/week

- **PDT is abolished.** "The definition of a 'pattern day trader' has been eliminated ...
  no longer required to track 'round-trip' counts"; **$25,000 minimum removed**; "unlimited
  day trades" (https://docs.alpaca.markets/us/docs/the-intraday-margin-rule, updated
  2026-07-07; Alpaca changelog 2026-06-03 deprecates `pattern_day_trader` / `pdt_check`).
- Replacement: **Intraday Margin Rule** — Intraday Margin Level (IML) / IML-reducing
  transactions / Intraday Margin Deficit (IMD); call satisfied within 2 business days; a
  90-day restriction on new debits/shorts only if IMDs go unmet past 5 business days; **de
  minimis: no call below $1,000 or 5% of equity**. FINRA allows firms to run legacy PDT
  during a 12-month transition, but Alpaca documents the new rule
  (https://docs.alpaca.markets/us/docs/understanding-finras-new-intraday-margin-rule-and-the-end-of-pdt).
- Standard Reg T still applies: accounts open as margin; **$2,000 equity minimum** for
  margin debits/shorts; ~2× overnight, up to 4× intraday buying power for Reg T ≥ $2,000
  (https://docs.alpaca.markets/us/docs/margin-and-short-selling); margin rate 6.50%/yr.
- At ~2 RT/week (~$1,000 tickets, $3,000 book) **nothing binds**: either a cash account
  (T+1 settlement, no shorts/leverage, proceeds reusable next day) or the default margin
  account works; no PDT counting, no day-trade minimum, no meaningful margin interest.

---

## 7. One-line answer

All-in Alpaca broker cost for a $1,000 marketable small-cap RT is **≈0.2 bps today /
0.3–1.2 bps from 2027** — negligible next to the ~**248 bps** spread the sim already pays;
so **25 bps RT is the honest PRIMARY rung** (a ~20–100× conservative slippage allowance,
verified as a plateau against 50), **100 bps is over-conservative display-only stress**, and
the binding economic constraint is the **$99/mo SIP feed**, which costs more per fill than
the lane currently earns on validation cadence. Paper trading cannot settle any of this: it
fills instantly at NBBO regardless of displayed size, never fails, and charges no fees —
treat paper P&L as an upper bound and compare fill *rates* and *UNKNOWN-would-be* counts to
the sim, not dollars to dollars.
