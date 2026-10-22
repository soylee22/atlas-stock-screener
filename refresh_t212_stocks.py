"""Fast parallel technical and dividend refresher for the Trading 212 stock universe via Yahoo Chart API."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import gzip
import json
import logging
from pathlib import Path
import time
import urllib.request

import app as model

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "seed" / "stocks-2026-10-23.csv"
CHECKPOINT_PATH = ROOT / "seed" / "stock-history-checkpoint.json.gz"


def load_checkpoint() -> dict[str, dict]:
    if CHECKPOINT_PATH.is_file():
        try:
            return json.loads(gzip.decompress(CHECKPOINT_PATH.read_bytes()).decode("utf-8"))
        except Exception as e:
            logging.warning("Failed loading checkpoint: %s", e)
    return {}


def save_checkpoint(data: dict[str, dict]):
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    CHECKPOINT_PATH.write_bytes(compressed)


def fetch_stock_direct(symbol: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=10y&interval=1wk&events=div"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    result = data.get("chart", {}).get("result", [])
    if not result:
        return {"symbol": symbol, "error": "No chart data"}

    chart = result[0]
    meta = chart.get("meta", {})
    indicators = chart.get("indicators", {}).get("quote", [{}])[0]
    highs = [h for h in indicators.get("high", []) if h is not None]
    lows = [l for l in indicators.get("low", []) if l is not None]
    closes = [c for c in indicators.get("close", []) if c is not None]

    w_r = None
    w_zone = None
    asof = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    period = "14 weekly candles"

    if len(highs) >= 14 and len(lows) >= 14 and closes:
        h14 = max(highs[-14:])
        l14 = min(lows[-14:])
        c = closes[-1]
        if h14 > l14:
            w_r = round(((h14 - c) / (h14 - l14)) * -100, 2)
            w_zone = "Oversold" if w_r <= -80 else "Overbought" if w_r >= -20 else "Neutral"

    # Dividends
    divs = chart.get("events", {}).get("dividends", {})
    by_year = {}
    for ts, item in divs.items():
        try:
            yr = datetime.fromtimestamp(int(item["date"]), tz=timezone.utc).year
            by_year[yr] = by_year.get(yr, 0.0) + float(item["amount"])
        except Exception:
            continue

    curr_year = datetime.now(timezone.utc).year
    last_full_year = curr_year - 1
    streak = 0
    y = last_full_year
    while by_year.get(y, 0) > 0 and by_year.get(y - 1, 0) > 0 and by_year[y] > by_year[y - 1]:
        streak += 1
        y -= 1

    cagr_5y = None
    if last_full_year in by_year and (last_full_year - 5) in by_year:
        p0 = by_year[last_full_year - 5]
        p5 = by_year[last_full_year]
        if p0 > 0 and p5 > 0:
            cagr_5y = round(((p5 / p0) ** (1 / 5) - 1) * 100, 2)

    return {
        "symbol": symbol,
        "williams_r": w_r,
        "williams_zone": w_zone,
        "williams_r_period": period,
        "williams_asof": asof,
        "div_years": streak if streak > 0 else (1 if by_year.get(last_full_year, 0) > 0 else 0),
        "div_growth": cagr_5y,
        "price_latest": meta.get("regularMarketPrice") or (closes[-1] if closes else None),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh_stocks(limit: int = 0, workers: int = 12, force: bool = False):
    if not CSV_PATH.is_file():
        raise FileNotFoundError(f"Catalogue not found: {CSV_PATH}")

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    if limit > 0:
        rows = rows[:limit]

    checkpoint = load_checkpoint() if not force else {}
    needed = [r for r in rows if r["symbol"] not in checkpoint or checkpoint[r["symbol"]].get("williams_r") is None]
    print(f"Loaded {len(checkpoint)} cached histories. Fetching {len(needed)} / {len(rows)} stocks via Yahoo Chart API...")

    if needed:
        completed = 0
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(fetch_stock_direct, r["symbol"]): r for r in needed}
            for future in as_completed(future_map):
                r = future_map[future]
                sym = r["symbol"]
                try:
                    res = future.result()
                    checkpoint[sym] = res
                except Exception as e:
                    logging.warning("Error fetching %s: %s", sym, e)
                completed += 1
                if completed % 100 == 0 or completed == len(needed):
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    print(f"Progress: {completed} / {len(needed)} fetched ({rate:.1f} stocks/sec)")
                    save_checkpoint(checkpoint)

        save_checkpoint(checkpoint)
        print(f"Saved checkpoint with {len(checkpoint)} stock records.")

    # Upsert into screener.sqlite
    store = model.Store(model.DB)
    upsert_rows = []
    valid_williams = 0

    for r in rows:
        sym = r["symbol"]
        cached = checkpoint.get(sym, {})
        mcap = float(r.get("market_cap") or 0)
        price = float(cached.get("price_latest") or r.get("price") or 0)
        net_inc = float(r.get("net_income") or 0)
        roe = float(r.get("roe") or 0)
        div_y = float(r.get("div_yield") or 0)
        w_r = cached.get("williams_r")
        if w_r is not None:
            valid_williams += 1

        record = {
            "symbol": sym,
            "name": r.get("name", sym),
            "region_code": r.get("region_code", "us"),
            "region": r.get("market", "US"),
            "instrument": "stock",
            "active": True,
            "main_listing": True,
            "listing_reason": "Trading 212 investable primary listing",
            "market_cap": mcap,
            "market_cap_local": mcap,
            "price": price,
            "price_local": price,
            "quote_currency": r.get("currency", "USD"),
            "financial_currency": r.get("currency", "USD"),
            "net_income": net_inc,
            "net_income_local": net_inc,
            "roe": roe,
            "div_yield": div_y,
            "div_years": cached.get("div_years"),
            "div_growth": cached.get("div_growth"),
            "sector": r.get("sector") or "Unclassified",
            "industry": r.get("industry") or "Unclassified",
            "exchange": r.get("exchange") or "Unknown",
            "williams_r": w_r,
            "williams_zone": cached.get("williams_zone"),
            "williams_r_period": cached.get("williams_r_period"),
            "williams_asof": cached.get("williams_asof"),
            "technical_version": 2 if w_r is not None else None,
            "financial_fetched": datetime.now(timezone.utc).isoformat(),
        }
        upsert_rows.append(record)

    # Deactivate older non-catalogue stocks so only investable universe shows
    with store.connect() as conn:
        conn.execute("UPDATE stocks SET data = json_set(data, '$.active', json('false'))")

    store.upsert_many(upsert_rows)
    print(f"Upserted {len(upsert_rows)} investable stocks into screener.sqlite ({valid_williams} with valid Williams %R).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast parallel stock refresher")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    refresh_stocks(limit=args.limit, workers=args.workers, force=args.force)
