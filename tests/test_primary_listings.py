import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as model
import primary_listings as primary


def record(symbol, region='de', exchange='Frankfurt', name='Issuer'):
    return dict(symbol=symbol, region_code=region, exchange=exchange, name=name,
                active=True, instrument='stock')


def test_foreign_quotes_are_not_promoted_without_a_covered_home_listing():
    rows = [record(s, name='Ping An Insurance (Group) Company of China, Ltd.')
            for s in ['PZX.F', 'PZX1.F', 'PZXB.F']]
    rows += [record('CHL.F', name='China Life Insurance Company Limited'),
             record('XOM.NE', 'ca', 'Cboe CA', 'Exxon Mobil Corporation'),
             record('ALV.F', name='Allianz SE'), record('ALV.DE', exchange='XETRA', name='Allianz SE')]
    flags = model.main_listing_flags(rows, primary.catalogue())
    assert {s for s, r in flags.items() if r['main_listing']} == {'ALV.DE'}
    assert 'wrapper' in flags['XOM.NE']['listing_reason']
    assert 'Secondary' in flags['ALV.F']['listing_reason']
    assert 'unconfirmed' in flags['PZX.F']['listing_reason']


def test_ordinary_classes_and_genuine_foreign_incorporated_primary_survive():
    rows = [record(s, 'us', 'NasdaqGS', 'Alphabet Inc.') for s in ['GOOG', 'GOOGL']]
    rows += [record(s, 'us', 'NYSE', 'Berkshire Hathaway Inc.') for s in ['BRK-A', 'BRK-B']]
    rows += [record(s, 'se', 'Stockholm', 'Investor AB (publ)') for s in ['INVE-A.ST', 'INVE-B.ST']]
    rows += [dict(record('ACN', 'us', 'NYSE', 'Accenture plc'), domicile='Ireland')]
    assert all(r['main_listing'] for r in model.main_listing_flags(rows, primary.catalogue()).values())


def test_cached_flags_are_repaired_in_table_chart_and_csv(tmp_path, monkeypatch):
    import csv
    import io
    import time
    store = model.Store(tmp_path / 'cache.sqlite')
    rows = [record('XOM', 'us', 'NYSE', 'ExxonMobil Holdings Corporation'),
            record('XOM.NE', 'ca', 'Cboe CA', 'Exxon Mobil Corporation')]
    for row in rows:
        row.update(main_listing=True, quote_currency='USD', market_cap_local=1e9,
                   net_income_local=100, financial_currency='USD', div_years=5)
    store.upsert_many(rows)
    store.set_meta('listing_policy_version', 10)
    store.set_meta('listing_classified', time.time())
    monkeypatch.setattr(model, 'store', store)
    selected, count = model.select_rows('Exxon', '', '[]', 'market_cap', 'desc', False, '', 100, 0, True)
    assert count == 1 and selected[0]['symbol'] == 'XOM'
    assert store.meta('listing_policy_version') == 11
    assert [r['symbol'] for r in model.chart(x='net_income', y='div_years', search='Exxon', main_only=True)['rows']] == ['XOM']
    exported = csv.DictReader(io.StringIO(model.export(search='Exxon', columns='symbol', main_only=True).body.decode()))
    assert [r['symbol'] for r in exported] == ['XOM']
    all_rows, count = model.select_rows('Exxon', '', '[]', 'market_cap', 'desc', False, '', 100, 0, False)
    assert {r['symbol'] for r in all_rows} == {'XOM', 'XOM.NE'}


def test_classes_need_explicit_class_description_and_same_primary_venue():
    def row(source, name, is_primary):
        venue, ticker = source.split(':')
        return dict(s=source, d=[ticker, name, is_primary, 'United States', venue, ['common'], 'stock'])
    payload = dict(totalCount=4, data=[row('NASDAQ:GOOG', 'Alphabet Inc. Class C', True),
        row('NASDAQ:GOOGL', 'Alphabet Inc. Class A', False),
        row('NYSE:OTHER', 'Alphabet Inc. Class A', False),
        row('NASDAQ:LOOKALIKE', 'Alphabet Incorporated Class A', False)])
    parsed = primary.parse_market('america', payload)
    assert primary.exclusion(parsed['GOOGL']) is None
    assert primary.exclusion(parsed['OTHER'])
    assert primary.exclusion(parsed['LOOKALIKE'])


def test_exact_yahoo_ticker_mappings():
    assert primary.yahoo_symbols('uk', 'LSE', 'BP.') == ['BP.L']
    assert primary.yahoo_symbols('uk', 'LSE', 'BT.A') == ['BT-A.L']
    assert primary.yahoo_symbols('america', 'NYSE', 'BRK.B') == ['BRK-B']
    assert primary.yahoo_symbols('canada', 'TSX', 'BEP.UN') == ['BEP-UN.TO']
    assert primary.yahoo_symbols('denmark', 'OMXCOP', 'NOVO_B') == ['NOVO-B.CO']
    assert primary.yahoo_symbols('germany', 'XETR', 'ALV') == ['ALV.DE']
    assert primary.yahoo_symbols('germany', 'GETTEX', 'ALV') == []


def test_incomplete_refresh_cannot_replace_successful_cache(tmp_path, monkeypatch):
    path = tmp_path / 'primary.json'
    path.write_text('{"previous":"complete"}')
    before = path.read_bytes()
    monkeypatch.setattr(primary, 'fetch_market', lambda market: (_ for _ in ()).throw(ValueError('Incomplete response')))
    with pytest.raises(ValueError):
        primary.refresh_catalogue(path)
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        primary.parse_market('germany', dict(totalCount=20, data=[dict(s='bad', d=[])]))


def test_unknown_and_preferred_designations_are_excluded():
    assert primary.exclusion(None)
    assert primary.exclusion(dict(primary=True, source='NYSE:PREF', type='stock', types=['preferred']))
    assert primary.exclusion(dict(primary=True, source='NEO:CDR', type='dr', types=['']))
