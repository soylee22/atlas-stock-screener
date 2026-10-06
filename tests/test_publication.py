import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as model
import build_site
import refresh_data


def seeded_store(tmp_path):
    store = model.Store(tmp_path / 'cache' / 'test.sqlite')
    model.LogoCache(store)
    store.set_meta('fx', {'USD': {'rate': 1}, 'GBP': {'rate': 1.25}})
    store.upsert_many([dict(symbol='TEST.' + code.upper(), name='Test ' + code, region_code=code,
        region=name, instrument='stock', active=True, exchange='NYSE' if code == 'us' else name,
        quote_currency='GBp', financial_currency='USD', price_local=1000, market_cap_local=10e9,
        net_income_local=1e9, financial_fetched='2026-10-01T00:00:00+00:00',
        private_note='DO-NOT-PUBLISH', dividend_currency='GBP',
        dividend_events=[{'date': '2025-06-01', 'amount': 2}],
        financial_error='/private/path SECRET', description='Public description')
        for code, name in model.REGIONS.items()])
    return store


def test_public_build_and_seed_only_publish_allowed_data(tmp_path):
    store = seeded_store(tmp_path)
    output, seed = tmp_path / 'site', tmp_path / 'seed.tar.gz'
    result = build_site.build_site(store.path, output, seed)
    assert result['detail_files'] == 6
    html = (output / 'index.html').read_text()
    assert 'content="snapshot"' in html
    assert 'src="./static/app.js"' in html
    data = json.loads((output / 'data' / 'stocks.json').read_text())
    mapped = [dict(zip(data['fields'], row)) for row in data['rows']]
    assert mapped[0]['market_cap'] == 12.5e9
    assert mapped[0]['price'] == 12.5
    assert 'private_note' not in data['fields']
    detail = json.loads(next((output / 'data' / 'details').glob('*.json')).read_text())
    assert 'private_note' not in detail
    assert '/private/path' not in detail['financial_error']
    csv = (output / detail['dividend_exports']['annual']).read_text()
    assert '2025' in csv and '2.5' in csv
    restored = model.Store(tmp_path / 'restored' / 'test.sqlite')
    assert refresh_data.restore_seed(restored, seed)
    assert not refresh_data.restore_seed(restored, seed)
    row = restored.get('TEST.US')
    assert row['market_cap'] == 12.5e9 and row['net_income'] == 1e9
    assert 'private_note' not in row
    with restored.connect() as conn:
        timestamp = conn.execute('SELECT enriched FROM stocks LIMIT 1').fetchone()[0]
        assert timestamp == 1790812800


def test_builder_rejects_source_and_unrelated_output(tmp_path):
    store = seeded_store(tmp_path)
    with pytest.raises(ValueError, match='source or cache'):
        build_site.build_site(store.path, store.path.parent)
    output = tmp_path / 'existing'
    output.mkdir()
    (output / 'precious.txt').write_text('keep')
    with pytest.raises(ValueError, match='not an Atlas'):
        build_site.build_site(store.path, output)
    assert (output / 'precious.txt').read_text() == 'keep'


def test_incomplete_quote_scan_restores_previous_data(tmp_path):
    store = seeded_store(tmp_path)
    class BrokenPipeline:
        def __init__(self):
            self.store = store
        def load_fx(self):
            store.set_meta('fx', {'GBP': {'rate': 99}})
        def ingest(self):
            store.upsert_many([dict(symbol='TEST.US', region_code='us', price_local=1)])
            store.set_meta('coverage', {'us': {'finished': True}})
    assert not refresh_data.refresh_quotes(BrokenPipeline())
    assert store.get('TEST.US')['price'] == 12.5
    assert store.meta('fx')['GBP']['rate'] == 1.25
    assert 'Previous quotes retained' in store.meta('quote_error')
