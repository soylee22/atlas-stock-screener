"""Daily 2500-trading-day SD channel scan with an email summary.

Port of the Pine indicator "Fixed 2500-Trading-Day Std Dev Channel" (daily chart):
window = bars within 2500 * 365.25 / 252 calendar days of the last bar,
least-squares line of close on time, residual SD with n - 1,
SD position z = (last close - line value at last bar) / SD.

Universe: sd_channel/universe.csv (stocks over $50bn and Atlas ETFs, frozen 2026-11-17).
State: sd_channel/state.json holds the previous run's z values, to report band crossings.

Env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL. Without them the
email HTML is written to sd_channel/latest.html and nothing is sent.
Run: python sd_channel/daily.py [--no-email]
"""
import argparse
import json
import math
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
CAL_DAYS = 2500 * 365.25 / 252.0
MIN_R = 0.7
MIN_AUM = 0.5
PAGE = "https://soylee22.github.io/sd-channel-scanner/"
# Band edges the indicator draws. A crossing is a move from one side of an edge to the other.
EDGES = [(-4, "-4 SD"), (-3, "-3 SD"), (1, "+1 SD"), (2, "+2 SD")]


def channel(close: pd.Series) -> dict | None:
    close = close.dropna()
    if len(close) < 250:
        return None
    cutoff = close.index[-1] - pd.Timedelta(milliseconds=round(CAL_DAYS * 86400000.0))
    sub = close[close.index >= cutoff]
    win = sub.to_numpy(dtype=float)
    n = len(win)
    years = (sub.index[-1] - sub.index[0]).days / 365.25
    if n < 2 or years < 9.5:
        return None
    ratio = win[1:] / win[:-1]
    if max(ratio.max(), 1 / ratio.min()) > 3:
        return None  # Yahoo unit break (pence/pound flip).
    t = np.arange(n, dtype=float)
    b, a = np.polyfit(t, win, 1)
    fitted = a + b * t
    sd = math.sqrt(((win - fitted) ** 2).sum() / (n - 1))
    if sd <= 0:
        return None
    return {"z": (win[-1] - fitted[-1]) / sd, "R": float(np.corrcoef(t, win)[0, 1]),
            "close": float(win[-1]), "line": float(fitted[-1]), "sd": float(sd),
            "asof": sub.index[-1].date().isoformat()}


def fetch(tickers: list[str], batch: int = 100) -> dict[str, pd.Series]:
    out = {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        df = yf.download(chunk, period="11y", interval="1d", auto_adjust=False,
                         progress=False, group_by="ticker", threads=True)
        for tk in chunk:
            try:
                s = df[tk]["Close"]
                out[tk] = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
            except KeyError:
                pass
        print(f"fetched {min(i + batch, len(tickers))}/{len(tickers)}", file=sys.stderr)
    return out


def crossing(prev: float | None, now: float) -> str | None:
    if prev is None:
        return None
    for edge, label in EDGES:
        if prev >= edge > now:
            return f"fell below {label}"
        if prev < edge <= now:
            return f"rose above {label}"
    return None


def fmt_z(z: float) -> str:
    return f"{z:+.2f}"


def colour(z: float) -> str:
    if z >= 2: return "#2f66d0"
    if z >= 1: return "#0b8fae"
    if z <= -3: return "#a3163a"
    if z <= -1: return "#d4355a"
    return "#17202b"


def table(df: pd.DataFrame, size_label: str) -> str:
    head = ("<tr>" + "".join(f'<th style="text-align:{al};padding:4px 8px;border-bottom:1px solid #dde2e9;'
            f'font-size:11px;color:#5d6878;text-transform:uppercase">{h}</th>'
            for h, al in [("Ticker", "left"), ("Name", "left"), (size_label, "right"),
                          ("SD", "right"), ("1d", "right"), ("R", "right")]) + "</tr>")
    rows = []
    for r in df.itertuples():
        dz = "" if pd.isna(r.dz) else f"{r.dz:+.2f}"
        size = "" if pd.isna(r.size_usd_bn) else f"{r.size_usd_bn:,.1f}"
        rows.append(
            f'<tr><td style="padding:4px 8px;font-family:Menlo,monospace">{escape(r.ticker)}</td>'
            f'<td style="padding:4px 8px">{escape(str(r.name)[:42])}</td>'
            f'<td style="padding:4px 8px;text-align:right">{size}</td>'
            f'<td style="padding:4px 8px;text-align:right;font-weight:600;color:{colour(r.z)}">{fmt_z(r.z)}</td>'
            f'<td style="padding:4px 8px;text-align:right;color:#5d6878">{dz}</td>'
            f'<td style="padding:4px 8px;text-align:right;color:#5d6878">{r.R:.2f}</td></tr>')
    return f'<table style="border-collapse:collapse;font-size:13px;width:100%">{head}{"".join(rows)}</table>'


def zone_counts(df: pd.DataFrame) -> str:
    z = df.z
    parts = [("above +2", (z >= 2).sum()), ("+1 to +2", ((z >= 1) & (z < 2)).sum()),
             ("-1 to +1", ((z > -1) & (z < 1)).sum()), ("-1 to -3", ((z <= -1) & (z > -3)).sum()),
             ("-3 to -4", ((z <= -3) & (z > -4)).sum()), ("below -4", (z <= -4).sum())]
    return " · ".join(f"{k} {v}" for k, v in parts)


def build_html(df: pd.DataFrame, asof: str, missing: int) -> tuple[str, str]:
    good = df[df.R >= MIN_R]
    s = good[good.kind == "stock"].sort_values("z")
    e = good[(good.kind == "etf") & (good.size_usd_bn >= MIN_AUM)].sort_values("z")
    cross = df[df.cross.notna()].sort_values("z")
    h2 = 'style="font-size:16px;margin:22px 0 6px"'
    cross_html = ("<p>No band crossings since the last run.</p>" if cross.empty else
                  "<ul style='padding-left:18px;margin:4px 0'>" + "".join(
                      f"<li><b>{escape(r.ticker)}</b> {escape(str(r.name)[:40])} {r.cross} "
                      f"(<span style='color:{colour(r.z)}'>{fmt_z(r.z)}</span>, R {r.R:.2f})</li>"
                      for r in cross.itertuples()) + "</ul>")
    lowest = s.iloc[0]
    subject = (f"SD channel {asof}: {len(cross)} crossings · lowest stock {lowest.ticker} {fmt_z(lowest.z)}")
    body = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#17202b;max-width:720px">
<h1 style="font-size:20px;margin:0 0 4px">2500-day SD channel, {asof}</h1>
<p style="color:#5d6878;margin:0 0 12px">SD position = (close − 10-year regression line) ÷ residual SD. Bands: +1, +2, −3, −4. Tables show Pearson R ≥ {MIN_R} only.</p>
<p style="margin:0 0 16px"><a href="{PAGE}" style="display:inline-block;background:#17202b;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;padding:8px 14px;border-radius:6px">Open live interactive rankings</a></p>
<h2 {h2}>Band crossings since last run</h2>{cross_html}
<p style="font-size:13px;color:#5d6878">Stocks: {zone_counts(s)}<br>ETFs: {zone_counts(e)}</p>
<h2 {h2}>Stocks furthest below the line</h2>{table(s.head(15), "Cap $bn")}
<h2 {h2}>ETFs furthest below the line (AUM ≥ {MIN_AUM}bn)</h2>{table(e.head(10), "AUM bn")}
<h2 {h2}>Stocks furthest above the line</h2>{table(s.tail(10).iloc[::-1], "Cap $bn")}
<p style="font-size:12px;color:#5d6878;margin-top:20px">{len(df)} lines scanned, {missing} without usable history today. Full searchable table and CSV export: <a href="{PAGE}">{PAGE}</a>.</p>
</div>"""
    return subject, body


def build_site_json(df: pd.DataFrame, asof: str, missing: int) -> dict:
    def num(v, digits=4):
        return None if pd.isna(v) else round(float(v), digits)

    ordered = df.sort_values("z")
    rows = []
    for r in ordered.itertuples():
        cur = "" if pd.isna(getattr(r, "aum_currency", None)) else str(r.aum_currency)
        cross = "" if pd.isna(r.cross) or not r.cross else str(r.cross)
        rows.append([
            str(r.ticker),
            str(r.name),
            "s" if r.kind == "stock" else "e",
            "" if pd.isna(r.region) else str(r.region),
            num(r.size_usd_bn, 2),
            num(r.z, 3),
            num(r.dz, 3),
            num(r.R, 3),
            num(r.close, 4),
            num(r.line, 4),
            num(getattr(r, "sd", np.nan), 4),
            cur,
            cross,
        ])
    cross_df = ordered[ordered.cross.notna()]
    crossings = [
        [str(r.ticker), str(r.name), "s" if r.kind == "stock" else "e",
         num(r.z, 3), num(r.dz, 3), num(r.R, 3), str(r.cross)]
        for r in cross_df.itertuples()
    ]
    return {
        "asof": asof,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scanned": len(rows),
        "missing": int(missing),
        "crossings": crossings,
        "rows": rows,
    }


def send(subject: str, html: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pw = (os.environ.get("SMTP_PASS") or "").replace(" ", "")
    to = os.environ.get("NOTIFY_EMAIL") or user
    if not (host and user and pw and to):
        print("SMTP environment not set. Email not sent.")
        return False
    port = int(os.environ.get("SMTP_PORT") or 465)
    msg = MIMEMultipart()
    msg["Subject"], msg["From"], msg["To"] = subject, f"SD Channel Scan <{user}>", to
    msg.attach(MIMEText(html, "html", "utf-8"))
    if port == 465:
        with smtplib.SMTP_SSL(host, port) as srv:
            srv.login(user, pw)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as srv:
            srv.starttls()
            srv.login(user, pw)
            srv.send_message(msg)
    print(f"Email sent: {subject}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()

    uni = pd.read_csv(HERE / "universe.csv")
    state_path = HERE / "state.json"
    prev = json.loads(state_path.read_text())["z"] if state_path.exists() else {}
    series = fetch(uni.ticker.tolist())

    recs = []
    for row in uni.to_dict("records"):
        s = series.get(row["ticker"])
        res = channel(s) if s is not None else None
        if res:
            row.update(res)
            p = prev.get(row["ticker"])
            row["dz"] = res["z"] - p if p is not None else np.nan
            row["cross"] = crossing(p, res["z"]) if row["kind"] == "stock" or (row["size_usd_bn"] or 0) >= MIN_AUM else None
            if res["R"] < MIN_R:
                row["cross"] = None
            recs.append(row)
    df = pd.DataFrame(recs)
    asof = df["asof"].max()
    missing = len(uni) - len(df)

    subject, html = build_html(df, asof, missing)
    (HERE / "latest.html").write_text(html)
    out = df.sort_values("z")[["ticker", "name", "kind", "region", "size_usd_bn", "z", "dz", "R",
                               "close", "line", "asof", "cross"]]
    csv_bytes = out.round(4).to_csv(index=False).encode()
    (HERE / "latest.csv").write_bytes(csv_bytes)
    site_data = build_site_json(df, asof, missing)
    (HERE / "data.json").write_text(json.dumps(site_data, separators=(",", ":")))
    # Keep last-known values for lines that failed today, so a one-day gap does not fake a crossing later.
    new_state = dict(prev)
    new_state.update({r.ticker: round(r.z, 4) for r in df.itertuples()})
    state_path.write_text(json.dumps({"asof": asof, "z": new_state}, separators=(",", ":")))
    print(subject)
    if not args.no_email:
        send(subject, html)


if __name__ == "__main__":
    main()
