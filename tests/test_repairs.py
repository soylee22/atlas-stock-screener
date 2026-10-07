import sys
from pathlib import Path
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as model


def test_main_listings_exclude_receipts_preferred_and_bullion_but_keep_reits_and_classes():
    names={'PHYS':'Sprott Physical Gold Trust','PSLV':'Sprott Physical Silver Trust','GOOGM':'Alphabet Inc. Depositary Shares representing Preferred Stock','BRF-PA.TO':'Brookfield Renewable Power Preferred Equity Inc.','PFBC':'Preferred Bank','O':'Realty Income Corporation','GOOG':'Alphabet Inc. Class C','GOOGL':'Alphabet Inc. Class A','MSFT.NE':'MICROSOFT CORP CDR CAD HEDGED'}
    rows=[dict(symbol=s,name=n,region_code='ca' if s.endswith(('.TO','.NE')) else 'us',exchange='Toronto' if s.endswith('.TO') else 'Cboe CA' if s.endswith('.NE') else 'NasdaqGS',instrument='stock',active=True) for s,n in names.items()]
    kept={s for s,v in model.main_listing_flags(rows).items() if v['main_listing']}
    assert kept=={'PFBC','O','GOOG','GOOGL'}


def test_empty_profile_response_cannot_erase_verified_growth_history():
    frame=pd.DataFrame([[133.1,121,110,100]],index=['Net Income'],columns=pd.to_datetime(['2025-12-31','2024-12-31','2023-12-31','2022-12-31']))
    old=model.annual_growth_values(frame,'KRW')
    new=model.annual_growth_values(pd.DataFrame(),None,old)
    assert new['net_income_growth_3y']==pytest.approx(10)
    assert new['annual_income_history']==old['annual_income_history']


def test_primary_view_excludes_investment_trusts_and_split_funds_without_excluding_reits():
    names={'TEM.L':('Templeton Emerging Markets Investment Trust plc',None),
           'SHORT.L':('Templeton Emerging Mkts Invmt Tr TEMIT',None),
           'IAD.L':('Invesco Asia Dragon Trust plc','Asset Management'),
           'CTY.L':('The City of London Investment Trust plc','Asset Management'),
           'XTD.TO':('TDb Split Corp.',None),
           'TRUST.L':('Trust Software plc','Software - Application'),
           'REIT.L':('Regional REIT Limited','REIT - Office')}
    rows=[dict(symbol=s,name=n,industry=i,region_code='ca' if s.endswith('.TO') else 'gb',exchange='Toronto' if s.endswith('.TO') else 'LSE',instrument='stock',active=True) for s,(n,i) in names.items()]
    kept={s for s,v in model.main_listing_flags(rows).items() if v['main_listing']}
    assert kept=={'TRUST.L','REIT.L'}


def test_conflicting_currency_is_withheld_from_rankings_until_verified():
    row=dict(symbol='241560.KS',financial_currency='USD',quote_currency='KRW',net_income_local=498988734000,
             annual_income_history={'currency':'KRW','net_income':{'2025-12-31':402344616000}})
    result=model.dollarise(row,{'USD':{'rate':1},'KRW':{'rate':.0007}})
    assert result['net_income'] is None
    assert result['financial_quality_note']


def test_empty_profile_does_not_certify_annual_history_unavailable():
    data=model.annual_growth_values(pd.DataFrame(),'USD')
    assert data['annual_growth_version']==0
    old=dict(annual_growth_version=1,annual_income_history={'currency':'USD','revenue':{},'net_income':{}})
    assert model.dollarise(old,{})['annual_growth_version']==0
    old['annual_growth_status']='no_data'
    assert model.dollarise(old,{})['annual_growth_version']==1


def test_custom_screen_is_packed_and_excludes_private_statement_payload(tmp_path,monkeypatch):
    import json
    s=model.Store(tmp_path/'s.sqlite')
    s.upsert_many([dict(symbol='TEST',name='Test',region_code='us',instrument='stock',active=True,exchange='NYSE',private_note='secret',technical_history={'daily':{'closes':[10,20],'dates':['2026-01-01','2026-01-02']}},technical_currency='USD')])
    monkeypatch.setattr(model,'store',s)
    data=json.loads(model.technical_screen().body)
    row=dict(zip(data['fields'],data['rows'][0]))
    assert row['symbol']=='TEST'
    assert 'private_note' not in row and 'statement_history' not in row
    assert data['technicals']['TEST']['daily']['closes']==[10,20]


def test_verified_seed_repairs_growth_currency_after_headline_migration(tmp_path,monkeypatch):
    import json
    import refresh_data
    s=model.Store(tmp_path/'s.sqlite')
    old=dict(symbol='BOB',region_code='kr',financial_currency='KRW',financial_currency_version=2,
             annual_income_history={'currency':'USD','net_income':{'2025-12-31':100}},net_income_growth_3y_period='USD')
    s.upsert_many([old])
    seed={'rows':[dict(old,annual_income_history={'currency':'KRW','net_income':{'2025-12-31':100}},net_income_growth_3y_period='KRW')]}
    (tmp_path/'seed').mkdir();(tmp_path/'seed'/'verified-repairs.json').write_text(json.dumps(seed))
    monkeypatch.setattr(model,'ROOT',tmp_path)
    refresh_data.restore_verified_repairs(s)
    assert s.get('BOB')['annual_income_history']['currency']=='KRW'
    assert s.get('BOB')['net_income_growth_3y_period']=='KRW'
    s.upsert_many([dict(symbol='BOB',region_code='kr',annual_income_history={'currency':'USD','net_income':{'2026-12-31':200}})])
    refresh_data.restore_verified_repairs(s)
    assert s.get('BOB')['annual_income_history']['net_income']=={'2026-12-31':200}
