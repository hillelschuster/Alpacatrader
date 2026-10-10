#!/usr/bin/env python3
"""Provider fee ledger: pure broker/regulatory fee profiles + frozen-cohort reprice.

This is an ECONOMICS reprice, not an alpha study. It reprices already-resolved
cohort trades (immutable signals, immutable price horizons) with exact provider
fee schedules and a widened additional-slippage ladder, and reports the money
math per provider, per cohort, per day.

What it does
------------
* Pure fee profiles (no network, no broker API, no orders, no account access):
  Alpaca current 2026Q4 retail schedule and the verified Jan-2027 TAF resume
  scenario, plus the supported IBKR Lite / IBKR Pro tiered / IBKR Pro fixed
  components. Unknown route/eligibility items are UNKNOWN, never zero. IBKR
  Lite's $0 commission is modeled as the QUALIFYING retail US-resident RTH
  scenario (legal disclosure + plan page cited); the monthly 10% combined
  OnClose/OnOpen/Outside-RTH/sub-1.00 USD NMS trigger (lesser of $0.005/share
  or 1% of trade value), non-exchange-listed symbols and non-retail rejections
  are documented as conditions, and a non-qualifying case falls to the Fixed
  profile, never to a silent $0.
* Reprices the KNOWN fixed-base frontier cohort (143 intents), its 79
  L1-supported subset, and the repeat cohort (157 intents, 85 quote-supported)
  from the frozen completed-study artifacts, with the observed ASK/BID and the
  integer fee-funded quantities unchanged.
* Separates three money effects that naive models smear into one "25bps fee":
    (1) the observed ASK->BID price effect (the touch),
    (2) the additional per-side slippage ladder 0/5/10/25/50/75/100/125/150,
    (3) the real broker + regulatory fees per provider profile.
  No full-spread-per-leg charge, no assumed 25bps fee, no live quote.
* Fees follow the schedule's own posting rule: Alpaca's schedule states fees are
  "calculated daily, per account, and rounded up to the nearest cent"; each fee
  TYPE is aggregated per day per account and only then rounded up. Rounding
  every trade up to a cent individually would overstate cost; aggregate
  component totals are preserved next to the posted EOD amounts.
* Round-trip fee bps use the ACTUAL two-sided notional (SELL notional plus the
  BUY notional that funded it), never the one-way ticket dollars.

Public API meant for future models (import-safe; no side effects at import):

    PROVIDERS                        # profile registry, keyed by profile id
    ProviderProfile, FeeComponent    # frozen dataclasses
    fee_funded_quantity(budget, price, residual_bps) -> int
    order_fees(profile, side, price, quantity) -> dict[str, float | None]
    trade_fee_record(profile, entry_price, exit_price, quantity) -> dict
    up_cent(raw) -> float
    allocate_posted_fees(rows, profile) -> dict  # EOD per-day per-TYPE up-cent + allocation
    round_trip_fee_bps(fee_usd, entry_notional) -> float | None  # RT cost basis
    fee_turnover_bps(fee_usd, buy_notional, sell_notional) -> float | None  # diagnostic
    build_day_ledger(rows, profile) -> dict  # per-day per-TYPE EOD postings
    load_cohort(name, budget, rungs) -> list[dict]      # raw-field-validated
    reprice_rows(records, rungs, profile_ids, budget) -> list[dict]
    block_stats(rows, profile, period_days) / cohort_rollup(...) -> dict

Required raw input fields (validated, missing -> error, never silently zero):

    frontier pairs (kind=frontier_pairs): cohort, day, ticker, entry_et,
    exit_day, exit_et, entry_ask_250, exit_bid_250, entry_ask_shares_250,
    exit_bid_shares_250, priced_250, pair_status_250, and, per stored rung r,
    p_q_250_<budget>_<r>, p_sup_250_<budget>_<r>, p_noorder_250_<budget>_<r>.
    repeat audit rows (kind=repeat_rows): day, ticker, entry_et, exit_day,
    exit_et, entry_ask, exit_bid, entry_displayed_shares, exit_displayed_shares,
    status, and per stored rung r, quantity_<r>.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

STUDY = "alpha_provider_fee_ledger"
STATUS = (
    "ECONOMICS-REPRICE: fee schedules verified as-of 2026-10-09 against primary "
    "sources; cohort signals/prices immutable and reused from completed studies; "
    "DISCOVERY-NOT-VALIDATED economics, not a profitability claim"
)
AS_OF = "2026-10-09"
TRADING_DAYS_YEAR = 252
DEFAULT_BUDGET_USD = 1000.0
DEFAULT_LATENCY_MS = 250
# additional per-side slippage ladder (bps, round trip charged per side);
# replaces the old fixed 100-150 research rulers with a 0-150 comparison
SLIPPAGE_LADDER_BPS: tuple[int, ...] = (0, 5, 10, 25, 50, 75, 100, 125, 150)
# Repeat-audit stored rungs (kept for stored-quantity preference / reconciliation).
REPEAT_STORED_RUNGS = (0, 25, 50, 100)
FRONTIER_STORED_RUNGS = (0, 10, 25, 50)

OS_ROOT = Path.home() / "alpha-data" / "open-search-v1"
OUT_ROOT = OS_ROOT / "provider_fee_ledger"

PROTECTED_DAY_PREFIXES = ("2024", "2025-01", "2026-06", "2026-07", "2026-08")

# displayed quote size epoch of the SIP corpus (round lots before, shares after)
UNIT_EPOCH = "2025-11-03"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    """Atomic JSON write: a reader never sees a half-written artifact."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str) + "\n")
    tmp.replace(path)


def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    tmp.replace(path)


# --------------------------------------------------------------------------
# pure money helpers
# --------------------------------------------------------------------------


def fee_funded_quantity(budget_usd: float, price: float, residual_bps: float) -> int:
    """Integer fee-funded quantity, identical to the completed studies' rule.

    quantity = int(budget // price * (1 + side)), side = residual_bps / 20_000,
    computed with Python floats so the float-floor matches the frozen
    artifacts (polars float floordiv can differ, e.g. 1000 // 1.6 -> 625).
    Quantities stay integer; broker fractional availability is unverified.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    side = float(residual_bps) / 20_000.0
    return int(budget_usd // (price * (1.0 + side)))


def up_cent(raw: float) -> float:
    """Fees are calculated daily, per account, per fee TYPE, rounded UP to a cent.

    The guard band absorbs binary float error (0.02*100 == 2.0000000000000004)
    so an exact cent amount never rounds to two cents.
    """
    if raw is None:
        raise ValueError("cannot post-round an UNKNOWN amount")
    return math.ceil(round(raw * 100.0, 9)) / 100.0


def round_trip_fee_bps(fee_usd: float | None, entry_notional: float | None) -> float | None:
    """Round-trip fee in bps of the ENTRY (buy) notional.

    A round-trip cost is charged on the capital committed to the entry ticket:
    RT bps = total round-trip fee / entry notional x 10,000, the same
    denominator as the 25-150bps RT cost ladder. The exit-side notional is a
    different, usually smaller amount, so dividing by the sum of both legs
    measures fee-per-turnover, NOT a round-trip cost; that metric lives in
    ``fee_turnover_bps`` under its own clearly different name and is never
    presented as a RT cost.
    """
    if fee_usd is None or entry_notional is None or entry_notional <= 0:
        return None
    return fee_usd / entry_notional * 10_000.0


def fee_turnover_bps(
    fee_usd: float | None, buy_notional: float | None, sell_notional: float | None
) -> float | None:
    """Diagnostic fee-per-turnover on the TWO-SIDED notional (not a RT cost).

    Kept because it is the honest denominator when a fee is charged on both
    legs' volume; it must never be compared with a 25-150bps RT cost ladder.
    """
    if fee_usd is None or buy_notional is None or sell_notional is None:
        return None
    turnover = buy_notional + sell_notional
    if turnover <= 0:
        return None
    return fee_usd / turnover * 10_000.0


# --------------------------------------------------------------------------
# fee profiles (pure data, sourced)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeeComponent:
    """One pass-through fee component of a schedule.

    ``rate is None`` marks an UNKNOWN, unmeasured component (e.g. a
    route-dependent exchange fee); it is never replaced by 0.
    """

    name: str
    sides: tuple[str, ...]  # ("buy",), ("sell",) or ("buy", "sell")
    basis: str  # "shares" | "value" | "commission_pct"
    rate: float | None
    per_order_min_usd: float = 0.0
    per_order_max_pct_of_value: float | None = None
    per_trade_cap_usd: float | None = None
    source: str = ""
    note: str = ""


@dataclass(frozen=True)
class SourceRef:
    label: str
    url: str
    as_of: str
    establishes: str


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    provider: str
    plan: str  # "alpaca_retail" | "ibkr_lite" | "ibkr_pro_tiered" | "ibkr_pro_fixed"
    scenario: str
    components: tuple[FeeComponent, ...]
    sources: tuple[SourceRef, ...]
    eligibility: str
    restrictions: str
    daily_up_cent_posting: bool
    conditional: bool = False
    conditional_assumption: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_complete(self) -> bool:
        """False when any component is UNKNOWN: the profile total is UNKNOWN."""
        return all(c.rate is not None for c in self.components)

    def unknown_components(self) -> list[str]:
        return [c.name for c in self.components if c.rate is None]


SRC_ALPACA_FEE = SourceRef(
    label="Alpaca Broker Partner Fee Schedule (Exhibit B), revised Oct 1 2026",
    url="https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf",
    as_of="2026-10-01",
    establishes=(
        "regular Alpaca Securities retail stock/ETF commission 0 (except Elite "
        "Smart Router / non-retail / business partner arrangements); equities "
        "pass-through regulatory fees only: SEC $0.0000206 x sell trade value, "
        "TAF $0 per share (2026Q4 holiday), CAT $0.000003 per executed "
        "equivalent NMS share on buys and sells; fees calculated daily, per "
        "account, per fee type, rounded up to the nearest cent"
    ),
)
SRC_ALPACA_EXHIBIT_B = SourceRef(
    label="Alpaca Broker API Exhibit B: Other Fees, revised Oct 1 2026",
    url="https://files.alpaca.markets/disclosures/library/BrokerAPIExhibitB.pdf",
    as_of="2026-10-01",
    establishes=(
        "equities: SEC transaction fee $0.0000206 x trade value, sells only; "
        "FINRA TAF $0 per share, sells only; FINRA CAT $0.000003 per executed "
        "equivalent share, buys and sells; 'Fees are calculated daily, per "
        "account, and rounded up to the nearest cent'"
    ),
)
SRC_FINRA_HOLIDAY = SourceRef(
    label="SEC Release 34-106409 (SR-FINRA-2026-021): temporary TAF pause",
    url="https://public-inspection.federalregister.gov/2026-19392.pdf",
    as_of="2026-09-18",
    establishes=(
        "TAF rates set to $0.00 for transactions Oct 1 2026 - Dec 31 2026; the "
        "previous rates resume with January 2027 transactions (invoicing Feb "
        "2027); rates otherwise held through Dec 31 2028 by SR-FINRA-2026-020"
    ),
)
SRC_FINRA_RATE = SourceRef(
    label="FINRA Schedule A Section 1 Member Regulatory Fees (current equity TAF rate)",
    url="https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees",
    as_of="2026-10-09",
    establishes=(
        "current equity TAF rate $0.000195 per share on covered equity sales "
        "($0.0195 cents/share); the Jan-2027 resume rate"
    ),
)
SRC_TAF_CAP = SourceRef(
    label="SEC filing Exhibit 5 to SR-FINRA-2026-021 (bracketed prior rate)",
    url="https://www.sec.gov/files/rules/sro/finra/2026/34-106409-ex5.pdf",
    as_of="2026-09-15",
    establishes=(
        "equity TAF of $0.000195 per share for each sale of a covered equity "
        "security is the bracketed pre-holiday rate; per-trade maximum $9.79 "
        "is the published equity TAF cap (retail schedules disclose it as a "
        "max of $9.79 per trade)"
    ),
)
SRC_IBKR_COMMISSIONS = SourceRef(
    label="IBKR Commissions - Stocks, ETFs, Warrants (US)",
    url="https://www.interactivebrokers.com/en/pricing/commissions-stocks.php",
    as_of="2026-10-09",
    establishes=(
        "US stocks: IBKR Pro Tiered USD 0.0035/share (<=300,000 monthly shares) "
        "min USD 0.35 per order, max 1% of trade value; Pro Fixed USD 0.005/"
        "share min USD 1.00 per order, max 1% of trade value; Lite USD 0.002/"
        "share min USD 0.003 per order, US residents only; third-party fees: "
        "Tiered = regulatory + exchange + clearing + pass-through, Fixed = "
        "regulatory, Lite = regulatory; NSCC/DTC clearing USD 0.00020 per "
        "share; NYSE pass-through commissions x 0.000175, FINRA pass-through "
        "commissions x 0.000565; regulatory USD 0.0000206 x aggregate sales, "
        "TAF 0.00 x quantity sold (holiday), CAT 0.000003 x quantity"
    ),
)
SRC_IBKR_LITE_VARIANT = SourceRef(
    label="IBKR commissions overview (advertised zero-commission variant)",
    url="https://investors.interactivebrokers.com/en/pricing/commissions-home.php",
    as_of="2026-10-09",
    establishes=("IBKR marketing pages advertise US stock commissions starting at USD 0"),
)
SRC_IBKR_LITE_LEGAL = SourceRef(
    label="IBKR Disclaimers: IBKR Lite Commission Free Pricing Plan (legal disclosure)",
    url="https://www.interactivebrokers.com/en/general/disclaimers-ibkr-lite.php",
    as_of="2026-10-09",
    establishes=(
        "IBKR Lite provides commission-free trades in US exchange-listed stocks "
        "and ETFs routed to select market makers; non-exchange-listed US stock "
        "(Pink sheet, OTCBB) falls under the standard commission schedule; "
        "non-retail-behavior orders may be rejected and resubmitted on a Fixed "
        "commission basis; OnClose/OnOpen/outside-RTH orders stay free only "
        "while they do not exceed 10% of the account's monthly US stock volume, "
        "otherwise USD 0.005 per share"
    ),
)
SRC_IBKR_LITE_WHY = SourceRef(
    label="IBKR Lite plan page (Try IBKR Lite, footnotes 2 and 12)",
    url="https://www.interactivebrokers.com/en/trading/why-ibkr-lite.php",
    as_of="2026-10-09",
    establishes=(
        "$0 commissions on US exchange-listed stock and ETF trades; plan is for "
        "US residents (Individual, Joint, IRA, Trust with natural-person "
        "beneficiary/trustee, plus US financial advisors); at month end, if the "
        "combined volume of OnClose, OnOpen, Outside-RTH or sub-USD 1.00 NMS "
        "exceeds 10% of the account's monthly US stock trading volume, a "
        "commission of the LESSER of USD 0.005 per share or 1% of trade value "
        "is charged; regular hours 09:30-16:00"
    ),
)
SRC_IBKR_DATA = SourceRef(
    label="IBKR Market Data Pricing (streaming market data table)",
    url="https://www.interactivebrokers.com/en/pricing/market-data-pricing.php",
    as_of="2026-10-09",
    establishes=(
        "non-pro NBBO consolidated tapes: NYSE Network A/CTA $1.50/month, "
        "Network B (NYSE American/BATS/ARCA/IEX/regional) $1.50/month, NASDAQ "
        "Network C/UTP $1.50/month; pro $45/$25/$25 per month; subscriptions "
        "are not pro-rated; free Cboe One + IEX streaming is non-consolidated "
        "and is not the consolidated NBBO"
    ),
)
SRC_ALPACA_DATA = SourceRef(
    label="Alpaca About Market Data API (Trading API subscription plans)",
    url="https://docs.alpaca.markets/us/docs/about-market-data-api",
    as_of="2026-10-06",
    establishes=(
        "Alpaca Trading API: Basic free (equities real-time = IEX only), Algo "
        "Trader Plus $99/month (all US stock exchanges); Broker API plans "
        "separate. The Basic IEX feed is not the consolidated full-market SIP."
    ),
)

ELIG_UNKNOWN = (
    "account eligibility UNKNOWN: US residency, non-professional classification "
    "and house/margin eligibility are not established by any source used here; "
    "nothing is inferred from the folder or model names"
)


def _regulatory(taf_rate: float | None, taf_cap: float | None) -> tuple[FeeComponent, ...]:
    """SEC/TAF/CAT pass-through components for one regulatory scenario."""
    return (
        FeeComponent(
            name="sec",
            sides=("sell",),
            basis="value",
            rate=0.0000206,
            source=SRC_ALPACA_EXHIBIT_B.url,
            note="SEC transaction fee: sells only",
        ),
        FeeComponent(
            name="taf",
            sides=("sell",),
            basis="shares",
            rate=taf_rate,
            per_trade_cap_usd=taf_cap,
            source=SRC_FINRA_RATE.url if taf_rate else SRC_FINRA_HOLIDAY.url,
            note=(
                "FINRA TAF on covered equity sales; 2026Q4 holiday rate $0 "
                "(Oct-Dec 2026), Jan-2027 resume at $0.000195/share with the "
                "published $9.79 per-trade cap"
            ),
        ),
        FeeComponent(
            name="cat",
            sides=("buy", "sell"),
            basis="shares",
            rate=0.000003,
            source=SRC_ALPACA_EXHIBIT_B.url,
            note="FINRA CAT per executed equivalent NMS share, both sides",
        ),
    )


def _alpaca(
    profile_id: str, scenario: str, taf_rate: float | None, taf_cap: float | None
) -> ProviderProfile:
    return ProviderProfile(
        profile_id=profile_id,
        provider="alpaca",
        plan="alpaca_retail",
        scenario=scenario,
        components=(
            FeeComponent(
                name="commission",
                sides=("buy", "sell"),
                basis="shares",
                rate=0.0,
                source=SRC_ALPACA_FEE.url,
                note=(
                    "retail stock/ETF commission 0; Elite Smart Router / "
                    "non-retail / business partner arrangements excluded and "
                    "not modeled"
                ),
            ),
            *_regulatory(taf_rate, taf_cap),
            FeeComponent(
                name="exchange_route",
                sides=("buy", "sell"),
                basis="shares",
                rate=0.0,
                source=SRC_ALPACA_EXHIBIT_B.url,
                note=(
                    "the retail equities schedule passes through regulatory "
                    "fees only; no equity exchange pass-through is listed for "
                    "regular retail accounts"
                ),
            ),
        ),
        sources=(
            SRC_ALPACA_FEE,
            SRC_ALPACA_EXHIBIT_B,
            SRC_FINRA_HOLIDAY,
            SRC_FINRA_RATE,
            SRC_TAF_CAP,
        ),
        eligibility=(
            "regular retail Alpaca Securities account assumed (the schedule's "
            "zero-commission class); non-retail classes are excluded, not priced"
        ),
        restrictions=(
            "rates are regulator-set and may change (SEC fee rate floats; TAF "
            "holiday Oct-Dec 2026); this is the FORWARD CURRENT schedule applied "
            "to frozen historical PRICES, not a true historical account ledger"
        ),
        daily_up_cent_posting=True,
    )


def _ibkr(
    profile_id: str,
    plan: str,
    scenario: str,
    taf_rate: float | None,
    taf_cap: float | None,
    *,
    commission: FeeComponent,
    extra: tuple[FeeComponent, ...],
    conditional: bool = False,
    conditional_assumption: str = "",
    eligibility: str | None = None,
    restrictions: str | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        profile_id=profile_id,
        provider="ibkr",
        plan=plan,
        scenario=scenario,
        components=(commission, *_regulatory(taf_rate, taf_cap), *extra),
        sources=(
            SRC_IBKR_COMMISSIONS,
            SRC_IBKR_LITE_LEGAL,
            SRC_IBKR_LITE_WHY,
            SRC_FINRA_HOLIDAY,
            SRC_FINRA_RATE,
        ),
        eligibility=ELIG_UNKNOWN if eligibility is None else eligibility,
        restrictions=(
            "IBKR SmartRouting pass-through policy and the executing venue of "
            "each order are unmeasured; the schedule's third-party fee "
            "treatment differs by plan (Tiered adds exchange + clearing + "
            "pass-through)"
        )
        if restrictions is None
        else restrictions,
        daily_up_cent_posting=False,
        conditional=conditional,
        conditional_assumption=conditional_assumption,
    )


LITE_ELIGIBILITY = (
    "IBKR Lite plan eligibility UNKNOWN for the modeled account: the plan is "
    "limited to US residents (Individual, Joint, IRA, Trust with natural-person "
    "beneficiary/trustee, plus US financial advisors); whether the account "
    "holder is a US resident, retail-classified and non-professional is not "
    "established by any source used here and nothing is inferred from folder "
    "or model names"
)
LITE_RESTRICTIONS = (
    "the $0 commission is a CONDITIONAL qualifying-plan price, not a general "
    "fee fact: it covers US exchange-listed stocks/ETF orders during regular "
    "hours by qualifying retail clients. Non-exchange-listed symbols (Pink "
    "sheet / OTCBB, warrants, other products) fall under the standard "
    "commission schedule; non-retail-behavior orders may be rejected and "
    "resubmitted on a Fixed basis; sub-USD-1.00 NMS symbols and combined "
    "OnClose/OnOpen/Outside-RTH volume above 10% of the account's monthly US "
    "stock volume trigger the lesser of USD 0.005 per share or 1% of trade "
    "value. Whether each cohort trade qualifies is UNKNOWN per case; the Fixed "
    "plan is a separate profile, never a silent $0 fallback"
)
LITE_ASSUMPTION = (
    "STANDARD QUALIFYING RETAIL RTH assumption: every priced cohort leg is "
    "treated as a US exchange-listed stock/ETF order inside regular trading "
    "hours by a retail US-resident non-professional Lite client, well below "
    "the monthly 10% combined-volume trigger. That is a labeled scenario "
    "assumption, not a verified property of these trades or of this account"
)


IBKR_LITE_COMMISSION = FeeComponent(
    name="commission",
    sides=("buy", "sell"),
    basis="shares",
    rate=0.0,
    per_order_min_usd=0.0,
    source=SRC_IBKR_LITE_LEGAL.url,
    note=(
        "IBKR Lite QUALIFYING retail US-resident plan: commission-free trades in "
        "US exchange-listed stocks and ETFs (routed to select market makers) at "
        "$0 per order, so the standard per-share rate, per-order minimum and "
        "per-order maximum are all $0 for qualifying orders. The US commissions "
        "table's Lite row (USD 0.002/share, min USD 0.003, 'maximum per order "
        "USD 0.00') contradicts itself and is ANNOTATED, not modeled: the legal "
        "disclosure and the plan page are the governing primary sources. "
        "Trigger kept OFF this profile: if the month's combined OnClose/OnOpen/"
        "Outside-RTH/sub-1.00-USD NMS volume exceeds 10% of the account's "
        "monthly US stock volume, the commission becomes the LESSER of USD "
        "0.005 per share or 1% of trade value; non-exchange-listed symbols "
        "(Pink sheet/OTCBB) fall under the standard commission schedule; "
        "non-retail-behavior orders can be rejected and resubmitted on a Fixed "
        "basis"
    ),
)
IBKR_PRO_TIERED_COMMISSION = FeeComponent(
    name="commission",
    sides=("buy", "sell"),
    basis="shares",
    rate=0.0035,
    per_order_min_usd=0.35,
    per_order_max_pct_of_value=0.01,
    source=SRC_IBKR_COMMISSIONS.url,
    note="IBKR Pro Tiered first tier (<=300,000 monthly shares)",
)
IBKR_PRO_FIXED_COMMISSION = FeeComponent(
    name="commission",
    sides=("buy", "sell"),
    basis="shares",
    rate=0.005,
    per_order_min_usd=1.00,
    per_order_max_pct_of_value=0.01,
    source=SRC_IBKR_COMMISSIONS.url,
    note="IBKR Pro Fixed",
)
IBKR_TIERED_EXTRAS = (
    FeeComponent(
        name="clearing",
        sides=("buy", "sell"),
        basis="shares",
        rate=0.0002,
        source=SRC_IBKR_COMMISSIONS.url,
        note="NSCC/DTC clearing $0.00020 per share (Tiered plan only)",
    ),
    FeeComponent(
        name="pass_through",
        sides=("buy", "sell"),
        basis="commission_pct",
        rate=0.000175 + 0.000565,
        source=SRC_IBKR_COMMISSIONS.url,
        note="NYSE pass-through commissions x 0.000175 plus FINRA x 0.000565 (Tiered plan only)",
    ),
    FeeComponent(
        name="exchange_route",
        sides=("buy", "sell"),
        basis="shares",
        rate=None,
        source=SRC_IBKR_COMMISSIONS.url,
        note=(
            "venue-specific exchange fees (per-venue linked schedule); the "
            "executing venue/route of each order is unmeasured, so this "
            "component is UNKNOWN, not 0"
        ),
    ),
)

PROVIDERS: dict[str, ProviderProfile] = {
    p.profile_id: p
    for p in (
        _alpaca("alpaca_current_2026q4", "current_2026q4", 0.0, None),
        _alpaca("alpaca_taf_jan2027", "taf_jan2027", 0.000195, 9.79),
        _ibkr(
            "ibkr_lite_current",
            "lite",
            "current_2026q4",
            0.0,
            None,
            commission=IBKR_LITE_COMMISSION,
            extra=(),
            conditional=True,
            conditional_assumption=LITE_ASSUMPTION,
            eligibility=LITE_ELIGIBILITY,
            restrictions=LITE_RESTRICTIONS,
        ),
        _ibkr(
            "ibkr_lite_taf_jan2027",
            "lite",
            "taf_jan2027",
            0.000195,
            9.79,
            commission=IBKR_LITE_COMMISSION,
            extra=(),
            conditional=True,
            conditional_assumption=LITE_ASSUMPTION,
            eligibility=LITE_ELIGIBILITY,
            restrictions=LITE_RESTRICTIONS,
        ),
        _ibkr(
            "ibkr_pro_tiered_current",
            "pro_tiered",
            "current_2026q4",
            0.0,
            None,
            commission=IBKR_PRO_TIERED_COMMISSION,
            extra=IBKR_TIERED_EXTRAS,
        ),
        _ibkr(
            "ibkr_pro_tiered_taf_jan2027",
            "pro_tiered",
            "taf_jan2027",
            0.000195,
            9.79,
            commission=IBKR_PRO_TIERED_COMMISSION,
            extra=IBKR_TIERED_EXTRAS,
        ),
        _ibkr(
            "ibkr_pro_fixed_current",
            "pro_fixed",
            "current_2026q4",
            0.0,
            None,
            commission=IBKR_PRO_FIXED_COMMISSION,
            extra=(),
        ),
        _ibkr(
            "ibkr_pro_fixed_taf_jan2027",
            "pro_fixed",
            "taf_jan2027",
            0.000195,
            9.79,
            commission=IBKR_PRO_FIXED_COMMISSION,
            extra=(),
        ),
    )
}

ALL_PROFILE_IDS = tuple(PROVIDERS)

DATA_OPEX = {
    "alpaca_algo_trader_plus_usd_per_month": 99.0,
    "alpaca_algo_trader_plus_usd_per_year": 1188.0,
    "alpaca_algo_trader_plus_note": (
        "all-US-exchange equities real-time data; the free Basic plan is "
        "IEX-only and is NOT the consolidated full-market SIP"
    ),
    "ibkr_nbbo_nonpro_usd_per_month": 4.50,
    "ibkr_nbbo_nonpro_usd_per_year": 54.0,
    "ibkr_nbbo_nonpro_note": (
        "non-pro NBBO = three consolidated tape subscriptions at $1.50/month "
        "each; not pro-rated mid-month"
    ),
    "ibkr_nbbo_pro_usd_per_month": 95.0,
    "ibkr_nbbo_pro_usd_per_year": 1140.0,
    "ibkr_nbbo_pro_note": (
        "professional classification raises the same three tapes to "
        "$45/$25/$25 per month; professional status is UNKNOWN"
    ),
    "free_non_consolidated_note": (
        "free Cboe One / IEX streaming is non-consolidated: it is not the "
        "consolidated NBBO this frozen SIP corpus was priced on, so a 'free "
        "IEX data' argument does not transfer to these economics"
    ),
    "sources": [SRC_ALPACA_DATA.url, SRC_IBKR_DATA.url],
    "shared_once_per_portfolio": True,
    "shared_once_note": (
        "data opex is charged once per family/portfolio, never per strategy "
        "and never inside per-fill broker fees"
    ),
}


# --------------------------------------------------------------------------
# pure fee computation
# --------------------------------------------------------------------------


def order_fees(
    profile: ProviderProfile, side: str, price: float, quantity: int
) -> dict[str, float | None]:
    """Per-order fee by component for one leg of a known fill.

    Contract: every component the PROFILE carries is present in the result; a
    component that does not apply to this leg (e.g. the sell-only SEC fee on a
    buy order) is 0.0, while a carried-but-UNKNOWN component is None.
    Components the profile does not carry at all (a clearing or exchange fee on
    a zero-commission plan, say) are simply ABSENT from the dict: use
    ``dict.get(name)`` and read absence as "this plan does not charge it",
    never as a measured zero for an unmeasured fee. An order that would not
    exist (quantity 0) must not call this: a min-capital no-order sends no
    order and has no fee at all.
    """
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    if quantity < 1:
        raise ValueError("a $0-quantity order is not sent and has no fee")
    value = price * quantity
    commission = 0.0
    fees: dict[str, float | None] = {c.name: 0.0 for c in profile.components}
    for comp in profile.components:
        if side not in comp.sides:
            continue
        if comp.rate is None:
            fees[comp.name] = None
            continue
        if comp.basis == "shares":
            amount = comp.rate * quantity
        elif comp.basis == "value":
            amount = comp.rate * value
        else:
            continue  # commission_pct pass 2
        if comp.per_order_min_usd:
            amount = max(amount, comp.per_order_min_usd)
        if comp.per_order_max_pct_of_value:
            amount = min(amount, comp.per_order_max_pct_of_value * value)
        if comp.per_trade_cap_usd is not None:
            amount = min(amount, comp.per_trade_cap_usd)
        fees[comp.name] = amount
        if comp.name == "commission":
            commission = amount
    for comp in profile.components:
        if side not in comp.sides or comp.basis != "commission_pct":
            continue
        fees[comp.name] = None if comp.rate is None else comp.rate * commission
    return fees


def trade_fee_record(
    profile: ProviderProfile,
    entry_price: float,
    exit_price: float,
    quantity: int,
) -> dict[str, Any]:
    """Both legs of one known filled round trip under one profile."""
    buy = order_fees(profile, "buy", entry_price, quantity)
    sell = order_fees(profile, "sell", exit_price, quantity)
    total = 0.0
    complete = True
    for leg in (buy, sell):
        for value in leg.values():
            if value is None:
                complete = False
            else:
                total += value
    buy_notional = entry_price * quantity
    sell_notional = exit_price * quantity
    return {
        "buy_leg_fees": buy,
        "sell_leg_fees": sell,
        "fee_total_usd": total if complete else None,
        "fee_rt_bps": round_trip_fee_bps(total if complete else None, buy_notional),
        "fee_turnover_bps": fee_turnover_bps(
            total if complete else None, buy_notional, sell_notional
        ),
        "fee_rt_bps_denominator_note": (
            "round-trip fee / entry (buy) notional x 10,000; the entry ticket "
            "is the RT cost denominator, matching the 25-150bps RT ladder"
        ),
        "buy_notional_usd": buy_notional,
        "sell_notional_usd": sell_notional,
    }


# --------------------------------------------------------------------------
# cohort loading (frozen artifacts only; no quote files, no network)
# --------------------------------------------------------------------------

COHORTS: dict[str, dict[str, Any]] = {
    "base_143": {
        "kind": "frontier_pairs",
        "label": (
            "late143 fixed immutable base cohort: 2025-02..2026-05 confirmation "
            "block (332 replay trading days, 124 observed signal days, 143 "
            "intents); previously explored, NOT pristine"
        ),
        "path": OS_ROOT / "sparse_execution_frontier" / "late143" / "pairs.parquet",
        "source_study": "alpha_sparse_execution_frontier",
        "budget_usd": DEFAULT_BUDGET_USD,
        "latency_ms": DEFAULT_LATENCY_MS,
        "period_days": 332,
        "select": None,
    },
    "frontier_79": {
        "kind": "frontier_pairs",
        "label": (
            "the 79 late143 intents L1-supported at the $1000 budget, 250ms "
            "arrival, primary 2s quote rule (the frontier's known subset; the "
            "same artifact, membership never re-selected)"
        ),
        "path": OS_ROOT / "sparse_execution_frontier" / "late143" / "pairs.parquet",
        "source_study": "alpha_sparse_execution_frontier",
        "budget_usd": DEFAULT_BUDGET_USD,
        "latency_ms": DEFAULT_LATENCY_MS,
        "period_days": 332,
        "select": "supported_at_r0",
    },
    "repeat_157": {
        "kind": "repeat_rows",
        "label": (
            "repeat_h60 chosen view of alpha_sparse_daily: 2025-02..2026-05 "
            "confirmation block (332 replay trading days, 124 observed signal "
            "days, 157 known fills; 85 quote-supported); previously explored, "
            "NOT pristine"
        ),
        "path": OS_ROOT / "learned_sparse_daily" / "quote_audit" / "confirmation" / "rows.parquet",
        "source_study": "alpha_quote_audit over alpha_sparse_daily",
        "budget_usd": DEFAULT_BUDGET_USD,
        "period_days": 332,
        "select": None,
    },
}

FRONTIER_REQUIRED = (
    "cohort",
    "day",
    "ticker",
    "entry_et",
    "exit_day",
    "exit_et",
    "entry_ask_250",
    "exit_bid_250",
    "entry_ask_shares_250",
    "exit_bid_shares_250",
    "priced_250",
    "pair_status_250",
)
REPEAT_REQUIRED = (
    "day",
    "ticker",
    "entry_et",
    "exit_day",
    "exit_et",
    "entry_ask",
    "exit_bid",
    "entry_displayed_shares",
    "exit_displayed_shares",
    "status",
)


def guarded_day(day: str) -> str:
    """Refuse protected unread windows even though this study never re-reads quotes."""
    if day.startswith(PROTECTED_DAY_PREFIXES):
        raise ValueError(f"protected unread day window refused: {day}")
    return day


def _require_columns(frame: pl.DataFrame, required: tuple[str, ...], path: Path) -> None:
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise SystemExit(f"{path}: missing required fields {missing}")


def load_frontier_pairs(
    path: Path, budget: float, latency_ms: int, rungs: tuple[int, ...]
) -> list[dict[str, Any]]:
    stored_rungs = tuple(r for r in FRONTIER_STORED_RUNGS if r in rungs)
    qty_cols = [f"p_q_{latency_ms}_{int(budget)}_{r}" for r in stored_rungs]
    sup_cols = [f"p_sup_{latency_ms}_{int(budget)}_{r}" for r in stored_rungs]
    _require_columns(
        frame := pl.read_parquet(path),
        FRONTIER_REQUIRED + tuple(sup_cols) + tuple(qty_cols),
        path,
    )
    out: list[dict[str, Any]] = []
    for row in frame.select(FRONTIER_REQUIRED + tuple(sup_cols) + tuple(qty_cols)).to_dicts():
        day = guarded_day(row["day"])
        exit_day = guarded_day(row["exit_day"])
        priced = bool(row["priced_250"])
        stored_qty = {
            int(r): row[c]
            for r, c in zip(stored_rungs, qty_cols, strict=True)
            if row.get(c) is not None
        }
        out.append(
            {
                "cohort_kind": "frontier_pairs",
                "day": day,
                "exit_day": exit_day,
                "ticker": row["ticker"],
                "entry_et": int(row["entry_et"]) if row["entry_et"] is not None else None,
                "exit_et": int(row["exit_et"]) if row["exit_et"] is not None else None,
                "entry_ask": row["entry_ask_250"] if priced else None,
                "exit_bid": row["exit_bid_250"] if priced else None,
                "entry_displayed_shares": row["entry_ask_shares_250"] if priced else None,
                "exit_displayed_shares": row["exit_bid_shares_250"] if priced else None,
                "legs_priced": priced,
                "unpriced_cause": None if priced else str(row["pair_status_250"]),
                "stored_supported_r0": (row.get(f"p_sup_{latency_ms}_{int(budget)}_0") or None),
                "stored_quantity_by_rung": stored_qty,
                "proxy_net": None,
            }
        )
    return out


def load_repeat_rows(path: Path, rungs: tuple[int, ...]) -> list[dict[str, Any]]:
    _require_columns(pl.read_parquet(path), REPEAT_REQUIRED, path)
    frame = pl.read_parquet(path)
    qty_cols = {r: f"quantity_{r}" for r in REPEAT_STORED_RUNGS if f"quantity_{r}" in frame.columns}
    out: list[dict[str, Any]] = []
    for row in frame.select(REPEAT_REQUIRED + tuple(qty_cols.values())).to_dicts():
        day = guarded_day(row["day"])
        exit_day = guarded_day(row["exit_day"])
        status = str(row["status"])
        # Priced means both legs have a usable observed quote; capacity
        # shortfalls stay priced-but-UNKNOWN support, never a zero fill.
        both_quoted = row["entry_ask"] is not None and row["exit_bid"] is not None
        unpriced_cause = None if both_quoted else status
        stored_qty = {r: row[c] for r, c in qty_cols.items() if row.get(c) is not None}
        out.append(
            {
                "cohort_kind": "repeat_rows",
                "day": day,
                "exit_day": exit_day,
                "ticker": row["ticker"],
                "entry_et": int(row["entry_et"]) if row["entry_et"] is not None else None,
                "exit_et": int(row["exit_et"]) if row["exit_et"] is not None else None,
                "entry_ask": float(row["entry_ask"]) if both_quoted else None,
                "exit_bid": float(row["exit_bid"]) if both_quoted else None,
                "entry_displayed_shares": row["entry_displayed_shares"] if both_quoted else None,
                "exit_displayed_shares": row["exit_displayed_shares"] if both_quoted else None,
                "legs_priced": both_quoted,
                "unpriced_cause": unpriced_cause,
                "stored_supported_r0": (
                    True if status == "quoted_capacity_supported_not_fill_guaranteed" else None
                ),
                "stored_quantity_by_rung": stored_qty,
                "proxy_net": row.get("proxy_net"),
            }
        )
    return out


def load_cohort(
    name: str, budget: float | None = None, rungs: tuple[int, ...] = SLIPPAGE_LADDER_BPS
) -> list[dict[str, Any]]:
    """Normalized trade records for one cohort (raw fields validated)."""
    if name not in COHORTS:
        raise SystemExit(f"unknown cohort {name!r}; known: {sorted(COHORTS)}")
    cfg = COHORTS[name]
    path: Path = cfg["path"]
    if not path.exists():
        raise SystemExit(f"[{name}] input artifact not found: {path}")
    budget = float(cfg["budget_usd"] if budget is None else budget)
    if cfg["kind"] == "frontier_pairs":
        records = load_frontier_pairs(path, budget, cfg["latency_ms"], rungs)
        if cfg["select"] == "supported_at_r0":
            records = [r for r in records if r["stored_supported_r0"]]
    else:
        records = load_repeat_rows(path, rungs)
    for r in records:
        r["cohort"] = name
        r["budget_usd"] = budget
    return records


# --------------------------------------------------------------------------
# reprice
# --------------------------------------------------------------------------


def reprice_trade(record: dict[str, Any], rung_bps: int, budget: float) -> dict[str, Any]:
    """One trade at one slippage rung: integer quantity, touch price effect,
    additional slippage and the fill/UNKNOWN status. Broker fees are attached
    by caller-side profile loops; this function is profile-agnostic."""
    side = float(rung_bps) / 20_000.0
    row: dict[str, Any] = {
        "day": record["day"],
        "exit_day": record["exit_day"],
        "ticker": record["ticker"],
        "entry_et": record["entry_et"],
        "exit_et": record["exit_et"],
        "rung_bps": int(rung_bps),
        "budget_usd": budget,
        "proxy_net": record.get("proxy_net"),
    }
    if not record["legs_priced"]:
        row.update(
            {
                "status": "unknown_unpriced",
                "unpriced_cause": record["unpriced_cause"],
                "entry_ask": None,
                "exit_bid": None,
                "quantity": None,
                "quantity_basis": None,
                "entry_price": None,
                "exit_price": None,
                "buy_notional_usd": None,
                "sell_notional_usd": None,
                "net_usd_slippage_only": None,
                "net_frac_slippage_only": None,
                "price_effect_bps": None,
                "known_fill": False,
            }
        )
        return row
    entry_ask = float(record["entry_ask"])
    exit_bid = float(record["exit_bid"])
    if entry_ask <= 0 or exit_bid <= 0:
        raise SystemExit(f"non-firm observed quote for {record['ticker']} on {record['day']}")
    stored = record["stored_quantity_by_rung"].get(int(rung_bps))
    derived = fee_funded_quantity(budget, entry_ask, rung_bps)
    quantity = int(stored) if stored is not None else derived
    basis = "stored_artifact" if stored is not None else "derived_fee_funded_rule"
    row.update({"entry_ask": entry_ask, "exit_bid": exit_bid})
    if quantity < 1:
        # min-capital no-order: no order, no fee, no fill; cash retained (KNOWN)
        entry_price = entry_ask * (1.0 + side)
        row.update(
            {
                "status": "known_no_order_min_capital",
                "unpriced_cause": None,
                "quantity": 0,
                "quantity_basis": basis,
                "entry_price": entry_price,
                "exit_price": exit_bid * (1.0 - side),
                "buy_notional_usd": 0.0,
                "sell_notional_usd": 0.0,
                "net_usd_slippage_only": None,
                "net_frac_slippage_only": None,
                "price_effect_bps": (exit_bid / entry_ask - 1.0) * 10_000.0,
                "known_fill": False,
            }
        )
        return row
    entry_price = entry_ask * (1.0 + side)
    exit_price = exit_bid * (1.0 - side)
    buy_notional = quantity * entry_price
    sell_notional = quantity * exit_price
    entry_l1 = record["entry_displayed_shares"]
    exit_l1 = record["exit_displayed_shares"]
    entry_ok = entry_l1 is not None and quantity <= float(entry_l1)
    exit_ok = exit_l1 is not None and quantity <= float(exit_l1)
    if not (entry_ok and exit_ok):
        missing = [leg for leg, ok in (("entry", entry_ok), ("exit", exit_ok)) if not ok]
        row.update(
            {
                "status": "unknown_l1_capacity_" + "_".join(missing),
                "unpriced_cause": None,
                "quantity": quantity,
                "quantity_basis": basis,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "buy_notional_usd": buy_notional,
                "sell_notional_usd": sell_notional,
                "net_usd_slippage_only": None,
                "net_frac_slippage_only": None,
                "price_effect_bps": (exit_bid / entry_ask - 1.0) * 10_000.0,
                "known_fill": False,
            }
        )
        return row
    net = sell_notional - buy_notional
    row.update(
        {
            "status": "priced_supported",
            "unpriced_cause": None,
            "quantity": quantity,
            "quantity_basis": basis,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "buy_notional_usd": buy_notional,
            "sell_notional_usd": sell_notional,
            "net_usd_slippage_only": net,
            "net_frac_slippage_only": net / budget,
            "price_effect_bps": (exit_bid / entry_ask - 1.0) * 10_000.0,
            "known_fill": True,
        }
    )
    return row


FEE_COMPONENT_ORDER = (
    "commission",
    "exchange_route",
    "sec",
    "taf",
    "cat",
    "clearing",
    "pass_through",
)


def reprice_rows(
    records: list[dict[str, Any]],
    rungs: tuple[int, ...],
    profile_ids: tuple[str, ...],
    budget: float,
) -> list[dict[str, Any]]:
    """Per-trade x per-rung x per-profile fee rows (pure Python money math).

    Two passes per profile block: (1) raw per-order regulator math on the
    causal integer quantity, then (2) the EOD posting pass -
    ``allocate_posted_fees`` - which aggregates each fee TYPE per day per
    account, rounds up to the cent where the provider states that policy, and
    allocates the posted amount pro-rata across the day's known fills so the
    trade-level cash nets reconcile exactly with gross - posted.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        for rung in rungs:
            base = reprice_trade(record, rung, budget)
            for profile_id in profile_ids:
                profile = PROVIDERS[profile_id]
                row = dict(base)
                row["profile"] = profile_id
                if not base["known_fill"]:
                    for comp in FEE_COMPONENT_ORDER:
                        row[f"fee_{comp}_buy"] = None
                        row[f"fee_{comp}_sell"] = None
                    row["fee_total_usd"] = None
                    row["fee_posted_allocated_usd"] = None
                    row["fee_rt_bps_of_entry_notional"] = None
                    row["fee_turnover_bps_two_sided_diagnostic"] = None
                    row["net_after_posted_fees_usd"] = None
                    row["fee_total_complete"] = False
                else:
                    fr = trade_fee_record(
                        profile, base["entry_price"], base["exit_price"], base["quantity"]
                    )
                    for comp in FEE_COMPONENT_ORDER:
                        row[f"fee_{comp}_buy"] = fr["buy_leg_fees"].get(comp)
                        row[f"fee_{comp}_sell"] = fr["sell_leg_fees"].get(comp)
                    row["fee_total_usd"] = fr["fee_total_usd"]
                    row["fee_posted_allocated_usd"] = None  # set by the EOD pass
                    row["fee_rt_bps_of_entry_notional"] = None
                    row["fee_turnover_bps_two_sided_diagnostic"] = None
                    row["net_after_posted_fees_usd"] = None
                    row["fee_total_complete"] = profile.total_complete
                rows.append(row)
    for profile_id in profile_ids:
        for rung in rungs:
            block = [
                r for r in rows if r["profile"] == profile_id and int(r["rung_bps"]) == int(rung)
            ]
            allocate_posted_fees(block, PROVIDERS[profile_id])
    return rows


def allocate_posted_fees(rows: list[dict[str, Any]], profile: ProviderProfile) -> dict[str, Any]:
    """EOD posting pass over one profile block: per-day per-TYPE up-cent, then
    a pro-rata allocation of the posted amount across the day's known fills.

    The block is ONE slippage-rung scenario: a trade executes once, so rungs
    never merge into the same day buckets. A buy order posts its fees on the
    entry day and a sell order on the exit day (that is when each order
    executes), but the posted amount is aggregated per fee TYPE per account per
    day, so both legs' same-type fees merge into ONE day+type bucket before the
    up-cent rounding.

    Provider account cash is what the schedule actually charges: each fee TYPE
    aggregated per account per day, rounded UP to the cent (Alpaca states this;
    unverified providers keep raw sums). The rounding dust is allocated
    pro-rata by raw component share so that, for a complete profile,

        sum(trade net_after_posted_fees_usd) == gross - posted total

    reconciles exactly at both trade and day level. Raw per-trade amounts stay
    untouched as the diagnostic record. Profiles carrying an UNKNOWN component
    (e.g. an unmeasured exchange route) get no posted allocation and no cash
    net: the cash consequence of an unmeasured fee is UNKNOWN, never zero.
    """
    buckets: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for i, r in enumerate(rows):
        if not r["known_fill"]:
            continue
        # a buy order posts its fees on the entry day and a sell order on the
        # exit day (that is when each order executes), but the posted amount is
        # aggregated per fee TYPE per account per day, so both legs' same-type
        # fees merge into ONE day+type bucket before the up-cent rounding.
        for leg_day, prefix in ((r["day"], "buy"), (r["exit_day"], "sell")):
            for comp in FEE_COMPONENT_ORDER:
                value = r[f"fee_{comp}_{prefix}"]
                if value is None or float(value) == 0.0:
                    continue
                buckets.setdefault((leg_day, comp), []).append((i, float(value)))
    posted_by_day: dict[str, dict[str, float]] = {}
    raw_by_day: dict[str, dict[str, float]] = {}
    alloc: dict[int, float] = {}
    for (leg_day, comp), items in buckets.items():
        raw_sum = sum(v for _, v in items)
        raw_by_day.setdefault(leg_day, {})
        raw_by_day[leg_day][comp] = raw_by_day[leg_day].get(comp, 0.0) + raw_sum
        posted_type = up_cent(raw_sum) if profile.daily_up_cent_posting else raw_sum
        posted_by_day.setdefault(leg_day, {})
        posted_by_day[leg_day][comp] = posted_by_day[leg_day].get(comp, 0.0) + posted_type
        for i, v in items:
            alloc[i] = alloc.get(i, 0.0) + posted_type * v / raw_sum
    complete = profile.total_complete
    for i, r in enumerate(rows):
        if not r["known_fill"] or not complete:
            r["fee_posted_allocated_usd"] = None
            r["net_after_posted_fees_usd"] = None
            r["fee_rt_bps_of_entry_notional"] = None
            r["fee_turnover_bps_two_sided_diagnostic"] = None
            continue
        allocated = alloc.get(i, 0.0)
        r["fee_posted_allocated_usd"] = allocated
        r["net_after_posted_fees_usd"] = r["net_usd_slippage_only"] - allocated
        r["fee_rt_bps_of_entry_notional"] = round_trip_fee_bps(allocated, r["buy_notional_usd"])
        r["fee_turnover_bps_two_sided_diagnostic"] = fee_turnover_bps(
            r["fee_total_usd"], r["buy_notional_usd"], r["sell_notional_usd"]
        )
    return {
        "posted_by_day": posted_by_day,
        "raw_by_day": raw_by_day,
        "posting_policy": (
            "each fee TYPE per day per account up-cent (Alpaca schedule), "
            "allocated pro-rata by raw share"
            if profile.daily_up_cent_posting
            else "provider posting/rounding policy unverified: raw sums, no up-cent"
        ),
        "cash_net_basis_complete": complete,
    }


# --------------------------------------------------------------------------
# per-day EOD posting for one profile/cohort/rung block
# --------------------------------------------------------------------------


def build_day_ledger(
    rows: list[dict[str, Any]], profile: ProviderProfile
) -> dict[str, dict[str, Any]]:
    """Group one block's known fills into per-execution-day EOD postings.

    Returns day -> {n_orders, fee_raw_by_type, fee_posted_by_type_eod,
    fee_raw_total_usd, fee_posted_total_usd_eod, fee_components_complete, ...}.
    A buy order posts on the entry day and a sell order on the exit day,
    because that is when each order executes; same-day round trips collapse to
    one day. Identical aggregation to ``allocate_posted_fees``, so the day
    posted totals reconcile with the sum of the trade-level allocations.
    """
    raw_by_day: dict[str, dict[str, float]] = {}
    orders: dict[str, int] = {}
    unknown_by_day: dict[str, bool] = {}
    carried = {c.name for c in profile.components}
    for r in rows:
        if not r["known_fill"]:
            continue
        for leg_day, prefix in ((r["day"], "buy"), (r["exit_day"], "sell")):
            orders[leg_day] = orders.get(leg_day, 0) + 1
            bucket = raw_by_day.setdefault(leg_day, {})
            for comp in FEE_COMPONENT_ORDER:
                if comp not in carried:
                    continue  # the plan does not charge this component at all
                value = r[f"fee_{comp}_{prefix}"]
                if value is None:
                    unknown_by_day[leg_day] = True
                    continue
                if float(value) == 0.0:
                    bucket.setdefault(comp, 0.0)
                    continue
                bucket[comp] = bucket.get(comp, 0.0) + float(value)
    ledger: dict[str, dict[str, Any]] = {}
    for day in sorted(set(raw_by_day) | set(orders)):
        raw = raw_by_day.get(day, {})
        posted = None
        raw_total = sum(raw.values())
        posted_total = None
        if profile.daily_up_cent_posting and raw:
            # each fee TYPE aggregated per day per account, then up-cent;
            # raw component totals are preserved beside the posting
            posted = {k: up_cent(v) for k, v in sorted(raw.items())}
            posted_total = round(sum(posted.values()), 10)
        ledger[day] = {
            "n_orders": orders.get(day, 0),
            "fee_raw_by_type_usd": dict(sorted(raw.items())),
            "fee_posted_by_type_usd_eod": posted,
            "fee_raw_total_usd": raw_total,
            "fee_posted_total_usd_eod": posted_total,
            "fee_components_complete": not unknown_by_day.get(day, False),
            "daily_up_cent_posting": profile.daily_up_cent_posting,
            "rounding_policy": (
                "per fee TYPE per day per account, up-cent (schedule states it)"
                if profile.daily_up_cent_posting
                else "provider posting/rounding policy unverified: raw regulator math only"
            ),
        }
    return ledger


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def provider_data_opex(profile: ProviderProfile) -> dict[str, Any]:
    """Data opex for the profile's provider/plan (sourced, shared once)."""
    if profile.provider == "alpaca":
        return {
            "usd_per_month": DATA_OPEX["alpaca_algo_trader_plus_usd_per_month"],
            "usd_per_year": DATA_OPEX["alpaca_algo_trader_plus_usd_per_year"],
            "plan": "Algo Trader Plus (all US stock exchanges)",
            "classification_known": True,
            "note": DATA_OPEX["alpaca_algo_trader_plus_note"],
            "source": SRC_ALPACA_DATA.url,
        }
    return {
        "usd_per_month": None,
        "usd_per_year": None,
        "plan": "IBKR consolidated NBBO (tapes A + B + C)",
        "classification_known": False,
        "usd_per_year_if_non_professional": DATA_OPEX["ibkr_nbbo_nonpro_usd_per_year"],
        "usd_per_year_if_professional": DATA_OPEX["ibkr_nbbo_pro_usd_per_year"],
        "note": (
            "professional vs non-professional subscriber status is UNKNOWN, so "
            "the annual data opex is UNKNOWN between the two sourced bounds; "
            + DATA_OPEX["ibkr_nbbo_nonpro_note"]
        ),
        "source": SRC_IBKR_DATA.url,
    }


def block_stats(
    rows: list[dict[str, Any]], profile: ProviderProfile, period_days: int
) -> dict[str, Any]:
    """Fees + separated money effects for one (cohort, rung, profile) block."""
    fills = [r for r in rows if r["known_fill"]]
    noorder = [r for r in rows if r["status"] == "known_no_order_min_capital"]
    unknown = [
        r for r in rows if not r["known_fill"] and r["status"] != "known_no_order_min_capital"
    ]
    causes: dict[str, int] = {}
    for r in unknown:
        causes[str(r["status"])] = causes.get(str(r["status"]), 0) + 1
    fee_totals = [r["fee_total_usd"] for r in fills]
    raw_measured = [f for f in fee_totals if f is not None]
    posted_alloc = [r["fee_posted_allocated_usd"] for r in fills]
    posted_measured = [p for p in posted_alloc if p is not None]
    buy_notional = sum(r["buy_notional_usd"] for r in fills)
    sell_notional = sum(r["sell_notional_usd"] for r in fills)
    net = [r["net_usd_slippage_only"] for r in fills]
    net_after = [r["net_after_posted_fees_usd"] for r in fills]
    net_after_measured = [n for n in net_after if n is not None]
    ledger = build_day_ledger(rows, profile)
    complete = bool(posted_measured) and len(posted_measured) == len(fills)
    raw_total = round(sum(raw_measured), 10) if len(raw_measured) == len(fills) else None
    posted_total = round(sum(posted_measured), 10) if complete else None
    posting_delta = (
        round(posted_total - raw_total, 10) if (complete and raw_total is not None) else None
    )
    mean_posted = _mean(posted_measured) if complete else None
    mean_raw = _mean(raw_measured) if len(raw_measured) == len(fills) else None
    n_fills = len(fills)
    days_with_fills = len(ledger)
    fills_per_year = n_fills / period_days * TRADING_DAYS_YEAR
    gross_total = round(sum(net), 10) if net else None
    reconcile = None
    if complete and gross_total is not None:
        trade_net_total = round(sum(net_after_measured), 10)
        day_posted_total = round(
            sum(d["fee_posted_total_usd_eod"] or 0.0 for d in ledger.values()), 10
        )
        reconcile = {
            "gross_slippage_only_usd": gross_total,
            "posted_fee_total_usd": posted_total,
            "day_level_posted_total_usd": day_posted_total,
            "trade_level_net_total_usd": trade_net_total,
            "gross_minus_posted_usd": round(gross_total - posted_total, 10),
            "reconciles": (
                abs(trade_net_total - (gross_total - posted_total)) < 1e-9
                and abs(day_posted_total - posted_total) < 1e-9
            ),
            "note": (
                "sum of trade-level posted nets equals gross minus the day-level "
                "posted totals exactly; the pro-rata allocation of the up-cent "
                "rounding dust is what makes trade, day and gross levels agree"
            ),
        }
    stats = {
        "budget_usd": rows[0]["budget_usd"] if rows else None,
        "n_trades": len(rows),
        "n_priced_supported_fills": n_fills,
        "n_known_no_order": len(noorder),
        "n_unknown": len(unknown),
        "unknown_causes": dict(sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "coverage_fraction_of_intents": (n_fills / len(rows)) if rows else None,
        "unknown_outcomes_unmeasured_not_zero": True,
        "known_no_order_note": (
            "a budget below one integer share at the quoted fee-adjusted ASK "
            "sends no order: no fill, no fee, cash retained; it is never a "
            "zero-return fill and never an UNKNOWN loss"
        ),
        "touch_price_effect": {
            "mean_price_effect_bps": _mean([r["price_effect_bps"] for r in fills]),
            "mean_net_frac_slippage_only": _mean([r["net_frac_slippage_only"] for r in fills]),
            "mean_net_usd_slippage_only_per_fill": _mean(net),
            "net_usd_slippage_only_total": round(sum(net), 10) if net else None,
            "note": (
                "observed ASK->BID price effect plus the additional per-side "
                "slippage rung; the rung-0 value isolates the observed touch"
            ),
        },
        "broker_fees": {
            "profile": profile.profile_id,
            "provider": profile.provider,
            "plan": profile.plan,
            "scenario": profile.scenario,
            "total_complete": profile.total_complete,
            "conditional": profile.conditional,
            "conditional_assumption": profile.conditional_assumption,
            "unknown_components": profile.unknown_components(),
            "fee_raw_component_total_usd": raw_total,
            "fee_posted_eod_total_usd": posted_total,
            "posting_eod_minus_raw_usd": posting_delta,
            "posting_policy": (
                "Alpaca: each fee TYPE aggregated per day per account, then "
                "rounded up to the cent; the schedule states NO per-trade floor "
                "or per-trade rounding for these regulatory fees; per-trade "
                "amounts stay unrounded regulator math; prorating the daily "
                "aggregate is reporting only and never a causal entry-time "
                "charge; unrounded component totals are preserved"
                if profile.daily_up_cent_posting
                else "provider posting/rounding policy unverified: raw regulator math only"
            ),
            "mean_posted_fee_usd_per_fill": mean_posted,
            "mean_raw_fee_usd_per_fill_diagnostic": mean_raw,
            "fee_rt_bps_of_entry_notional": (
                (sum(posted_measured) / buy_notional * 10_000.0)
                if complete and buy_notional > 0
                else None
            ),
            "fee_rt_bps_denominator_note": (
                "round-trip posted fee / ENTRY (buy) notional x 10,000: the "
                "one-way entry ticket is the RT cost denominator, matching the "
                "25-150bps RT cost ladder; the two-sided fee/turnover rate is "
                "reported separately under its own name and is never compared "
                "with a RT cost ladder"
            ),
            "fee_turnover_bps_two_sided_diagnostic": (
                (sum(raw_measured) / (buy_notional + sell_notional) * 10_000.0)
                if raw_total is not None and (buy_notional + sell_notional) > 0
                else None
            ),
            "total_entry_notional_usd": round(buy_notional, 10),
            "total_two_sided_notional_usd": round(buy_notional + sell_notional, 10),
            "conditional_if_unknown_matched_covered_fee_usd_per_year": (
                mean_posted * (len(rows) / period_days) * TRADING_DAYS_YEAR
                if mean_posted is not None
                else None
            ),
            "conditional_note": (
                "fee-only conditional spread of the covered mean fee over every "
                "intent assuming UNKNOWN outcomes matched covered ones; it is "
                "not an EV claim and no fill is invented for UNKNOWN legs"
            ),
        },
        "net_after_broker_fees": {
            "cash_basis": (
                "posted EOD allocation (per-day per-TYPE up-cent, pro-rata by "
                "raw share): what the provider account actually charges"
                if profile.daily_up_cent_posting
                else "raw sums (provider posting/rounding policy unverified)"
            ),
            "mean_usd_per_fill": _mean(net_after_measured),
            "total_usd": round(sum(net_after_measured), 10) if net_after_measured else None,
            "unknown_if_profile_incomplete": not profile.total_complete,
            "unknown_total_note": (
                "an unmeasured route fee keeps the cash net UNKNOWN: no fee is "
                "invented and the net is never computed against a partial fee"
                if not profile.total_complete
                else None
            ),
            "reconciliation": reconcile,
        },
        "annualization": {
            "period_days": period_days,
            "fills_per_year": fills_per_year,
            "fee_usd_per_year_covered_case_posted": (mean_posted * fills_per_year)
            if mean_posted is not None
            else None,
            "fee_usd_per_year_covered_case_raw_diagnostic": (mean_raw * fills_per_year)
            if mean_raw is not None
            else None,
            "fee_usd_per_observed_day_with_fills_posted": (
                (sum(posted_measured) / days_with_fills) if (complete and days_with_fills) else None
            ),
            "fee_usd_per_observed_day_with_fills_raw_diagnostic": (
                (sum(raw_measured) / days_with_fills)
                if (len(raw_measured) == len(fills) and days_with_fills)
                else None
            ),
            "known_fills_per_observed_day": (n_fills / days_with_fills)
            if days_with_fills
            else None,
            "observed_days_with_fills": days_with_fills,
            "basis": (
                "fills/period trading days x 252 for the covered case only; "
                "UNKNOWN outcomes are never spread into the annualization; "
                "posted-basis figures are the cash figures, raw is diagnostic"
            ),
            "no_cagr": True,
        },
        "shared_data_opex": provider_data_opex(profile),
        "shared_data_opex_note": (
            "data opex is a separate per-portfolio line, shared once per "
            "family, never inside per-fill fees and never charged per strategy"
        ),
    }
    stats["whole_portfolio_certified"] = False
    stats["partial_coverage_note"] = (
        f"{n_fills} of {len(rows)} intents are priced-and-supported at this rung; "
        "the rest stay UNKNOWN (unpriced legs, L1 capacity shortfalls) and are "
        "never booked as zero-fee cash or zero-return fills"
    )
    return stats


def cohort_rollup(
    name: str,
    records: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    rungs: tuple[int, ...],
    profile_ids: tuple[str, ...],
    period_days: int,
    input_sha: str,
) -> dict[str, Any]:
    by_block: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_block.setdefault((int(r["rung_bps"]), r["profile"]), []).append(r)
    n_priced = sum(1 for r in records if r["legs_priced"])
    n_supported_r0 = sum(1 for r in records if r["stored_supported_r0"] and r["legs_priced"])
    expected = {
        "base_143": {"n_trades": 143, "n_priced": 110, "n_supported_r0": 79},
        "frontier_79": {"n_trades": 79, "n_priced": 79, "n_supported_r0": 79},
        "repeat_157": {"n_trades": 157, "n_priced": 120, "n_supported_r0": 85},
    }.get(name, {})
    got = {
        "n_trades": len(records),
        "n_priced": n_priced,
        "n_supported_r0": n_supported_r0,
    }
    rollup = {
        "cohort": name,
        "label": COHORTS[name]["label"],
        "source_study": COHORTS[name]["source_study"],
        "input_artifact": {"path": str(COHORTS[name]["path"]), "sha256": input_sha},
        "budget_usd": COHORTS[name]["budget_usd"],
        "rungs_bps": list(rungs),
        "profiles": list(profile_ids),
        "counts": got,
        "reconciliation_vs_source_artifact": {
            "expected": expected,
            "observed": got,
            "reconciles": all(expected.get(k) == v for k, v in got.items()) if expected else None,
            "note": (
                "headline known counts must match the completed studies: the "
                "ledger reprices the frozen cohorts and never re-selects them"
            ),
        },
        "rung_blocks": {},
        "whole_portfolio_certified": False,
        "quote_audit_disclosure": (
            "as-of NBBO price audit at the frozen 250ms arrival clock, replayed "
            "from the completed studies' artifacts: this is not a causal live "
            "quote, not an exchange fill and not a guarantee of venue fills"
        ),
    }
    for rung in rungs:
        rollup["rung_blocks"][str(int(rung))] = {}
        for pid in profile_ids:
            block = by_block.get((int(rung), pid))
            if not block:
                continue
            rollup["rung_blocks"][str(int(rung))][pid] = block_stats(
                block, PROVIDERS[pid], period_days
            )
    return rollup


def profile_snapshot(pid: str) -> dict[str, Any]:
    p = PROVIDERS[pid]
    return {
        "profile_id": pid,
        "provider": p.provider,
        "plan": p.plan,
        "scenario": p.scenario,
        "total_complete": p.total_complete,
        "unknown_components": p.unknown_components(),
        "daily_up_cent_posting": p.daily_up_cent_posting,
        "conditional": p.conditional,
        "conditional_assumption": p.conditional_assumption,
        "eligibility": p.eligibility,
        "restrictions": p.restrictions,
        "notes": list(p.notes),
        "components": [
            {
                "name": c.name,
                "sides": list(c.sides),
                "basis": c.basis,
                "rate": c.rate,
                "rate_known": c.rate is not None,
                "per_order_min_usd": c.per_order_min_usd,
                "per_order_max_pct_of_value": c.per_order_max_pct_of_value,
                "per_trade_cap_usd": c.per_trade_cap_usd,
                "source": c.source,
                "note": c.note,
            }
            for c in p.components
        ],
        "first_source_urls": [s.url for s in p.sources],
        "sources": [
            {"label": s.label, "url": s.url, "as_of": s.as_of, "establishes": s.establishes}
            for s in p.sources
        ],
        "as_of": AS_OF,
    }


def source_snapshot(inputs: dict[str, str]) -> dict[str, Any]:
    refs: dict[str, SourceRef] = {}
    for p in PROVIDERS.values():
        for s in p.sources:
            refs[s.url] = s
    op = DATA_OPEX["sources"]
    for url in op:
        refs.setdefault(url, SRC_IBKR_DATA if "interactivebrokers" in url else SRC_ALPACA_DATA)
    return {
        "study": STUDY,
        "status": STATUS,
        "sources_accessed": AS_OF,
        "primary_sources": [
            {"label": s.label, "url": s.url, "as_of": s.as_of, "establishes": s.establishes}
            for s in refs.values()
        ],
        "fee_schedule_forward_application_note": (
            "the CURRENT 2026Q4 schedules are applied FORWARD to the frozen "
            "historical PRICE scenarios of these cohorts; 2026 fees are never "
            "backdated onto historical trades as a true historical account "
            "ledger. The account-level daily rounding assumption is modeled on "
            "the KNOWN supported subset only; it is not applied to an entire "
            "account that would also contain the UNKNOWN fills, whose fees are "
            "never estimated"
        ),
        "taf_note": (
            "TAF is $0/share for Oct 1 - Dec 31 2026 transactions (SR-FINRA-2026-021) "
            "and resumes at the previous equity rate $0.000195/share with the "
            "$9.79 per-trade cap from Jan 2027 transactions; both scenarios are "
            "priced, never guessed"
        ),
        "regulatory_rate_volatility_note": (
            "SEC/TAF/CAT rates are regulator-set and change; the as-of dates "
            "above are the carrying basis of every number in this ledger"
        ),
        "no_live_access_note": (
            "no broker API, no orders, no account access, no paid calls, no "
            "network calls at run time: profiles are static sourced data and "
            "cohorts come from completed studies' artifacts"
        ),
        "inputs": inputs,
        "input_sha256": dict(inputs),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Provider fee ledger: pure sourced fee profiles + frozen-cohort "
            "economics reprice (no network, no broker API, no orders)"
        )
    )
    p.add_argument("--out", type=Path, default=OUT_ROOT, help=f"output root (default: {OUT_ROOT})")
    p.add_argument(
        "--cohorts",
        default=",".join(COHORTS),
        help="comma-separated cohort names (default: the known cohorts " + ",".join(COHORTS) + ")",
    )
    p.add_argument(
        "--rungs",
        default=",".join(str(x) for x in SLIPPAGE_LADDER_BPS),
        help="additional per-side slippage ladder in bps "
        "(default: " + ",".join(str(x) for x in SLIPPAGE_LADDER_BPS) + ")",
    )
    p.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET_USD,
        help=f"one-way ticket budget in USD (default: {DEFAULT_BUDGET_USD})",
    )
    p.add_argument(
        "--profiles",
        default=",".join(ALL_PROFILE_IDS),
        help="comma-separated provider profile ids (default: all sourced profiles)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help=(
            "resume: skip cohorts whose rollup exists and whose input sha, "
            "budget, rungs and profile set are unchanged"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="rewrite cohort rollups even when --resume would skip them",
    )
    return p


def _profile_registry_digest() -> str:
    """Digest of the fee-profile registry: any rate/source/condition change
    invalidates a resume gate so stale Lite/Alpaca numbers are never reused."""
    payload = json.dumps(
        [
            {
                "id": p.profile_id,
                "components": [
                    (
                        c.name,
                        c.sides,
                        c.basis,
                        c.rate,
                        c.per_order_min_usd,
                        c.per_order_max_pct_of_value,
                        c.per_trade_cap_usd,
                    )
                    for c in p.components
                ],
                "posting": p.daily_up_cent_posting,
                "conditional": p.conditional,
            }
            for p in PROVIDERS.values()
        ],
        default=str,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _resume_signature(
    cfg_path: Path, budget: float, rungs: tuple[int, ...], profiles: tuple[str, ...]
) -> str:
    return "|".join(
        [
            sha256_file(cfg_path),
            sha256_file(Path(__file__).resolve()),
            f"{budget:.2f}",
            ",".join(str(int(r)) for r in rungs),
            ",".join(profiles),
            _profile_registry_digest(),
        ]
    )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    t0 = time.time()
    rungs = tuple(sorted({int(x) for x in args.rungs.split(",") if x.strip()}))
    if not rungs:
        raise SystemExit("rungs must be non-empty")
    if args.budget <= 0:
        raise SystemExit("budget must be positive")
    cohort_names = [c.strip() for c in args.cohorts.split(",") if c.strip()]
    unknown = [c for c in cohort_names if c not in COHORTS]
    if unknown:
        raise SystemExit(f"unknown cohorts {unknown}; known: {sorted(COHORTS)}")
    profile_ids = tuple(x.strip() for x in args.profiles.split(",") if x.strip())
    bad_profiles = [p for p in profile_ids if p not in PROVIDERS]
    if bad_profiles:
        raise SystemExit(f"unknown profiles {bad_profiles}; known: {list(ALL_PROFILE_IDS)}")

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    trades_root = out / "trades"
    days_root = out / "days"
    cohorts_root = out / "cohorts"
    for d in (trades_root, days_root, cohorts_root):
        d.mkdir(parents=True, exist_ok=True)

    # contracts/profile/source snapshots are static snapshot metadata
    write_json(
        out / "contract.json",
        {
            "study": STUDY,
            "status": STATUS,
            "generated_at": datetime.now(UTC).isoformat(),
            "as_of": AS_OF,
            "purpose": (
                "reprice the frozen known cohorts with sourced provider fee "
                "profiles and a widened 0-150bps additional-slippage ladder; "
                "signals, horizons and quote artifacts stay immutable"
            ),
            "cohorts": {
                name: {
                    "label": COHORTS[name]["label"],
                    "source_study": COHORTS[name]["source_study"],
                    "input_artifact": str(COHORTS[name]["path"]),
                    "budget_usd": COHORTS[name]["budget_usd"],
                    "period_days": COHORTS[name]["period_days"],
                    "latency_ms": COHORTS[name].get("latency_ms"),
                }
                for name in COHORTS
            },
            "slippage_ladder_bps_per_side": [int(r) for r in rungs],
            "ladder_replaces": (
                "the fixed 100-150bps research rulers are replaced by a "
                "0/5/10/25/50/75/100/125/150 comparison; real broker fees are "
                "reported separately, never as an assumed 25bps fee"
            ),
            "no_live_access": True,
            "no_orders_no_broker_api_no_account_access": True,
            "no_network_calls_at_runtime": True,
            "protected_windows_unread": True,
        },
    )
    write_json(
        out / "profiles.json",
        {
            "study": STUDY,
            "as_of": AS_OF,
            "profiles": [profile_snapshot(pid) for pid in ALL_PROFILE_IDS],
            "data_opex": DATA_OPEX,
            "import_api": (
                "from alpha_provider_fee_ledger import PROVIDERS, order_fees, "
                "trade_fee_record, up_cent, round_trip_fee_bps, "
                "fee_funded_quantity, build_day_ledger, load_cohort, "
                "reprice_rows"
            ),
        },
    )

    summary: dict[str, Any] = {
        "study": STUDY,
        "status": STATUS,
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": AS_OF,
        "assumptions": {
            "budget_usd": args.budget,
            "slippage_ladder_bps_per_side": [int(r) for r in rungs],
            "integer_quantity_rule": (
                "quantity = int(budget // ask*(1+side)) with the stored "
                "artifact quantity preferred and identical when present; "
                "quantities are integer shares and are never resized"
            ),
            "no_full_spread_per_leg": (
                "the observed ASK->BID touch is charged exactly once; the "
                "additional ladder is a declared per-side slippage assumption; "
                "no full-spread-per-leg and no assumed 25bps fee"
            ),
            "price_audit_not_causal_live_quote": (
                "every price comes from the completed studies' frozen quote "
                "artifacts at the declared 250ms arrival clock; this ledger is "
                "a price audit / economics reprice, not a causal live system"
            ),
            "taf_scenarios": (
                "current: TAF $0/share (Oct 1 - Dec 31 2026 holiday, "
                "SR-FINRA-2026-021); jan2027: $0.000195/share on sells with the "
                "$9.79 per-trade cap (previous equity rate resumes with Jan 2027 "
                "transactions)"
            ),
            "eod_posting": (
                "Alpaca: fees are calculated daily, per ACCOUNT, per fee TYPE, "
                "and rounded up to the nearest cent (confirmed exact in the "
                "Oct 1 2026 schedule); there is no per-trade floor or per-trade "
                "rounding; fees accrue intraday and post EOD; the prorated "
                "daily aggregate is reporting only and never a causal "
                "entry-time charge; unrounded component totals are preserved "
                "beside the posted totals and per-trade amounts are never "
                "individually up-rounded"
            ),
            "ibkr_rounding_policy_unverified": (
                "IBKR's posting/rounding policy is not in the cited sources, "
                "so IBKR blocks carry raw regulator math only"
            ),
            "forward_schedule_on_frozen_prices": (
                "the CURRENT 2026Q4 fee schedules are applied FORWARD to the "
                "frozen historical PRICE scenarios of these cohorts, not as a "
                "true historical account ledger; the account-level daily "
                "rounding is modeled on the known supported subset only, never "
                "on an entire account that would also contain the UNKNOWN fills"
            ),
            "ibkr_lite_qualifying_assumption": (
                "IBKR Lite $0 commission is a CONDITIONAL qualifying-plan price "
                "(US exchange-listed stock/ETF, regular hours, US-resident "
                "retail non-pro client, monthly combined OnClose/OnOpen/"
                "Outside-RTH/sub-1.00-USD NMS volume <= 10% of US stock "
                "volume): it is a labeled scenario assumption for these "
                "repriced trades, not a verified property of the cohort "
                "symbols or of the modeler's account; eligibility unknowns "
                "(residency, professional classification, symbol type) are "
                "UNKNOWN, and a non-qualifying case falls to the Fixed plan "
                "profile, never to a silent $0"
            ),
            "fee_bps_denominator": (
                "round-trip posted fee / ENTRY (buy) notional x 10,000: the "
                "one-way entry ticket is the RT cost denominator, the same "
                "basis as the 25-150bps RT cost ladder; a two-sided "
                "fee-per-turnover rate is reported separately under its own "
                "name (fee_turnover_bps_two_sided_diagnostic) and is never "
                "compared with a RT cost ladder"
            ),
            "posted_cash_basis": (
                "provider account cash deducts the POSTED EOD amounts: each fee "
                "TYPE aggregated per account per day, rounded up to the cent "
                "(Alpaca schedule; IBKR posting unverified so raw sums), then "
                "allocated pro-rata across the day's known fills by raw "
                "component share. Cash nets use the posted allocation; the "
                "unrounded per-trade regulator math is kept as the diagnostic "
                "record; sum(trade net) == gross - posted reconciles exactly. "
                "Profiles carrying an UNKNOWN component keep the cash "
                "consequence UNKNOWN (no invented fee, no partial-fee net)"
            ),
            "known_no_order": (
                "quantity 0 (budget below one integer share at the quoted "
                "fee-adjusted ASK): no order, no fee, no fill, cash retained"
            ),
            "unpriced_policy": (
                "unpriced legs and L1 capacity shortfalls stay UNKNOWN; fees "
                "and outcomes are never zeroed to cash and no whole-case EV is "
                "claimed from a supported subset"
            ),
            "data_opex_separate": DATA_OPEX,
            "no_cagr": True,
        },
        "cohorts": {},
        "runtime_s": None,
    }

    for name in cohort_names:
        cfg = COHORTS[name]
        rollup_path = cohorts_root / f"{name}.json"
        signature = _resume_signature(cfg["path"], args.budget, rungs, profile_ids)
        if args.resume and not args.force and rollup_path.exists():
            try:
                prior = json.loads(rollup_path.read_text())
                if prior.get("resume_signature") == signature:
                    summary["cohorts"][name] = prior
                    print(f"[{name}] resumed (signature unchanged)", flush=True)
                    continue
            except (json.JSONDecodeError, OSError):
                pass
        records = load_cohort(name, args.budget, rungs)
        rows = reprice_rows(records, rungs, profile_ids, args.budget)
        rollup = cohort_rollup(
            name, records, rows, rungs, profile_ids, cfg["period_days"], signature.split("|")[0]
        )
        rollup["resume_signature"] = signature
        rollup["program_sha256"] = sha256_file(Path(__file__).resolve())
        write_json(rollup_path, rollup)
        summary["cohorts"][name] = rollup
        # per-cohort per-profile artifacts: per-trade rows + per-day EOD ledger
        for pid in profile_ids:
            profile = PROVIDERS[pid]
            block_rows = [r for r in rows if r["profile"] == pid]
            if not block_rows:
                continue
            part = trades_root / name / f"{pid}.parquet"
            part.parent.mkdir(parents=True, exist_ok=True)
            if not (args.resume and part.exists()):
                atomic_write_parquet(pl.DataFrame(block_rows, infer_schema_length=None), part)
            day_path = days_root / name / f"{pid}.jsonl"
            day_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = day_path.with_suffix(".jsonl.tmp")
            with tmp.open("w") as f:
                # one EOD ledger per slippage-rung scenario: a trade executes
                # once, so rungs never merge into the same day posting buckets
                for rung in rungs:
                    rung_rows = [r for r in block_rows if int(r["rung_bps"]) == int(rung)]
                    ledger = build_day_ledger(rung_rows, profile)
                    for day in sorted(ledger):
                        payload = {
                            "day": day,
                            "rung_bps": int(rung),
                            "profile": pid,
                            "cohort": name,
                        }
                        payload.update(ledger[day])
                        f.write(json.dumps(payload, default=str) + "\n")
            tmp.replace(day_path)
        print(
            f"[{name}] {len(records)} intents repriced x {len(rungs)} rungs x "
            f"{len(profile_ids)} profiles; {rollup['counts']}",
            flush=True,
        )

    inputs = {name: str(COHORTS[name]["path"]) for name in cohort_names}
    write_json(out / "source_snapshot.json", source_snapshot(inputs))
    summary["runtime_s"] = round(time.time() - t0, 1)
    write_json(out / "summary.json", summary)
    snap_dir = out / "producer_snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), snap_dir / Path(__file__).name)
    print(
        json.dumps(
            {
                "out": str(out),
                "cohorts": {
                    k: {
                        "counts": v["counts"],
                        "reconciles": (v["reconciliation_vs_source_artifact"]["reconciles"]),
                    }
                    for k, v in summary["cohorts"].items()
                },
                "profiles": list(profile_ids),
                "rungs_bps": [int(r) for r in rungs],
                "runtime_s": summary["runtime_s"],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
