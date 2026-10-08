"""Local Yahoo Finance screener. Run: python3 app.py --port 8765."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import ipaddress
import json
import logging
import math
import queue
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import yfinance as yf
from PIL import Image
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "screener.sqlite"
REGIONS = {"us": "United States", "gb": "United Kingdom", "ca": "Canada",
           "jp": "Japan", "kr": "South Korea", "tw": "Taiwan",
           "de": "Germany", "es": "Spain", "it": "Italy", "nl": "Netherlands",
           "dk": "Denmark", "se": "Sweden"}
EUROPE_REGIONS = {"de", "es", "it", "nl", "dk", "se"}
LOG = logging.getLogger("screener")


def region_query(region):
    query = yf.EquityQuery("eq", ["region", region])
    # These feeds contain large securitised-product universes. Require a company market cap.
    return yf.EquityQuery("and", [query, yf.EquityQuery("gt", ["intradaymarketcap", 0])]) if region in EUROPE_REGIONS else query


def col(key, label, kind="number", default=False, group="Overview", description=""):
    return dict(key=key, label=label, kind=kind, default=default, group=group, description=description)


GROWTH_HORIZONS = (1, 3, 5, 10)
GROWTH_KEYS = [f"{metric}_growth_{years}y" for metric in ("revenue", "net_income") for years in GROWTH_HORIZONS]
GROWTH_COLUMNS = [col(key, f"{'Revenue' if key.startswith('revenue') else 'Net income'} {'growth 1Y' if years == 1 else 'CAGR ' + str(years) + 'Y'}",
    "percent", group="Growth", description=f"{years}-year growth over consecutive completed fiscal-year statements in reporting currency. "
    + ("Latest FY versus preceding FY. Requires a positive base." if years == 1 else "Annualised CAGR. Requires a positive base and non-negative endpoint.")
    + " Yahoo usually supplies four annual records. Longer horizons stay unavailable until enough history is cached.")
    for metric in ("revenue", "net_income") for years in GROWTH_HORIZONS for key in [f"{metric}_growth_{years}y"]]


COLUMNS = [
    col("market_cap", "Market cap", "usd", True, description="Yahoo market cap converted from its base quote currency to USD."),
    col("price", "Price", "price", True, description="Latest Yahoo quote in USD. UK pence prices are divided by 100 before conversion."),
    col("volume", "Volume", "number", True, description="Latest session volume in shares. Volume is not a monetary amount."),
    col("region", "Market", "text", True, description="Listing market. This is not the company's country of domicile."),
    col("sector", "Sector", "text", True), col("industry", "Industry", "text", True),
    col("net_income", "Net income", "usd", True, "Financials", "Four reported quarters when available, otherwise latest FY. Check the row's income period."),
    col("net_margin", "Net margin", "percent", True, "Financials", "Net income divided by revenue for the same reported period."),
    col("capex", "Capex", "usd", True, "Cash flow", "Capital expenditure shown as a positive outflow. Period shown in company details."),
    col("fcf", "Free cash flow", "usd", True, "Cash flow", "Operating cash flow less capital expenditure, from reported statements."),
    col("fcf_change", "FCF growth", "percent", True, "Cash flow", "Latest FY versus preceding FY. Withheld when prior FCF is zero or negative."),
    col("div_yield", "Div yield", "percent", True, "Dividends", "Yahoo indicated annual dividend yield, already expressed in percentage points."),
    col("div_years", "Div growth years", "integer", True, "Dividends", "Observed consecutive increases in total annual dividends per share, ending in the latest completed calendar year. History can be incomplete."),
    col("div_growth", "Div growth 5Y", "percent", True, "Dividends", "Five-year CAGR of local-currency annual dividends per share over completed calendar years. Requires both endpoints and all intervening years."),
    col("exchange", "Exchange", "text", True),
    col("change", "Day change", "percent", group="Performance"),
    col("revenue", "Revenue", "usd", group="Financials"),
    *GROWTH_COLUMNS,
    col("operating_cf", "Operating cash flow", "usd", group="Cash flow"),
    col("fcf_delta", "FCF change $", "usd", group="Cash flow", description="Latest FY less preceding FY, converted using the current cached FX rate."),
    col("fcf_yield", "FCF yield", "percent", group="Cash flow", description="Reported FCF divided by current USD market cap. Period may be TTM or FY."),
    col("pe", "P/E", group="Valuation"), col("forward_pe", "Forward P/E", group="Valuation"),
    col("price_book", "Price / book", group="Valuation"),
    col("roe", "Return on equity", "percent", group="Financials"),
    col("roic_proxy", "ROIC proxy", "percent", group="Financials", description="FY after-tax operating income proxy / average book debt plus equity less cash. Uses effective tax, excludes missing/invalid inputs and makes no lease, R&D or goodwill adjustments. Compare operating businesses with consistent methods."),
    col("roic_proxy_period", "ROIC proxy period", "text", group="Financials"),
    col("debt", "Total debt", "usd", group="Financials"), col("cash", "Cash", "usd", group="Financials"),
    col("beta", "Beta", group="Performance"),
    col("avg_volume", "Avg volume 3M", group="Overview"),
    col("low_52w", "52W low", "price", group="Performance"),
    col("high_52w", "52W high", "price", group="Performance"),
    col("below_52w_high", "Below 52W high", "percent", group="Performance", description="100 × (52-week high − latest price) / high, using matching quote units. 0% is at the high. Lower values are closer to the high. Negative values indicate a price above the quoted high."),
    col("change_52w", "52W change", "percent", group="Performance"),
    *[col("sma_"+suffix+"_distance",label+" distance","percent",group="Technicals",description="Completed-session close / SMA minus one, as a percentage. Positive above, negative below. Weekly SMA uses completed weekly closes. See source dates.") for suffix,label in [("200d","200-day SMA"),("200w","200-week SMA"),("custom","Custom SMA")]],
    *[col("sma_"+suffix,label,"price",group="Technicals") for suffix,label in [("200d","200-day SMA"),("200w","200-week SMA"),("custom","Custom SMA")]],
    *[col("sma_"+suffix+"_period",label+" period","text",group="Technicals") for suffix,label in [("200d","200-day SMA"),("200w","200-week SMA"),("custom","Custom SMA")]],
    col("technical_asof","SMA reference close date","text",group="Technicals"),
    col("quote_currency", "Quote currency", "text"),
    col("financial_currency", "Reporting currency", "text", group="Financials"),
    col("income_period", "Income period", "text", group="Financials"),
    col("cf_period", "Cash flow period", "text", group="Cash flow"),
    col("fcf_growth_period", "FCF growth period", "text", group="Cash flow"),
    *[col(field["key"] + "_period", field["label"] + " period", "text", group="Growth") for field in GROWTH_COLUMNS],
    col("annual_growth_fetched", "Annual statements fetched", "text", group="Growth"),
    col("quote_time", "Quote time", "text"), col("financial_fetched", "Financials fetched", "text", group="Financials"),
    col("delay", "Quote delay (min)", "integer"),
    col("quote_type", "Yahoo instrument type", "text"),
]
FIELDS = {x["key"]: x for x in COLUMNS}
FIELDS.update({"symbol": dict(kind="text"), "name": dict(kind="text"), "instrument": dict(kind="text")})
MONETARY_FINANCIAL = ["net_income", "revenue", "capex", "fcf", "operating_cf", "fcf_delta", "debt", "cash"]

MAIN_EXCHANGES = {
    "us": {"NYSE", "NasdaqGS", "NasdaqGM", "NasdaqCM", "NYSE American", "NYSEArca", "Cboe US"},
    "gb": {"LSE", "Aquis AQSE"},
    "ca": {"Toronto", "TSXV", "Canadian Sec", "Cboe CA"},
    "jp": {"Tokyo"}, "kr": {"KSE", "KOSDAQ"}, "tw": {"Taiwan", "Taipei Exchange"},
    "de": {"XETRA", "Frankfurt"}, "es": {"MCE", "Madrid"}, "it": {"Milan"},
    "nl": {"Amsterdam"}, "dk": {"Copenhagen"}, "se": {"Stockholm"},
}


def main_listing_flags(rows):
    """Choose home-market counterparts globally, before user filters are applied."""
    from listing_types import catalogue, security_reason
    security_types = catalogue()
    flags, groups = {}, {}
    for row in rows:
        symbol, region = row["symbol"], row.get("region_code")
        reason = None
        if row.get("instrument") != "stock" or not row.get("active"):
            reason = "Not an active stock listing"
        elif re.search(r"\b(ETF|ETN|ETP|ETC|[2-5](?:\.\d+)?x|daily leveraged|daily short)\b|\bLeverage Shares\b", (row.get("name") or ""), re.I):
            reason = "Exchange-traded or leveraged product mislabelled by Yahoo"
        elif row.get("exchange") not in MAIN_EXCHANGES.get(region, set()):
            reason = "OTC, international order book or secondary trading venue"
        elif classified_reason := security_reason(row, security_types):
            reason = classified_reason
        elif re.search(r"\b(CDR|GDR|CAD\s+HE|depositary receipt|depository receipt)", (row.get("name") or ""), re.I):
            reason = "Depositary receipt or CAD-hedged wrapper"
        elif re.search(r"\b(?:preferred (?:stock|equity|shares?|securities)|property preferred|depositary shares?|depository shares?|American depositary|American depository|ADR|ADS|warrants?|subscription rights?)\b", (row.get("name") or ""), re.I) or re.search(r"-(?:P[A-Z]|PR(?:[.-][A-Z])?)\.(?:TO|NE)$", symbol) or (region == "tw" and re.fullmatch(r"\d{4}[A-C]\.TW", symbol)) or (region == "kr" and re.fullmatch(r"\d{5}[5-9]\.KS", symbol)):
            reason = "Preferred security or depositary instrument"
        elif re.search(r"\bphysical (?:gold|silver|platinum|palladium|uranium)\b|\binvestment trust\b|\b(?:invmt|invt) tr\b|\bsplit corp\b|\b(?:income|investment|bond|equity|mutual|closed.end).*\bfund\b", (row.get("name") or ""), re.I) or (row.get('industry') == 'Asset Management' and re.search(r"\b(?:trust|fund)\b", (row.get('name') or ''), re.I)):
            reason = "Investment fund or commodity trust"
        elif region == 'gb' and re.search(r'\btrust\s+(?:plc|limited|ltd)\b', (row.get('name') or ''), re.I) and not re.search(r'\bREIT\b', (row.get('name') or ''), re.I) and not (row.get('industry') or '').startswith('REIT'):
            reason = "UK listed investment trust"
        elif region == "gb" and re.fullmatch(r"0[A-Z0-9]+\.L", symbol):
            reason = "London international or secondary quote"
        elif region == "it" and re.match(r"^1[A-Z].*\.MI$", symbol):
            reason = "Italian quote of a foreign equity"
        flags[symbol] = dict(main_listing=False, listing_reason=reason)
        if reason:
            continue
        # Keep legal names and share-class labels. Avoid merging unrelated issuers.
        name = re.sub(r"[^\w]", "", (row.get("name") or symbol).casefold()) or symbol
        groups.setdefault(name, []).append(row)

    country_codes = {name: code for code, name in REGIONS.items()}
    for candidates in groups.values():
        homes = [country_codes.get(r.get("domicile")) for r in candidates]
        homes = [home for home in homes if home]
        home = max(set(homes), key=lambda h: (homes.count(h), h)) if homes else None
        preferred = [r for r in candidates if r["region_code"] == home] or candidates

        def liquidity(row):
            volume = number(row.get("avg_volume")) or number(row.get("volume")) or 0
            return volume * (number(row.get("price")) or 0)

        representative = max(preferred, key=lambda r: (liquidity(r), r["symbol"]))
        region = representative["region_code"]
        # Retain different share classes in the selected market. For a repeated
        # ticker on Canadian exchanges, prefer TSX over its Cboe counterpart.
        selected = [r for r in candidates if r["region_code"] == region]
        if region == 'de' and any(r.get('exchange') == 'XETRA' for r in selected):
            selected = [r for r in selected if r.get('exchange') == 'XETRA']
        bases = {}
        for row in selected:
            base = row["symbol"].rsplit(".", 1)[0] if region == "ca" else row["symbol"]
            bases.setdefault(base, []).append(row)
        kept = set()
        for same_ticker in bases.values():
            best = max(same_ticker, key=lambda r: (r.get("exchange") == "Toronto", liquidity(r), r["symbol"]))
            kept.add(best["symbol"])
        representative = max((r for r in selected if r['symbol'] in kept), key=lambda r: (liquidity(r), r['symbol']))
        for row in candidates:
            main = row["symbol"] in kept
            flags[row["symbol"]] = dict(main_listing=main,
                listing_reason="Main exchange listing" if main else "Alternative listing of " + representative["symbol"])
    return flags


def company_domain(website):
    if not isinstance(website, str) or not website:
        return None
    try:
        host = urlparse(website if "://" in website else "https://" + website).hostname
        host = host.lower().rstrip(".").encode("idna").decode() if host else ""
        if host.startswith("www."):
            host = host[4:]
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host:
            return None
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split(".")):
            return None
        if host.endswith((".local", ".localhost", ".invalid")):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            return host
    except (ValueError, UnicodeError):
        return None


def icon_extension(content):
    if not content or len(content) > 1_000_000:
        raise ValueError("Icon file is empty or too large")
    with Image.open(io.BytesIO(content)) as picture:
        if not (1 <= picture.width <= 512 and 1 <= picture.height <= 512):
            raise ValueError("Icon dimensions are out of range")
        extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "ICO": "ico"}.get(picture.format)
        if not extension:
            raise ValueError("Unsupported icon format")
        picture.verify()
    return extension


class LogoCache:
    """Cache real website icons once per public company domain."""
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root) if root else store.path.parent / "logos"
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue = queue.PriorityQueue()
        self.pending, self.inflight = {}, set()
        self.lock = threading.Lock()
        self.sequence, self.last_seed = 0, 0
        with store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS logo_assets (
                domain TEXT PRIMARY KEY, filename TEXT, source_url TEXT,
                fetched TEXT, attempted REAL NOT NULL DEFAULT 0, error TEXT)""")

    def asset(self, domain):
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM logo_assets WHERE domain=?", (domain,)).fetchone()
        return dict(row) if row else None

    def enqueue(self, website, priority=False):
        domain = company_domain(website)
        if not domain:
            return
        asset = self.asset(domain)
        if asset and time.time() - asset["attempted"] < (30 * 86400 if asset["filename"] else 86400):
            if not asset["filename"] or (self.root / asset["filename"]).is_file():
                return
        rank = 0 if priority else 1
        with self.lock:
            if domain in self.inflight or self.pending.get(domain, 2) <= rank:
                return
            self.pending[domain] = rank
            self.sequence += 1
            self.queue.put((rank, self.sequence, domain))

    def decorate(self, row):
        row = dict(row)
        domain = company_domain(row.get("website"))
        asset = self.asset(domain) if domain else None
        row["logo_url"] = "/logos/" + asset["filename"] if asset and asset["filename"] and (self.root / asset["filename"]).is_file() else None
        self.enqueue(row.get("website"), priority=True)
        return row

    def fetch(self, domain):
        # The request destination is fixed. A source website cannot redirect this
        # server into a private network or choose a local cache path.
        url = "https://www.google.com/s2/favicons"
        response = requests.get(url, params={"domain": domain, "sz": 128}, timeout=(3, 8), stream=True)
        with response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > 1_000_000:
                    raise ValueError("Icon response is too large")
                chunks.append(chunk)
            content = b"".join(chunks)
            extension = icon_extension(content)
            filename = hashlib.sha256(domain.encode()).hexdigest() + "." + extension
            destination = self.root / filename
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
            source = response.url
        with self.store.connect() as conn:
            conn.execute("""INSERT INTO logo_assets VALUES (?,?,?,?,?,NULL)
                ON CONFLICT(domain) DO UPDATE SET filename=excluded.filename,
                source_url=excluded.source_url,fetched=excluded.fetched,attempted=excluded.attempted,error=NULL""",
                (domain, filename, source, now_iso(), time.time()))

    def seed(self):
        with self.store.connect() as conn:
            websites = conn.execute("""SELECT json_extract(data,'$.website') FROM stocks
                WHERE json_extract(data,'$.website') IS NOT NULL AND json_extract(data,'$.active')=1
                ORDER BY (json_extract(data,'$.main_listing')=1) DESC,json_extract(data,'$.market_cap') DESC""").fetchall()
        for website in dict.fromkeys(r[0] for r in websites):
            self.enqueue(website)

    def loop(self, stop):
        while not stop.is_set():
            with self.lock:
                seed = time.time() - self.last_seed > 60
                if seed:
                    self.last_seed = time.time()
            if seed:
                self.seed()
            try:
                rank, _, domain = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            with self.lock:
                if self.pending.get(domain) != rank or domain in self.inflight:
                    continue
                self.pending.pop(domain)
                self.inflight.add(domain)
            try:
                self.fetch(domain)
            except Exception as exc:
                with self.store.connect() as conn:
                    conn.execute("""INSERT INTO logo_assets(domain,attempted,error) VALUES(?,?,?)
                        ON CONFLICT(domain) DO UPDATE SET attempted=excluded.attempted,error=excluded.error""",
                        (domain, time.time(), str(exc)[:250]))
                if "429" in str(exc):
                    stop.wait(30)
            finally:
                with self.lock:
                    self.inflight.discard(domain)
            stop.wait(.1)

    def status(self):
        with self.store.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cached FROM logo_assets WHERE filename IS NOT NULL").fetchone()
        with self.lock:
            return dict(cached=row[0], queued=len(self.pending), downloading=len(self.inflight))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def base_currency(currency):
    return {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}.get(currency, currency)


def usd(value, currency, fx, price=False):
    value = number(value)
    rate = number(fx.get(base_currency(currency), {}).get("rate"))
    if value is None or rate is None:
        return None
    unit = 0.01 if price and currency in {"GBp", "GBX", "ZAc", "ILA"} else 1
    return value * unit * rate


def dollarise(row, fx):
    row = dict(row)
    currency = row.get("quote_currency")
    for key in ["price", "low_52w", "high_52w"]:
        row[key] = usd(row.get(key + "_local"), currency, fx, price=True)
    row["market_cap"] = usd(row.get("market_cap_local"), currency, fx)
    conflict = row.get("annual_income_history", {}).get("currency")
    conflict = conflict and row.get("financial_currency") and conflict != row["financial_currency"] and row.get("financial_currency_version", 0) < 2
    if conflict:
        row["financial_quality_note"] = "Yahoo profile and annual statement currencies conflict. Dollar financials are withheld pending verification."
        row["statement_version"] = 0
        row["roic_proxy"] = None
        row["roic_proxy_reason"] = "Statement currency conflict requires verification"
        for statement in row.get("statement_history", {}).values():
            statement["currency"] = None
            statement["status"] = "currency_conflict"
    for key in MONETARY_FINANCIAL:
        currency = row.get("financial_field_currencies", {}).get(key, row.get("financial_currency"))
        row[key] = None if conflict else usd(row.get(key + "_local"), currency, fx)
    for key in ["sma_200d", "sma_200w"]:
        row[key] = usd(row.get(key+"_local"),row.get("technical_currency"),fx,price=True)
    annual = row.get("annual_income_history", {})
    if row.get("annual_growth_version") and "annual_income_history" in row and not annual.get("revenue") and not annual.get("net_income") and row.get("annual_growth_status") != "no_data":
        # yfinance profile requests can silently yield empty frames. They do not prove no source history exists.
        row["annual_growth_version"] = 0
    cap, fcf = row.get("market_cap"), row.get("fcf")
    row["fcf_yield"] = fcf / cap * 100 if cap and cap > 0 and fcf is not None else None
    price, high = number(row.get("price_local")), number(row.get("high_52w_local"))
    row["below_52w_high"] = (1 - price / high) * 100 if high is not None and high > 0 and price is not None and price >= 0 else None
    return row


def quote_row(quote, region):
    symbol = quote["symbol"]
    # Yahoo labels many Taiwan warrants EQUITY. Preserve them but separate them.
    instrument = "stock" if quote.get("quoteType") == "EQUITY" else "other"
    if region == "tw" and not re.fullmatch(r"\d{4}\.(TW|TWO)", symbol) and not (number(quote.get("marketCap")) or 0) > 0:
        instrument = "other"
    if re.search(r"\b(warrant|rights)\b", quote.get("longName", quote.get("shortName", "")), re.I):
        instrument = "other"
    timestamp = quote.get("regularMarketTime")
    return {
        "symbol": symbol, "name": quote.get("longName") or quote.get("shortName") or symbol,
        "region": REGIONS[region], "region_code": region, "instrument": instrument,
        "quote_type": quote.get("quoteType"),
        "exchange": quote.get("fullExchangeName") or quote.get("exchange"),
        "quote_currency": quote.get("currency"), "financial_currency": quote.get("financialCurrency"),
        "price_local": number(quote.get("regularMarketPrice")), "market_cap_local": number(quote.get("marketCap")),
        "volume": number(quote.get("regularMarketVolume")), "avg_volume": number(quote.get("averageDailyVolume3Month")),
        "change": number(quote.get("regularMarketChangePercent")), "div_yield": number(quote.get("dividendYield")),
        "pe": number(quote.get("trailingPE")), "forward_pe": number(quote.get("forwardPE")),
        "price_book": number(quote.get("priceToBook")),
        "low_52w_local": number(quote.get("fiftyTwoWeekLow")), "high_52w_local": number(quote.get("fiftyTwoWeekHigh")),
        "change_52w": number(quote.get("fiftyTwoWeekChangePercent")),
        "quote_time": datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else None,
        "quote_timestamp": timestamp, "quote_fetched": now_iso(), "delay": quote.get("exchangeDataDelayedBy"),
        "market_state": quote.get("marketState"), "source": "Yahoo Finance", "active": True,
    }


def statement_series(frame, names):
    for name in names:
        if name in frame.index:
            series = pd.to_numeric(frame.loc[name], errors="coerce").dropna().sort_index(ascending=False)
            if not series.empty:
                return series
    return pd.Series(dtype=float)


def four_quarters(series):
    if len(series) < 4:
        return None
    dates = list(series.index[:4])
    gaps = [(dates[i] - dates[i + 1]).days for i in range(3)]
    if not all(65 <= gap <= 115 for gap in gaps):
        return None
    return number(series.iloc[:4].sum()), "TTM " + dates[0].date().isoformat()


def annual_value(series):
    return (number(series.iloc[0]), "FY " + series.index[0].date().isoformat()) if len(series) else (None, None)


def parse_annual_records(payload):
    """Keep full fiscal years in one currency directly from Yahoo records."""
    body = payload.get("timeseries")
    if not isinstance(body, dict):
        raise ValueError("Yahoo returned an unexpected annual statement response")
    if body.get("error") and body["error"].get("code") not in {"Not Found", "NotFound"}:
        raise ValueError("Yahoo annual statement request failed")
    names = {"annualTotalRevenue": "Total Revenue", "annualNetIncome": "Net Income",
             "annualNetIncomeCommonStockholders": "Net Income Common Stockholders"}
    records = []
    for group in body.get("result") or []:
        for key, label in names.items():
            for item in group.get(key, []):
                try:
                    day = date.fromisoformat(item["asOfDate"]).isoformat()
                except (KeyError, ValueError):
                    continue
                value = number(item.get("reportedValue", {}).get("raw"))
                if item.get("periodType") == "12M" and item.get("currencyCode") and value is not None:
                    records.append((day, label, item["currencyCode"], value))
    if not records:
        return pd.DataFrame(), None
    # Prefer the latest revenue currency. Never combine changing currencies.
    revenue = [r for r in records if r[1] == "Total Revenue"]
    currency = max(revenue or records, key=lambda r: r[0])[2]
    series = {}
    for day, label, unit, value in records:
        if unit == currency:
            series.setdefault(label, {})[pd.Timestamp(day)] = value
    return pd.DataFrame.from_dict(series, orient="index"), currency


def fetch_annual_income(symbol):
    end = int(pd.Timestamp.utcnow().ceil("D").timestamp())
    ticker = yf.Ticker(symbol)
    types = "annualTotalRevenue,annualNetIncome,annualNetIncomeCommonStockholders"
    url = f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}?symbol={symbol}&type={types}&period1=1483142400&period2={end}"
    response = ticker._data.cache_get(url=url, timeout=15)
    response.raise_for_status()
    return parse_annual_records(response.json())


def annual_growth_values(income, currency, previous=None):
    """Retain Yahoo FY observations, never substitute quarterly or TTM totals."""
    previous = previous or {}
    cached = previous.get("annual_income_history", {})
    if income.empty and not currency and cached.get("currency"):
        currency = cached["currency"]
    history = dict(currency=currency)
    out = dict(annual_growth_version=1, annual_growth_fetched=now_iso(), annual_growth_missing={})
    for metric, names in [("revenue", ["Total Revenue"]), ("net_income", ["Net Income", "Net Income Common Stockholders"])]:
        observations = dict(cached.get(metric, {})) if currency and cached.get("currency") == currency else {}
        observations.update({str(day.date()): number(value) for day, value in statement_series(income, names).items()})
        # Ignore malformed/non-finite cached observations rather than inventing zeroes.
        valid = {}
        for day, value in observations.items():
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and number(value) is not None:
                    valid[date.fromisoformat(day).isoformat()] = number(value)
            except ValueError:
                continue
        observations = valid
        dates = sorted(observations, reverse=True)
        history[metric] = {day: observations[day] for day in dates}
        for years in GROWTH_HORIZONS:
            key = f"{metric}_growth_{years}y"
            out[key], out[key + "_period"] = None, None
            reason = None
            if not currency:
                reason = "Reporting currency unavailable"
            elif len(dates) < years + 1:
                reason = f"Needs {years + 1} annual records. {len(dates)} available from Yahoo/cache."
            else:
                window = dates[:years + 1]
                days = [date.fromisoformat(day) for day in window]
                out[key + "_period"] = f"FY {window[0]} / {window[-1]} · {currency} · Yahoo"
                if not all(330 <= (a - b).days <= 400 for a, b in zip(days, days[1:])) or abs((days[0] - days[-1]).days - 365.25 * years) > 45:
                    reason = "Consecutive full fiscal years unavailable"
                else:
                    latest, base = observations[window[0]], observations[window[-1]]
                    if base <= 0:
                        reason = "Percentage growth requires a positive starting value"
                    elif years > 1 and latest < 0:
                        reason = "CAGR unavailable with a negative ending value"
                    else:
                        out[key] = number(((latest / base) ** (1 / years) - 1) * 100)
            if reason:
                out["annual_growth_missing"][key] = reason
    out["annual_income_history"] = history
    if not history.get("revenue") and not history.get("net_income"):
        out["annual_growth_version"] = 0
    return out


def financial_values(income_q, income_a, cf_q, cf_a):
    out = {}
    net_names = ["Net Income", "Net Income Common Stockholders"]
    ni_q = statement_series(income_q, net_names)
    rev_q = statement_series(income_q, ["Total Revenue"])
    common_q = ni_q.index.intersection(rev_q.index).sort_values(ascending=False)
    ni = four_quarters(ni_q.loc[common_q])
    rev = four_quarters(rev_q.loc[common_q])
    if ni and rev and ni[1] == rev[1]:
        net, revenue, period = ni[0], rev[0], ni[1]
    else:
        ni_a = statement_series(income_a, net_names)
        rev_a = statement_series(income_a, ["Total Revenue"])
        common_a = ni_a.index.intersection(rev_a.index).sort_values(ascending=False)
        if len(common_a):
            net, revenue, period = number(ni_a[common_a[0]]), number(rev_a[common_a[0]]), "FY " + common_a[0].date().isoformat()
        else:
            net, revenue, period = None, None, None
    out.update(net_income_local=net, revenue_local=revenue, income_period=period,
               net_margin=net / revenue * 100 if revenue and net is not None else None)

    def cash_series(frame):
        ocf = statement_series(frame, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = statement_series(frame, ["Capital Expenditure", "Capital Expenditures"]).abs()
        common = ocf.index.intersection(capex.index).sort_values(ascending=False)
        return ocf.loc[common], capex.loc[common], (ocf.loc[common] - capex.loc[common])

    oq, cq, fq = cash_series(cf_q)
    oa, ca, fa = cash_series(cf_a)
    fcf_ttm = four_quarters(fq)
    if fcf_ttm:
        out.update(fcf_local=fcf_ttm[0], capex_local=four_quarters(cq)[0],
                   operating_cf_local=four_quarters(oq)[0], cf_period=fcf_ttm[1])
    elif len(fa):
        out.update(fcf_local=number(fa.iloc[0]), capex_local=number(ca.iloc[0]),
                   operating_cf_local=number(oa.iloc[0]), cf_period="FY " + fa.index[0].date().isoformat())
    else:
        out.update(fcf_local=None, capex_local=None, operating_cf_local=None, cf_period=None)
    out.update(fcf_change=None, fcf_delta_local=None, fcf_growth_period=None)
    if len(fa) >= 2 and 300 <= (fa.index[0] - fa.index[1]).days <= 430:
        latest, previous = number(fa.iloc[0]), number(fa.iloc[1])
        out.update(fcf_delta_local=latest - previous,
                   fcf_change=(latest / previous - 1) * 100 if previous > 0 else None,
                   fcf_growth_period=f"FY {fa.index[0].date()} / {fa.index[1].date()}")
    return out


def dividend_values(dividends, today=None):
    today = today or date.today()
    end_year = today.year - 1
    if dividends.empty:
        return dict(div_years=None, div_growth=None, dividend_history_start=None, dividend_years={})
    totals = dividends.groupby(dividends.index.year).sum()
    totals = {int(y): float(v) for y, v in totals.items() if y <= end_year}
    streak = 0
    if totals.get(end_year, 0) > 0:
        year = end_year
        while totals.get(year - 1, 0) > 0 and totals[year] > totals[year - 1] * (1 + 1e-6):
            streak += 1
            year -= 1
    growth = None
    if all(totals.get(y, 0) > 0 for y in range(end_year - 5, end_year + 1)):
        growth = ((totals[end_year] / totals[end_year - 5]) ** 0.2 - 1) * 100
    return dict(div_years=streak, div_growth=growth, dividend_history_start=str(dividends.index.min().date()),
                dividend_end_year=end_year, dividend_years=totals)


class Store:
    def __init__(self, path=DB):
        self.path = Path(path)
        self.listing_lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS stocks (symbol TEXT PRIMARY KEY, region TEXT, data TEXT NOT NULL,
                    enriched REAL DEFAULT 0, attempted REAL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS stocks_region ON stocks(region);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);""")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def meta(self, key, default=None):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))

    def get(self, symbol):
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM stocks WHERE symbol=?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else None

    def upsert_many(self, rows, enriched=False):
        fx = self.meta("fx", {"USD": {"rate": 1}})
        with self.connect() as conn:
            for incoming in rows:
                old = conn.execute("SELECT data FROM stocks WHERE symbol=?", (incoming["symbol"],)).fetchone()
                row = json.loads(old[0]) if old else {}
                # A quote's reporting currency does not override a verified statement currency.
                if row.get("financial_fetched") and not enriched:
                    incoming = dict(incoming)
                    if incoming.get("financial_currency_version", 0) < 2:
                        incoming.pop("financial_currency", None)
                row.update(incoming)
                row = dollarise(row, fx)
                conn.execute("""INSERT INTO stocks(symbol,region,data,enriched,attempted) VALUES(?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET region=excluded.region,data=excluded.data,
                    enriched=CASE WHEN ? THEN excluded.enriched ELSE stocks.enriched END,
                    attempted=CASE WHEN ? THEN excluded.attempted ELSE stocks.attempted END""",
                    (row["symbol"], row["region_code"], json.dumps(row, allow_nan=False), time.time() if enriched else 0,
                     time.time() if enriched else 0, enriched, enriched))

    def recalibrate_fx(self):
        fx = self.meta("fx", {})
        with self.connect() as conn:
            rows = conn.execute("SELECT symbol,data FROM stocks").fetchall()
            conn.executemany("UPDATE stocks SET data=? WHERE symbol=?",
                             [(json.dumps(dollarise(json.loads(r["data"]), fx)), r["symbol"]) for r in rows])

    def enrichment_candidates(self):
        """Scan once, serving never-fetched main companies before weekly refreshes."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT symbol,region,enriched FROM stocks WHERE
                (enriched < ? OR COALESCE(json_extract(data,'$.annual_growth_version'),0)<1)
                AND attempted < ? AND json_extract(data,'$.instrument')='stock'
                AND json_extract(data,'$.active')=1 AND COALESCE(json_extract(data,'$.main_listing'),1)=1
                ORDER BY (enriched=0) DESC, json_extract(data,'$.market_cap') DESC""",
                (time.time()-7*86400, time.time()-6*3600)).fetchall()
        result = []
        for missing in (True, False):
            buckets = {region: [] for region in REGIONS}
            for row in rows:
                if (row['enriched'] == 0) == missing and row['region'] in buckets:
                    buckets[row['region']].append(row['symbol'])
            result.extend(buckets[region][i]
                for i in range(max((len(v) for v in buckets.values()), default=0))
                for region in REGIONS if i < len(buckets[region]))
        return result

    def claim_enrichment(self, symbol):
        with self.connect() as conn:
            cursor = conn.execute("""UPDATE stocks SET attempted=? WHERE symbol=?
                AND (enriched < ? OR COALESCE(json_extract(data,'$.annual_growth_version'),0)<1)
                AND attempted < ? AND json_extract(data,'$.instrument')='stock'
                AND json_extract(data,'$.active')=1 AND COALESCE(json_extract(data,'$.main_listing'),1)=1""",
                (time.time(), symbol, time.time()-7*86400, time.time()-6*3600))
        return cursor.rowcount == 1

    def next_enrichment(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT symbol FROM stocks WHERE
                (enriched < ? OR COALESCE(json_extract(data,'$.annual_growth_version'),0)<1) AND attempted < ?
                AND json_extract(data,'$.instrument')='stock' AND json_extract(data,'$.active')=1
                ORDER BY (json_extract(data,'$.main_listing')=1) DESC,
                (enriched=0) DESC, json_extract(data,'$.market_cap') DESC LIMIT 1""",
                (time.time() - 7 * 86400, time.time() - 6 * 3600)).fetchone()
            if row:
                conn.execute("UPDATE stocks SET attempted=? WHERE symbol=?", (time.time(), row[0]))
        return row[0] if row else None

    def failed_enrichment(self, symbol, error):
        with self.connect() as conn:
            conn.execute("UPDATE stocks SET attempted=?, data=json_set(data,'$.financial_error',?) WHERE symbol=?",
                         (time.time(), str(error)[:250], symbol))

    def growth_candidates(self):
        """Scan once per batch, then alternate markets in market-cap order."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT symbol,region FROM stocks
                WHERE (COALESCE(json_extract(data,'$.annual_growth_version'),0)<1 OR COALESCE(json_extract(data,'$.annual_growth_fetched'),'') < ?)
                AND COALESCE(json_extract(data,'$.annual_growth_attempted'),0) < ?
                AND json_extract(data,'$.instrument')='stock' AND json_extract(data,'$.active')=1
                AND COALESCE(json_extract(data,'$.main_listing'),1)=1
                ORDER BY json_extract(data,'$.market_cap') DESC""", (datetime.fromtimestamp(time.time()-7*86400,timezone.utc).isoformat(), time.time() - 6 * 3600,)).fetchall()
        buckets = {region: [] for region in REGIONS}
        for row in rows:
            buckets[row['region']].append(row['symbol'])
        return [buckets[region][i] for i in range(max((len(v) for v in buckets.values()), default=0))
                for region in REGIONS if i < len(buckets[region])]

    def claim_growth(self, symbol):
        with self.connect() as conn:
            cursor = conn.execute("""UPDATE stocks SET data=json_set(data,'$.annual_growth_attempted',?)
                WHERE symbol=? AND (COALESCE(json_extract(data,'$.annual_growth_version'),0)<1 OR COALESCE(json_extract(data,'$.annual_growth_fetched'),'') < ?)
                AND COALESCE(json_extract(data,'$.annual_growth_attempted'),0)<?""",
                (time.time(), symbol, datetime.fromtimestamp(time.time()-7*86400,timezone.utc).isoformat(), time.time() - 6 * 3600))
        return cursor.rowcount == 1

    def next_growth_enrichment(self):
        for symbol in self.growth_candidates():
            if self.claim_growth(symbol):
                return symbol
        return None

    def classify_listings(self, force=False):
        # Refresh the classification as home-country profiles become available.
        with self.listing_lock:
            if not force and self.meta("listing_policy_version", 0) == 10 and time.time() - self.meta("listing_classified", 0) < 300:
                return
            with self.connect() as conn:
                keys = ['symbol','name','region_code','exchange','domicile','instrument','active','price','volume','avg_volume','main_listing','listing_reason','industry']
                projection = "json_object("+','.join(f"'{key}',json_extract(data,'$.{key}')" for key in keys)+")"
                rows = [json.loads(r[0]) for r in conn.execute("SELECT "+projection+" FROM stocks")]
                flags = main_listing_flags(rows)
                changes = [(json.dumps(flags[r['symbol']]['main_listing']),flags[r['symbol']]['listing_reason'],r['symbol']) for r in rows
                    if r.get('main_listing') != flags[r['symbol']]['main_listing'] or r.get('listing_reason') != flags[r['symbol']]['listing_reason']]
                conn.executemany("UPDATE stocks SET data=json_set(data,'$.main_listing',json(?),'$.listing_reason',?) WHERE symbol=?", changes)
            self.set_meta("listing_classified", time.time())
            self.set_meta("listing_policy_version", 10)


def query_sql(search="", regions="", filters="[]", sort="market_cap", direction="desc", include_other=False, only_symbols="", main_only=False):
    if sort not in FIELDS:
        raise ValueError("Unknown sort column")
    if direction not in {"asc", "desc"}:
        raise ValueError("Unknown sort direction")
    where, args = ["json_extract(data,'$.active')=1"], []
    if not include_other:
        where.append("json_extract(data,'$.instrument')='stock'")
    if main_only:
        where.append("json_extract(data,'$.main_listing')=1")
    if search:
        where.append("(symbol LIKE ? OR json_extract(data,'$.name') LIKE ?)")
        args.extend([f"%{search}%"] * 2)
    if regions:
        codes = regions.split(",")
        if not all(r in REGIONS for r in codes):
            raise ValueError("Unknown market")
        where.append("region IN (" + ",".join("?" for _ in codes) + ")")
        args.extend(codes)
    if only_symbols:
        syms = only_symbols.split(",")[:1000]
        where.append("symbol IN (" + ",".join("?" for _ in syms) + ")")
        args.extend(syms)
    rules = json.loads(filters)
    if not isinstance(rules, list) or len(rules) > 40:
        raise ValueError("Use at most 40 filters")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Each filter must be an object")
        key, op = rule.get("field"), rule.get("op")
        if key not in FIELDS:
            raise ValueError("Unknown filter column")
        expr = f"json_extract(data,'$.{key}')"
        if op in {"missing", "present"}:
            where.append(expr + (" IS NULL" if op == "missing" else " IS NOT NULL"))
        elif FIELDS[key]["kind"] == "text" and op in {"eq", "ne", "contains", "not_contains"}:
            value = str(rule.get("value", ""))
            operation = {"eq": "=", "ne": "!=", "contains": "LIKE", "not_contains": "NOT LIKE"}[op]
            where.append(expr + f" {operation} ? COLLATE NOCASE")
            args.append(f"%{value}%" if "contains" in op else value)
        elif FIELDS[key]["kind"] != "text" and op in {"eq", "ne", "gt", "gte", "lt", "lte", "between"}:
            value = number(rule.get("value"))
            if value is None:
                raise ValueError("Enter a valid number")
            if op == "between":
                value2 = number(rule.get("value2"))
                if value2 is None or value2 < value:
                    raise ValueError("The range end must be at least the range start")
                where.append(expr + " BETWEEN ? AND ?")
                args.extend([value, value2])
            else:
                where.append(expr + " " + {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op] + " ?")
                args.append(value)
        else:
            raise ValueError("Unsupported filter operation")
    expr = f"json_extract(data,'$.{sort}')"
    collate = " COLLATE NOCASE" if FIELDS[sort]["kind"] == "text" else ""
    return " AND ".join(where), args, f"({expr} IS NULL) ASC, {expr}{collate} {direction.upper()}, symbol ASC"


class Pipeline:
    def __init__(self, store, logos=None):
        self.store = store
        self.logos = logos
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.priority = queue.Queue()
        self.pending = set()
        self.pending_lock = threading.Lock()
        self.last_activity = time.time()

    def start(self):
        self.store.classify_listings(force=True)
        for target in [self.quote_loop, self.financial_loop, self.financial_loop]:
            threading.Thread(target=target, daemon=True).start()
        if self.logos:
            for _ in range(2):
                threading.Thread(target=self.logos.loop, args=(self.stop,), daemon=True).start()

    def enqueue(self, symbols):
        with self.pending_lock:
            for symbol in symbols:
                if symbol not in self.pending and self.store.get(symbol):
                    self.pending.add(symbol)
                    self.priority.put(symbol)

    def load_fx(self):
        fx = self.store.meta("fx", {"USD": {"rate": 1, "date": now_iso()}})
        currencies = {"GBP", "CAD", "JPY", "KRW", "TWD", "EUR", "DKK", "SEK", "HKD", "CNY", "CHF", "AUD"}
        with self.store.connect() as conn:
            currencies.update(base_currency(r[0]) for r in conn.execute("SELECT DISTINCT json_extract(data,'$.quote_currency') FROM stocks") if r[0])
            currencies.update(base_currency(r[0]) for r in conn.execute("SELECT DISTINCT json_extract(data,'$.financial_currency') FROM stocks") if r[0])
        currencies.discard("USD")
        for currency in currencies:
            if self.stop.is_set():
                break
            try:
                hist = yf.Ticker(currency + "USD=X").history(period="5d")
                if hist.empty:
                    raise ValueError("No FX quote")
                rate = number(hist["Close"].iloc[-1])
                if not rate or rate <= 0:
                    raise ValueError("Invalid FX quote")
                fx[currency] = dict(rate=rate, date=str(hist.index[-1].date()), fetched=now_iso())
            except Exception as exc:
                LOG.warning("FX %s: %s", currency, exc)
        fx["USD"] = dict(rate=1, date=date.today().isoformat(), fetched=now_iso())
        self.store.set_meta("fx", fx)
        self.store.recalibrate_fx()

    def quote_loop(self):
        while not self.stop.is_set():
            last = self.store.meta("last_quote_run", 0)
            if self.refresh.is_set() or time.time() - last > 86400:
                self.refresh.clear()
                self.store.set_meta("refreshing", True)
                try:
                    self.load_fx()
                    self.ingest()
                    self.store.classify_listings(force=True)
                except Exception as exc:
                    self.store.set_meta("quote_error", str(exc)[:300])
                    LOG.exception("Quote refresh failed")
                finally:
                    self.store.set_meta("refreshing", False)
                    self.store.set_meta("last_quote_run", time.time())
            self.stop.wait(5)

    def ingest(self, regions=None):
        run_id = now_iso()
        regions = list(REGIONS) if regions is None else list(regions)
        if not regions or any(region not in REGIONS for region in regions):
            raise ValueError("Choose configured listing markets")
        coverage = self.store.meta("coverage", {})
        current = {}
        self.store.set_meta("quote_error", None)
        # Seed the first page in every market before filling the entire universe.
        for region in regions:
            if self.stop.is_set():
                return
            coverage[region] = self.page(region, 0, run_id, sort="intradaymarketcap")
            coverage[region]["loaded"] = 0
            current[region] = coverage[region]
            self.store.set_meta("coverage", coverage)
        for region, progress in current.items():
            if self.stop.is_set():
                return
            if progress.get("error"):
                continue
            try:
                self.partition(region, run_id, progress, coverage, expected=progress["expected"])
                # Catch listings moving across a partition boundary during a live scan.
                for pivot in sorted(set(progress.get("boundaries", []))):
                    self.partition(region, run_id, progress, coverage, pivot * .8, pivot * 1.2)
            except Exception as exc:
                progress["error"] = str(exc)[:250]
            if not progress.get("error"):
                latest = yf.screen(region_query(region), size=1)
                progress["expected"] = latest.get("total", progress["expected"])
                with self.store.connect() as conn:
                    conn.execute("UPDATE stocks SET data=json_set(data,'$.active',0) WHERE region=? AND json_extract(data,'$.universe_run')!=?", (region, run_id))
                    progress["stored"] = conn.execute("SELECT COUNT(*) FROM stocks WHERE region=? AND json_extract(data,'$.active')=1", (region,)).fetchone()[0]
                progress["complete"] = progress["stored"] == progress["expected"]
                progress["difference"] = progress["stored"] - progress["expected"]
                progress["finished"] = True
                if region in EUROPE_REGIONS:
                    progress["scope"] = "Yahoo regional equities with positive market cap"
            self.store.set_meta("coverage", coverage)
        self.load_fx()
        self.store.set_meta("quote_completed", now_iso())

    def partition(self, region, run_id, progress, coverage, lower=None, upper=None, expected=None, depth=0):
        """Yahoo silently clamps offsets near 10,000. Disjoint price ranges avoid it."""
        if self.stop.is_set():
            return
        if depth > 12:
            raise ValueError("A price partition still exceeds Yahoo's pagination limit")
        conditions = [region_query(region)]
        if lower is not None:
            conditions.append(yf.EquityQuery("gt", ["intradayprice", lower]))
        if upper is not None:
            conditions.append(yf.EquityQuery("lte", ["intradayprice", upper]))
        query = conditions[0] if len(conditions) == 1 else yf.EquityQuery("and", conditions)
        first = self.page(region, 0, run_id, query=query)
        if first.get("error"):
            raise ValueError(first["error"])
        expected = first["expected"]
        if expected > 9500:
            pivot = (lower * 10 if lower else 1) if upper is None else (math.sqrt(lower * upper) if lower else upper / 10)
            progress.setdefault("boundaries", []).append(pivot)
            self.partition(region, run_id, progress, coverage, lower, pivot, depth=depth + 1)
            self.partition(region, run_id, progress, coverage, pivot, upper, depth=depth + 1)
            return
        progress["loaded"] += first["loaded"]
        for offset in range(250, expected, 250):
            if self.stop.is_set():
                return
            result = self.page(region, offset, run_id, query=query)
            if result.get("error"):
                raise ValueError(result["error"])
            progress["loaded"] += result["loaded"]
            self.store.set_meta("coverage", coverage)
            self.stop.wait(0.12)
        self.store.set_meta("coverage", coverage)

    def page(self, region, offset, run_id, sort="ticker", query=None):
        last_error = "Unknown Yahoo error"
        for attempt in range(3):
            try:
                data = yf.screen(query or region_query(region), size=250, offset=offset,
                                 sortField=sort, sortAsc=sort == "ticker")
                quotes = data.get("quotes", [])
                if not quotes and data.get("total", 0) > offset:
                    raise ValueError("Yahoo returned an empty page before the end")
                rows = [dict(quote_row(q, region), universe_run=run_id) for q in quotes]
                self.store.upsert_many(rows)
                return dict(expected=data.get("total", 0), loaded=len(rows), fetched=now_iso(), complete=False)
            except Exception as exc:
                last_error = str(exc)[:250]
                LOG.warning("%s page %s: %s", region, offset, exc)
                if attempt < 2:
                    self.stop.wait(2 ** (attempt + 1))
        return dict(expected=0, loaded=0, error=last_error, complete=False)

    def enrich(self, symbol):
        ticker = yf.Ticker(symbol)
        info = ticker.get_info()
        if not info or not info.get("symbol"):
            raise ValueError("Yahoo did not return company details")
        row = self.store.get(symbol)
        from statements import capture_statements
        from currency_validation import verify_currencies, verified_totals
        frames = {kind+'_'+freq: getattr(ticker, method)(pretty=True,freq='yearly' if freq=='annual' else 'quarterly')
                  for kind,method in [('income','get_income_stmt'),('balance','get_balance_sheet'),('cashflow','get_cashflow')]
                  for freq in ['annual','quarterly']}
        currencies = verify_currencies(ticker, frames)
        values = verified_totals(frames,currencies,financial_values,row)
        statement_values, _ = capture_statements(ticker,currencies.get('income_annual'),row,frames,currencies)
        values.update(statement_values)
        annual_income = frames['income_annual'] if currencies.get('income_annual') else pd.DataFrame()
        values.update(annual_growth_values(annual_income,currencies.get('income_annual'),row))
        try:
            # Explicit start avoids invalid 'max' ranges on some secondary listings.
            history = ticker.history(start="1900-01-01", auto_adjust=False, actions=True, raise_errors=True)
            from technicals import technical_values
            values.update(technical_values(history,ticker.get_history_metadata().get("currency")))
            dividends = history["Dividends"][history["Dividends"] != 0] if "Dividends" in history else pd.Series(dtype=float)
            values.update(dividend_values(dividends))
            values["dividend_events"] = [dict(date=str(d.date()), amount=number(amount)) for d, amount in dividends.items()]
            values["dividend_currency"] = info.get("currency")
            values["dividend_fetched"] = now_iso()
            values["dividend_error"] = None
        except Exception as exc:
            values["dividend_error"] = str(exc)[:250]
        values.update(symbol=symbol, region_code=row["region_code"], sector=info.get("sector"), industry=info.get("industry"),
                      beta=number(info.get("beta")),
                      roe=number(info.get("returnOnEquity")) * 100 if number(info.get("returnOnEquity")) is not None else None,
                      description=info.get("longBusinessSummary"), website=info.get("website"),
                      domicile=info.get("country"), financial_fetched=now_iso(), financial_error=None)
        self.store.upsert_many([values], enriched=True)
        if self.logos:
            self.logos.enqueue(values.get("website"))

    def financial_loop(self):
        while not self.stop.is_set():
            try:
                symbol = self.priority.get_nowait()
                with self.pending_lock:
                    self.pending.discard(symbol)
            except queue.Empty:
                symbol = self.store.next_enrichment()
            if not symbol:
                self.stop.wait(2)
                continue
            try:
                self.enrich(symbol)
                self.store.set_meta("last_financial", dict(symbol=symbol, time=now_iso()))
            except Exception as exc:
                LOG.warning("Financials %s: %s", symbol, exc)
                self.store.failed_enrichment(symbol, exc)
                if "rate" in str(exc).lower() or "429" in str(exc):
                    self.stop.wait(60)
            self.stop.wait(0.3)


store = Store()
logos = LogoCache(store)
pipeline = Pipeline(store, logos)


@asynccontextmanager
async def lifespan(app):
    pipeline.start()
    yield
    pipeline.stop.set()


app = FastAPI(title="Atlas stock screener", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/logos", StaticFiles(directory=logos.root), name="logos")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/schema")
def schema():
    return dict(columns=COLUMNS, regions=REGIONS)


@app.get("/api/status")
def status():
    store.classify_listings()
    with store.connect() as conn:
        counts = conn.execute("""SELECT region,COUNT(*) AS total,
            SUM(json_extract(data,'$.instrument')='stock') AS stocks,
            SUM(json_extract(data,'$.main_listing')=1) AS main_stocks,
            SUM(enriched>0 AND json_extract(data,'$.main_listing')=1) AS main_enriched,
            SUM(enriched>0) AS enriched,
            SUM(json_extract(data,'$.fcf') IS NOT NULL) AS fcf
            FROM stocks WHERE json_extract(data,'$.active')=1 GROUP BY region""").fetchall()
        fx_missing = conn.execute("SELECT COUNT(*) FROM stocks WHERE json_extract(data,'$.market_cap_local') IS NOT NULL AND json_extract(data,'$.market_cap') IS NULL").fetchone()[0]
    return dict(counts=[dict(r) for r in counts], coverage=store.meta("coverage", {}), fx=store.meta("fx", {}),
                refreshing=store.meta("refreshing", False), completed=store.meta("quote_completed"),
                error=store.meta("quote_error"), last_financial=store.meta("last_financial"), missing_fx=fx_missing,
                logos=logos.status())


def select_rows(search, regions, filters, sort, direction, include_other, only_symbols, limit, offset, main_only=False):
    if main_only:
        store.classify_listings()
    try:
        where, args, order = query_sql(search, regions, filters, sort, direction, include_other, only_symbols, main_only)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with store.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM stocks WHERE " + where, args).fetchone()[0]
        rows = conn.execute("SELECT data FROM stocks WHERE " + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return [json.loads(row[0]) for row in rows], total


@app.get("/api/stocks")
def stocks(search: str = "", regions: str = "", filters: str = "[]", sort: str = "market_cap", direction: str = "desc",
           include_other: bool = False, only_symbols: str = "", main_only: bool = True,
           limit: int = Query(100, ge=1, le=250), offset: int = Query(0, ge=0)):
    rows, total = select_rows(search, regions, filters, sort, direction, include_other, only_symbols, limit, offset, main_only)
    pipeline.last_activity = time.time()
    pipeline.enqueue([r["symbol"] for r in rows[:30] if not r.get("financial_fetched") and not r.get("financial_error")])
    return dict(rows=[logos.decorate(row) for row in rows], total=total, offset=offset)


@app.get("/api/stock/{symbol}")
def stock(symbol: str):
    row = store.get(symbol)
    if row is None:
        raise HTTPException(404, "Unknown symbol")
    if not row.get("financial_fetched"):
        pipeline.enqueue([symbol])
    return logos.decorate(row)


@app.get("/api/chart")
def chart(x: str = "net_income", y: str = "div_years", search: str = "", regions: str = "",
          filters: str = "[]", sort: str = "market_cap", direction: str = "desc",
          include_other: bool = False, only_symbols: str = "", main_only: bool = True):
    if any(key not in FIELDS or FIELDS[key]["kind"] == "text" for key in [x, y]):
        raise HTTPException(400, "Choose numeric chart metrics")
    if main_only:
        store.classify_listings()
    try:
        where, args, _ = query_sql(search, regions, filters, sort, direction, include_other, only_symbols, main_only)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    keys = sorted({"symbol", "name", "region", "region_code", "sector", "industry", "exchange",
                   "income_period", "cf_period", "fcf_growth_period", "financial_fetched", "quote_time", x, y,
                   "technical_asof", *[key.removesuffix("_distance") + "_period" for key in (x, y) if key in GROWTH_KEYS or key.startswith("sma_")]})
    # Project only plot fields and paired values. Do not load every dividend event or description.
    projection = "json_object(" + ",".join(f"'{key}',json_extract(data,'$.{key}')" for key in keys) + ")"
    paired = " AND ".join(f"json_type(data,'$.{key}') IN ('integer','real')" for key in {x, y})
    def pending_sql(key):
        missing = f"COALESCE(json_type(data,'$.{key}') IN ('integer','real'),0)=0"
        if key.startswith("sma_"):
            source = "COALESCE(json_extract(data,'$.technical_version'),0)<1"
        elif key in GROWTH_KEYS:
            source = "COALESCE(json_extract(data,'$.annual_growth_version'),0)<1"
        elif key == "roic_proxy":
            source = "COALESCE(json_extract(data,'$.statement_version'),0)<1"
        elif key in {"net_income", "revenue", "net_margin"}:
            source = "json_extract(data,'$.financial_fetched') IS NULL AND json_extract(data,'$.income_fetched') IS NULL"
        elif (FIELDS[key].get("group") in {"Financials", "Cash flow", "Dividends"} and key != "div_yield") or key == "beta":
            source = "json_extract(data,'$.financial_fetched') IS NULL"
        else:
            return "0"
        return f"(({missing}) AND ({source}))"
    pending = f"({pending_sql(x)} OR {pending_sql(y)})"
    with store.connect() as conn:
        total, plotted, awaiting = conn.execute(
            f"SELECT COUNT(*),COALESCE(SUM(CASE WHEN {paired} THEN 1 ELSE 0 END),0),"
            f"COALESCE(SUM(CASE WHEN {pending} THEN 1 ELSE 0 END),0) FROM stocks WHERE " + where, args).fetchone()
        records = conn.execute("SELECT " + projection + " FROM stocks WHERE " + where + " AND " + paired, args).fetchall()
    return dict(rows=[json.loads(row[0]) for row in records], total=total,
                coverage=dict(awaiting=awaiting, unavailable=total-plotted-awaiting))


@app.get("/api/technical-screen")
def technical_screen():
    # Custom SMA screening uses the same browser calculation in local and Pages modes.
    store.classify_listings()
    keys = sorted(set(["symbol","name","region_code","instrument","active","main_listing","listing_reason","annual_growth_version","income_fetched","statement_version","technical_version", *FIELDS]))
    parts = ["json_object("+','.join(f"'{k}',json_extract(data,'$.{k}')" for k in keys[i:i+32])+")" for i in range(0,len(keys),32)]
    projection = parts[0]
    for part in parts[1:]: projection = "json_patch("+projection+","+part+")"
    with store.connect() as conn:
        records = [json.loads(r[0]) for r in conn.execute("SELECT "+projection+" FROM stocks WHERE json_extract(data,'$.active')=1")]
        technicals = [dict(r) for r in conn.execute("SELECT symbol,json_extract(data,'$.technical_history') AS history,json_extract(data,'$.technical_currency') AS currency FROM stocks WHERE json_type(data,'$.technical_history')='object'")]
    payload = dict(fields=keys,rows=[[row.get(key) for key in keys] for row in records],
        technicals={r['symbol']:json.loads(r['history']) for r in technicals},fx=store.meta('fx',{}),
        currencies={r['symbol']:r['currency'] for r in technicals})
    # Packed keys and direct serialization avoid walking millions of repeated fields in FastAPI.
    return Response(json.dumps(payload,separators=(',',':'),allow_nan=False),media_type='application/json')


@app.get("/api/analysis-peers/{symbol}")
def analysis_peers(symbol: str):
    row = store.get(symbol)
    if row is None:
        raise HTTPException(404, "Unknown symbol")
    store.classify_listings()
    keys = sorted({"symbol", "name", "main_listing", "instrument", "active", "region_code", "website", "domicile", "income_fetched", "statement_version", *FIELDS})
    objects = ["json_object(" + ",".join(f"'{key}',json_extract(data,'$.{key}')" for key in keys[i:i+40]) + ")" for i in range(0,len(keys),40)]
    projection = objects[0]
    for obj in objects[1:]:
        projection = f"json_patch({projection},{obj})"
    where = "json_extract(data,'$.active')=1 AND json_extract(data,'$.instrument')='stock' AND json_extract(data,'$.main_listing')=1"
    with store.connect() as conn:
        total, classified = conn.execute("SELECT COUNT(*),SUM(json_extract(data,'$.sector') IS NOT NULL) FROM stocks WHERE " + where).fetchone()
        records = conn.execute("SELECT " + projection + " FROM stocks WHERE " + where + " AND (json_extract(data,'$.sector')=? OR json_extract(data,'$.industry')=?)", (row.get('sector'),row.get('industry'))).fetchall()
    return dict(rows=[json.loads(r[0]) for r in records],population=dict(total=total,sector_classified=classified or 0))


@app.get("/export-worker.js")
def export_worker():
    return FileResponse(ROOT / "static" / "export-worker.js", media_type="application/javascript")


@app.get("/api/dividends/{symbol}/export")
def dividend_export(symbol: str, mode: str = "annual"):
    if mode not in {"annual", "events"}:
        raise HTTPException(400, "Choose annual or events")
    row = store.get(symbol)
    if row is None:
        raise HTTPException(404, "Unknown symbol")
    currency = row.get("dividend_currency") or row.get("quote_currency")
    unit = .01 if currency in {"GBp", "GBX", "ZAc", "ILA"} else 1
    currency = base_currency(currency)
    fx = store.meta("fx", {}).get(currency, {})
    rate = fx.get("rate")
    events = [dict(date=e["date"], amount=e["amount"] * unit) for e in row.get("dividend_events", [])]
    out = io.StringIO()
    writer = csv.writer(out)
    if mode == "events":
        writer.writerow(["ex_dividend_date", "dividend_per_share_local", "currency", "dividend_per_share_usd_current_fx", "fx_asof"])
        for event in reversed(events):
            writer.writerow([event["date"], event["amount"], currency, event["amount"] * rate if rate else None, fx.get("date")])
    else:
        writer.writerow(["year", "dividend_per_share_local", "currency", "dividend_per_share_usd_current_fx", "growth_percent", "events", "period", "fx_asof"])
        if events:
            totals = {}
            for event in events:
                year = int(event["date"][:4])
                entry = totals.setdefault(year, dict(total=0, count=0))
                entry["total"] += event["amount"]
                entry["count"] += 1
            current = date.today().year
            for year in range(current, min(totals) - 1, -1):
                entry = totals.get(year, dict(total=0, count=0))
                previous = totals.get(year - 1, {}).get("total", 0)
                growth = (entry["total"] / previous - 1) * 100 if previous > 0 and year < current else None
                writer.writerow([year, entry["total"], currency, entry["total"] * rate if rate else None,
                                 growth, entry["count"], "YTD" if year == current else "calendar year", fx.get("date")])
    safe_symbol = re.sub(r"[^A-Za-z0-9._-]", "_", symbol)
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{safe_symbol}-dividends-{mode}.csv"'})


class Symbols(BaseModel):
    symbols: list[str] = Field(max_length=250)


@app.post("/api/enrich")
def enrich(body: Symbols):
    pipeline.enqueue(body.symbols)
    return dict(queued=len(body.symbols))


@app.post("/api/refresh")
def refresh():
    if not store.meta("refreshing", False):
        pipeline.refresh.set()
    return dict(queued=True)


@app.get("/api/export")
def export(search: str = "", regions: str = "", filters: str = "[]", sort: str = "market_cap", direction: str = "desc",
           include_other: bool = False, only_symbols: str = "", columns: str = "", main_only: bool = True):
    selected = columns.split(",") if columns else [x["key"] for x in COLUMNS if x["default"]]
    if not all(k in FIELDS for k in selected):
        raise HTTPException(400, "Unknown export column")
    selected = list(dict.fromkeys(["symbol", "name"] + selected + ["income_period", "cf_period", "fcf_growth_period", "quote_time", "financial_fetched"]
                                 + [key.removesuffix("_distance") + "_period" for key in selected if key in GROWTH_KEYS or key.startswith("sma_")]
                                 + (["technical_asof"] if any(key.startswith("sma_") for key in selected) else [])
                                 + (["annual_growth_fetched"] if any(key in GROWTH_KEYS for key in selected) else [])))
    rows, _ = select_rows(search, regions, filters, sort, direction, include_other, only_symbols, 100000, 0, main_only)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(selected)
    for row in rows:
        values = []
        for key in selected:
            value = row.get(key)
            # Prevent spreadsheet formula execution in source-provided labels.
            if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="atlas-stocks-usd.csv"'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
