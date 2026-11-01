#!/usr/bin/env python3
"""Refresh live Lodestar World momentum and drumbeat in Atlas.

Pulls live market quotes for the 5 global ETFs, S&P 500, and GBP/USD.
Updates static/lodestar-data.js with the current intra-month standings,
lead margins, and cash gate status in under 2 seconds.
"""

from __future__ import annotations

import json
import re
import sys
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA_JS = ROOT / "static" / "lodestar-data.js"
BASE_JSON = ROOT / "data" / "lodestar_base.json"

TICKERS = {
    "WLD_MOM":   "IWMO.L",
    "WLD_QUAL":  "IWQU.L",
    "WLD_VAL":   "IWVL.L",
    "WLD_SMALL": "WSML.L",
    "WLD_DIV":   "TDGB.L",
}

FACTOR_NAMES = {
    "WLD_MOM":   "World Momentum",
    "WLD_QUAL":  "World Quality",
    "WLD_VAL":   "World Value",
    "WLD_SMALL": "World Small Cap",
    "WLD_DIV":   "Dividend Leaders",
}

STYLES = {
    "WLD_MOM":   "Momentum",
    "WLD_QUAL":  "Quality",
    "WLD_VAL":   "Value",
    "WLD_SMALL": "Size",
    "WLD_DIV":   "Dividend",
}

TRADABLE_ISINS = {
    "WLD_MOM":   "IE00BP3QZ825",
    "WLD_QUAL":  "IE00BP3QZ601",
    "WLD_VAL":   "IE00BP3QZB59",
    "WLD_SMALL": "IE00BF4RFH31",
    "WLD_DIV":   "NL0011683594",
}

# Base closing prices (in GBP) for the active 8-month and 19-month lookback windows.
DEFAULT_BASE = {
    "base_month_8m": "2026-03",
    "base_month_19m": "2025-04",
    "base_prices": {
        "WLD_MOM":   69.590364,
        "WLD_QUAL":  58.552467,
        "WLD_VAL":   46.527692,
        "WLD_SMALL": 6.899065,
        "WLD_DIV":   44.552559,
        "SPX":       5611.850098,
    }
}


def load_base_prices() -> dict:
    if BASE_JSON.is_file():
        try:
            return json.loads(BASE_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    BASE_JSON.parent.mkdir(exist_ok=True)
    BASE_JSON.write_text(json.dumps(DEFAULT_BASE, indent=2), encoding="utf-8")
    return DEFAULT_BASE


def refresh_lodestar() -> bool:
    if not DATA_JS.is_file():
        return False

    base_info = load_base_prices()
    base_prices = base_info["base_prices"]

    # 1. Fetch FX
    fx = 1.339
    try:
        t_fx = yf.Ticker("GBPUSD=X").fast_info.last_price
        if t_fx and t_fx > 0:
            fx = float(t_fx)
    except Exception:
        pass

    # 2. Fetch ETF quotes & S&P 500
    quotes_gbp = {}
    for slot, sym in TICKERS.items():
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info
            p = fi.last_price
            c = getattr(fi, "currency", "GBP")
            if p is not None and p > 0:
                p_val = float(p)
                if c == "USD":
                    p_val = p_val / fx
                elif c == "GBp":
                    p_val = p_val / 100.0
                quotes_gbp[slot] = p_val
        except Exception:
            pass

    spx_p = None
    try:
        spx_raw = yf.Ticker("^GSPC").fast_info.last_price
        if spx_raw and spx_raw > 0:
            spx_p = float(spx_raw)
    except Exception:
        pass

    if len(quotes_gbp) < 3:
        # Insufficient quotes
        return False

    # 3. Calculate live momentum
    ranks = []
    for slot, sym in TICKERS.items():
        cur_p = quotes_gbp.get(slot)
        base_p = base_prices.get(slot)
        if cur_p and base_p:
            mom = round((cur_p / base_p - 1.0) * 100.0, 2)
            ranks.append({
                "slot": slot,
                "name": FACTOR_NAMES[slot],
                "style": STYLES[slot],
                "ticker": sym,
                "isin": TRADABLE_ISINS[slot],
                "momentum": mom,
            })

    if not ranks:
        return False

    ranks.sort(key=lambda x: -x["momentum"])
    top_mom = ranks[0]["momentum"]

    # Days remaining
    now = datetime.now()
    _, last_day = monthrange(now.year, now.month)
    days_left = max(0, last_day - now.day)

    # Cash gate
    spx_base = base_prices.get("SPX", 5611.85)
    spx_chg = round((spx_p / spx_base - 1.0) * 100.0, 2) if spx_p else 35.07
    is_cash = spx_chg < -25.0

    live_standings = []
    for i, r in enumerate(ranks, 1):
        gap = round(r["momentum"] - top_mom, 2)
        bar_pct = round(max(15.0, (r["momentum"] / top_mom) * 100.0 if top_mom > 0 else 20.0), 1)
        live_standings.append({
            "rank": i,
            "slot": r["slot"],
            "name": r["name"],
            "style": r["style"],
            "ticker": r["ticker"],
            "isin": r["isin"],
            "momentum": r["momentum"],
            "gap": gap,
            "barPct": bar_pct,
            "isLeader": (i == 1),
            "isHeld": (r["slot"] == "WLD_VAL"),
            "targetWeight": 100 if (i == 1 and not is_cash) else 0,
        })

    # Read DATA from lodestar-data.js
    text = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"const DATA = (\{.*\});", text)
    if not m:
        return False

    data = json.loads(m.group(1))
    if "leaderboard" not in data:
        data["leaderboard"] = {}

    data["leaderboard"]["live"] = {
        "asof": date.today().isoformat(),
        "daysLeft": days_left,
        "projectedPick": "Cash" if is_cash else ranks[0]["name"],
        "projectedTicker": "CASH" if is_cash else ranks[0]["ticker"],
        "cashGateActive": is_cash,
        "spxChange": spx_chg,
        "standings": live_standings,
    }

    # Write back
    new_text = "/* Generated by build_data.py / refresh_lodestar.py. Do not edit by hand. */\nconst DATA = " + json.dumps(data) + ";"
    DATA_JS.write_text(new_text, encoding="utf-8")
    print(f"Lodestar World live drumbeat updated: {ranks[0]['name']} leads at +{ranks[0]['momentum']}%, SPX {spx_chg}%, {days_left}d left.")
    return True


if __name__ == "__main__":
    refresh_lodestar()
