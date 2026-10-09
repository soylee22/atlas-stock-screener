"""Dated ETF catalogue and independently cached Yahoo London price histories."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import time

import pandas as pd
import requests
import yfinance as yf
from technicals import technical_values
from market_sessions import SessionCutoff

ROOT = Path(__file__).resolve().parent
CATALOGUE = ROOT / 'seed' / 'etfs-2026-10-09.csv'
CATALOGUE_DATE = '2026-10-09'
SOURCE = 'User-supplied TradingView ETF screener CSV'
CACHE = ROOT / 'data' / 'etfs.json'
REGIONS = {'gb': 'UK / London candidate listings'}


class QuoteUnitError(ValueError):
    pass


def column(key, label, kind='text', default=True, group='Fund', description=''):
    return dict(key=key, label=label, kind=kind, default=default, group=group, description=description)


COLUMNS = [
    column('aum', 'AUM', 'usd', description='CSV fund assets in GBP converted using cached FX. Fund size, not company market cap.'),
    column('price', 'Price', 'price', description='Previous completed Yahoo daily close in USD, or dated CSV price where history is unavailable.'),
    column('expense_ratio', 'TER / expense ratio', 'percent', description='Annual expense ratio from the CSV. 0.07 means 0.07%. Fees differ from total ownership costs.'),
    column('asset_class', 'Asset class'), column('focus', 'Focus'),
    column('specialism', 'Sector / specialism', description='Explicit sector focus from the CSV, or a theme hinted by the fund name. Confirm the benchmark and portfolio with the issuer.'),
    column('product_type', 'Product type', default=False, description='ETF, ETC or ETP where explicitly stated in the imported name. Otherwise type unconfirmed.'),
    column('exposure', 'Exposure', default=False, description='Leveraged or inverse only where explicitly named. Standard / unspecified is not a certified exclusion of leverage.'),
    column('nav_return_3y', 'NAV total return 3Y', 'percent', description='Cumulative three-year NAV total return imported on 9 October 2026. Return currency is not supplied. Not CAGR or market-price return.'),
    column('williams_r', 'Williams %R · weekly', 'number', True, 'Technicals', '14 weekly Yahoo OHLC candles. Oversold <= -80. Includes the developing week through the latest completed exchange session.'),
    column('holdings', 'Holdings', 'integer', False),
    column('quote_currency', 'Quote currency', default=False),
    column('change', 'Session change', 'percent', False, 'Price', 'Change between the last two completed Yahoo daily closes. CSV 1-day change is the fallback.'),
    column('volume', 'Volume', 'number', False, 'Price'),
    column('turnover', 'CSV turnover', 'usd', False, 'Price', 'Dated CSV price times volume, converted from its own turnover currency.'),
    column('relative_volume', 'CSV relative volume', 'number', False, 'Price'),
    column('below_52w_high', 'Below 52W high', 'percent', False, 'Technicals', 'Distance from the maximum split-adjusted daily high in the trailing calendar year.'),
    column('sma_200d_distance', '200-day SMA distance', 'percent', False, 'Technicals'),
    column('sma_200w_distance', '200-week SMA distance', 'percent', False, 'Technicals'),
    column('sma_custom_distance', 'Custom SMA distance', 'percent', False, 'Technicals'),
    column('exchange', 'Exchange', default=False),
    column('mapping_status', 'Yahoo mapping', default=False),
    column('catalogue_date', 'Fund data date', default=False),
    column('price_period', 'Price period', default=False),
    column('technical_fetched', 'History fetched', default=False),
]

# Put entry position and performance beside size and fees in the initial layout.
_front=['aum','price','expense_ratio','williams_r','nav_return_3y','asset_class','focus','specialism']
COLUMNS.sort(key=lambda field: _front.index(field['key']) if field['key'] in _front else len(_front))

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def convert(value, currency, fx, *, price=False):
    value = number(value)
    unit = .01 if price and currency in {'GBp','GBX'} else 1
    currency = 'GBP' if currency in {'GBp', 'GBX'} else currency
    rate = 1 if currency == 'USD' else number(fx.get(currency, {}).get('rate'))
    return value * unit * rate if value is not None and rate is not None and rate > 0 else None


def usd_price(value, currency, fx):
    return convert(value, currency, fx, price=True)


def dollarise(row, fx):
    row = dict(row)
    row['price'] = usd_price(row.get('price_local'), row.get('quote_currency'), fx)
    row['aum'] = convert(row.get('aum_local'), row.get('aum_currency'), fx)
    row['turnover'] = convert(row.get('turnover_local'), row.get('turnover_currency'), fx)
    return row


def specialism(name, focus):
    themes=[('Semiconductors',r'semiconductor'),('Robotics / automation',r'robot|automat'),
        ('Artificial intelligence',r'artificial intelligence'),('Clean energy',r'clean energy|renewable|solar|wind'),
        ('Biotechnology',r'biotech'),('Cybersecurity',r'cyber'),('Infrastructure',r'infrastructure'),
        ('Water',r'\bwater\b'),('Technology',r'technolog'),('Healthcare',r'health'),
        ('Financials',r'financial|banks'),('Energy',r'energy|oil'),('Property',r'real estate|property')]
    for label,pattern in themes:
        if re.search(pattern,name,re.I):
            return label + ' · name hint'
    return 'Theme unspecified' if focus=='Theme' else focus


def import_catalogue(path=CATALOGUE):
    rows = []
    seen = set()
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        for record in csv.DictReader(source):
            symbol, name = record['Symbol'].strip(), record['Description'].strip()
            if not re.fullmatch(r'[A-Z0-9]+', symbol) or symbol in seen or not name:
                raise ValueError('ETF catalogue needs unique bare symbols and descriptions')
            seen.add(symbol)
            product = next((label for label in ['ETF', 'ETC', 'ETP'] if re.search(r'\b' + label + r'\b', name, re.I)), 'Unconfirmed')
            inverse_name=re.sub(r'\bshort[\s-]*(?:maturity|duration|term|dated)\b','',name,flags=re.I)
            inverse = bool(re.search(r'\b(inverse|short)\b', inverse_name, re.I))
            leveraged = bool(re.search(r'\b(leverage\w*|[235]x|[235]times)\b|\b[235]\s*x\b', name, re.I))
            exposure = 'Leveraged & inverse' if inverse and leveraged else 'Inverse / short' if inverse else 'Leveraged' if leveraged else 'Standard / unspecified'
            rows.append(dict(symbol=symbol, yahoo_symbol=symbol + '.L', name=name,
                active=True, instrument='etf', main_listing=True, region_code='gb', region='UK / London candidates',
                exchange='London candidate', quote_currency=record['Price - Currency'],
                price_source='CSV snapshot', catalogue_price_local=number(record['Price']), price_local=number(record['Price']), price_period=f'CSV {CATALOGUE_DATE} · capture time unknown',
                change=number(record['Price change %, 1 day']),
                turnover_local=number(record['Price × volume (turnover), 1 day']),
                turnover_currency=record['Price × volume (turnover), 1 day - Currency'],
                relative_volume=number(record['Relative volume, 1 day']),
                aum_local=number(record['Assets under management']), aum_currency=record['Assets under management - Currency'],
                expense_ratio=number(record['Expense ratio']), nav_return_3y=number(record['NAV total return, 3 years']),
                nav_return_3y_period=f'3Y cumulative · imported {CATALOGUE_DATE} · return currency unknown',
                aum_period=f'CSV {CATALOGUE_DATE} · original AUM currency {record["Assets under management - Currency"]}',
                expense_ratio_period=f'CSV {CATALOGUE_DATE} · annual expense ratio',
                nav_return_currency=None, holdings=number(record['Total holdings']),
                asset_class=record['Asset class'], focus=record['Focus'], specialism=specialism(name,record['Focus']), product_type=product, exposure=exposure,
                catalogue_date=CATALOGUE_DATE, catalogue_source=SOURCE,
                mapping_status='Not checked', mapping_reason='London ticker candidate has not been verified against Yahoo metadata.'))
    if not rows:
        raise ValueError('ETF catalogue is empty')
    return rows


def identity_error(row, metadata):
    if metadata.get('symbol', '').upper() != row['yahoo_symbol'].upper():
        return 'Yahoo returned a different symbol'
    is_etp_equity=metadata.get('instrumentType')=='EQUITY' and (row.get('product_type') in {'ETC','ETP'} or row.get('asset_class') in {'Commodities','Currency','Alternatives'})
    if metadata.get('exchangeName') != 'LSE' or (metadata.get('instrumentType') != 'ETF' and not is_etp_equity):
        return 'Yahoo does not confirm a compatible ETF/ETP on London Stock Exchange'
    normal = lambda c: 'GBP' if c in {'GBp', 'GBX'} else c
    # Both the base currency and pence denomination must match the catalogue.
    if normal(metadata.get('currency')) != normal(row['quote_currency']) or ((metadata.get('currency') in {'GBp','GBX'}) != (row['quote_currency'] in {'GBp','GBX'})):
        return 'Yahoo quote currency or denomination does not match the catalogue'
    stop = {'etf','etc','etp','ucits','usd','gbp','eur','acc','dist','plc','fund','funds','class','the','and','a','i','ii','iii','iv','inc','distributing','accumulating','uc','h','gbx'}
    tokens = lambda s: set(re.findall(r'[a-z0-9]+', s.lower())) - stop
    expected, actual = tokens(row['name']), tokens(metadata.get('longName') or metadata.get('shortName') or '')
    if len(expected & actual) < 2 or (is_etp_equity and len(expected & actual)/max(1,len(expected | actual))<.8):
        return 'Yahoo fund description does not sufficiently match the catalogue'
    return None


def apply_history(row, history, metadata, today=None, *, asof=None):
    error = identity_error(row, metadata)
    if error:
        raise ValueError(error)
    history = history.copy()
    history.index = pd.to_datetime(history.index)
    if history.index.tz is not None:
        history.index = history.index.tz_localize(None)
    cutoff = SessionCutoff(today, asof=asof, metadata=metadata, region='gb')
    today = cutoff.today
    complete = cutoff.completed(history).dropna(subset=['Close']).sort_index()
    if complete.empty:
        raise ValueError('Yahoo returned no completed sessions')
    latest = float(complete.Close.iloc[-1])
    if not math.isfinite(latest) or latest <= 0:
        raise ValueError('Yahoo returned an invalid latest close')
    imported_price = number(row.get('catalogue_price_local'))
    if imported_price and abs((today - date.fromisoformat(CATALOGUE_DATE)).days) <= 7 and (latest / imported_price < .05 or latest / imported_price > 20):
        raise QuoteUnitError('Latest Yahoo close differs over twenty-fold from the dated catalogue. Quote units require verification.')
    ratios = complete.Close / complete.Close.shift(1)
    jumps = complete.index[(ratios < .05) | (ratios > 20)]
    regime_start = jumps[-1] if len(jumps) else None
    coherent = history[history.index >= regime_start] if regime_start is not None else history
    currency = metadata['currency']
    out = dict(row, mapping_status='Verified', mapping_reason=None, yahoo_name=metadata.get('longName') or metadata.get('shortName'),
        exchange='LSE', region='UK / London', quote_currency=currency, yahoo_instrument_type=metadata.get('instrumentType'))
    out.update(technical_values(coherent, currency, validate_daily_close=False, asof=cutoff.asof, metadata=metadata, region='gb'))
    out['history_regime_start'] = str(regime_start.date()) if regime_start is not None else None
    out['history_quality_note'] = 'A price discontinuity above twenty-fold was detected. Indicators use only the subsequent consistent segment. No unit correction is guessed.' if regime_start is not None else None
    candles=coherent[['High','Low','Close']]
    out['williams_input_note']='Weekly extrema use reported daily High and Low, with the latest reported Close. Intermediate closes outside intraday ranges do not change these extrema. No prices are estimated or clamped.'
    out['williams_daily_range_discrepancies']=int(((candles.Close<candles.Low)|(candles.Close>candles.High)).tail(80).sum())
    out.update(price_local=float(complete.Close.iloc[-1]), price_period=f'Yahoo daily close {complete.index[-1].date()} · {currency}',
        price_source='Yahoo Finance', price_fetched=now(), quote_time=complete.index[-1].isoformat(),
        change=(float(complete.Close.iloc[-1]) / float(complete.Close.iloc[-2]) - 1) * 100 if len(complete) > 1 and complete.Close.iloc[-2] > 0 else None,
        volume=number(complete.Volume.iloc[-1]) if 'Volume' in complete else None)
    recent = complete[complete.index.date >= today - timedelta(days=365)]
    high = number(recent.High.max()) if 'High' in recent else None
    out['below_52w_high'] = (1 - out['price_local'] / high) * 100 if high and high > 0 and (regime_start is None or regime_start.date() < today - timedelta(days=365)) else None
    out['below_52w_high_period'] = f'Yahoo daily highs · trailing calendar year to {complete.index[-1].date()}'
    if 'Dividends' in history:
        events = coherent[coherent['Dividends'] > 0]['Dividends']
        out['distribution_events'] = [dict(date=str(d.date()), amount=float(v)) for d,v in events.items()]
        out['distribution_currency'] = currency
        out['distribution_history_start'] = str(coherent.index[0].date())
    return out


def read_cache(path=CACHE):
    try:
        cache = json.loads(Path(path).read_text())
        if cache.get('version') != 1:
            raise ValueError('Unsupported ETF cache')
        return cache
    except FileNotFoundError:
        return dict(version=1, rows=[], refresh={})


def write_cache(cache, path=CACHE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(cache, separators=(',', ':'), allow_nan=False))
    temporary.replace(path)


def merged_rows(cache):
    existing = {r['symbol']: r for r in cache['rows']}
    # Dated imported fund metadata is authoritative. Retain successful quote/history data.
    keep = {'price_local','quote_currency','change','exchange','region','price_period','price_source'}
    rows = []
    for imported in import_catalogue():
        retained = existing.get(imported['symbol'], {})
        row = dict(retained, **imported)
        for key, value in retained.items():
            if key not in imported or (key in keep and retained.get('mapping_status') == 'Verified'):
                row[key] = value
        if retained.get('mapping_status'):
            row['mapping_status'] = retained['mapping_status']
            row['mapping_reason'] = retained.get('mapping_reason')
        rows.append(row)
    return rows


def refresh(cache_path=CACHE, seconds=1200, limit=1000, fetcher=None):
    cache = read_cache(cache_path)
    cache['rows'] = merged_rows(cache)
    checkpoint = dict(started=now(), attempted=0, succeeded=0, failed=0, rate_limited=False)
    cache['refresh'] = checkpoint
    cutoff = datetime.now(timezone.utc) - timedelta(hours=20)
    retry = datetime.now(timezone.utc) - timedelta(hours=6)
    session_date, session_ready = SessionCutoff(region='gb').latest_session()
    due = lambda row: row.get('etf_history_version') != 4 or not row.get('technical_fetched') or datetime.fromisoformat(row['technical_fetched']) < cutoff or (row.get('technical_asof') or '') < session_date
    ready = lambda row: (row.get('technical_fetched') and row.get('etf_history_version')!=4) or not row.get('history_attempted') or datetime.fromisoformat(row['history_attempted']) < retry or (row.get('technical_fetched') and datetime.fromisoformat(row['history_attempted']) < session_ready)
    candidates = sorted([r for r in cache['rows'] if due(r) and ready(r)], key=lambda r: (bool(r.get('technical_fetched')), r.get('technical_fetched') or '', -(r.get('aum_local') or 0), r['symbol']))
    deadline = time.monotonic() + seconds
    for row in candidates[:limit]:
        if time.monotonic() >= deadline:
            break
        row['history_attempted'] = now()
        checkpoint['attempted'] += 1
        try:
            if fetcher:
                history, metadata = fetcher(row['yahoo_symbol'])
            else:
                ticker = yf.Ticker(row['yahoo_symbol'])
                history = ticker.history(period='5y', auto_adjust=False, actions=True, raise_errors=True, timeout=15)
                metadata = ticker.history_metadata
            refreshed = apply_history(row, history, metadata)
            refreshed['history_error'] = None
            refreshed['etf_history_version'] = 4
            row.clear()
            row.update(refreshed)
            checkpoint['succeeded'] += 1
        except Exception as exc:
            message = str(exc)
            if isinstance(exc, QuoteUnitError):
                attempted = row['history_attempted']
                imported = next(r for r in import_catalogue() if r['symbol'] == row['symbol'])
                row.clear()
                row.update(imported, history_attempted=attempted, history_quality_note=message)
            row['history_error'] = 'Yahoo refresh failed. Previous data retained.' if row.get('technical_fetched') else 'Yahoo history unavailable or mapping unconfirmed.'
            if not row.get('technical_fetched'):
                row.update(mapping_status='Unavailable / unconfirmed', mapping_reason=message[:250], williams_version=1, williams_reason=row['history_error'])
            checkpoint['failed'] += 1
            if '429' in message or 'rate limit' in message.lower() or type(exc).__name__ == 'YFRateLimitError':
                checkpoint['rate_limited'] = True
        if checkpoint['attempted'] % 20 == 0 or checkpoint['rate_limited']:
            write_cache(cache, cache_path)
            print(json.dumps(checkpoint), flush=True)
        if checkpoint['rate_limited']:
            break
    checkpoint['finished'] = now()
    checkpoint['remaining'] = sum(due(r) for r in cache['rows'])
    write_cache(cache, cache_path)
    return cache


def restore(cache_path=CACHE, base='https://soylee22.github.io/atlas-stock-screener/'):
    if read_cache(cache_path)['rows']:
        return False
    response = requests.get(base + 'data/etfs/seed.json.gz', timeout=30)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    content = gzip.decompress(response.content) if response.content[:2] == b'\x1f\x8b' else response.content
    cache = json.loads(content)
    if cache.get('version') != 1 or not isinstance(cache.get('rows'), list) or not cache['rows']:
        raise ValueError('Invalid ETF recovery snapshot')
    write_cache(cache, cache_path)
    return True


def publish(output, fx, built, cache_path=CACHE, compress=True):
    cache = read_cache(cache_path)
    rows = [dollarise(r, fx) for r in merged_rows(cache)]
    output = Path(output)
    def write(name, value, zipped=False):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        if zipped:
            path.with_suffix('.json.gz').write_bytes(gzip.compress(content, mtime=0))
        else:
            path.write_bytes(content)
    for row in rows:
        row['detail_key'] = hashlib.sha256(row['symbol'].encode()).hexdigest()
        write('details/' + row['detail_key'] + '.json', row, compress)
    fields = ['symbol','name','instrument','active','main_listing','region_code','region','detail_key',
        'catalogue_source','catalogue_price_local','price_source','history_error','mapping_reason','nav_return_currency','nav_return_3y_period',
        'aum_period','expense_ratio_period','price_local','aum_local','aum_currency','turnover_local','turnover_currency','quote_time','technical_version','technical_asof',
        'history_quality_note','history_regime_start','williams_input_note','williams_daily_range_discrepancies','technical_calendar','williams_source_note','williams_version','williams_reason','williams_asof','williams_zone','williams_r_period','williams_provisional',
        'sma_200d_period','sma_200w_period','below_52w_high_period', *[c['key'] for c in COLUMNS]]
    fields = list(dict.fromkeys(fields))
    write('schema.json', dict(universe='etf', columns=COLUMNS, regions=REGIONS))
    write('stocks.json', dict(version=1, built=built, fields=fields, rows=[[r.get(k) for k in fields] for r in rows]), compress)
    write('technicals.json', dict(built=built, technicals={r['symbol']:r['technical_history'] for r in rows if r.get('technical_history')}, currencies={r['symbol']:r.get('technical_currency') for r in rows if r.get('technical_history')}), compress)
    valid = sum(r.get('williams_r') is not None for r in rows)
    status = dict(counts=[dict(region='gb', stocks=len(rows), main_stocks=len(rows), enriched=valid, main_enriched=valid)],
        fx=fx, etfs=dict(total=len(rows), williams=valid, verified=sum(r.get('mapping_status') == 'Verified' for r in rows),
            fees=sum(r.get('expense_ratio') is not None for r in rows), aum=sum(r.get('aum') is not None for r in rows),
            nav_return_3y=sum(r.get('nav_return_3y') is not None for r in rows), catalogue_date=CATALOGUE_DATE, refresh=cache.get('refresh')),
        snapshot=dict(built=built, index_compression='gzip' if compress else None, technical_compression='gzip' if compress else None,
            detail_compression='gzip' if compress else None, cadence='ETF price histories refresh nightly from 01:23 UK time. Fund metadata is a dated CSV import.'))
    write('status.json', status)
    write('seed.json', dict(cache, rows=merged_rows(cache)), True)
    return status['etfs']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--publish-local', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.seconds <= 1200 or not 0 <= args.limit <= 1000:
        parser.error('Use 0 to 1200 seconds and 0 to 1000 funds')
    if args.restore:
        try:
            restore()
        except requests.RequestException:
            print('ETF recovery unavailable. CSV retained, history will refill.', flush=True)
    cache = refresh(seconds=args.seconds, limit=args.limit)
    print(json.dumps(cache['refresh']), flush=True)
    if args.publish_local:
        import app
        print(json.dumps(publish(ROOT / 'data' / 'etf-public', app.Store().meta('fx', {}), now())))


if __name__ == '__main__':
    main()
