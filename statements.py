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


def roic_proxy(history):
    """FY NOPAT proxy / average book debt + equity - cash. Never substitute ROE."""
    income = history.get('income_annual', {})
    balance = history.get('balance_annual', {})
    if not income.get('currency') or income.get('currency') != balance.get('currency'):
        return dict(roic_proxy=None, roic_proxy_period=None, roic_proxy_inputs=None, roic_proxy_reason='Matching reporting currency unavailable')
    balances = {p['end_date']: p['values'] for p in balance.get('periods', [])}
    for period in reversed(income.get('periods', [])):
        day = period['end_date']
        # Do not silently present an older valid ROIC as the latest fiscal year.
        values = period['values']
        prior = [d for d in balances if 330 <= (date.fromisoformat(day)-date.fromisoformat(d)).days <= 400]
        op, pretax, tax = (finite(values.get(k)) for k in ['Operating Income','Pretax Income','Tax Provision'])
        if day not in balances or not prior or op is None or pretax is None or tax is None or pretax <= 0 or not 0 <= tax/pretax <= 1:
            break
        def capital(v):
            parts = [finite(v.get(k)) for k in ['Total Debt','Stockholders Equity','Cash And Cash Equivalents']]
            return parts[0]+parts[1]-parts[2] if all(p is not None for p in parts) else None
        first, last = capital(balances[max(prior)]), capital(balances[day])
        if first is None or last is None or first <= 0 or last <= 0:
            break
        result = op*(1-tax/pretax)/((first+last)/2)*100
        return dict(roic_proxy=result, roic_proxy_period=f'FY {day} · {income["currency"]} · average book capital',
                    roic_proxy_reason=None,
                    roic_proxy_inputs=dict(operating_income=op,pretax_income=pretax,tax_provision=tax,
                        opening_capital=first,closing_capital=last,opening_date=max(prior),closing_date=day,
                        currency=income['currency'],method='Operating income × (1 − Tax Provision / Pretax Income) / average(Total Debt + Stockholders Equity − Cash And Cash Equivalents)'))
    return dict(roic_proxy=None,roic_proxy_period=None,roic_proxy_inputs=None,
                roic_proxy_reason='Latest FY needs matched income and two positive capital balances with a valid effective tax rate')


def capture_statements(ticker, currency, previous=None, frames=None):
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
                history[key] = retain_statement(frame,currency,frequency,old.get(key))
                history[key]['status'] = 'available' if not frame.empty else 'no_data'
            except Exception:
                # Preserve cached values without publishing provider errors or local paths.
                history[key] = old.get(key, dict(currency=currency,frequency=frequency,periods=[],units={}))
                errors[key] = 'Yahoo statement fetch failed. Cached records retained.'
    if not any(s.get('periods') for s in history.values()):
        errors['all_statements'] = 'No statement observations returned. Source fetch remains pending.'
    values = dict(statement_history=history,statement_errors=errors,statement_version=0 if errors or not currency else 1,
                  statement_fetched=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    values.update(roic_proxy(history))
    if previous.get('sector') == 'Financial Services':
        values.update(roic_proxy=None,roic_proxy_period=None,roic_proxy_inputs=None,
                      roic_proxy_reason='Industrial ROIC proxy is not used for financial-sector businesses')
    return values, frames
