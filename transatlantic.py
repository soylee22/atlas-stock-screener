"""Transatlantic 50 portfolio constituents, data publisher, and snapshot exporter."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import app as model

ROOT = Path(__file__).resolve().parent
SEED = ROOT / "seed" / "transatlantic.json"

REGIONS = {
    "us": "United States",
    "gb": "United Kingdom",
    "de": "Germany",
    "fr": "France",
    "se": "Sweden",
    "nl": "Netherlands",
    "it": "Italy",
    "dk": "Denmark",
    "no": "Norway",
    "at": "Austria",
    "fi": "Finland",
}

PORTFOLIO_COLUMNS = [
    model.col("rank", "Rank", "integer", True, "Portfolio", "Constituent rank in the Transatlantic 50 quality dividend portfolio."),
    model.col("weight", "Weight (%)", "percent", True, "Portfolio", "Target portfolio weight percentage in the Transatlantic 50."),
    model.col("roe", "ROE (%)", "percent", True, "Financials", "Return on Equity operational efficiency."),
]

# Order columns so Rank and Weight are visible by default alongside key metrics
COLUMNS = PORTFOLIO_COLUMNS + [
    c for c in model.COLUMNS if c["key"] not in {"rank", "weight", "roe"}
]


def load_holdings():
    if not SEED.is_file():
        raise FileNotFoundError(f"Transatlantic seed file not found: {SEED}")
    data = json.loads(SEED.read_text())
    return data.get("holdings", [])


def publish(output, fx, built, compress=True):
    """Publish the Transatlantic 50 standalone snapshot into data/transatlantic/."""
    holdings = load_holdings()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)

    def write(name, value, zipped=False):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if zipped:
            path.with_suffix(".json.gz").write_bytes(gzip.compress(content, mtime=0))
        else:
            path.write_bytes(content)

    for row in holdings:
        row["detail_key"] = hashlib.sha256(row["symbol"].encode()).hexdigest()
        write("details/" + row["detail_key"] + ".json", row, compress)

    fields = [
        "symbol", "name", "country", "region_code", "region", "sector", "industry",
        "instrument", "active", "main_listing", "detail_key", "logo_url", "rank", "weight", "weight_pct", "roe",
        "net_income", "price", "market_cap", "div_yield", "div_growth", "div_years",
        "williams_r", "williams_zone", "williams_r_period", "williams_asof",
        "williams_monthly_r", "williams_monthly_zone", "williams_monthly_r_period", "williams_monthly_asof",
        *[c["key"] for c in COLUMNS],
    ]
    fields = list(dict.fromkeys(fields))

    write("schema.json", dict(universe="transatlantic", columns=COLUMNS, regions=REGIONS))
    write(
        "stocks.json",
        dict(version=1, built=built, fields=fields, rows=[[r.get(k) for k in fields] for r in holdings]),
        compress,
    )
    technicals = {r["symbol"]: r.get("technical_history") for r in holdings if r.get("technical_history")}
    currencies = {r["symbol"]: r.get("technical_currency") for r in holdings if r.get("technical_history")}
    write("technicals.json", dict(built=built, technicals=technicals, currencies=currencies), compress)

    counts = []
    for code, label in REGIONS.items():
        regional = [r for r in holdings if r.get("region_code") == code]
        counts.append(
            dict(
                region=code,
                stocks=len(regional),
                main_stocks=len(regional),
                enriched=len(regional),
                main_enriched=len(regional),
            )
        )

    oversold_weekly = sum((r.get("williams_r") or 0) <= -80 for r in holdings)
    oversold_monthly = sum((r.get("williams_monthly_r") or 0) <= -80 for r in holdings)

    status = dict(
        counts=counts,
        fx=fx,
        transatlantic=dict(
            total=len(holdings),
            weekly_oversold=oversold_weekly,
            monthly_oversold=oversold_monthly,
            cadence="Updated daily following market close.",
        ),
        snapshot=dict(
            built=built,
            index_compression="gzip" if compress else None,
            technical_compression="gzip" if compress else None,
            detail_compression="gzip" if compress else None,
            cadence="Transatlantic 50 constituent snapshot.",
        ),
    )
    write("status.json", status)
    return status["transatlantic"]
