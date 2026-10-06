import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATH))
import app as screener


def test_pence_price_and_market_cap_have_different_units():
    fx = {"GBP": {"rate": 1.25}, "USD": {"rate": 1}}
    row = screener.dollarise(dict(quote_currency="GBp", price_local=1200, market_cap_local=10e9,
                                   financial_currency="USD", net_income_local=1e9), fx)
    assert row["price"] == 15
    assert row["market_cap"] == 12.5e9
    assert row["net_income"] == 1e9


def test_unknown_currency_does_not_invent_conversion():
    assert screener.usd(100, "NOPE", {}) is None
    assert screener.number(float("nan")) is None


def test_cash_flow_ttm_and_annual_growth_are_distinct():
    qdates = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
    adates = pd.to_datetime(["2025-12-31", "2024-12-31"])
    income = pd.DataFrame([[10, 10, 10, 10], [100, 100, 100, 100]], index=["Net Income", "Total Revenue"], columns=qdates)
    qcf = pd.DataFrame([[30, 30, 30, 30], [-10, -10, -10, -10]], index=["Operating Cash Flow", "Capital Expenditure"], columns=qdates)
    acf = pd.DataFrame([[100, 80], [-20, -20]], index=qcf.index, columns=adates)
    values = screener.financial_values(income, pd.DataFrame(), qcf, acf)
    assert values["net_income_local"] == 40
    assert values["net_margin"] == 10
    assert values["capex_local"] == 40
    assert values["fcf_local"] == 80
    assert values["fcf_change"] == pytest.approx(100 / 3)
    assert values["fcf_delta_local"] == 20
    assert values["cf_period"] == "TTM 2026-06-30"
    assert values["fcf_growth_period"] == "FY 2025-12-31 / 2024-12-31"


def test_quarter_gaps_do_not_create_false_ttm():
    series = pd.Series([1, 2, 3, 4], index=pd.to_datetime(["2026-06-30", "2025-12-31", "2025-09-30", "2025-06-30"]))
    assert screener.four_quarters(series) is None


def test_margin_does_not_mix_reporting_periods():
    frame = pd.DataFrame([[1, None], [None, 10]], index=["Net Income", "Total Revenue"], columns=pd.to_datetime(["2025-12-31", "2024-12-31"]))
    result = screener.financial_values(pd.DataFrame(), frame, pd.DataFrame(), pd.DataFrame())
    assert result["net_margin"] is None


def test_negative_fcf_base_withholds_percent_growth():
    cf = pd.DataFrame([[10, 5], [-5, -10]], index=["Operating Cash Flow", "Capital Expenditure"], columns=pd.to_datetime(["2025-12-31", "2024-12-31"]))
    values = screener.financial_values(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), cf)
    assert values["fcf_change"] is None
    assert values["fcf_delta_local"] == 10


def test_dividend_streak_excludes_partial_year_and_counts_gaps():
    dividends = pd.Series([1, 1.1, 1.2, 1.3, 1.4, 1.5, .1], index=pd.to_datetime([f"{y}-06-01" for y in range(2020, 2027)]))
    values = screener.dividend_values(dividends, date(2026, 10, 6))
    assert values["div_years"] == 5
    assert values["div_growth"] == pytest.approx((1.5 ** .2 - 1) * 100)
    values = screener.dividend_values(dividends.drop(pd.Timestamp("2024-06-01")), date(2026, 10, 6))
    assert values["div_years"] == 0
    assert values["div_growth"] is None


def test_missing_dividend_history_remains_missing():
    values = screener.dividend_values(pd.Series(dtype=float))
    assert values["div_years"] is None


def test_large_universe_uses_complete_partitions_instead_of_clamped_offsets(tmp_path, monkeypatch):
    pipeline = screener.Pipeline(screener.Store(tmp_path / "test.sqlite"))
    prices = [0.5] * 5000 + [20] * 5100
    calls = []

    def fake_page(region, offset, run_id, sort="ticker", query=None):
        selected = prices
        body = query.to_dict()
        for condition in body.get("operands", []):
            if not isinstance(condition, dict):
                continue
            operands = condition.get("operands", [])
            if operands and operands[0] == "intradayprice":
                selected = [p for p in selected if p > operands[1]] if condition["operator"] == "GT" else [p for p in selected if p <= operands[1]]
        calls.append((offset, len(selected)))
        return dict(expected=len(selected), loaded=len(selected[offset:offset + 250]))

    monkeypatch.setattr(pipeline, "page", fake_page)
    progress = {"loaded": 0}
    pipeline.partition("us", "test", progress, {"us": progress})
    assert progress["loaded"] == len(prices)
    assert all(offset < 9500 for offset, _ in calls)


def test_yahoo_etfs_and_warrants_are_separate_from_default_stock_view():
    assert screener.quote_row(dict(symbol="FAKE", quoteType="ETF"), "us")["instrument"] == "other"
    assert screener.quote_row(dict(symbol="030001.TW", quoteType="EQUITY"), "tw")["instrument"] == "other"
    assert screener.quote_row(dict(symbol="2330.TW", quoteType="EQUITY"), "tw")["instrument"] == "stock"
    assert screener.quote_row(dict(symbol="2881A.TW", quoteType="EQUITY", marketCap=1e9), "tw")["instrument"] == "stock"


def test_dividend_csv_preserves_pence_units_gaps_and_ytd(tmp_path, monkeypatch):
    import csv
    import io
    store = screener.Store(tmp_path / "test.sqlite")
    store.set_meta("fx", {"GBP": {"rate": 1.25, "date": "2026-10-06"}})
    store.upsert_many([dict(symbol="TEST.L", region_code="gb", quote_currency="GBp", dividend_currency="GBp",
                            dividend_events=[dict(date="2023-06-01", amount=100), dict(date="2025-06-01", amount=200), dict(date="2026-06-01", amount=50)])])
    monkeypatch.setattr(screener, "store", store)
    response = screener.dividend_export("TEST.L", "annual")
    rows = {int(r["year"]): r for r in csv.DictReader(io.StringIO(response.body.decode()))}
    assert float(rows[2025]["dividend_per_share_local"]) == 2
    assert float(rows[2025]["dividend_per_share_usd_current_fx"]) == 2.5
    assert float(rows[2024]["dividend_per_share_local"]) == 0
    assert rows[2026]["growth_percent"] == ""
    assert rows[2026]["period"] == "YTD"
    assert rows[2025]["currency"] == "GBP"


def test_numeric_sorting_missing_last_and_numeric_range(tmp_path):
    store = screener.Store(tmp_path / "test.sqlite")
    store.set_meta("fx", {"USD": {"rate": 1}})
    store.upsert_many([dict(symbol=s, name=s, region_code="us", region="United States", instrument="stock", active=True,
                            market_cap_local=cap, quote_currency="USD") for s, cap in [("BIG", 10e9), ("SMALL", 100e6), ("MISSING", None)]])
    where, args, order = screener.query_sql()
    with store.connect() as conn:
        assert [r[0] for r in conn.execute(f"SELECT symbol FROM stocks WHERE {where} ORDER BY {order}", args)] == ["BIG", "SMALL", "MISSING"]
        where, args, order = screener.query_sql(filters=json.dumps([dict(field="market_cap", op="gte", value=1e9)]))
        assert [r[0] for r in conn.execute(f"SELECT symbol FROM stocks WHERE {where} ORDER BY {order}", args)] == ["BIG"]


def test_quotes_do_not_change_statement_currency(tmp_path):
    store = screener.Store(tmp_path / "test.sqlite")
    store.upsert_many([dict(symbol="TEST", region_code="gb", financial_currency="USD", financial_fetched="2026-10-06")], enriched=True)
    store.upsert_many([dict(symbol="TEST", region_code="gb", financial_currency="GBP")])
    assert store.get("TEST")["financial_currency"] == "USD"


def listing(symbol, region, exchange, name, domicile=None, volume=100, price=10):
    return dict(symbol=symbol, region_code=region, region=screener.REGIONS[region], exchange=exchange,
                name=name, domicile=domicile, instrument="stock", active=True, price=price, volume=volume)


def test_main_view_removes_nvidia_wrappers_and_secondary_quotes():
    rows = [listing("NVDA", "us", "NasdaqGS", "NVIDIA Corporation", "United States"),
            listing("NVDA.NE", "ca", "Cboe CA", "NVIDIA Corporation", volume=10000),
            listing("NVDA.TO", "ca", "Toronto", "NVIDIA Corporation"),
            listing("0R1I.IL", "gb", "IOB", "NVIDIA Corporation"),
            listing("NVDD.XC", "gb", "Cboe UK", "NVIDIA Corporation")]
    flags = screener.main_listing_flags(rows)
    assert {s for s, f in flags.items() if f["main_listing"]} == {"NVDA"}


def test_main_view_prefers_home_market_and_keeps_share_classes():
    rows = [listing("GOOG", "us", "NasdaqGS", "Alphabet Inc.", "United States"),
            listing("GOOGL", "us", "NasdaqGS", "Alphabet Inc.", "United States"),
            listing("GOOG.TO", "ca", "Toronto", "Alphabet Inc."),
            listing("RY", "us", "NYSE", "Royal Bank of Canada", "Canada", volume=10000),
            listing("RY.TO", "ca", "Toronto", "Royal Bank of Canada"),
            listing("TSM", "us", "NYSE", "Taiwan Semiconductor", "Taiwan"),
            listing("2330.TW", "tw", "Taiwan", "Taiwan Semiconductor")]
    flags = screener.main_listing_flags(rows)
    assert {s for s, f in flags.items() if f["main_listing"]} == {"GOOG", "GOOGL", "RY.TO", "2330.TW"}


def test_main_view_keeps_real_small_exchange_companies_and_excludes_wrappers():
    rows = [listing("REAL.NE", "ca", "Cboe CA", "Canadian Business Inc.", "Canada"),
            listing("SMALL.V", "ca", "TSXV", "Small Canadian Business"),
            listing("SMALL.L", "gb", "LSE", "Small British Business"),
            listing("ASML.NE", "ca", "Cboe CA", "ASML CDR (CAD Hedged)"),
            listing("NVHI.NE", "ca", "Cboe CA", "NINEPOINT NVIDIA HIGHSHARES ETF"),
            listing("3NVD.L", "gb", "LSE", "Leverage Shares 3x NVIDIA ETP"),
            listing("3GOL.L", "gb", "LSE", "WisdomTree Gold 3x Daily Leveraged"),
            listing("OTC", "us", "OTC Markets OTCQX", "OTC Company")]
    flags = screener.main_listing_flags(rows)
    assert {s for s, f in flags.items() if f["main_listing"]} == {"REAL.NE", "SMALL.V", "SMALL.L"}


def test_main_filter_runs_before_market_selection_and_csv_matches(tmp_path, monkeypatch):
    import csv
    import io
    store = screener.Store(tmp_path / "test.sqlite")
    store.set_meta("fx", {"USD": {"rate": 1}})
    rows = [listing("NVDA", "us", "NasdaqGS", "NVIDIA Corporation", "United States"),
            listing("NVDA.TO", "ca", "Toronto", "NVIDIA Corporation"),
            listing("RY.TO", "ca", "Toronto", "Royal Bank of Canada", "Canada")]
    for row in rows:
        row.update(quote_currency="USD", price_local=10, market_cap_local=1e9)
    store.upsert_many(rows)
    store.classify_listings(force=True)
    monkeypatch.setattr(screener, "store", store)
    result, total = screener.select_rows("", "ca", "[]", "market_cap", "desc", False, "", 100, 0, True)
    assert total == 1 and result[0]["symbol"] == "RY.TO"
    response = screener.export(search="NVIDIA", columns="symbol", main_only=True)
    assert [r["symbol"] for r in csv.DictReader(io.StringIO(response.body.decode()))] == ["NVDA"]
    response = screener.export(search="NVIDIA", columns="symbol", main_only=False)
    assert {r["symbol"] for r in csv.DictReader(io.StringIO(response.body.decode()))} == {"NVDA", "NVDA.TO"}


@pytest.mark.parametrize("kwargs", [dict(sort="bad"), dict(direction="DROP TABLE"), dict(filters='[null]'), dict(filters='[{"field":"market_cap","op":"gte","value":"bad"}]'), dict(filters='[{"field":"symbol); DROP TABLE stocks","op":"eq","value":"x"}]')])
def test_invalid_queries_are_rejected(kwargs):
    with pytest.raises(ValueError):
        screener.query_sql(**kwargs)
