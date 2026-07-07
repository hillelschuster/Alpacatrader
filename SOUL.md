# SOUL — Alpacatrader Identity

> Read this to understand *what kind of trader this bot is* before touching entry, risk, sizing, scanner, or exit logic.
> For implementation authority, see `SPEC.md`. SOUL.md is identity, not a spec.

## Identity

Alpacatrader is an **attention-first, top-gainer momentum bot** built to **catch runners** — sustained directional moves with real volume. It thinks like a seasoned retail momentum trader: the top gainers on the tape are the day's best opportunity, and the bot's job is to find a safe way to participate in them, not to invent reasons to skip them.

## Core Belief

**Top gainers are candidates first, not suspects first.**

Every top gainer is an option to trade. The bot seeks a participation path when logic, risk, and execution align. Filtering must be selective and intelligent — never fear-based. A stock up 20% with real volume and a clean setup stays a live candidate. Risk controls protect the account; they do not erase the edge.

```text
Top gainer + attention + definable risk + verified execution safety = possible trade.
```

## Operating Principles

1. **Candidates first.** Qualitative labels (Chinese ADR, biotech, low float, no news, speculative, parabolic look) are sizing/confidence inputs — never hard rejections. The only hard blocks are mechanical execution-safety failures (stale quote, crossed/wide spread, halt, loss caps, market-hours, impossible risk definition, broker safety). Missing non-critical data reduces confidence or size; it does not declare a stock untradeable.

2. **Attention is the primary edge.** The bot trades attention — price gain + volume/RVOL + HOD acceleration — not fundamentals, not analyst ratings, not news sentiment. Sizing scales with attention. Candidates are ranked by attention before processing. Missing attention data is a confidence reduction, not a trade killer.

3. **Defined risk, every trade.** Every entry has a logical stop, risk-per-share, dollar risk, and share count — computed *before* the order is submitted. Stop protection must be **verified** against live/pending broker truth, never assumed from local metadata. Risk is bounded first; opportunity is sized within that bound.

4. **Protection without paralysis.** Daily loss cap, per-symbol loss cap, open-risk cap, and max-trade-risk cap are guardrails, not trade-avoidance devices. If the bot "never loses" because it sizes every trade to irrelevance, it has failed its mission. Sizing penalties exist to protect the account, not to avoid trading. A floor prevents zero; the target sizing for a typical top gainer (attention ≥ 70) should be meaningful, not microscopic.

5. **Runners are the goal; scaling-in is the mechanism.** The bot exists to catch runners, not just to scalp single entries. Scaling *in* when a position proves itself matters as much as scaling out. **This is central to the identity but deferred, not abandoned** — see Current Reality below. Future sessions must not treat single-entry-and-scale-out as the final design.

6. **Paper mode is live rehearsal, not a sandbox.** Paper mode must behave as close to live as data/tool limits allow: real market hours, real spread/slippage gating, real decision logging, real reconciliation against broker truth. Sloppy paper mode becomes dangerous live mode. Treat every paper trade as a live rehearsal.

7. **Data failure is explicit, never silent.** Missing data is a state, not a price. The bot never fabricates `price=0.0`, never computes P&L from synthetic data, and never manufactures bearish structure (e.g. BACKSIDE) from missing VWAP. Data gaps reduce confidence/size or produce machine-readable skip reasons — they do not masquerade as trading signals.

8. **LLM is a pre-market annotator, never an execution-path component.** Trading decisions remain fully deterministic and rule-based — no LLM approval, no AI override, no black-box model in entry/exit/sizing. LLM enrichment runs before market open as a batch process, is disabled by default, and its output is annotation-only — never a gate, filter, or sizing input. LLM failure degrades to "no enrichment" (current behavior). This is guaranteed by `SPEC.md §1.3` and `§11.8`. Future agents must not silently introduce LLM dependency into the decision loop.

## Current Stack (verified from code)

- **Broker / market data:** Alpaca (paper mode target). Live quotes + 1-min bars via Alpaca/IEX free-tier paths. `AlpacaExecutionGateway` routes real paper orders; no synthetic fills in broker mode.
- **Scanner:** Finviz free-tier top-gainer scrape (primary). Stale/empty Finviz → `yfinance` fallback. Float enrichment via `yfinance.Ticker(...).info.get("floatShares")`.
- **Decision path:** scanner → enrichment → snapshot validation → attention ranking → data confidence → soft annotations → mechanical hard filters → move classification → entry detection → risk sizing → pre-submit quote recheck → order submit → fill confirm → verified stop protection → decision log.
- **Sim path:** off-hours historical-bar simulation (yesterday's last 2h), shared enrichment math with live path.
- **Tests:** pytest suite, 834 passing as of v0.4.0 remediation commit `386a47e`.
- **Live mode:** disabled unless `TRADING_LIVE_TRADING_CONFIRMED=yes_i_accept_the_risks`.

## Current Reality (honest gaps)

The soul is correctly specified but not yet fully realized. These are known limitations, not contradictions:

- **Runner capture is not implemented.** Runner state exists in the schema but no runtime transition creates a runner. Runner trailing accepts `highest_price_seen` but does not use it. v0.4.0 is a single-entry momentum bot wearing a "catch runners" label. This is honest *if acknowledged*; it is dishonest if shipped as final. **v0.5.0 delivers runtime runner transitions, ATR Chandelier trailing exits, and scaling-in — see `SPEC.md §11`.**
- **Scaling-in is unimplemented.** No module, no state transitions, no sizing logic for adding to winning positions. Specified in the mental model (`SPEC.md §1.1`), deferred in `SPEC.md §10.1`.
- **yfinance fallback is a static watchlist**, not dynamic top-gainer discovery. When Finviz is unavailable, the bot degrades from "today's top gainers" to a curated basket of volatile names. This is a fallback, not a substitute. The primary edge comes from Finviz-driven discovery. Future: wire a second dynamic scanner source, or accept that data-absent days are watch-only days.
- **News/catalyst awareness is unwired.** `has_news`/`has_catalyst` are never populated at runtime, so the attention-dependent `no_news`/`no_catalyst` sizing penalty never fires; candidates always receive `news_unknown`/`catalyst_unknown` (1.0x). Acceptable v0.4.0 simplification — top-gainer momentum is the primary signal — but the declared penalty is currently dead code.
- **Sizing can crush to trivial size.** Stacked multipliers (attention × float_unknown × parabolic × lunch × price_below_2 × data_confidence) can reduce a $250 starter risk to single-digit dollars before the 0.25 floor. If the bot watches far more than it trades, the multipliers need recalibration, not the philosophy.
- **Paper-mode realism gaps.** No US holiday calendar, no pre-market scanning, no half-day early-close handling, 60s poll (not true sleep-until-open), and `paper_mode` flag is stored but never gates behavior. Holiday integration and a live `paper_mode` distinction are live-mode prerequisites.

## Future Direction

- **v0.5.0 mandate:** runner capture + scaling-in + live readiness. See `SPEC.md §11` for the full researched implementation plan (7 phases, dependency-ordered).
- **VPS running 24/7:** wake 30–60 min before market open, monitor premarket/top gainers, trade market hours with discipline.
- **SIP data feed** (~$99/mo) if the cost/logic tradeoff is worth it for execution quality. Deferred to v0.6 — test with IEX first.
- **Broker/data stack flexibility:** Alpaca is the current target; if IBKR or another stack becomes economically/logically better later, the system may move — but for now it must fit Alpaca perfectly.
- **LLM ecosystem:** pre-market catalyst annotation (Anthropic Claude Haiku, ~$0.03/day, disabled by default, annotation-only). See `SPEC.md §11.8`. Never in the execution path, never an approval gate.

## Authority

- `SPEC.md` is the sole active implementation spec. SOUL.md is the identity/mental-model doc; it does not override or compete with SPEC.
- If intent here is ambiguous and not explicit in code, SPEC, user instructions, or verified docs — **ask the user**. Do not invent major trading philosophy from a single inference.
