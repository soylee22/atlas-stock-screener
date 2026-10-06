import json
import re
import shutil
import sys
import threading
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
    script = re.search(r'src="\./(static/[a-f0-9]{16}/app.js)"', html).group(1)
    assert (output / script).is_file()
    assert (output / script).parent.joinpath('data-source.js').is_file()
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


def test_annual_only_stock_has_detail_without_fabricated_dividend_history(tmp_path):
    store = seeded_store(tmp_path)
    store.upsert_many([dict(symbol='ANNUAL',region_code='us',active=True,instrument='stock',
        annual_growth_version=1,income_fetched='2026-10-06',revenue_growth_1y=20,
        annual_income_history={'currency':'USD','revenue':{'2026-06-30':120,'2025-06-30':100}})])
    output = tmp_path / 'site'
    build_site.build_site(store.path,output)
    snapshot = json.loads((output / 'data' / 'stocks.json').read_text())
    row = next(dict(zip(snapshot['fields'],values)) for values in snapshot['rows'] if values[snapshot['fields'].index('symbol')]=='ANNUAL')
    assert row['annual_growth_version'] == 1 and row['income_fetched'] == '2026-10-06'
    detail = json.loads((output / 'data' / 'details' / (row['detail_key']+'.json')).read_text())
    assert detail['annual_income_history']['revenue']['2026-06-30'] == 120
    assert not detail.get('financial_fetched') and not detail.get('dividend_events')
    assert (output / detail['dividend_exports']['annual']).read_text().count('\n') == 1


def test_incomplete_quote_scan_restores_previous_data(tmp_path, caplog):
    store = seeded_store(tmp_path)
    class BrokenPipeline:
        def __init__(self):
            self.store = store
            self.stop = threading.Event()
        def load_fx(self):
            store.set_meta('fx', {'GBP': {'rate': 99}})
        def ingest(self):
            store.upsert_many([dict(symbol='TEST.US', region_code='us', price_local=1)])
            store.set_meta('coverage', {'us': {'finished': True}})
    assert not refresh_data.refresh_quotes(BrokenPipeline())
    assert store.get('TEST.US')['price'] == 12.5
    assert store.meta('fx')['GBP']['rate'] == 1.25
    assert 'Previous quotes retained' in store.meta('quote_error')
    assert 'Yahoo did not finish every market' in caplog.text


def test_expired_budget_stops_new_source_requests(tmp_path, monkeypatch):
    store = seeded_store(tmp_path)
    pipeline = model.Pipeline(store)
    pipeline.stop.set()
    def unexpected(*args, **kwargs):
        pytest.fail('No new Yahoo request is allowed after the job deadline')
    monkeypatch.setattr(model.yf, 'Ticker', unexpected)
    monkeypatch.setattr(model.yf, 'screen', unexpected)
    pipeline.load_fx()
    pipeline.ingest()
    assert store.get('TEST.US')['price'] == 12.5


def test_deployments_version_modules_and_retain_previous_bundle(tmp_path, monkeypatch):
    store = seeded_store(tmp_path)
    source = tmp_path / 'source'
    shutil.copytree(model.ROOT / 'static', source / 'static')
    monkeypatch.setattr(model, 'ROOT', source)
    output = tmp_path / 'site'
    build_site.build_site(store.path, output)
    old_script = re.search(r'src="\./(static/[a-f0-9]{16}/app.js)"', (output / 'index.html').read_text()).group(1)
    with (source / 'static' / 'format.js').open('a') as target:
        target.write('\n// New calculation helper revision\n')
    build_site.build_site(store.path, output)
    new_script = re.search(r'src="\./(static/[a-f0-9]{16}/app.js)"', (output / 'index.html').read_text()).group(1)
    assert old_script != new_script
    assert (output / old_script).is_file()
    assert (output / new_script).is_file()
    assert 'New calculation helper revision' not in (output / old_script).parent.joinpath('format.js').read_text()
    assert 'New calculation helper revision' in (output / new_script).parent.joinpath('format.js').read_text()
