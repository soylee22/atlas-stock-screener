import pandas as pd
import pytest
import etfs
from technicals import technical_values


def candles():
    frame = pd.DataFrame({'High':110.,'Low':90.,'Close':100.},index=pd.bdate_range('2025-01-02','2026-10-15'))
    frame.loc['2026-10-15','Close'] = float('nan')
    return frame


def meta(**kwargs):
    return dict(currency='USD',exchangeName='NYQ',regularMarketPrice=101.,
                regularMarketTime=int(pd.Timestamp('2026-10-15T20:00:01Z').timestamp()),**kwargs)


def compute(frame=None,metadata=None,currency='USD'):
    return technical_values(candles() if frame is None else frame,currency,asof='2026-10-16T00:30:00Z',metadata=meta() if metadata is None else metadata,region='us')


def test_missing_latest_close_uses_matched_reported_regular_quote_with_source_note():
    frame=candles(); result=compute(frame)
    assert pd.isna(frame.Close.iloc[-1])  # Source input is not mutated.
    assert result['williams_r']==-45
    assert result['williams_monthly_r']==-45
    assert result['technical_asof']=='2026-10-15'
    assert result['technical_history']['daily']['closes'][-1]==101
    assert '2026-10-15T20:00:01+00:00' in result['williams_source_note']
    assert 'reported price, not an estimate' in result['williams_monthly_source_note']


@pytest.mark.parametrize('changes',[
    {'currency':'GBP'}, {'regularMarketPrice':500}, {'regularMarketPrice':0},
    {'regularMarketPrice':float('nan')},
    {'regularMarketTime':int(pd.Timestamp('2026-10-14T20:00:00Z').timestamp())},
    {'regularMarketTime':int(pd.Timestamp('2026-10-16T20:00:00Z').timestamp())},
    {'regularMarketTime':int(pd.Timestamp('2026-10-15T23:00:00Z').timestamp())},
])
def test_unmatched_quote_currency_date_range_future_or_extended_hours_is_rejected(changes):
    metadata=meta();metadata.update(changes)
    assert compute(metadata=metadata)['williams_r'] is None


def test_valid_history_is_not_overwritten_and_older_missing_closes_are_not_filled():
    frame=candles();frame.loc['2026-10-15','Close']=95
    assert compute(frame)['williams_r']==-75
    frame=candles();frame.loc['2026-10-14','Close']=float('nan')
    assert compute(frame)['williams_r'] is None


def test_etf_repair_occurs_before_trailing_close_truncation():
    row=dict(etfs.import_catalogue()[0],catalogue_price_local=100.)
    metadata=dict(symbol=row['yahoo_symbol'],currency='GBp',exchangeName='LSE',instrumentType='ETF',longName=row['name'],
                  regularMarketPrice=95.,regularMarketTime=int(pd.Timestamp('2026-10-15T15:35:00Z').timestamp()))
    result=etfs.apply_history(row,candles(),metadata,asof=pd.Timestamp('2026-10-15T17:00:00Z'))
    assert result['price_local']==95 and result['technical_asof']=='2026-10-15'
    assert result['williams_r']==-75
    assert 'reported regular-market quote 95 GBp' in result['williams_source_note']
