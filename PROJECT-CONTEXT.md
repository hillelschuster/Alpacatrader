# PROJECT-CONTEXT.md — canonical project anchor

**Project:** top-gainer executable-EV research  
**Repo/branch:** `hillelschuster/Alpacatrader` / `basket-phase2-f1`  
**Purpose of this file:** preserve the actual economic thesis, research mentality, important evidence, and current frontier across model/context resets. **Read this before substantive work.**

---

## 1. Objective

Discover the **highest realistic executable expected value contained in the top-gainer phenomenon**.

The goal is not to defend prior work, maximize benchmark metrics, preserve old strategy families, or make the research look rigorous. The goal is to find where the money is, understand why it is there, and turn it into something causally executable after realistic friction.

The central phenomenon is unusually strong and deserves aggressive discovery:

> **Very early top gainers contain a disproportionate share of the day's eventual extreme winners.**

The original basket idea follows directly:

> At a chosen causal PM/RTH clock, buy a small basket of the top 2/3/4 gainers, because early identification is noisy but the right tail is huge. Then learn how these names actually evolve: keep/reinforce the live claims, stop financing dead ones, possibly replace them with stronger emerging leaders, re-enter resurrections, and hold cash when nothing deserves capital.

This is the research center unless evidence materially changes it.

---

## 2. Mental model / research style

### Discovery first, validation second

There are two different modes and they must not be confused.

**Discovery mode:**
- follow the data and follow the money;
- run many economically sensible probes;
- inspect actual paths and dollar contribution;
- use intuition aggressively;
- let one result generate the next experiment;
- allow simple heuristics, path-dependent ideas, management rules, and combinations;
- use the Atlas as an idea/retrieval engine;
- stop wasting time on dead branches and drill hard into recurring positive-dollar behavior.

**Validation mode:**
- reconstruct causally;
- eliminate lookahead/leakage;
- apply realistic execution/friction;
- check path/day/month dependence;
- verify implementation semantics;
- use truly unseen periods only once something deserves them;
- try to kill apparent alpha after it has actually appeared.

A repeated failure in prior assistant work was dragging validation/falsification logic too far upstream and choking discovery. **Do not repeat that.**

### Evidence hierarchy

Prefer:

`phenomenon → path anatomy → statistical pattern → economic mechanism → executable action`

over:

`arbitrary rule → parameter grid → best cell → story`.

But do not turn that preference into paralysis. If a simple empirical grid directly answers the economic question, run it.

### Rulers, not truths

Fixed times, thresholds, stops, targets, basket sizes, model classes, and re-entry rules are **rulers until evidence earns them**.

A negative result falsifies only what was actually tested.

Do not let simulator/reporting limitations become economic conclusions.

Cash is a legitimate action.

Tail economics matter enormously. Preserve the actual giant-dollar contribution; do not trim away the phenomenon.

---

## 3. Important reliability warning about LLM continuity

The assistant/model has **drifted multiple times** in this project:
- over-focused on recently tested formulations;
- lost the original basket thesis in context rot;
- treated EOD accounting as if it were the economic horizon;
- over-preferred determinism/falsification during discovery;
- over-expanded simple questions into large frameworks;
- repeatedly had to be pulled back toward the actual objective.

Therefore:

> **Treat model recollection/interpretation as roughly 60–80% reliable unless verified against this file or repository evidence.**

This is not a statistical calibration; it is an operational warning. The model is useful for coding, research breadth, skepticism, synthesis, and large-scale reading, but it does **not** possess the user's tacit mental model. When model framing conflicts with strong project evidence or the canonical thesis here, verify before following it.

The user holds the economic intuition; the model supplies research/coding horsepower; evidence gets the final vote.

---

## 4. Core empirical phenomenon already established

### Development universe
- 1,066 dev days.
- 2021-02..2023-12 plus 2025-02..2026-05.
- Sealed/reserved periods exist and should not be casually consumed.

### Early top-gainer concentration
The earlier work repeatedly found that early top-gainer selections contain a disproportionate fraction of later giant runners. Exact definitions vary by family/clock; recover them from repo artifacts rather than relying on rough memory.

Key verified examples from Phase 1:
- A_pm +30/+50/+100 occurrence rates roughly 44.1% / 25.1% / 9.7%.
- B600 +30/+50/+100 roughly 39.6% / 22.3% / 8.0%.
- matched/random B600 +30 was only ~1.65%.
- Market had +50 opportunity on 959/1066 days, +100 on 511, +200 on 146.
- A_pm median best-member/day post-entry MFE ~+26%, p90 ~+97%, p99 ~+310%.
- Individual ticket median MFE ~+10%, MAE ~-13.5%.

Eventual monster capture examples:
- eventual +50: A_pm/570 top3 contains ~27.95%; B600 top3 ~67.36%.
- eventual +100: A_pm ~20.16%; B600 ~49.9%.
- eventual +200: A_pm ~19.86%; B600 ~48.63%.

The user's rough recollection that PM top-gainer sets captured ~30–40% of the eventual leaders is directionally consistent with earlier work, but the exact denominator/clock must be recovered from artifacts before quoting it precisely.

### Important qualitative finding
Information improves with time while optionality decays. Early baskets contain more false claims but also acquire exposure before the future monster becomes obvious.

This tension is the whole economic reason the basket is interesting.

---

## 5. Original basket thesis — keep this visible

The strong thesis is **not** "predict the one winner perfectly."

It is:

> **Select a few of the actual top gainers at a fixed early PM/RTH time, own the race, and then exploit the path evolution.**

Possible actions later:
- hold;
- reduce;
- exit;
- add;
- reallocate among held names;
- replace a dead claim with a stronger current leader;
- re-enter a resurrected name;
- hold cash.

Potential basket sizes 2/3/4 and entry clocks are empirical choices, not doctrine.

The most intuitive immediate experiment is deliberately simple:

> At each chosen PM/RTH clock, take the actual top 1/2/3/4 gainers, execute at the next valid price, and map the equal-dollar basket ride to later fixed clocks. Then study the individual member paths beneath the basket average.

This simple test was surprisingly under-emphasized after the project grew complicated. Do not lose it again.

---

## 6. What earlier management research taught us

The project has already tried many management families. Most did **not** establish incremental executable EV.

Important negative/qualified findings:
- F1 negative.
- F3 release rules improved accounting but remained negative/insufficient.
- F4 partial exits mostly negative; one narrow positive cell existed but not a broad mechanism.
- F5 new-high add: no robust positive result.
- F6 state conditioning improved some equal-redeploy accounting but was unstable / near-zero per-dollar.
- fixed-clock SEGMENT exits generally negative; earlier cuts often reduced losses.
- unconditional derisk sometimes beat damage-only rules, implying time/exposure effects rather than a clean damage separator.
- simple harvest/touch rules improved hold but remained negative/unstable.
- giant runners showed why fixed +30/+50 exits can destroy the right tail.
- giveback10 became a useful simple management ruler, positive versus hold-flat in some accounting, but it still cut later recoveries and is not an optimal policy.

Do **not** infer from these failures that the basket thesis is dead. They close those specific formulations.

### H025
A separate H025/flush mechanism passed true OOS/live-paper work. It is valid evidence in its own lane, but **do not anchor the whole project on it.** The current objective is broader: exploit the top-gainer inefficiency and basket/race phenomenon.

---

## 7. CV01 — latest clean continuation/ownership result

Commit `74fd0ea` on `basket-phase2-f1`.

CV01 built a current-dollar continuation primitive:

`CV = W_hold / W_exit - 1`

where `W_exit` is current next-open liquidation wealth; original entry cost is sunk.

### A — unconditional continuation anatomy
Across 6,160 members / 1,066 dev days, day/family-balanced mean continuation:
- +60 min: about **-0.44%**
- +120 min: about **-0.56%**
- +240 min: about **-0.81%**

Occupancy-weighted values were also negative.

This means: **an arbitrary currently owned basket member does not deserve unconditional continued ownership.**

But the right tail remains enormous. Future giant members contribute huge continuation value. Therefore the economic problem is selection under asymmetry, not blanket holding.

### B — retain vs sell/reclaim/re-enter
At first giveback10 sale, compare:
- retain existing shares;
- sell to cash, wait for a completed close reclaiming the sale price, then re-enter next open.

Retaining beat this simple re-entry formulation by roughly 0.7–1.0 percentage points at the reported endpoints, but **both branches fell below cash after roughly 15 minutes**.

Interpretation:
- continuous ownership has option value relative to a crude exit/reclaim cycle;
- simple price reclaim is not enough "proof of life";
- unconditional hold is still not positive EV;
- giant ownership matters, but we do not yet know how to identify the giant causally.

A useful current question is whether observable state can distinguish transient damage inside a living convex path from prolonged residence in a dying path. But this is only one branch; do not let it displace the broader basket/race discovery program.

---

## 8. Tape Atlas — what it is and why it exists

The Atlas was built because the project was becoming an endless sequence of arbitrary rules without first understanding the movement grammar.

Original evidence order:

> **raw tape → recurring structure / retrieval geometry → empirical lexicon → subsequent path distributions → economic hypotheses → executable rules**

The Atlas is **not a strategy**. It is an empirical memory / retrieval system for the personality of top-gainer runs.

It is intended to help answer:
- what recurring run types exist;
- how fast/slow they move;
- how deep/long drawdowns are;
- how reclaims behave;
- time between highs;
- failed pushes;
- MFE surrender;
- volume/print acceleration and decay;
- relative leader/peer behavior;
- rank evolution;
- eventual death/explosion;
- which historical paths look like the current path.

### Main Atlas substrate
- selected-member panel: ~1.9M rows, 6,160 memberships, 5,905 unique day/ticker paths, 1,066 days.
- raw trades: ~23.9GB, microsecond prints.
- quotes: ~4.1GB / very large row count, selected-symbol coverage.
- full-roster checkpoint board across multiple morning clocks.
- broad minute race board and candidate-net race tiers.

### Representation views
The Atlas built multiple ways to represent the same run:
- magnitude;
- normalized shape;
- duration/event structure;
- activity/microstructure;
- balanced view;
- one learned sequence representation.

Nearest-neighbour / density / recurrence / rarity geometry exists.

Critical caveat: much of the first geometry was **retrospective/full-path** and therefore descriptive, not directly live tradable. For action research, compute genuinely causal prefixes / historical-only reference libraries.

### Why it matters to the basket
The basket gets us aboard the inefficiency. The Atlas can help understand the **personalities of the horses**:
- which leader is strengthening;
- which is merely damaged but alive;
- which is exhausting/dying;
- which outsider is emerging;
- when a supposedly dead name resurrects.

Use it to generate and explain hypotheses, not to constrain discovery.

---

## 9. Atlas discovery results worth remembering

- geometry recurrence differed strongly by view; duration/magnitude/activity had more recurrence than normalized shape.
- views were often nearly orthogonal; magnitude/activity were much more related than shape/duration.
- one learned sequence representation showed strong reconstruction-quality improvement over frozen-random, proving sequence structure is learnable, **not** proving alpha.
- EV-01 found a large retrospective association between balanced-view density and exit-vs-hold accounting, but the descriptor used future tape / full-history neighbour information relative to tested action times. Therefore EV-01 is **retrospective anatomy, not causal alpha**.

Do not promote EV-01 as executable evidence.

---

## 10. Immediate research center / overnight direction

The current instruction is to **harvest EV from the original basket thesis aggressively**.

Before deciding what to run, browse/index/digest the repository deeply so old evidence is not forgotten.

Then follow the data and money.

Priority research families:

### A. Pure basket ride
Across multiple causal PM/RTH entry clocks:
- top 1 / 2 / 3 / 4;
- equal-dollar;
- realistic next-open execution;
- later fixed exit clocks;
- mean, median, downside, hit rate, distribution, giant contribution, day/month stability;
- preserve individual member paths.

This is the cleanest answer to: **where in clock-time is the raw top-gainer basket itself positive or negative?**

### B. Path personality
For those exact selected baskets, inspect:
- winner emergence;
- damage tolerance;
- new-high process;
- time between highs;
- failed pushes;
- recovery/reclaim quality;
- rank loss/recovery;
- peer behavior;
- print/volume/activity acceleration or decay;
- exhaustion / resurrection;
- how the future monster differs, if at all, before becoming obvious.

### C. Simple causal management
Then test many intuitive, economically connected ideas, for example:
- drop clearly deteriorating member;
- partial reduction of weakest claim;
- retain/reinforce strongest survivor;
- redistribute freed cash among survivors;
- replace weakest with stronger current top-gainer outsider;
- re-enter after genuine resurrection;
- cash when no claim deserves exposure;
- compare N=2/3/4 and different PM/RTH start clocks.

Do not mechanically enumerate thousands of parameter cells. There may be thousands of possible expressions of the thesis; **follow promising dollar behavior recursively.**

When a run makes money:
1. identify exactly which members/days created it;
2. inspect the paths;
3. understand the observable precursor;
4. design the next smallest discriminating test;
5. only later harden causality/validation.

When a branch repeatedly loses, stop spending time on it.

### D. Dynamic race later / in parallel when justified
Potential eventual architecture:

`early basket → observe live race → cut dead claims → preserve/reinforce convex survivors → replace weakest with stronger emergent leader → re-enter resurrection → cash when nothing deserves capital`

This architecture is a hypothesis, not yet earned policy.

---

## 11. Important anti-drift rules

Before an important conclusion, ask:

> **Does this answer the economic question, or merely explain the experiment we happened to run?**

Never silently let these substitute for one another:
- economic thesis;
- experiment;
- implementation;
- reported metric;
- actual evidence.

Specific prohibitions:
- do not treat EOD as the strategy merely because the simulator marks terminal wealth there;
- do not let H025 become the default center;
- do not let the latest Atlas/ML artifact redefine the objective;
- do not confuse descriptive hindsight anatomy with causal actionability;
- do not turn every simple idea into a giant framework;
- do not over-guardrail discovery before an edge even appears;
- do not burn reserved data on exploratory anatomy;
- do not optimize classification metrics instead of executable dollars;
- do not trim away the giant tail that creates the phenomenon.

---

## 12. Implementation/evidence discipline

Implementation bugs, leakage, stale artifacts, wrong execution semantics, duplicate sleeve accounting, wrong universe/ranking, future-neighbour leakage, and fake alpha are first-order findings.

When material, inspect code and data lineage.

Known duplicate issue: A_pm/B600 can overlap on the same `(day,ticker)`; pooled analysis must account for independent paths / duplicate memberships appropriately.

Execution should use realistic next executable prices and friction; distinguish action time from fill time.

Prospective rosters/ranks must use only information available by the decision instant.

---

## 13. Key repository anchors

Read as needed:
- `AGENTS.md`
- `researches/INTENT.md`
- `researches/STATE.md`
- `HANDOFF.md`
- `researches/ATLAS-PHASE1-SYNTHESIS.md`
- `researches/ATLAS-PHASE2-CLOSURE.md`
- `researches/PLAN-TAPE-ATLAS.md`
- `researches/PRE-REG-TAPE-ATLAS-EV01.md`
- `factory/artifacts/basket/phase2/ATLAS/EV01/`
- `factory/artifacts/basket/phase2/ATLAS/CV01/report.md`

Relevant commits mentioned in recent work:
- `14e376d...` — observation/storage/race architecture
- `6d3817b` — race v2 + EV-01 prereg
- `0bbc398` — EV-01 execution
- `74fd0ea` — CV01 current-dollar / ownership-continuity diagnostics

Recover exact historical PM/top-gainer containment artifacts from the repo before making precise percentage claims.

---

## 14. Communication contract

Keep answers short by default.

Lead with the answer.

Do not restate the full history unless needed.

For research review:
- state exact alignment/drift briefly;
- distinguish verified fact / result / interpretation / hypothesis / ruler / untested idea;
- say plainly when evidence contradicts prior framing;
- prefer one decisive measurement over ten pages of prose.

The project does not need more activity. It needs **maximum useful reasoning per unit of context**.

---

## 15. One-sentence reset for a new conversation

If all other context is lost, start here:

> **We have strong evidence that very early top-gainer sets contain disproportionate future convex winners; the current mission is to exploit that by systematically studying and trading small fixed-clock top-gainer baskets, using the Atlas/raw tape to understand the life cycle of winners and failures, following actual executable dollars aggressively in discovery, and only then hardening the surviving mechanisms with causal validation.**
