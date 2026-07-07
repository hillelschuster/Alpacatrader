"""
Finviz free-tier scanner adapter — ticker discovery only.

Provides the FinvizRow dataclass, Finviz scrape, yfinance watchlist utility,
and stale-result detection. Static watchlists are watch-only, not automatic
top-gainer trade discovery.
"""

from typing import Optional
from dataclasses import dataclass

import requests
from loguru import logger

_FINVIZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Alpacatrader/0.3; +https://github.com/alpacatrader)"
}
_FINVIZ_URL = "https://finviz.com/screener.ashx?v=111&s=ta_topgainers"
_FINVIZ_TIMEOUT = 10


# ──────────────────────────────────────────────────────────────────
#  Finviz row model
# ──────────────────────────────────────────────────────────────────


@dataclass
class FinvizRow:
    ticker: str = ""
    company: str = ""
    sector: str = ""
    industry: str = ""
    country: str = ""
    market_cap: float = 0.0
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    exchange: str = ""


# ──────────────────────────────────────────────────────────────────
#  Finviz free-tier scrape
#
#  IMPORTANT: Finviz free screener (v=111&s=ta_topgainers) lists only
#  11 columns: No, Ticker, Company, Sector, Industry, Country,
#  Market Cap, P/E, Price, Change, Volume.
#  It does NOT include Relative Volume, Float, or Short Float in the
#  free tier. Those fields require Finviz Elite ($39.50/mo, v=151).
#
#  Use Finviz for ticker discovery + price/volume cross-check only.
#  All float, RVOL, and fundamental data must come from yfinance.
# ──────────────────────────────────────────────────────────────────


def scrape_finviz_gainers() -> dict[str, FinvizRow]:
    """Scrape Finviz top gainers table using BeautifulSoup.

    Returns dict[ticker, FinvizRow] for ticker discovery and price/volume
    cross-reference. Empty dict on failure — Finviz is supplemental, not primary.

    LAYOUT (v=111, 11 columns):
      cols[0]=No, cols[1]=Ticker, cols[2]=Company, cols[3]=Sector,
      cols[4]=Industry, cols[5]=Country, cols[6]=Market Cap,
      cols[7]=P/E, cols[8]=Price, cols[9]=Change, cols[10]=Volume

    RVOL, Float, and Short Float are NOT available in the free tier.
    """
    try:
        resp = requests.get(_FINVIZ_URL, headers=_FINVIZ_HEADERS, timeout=_FINVIZ_TIMEOUT)
    except requests.RequestException as e:
        logger.debug(f"Finviz request failed: {e}")
        return {}

    if resp.status_code != 200:
        logger.debug(f"Finviz returned {resp.status_code}")
        return {}

    if "Too many requests" in resp.text:
        logger.warning("Finviz rate limit hit — skipping this cycle")
        return {}

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("beautifulsoup4 not installed — skipping Finviz scrape")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="styled-table-new")
    if not table:
        table = soup.find("table")
    if not table:
        return {}

    results: dict[str, FinvizRow] = {}
    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cols = row.find_all("td")
        # v=111 has exactly 11 columns — reject rows with fewer
        if len(cols) < 11:
            continue
        try:
            ticker = cols[1].get_text(strip=True)
            if not ticker or not ticker.isascii():
                continue

            price_str = cols[8].get_text(strip=True)
            price = float(price_str) if price_str and price_str != "-" else 0.0

            change_str = cols[9].get_text(strip=True).replace("%", "")
            change_pct = float(change_str) if change_str and change_str != "-" else 0.0

            vol_str = cols[10].get_text(strip=True).replace(",", "")
            volume = _parse_finviz_volume(vol_str)

            mcap_str = cols[6].get_text(strip=True)
            market_cap = _parse_finviz_market_cap(mcap_str)

            results[ticker] = FinvizRow(
                ticker=ticker,
                company=cols[2].get_text(strip=True),
                sector=cols[3].get_text(strip=True),
                industry=cols[4].get_text(strip=True),
                country=cols[5].get_text(strip=True),
                market_cap=market_cap,
                price=price,
                change_pct=change_pct,
                volume=volume,
            )
        except (ValueError, IndexError):
            continue

    logger.debug(f"Finviz scrape: {len(results)} rows parsed")
    return results


def _parse_finviz_volume(raw: str) -> int:
    """Parse Finviz volume strings like '5.2M' or '500K' to integer."""
    if not raw or raw == "-":
        return 0
    raw = raw.upper().replace(",", "")
    multiplier = 1
    if "B" in raw:
        multiplier = 1_000_000_000
        raw = raw.replace("B", "")
    elif "M" in raw:
        multiplier = 1_000_000
        raw = raw.replace("M", "")
    elif "K" in raw:
        multiplier = 1_000
        raw = raw.replace("K", "")
    try:
        return max(0, int(float(raw) * multiplier))
    except ValueError:
        return 0


def _parse_finviz_market_cap(raw: str) -> float:
    """Parse Finviz market cap strings like '5.2B' or '500M' to float."""
    if not raw or raw == "-":
        return 0.0
    raw = raw.upper().replace(",", "")
    multiplier = 1.0
    if "B" in raw:
        multiplier = 1_000_000_000.0
        raw = raw.replace("B", "")
    elif "M" in raw:
        multiplier = 1_000_000.0
        raw = raw.replace("M", "")
    elif "K" in raw:
        multiplier = 1_000.0
        raw = raw.replace("K", "")
    try:
        return float(raw) * multiplier
    except ValueError:
        return 0.0


# ── yfinance static watchlist utility ────────────────────────────────────────

# Static equity-focused watchlist for yfinance intraday observation.
# Not used as automatic trade discovery when dynamic top-gainer sources fail.
# This list is dated 2026-06 — refresh periodically.
# EXCLUDES: ETFs, mutual funds, and indices (filtered dynamically below).
_VOLATILE_WATCHLIST: list[str] = [
    # Momentum equity names (common small-mid cap runners)
    "RKLB", "ASTS", "LUNR", "PLTR", "SOFI", "HOOD", "RIVN",
    "MARA", "RIOT", "CLSK", "COIN", "MSTR",
    "NU", "AFRM", "UPST", "SOUN", "IONQ", "QBTS", "RGTI",
]


_ETF_LIKE_QUOTE_TYPES = frozenset({"ETF", "MUTUALFUND", "FUND", "INDEX"})


def scrape_yfinance_gainers(
    min_price: float = 2.0, max_price: float = 20.0, min_gap_pct: float = 5.0,
) -> dict[str, "FinvizRow"]:
    """Scan a curated watchlist for intraday gainers using yfinance.

    Filters out ETFs/mutual funds/funds/indexes via fast_info.quote_type.
    Returns dict[ticker, FinvizRow] for watch-only observation, with real-time
    change% and volume from yfinance fast_info. Automatic scanner paths must
    not treat this static list as today's top-gainer discovery.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — skipping yfinance gainer scan")
        return {}

    results: dict[str, "FinvizRow"] = {}
    for sym in _VOLATILE_WATCHLIST:
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info

            # Skip ETFs, mutual funds, funds, and indexes
            qt = fi.quote_type
            if qt and qt.upper() in _ETF_LIKE_QUOTE_TYPES:
                continue

            prev = fi.previous_close
            last = fi.last_price
            vol = fi.last_volume
            if not prev or not last or prev <= 0:
                continue
            if last < min_price or last > max_price:
                continue
            chg = (last - prev) / prev * 100
            if chg < min_gap_pct:
                continue
            info = t.info or {}
            results[sym] = FinvizRow(
                ticker=sym,
                company=info.get("longName", info.get("shortName", "")),
                sector=info.get("sector", ""),
                industry=info.get("industry", ""),
                country=info.get("country", ""),
                market_cap=float(info.get("marketCap", 0) or 0),
                price=last,
                change_pct=chg,
                volume=int(vol or 0),
                exchange=str(fi.exchange or ""),
            )
        except Exception:
            continue

    sorted_items = sorted(results.items(), key=lambda x: x[1].change_pct, reverse=True)
    return dict(sorted_items)


def _finviz_is_stale(result: dict) -> bool:
    """Return True if the Finviz result is clearly stale/broken.

    Checks two dimensions independently (OR logic):
      - Zero change: >=80% of rows have change_pct == 0.0
      - Zero volume: >=80% of rows have volume == 0

    Each dimension is a hard OR — either triggering independently marks the
    result as stale. A mix of nonzero change with zero volume, or nonzero
    volume with zero change, can still be stale if >=80% of the rows fail
    on that dimension. This is by design — the top-gainers table should
    have near-100% nonzero change, and a real trading day should have
    near-100% nonzero volume.
    """
    if not result:
        return True
    n = len(result)
    if n < 3:
        return False  # small result set — not enough data to call stale
    zero_change = sum(1 for r in result.values() if r.change_pct == 0.0)
    zero_volume = sum(1 for r in result.values() if r.volume == 0)
    if zero_change >= n * 0.8:
        return True
    if zero_volume >= n * 0.8:
        return True
    return False


# ── Float enrichment (SPEC §5.3) ───────────────────────────────────


def enrich_float_shares(symbol: str) -> Optional[int]:
    """Fetch float shares for a symbol from yfinance.

    Uses ``yf.Ticker(symbol).info.get("floatShares")`` per Context7-verified
    yfinance API (ranaroussi/yfinance).  Returns ``None`` when yfinance is
    unavailable, the ticker is unknown, or ``floatShares`` is missing/zero.

    Bounded: a single info fetch per call.  Callers should cache or batch
    when scanning many symbols.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. ``"DSY"``).

    Returns
    -------
    int or None
        Float share count, or ``None`` if unavailable.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.debug("yfinance not installed — cannot enrich float for %s", symbol)
        return None

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        logger.debug("yfinance info fetch failed for %s", symbol)
        return None

    raw = info.get("floatShares")
    if raw is None:
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None
