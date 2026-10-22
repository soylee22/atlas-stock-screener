"""Build a streamlined stock catalogue from Trading 212 enriched with TradingView fundamentals.

Filters:
1. Tradeable on Trading 212 with open buying capacity (maxOpenQuantity > 0).
2. Primary ordinary listings on major exchanges (US, UK, Germany, France, Canada, Netherlands, Spain, Switzerland, Italy, Sweden, Denmark).
3. Market cap >= $500M USD to eliminate illiquid penny shares and micro-caps.
4. Positive net income (profitable businesses only).
5. Active dividend yield (> 0%) isolating productive cash-returning capital.
6. Matched by ISIN to verified Trading 212 instruments.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "seed" / "stocks-2026-10-23.csv"
T212_CACHE = Path("/tmp/t212_instruments.json")
if not T212_CACHE.exists():
    T212_CACHE = Path("/Users/leeslater/SecondBrain/private/t212_instruments.json")

MARKET_SUFFIXES = {
    "america": "",
    "uk": ".L",
    "germany": ".DE",
    "france": ".PA",
    "canada": ".TO",
    "netherlands": ".AS",
    "spain": ".MC",
    "switzerland": ".SW",
    "italy": ".MI",
    "sweden": ".ST",
    "denmark": ".CO",
}

REGION_CODES = {
    "america": "us",
    "uk": "gb",
    "germany": "de",
    "france": "fr",
    "canada": "ca",
    "netherlands": "nl",
    "spain": "es",
    "switzerland": "ch",
    "italy": "it",
    "sweden": "se",
    "denmark": "dk",
}


def load_t212_stocks() -> dict[str, dict]:
    """Load investable stocks from Trading 212 instrument master."""
    if not T212_CACHE.exists():
        raise FileNotFoundError(f"Trading 212 instrument cache not found: {T212_CACHE}")
    data = json.loads(T212_CACHE.read_text())
    stocks = [
        d for d in data
        if d.get("type") == "STOCK" and (d.get("maxOpenQuantity") or 0) > 0 and d.get("isin")
    ]
    by_isin = {}
    for s in stocks:
        isin = s["isin"]
        # If multiple share classes exist, prefer US or London or Xetra lines
        if isin not in by_isin:
            by_isin[isin] = s
        else:
            prev = by_isin[isin]
            p_t = prev.get("ticker", "")
            s_t = s.get("ticker", "")
            if s_t.endswith("_US_EQ") or s_t.endswith("l_EQ") or s_t.endswith("d_EQ"):
                by_isin[isin] = s
    return by_isin


def fetch_tv_market(market: str, min_mcap: float = 500_000_000, min_yield: float = 0.0) -> list[dict]:
    """Fetch primary stocks meeting market cap and profitability thresholds from TradingView."""
    url = f"https://scanner.tradingview.com/{market}/scan"
    cols = [
        "name", "description", "close", "currency", "market_cap_basic",
        "return_on_equity", "dividends_yield", "net_income",
        "sector", "industry", "country", "exchange", "isin"
    ]
    filters = [
        {"left": "type", "operation": "in_range", "right": ["stock", "dr"]},
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "market_cap_basic", "operation": "egreater", "right": min_mcap},
        {"left": "net_income", "operation": "greater", "right": 0},
    ]
    if min_yield > 0:
        filters.append({"left": "dividends_yield", "operation": "greater", "right": min_yield})

    payload = {
        "filter": filters,
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": cols,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 5000]
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        results = []
        for row in data.get("data", []):
            d = row.get("d", [])
            if len(d) == len(cols):
                rec = dict(zip(cols, d))
                rec["tv_symbol"] = row.get("s", "")
                results.append(rec)
        return results
    except Exception as e:
        logging.warning("Failed fetching %s: %s", market, e)
        return []


def to_yahoo_symbol(ticker: str, market: str, exchange: str) -> str:
    """Format TradingView ticker into Yahoo Finance symbol."""
    if market == "uk":
        ticker = ticker.rstrip(".")
    clean = ticker.replace(".", "-").replace("_", "-")
    suffix = MARKET_SUFFIXES.get(market, "")
    if market == "canada" and exchange in ("TSXV", "V"):
        suffix = ".V"
    return f"{clean}{suffix}"


def build_catalogue(min_mcap: float = 500_000_000, min_yield: float = 0.0) -> list[dict]:
    t212_stocks = load_t212_stocks()
    print(f"Loaded {len(t212_stocks)} unique ISINs from Trading 212 investable universe.")

    catalogue = []
    seen_isins = set()
    seen_symbols = set()

    for market in MARKET_SUFFIXES:
        rows = fetch_tv_market(market, min_mcap=min_mcap, min_yield=min_yield)
        matched = 0
        for r in rows:
            isin = r.get("isin")
            if not isin or isin not in t212_stocks or isin in seen_isins:
                continue

            t212_item = t212_stocks[isin]
            ticker = r["name"]
            exchange = r.get("exchange", "")
            ysym = to_yahoo_symbol(ticker, market, exchange)

            if ysym in seen_symbols:
                continue

            seen_isins.add(isin)
            seen_symbols.add(ysym)
            matched += 1

            catalogue.append({
                "symbol": ysym,
                "name": r["description"] or t212_item.get("name", ticker),
                "isin": isin,
                "t212_ticker": t212_item.get("ticker", ""),
                "region_code": REGION_CODES.get(market, "us"),
                "market": market,
                "exchange": exchange,
                "country": r.get("country", ""),
                "sector": r.get("sector") or "Unclassified",
                "industry": r.get("industry") or "Unclassified",
                "currency": r.get("currency", "USD"),
                "price": r.get("close") or 0.0,
                "market_cap": r.get("market_cap_basic") or 0.0,
                "net_income": r.get("net_income") or 0.0,
                "roe": r.get("return_on_equity") or 0.0,
                "div_yield": r.get("dividends_yield") or 0.0,
            })
        print(f"Market {market:12}: {len(rows):4} TradingView records -> {matched:4} matched on T212")

    catalogue.sort(key=lambda x: x["market_cap"], reverse=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol", "name", "isin", "t212_ticker", "region_code", "market", "exchange",
        "country", "sector", "industry", "currency", "price", "market_cap",
        "net_income", "roe", "div_yield"
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(catalogue)

    print(f"Saved {len(catalogue)} investable stocks to {OUT_CSV}")
    return catalogue


if __name__ == "__main__":
    build_catalogue(min_mcap=500_000_000, min_yield=0.01)
