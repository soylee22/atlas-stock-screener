"""Cached primary-exchange designations. Financial data remains from Yahoo."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
MARKETS = ('america', 'uk', 'canada', 'japan', 'korea', 'taiwan',
           'germany', 'spain', 'italy', 'netherlands', 'denmark', 'sweden')
COLUMNS = ['name', 'description', 'is_primary', 'country', 'exchange', 'typespecs', 'type']
# Exact venue/ticker mappings, not issuer-name or domicile guesses.
VENUES = {
    'america': {'NASDAQ': '', 'NYSE': '', 'AMEX': '', 'CBOE': ''},
    'uk': {'LSE': '.L', 'AQUIS': '.L'},
    'canada': {'TSX': '.TO', 'TSXV': '.V', 'CSE': '.CN', 'NEO': '.NE'},
    'japan': {'TSE': '.T'},
    'korea': {'KRX': ('.KS', '.KQ')},
    'taiwan': {'TWSE': '.TW', 'TPEX': '.TWO'},
    'germany': {'XETR': '.DE', 'FWB': '.F'},
    'spain': {'BME': '.MC'}, 'italy': {'MIL': '.MI'},
    'netherlands': {'EURONEXT': '.AS'}, 'denmark': {'OMXCOP': '.CO'},
    'sweden': {'OMXSTO': '.ST'},
}


def yahoo_symbols(market, exchange, ticker):
    suffixes = VENUES[market].get(exchange)
    if suffixes is None:
        return []
    if market == 'uk':
        ticker = ticker.rstrip('.')
    ticker = ticker.replace('.', '-').replace('_', '-')
    if market == 'korea' and ticker.isdigit():
        ticker = ticker.zfill(6)
    if market == 'japan' and ticker.isdigit():
        ticker = ticker.zfill(4)
    return [ticker + suffix for suffix in ((suffixes,) if isinstance(suffixes, str) else suffixes)]


def parse_market(market, payload):
    records = payload.get('data')
    if not isinstance(records, list) or not records or payload.get('totalCount') != len(records):
        raise ValueError(f'Incomplete primary-listing response for {market}')
    result = {}
    for row in records:
        values = row.get('d')
        if not isinstance(values, list) or len(values) != len(COLUMNS):
            raise ValueError(f'Invalid primary-listing row for {market}')
        ticker, name, primary, country, exchange, types, kind = values
        if type(primary) is not bool or kind not in {'stock', 'dr'} or not isinstance(types, list):
            raise ValueError(f'Invalid primary-listing designation for {market}')
        if row.get('s') != f'{exchange}:{ticker}':
            raise ValueError(f'Conflicting primary-listing identifier for {market}')
        for symbol in yahoo_symbols(market, exchange, ticker):
            value = dict(primary=primary, source=row['s'], type=kind, types=types)
            share_class = re.fullmatch(r'(.+?)\s+Class\s+[A-Z0-9]+', name)
            if share_class and kind == 'stock' and 'common' in types:
                value['class_issuer'] = share_class[1]
            if symbol in result and result[symbol] != value:
                raise ValueError(f'Ambiguous primary-listing mapping: {symbol}')
            result[symbol] = value
    if not result:
        raise ValueError(f'No supported primary-listing venues for {market}')
    class_primaries = {(r['source'].split(':')[0], r['class_issuer']): r['source']
                      for r in result.values() if r['primary'] and r.get('class_issuer')}
    for record in result.values():
        if not record['primary'] and record.get('class_issuer'):
            counterpart = class_primaries.get((record['source'].split(':')[0], record['class_issuer']))
            if counterpart:
                record['ordinary_class_of'] = counterpart
    return result


def fetch_market(market):
    response = requests.post(f'https://scanner.tradingview.com/{market}/scan', json={
        'columns': COLUMNS, 'range': [0, 100000],
        'filter': [dict(left='type', operation='in_range', right=['stock', 'dr'])],
    }, timeout=45)
    response.raise_for_status()
    payload = response.json()
    return market, parse_market(market, payload), payload['totalCount']


def refresh_catalogue(path):
    # Build a complete replacement before touching the last successful cache.
    records, counts = {}, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for market, regional, count in pool.map(fetch_market, MARKETS):
            if records.keys() & regional.keys():
                raise ValueError('Ambiguous cross-market primary-listing symbols')
            records.update(regional)
            counts[market] = count
    data = dict(version=1, fetched=datetime.now(timezone.utc).isoformat(),
                source='TradingView public stock screener primary-listing designation',
                counts=counts, records=records)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, separators=(',', ':')) + '\n')
    temporary.replace(path)
    return data


@lru_cache(maxsize=4)
def read_catalogue(path, modified):
    data = json.loads(Path(path).read_text())
    records = data.get('records')
    if (data.get('version') != 1 or set(data.get('counts', {})) != set(MARKETS)
            or not isinstance(records, dict) or not records or not isinstance(data.get('fetched'), str)
            or any(type(n) is not int or n <= 0 for n in data['counts'].values())
            or any(not isinstance(r, dict) or type(r.get('primary')) is not bool
                   or r.get('type') not in {'stock', 'dr'} or not isinstance(r.get('types'), list)
                   or not isinstance(r.get('source'), str) or ':' not in r['source'] for r in records.values())):
        raise ValueError('Invalid primary-listing catalogue')
    return data


def catalogue():
    candidates = []
    for path in [ROOT / 'seed' / 'primary-listings.json', ROOT / 'data' / 'primary-listings.json']:
        if path.is_file():
            try:
                candidates.append(read_catalogue(str(path), path.stat().st_mtime_ns))
            except (OSError, ValueError):
                continue
    return max(candidates, key=lambda d: d['fetched'])['records'] if candidates else {}


def exclusion(record):
    if not record:
        return 'Primary listing unconfirmed. Visible with Main listings only switched off.'
    if record['type'] == 'dr' or any(t in {'depositary', 'preferred', 'warrant', 'right'} for t in record['types']):
        return 'Depositary wrapper or non-ordinary security (' + record['source'] + ')'
    if not record['primary'] and not record.get('ordinary_class_of'):
        return 'Secondary quotation (' + record['source'] + ')'
    return None


if __name__ == '__main__':
    try:
        data = refresh_catalogue(ROOT / 'data' / 'primary-listings.json')
        print(f"Primary designations refreshed: {len(data['records']):,} Yahoo symbols")
    except Exception as error:
        if not catalogue():
            raise
        print(f'Primary-designation refresh unavailable. Retaining cached designations: {type(error).__name__}')
