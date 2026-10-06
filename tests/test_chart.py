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
