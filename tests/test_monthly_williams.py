from datetime import date
import pandas as pd
import pytest

from technicals import monthly_williams, technical_values


def history(close=15):
    return pd.DataFrame({'High':20.,'Low':10.,'Close':float(close)},
        index=pd.bdate_range('2024-05-01','2026-10-09'))


@pytest.mark.parametrize('close,expected,zone',[(10,-100,'Oversold'),(12,-80,'Oversold'),(15,-50,'Neutral'),(18,-20,'Overbought'),(20,0,'Overbought')])
def test_monthly_formula_and_zones(close,expected,zone):
    row=monthly_williams(history(close),date(2026,10,10))
    assert row['williams_monthly_r']==pytest.approx(expected)
    assert row['williams_monthly_zone']==zone
    assert row['williams_monthly_oversold_price_local']==12
    assert row['williams_monthly_overbought_price_local']==18
    assert row['williams_monthly_asof']=='2026-10-09'
    assert row['williams_monthly_provisional'] is True
    assert 'ending 2026-10-31' in row['williams_monthly_r_period']
    assert 'developing month' in row['williams_monthly_r_period']


def test_fourteen_calendar_months_not_weeks_or_close_extrema():
    frame=history()
    frame.loc['2025-08-29','High']=300  # Before the September 2025 to October 2026 window.
    frame.loc['2025-09-01','High']=30   # Inside that window, well outside 14 weeks.
    row=technical_values(frame,'USD',date(2026,10,10))
    assert row['williams_monthly_r']==pytest.approx(-75)
    assert row['williams_monthly_high_local']==30
    assert row['williams_r']==pytest.approx(-50)
    assert row['williams_provisional'] is False
    assert row['williams_monthly_provisional'] is True
    scaled=monthly_williams(frame*100,date(2026,10,10))
    assert scaled['williams_monthly_r']==pytest.approx(row['williams_monthly_r'])


@pytest.mark.parametrize('mode',['short','close-only','flat','gap','invalid'])
def test_monthly_missing_and_invalid_history_is_not_invented(mode):
    frame=history()
    if mode=='short':frame=frame.loc['2025-10-01':]
    if mode=='close-only':frame=frame[['Close']]
    if mode=='flat':frame.loc[:,:]=15
    if mode=='gap':frame=frame.drop(frame.loc['2026-05-01':'2026-05-31'].index)
    if mode=='invalid':frame.loc['2026-09-01','High']=None
    row=monthly_williams(frame,date(2026,10,10))
    assert row['williams_monthly_r'] is None
    assert row['williams_monthly_reason']
    assert row['williams_monthly_version']==1


@pytest.mark.parametrize('stamp,provisional',[
    ('2026-01-30T16:59:00Z',True),('2026-01-30T17:00:00Z',False),
    ('2026-01-31T10:00:00Z',False),
    ('2024-03-28T16:59:00Z',True),('2024-03-28T17:00:00Z',False)])
def test_month_completion_uses_last_scheduled_session_and_allowance(stamp,provisional):
    frame=pd.DataFrame({'High':20.,'Low':10.,'Close':15.},index=pd.bdate_range('2022-01-03',pd.Timestamp(stamp).date()))
    row=monthly_williams(frame,asof=stamp,region='gb')
    assert row['williams_monthly_provisional'] is provisional
    assert row['williams_monthly_r']==-50


def test_monthly_source_gaps_are_separate_from_weekly_gaps():
    frame=history().drop(pd.Timestamp('2025-09-02'))
    row=technical_values(frame,'GBP',asof='2026-10-09T19:00:00Z',region='gb')
    assert '2025-09-02' in row['williams_monthly_source_note']
    assert '2025-09-02' not in (row['williams_source_note'] or '')
    assert row['williams_monthly_r']==-50
    assert row['williams_monthly_asof']=='2026-10-09'


def test_forming_daily_bar_excluded_before_close_from_both_timeframes():
    frame=history()
    frame.loc['2026-10-09']=[1000,1,900]
    row=technical_values(frame,'GBP',asof='2026-10-09T12:00:00Z',region='gb')
    assert row['williams_monthly_asof']=='2026-10-08'
    assert row['williams_monthly_r']==-50
    assert row['williams_r']==-50


def test_monthly_chart_coverage_and_export_keep_independent_dates(tmp_path,monkeypatch):
    import app
    store=app.Store(tmp_path/'monthly.sqlite')
    common=dict(region_code='us',instrument='stock',active=True,williams_version=2)
    store.upsert_many([dict(common,symbol='VALID',name='Valid',williams_r=-50,williams_monthly_r=-90,
        williams_monthly_version=1,williams_monthly_asof='2026-10-09',williams_monthly_r_period='14 monthly candles',
        williams_monthly_provisional=True,williams_monthly_source_note='Yahoo missing 2025-09-02'),
        dict(common,symbol='PENDING'),dict(common,symbol='SHORT',williams_monthly_version=1)])
    monkeypatch.setattr(app,'store',store)
    chart=app.chart(x='williams_r',y='williams_monthly_r',main_only=False)
    assert chart['coverage']==dict(awaiting=1,unavailable=1)
    assert chart['rows'][0]['williams_monthly_r']==-90
    assert chart['rows'][0]['williams_monthly_provisional']==True  # SQLite JSON projects booleans as 0/1.
    assert chart['rows'][0]['williams_monthly_source_note']=='Yahoo missing 2025-09-02'
    exported=app.export(columns='williams_monthly_r',main_only=False).body.decode()
    assert 'williams_monthly_asof' in exported and '14 monthly candles' in exported
    assert 'Yahoo missing 2025-09-02' in exported


def test_monthly_publication_preserves_sources_but_not_private_data():
    import build_site
    row=technical_values(history(),'USD',date(2026,10,10))
    row['private_note']='Not for publication'
    public=build_site.public_row(row)
    assert public['williams_monthly_r']==-50
    assert public['williams_monthly_high_local']==20
    assert public['williams_monthly_provisional'] is True
    assert 'private_note' not in public
    assert {'williams_monthly_r','williams_monthly_version','williams_monthly_source_note','williams_monthly_r_period'}<=set(build_site.INDEX_FIELDS)


def test_fund_monthly_history_uses_existing_unit_guard_and_survives_publication(tmp_path):
    import etfs,json,gzip
    row=etfs.import_catalogue()[0]
    frame=history(close=15)
    row=dict(row,catalogue_price_local=15)
    metadata=dict(symbol=row['yahoo_symbol'],exchangeName='LSE',instrumentType='ETF',currency=row['quote_currency'],longName=row['name'])
    computed=etfs.apply_history(row,frame,metadata,today=date(2026,10,10))
    assert computed['williams_monthly_r']==-50
    assert computed['williams_monthly_provisional'] is True
    cache=tmp_path/'etfs.json'
    etfs.write_cache(dict(version=1,rows=[computed],refresh={}),cache)
    site=tmp_path/'public'
    etfs.publish(site,{'GBP':{'rate':1.25}},'fixed',cache)
    snapshot=json.loads(gzip.decompress((site/'stocks.json.gz').read_bytes()))
    published=next(dict(zip(snapshot['fields'],r)) for r in snapshot['rows'] if r[0]==row['symbol'])
    assert published['williams_monthly_r']==-50
    assert published['williams_monthly_version']==1
    assert published['williams_monthly_r_period']==computed['williams_monthly_r_period']


def test_fund_reuses_recorded_close_only_with_matching_price_anchors(monkeypatch):
    import etfs
    row=etfs.import_catalogue()[0]
    row=dict(row,catalogue_price_local=15)
    meta=dict(symbol=row['yahoo_symbol'],exchangeName='LSE',instrumentType='ETF',currency=row['quote_currency'],longName=row['name'])
    old=etfs.apply_history(row,history(),meta,today=date(2026,10,10))
    fresh=history();fresh.loc['2026-10-09','Close']=None
    result=etfs.apply_history(old,fresh,meta,today=date(2026,10,10))
    assert result['williams_monthly_r']==-50
    assert result['williams_r']==-50
    assert result['technical_asof']=='2026-10-09'
    assert 'previously returned Yahoo Close' in result['williams_monthly_source_note']
    assert '2026-10-09' in result['williams_source_note']
    changed=fresh.copy();changed.loc[:'2026-10-08','Close']=10
    assert etfs.retain_reported_closes(old,changed)==[]
    assert pd.isna(changed.loc['2026-10-09','Close'])
    incomplete=etfs.apply_history(old,changed,meta,today=date(2026,10,10))
    assert incomplete['williams_monthly_r']==-100
    assert incomplete['williams_monthly_asof']=='2026-10-08'
    changed.loc['2026-09-01','High']=None
    incomplete=etfs.apply_history(old,changed,meta,today=date(2026,10,10))
    assert incomplete['williams_monthly_r']==-50
    assert 'retained reading' in incomplete['williams_monthly_r_period']
    assert 'Retained the previous dated reading' in incomplete['williams_monthly_source_note']
    monkeypatch.setattr(etfs,'close_checkpoint',lambda:{row['symbol']:dict(currency=row['quote_currency'],closes={'2026-10-07':15.,'2026-10-08':15.,'2026-10-09':15.})})
    recovered=etfs.apply_history(row,fresh,meta,today=date(2026,10,10))
    assert recovered['williams_monthly_r']==-50
    assert recovered['technical_asof']=='2026-10-09'
    wrong=dict(row,quote_currency='wrong')
    assert etfs.retain_reported_closes(wrong,fresh.copy())==[]


def test_fund_missing_trailing_close_uses_dated_available_history_not_invented_price(monkeypatch):
    import etfs
    monkeypatch.setattr(etfs,'close_checkpoint',lambda:{})
    row=dict(etfs.import_catalogue()[0],catalogue_price_local=15)
    meta=dict(symbol=row['yahoo_symbol'],exchangeName='LSE',instrumentType='ETF',currency=row['quote_currency'],longName=row['name'])
    frame=history();frame.loc['2026-10-09','Close']=None
    result=etfs.apply_history(row,frame,meta,asof='2026-10-10T10:00:00Z')
    assert result['williams_monthly_r']==-50
    assert result['williams_monthly_asof']=='2026-10-08'
    assert result['technical_asof']=='2026-10-08'
    assert '2026-10-09' in result['williams_monthly_source_note']
    assert 'trailing candle' in result['history_quality_note']
    frame.loc['2026-09-01','Close']=None
    invalid=etfs.apply_history(row,frame,meta,asof='2026-10-10T10:00:00Z')
    assert invalid['williams_monthly_r'] is None
