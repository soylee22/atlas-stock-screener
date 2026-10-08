"""Retain Yahoo statement line items and a deliberately labelled ROIC proxy."""
from datetime import datetime, timezone, date
import math
import re


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def line_unit(label):
    normal = re.sub(r'[^a-z]', '', label.lower())
    if 'eps' in normal or 'pershare' in normal:
        return 'currency_per_share'
    if 'averageshares' in normal or 'sharesnumber' in normal or 'sharesoutstanding' in normal or normal == 'shareissued':
        return 'shares'
    if normal == 'taxrateforcalcs':
        return 'ratio'
    return 'currency'


def retain_statement(frame, currency, frequency, previous=None, fetched=None):
    fetched = fetched or datetime.now(timezone.utc).isoformat(timespec='seconds')
    previous = previous or {}
    if frame.empty and not currency and previous.get('currency'):
        return {**previous,'fetched_at':fetched}
    old = previous if currency and previous.get('currency') == currency else {}
    periods = {p['end_date']: p for p in old.get('periods', [])}
    units = dict(old.get('units', {}))
    for column in frame.columns:
        day = column.date().isoformat()
        values = {str(label): finite(value) for label, value in frame[column].items()}
        if any(v is not None for v in values.values()):
            periods[day] = dict(end_date=day, fetched_at=fetched, values=values)
            units.update({label: line_unit(label) for label in values})
    return dict(currency=currency, frequency=frequency, fetched_at=fetched, units=units,
                periods=[periods[day] for day in sorted(periods)])


RETURN_METHODS = {
    'roic_proxy': 'Operating income × (1 − Tax Provision / Pretax Income) / average(Total Debt + Stockholders Equity − Cash And Cash Equivalents)',
    'roce': 'EBIT / average(Total Assets − Current Liabilities)',
}


def annual_return(history, period, metric):
    income, balance = history.get('income_annual', {}), history.get('balance_annual', {})
    day, values = period['end_date'], period['values']
    result = dict(end_date=day, currency=income.get('currency'), value=None, inputs=None, reason=None)
    if not income.get('currency') or income.get('currency') != balance.get('currency'):
        result['reason'] = 'Matching reporting currency unavailable'
        return result
    balances = {p['end_date']: p['values'] for p in balance.get('periods', [])}
    prior = [d for d in balances if 330 <= (date.fromisoformat(day)-date.fromisoformat(d)).days <= 400]
    if day not in balances or not prior:
        result['reason'] = 'Needs matched opening and closing annual capital balances'
        return result
    opening = max(prior)
    fields = ['Total Debt','Stockholders Equity','Cash And Cash Equivalents'] if metric == 'roic_proxy' else ['Total Assets','Current Liabilities']
    def capital(v):
        parts = [finite(v.get(k)) for k in fields]
        if any(p is None for p in parts): return None
        return parts[0]+parts[1]-parts[2] if metric == 'roic_proxy' else parts[0]-parts[1]
    first, last = capital(balances[opening]), capital(balances[day])
    if first is None or last is None or first <= 0 or last <= 0:
        result['reason'] = 'Needs complete inputs and two positive capital balances'
        return result
    inputs = dict(opening_capital=first, closing_capital=last, opening_date=opening,
        closing_date=day, currency=income['currency'], method=RETURN_METHODS[metric],
        opening_balance={k:finite(balances[opening].get(k)) for k in fields},
        closing_balance={k:finite(balances[day].get(k)) for k in fields})
    if metric == 'roic_proxy':
        op, pretax, tax = (finite(values.get(k)) for k in ['Operating Income','Pretax Income','Tax Provision'])
        if op is None or pretax is None or tax is None or pretax <= 0 or not 0 <= tax/pretax <= 1:
            result['reason'] = 'Needs operating income and a valid effective tax rate between 0% and 100%'
            return result
        numerator = op*(1-tax/pretax)
        inputs.update(operating_income=op, pretax_income=pretax, tax_provision=tax)
    else:
        numerator = finite(values.get('EBIT'))
        if numerator is None:
            result['reason'] = 'Yahoo EBIT unavailable for this fiscal year'
            return result
        inputs['ebit'] = numerator
    inputs.update(numerator=numerator, average_capital=(first+last)/2)
    result.update(value=numerator/inputs['average_capital']*100, inputs=inputs)
    return result


def capital_returns(history, sector=None):
    """Annual book-capital ratios. Five-year means five consecutive FY ratios, not CAGR."""
    periods = sorted(history.get('income_annual', {}).get('periods', []), key=lambda p:p['end_date'])
    result = dict(capital_returns_version=1, capital_returns_history=[])
    annual = {key:[annual_return(history,p,key) for p in periods] for key in RETURN_METHODS}
    for key, observations in annual.items():
        if sector == 'Financial Services':
            for observation in observations:
                observation.update(value=None, inputs=None, reason='Industrial capital-return ratios are not used for financial-sector businesses')
        latest = observations[-1] if observations else None
        result[key] = latest['value'] if latest else None
        result[key+'_inputs'] = latest['inputs'] if latest else None
        result[key+'_reason'] = latest['reason'] if latest else 'Awaiting annual income and balance sheets'
        result[key+'_period'] = ('FY '+latest['end_date']+' · '+str(latest['currency'])+' · average book capital') if latest and latest['value'] is not None else None
        window = observations[-5:]
        count = sum(p['value'] is not None for p in window)
        consecutive = all(330 <= (date.fromisoformat(b['end_date'])-date.fromisoformat(a['end_date'])).days <= 400 for a,b in zip(window,window[1:]))
        complete = len(window) == 5 and count == 5 and consecutive
        result[key+'_5y_avg'] = sum(p['value'] for p in window)/5 if complete else None
        result[key+'_5y_count'] = count
        result[key+'_5y_avg_period'] = f"FY {window[0]['end_date']} to {window[-1]['end_date']} · arithmetic mean of 5 annual ratios" if complete else None
        result[key+'_5y_avg_reason'] = None if complete else ('Industrial capital-return ratios are not used for financial-sector businesses' if sector == 'Financial Services' else f'Needs 5 valid consecutive annual ratios ending at latest FY. {count} valid in the latest {len(window)} cached fiscal years. Each ratio needs opening and closing balances.')
    for i,period in enumerate(periods):
        result['capital_returns_history'].append(dict(end_date=period['end_date'],
            currency=history.get('income_annual', {}).get('currency'),
            **{key:annual[key][i]['value'] for key in RETURN_METHODS},
            inputs={key:annual[key][i]['inputs'] for key in RETURN_METHODS},
            reasons={key:annual[key][i]['reason'] for key in RETURN_METHODS}))
    return result


def roic_proxy(history):
    # Retain the existing callable for integrations and old tests.
    return capital_returns(history)


def capture_statements(ticker, currency, previous=None, frames=None, currencies=None):
    previous = previous or {}
    old = previous.get('statement_history', {})
    frames = dict(frames or {})
    history, errors = {}, {}
    for kind, method in [('income','get_income_stmt'),('balance','get_balance_sheet'),('cashflow','get_cashflow')]:
        for frequency, freq in [('annual','yearly'),('quarterly','quarterly')]:
            key = kind+'_'+frequency
            try:
                frame = frames.get(key)
                if frame is None:
                    frame = getattr(ticker, method)(pretty=True, freq=freq)
                    frames[key] = frame
                history[key] = retain_statement(frame,(currencies or {}).get(key,currency),frequency,old.get(key))
                history[key]['status'] = 'available' if not frame.empty else 'no_data'
            except Exception:
                # Preserve cached values without publishing provider errors or local paths.
                history[key] = old.get(key, dict(currency=currency,frequency=frequency,periods=[],units={}))
                errors[key] = 'Yahoo statement fetch failed. Cached records retained.'
    if not any(s.get('periods') for s in history.values()):
        errors['all_statements'] = 'No statement observations returned. Source fetch remains pending.'
    values = dict(statement_history=history,statement_errors=errors,statement_version=0 if errors or not currency else 1,
                  statement_fetched=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    values.update(capital_returns(history, previous.get('sector')))
    return values, frames
