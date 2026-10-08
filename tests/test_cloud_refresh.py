import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as model
import cloud_refresh
import refresh_data
import build_site


def row(symbol, region='us', **extra):
    return dict(symbol=symbol, region_code=region, active=True, instrument='stock',
        main_listing=True, market_cap_local=1000, **extra)


def test_new_main_profiles_precede_stale_and_rotate_markets(tmp_path):
    store=model.Store(tmp_path/'test.sqlite')
    store.upsert_many([row('STALE', annual_growth_version=1)], enriched=True)
    with store.connect() as conn:
        conn.execute('UPDATE stocks SET enriched=?, attempted=0', (time.time()-8*86400,))
    store.upsert_many([row('US1'),row('US2'),row('JP1','jp'),
        dict(row('PREF'),main_listing=False),
        dict(row('INACTIVE'),active=False)])
    assert store.enrichment_candidates()==['US1','JP1','US2','STALE']
    assert store.claim_enrichment('US1')
    assert not store.claim_enrichment('US1')
    assert not store.claim_enrichment('PREF')
    assert not store.claim_enrichment('INACTIVE')
    assert store.enrichment_candidates()==['US2','JP1','STALE']


def test_cloud_limit_counts_real_claims_and_keeps_completed_rows(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'test.sqlite')
    store.upsert_many([row('ONE')])
    monkeypatch.setattr(refresh_data,'restore_seed',lambda *args:False)
    monkeypatch.setattr(store,'classify_listings',lambda **kwargs:None)
    store.set_meta('last_quote_run',time.time())
    class Pipeline:
        def __init__(self,*args): self.stop=threading.Event()
        def enrich(self,symbol):
            store.upsert_many([dict(row(symbol),financial_fetched=model.now_iso(),annual_growth_version=1)],enriched=True)
    monkeypatch.setattr(model,'Pipeline',Pipeline)
    result=refresh_data.refresh(store,tmp_path/'absent',seconds=5,limit=20,workers=4)
    assert result['financials']==dict(attempted=1,succeeded=1,failed=0)
    assert result['stop_reason']=='queue_complete'
    assert result['remaining_eligible']==0
    assert store.get('ONE')['financial_fetched']


def test_full_profile_rate_limit_stops_queue_and_preserves_old_values(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'test.sqlite')
    store.upsert_many([dict(row('FIRST'),net_income_local=300),row('NEXT')])
    monkeypatch.setattr(refresh_data,'restore_seed',lambda *args:False)
    monkeypatch.setattr(store,'classify_listings',lambda **kwargs:None)
    store.set_meta('last_quote_run',time.time())
    class Pipeline:
        def __init__(self,*args): self.stop=threading.Event()
        def enrich(self,symbol): raise RuntimeError('HTTP 429 Too Many Requests')
    monkeypatch.setattr(model,'Pipeline',Pipeline)
    result=refresh_data.refresh(store,tmp_path/'absent',seconds=5,limit=20,workers=1)
    assert result['financials']==dict(attempted=1,succeeded=0,failed=1)
    assert result['stop_reason']=='rate_limited'
    assert store.get('FIRST')['net_income_local']==300
    assert store.enrichment_candidates()==['NEXT']


def test_two_cloud_parts_aggregate_and_skip_after_rate_limit(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'test.sqlite')
    monkeypatch.setenv('GITHUB_RUN_ID','run-1')
    calls=[]
    def refresh(*args,**kwargs):
        calls.append(kwargs)
        return dict(financials=dict(attempted=4,succeeded=3,failed=1),quotes_attempted=True,
            quotes_succeeded=True,stop_reason='rate_limited')
    monkeypatch.setattr(refresh_data,'refresh',refresh)
    monkeypatch.setattr(refresh_data,'backfill_growth',lambda *args,**kwargs:pytest.fail('Rate-limited run must stop source work'))
    cloud_refresh.cloud_batch(store,'1',seconds=11,limit=9,force_quotes=True)
    cloud_refresh.cloud_batch(store,'2',seconds=11,limit=9)
    result=cloud_refresh.cloud_batch(store,'history')
    assert calls==[dict(seconds=5,limit=4,force_quotes=True,workers=4)]
    assert result['financials']==dict(attempted=4,succeeded=3,failed=1)
    assert result['phase']=='finished' and result['rate_limited']
    status=build_site.snapshot_status([],{'cloud_refresh':result},0,model.now_iso())
    assert status['cloud_refresh']==result and '01:23 UK' in status['snapshot']['cadence']
    monkeypatch.setenv('GITHUB_RUN_ID','other-run')
    with pytest.raises(ValueError,match='another run'):
        cloud_refresh.cloud_batch(store,'2')


def test_checkpoint_parts_share_exact_budget(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'test.sqlite')
    calls=[]
    def refresh(*args,**kwargs):
        calls.append(kwargs)
        return dict(financials=dict(attempted=kwargs['limit'],succeeded=kwargs['limit'],failed=0),
            quotes_attempted=kwargs['force_quotes'],quotes_succeeded=True,stop_reason='profile_limit')
    monkeypatch.setattr(refresh_data,'refresh',refresh)
    cloud_refresh.cloud_batch(store,'1',seconds=11,limit=9,force_quotes=True)
    result=cloud_refresh.cloud_batch(store,'2',seconds=11,limit=9)
    assert sum(c['seconds'] for c in calls)==11
    assert sum(c['limit'] for c in calls)==9
    assert result['financials']['succeeded']==9 and result['quotes_succeeded']
    assert not calls[1]['force_quotes']
