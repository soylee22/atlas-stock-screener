"""Restore a public seed and refresh Yahoo data within a bounded job."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import app as model
from build_site import ICON_FILE
EUROPE_SEED = model.ROOT / 'seed' / 'europe.json'


def restore_verified_repairs(store):
    path=model.ROOT/'seed'/'verified-repairs.json'
    if not path.is_file(): return
    for incoming in json.loads(path.read_text()).get('rows',[]):
        old=store.get(incoming['symbol'])
        if old is None: continue
        values=dict(symbol=old['symbol'],region_code=old['region_code'])
        # Seed only missing technical data and pre-verification financial fields.
        # Never replace later verified source observations on scheduled runs.
        if not old.get('technical_version') and incoming.get('technical_version'):
            values.update({k:v for k,v in incoming.items() if k.startswith(('technical_','sma_'))})
        if old.get('financial_currency_version',0)<2 and incoming.get('financial_currency_version')==2:
            values.update({k:v for k,v in incoming.items() if k in {'financial_currency','financial_currency_version','financial_field_currencies','financial_quality_note','statement_history','statement_version','statement_fetched','statement_errors','income_period','cf_period','fcf_growth_period','net_margin','fcf_change','roic_proxy','roic_proxy_period','roic_proxy_inputs','roic_proxy_reason'} or k in model.MONETARY_FINANCIAL or k.removesuffix('_local') in model.MONETARY_FINANCIAL})
            # Verified currency takes precedence even when quote-only cache merging would retain profile metadata.
        fresh = incoming.get('annual_income_history',{})
        cached = old.get('annual_income_history',{})
        # The old profile refresh may have stamped correct raw amounts with the wrong currency.
        # Transfer the independently verified FY history and labels without overwriting newer FY observations.
        newest = lambda h: max([*h.get('revenue',{}),*h.get('net_income',{})],default='')
        if incoming.get('financial_currency_version')==2 and fresh.get('currency') and fresh.get('currency')!=cached.get('currency') and newest(fresh)>=newest(cached):
            values.update({k:v for k,v in incoming.items() if k.startswith(('annual_','revenue_growth_','net_income_growth_'))})
        if len(values)>2: store.upsert_many([values])


def restore_market_extension(store):
    path = EUROPE_SEED
    if not path.is_file():
        return
    payload = json.loads(path.read_text())
    with store.connect() as conn:
        existing = {r[0] for r in conn.execute('SELECT symbol FROM stocks')}
    incoming = [r for r in payload.get('rows', []) if r['symbol'] not in existing]
    if incoming:
        store.upsert_many(incoming)
        store.set_meta('europe_bootstrap_pending', True)
        with store.connect() as conn:
            conn.executemany('UPDATE stocks SET enriched=?,attempted=? WHERE symbol=?',
                [(datetime.fromisoformat(r['financial_fetched']).timestamp(),datetime.fromisoformat(r['financial_fetched']).timestamp(),r['symbol']) for r in incoming if r.get('financial_fetched')])
    coverage = store.meta('coverage', {})
    for region, value in payload.get('coverage', {}).items():
        if region not in coverage:
            coverage[region] = value
    store.set_meta('coverage', coverage)
    fx = store.meta('fx', {})
    for currency, value in payload.get('fx', {}).items():
        if currency not in fx or value.get('fetched', '') > fx[currency].get('fetched', ''):
            fx[currency] = value
    store.set_meta('fx', fx)


def restore_seed(store, seed):
    with store.connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]:
            restore_verified_repairs(store)
            restore_market_extension(store)
            return False
    logos = model.LogoCache(store)
    with tarfile.open(seed, "r:gz") as archive:
        payload = json.load(archive.extractfile("data.json"))
        if payload.get("version") != 1:
            raise ValueError("Unsupported seed version")
        for key, value in payload["metadata"].items():
            store.set_meta(key, value)
        store.upsert_many(payload["rows"])
        timestamps = []
        for row in payload["rows"]:
            if row.get("financial_fetched"):
                fetched = datetime.fromisoformat(row["financial_fetched"]).timestamp()
                timestamps.append((fetched, fetched, row["symbol"]))
        with store.connect() as conn:
            conn.executemany("UPDATE stocks SET enriched=?,attempted=? WHERE symbol=?", timestamps)
            for asset in payload["logos"]:
                filename = asset["filename"]
                if not ICON_FILE.fullmatch(filename):
                    raise ValueError("Unsafe icon filename in seed")
                content = archive.extractfile("logos/" + filename).read()
                model.icon_extension(content)
                (logos.root / filename).write_bytes(content)
                conn.execute("INSERT OR REPLACE INTO logo_assets VALUES(?,?,?,?,?,?)",
                    (asset["domain"], filename, asset["source_url"], asset["fetched"], asset["attempted"], None))
    restore_verified_repairs(store)
    restore_market_extension(store)
    return True


def restore_published_seed(store):
    with store.connect() as conn:
        if conn.execute('SELECT COUNT(*) FROM stocks').fetchone()[0]:
            return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'published.tar.gz'
            with urlopen('https://soylee22.github.io/atlas-stock-screener/data/cache-seed.tar.gz',timeout=240) as response, path.open('wb') as target:
                while chunk:=response.read(1024*1024):
                    target.write(chunk)
            return restore_seed(store,path)
    except OSError as error:
        logging.warning('Published recovery seed unavailable. Using starter seed: %s',error)
        return False


def refresh_quotes(pipeline):
    store = pipeline.store
    with store.connect() as conn:
        before = dict(conn.execute("SELECT region,COUNT(*) FROM stocks WHERE json_extract(data,'$.active')=1 GROUP BY region"))
    temporary = tempfile.NamedTemporaryFile(dir=store.path.parent, suffix=".sqlite", delete=False)
    backup = Path(temporary.name)
    temporary.close()
    with store.connect() as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    try:
        pipeline.load_fx()
        pipeline.ingest()
        if pipeline.stop.is_set():
            raise ValueError("Quote refresh exceeded the job time budget")
        coverage = store.meta("coverage", {})
        if any(coverage.get(code, {}).get("error") or not coverage.get(code, {}).get("finished") for code in model.REGIONS):
            raise ValueError("Yahoo did not finish every market")
        with store.connect() as conn:
            after = dict(conn.execute("SELECT region,COUNT(*) FROM stocks WHERE json_extract(data,'$.active')=1 GROUP BY region"))
        if any(after.get(code, 0) < count * .97 for code, count in before.items()):
            raise ValueError("Yahoo scan lost more than 3% of a cached market")
        store.set_meta("last_quote_run", time.time())
        store.set_meta("quote_error", None)
        return True
    except Exception as exc:
        with sqlite3.connect(backup) as source, store.connect() as destination:
            source.backup(destination)
        store.set_meta("quote_error", "Yahoo quote refresh failed. Previous quotes retained.")
        logging.warning("Quote refresh retained the previous snapshot: %s", exc)
        return False
    finally:
        backup.unlink(missing_ok=True)


def refresh(store, seed, seconds=720, limit=400, force_quotes=False, workers=2):
    restored = restore_seed(store, seed)
    started = model.now_iso()
    deadline = time.monotonic() + seconds
    logos = model.LogoCache(store)
    pipeline = model.Pipeline(store, logos)
    timer = threading.Timer(seconds, pipeline.stop.set)
    timer.daemon = True
    timer.start()
    due = force_quotes or time.time() - (store.meta("last_quote_run", 0) or 0) >= 86400
    quotes_ok = refresh_quotes(pipeline) if due else None
    store.classify_listings(force=True)
    counts = dict(attempted=0, succeeded=0, failed=0)
    lock = threading.Lock()
    queue = iter(store.enrichment_candidates())
    rate_limited = threading.Event()

    def worker():
        while not pipeline.stop.is_set() and time.monotonic() < deadline:
            with lock:
                if counts["attempted"] >= limit:
                    break
                symbol = next(queue, None)
                if not symbol:
                    break
                if not store.claim_enrichment(symbol):
                    continue
                counts["attempted"] += 1
            try:
                pipeline.enrich(symbol)
                store.set_meta("last_financial", dict(symbol=symbol, time=model.now_iso()))
                with lock:
                    counts["succeeded"] += 1
            except Exception as exc:
                store.failed_enrichment(symbol, exc)
                with lock:
                    counts["failed"] += 1
                if "429" in str(exc) or "rate" in str(exc).lower():
                    rate_limited.set()
                    pipeline.stop.set()
                    break
            pipeline.stop.wait(.3)

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(4, workers)))]
    icon_workers = [threading.Thread(target=logos.loop, args=(pipeline.stop,), daemon=True) for _ in range(2)]
    for thread in workers + icon_workers:
        thread.start()
    heartbeat = time.monotonic()
    while any(thread.is_alive() for thread in workers) and time.monotonic() < deadline:
        for thread in workers:
            thread.join(timeout=1)
        if time.monotonic() - heartbeat > 60:
            print(json.dumps(dict(progress=counts)), flush=True)
            heartbeat = time.monotonic()
    pipeline.stop.set()
    timer.cancel()
    for thread in workers + icon_workers:
        thread.join(timeout=15)
    store.classify_listings(force=True)
    result = dict(seed_restored=restored, quotes_attempted=due, quotes_succeeded=quotes_ok,
        financials=counts, icons=logos.status(), started=started, finished=model.now_iso(),
        stop_reason='rate_limited' if rate_limited.is_set() else 'time_budget' if time.monotonic() >= deadline
            else 'profile_limit' if counts['attempted'] >= limit else 'queue_complete',
        remaining_eligible=len(store.enrichment_candidates()))
    store.set_meta("refresh_health", result)
    print(json.dumps(result), flush=True)
    return result


def backfill_growth(store, seconds=1200, limit=30000, workers=4, regions=None):
    """Fetch annual statements across all main listings without full profiles."""
    deadline = time.monotonic() + seconds
    counts = dict(attempted=0, succeeded=0, failed=0, no_data=0)
    lock, stop = threading.Lock(), threading.Event()
    rate_limited = threading.Event()
    queue = store.growth_candidates()
    if regions is not None:
        if not regions or any(region not in model.REGIONS for region in regions):
            raise ValueError('Choose configured listing markets')
        with store.connect() as conn:
            allowed = {r[0] for r in conn.execute('SELECT symbol FROM stocks WHERE region IN ('+','.join('?' for _ in regions)+')',list(regions))}
        queue = [symbol for symbol in queue if symbol in allowed]
    candidates = iter(queue)
    heartbeat = time.monotonic()

    def worker():
        while not stop.is_set() and time.monotonic() < deadline:
            with lock:
                if counts["attempted"] >= limit:
                    return
                symbol = next(candidates, None)
                if not symbol:
                    return
                if not store.claim_growth(symbol):
                    continue
                counts["attempted"] += 1
            try:
                row = store.get(symbol)
                income, currency = model.fetch_annual_income(symbol)
                if stop.is_set() or time.monotonic() >= deadline:
                    # Do not postpone an unfinished request for the retry cooldown.
                    with store.connect() as conn:
                        conn.execute("UPDATE stocks SET data=json_remove(data,'$.annual_growth_attempted') WHERE symbol=? AND COALESCE(json_extract(data,'$.annual_growth_version'),0)<1", (symbol,))
                    return
                values = model.annual_growth_values(income, currency, row)
                values.update(symbol=symbol, region_code=row["region_code"])
                if not income.empty and currency:
                    # Fill FY income totals for listings without a loaded income period.
                    if not row.get("income_period") and (not row.get("financial_fetched") or row.get("financial_currency") == currency):
                        totals = model.financial_values(model.pd.DataFrame(), income, model.pd.DataFrame(), model.pd.DataFrame())
                        values.update({key: totals[key] for key in ["net_income_local", "revenue_local", "net_margin", "income_period"]})
                        values.update(financial_currency=currency, income_fetched=model.now_iso())
                    values["annual_growth_status"] = "available"
                else:
                    values["annual_growth_status"] = "no_data"
                    values["annual_growth_version"] = 1
                    values["annual_growth_missing"] = {key: "Yahoo returned no full annual records in a reporting currency" for key in model.GROWTH_KEYS}
                store.upsert_many([values])
                with lock:
                    counts["no_data" if income.empty else "succeeded"] += 1
            except Exception as exc:
                logging.warning("Annual statements %s: %s", symbol, exc)
                with lock:
                    counts["failed"] += 1
                if "429" in str(exc) or "rate" in str(exc).lower():
                    rate_limited.set()
                    stop.set()
                    return
            stop.wait(.05)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(4, workers)))]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads) and time.monotonic() < deadline:
        for thread in threads:
            thread.join(timeout=.5)
        if time.monotonic() - heartbeat >= 30:
            print(json.dumps(dict(annual_progress=counts.copy())), flush=True)
            heartbeat = time.monotonic()
    stop.set()
    for thread in threads:
        thread.join(timeout=16)
    result = dict(annual_growth=counts.copy(), finished=model.now_iso(), rate_limited=rate_limited.is_set())
    store.set_meta("annual_growth_backfill", result)
    print(json.dumps(result), flush=True)
    return result


def backfill_statements(store, seconds=180, limit=60):
    """Retain complete statement sets for existing verified reporting currencies."""
    from statements import capture_statements
    deadline = time.monotonic() + seconds
    counts = dict(attempted=0, succeeded=0, failed=0)
    with store.connect() as conn:
        candidates = [json.loads(r[0]) for r in conn.execute("""SELECT data FROM stocks WHERE
            json_extract(data,'$.active')=1 AND json_extract(data,'$.instrument')='stock'
            AND json_extract(data,'$.main_listing')=1
            AND (COALESCE(json_extract(data,'$.statement_version'),0)<1 OR COALESCE(json_extract(data,'$.financial_currency_version'),0)<2)
            AND COALESCE(json_extract(data,'$.statement_attempted'),0)<?
            AND (json_extract(data,'$.annual_income_history.currency') IS NOT NULL
                OR json_extract(data,'$.financial_fetched') IS NOT NULL)
            """ + model.COLLECTION_SCOPE + """ ORDER BY json_extract(data,'$.market_cap') DESC""",(time.time()-6*3600,))]
    # Seed familiar companies, then keep each market represented.
    priority = {'AAPL','MSFT','NVDA','GOOG','KO','PEP','JNJ','PG','O','JPM','HSBA.L','RY.TO','7203.T','005930.KS','2330.TW','241560.KS'}
    first = [r for r in candidates if r['symbol'] in priority]
    buckets = {code:[r for r in candidates if r['region_code']==code and r['symbol'] not in priority] for code in model.REGIONS}
    queue = first + [buckets[code][i] for i in range(max((len(v) for v in buckets.values()),default=0)) for code in model.REGIONS if i<len(buckets[code])]
    for row in queue[:limit]:
        if time.monotonic() >= deadline:
            break
        currency = row.get('annual_income_history',{}).get('currency') or (row.get('financial_currency') if row.get('financial_fetched') else None)
        if not currency:
            continue
        counts['attempted'] += 1
        store.upsert_many([dict(symbol=row['symbol'],region_code=row['region_code'],statement_attempted=time.time())])
        from currency_validation import verify_currencies, verified_totals
        ticker = model.yf.Ticker(row['symbol'])
        _, frames = capture_statements(ticker,currency,row)
        try:
            currencies = verify_currencies(ticker,frames)
            values, _ = capture_statements(ticker,currencies.get('income_annual'),row,frames,currencies)
            values.update(verified_totals(frames,currencies,model.financial_values,row))
            values.update(symbol=row['symbol'],region_code=row['region_code'])
            if currencies.get('income_annual'):
                values.update(model.annual_growth_values(frames['income_annual'],currencies['income_annual'],row))
        except Exception as exc:
            counts['failed'] += 1
            logging.warning('Statement currency verification %s: %s',row['symbol'],exc)
            break
        store.upsert_many([values])
        counts['succeeded' if values['statement_version']==1 else 'failed'] += 1
        print(json.dumps(dict(statement_progress=counts)),flush=True)
        # yfinance can convert a rate-limited timeseries request to an empty frame.
        # Do not hammer further requests after a failed statement set.
        if values['statement_errors']:
            break
    result = dict(statements=counts,finished=model.now_iso())
    store.set_meta('statement_backfill',result)
    print(json.dumps(result),flush=True)
    return result


def backfill_technicals(store,seconds=180,limit=120):
    from technicals import technical_values
    deadline=time.monotonic()+seconds
    store.classify_listings(force=True)
    with store.connect() as conn:
        rows=[json.loads(r[0]) for r in conn.execute("""SELECT data FROM stocks WHERE
            json_extract(data,'$.active')=1 AND json_extract(data,'$.main_listing')=1
            """ + model.COLLECTION_SCOPE + """ ORDER BY json_extract(data,'$.market_cap') DESC""")]
    from market_sessions import SessionCutoff
    cutoffs = {}
    def due(row):
        venue = (row.get('exchange'), row['region_code'])
        if venue not in cutoffs:
            cutoffs[venue] = SessionCutoff(metadata={'exchange':venue[0]}, region=venue[1]).latest_session()
        session_date, session_ready = cutoffs[venue]
        upgrade = (row.get('williams_version') or 0) < 2
        stale = (row.get('technical_fetched') or '') < model.datetime.fromtimestamp(time.time()-86400,model.timezone.utc).isoformat()
        new_session = session_date and (row.get('technical_asof') or '') < session_date
        ready = upgrade or row.get('technical_attempted',0) < time.time()-6*3600 or (session_ready is not None and row.get('technical_attempted',0) < session_ready.timestamp())
        return (upgrade or stale or new_session) and ready
    rows = [row for row in rows if due(row)]
    priority={'AAPL','MSFT','NVDA','GOOG','KO','PEP','JNJ','PG','O','JPM','HSBA.L','RY.TO','7203.T','005930.KS','2330.TW','241560.KS'}
    queue=[r for r in rows if r['symbol'] in priority]
    buckets={c:[r for r in rows if r['region_code']==c and r['symbol'] not in priority] for c in model.REGIONS}
    balanced=[buckets[c][i] for i in range(max((len(v) for v in buckets.values()),default=0)) for c in model.REGIONS if i<len(buckets[c])]
    from collections import deque
    missing=deque(r for r in balanced if not r.get('williams_version'))
    stale=deque(r for r in balanced if r.get('williams_version'))
    # Reserve three slots for first fetches and one for refreshing cached histories.
    # Otherwise the largest stale stocks can consume every daily batch indefinitely.
    step=0
    while missing or stale:
        chosen=stale if step%4==3 and stale else missing if missing else stale
        queue.append(chosen.popleft());step+=1
    counts=dict(attempted=0,succeeded=0,failed=0,williams_available=0)
    for row in queue[:limit]:
        if time.monotonic()>deadline: break
        counts['attempted']+=1
        store.upsert_many([dict(symbol=row['symbol'],region_code=row['region_code'],technical_attempted=time.time())])
        try:
            ticker=model.yf.Ticker(row['symbol'])
            history=ticker.history(start=model.date.fromtimestamp(time.time()-7*366*86400).isoformat(),auto_adjust=False,actions=False,raise_errors=True,timeout=15)
            metadata=ticker.get_history_metadata()
            values=technical_values(history,metadata.get('currency'),metadata=metadata,region=row['region_code'])
            counts['williams_available'] += int(values['williams_r'] is not None)
            values.update(symbol=row['symbol'],region_code=row['region_code'])
            store.upsert_many([values])
            counts['succeeded']+=1
        except Exception as exc:
            counts['failed']+=1
            logging.warning('Price history %s: %s',row['symbol'],exc)
            if '429' in str(exc) or 'rate' in str(exc).lower(): break
        if counts['attempted']%10==0: print(json.dumps(dict(technical_progress=counts)),flush=True)
    result=dict(technicals=counts,finished=model.now_iso())
    store.set_meta('technical_backfill',result)
    print(json.dumps(result),flush=True)
    return result



def bootstrap_europe(store, seconds=300, limit=600):
    if not store.meta('europe_bootstrap_pending', False):
        print(json.dumps({'europe_bootstrap': 'Already imported'}), flush=True)
        return
    started = time.monotonic()
    profiles, rate_limited = [], False
    pipeline = model.Pipeline(store)
    for symbol in ['SAP.DE','ITX.MC','ENI.MI','ASML.AS','NOVO-B.CO','VOLV-B.ST']:
        if time.monotonic()-started >= seconds:
            break
        row = store.get(symbol)
        if not row or row.get('financial_fetched'):
            continue
        try:
            pipeline.enrich(symbol)
            profiles.append(symbol)
        except Exception as error:
            store.failed_enrichment(symbol, error)
            if '429' in str(error) or 'rate' in str(error).lower():
                rate_limited = True
                break
    remaining = seconds-(time.monotonic()-started)
    result = backfill_growth(store, seconds=remaining, limit=limit, workers=4, regions=sorted(model.EUROPE_REGIONS)) if remaining>0 and not rate_limited else {'source_pending':True}
    store.set_meta('europe_bootstrap_pending', False)
    print(json.dumps({'europe_bootstrap':result, 'profiles':profiles}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=model.DB)
    parser.add_argument("--seed", type=Path, default=model.ROOT / "seed" / "bootstrap.tar.gz")
    parser.add_argument("--seconds", type=int, default=720)
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--force-quotes", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--published-fallback", action="store_true")
    parser.add_argument("--growth-only", action="store_true")
    parser.add_argument("--europe-only", action="store_true")
    parser.add_argument("--statements-only", action="store_true")
    parser.add_argument("--technicals-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = model.Store(args.database)
    store.set_meta('collection_min_market_cap', 20_000_000_000)
    if args.seed_only:
        restored=restore_published_seed(store) if args.published_fallback else False
        print(json.dumps(dict(seed_restored=restore_seed(store, args.seed) or restored)))
    elif args.europe_only:
        restore_seed(store,args.seed)
        bootstrap_europe(store,max(1,args.seconds),max(0,args.limit))
    elif args.technicals_only:
        restore_seed(store,args.seed)
        backfill_technicals(store,max(1,args.seconds),max(0,args.limit))
    elif args.statements_only:
        restore_seed(store, args.seed)
        backfill_statements(store,max(1,args.seconds),max(0,args.limit))
    elif args.growth_only:
        restore_seed(store, args.seed)
        backfill_growth(store, max(1, args.seconds), max(0, args.limit), args.workers)
    else:
        refresh(store, args.seed, max(1, args.seconds), max(0, args.limit), args.force_quotes, args.workers)


if __name__ == "__main__":
    main()
