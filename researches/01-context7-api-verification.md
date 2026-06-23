# Context7 API Verification Report

**Date**: 2026-06-13
**Agent**: Subagent A — Context7 Librarian
**Scope**: Third-party library API usage in `src/`, `config/`, `tests/`

---

## Docs Consulted

| Library | Source | ID |
|---|---|---|
| alpaca-py v0.43+ | Context7 + GitHub README/docs | `/alpacahq/alpaca-py` |
| yfinance v0.2.40+ | Context7 + GitHub docs/wiki | `/ranaroussi/yfinance` |
| pydantic v2 | Context7 + GitHub migration docs | `/pydantic/pydantic` |
| pydantic-settings v2 | Context7 + GitHub docs | `/pydantic/pydantic-settings` |
| beautifulsoup4 v4.12+ | Context7 + GitHub docs | `/wention/beautifulsoup4` |
| loguru v0.7+ | Context7 + GitHub API docs | `/delgan/loguru` |
| click v8.1+ | Context7 + GitHub docs | `/pallets/click` |
| lxml | Not in dependencies; not imported | N/A |
| litellm | Not in dependencies; not imported | N/A |

---

## Verified API Table

### alpaca-py — `src/paper_execution.py`, `src/market_data.py`, `src/market_data_sim.py`

| API Call | File:Line | Status | Notes |
|---|---|---|---|
| `TradingClient(api_key, secret_key, paper=True)` | `paper_execution.py:313` | ✅ Current | Constructor params match docs |
| `LimitOrderRequest(symbol, qty, side, time_in_force, limit_price)` | `paper_execution.py:343-349` | ✅ Current | Standard limit order request |
| `StopOrderRequest(symbol, qty, side, time_in_force, stop_price)` | `paper_execution.py:409-415` | ✅ Current | Standard stop order request |
| `MarketOrderRequest(symbol, qty, side, time_in_force)` | `paper_execution.py:452-456` | ✅ Current | Standard market order request |
| `GetOrdersRequest(status, side)` | `paper_execution.py:379` | ⚠️ Imported but unused | `QueryOrderStatus` imported but never passed; `get_order_by_id()` used instead |
| `client.submit_order(order_data)` | `paper_execution.py:350, 416, 458` | ✅ Current | Returns `Order` object with `.id` |
| `client.get_order_by_id(order_id)` | `paper_execution.py:381, 484` | ✅ Current | Direct lookup by order ID |
| `StockHistoricalDataClient(api_key, secret_key)` | `market_data.py:92`, `market_data_sim.py:54` | ✅ Current | Constructor matches docs |
| `StockLatestQuoteRequest(symbol_or_symbols=...)` | `market_data.py:96` | ✅ Current | Parameter name correct |
| `StockBarsRequest(symbol_or_symbols=..., timeframe=..., limit=...)` | `market_data.py:121-125`, `market_data_sim.py:63-68` | ✅ Current | Uses `TimeFrame.Minute` |
| `client.get_stock_latest_quote(req)` | `market_data.py:97` | ✅ Current | Returns dict keyed by symbol |
| `client.get_stock_bars(req)` | `market_data.py:126`, `market_data_sim.py:70` | ✅ Current | Returns `BarSet` with `.data` dict |
| `OrderSide.BUY` / `OrderSide.SELL` | `paper_execution.py:346, 412, 455` | ✅ Current | Enum values unchanged |
| `TimeInForce.DAY` / `TimeInForce.GTC` | `paper_execution.py:347, 413, 456` | ✅ Current | Enum values unchanged |
| `QueryOrderStatus.OPEN` | `paper_execution.py:380` | ⚠️ Imported but never used | Unused import — harmless but dead code |
| `alpaca_order.id`, `.status`, `.filled_avg_price`, `.filled_qty` | `paper_execution.py:351, 382-384, 485-487` | ✅ Current | Order response attributes match docs |
| `bar_set.data.get(symbol, [])` | `market_data.py:127`, `market_data_sim.py:71` | ✅ Current | BarSet data access pattern correct |

### yfinance — `src/scanner/enrichment.py`

| API Call | File:Line | Status | Notes |
|---|---|---|---|
| `yf.Ticker(sym)` | `enrichment.py:214` | ✅ Current | Constructor unchanged |
| `t.fast_info` | `enrichment.py:215` | ✅ Current | Cacheable property since v0.2.6 |
| `fi.quote_type` | `enrichment.py:218` | ✅ Current | String field |
| `fi.previous_close` | `enrichment.py:222` | ✅ Current | Float field |
| `fi.last_price` | `enrichment.py:223` | ✅ Current | Float field |
| `fi.last_volume` | `enrichment.py:224` | ✅ Current | Int field |
| `fi.exchange` | `enrichment.py:243` | ✅ Current | String field |
| `t.info` | `enrichment.py:232` | ✅ Current | Dict — fallback for metadata fields |
| `t.info.get("longName", ...)` | `enrichment.py:235` | ✅ Current | Standard dict access |

### pydantic v2 — `src/models/schemas.py`, `config/settings.py`

| API Call | File:Line | Status | Notes |
|---|---|---|---|
| `BaseModel` | `schemas.py:18` | ✅ Current | Core v2 base class |
| `ConfigDict(frozen=True)` | `schemas.py:129, 150, 236` | ✅ Current | v2 replacement for `Config.frozen = True` |
| `ConfigDict(frozen=False)` (default) | `schemas.py:207` | ✅ Current | Mutable model for position state |
| `Field(default=..., ge=..., le=...)` | `schemas.py:99+` | ✅ Current | v2 field declaration |
| `field_validator` | `schemas.py:122, 143`; `settings.py:13, 67, 78, 98, 132, 139` | ✅ Current | v2 replacement for `@validator` |
| `model_validator(mode="after")` | `schemas.py:191` | ✅ Current | v2 replacement for `@root_validator` |
| `model_dump_json()` | `schemas.py:338` | ✅ Current | v2 replacement for `.json()` |
| `model_copy(update=...)` | `market_data.py:118` | ✅ Current | v2 replacement for `.copy(update=...)` |
| `model_validate()` / `cls(**data)` | `schemas.py:344` | ✅ Current | v2 pattern |
| `pydantic-settings.BaseSettings` | `settings.py:15, 24` | ✅ Current | v2 settings base |
| `SettingsConfigDict(env_prefix=..., extra=...)` | `settings.py:87, 108, 146, 156` | ✅ Current | v2 config dict |
| `settings_customise_sources()` | `settings.py:28-36` | ✅ Current | v2 API for source ordering |

### beautifulsoup4 — `src/scanner/enrichment.py`

| API Call | File:Line | Status | Notes |
|---|---|---|---|
| `BeautifulSoup(resp.text, "html.parser")` | `enrichment.py:88` | ✅ Current | Uses stdlib parser; no lxml needed |
| `soup.find("table", class_="styled-table-new")` | `enrichment.py:89` | ✅ Current | Standard BS4 search |
| `soup.find("table")` | `enrichment.py:91` | ✅ Current | Fallback if class not found |
| `table.find_all("tr")[1:]` | `enrichment.py:96` | ✅ Current | Skip header row |
| `row.find_all("td")` | `enrichment.py:98` | ✅ Current | Cell extraction |
| `cols[N].get_text(strip=True)` | `enrichment.py:103, 107, 110, 113, 121-127` | ✅ Current | Text extraction |

### loguru — `main.py`, `src/*.py`, `tests/*.py`

| API Call | File:Line | Status | Notes |
|---|---|---|---|
| `logger.remove()` | `main.py:35` | ✅ Current | Clears default handler |
| `logger.add(sys.stderr, format=..., level=..., colorize=True)` | `main.py:36-38` | ✅ Current | Stderr sink with formatting |
| `logger.add(path, format=..., rotation=..., retention=..., compression=...)` | `main.py:39-41` | ✅ Current | File sink with rotation/retention |
| `logger.exception("...")` | `paper_execution.py:353, 389, 419` | ✅ Current | Logs traceback + message |
| `logger.warning("fmt {}", arg)` | `market_data.py:190`, `enrichment.py:79` | ✅ Current | Positional formatting |
| `logger.info("fmt {}", arg)` | `app.py:369-374` | ✅ Current | Positional formatting |
| `import loguru` (module-level) | `decision_pipeline.py:45` | ✅ Works but atypical | Uses `loguru.logger.exception(...)` instead of `from loguru import logger` |

### click v8.1+ — `main.py`, `tests/test_cli_rebuild.py`

| API Call | File:Line | Status | Notes |
|---|---|---|---|
| `@click.command()` | `main.py:44` | ✅ Current | Standard decorator |
| `@click.option("--mode", type=click.Choice([...]))` | `main.py:45-46` | ✅ Current | Choice validation |
| `@click.option("--once/--loop", default=True)` | `main.py:47` | ✅ Current | Boolean flag pattern |
| `@click.option("--config", "-c")` | `main.py:48` | ✅ Current | String option |
| `click.echo(...)` | `main.py:57` | ✅ Current | Print helper |
| `CliRunner().invoke(main_cli, args)` | `test_cli_rebuild.py:35` | ✅ Current | Test runner |

### Libraries NOT Found in Codebase

| Library | Status |
|---|---|
| `litellm` | ❌ Not in `requirements.txt`, `pyproject.toml`, or any `.py` import |
| `lxml` | ❌ Not in `requirements.txt`, `pyproject.toml`, or any `.py` import. BeautifulSoup uses stdlib `html.parser` |
| `openai` / `anthropic` (LLM providers) | ❌ Not imported anywhere |

---

## Deprecated / Risky Usage

### 🔴 Deprecated

**None found.** All library APIs are current versions. Specific wins:
- Pydantic v1 → v2 migration is complete: no `.dict()`, `.json()`, `.copy()`, or `@validator`/`@root_validator` usage.
- yfinance uses `fast_info` (v0.2.6+ attribute migration) instead of raw `info` for price/volume data.
- alpaca-py uses `paper=True` kwarg (modern pattern).

### 🟡 Risky / Suboptimal

1. **`QueryOrderStatus` imported but unused** — `paper_execution.py:380`
   - `from alpaca.trading.enums import QueryOrderStatus` is imported in `confirm_fill()` on line 380 but never referenced. Dead import. Could be removed.

2. **Lazy inline imports of alpaca-py** — `paper_execution.py:312, 341-342, 379-380, 407-408, 450-451`
   - Each method does `from alpaca.trading... import ...` inside the method body (not at module level). This is intentional per comments (avoids websocket DeprecationWarning at import time), but makes the code ~50% imports, obscures dependency visibility, and slows hot-path execution.

3. **`confirm_fill` fallback simulation** — `paper_execution.py:369-397`
   - On API failure, `confirm_fill` catches the exception, does `time.sleep(0.5)`, and proceeds with a simulated fill rather than retrying or raising. This could silently mask network issues during paper trading.

4. **No OCO / bracket / stop-limit orders** — `paper_execution.py`
   - The codebase uses only single-leg `LimitOrderRequest`, `StopOrderRequest`, and `MarketOrderRequest`. No `TakeProfitRequest`, `StopLossRequest`, or bracket order support. The `OrderActionType.OCO` enum member is defined in `schemas.py:71` but never instantiated — dead enum value.

5. **yfinance sequential ticker calls** — `enrichment.py:212-246`
   - Iterates 18 tickers sequentially, each doing one `yf.Ticker()` + `fast_info` + optional `t.info` API call. No parallelization or delay staggering. Yahoo rate-limiting risk on rapid restarts.

6. **f-string in loguru calls** — `enrichment.py:71, 75, 79`
   - Uses `logger.debug(f"Finviz returned {resp.status_code}")` — f-strings are evaluated eagerly even if the log level discards the message. Loguru recommends `logger.debug("Finviz returned {}", resp.status_code)` for deferred formatting. Minor perf impact only.

7. **`import loguru` (module level) not `from loguru import logger`** — `decision_pipeline.py:45`
   - Uses `loguru.logger.exception(...)` on lines 350, 368. Works correctly but is inconsistent with the 9 other files that all use `from loguru import logger`. Cosmetic inconsistency.

---

## Required Spec Implications

### Order Lifecycle / Brackets / OCO (SPEC §14-15)

- **Current**: Single-leg limit entries (day), stop-loss (GTC), market exits (day). No bracket orders, no OCO, no stop-limit, no trailing stop.
- **Gap**: The `OrderActionType.OCO` enum exists (`schemas.py:71`) but is dead code. If the spec requires OCO (e.g., entry + stop-loss + take-profit as a bracket), implement using `TakeProfitRequest`/`StopLossRequest` from `alpaca.trading.requests`, or use the `parent_order_id` mechanism for OCO groups. `PaperExecutionGateway` has no bracket simulation.

### Order Status / Fills (SPEC §15.3)

- **Current**: `confirm_fill` checks `alpaca_order.status == "filled"` and reads `filled_avg_price`/`filled_qty`. No partial-fill handling (assumes all-or-nothing).
- **Gap**: If partial fills are expected, the `filled_qty` and `filled_avg_price` should be used to update `current_shares` incrementally rather than overwriting.

### yfinance `fast_info` Attribute Stability (SPEC §22.13)

- `fast_info.quote_type`, `.previous_close`, `.last_price`, `.last_volume`, `.exchange` — confirmed stable through 0.2.40+. **Risk**: `last_price` and `last_volume` are only populated during market hours when recent trade data exists. Off-hours the values may be `None`. The code handles `None` via `last_price or 0` patterns with guard clauses — adequate.
- **Note**: `fast_info` fetches from Yahoo's v7/finance API which has no SLA. Rate limiting (429s) will cause `Ticker.fast_info` to raise. The current code catches all exceptions (`except Exception: continue`) — silent failure.

### Pydantic v2 Stability (SPEC §1)

- All Pydantic models use confirmed v2 APIs. `model_dump_json()` is the current serialization method. No breaking-change risk from v1→v2 migration.
- **No risk of `model_dump_json` argument changes** — the method signature is stable through v2.x.

### BeautifulSoup Parser (SPEC §2.3)

- Uses `"html.parser"` (stdlib). No `lxml` dependency needed or expected. **Risk**: `html.parser` is less forgiving of malformed HTML than `lxml` — if Finviz changes their HTML significantly, the parser may produce incomplete results silently. The code defensively falls back to `soup.find("table")` if the known class is missing.

### click CLI Decorators (SPEC §22.16)

- All click usage is standard v8.1+. `@click.option("--once/--loop", default=True)` is the correct boolean-flag pattern. No deprecated APIs.
- `CliRunner` usage in tests matches current click test patterns.

### litellm — Required per instructions, absent from codebase

- **Not imported, not in dependencies.** If LLM integration is planned (e.g., for catalyst analysis or decision augmentation), this is a zero-state gap. The `.env.example` has no LLM-related vars.
