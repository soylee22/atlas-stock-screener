import gzip
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import etfs
from technicals import weekly_williams


def sample():
    return dict(etfs.import_catalogue()[0], catalogue_price_local=100.)


def history():
    dates=pd.bdate_range('2026-04-01','2026-10-07')
    frame=pd.DataFrame({'High':110.,'Low':90.,'Close':100.,'Volume':1000.,'Dividends':0.},index=dates)
    frame.iloc[-1,frame.columns.get_loc('Close')]=92.
    frame.iloc[20,frame.columns.get_loc('Dividends')]=.5
    return frame


def metadata(row):
    return dict(symbol=row['yahoo_symbol'],currency='GBp',exchangeName='LSE',instrumentType='ETF',longName=row['name'])


def test_catalogue_units_and_coverage():
    rows=etfs.import_catalogue()
    assert len(rows)==len({r['symbol'] for r in rows})==729
    assert sum(r['expense_ratio'] is not None for r in rows)==724
    assert sum(r['nav_return_3y'] is not None for r in rows)==605
    assert sum(r['aum_local'] is not None for r in rows)==688
    row=next(r for r in rows if r['symbol']=='CSP1')
    converted=etfs.dollarise(row,{'GBP':{'rate':1.25}})
    assert converted['price']==pytest.approx(63565*.01*1.25)
    assert converted['aum']==pytest.approx(row['aum_local']*1.25)
    assert converted['expense_ratio']==.07
    assert converted['nav_return_3y']==row['nav_return_3y']
    assert converted['nav_return_currency'] is None
    assert etfs.dollarise(row,{})['aum'] is None
    assert converted['aum']>20e9
    assert any(etfs.dollarise(r,{'GBP':{'rate':1.25}}).get('aum',0) and etfs.dollarise(r,{'GBP':{'rate':1.25}})['aum']<20e9 for r in rows)


@pytest.mark.parametrize('change',[
    {'symbol':'CSP1'}, {'exchangeName':'NYQ'}, {'instrumentType':'EQUITY'},
    {'currency':'GBP'}, {'currency':'EUR'}, {'longName':'An unrelated fund'},
])
def test_mapping_withholds_wrong_tickers(change):
    row=sample(); meta=metadata(row);meta.update(change)
    with pytest.raises(ValueError):
        etfs.apply_history(row,history(),meta,today=date(2026,10,9))
    assert row['mapping_status']=='Not checked'


def test_fund_history_and_import_dates():
    row=sample(); result=etfs.apply_history(row,history(),metadata(row),today=date(2026,10,9))
    assert result['williams_r']==pytest.approx(-90)
    assert result['mapping_status']=='Verified'
    assert result['price_local']==92
    assert result['catalogue_date']=='2026-10-09'
    assert result['nav_return_3y']==row['nav_return_3y']
    assert result['expense_ratio']==row['expense_ratio']
    assert result['distribution_events'][0]['amount']==.5
    assert result['technical_history']['weekly']['closes']


def test_weekly_formula_uses_extrema_and_latest_close_without_clamping():
    row=sample(); h=history();h.iloc[-10,h.columns.get_loc('Close')]=110.1
    strict=weekly_williams(h,date(2026,10,9))
    assert strict['williams_r'] is None
    fund=etfs.apply_history(row,h,metadata(row),today=date(2026,10,9))
    assert fund['williams_r']==pytest.approx(-90)
    assert fund['williams_daily_range_discrepancies']==1
    h.iloc[-1,h.columns.get_loc('Close')]=120
    assert etfs.apply_history(row,h,metadata(row),today=date(2026,10,9))['williams_r'] is None
    h.iloc[-1,h.columns.get_loc('Close')]=92
    h.iloc[-10,h.columns.get_loc('High')]=0
    assert etfs.apply_history(row,h,metadata(row),today=date(2026,10,9))['williams_r'] is None


def test_publication_is_separate_and_preserves_small_funds(tmp_path):
    row=sample(); row['aum_local']=1e6
    cache_path=tmp_path/'etfs.json';etfs.write_cache({'version':1,'rows':[row],'refresh':{}},cache_path)
    target=tmp_path/'site';status=etfs.publish(target,{'GBP':{'rate':1.25}},'fixed',cache_path)
    assert status['total']==729
    source=json.loads(gzip.decompress((target/'stocks.json.gz').read_bytes()))
    rows=[dict(zip(source['fields'],v)) for v in source['rows']]
    assert len(rows)==729
    assert all(r['instrument']=='etf' for r in rows)
    assert not any('net_income' in r for r in rows)
    assert json.loads((target/'schema.json').read_text())['universe']=='etf'
    seed=json.loads(gzip.decompress((target/'seed.json.gz').read_bytes()))
    assert len(seed['rows'])==729
    assert {r['nav_return_3y_period'] for r in rows}=={'3Y cumulative · imported 2026-10-09 · return currency unknown'}


def test_failed_refresh_retains_successful_history_and_metadata(tmp_path,monkeypatch):
    imported=sample();old=etfs.apply_history(imported,history(),metadata(imported),today=date(2026,10,9))
    old.update(technical_fetched='2020-01-01T00:00:00+00:00',history_attempted='2020-01-01T00:00:00+00:00',etf_history_version=3)
    monkeypatch.setattr(etfs,'import_catalogue',lambda:[imported])
    path=tmp_path/'etfs.json';etfs.write_cache(dict(version=1,rows=[old]),path)
    def failed(symbol):raise RuntimeError('Yahoo request failed')
    result=etfs.refresh(path,seconds=5,limit=1,fetcher=failed)['rows'][0]
    assert result['williams_r']==old['williams_r']
    assert result['price_local']==old['price_local']
    assert result['mapping_status']=='Verified'
    assert result['history_error']
    assert result['catalogue_date']==imported['catalogue_date']


def test_empty_cache_recovers_public_history(tmp_path,monkeypatch):
    path=tmp_path/'etfs.json';cache=dict(version=1,rows=[sample()],refresh={})
    class Response:
        status_code=200
        content=gzip.compress(json.dumps(cache).encode())
        def raise_for_status(self):pass
    monkeypatch.setattr(etfs.requests,'get',lambda *a,**k:Response())
    assert etfs.restore(path)
    assert etfs.read_cache(path)==cache
    assert not etfs.restore(path)


def test_etcs_need_stronger_identity_and_short_maturity_is_not_inverse():
    rows=etfs.import_catalogue()
    row=next(r for r in rows if r['symbol']=='SGLD')
    meta=dict(symbol='SGLD.L',currency='USD',exchangeName='LSE',instrumentType='EQUITY',longName='Invesco Physical Gold ETC')
    assert etfs.identity_error(row,meta) is None
    meta['longName']='Invesco Physical Silver ETC'
    assert etfs.identity_error(row,meta)
    assert next(r for r in rows if r['symbol']=='MINT')['exposure']=='Standard / unspecified'
    assert next(r for r in rows if 'Leverage' in r['name'] and r['exposure']=='Leveraged')['exposure']=='Leveraged'


def test_discontinuity_starts_new_segment_without_guessing_units():
    row=sample();h=history()
    h.iloc[-1,h.columns.get_loc('Close')]=1.
    h.iloc[-1,h.columns.get_loc('High')]=1.1
    h.iloc[-1,h.columns.get_loc('Low')]=.9
    row['catalogue_price_local']=1.
    result=etfs.apply_history(row,h,metadata(row),today=date(2026,10,9))
    assert result['price_local']==1.
    assert result['williams_r'] is None
    assert result['sma_200d_distance'] is None
    assert result['below_52w_high'] is None
    assert result['history_regime_start']=='2026-10-07'
    assert result['history_quality_note']
    assert len(result['technical_history']['daily']['closes'])==1


def test_latest_unit_conflict_replaces_bad_cached_quote_with_catalogue(tmp_path,monkeypatch):
    imported=sample();old=dict(imported,price_local=1.,mapping_status='Verified',technical_fetched='2020-01-01T00:00:00+00:00',etf_history_version=2)
    monkeypatch.setattr(etfs,'import_catalogue',lambda:[imported])
    path=tmp_path/'etfs.json';etfs.write_cache(dict(version=1,rows=[old]),path)
    h=history();h['Close']=1.
    result=etfs.refresh(path,seconds=5,limit=1,fetcher=lambda _: (h,metadata(imported)))['rows'][0]
    assert result['price_local']==imported['price_local']
    assert result['mapping_status']=='Unavailable / unconfirmed'
    assert not result.get('technical_fetched')
    assert 'twenty-fold' in result['mapping_reason']
