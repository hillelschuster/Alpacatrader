# HANDOFF — Flush-Bid Top-Gainer Mechanism

**Written 2026-09-11, end of day-1 live paper session. For the active BASKET Phase-2 program, read §17 first; it supersedes this document's old research-roadmap framing while preserving the flush-bid/live-operational record.**
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
- **Latest state: §16 (2026-09-13).** Sections §13–§16 are newer than this TL;DR and supersede it where
  they conflict.
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

## 17. Active BASKET-01 Phase-2 EV-Harvesting Handoff (2026-09-24, in progress)

**Read this section before acting on the BASKET research work.** It is the current handoff for
the active isolated worktree:

```text
/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1
```

The older flush-bid work remains a separate validated/paper-trading mechanism. Do not alter the
bot while working BASKET. BASKET is research-only under `factory/` and `researches/`; it must
earn a validated architecture before anything moves into `src/`.

### 17.1 The user's operating intent

The objective is not procedural completion, nor another broad proof that top-gainer opportunity
exists. The objective is to discover the strongest **causal, executable architecture for
harvesting the already measured top-gainer right tail**.

The economic center of gravity is:

> Enter early enough to own the exceptional continuation tail; then use causal survival,
> golden-window, and capital-allocation information to make ordinary failed participation cheap
> without prematurely destroying the rare survivors that pay for the book.

This creates a deliberate asymmetry. A false positive can cost a few percentage points; a false
negative can forfeit +50%, +100%, or much more. Never score a BASKET module as generic
winner/loser classification accuracy. Score it as preserved future economic value minus the cost
of failures, capital usage, friction, drawdown, and execution.

The user explicitly wants strong subagents assigned to **whole coherent economic families**, not
micro-tasks or arbitrary parameter sweeps. Good parallel lanes include survival/release,
golden-window continuation signatures, scale-in, per-ticket/basket scale-out, reserve deployment,
released-capital recycling, breadth/joint-survivor economics, weak-regime behavior, execution,
and independent code/result verification. Parallel depth is valuable; random knob multiplication
is not.

The hierarchy for every lane is:

```text
economic mechanism -> causal/statistical definition -> implementation -> artifacts/results -> interpretation
```

The code is part of the research argument. A perfectly tested answer to the wrong economic
question is worthless. An economically interesting claim with leakage, wrong fills, or bad cash
accounting is equally worthless.

### 17.2 Current BASKET thesis and boundaries

Phase 1 is closed. It supplied an independently audited, causal measurement layer on the same
1,066 permitted development days. Do not reopen a generic "does BASKET exist?" investigation
unless a concrete material measurement defect is found.

Phase 2 asks how to monetize the population's observed right tail:

1. **Entry / breadth:** early participation preserves tail; later checkpoints improve identity
   resolution. Do not assume one ticket must win: multi-survivor outcomes are material enough to
   measure and must shape capital decisions.
2. **Survival / release:** cut economic failures selectively, not every drawdown. Depth,
   duration, recovery/reclaim, prior MFE, giveback, and failed reclaim matter because a large
   eventual winner can suffer substantial interim deterioration.
3. **Golden window (09:45/10:00, ET 585/600):** this is potentially a capital-allocation
   information event, not merely a stop checkpoint. It can eventually inform hold, add, reduce,
   release, reserve deployment, or recycling.
4. **Dynamic capital:** the final architecture may legitimately combine entry, reserve cash,
   scale-ins, partial reductions, full releases, and redistribution. Complexity is acceptable
   only when each module earns stable incremental net EV and remains causal/operationally clean.

Do not mentally canonize R0/R2/R3, early finite grids, or any one existing formulation. They are
baselines and measurements. If a causal map reveals a material economic state that the current
family misses, state it clearly and design one bounded follow-up. Do not silently explode the
grid.

### 17.3 Non-negotiable measurement contract

Authoritative sources: `researches/PRE-REG-BASKET-02.md` and
`factory/BASKET-SIM-CONTRACT.md`.

- Exact permitted development calendar: **1,066 days** total:
  - Block 1: `2021-02` through `2023-12`, **734 days / 35 months**.
  - Block 2: `2025-02` through `2026-05`, **332 days / 16 months**.
- Never read sealed `2024` / `2025-01` or reserved `2026-06..08` in a BASKET dev lane.
- Causal timing: a bar stamped `t` completes at `t+1`; decision state uses only the completed
  bar history permitted by the family; any action fills at the first eligible **later bar open**.
  Do not use same-bar high as a subsequent outcome.
- Sleeve accounting: `C0 = 1` per independent basket-day sleeve; no leverage, cash never
  negative, deployed notional never above `C0`. Blocked slots stay cash; no replacement unless a
  family explicitly and causally implements it.
- Costs: report net at **100 bps** total round trip and adversity at **150 bps**. Report gross
  where contract-required, but never confuse gross/MFE with executable net P&L.
- Retain every declared cell and both blocks. Do not select a strong-looking dev cell. A future
  strategy claim needs a pre-declared fresh test; development results are mechanism evidence.
- Long computations must write incremental month/day parts and resume safely. Verify artifacts
  before reasoning from them.

### 17.4 Evidence already established

#### Frozen Phase-1 / baseline facts

- **F1 primitive surface:** 120 frozen entry/breadth/primitive-exit cells over the common 1,066
  days. Every surface-wide mean is negative at 100 and 150 bps. This is a baseline constraint,
  not a denial of untested conditional management/capital actions.
- **F2/F12 causal golden-window panel:** `factory/artifacts/basket/phase2/F2_F12/`.
  - 182,094 unique observations, 51 fields, 51 monthly shards, five entry families
    (`A_pm`, `A_pm31`, `A_open`, `B585`, `B600`) and applicable checkpoints
    `{580,585,590,600,615}`.
  - State uses bars `et <= checkpoint`; forward outcomes use `et > checkpoint` only.
  - At ET 600, remaining-tail base rates are substantial but non-executable path facts. For
    example B600 mean remaining MFE is 11.80% in block 1 and 15.29% in block 2; +50% touch is
    3.70% / 5.40%; +100% touch 0.97% / 1.64%.
  - Rank change, relative strength, MFE-so-far, and short velocity vary descriptively, but F2
    did **not** declare an executable golden gate.
- **F8 joint basket economics:** `factory/artifacts/basket/phase2/F8/`.
  - Multiple survivors are plausible, not an assumption to dismiss. Across its 120 frozen F1
    cells, pooled at-least-two +30% MFE-touch rates span 1.03%--15.76%; at-least-two EOD
    net-positive member rates span 3.66%--37.99%.
  - These are input/path/economic shape measurements, not proof of executable profitability or
    a one-winner architecture.
- **F9 weighting:** initial equal/rank-linear/score-gap reweighting is negative surface-wide;
  it does not establish sizing alpha.
- **F10/F14:** causal descriptive environment map, not a pre-10 entry gate.
- **F11:** stored-open F1 baseline stays negative throughout its 0/50/100/150/200-bps ladder.
  Conservative minute and quote-aware execution were blocked by unregistered fill semantics and
  missing quote input; no capacity claim was fabricated.

#### Integration-1 promotion map

`factory/artifacts/basket/phase2/INTEGRATION_1/promotion_map.json` is the current bridge from
baseline evidence to implementation discovery. It explicitly makes **no strategy promotion**.
Its original queue is F3 -> F5 -> F4 -> F6 -> F7, but treat this as dependency guidance, not a
reason to stop economically independent mapping lanes. The map's falsification standard is the
right one: a management/capital module must improve net C0 economics across both blocks after
friction without materially destroying the valuable tail it is meant to harvest.

### 17.5 Completed Phase-2 deliverables (verify before relying on them)

#### F3: survival / weakening / release

Paths:

```text
factory/scripts/basket_f3_sim.py
factory/scripts/basket_f3_structural_map.py
factory/scripts/basket_f3_economics.py
tests/test_basket_f3_sim.py
tests/test_basket_f3_structural_map.py
tests/test_basket_f3_economics.py
factory/artifacts/basket/phase2/F3/
```

1. **Causal structural map** is complete.
   - `structural_map/structural_daily.parquet`: **673,952** completed-bar own-path state rows.
   - 51 monthly resumable parts; exact 1,066 / 734+332 day guard and input hashes.
   - Maps entry-relative drawdown, consecutive below-entry duration, reclaim/recovery behavior,
     running-peak drawdown, MFE surrendered, time since high, and failed reclaim.
   - Future high/low/close outcomes are measured from decision close using only bars strictly
     after the decision bar. Empty strata are retained. It is descriptive; it creates no fitted
     threshold or selected rule.
2. **Fixed release-module surface** is retained and audited.
   - A fixed A_pm/T570/N2 R0/R2/R3 experiment: R0, six R2 cells
     (`L={10,15}`, `w={3,5,10}`), three R3 cells (`g={40,50,60}`), each at 100/150 bps:
     **20 cells** total.
   - Direct audit performed in this session: every cell has 1,066 daily rows and 51 monthly
     daily parts; no `.tmp`. `basket_sim.py --self-test` and focused F3 tests passed.
   - All pooled net means remain negative. The release cells improved mean versus their matching
     R0 comparator in both blocks, but that is development-only and does not make an edge.
3. **Economic synthesis** was generated after the map:
   - `F3/economic_synthesis.json` and `F3/ECONOMIC_SYNTHESIS.md` retain all 20 cells and
     separate daily C0 accounting, ticket realized/marked economics, MFE-tail contribution,
     action timing/counts, monthly/block/year results, and the R0-only F8 joint context.
   - Reported mechanism result: R2 reduces failed-ticket loss more but has lower ticket net in
     the raw-MFE>=50% tail cohort; R3 preserves more of that tail cohort but reduces failure
     losses less. All 18 release cells improve mean C0 EV versus same-friction R0 in both
     blocks, while all remain negative. This is the real trade-off for F4/F6/F7; raw MFE is not
     executable P&L.
   - Agent-reported verification: 22 economics tests, compile, artifact audit, two identical
     output-hash runs; re-run locally before any selection/integration claim.

**F3 interpretation:** release is not dead merely because it does not rescue the full basket
mean. It gives an empirically measured cost-versus-tail frontier. F4 must ask whether partial
reductions improve that frontier; F6/F7 must ask whether capital released or held can earn more
elsewhere without creating a new tail-destruction problem.

#### Golden-window capital-allocation map

Paths:

```text
factory/scripts/basket_capital_map.py
tests/test_basket_capital_map.py
factory/artifacts/basket/phase2/capital_allocation_map/
```

This is a newly completed descriptive map built from F2/F12 state rows and F8 MFE-only
multi-survivor context. It does not alter F2/F12 or execute trades.

- Scope: **78,088** eligible observations at ET 585/600 across all applicable entry
  family/checkpoint/block combinations. It records **450** fixed feature tables,
  **54** fixed interaction tables, and **30** F8 multi-survivor summaries.
- Important causal boundary: B600 does not exist as an already-entered candidate at ET 585; the
  map retains that combination as explicitly not applicable rather than fabricating a row.
- Current-rank top versus bottom quintiles show median remaining-MFE differences of +7.45pp /
  +6.89pp and +50%-touch differences +5.19pp / +4.32pp across the nine applicable
  family/checkpoint pairs in blocks 1/2, **but** adverse-first risk is also +21.89pp / +18.64pp.
  Stronger return-from-fill quintiles have more remaining upside and lower adverse-first
  incidence in both blocks. These are associations, not gates or action effects.
- F8's >=2-member +30% MFE frequency is higher at N=4 than N=2, but varies materially by entry
  family/block. Do not turn it into a simplistic "always own four" conclusion.
- Agent-reported verification: tests 3 passed, compile/output completeness passed, repeated
  generation byte-identical, no `.tmp`; LSP unavailable for this worktree. Reverify before a
  strategy claim.

**Use:** This map makes the golden window a serious candidate capital-allocation event. It does
not authorize a gate. Its observations should motivate bounded next-open F5/F6/F7 execution
tests, each with incremental net EV and tail-preservation accounting.

### 17.6 Active workers -- DO NOT KILL

At the time this handoff was written, two whole-family compute agents are still active. Do not
cancel, restart, or duplicate them. Wait for their actual completion notification and then call
`background_output` on the listed task ID.

| Worker | Task ID | Scope | What must be checked when it finishes |
|---|---|---|---|
| F5 full lane | `bg_eec5ec64` | Repair staged ADD reservation bug; add real executed-tranche net-EV accounting; rerun all 840 cells x 1,066 days. | The RED regression must show a reservation-skipped new-high does not consume `next_add`; full 840-cell rerun must be clean; decision/event files real; tranche EV separated from paired basket delta; both blocks/frictions and source hashes verified. |
| F4 per-ticket scale-out | `bg_a85c0e4b` | Full per-ticket partial-reduction family using F3 R2/R3 vocabulary, patterns `{25->25->rest, 33->33->rest, 50->rest}`. | Confirm exact grid/control count, next-open reductions, no double-fire, C0/tail accounting, full 1,066-day surface, and that any basket-level K-mode limitation is explicit rather than silently omitted. |

The current status may expose intermediate untracked F4 scripts/tests; they are agent-owned
while the worker is running. Do not edit/revert them in a new session.

### 17.7 F5: why the prior surface is invalid until the worker returns

Paths:

```text
factory/scripts/basket_f5_scalein.py
tests/test_basket_f5_scalein.py
factory/artifacts/basket/phase2/F5/
```

The initial 840-cell F5 surface (five entry families x N={2,3,4} x R0/R1(-8,-10,-15) x
seven registered add schedules x 100/150 bps) was useful exploration but **not acceptable
evidence**:

1. `ScaleInRule.evaluate` advanced `next_add` before marking an early strict-new-high event as
   `pending_entry_cash_reserved`; this can spend a staged tranche slot even though no ADD was
   funded/scheduled, suppressing a later eligible first tranche.
2. Paired basket-day P&L versus no-add was reported, but PRE-REG requires the incremental net EV
   of the actually executed added capital. A full repair must retain decision, later-open fill,
   allocated notional, friction, subsequent tranche outcome, skip reason, and matched no-add
   comparison. Unfunded/cap/reservation skips cannot be called executed adds.
3. Existing reuse logic could synthesize missing decision artifacts as `[]`; full rerun must
   demand genuine decision/event evidence for add cells.

The F5 mechanism remains strategically important: a threshold-free own-ticket strict new high
is a coherent continuation hypothesis. Do not weaken it into an F2-derived gate or make it a
generic strength classifier. The question is whether the extra tranche earns net EV after its
next-open fill while preserving/harvesting remaining tail, separately in both blocks.

### 17.8 Next shared-core lane: F6/F7 dynamic capital

Do not edit `factory/scripts/basket_sim.py` while F5 is running. Once F5's completed source and
surface are verified, launch **one whole shared dynamic-capital owner** for the required batch
engine extension and the F6/F7 families. This serialization is technical, not ceremonial: F5
uses the shared simulator, and mid-run core changes can invalidate its hashes/reproducibility.

The existing F6 design specification says the smallest correct change is a batch checkpoint hook:

- `reserve_frac` currently only reduces entry budget; it does not deploy reserve.
- The current per-ticket `on_checkpoint(et, ticket, bar)` cannot know the whole current survivor
  set or allocate one sleeve reserve deterministically.
- Add a batch callback after all ticket decisions at ET 585/600, but before/with no same-bar
  execution. It receives the ordered eligible open survivor set and schedules standard `ADD`
  actions at each recipient's first later bar open.
- A release/reduce pending on the checkpoint bar excludes that ticket; earlier pending actions,
  cap, cash, order, carry/no-resumption, and C0 invariants are honored. Unallocated/cap-limited/
  unfunded reserve remains cash and is reported; never partially fill it by assumption.

F6 minimum economic experiment after the core is stable:

- `p={0.67,0.50,0.33}` reserve at entry, plus `p=1.0` full-deployment control.
- checkpoint `585` or `600`.
- policy `cash` versus equal across currently eligible survivors.
- no golden-gate survivor policy until a simple predicate is explicitly declared from an economic
  map and tested as a bounded new formulation.

F7 follows a demonstrated F3/F4 release path and tests recycle fractions `{0,50,100}%` versus
cash/equal survivors. It must measure incremental recycled-tranche net EV; it is not permission
to pour released cash into rank 1. Prefer a single combined dynamic-capital owner to avoid
conflicting `basket_sim.py` changes.

### 17.9 What the eventual integration must do

The final `INTEGRATION_2` owner must not merely concatenate READMEs. For each candidate module,
personally answer:

1. What economic question did it test?
2. Did the code implement that question with causal state and next-open execution?
3. What capital/execution assumptions actually applied?
4. What did it do to ordinary failed-ticket cost?
5. What did it do to exceptional-tail contribution, false/early release, and multi-survivor
   opportunity?
6. Did incremental net EV survive both blocks, friction adversity, nearby declared variants,
   month/year concentration, and realistic capital utilization?
7. Does it add independent value to another module, or merely duplicate/undo it?

Only combine modules that earn a stable independent place. A spectacular cumulative dev cell is
not a strategy. Conversely, do not dismiss a large verified result merely because it is large:
audit it, understand where the money came from, and test it fresh.

The desired output is a small, coherent causal architecture for harvesting the inefficiency, not
a giant report and not a minimalist hold-or-sell dogma.

### 17.10 Worker and workspace discipline

- The worktree is intentionally dirty. At handoff time, `factory/STATE.md`, most Phase-2 scripts,
  tests, canonical SIP artifacts, and Phase-2 result directories are untracked/modified by
  concurrent work. Never blindly stage, revert, reset, checkout, or commit all changes.
- `uv.lock` can be touched incidentally by `uv run`; it is **not** part of these research lanes.
  Check it before/after a worker; restore only a confirmed agent-introduced lock refresh, never
  user changes. A prior F3 lock refresh was removed; it may become dirty again while F4/F5 run.
- Subagents sometimes start in a clean sibling checkout and falsely conclude active untracked
  files do not exist. Every agent prompt must require exact active-root confirmation and absolute
  paths/workdir before it reads or writes:
  `/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1`.
- This environment's LSP diagnostics reject isolated-worktree file paths as outside the request
  cwd. Do not call that a clean diagnostic run. Use focused tests, `py_compile`, deterministic
  canaries, and artifact audits; record the actual LSP limitation.
- Use `apply_patch` for edits. Do not use destructive git commands. Never delete a failing test
  or weaken it to force a pass.
- Research output must be incremental/resumable. Before interpreting an artifact, check exact
  date counts, both blocks, required parts, hashes, no temporary files, and full grid coverage.
- After a meaningful completed experiment, append concise current facts to `factory/STATE.md`,
  `researches/HYPOTHESES.md`, and experiment ledgers **only after** the owner/orchestrator has
  verified the actual result. Do not confuse agent self-report with accepted evidence.

### 17.11 Immediate commands for the next agent

Start in the active worktree and do not touch a running worker's paths:

```bash
git status --short
git diff -- uv.lock
/home/hillel/projects/Alpacatrader/.venv/bin/python factory/scripts/basket_sim.py --self-test
```

When a worker-complete system notification arrives, retrieve the exact result via
`background_output(task_id=...)`, then independently read every changed source/test/README and
run its focused tests plus a direct artifact audit. Do not mark a family complete from its
self-report alone.

### 17.12 Current task ledger

1. Baseline/integration-1 validation: complete.
2. F3 release surface + structural map + economic synthesis: complete, locally audited in part;
   reverify the economics producer before integration.
3. Golden-window capital-allocation map: generated; needs normal independent source/artifact
   verification before any follow-up predicate is declared.
4. F5 full repair/re-run: active worker, do not interrupt.
5. F4 per-ticket scale-out: active worker, do not interrupt.
6. F6/F7 dynamic capital core + surfaces: queued after F5, then use F3/F4/golden evidence without
   cherry-picking.
7. Integration-2: queued after family results; it must decide what genuinely earns inclusion in a
   causal architecture.

**Bottom line:** pursue EV extraction aggressively, not process for process's sake. Preserve the
right tail; make failures cheap; let the golden window and joint-survivor structure inform capital
only through causal, execution-correct incremental tests. Use strong agents for full economic
lanes, inspect their code and evidence, and integrate only what earns its place.

## 18. 2026-09-24 BASKET Phase-2 restart handoff (current override)

**Read this section after the repository orientation and before doing any BASKET work.** It
overrides the stale worker statuses in section 17.12. The active worktree is:

```text
/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1
```

There are no running BASKET worker processes at this handoff. F4 completed and its worker result
was collected. F5 was explicitly cancelled only after its core simulations finished and its
post-processing had demonstrably stopped making progress. No commit was made.

### 18.1 Objective and non-negotiable measurement contract

The project objective remains a profitable, execution-realistic top-gainer/momentum mechanism.
BASKET Phase 2 is not an exercise in classifier accuracy or loss minimization. The desired
architecture must preserve exceptional right-tail survivors while reducing ordinary failure cost
and deploying capital only where incremental **net** EV earns it.

Keep these rules fixed unless a new pre-registered formulation explicitly replaces them:

- Use only the canonical 1,066 development days:
  - block 1: `2021-02` through `2023-12`, 734 days / 35 months.
  - block 2: `2025-02` through `2026-05`, 332 days / 16 months.
- Do not load sealed `2024`, sealed `2025-01`, or reserved `2026-06..08`.
- Decisions see completed-bar state only. All actions fill at a strictly later, eligible bar open.
- `C0=1`, no leverage, no invented partial fills, and explicit cash/deployed accounting.
- Evaluate both 100 and 150 bps round-trip friction; distinguish realized from marked open P&L.
- Require full finite surfaces, both blocks, calendar/month coverage, no temporary artifacts, and
  retained negative/null rows. Never cherry-pick a cell into a strategy.
- The relevant asymmetry is that prematurely releasing a real +50%/+100%/+300% survivor can cost
  much more than carrying an ordinary failure briefly. Tail shares/MFE are opportunity measures,
  not executable P&L or a substitute for tranche economics.

Read `researches/INTENT.md`, `researches/STATE.md`, `researches/HYPOTHESES.md`, the tail of
`factory/STATE.md`, this handoff, `researches/PRE-REG-BASKET-02.md`, and
`factory/BASKET-SIM-CONTRACT.md` before changing research logic.

### 18.2 Worktree and evidence hygiene

The worktree is intentionally dirty. At this handoff, `HANDOFF.md`, `factory/STATE.md`, and
`uv.lock` are modified; BASKET scripts/tests/artifacts and canonical SIP data are untracked in
this checkout. These are not permission to bulk-add, reset, checkout, revert, clean, or commit.
Do not touch `uv.lock` unless a change can be attributed to this work; it is not research output.

- Use the absolute active-worktree path above in every agent prompt. Several earlier agents used
  clean sibling checkouts and falsely reported active untracked paths missing.
- Use `/home/hillel/projects/Alpacatrader/.venv/bin/python` for focused project checks. `uv run`
  may incidentally dirty `uv.lock`; use frozen/no-sync invocations only if it is needed for
  read-only data inspection.
- LSP diagnostics reject this isolated worktree as outside the configured request cwd. Record that
  limitation; use focused pytest, `py_compile`, simulator self-test, deterministic canaries, and
  direct artifact audits instead of claiming LSP is clean.
- Long computations must write incrementally and resume. But generated output is not accepted from
  an agent self-report: independently verify the exact code, tests, calendar, grid, parts, hashes,
  and economics after it is stable.
- Do not modify `factory/scripts/basket_sim.py` while doing F5 recovery. F6/F7 are the later,
  intentional shared-core owners.

### 18.3 Baseline, F3, and descriptive capital-map state

Phase 1 is closed. Do not rerun broad existence tests unless a concrete measurement defect is
found. The known baseline work is:

- F1: 120 `validated_frozen` cells over all 1,066 days; primitive net surface is negative at 100
  and 150 bps.
- F2/F12: causal 182,094-row, 51-column state panel with the five frozen entry families and
  checkpoints `{580,585,590,600,615}`. Outcomes are strictly later than checkpoint.
- F8: multi-survivor/joint sleeve economics show concurrent right-tail opportunity is material.
- F9/F10/F11/F14: descriptive weighting/environment/execution maps only; no executable gate or
  promotion was declared.
- `factory/artifacts/basket/phase2/INTEGRATION_1/promotion_map.json` says
  `measurement_integration_only_no_strategy_promotion` and preserves the dependency direction
  F3 -> F5 -> F4 -> F6 -> F7.

F3 is complete as a descriptive release-cost lane:

- `factory/scripts/basket_f3_sim.py`: 20 fixed A_pm/T570/N2 R0/R2/R3 cells at both frictions.
- `factory/scripts/basket_f3_structural_map.py`: causal 673,952-row own-path map in 51 monthly
  parts; direct audit found 20 cells, 1,066 days/cell, 51 map parts, and no temporary artifacts.
- `factory/artifacts/basket/phase2/F3/acceptance_bridge.md` explicitly says the map supplies
  vocabulary, not an outcome-fitted module choice.
- Earlier independent check passed:
  `pytest -q tests/test_basket_f3_sim.py tests/test_basket_f3_structural_map.py` = 18 passed;
  simulator self-test and F3 compilation passed.
- F3 economics synthesis was generated in `basket_f3_economics.py` and
  `factory/artifacts/basket/phase2/F3/`, but its source/artifact acceptance should be repeated
  before it is used in Integration 2. Agent-reported direction: R2 reduces failure loss more but
  gives up more ticket-tail than R3; all F3 mean C0 outcomes remain negative.

The golden-window capital allocation map exists at
`factory/scripts/basket_capital_map.py` and
`factory/artifacts/basket/phase2/capital_allocation_map/`. It reports 78,088 eligible ET585/600
observations and does not fabricate B600-at-ET585. It is descriptive only: no F2/F12/golden gate,
allocation predicate, or capital deployment rule has been promoted.

### 18.4 F4 final per-ticket scale-out lane

F4 is now a completed **per-ticket** economic lane, but no strategy was promoted.

Source ownership:

- `factory/scripts/basket_f4_scaleout.py`: runner, provenance, and audit.
- `factory/scripts/basket_f4_rules.py`: finite cell/trigger definitions and local release rules.
- `factory/scripts/basket_f4_paths.py`: raw path map and executed action chronology.
- `factory/scripts/basket_f4_metrics.py` and `basket_f4_blocks.py`: report metrics.
- `tests/test_basket_f4_scaleout.py`.
- `factory/artifacts/basket/phase2/F4/`.

The registered grid is exactly 74 cells:

- Fixed anchor `A_pm`, ET570, N=2, two equal slots, `C0=1`, no leverage.
- Nine F3-vocabulary triggers: R2 with `L={10,15}` and `w={3,5,10}`, plus R3 with
  `g={40,50,60}`.
- Partial current-share patterns: `25 -> 25 -> rest`, `33 -> 33 -> rest`, and `50 -> rest`.
- 54 partials, 18 same-trigger full exits, and two hold controls; every cell at 100/150 bps.
- A pending action blocks a new stage until execution. R2 preserves adverse
  `min(next_open, level)` execution; R3 uses next eligible open. The core simulator owns release
  precedence, carry, forced flat, friction, cash, and deterministic action ordering.

Final stable artifact snapshot checked directly after worker completion:

- `surface.json` has 74 rows: one hold, nine full exits, and 27 partials per friction.
- `coverage_audit.json` exists; `README.md` exists and documents the full calendar/part contract.
- Earlier direct audit saw exact 1,066 days/cell, 51 monthly daily/ticket parts/cell, action
  chronology where required, and no temporary files. Earlier focused F4 pytest passed 17 tests.
- The F4 worker reports 36 focused F4/F3/simulator tests, simulator self-test, compilation,
  deterministic canary, and F3-parity checks passed. Treat that as a claim until a fresh
  independent final test/audit is run against the stable final sources/artifacts.
- During the worker's final report refresh, `surface.json` was temporarily observed at 68 rows,
  missing six R3_g60 partial rows. Do not reuse that snapshot or its preliminary aggregation. The
  current post-completion snapshot is back to the exact 74-row registered grid.

F4 economic result from its final README:

- Every partial treatment improves pooled EV versus hold, but every partial treatment remains
  negative. At 100 bps the partial mean C0 range is -4.01% to -3.05%/basket-day; at 150 bps it is
  -4.48% to -3.53%.
- Partial scale-out usually loses to full exit at the same trigger: 12/27 partial cells beat
  matched full exit on pooled EV at each friction, but only one
  `R2(L=15,w=5), 50 -> rest` cell beats it in both blocks. That one cell is not a selection rule.
- Partial reductions improve failed-ticket loss versus hold, but reduced tranches themselves are
  negative on average at both frictions. At raw +50% MFE touch, 84.6-100% of shares remain, yet
  retained shares do not make sold tranches profitable or establish tail capture.
- The correct conclusion is no F4 promotion: partials soften failure cost relative to holding, but
  are not a stable family-level improvement over same-trigger full exit and preserve insufficient
  tranche EV at realistic friction.

F4 deliberately does **not** implement basket-level simultaneous de-risking K={2,3}; that needs
the later shared batch callback. Do not create a local alternate simulator to fake it.

### 18.5 F5 state: core simulations preserved, reconciliation cancelled and broken

F5 tests causal own-ticket continuation adds; it is not a golden-window gate.

Registered full grid in `factory/scripts/basket_f5_scalein.py`:

- Five F1 entry forms: `A_pm`, `A_pm31`, `A_open` at ET570, B at ET585, B at ET600.
- N={2,3,4}; exits hold R0 or R1(-8/-10/-15).
- Add schedules `[]`, `[25]`, `[50]`, `[100]`, `[25,25]`, `[25,50]`, `[50,25]`, fractions of the
  original ticket notional, capped at +100% cumulative original-unit add.
- Both 100 and 150 bps: 5 * 3 * 4 * 7 * 2 = **840 cells**.
- Trigger is strict own-ticket completed-bar `high > prior running peak`, evaluated before peak
  update, with execution at the first later open. Existing reservation logic records an early
  high before all selected entries fill as `pending_entry_cash_reserved` and must not consume the
  staged add index.

The original F5 worker was stopped because it was inactive, not because core simulation was slow:

- All 840 cell `run_summary.json` files had `days_n=1066`; the initial `surface.json` contains
  all 840 core rows and only the core keys `family_id`, `cells`, `completed_cells`, and
  `expected_cells`.
- At the stop snapshot, 168 `add_events.json` files existed, zero `executed_tranches.json` files
  existed, no F5 `README.md` existed, and there was no `coverage_audit.json`.
- `surface.json` last modified at 2026-09-24T11:36:22+03:00; at 12:23 no Python process existed
  and no output had advanced. The F5 background task was explicitly cancelled after user approval.
- No one reran the 840 core simulations after cancellation. Preserve existing simulator outputs:
  `daily.parquet`, `tickets.parquet`, `run_summary.json`, configs, and monthly parts.

Confirmed F5 reconciliation defects in `factory/scripts/basket_f5_scalein.py`:

1. `_complete_surface()` lines 289-308 loads a variable named `tickets` for each initial cell, but
   its second loop (lines 309-337) calls `_add_event_outcomes(..., tickets.to_dicts(), ...)` without
   loading the current cell's tickets. Python therefore passes tickets from the **last** row of the
   first loop to every event reconstruction. This is a correctness defect, not merely speed.
2. `_add_event_outcomes()` lines 377-395 rebuilds a day/ticker view by calling
   `pl.read_parquet(sim.BARS_DIR / f"{day}.parquet")` for every event day in every add cell. It
   has no cross-cell batching/cache. After 840 simulations, this can reread canonical raw bars an
   enormous number of times. It is the likely reason the reconciliation phase appeared stuck, but
   no terminal traceback/log from the cancelled agent was collected, so do not claim a specific
   exception without reproducing it.
3. Existing F5 tests cover strict highs, reservation behavior, grid size, next-open fills,
   `add_event_outcomes`, and unfunded/cap skips. They do **not** prove that `_complete_surface()`
   uses each current cell's ticket table, nor that bar loading is bounded/reused.

The cancelled F5 worker must be replaced by a small TDD repair, not a new full simulation run:

- First add a RED regression that creates two distinct cell ticket tables and proves
  reconciliation passes the current cell's tickets to event accounting; it must fail on the stale
  variable implementation.
- Add a separate focused test for safe reuse/batching of immutable canonical day-bar data. Do not
  change event timing, selected columns, session-end filtering, friction, or terminal mark/exit
  math.
- Implement a reconciliation-only entry point that loads and validates the 840 already-complete
  core rows. It must never call `_clean_cell_outputs`, `sim.run`, or overwrite core daily/ticket
  files, summaries, or monthly parts.
- Correct current-cell ticket loading in the second loop. Use atomic writes for derived
  `add_events.json`, `executed_tranches.json`, final `surface.json`, and README; it must be safe
  to rerun after interruption. A persistent reconciliation manifest was intentionally not chosen;
  idempotent atomic derived-file replacement is the desired minimum.
- Design bar reuse from actual memory/data-size evidence. A naive cache of all canonical days may
  be too large; a bounded cache that thrashes will not solve the repeated-load cost. The correct
  small approach may batch event reconstruction by day/ticker before reading bars, but prove it
  with a focused test and do not introduce a framework.
- After repair, regenerate derived evidence for all 840 existing cells, require all 720 add cells
  to have valid decision/event/tranche evidence, run the surface validator, write `README.md`, and
  independently audit exact rows/days/month parts/no temporary files. Only then interpret F5 EV.

The uncompleted planning subagent asked whether atomic derived-file replacement was acceptable;
the intended answer was yes, but the plan call was interrupted when the user requested this
handoff. No recovery code was written, no test was added, and no artifacts were deleted.

### 18.6 F6, F7, and Integration 2 remain queued

Do not begin these until F5 is repaired and independently accepted, and do not edit the shared
simulator during F5 recovery.

F6 requires a real shared batch checkpoint hook in `factory/scripts/basket_sim.py`:

- `reserve_frac` currently only constrains initial entry; it does not deploy reserve.
- At ET585/600, after all ticket decisions for the completed bar, an ordered batch callback must
  choose currently eligible open survivors and schedule normal later-open ADDs.
- Pending releases/reductions exclude recipients. Cash, cap, priority, carry/no-resumption,
  forced-flat precedence, `cash>=0`, and `deployed<=C0` remain engine-owned.
- Test p={0.67,0.50,0.33} against full-deployment p=1, ET585/600, and cash vs equal-survivor
  policies. No golden gate exists yet.

F7 may recycle only a demonstrated F3/F4 release path, with recycle fractions {0,50,100}% versus
cash/equal survivors. Measure incremental recycled-tranche net EV. It is not permission to pour
released cash into rank 1.

`INTEGRATION_2` must synthesize, not concatenate. For each candidate module it must establish the
economic question, causal implementation, actual capital/fill assumptions, failure-cost change,
tail/multi-survivor effect, both-block/friction/nearby-variant stability, capital utilization, and
independent contribution. The likely honest outcome remains no promotion unless the stable
incremental net evidence says otherwise.

### 18.7 Restart protocol and verification commands

Before any edit, inspect the current state rather than trusting this prose blindly:

```bash
git status --short
git diff -- HANDOFF.md factory/STATE.md uv.lock
/home/hillel/projects/Alpacatrader/.venv/bin/python factory/scripts/basket_sim.py --self-test
```

Start with F4 final independent acceptance, read all F4 producer/test files, then run at least:

```bash
/home/hillel/projects/Alpacatrader/.venv/bin/python -m pytest -q tests/test_basket_f4_scaleout.py
/home/hillel/projects/Alpacatrader/.venv/bin/python -m py_compile \
  factory/scripts/basket_f4_scaleout.py factory/scripts/basket_f4_rules.py \
  factory/scripts/basket_f4_paths.py factory/scripts/basket_f4_metrics.py \
  factory/scripts/basket_f4_blocks.py
```

Then repair F5 test-first. Use only focused F5 tests while changing F5, and add a reconciliation
canary that copies a minimal completed core-cell fixture rather than touching canonical F5 output.
After its full derived-output refresh, run the F5 suite, simulator self-test, source compilation,
and a direct 840-cell artifact audit. Update `factory/STATE.md`,
`researches/HYPOTHESES.md`, experiment ledgers, and this handoff only after actual independent
acceptance.

**Current decision state:** F4 is a negative/no-promotion per-ticket scale-out result despite
failure-loss improvement. F5 has no valid reconciled economics yet. No F6/F7 experiment or
Integration 2 decision exists. Do not accidentally convert descriptive tail observations, a
single F4 exception, or incomplete F5 output into a strategy claim.
