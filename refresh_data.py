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

import app as model
from build_site import ICON_FILE


def restore_seed(store, seed):
    with store.connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]:
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
    return True


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


def refresh(store, seed, seconds=720, limit=400, force_quotes=False):
    restored = restore_seed(store, seed)
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

    def worker():
        while not pipeline.stop.is_set() and time.monotonic() < deadline:
            with lock:
                if counts["attempted"] >= limit:
                    break
                counts["attempted"] += 1
            symbol = store.next_enrichment()
            if not symbol:
                break
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
                    break
            pipeline.stop.wait(.3)

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
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
        financials=counts, icons=logos.status(), finished=model.now_iso())
    store.set_meta("refresh_health", result)
    print(json.dumps(result), flush=True)
    return result


def backfill_growth(store, seconds=1200, limit=30000, workers=4):
    """Fetch annual statements across all main listings without full profiles."""
    deadline = time.monotonic() + seconds
    counts = dict(attempted=0, succeeded=0, failed=0, no_data=0)
    lock, stop = threading.Lock(), threading.Event()
    candidates = iter(store.growth_candidates())
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
                    values["annual_growth_missing"] = {key: "Yahoo returned no full annual records in a reporting currency" for key in model.GROWTH_KEYS}
                store.upsert_many([values])
                with lock:
                    counts["no_data" if income.empty else "succeeded"] += 1
            except Exception as exc:
                logging.warning("Annual statements %s: %s", symbol, exc)
                with lock:
                    counts["failed"] += 1
                if "429" in str(exc) or "rate" in str(exc).lower():
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
    result = dict(annual_growth=counts.copy(), finished=model.now_iso())
    store.set_meta("annual_growth_backfill", result)
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=model.DB)
    parser.add_argument("--seed", type=Path, default=model.ROOT / "seed" / "bootstrap.tar.gz")
    parser.add_argument("--seconds", type=int, default=720)
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--force-quotes", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--growth-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = model.Store(args.database)
    if args.seed_only:
        print(json.dumps(dict(seed_restored=restore_seed(store, args.seed))))
    elif args.growth_only:
        restore_seed(store, args.seed)
        backfill_growth(store, max(1, args.seconds), max(0, args.limit), args.workers)
    else:
        refresh(store, args.seed, max(1, args.seconds), max(0, args.limit), args.force_quotes)


if __name__ == "__main__":
    main()
