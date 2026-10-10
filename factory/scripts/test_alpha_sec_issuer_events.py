"""Consumer-visible edges of the SEC issuer-event collector's PIT contract.

These guard the contract a future catalyst study depends on: a CURRENT ticker->CIK
index never proves a historical association, a dated as-filed DEI TradingSymbol
accepted at or before the trade clock does, a filing accepted later on the SAME
trade day is not yet evidence for that day, an unmapped or non-tagged history is an
explicit UNKNOWN and never a "no filing" / "no event" claim, the panel projection
cannot reach an outcome column, and a missing declared User-Agent is a hard stop
instead of an undeclared request.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpha_sec_issuer_events as asec  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402

DAY = "2023-03-15"
TICKERS = {"AAPL": "0000320193", "NEWC": "0001111111", "OLDC": "0000222222"}


# ----- stubs -------------------------------------------------------------------
class _Resp:
    def __init__(self, status_code: int, body=b"", text: str = ""):
        self.status_code = status_code
        self.content = body
        self.text = text

    def json(self):
        return json.loads(self.content)


class _Session:
    """A scripted session over the SEC's public endpoints. No network is touched."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.calls.append(url)
        route = self.routes.get(url)
        if route is None:
            return _Resp(404, b'{"error":"not scripted"}')
        if isinstance(route, _Resp):
            return route
        if isinstance(route, bytes):
            return _Resp(200, route)
        return _Resp(200, json.dumps(route).encode())


def _ticker_map() -> dict:
    return {
        str(i): {"cik_str": int(cik), "ticker": tk, "title": f"{tk} Inc."}
        for i, (tk, cik) in enumerate(TICKERS.items())
    }


def _submission(
    accession: str,
    form: str,
    acceptance: str,
    filing_date: str,
    report_date: str = "",
    primary: str = "primary.htm",
    items: str = "",
) -> dict:
    return {
        "accessionNumber": accession,
        "filingDate": filing_date,
        "reportDate": report_date,
        "acceptanceDateTime": acceptance,
        "act": "34",
        "form": form,
        "fileNumber": "001-00000",
        "filmNumber": "0000000",
        "items": items,
        "size": "1 MB",
        "isXBRL": [1],
        "isInlineXBRL": [1],
        "primaryDocument": primary,
        "primaryDocDescription": form,
    }


def _recent(rows: list[dict], files: list[dict] | None = None) -> dict:
    keys = list(asec.submission_columns())
    payload: dict = {k: [r.get(k) for r in rows] for k in keys}
    # the XBRL booleans arrive as flat ints, not per-row lists
    for k in ("isXBRL", "isInlineXBRL"):
        payload[k] = [r.get(k) for r in rows]
    return {"cik": 320193, "name": "stub", "filings": {"recent": payload, "files": files or []}}


def _ix(tag: str, value: str, scale: int | None = None) -> str:
    scale_attr = f' scale="{scale}"' if scale is not None else ""
    return f'<ix:nonnumeric name="dei:{tag}" contextref="c-1"{scale_attr}>{value}</ix:nonnumeric>'


def _ix_num(tag: str, value: str, scale: int | None = None) -> str:
    scale_attr = f' scale="{scale}"' if scale is not None else ""
    return (
        f'<ix:nonfraction name="dei:{tag}" contextref="c-1" unitref="shares" '
        f'decimals="-3" format="ixt:numdotdecimal"{scale_attr}>{value}</ix:nonfraction>'
    )


def _cover_document(
    symbol: str,
    shares: str = "12,500,000",
    scale: int | None = None,
    include_symbol: bool = True,
) -> bytes:
    body = ["<html><body>"]
    if include_symbol:
        body.append(_ix("EntityRegistrantName", "NEWC Corporation"))
        body.append(_ix("Security12bTitle", "Common Stock, $0.0001 par value"))
        body.append(_ix("SecurityExchangeName", "NASDAQ"))
        body.append(_ix("TradingSymbol", symbol))
    body.append(_ix_num("EntityCommonStockSharesOutstanding", shares, scale))
    body.append("</body></html>")
    return "".join(body).encode()


def _concept(tag: str, rows: list[dict]) -> dict:
    return {
        "cik": 1111111,
        "taxonomy": "dei",
        "tag": tag,
        "label": tag,
        "units": {"shares" if "Shares" in tag else "USD": rows},
    }


def _symbol_fact(value: str, filed: str, accn: str, form: str = "10-K") -> dict:
    return {"val": value, "end": filed, "filed": filed, "accn": accn, "form": form, "frame": None}


# ----- dated association vs the current ticker map ------------------------------
def _manifest(
    series: list[dict], archive: list[dict] | None = None, cik: str = "0001111111"
) -> dict:
    return {
        "ticker": "NEWC",
        "cik": cik,
        "seed_title": "NEWC Inc.",
        "seed_map_source": asec.SEC_COMPANY_TICKERS_URL,
        "cik_status": "mapped" if cik else "unknown_no_cik_seed",
        "association_floor": "2019-02-01",
        "association_series": series,
        "archive": archive or [],
    }


def _dated(symbol: str, acceptance: str, accn: str = "0001111111-20-000001") -> dict:
    return {
        "source": "sec_archive_primary_document",
        "symbol": symbol,
        "accession": accn,
        "form": "10-K",
        "form_class": "periodic_report",
        "is_amendment": False,
        "filed": acceptance[:10],
        "acceptance_datetime": acceptance,
        "acceptance_us": asec.acceptance_us(asec.parse_acceptance(acceptance)),
        "public_first_visibility_lag": "unknown",
    }


def test_dated_as_filed_cover_before_the_clock_associates_the_ticker():
    clock = clock_us(DAY, 585, 250)
    row = asec.association_for(
        "NEWC", clock, _manifest([_dated("NEWC", "2020-06-02T14:00:00.000Z")])
    )
    assert row["status"] == asec.ASSOCIATED
    assert row["proof"]["symbol"] == "NEWC"
    assert row["proof"]["acceptance_datetime"] == "2020-06-02T14:00:00.000Z"
    assert row["proof"]["acceptance_us"] <= clock
    assert row["public_first_visibility_lag"] == "unknown"


def test_current_ticker_map_never_associates_a_ticker_before_its_dated_filing():
    # The seeded CIK is currently mapped to NEWC, but its only dated TradingSymbol
    # evidence was accepted 8 months AFTER this trade clock: a current map is a
    # discovery seed, not a point-in-time association.
    row = asec.association_for(
        "NEWC", clock_us(DAY, 585, 250), _manifest([_dated("NEWC", "2023-11-02T14:00:00.000Z")])
    )
    assert row["status"] == asec.NO_EVIDENCE_BEFORE_CLOCK
    assert "seeded CIK" in row["reason"]
    assert row["association_floor"] == "2019-02-01"


def test_a_recycled_symbol_is_not_associated_to_the_new_issuer_early():
    # The same CIK traded as OLDC before the clock; querying NEWC for that day must
    # NOT inherit the association from the seeded CIK.
    row = asec.association_for(
        "NEWC",
        clock_us(DAY, 585, 250),
        _manifest([_dated("OLDC", "2021-05-03T14:00:00.000Z")]),
    )
    assert row["status"] == asec.NO_EVIDENCE_BEFORE_CLOCK
    assert row["latest_symbol_before_clock"] == "OLDC"


def test_symbol_recycling_switches_per_day_from_the_same_series():
    series = [
        _dated("OLDC", "2021-05-03T14:00:00.000Z"),
        _dated("NEWC", "2024-02-01T14:00:00.000Z"),
    ]
    manifest = _manifest(series)
    early = asec.association_for("NEWC", clock_us("2022-06-01", 585, 250), manifest)
    late = asec.association_for("NEWC", clock_us("2025-03-03", 585, 250), manifest)
    assert early["status"] == asec.NO_EVIDENCE_BEFORE_CLOCK
    assert late["status"] == asec.ASSOCIATED
    assert late["proof"]["symbol"] == "NEWC"


# ----- late acceptance on the trade day ----------------------------------------
def test_late_same_day_acceptance_is_not_evidence_for_that_days_clock():
    # A filing accepted at 19:00 ET (23:00Z in March, EDT) on the trade day is after
    # a 10:06 ET intent clock: it exists in the record but must not become same-day
    # association evidence.
    clock = clock_us(DAY, 585, 250)
    row = asec.association_for("NEWC", clock, _manifest([_dated("NEWC", f"{DAY}T23:00:00.000Z")]))
    assert row["status"] == asec.NO_EVIDENCE_BEFORE_CLOCK
    assert "proof" not in row


def test_acceptance_before_the_same_days_clock_associates_that_day():
    # 09:20 ET on the trade day is 13:20Z in March (EDT) and precedes the clock.
    row = asec.association_for(
        "NEWC", clock_us(DAY, 585, 250), _manifest([_dated("NEWC", f"{DAY}T13:20:00.000Z")])
    )
    assert row["status"] == asec.ASSOCIATED


def test_evening_acceptance_flags_the_documented_next_business_day_dissemination():
    # SEC documents that submissions beginning after 5:30 p.m. ET are disseminated
    # the next business day; the collector records the flag, never applies it.
    late = asec.dissemination_flags(asec.parse_acceptance(f"{DAY}T23:00:00.000Z"), "8-K")
    assert late["after_530pm_et"] is True
    assert late["documented_possible_next_business_day_dissemination"] is True
    assert late["clock_source"] == "acceptanceDateTime"

    early = asec.dissemination_flags(asec.parse_acceptance(f"{DAY}T17:30:00.000Z"), "8-K")
    assert early["after_530pm_et"] is False
    assert early["documented_possible_next_business_day_dissemination"] is False

    # ownership forms 3/4/5 keep their own documented 10:00 p.m. ET cut-off
    ownership = asec.dissemination_flags(asec.parse_acceptance("2023-03-16T02:30:00.000Z"), "4")
    assert ownership["after_530pm_et"] is True
    assert ownership["after_ownership_10pm_et"] is True

    dateless = asec.dissemination_flags(None, "8-K")
    assert dateless["after_530pm_et"] is None


# ----- UNKNOWN is never "no filing" / "no event" -------------------------------
def test_unmapped_ticker_is_unknown_and_never_a_no_filing_claim():
    row = asec.association_for("ZZZZ", clock_us(DAY, 585, 250), _manifest([], cik=None))
    assert row["status"] == asec.NO_CIK_SEED
    assert row["reason"].startswith("ticker absent from the current SEC company_tickers index")
    assert "cik" in row and row["cik"] is None


def test_a_retrieved_cover_without_a_trading_symbol_fact_is_unknown_not_absent():
    # A verbatim as-filed cover accepted before the clock that carries no
    # dei:TradingSymbol fact (pre-inline-XBRL cover, or NoTradingSymbolFlag) is an
    # UNKNOWN symbol, never a claim that the security did not exist.
    clock = clock_us(DAY, 585, 250)
    cover = {
        "accession": "0000222222-19-000009",
        "acceptance_datetime": "2019-05-02T15:00:00.000Z",
        "status": "retrieved",
        "trading_symbols_as_filed": [],
    }
    row = asec.association_for("NEWC", clock, _manifest([], archive=[cover]))
    assert row["status"] == asec.NO_SYMBOL_FACT
    assert "UNKNOWN, not absent" in row["reason"]
    assert row["retrieved_covers"] == ["0000222222-19-000009"]


def test_no_dated_evidence_reason_names_the_floor():
    row = asec.association_for("NEWC", clock_us(DAY, 585, 250), _manifest([]))
    assert row["status"] == asec.NO_EVIDENCE_BEFORE_CLOCK
    assert "2019-02-01" in row["reason"]


def test_the_association_vocabulary_has_no_no_filing_or_no_event_status():
    for status in asec.ASSOCIATION_STATUSES:
        assert "no_filing" not in status
        assert "no_event" not in status
        assert "not_filed" not in status
    assert asec.ASSOCIATED not in asec.ASSOCIATION_STATUSES[1:]


# ----- protected days, panel projection, user agent ----------------------------
def test_protected_day_is_refused_before_any_read():
    with pytest.raises(SystemExit):
        asec.guard_days([DAY, "2024-05-01"])
    with pytest.raises(SystemExit):
        asec.guard_days(["2025-01-15"])
    asec.guard_days([DAY, "2023-12-29", "2025-05-30"])


def test_panel_projection_cannot_reach_an_outcome_or_label_column():
    protected = [
        "gross_15",
        "gross_60",
        "gross_390",
        "exit_et_15",
        "exit_et_60",
        "exit_et_390",
        "exit_status_15",
        "exit_status_60",
        "exit_status_390",
        "entry_open",
        "entry_status",
        "net",
        "label",
    ]
    for name in asec.PANEL_COLUMNS:
        assert name not in protected
    source = Path(asec.__file__).read_text()
    assert "gross_" not in source
    assert "exit_et" not in source


def test_panel_pairs_uses_the_earliest_intent_clock_per_ticker_day(tmp_path, monkeypatch):
    root = tmp_path / "panel"
    (root / "days").mkdir(parents=True)
    pl.DataFrame(
        {
            "day": [DAY] * 3,
            "ticker": ["AAAA", "AAAA", "BBBB"],
            "t": [585, 600, 575],
        }
    ).write_parquet(root / "days" / f"{DAY}.parquet")
    monkeypatch.setattr(asec, "PANEL_DAYS", root / "days")
    universe = asec.panel_pairs([DAY])
    assert set(universe.clock) == {(DAY, "AAAA"), (DAY, "BBBB")}
    assert universe.clock[(DAY, "AAAA")] == clock_us(DAY, 586, 250)
    assert universe.clock[(DAY, "BBBB")] == clock_us(DAY, 576, 250)
    assert universe.earliest_clock_by_ticker()["AAAA"] == clock_us(DAY, 586, 250)
    assert universe.by_ticker()["BBBB"] == (DAY,)


def test_calendar_days_excludes_the_protected_windows(tmp_path, monkeypatch):
    root = tmp_path / "panel"
    (root / "days").mkdir(parents=True)
    for day in ("2021-02-01", "2024-05-01", "2025-01-15", "2026-06-01", "2023-03-15"):
        pl.DataFrame({"day": [day], "ticker": ["AAAA"], "t": [575]}).write_parquet(
            root / "days" / f"{day}.parquet"
        )
    monkeypatch.setattr(asec, "PANEL_DAYS", root / "days")
    days = asec.calendar_days(None, ["train", "validation", "confirmation"])
    assert days == ["2021-02-01", "2023-03-15"]


def test_missing_user_agent_is_a_prerequisite_failure_not_a_fabricated_contact(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    args = asec.argparse.Namespace(user_agent=None)
    with pytest.raises(SystemExit) as err:
        asec.resolve_user_agent(args)
    assert "SEC_USER_AGENT" in str(err.value)
    assert "no contact is fabricated here" in str(err.value)

    # a bare product name has no contact token and is refused, not padded with a
    # fabricated e-mail address
    with pytest.raises(SystemExit):
        asec.resolve_user_agent(asec.argparse.Namespace(user_agent="Research Bot"))

    monkeypatch.setenv("SEC_USER_AGENT", "Alpacatrader research contact@example.invalid")
    assert asec.resolve_user_agent(args).endswith("contact@example.invalid")
    assert asec.sec_headers("Alpacatrader research contact@example.invalid")["User-Agent"] == (
        "Alpacatrader research contact@example.invalid"
    )


# ----- verbatim as-filed cover extraction --------------------------------------
def test_dei_cover_parsing_takes_as_filed_symbol_class_and_scaled_shares():
    doc = (
        _ix("EntityRegistrantName", "NEWC Corporation")
        + _ix("Security12bTitle", "Common Stock, $0.0001 par value")
        + _ix("SecurityExchangeName", "NASDAQ")
        + _ix("TradingSymbol", " NEWC ")
        + _ix_num("EntityCommonStockSharesOutstanding", "12,500,000")
        + _ix_num("EntityPublicFloat", "8.4", scale=6)
    )
    facts = asec.parse_dei_cover(doc)
    assert facts["TradingSymbol"]["value"] == "NEWC"
    assert facts["Security12bTitle"]["value"] == "Common Stock, $0.0001 par value"
    assert facts["SecurityExchangeName"]["value"] == "NASDAQ"
    summary = asec.cover_summary(facts)
    assert summary["trading_symbols_as_filed"] == ["NEWC"]
    assert summary["security_class_titles_as_filed"] == ["Common Stock, $0.0001 par value"]
    assert summary["exchange_as_filed"] == "NASDAQ"
    assert summary["issuer_name_as_filed"] == "NEWC Corporation"
    # shares outstanding, exactly as filed, NEVER a free-float share count
    assert summary["shares_outstanding_as_filed"] == pytest.approx(12_500_000.0)
    assert summary["public_float_usd_as_filed"] == pytest.approx(8.4e6)
    assert summary["inline_xbrl_dei_cover_present"] is True
    assert "free_float" not in summary


def test_dei_cover_parsing_keeps_every_listing_of_a_multi_class_security():
    doc = (
        _ix("Security12bTitle", "Class A Common Stock")
        + _ix("TradingSymbol", "FATA")
        + _ix("Security12bTitle", "Class B Common Stock")
        + _ix("TradingSymbol", "FATBB")
    )
    facts = asec.parse_dei_cover(doc)
    assert sorted(asec.cover_trading_symbols(facts)) == ["FATA", "FATBB"]
    assert len(facts["TradingSymbol#multi"]["records"]) == 2


def test_a_non_inline_xbrl_cover_yields_no_facts():
    facts = asec.parse_dei_cover("<html><body>no inline xbrl here</body></html>")
    assert facts == {}
    assert asec.cover_summary(facts)["inline_xbrl_dei_cover_present"] is False
    assert asec.cover_summary(facts)["shares_outstanding_as_filed"] is None


def test_amendment_and_form_class_labels_are_kept_separate_from_the_original():
    assert asec.form_class("424B4") == "ipo_prospectus"
    assert asec.form_class("S-1") == "registration"
    assert asec.form_class("8-A12B") == "exchange_listing_registration"
    assert asec.form_class("10-K/A") == "periodic_report"
    assert asec.form_class("XYZ-9") == "other_form"
    assert asec.is_amendment("10-K/A") is True
    assert asec.is_amendment("10-K") is False
    assert asec.base_form("8-K/A") == "8-K"
    assert asec.base_form("8-K") == "8-K"


# ----- end to end over a scripted SEC ------------------------------------------
def _routes() -> dict[str, object]:
    # 23:00Z is 19:00 ET in March (EDT): after the day's clock and after the
    # documented 5:30 p.m. ET dissemination cut-off.
    recent = _recent(
        [
            _submission(
                "0001111111-23-000005",
                "8-K",
                f"{DAY}T23:00:00.000Z",
                DAY,
                primary="8k.htm",
                items="Item 7.01",
            ),
            # 13:20Z is 09:20 ET: before the 10:06 ET intent clock on the same day.
            _submission(
                "0001111111-23-000001",
                "S-1",
                f"{DAY}T13:20:00.000Z",
                DAY,
                report_date="",
                primary="s1.htm",
            ),
            _submission("0001111111-20-000001", "10-K", "2020-06-02T14:00:00.000Z", "2020-06-02"),
        ]
    )
    return {
        asec.SEC_COMPANY_TICKERS_URL: json.dumps(_ticker_map()).encode(),
        asec.submissions_url("0001111111"): recent,
        asec.submissions_url("0000111111"): _recent(
            [_submission("0000111111-19-000009", "10-K", "2019-05-02T15:00:00.000Z", "2019-05-02")]
        ),
        asec.submissions_url("0000320193"): _recent([]),
        asec.concept_url("0001111111", asec.DEI_SYMBOL_TAG): _concept(
            asec.DEI_SYMBOL_TAG,
            [_symbol_fact("NEWC", "2020-06-02", "0001111111-20-000001")],
        ),
        asec.concept_url("0001111111", asec.DEI_SHARES_TAG): _concept(
            asec.DEI_SHARES_TAG,
            [
                {
                    "end": "2020-05-29",
                    "val": 12_500_000,
                    "filed": "2020-06-02",
                    "accn": "0001111111-20-000001",
                    "form": "10-K",
                    "fy": 2020,
                    "fp": "FY",
                    "frame": "CY2020Q1I",
                },
                {
                    "end": "2023-03-10",
                    "val": 31_250_000,
                    "filed": DAY,
                    "accn": "0001111111-23-000001",
                    "form": "S-1",
                    "fy": 2023,
                    "fp": "FY",
                    "frame": None,
                },
            ],
        ),
        asec.archive_url("0001111111", "0001111111-20-000001", "primary.htm"): _cover_document(
            "NEWC"
        ),
        asec.archive_url("0001111111", "0001111111-23-000001", "s1.htm"): _cover_document(
            "NEWC", shares="31,250,000"
        ),
        asec.archive_url("0001111111", "0001111111-23-000005", "8k.htm"): _cover_document(
            "NEWC", shares="30,000,000"
        ),
    }


def _args(tmp_path: Path, **kw) -> asec.argparse.Namespace:
    base = {
        "out": tmp_path / "out",
        "days": None,
        "blocks": "train,validation,confirmation",
        "resume": False,
        "plan_only": False,
        "user_agent": "Alpacatrader research contact@example.invalid",
        "req_sleep": 0.0,
        "limit_issuers": None,
    }
    base.update(kw)
    return asec.argparse.Namespace(**base)


def test_plan_only_writes_the_universe_before_any_network_request(tmp_path, monkeypatch):
    root = tmp_path / "panel"
    (root / "days").mkdir(parents=True)
    pl.DataFrame({"day": [DAY, DAY], "ticker": ["NEWC", "OTHER"], "t": [585, 600]}).write_parquet(
        root / "days" / f"{DAY}.parquet"
    )
    monkeypatch.setattr(asec, "PANEL_DAYS", root / "days")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    def _boom(*a, **k):  # any request at all is a contract violation
        raise AssertionError("plan-only must not touch the network")

    monkeypatch.setattr(asec, "get_json", _boom)
    monkeypatch.setattr(asec, "get_bytes", _boom)
    asec.run(_args(tmp_path, plan_only=True))

    universe = json.loads((tmp_path / "out" / "plan" / "universe.json").read_text())
    assert universe["planned_before_any_network_read"] is True
    assert universe["panel_columns_projected"] == ["day", "ticker", "t"]
    assert [(p["day"], p["ticker"]) for p in universe["pairs"]] == [(DAY, "NEWC"), (DAY, "OTHER")]
    # the association floor is derived from the earliest requested decision day and
    # is written down, not silently applied
    expected_floor = (
        date.fromisoformat(DAY) - timedelta(days=asec.ASSOCIATION_LOOKBACK_DAYS)
    ).isoformat()
    assert universe["association_floor"] == expected_floor
    summary = json.loads((tmp_path / "out" / "plan" / "summary.json").read_text())
    assert summary["network_requests_made"] == 0
    assert not (tmp_path / "out" / "raw").exists()


def test_run_collects_association_matrix_events_and_resumes_per_issuer(tmp_path, monkeypatch):
    import requests

    root = tmp_path / "panel"
    (root / "days").mkdir(parents=True)
    # a 2021 decision day keeps the association floor early enough that the 2020
    # as-filed cover survives it as point-in-time evidence for the later day
    for day, pairs in (
        ("2021-02-01", [("NEWC", 586)]),
        (DAY, [("NEWC", 585), ("QQQQ", 600)]),
    ):
        pl.DataFrame(
            {"day": [day] * len(pairs), "ticker": [t for t, _ in pairs], "t": [m for _, m in pairs]}
        ).write_parquet(root / "days" / f"{day}.parquet")
    monkeypatch.setattr(asec, "PANEL_DAYS", root / "days")
    out = tmp_path / "out"
    session = _Session(_routes())
    monkeypatch.setattr(requests, "Session", lambda *a, **k: session)
    asec.run(_args(tmp_path))
    assert session.calls, "the run must have requested the SEC endpoints"
    for url in session.calls:
        assert url.startswith(("https://www.sec.gov/", "https://data.sec.gov/"))

    assoc = json.loads((out / "association" / f"{DAY}.json").read_text())
    rows = {r["ticker"]: r for r in assoc["association"]}
    assert rows["NEWC"]["status"] == asec.ASSOCIATED
    assert rows["NEWC"]["proof"]["symbol"] == "NEWC"
    assert rows["QQQQ"]["status"] == asec.NO_CIK_SEED
    assert assoc["status_counts"][asec.ASSOCIATED] == 1
    assert assoc["status_counts"][asec.NO_CIK_SEED] == 1

    # the earlier decision day resolves from the SAME dated series
    early = json.loads((out / "association" / "2021-02-01.json").read_text())
    assert early["association"][0]["ticker"] == "NEWC"
    assert early["association"][0]["status"] == asec.ASSOCIATED

    events = json.loads((out / "events" / f"{DAY}.json").read_text())["events"]
    by_accession = {e["accession"]: e for e in events}
    # the S-1 accepted at 09:20 ET, before the 10:06 ET intent clock, with its
    # verbatim cover attached and its as-filed shares outstanding
    s1 = by_accession["0001111111-23-000001"]
    assert s1["form_class"] == "registration"
    assert s1["shares_outstanding_as_filed"] == pytest.approx(31_250_000.0)
    assert s1["archive_status"] == "retrieved"
    assert s1["cover_trading_symbols_as_filed"] == ["NEWC"]
    assert s1["shares_clock_source"] == "filed_date_only_not_intraday"
    # the 8-K accepted at 19:00 ET on the same trade day is recorded as an event, but
    # it is after that day's clock and carries the documented dissemination flag.
    eightk = by_accession["0001111111-23-000005"]
    assert eightk["form_class"] == "current_report"
    assert eightk["after_530pm_et"] is True
    assert eightk["documented_possible_next_business_day_dissemination"] is True
    assert eightk["public_first_visibility_lag"] == "unknown"
    for event in events:
        assert event["day"] == DAY
        assert event["availability"]["public_first_visibility_lag"] == "unknown"
        assert set(event["availability"]["date_only_fields"]) == {"filingDate", "reportDate"}

    summary = json.loads((out / "summary.json").read_text())
    assert summary["orders_or_paid_purchases"] is False
    assert summary["protected_outcomes_read"] is False
    assert summary["user_agent_never_printed_or_stored"] is True
    assert "contact@example.invalid" not in json.dumps(summary)
    assert summary["association_status_counts"][asec.ASSOCIATED] == 2
    assert summary["events_total"] == 2

    # resume: a completed issuer manifest is not refetched, and no SEC request is
    # repeated for a step or an archive page that is already on disk
    before = json.loads((out / "manifest" / "issuer" / "NEWC.json").read_text())
    resumed_session = _Session(_routes())
    monkeypatch.setattr(requests, "Session", lambda *a, **k: resumed_session)
    asec.run(_args(tmp_path, resume=True))
    assert resumed_session.calls == []
    after = json.loads((out / "manifest" / "issuer" / "NEWC.json").read_text())
    assert after["status"] == "complete"
    assert after["steps"]["submissions"]["resumed"] is True
    assert after["steps"][f"concept_{asec.DEI_SYMBOL_TAG}"]["resumed"] is True
    assert all(a.get("resumed") for a in after["archive"])
    assert len(after["filings"]) == len(before["filings"])
    assert len(after["association_series"]) == len(before["association_series"])
    assert len(after["events"]) == len(before["events"])
    # the verbatim covers are cached under the producer's own raw root
    assert (out / "raw" / "archive" / "0001111111" / "0001111111-23-000001.htm").exists()
    assert {a["status"] for a in after["archive"]} == {"retrieved"}


def test_full_history_follows_the_linked_submissions_pages(tmp_path):
    out = tmp_path / "out"
    pace = asec.Pace(0.0)
    # CIK 11's recent page links an older page, and that older page links one more.
    recent = _recent(
        [_submission("0000111111-21-000001", "10-K", "2021-04-02T14:00:00.000Z", "2021-04-02")],
        files=[
            {
                "name": "CIK0000111111-submissions-001.json",
                "filingCount": 3,
                "filingFrom": "2015-01-05",
                "filingTo": "2020-12-31",
            }
        ],
    )
    shard = _recent(
        [_submission("0000111111-19-000002", "10-K", "2019-05-02T14:00:00.000Z", "2019-05-02")],
        files=[
            {
                "name": "CIK0000111111-submissions-002.json",
                "filingCount": 1,
                "filingFrom": "2010-01-04",
                "filingTo": "2014-12-31",
            }
        ],
    )
    too_old = _recent(
        [_submission("0000111111-15-000003", "10-K", "2015-05-01T14:00:00.000Z", "2015-05-01")]
    )
    session = _Session(
        {
            asec.submissions_url("0000111111"): recent,
            asec.shard_url("CIK0000111111-submissions-001.json"): shard,
            asec.shard_url("CIK0000111111-submissions-002.json"): too_old,
        }
    )
    step = asec.fetch_submissions(session, pace, {}, "0000111111", "2019-01-01", out)
    assert step["status"] == "complete"
    assert step["pages"] == 2  # recent + the one linked page after the floor
    assert step["full_history_linked_pages_followed"] is True
    assert step["shards_skipped_before_floor"] == 1  # the 2014 page is recorded, not fetched
    assert {r["accessionNumber"] for r in step["rows"]} == {
        "0000111111-21-000001",
        "0000111111-19-000002",
    }
    # the verbatim pages are cached, and the older linked page was never requested
    assert (out / "raw" / "submissions").exists()
    assert asec.shard_url("CIK0000111111-submissions-002.json") not in session.calls


def test_full_history_has_no_page_cap_and_keeps_the_deepest_dated_proof(tmp_path):
    # 13 RELEVANT linked shards. A fixed page cap stopped at 12 pages (the recent
    # page plus 11 shards) and silently dropped the oldest relevant shards - among
    # them the dated as-filed 10-K cover that is the ONLY before-clock association
    # proof for this issuer, which would have turned a positive association into a
    # false UNKNOWN. The full relevant history must be followed instead.
    out = tmp_path / "out"
    pace = asec.Pace(0.0)
    floor = "2019-01-01"
    shard_names = [f"CIK0000111111-submissions-{i:03d}.json" for i in range(1, 14)]
    recent = _recent(
        [_submission("0000111111-23-000001", "10-K", "2023-04-03T14:00:00.000Z", "2023-04-03")],
        files=[
            *[
                {
                    "name": name,
                    "filingCount": 1,
                    "filingFrom": "2019-01-01",
                    "filingTo": "2023-01-01",
                }
                for name in shard_names
            ],
            # affirmatively older than the floor: recorded, never requested
            {
                "name": "CIK0000111111-submissions-014.json",
                "filingCount": 1,
                "filingFrom": "2010-01-04",
                "filingTo": "2018-12-31",
            },
        ],
    )
    routes: dict[str, object] = {asec.submissions_url("0000111111"): recent}
    # The first shard carries a too-old row (dropped by the row floor) and links back
    # to the recent page and to an already-queued shard: neither may be fetched twice.
    routes[asec.shard_url(shard_names[0])] = _recent(
        [
            _submission("0000111111-18-000009", "10-K", "2018-06-01T14:00:00.000Z", "2018-06-01"),
            _submission("0000111111-22-000002", "10-K", "2022-04-01T14:00:00.000Z", "2022-04-01"),
        ],
        files=[
            {"name": "CIK0000111111.json", "filingFrom": "2023-01-01", "filingTo": "2023-12-31"},
            {"name": shard_names[-1], "filingFrom": "2019-01-01", "filingTo": "2019-12-31"},
        ],
    )
    for index, name in enumerate(shard_names[1:-1], start=2):
        routes[asec.shard_url(name)] = _recent(
            [
                _submission(
                    f"0000111111-20-{index:06d}",
                    "10-K",
                    "2020-06-01T14:00:00.000Z",
                    "2020-06-01",
                )
            ]
        )
    # The deepest relevant shard holds the only dated as-filed cover accepted before
    # any panel clock: without a page cap it is fetched, and the proof survives.
    accn = "0000111111-19-000010"
    routes[asec.shard_url(shard_names[-1])] = _recent(
        [_submission(accn, "10-K", "2019-05-02T15:00:00.000Z", "2019-05-02")]
    )
    session = _Session(routes)
    step = asec.fetch_submissions(session, pace, {}, "0000111111", floor, out)
    assert step["status"] == "complete"
    # the recent page + all 13 relevant shards: no fixed page bound applies
    assert step["pages"] == 14
    assert step["full_history_linked_pages_followed"] is True
    # every relevant page was requested exactly once, and no page was requested twice
    assert session.calls.count(asec.submissions_url("0000111111")) == 1
    for name in shard_names:
        assert session.calls.count(asec.shard_url(name)) == 1
    # the excluded older page is explicit, by name, instead of a silent drop
    assert step["shards_skipped_before_floor"] == 1
    assert step["shards_skipped_before_floor_names"] == ["CIK0000111111-submissions-014.json"]
    assert asec.shard_url("CIK0000111111-submissions-014.json") not in session.calls
    # the row floor still drops the one too-old row of a fetched page, and counts it
    assert step["rows_total"] == 15
    assert step["rows_dropped_before_floor"] == 1
    assert step["rows_persisted"] == len(step["rows"]) == 14
    # every fetched page is cached verbatim
    assert len(list((out / "raw" / "submissions").glob("*.json"))) == 14
    # the dated proof of the deepest relevant shard survives the collection
    assert accn in {r["accessionNumber"] for r in step["rows"]}
    # and it flows through the real assembly path into a POSITIVE association: the
    # shard a page cap would have dropped is exactly the evidence for this issuer
    series = asec.build_symbol_series(
        {
            "rows": asec.concept_facts(
                _concept(asec.DEI_SYMBOL_TAG, [_symbol_fact("NEWC", "2019-05-02", accn)]),
                asec.DEI_SYMBOL_TAG,
            )
        },
        step["rows"],
        [],
    )
    manifest = _manifest(series, cik="0000111111")
    manifest["association_floor"] = floor
    row = asec.association_for("NEWC", clock_us(DAY, 585, 250), manifest)
    assert row["status"] == asec.ASSOCIATED
    assert row["proof"]["accession"] == "0000111111-19-000010"


def test_an_unreachable_submissions_page_is_unknown_not_an_empty_history(tmp_path):
    out = tmp_path / "out"
    pace = asec.Pace(0.0)
    step = asec.fetch_submissions(_Session({}), pace, {}, "0001111111", "2019-01-01", out)
    assert step["status"] == "unknown_fetch_failed"
    assert step["http_status"] == 404
    assert step["rows"] == []
    assert step["rows_total"] == 0


def test_an_unreachable_archive_is_unknown_never_a_fabricated_cover(tmp_path):
    out = tmp_path / "out"
    pace = asec.Pace(0.0)
    session = _Session({})  # every archive URL 404s
    row = {
        "accessionNumber": "0001111111-23-000007",
        "form": "S-1",
        "acceptanceDateTime": f"{DAY}T13:20:00.000Z",
        "filingDate": DAY,
        "reportDate": "",
        "primaryDocument": "missing.htm",
    }
    doc = asec.retrieve_archive(
        session, pace, asec.sec_headers("Org contact@example.invalid"), out, "0001111111", row
    )
    assert doc["status"] == "unknown_fetch_failed"
    assert doc["http_status"] == 404
    assert doc["public_first_visibility_lag"] == "unknown"
    # nothing is stored and no cover fact is invented
    assert "trading_symbols_as_filed" not in doc
    assert "shares_outstanding_as_filed" not in doc
    assert "raw_sha256" not in doc
    assert not (out / "raw" / "archive").exists()


def test_a_missing_primary_document_is_unknown_not_a_guess(tmp_path):
    out = tmp_path / "out"
    pace = asec.Pace(0.0)
    row = {
        "accessionNumber": "0001111111-23-000008",
        "form": "S-1",
        "acceptanceDateTime": f"{DAY}T13:20:00.000Z",
        "filingDate": DAY,
        "primaryDocument": "",
    }
    doc = asec.retrieve_archive(_Session({}), pace, {}, out, "0001111111", row)
    assert doc["status"] == "unknown_no_primary_document"
    assert "source_url" not in doc
