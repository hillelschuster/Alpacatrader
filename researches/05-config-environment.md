# Config & Environment Audit — Batch F

**Date:** 2026-06-13
**Scope:** `config/`, `.env`, `.env.example`, `src/`, `tests/`, `pyproject.toml`, `requirements.txt`
**Auditor:** Subagent E

---

## 1. Config Inventory

### 1.1 `config/settings.py` — Pydantic Settings classes

| Section | Class | Fields | Env Prefix | File:Line |
|---------|-------|--------|------------|-----------|
| `trading` | `TradingSettings` | `mode`, `live_trading_confirmed` | `TRADING_` | `config/settings.py:61-88` |
| `logging` | `LoggingSettings` | `level`, `dir`, `format`, `retention_days` | `LOGGING_` | `config/settings.py:90-108` |
| `phase1` | `Phase1Settings` | `max_quote_age_seconds`, `fresh_quote_seconds`, `scanner_interval_seconds`, `monitor_interval_seconds`, `max_candidates`, `focus_price_min`, `focus_price_max`, `starter_risk_pct`, `max_trade_risk_pct`, `max_daily_loss_pct`, `max_positions`, `max_open_risk_pct` | `PHASE1_` | `config/settings.py:111-146` |
| root | `Settings` | `trading`, `logging`, `phase1` | (none, reads `.env`) | `config/settings.py:149-193` |

### 1.2 `config/default_config.yaml` — YAML defaults

| Section | Keys | File:Line |
|---------|------|-----------|
| `trading` | `mode`, `live_trading_confirmed` | `config/default_config.yaml:11-13` |
| `logging` | `level`, `dir`, `format`, `retention_days` | `config/default_config.yaml:15-19` |
| `phase1` | 12 fields (all of Phase1Settings except validators) | `config/default_config.yaml:24-41` |

YAML mirrors the Settings model exactly — no stale keys.

---

## 2. Env Key Inventory

### 2.1 `.env` (actual, git-ignored) — key names only, values redacted

| Key | Present | Corresponding Setting | Notes |
|-----|---------|----------------------|-------|
| `ALPACA_API_KEY` | ✅ | None (raw `os.getenv` in src) | Read by 3 src files |
| `ALPACA_SECRET_KEY` | ✅ | None (raw `os.getenv` in src) | Read by 3 src files |
| `ALPACA_PAPER` | ✅ | None | **Dead** — not read anywhere |
| `ALPACA_BASE_URL` | ✅ | None | **Dead** — not read anywhere |
| `ALPACA_DATA_URL` | ✅ | None | **Dead** — not read anywhere |
| `LLM_MODEL` | ✅ | None | **Dead** — not read anywhere |
| `LLM_API_KEY` | ✅ | None | **Dead** — not read anywhere |
| `TRADING_MODE` | ✅ | `TradingSettings.mode` (`TRADING_` prefix) | Works |
| `LIVE_TRADING_CONFIRMED` | ✅ | `TradingSettings.live_trading_confirmed` | **WRONG PREFIX** — should be `TRADING_LIVE_TRADING_CONFIRMED` per `env_prefix="TRADING_"` |
| `SCANNER_POLL_INTERVAL_SECONDS` | ✅ | None | **Dead** — no settings field, no code reference |
| `SCANNER_MONITOR_INTERVAL_SECONDS` | ✅ | None | **Dead** — no settings field, no code reference |
| `LOGGING_LEVEL` | ✅ | `LoggingSettings.level` (`LOGGING_` prefix) | Works |
| `LOGGING_DIR` | ✅ | `LoggingSettings.dir` (`LOGGING_` prefix) | Works |
| `DATABASE_PATH` | ✅ | None | **Dead** — not read anywhere |

**Total .env keys:** 14  **Wired to Settings:** 4  **Dead/Orphaned:** 10

### 2.2 `.env.example` (template)

| Key | Comment | File:Line |
|-----|---------|-----------|
| `TRADING_MODE=paper` | Active | `.env.example:8` |
| `TRADING_LIVE_TRADING_CONFIRMED=no` | Active (correct prefix) | `.env.example:9` |
| `ALPACA_API_KEY` | Commented out | `.env.example:13` |
| `ALPACA_SECRET_KEY` | Commented out | `.env.example:14` |
| `APCA_API_KEY_ID` | Commented out (legacy) | `.env.example:17` |
| `APCA_API_SECRET_KEY` | Commented out (legacy) | `.env.example:18` |
| `ALPACATRADER_CONFIG` | Commented out (custom config path) | `.env.example:25` |
| `LOGGING_LEVEL=INFO` | Active | `.env.example:26` |
| `LOGGING_DIR=./logs` | Active | `.env.example:27` |

The `.env.example` is clean and minimal — only 3 active keys. Good hygiene.

---

## 3. Read-Site Table

Every code reference to a config key or env variable, with the field it reads and whether the source exists.

### 3.1 Settings model → Runtime

| Caller | What it uses | Settings Source | Wired? | File:Line |
|--------|-------------|----------------|--------|-----------|
| `main.py:setup_logging` | `settings.logging.dir`, `.level`, `.retention_days` | `LoggingSettings` | ✅ | `main.py:33-41` |
| `main.py:main_cli` | `settings.trading.mode` | `TradingSettings` | ✅ | `main.py:68-70` |
| `main.py:main_cli` | `settings.validate_live_trading()` | `TradingSettings` | ✅ | `main.py:73` |
| `main.py:_run_rebuild_paper` | **NONE of `settings.phase1.*`** | `Phase1Settings` | ❌ | `main.py:199-287` |
| `main.py:_run_rebuild_mock` | **NONE of `settings.phase1.*`** | `Phase1Settings` | ❌ | `main.py:106-196` |
| `main.py:_run_rebuild_sim` | **NONE of `settings.phase1.*`** | `Phase1Settings` | ❌ | `main.py:290-372` |
| `main.py:_run_paper_loop` | **NONE of `settings.phase1.*`** | `Phase1Settings` | ❌ | `main.py:375-409` |

**Finding:** `Settings.load()` is called and `trading`/`logging` are used, but `settings.phase1` is completely ignored by every code path in `main.py`. All phase1 defaults are duplicated as Python-level defaults in `decision_pipeline.py`, `app.py`, and `hard_filters.py`.

### 3.2 Hardcoded defaults (not reading settings)

| Location | Parameter | Hardcoded Value | Matching Phase1 Field | File:Line |
|----------|-----------|-----------------|----------------------|-----------|
| `run_pipeline` | `starter_risk_pct` | `0.0025` | `phase1.starter_risk_pct` | `decision_pipeline.py:201` |
| `run_pipeline` | `max_positions` | `3` | `phase1.max_positions` | `decision_pipeline.py:202` |
| `run_pipeline` | `max_open_risk_pct` | `0.03` | `phase1.max_open_risk_pct` | `decision_pipeline.py:203` |
| `run_pipeline` | `max_daily_loss_pct` | `0.03` | `phase1.max_daily_loss_pct` | `decision_pipeline.py:204` |
| `run_pipeline` | `focus_price_min` | `1.0` | `phase1.focus_price_min` | `decision_pipeline.py:205` |
| `run_pipeline` | `focus_price_max` | `50.0` | `phase1.focus_price_max` | `decision_pipeline.py:206` |
| `run_pipeline` | `equity` | `100_000.0` | (no setting — CLI arg?) | `decision_pipeline.py:200` |
| `TradingApp.__init__` | `monitor_interval_seconds` | `10.0` | `phase1.monitor_interval_seconds` | `app.py:80` |
| `TradingApp.__init__` | `scan_interval_seconds` | `30.0` | `phase1.scanner_interval_seconds` | `app.py:81` |
| `TradingApp.__init__` | `starter_risk_pct` | `0.0025` | `phase1.starter_risk_pct` | `app.py:87` |
| `TradingApp.__init__` | `max_positions` | `3` | `phase1.max_positions` | `app.py:88` |
| `TradingApp.__init__` | `max_open_risk_pct` | `0.03` | `phase1.max_open_risk_pct` | `app.py:89` |
| `TradingApp.__init__` | `max_daily_loss_pct` | `0.03` | `phase1.max_daily_loss_pct` | `app.py:90` |
| `TradingApp.__init__` | `focus_price_min` | `1.0` | `phase1.focus_price_min` | `app.py:91` |
| `TradingApp.__init__` | `focus_price_max` | `50.0` | `phase1.focus_price_max` | `app.py:92` |
| `TradingApp.__init__` | `equity` | `100_000.0` | (no setting) | `app.py:86` |
| `hard_filters.quote_age_tier` | `fresh_s` | `5.0` | `phase1.fresh_quote_seconds` | `hard_filters.py:27` |
| `hard_filters.quote_age_tier` | `max_s` | `15.0` | `phase1.max_quote_age_seconds` | `hard_filters.py:27` |
| `hard_filters.check_execution_data` | `max_quote_age_seconds` | `15.0` | `phase1.max_quote_age_seconds` | `hard_filters.py:97` |
| `hard_filters.run_hard_filters` | `max_quote_age_seconds` | `15.0` | `phase1.max_quote_age_seconds` | `hard_filters.py:238` |
| `hard_filters.check_liquidity_spread` | `min_dollar_volume` | `100_000.0` | (no setting) | `hard_filters.py:126` |
| `sizing.entry_sizing` | `max_trade_risk_pct` | `0.01` | `phase1.max_trade_risk_pct` | `sizing.py:84` |

### 3.3 Raw `os.getenv` reads (bypassing Settings)

| Env Var | Read in | File:Line | Settings Equivalent? |
|---------|---------|-----------|---------------------|
| `ALPACA_API_KEY` | `market_data.py`, `market_data_sim.py`, `paper_execution.py` | `:80,:47,:305` | No — no BrokerSettings |
| `APCA_API_KEY_ID` (fallback) | same 3 files | `:80,:47,:305` | No — legacy fallback |
| `ALPACA_SECRET_KEY` | same 3 files | `:81,:48,:306` | No |
| `APCA_API_SECRET_KEY` (fallback) | same 3 files | `:81,:48,:306` | No — legacy fallback |
| `ALPACATRADER_CONFIG` | `config/settings.py` | `:43` | Special — config path override |

---

## 4. Missing / Dead Keys

### 4.1 Dead Config Keys (defined in Settings but never read by runtime)

| Key | Defined In | Last Read | Verdict |
|-----|-----------|-----------|---------|
| `phase1.max_candidates` | `settings.py:121`, `yaml:32` | **Never** by runtime | **Dead** — scanner doesn't use it |
| `phase1.fresh_quote_seconds` | `settings.py:116`, `yaml:27` | Hardcoded to `5.0` in `hard_filters.py:27` | **Zombie** — value exists in config but hardcoded in src |
| `phase1.max_trade_risk_pct` | `settings.py:127`, `yaml:38` | Hardcoded to `0.01` in `sizing.py:84` | **Zombie** — value exists in config but hardcoded in src |
| `phase1.scanner_interval_seconds` | `settings.py:117`, `yaml:28` | Hardcoded to `30.0` in `app.py:81` | **Zombie** — value exists in config but not wired |
| `phase1.monitor_interval_seconds` | `settings.py:118`, `yaml:29` | Hardcoded to `10.0` in `app.py:80` | **Zombie** — value exists in config but not wired |

### 4.2 Dead Env Keys (in `.env` but no reader)

| Key | In `.env` | Read Anywhere? | Verdict |
|-----|-----------|---------------|---------|
| `ALPACA_PAPER` | ✅ | ❌ | **Dead** — leftover from v0.3 |
| `ALPACA_BASE_URL` | ✅ | ❌ | **Dead** — leftover from v0.3; `paper_execution.py` doesn't use it |
| `ALPACA_DATA_URL` | ✅ | ❌ | **Dead** — leftover from v0.3; `market_data.py` doesn't use it |
| `LLM_MODEL` | ✅ | ❌ | **Dead** — legacy LLM features removed (Batch E) |
| `LLM_API_KEY` | ✅ | ❌ | **Dead** — legacy LLM features removed (Batch E) |
| `SCANNER_POLL_INTERVAL_SECONDS` | ✅ | ❌ | **Dead** — no scanner setting references this |
| `SCANNER_MONITOR_INTERVAL_SECONDS` | ✅ | ❌ | **Dead** — no scanner setting references this |
| `DATABASE_PATH` | ✅ | ❌ | **Dead** — no database persistence yet |
| `LIVE_TRADING_CONFIRMED` | ✅ | ⚠️ Wrong prefix | **Misnamed** — should be `TRADING_LIVE_TRADING_CONFIRMED` to match `env_prefix` |

### 4.3 Missing Env Keys (in settings but not in `.env.example`)

| Key | In Settings? | In `.env.example`? | Severity |
|-----|-------------|-------------------|----------|
| `PHASE1_MAX_QUOTE_AGE_SECONDS` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_FRESH_QUOTE_SECONDS` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_SCANNER_INTERVAL_SECONDS` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_MONITOR_INTERVAL_SECONDS` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_MAX_CANDIDATES` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_FOCUS_PRICE_MIN` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_FOCUS_PRICE_MAX` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_STARTER_RISK_PCT` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_MAX_TRADE_RISK_PCT` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_MAX_DAILY_LOSS_PCT` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_MAX_POSITIONS` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `PHASE1_MAX_OPEN_RISK_PCT` | ✅ (`PHASE1_` prefix) | ❌ | Low — defaults suffice |
| `LOGGING_FORMAT` | ✅ (`LOGGING_` prefix) | ❌ | Low — defaults suffice |
| `LOGGING_RETENTION_DAYS` | ✅ (`LOGGING_` prefix) | ❌ | Low — defaults suffice |

---

## 5. Spec Implications

### 5.1 Critical: Phase1 Settings Not Wired Into Runtime

`Settings.load()` returns a fully populated `Settings` object, but `settings.phase1` is **never read** by any runtime code path:

- `main.py:_run_rebuild_paper()` passes no phase1 values to `run_pipeline()` — it uses Python defaults
- `main.py:_run_rebuild_mock()` hardcodes `focus_price_min=1.0, focus_price_max=50.0` instead of reading from settings
- `main.py:_run_paper_loop()` hardcodes `monitor_interval_seconds=10.0, scan_interval_seconds=30.0` instead of reading from settings
- `TradingApp.__init__()` has all-risk defaults hardcoded, not injected from settings

**Operational impact:** Changing `config/default_config.yaml` or setting `PHASE1_*` env vars has **zero effect** on bot behavior. The config is decorative.

### 5.2 Duplicate Defaults — Divergence Risk

Every risk/gating parameter exists in **at least 3 places**:

1. `config/settings.py Phase1Settings` class defaults
2. `config/default_config.yaml` YAML values
3. `src/decision_pipeline.py` function parameter defaults
4. `src/app.py TradingApp.__init__` parameter defaults
5. `src/hard_filters.py` function parameter defaults

If someone updates the YAML expecting it to take effect, they are silently wrong. The runtime function defaults will continue to be used.

### 5.3 Env Naming Inconsistency

`.env` has `LIVE_TRADING_CONFIRMED=no` but the Pydantic `env_prefix="TRADING_"` means it expects `TRADING_LIVE_TRADING_CONFIRMED`. The `.env.example` correctly uses the prefixed form. **Runtime behavior:** the env var `LIVE_TRADING_CONFIRMED` is never picked up by `TradingSettings`; the default `"no"` from the Pydantic field definition is used, so live mode is correctly blocked. But the user may think they set it.

### 5.4 Env/Config Precedence Ambiguity

`BaseSettings.settings_customise_sources` order in `config/settings.py:36`:
```python
return env_settings, dotenv_settings, init_settings, file_secret_settings
```

This means: OS env > `.env` file > init (YAML/programmatic) > file secrets.

The `Settings.load()` calls `cls(trading=TradingSettings(**yaml_data["trading"]))` — here the YAML dict is passed as **init** args. Per the ordering, env vars will override YAML. This is correct, but:

- `_load_yaml_config()` runs **before** `cls()` construction, so it can read `ALPACATRADER_CONFIG` from the environment — correct.
- But when both YAML and env provide the same value, env wins — which may surprise users who expect YAML to take priority when explicitly passed via `--config`.

### 5.5 Alpaca API Key Management Bypasses Settings

`ALPACA_API_KEY` / `ALPACA_SECRET_KEY` are read via raw `os.getenv()` in three separate source files (`market_data.py`, `market_data_sim.py`, `paper_execution.py`), with legacy fallback `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`. This works but means:

- No validation at startup
- No single source of truth for Alpaca credentials
- The `LegacySettings` / `BrokerSettings` classes were removed in Batch E, but the env vars they once governed remain in use — just untracked by the config system
- `ALPACA_PAPER`, `ALPACA_BASE_URL`, `ALPACA_DATA_URL` in `.env` are completely dead

### 5.6 `.env` Is Bloated With v0.3 Legacy

The actual `.env` file (14 keys) is significantly larger than `.env.example` (3 active keys). Of those 14:
- Only `TRADING_MODE`, `LOGGING_LEVEL`, `LOGGING_DIR` are fully wired
- `LIVE_TRADING_CONFIRMED` is mis-prefixed (functions by accident via default)
- The remaining 10 keys are dead or untracked

### 5.7 `max_candidates` Is Present But Never Used

`Phase1Settings.max_candidates=30` exists in config and YAML, but no scanner code limits candidate count using it. The scanner returns whatever Finviz returns. No filtering stage reads `max_candidates` from any source.

### 5.8 `max_trade_risk_pct` Is Present But Never Wired to Entry Sizing

`entry_sizing()` in `sizing.py:84` accepts `max_trade_risk_pct: float = 0.01` but **never uses it**. The parameter is in the signature but absent from the function body. Meanwhile, `Phase1Settings.max_trade_risk_pct=0.01` matches this default but neither is connected.

---

## 6. Summary Table

| Issue | Severity | Location | Fix Required |
|-------|----------|----------|-------------|
| Phase1 settings not wired to runtime | **Critical** | All `main.py` code paths | Wire `settings.phase1.*` into `run_pipeline()` and `TradingApp()` calls |
| `.env` has 10 dead/orphaned keys | Medium | `.env` | Prune to match current schema |
| `LIVE_TRADING_CONFIRMED` mis-prefixed in `.env` | Medium | `.env:22` | Rename to `TRADING_LIVE_TRADING_CONFIRMED` |
| 3 duplicate default sites for every risk param | Medium | `decision_pipeline.py`, `app.py`, `hard_filters.py` | Consolidate to single source (Settings) |
| Alpaca credentials bypass Settings (raw `os.getenv`) | Medium | `market_data.py`, `market_data_sim.py`, `paper_execution.py` | Add to Settings or validate at startup |
| `max_candidates` config key is dead | Low | `settings.py:121`, `yaml:32` | Wire to scanner or remove |
| `max_trade_risk_pct` unused in sizing body | Low | `sizing.py:84` (parameter), body ignores it | Wire the check or remove the param |
| `fresh_quote_seconds` hardcoded to 5.0 in `hard_filters.py` | Low | `hard_filters.py:27` | Read from settings pipeline |
| `.env.example` missing `PHASE1_*`, `LOGGING_FORMAT`, `LOGGING_RETENTION_DAYS` | Low | `.env.example` | Optional — add commented-out entries |
| `.env` and `.env.example` version mismatch (v0.3.0 vs v0.4.0) | Info | `.env:1`, `.env.example:2` | Sync header comments |
