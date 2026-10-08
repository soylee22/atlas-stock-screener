import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as model
import build_site
import profile_seed


def test_profile_seed_preserves_quotes_fx_newer_history_and_verified_currency(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'cache.sqlite')
    monkeypatch.setattr(store,'classify_listings',lambda **kwargs:None)
    store.set_meta('fx',{'KRW':{'rate':.001},'USD':{'rate':1}})
    base=dict(region_code='kr',active=True,instrument='stock',main_listing=True,
        quote_currency='KRW',price_local=2000,market_cap_local=100000,quote_fetched='2026-10-08')
    store.upsert_many([dict(base,symbol='NEW'),dict(base,symbol='RECENT',financial_fetched='2026-10-08',financial_currency_version=2,net_income_local=100,financial_currency='KRW'),dict(base,symbol='UNVERIFIED')])
    source=[dict(base,symbol=symbol,price_local=1,market_cap_local=2,financial_fetched='2026-10-07',
        financial_currency_version=2 if symbol!='UNVERIFIED' else 0,financial_currency='KRW',net_income_local=499e9,
        dividend_fetched='2026-10-07',div_years=15,dividend_events=[dict(date='1984-01-01',amount=20)],
        annual_growth_fetched='2026-10-07',annual_growth_version=1,
        annual_income_history={'currency':'KRW','net_income':{'2025-12-31':300},'revenue':{'2025-12-31':1000}},
        private_note='PRIVATE',sector='Industrials') for symbol in ['NEW','RECENT','UNVERIFIED']]
    seed=tmp_path/'source.tar.gz'
    build_site.make_seed(seed,source,{'fx':{'KRW':{'rate':999}}},[],tmp_path)
    result=profile_seed.merge_profiles(store,seed)
    assert result['merged_profiles']==1
    for symbol in ['NEW','RECENT','UNVERIFIED']:
        row=store.get(symbol)
        assert row['price_local']==2000 and row['price']==2
        assert row['market_cap_local']==100000 and row['quote_fetched']=='2026-10-08'
        assert row['div_years']==15 and row['dividend_events'][0]['date']=='1984-01-01'
        assert 'private_note' not in row
    assert store.get('NEW')['net_income']==pytest.approx(499e6)
    assert store.get('RECENT')['net_income_local']==100
    assert store.get('UNVERIFIED')['net_income_local']==300
    assert store.get('UNVERIFIED')['income_period']=='FY 2025-12-31'
    assert not store.get('UNVERIFIED').get('financial_fetched')
    assert store.meta('fx')['KRW']['rate']==.001


def test_older_dividends_do_not_replace_newer_collection(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'cache.sqlite')
    monkeypatch.setattr(store,'classify_listings',lambda **kwargs:None)
    base=dict(symbol='TEST',region_code='us',active=True,instrument='stock',main_listing=True)
    store.upsert_many([dict(base,dividend_fetched='2026-10-08',div_years=20)])
    seed=tmp_path/'source.tar.gz'
    build_site.make_seed(seed,[dict(base,dividend_fetched='2026-10-07',div_years=19)],{},[],tmp_path)
    profile_seed.merge_profiles(store,seed)
    assert store.get('TEST')['div_years']==20
