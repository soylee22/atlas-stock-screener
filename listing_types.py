"""Security-type exclusions, separate from Yahoo issuer names and financial data."""
from __future__ import annotations
import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIRECTORY_URLS = {
    'nasdaqlisted': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
    'otherlisted': 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
}
CATEGORIES = {'Preferred security', 'Depositary receipt', 'Warrant or right', 'Acquisition unit',
              'Debt security', 'Exchange-traded product', 'Exchange-traded fund', 'Test security', 'Converted or retired share class', 'Savings share'}


def description_type(name):
    # A provider's ordinary corporate stock must survive its product-brand rule.
    if re.match(r'^WisdomTree\b', name, re.I) and not re.fullmatch(r'WisdomTree,?\s+Inc\.?', name, re.I):
        return 'Exchange-traded product'
    if re.search(r'\bPIBS\b|\bpermanent interest bearing shares?\b', name, re.I):
        return 'Debt security'
    if re.search(r'\b(?:preferred|preference)\s+(?:stock|shares?|securities|limited partnership units)|\bpfd\b|\b(?:pref|prf)\s*(?:shares?|shs|stock|$)|\bvorzugsaktien?\b', name, re.I):
        return 'Preferred security'
    if re.search(r'\b(?:american|global)\s+deposit[ao]ry|\bdeposit[ao]ry\s+(?:shares?|receipts?)|\b(?:ADR|ADS|GDR|CDR)\b', name, re.I):
        return 'Depositary receipt'
    if re.search(r'\bwarrants?\b|\bsubscription rights?\b| - Rights\b', name, re.I):
        return 'Warrant or right'
    if re.search(r' - Units(?:\s|$)|\bunits?\b.*\b(?:warrants?|rights?)\b', name, re.I):
        return 'Acquisition unit'
    if re.search(r'\b(?:senior|subordinated|perpetual|convertible|exchange.traded)\s+(?:(?:secured|unsecured)\s+)?(?:notes?|debentures?|bonds?)\b|\b\d+(?:\.\d+)?%.*\b(?:notes?|debentures?|bonds?)\b', name, re.I):
        return 'Debt security'
    if re.search(r'\bsavings? shares?\b|\brisparmio\b', name, re.I):
        return 'Savings share'
    return None


def yahoo_symbol(record):
    if record.get('Symbol'):
        return record['Symbol']
    symbol = record.get('CQS Symbol') or ''
    # CQS preserves class semantics. A dot class is ordinary, lower-case p is preferred.
    if 'p' in symbol:
        root, series = symbol.split('p', 1)
        return root + '-P' + series
    for suffix, yahoo in [('.WS', '-WT'), ('.RT', '-RI'), ('.U', '-UN')]:
        if suffix in symbol:
            return symbol.replace(suffix, yahoo)
    return symbol.replace('.', '-')


def parse_directory(text):
    records = list(csv.DictReader(io.StringIO(text), delimiter='|'))
    if not records or 'Security Name' not in records[0]:
        raise ValueError('Invalid Nasdaq security directory')
    result = {}
    for record in records:
        symbol, name = yahoo_symbol(record), record.get('Security Name') or ''
        if not symbol or symbol.startswith('File Creation'):
            continue
        category = ('Test security' if record.get('Test Issue') == 'Y' else
                    'Exchange-traded fund' if record.get('ETF') == 'Y' or record.get('NextShares') == 'Y' else
                    description_type(name))
        if category:
            result[symbol] = dict(category=category, description=name)
    return result, len(records)


@lru_cache(maxsize=4)
def read_catalogue(path, modified):
    data = json.loads(Path(path).read_text())
    if data.get('version') != 1 or not isinstance(data.get('us'), dict):
        raise ValueError('Unsupported listing catalogue')
    return data


def catalogue():
    seed = ROOT / 'seed' / 'listing-types.json'
    data = read_catalogue(str(seed), seed.stat().st_mtime_ns) if seed.is_file() else {'us': {}, 'verified': {}}
    cached = ROOT / 'data' / 'listing-types.json'
    if cached.is_file():
        try:
            latest = read_catalogue(str(cached), cached.stat().st_mtime_ns)
            if latest.get('fetched', '') >= data.get('fetched', ''):
                data = {**data, 'us': latest['us']}
        except (ValueError, OSError):
            pass
    return data


def security_reason(row, data):
    symbol, region = row['symbol'], row.get('region_code')
    verified = data.get('verified', {}).get(symbol)
    record = verified or (data.get('us', {}).get(symbol) if region == 'us' else None)
    if record and record.get('category') in CATEGORIES:
        return record['category'] + ' (verified security classification)'
    if region == 'us' and re.search(r'-(?:P[A-Z0-9]*|PR[.-]?[A-Z0-9]*|WT[A-Z0-9]*|WS[A-Z0-9]*|RI|UN)$', symbol):
        return 'Preferred security, warrant, right or acquisition unit (ticker suffix)'
    if region == 'ca' and re.search(r'-(?:P(?:R|F)?[A-Z0-9]+|DB[A-Z0-9]*|WT[A-Z0-9]*|NT|CV)\.(?:TO|NE|V|CN)$', symbol):
        return 'Preferred or non-equity security (Canadian ticker suffix)'
    if region == 'se' and re.search(r'-PREF(?:[.-]?[A-Z0-9]+)?\.ST$', symbol):
        return 'Preferred security (Swedish ticker suffix)'
    if region == 'tw' and re.fullmatch(r'\d{4}[A-Z]\.(?:TW|TWO)', symbol):
        return 'Preferred security (Taiwanese ticker suffix)'
    return description_type(row.get('name') or '')


def refresh_directory(destination=None):
    """Refresh once daily. Failed or incomplete downloads preserve the cached catalogue."""
    destination = Path(destination or ROOT / 'data' / 'listing-types.json')
    seed = catalogue()
    if destination.is_file():
        try:
            latest = json.loads(destination.read_text())
            if latest.get('fetched', '')[:10] == datetime.now(timezone.utc).date().isoformat():
                return {'security_directory': 'Already checked today'}
        except (OSError, ValueError):
            pass
    combined, counts = {}, {}
    for name, url in DIRECTORY_URLS.items():
        request = urllib.request.Request(url, headers={'User-Agent': 'Atlas stock screener security classification'})
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read(5_000_001).decode('utf-8-sig')
        if len(text) > 5_000_000:
            raise ValueError('Oversized security directory')
        types, count = parse_directory(text)
        if count < 1000 or 'File Creation Time' not in text:
            raise ValueError('Incomplete security directory')
        combined.update({symbol: {**record, 'source': url} for symbol, record in types.items()})
        counts[name] = count
    data = dict(version=1, fetched=datetime.now(timezone.utc).isoformat(), us=combined,
                verified=seed.get('verified', {}), counts=counts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    temporary.replace(destination)
    return {'security_exclusions': len(combined), 'directories': counts}


if __name__ == '__main__':
    try:
        print(json.dumps(refresh_directory()))
    except Exception as error:
        print(json.dumps({'security_directory': 'Source unavailable. Previous classifications retained.', 'error_type': type(error).__name__}))
