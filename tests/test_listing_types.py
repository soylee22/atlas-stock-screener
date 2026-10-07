import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as model


def test_parent_company_labels_do_not_admit_us_preferred_series():
    symbols=['JPM','JPM-PC','JPM-PD','JPM-PJ','JPM-PK','JPM-PL','JPM-PM']
    rows=[dict(symbol=s,name='JPMorgan Chase & Co.',region_code='us',exchange='NYSE',active=True,instrument='stock') for s in symbols]
    kept={s for s,f in model.main_listing_flags(rows).items() if f['main_listing']}
    assert kept=={'JPM'}


def test_parent_labels_across_markets_preserve_ordinary_classes_and_reit_units():
    cases=[('BRK-A','us','NYSE',True),('BRK-B','us','NYSE',True),('BF-B','us','NYSE',True),
           ('BEP','us','NYSE',True),('PFBC','us','NasdaqGS',True),('TEMPO','us','NasdaqGM',True),
           ('JPM-PC','us','NYSE',False),('BAC-PB','us','NYSE',False),('JPM-P','us','NYSE',False),
           ('AGNCP','us','NasdaqGS',False),('ACGLN','us','NasdaqGS',False),('ALOVW','us','NasdaqGM',False),
           ('AAC-UN','us','NYSE',False),('AAC-WT','us','NYSE',False),('ASGI-RI','us','NYSE',False),
           ('TRP-PFA.TO','ca','Toronto',False),('TD-PFA.TO','ca','Toronto',False),('AFN-DBK.TO','ca','Toronto',False),
           ('AGMR-WTB.TO','ca','Toronto',False),('REAL-UN.TO','ca','Toronto',True),('AGF-B.TO','ca','Toronto',True),
           ('VOW3.DE','de','XETRA',False),('VOW.DE','de','XETRA',True),('HEN3.DE','de','XETRA',False),
           ('FPE3.DE','de','XETRA',False),('SRT3.DE','de','XETRA',False),('SIX3.DE','de','XETRA',False),
           ('P911.DE','de','XETRA',False),('PAH3.DE','de','XETRA',False),('EDNR.MI','it','Milan',False),
           ('ALM-PREF.ST','se','Stockholm',False),('INVE-A.ST','se','Stockholm',True),('INVE-B.ST','se','Stockholm',True),
           ('NOVO-B.CO','dk','Copenhagen',True),('ASML.AS','nl','Amsterdam',True),('ENI.MI','it','Milan',True),
           ('SAN.MC','es','MCE',True),('007330.KQ','kr','KOSDAQ',True),('005935.KS','kr','KSE',False),
           ('2881D.TW','tw','Taiwan',False),('XXXXA.TWO','tw','Taipei Exchange',True),('7203.T','jp','Tokyo',True)]
    rows=[dict(symbol=s,name='Issuer '+s,region_code=c,exchange=e,active=True,instrument='stock') for s,c,e,_ in cases]
    flags=model.main_listing_flags(rows)
    assert {s:flags[s]['main_listing'] for s,_,_,_ in cases}=={s:keep for s,_,_,keep in cases}


def test_directory_symbols_keep_ordinary_class_semantics_and_reject_preferred():
    from listing_types import parse_directory, description_type
    text='ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nJPM$C|JPM Depositary Shares Preferred Stock|N|JPMpC|N|100|N|JPM-C\nBRK.B|Berkshire Class B Common Stock|N|BRK.B|N|100|N|BRK.B\nBEP|Brookfield Limited Partnership Units|N|BEP|N|100|N|BEP\nFile Creation Time: 10072026|||||||\n'
    types,count=parse_directory(text)
    assert types=={'JPM-PC':{'category':'Preferred security','description':'JPM Depositary Shares Preferred Stock'}}
    assert count==4
    assert description_type('Preferred Bank - Common Stock') is None
    assert description_type('The Law Debenture Corporation p.l.c.') is None
    assert description_type('NOTE AB (publ)') is None
    assert description_type('Alpha 7.5% Senior Notes due 2030')=='Debt security'


def test_directory_failure_retains_complete_cache(tmp_path,monkeypatch):
    import json
    import listing_types as types
    path=tmp_path/'listing-types.json'
    previous={'version':1,'fetched':'2026-01-01T00:00:00+00:00','us':{'AGNCP':{'category':'Preferred security'}}}
    path.write_text(json.dumps(previous))
    monkeypatch.setattr(types.urllib.request,'urlopen',lambda *a,**k: (_ for _ in ()).throw(OSError('Provider unavailable')))
    import pytest
    with pytest.raises(OSError): types.refresh_directory(path)
    assert json.loads(path.read_text())==previous


def test_uk_parent_names_and_products_keep_ordinary_equities():
    cases=[('BP-A.L','BP p.l.c.',False),('BP-B.L','BP p.l.c.',False),('BP.L','BP p.l.c.',True),
           ('LLPC.L','Lloyds Banking Group PLC',False),('LLPD.L','Lloyds Banking Group plc',False),
           ('LLOY.L','Lloyds Banking Group plc',True),('STAB.L','Standard Chartered PLC',False),
           ('STAC.L','Standard Chartered PLC',False),('STAN.L','Standard Chartered PLC',True),
           ('CVBP.L','Coventry Building Society 12.125% PIBS GBP1000',False),
           ('AGAP.L','WisdomTree Agriculture',False),('BT-A.L','BT Group plc',True),('ABDP.L','AB Dynamics plc',True)]
    rows=[dict(symbol=s,name=n,region_code='gb',exchange='LSE',active=True,instrument='stock') for s,n,_ in cases]
    flags=model.main_listing_flags(rows)
    assert {s:flags[s]['main_listing'] for s,_,_ in cases}=={s:keep for s,_,keep in cases}
    from listing_types import description_type
    assert description_type('WisdomTree, Inc.') is None
    assert description_type('WisdomTree Inc.') is None
    assert description_type('9.25% NON-CUM IRREDEEMABLE PREF SHS')=='Preferred security'


def test_cached_old_flags_are_reclassified_for_table_chart_and_export(tmp_path,monkeypatch):
    import csv,io,time
    store=model.Store(tmp_path/'cached.sqlite')
    rows=[dict(symbol=s,name='JPMorgan Chase & Co.',region_code='us',exchange='NYSE',active=True,instrument='stock',
               main_listing=True,quote_currency='USD',price_local=100,market_cap_local=1e9,financial_currency='USD',net_income_local=10,div_years=5)
          for s in ['JPM','JPM-PC','JPM-PD','JPM-PJ','JPM-PK']]
    store.upsert_many(rows)
    store.set_meta('listing_policy_version',9)
    store.set_meta('listing_classified',time.time())
    monkeypatch.setattr(model,'store',store)
    selected,total=model.select_rows('JPM','','[]','market_cap','desc',False,'',100,0,True)
    assert total==1 and selected[0]['symbol']=='JPM'
    assert store.meta('listing_policy_version')==10
    exported=list(csv.DictReader(io.StringIO(model.export(search='JPM',columns='symbol',main_only=True).body.decode())))
    assert [r['symbol'] for r in exported]==['JPM']
    chart=model.chart(x='net_income',y='div_years',search='JPM',main_only=True)
    assert [r['symbol'] for r in chart['rows']]==['JPM']
