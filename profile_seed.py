"""Merge previously collected public profiles without replacing newer quotes."""
import argparse
import json
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import app as model
from build_site import ICON_FILE, public_row

VERSION = '2026-10-08-v2'
URL = 'https://github.com/soylee22/atlas-stock-screener/releases/download/data-bootstrap-2026-10-08/collected-profiles.tar.gz'
PROFILE_KEYS = {field['key'] for field in model.COLUMNS if field['group'] in {'Financials', 'Cash flow'}} - set(model.GROWTH_KEYS)
PROFILE_KEYS |= set(model.MONETARY_FINANCIAL) | {key+'_local' for key in model.MONETARY_FINANCIAL}
PROFILE_KEYS |= {'sector','industry','description','website','domicile','beta','roe',
    'financial_fetched','financial_error','financial_currency','financial_currency_version',
    'financial_field_currencies','financial_quality_note','income_period','cf_period','fcf_growth_period',
    'statement_history','statement_version','statement_fetched','statement_errors',
    'roic_proxy','roic_proxy_period','roic_proxy_inputs','roic_proxy_reason'}


def merge_profiles(store, path):
    with tarfile.open(path,'r:gz') as archive:
        payload=json.load(archive.extractfile('data.json'))
        if payload.get('version') != 1:
            raise ValueError('Unsupported public profile archive')
        with store.connect() as conn:
            current={r[0]:json.loads(r[1]) for r in conn.execute('SELECT symbol,data FROM stocks')}
        updates, timestamps = [], []
        for raw in payload['rows']:
            source=public_row(raw)
            old=current.get(source.get('symbol'))
            if not old or not old.get('active') or not old.get('main_listing'):
                continue
            values=dict(symbol=old['symbol'],region_code=old['region_code'])
            for key in ['sector','industry','description','website','domicile']:
                if not old.get(key) and source.get(key):
                    values[key]=source[key]
            if (source.get('financial_fetched') or '') > (old.get('financial_fetched') or '') and (source.get('financial_currency_version') or 0) >= max(2, (old.get('financial_currency_version') or 0)):
                values.update({k:v for k,v in source.items() if k in PROFILE_KEYS})
                fetched=datetime.fromisoformat(source['financial_fetched']).timestamp()
                timestamps.append((fetched,fetched,old['symbol']))
            for prefixes, stamp in [
                (('annual_','revenue_growth_','net_income_growth_'),'annual_growth_fetched'),
                (('dividend_','div_years','div_growth'),'dividend_fetched'),
                (('technical_','sma_'),'technical_fetched')]:
                if stamp=='annual_growth_fetched' and (source.get('financial_currency_version') or 0)<2 and source.get('annual_growth_status')!='available':
                    continue
                if (source.get(stamp) or '') > (old.get(stamp) or ''):
                    values.update({k:v for k,v in source.items() if k.startswith(prefixes)})
            # Independently fetched annual history can fill FY income for quote-only rows.
            # Older unverified profile monetary totals are never copied.
            history=values.get('annual_income_history',{})
            if not old.get('income_period') and not values.get('financial_fetched') and not old.get('financial_fetched') and history.get('currency'):
                period=max([*history.get('net_income',{}),*history.get('revenue',{})],default='')
                if period:
                    income=history.get('net_income',{}).get(period)
                    revenue=history.get('revenue',{}).get(period)
                    values.update(net_income_local=income,revenue_local=revenue,
                        net_margin=income/revenue*100 if income is not None and revenue else None,
                        financial_currency=history['currency'],income_period='FY '+period,
                        income_fetched=values['annual_growth_fetched'])
            if len(values)>2:
                updates.append(values)
        store.upsert_many(updates)
        logos=model.LogoCache(store)
        domains={model.company_domain(r.get('website')) for r in updates if r.get('website')}
        copied=0
        with store.connect() as conn:
            conn.executemany('UPDATE stocks SET enriched=?,attempted=? WHERE symbol=?',timestamps)
            for asset in payload['logos']:
                if asset['domain'] not in domains:
                    continue
                filename=asset['filename']
                if not ICON_FILE.fullmatch(filename):
                    raise ValueError('Unsafe icon filename')
                old=conn.execute('SELECT filename FROM logo_assets WHERE domain=?',(asset['domain'],)).fetchone()
                if old and old[0] and (logos.root/old[0]).is_file():
                    continue
                content=archive.extractfile('logos/'+filename).read()
                model.icon_extension(content)
                (logos.root/filename).write_bytes(content)
                conn.execute('INSERT OR REPLACE INTO logo_assets VALUES(?,?,?,?,?,?)',
                    (asset['domain'],filename,asset['source_url'],asset['fetched'],asset['attempted'],None))
                copied+=1
    result=dict(version=VERSION,merged_profiles=len(timestamps),updated_rows=len(updates),icons=copied,finished=model.now_iso())
    store.set_meta('profile_seed',result)
    store.classify_listings(force=True)
    print(json.dumps(result),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=Path)
    args=parser.parse_args()
    store=model.Store()
    if store.meta('profile_seed',{}).get('version') == VERSION:
        print('Collected profile seed already merged',flush=True)
        return
    if args.seed:
        merge_profiles(store,args.seed)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profiles.tar.gz'
            with urlopen(URL,timeout=120) as response, path.open('wb') as target:
                while chunk:=response.read(1024*1024):
                    target.write(chunk)
            merge_profiles(store,path)


if __name__=='__main__':
    main()
