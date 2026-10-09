"""Build the standalone GitHub Pages snapshot and optional public-data seed."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path

import app as model

EXTRA_FIELDS = {
    "symbol", "name", "region_code", "instrument", "active", "main_listing", "listing_reason",
    "website", "domicile", "description", "source", "quote_fetched", "quote_timestamp", "market_state",
    "financial_error", "dividend_error", "dividend_fetched", "dividend_currency", "dividend_events",
    "dividend_years", "dividend_history_start", "dividend_end_year", "universe_run",
    "price_local", "market_cap_local", "low_52w_local", "high_52w_local",
    "annual_income_history", "annual_growth_version", "annual_growth_fetched", "annual_growth_missing",
    "technical_history", "technical_currency", "technical_version", "technical_fetched", "technical_basis", "technical_price_local", "sma_200d_local", "sma_200w_local", "annual_growth_status", "income_fetched", "financial_quality_note", "financial_currency_version", "financial_field_currencies",
    "statement_history", "statement_errors", "statement_version", "statement_fetched", "roic_proxy_inputs", "roic_proxy_reason", "roce_inputs", "roce_reason",
    "capital_returns_history", "capital_returns_version", "roic_proxy_5y_avg_reason", "roce_5y_avg_reason",
    "eps_diluted_local", "eps_reason", "eps_version",
    "williams_version", "williams_reason", "williams_provisional", "williams_high_local", "williams_low_local", "williams_close_local", "williams_oversold_price_local", "williams_overbought_price_local",
    *(key + "_local" for key in model.MONETARY_FINANCIAL),
}
PUBLIC_FIELDS = EXTRA_FIELDS | set(model.FIELDS)
INDEX_FIELDS = list(dict.fromkeys([
    "symbol", "name", "region_code", "instrument", "active", "main_listing", "listing_reason",
    "financial_error", "financial_quality_note", "technical_version", "williams_version", "williams_reason", "williams_provisional", "detail_key", "logo_url", "annual_growth_missing", "annual_growth_version", "income_fetched", "dividend_fetched", "statement_version", "eps_version", *[field["key"] for field in model.COLUMNS],
]))
META_KEYS = ["coverage", "fx", "quote_completed", "last_quote_run", "quote_error", "last_financial", "refresh_health", "annual_growth_backfill", "statement_backfill", "cloud_refresh", "profile_seed", "technical_backfill"]
ICON_FILE = re.compile(r"[a-f0-9]{64}\.(png|jpg|gif|webp|ico)")


def public_row(row):
    out = {key: value for key, value in row.items() if key in PUBLIC_FIELDS}
    for key in ["financial_error", "dividend_error"]:
        if out.get(key):
            out[key] = "Yahoo source unavailable. Cached values retained where available."
    return out


def clean_metadata(data):
    if isinstance(data, dict):
        return {key: "Yahoo refresh failed. Cached data retained." if key in {"error", "quote_error"} and value else clean_metadata(value) for key, value in data.items()}
    if isinstance(data, list):
        return [clean_metadata(value) for value in data]
    return data


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def write_data_json(path, data, compressed=False):
    if not compressed:
        write_json(path, data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    path.with_suffix('.json.gz').write_bytes(gzip.compress(content, mtime=0))


def snapshot_status(rows, metadata, icon_count, built):
    counts = []
    for code in model.REGIONS:
        regional = [r for r in rows if r.get("active") and r["region_code"] == code]
        counts.append(dict(region=code, total=len(regional), stocks=sum(r.get("instrument") == "stock" for r in regional),
            enriched=sum(bool(r.get("financial_fetched")) for r in regional),
            main_stocks=sum(bool(r.get("main_listing")) for r in regional),
            main_enriched=sum(bool(r.get("main_listing") and r.get("financial_fetched")) for r in regional),
            fcf=sum(r.get("fcf") is not None for r in regional)))
    main = [r for r in rows if r.get('active') and r.get('instrument') == 'stock' and r.get('main_listing')]
    capital_coverage = dict(main_listings=len(main), method='Calculated from matched annual Yahoo statements, not issuer-reported ROIC or ROCE.', metrics={})
    for key in model.CAPITAL_KEYS:
        available = sum(model.number(r.get(key)) is not None for r in main)
        pending = sum(model.number(r.get(key)) is None and not r.get('statement_version') for r in main)
        capital_coverage['metrics'][key] = dict(available=available, not_fetched=pending, unavailable=len(main)-available-pending)
    return dict(capital_returns=capital_coverage, counts=counts, coverage=metadata.get("coverage", {}), fx=metadata.get("fx", {}),
        refreshing=False, completed=metadata.get("quote_completed"), error=metadata.get("quote_error"),
        last_financial=metadata.get("last_financial"), refresh_health=metadata.get("refresh_health"),
        annual_growth_backfill=metadata.get("annual_growth_backfill"), statement_backfill=metadata.get("statement_backfill"),
        cloud_refresh=metadata.get("cloud_refresh"), profile_seed=metadata.get("profile_seed"), technical_backfill=metadata.get("technical_backfill"),
        missing_fx=sum(r.get("market_cap_local") is not None and r.get("market_cap") is None for r in rows),
        logos=dict(cached=icon_count, queued=0, downloading=0), snapshot=dict(built=built, version=1,
            cadence="Nightly at 01:23 UK time. Quotes daily, company profiles on a seven-day cache.",
            actions_url="https://github.com/soylee22/atlas-stock-screener/actions/workflows/pages.yml"))


def make_seed(destination, rows, metadata, assets, icon_root):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(version=1, rows=rows, metadata=metadata, logos=assets)
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    with tarfile.open(destination, "w:gz") as archive:
        member = tarfile.TarInfo("data.json")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
        for asset in assets:
            filename = asset["filename"]
            path = icon_root / filename
            if path.is_file() and not path.is_symlink():
                member = tarfile.TarInfo("logos/" + filename)
                member.size = path.stat().st_size
                with path.open("rb") as source:
                    archive.addfile(member, source)


def build_site(database, output, seed=None, compress_details=False, compress_data=False, minimum_market_cap=0):
    store = model.Store(database)
    # Recompute quote-derived values when upgrading a cached snapshot.
    store.recalibrate_fx()
    store.classify_listings(force=True)
    icon_root = store.path.parent / "logos"
    with store.connect() as conn:
        rows = [public_row(json.loads(r[0])) for r in conn.execute("SELECT data FROM stocks ORDER BY symbol")]
        assets = [dict(r) for r in conn.execute("SELECT * FROM logo_assets WHERE filename IS NOT NULL")]
    assets = [a for a in assets if ICON_FILE.fullmatch(a["filename"]) and (icon_root / a["filename"]).is_file() and not (icon_root / a["filename"]).is_symlink()]
    metadata = clean_metadata({key: store.meta(key) for key in META_KEYS})
    active = [r for r in rows if r.get("active")]
    if not active or not all(any(r["region_code"] == code for r in active) for code in model.REGIONS):
        raise ValueError("Publish only a snapshot containing every configured market")
    # Keep every source row in the recovery seed, but only publish the large-cap scope.
    active = [r for r in active if (r.get('market_cap') or 0) >= minimum_market_cap]
    output = Path(output).resolve()
    protected = [model.ROOT.resolve(), store.path.parent.resolve()]
    if any(output == path or output in path.parents for path in protected):
        raise ValueError("Output must not contain the source or cache directory")
    if output.exists() and any(output.iterdir()) and not (output / ".atlas-generated").exists():
        # Permit upgrading earlier generated snapshots, identified by their mode marker.
        index = output / "index.html"
        if not index.is_file() or 'name="atlas-data-mode" content="snapshot"' not in index.read_text():
            raise ValueError("Output is not an Atlas generated directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / ".atlas-generated").write_text("Atlas generated site\n")
    # Only clear this builder's generated directories, leaving unrelated files alone.
    for name in ["static", "data", "logos"]:
        target = output / name
        if target.exists():
            shutil.rmtree(target)
    shutil.copytree(model.ROOT / "static", output / "static")
    # Version the entire module graph, including relative imports, to avoid mixed cached code.
    source_files = sorted(p for p in (model.ROOT / "static").iterdir() if p.is_file())
    version = hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in source_files)).hexdigest()[:16]
    asset_cache = store.path.parent / "site-assets"
    current = asset_cache / version
    if not current.exists():
        shutil.copytree(model.ROOT / "static", current)
    previous = sorted((p for p in asset_cache.iterdir() if p.is_dir() and re.fullmatch(r"[a-f0-9]{16}", p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
    # Retain previous bundles while an edge cache can still serve an older index page.
    retained = [current, *[bundle for bundle in previous if bundle != current][:2]]
    for bundle in retained:
        destination = output / "static" / bundle.name
        destination.mkdir()
        for source_file in source_files:
            old_file = bundle / source_file.name
            if old_file.is_file() and not old_file.is_symlink():
                shutil.copyfile(old_file, destination / source_file.name)
    for bundle in previous:
        if bundle not in retained:
            shutil.rmtree(bundle)
    shutil.copyfile(model.ROOT / "static" / "export-worker.js", output / "export-worker.js")
    html = (model.ROOT / "static" / "index.html").read_text()
    html = html.replace("<head>", '<head>\n<meta name="atlas-data-mode" content="snapshot">')
    html = html.replace('href="/', 'href="./').replace('src="/', 'src="./')
    html = html.replace('./static/', f'./static/{version}/')
    html = html.replace("while the local service runs", "on GitHub")
    html = html.replace("Use Refresh data for a new universe scan.", "Use Refresh data to load the latest published snapshot.")
    html = html.replace("Financials load in a rolling queue, prioritising visible rows, and are cached for seven days.", "GitHub collects data nightly from 01:23 UK time. Initial collection can take several nights. Company profiles use a seven-day cache.")
    html = html.replace("Files are stored locally", "Files are stored with the published site")
    (output / "index.html").write_text(html)
    (output / ".nojekyll").write_text("")
    (output / "logos").mkdir()
    by_domain = {asset["domain"]: asset for asset in assets}
    for asset in assets:
        shutil.copyfile(icon_root / asset["filename"], output / "logos" / asset["filename"])
    built = model.now_iso()
    write_data_json(output / "data" / "technicals.json", dict(built=built,
        technicals={r['symbol']:r['technical_history'] for r in active if r.get('technical_history')},
        currencies={r['symbol']:r.get('technical_currency') for r in active if r.get('technical_history')}), compress_data)
    # CSV calculations use this same database and FX snapshot.
    model.store = store
    for row in active:
        asset = by_domain.get(model.company_domain(row.get("website")))
        row["logo_url"] = "logos/" + asset["filename"] if asset else None
        has_details = bool(row.get("technical_version") or row.get("financial_fetched") or row.get("statement_history") or row.get("annual_growth_version") or row.get("dividend_events") or row.get("description"))
        row["detail_key"] = hashlib.sha256(row["symbol"].encode()).hexdigest() if has_details else None
        if has_details:
            detail = dict(row)
            detail["dividend_exports"] = {mode: f"data/dividends/{row['detail_key']}-{mode}.csv" for mode in ["annual", "events"]}
            filename = output / "data" / "details" / (row["detail_key"] + ".json")
            if compress_details:
                filename.parent.mkdir(parents=True, exist_ok=True)
                filename.with_suffix('.json.gz').write_bytes(gzip.compress(
                    json.dumps(detail, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(), mtime=0))
            else:
                write_json(filename, detail)
            for mode, filename in detail["dividend_exports"].items():
                destination = output / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(model.dividend_export(row["symbol"], mode).body)
    write_json(output / "data" / "schema.json", dict(columns=model.COLUMNS, regions=model.REGIONS))
    write_data_json(output / "data" / "stocks.json", dict(version=1, built=built, fields=INDEX_FIELDS, rows=[[r.get(key) for key in INDEX_FIELDS] for r in active]), compress_data)
    status = snapshot_status(active, metadata, len(assets), built)
    status['snapshot']['minimum_market_cap'] = minimum_market_cap
    status['snapshot']['source_records'] = len(rows)
    status['snapshot']['detail_compression'] = 'gzip' if compress_details else None
    status['snapshot']['index_compression'] = status['snapshot']['technical_compression'] = 'gzip' if compress_data else None
    write_json(output / "data" / "status.json", status)
    if seed:
        # Browser-derived fields and storage are never part of the source seed.
        make_seed(seed, [public_row(r) for r in rows], metadata, assets, icon_root)
    size = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    if size > 900_000_000:
        raise ValueError(f"Snapshot exceeds the Pages publication size budget: {size:,} bytes")
    result = dict(built=built, stocks=sum(c["stocks"] for c in status["counts"]), main_listings=sum(c["main_stocks"] for c in status["counts"]),
        detail_files=sum(bool(r["detail_key"]) for r in active), icons=len(assets), bytes=size)
    print(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=model.DB)
    parser.add_argument("--output", type=Path, default=model.ROOT / "public")
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--compress-details", action="store_true")
    parser.add_argument("--compress-data", action="store_true")
    parser.add_argument("--minimum-market-cap", type=float, default=20_000_000_000)
    args = parser.parse_args()
    if args.minimum_market_cap < 0:
        parser.error("Minimum market cap must be non-negative")
    build_site(args.database, args.output, args.seed, args.compress_details, args.compress_data, args.minimum_market_cap)


if __name__ == "__main__":
    main()
