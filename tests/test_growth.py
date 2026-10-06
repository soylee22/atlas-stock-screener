import csv
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as model
import build_site
import refresh_data


def annual(values, metric='Total Revenue', end=2026):
    return pd.DataFrame([values], index=[metric], columns=pd.to_datetime([f'{end-i}-06-30' for i in range(len(values))]))


def test_yahoo_four_year_window_supports_only_1y_and_3y():
    data = model.annual_growth_values(annual([133.1, 121, 110, 100]), 'USD')
    assert data['revenue_growth_1y'] == pytest.approx(10)
    assert data['revenue_growth_3y'] == pytest.approx(10)
    assert data['revenue_growth_5y'] is None
    assert data['revenue_growth_10y'] is None
    assert '6 annual records' in data['annual_growth_missing']['revenue_growth_5y']
    assert data['revenue_growth_3y_period'] == 'FY 2026-06-30 / 2023-06-30 · USD · Yahoo'


def test_rate_limit_stops_queue_without_marking_source_unavailable(tmp_path, monkeypatch):
    store = model.Store(tmp_path/'rate.sqlite')
    store.upsert_many([dict(symbol=f'RATE{i}',region_code='us',active=True,instrument='stock') for i in range(10)])
    def limited(symbol):
        raise RuntimeError('HTTP 429 Too Many Requests')
    monkeypatch.setattr(model,'fetch_annual_income',limited)
    result = refresh_data.backfill_growth(store,seconds=10,limit=10,workers=1)
    assert result['annual_growth'] == dict(attempted=1,succeeded=0,failed=1,no_data=0)
    assert len(store.growth_candidates()) == 9
    assert not any(store.get(f'RATE{i}').get('annual_growth_version') for i in range(10))


def test_accumulated_history_retains_older_years_and_updates_restatements():
    old = model.annual_growth_values(annual([100 * 1.1 ** i for i in range(3, -1, -1)], end=2019), 'JPY')
    for year in range(2020, 2027):
        frame = annual([100 * 1.1 ** (year-i-2016) for i in range(4)], end=year)
        old = model.annual_growth_values(frame, 'JPY', old)
    assert old['revenue_growth_5y'] == pytest.approx(10)
    assert old['revenue_growth_10y'] == pytest.approx(10)
    assert len(old['annual_income_history']['revenue']) == 11
    changed = model.annual_growth_values(annual([300, 200]), 'JPY', old)
    assert changed['annual_income_history']['revenue']['2025-06-30'] == 200
    assert changed['revenue_growth_1y'] == pytest.approx(50)
    assert changed['annual_income_history']['revenue']['2016-06-30'] == 100


def test_currency_change_discards_incompatible_history():
    old = model.annual_growth_values(annual([100]*11), 'JPY')
    data = model.annual_growth_values(annual([120, 100]), 'USD', old)
    assert data['revenue_growth_1y'] == pytest.approx(20)
    assert data['revenue_growth_3y'] is None
    assert len(data['annual_income_history']['revenue']) == 2
    unknown = model.annual_growth_values(annual([120, 100]), None, old)
    assert unknown['revenue_growth_1y'] is None


@pytest.mark.parametrize('values,expected', [([80,100],-20),([0,100],-100),([-50,100],-150),([10,-10],None),([10,0],None)])
def test_income_yoy_keeps_declines_but_rejects_loss_or_zero_base(values, expected):
    data = model.annual_growth_values(annual(values, 'Net Income'), 'USD')
    assert data['net_income_growth_1y'] == (pytest.approx(expected) if expected is not None else None)


def test_cagr_rejects_negative_endpoint_and_preserves_zero_endpoint():
    data = model.annual_growth_values(annual([-10,30,20,100], 'Net Income'), 'USD')
    assert data['net_income_growth_3y'] is None
    data = model.annual_growth_values(annual([0,30,20,100], 'Net Income'), 'USD')
    assert data['net_income_growth_3y'] == -100


def test_missing_fiscal_year_and_quarterly_intervals_are_not_cagr():
    missing = annual([40,30,20,10]).rename(columns={pd.Timestamp('2024-06-30'): pd.Timestamp('2022-06-30')})
    assert model.annual_growth_values(missing, 'USD')['revenue_growth_3y'] is None
    quarterly = pd.DataFrame([[40,30,20,10]], index=['Total Revenue'], columns=pd.to_datetime(['2026-06-30','2026-03-31','2025-12-31','2025-09-30']))
    assert model.annual_growth_values(quarterly, 'USD')['revenue_growth_1y'] is None
    nan = model.annual_growth_values(annual([100, float('nan'), 50]), 'USD')
    assert nan['revenue_growth_1y'] is None
    json.dumps(nan, allow_nan=False)


@pytest.mark.parametrize('price,high,expected', [(80,100,20),(100,100,0),(105,100,-5),(10,0,None),(None,100,None)])
def test_52w_distance_uses_matching_local_quote_units_without_fx(price, high, expected):
    row = model.dollarise(dict(price_local=price, high_52w_local=high, quote_currency='GBp'), {})
    assert row['below_52w_high'] == (pytest.approx(expected) if expected is not None else None)


def test_growth_filter_sort_chart_and_csv_share_values_and_periods(tmp_path, monkeypatch):
    store = model.Store(tmp_path / 'test.sqlite')
    for symbol, growth in [('SMALL', 2), ('LARGE', 20), ('MISSING', None)]:
        store.upsert_many([dict(symbol=symbol,name=symbol,region_code='us',instrument='stock',active=True,
            revenue_growth_1y=growth, net_income_growth_3y=10,
            revenue_growth_1y_period='FY 2026 / 2025 · USD · Yahoo')])
    monkeypatch.setattr(model, 'store', store)
    rows, total = model.select_rows('', '', '[]', 'revenue_growth_1y', 'desc', False, '', 100, 0, False)
    assert [r['symbol'] for r in rows] == ['LARGE','SMALL','MISSING']
    chart = model.chart(x='revenue_growth_1y', y='net_income_growth_3y', main_only=False,
        filters='[{"field":"revenue_growth_1y","op":"gte","value":10}]')
    assert chart['total'] == 1 and chart['rows'][0]['symbol'] == 'LARGE'
    assert chart['rows'][0]['revenue_growth_1y_period'] == 'FY 2026 / 2025 · USD · Yahoo'
    rows = list(csv.DictReader(io.StringIO(model.export(columns='revenue_growth_1y', main_only=False).body.decode())))
    assert rows[0]['revenue_growth_1y_period'] and float(rows[0]['revenue_growth_1y']) == 20


def test_growth_backfill_preserves_profile_age_and_public_seed_history(tmp_path, monkeypatch):
    store = model.Store(tmp_path / 'test.sqlite')
    store.upsert_many([dict(symbol='TEST',region_code='us',instrument='stock',active=True,
        financial_currency='USD',financial_fetched='2026-10-01',private_note='secret')], enriched=True)
    with store.connect() as conn:
        enriched = conn.execute('SELECT enriched FROM stocks').fetchone()[0]
    monkeypatch.setattr(model, 'fetch_annual_income', lambda symbol: (annual([133.1,121,110,100]), 'USD'))
    result = refresh_data.backfill_growth(store, seconds=5, limit=10)
    assert result['annual_growth'] == dict(attempted=1,succeeded=1,failed=0,no_data=0)
    row = store.get('TEST')
    assert row['financial_fetched'] == '2026-10-01'
    with store.connect() as conn:
        assert conn.execute('SELECT enriched FROM stocks').fetchone()[0] == enriched
    public = build_site.public_row(row)
    assert public['annual_income_history']['currency'] == 'USD'
    assert public['revenue_growth_3y'] == pytest.approx(10)
    assert public['annual_growth_version'] == 1
    assert 'private_note' not in public and 'annual_growth_attempted' not in public
    assert store.next_growth_enrichment() is None


def yahoo_record(day, value, currency='JPY', period='12M'):
    return dict(asOfDate=day,reportedValue=dict(raw=value),currencyCode=currency,periodType=period)


def test_direct_annual_records_verify_currency_and_periods():
    payload = {'timeseries': {'result': [
        {'annualTotalRevenue': [yahoo_record('2026-03-31',120),yahoo_record('2025-03-31',100),yahoo_record('2024-03-31',80,'USD'),yahoo_record('2026-06-30',90,period='3M')]},
        {'annualNetIncome': [yahoo_record('2026-03-31',12),yahoo_record('2025-03-31',10)]}]}}
    income,currency = model.parse_annual_records(payload)
    assert currency == 'JPY'
    assert list(income.columns) == list(pd.to_datetime(['2026-03-31','2025-03-31']))
    values = model.annual_growth_values(income,currency)
    assert values['revenue_growth_1y'] == pytest.approx(20)
    assert values['net_income_growth_1y'] == pytest.approx(20)
    assert values['revenue_growth_3y'] is None
    with pytest.raises(ValueError):
        model.parse_annual_records({'finance':{'error':{'code':'Failure'}}})
    with pytest.raises(ValueError):
        model.parse_annual_records({'timeseries':{'error':{'code':'Unauthorized'}}})
    assert model.parse_annual_records({'timeseries':{'result':[]}})[0].empty


def test_whole_universe_queue_balances_unprofiled_stocks_and_skips_secondary(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'test.sqlite')
    for region in model.REGIONS:
        for i in range(3):
            store.upsert_many([dict(symbol=f'{region}{i}',region_code=region,instrument='stock',active=True,main_listing=True,
                quote_currency='USD',market_cap_local=(3-i)*1e9)])
    store.upsert_many([dict(symbol='WRAPPER',region_code='ca',instrument='stock',active=True,main_listing=False)])
    queue=store.growth_candidates()
    assert queue[:6] == [region+'0' for region in model.REGIONS]
    assert 'WRAPPER' not in queue and len(queue)==18
    income=annual([120,100])
    income.loc['Net Income']=[12,10]
    monkeypatch.setattr(model,'fetch_annual_income',lambda symbol:(income,'JPY'))
    result=refresh_data.backfill_growth(store,seconds=10,limit=18)
    assert result['annual_growth']==dict(attempted=18,succeeded=18,failed=0,no_data=0)
    assert not store.growth_candidates()
    row=store.get('jp0')
    assert row['revenue_growth_1y']==pytest.approx(20)
    assert row['financial_currency']=='JPY' and row['income_period']=='FY 2026-06-30'
    assert row['net_margin']==pytest.approx(10)
    assert not row.get('financial_fetched')


def test_source_empty_checked_but_http_failure_remains_pending(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'test.sqlite')
    store.upsert_many([dict(symbol=s,region_code='us',instrument='stock',active=True,main_listing=True) for s in ['EMPTY','FAIL']])
    def fetch(symbol):
        if symbol=='FAIL': raise ValueError('HTTP 503')
        return pd.DataFrame(),None
    monkeypatch.setattr(model,'fetch_annual_income',fetch)
    result=refresh_data.backfill_growth(store,seconds=10,limit=2)
    assert result['annual_growth']==dict(attempted=2,succeeded=0,failed=1,no_data=1)
    assert store.get('EMPTY')['annual_growth_version']==1
    assert store.get('EMPTY')['annual_growth_status']=='no_data'
    assert not store.get('FAIL').get('annual_growth_version')
    assert not store.growth_candidates()
