import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dividend_quality import quality_values


def sample(count=6):
    years = range(2020, 2020+count)
    cash = [dict(end_date=f'{y}-12-31', values={'Operating Cash Flow':120*1.1**(y-2020),
        'Capital Expenditure':-20*1.1**(y-2020), 'Common Stock Dividend Paid':-40*1.08**(y-2020)}) for y in years]
    events = [dict(date=f'{y}-06-01', amount=1.08**(y-2020)) for y in range(2018,2026)]
    return dict(sector='Technology', statement_fetched='2026-10-14', dividend_fetched='2026-10-14',
        dividend_history_start='2018-06-01', dividend_currency='USD', dividend_events=events,
        statement_history={'cashflow_annual':dict(currency='USD',periods=cash),
        'balance_quarterly':dict(currency='USD',periods=[dict(end_date='2026-06-30',values={'Current Assets':120,'Current Liabilities':100})])})


def test_payout_growth_and_matched_dividends():
    result = quality_values(sample())
    assert result['fcf_growth_5y'] == pytest.approx(10)
    assert result['div_fiscal_growth_5y'] == pytest.approx(8)
    assert result['fcf_div_growth_gap_5y'] == pytest.approx(2)
    assert result['fcf_payout'] == pytest.approx(40*(1.08/1.1)**5)
    assert result['current_ratio'] == pytest.approx(1.2)
    assert result['fcf_payout_inputs']['period_ends'] == ['2025-12-31']


def test_short_history_does_not_substitute_three_years():
    result = quality_values(sample(4))
    assert result['fcf_growth_3y'] == pytest.approx(10)
    assert result['fcf_growth_5y'] is None
    assert '6 annual records' in result['dividend_quality_reasons']['fcf_growth_5y']


@pytest.mark.parametrize('base', [0, -100])
def test_bad_base_and_non_positive_payout(base):
    row = sample()
    row['statement_history']['cashflow_annual']['periods'][0]['values']['Operating Cash Flow'] = base + 20
    result = quality_values(row)
    assert result['fcf_growth_5y'] is None
    row['statement_history']['cashflow_annual']['periods'][-1]['values']['Operating Cash Flow'] = 0
    assert quality_values(row)['fcf_payout'] is None


def test_missing_data_and_conflict_not_zero():
    row = sample()
    row['statement_history']['cashflow_annual']['periods'][-1]['values'].pop('Common Stock Dividend Paid')
    assert quality_values(row)['fcf_payout'] is None
    row['statement_history']['cashflow_annual']['status'] = 'currency_conflict'
    assert quality_values(row)['fcf_growth_1y'] is None
    row = sample()
    row['dividend_history_start'] = '2021-06-01'
    assert quality_values(row)['fcf_div_growth_gap_5y'] is None
    row['statement_history']['balance_quarterly']['periods'][0]['values']['Current Liabilities'] = 0
    assert quality_values(row)['current_ratio'] is None


def test_fiscal_year_dividend_aggregation_and_gap():
    row = sample()
    for p in row['statement_history']['cashflow_annual']['periods']:
        p['end_date'] = p['end_date'].replace('12-31','03-31')
    result = quality_values(row)
    assert result['div_fiscal_growth_5y'] == pytest.approx(8)
    assert result['dividend_quality_history'][-1]['start_exclusive'] == '2024-03-31'
    row['statement_history']['cashflow_annual']['periods'].pop(2)
    assert quality_values(row)['fcf_growth_3y'] is None


def test_quarterly_payout_and_gap_fallback():
    row = sample()
    periods = [dict(end_date=d,values={'Operating Cash Flow':50,'Capital Expenditure':-10,
        'Common Stock Dividend Paid':-10}) for d in ['2025-09-30','2025-12-31','2026-03-31','2026-06-30']]
    row['statement_history']['cashflow_quarterly'] = dict(currency='USD',periods=periods)
    result = quality_values(row)
    assert result['fcf_payout'] == 25
    assert result['fcf_payout_period'].startswith('TTM 2026-06-30')
    periods[1]['end_date'] = '2025-10-15'
    assert quality_values(row)['fcf_payout_period'].startswith('FY 2025-12-31')


def test_financial_businesses_are_withheld():
    row = sample()
    row['sector'] = 'Financial Services'
    result = quality_values(row)
    assert result['current_ratio'] is None
    assert result['fcf_payout'] is None
    assert result['fcf_growth_5y'] is None
    assert 'financial businesses' in result['dividend_quality_reasons']['fcf_growth_5y']


def test_quarterly_payout_without_annual_source():
    row = sample()
    row['statement_history'].pop('cashflow_annual')
    row['statement_history']['cashflow_quarterly'] = dict(currency='USD',periods=[
        dict(end_date=d,values={'Operating Cash Flow':40,'Capital Expenditure':-10,'Cash Dividends Paid':-9})
        for d in ['2025-09-30','2025-12-31','2026-03-31','2026-06-30']])
    result = quality_values(row)
    assert result['fcf_payout'] == 30
    assert 'potentially including preferred' in result['fcf_payout_basis']


def test_database_chart_export_and_compact_projection(tmp_path, monkeypatch):
    import app as model
    store = model.Store(tmp_path/'test.sqlite')
    store.set_meta('fx', {'USD':{'rate':1}})
    row = {**sample(), 'symbol':'TEST', 'name':'Test Company', 'region_code':'us',
        'instrument':'stock', 'active':True, 'main_listing':True,
        'quote_currency':'USD', 'price_local':90, 'market_cap_local':30e9}
    store.upsert_many([row])
    monkeypatch.setattr(model,'store',store)
    filters = '[{"field":"fcf_growth_5y","op":"gte","value":9}]'
    selected,total = model.select_rows('', '', filters,'fcf_payout','asc',False,'',100,0,False)
    assert total == 1 and selected[0]['current_ratio'] == 1.2
    plotted = model.chart(x='fcf_growth_5y', y='fcf_payout', filters=filters,main_only=False)
    assert len(plotted['rows']) == 1
    assert plotted['coverage'] == {'awaiting':0, 'unavailable':0}
    assert plotted['rows'][0]['fcf_growth_5y_period'].startswith('FY 2025')
    export = model.export(filters=filters, columns='fcf_growth_5y,fcf_payout',main_only=False)
    assert b'fcf_payout_basis' in export.body and b'Common dividend cash outflow' in export.body
    monkeypatch.setattr(store,'classify_listings',lambda **kw:None)
    compact = model.technical_screen(include_history=False)
    import json
    payload = json.loads(compact.body)
    assert payload['technicals'] == {}
    assert 'fcf_growth_5y' in payload['fields']
