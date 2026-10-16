"""Intraday jobs must bypass daily caches without polling closed exchanges."""
import pandas as pd
import pytest
import app
import etfs
import refresh_data
from market_sessions import SessionCutoff


@pytest.mark.parametrize('stamp,attempted,expected',[
    ('2026-10-16T11:17:00Z',None,True),
    ('2026-10-16T11:17:00Z','2026-10-16T08:17:00Z',True),
    ('2026-10-16T11:17:00Z','2026-10-16T10:18:00Z',False),
    ('2026-10-16T11:17:00Z','2026-10-16T10:17:00Z',True),
    ('2026-10-16T06:59:00Z',None,False),
    ('2026-10-16T17:00:00Z',None,False),
    ('2026-10-17T11:17:00Z',None,False),
    ('2026-12-25T11:17:00Z',None,False),
])
def test_intraday_session_and_hourly_attempt_limit(stamp,attempted,expected):
    assert bool(SessionCutoff(asof=stamp,region='gb').intraday_ready(attempted)) is expected


def test_evening_run_collects_a_newly_completed_close_without_repolling_closed_market():
    london = SessionCutoff(asof='2026-10-16T16:10:00Z',region='gb')
    assert london.intraday_ready('2026-10-16T14:00:00Z', completed_asof='2026-10-15')
    assert not london.intraday_ready('2026-10-16T14:00:00Z', completed_asof='2026-10-16')
    assert not london.intraday_ready('2026-10-16T15:45:00Z', completed_asof='2026-10-15')
    usa = SessionCutoff(asof='2026-10-16T21:00:00Z',region='us')
    assert usa.intraday_ready('2026-10-16T18:00:00Z', completed_asof='2026-10-15')
    assert not usa.intraday_ready('2026-10-16T18:00:00Z', completed_asof='2026-10-16')


class MiddayCutoff(SessionCutoff):
    def __init__(self,*args,**kwargs):
        kwargs.setdefault('asof','2026-10-16T11:17:00Z')
        super().__init__(*args,**kwargs)


def history():
    return pd.DataFrame({'High':110.,'Low':90.,'Close':100.},index=pd.bdate_range('2025-01-02','2026-10-16'))


def test_stock_intraday_refreshes_fresh_daily_cache_but_not_recent_attempt_or_closed_market(tmp_path,monkeypatch):
    import market_sessions
    monkeypatch.setattr(market_sessions,'SessionCutoff',MiddayCutoff)
    store=app.Store(tmp_path/'stocks.sqlite')
    monkeypatch.setattr(store,'classify_listings',lambda **kwargs:None)
    for symbol,region,exchange,attempt in [('DUE','gb','LSE','08:17'),('RECENT','gb','LSE','10:45'),('CLOSED','us','NYSE','08:17')]:
        store.upsert_many([dict(symbol=symbol,name=symbol,region_code=region,exchange=exchange,
            active=True,instrument='stock',main_listing=True,market_cap_local=30e9,quote_currency='USD',
            technical_version=5,williams_version=2,williams_monthly_version=1,
            technical_asof='2026-10-15',technical_fetched='2026-10-16T08:17:00+00:00',
            technical_attempted=pd.Timestamp('2026-10-16T'+attempt+':00Z').timestamp())])
    calls=[]
    class Ticker:
        def __init__(self,symbol):calls.append(symbol)
        def history(self,**kwargs):return history()
        def get_history_metadata(self):return {'currency':'GBP','exchangeName':'LSE'}
    monkeypatch.setattr(app.yf,'Ticker',Ticker)
    result=refresh_data.backfill_technicals(store,seconds=10,limit=10,intraday=True)
    assert calls==['DUE']
    assert result['mode']=='intraday' and result['technicals']['succeeded']==1


def test_etf_intraday_bypasses_daily_cache_and_only_refreshes_verified_old_attempts(tmp_path,monkeypatch):
    monkeypatch.setattr(etfs,'SessionCutoff',MiddayCutoff)
    catalogue=[dict(row,catalogue_price_local=100.) for row in etfs.import_catalogue()[:3]]
    rows=[]
    for base,attempt,status in zip(catalogue,['08:17','10:45','08:17'],['Verified','Verified','Not checked']):
        rows.append(dict(base,catalogue_price_local=100.,mapping_status=status,etf_history_version=8,
            technical_fetched='2026-10-16T08:17:00+00:00',history_attempted='2026-10-16T'+attempt+':00+00:00',technical_asof='2026-10-15'))
    monkeypatch.setattr(etfs,'import_catalogue',lambda:catalogue)
    path=tmp_path/'etfs.json';etfs.write_cache({'version':1,'rows':rows},path)
    calls=[]
    def fetch(symbol):
        calls.append(symbol);row=next(r for r in rows if r['yahoo_symbol']==symbol)
        return history(),dict(symbol=symbol,currency='GBp',exchangeName='LSE',instrumentType='ETF',longName=row['name'])
    result=etfs.refresh(path,seconds=10,limit=10,fetcher=fetch,intraday=True)
    assert calls==[rows[0]['yahoo_symbol']]
    assert result['refresh']['mode']=='intraday' and result['refresh']['succeeded']==1, result['rows'][0]
