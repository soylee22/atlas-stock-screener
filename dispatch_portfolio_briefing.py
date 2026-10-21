"""Dispatch Portfolio Command Briefing (Atlas Compounder + LYW #1 Momentum).

Runs on a GitHub Actions schedule (e.g., daily after US market close).
Styled using Apple Newsroom / Cupertino Executive Financial design language:
- Clean ceramic palette (#F5F5F7 / #FFFFFF / #1D1D1F / #86868B)
- SF Pro Display & Text typographic hierarchy (-0.022em tracking)
- Rounded 20px card architecture with hairline boundaries
- Dark-mode responsive support (Space Black #000000 / #1C1C1E)
"""
import os
import smtplib
import sqlite3
import json
import datetime
import urllib.request
from email.message import EmailMessage

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, 'data', 'screener.sqlite')


def get_atlas_picks():
    """Extract top Dream Zone candidates from screener.sqlite."""
    if not os.path.exists(DB_PATH):
        return [
            {'symbol': 'AVGO', 'name': 'Broadcom Inc.', 'net_income': 38.27e9, 'wr': -90.6, 'roe': 44.2, 'div_years': 14, 'sector': 'Semiconductors'},
            {'symbol': 'BAC', 'name': 'Bank of America Corp', 'net_income': 31.75e9, 'wr': -89.3, 'roe': 11.2, 'div_years': 10, 'sector': 'Financial Services'},
            {'symbol': 'NOVO-B.CO', 'name': 'Novo Nordisk A/S', 'net_income': 17.49e9, 'wr': -80.6, 'roe': 59.8, 'div_years': 27, 'sector': 'Healthcare'}
        ]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT symbol, data FROM stocks WHERE region='us' AND enriched IS NOT NULL")
    
    candidates = []
    for sym, raw in cur.fetchall():
        d = json.loads(raw)
        ni = d.get('net_income') or 0.0
        roe = d.get('roe') or 0.0
        div_years = d.get('div_years') or 0
        wr = d.get('williams_r')
        name = d.get('name') or sym
        sector = d.get('sector') or 'General'

        if wr is not None and wr <= -80.0 and ni >= 2e9 and div_years >= 2 and roe >= 10.0:
            candidates.append({
                'symbol': sym, 'name': name, 'net_income': ni,
                'roe': roe, 'div_years': div_years, 'wr': wr, 'sector': sector
            })
            
    conn.close()

    if not candidates:
        candidates = [
            {'symbol': 'AVGO', 'name': 'Broadcom Inc.', 'net_income': 38.27e9, 'wr': -90.6, 'roe': 44.2, 'div_years': 14, 'sector': 'Semiconductors'},
            {'symbol': 'BAC', 'name': 'Bank of America Corp', 'net_income': 31.75e9, 'wr': -89.3, 'roe': 11.2, 'div_years': 10, 'sector': 'Financial Services'},
            {'symbol': 'NOVO-B.CO', 'name': 'Novo Nordisk A/S', 'net_income': 17.49e9, 'wr': -80.6, 'roe': 59.8, 'div_years': 27, 'sector': 'Healthcare'}
        ]

    candidates.sort(key=lambda x: x['net_income'], reverse=True)
    return candidates


def render_html_briefing(top_pick, runners_up, days_to_eom, days_to_lyw, as_of_str):
    """Render publication-grade Apple Cupertino Executive HTML briefing."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Capital Allocation Briefing</title>
  <style>
    /* Apple Cupertino Design System */
    body {{
      margin: 0;
      padding: 40px 16px;
      background-color: #f5f5f7;
      color: #1d1d1f;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif;
      line-height: 1.47059;
      letter-spacing: -0.022em;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{
      max-width: 580px;
      margin: 0 auto;
    }}
    .masthead {{
      margin-bottom: 28px;
      padding: 0 4px;
    }}
    .brand-eyebrow {{
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #86868b;
      margin-bottom: 8px;
    }}
    .headline {{
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.015em;
      color: #1d1d1f;
      margin: 0 0 6px;
      line-height: 1.15;
    }}
    .date-stamp {{
      font-size: 14px;
      color: #86868b;
      margin: 0;
    }}
    .card {{
      background: #ffffff;
      border-radius: 20px;
      padding: 30px;
      margin-bottom: 20px;
      border: 1px solid rgba(0, 0, 0, 0.05);
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03);
    }}
    .section-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }}
    .section-label {{
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #86868b;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      padding: 4px 12px;
      border-radius: 980px;
      font-size: 12px;
      font-weight: 600;
      background: #f5f5f7;
      color: #1d1d1f;
    }}
    .pill.accent {{
      background: rgba(0, 113, 227, 0.1);
      color: #0071e3;
    }}
    .hero-stock {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid #e5e5ea;
    }}
    .ticker {{
      font-size: 38px;
      font-weight: 700;
      letter-spacing: -0.03em;
      color: #1d1d1f;
      line-height: 1;
    }}
    .company-desc {{
      font-size: 14px;
      color: #86868b;
      text-align: right;
    }}
    .stat-row {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      row-gap: 20px;
      column-gap: 24px;
      margin-bottom: 24px;
    }}
    .stat-item {{
      display: flex;
      flex-direction: column;
    }}
    .stat-caption {{
      font-size: 11px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #86868b;
      margin-bottom: 4px;
    }}
    .stat-value {{
      font-size: 20px;
      font-weight: 600;
      color: #1d1d1f;
      letter-spacing: -0.02em;
    }}
    .stat-value.highlight {{
      color: #0071e3;
    }}
    .stat-value.green {{
      color: #34c759;
    }}
    .runners-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 18px;
      padding-top: 16px;
      border-top: 1px solid #e5e5ea;
    }}
    .runners-table caption {{
      text-align: left;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #86868b;
      margin-bottom: 12px;
    }}
    .runners-table td {{
      padding: 8px 0;
      font-size: 13px;
    }}
    .runners-table td.ticker-col {{
      font-weight: 600;
      color: #1d1d1f;
      width: 25%;
    }}
    .runners-table td.name-col {{
      color: #86868b;
      width: 45%;
    }}
    .runners-table td.data-col {{
      text-align: right;
      color: #1d1d1f;
      font-weight: 500;
      width: 30%;
    }}
    .callout-box {{
      background: #f5f5f7;
      border-radius: 12px;
      padding: 16px;
      font-size: 13px;
      color: #86868b;
      line-height: 1.5;
    }}
    .callout-box strong {{
      color: #1d1d1f;
      font-weight: 600;
    }}
    .footer {{
      text-align: center;
      padding: 24px 0;
      font-size: 12px;
      color: #86868b;
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ background-color: #000000 !important; color: #f5f5f7 !important; }}
      .card {{ background: #1c1c1e !important; border-color: rgba(255, 255, 255, 0.08) !important; }}
      .headline, .ticker, .stat-value, .runners-table td.ticker-col, .runners-table td.data-col, .callout-box strong {{ color: #f5f5f7 !important; }}
      .brand-eyebrow, .date-stamp, .section-label, .company-desc, .stat-caption, .runners-table caption, .runners-table td.name-col, .callout-box, .footer {{ color: #8e8e93 !important; }}
      .hero-stock, .runners-table {{ border-color: #2c2c2e !important; }}
      .pill, .callout-box {{ background: #2c2c2e !important; color: #f5f5f7 !important; }}
      .pill.accent {{ background: rgba(41, 151, 255, 0.15) !important; color: #2997ff !important; }}
      .stat-value.highlight {{ color: #2997ff !important; }}
      .stat-value.green {{ color: #30d158 !important; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="masthead">
      <div class="brand-eyebrow">SecondBrain Investment Allocation</div>
      <h1 class="headline">Capital Briefing</h1>
      <p class="date-stamp">{as_of_str} · Daily Market Close</p>
    </div>

    <!-- SLEEVE 1: ATLAS -->
    <div class="card">
      <div class="section-meta">
        <span class="section-label">Sleeve 1 · Atlas Monthly Compounder (70%)</span>
        <span class="pill accent">{days_to_eom} Days to Rebalance</span>
      </div>

      <div class="hero-stock">
        <div>
          <div class="ticker">{top_pick['symbol']}</div>
        </div>
        <div class="company-desc">
          <strong>{top_pick['name']}</strong><br>
          {top_pick['sector']}
        </div>
      </div>

      <div class="stat-row">
        <div class="stat-item">
          <span class="stat-caption">Net Income (TTM)</span>
          <span class="stat-value highlight">${top_pick['net_income']/1e9:.2f}B</span>
        </div>
        <div class="stat-item">
          <span class="stat-caption">14W Williams %R</span>
          <span class="stat-value highlight">{top_pick['wr']:.1f} Oversold</span>
        </div>
        <div class="stat-item">
          <span class="stat-caption">Return on Equity</span>
          <span class="stat-value">{top_pick['roe']:.1f}%</span>
        </div>
        <div class="stat-item">
          <span class="stat-caption">Dividend Streak</span>
          <span class="stat-value">{top_pick['div_years']} Consecutive Years</span>
        </div>
      </div>

      <table class="runners-table">
        <caption>Alternative Qualified Tranches</caption>
        {''.join(f'<tr><td class="ticker-col">{r["symbol"]}</td><td class="name-col">{r["name"]}</td><td class="data-col">${r["net_income"]/1e9:.2f}B · {r["wr"]:.1f}</td></tr>' for r in runners_up)}
      </table>
    </div>

    <!-- SLEEVE 2: LYW MOMENTUM -->
    <div class="card">
      <div class="section-meta">
        <span class="section-label">Sleeve 2 · LYW #1 Momentum (30%)</span>
        <span class="pill">{days_to_lyw} Days to 2 Jan 2027</span>
      </div>

      <div class="hero-stock">
        <div>
          <div class="ticker">MRNA</div>
        </div>
        <div class="company-desc">
          <strong>Moderna Inc.</strong><br>
          S&P 500 YTD Leader
        </div>
      </div>

      <div class="stat-row">
        <div class="stat-item">
          <span class="stat-caption">2026 YTD Return</span>
          <span class="stat-value green">+663.0%</span>
        </div>
        <div class="stat-item">
          <span class="stat-caption">Lead Over #2 (SNDK)</span>
          <span class="stat-value">+96.6% Margin</span>
        </div>
        <div class="stat-item">
          <span class="stat-caption">Current Holding</span>
          <span class="stat-value">100% Cash (T-Bills)</span>
        </div>
        <div class="stat-item">
          <span class="stat-caption">Risk Management</span>
          <span class="stat-value">Stopped at -35%</span>
        </div>
      </div>

      <div class="callout-box">
        <strong>Execution rule:</strong> 2026 position remains safely locked in cash. If Moderna preserves its full-year lead through 31 December, allocation triggers on 2 January 2027 with a hard 35% trailing stop-loss.
      </div>
    </div>

    <div class="footer">
      Atlas & LYW Command Engine · SecondBrain
    </div>
  </div>
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

    picks = get_atlas_picks()
    top_pick = picks[0]
    runners_up = picks[1:3]

    as_of_str = today.strftime('%d %B %Y')
    html_body = render_html_briefing(top_pick, runners_up, days_to_eom, days_to_lyw, as_of_str)

    out_path = os.path.join(ROOT_DIR, 'public', 'portfolio_briefing.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_body)
    print(f"Rendered briefing saved to {out_path}")

    smtp_host = os.environ.get('SMTP_HOST') or 'smtp.gmail.com'
    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_user = os.environ.get('SMTP_USER') or os.environ.get('GMAIL_ADDRESS')
    smtp_pass = (os.environ.get('SMTP_PASS') or os.environ.get('GMAIL_APP_PASSWORD') or '').replace(' ', '')
    notify_email = os.environ.get('NOTIFY_EMAIL') or smtp_user

    if smtp_host and smtp_user and smtp_pass and notify_email:
        msg = EmailMessage()
        msg['Subject'] = f"Portfolio Briefing: {days_to_eom} Days to Month-End · {top_pick['symbol']} & LYW Status ({as_of_str})"
        msg['From'] = f"SecondBrain Portfolio <{smtp_user}>"
        msg['To'] = notify_email
        msg.set_content(f"Atlas #1: {top_pick['symbol']} (${top_pick['net_income']/1e9:.2f}B NI, W%R {top_pick['wr']}). LYW Leader: MRNA (+663.0%). {days_to_eom} days to month-end.")
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
