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
  compare live vs sim → decay dashboard → (then) tiny live money decision.
- **Context discipline (user-instructed)**: **compress big and often.** See §13. Do not let the window balloon.

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
