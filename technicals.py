"""Simple moving averages of split-adjusted, dividend-unadjusted Yahoo closes."""
from datetime import date,datetime,timezone
import pandas as pd


def technical_values(history,currency,today=None):
    today=today or date.today()
    closes=history.get('Close',pd.Series(dtype=float)).dropna().sort_index()
    # A session dated today may still be trading. Keep the prior completed session.
    closes=closes[[day.date()<today for day in closes.index]]
    closes=closes[closes>0]
    if not isinstance(closes.index,pd.DatetimeIndex): closes.index=pd.to_datetime(closes.index)
    if closes.index.tz is not None:
        closes.index=closes.index.tz_localize(None)
    daily=closes.tail(500)
    weekly=closes.resample('W-FRI').last().dropna() if len(closes) else pd.Series(dtype=float)
    weekly=weekly[[day.date()<today for day in weekly.index]].tail(260)
    cache={kind:dict(closes=[float(v) for v in series],dates=[str(d.date()) for d in series.index]) for kind,series in [('daily',daily),('weekly',weekly)]}
    result=dict(technical_history=cache,technical_currency=currency,technical_version=1,
        technical_fetched=datetime.now(timezone.utc).isoformat(timespec='seconds'),technical_asof=str(daily.index[-1].date()) if len(daily) else None,
        technical_price_local=float(daily.iloc[-1]) if len(daily) else None,
        technical_basis='Yahoo Close: split-adjusted, not dividend-adjusted. Excludes current session and incomplete week.')
    for kind,label in [('daily','200d'),('weekly','200w')]:
        series=daily if kind=='daily' else weekly
        average=float(series.tail(200).mean()) if len(series)>=200 else None
        result['sma_'+label+'_local']=average
        result['sma_'+label+'_distance']=(result['technical_price_local']/average-1)*100 if average and currency else None
        result['sma_'+label+'_period']=f'{series.index[-200].date()} to {series.index[-1].date()} · {currency}' if average else f'Needs 200 completed {kind} closes. {len(series)} available.'
    return result
