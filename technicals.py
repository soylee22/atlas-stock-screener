"""Moving averages and weekly/monthly Williams %R from Yahoo candles."""
from datetime import date,datetime,timezone
import math
import pandas as pd
from market_sessions import SessionCutoff


def reported_quote_close(history, currency, metadata, cutoff):
    """Use a dated regular-market quote only for a missing latest daily Close."""
    meta = metadata or {}
    if cutoff.calendar is None or not currency or meta.get('currency') != currency or not all(k in history for k in ('High','Low','Close')):
        return history, None
    try:
        price = float(meta['regularMarketPrice'])
        stamp = pd.Timestamp(meta['regularMarketTime'], unit='s', tz='UTC')
        observed = cutoff.observed(history)
        day = stamp.tz_convert(cutoff.calendar.tz).date()
        if not math.isfinite(price) or price <= 0 or stamp > cutoff.asof or observed.empty or observed.index[-1].date() != day:
            return history, None
        if not cutoff.calendar.is_session(str(day)) or not cutoff.calendar.session_open(str(day)) <= stamp <= cutoff.calendar.session_close(str(day)) + pd.Timedelta(minutes=30):
            return history, None
        row = observed.iloc[-1]
        if pd.notna(row.Close):
            return history, None
        low, high = float(row.Low), float(row.High)
        if not all(math.isfinite(v) for v in (low,high)) or not 0 < low <= price <= high:
            return history, None
        frame = history.copy()
        frame.index = pd.to_datetime(frame.index)
        if frame.index.tz is not None:
            frame.index = frame.index.tz_localize(None)
        frame.loc[observed.index[-1], 'Close'] = price
        note = f'Latest daily Close was missing from Yahoo history. Used its reported regular-market quote {price:g} {currency} at {stamp.isoformat()} for {day}. This is a reported price, not an estimate.'
        return frame, note
    except (KeyError, ValueError, TypeError, OverflowError):
        return history, None


def note_reported_quote(values, note):
    if note:
        for key in ('williams_source_note','williams_monthly_source_note','technical_basis'):
            values[key] = ' '.join(filter(None, [values.get(key), note]))
    return values


def technical_values(history,currency,today=None, *, validate_daily_close=True, asof=None, metadata=None, region=None):
    cutoff = SessionCutoff(today, asof=asof, metadata=metadata, region=region)
    history, quote_note = reported_quote_close(history, currency, metadata, cutoff)
    observed = cutoff.observed(history)
    history = cutoff.completed(history)
    closes=history.get('Close',pd.Series(dtype=float)).dropna().sort_index()
    closes=closes[closes>0]
    if not isinstance(closes.index,pd.DatetimeIndex): closes.index=pd.to_datetime(closes.index)
    if closes.index.tz is not None:
        closes.index=closes.index.tz_localize(None)
    daily=closes.tail(500)
    weekly=closes.resample('W-FRI').last().dropna() if len(closes) else pd.Series(dtype=float)
    weekly=weekly.tail(261)
    weekly=weekly[[cutoff.week_complete(day) for day in weekly.index]].tail(260)
    cache={kind:dict(closes=[float(v) for v in series],dates=[str(d.date()) for d in series.index]) for kind,series in [('daily',daily),('weekly',weekly)]}
    result=dict(technical_history=cache,technical_currency=currency,technical_version=5,
        technical_fetched=datetime.now(timezone.utc).isoformat(timespec='seconds'),technical_asof=str(daily.index[-1].date()) if len(daily) else None,
        technical_price_local=float(daily.iloc[-1]) if len(daily) else None,
        technical_basis='Yahoo Close: split-adjusted, not dividend-adjusted. Includes daily sessions after exchange close plus 30 minutes. Weekly averages use completed market weeks.')
    for kind,label in [('daily','200d'),('weekly','200w')]:
        series=daily if kind=='daily' else weekly
        average=float(series.tail(200).mean()) if len(series)>=200 else None
        result['sma_'+label+'_local']=average
        result['sma_'+label+'_distance']=(result['technical_price_local']/average-1)*100 if average and currency else None
        result['sma_'+label+'_period']=f'{series.index[-200].date()} to {series.index[-1].date()} · {currency}' if average else f'Needs 200 completed {kind} closes. {len(series)} available.'
    result.update(weekly_williams(observed, today, validate_daily_close=validate_daily_close, cutoff=cutoff, metadata=metadata))
    result.update(monthly_williams(observed, today, validate_daily_close=validate_daily_close, cutoff=cutoff, metadata=metadata))
    result['technical_calendar'] = cutoff.calendar_name
    return note_reported_quote(result, quote_note)


def weekly_williams(history, today=None, *, validate_daily_close=True, cutoff=None, asof=None, metadata=None, region=None):
    """14 weekly OHLC bars including a reported developing daily session."""
    return period_williams(history, today, validate_daily_close=validate_daily_close, cutoff=cutoff,
        asof=asof, metadata=metadata, region=region, interval='weekly')


def monthly_williams(history, today=None, *, validate_daily_close=True, cutoff=None, asof=None, metadata=None, region=None):
    """14 calendar-month OHLC bars, including the developing month."""
    values = period_williams(history, today, validate_daily_close=validate_daily_close, cutoff=cutoff,
        asof=asof, metadata=metadata, region=region, interval='monthly')
    return {key.replace('williams_', 'williams_monthly_', 1): value for key,value in values.items()}


def period_williams(history, today=None, *, validate_daily_close=True, cutoff=None, asof=None, metadata=None, region=None, interval):
    cutoff = cutoff or SessionCutoff(today, asof=asof, metadata=metadata, region=region)
    out = dict(williams_r=None, williams_zone=None, williams_r_period=None,
        williams_asof=None, williams_provisional=None, williams_version=2 if interval=='weekly' else 1,
        williams_reason=None, williams_source_note=None)
    if not all(k in history for k in ('High', 'Low', 'Close')):
        out['williams_reason'] = 'Yahoo OHLC history unavailable. Closes alone cannot calculate Williams %R.'
        return out
    candles = history[['High', 'Low', 'Close']].copy().sort_index()
    candles.index = pd.to_datetime(candles.index)
    if candles.index.tz is not None:
        candles.index = candles.index.tz_localize(None)
    candles = cutoff.observed(candles)
    if candles.empty:
        out['williams_reason'] = 'No reported exchange sessions available.'
        return out
    out['williams_asof'] = str(candles.index[-1].date())
    # Do not silently skip corrupt daily bars or missing whole periods.
    valid = candles.notna().all(axis=1) & candles.apply(lambda col: col.map(lambda v: pd.notna(v) and abs(v) != float('inf'))).all(axis=1) & (candles['Low'] > 0) & (candles['High'] >= candles['Low']) & (candles['Close'] > 0)
    if validate_daily_close:
        valid &= (candles['Close'] >= candles['Low']) & (candles['Close'] <= candles['High'])
    rule = 'W-FRI' if interval=='weekly' else 'ME'
    periods = candles.resample(rule).agg({'High':'max', 'Low':'min', 'Close':'last'})
    periods.loc[~valid.resample(rule).min().fillna(False).astype(bool), :] = float('nan')
    window = periods.tail(14)
    complete = cutoff.week_complete if interval=='weekly' else cutoff.month_complete
    period_name = 'week' if interval=='weekly' else 'month'
    out['williams_provisional'] = not complete(periods.index[-1])
    start = window.index[0] - pd.Timedelta(days=4) if interval=='weekly' else window.index[0].replace(day=1)
    out['williams_source_note'] = cutoff.source_note(candles, max(start, candles.index[0]))
    out['williams_r_period'] = f"14 {interval} candles ending {periods.index[-1].date()} · through {out['williams_asof']} · " + (f'developing {period_name}' if out['williams_provisional'] else f'completed {period_name}')
    if candles.index[-1].date() == cutoff.today and cutoff.session_developing():
        out['williams_r_period'] += ' · developing daily session (Yahoo snapshot, may be delayed)'
        stamp = (metadata or {}).get('regularMarketTime')
        try:
            quote_time = pd.Timestamp(stamp, unit='s', tz='UTC') if stamp else None
            if quote_time is not None and quote_time <= cutoff.asof and quote_time.tz_convert(cutoff.calendar.tz).date() == cutoff.today:
                out['williams_r_period'] += ' · source quote ' + quote_time.isoformat()
        except (ValueError, TypeError, OverflowError):
            pass
    if out['williams_source_note'] and cutoff.calendar is not None:
        out['williams_r_period'] += ' · source gaps'
    if len(window) < 14 or window.isna().any().any():
        out['williams_reason'] = f'Needs 14 consecutive valid {interval} High, Low and Close candles.'
        return out
    high, low, close = float(window.High.max()), float(window.Low.min()), float(window.Close.iloc[-1])
    if high <= low:
        out['williams_reason'] = f'Flat 14-{period_name} price range. Williams %R is undefined.'
        return out
    if not low <= close <= high:
        out['williams_reason'] = f'Latest close is outside the reported 14-{period_name} high/low range.'
        return out
    value = -100 * (high-close)/(high-low)
    out.update(williams_r=value, williams_zone='Oversold' if value <= -80 else 'Overbought' if value >= -20 else 'Neutral',
        williams_high_local=high, williams_low_local=low, williams_close_local=close,
        williams_oversold_price_local=high-.8*(high-low), williams_overbought_price_local=high-.2*(high-low))
    return out
