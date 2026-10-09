from datetime import date
import gzip
import io
import json
import tarfile

import pandas as pd
import pytest

import app
import build_site
from technicals import technical_values, weekly_williams


def candles(close=15):
    return pd.DataFrame({'High':20.,'Low':10.,'Close':float(close)},index=pd.bdate_range('2026-06-01','2026-10-09'))


@pytest.mark.parametrize('close,expected,zone',[(10,-100,'Oversold'),(12,-80,'Oversold'),(15,-50,'Neutral'),(18,-20,'Overbought'),(20,0,'Overbought')])
def test_formula_and_inclusive_price_zones(close,expected,zone):
    row=weekly_williams(candles(close),date(2026,10,9))
    assert row['williams_r']==pytest.approx(expected)
    assert row['williams_zone']==zone
    assert row['williams_oversold_price_local']==12
    assert row['williams_overbought_price_local']==18
    assert row['williams_asof']=='2026-10-08'
    assert row['williams_provisional'] is True


def test_weekly_extrema_are_not_close_extrema_or_daily_lookback():
    frame=candles()
    frame.loc['2026-07-03','High']=200 # Older than 14 weekly candles ending Oct 9.
    frame.loc['2026-07-20','High']=30 # Inside 14 weeks, outside 14 daily sessions.
    frame.loc['2026-10-09']=[1000,1,900] # Excluded forming daily session.
    row=weekly_williams(frame,date(2026,10,9))
    assert row['williams_r']==pytest.approx(-75)
    assert row['williams_high_local']==30
    assert row['williams_close_local']==15
    completed=weekly_williams(candles(),date(2026,10,10))
    assert completed['williams_provisional'] is False
    assert completed['williams_asof']=='2026-10-09'


@pytest.mark.parametrize('mode',['short','close-only','flat','gap','invalid'])
def test_no_invented_range_or_missing_history(mode):
    frame=candles()
    if mode=='short':frame=frame.tail(15)
    if mode=='close-only':frame=frame[['Close']]
    if mode=='flat':frame.loc[:,:]=15
    if mode=='gap':frame=frame.drop(frame.loc['2026-09-07':'2026-09-11'].index)
    if mode=='invalid':frame.loc['2026-10-08','High']=None
    row=technical_values(frame,'USD',date(2026,10,9))
    assert row['williams_r'] is None
    assert row['williams_reason']
    assert row['williams_version']==1


def test_collection_scope_is_usd_inclusive_and_primary(tmp_path):
    store=app.Store(tmp_path/'db.sqlite')
    store.set_meta('fx',{'USD':{'rate':1},'KRW':{'rate':.001}})
    store.set_meta('collection_min_market_cap',20e9)
    rows=[('SMALL',19.99e9,'USD',True),('EDGE',20e9,'USD',True),('KRW',20e12,'KRW',True),('WRAPPER',30e9,'USD',False),('MISSING',None,'USD',True)]
    store.upsert_many([dict(symbol=s,region_code='us',instrument='stock',active=True,main_listing=p,market_cap_local=c,quote_currency=cur) for s,c,cur,p in rows])
    assert set(store.enrichment_candidates())=={'EDGE','KRW'}
    assert set(store.growth_candidates())=={'EDGE','KRW'}
    assert not store.claim_enrichment('SMALL')
    assert not store.claim_enrichment('WRAPPER')
    assert store.next_enrichment() in {'EDGE','KRW'}


def test_compact_publication_preserves_small_caps_in_recovery(tmp_path,monkeypatch):
    from test_publication import seeded_store
    store=seeded_store(tmp_path)
    store.upsert_many([dict(symbol='LARGE',name='Large',region_code='us',active=True,instrument='stock',quote_currency='USD',market_cap_local=20e9,technical_version=1,
        williams_version=1,williams_r=-85,williams_zone='Oversold',williams_r_period='14 weeks',technical_history={'daily':{'closes':[1],'dates':['2026-10-08']}})])
    monkeypatch.setattr(app.Store,'classify_listings',lambda *args,**kwargs:None)
    seed=tmp_path/'seed.tar.gz';output=tmp_path/'site'
    build_site.build_site(store.path,output,seed,True,True,20e9)
    data=json.load(gzip.open(output/'data/stocks.json.gz','rt'))
    assert len(data['rows'])==1
    row=dict(zip(data['fields'],data['rows'][0]))
    assert row['symbol']=='LARGE' and row['williams_r']==-85
    assert row['detail_key']
    technical=json.load(gzip.open(output/'data/technicals.json.gz','rt'))
    assert set(technical['technicals'])=={'LARGE'}
    with tarfile.open(seed) as archive:recovery=json.load(archive.extractfile('data.json'))
    assert any(r['symbol']=='TEST.US' for r in recovery['rows'])
    assert json.loads((output/'data/status.json').read_text())['snapshot']['minimum_market_cap']==20e9
