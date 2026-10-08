import pytest
import sys
from pathlib import Path
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as model


def test_chart_uses_full_filtered_population_and_compact_fields(tmp_path, monkeypatch):
    store = model.Store(tmp_path / 'chart.sqlite')
    store.set_meta('fx', {'USD': {'rate': 1}})
    store.upsert_many([dict(symbol=f'TEST{i}', name=f'Test {i}', region_code='us', region='United States',
        active=True, instrument='stock', exchange='NYSE', financial_currency='USD', net_income_local=i, div_years=i % 30,
        private_note='never return', dividend_events=[{'date':'2025-01-01','amount':1}]) for i in range(301)])
    store.upsert_many([dict(symbol='MISSING',name='Missing',region_code='us',region='United States',active=True,instrument='stock',exchange='NYSE',financial_currency='USD',net_income_local=100,div_years=None)])
    monkeypatch.setattr(model, 'store', store)
    result = model.chart(x='net_income', y='div_years', main_only=False,
        filters='[{"field":"net_income","op":"gte","value":10}]')
    assert result['total'] == 292
    assert len(result['rows']) == 291
    assert all(r['net_income'] >= 10 for r in result['rows'])
    assert result['rows'][0]['symbol']
    assert all('private_note' not in r and 'dividend_events' not in r for r in result['rows'])


@pytest.mark.parametrize('axis', ['sector', 'unknown', 'symbol'])
def test_chart_rejects_non_numeric_axes(axis):
    with pytest.raises(HTTPException) as error:
        model.chart(x=axis)
    assert error.value.status_code == 400


def test_chart_distinguishes_pending_from_checked_unavailable(tmp_path, monkeypatch):
    store = model.Store(tmp_path / 'coverage.sqlite')
    common = dict(region_code='us', region='United States', active=True, instrument='stock')
    store.upsert_many([
        dict(common,symbol='ZERO',revenue_growth_3y=0,net_income_growth_1y=-150),
        dict(common,symbol='PENDING'),
        dict(common,symbol='INVALID',annual_growth_version=1,revenue_growth_3y=12),
        dict(common,symbol='EMPTY',annual_growth_version=1),
    ])
    monkeypatch.setattr(model, 'store', store)
    result = model.chart(x='revenue_growth_3y',y='net_income_growth_1y',main_only=False)
    assert result['total'] == 4
    assert len(result['rows']) == 1
    assert result['coverage'] == dict(awaiting=1, unavailable=2)
    assert result['rows'][0]['net_income_growth_1y'] == -150
    filtered = model.chart(x='revenue_growth_3y',y='net_income_growth_1y',main_only=False,search='ZERO')
    assert filtered['coverage'] == dict(awaiting=0, unavailable=0)


def test_chart_income_fetch_does_not_pretend_full_profile_loaded(tmp_path, monkeypatch):
    store = model.Store(tmp_path / 'income.sqlite')
    store.set_meta('fx', {'USD': {'rate': 1}})
    common = dict(region_code='us',active=True,instrument='stock',financial_currency='USD')
    store.upsert_many([dict(common,symbol='ANNUAL',income_fetched='2026-10-06',revenue_local=10),dict(common,symbol='PENDING')])
    monkeypatch.setattr(model,'store',store)
    assert model.chart(x='revenue',y='net_income',main_only=False)['coverage'] == dict(awaiting=1,unavailable=1)
    assert model.chart(x='revenue',y='fcf',main_only=False)['coverage'] == dict(awaiting=2,unavailable=0)


def test_analysis_peers_are_global_compact_and_exclude_wrappers(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'peers.sqlite')
    shared=dict(region_code='us',active=True,instrument='stock',exchange='NYSE',sector='Technology',industry='Software')
    store.upsert_many([dict(shared,symbol='TARGET',name='Target'),dict(shared,symbol='PEER',name='Peer',private_note='secret',dividend_events=[{'date':'2025-01-01','amount':1}]),dict(shared,symbol='PEER.OTC',name='Peer',exchange='PNK'),dict(shared,symbol='OTHER',sector='Energy',industry='Oil')])
    monkeypatch.setattr(model,'store',store)
    result=model.analysis_peers('TARGET')
    assert {r['symbol'] for r in result['rows']}=={'TARGET','PEER'}
    assert result['population']['total']==3
    assert all('private_note' not in r and 'dividend_events' not in r for r in result['rows'])
    with pytest.raises(HTTPException):model.analysis_peers('UNKNOWN')


def test_independent_dividend_fetch_is_unavailable_rather_than_waiting(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'dividend.sqlite')
    store.upsert_many([dict(symbol=symbol,region_code='us',active=True,instrument='stock',main_listing=True,
        net_income_local=100,income_fetched='2026-10-08',**extra) for symbol,extra in
        [('CHECKED',{'dividend_fetched':'2026-10-07'}),('PENDING',{})]])
    monkeypatch.setattr(model,'store',store)
    result=model.chart(x='net_income',y='div_years',main_only=False)
    assert result['coverage']==dict(awaiting=1,unavailable=1)
