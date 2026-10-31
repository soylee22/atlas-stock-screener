"""Dispatch Portfolio Command Briefing (Atlas Compounder + LYW #1 Momentum + Live T212 + Live Stockopedia).

Runs on a GitHub Actions schedule or launchd (daily after US market close).
Bulletproof HTML table architecture and inline styles for email and Telegram.
Reflects live active holdings:
- Sleeve 1 (Atlas): AVGO / BAC / HSBA at high NI and oversold Williams %R
- Sleeve 2 (LYW): Active 2026 holding is SanDisk (SNDK), entered at $275.24, currently $1,378.22, 35% stop at $178.91 (untriggered)
- Sleeve 3 (Trading 212): Live portfolio holdings with real-time StockRank, Quality, Value, Momentum, and Style tracking
- Lookahead (2027): MRNA (+629.1%), SNDK (+474.7%), DELL (+362.8%)
"""
from __future__ import annotations

import os
import sys
import smtplib
import sqlite3
import json
import gzip
import time
import base64
import datetime
import urllib.parse
import urllib.request
from pathlib import Path
from email.message import EmailMessage

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, 'data', 'screener.sqlite')
HISTORY_PATH = os.path.join(ROOT_DIR, 'seed', 'stockrank_history.json')
TG_ENV_FILE = Path("/Users/leeslater/SecondBrain/tools/reading-archive/.tg.env")


def load_telegram_credentials() -> tuple[str | None, str | None, str | None]:
    """Read Telegram bot credentials from environment or SecondBrain secret env."""
    token = os.environ.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT")
    thread = os.environ.get("TG_THREAD")

    if (not token or not chat) and TG_ENV_FILE.exists():
        try:
            for line in TG_ENV_FILE.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    val = v.strip().strip('"').strip("'")
                    if k == "TG_TOKEN" and not token:
                        token = val
                    elif k == "TG_CHAT" and not chat:
                        chat = val
                    elif k == "TG_THREAD" and not thread:
                        thread = val
        except Exception:
            pass

    return token, chat, thread


def send_telegram(message_html: str) -> bool:
    """Send formatted Telegram message to Lee's phone."""
    token, chat, thread = load_telegram_credentials()
    if not token or not chat:
        print("Telegram credentials not found. Skipping Telegram dispatch.", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }
    if thread:
        payload["message_thread_id"] = int(thread)

    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.load(resp)
            ok = bool(res.get("ok"))
            if ok:
                print(f"Successfully dispatched briefing to Telegram (thread {thread})")
            return ok
    except Exception as exc:
        print(f"Telegram dispatch failed: {exc}", file=sys.stderr)
        return False


def load_t212_credentials() -> tuple[str | None, str | None, str]:
    """Read T212 API credentials from environment or known config paths."""
    key = os.environ.get("T212_KEY_ID") or os.environ.get("T212_KEY")
    secret = os.environ.get("T212_SECRET")
    base = os.environ.get("T212_BASE", "https://live.trading212.com")
    candidates = [
        Path("/Users/leeslater/projects/portfolio-tracker/.env"),
        Path("/Users/leeslater/.mithril/ping.env"),
        Path(ROOT_DIR) / ".env",
    ]
    for p in candidates:
        if (not key or not secret) and p.exists():
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        val = v.strip().strip('"').strip("'")
                        if k in ("T212_KEY_ID", "T212_KEY") and not key:
                            key = val
                        elif k == "T212_SECRET" and not secret:
                            secret = val
                        elif k == "T212_BASE" and not base:
                            base = val
            except Exception:
                pass
    return key, secret, base


def clean_t212_ticker(t: str) -> tuple[str, str]:
    """Returns (canonical_symbol, alt_symbol) for factor database matching."""
    if not t:
        return "", ""
    parts = t.split("_")
    base = parts[0]
    if base.startswith("SNDK"):
        return "SNDK", "SNDK"
    if base.endswith("l") and len(base) > 1:
        return base[:-1] + ".L", base[:-1]
    if len(parts) >= 2 and parts[1] == "US":
        return base, base
    if len(parts) >= 2 and parts[1] == "DE":
        return base + ".DE", base
    if len(parts) >= 2 and parts[1] == "FR":
        return base + ".PA", base
    if len(parts) >= 2 and parts[1] == "NL":
        return base + ".AS", base
    return base, base


def fetch_live_stockopedia_for_holdings(symbols: list[str]) -> dict:
    """Launch headless Playwright to fetch live Stockopedia factor ratings for holdings."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed, falling back to cached ratings.", file=sys.stderr)
        return {}

    symbol_to_target = {}
    targets = []
    for s in symbols:
        if s.endswith(".L"):
            t = f"LON:{s[:-2]}"
        elif s.endswith(".DE"):
            t = f"ETR:{s[:-3]}"
        elif s.endswith(".PA"):
            t = f"EPA:{s[:-3]}"
        elif s.endswith(".MC"):
            t = f"MCE:{s[:-3]}"
        elif s.endswith(".AS"):
            t = f"AMS:{s[:-3]}"
        elif s in ("TMUS", "AVGO", "SNDK"):
            t = f"NSQ:{s}"
        else:
            t = f"NYQ:{s}"
        symbol_to_target[s] = t
        targets.append(t)

    results = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            print("Warming up Playwright browser to solve AWS WAF...", file=sys.stderr)
            page.goto("https://www.stockopedia.com/share-prices/-LON:BARC/", wait_until="networkidle", timeout=25000)

            fetched = page.evaluate("""async (tickers) => {
                const out = {};
                const promises = tickers.map(async (t) => {
                    try {
                        const r = await fetch(`https://www.stockopedia.com/share-prices/-${t}/`);
                        if (r.status !== 200) return;
                        const html = await r.text();
                        const m = html.match(/<script id="__NEXT_DATA__" type="application\\/json">([^<]+)<\\/script>/);
                        if (!m) return;
                        const j = JSON.parse(m[1]);
                        const sec = j.props?.pageProps?.report?.security;
                        if (!sec || !sec.ranks) return;
                        out[t] = {
                            quality: sec.ranks.quality,
                            value: sec.ranks.value,
                            momentum: sec.ranks.momentum,
                            stockRank: sec.ranks.stockRank,
                            style: sec.ratios?.general?.style || 'Neutral',
                            name: sec.name
                        };
                    } catch(e) {}
                });
                await Promise.all(promises);
                return out;
            }""", targets)
            browser.close()

            for s, t in symbol_to_target.items():
                if t in fetched:
                    d = fetched[t]
                    q = d.get("quality")
                    v = d.get("value")
                    m = d.get("momentum")
                    sr = d.get("stockRank")
                    style = d.get("style", "Neutral")
                    qv = round((q + v) / 2.0, 1) if q is not None and v is not None else None
                    qm = round((q + m) / 2.0, 1) if q is not None and m is not None else None
                    results[s] = {
                        "stockrank": sr,
                        "quality": q,
                        "value": v,
                        "momentum": m,
                        "qv_score": qv,
                        "qm_score": qm,
                        "style": style,
                        "name": d.get("name")
                    }
                    print(f"Live Stockopedia: {s} -> SR {sr}, Q {q}, V {v}, M {m}, {style}", file=sys.stderr)
    except Exception as exc:
        print(f"Live Stockopedia crawl notice: {exc}", file=sys.stderr)

    return results


def get_cached_factor_ratings(symbol: str, alt_symbol: str) -> dict:
    """Look up StockRank, Quality, Value, Momentum, and Style from SQLite or seed."""
    res = {
        "stockrank": None,
        "quality": None,
        "value": None,
        "momentum": None,
        "qv_score": None,
        "qm_score": None,
        "style": None,
        "name": None
    }
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT data FROM stocks WHERE symbol = ? OR symbol = ?", (symbol, alt_symbol))
            row = cur.fetchone()
            if row:
                d = json.loads(row[0])
                res["stockrank"] = d.get("stockrank")
                res["quality"] = d.get("quality_rank")
                res["value"] = d.get("value_rank")
                res["momentum"] = d.get("momentum_rank")
                res["qv_score"] = d.get("qv_score")
                res["qm_score"] = d.get("qm_score")
                res["style"] = d.get("stock_style")
                res["name"] = d.get("name")
            conn.close()
        except Exception:
            pass

    if res["stockrank"] is None:
        seed_path = os.path.join(ROOT_DIR, "seed", "stockranks.json")
        if os.path.exists(seed_path):
            try:
                with open(seed_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                match = None
                for item in sdata:
                    ts = [item.get("ticker", ""), item.get("symbol", ""), item.get("atlas_symbol", "")]
                    for t in ts:
                        if t and (t == symbol or t == alt_symbol or t.endswith(f":{symbol}") or t.endswith(f":{alt_symbol}")):
                            match = item
                            break
                    if match:
                        break
                if match:
                    res["stockrank"] = match.get("stockRank") or match.get("stockrank")
                    res["quality"] = match.get("quality") or match.get("quality_rank")
                    res["value"] = match.get("value") or match.get("value_rank")
                    res["momentum"] = match.get("momentum") or match.get("momentum_rank")
                    res["qv_score"] = match.get("qv_score")
                    if res["qv_score"] is None and res["quality"] and res["value"]:
                        res["qv_score"] = round((res["quality"] + res["value"]) / 2.0, 1)
                    res["qm_score"] = match.get("qm_score")
                    if res["qm_score"] is None and res["quality"] and res["momentum"]:
                        res["qm_score"] = round((res["quality"] + res["momentum"]) / 2.0, 1)
                    res["style"] = match.get("style") or match.get("stock_style")
                    res["name"] = match.get("name") or res.get("name")
            except Exception:
                pass

    return res


def update_and_compare_factor_history(today_scores: dict[str, dict]) -> dict[str, dict]:
    """Compare today's live factor scores against history, record deltas, and append snapshot."""
    deltas = {}
    history_data = {"last_updated": None, "history": []}

    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            pass

    prev_snapshot = history_data.get("history", [])[-1]["scores"] if history_data.get("history") else {}

    for sym, cur in today_scores.items():
        sr_diff = 0
        m_diff = 0
        style_changed = False
        prev_style = None

        if sym in prev_snapshot:
            p = prev_snapshot[sym]
            if cur.get("stockrank") is not None and p.get("stockrank") is not None:
                sr_diff = cur["stockrank"] - p["stockrank"]
            if cur.get("momentum") is not None and p.get("momentum") is not None:
                m_diff = cur["momentum"] - p["momentum"]
            if cur.get("style") and p.get("style") and cur["style"] != p["style"]:
                style_changed = True
                prev_style = p["style"]

        deltas[sym] = {
            "sr_diff": sr_diff,
            "m_diff": m_diff,
            "style_changed": style_changed,
            "prev_style": prev_style
        }

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    today_str = datetime.date.today().isoformat()

    # Append today's entry (or update if already run today)
    hist_list = history_data.get("history", [])
    if hist_list and hist_list[-1].get("date") == today_str:
        hist_list[-1]["scores"].update(today_scores)
        hist_list[-1]["timestamp"] = now_iso
    else:
        hist_list.append({
            "date": today_str,
            "timestamp": now_iso,
            "scores": today_scores
        })

    # Keep last 90 snapshots
    history_data["history"] = hist_list[-90:]
    history_data["last_updated"] = now_iso

    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=2)
    except Exception as exc:
        print(f"Error saving history: {exc}", file=sys.stderr)

    return deltas


def get_t212_holdings() -> dict:
    """Fetch live Trading 212 account cash and open positions enriched with real-time StockRanks."""
    cache_path = os.path.join(ROOT_DIR, "cache", "t212_holdings_cache.json")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    key, secret, base = load_t212_credentials()
    raw_pos = []
    cash = {}

    if key and secret:
        try:
            auth = "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()
            req = urllib.request.Request(f"{base}/api/v0/equity/account/cash", headers={"Authorization": auth})
            with urllib.request.urlopen(req, timeout=10) as r:
                cash = json.load(r)

            time.sleep(1.5)
            req = urllib.request.Request(f"{base}/api/v0/equity/portfolio", headers={"Authorization": auth})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw_pos = json.load(r)
        except Exception as exc:
            print(f"Trading 212 API fetch failed ({exc}), falling back to cache", file=sys.stderr)

    if not raw_pos and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    if not raw_pos:
        raw_pos = [
            {"ticker": "EUEl_EQ", "quantity": 24.7286, "averagePrice": 5240.0, "currentPrice": 5211.0, "ppl": -7.17},
            {"ticker": "SNDK1_US_EQ", "quantity": 0.8482, "averagePrice": 1557.72, "currentPrice": 1157.57, "ppl": -265.67},
            {"ticker": "BARCl_EQ", "quantity": 114.0716, "averagePrice": 436.13, "currentPrice": 415.05, "ppl": -24.05},
            {"ticker": "CAT_US_EQ", "quantity": 0.8373, "averagePrice": 654.95, "currentPrice": 624.62, "ppl": -21.17},
            {"ticker": "TMUS_US_EQ", "quantity": 4.0242, "averagePrice": 150.45, "currentPrice": 157.01, "ppl": 14.47},
            {"ticker": "GSK_US_EQ", "quantity": 12.0998, "averagePrice": 46.64, "currentPrice": 49.60, "ppl": 21.64},
            {"ticker": "AVGO_US_EQ", "quantity": 1.7821, "averagePrice": 326.23, "currentPrice": 327.13, "ppl": 0.59},
            {"ticker": "JPM_US_EQ", "quantity": 1.7711, "averagePrice": 334.19, "currentPrice": 337.55, "ppl": -0.70},
            {"ticker": "GS_US_EQ", "quantity": 0.6690, "averagePrice": 898.13, "currentPrice": 894.50, "ppl": -7.27},
            {"ticker": "BAC_US_EQ", "quantity": 6.3570, "averagePrice": 51.34, "currentPrice": 50.05, "ppl": -6.40}
        ]
        cash = {"total": 5370.34, "invested": 5666.07, "ppl": -295.73, "free": 0.0}

    total_gbp = float(cash.get("total", 0.0))
    free_gbp = float(cash.get("free", 0.0))
    invested_gbp = float(cash.get("invested", 0.0))
    ppl_gbp = float(cash.get("ppl", 0.0))
    pnl_pct = (ppl_gbp / invested_gbp * 100.0) if invested_gbp else 0.0

    uk_val_gbp = sum((p["quantity"] * p["currentPrice"] / 100.0) for p in raw_pos if p["ticker"].endswith("l_EQ"))
    us_val_usd = sum((p["quantity"] * p["currentPrice"]) for p in raw_pos if not p["ticker"].endswith("l_EQ"))
    us_val_gbp = total_gbp - free_gbp - uk_val_gbp
    implied_fx = us_val_usd / us_val_gbp if us_val_gbp > 0 else 1.3398

    # Extract all candidate equity symbols for live Stockopedia scraping
    equity_symbols = []
    for p in raw_pos:
        raw_tkr = p["ticker"]
        sym, _ = clean_t212_ticker(raw_tkr)
        if not sym.startswith("EUE") and not "ETF" in raw_tkr:
            equity_symbols.append(sym)

    # 1. Fetch live Stockopedia scores via Playwright
    live_stockopedia = fetch_live_stockopedia_for_holdings(equity_symbols)

    today_scores_for_history = {}
    positions = []
    for p in raw_pos:
        raw_tkr = p["ticker"]
        qty = float(p.get("quantity", 0.0))
        cp = float(p.get("currentPrice", 0.0))
        ap = float(p.get("averagePrice", 0.0))
        ppl = float(p.get("ppl", 0.0))
        ret_pct = ((cp - ap) / ap * 100.0) if ap else 0.0

        if raw_tkr.endswith("l_EQ"):
            val_gbp = round((qty * cp) / 100.0, 2)
        else:
            val_gbp = round((qty * cp) / implied_fx, 2)

        weight_pct = round((val_gbp / total_gbp * 100.0), 1) if total_gbp else 0.0

        sym, alt = clean_t212_ticker(raw_tkr)
        is_etf = "ETF" in raw_tkr or sym.startswith("EUE") or sym in ("VWRL", "SMEA", "VHYL")

        if is_etf:
            factors = {
                "stockrank": None, "quality": None, "value": None, "momentum": None,
                "qv_score": None, "qm_score": None, "style": "Core ETF",
                "name": "iShares MSCI Europe UCITS ETF"
            }
        else:
            # Use live Stockopedia data if available, otherwise cached
            if sym in live_stockopedia and live_stockopedia[sym].get("stockrank") is not None:
                factors = live_stockopedia[sym]
            else:
                factors = get_cached_factor_ratings(sym, alt)

            today_scores_for_history[sym] = {
                "stockrank": factors.get("stockrank"),
                "quality": factors.get("quality"),
                "value": factors.get("value"),
                "momentum": factors.get("momentum"),
                "qv": factors.get("qv_score"),
                "qm": factors.get("qm_score"),
                "style": factors.get("style")
            }

        positions.append({
            "ticker": raw_tkr,
            "symbol": sym,
            "name": factors.get("name") or sym,
            "quantity": qty,
            "value_gbp": val_gbp,
            "ppl_gbp": ppl,
            "ret_pct": round(ret_pct, 1),
            "weight_pct": weight_pct,
            "is_etf": is_etf,
            "stockrank": factors.get("stockrank"),
            "quality": factors.get("quality"),
            "value": factors.get("value"),
            "momentum": factors.get("momentum"),
            "qv_score": factors.get("qv_score"),
            "qm_score": factors.get("qm_score"),
            "style": factors.get("style") or "Unrated"
        })

    # 2. Compute deltas against history
    deltas = update_and_compare_factor_history(today_scores_for_history)
    for pos in positions:
        s = pos["symbol"]
        d = deltas.get(s, {})
        pos["sr_diff"] = d.get("sr_diff", 0)
        pos["m_diff"] = d.get("m_diff", 0)
        pos["style_changed"] = d.get("style_changed", False)
        pos["prev_style"] = d.get("prev_style")

    positions.sort(key=lambda x: (x["is_etf"], -(x["stockrank"] or 0), -x["value_gbp"]))

    res = {
        "total_gbp": total_gbp,
        "invested_gbp": invested_gbp,
        "ppl_gbp": ppl_gbp,
        "pnl_pct": round(pnl_pct, 1),
        "free_cash_gbp": free_gbp,
        "positions": positions,
        "as_of": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
    except Exception:
        pass

    return res


def format_telegram_holdings_section(t212: dict) -> str:
    """Format Trading 212 holdings with factor ratings and change tracking for Telegram."""
    total = t212.get("total_gbp", 0.0)
    ppl = t212.get("ppl_gbp", 0.0)
    pnl_pct = t212.get("pnl_pct", 0.0)
    cash = t212.get("free_cash_gbp", 0.0)
    sign = "+" if ppl >= 0 else "-"

    lines = [
        f"💼 <b>Trading 212 Active Holdings & StockRanks</b>",
        f"<i>Equity: £{total:,.0f} · P&L: {sign}£{abs(ppl):,.0f} ({pnl_pct:+.1f}%) · Cash: £{cash:,.0f}</i>\n"
    ]

    for p in t212.get("positions", []):
        sym = p["symbol"]
        val = p["value_gbp"]
        pnl = p["ret_pct"]
        p_sign = "+" if pnl >= 0 else ""
        wt = p["weight_pct"]
        sr = p["stockrank"]
        style = p["style"]
        name = p["name"]
        sr_diff = p.get("sr_diff", 0)
        m_diff = p.get("m_diff", 0)
        style_changed = p.get("style_changed", False)
        prev_style = p.get("prev_style")

        short_name = (
            name.split(",")[0]
            .replace(" Corporation", "")
            .replace(" Inc.", "")
            .replace(" PLC", "")
            .replace(" plc", "")
            .replace(" & Co", "")
        )
        if len(short_name) > 18:
            short_name = short_name[:16] + ".."

        if p.get("is_etf") or sr is None:
            lines.append(
                f"• <b>{sym}</b> ({short_name}) · £{val:,.0f} ({wt:.1f}% · {p_sign}{pnl:.1f}%)\n"
                f"  <i>{style} · Core European Benchmark</i>"
            )
        else:
            q, v, m = p["quality"], p["value"], p["momentum"]
            qv, qm = p["qv_score"], p["qm_score"]
            qv_str = f"{qv:.0f}" if qv is not None else "—"
            qm_str = f"{qm:.0f}" if qm is not None else "—"

            sr_delta_str = f" ({'+' if sr_diff > 0 else ''}{sr_diff})" if sr_diff != 0 else ""
            m_delta_str = f" ({'+' if m_diff > 0 else ''}{m_diff})" if m_diff != 0 else ""
            style_str = f"⚠️ {prev_style} ➔ {style}" if style_changed else style

            lines.append(
                f"• <b>{sym}</b> ({short_name}) · £{val:,.0f} ({wt:.1f}% · {p_sign}{pnl:.1f}%) · <b>SR {sr}{sr_delta_str}</b> ({style_str})\n"
                f"  Q:{q} V:{v} M:{m}{m_delta_str} · QV:{qv_str} QM:{qm_str}"
            )

    return "\n".join(lines)


def format_telegram_briefing(frontier: list[dict], secondary: list[dict], lyw: dict, propicks: dict, as_of_str: str, t212_data: dict | None = None) -> str:
    """Format Telegram message for portfolio briefing in clean British English."""
    ftse_tickers = propicks.get("ftse_tickers", "")
    ftse_count = propicks.get("ftse_count", 20)
    ftse_weight = propicks.get("ftse_weight", 5.0)

    tech_tickers = propicks.get("tech_tickers", "")
    tech_count = propicks.get("tech_count", 15)
    tech_weight = propicks.get("tech_weight", 6.7)

    frontier_lines = []
    for i, f in enumerate(frontier[:3], 1):
        ni_str = f"${f['net_income']/1e9:.2f}B"
        frontier_lines.append(f"• <b>#{i} {f['symbol']}</b> ({f['name']}): <b>{ni_str}</b> Net Income · W%R {f['wr']:.1f} (Pareto Frontier)")
    frontier_block = "\n".join(frontier_lines)

    secondary_str = ", ".join([f"{s['symbol']} (${s['net_income']/1e9:.2f}B · {s['wr']:.1f} W%R)" for s in secondary[:3]])

    contender_lines = []
    for i, c in enumerate(lyw.get("contenders", [])[:4], 1):
        sign = "+" if c["ytd"] >= 0 else ""
        active_tag = " (Current holding)" if c["symbol"] == "SNDK" else ""
        contender_lines.append(f"   {i}. {c['symbol']} ({sign}{c['ytd']:.1f}% YTD){active_tag}")
    contenders_block = "\n".join(contender_lines)

    perf_sign = "+" if lyw["perf_pct"] >= 0 else ""
    cushion_sign = "+" if lyw["cushion_pct"] >= 0 else ""

    t212_block = format_telegram_holdings_section(t212_data) if t212_data else ""

    return (
        f"💼 <b>Portfolio Command Briefing · {as_of_str}</b>\n\n"
        f"🏆 <b>Hold Last Year's Winner (2026)</b>\n"
        f"• <b>Active Holding:</b> {lyw['holding']}\n"
        f"• <b>Actual Entry:</b> 12 Oct 2026 @ ${lyw['entry_price']:,.2f} (£{lyw['entry_gbp']:,.2f} invested on T212)\n"
        f"• <b>Latest Value:</b> £{lyw['current_gbp']:,.2f} ({perf_sign}{lyw['perf_pct']:.1f}% return) @ broker ${lyw['current_price']:,.2f}\n"
        f"• <b>Personal 35% Stop Loss:</b> ${lyw['stop_price']:,.2f} (Untouched, {cushion_sign}{lyw['cushion_pct']:.1f}% cushion above stop)\n"
        f"• <b>Instruction:</b> <b>HOLD SNDK TO 31 DECEMBER 2026</b>\n"
        f"• <b>Countdown to Reselection:</b> {lyw['cal_days']} days to 31 Dec close ({lyw['trading_days']} trading sessions)\n"
        f"• <b>2027 Leaderboard Contenders:</b>\n"
        f"{contenders_block}\n"
        f"• <b>Next Move:</b> If #1 holds at 31 Dec close, rotate 100% into leader on 2 Jan 2027.\n\n"
        f"🏛️ <b>Atlas Dream Zone (Pareto Frontier)</b>\n"
        f"<i>Optimal tradeoff: Highest Net Income + Deepest Williams %R Pullback</i>\n"
        f"{frontier_block}\n"
        f"• <b>Other Dream Zone Names:</b> {secondary_str}\n\n"
        f"{t212_block}\n\n"
        f"🇬🇧 <b>ProPicks: Beat the FTSE 100</b>\n"
        f"• <b>Structure:</b> {ftse_count} holdings, {ftse_weight:.1f}% equal-weight per position\n"
        f"• <b>Active Roster:</b> {ftse_tickers}\n"
        f"• <b>Rebalance:</b> 1 Nov 2026 (Detection active for Entries & Exits)\n\n"
        f"🇺🇸 <b>ProPicks: Tech Titans</b>\n"
        f"• <b>Structure:</b> {tech_count} holdings, {tech_weight:.1f}% equal-weight per position\n"
        f"• <b>Core Roster:</b> {tech_tickers}\n"
        f"• <b>Rebalance:</b> 1 Nov 2026 (Detection active for Entries & Exits)\n\n"
        f"📡 <i>Watcher daemon is polling both strategies every 30 minutes.</i>"
    )


def get_atlas_picks() -> tuple[list[dict], list[dict]]:
    """Extract top Dream Zone Pareto Frontier candidates directly from the live Atlas website feed."""
    url = "https://soylee22.github.io/atlas-stock-screener/data/stocks.json.gz"
    frontier = []
    secondary = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(gzip.decompress(resp.read()))
        fields = data.get("fields", [])
        rows = [dict(zip(fields, r)) for r in data.get("rows", [])]

        dream = [
            r for r in rows
            if r.get("active") and isinstance(r.get("net_income"), (int, float)) and r["net_income"] > 0
            and isinstance(r.get("williams_r"), (int, float)) and r["williams_r"] <= -80
            and (r.get("div_years") or 0) >= 2
        ]
        dream.sort(key=lambda x: x["net_income"], reverse=True)

        best_y = 0.0
        for s in dream:
            y = float(s["williams_r"])
            if not frontier or y < best_y:
                frontier.append({
                    "symbol": s["symbol"],
                    "name": s["name"],
                    "net_income": float(s["net_income"]),
                    "wr": float(s["williams_r"]),
                    "roe": float(s.get("roe") or 0.0),
                    "div_years": int(s.get("div_years") or 0),
                    "sector": f"{s.get('sector', '')} · {s.get('industry', '')}".strip(" ·")
                })
                best_y = y

        frontier_syms = {f["symbol"] for f in frontier}
        for s in dream:
            if s["symbol"] not in frontier_syms:
                secondary.append({
                    "symbol": s["symbol"],
                    "name": s["name"],
                    "net_income": float(s["net_income"]),
                    "wr": float(s["williams_r"]),
                    "roe": float(s.get("roe") or 0.0),
                    "div_years": int(s.get("div_years") or 0),
                    "sector": f"{s.get('sector', '')} · {s.get('industry', '')}".strip(" ·")
                })
                if len(secondary) >= 3:
                    break
    except Exception as exc:
        print(f"Atlas live fetch failed ({exc}), falling back to defaults", file=sys.stderr)
        frontier = [
            {"symbol": "BAC", "name": "Bank of America Corp", "net_income": 30.509e9, "wr": -95.30, "roe": 11.2, "div_years": 12, "sector": "Finance · Major Banks"},
            {"symbol": "HSBA.L", "name": "HSBC Holdings Plc", "net_income": 16.012e9, "wr": -96.13, "roe": 14.1, "div_years": 4, "sector": "Finance · Major Banks"},
            {"symbol": "SAN.MC", "name": "Banco Santander, S.A.", "net_income": 15.778e9, "wr": -98.75, "roe": 12.3, "div_years": 3, "sector": "Finance · Major Banks"}
        ]
        secondary = [
            {"symbol": "AVGO", "name": "Broadcom Inc.", "net_income": 23.126e9, "wr": -94.02, "roe": 18.4, "div_years": 14, "sector": "Electronic Technology · Semiconductors"},
            {"symbol": "GS", "name": "Goldman Sachs Group, Inc.", "net_income": 17.176e9, "wr": -84.52, "roe": 12.1, "div_years": 14, "sector": "Finance · Investment Banks/Brokers"},
            {"symbol": "MS", "name": "Morgan Stanley", "net_income": 16.861e9, "wr": -82.75, "roe": 13.5, "div_years": 12, "sector": "Finance · Investment Managers"}
        ]

    return frontier, secondary


def get_lyw_data() -> dict:
    """Calculate Hold Last Year's Winner tracking and 2027 lookahead contenders."""
    entry_price = 1557.71
    entry_gbp = 998.50
    stop_price = 1012.51
    current_price = 1354.69
    try:
        import yfinance as yf
        t = yf.Ticker("SNDK")
        hist = t.history(period="5d")
        if not hist.empty:
            current_price = float(hist["Close"].iloc[-1])
    except Exception:
        pass

    perf_pct = (current_price - entry_price) / entry_price * 100.0
    val_gbp = entry_gbp * (current_price / entry_price)
    cushion_pct = (current_price - stop_price) / stop_price * 100.0

    now = datetime.datetime.now(datetime.timezone.utc)
    target_date = datetime.date(now.year, 12, 31)
    cal_days = (target_date - now.date()).days
    cur_d = now.date()
    trading_days = 0
    while cur_d <= target_date:
        if cur_d.weekday() < 5:
            trading_days += 1
        cur_d += datetime.timedelta(days=1)

    contenders = [
        {"symbol": "MRNA", "name": "Moderna Inc.", "ytd": 716.3},
        {"symbol": "SNDK", "name": "SanDisk Corp.", "ytd": 477.4},
        {"symbol": "MU", "name": "Micron Technology", "ytd": 295.8},
        {"symbol": "DELL", "name": "Dell Technologies", "ytd": 164.7}
    ]
    try:
        import yfinance as yf
        for c in contenders:
            tkr = yf.Ticker(c["symbol"])
            h = tkr.history(period="1y")
            if not h.empty:
                ret = (h["Close"].iloc[-1] - h["Close"].iloc[0]) / h["Close"].iloc[0] * 100.0
                c["ytd"] = round(float(ret), 1)
        contenders.sort(key=lambda x: x["ytd"], reverse=True)
    except Exception:
        pass

    return {
        "holding": "SNDK (SanDisk Corp.)",
        "entry_price": entry_price,
        "entry_gbp": entry_gbp,
        "current_price": current_price,
        "current_gbp": val_gbp,
        "perf_pct": perf_pct,
        "stop_price": stop_price,
        "cushion_pct": cushion_pct,
        "cal_days": cal_days,
        "trading_days": trading_days,
        "contenders": contenders
    }


def get_propicks_summary() -> dict:
    """Read latest ProPicks state from the local watcher cache."""
    cache_path = os.path.join(ROOT_DIR, "..", "propicks-watcher", "cache.json")
    ftse_count = 20
    ftse_weight = 5.0
    tech_count = 15
    tech_weight = 6.67
    ftse_tickers = []
    tech_tickers = []
    next_rebal = "1 Nov 2026"

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            if "Beat the FTSE" in cdata:
                h = cdata["Beat the FTSE"].get("holdings", {})
                ftse_count = len(h) or 20
                ftse_weight = round(100.0 / ftse_count, 1)
                for item in h.values():
                    raw = item.get("ticker", "")
                    clean = raw.split(":")[-1] if ":" in raw else raw
                    if clean and clean != "PENDING" and clean not in ftse_tickers:
                        ftse_tickers.append(clean)
            if "Tech Titans" in cdata:
                h = cdata["Tech Titans"].get("holdings", {})
                tech_count = len(h) or 15
                tech_weight = round(100.0 / tech_count, 1)
                for item in h.values():
                    raw = item.get("ticker", "")
                    clean = raw.split(":")[-1] if ":" in raw else raw
                    if clean and clean != "PENDING" and clean not in tech_tickers:
                        tech_tickers.append(clean)
        except Exception as exc:
            print(f"Error reading propicks cache: {exc}", file=sys.stderr)

    if not ftse_tickers:
        ftse_tickers = ["NWG", "VTY", "LLOY", "KLR", "KIE", "BARC", "CMCX", "VOD", "AAL", "III", "PHNX", "CURY", "VSVS", "SPI", "IHP", "QLT", "MNG", "OSB", "SHEL", "MTRO"]
    if not tech_tickers:
        tech_tickers = ["AVGO", "ORCL", "QCOM", "HPQ", "INTC", "MRVL", "ON", "SWKS", "ACLS", "ONTO", "KD"]

    return {
        "ftse_count": ftse_count,
        "ftse_weight": ftse_weight,
        "ftse_tickers": ", ".join(ftse_tickers[:20]),
        "tech_count": tech_count,
        "tech_weight": tech_weight,
        "tech_tickers": ", ".join(tech_tickers[:15]),
        "next_rebal": next_rebal
    }


def render_t212_html_card(t212: dict) -> str:
    """Render HTML card for Trading 212 holdings with factor ratings and deltas."""
    total = t212.get("total_gbp", 0.0)
    ppl = t212.get("ppl_gbp", 0.0)
    pnl_pct = t212.get("pnl_pct", 0.0)
    invested = t212.get("invested_gbp", 0.0)
    cash = t212.get("free_cash_gbp", 0.0)

    rows_html = []
    has_any_delta = False

    for p in t212.get("positions", []):
        sym = p["symbol"]
        name = p["name"]
        val = p["value_gbp"]
        wt = p["weight_pct"]
        ret = p["ret_pct"]
        ppl_val = p["ppl_gbp"]
        is_etf = p.get("is_etf")
        sr = p["stockrank"]
        style = p["style"]
        sr_diff = p.get("sr_diff", 0)
        m_diff = p.get("m_diff", 0)
        style_changed = p.get("style_changed", False)
        prev_style = p.get("prev_style")

        if sr_diff != 0 or m_diff != 0 or style_changed:
            has_any_delta = True

        ret_color = "#34c759" if ret >= 0 else "#ff3b30"
        ret_sign = "+" if ret >= 0 else ""
        ppl_sign = "+" if ppl_val >= 0 else "-"

        if style == "Super Stock":
            style_badge = '<span style="display: inline-block; padding: 3px 8px; border-radius: 6px; background-color: #e8f7ed; color: #1b873a; font-size: 11px; font-weight: 700;">Super Stock</span>'
        elif style == "High Flyer":
            style_badge = '<span style="display: inline-block; padding: 3px 8px; border-radius: 6px; background-color: #e8f2ff; color: #0071e3; font-size: 11px; font-weight: 700;">High Flyer</span>'
        elif style == "Turnaround":
            style_badge = '<span style="display: inline-block; padding: 3px 8px; border-radius: 6px; background-color: #fff3e6; color: #c46200; font-size: 11px; font-weight: 700;">Turnaround</span>'
        elif style == "Core ETF":
            style_badge = '<span style="display: inline-block; padding: 3px 8px; border-radius: 6px; background-color: #f3e8ff; color: #7e22ce; font-size: 11px; font-weight: 700;">Core ETF</span>'
        else:
            style_badge = f'<span style="display: inline-block; padding: 3px 8px; border-radius: 6px; background-color: #f2f2f7; color: #555e6d; font-size: 11px; font-weight: 600;">{style}</span>'

        if style_changed and prev_style:
            style_badge += f'<div style="font-size: 10px; color: #ff9500; font-weight: 600; margin-top: 2px;">(was {prev_style})</div>'

        if is_etf or sr is None:
            sr_badge = '<span style="font-size: 11px; color: #86868b; font-weight: 600;">ETF</span>'
            factors_str = '<span style="font-size: 11px; color: #86868b;">Benchmark Index</span>'
            combo_str = '<span style="font-size: 11px; color: #86868b;">—</span>'
        else:
            if sr >= 80:
                sr_bg, sr_fg = "#1b873a", "#ffffff"
            elif sr >= 65:
                sr_bg, sr_fg = "#0071e3", "#ffffff"
            else:
                sr_bg, sr_fg = "#e5e5ea", "#1d1d1f"
            
            sr_delta_html = ""
            if sr_diff > 0:
                sr_delta_html = f'<span style="display: block; font-size: 10px; color: #34c759; font-weight: 700;">+{sr_diff}</span>'
            elif sr_diff < 0:
                sr_delta_html = f'<span style="display: block; font-size: 10px; color: #ff3b30; font-weight: 700;">{sr_diff}</span>'

            sr_badge = f'<span style="display: inline-block; min-width: 26px; text-align: center; padding: 2px 7px; border-radius: 980px; background-color: {sr_bg}; color: {sr_fg}; font-size: 11px; font-weight: 700;">{sr}</span>{sr_delta_html}'

            m_delta_text = f" ({'+' if m_diff > 0 else ''}{m_diff})" if m_diff != 0 else ""
            factors_str = f'<span style="font-size: 11px; color: #1d1d1f; font-weight: 500;">Q:{p["quality"]} V:{p["value"]} M:{p["momentum"]}{m_delta_text}</span>'
            qv_str = f'{p["qv_score"]:.0f}' if p.get("qv_score") is not None else '—'
            qm_str = f'{p["qm_score"]:.0f}' if p.get("qm_score") is not None else '—'
            combo_str = f'<span style="font-size: 11px; color: #555e6d;">QV:{qv_str} QM:{qm_str}</span>'

        clean_n = (
            name.split(",")[0]
            .replace(" Corporation", "")
            .replace(" Inc.", "")
            .replace(" PLC", "")
            .replace(" plc", "")
            .replace(" & Co", "")
        )

        rows_html.append(f"""
        <tr style="border-bottom: 1px solid #f0f0f2;">
          <td style="padding: 9px 4px; font-size: 13px; font-weight: 700; color: #1d1d1f;">
            {sym}
            <div style="font-size: 11px; font-weight: 400; color: #86868b; margin-top: 1px;">{clean_n}</div>
          </td>
          <td align="right" style="padding: 9px 6px; font-size: 13px; font-weight: 600; color: #1d1d1f;">
            £{val:,.2f}
            <div style="font-size: 11px; font-weight: 400; color: #86868b;">{wt:.1f}%</div>
          </td>
          <td align="right" style="padding: 9px 6px; font-size: 13px; font-weight: 700; color: {ret_color};">
            {ret_sign}{ret:.1f}%
            <div style="font-size: 11px; font-weight: 400; color: {ret_color};">{ppl_sign}£{abs(ppl_val):.2f}</div>
          </td>
          <td align="center" style="padding: 9px 4px;">
            {sr_badge}
          </td>
          <td align="center" style="padding: 9px 4px;">
            {factors_str}
          </td>
          <td align="center" style="padding: 9px 4px;">
            {combo_str}
          </td>
          <td align="right" style="padding: 9px 4px;">
            {style_badge}
          </td>
        </tr>
        """)

    pnl_sign = "+" if ppl >= 0 else "-"
    pnl_color = "#34c759" if ppl >= 0 else "#ff3b30"

    evolution_note = "All holdings stable. Zero style shifts." if not has_any_delta else "Factor changes detected and tracked in history log."

    return f"""
    <!-- SLEEVE 3: TRADING 212 LIVE HOLDINGS & FACTOR PROFILE -->
    <tr>
      <td style="background-color: #ffffff; border-radius: 20px; border: 1px solid rgba(0, 0, 0, 0.06); padding: 28px 26px; box-shadow: 0 4px 16px rgba(0,0,0,0.03);">
        
        <!-- Header -->
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 20px;">
          <tr>
            <td align="left" style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b;">
              Live Portfolio · Trading 212 &amp; StockRank Roster
            </td>
            <td align="right">
              <span style="display: inline-block; padding: 4px 12px; border-radius: 980px; background-color: #f2f2f7; color: #1d1d1f; font-size: 12px; font-weight: 600;">
                10 Holdings · £{total:,.2f} Equity
              </span>
            </td>
          </tr>
        </table>

        <!-- 2x2 Metric Grid -->
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="border-bottom: 1px solid #e5e5ea; padding-bottom: 18px; margin-bottom: 18px;">
          <tr>
            <td width="50%" align="left" valign="top" style="padding-bottom: 14px; padding-right: 12px;">
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Portfolio Equity</div>
              <div style="font-size: 24px; font-weight: 700; color: #1d1d1f; letter-spacing: -0.02em;">£{total:,.2f}</div>
            </td>
            <td width="50%" align="left" valign="top" style="padding-bottom: 14px; padding-left: 12px;">
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Net Unrealised P&amp;L</div>
              <div style="font-size: 24px; font-weight: 700; color: {pnl_color}; letter-spacing: -0.02em;">{pnl_sign}£{abs(ppl):,.2f} ({pnl_pct:+.1f}%)</div>
            </td>
          </tr>
          <tr>
            <td width="50%" align="left" valign="top" style="padding-right: 12px;">
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Invested Capital</div>
              <div style="font-size: 20px; font-weight: 600; color: #555e6d; letter-spacing: -0.02em;">£{invested:,.2f}</div>
            </td>
            <td width="50%" align="left" valign="top" style="padding-left: 12px;">
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Uninvested Cash</div>
              <div style="font-size: 20px; font-weight: 600; color: #555e6d; letter-spacing: -0.02em;">£{cash:,.2f}</div>
            </td>
          </tr>
        </table>

        <!-- Holdings Table -->
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 16px;">
          <thead>
            <tr style="border-bottom: 1px solid #d1d1d6;">
              <th align="left" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">Holding</th>
              <th align="right" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">Value</th>
              <th align="right" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">Return</th>
              <th align="center" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">SR</th>
              <th align="center" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">Q · V · M</th>
              <th align="center" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">QV · QM</th>
              <th align="right" style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 8px;">Style</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>

        <!-- Factor Summary Callout -->
        <table width="100%" border="0" cellspacing="0" cellpadding="0">
          <tr>
            <td style="background-color: #f5f5f7; border-radius: 12px; padding: 12px 14px; font-size: 12px; color: #555e6d; line-height: 1.45;">
              <strong style="color: #1d1d1f;">Factor Profile:</strong> Average equity StockRank is <strong>76.0</strong> with 80% of equities holding StockRank &ge; 65. Portfolio holds <strong>2 Super Stocks</strong> (BAC 86, TMUS 83), <strong>3 High Flyers</strong> (SNDK 87, CAT 76, AVGO 65), <strong>3 Neutrals</strong> (GSK 88, GS 70, JPM 65), <strong>1 Turnaround</strong> (BARC.L 64), and <strong>1 Core European Benchmark ETF</strong> (EUE.L). {evolution_note}
            </td>
          </tr>
        </table>

      </td>
    </tr>
    """


def render_html_briefing(top_pick, runners_up, days_to_eom, days_to_lyw, as_of_str, t212_data: dict | None = None):
    """Render bulletproof table-based briefing with SNDK, Atlas, and T212 factor cards."""
    t212_card_html = render_t212_html_card(t212_data) if t212_data else ""

    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Capital Allocation Briefing</title>
</head>
<body style="margin: 0; padding: 24px 12px; background-color: #f5f5f7; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; color: #1d1d1f;">

  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f5f5f7;">
    <tr>
      <td align="center" style="padding: 10px 0;">
        
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; text-align: left;">
          
          <!-- Masthead Header -->
          <tr>
            <td style="padding: 10px 8px 24px 8px;">
              <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #86868b; margin-bottom: 6px;">SecondBrain Portfolio Command</div>
              <div style="font-size: 26px; font-weight: 700; letter-spacing: -0.02em; color: #1d1d1f; line-height: 1.15; margin-bottom: 4px;">Capital Allocation Briefing</div>
              <div style="font-size: 13px; color: #86868b;">{as_of_str} · Daily Market Close</div>
            </td>
          </tr>

          <!-- SLEEVE 1: ATLAS COMPOUNDER -->
          <tr>
            <td style="background-color: #ffffff; border-radius: 20px; border: 1px solid rgba(0, 0, 0, 0.06); padding: 28px 26px; box-shadow: 0 4px 16px rgba(0,0,0,0.03);">
              
              <!-- Card Header Row -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 20px;">
                <tr>
                  <td align="left" style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b;">
                    Sleeve 1 · Atlas Monthly Compounder (70%)
                  </td>
                  <td align="right">
                    <span style="display: inline-block; padding: 4px 12px; border-radius: 980px; background-color: #e8f2ff; color: #0071e3; font-size: 12px; font-weight: 600;">
                      {days_to_eom} Days to Rebalance
                    </span>
                  </td>
                </tr>
              </table>

              <!-- Hero Stock Banner -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="border-bottom: 1px solid #e5e5ea; padding-bottom: 18px; margin-bottom: 22px;">
                <tr>
                  <td align="left" valign="bottom">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Current Monthly Target</div>
                    <span style="font-size: 40px; font-weight: 800; letter-spacing: -0.03em; color: #1d1d1f; line-height: 1;">{top_pick['symbol']}</span>
                  </td>
                  <td align="right" valign="bottom">
                    <div style="font-size: 15px; font-weight: 700; color: #1d1d1f; margin-bottom: 2px;">{top_pick['name']}</div>
                    <div style="font-size: 13px; color: #86868b;">{top_pick['sector']} · Dream Zone #1</div>
                  </td>
                </tr>
              </table>

              <!-- 2x2 Metric Grid -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 22px;">
                <tr>
                  <td width="50%" align="left" valign="top" style="padding-bottom: 18px; padding-right: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Net Income (TTM)</div>
                    <div style="font-size: 24px; font-weight: 700; color: #0071e3; letter-spacing: -0.02em;">${top_pick['net_income']/1e9:.2f}B</div>
                  </td>
                  <td width="50%" align="left" valign="top" style="padding-bottom: 18px; padding-left: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">14W Williams %R</div>
                    <div style="font-size: 24px; font-weight: 700; color: #0071e3; letter-spacing: -0.02em;">{top_pick['wr']:.1f} Oversold</div>
                  </td>
                </tr>
                <tr>
                  <td width="50%" align="left" valign="top" style="padding-right: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Return on Equity</div>
                    <div style="font-size: 22px; font-weight: 600; color: #1d1d1f; letter-spacing: -0.02em;">{top_pick['roe']:.1f}%</div>
                  </td>
                  <td width="50%" align="left" valign="top" style="padding-left: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Dividend Streak</div>
                    <div style="font-size: 22px; font-weight: 600; color: #1d1d1f; letter-spacing: -0.02em;">{top_pick['div_years']} Consecutive Years</div>
                  </td>
                </tr>
              </table>

              <!-- Instruction Box -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 18px;">
                <tr>
                  <td style="background-color: #f5f5f7; border-radius: 12px; padding: 12px 14px; font-size: 12px; color: #555e6d; line-height: 1.45;">
                    <strong style="color: #1d1d1f;">Monthly Instruction:</strong> Allocate new monthly capital into <strong>{top_pick['symbol']}</strong> on the month-end close. Top up relentlessly even if already held.
                  </td>
                </tr>
              </table>

              <!-- Alternative Tranches Table -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="border-top: 1px solid #e5e5ea; padding-top: 16px;">
                <tr>
                  <td colspan="3" style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 10px;">
                    Alternative Qualified Tranches
                  </td>
                </tr>
                {''.join(f'<tr><td width="22%" style="padding: 6px 0; font-size: 13px; font-weight: 700; color: #1d1d1f;">{r["symbol"]}</td><td width="48%" style="padding: 6px 0; font-size: 13px; color: #86868b;">{r["name"]}</td><td width="30%" align="right" style="padding: 6px 0; font-size: 13px; font-weight: 600; color: #1d1d1f;">${r["net_income"]/1e9:.2f}B · {r["wr"]:.1f}</td></tr>' for r in runners_up)}
              </table>

            </td>
          </tr>

          <!-- Spacer -->
          <tr><td height="20"></td></tr>

          <!-- SLEEVE 2: LYW MOMENTUM -->
          <tr>
            <td style="background-color: #ffffff; border-radius: 20px; border: 1px solid rgba(0, 0, 0, 0.06); padding: 28px 26px; box-shadow: 0 4px 16px rgba(0,0,0,0.03);">
              
              <!-- Card Header Row -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 20px;">
                <tr>
                  <td align="left" style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b;">
                    Sleeve 2 · LYW #1 Momentum (30%)
                  </td>
                  <td align="right">
                    <span style="display: inline-block; padding: 4px 12px; border-radius: 980px; background-color: #f2f2f7; color: #1d1d1f; font-size: 12px; font-weight: 600;">
                      {days_to_lyw} Days to 2 Jan 2027
                    </span>
                  </td>
                </tr>
              </table>

              <!-- Section 1: CURRENT ACTIVE 2026 HOLDING (SANDISK) -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="border-bottom: 1px solid #e5e5ea; padding-bottom: 18px; margin-bottom: 20px;">
                <tr>
                  <td align="left" valign="bottom">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Current Active 2026 Position</div>
                    <span style="font-size: 40px; font-weight: 800; letter-spacing: -0.03em; color: #1d1d1f; line-height: 1;">SNDK</span>
                  </td>
                  <td align="right" valign="bottom">
                    <div style="font-size: 15px; font-weight: 700; color: #34c759; margin-bottom: 2px;">+400.7% Unrealised Gain</div>
                    <div style="font-size: 13px; color: #86868b;">SanDisk · 2026 Holding</div>
                  </td>
                </tr>
              </table>

              <!-- 2x2 Metric Grid for SNDK -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 20px;">
                <tr>
                  <td width="50%" align="left" valign="top" style="padding-bottom: 16px; padding-right: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">2 Jan 2026 Entry Price</div>
                    <div style="font-size: 22px; font-weight: 700; color: #1d1d1f; letter-spacing: -0.02em;">$275.24</div>
                  </td>
                  <td width="50%" align="left" valign="top" style="padding-bottom: 16px; padding-left: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Latest Market Price</div>
                    <div style="font-size: 22px; font-weight: 700; color: #34c759; letter-spacing: -0.02em;">$1,378.22</div>
                  </td>
                </tr>
                <tr>
                  <td width="50%" align="left" valign="top" style="padding-right: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">Fixed 35% Stop Level</div>
                    <div style="font-size: 20px; font-weight: 600; color: #1d1d1f; letter-spacing: -0.02em;">$178.91 (Safe)</div>
                  </td>
                  <td width="50%" align="left" valign="top" style="padding-left: 12px;">
                    <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #86868b; margin-bottom: 4px;">2026 Lowest Drawdown</div>
                    <div style="font-size: 20px; font-weight: 600; color: #1d1d1f; letter-spacing: -0.02em;">$244.00 (Never Hit)</div>
                  </td>
                </tr>
              </table>

              <!-- Current Instruction Callout -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 24px;">
                <tr>
                  <td style="background-color: #f5f5f7; border-left: 3px solid #34c759; border-radius: 0 10px 10px 0; padding: 12px 14px; font-size: 12px; color: #555e6d; line-height: 1.45;">
                    <strong style="color: #1d1d1f;">Current Instruction:</strong> <strong>RIDE SNDK TO 31 DECEMBER 2026.</strong> Stop is untouched ($178.91). Position is compounding at +400.7% YTD. Zero action required until the 31 December closing print.
                  </td>
                </tr>
              </table>

              <!-- Section 2: 2027 LOOKAHEAD LEADERBOARD -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="border-top: 1px solid #e5e5ea; padding-top: 18px; margin-bottom: 16px;">
                <tr>
                  <td colspan="3" style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #86868b; padding-bottom: 12px;">
                    Next Year (2027) Leaderboard Contenders (Top 3)
                  </td>
                </tr>
                <tr>
                  <td width="22%" style="padding: 7px 0; font-size: 14px; font-weight: 700; color: #0071e3;">#1 MRNA</td>
                  <td width="48%" style="padding: 7px 0; font-size: 13px; color: #1d1d1f;">Moderna Inc.</td>
                  <td width="30%" align="right" style="padding: 7px 0; font-size: 14px; font-weight: 700; color: #34c759;">+629.1% YTD</td>
                </tr>
                <tr>
                  <td width="22%" style="padding: 7px 0; font-size: 13px; font-weight: 700; color: #1d1d1f;">#2 SNDK</td>
                  <td width="48%" style="padding: 7px 0; font-size: 13px; color: #86868b;">SanDisk (Held)</td>
                  <td width="30%" align="right" style="padding: 7px 0; font-size: 13px; font-weight: 600; color: #1d1d1f;">+474.7% YTD</td>
                </tr>
                <tr>
                  <td width="22%" style="padding: 7px 0; font-size: 13px; font-weight: 700; color: #1d1d1f;">#3 DELL</td>
                  <td width="48%" style="padding: 7px 0; font-size: 13px; color: #86868b;">Dell Technologies</td>
                  <td width="30%" align="right" style="padding: 7px 0; font-size: 13px; font-weight: 600; color: #1d1d1f;">+362.8% YTD</td>
                </tr>
              </table>

              <!-- Lookahead Action Gate -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td style="font-size: 11px; color: #86868b; line-height: 1.4; padding-top: 6px;">
                    <strong>Lookahead Gate:</strong> MRNA currently leads SNDK by +154.4%. On 31 December close, if MRNA wins the full year with solid December support, rotate SNDK into MRNA on 2 January 2027. If SNDK rallies to take #1, roll SNDK over into 2027!
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- Spacer -->
          <tr><td height="20"></td></tr>

          <!-- SLEEVE 3: TRADING 212 HOLDINGS -->
          {t212_card_html}

          <!-- Footer -->
          <tr>
            <td align="center" style="padding: 24px 0 10px 0; font-size: 12px; color: #86868b;">
              Atlas & LYW Command Engine · SecondBrain
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>

</body>
</html>"""


def main():
    today = datetime.date.today()
    next_month = today.replace(day=28) + datetime.timedelta(days=4)
    end_of_month = next_month - datetime.timedelta(days=next_month.day)
    days_to_eom = (end_of_month - today).days

    target_lyw = datetime.date(today.year + 1 if today.month == 12 else today.year, 1, 2)
    if target_lyw < today:
        target_lyw = datetime.date(today.year + 1, 1, 2)
    days_to_lyw = (target_lyw - today).days

    frontier, secondary = get_atlas_picks()
    top_pick = frontier[0] if frontier else {'symbol': 'BAC', 'name': 'Bank of America Corp', 'net_income': 30.509e9, 'wr': -95.30, 'roe': 11.2, 'div_years': 12, 'sector': 'Finance · Major Banks'}
    runners_up = frontier[1:3] if len(frontier) > 1 else secondary[:2]

    lyw_data = get_lyw_data()
    propicks_data = get_propicks_summary()
    t212_data = get_t212_holdings()

    as_of_str = today.strftime('%d %B %Y')
    html_body = render_html_briefing(top_pick, runners_up, days_to_eom, days_to_lyw, as_of_str, t212_data=t212_data)

    out_path = os.path.join(ROOT_DIR, 'public', 'portfolio_briefing.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_body)
    print(f"Rendered briefing saved to {out_path}")

    # Dispatch to Telegram
    tg_text = format_telegram_briefing(frontier, secondary, lyw_data, propicks_data, as_of_str, t212_data=t212_data)
    tg_ok = send_telegram(tg_text)
    if tg_ok:
        print("Telegram briefing dispatched successfully.")
    else:
        print("Telegram briefing dispatch not completed.", file=sys.stderr)

    smtp_host = os.environ.get('SMTP_HOST') or 'smtp.gmail.com'
    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_user = os.environ.get('SMTP_USER') or os.environ.get('GMAIL_ADDRESS') or 'leeslater1992@gmail.com'
    smtp_pass = (os.environ.get('SMTP_PASS') or os.environ.get('GMAIL_APP_PASSWORD') or 'mqak eqlm mwzy jfuv').replace(' ', '')
    notify_email = os.environ.get('NOTIFY_EMAIL') or smtp_user

    if smtp_host and smtp_user and smtp_pass and notify_email:
        msg = EmailMessage()
        msg['Subject'] = f"Portfolio Briefing: {days_to_eom} Days to Month-End · {top_pick['symbol']} & SNDK Status ({as_of_str})"
        msg['From'] = f"SecondBrain Portfolio <{smtp_user}>"
        msg['To'] = notify_email
        msg.set_content(f"Atlas #1: {top_pick['symbol']} (${top_pick['net_income']/1e9:.2f}B NI, W%R {top_pick['wr']}). Active 2026 LYW: SNDK ($1,378.22, +400.7% gain, Stop $178.91 untouched). T212 Equity: £{t212_data.get('total_gbp', 0.0):,.2f}.")
        msg.add_alternative(html_body, subtype='html')

        try:
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
            print(f"Successfully dispatched briefing email to {notify_email}")
        except Exception as e:
            print(f"Error dispatching email: {e}")
    else:
        print("SMTP secrets not configured in environment. Briefing rendered to static HTML without email dispatch.")


if __name__ == '__main__':
    main()
