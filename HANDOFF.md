# HANDOFF — Flush-Bid Top-Gainer Mechanism

**Written 2026-09-11, end of day-1 live paper session. Read this file completely before doing anything.**
You are the next agent continuing a research→live pipeline that just achieved its **first true pre-registered
out-of-sample pass** and is now in the **live paper-trading validation phase**. Your job: run and monitor the
live bot, accumulate paper fills, verify fill realism vs the sim, watch for decay, and only then consider
tiny real-money sizing. Protect the research discipline above all: **profitability is the only goal.**

---

## 0. TL;DR — where things stand

- **The mechanism**: extreme top-gainer leaders that show a fresh, thrusting state get a resting −10% bid;
  when a fast flush hits the bid, hold for a snap-back toward the pre-flush price with a hard stop and a
  30-bar time limit. Sell-all-at-c0 beats scale-out; tight stops/scale-outs/most "improvements" lose money.
- **Research status**: frozen rule `PRE-REG-FLUSH-01.md` passed **untouched OOS** (2024-01..2025-02):
  primary subset (prior_flush>=2) **n=381, +1.14%/trade net of 100bps, 12/14 months positive** (dev was
  +1.20%, 15/18 — replication at ~95% magnitude). This is the project's first genuine OOS pass.
- **Live status**: `flush_bot.py` v2 armed (`live:true`, paper) since 2026-09-11; day 1 produced **zero fills**
  (correct behavior — no ≥+100% fresh+thrust qualifier while the pipeline was healthy; two qualifying windows
  were missed by feed bugs that are now fixed). Account flat: 0 positions, 0 orders, equity ~$99.3k.
- **Your immediate job**: Monday session monitoring → post-close parity + ledger → accumulate 30+ fills →
  compare live vs the **A3b baseline** (§14: pf2 +1.13%, 10/14, ~19/mo, worst −3.1%) → decay dashboard →
  (then) tiny live money decision. The ledger's `summary_by_tag` partitions fills by `pf_est`/`n_strict_est`
  (forward test of H029) and prints the A3b row.
- **Latest state: §17 (2026-10-08) + §18 (2026-10-09).** Sections §13–§16 are historical (the §16
  forward-paper plan for the flush rule is void — see §17); where they conflict with
  §17/§18, those sections govern.
- **Context discipline (user-instructed)**: **compress big and often.** See §10. Do not let the window balloon.

---

## 1. The objective (never lose sight)

Find and exploit a profitable top-gainer/momentum trading mechanism. **All research, tooling, and process
exist only to help find, validate, or execute an edge.** The user is the decision maker; follow explicit
instructions strictly, deviate only for a clearly better route toward profitability, and say why briefly.
The current mechanism (flush-bid) is the most validated thing the project has ever had. Your job is to
convert that into live evidence, without wrecking the discipline that produced it.

---

## 2. The mechanism — exact frozen spec (do not "improve" it)

Source of truth: `researches/PRE-REG-FLUSH-01.md`. Summary:

1. **Universe**: PIT US listed stocks (NASDAQ/NYSE/AMEX), junk suffixes excluded
   (`data/pit/pit_symbols.parquet`, vintages 2023-11..2026-08; `eligible_set(day)` in `tv_leaderboard.py`).
   Live: Alpaca movers filtered to latest PIT vintage + price floor **$2** before ranking.
2. **Causal top-3**: rank by gain vs immediately-previous session close, desc, computed only from data
   observable at minute t (decision uses last completed bar = `et <= minute_now - 1`; never future info).
3. **Strict state minute** (all three must hold at the same completed bar, rank<=3):
   `gain >= 1.00` AND `pullback = c/cummax - 1 >= -0.01` (fresh) AND `r15 = c/c_prev15 - 1 >= 0.03` (thrust).
4. **Rolling bid**: while flat, resting BUY limit at `B = 0.9 * close(latest strict-state minute)`,
   anchor/expiry refreshed at every strict-state minute, expires `WIN=120 min` after the last refresh.
   *Live execution choice*: the broker order is replaced only when the executable tick moves — refreshing
   the order every minute would destroy queue priority (backtest has no queue model). Documented in code.
5. **Fill**: first **new** bar (real print; not ffilled) with `low <= B`; fill assumed at B.
6. **Exit (tl30)**: stop `0.9*B` checked first (exit at `min(open, stop)`), else target `c0` (the pre-flush
   close; limit sell), else after **30 completed bars** exit at close. 100bps friction in all sims.
7. **Re-arm**: only after the previous order resolves (exit or expiry), one order per ticker at a time.
8. **Live guards**: max 3 concurrent (positions + resting buys), fixed `NOTIONAL=$2,000/trade`,
   no new entries after **15:30 ET**, flatten + cancel at **15:55 ET**, `data/KILL` kill switch.

**Fidelity choices already made deliberately (do NOT "fix" these without a new pre-reg):**
- Tick-only broker replacement (above).
- Live rank applies to the latest completed bar's state (no rank history available live).
- On restart, owned resting buys are cancelled (next state minute re-places).

---

## 3. Evidence base — know these numbers cold

**Pre-registered OOS (2024-01..2025-02, 14 untouched months, IEX/SIP mix pipeline unchanged):**

| subset | n | mean net/trade | median | months+ | worst month |
|---|---|---|---|---|---|
| **pf>=2 (primary)** | **381** | **+1.14%** | +4.74% | **12/14** | −2.03% |
| all fills | 541 | +0.91% | — | 11/14 | −2.51% |
| rank1 | 443 | +0.76% | — | 11/14 | — |

Dev span (2025-03..2026-08, seen): pf2 n=658 +1.20%, 15/18; all n=937 +0.39%, 11/18; rank1 n=713 +0.96%, 13/18.
Parity check: the OOS runner reproduces `lb18_roll.json` dev stats **exactly** — keep that invariant.

**Execution realism (pooled 1,478 fills, 32 months):**
- Clean touch fills n=1363 mean **+1.27%** (27/32 months+); gap-through fills n=115 mean **−7.64%** (4/29).
  Gap share 7.8%. The edge is a broad plateau, not a knife-edge (stop −10/−12/−15 → +0.58/+0.61/+0.62;
  tl 20/30/45/60 → +0.53/+0.58/+0.42/+0.40).
- Stop breaches >−11%: 135 fills (9.1%); worst: AFJK −35.9%, CRML −27.5%, JLHL −26.8%. Stops are NOT a hard
  floor when price cascades through them (no missing-bar-halt class; it's fast collapse).
- **Capacity**: fill-bar dollar volume p25/med/p75 = $1.67M/$3.45M/$6.40M; at 5% participation ≈
  $84k/$173k/$320k per fill. At 10% ≈ $345k median. Small-size feasible; institutional not.
- **Decay watch**: all-fills H1 (2024-01..2025-06) +1.08% vs H2 (2025-07..2026-08) −0.05%. Monitor monthly.
- **Microstructure (OOS 541 fills, SIP trades)**: market traded at/through our bid in **98–99%** of fills;
  median 52.6k shares at/below bid; median dwell 15.3s (clean 8.8s; gap 22.6s; strong 4.5s). NBBO spread
  median $0.10. **Queue position is the one unmeasured assumption** — only real orders can settle it.

Selectivity: ~1.9 fills/day market-wide (all), ~1.3/day pf2 — this mechanism is **rare by design**.

---

## 4. Live systems inventory

| system | file | what it does |
|---|---|---|
| **flush bot** | `factory/scripts/flush_bot.py` | The live paper trader (v2, ~660 lines). TV/Alpaca candidates → IEX bars → strict state → resting bids → OCO exits → tl30 → guards. Journals to `data/forward/bot/<ET-day>/journal.jsonl`. `--live` sends paper orders; default dry-run; `--probe` logs state lines only. |
| bot supervisor | `factory/scripts/flush_bot_supervisor.sh` | git-bash loop; relaunches bot within 5s of exit; passes `--live` iff `data/forward/bot/LIVE` exists; logs `logs/flush_bot*.log`. |
| observer | `factory/scripts/forward_observe.py` | Live scanner/movers poller + minute-bar logger (SIP→IEX for history; IEX-first intraday). Writes `data/forward/<day>/{scans,bars,state,promotions,deep}.jsonl`. |
| observer supervisor | `factory/scripts/observe_supervisor.sh` | Same pattern for the observer. |
| parity replay | `factory/scripts/flush_bot_parity.py` | Rebuilds the day from the bot journal + bars, runs the frozen engine, diffs expected vs journal fills. **Approx** (documented list: one-bar scan timing, guards not replicated). |
| ledger | `factory/scripts/flush_bot_ledger.py` | Per-trade P&L from Alpaca paper orders joined to journal intents; filters foreign fills by journal symbol-days; ET-day attribution; round-trip friction once. |
| bars backfill | `factory/scripts/forward_backfill_bars.py` | Fetches Alpaca bars for a past day (IEX for today, SIP for history). |
| mock tests | `factory/scripts/test_flush_bot.py` | 7 offline lifecycle tests (refresh-on-tick, expiry, protect, tl30, exit bookkeeping, day-roll, clock fallback). |
| research pipeline | `factory/scripts/lb18.py`, `lb18_episodes.py`, `lb18_oos.py`, `lb18_roll.py`, `lb18_exec.py`, `lb18_fills_micro.py` | Historical leaderboard build (377 days, `data/leaderboard/`), episode/lifecycle engine (`run_engine`/`sim_tl30` are canonical), OOS runner, execution-realism studies. |
| legacy scanner | `factory/scripts/tv_leaderboard.py` | TradingView scanner replay + `eligible_set(day)` PIT. Live TV rows went stale/divergent → now a fallback only. |

Flags & switches:
- `data/forward/bot/LIVE` — arming flag. Present = live paper orders. Remove = dry-run (supervisor relaunches).
- `data/KILL` — kill switch. Bot exits within ~60s; remove it and the supervisor relaunches in ≤5s.
  **Any code edit to flush_bot.py requires this KILL-toggle restart** (the supervisor only restarts on exit).

---

## 5. Environment & exact commands

- Repo root: `/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader` (Hebrew path — always quote it). WSL Linux.
- **Research scripts (WSL)**: `export PATH="$HOME/.local/bin:$PATH"` then
  `uv run --python 3.11 --with pandas --with pyarrow [--with alpaca-py --with python-dotenv] python <script>`.
- **Live scripts (Windows venv)** — the bot/observer run under the supervisor's interpreter:
  `/mnt/c/Users/הלל/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe` (has loguru, pandas, dotenv,
  alpaca data+trading). Invoked via git-bash by the supervisor scripts.
- **Launch a supervisor from WSL** (the start title MUST contain a space, or WSL interop strips quotes and
  cmd fails with "cannot find the file"):
  ```
  cmd.exe /c start "flush bot" /min "C:\Program Files\Git\bin\bash.exe" -lc "bash '/c/Users/הלל/Desktop/algo projects/Alpacatrader/factory/scripts/flush_bot_supervisor.sh'"
  ```
  Do NOT append more commands in the same WSL call — it hangs (launch still succeeds). Check logs separately.
- **KILL restart**: `touch data/KILL` → wait ≤60s (bot logs `kill_file`, exits) → `rm data/KILL` →
  supervisor relaunches ≤5s. Verify: supervisor log tail + journal `start` event with `live:true`.
- **Health check (one-liner set)**:
  ```
  J=data/forward/bot/$(TZ=America/New_York date +%F)/journal.jsonl
  rtk read "$J" --tail-lines 5
  rtk read "$J" | grep -c '"event": "error"'
  rtk read logs/flush_bot_supervisor.log --tail-lines 3
  ```
  (Fall back to `tail`/`grep` if `rtk` misbehaves.)
- Market hours: **09:30–16:00 ET = 16:30–23:00 IDT**. The user is often absent during the session — be
  autonomous, report via the journal + commits.

---

## 6. Alpaca / data quirks already paid for (do not rediscover)

- **SIP intraday is sparse on this plan** (~2 stale rows; full history works). Intraday → **IEX first**,
  SIP fallback for gaps/history. This exact bug blinded the bot on day 1 (zero bids until fixed).
- Daily SIP bar queries are blocked ("subscription does not permit querying recent SIP data").
- This `alpaca-py` version has **no `get_account_activities`** (use `get_orders`).
- `cancel_order_by_id` returns **None** — never branch on its return; re-poll order status instead.
- Trading API `/clock` can 500 for extended periods (observed 46 min on day 1); bot now retries + falls back
  to a local ET session clock.
- Paper account contains **54 foreign fills (June/July 2026, not ours)** — the ledger filters fills to
  symbol-days present in the bot journal; keep that filter.
- TV scanner rows can be stale/divergent vs Alpaca (day-1: SWRD TV +59.6% vs Alpaca +128.9%) → Alpaca movers
  are the primary candidate source; TV is fallback. Movers are full of warrants/rights/units → filter to PIT
  + $2 **before** ranking (rank<=3 gate depends on it).

---

## 7. WSL / tooling landmines

- Detached processes (`nohup`/`tmux`/`setsid`) get **reaped between tool calls**. Long jobs: run in
  ≤14-minute foreground chunks with **per-day incremental, resumable writes** (this pattern is baked into
  `lb18.py` and friends), or accept a chunked run.
- `/mnt/c` parquet reads are ~10x slower than ext4 — stage big files to `/tmp/opencode/...` first
  (`H12_STAGE` env var pattern).
- LSP diagnostics on pandas/alpaca-heavy files are a known **false-positive class** (basedpyright missing;
  NDArray `.median/.quantile`, alpaca stub attrs). Verify with `python -m py_compile` + runtime tests.
- `logs/forward_observe.log` may contain non-UTF8 bytes; use `grep -a` / `tail -c`.
- Disk budget: user cap ~30GB additive; currently ~41G used of 1TB total. Delete raw months after cleaning;
  delete staged /tmp copies per month.
- Never restart the **observer** mid-session (duplicate promotion rows); restart it post-close.

---

## 8. Research discipline (non-negotiable — this is what produced the OOS pass)

1. **No lookahead, causal only.** Decision at minute t uses completed bars `et <= t-1`; fills on new bars.
2. **Pre-register before computing.** Any new rule/hypothesis (including "improvements") gets a pre-reg file
   with exact spec, gates, and untouched data blocks, committed **before** the first number is computed.
   See `researches/PRE-REG-H12.md` and `PRE-REG-FLUSH-01.md` for the format.
3. **Month-blocked, pooled evidence.** ≥6 months pooled dev, ≥2 pre-registered unseen collision months,
   **100bps friction**. Single-month positives are noise until proven otherwise.
4. **Do not tune on seen data.** All 32 months (2024-01..2026-08) have now been seen for the flush rule.
   The ONLY valid tests from here are: live/forward evidence, or genuinely new data as it arrives.
5. **Failures are recorded, not hidden** — `HYPOTHESES.md`, `STATE.md`, `EXPERIMENTS.jsonl` (append),
   `HYPOTHESES.jsonl`. Then commit + push (the user expects origin current).
6. **One variable at a time.** Conditioning stacks on n≈400 overfit; the rule survived because it was frozen.

---

## 9. Your immediate roadmap

*(Partly superseded by §16 — 2026-09-13. The session-day routine below still applies.)*

**Monday 2026-09-14 (and every session day):**
1. Pre-open (~16:15 IDT): health check — supervisor alive, `LIVE` flag present, no `KILL`, journal polling,
   account flat, zero errors. Confirm the bot's process start event is `live:true`.
2. During session: periodic journal spot-checks. Watch for: `place_bid` events (B = 0.9*state close, qty,
   `flushbot-` client_order_id), `refresh`/`expire` discipline, `fill` → `oco` (stop 0.9B / target c0),
   `tl30_exit`, `exit`, and any `error`. Investigate any error the same day.
3. Post-close (~23:15 IDT): run
   `flush_bot_parity.py --day <YYYY-MM-DD>` (label results **approximate** until the faithful replay
   exists) and `flush_bot_ledger.py`; commit artifacts; update `factory/STATE.md` + `EXPERIMENTS.jsonl`.
4. **Accumulate to 30+ fills** (may take many sessions — the rule is selective). Track vs sim profile:
   mean ≈ +0.9..+1.2% net/trade, median ≈ +4.7%, wins at +11.1% (c0), losses −10%, gap-through ≈ 8%.
5. **Decay dashboard**: monthly per-fill stats; compare against the H1/H2 split (already softening in H2 —
   watch, don't panic).
6. **Fill realism**: partial fills, fill vs bid, OCO behavior, queue reality. The microstructure tooling
   (`lb18_fills_micro.py`) shows the tape supports fills; real orders are the only ground truth.

**Conditional / later:**
- Faithful live-loop parity replay (one-bar timing + POS_MAX/15:30/anchor guards) if the approximate
  reconciliation proves insufficient once fills exist.
- After 30+ fills match the sim: decide **tiny real-money sizing** (start far below 5% of fill-bar volume;
  median 5% capacity ≈ $173k). Consider a gap-through guard as a **new pre-registered** refinement —
  never retrofitted to seen data.
- Phase 2 (after accumulation): sub-minute SIP-trades pilot (5/15/30s aggregation around fills) to measure
  fill realism + exit timing. Treat as measurement first, alpha second.

**Do NOT (without an explicit user request and a new pre-reg):**
- Tune exits/stops/trails/tl on seen months. Tune the state thresholds. Expand/replace the population.
- Revive pre-bid ML (three prior attempts died; post-fill management ML is the only unexplored ML angle).
- Add frameworks/infra. The bot is intentionally small. Keep it that way.

---

## 10. Context discipline — COMPRESS BIG AND OFTEN (user instruction)

The user explicitly wants the next agent to **make big compressions along the way**:

1. **Compress after every closed arc** (a finished investigation, a deployed fix, a completed analysis).
   Do not wait for the session to get huge.
2. Use the `compress` tool on raw tool-output ranges aggressively; keep only active work uncompressed.
3. Write **dense, self-contained compression summaries**: file paths, function names, exact numbers,
   commit hashes, decisions, gotchas. The summary becomes the authoritative record — the repo files
   (`STATE.md`, `HYPOTHESES.md`, artifacts) are the durable one. If it matters, it should be in BOTH.
4. Keep the live state small in context; the journal + repo are the memory, not the conversation.
5. Batch independent compressions in one call when several closed ranges are ready.

The previous session ran long; a fresh session with this handoff + repo memory should start near-empty and
stay lean by compressing each hour of work.

---

## 11. Key files & artifacts index

```
researches/PRE-REG-FLUSH-01.md        # frozen rule + gates (the contract)
researches/HYPOTHESES.md              # living hypotheses; see 2026-09-09..2026-09-11 sections
researches/STATE.md                   # leaderboard contract + current truth
factory/STATE.md                      # operational chronicle (read the tail!)
factory/EXPERIMENTS.jsonl             # EXP-49..61 cover the OOS pass + live day 1
factory/HYPOTHESES.jsonl              # H025 = flush rule, OOS-PASS-PAPER
data/pit/pit_symbols.parquet          # PIT universe 2023-11..2026-08 (3.94M rows)
data/leaderboard/                     # 377 days of lb_/path_ parquet (research population)
data/forward/bot/<ET-day>/journal.jsonl  # LIVE bot journal (source of truth for live behavior)
data/forward/<day>/*.jsonl            # observer scans/bars/state
factory/artifacts/lb18_oos_oos.json   # OOS PASS evidence (+ .parquet fills)
factory/artifacts/lb18_oos_dev.json   # dev parity evidence
factory/artifacts/lb18_exec.json      # execution realism (fills/capacity/tails/decay)
factory/artifacts/lb18_fills_micro.*  # SIP microstructure around historical fills
factory/artifacts/flush_bot_parity_*.json / flush_bot_ledger.json  # live reconciliation
```

Recent commit map (origin/main): `33e2943` pre-reg → `8c8f3d2` OOS runner → `848d15d` **OOS PASS** →
`1da7bd1` micro → `45acbde` bot build → `88cff8d`/`51923da` launch+hardening → `5ff96fb` armed →
`c434921` audit fixes v2 → `2cb48c5` parity/ledger → `5f6dcc5`/`c45b07f`/`bcd9b02` day-1 feed fixes →
`32dd271` clock fallback → `de1a6d5` day-1 record → `a999dd4` tests → `cccf593` post-close scan gate.

---

## 12. How to behave with the user

- Concise, direct, no fluff. Report: what changed, where, evidence (numbers), next action.
- The user is the decision maker; when you propose something new, propose it plainly and wait.
- Never present a win that isn't pre-registered + untouched; the user values the discipline as much as the edge.
- When in doubt: **follow the money** — but never at the cost of the integrity rules that produced it.

Welcome aboard. The mechanism passed OOS. Now find out if the market actually pays for it.

---

## 13. v2.1 execution fixes (2026-09-12, market closed) — read before trusting live numbers

Advisor audit → 5 material fixes in `factory/scripts/flush_bot.py`; the frozen
alpha is untouched. Mock tests T1–T10 (11/11) in `factory/scripts/test_flush_bot.py`.
A `flush_bot.py` edit still requires the KILL-toggle restart (supervisor only
restarts on exit).

| What | Before | After |
|---|---|---|
| r15 parity | `shift(15)` on raw IEX rows = 15 printed bars | forward-filled 1-min grid = 15 CLOCK minutes (matches `lb18.py:97-103`) |
| tl30 parity | count of IEX bars from fill | 30 clock minutes from `filled_at` (≈30 new bars on the complete tape) |
| partial fills | working orders skipped; remainder unprotected | `sync_fills()` inspects each poll, cancels remainder, books the fill, protects held qty; `protect_resize` if the remainder slips in before the cancel |
| restart | in-memory meta; orphan risk | `startup_reconcile()` cancels owned resting buys, re-adopts positions, rehydrates entry_B/c0/ts from journal + broker |
| KILL | exit only | cancel owned entry buys, LEAVE protective OCO sells, warn unprotected, exit (idempotent on relaunch) |

`pf_est` now tags `place_bid`/`fill` with the causal prior-flush count
(IEX-undercounted, TAG only — never an entry gate). Paper deliberately trades
the broad population; partition fills ex-post into pf0 / pf1 / pf>=2. The frozen
pf result is unchanged; a corrected pf definition is a separate research variant.

Production-overlay measurement (seen data, NOT an alpha claim):
`factory/artifacts/lb18_overlay.json` (+ `factory/scripts/lb18_overlay.py`).
Frozen parity reproduced exactly. Overlay drops 30/541 fills (5.55%, all late-day,
`tf>=15:30`), flatten changes 0 exits, POS_MAX rejects 0 (concurrency never
binds). pf2 retained: n=355, +1.23%/trade, 12/14 months, 25.4 fills/month
(93% of frozen frequency). So the production policy does NOT degrade the edge —
if anything the 15:30 cutoff drops net-negative late fills. Do not retrofit that
observation as a refinement.

### Terminology (advisor #8)
Micro artifact "gap" = fill-bar close BELOW the bid (`fc<0`, i.e. traded through
B). Execution-study "gap-through" = the bar gapped past B so the resting bid
never controlled the fill. Related concepts, not the same; no bot bug.

### Ownership
The paper account is assumed DEDICATED to this bot; any position found is
adopted. If that assumption breaks, adopt only journal-attributed symbols
instead. Known caveat: 54 foreign paper fills Jun/Jul 2026 (filtered by
journal symbol-days).

---

## 14. Study A decomposition (2026-09-12) — what the live IEX feed actually costs

The frozen engine was replayed with independently sourced tapes (Amendment A1,
`factory/scripts/lb18_iex_hybrid.py`; artifact `lb18_iex_hybrid.json`). Parity
(frozen/frozen) is exact.

| variant | pf2 n | pf2 EV | months+ | missed_pf2 |
|---|---|---|---|---|
| frozen reference | 381 | +1.14% | 12/14 | 0 |
| **A3b (IEX state+B, consolidated exec) — deployable live model** | **282** | **+1.13%** | **10/14** | **77 (20%)** |
| A3a (IEX state, frozen B, consolidated exec) | 267 | +1.59% | 9/14 | 64 (17%) |
| A4 (frozen state+B, IEX exec) — control | 335 | −3.03% | 1/14 | 89 (23%) |

- A4 shows Study A's original failure was IEX-only *fill detection* — and that is
  NOT the live model (the bot's order rests at the broker and fills on the
  consolidated tape).
- A3b is the live model: **per-trade pf2 EV is preserved (+1.13% vs +1.14%)**,
  but ~20% of frozen pf2 fills are never taken and months+ drops to 10/14
  (worst −3.09%). The loss is the IEX state/anchor schedule (the sparser tape
  arms fewer anchors), not B — A3a (frozen B) recovers only 13 pf2 fills.
- IEX `prior_flush`/`pf_est` undercounts ~7% (variant pf2 282 vs 304 matched
  frozen pf2), so the live pf tag is conservative.

**Judge live paper fills against the A3b baseline, NOT the frozen one:
pf2 ≈ +1.13%/trade, 10/14 months, ~19 fills/mo post-overlay, worst month ≈ −3.1%.**

Open priced decision (user): real-time SIP (Algo Trader Plus, ~$99/mo) is the only
way to restore the frozen schedule. Not adopted. Study B (post-fill tail
management) does not need it.

**Fragility profile (A3b pf2, OOS — know this before trusting +1.13%):**
win 55.0%, mean win +9.24%, mean loss −8.77%, breakeven win-rate 48.7% ⇒ cushion
≈ 6.3pp of win rate. Exit mix: 48% exactly at target (+10.1% net), 28% exactly at
stop (−11%), 23% tl30/other. Bootstrap 95% CI of the mean [+0.01%, +2.21%] — the
lower bound touches zero; frozen CI [+0.18%, +2.09%]. Worst 5 fills = −75.9pp of a
+318pp total. H1 +2.18% (n=130) → H2 +0.23% (n=152): the live-model recent half is
flat. At ~19 fills/mo, ~14 months of forward fills are needed for the CI to
exclude zero. $/mo: ~$422 at $2k/trade, ~$2.1k at $10k, ~$5.3k at $25k (capacity
p25 ≈ $84k at 5% participation). 30 forward fills validate mechanics, not edge.

---

## 15. Post-fill management lane is CLOSED (2026-09-12)

Study B appeared to pass its gate with a ~60s post-fill exit (pf2 +1.14%→+1.73%),
but it priced the exit at the last SIP print with the same flat 1% friction as the
baseline. `PRE-REG-EXIT-01` re-ran it conservatively — exit at the **NBBO bid**
plus 50bps — over H ∈ {instant..30m} and four slippage levels. Every flat and
conditional time-stop fails: R1 −3.77% (instant) to −4.12% (30m), 0-1/14 months,
versus the frozen baseline +0.914%/+1.136% on the same fills. At the touch instant
the bid already sits below B (mean 0.967·B). The edge requires the RESTING limit
target at c0; a market time-stop sells the winners into a falling bid.

Consequences: keep the frozen exit unchanged; do NOT add a time-stop; no
real-time SIP needed for this question. Post-fill management is a well-tested
negative (Study B + EXIT-01). Files: `factory/scripts/lb18_exit.py`,
`factory/artifacts/lb18_exit.json`/.parquet, `researches/PRE-REG-EXIT-01.md`.

---

## 16. Latest state (2026-09-13, market closed) — research lanes closed, forward paper is the live experiment

Where the whole project stands, for an agent reading this cold. **The chronicle lives in
`factory/STATE.md` (read the tail); this section is the entry-point summary.**

**Live:** `flush_bot.py` v2.1 (IEX state feed, consolidated broker fills), armed `live:true`,
dedicated paper account, flat, healthy (`data/forward/bot/LIVE` present, no `data/KILL`,
`startup_reconcile` clean in the journal). Next session Mon 2026-09-14 09:30 ET. Any code edit
requires the KILL-toggle restart (§5).

**Judging rule:** judge live paper fills against **A3b** (§14), not the frozen engine: pf2
+1.13%, 10/14 months, ~19 fills/mo post-overlay, worst −3.1%; fragility profile in §14
(55% win, 6.3pp cushion, CI touches zero, ~14 forward months to exclude zero).

**Research lanes CLOSED (all tested negatives — do not reopen without a new pre-reg):**
- **Post-fill management** (flat and conditional time-stops at any horizon/slippage):
  `PRE-REG-EXIT-01.md`, `lb18_exit.py/.json`, H028. The edge IS the resting limit at c0;
  exits that fire early sell winners into a falling bid.
- **IEX-only feed replay**: Study A failed, but Amendment A1 (`PRE-REG-MICRO-01.md`,
  `lb18_iex_hybrid.py`) decomposed it — the failure was fill-venue selection, not the state
  feed. The live model A3b preserves per-trade EV and loses ~20% of pf2 fills to
  anchor/cadence drift. Real-time SIP (~$99/mo) would recover frequency, not quality — a
  deferred money decision, NOT needed for the frozen strategy.
- **Pattern digests** (top-3, every minute, no target — per the user's directive):
  all-minutes v1/v2 (raw, z-scored shape) and event-anchored E1 flush-touch / E3 volume-spike
  are all EXHAUSTED (best silhouettes 0.006 / 0.021 / 0.041 / 0.043 < the frozen 0.05 rule);
  E2 thrust was underpowered (<2,000 windows). `PRE-REG-PATTERN-01/02`,
  `pattern_digest.py`, `pattern_digest_events.py`, H030. The only remaining branch is a
  learned sequence embedding — separate pre-reg, not started.

**Open candidate (weak):** H029/H029r day-breadth — quiet days (0–1 strict names) beat busy
days OOS (+1.20% vs +0.46% all fills; rank1 −0.69% vs +1.33%) but it does NOT survive the
day-clustered bootstrap or permutation (p=0.19). No gating. The live bot tags `pf_est` +
`n_strict_est` on every bid/fill and `flush_bot_ledger.py` partitions by them, so forward
paper tests it for free.

**Next step (the only one that matters):** accumulate ~30 forward fills → judge vs A3b →
tiny real-money sizing (start far below 5% of fill-bar volume; capacity p25 ≈ $84k/trade).
More data/power (2021+ backbone staging) and SIP are priced decisions, not defaults.

---

## 17. Latest state (2026-10-08) — flush rule resolved NEGATIVE; the new research packet is the entry point

**This section supersedes the §16 forward-paper plan.** §13–§16 are retained as
history; do not act on the §16 plan ("accumulate ~30 forward fills → judge vs A3b →
real-money sizing"): its premise (the flush rule's +1.14% OOS pass) was an extraction
artifact, so there is nothing to accumulate evidence for.

**H025 flush-bid — DO NOT ARM.** NET-print resolution in the actual worktree
`/home/hillel/algo projects/worktrees/Alpacatrader/basket-phase2-f1` (HEAD 203ddaf;
evidence `factory/artifacts/h025_research/` there; producers `factory/scripts/
h025_research_*.py`) resolved the frozen rule negative: pf2-preorder +0.66% legacy →
**-1.72% resolved** net/fill at 100bps over 1046 allowed days (pessimistic bound
-1.86%); all-orders +0.09% → -2.27%; negative in every year 2021-2026. Every derived
family also failed (exit surface 0/29 positive; PRE-REG-STABILIZE-01 confirmation
32/32 cells; resolved-fill pocket scan no positive bin; entry-discount surface
negative at every depth; qualification curves 0/13). Route CLOSED. `flush_bot.py` is
not running; the LIVE+KILL flags in this checkout are untouched — arming/cleanup is
the user's decision. The historical OOS pass remains in the record as non-executable
evidence.

**Entry point = the new research packet, not the old paper plan.** The live lane is
an independent open-anchor program in this checkout: producers
`factory/scripts/alpha_*.py` (panel, sim, events, overnight, learned, sequence
payoff, proven-push, short-liquid diagnostic, quote audit, micro core/flow/reclaim),
with the panel, contract and per-family outputs under `/home/hillel/alpha-data/
open-search-v1` (`contract.json`: full-PIT SIP B top-10 admission, gain ≥5%, $1
floor, fresh price; causal 5-minute clock strictly et<t; minute-t open entry proxy
with bar-less minutes held in cash; fixed-time exits at the first actual open ≥
entry+h; unresolved exits stay UNKNOWN, never dropped; round-trip costs
100/150/200bps). All per-study numbers/status for the 8 study families (events,
learned, sequence, proven_push, short_diagnostic, overnight, micro_flow,
micro_reclaim) come ONLY from the generated packet
`factory/artifacts/alpha_search_20261008.json`; the parent executed the producer
after the remaining runs landed, and the packet's terse outcomes table is filed in
this section (and in the CURRENT sections of `researches/STATE.md` /
`researches/HYPOTHESES.md`, plus an addendum in `factory/STATE.md`).
Blocks: fit 2021-02..2022-12, validation 2023, out-of-fit confirmation
2025-02..2026-05 (previously explored periods, NOT a pristine holdout). Protected
2024 / 2025-01 / 2026-06..08 never read. Data/storage: SIP inputs on the shared
Windows mount (C: ~24Gi free); bulk outputs on Linux `/home/hillel/alpha-data/
open-search-v1`. No live flags or orders were changed by any of this.

### Packet outcomes (2026-10-08 BASE-WAVE; source of record
### `factory/artifacts/alpha_search_20261008.json`, status COMPLETE, sha256
### 8887fb1e74ff1abdb763a4c3f9aad332f2cefc1a34c53b4ce709d11b071ff157)

Ten registered studies (8 base + rare sparse extension + bid-backed burst); panel
1,066 days / 1,076,304 rows (train 484 / validation 250 / confirmation 332; the
confirmation block 2025-02..2026-05 was previously explored, NOT pristine). Books:
$3,000 = 3 x $1,000 slots (open families); $750 = 3 x $250 (micro families). `%/day`
= mean_daily_lower_bound_book_pct; `%/order` = mean_net_known_fill_per_order_pct;
UNKNOWN fills charged -100% of the order budget, never dropped.

| study | verdict | key readings | status |
|---|---|---|---|
| events | FROZEN_DIAGNOSTIC_NO_EDGE | val chosen squeeze_release_h390 -0.2429 %/day @100 (n=352); late -1.7345 %/order (n=543 known + 8 UNKNOWN) = -1.7489 %/day | all-known-negative |
| learned | frozen h15 thr0.01 | val +0.0926 %/day (n=208, CI [-0.3028,+0.5871]); late -0.2922 %/day (n=447); quote audits NOT whole-portfolio certified (132/208, 218/447 pairs UNKNOWN) | val-positive subset only |
| sequence | no_selected_head_positive_on_late_block | late h60/scale30 -0.3752 %/day (n=235); h15 -0.6057 %/day; encoder fit exposure | negative |
| proven_push | negative_at_all_cost_rungs | late -1.8365 %/order (n=1680) = -3.0976 %/day @100; -4.75 @200 | negative |
| short_diagnostic | CONDITIONAL_SHORT_DIAGNOSTIC_NOT_EXECUTABLE_ALPHA | per-order: train +1.1562 (n=377), val +2.257 (n=119), late -1.5353 (n=339+4 UNKNOWN); borrow/SSR unverified | late negative |
| overnight | negative_at_all_cost_rungs | chosen qA late -1.1466 %/day @100 (n=954+4 UNKNOWN) | negative |
| learned_sparse_extension (rare) | DISCOVERY-NOT-VALIDATED | h60 thr0.03: val +0.0907 %/day (n=61); LATE +0.1417 %/day @100 (n=143, 124 traded days, 8/16 months, +0.9869 %/order), +0.0694 @150, -0.0026 @200; quote audit 77/143 | ONLY positive lead |
| micro_flow | no_edge_at_any_cost | late -1.0333 %/order @0 residual, -2.0181 @100 (-1.0891 %/day); 58 dev dates UNKNOWN -> lower bounds | negative |
| micro_reclaim | not_positive | late -0.4533 %/order @0 residual, -1.4438 @100 (-0.8986 %/day), 1 UNKNOWN | negative |
| bid_backed_burst | DIAGNOSTIC not promoted | val @100 -0.8203 %/day (CI [-1.1788,-0.5876], 0 unknown); late @100 -0.9803 %/day | negative |

**Best candidate mechanism (keep; do not kill):** `learned_sparse_extension` h=60,
thr=0.03 — the sparse LightGBM payoff model's rare tail on the open-anchored panel:
late block +0.1417 %/day of the $3,000 book at 100bps (143 known fills over 124
traded days, 8/16 months positive; +0.0694 %/day at 150bps, -0.0026 at 200bps;
+0.9869 %/order). DISCOVERY-positive, NOT validated, and RETAINED (not closed, not
failed): day-bootstrap CI includes zero; the 30<=known<100 window was the
exploratory lane's initial selector floor (traded_days>=30), not a one-number
veto — the late block already carries 143 known fills over 124 traded days; the
late block was previously explored, and the side-aware quote audit covers 77/143
pairs (the other 66 are UNKNOWN, not cash; whole_portfolio_certified=false). Worst
measured month -1.1858 %/day of the book (2026-02) is a reported regime/tail risk,
not a kill. Annualization, if ever quoted: service = 252 regular sessions/yr (not
24/7); (143/332) x 252 = 108.542 fills/yr; x $1,000 order x +0.9869 %/order =
$1,071.196/yr before operating costs and tax = 35.7065% SIMPLE of the $3,000 book
(NOT CAGR); the $2,510.991/yr all-fills figure holds ONLY if the 77/143 quote-
covered pairs (mean +2.5716 %/order @0 extra, +2.3134 @25) are representative —
NOT confirmed. Stress = the measured 100/150/200bps rungs; 1000bps was never used
and is not a veto. Yearly 24/7 framing is a service question (regular US sessions
only, 252/yr; official SIP feed $99/mo = $1,188/yr unless already covered, Alpaca
market-data docs), not a strategy result.

**Precise missing data to promote:** whole-portfolio side-aware quote audit (77/143)
+ capacity/queue realism at order size; a pristine holdout block (2024/2025-01
sealed, 2026-06..08 protected); forward/unseen validation under the standing power
doctrine (>=6 months pooled development + >=2 pre-registered unseen collision
months at 100bps) on a pristine block — the >=100-known-fills figure is the
historical lane-selector floor (primary vs exploratory), not a blanket veto;
exit/stop-risk and re-entry-frequency evidence — DELIVERED by the 2026-10-09
NIGHT-WAVE packet (§18 below; the four night-extension workers that were
IN_PROGRESS here are the completed research wave; their absence from the base packet
was NOT a negative). The BASE-WAVE packet (ten studies) is COMPLETE for its
registered wave; §18 carries the night outcomes and the full validation objective
stays active.

**Replay (cwd = repo root):**
`uv run --no-sync python factory/scripts/alpha_search_report.py --root ~/alpha-data/open-search-v1 --out factory/artifacts/alpha_search_20261008.json`
regenerates the packet; per-family producers under `factory/scripts/`:
`alpha_open_events.py`, `alpha_open_learned.py` (replay `--horizon {15,60,390}
--threshold {0.01,0.03,0.05} --period {train,validation,confirmation} --cost 100`),
`alpha_sequence_payoff.py run --npz data/atlas/sequence/v1/runs/causal/block/embeddings.npz`,
`alpha_proven_push.py`, `alpha_short_diagnostic.py`, `alpha_open_overnight.py`,
`alpha_sparse_model_extension.py` (+ mandatory `alpha_quote_audit.py --trades .../trades_confirmation_100.parquet
--out .../learned_sparse_extension/quote_confirmation --latency-ms 250 --max-age-s 2.0 --order-budget 1000`),
`alpha_micro_flow.py --stage all`, `alpha_micro_reclaim.py report`,
`alpha_bid_backed_burst.py --command report` (outputs under
`~/alpha-data/open-search-v1/<family>`; models `learned/models/payoff_h{15,60,390}.joblib`
+ `feature_order.json`, api `alpha_open_learned.load_models/score_frame/feature_matrix/make_signals`).

**Execution risks:** six families use a minute-open proxy — quote-supported touch is
not a guaranteed exchange fill; micro ASK@signal+250ms / BID@target touches are
capacity-checked at $250/side but still not fill guarantees; 0bps residual on micro
is a diagnostic, not a free fill; UNKNOWN exits are charged -100% of the order
budget (never dropped, never cash); no-fill intents keep cash and pay no fee (not
losses, not cash returns); the confirmation block was previously explored (NOT
pristine); short borrow/SSR/locates/margin unverified; micro_flow's 58 UNKNOWN dev
dates make development figures lower bounds; the micro book assumes margin-style
funded cash reuse. A candidate positive only at low residual cost or with UNKNOWN
rows present is reported as such, never as a pass. Nothing here is a deployment; no
live flags or orders were changed.

### WAVE-2 (2026-10-09)  -  real-quote battery; entry point is the wave-2 summary artifact

**Source of record:** `factory/artifacts/wave2_alpha_search_summary.json` (sha256
`e7cd3640e10da3091e119abc769ae35cd3415a420ab688a263fbb0609acdd1e8`; 10 lanes, each with
its verbatim decision text and honest note). Quote acquisition now covers the full
admitted PANEL universe across all 1,003 sessions (train 421 / validation 250 /
confirmation 332, the latter previously explored  -  NOT pristine) via
`factory/scripts/alpha_quote_universe_acquire.py --universe panel --resume --blocks
<block>`. Dollars are $/calendar day at the 25bps rung on the stated book ($750 = 3x$250,
$3,000 = 3x$1,000), simple 252-session convention, not a CAGR; UNKNOWN executions are
charged the full budget in the day lower bound, never dropped.

**CLOSED NEGATIVE on real quotes** (do not reopen without a genuinely new mechanism):
- opening-range pullback (`alpha_opening_range_pullback.py`): all six views negative on
  both blocks at every rung; best validation pullback_retest@15m -1.21 $/day (233 known
  fills, 13 UNKNOWN), best late -4.40 $/day.
- liquidity recovery after flush (`alpha_liquidity_recovery.py`): DIAGNOSTIC, nothing
  promoted; least-bad flip_first_positive_1s hold900 val -0.31851 / conf -0.35026
  fraction of book/day, 0/9 positive confirmation months, 656 known + 173 UNKNOWN fills
  (parent-reported; the lane's results.json is absent at write time).
- multi-session carry (`alpha_multi_session_carry.py`): NO_EDGE_EVIDENCE  -  a dead gate,
  not a robustness statement; 3 funded intents per view across 397 pre-freeze candidate
  days, all exits UNKNOWN, every view $0.00/day.
- cross-name rotation (`alpha_cross_name_rotation.py`): negative OOS  -  chosen
  lgbm_reg_h15_thr50bps validation +0.67 $/day (115 known) but late -1.26 $/day (277
  known); touch-only baselines -61.15 validation / -62.31 late $/day; the lgbm_rank
  family produced 0 fills at every bar (a limitation: untested by construction).
- hour-of-day learner (`alpha_touch_hourly_payoff.py`): negative both blocks  -  chosen
  h15_net100bps validation partial +0.098 $/day (25 known) with whole-basis -3.90
  $/day and every other view negative; late -1.07 $/day (86 known).
- h390 whole-day hold (`alpha_retained_h390_lane.py`): all 12 cells negative on 2023
  validation (best -11.56 $/day) vs the frozen repeat_h60 reference (VAL +4.88 / late
  +9.28)  -  parent-reported, artifact absent at write time.

**LEADS ONLY (not validated, not promotion grounds):**
- passive mid resting entry + TTL (`alpha_passive_quote_confirmation.py`): chosen
  h60_thr200bps_mid_ttl120s validation +4.66 $/day, late +8.59 $/day on $3,000 (45/105
  known; non-chosen late ttl30 +12.15 $/day)  -  falsifier is the UNKNOWN share (57.7%
  validation / 54.0% late of attempts) and the -415.34 / -560.69 $/day full-loss bounds.
- adaptive depth participation (`alpha_adaptive_depth_sizing.py`): chosen
  h60_thr300bps_part50pct validation +1.36 $/day (53 known, 8 UNKNOWN = 12.9%), late
  +5.95 $/day (122 of 142 attempts)  -  falsifier is cost fragility (negative at the
  125bps rung on validation) and depth visibility is not a fill guarantee.
- the retained core lane (theta.030/h60/d0) stays the reference: late +3.38 $/day at
  25bps on the $750 book (131 known fills); the frozen minute-proxy retained rare h60
  cell stays the program lead (+$1,430.80/yr simple on the $3,000 proxy, repeat
  cadence  -  see 18).

**Exact next action for a future session:** (1) collapse the UNKNOWN share on the two
LEAD cells under a whole-portfolio side-aware quote audit (currently 57.7%/54.0% for
passive mid, 12.9% for adaptive depth) and price capacity/queue realism at order size;
(2) keep every cell on the flexible 25-150bps ladder  -  no fixed 100-150bps hurdle and no
arbitrary veto of the h390 hold's late positives, which are regime evidence on a
previously-explored block, not promotion grounds; (3) both remaining lane artifacts are NOW persisted and re-verified (2026-10-09):
~/alpha-data/open-search-v1/retained_h390_lane/results.json (producer re-run, 56.2s; all 12 val
cells negative, late +12.01 chosen / +37.21 non-chosen) and
~/alpha-data/open-search-v1/liquidity_recovery_five_min/summary.json (DIAGNOSTIC;
least-bad val -0.31851 / conf -0.35026 of book/day); (4) `alpha_delayed_sip_iex` COMPLETE:
the free-feed path is NEGATIVE (chosen val +4.73 $/day but late -1.76 $/day, all 12 views
negative late) - real-time SIP is required, so do NOT trade this model off a delayed feed;
(5) promotion still requires the standing power doctrine on pristine data (2024/2025-01
sealed, 2026-06..08 protected) plus actual-fill evidence. No live flags/orders changed;
nothing in this section is a deployment; do not arm the flush bot (17) and do not treat
the historical +1.14% OOS pass as executable.

## 18. Latest state (2026-10-09) — NIGHT-WAVE extensions COMPLETE; best MEASURED
## candidate is the repeat cadence of the SAME frozen h60 model; bot still must NOT
## be armed

**This section supersedes nothing in §17's H025 closure** — H025 flush-bid stays
CLOSED: do not re-arm `flush_bot.py`, do not resurrect the flush rule, do not treat
the historical +1.14% OOS pass as executable. §18 adds the completed night-extension
research on top of the retained open-anchor lead.

**Night packet (source of record):** `factory/artifacts/alpha_night_extensions.json`,
857,352 bytes, sha256
`e5d77d39cbaf90c5981c38a696eafed09ec9d963a74858a40d0bbe8345f8e683`, status
COMPLETE-4-OF-4-EXTENSIONS, kind ALPHA_NIGHT_EXTENSIONS_NOT_GOAL_COMPLETION, asof
2026-10-09. Regenerate (read-only):
`uv run --no-sync python factory/scripts/alpha_night_report.py --root ~/alpha-data/open-search-v1 --base factory/artifacts/alpha_search_20261008.json --out factory/artifacts/alpha_night_extensions.json`.
Every program is DISCOVERY-NOT-VALIDATED; the packet's goal is OPEN; this is NOT a
self-issued goal completion; "best measured" = best MEASURED on previously explored
blocks, not global-best and not live-filled.

**The four former IN_PROGRESS night workers are this completed research wave:**

1. **sparse_daily — daily frequency / re-entry (BEST MEASURED lead).** Same
   immutable 2021-22 LightGBM `~/alpha-data/open-search-v1/learned/models/
   payoff_h60.joblib` (sha256 c5493c6c…, 26 causal features, no refit/HPO/ticker
   features); only the admission cadence varies. Chosen on 2023 validation $/day
   @100bps before any late outcome: repeat_h60 (thr0.03 | exit_h60 | flat 15-min
   cooldown | max 3 attempts/ticker/day) +$2.8387/day vs control once_h60 +$2.7222/day
   (exact reproduction of the original +0.000907/day, 61/61 fills) — val 67 known
   fills / 55 traded days. Late block (2025-02..2026-05, 332 days, NOT pristine):
   +0.0018926 of the $3,000 book/day = +$5.6778/day = **+$1,430.80/yr simple (x252)**,
   157 known fills / 0 UNKNOWN on the same 124 traded days (+0.000305 @200bps).
   Added legs vs the same-cadence first attempt: +14 fills net **+$33.84/leg**
   (+$1.4270/day, +$473.76). Shorter re-entries lose (repeat_h30 legs -$9.23/leg;
   repeat_h15 val-negative; repeat_h30_strong 6 val / 14 late attempts). New-cohort
   quote audit: 85/157 quote-supported (covered mean +2.7513%/fill @0 extra residual,
   +1.7249 @100), 72 UNKNOWN, whole_portfolio_certified=false. Frequency honesty:
   (157/332)x252 = **119.17 fills/yr on the same 124 traded days** — ~10/month, NOT
   daily. Replay: `uv run --no-sync python factory/scripts/alpha_sparse_daily.py`
   (defaults out `~/alpha-data/open-search-v1/learned_sparse_daily`; flags --resume,
   --days, --skip-late).
2. **sparse_execution_frontier — quote service + capacity/latency ladder.** Actual
   ASK/BID touch with L1 capacity check; latencies 250/1000/2000/5000ms each priced on
   its own (250ms base evidence, never a best-latency pick; 15s stale leg =
   corroborated scenario only, primary rule never widened). Late @$1,000/250ms/2s:
   143 intents, 110 priced, 79 L1-supported (55.25%), 64 UNKNOWN; covered mean
   +2.5102% = +$25.10/fill (win 40.5%, PF 1.379, worst -30.26%), day-bootstrap CI95
   [-2.02%, +7.19%] includes zero; observed covered contribution $1,505.23/yr
   ($317.23/yr net of the $1,188 SIP plan); the $2,724.66/yr whole-case figure is
   CONDITIONAL on the unknown 64 matching the covered — not confirmed. Val cohort:
   41/61 supported, $111.03/yr covered. Integer qty=0 pairs corrected to a KNOWN
   no-order cash class; all 8 missing legs acquired read-only (HTTP 200: RKDA
   2023-12-28, RPGL 2026-01-30, JCSE 2026-03-30, CODX 2026-05-26). Replay: `uv run
   --no-sync python factory/scripts/alpha_sparse_execution_frontier.py` (defaults out
   `~/alpha-data/open-search-v1/sparse_execution_frontier`; --only late143,
   --fetch-missing, --sizes, --residuals, --latencies, --max-age-s).
3. **sparse_exit_management — paired stop risk; keep the BASE hold60m.** Entries and
   quantities immutable; policies hold60m (control at actual NBBO touch) vs
   quote-triggered stop10/stop15. Validation selection @+25bps lower bound (frozen
   2026-10-09T02:39:27+0300 before late outcomes): hold60m -0.0383711; stop10
   -0.0374688 (highest bound); stop15 -0.0407160. These are unknown-full-loss
   coding bounds, NOT measured portfolio EV; the data-unknown-count caveat matters.
   Late stop10 whole-book bound -$38,069.40/yr is a SYNTHETIC unpriced-UNKNOWN charge
   (known-covered contribution -$876.63/yr at 71.35 known fills/yr) — NOT
   observed PnL and NOT an expected loss. Paired validation (31 known pairs):
   worsened 10 vs improved 8, delta mean -0.79% (median 0); stops clamp the worst tail
   (control worst-5 -32.2% vs policy -10.3%) but cut recoveries (25 stop-through
   gaps, mean -100.1bps, worst -368.1bps) and the covered actual stop is WORSE than
   the control — so no stop policy is promoted. Base reproduction exact (val
   +0.0009073964; late +0.0014169262). Replay: `uv run --no-sync python
   factory/scripts/alpha_sparse_exit_management.py` (defaults out
   `~/alpha-data/open-search-v1/sparse_exit_management`).
4. **micro_payoff — supervised 5/15-second scalping learner: no edge for THIS fixed
   model.** Fit on the fully covered development months 2021-05-03..2021-10-29 (127
   days, 214,506 states per head, 5s sampling, equal-day weights, labels clipped
   [-0.2,0.3] fit-only); chosen h5 thr0.001 at 10bps residual frozen BEFORE any late
   record — validation EMPTY (0 signals / 0 fills over 250 days, 546,586 qualified
   states): an actual no-evidence outcome, NOT a measured zero edge. Late (187 days):
   9 signals, 8 fills on 6 traded days; mean daily net -$0.0439 @0 residual
   (-$1.03/fill, win 37.5%, PF 0.396, worst -$6.83), -$0.0546 @10, -$0.0705 @25,
   -$0.1499 @100; 0/9 positive months; CI includes zero. The everyday/scalping goal
   was MEASURED, not forced; this falsifies only this formulation — no universal
   "scalping is impossible" claim. Replay: `uv run --no-sync python
   factory/scripts/alpha_micro_payoff.py run` (or `... replay --horizon 5
   --threshold 0.001 --period late --cost 10`; defaults out
   `~/alpha-data/open-search-v1/micro_payoff`).

**Mechanism of the retained lead (descriptive profile; producer
factory/scripts/alpha_retained_mechanism.py, runtime 20.2s, output
~/alpha-data/open-search-v1/alpha_retained_mechanism/profile.json sha256
4d6d23d8ef73e9a14d552bf9cb4cce85d806612f4a9c4c4a2641bb509f7b8230):** the rare h60
head earns a deep-drawdown recovery inside a strong intraday uptrend, not positive
chase (val medians: +62.7% vs open, -15.2% off day high, +12.8% vs VWAP, ret3
+0.50% / ret15 -2.26%, volume acceleration 1.33x). Calibration is GROSS vs GROSS:
predict_gross 3.688% vs realized_gross 2.132% (val; 3.657% vs 2.002% late; error
~+1.6pp), never compared with net. Quote coverage is endogenous (late quoted-77
+2.111% vs unresolved-66 -0.325%) and the entry ASK sits above the bar open (mean
+29.6bps) — paying up. TOD/price-band splits are descriptive only.

**Cash-reuse eligibility (CURRENT Alpaca docs, cited 2026-10-09):**
https://docs.alpaca.markets/us/docs/understanding-the-new-intraday-margin-rule
(updated 2026-04-27) — pattern-day-trader designation, the 4-trades/5-days limit and
the $25,000 minimum are REMOVED; non-leveraged margin accounts re-enter without T+1
locks while sufficient-equity/house intraday-buying-power rules still apply; the
Trading API PDT account/config fields were removed (changelog 2026-07-06,
https://docs.alpaca.markets/us/changelog/2026-07-06-pdt-db49dba). Do NOT apply the
old $25k floor or 4/5-day cap. The $3,000/$750 research books assume margin-enabled
funded cash reuse; real house/asset eligibility is NOT verified, cash-account
settlement is not modeled, and no account CAGR is claimed.

**Standing guardrails:** UNKNOWN outcomes are counted and charged the producer's
full-unit lower bound, never cash, never dropped; known no-orders keep cash and are
never losses; the -$38,069.40/yr exit bound is a synthetic unpriced-UNKNOWN
convention, not an expected loss; annualization is a simple x252-session rate, never
a CAGR, and a daily-reset research book does not self-finance; no arbitrary
100-fill/CI/cost-stress veto (100bps is the baseline bookkeeping rung); the late
blocks were previously explored (NOT pristine) — promotion still requires the
standing power doctrine (>=6 months pooled dev + >=2 pre-registered unseen collision
months at 100bps) on a pristine block, plus L2 depth, actual exchange fills and
publication-latency evidence; news/catalyst access is repaired and its PIT revision
limits are recorded (factory/artifacts/alpha_news_access.json) — no catalyst alpha
inferred. No live flags or orders were changed; nothing here is a deployment; the
paper bot must NOT be armed on the flush rule or on any DISCOVERY-NOT-VALIDATED
candidate.
