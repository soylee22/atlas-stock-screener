import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as model
import build_site
import refresh_data


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def source_store(tmp_path):
    store = model.Store(tmp_path / 'quotes.sqlite')
    old = int(time.time()) - 7200
    store.set_meta('fx', {'USD': {'rate': 1}, 'GBP': {
        'rate': 1.25, 'date': '2026-10-06', 'fetched': stamp(old)}})
    store.set_meta('quote_completed', stamp(old))
    store.upsert_many([dict(symbol=symbol, region_code='us', active=True,
        instrument='stock', main_listing=True, quote_currency='USD', price_local=10,
        market_cap_local=30e9, quote_timestamp=old, quote_time=stamp(old),
        quote_fetched=stamp(old), financial_currency='USD', net_income_local=100,
        financial_fetched=stamp(old), statement_history={'retained': {}},
        technical_asof='2026-10-09') for symbol in ['FRESH', 'MISSING']], enriched=True)
    return store, old


class IncompleteScan:
    def __init__(self, store, old, changes=None):
        self.store = store
        self.stop = threading.Event()
        self.old = old
        self.changes = changes or {}

    def load_fx(self):
        self.store.set_meta('fx', {'USD': {'rate': 1}, 'GBP': {
            'rate': 1.5, 'date': '2026-10-12', 'fetched': stamp(self.old + 3600)}})

    def ingest(self):
        quote = dict(symbol='FRESH', region_code='us', quote_currency='USD',
            quote_type='EQUITY', price_local=20, market_cap_local=40e9,
            quote_timestamp=self.old + 3600, quote_time=stamp(self.old + 3600),
            quote_fetched=model.now_iso(), pe=12, active=True,
            financial_currency='KRW', net_income_local=999, technical_asof='wrong')
        quote.update(self.changes)
        self.store.upsert_many([quote, dict(symbol='NEW', region_code='us', active=True,
            instrument='stock', quote_currency='USD', price_local=5, market_cap_local=30e9)])
        # Real scans deactivate rows they did not return. Force the >3% loss guard.
        self.store.upsert_many([dict(symbol='MISSING', region_code='us', active=False),
            dict(symbol='NEW', region_code='us', active=False)])
        self.store.set_meta('coverage', {code: {'finished': True} for code in model.REGIONS})


def test_market_loss_keeps_fresh_quotes_membership_and_raw_sources(tmp_path):
    store, old = source_store(tmp_path)
    before = store.get('FRESH')
    assert not refresh_data.refresh_quotes(IncompleteScan(store, old))
    fresh, missing = store.get('FRESH'), store.get('MISSING')
    assert fresh['price_local'] == 20 and fresh['market_cap_local'] == 40e9
    assert fresh['quote_time'] == stamp(old + 3600)
    assert missing['active'] and missing['price_local'] == 10
    assert fresh['main_listing'] and store.get('NEW') is None
    for field in ['net_income_local', 'financial_currency', 'financial_fetched',
                  'statement_history', 'technical_asof']:
        assert fresh[field] == before[field]
    assert store.meta('fx')['GBP']['rate'] == 1.5
    assert store.meta('quote_refresh')['mode'] == 'partial'
    assert store.meta('quote_refresh')['updated'] == 1
    assert store.meta('quote_refresh')['cached'] == 1
    assert store.meta('last_quote_run') > time.time() - 10


@pytest.mark.parametrize('changes', [
    {'price_local': 0}, {'price_local': None}, {'price_local': 'invalid'},
    {'market_cap_local': -1}, {'quote_currency': 'GBp'}, {'region_code': 'gb'},
    {'quote_type': 'ETF'}, {'quote_time': 'not-a-date'},
    {'quote_timestamp': None}, {'quote_fetched': 'not-a-date'},
    {'quote_time': '2026-10-12T12:00:00'},
])
def test_invalid_observation_does_not_replace_cached_quote(tmp_path, changes):
    store, old = source_store(tmp_path)
    assert not refresh_data.refresh_quotes(IncompleteScan(store, old, changes))
    assert store.get('FRESH')['price_local'] == 10
    assert store.get('MISSING')['active']
    assert store.meta('quote_refresh')['mode'] == 'failed'
    assert store.meta('fx')['GBP']['rate'] == 1.25
    assert store.meta('quote_completed') == stamp(old)


def test_older_market_timestamp_cannot_replace_newer_cached_quote(tmp_path):
    store, old = source_store(tmp_path)
    changes = {'quote_timestamp': old - 1, 'quote_time': stamp(old - 1)}
    assert not refresh_data.refresh_quotes(IncompleteScan(store, old, changes))
    assert store.get('FRESH')['quote_timestamp'] == old
    assert store.meta('quote_refresh')['updated'] == 0


def test_interrupted_scan_retains_quotes_already_received(tmp_path):
    store, old = source_store(tmp_path)
    pipeline = IncompleteScan(store, old)
    original = pipeline.ingest
    def interrupted():
        original()
        pipeline.stop.set()
    pipeline.ingest = interrupted
    assert not refresh_data.refresh_quotes(pipeline)
    assert store.get('FRESH')['price_local'] == 20
    assert store.get('MISSING')['active']


def test_published_partial_counts_and_message_do_not_claim_full_success(tmp_path):
    store, old = source_store(tmp_path)
    refresh_data.refresh_quotes(IncompleteScan(store, old))
    metadata = {key: store.meta(key) for key in ['quote_refresh', 'quote_error', 'fx']}
    metadata = build_site.clean_metadata(metadata)
    status = build_site.snapshot_status([store.get(s) for s in ['FRESH', 'MISSING']], metadata, 0, stamp(old))
    assert status['quote_refresh']['published_updated'] == 1
    assert status['quote_refresh']['published_cached'] == 1
    assert status['quote_refresh']['mode'] == 'partial'
    assert 'Some quotes remain cached' in status['error']
    assert 'quote_refresh' in build_site.META_KEYS


def test_fx_rejects_invalid_undated_and_older_updates():
    previous = {'GBP': {'rate': 1.25, 'date': '2026-10-10', 'fetched': '2026-10-10'}}
    for bad in [{'rate': 99}, {'rate': -1, 'date': '2026-10-12', 'fetched': '2026-10-12'},
                {'rate': 99, 'date': '2026-10-09', 'fetched': '2026-10-09'}]:
        assert refresh_data.retained_fx(previous, {'GBP': bad}) == previous


def test_next_complete_scan_clears_partial_state(tmp_path):
    store, old = source_store(tmp_path)
    store.set_meta('quote_refresh', {'mode': 'partial', 'started': stamp(old)})
    store.set_meta('quote_error', 'incomplete')
    pipeline = IncompleteScan(store, old)
    original = pipeline.ingest
    def complete():
        original()
        store.upsert_many([dict(symbol='MISSING', region_code='us', active=True)])
    pipeline.ingest = complete
    assert refresh_data.refresh_quotes(pipeline)
    assert store.meta('quote_error') is None
    assert store.meta('quote_refresh')['mode'] == 'complete'
