import sys
from pathlib import Path
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from statements import retain_statement, capture_statements, roic_proxy, line_unit


def frame(rows, dates=('2026-06-30','2025-06-30')):
    return pd.DataFrame.from_dict(rows,orient='index',columns=pd.to_datetime(dates))


def test_retention_keeps_old_years_only_in_matching_currency_and_restates():
    first=retain_statement(frame({'Net Income':[100,80],'Diluted EPS':[2,1.6],'Diluted Average Shares':[50,50],'Tax Rate For Calcs':[.2,.2]}),'JPY','annual',fetched='old')
    new=retain_statement(frame({'Net Income':[120,95]},('2027-06-30','2026-06-30')),'JPY','annual',first,fetched='new')
    assert [p['end_date'] for p in new['periods']]==['2025-06-30','2026-06-30','2027-06-30']
    assert new['periods'][0]['fetched_at']=='old'
    assert new['periods'][1]['values']['Net Income']==95
    changed=retain_statement(frame({'Net Income':[1,2]}),'USD','annual',first)
    assert len(changed['periods'])==2 and changed['currency']=='USD'
    assert first['units']['Diluted EPS']=='currency_per_share'
    assert first['units']['Diluted Average Shares']=='shares'
    assert first['units']['Tax Rate For Calcs']=='ratio'
    assert line_unit('Total Assets')=='currency'


def test_nonfinite_and_missing_values_do_not_become_zeroes():
    result=retain_statement(frame({'Net Income':[0,float('nan')],'Total Revenue':[100,float('inf')]}),'USD','annual')
    assert len(result['periods'])==1
    assert result['periods'][0]['values']=={'Net Income':0,'Total Revenue':100}


def history():
    return {'income_annual':retain_statement(frame({'Operating Income':[30,20],'Pretax Income':[25,15],'Tax Provision':[5,3]}),'USD','annual'),
      'balance_annual':retain_statement(frame({'Total Debt':[100,80],'Stockholders Equity':[200,180],'Cash And Cash Equivalents':[40,20]}),'USD','annual')}


def test_roic_uses_after_tax_operating_income_and_average_capital():
    result=roic_proxy(history())
    assert result['roic_proxy']==pytest.approx(24/250*100)
    assert result['roic_proxy_inputs']['opening_capital']==240
    assert 'average book capital' in result['roic_proxy_period']


@pytest.mark.parametrize('field,value',[('Pretax Income',-1),('Tax Provision',30),('Operating Income',None)])
def test_invalid_roic_inputs_are_withheld(field,value):
    data=history();data['income_annual']['periods'][-1]['values'][field]=value
    assert roic_proxy(data)['roic_proxy'] is None


def test_missing_balance_or_currency_mismatch_does_not_use_roe():
    data=history();data['balance_annual']['currency']='JPY';assert roic_proxy(data)['roic_proxy'] is None
    data=history();data['balance_annual']['periods'].pop(0);assert roic_proxy(data)['roic_proxy'] is None


def test_capture_failure_retains_cache_and_marks_retryable():
    class Ticker:
        def get_income_stmt(self,**kwargs):return frame({'Net Income':[10,9]})
        def get_balance_sheet(self,**kwargs):raise RuntimeError('/private SECRET')
        def get_cashflow(self,**kwargs):return pd.DataFrame()
    result,frames=capture_statements(Ticker(),'USD',{'statement_history':history()})
    assert result['statement_version']==0
    assert result['statement_history']['balance_annual']['periods']
    assert '/private' not in str(result) and 'SECRET' not in str(result)
    assert result['statement_history']['cashflow_annual']['status']=='no_data'
    assert result['statement_history']['income_quarterly']['frequency']=='quarterly'


def test_share_issued_is_a_share_count_and_empty_set_remains_pending():
    assert line_unit('Share Issued')=='shares'
    class Empty:
        def get_income_stmt(self,**kwargs):return pd.DataFrame()
        def get_balance_sheet(self,**kwargs):return pd.DataFrame()
        def get_cashflow(self,**kwargs):return pd.DataFrame()
    result,_=capture_statements(Empty(),'USD')
    assert result['statement_version']==0
    assert 'all_statements' in result['statement_errors']


def test_financial_sector_is_not_given_industrial_roic():
    class Source:
        def get_income_stmt(self,**kwargs):return frame({'Operating Income':[30,20],'Pretax Income':[25,15],'Tax Provision':[5,3]})
        def get_balance_sheet(self,**kwargs):return frame({'Total Debt':[100,80],'Stockholders Equity':[200,180],'Cash And Cash Equivalents':[40,20]})
        def get_cashflow(self,**kwargs):return pd.DataFrame()
    values,_=capture_statements(Source(),'USD',{'sector':'Financial Services'})
    assert values['statement_version']==1
    assert values['roic_proxy'] is None and 'financial-sector' in values['roic_proxy_reason']


def test_statement_queue_preserves_profile_age_and_cools_down_after_failure(tmp_path,monkeypatch):
    import app as model
    import refresh_data
    import statements
    store=model.Store(tmp_path/'queue.sqlite')
    store.upsert_many([dict(symbol=s,region_code='us',active=True,instrument='stock',main_listing=True,
        financial_currency='USD',financial_fetched='2026-01-01',market_cap_local=100) for s in ['AAPL','NEXT']],enriched=True)
    with store.connect() as conn:
        old=conn.execute("SELECT enriched FROM stocks WHERE symbol='AAPL'").fetchone()[0]
    calls=[]
    def source(ticker,currency,previous):
        calls.append(previous['symbol'])
        return dict(statement_version=0,statement_errors={'all_statements':'No data'},statement_history={}),{}
    monkeypatch.setattr(statements,'capture_statements',source)
    monkeypatch.setattr(model.yf,'Ticker',lambda symbol:symbol)
    result=refresh_data.backfill_statements(store,seconds=10,limit=10)
    assert calls==['AAPL'] and result['statements']['failed']==1
    assert store.get('AAPL')['financial_fetched']=='2026-01-01'
    with store.connect() as conn:
        assert conn.execute("SELECT enriched FROM stocks WHERE symbol='AAPL'").fetchone()[0]==old
    refresh_data.backfill_statements(store,seconds=10,limit=10)
    assert calls==['AAPL','NEXT']
