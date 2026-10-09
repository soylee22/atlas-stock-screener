import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from statements import earnings_per_share
import app as model
import build_site


def source(dates, values, currency='GBP'):
    return dict(currency=currency, fetched_at='2026-10-09', periods=[
        dict(end_date=d, values={'Diluted EPS':v}) for d,v in zip(dates,values)])


def history():
    return dict(income_quarterly=source(['2025-09-30','2025-12-31','2026-03-31','2026-06-30'],[1,2,-1,0]),
                income_annual=source(['2024-06-30','2025-06-30'],[2,3]))


def test_quarterly_losses_zero_and_reporting_fx_not_pence_quote():
    row=model.dollarise(dict(quote_currency='GBp',financial_currency='USD',statement_history=history()),
                        {'GBP':{'rate':1.25},'USD':{'rate':1}})
    assert row['eps_diluted_local']==2
    assert row['eps_diluted']==2.5
    assert row['eps_diluted_period']=='TTM 2026-06-30'
    assert row['eps_currency']=='GBP' and row['eps_fetched']=='2026-10-09'


@pytest.mark.parametrize('mode',['gap','missing','too_few','unverified'])
def test_annual_fallback_requires_latest_four_quarters(mode):
    h=history(); q=h['income_quarterly']
    if mode=='gap':q['periods'][1]['end_date']='2025-10-01'
    if mode=='missing':q['periods'][-1]['values']['Diluted EPS']=None
    if mode=='too_few':q['periods'].pop(0)
    if mode=='unverified':q['currency']=None
    r=earnings_per_share(h)
    assert r['eps_diluted_local']==3 and r['eps_diluted_period']=='FY 2025-06-30'


def test_does_not_skip_latest_missing_annual_or_latest_quarter():
    h=history();h['income_quarterly']['periods'].append(dict(end_date='2026-09-30',values={}))
    h['income_annual']['periods'][-1]['values']['Diluted EPS']=None
    assert earnings_per_share(h)['eps_diluted_local'] is None
    assert earnings_per_share(h)['eps_version']==1
    assert earnings_per_share({})['eps_version']==0


def test_newer_annual_period_takes_precedence_over_stale_quarters():
    h=history();h['income_annual']['periods'].append(dict(end_date='2026-09-30',values={'Diluted EPS':4}))
    r=earnings_per_share(h)
    assert r['eps_diluted_local']==4 and r['eps_diluted_period']=='FY 2026-09-30'


def test_currency_conflicts_and_absent_fx_withhold_dollars():
    h=history()
    for s in h.values():s['currency']=None;s['status']='currency_conflict'
    assert earnings_per_share(h)['eps_diluted_local'] is None
    assert model.dollarise(dict(statement_history=history()),{})['eps_diluted'] is None


def test_cached_recalculation_publication_and_local_export(tmp_path,monkeypatch):
    import json,csv,io
    store=model.Store(tmp_path/'eps.sqlite')
    model.LogoCache(store)
    store.set_meta('fx',{'GBP':{'rate':1.25}})
    store.upsert_many([dict(symbol='TEST',name='Test',region_code='us',active=True,instrument='stock',
        main_listing=True,statement_history=history(),eps_diluted=999)])
    monkeypatch.setattr(model,'store',store)
    assert store.get('TEST')['eps_diluted']==2.5
    response=model.export(columns='eps_diluted',main_only=False)
    rows=list(csv.DictReader(io.StringIO(response.body.decode())))
    assert rows[0]['eps_diluted_period']=='TTM 2026-06-30' and rows[0]['eps_currency']=='GBP'
    store.upsert_many([dict(symbol='REGION.'+r,region_code=r,active=True,instrument='stock') for r in model.REGIONS if r!='us'])
    out=tmp_path/'site';build_site.build_site(store.path,out)
    snapshot=json.loads((out/'data/stocks.json').read_text())
    row=next(dict(zip(snapshot['fields'],v)) for v in snapshot['rows'] if v[snapshot['fields'].index('symbol')]=='TEST')
    assert row['eps_diluted']==2.5 and row['eps_version']==1
    detail=json.loads((out/'data/details'/f"{row['detail_key']}.json").read_text())
    assert detail['eps_diluted_local']==2 and detail['eps_reason'] is None
