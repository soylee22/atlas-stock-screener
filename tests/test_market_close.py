from datetime import date, datetime, timezone
import pandas as pd
import etfs
import technicals


def friday_history():
    frame = pd.DataFrame({'High':110., 'Low':90., 'Close':100., 'Volume':1000.},
                         index=pd.bdate_range('2026-04-01','2026-10-09'))
    frame.loc['2026-10-09','Close'] = 92.
    return frame


def test_london_friday_close_is_used_on_friday_evening():
    row = dict(etfs.import_catalogue()[0], catalogue_price_local=100.)
    meta = dict(symbol=row['yahoo_symbol'], currency='GBp', exchangeName='LSE', instrumentType='ETF', longName=row['name'])
    result = etfs.apply_history(row, friday_history(), meta, asof=datetime(2026,10,9,19,tzinfo=timezone.utc))
    assert result['technical_asof'] == '2026-10-09'
    assert result['price_local'] == 92.
    assert result['williams_provisional'] is False
    assert result['technical_history']['weekly']['dates'][-1] == '2026-10-09'


def compute(frame=None, region='gb', stamp='2026-10-09T19:00:00Z'):
    return technicals.technical_values(friday_history() if frame is None else frame,'GBP', region=region, asof=pd.Timestamp(stamp))


def test_open_market_and_publication_allowance_exclude_forming_daily_bar():
    for stamp in ['2026-10-09T15:29:00Z','2026-10-09T15:59:59Z']:
        result=compute(stamp=stamp)
        assert result['technical_asof']=='2026-10-08'
        assert result['williams_provisional'] is True
        assert result['technical_history']['weekly']['dates'][-1]=='2026-10-02'
    assert compute(stamp='2026-10-09T16:00:00Z')['technical_asof']=='2026-10-09'
    assert compute(region='us')['technical_asof']=='2026-10-08'
    assert compute(region='us')['williams_provisional'] is True
    assert compute(region='us',stamp='2026-10-09T20:30:00Z')['williams_provisional'] is False


def test_missing_thursday_stays_explicit_when_friday_week_is_complete():
    result=compute(friday_history().drop(pd.Timestamp('2026-10-08')))
    assert result['technical_asof']=='2026-10-09'
    assert result['williams_provisional'] is False
    assert result['williams_r']==-90
    assert '2026-10-08' in result['williams_source_note']
    assert 'Missing prices are not estimated' in result['williams_source_note']


def test_missing_friday_does_not_claim_current_source_close():
    result=compute(friday_history().iloc[:-1])
    assert result['technical_asof']=='2026-10-08'
    assert result['williams_provisional'] is False  # The market week has finished.
    assert '2026-10-09' in result['williams_source_note']


def test_holiday_friday_completes_on_thursday_and_daylight_saving_changes_close():
    frame=pd.DataFrame({'High':110.,'Low':90.,'Close':100.},index=pd.bdate_range('2025-11-01','2026-04-02'))
    result=compute(frame,stamp='2026-04-02T16:00:00Z')  # Good Friday is closed.
    assert result['technical_asof']=='2026-04-02'
    assert result['williams_provisional'] is False
    assert result['technical_history']['weekly']['dates'][-1]=='2026-04-03'
    from market_sessions import SessionCutoff
    winter=SessionCutoff(asof=pd.Timestamp('2026-01-09T16:45:00Z'),region='gb')
    assert not winter.week_complete('2026-01-09')
    assert SessionCutoff(asof=pd.Timestamp('2026-01-09T17:00:00Z'),region='gb').week_complete('2026-01-09')


def test_us_early_close_and_local_timezone_and_unknown_venue():
    from market_sessions import SessionCutoff
    assert not SessionCutoff(asof=pd.Timestamp('2026-11-27T18:29:59Z'),region='us').week_complete('2026-11-27')
    assert SessionCutoff(asof=pd.Timestamp('2026-11-27T18:30:00Z'),region='us').week_complete('2026-11-27')
    japan=compute(region='jp',stamp='2026-10-09T07:00:00Z')
    assert japan['technical_asof']=='2026-10-09' and japan['williams_provisional'] is False
    unknown=compute(region=None)
    assert unknown['technical_asof']=='2026-10-08'
    assert 'calendar unknown' in unknown['williams_source_note']
    frame=friday_history();frame.index=frame.index.tz_localize('Europe/London')
    assert compute(frame)['technical_asof']=='2026-10-09'


def test_etf_fresh_cache_fetches_newly_completed_session(tmp_path,monkeypatch):
    import market_sessions
    class EveningCutoff(market_sessions.SessionCutoff):
        def __init__(self,*args,**kwargs):
            kwargs.setdefault('asof',pd.Timestamp('2026-10-09T19:00:00Z'))
            super().__init__(*args,**kwargs)
    monkeypatch.setattr(etfs,'SessionCutoff',EveningCutoff)
    row=dict(etfs.import_catalogue()[0],catalogue_price_local=100.,etf_history_version=4,
             technical_fetched='2026-10-09T15:00:00+00:00',history_attempted='2026-10-09T15:00:00+00:00',technical_asof='2026-10-08')
    monkeypatch.setattr(etfs,'import_catalogue',lambda:[row])
    path=tmp_path/'cache.json';etfs.write_cache(dict(version=1,rows=[row]),path)
    calls=[]
    def fetch(symbol):
        calls.append(symbol)
        return friday_history(),dict(symbol=symbol,currency='GBp',exchangeName='LSE',instrumentType='ETF',longName=row['name'])
    result=etfs.refresh(path,seconds=10,limit=1,fetcher=fetch)
    assert calls==[row['yahoo_symbol']]
    assert result['rows'][0]['technical_asof']=='2026-10-09'


def test_public_stock_fields_keep_completion_and_gap_note():
    import build_site
    for key in ['williams_source_note','technical_calendar','williams_provisional']:
        assert key in build_site.INDEX_FIELDS
        assert key in build_site.public_row({key:'test'})


def test_stock_fresh_cache_fetches_newly_completed_session(tmp_path,monkeypatch):
    import app, refresh_data, market_sessions
    store=app.Store(tmp_path/'stocks.sqlite')
    store.upsert_many([dict(symbol='HSBA.L',name='HSBC',region_code='gb',exchange='LSE',instrument='stock',active=True,
        main_listing=True,market_cap_local=30e9,quote_currency='USD',williams_version=2,technical_version=2,
        technical_fetched='2026-10-09T15:00:00+00:00',technical_attempted=pd.Timestamp('2026-10-09T15:00:00Z').timestamp(),technical_asof='2026-10-08')])
    monkeypatch.setattr(store,'classify_listings',lambda **kwargs:None)
    class EveningCutoff(market_sessions.SessionCutoff):
        def __init__(self,*args,**kwargs):
            kwargs.setdefault('asof',pd.Timestamp('2026-10-09T19:00:00Z'))
            super().__init__(*args,**kwargs)
    monkeypatch.setattr(market_sessions,'SessionCutoff',EveningCutoff)
    calls=[]
    class Ticker:
        def __init__(self,symbol):calls.append(symbol)
        def history(self,**kwargs):return friday_history()
        def get_history_metadata(self):return dict(currency='GBP',exchangeName='LSE')
    monkeypatch.setattr(app.yf,'Ticker',Ticker)
    refresh_data.backfill_technicals(store,seconds=10,limit=1)
    assert calls==['HSBA.L']
    assert store.get('HSBA.L')['technical_asof']=='2026-10-09'
