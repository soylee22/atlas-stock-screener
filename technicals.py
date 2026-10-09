"""Moving averages and weekly Williams %R from split-adjusted Yahoo candles."""
from datetime import date,datetime,timezone
import pandas as pd


def technical_values(history,currency,today=None, *, validate_daily_close=True):
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
    result.update(weekly_williams(history, today, validate_daily_close=validate_daily_close))
    return result


def weekly_williams(history, today, *, validate_daily_close=True):
    """14 weekly OHLC bars, including a developing week through prior sessions."""
    out = dict(williams_r=None, williams_zone=None, williams_r_period=None,
        williams_asof=None, williams_provisional=None, williams_version=1,
        williams_reason=None)
    if not all(k in history for k in ('High', 'Low', 'Close')):
        out['williams_reason'] = 'Yahoo OHLC history unavailable. Closes alone cannot calculate Williams %R.'
        return out
    candles = history[['High', 'Low', 'Close']].copy().sort_index()
    candles.index = pd.to_datetime(candles.index)
    if candles.index.tz is not None:
        candles.index = candles.index.tz_localize(None)
    candles = candles[candles.index.date < today]
    if candles.empty:
        out['williams_reason'] = 'No completed daily sessions available.'
        return out
    out['williams_asof'] = str(candles.index[-1].date())
    # Do not silently skip corrupt daily bars or missing whole weeks.
    valid = candles.notna().all(axis=1) & candles.apply(lambda col: col.map(lambda v: pd.notna(v) and abs(v) != float('inf'))).all(axis=1) & (candles['Low'] > 0) & (candles['High'] >= candles['Low']) & (candles['Close'] > 0)
    if validate_daily_close:
        valid &= (candles['Close'] >= candles['Low']) & (candles['Close'] <= candles['High'])
    weekly = candles.resample('W-FRI').agg({'High':'max', 'Low':'min', 'Close':'last'})
    weekly.loc[~valid.resample('W-FRI').min().fillna(False).astype(bool), :] = float('nan')
    window = weekly.tail(14)
    out['williams_provisional'] = bool(weekly.index[-1].date() >= today)
    out['williams_r_period'] = f"14 weekly candles ending {weekly.index[-1].date()} · through {out['williams_asof']} · " + ('developing week' if out['williams_provisional'] else 'completed week')
    if len(window) < 14 or window.isna().any().any():
        out['williams_reason'] = 'Needs 14 consecutive valid weekly High, Low and Close candles.'
        return out
    high, low, close = float(window.High.max()), float(window.Low.min()), float(window.Close.iloc[-1])
    if high <= low:
        out['williams_reason'] = 'Flat 14-week price range. Williams %R is undefined.'
        return out
    if not low <= close <= high:
        out['williams_reason'] = 'Latest close is outside the reported 14-week high/low range.'
        return out
    value = -100 * (high-close)/(high-low)
    out.update(williams_r=value, williams_zone='Oversold' if value <= -80 else 'Overbought' if value >= -20 else 'Neutral',
        williams_high_local=high, williams_low_local=low, williams_close_local=close,
        williams_oversold_price_local=high-.8*(high-low), williams_overbought_price_local=high-.2*(high-low))
    return out
