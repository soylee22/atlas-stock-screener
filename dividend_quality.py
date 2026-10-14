"""Dividend screening from retained Yahoo statements. No extra network requests."""
from datetime import date
import math

HORIZONS = (1, 3, 5, 10)
METRICS = ('fcf_payout', 'current_ratio', *[f'{metric}_{n}y' for n in HORIZONS
    for metric in ('fcf_growth', 'div_fiscal_growth', 'fcf_div_growth_gap')])


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def source(history, name):
    item = history.get(name, {})
    return item if item.get('currency') and item.get('status') != 'currency_conflict' else {}


def periods(item):
    return sorted(item.get('periods', []), key=lambda p: p['end_date'])


def value(period, *names):
    for name in names:
        found = finite(period.get('values', {}).get(name))
        if found is not None:
            return found
    return None


def cash_fcf(period):
    ocf = value(period, 'Operating Cash Flow', 'Total Cash From Operating Activities')
    capex = value(period, 'Capital Expenditure', 'Capital Expenditures')
    return ocf - abs(capex) if ocf is not None and capex is not None else None


def consecutive(window, low=330, high=400):
    dates = [date.fromisoformat(p['end_date']) for p in window]
    return all(low <= (b-a).days <= high for a, b in zip(dates, dates[1:]))


def cagr(values, n):
    if any(v is None for v in values):
        return None, 'Missing annual operands'
    if values[0] <= 0:
        return None, 'Growth requires a positive starting value'
    if n > 1 and values[-1] < 0:
        return None, 'CAGR requires a non-negative ending value'
    return ((values[-1] / values[0]) ** (1/n) - 1) * 100, None


def quality_values(row):
    history = row.get('statement_history', {})
    out = {key: None for metric in METRICS for key in (metric, metric+'_period')}
    reasons = {key: 'Awaiting verified Yahoo statements' for key in METRICS}
    out.update(dividend_quality_version=1, dividend_quality_reasons=reasons,
               dividend_quality_history=[], dividend_quality_fetched=row.get('statement_fetched'))
    financial = row.get('sector') == 'Financial Services'
    if financial:
        reasons.update({k: 'Operating-company measure withheld for financial businesses' for k in METRICS})
        return out

    balances = [(p, s) for freq in ('quarterly', 'annual')
        for s in [source(history, 'balance_'+freq)] for p in periods(s)]
    if balances:
        latest, item = max(balances, key=lambda pair: pair[0]['end_date'])
        assets, liabilities = value(latest, 'Current Assets'), value(latest, 'Current Liabilities')
        out['current_ratio_period'] = f"As of {latest['end_date']} · {item['currency']}"
        if assets is not None and assets >= 0 and liabilities is not None and liabilities > 0:
            out['current_ratio'] = assets / liabilities
            reasons.pop('current_ratio', None)
        else:
            reasons['current_ratio'] = 'Needs current assets and positive current liabilities in latest balance sheet'

    annual = source(history, 'cashflow_annual')
    annual_periods = periods(annual)
    latest_annual = annual_periods[-1]['end_date'] if annual_periods else ''
    for freq in ('quarterly', 'annual'):
        item = source(history, 'cashflow_'+freq)
        window = periods(item)[-4:] if freq == 'quarterly' else periods(item)[-1:]
        if not window or freq == 'quarterly' and (len(window) != 4 or not consecutive(window, 65, 115) or window[-1]['end_date'] < latest_annual):
            continue
        fcfs = [cash_fcf(p) for p in window]
        # Prefer common dividends. Total cash dividends may include preferred dividends.
        labels = ['Common Stock Dividend Paid' if value(p, 'Common Stock Dividend Paid') is not None else 'Cash Dividends Paid' for p in window]
        dividends = [value(p, label) for p, label in zip(window, labels)]
        if any(v is None for v in fcfs + dividends):
            continue
        total_fcf, paid = sum(fcfs), sum(abs(v) for v in dividends)
        period = ('TTM ' if freq == 'quarterly' else 'FY ') + window[-1]['end_date'] + ' · ' + item['currency']
        out['fcf_payout_period'] = period
        out['fcf_payout_inputs'] = dict(fcf=total_fcf, dividends_paid=paid, currency=item['currency'],
            dividend_labels=labels, period_ends=[p['end_date'] for p in window])
        out['fcf_payout_basis'] = 'Common dividend cash outflow' if all(l == 'Common Stock Dividend Paid' for l in labels) else 'Total cash dividends, potentially including preferred dividends'
        if total_fcf > 0:
            out['fcf_payout'] = paid / total_fcf * 100
            reasons.pop('fcf_payout', None)
        else:
            reasons['fcf_payout'] = 'Payout ratio requires positive FCF, not a negative denominator'
        break
    else:
        reasons['fcf_payout'] = 'Needs matched dividend cash outflow and FCF in four consecutive quarters or latest FY'


    if annual_periods:
        events = row.get('dividend_events', [])
        start = row.get('dividend_history_start')
        for i, p in enumerate(annual_periods):
            end = date.fromisoformat(p['end_date'])
            beginning = date.fromisoformat(annual_periods[i-1]['end_date']) if i else end.replace(year=end.year-1, day=min(end.day, 28) if end.month == 2 else end.day)
            covered = bool(row.get('dividend_fetched') and start and start <= beginning.isoformat())
            amounts = [finite(e.get('amount')) for e in events if beginning.isoformat() < e.get('date', '') <= p['end_date']]
            dps = sum(amounts) if covered and all(a is not None and a >= 0 for a in amounts) else None
            out['dividend_quality_history'].append(dict(end_date=p['end_date'], start_exclusive=beginning.isoformat(),
                fcf=cash_fcf(p), dividends_per_share=dps, currency=annual['currency'], dividend_currency=row.get('dividend_currency')))

    points = out['dividend_quality_history']
    for n in HORIZONS:
        fk, dk, gk = (f'{metric}_{n}y' for metric in ('fcf_growth', 'div_fiscal_growth', 'fcf_div_growth_gap'))
        if len(points) < n+1:
            for key in (fk, dk, gk):
                reasons[key] = f'Needs {n+1} annual records, {len(points)} available from Yahoo/cache'
            continue
        window = points[-(n+1):]
        if not consecutive(window) or abs((date.fromisoformat(window[-1]['end_date'])-date.fromisoformat(window[0]['end_date'])).days-365.25*n) > 45:
            for key in (fk, dk, gk):
                reasons[key] = 'Consecutive full fiscal years unavailable'
            continue
        label = f"FY {window[-1]['end_date']} / {window[0]['end_date']}"
        for key, field in ((fk, 'fcf'), (dk, 'dividends_per_share')):
            currency = annual.get('currency') if key == fk else row.get('dividend_currency')
            out[key+'_period'] = label + ' · ' + (currency or 'Unknown currency')
            result, reason = cagr([p[field] for p in window], n) if currency else (None, 'Reporting currency unavailable')
            out[key] = result
            if reason:
                reasons[key] = reason
            else:
                reasons.pop(key, None)
        out[gk+'_period'] = label + ' · matched fiscal windows · percentage points'
        if out[fk] is not None and out[dk] is not None:
            out[gk] = out[fk] - out[dk]
            reasons.pop(gk, None)
        else:
            reasons[gk] = 'Needs FCF and dividend-per-share growth over the same fiscal windows'
    return out
