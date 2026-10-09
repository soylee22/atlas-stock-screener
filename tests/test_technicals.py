import sys
from pathlib import Path
from datetime import date
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from technicals import technical_values
from currency_validation import match_currencies,verified_totals
import app


def test_weekly_sma_is_weekly_closes_and_current_week_is_excluded():
    days=pd.bdate_range('2020-01-01','2026-10-07')
    frame=pd.DataFrame({'Close':[i*i for i in range(1,len(days)+1)]},index=days)
    result=technical_values(frame,'GBP',date(2026,10,7))
    completed=frame['Close'][frame.index.date<date(2026,10,7)]
    weekly=completed.resample('W-FRI').last().dropna()
    weekly=weekly[weekly.index.date<date(2026,10,7)]
    assert result['sma_200d_local']==pytest.approx(completed.tail(200).mean())
    assert result['sma_200w_local']==pytest.approx(weekly.tail(200).mean())
    assert result['sma_200w_local']!=pytest.approx(completed.tail(1000).mean())
    assert result['technical_asof']=='2026-10-06'
    assert result['technical_history']['weekly']['dates'][-1]=='2026-10-02'
    assert len(result['technical_history']['daily']['closes'])==500
    assert len(result['technical_history']['weekly']['closes'])==260


def test_short_price_history_does_not_invent_long_sma():
    result=technical_values(pd.DataFrame({'Close':[10]*20},index=pd.bdate_range('2026-01-01',periods=20)),'USD',date(2026,10,7))
    assert result['sma_200d_distance'] is None
    assert result['sma_200w_distance'] is None


def test_currencies_require_exact_raw_amount_and_period_metadata():
    frames={'income_annual':pd.DataFrame([[9000]],index=['Total Revenue'],columns=pd.to_datetime(['2025-12-31']))}
    payload={'timeseries':{'result':[{'annualTotalRevenue':[dict(asOfDate='2025-12-31',periodType='12M',currencyCode='KRW',reportedValue={'raw':9000})]}]}}
    assert match_currencies(payload,frames)=={'income_annual':'KRW'}
    payload['timeseries']['result'][0]['annualTotalRevenue'][0]['reportedValue']['raw']=9
    assert match_currencies(payload,frames)=={'income_annual':None}


def test_verified_currency_survives_store_merge_without_aging_profile(tmp_path):
    store=app.Store(tmp_path/'test.sqlite')
    store.upsert_many([dict(symbol='BOB',region_code='kr',financial_currency='USD',financial_fetched='2026-10-01')],enriched=True)
    store.upsert_many([dict(symbol='BOB',region_code='kr',financial_currency='KRW',financial_currency_version=2)])
    assert store.get('BOB')['financial_currency']=='KRW'


def test_rolling_technical_queue_advances_missing_data_and_refreshes_stale(tmp_path,monkeypatch):
    import primary_listings
    monkeypatch.setattr(primary_listings, 'catalogue', lambda: {
        f'T{i}': dict(primary=True, source=f'NYSE:T{i}', type='stock', types=['common']) for i in range(6)})
    import refresh_data
    store=app.Store(tmp_path/'queue.sqlite')
    for i in range(6):
        store.upsert_many([dict(symbol=f'T{i}',name=f'Test {i}',region_code='us',exchange='NYSE',instrument='stock',active=True,
             quote_currency='USD',market_cap_local=(100-i)*1e9,technical_version=1 if i<3 else 0,technical_fetched='2000-01-01')])
    requested=[]
    class Ticker:
        def __init__(self,symbol):requested.append(symbol)
        def history(self,**kwargs):return pd.DataFrame({'Close':[10]*250},index=pd.bdate_range('2025-01-01',periods=250))
        def get_history_metadata(self):return {'currency':'USD'}
    monkeypatch.setattr(app.yf,'Ticker',Ticker)
    refresh_data.backfill_technicals(store,seconds=10,limit=4)
    assert {'T3','T4','T5'}.issubset(requested)
    assert any(symbol in {'T0','T1','T2'} for symbol in requested)
