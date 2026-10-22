"""Build an expanded, institutional ETF catalogue from Trading 212 enriched with live TradingView AUM.

Filters:
1. Tradeable on Trading 212 across London (GBP/GBX/USD), Xetra (EUR), and Euronext Paris (EUR).
2. AUM floor >= $100M USD to eliminate illiquid micro-caps and closure risks.
3. Deduplicated by ISIN prioritizing London lines, adding unique European lines.
4. Preserves verified currency and price units for established baseline funds.
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLD_CSV = ROOT / 'seed' / 'etfs-2026-10-09.csv'
OUT_CSV = ROOT / 'seed' / 'etfs-2026-10-23.csv'
T212_CACHE = Path('/tmp/t212_instruments.json')
if not T212_CACHE.exists():
    T212_CACHE = Path('/Users/leeslater/SecondBrain/private/t212_instruments.json')


def is_london(item: dict) -> bool:
    t = item.get('ticker', '')
    return (t.endswith('l_EQ') or 
            item.get('workingScheduleId') in (70, 154) or 
            (item.get('currencyCode') in ('GBX', 'GBP') and not t.endswith('d_EQ') and not t.endswith('p_EQ')))


def is_xetra(item: dict) -> bool:
    t = item.get('ticker', '')
    return t.endswith('d_EQ') or (item.get('workingScheduleId') == 166 and item.get('currencyCode') == 'EUR')


def is_paris(item: dict) -> bool:
    t = item.get('ticker', '')
    return t.endswith('p_EQ')


def fetch_tv_market(market: str, aum_min: float = 100_000_000) -> list[dict]:
    url = f'https://scanner.tradingview.com/{market}/scan'
    all_data = []
    step = 1000
    start = 0
    while True:
        payload = {
            'filter': [
                {'left': 'typespecs', 'operation': 'has', 'right': ['etf']},
                {'left': 'aum', 'operation': 'egreater', 'right': aum_min}
            ],
            'options': {'lang': 'en'},
            'symbols': {'query': {'types': []}, 'tickers': []},
            'columns': [
                'name', 'description', 'close', 'currency', 'change', 'Value.Traded',
                'relative_volume_10d_calc', 'aum', 'expense_ratio', 'typespecs',
                'Perf.3Y', 'total_shares_outstanding'
            ],
            'sort': {'sortBy': 'aum', 'sortOrder': 'desc'},
            'range': [start, start + step]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            res = json.loads(resp.read().decode())
            batch = res.get('data', [])
            all_data.extend(batch)
            total = res.get('totalCount', 0)
            if len(all_data) >= total or not batch:
                break
            start += step
            time.sleep(0.2)
    return all_data


def classify_fund(name: str, existing_row: dict | None = None) -> tuple[str, str]:
    if existing_row and existing_row.get('Asset class') and existing_row.get('Focus'):
        return existing_row['Asset class'], existing_row['Focus']
    n = name.lower()
    if any(k in n for k in ['gold', 'silver', 'platinum', 'copper', 'commodity', 'commodities', 'crude', 'oil', 'natural gas', 'uranium', 'metals', 'agriculture', 'carbon']):
        focus = ('Precious metals' if any(k in n for k in ['gold', 'silver', 'platinum'])
                 else 'Energy' if any(k in n for k in ['oil', 'gas', 'uranium', 'energy'])
                 else 'Industrial metals' if 'copper' in n else 'Broad commodity')
        return 'Commodities', focus
    if any(k in n for k in ['bond', 'gilt', 'treasury', 'fixed income', 'overnight', 'ultrashort', 'rate swap', 'tips', 'inflation-linked', 'aggregate', 'debt', 'sofr', 'sonia', 'fed funds', 'corporate', 'credit']):
        if any(k in n for k in ['overnight', 'rate swap', '0-1', 'cash', 'money']):
            focus = 'Cash / Overnight'
        elif any(k in n for k in ['gilt', 'treasury', 'govt', 'government']):
            focus = 'Government'
        elif any(k in n for k in ['high yield', 'hy']):
            focus = 'High yield'
        elif any(k in n for k in ['tips', 'inflation']):
            focus = 'Inflation-linked'
        elif 'aggregate' in n:
            focus = 'Aggregate'
        else:
            focus = 'Investment grade'
        return 'Fixed income', focus
    if any(k in n for k in ['currency', 'forex', 'fx ']):
        return 'Currency', 'Currency'
    themes = ['semiconductor', 'robot', 'artificial intelligence', ' ai ', 'clean energy', 'solar', 'wind', 'biotech', 'cyber', 'infrastructure', 'water', 'cloud', 'battery', 'defence', 'defense', 'space', 'nuclear']
    if any(k in n for k in themes):
        return 'Equity', 'Theme'
    if any(k in n for k in ['dividend', 'high yield equity', 'income', 'aristocrats']):
        return 'Equity', 'High dividend yield'
    if any(k in n for k in ['momentum', 'quality', 'value', 'min vol', 'volatility', 'factor', 'smart beta']):
        return 'Equity', 'Factor'
    if any(k in n for k in ['real estate', 'property', 'reit']):
        return 'Equity', 'Real estate'
    if any(k in n for k in ['small cap', 'small-cap', 'russell 2000']):
        return 'Equity', 'Small cap'
    if any(k in n for k in ['s&p 500', 'nasdaq', 'ftse 100', 'dax', 'euro stoxx 50', 'topix', 'large cap']):
        return 'Equity', 'Large cap'
    return 'Equity', 'Total market'


def generate_catalogue(aum_min: float = 100_000_000, output_path: Path = OUT_CSV) -> list[dict]:
    old_catalogue = {}
    if OLD_CSV.exists():
        with open(OLD_CSV, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                old_catalogue[row['Symbol'].strip().upper()] = row

    t212_all = json.loads(T212_CACHE.read_text())
    etfs = [x for x in t212_all if x.get('type') == 'ETF']

    t212_uk = {x.get('shortName', '').strip().upper(): x for x in etfs if is_london(x)}
    t212_de = {x.get('shortName', '').strip().upper(): x for x in etfs if is_xetra(x)}
    t212_fr = {x.get('shortName', '').strip().upper(): x for x in etfs if is_paris(x)}

    tv_uk = fetch_tv_market('uk', aum_min=aum_min)
    tv_de = fetch_tv_market('germany', aum_min=aum_min)
    tv_fr = fetch_tv_market('france', aum_min=aum_min)

    records = []
    seen_symbols = set()
    seen_isins = set()

    # Pass 1: London listings (primary for UK accounts)
    for item in tv_uk:
        cols = item['d']
        sym = cols[0].strip().upper()
        if sym in t212_uk and sym not in seen_symbols:
            t212_item = t212_uk[sym]
            isin = t212_item.get('isin', '')
            desc = cols[1] or t212_item.get('name', '')
            old_row = old_catalogue.get(sym)
            asset_class, focus = classify_fund(desc, old_row)
            
            # Prefer preserved catalogue price/currency to prevent unit jump errors
            price = old_row['Price'] if (old_row and old_row.get('Price')) else cols[2]
            currency = old_row['Price - Currency'] if (old_row and old_row.get('Price - Currency')) else cols[3]
            
            aum = old_row['Assets under management'] if (old_row and old_row.get('Assets under management')) else cols[7]
            aum_currency = old_row['Assets under management - Currency'] if (old_row and old_row.get('Assets under management - Currency')) else 'USD'
            
            records.append({
                'Symbol': sym,
                'Description': desc,
                'Price': price if price is not None else '',
                'Price - Currency': currency or 'GBP',
                'Price change %, 1 day': cols[4] if cols[4] is not None else '',
                'Price × volume (turnover), 1 day': cols[5] if cols[5] is not None else '',
                'Price × volume (turnover), 1 day - Currency': currency or 'GBP',
                'Relative volume, 1 day': cols[6] if cols[6] is not None else '',
                'Assets under management': aum if aum is not None else '',
                'Assets under management - Currency': aum_currency,
                'NAV total return, 3 years': cols[10] if cols[10] is not None else (old_row.get('NAV total return, 3 years') if old_row else ''),
                'Total holdings': old_row.get('Total holdings', '') if old_row else '',
                'Expense ratio': cols[8] if cols[8] is not None else (old_row.get('Expense ratio') if old_row else ''),
                'Asset class': asset_class,
                'Focus': focus,
                'Yahoo Symbol': f'{sym}.L',
                'Region': 'gb',
                'Exchange': 'LSE'
            })
            seen_symbols.add(sym)
            if isin:
                seen_isins.add(isin)

    # Pass 2: Germany / Xetra listings (unique funds not in London)
    for item in tv_de:
        cols = item['d']
        raw_sym = cols[0].strip().upper()
        if raw_sym in t212_de:
            t212_item = t212_de[raw_sym]
            isin = t212_item.get('isin', '')
            if isin and isin in seen_isins:
                continue
            sym = raw_sym if raw_sym not in seen_symbols else f'{raw_sym}_DE'
            if sym in seen_symbols:
                continue
            desc = cols[1] or t212_item.get('name', '')
            old_row = old_catalogue.get(sym) or old_catalogue.get(raw_sym)
            asset_class, focus = classify_fund(desc, old_row)
            price = old_row['Price'] if (old_row and old_row.get('Price')) else cols[2]
            currency = old_row['Price - Currency'] if (old_row and old_row.get('Price - Currency')) else cols[3]
            records.append({
                'Symbol': sym,
                'Description': desc,
                'Price': price if price is not None else '',
                'Price - Currency': currency or 'EUR',
                'Price change %, 1 day': cols[4] if cols[4] is not None else '',
                'Price × volume (turnover), 1 day': cols[5] if cols[5] is not None else '',
                'Price × volume (turnover), 1 day - Currency': currency or 'EUR',
                'Relative volume, 1 day': cols[6] if cols[6] is not None else '',
                'Assets under management': cols[7] if cols[7] is not None else '',
                'Assets under management - Currency': 'USD',
                'NAV total return, 3 years': cols[10] if cols[10] is not None else '',
                'Total holdings': '',
                'Expense ratio': cols[8] if cols[8] is not None else '',
                'Asset class': asset_class,
                'Focus': focus,
                'Yahoo Symbol': f'{raw_sym}.DE',
                'Region': 'de',
                'Exchange': 'GER'
            })
            seen_symbols.add(sym)
            if isin:
                seen_isins.add(isin)

    # Pass 3: France / Paris listings (unique funds not in London or Xetra)
    for item in tv_fr:
        cols = item['d']
        raw_sym = cols[0].strip().upper()
        if raw_sym in t212_fr:
            t212_item = t212_fr[raw_sym]
            isin = t212_item.get('isin', '')
            if isin and isin in seen_isins:
                continue
            sym = raw_sym if raw_sym not in seen_symbols else f'{raw_sym}_PA'
            if sym in seen_symbols:
                continue
            desc = cols[1] or t212_item.get('name', '')
            old_row = old_catalogue.get(sym) or old_catalogue.get(raw_sym)
            asset_class, focus = classify_fund(desc, old_row)
            price = old_row['Price'] if (old_row and old_row.get('Price')) else cols[2]
            currency = old_row['Price - Currency'] if (old_row and old_row.get('Price - Currency')) else cols[3]
            records.append({
                'Symbol': sym,
                'Description': desc,
                'Price': price if price is not None else '',
                'Price - Currency': currency or 'EUR',
                'Price change %, 1 day': cols[4] if cols[4] is not None else '',
                'Price × volume (turnover), 1 day': cols[5] if cols[5] is not None else '',
                'Price × volume (turnover), 1 day - Currency': currency or 'EUR',
                'Relative volume, 1 day': cols[6] if cols[6] is not None else '',
                'Assets under management': cols[7] if cols[7] is not None else '',
                'Assets under management - Currency': 'USD',
                'NAV total return, 3 years': cols[10] if cols[10] is not None else '',
                'Total holdings': '',
                'Expense ratio': cols[8] if cols[8] is not None else '',
                'Asset class': asset_class,
                'Focus': focus,
                'Yahoo Symbol': f'{raw_sym}.PA',
                'Region': 'fr',
                'Exchange': 'PAR'
            })
            seen_symbols.add(sym)
            if isin:
                seen_isins.add(isin)

    # Ensure CSP1 is row 0 to preserve standard benchmark fixtures
    records.sort(key=lambda r: (0 if r['Symbol'] == 'CSP1' else 1, -(float(r['Assets under management'] or 0))))

    fieldnames = [
        'Symbol', 'Description', 'Price', 'Price - Currency', 'Price change %, 1 day',
        'Price × volume (turnover), 1 day', 'Price × volume (turnover), 1 day - Currency',
        'Relative volume, 1 day', 'Assets under management', 'Assets under management - Currency',
        'NAV total return, 3 years', 'Total holdings', 'Expense ratio', 'Asset class', 'Focus',
        'Yahoo Symbol', 'Region', 'Exchange'
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f'Successfully generated {len(records)} ETFs to {output_path}')
    return records


if __name__ == '__main__':
    generate_catalogue()
