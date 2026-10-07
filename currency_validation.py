"""Verify statement currencies against matching raw Yahoo timeseries records."""
import json
import math
from datetime import datetime, timezone
import pandas as pd

# One monetary anchor per statement set. Exact dates and raw amounts must match.
ANCHORS = {'income': ('TotalRevenue','Total Revenue'), 'balance': ('TotalAssets','Total Assets'),
           'cashflow': ('OperatingCashFlow','Operating Cash Flow')}


def match_currencies(payload, frames):
    records = {key: values for block in payload.get('timeseries',{}).get('result',[]) for key,values in block.items() if key not in {'meta','timestamp'}}
    currencies={}
    for name,frame in frames.items():
        kind,frequency=name.split('_')
        raw,label=ANCHORS[kind]
        period='annual' if frequency=='annual' else 'quarterly'
        found=set()
        if label in frame.index:
            for record in records.get(period+raw,[]):
                if record.get('periodType') != ('12M' if period=='annual' else '3M'):
                    continue
                day=pd.Timestamp(record['asOfDate'])
                value=record.get('reportedValue',{}).get('raw')
                if day in frame.columns and value is not None and math.isclose(float(frame.loc[label,day]),float(value),rel_tol=1e-9,abs_tol=.001) and record.get('currencyCode'):
                    found.add(record['currencyCode'])
        currencies[name]=next(iter(found)) if len(found)==1 else None
    return currencies


def verify_currencies(ticker,frames):
    types=','.join(period+raw for period in ('annual','quarterly') for raw,_ in ANCHORS.values())
    url=f'https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker.ticker}'
    url+=f'?symbol={ticker.ticker}&type={types}&period1=1483142400&period2={int(datetime.now(timezone.utc).timestamp())+86400}'
    response=ticker._data.cache_get(url=url,timeout=15)
    response.raise_for_status()
    payload=json.loads(response.text)
    if payload.get('timeseries',{}).get('error') or payload.get('finance',{}).get('error'):
        raise ValueError('Yahoo currency verification unavailable')
    return match_currencies(payload,frames)


def verified_totals(frames,currencies,financial_values,previous=None):
    previous=previous or {}
    empty=pd.DataFrame()
    # Use a single verified currency per financial family. Never sum incompatible quarters.
    ic=currencies.get('income_annual') or currencies.get('income_quarterly')
    cc=currencies.get('cashflow_annual') or currencies.get('cashflow_quarterly')
    chosen={key:frame if currencies.get(key) and currencies.get(key)==(ic if key.startswith('income') else cc) else empty for key,frame in frames.items()}
    values=financial_values(chosen.get('income_quarterly',empty),chosen.get('income_annual',empty),chosen.get('cashflow_quarterly',empty),chosen.get('cashflow_annual',empty))
    values.update(financial_currency=ic,financial_currency_version=2,financial_quality_note=None,
        financial_field_currencies={key:ic for key in ['net_income','revenue']})
    values['financial_field_currencies'].update({key:cc for key in ['capex','fcf','operating_cf','fcf_delta']})
    # Balance totals come from the verified latest balance, rather than profile amounts in another currency.
    bc=currencies.get('balance_quarterly') or currencies.get('balance_annual')
    bkey='balance_quarterly' if currencies.get('balance_quarterly') else 'balance_annual'
    balance=frames.get(bkey,empty)
    for key,label in [('debt','Total Debt'),('cash','Cash And Cash Equivalents')]:
        values[key+'_local']=None
        if bc and not balance.empty and label in balance.index:
            value=balance.loc[label,balance.columns.max()]
            values[key+'_local']=float(value) if pd.notna(value) else None
        values['financial_field_currencies'][key]=bc
    if previous.get('financial_currency_version') == 2:
        for family,keys in [('income',['net_income','revenue']),('cashflow',['capex','fcf','operating_cf','fcf_delta']),('balance',['debt','cash'])]:
            if not any(currencies.get(family+'_'+freq) for freq in ['annual','quarterly']) and all(frames.get(family+'_'+freq,empty).empty for freq in ['annual','quarterly']):
                for key in keys:
                    values[key+'_local']=previous.get(key+'_local')
                    values['financial_field_currencies'][key]=previous.get('financial_field_currencies',{}).get(key)
                if family=='income':
                    values.update(financial_currency=previous.get('financial_currency'),income_period=previous.get('income_period'),net_margin=previous.get('net_margin'))
                elif family=='cashflow':
                    for key in ['cf_period','fcf_growth_period','fcf_change']: values[key]=previous.get(key)
                values['financial_quality_note']='Yahoo returned no new records for one statement family. Previous verified totals are retained.'
    if any(not currencies.get(key) and not frame.empty for key,frame in frames.items()):
        values['financial_quality_note']='Some statement currencies could not be verified. Affected dollar totals are withheld.'
    return values
