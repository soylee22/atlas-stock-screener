import json
import app as model
import refresh_data


def test_europe_queries_and_us_scope():
    for region in ['de','es','it','nl','dk','se']:
        q=model.region_query(region).to_dict()
        assert q['operator']=='AND'
        assert {'operator':'GT','operands':['intradaymarketcap',0]} in q['operands']
    assert model.region_query('us').to_dict()=={'operator':'EQ','operands':['region','us']}


def test_europe_primary_exchanges_and_home_market_deduplication():
    def r(symbol,name,region,exchange,country=None,volume=1):
        return dict(symbol=symbol,name=name,region_code=region,exchange=exchange,domicile=country,active=True,instrument='stock',volume=volume,price=100)
    rows=[r('SAP.DE','SAP SE','de','XETRA','Germany'),r('SAP.F','SAP SE','de','Frankfurt','Germany',100),
          r('BMW3.DE','BMW preferred stock','de','XETRA'),r('SAN.MC','Santander','es','MCE','Spain'),
          r('ENI.MI','Eni','it','Milan','Italy'),r('1NVDA.MI','NVIDIA Corporation','it','Milan'),
          r('ASML.AS','ASML Holding N.V.','nl','Amsterdam','Netherlands'),r('ASML','ASML Holding N.V.','us','NasdaqGS','Netherlands',100),
          r('NOVO-B.CO','Novo Nordisk A/S','dk','Copenhagen','Denmark'),r('NOVO-A.CO','Novo Nordisk A/S','dk','Copenhagen','Denmark'),
          r('VOLV-B.ST','AB Volvo (publ)','se','Stockholm','Sweden')]
    flags=model.main_listing_flags(rows)
    kept={s for s,v in flags.items() if v['main_listing']}
    assert kept=={'SAP.DE','SAN.MC','ENI.MI','ASML.AS','NOVO-B.CO','NOVO-A.CO','VOLV-B.ST'}
    assert flags['SAP.F']['listing_reason']=='Alternative listing of SAP.DE'


def test_european_currency_values_and_region_filter():
    for region,currency,rate in [('de','EUR',1.1),('es','EUR',1.1),('it','EUR',1.1),('nl','EUR',1.1),('dk','DKK',.15),('se','SEK',.1)]:
        row=model.dollarise(dict(region_code=region,quote_currency=currency,financial_currency=currency,price_local=20,net_income_local=100),{currency:{'rate':rate}})
        assert row['price']==20*rate and row['net_income']==100*rate
    where,args,_=model.query_sql(regions='de,dk,se',main_only=True)
    assert args==['de','dk','se'] and "main_listing" in where


def test_extension_seed_does_not_replace_fresher_cached_financials(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'cache.sqlite')
    store.upsert_many([dict(symbol='SAP.DE',region_code='de',net_income_local=999,financial_currency='EUR')])
    seed=tmp_path/'europe.json';seed.write_text(json.dumps({'rows':[dict(symbol='SAP.DE',region_code='de',net_income_local=1),dict(symbol='SAN.MC',region_code='es',name='Santander',financial_fetched='2026-10-07T12:00:00+00:00')],'coverage':{'es':{'finished':True}},'fx':{'DKK':{'rate':.15,'fetched':'2026-10-07'}}}))
    monkeypatch.setattr(refresh_data,'EUROPE_SEED',seed)
    refresh_data.restore_market_extension(store)
    refresh_data.restore_market_extension(store)
    assert store.get('SAP.DE')['net_income_local']==999
    assert store.get('SAN.MC')['name']=='Santander'
    assert store.meta('coverage')['es']['finished']
    assert store.meta('fx')['DKK']['rate']==.15


def test_targeted_growth_only_claims_the_selected_new_markets(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'growth.sqlite')
    for symbol,region in [('SAP.DE','de'),('SAN.MC','es'),('OLD','us')]:
        store.upsert_many([dict(symbol=symbol,region_code=region,active=True,instrument='stock',main_listing=True)])
    calls=[]
    def fetch(symbol):
        calls.append(symbol)
        return model.pd.DataFrame(),None
    monkeypatch.setattr(model,'fetch_annual_income',fetch)
    result=refresh_data.backfill_growth(store,seconds=10,limit=10,workers=1,regions=['de','es'])
    assert set(calls)=={'SAP.DE','SAN.MC'}
    assert result['annual_growth']['no_data']==2
    assert not store.get('OLD').get('annual_growth_attempted')


def test_bootstrap_stops_new_requests_after_provider_rate_limit(tmp_path,monkeypatch):
    store=model.Store(tmp_path/'bootstrap.sqlite')
    store.upsert_many([dict(symbol='SAP.DE',region_code='de'),dict(symbol='ITX.MC',region_code='es')])
    store.set_meta('europe_bootstrap_pending',True)
    calls=[]
    def enrich(self,symbol):
        calls.append(symbol)
        raise RuntimeError('429 Too Many Requests, rate limited')
    monkeypatch.setattr(model.Pipeline,'enrich',enrich)
    monkeypatch.setattr(refresh_data,'backfill_growth',lambda *args,**kw: (_ for _ in ()).throw(AssertionError('No further provider requests')))
    refresh_data.bootstrap_europe(store,seconds=10,limit=10)
    assert calls==['SAP.DE']
    assert store.get('SAP.DE')['financial_error']
