"""Tests for Transatlantic 50 constituents, data publishing and quadrant settings."""
from pathlib import Path
import json
import tempfile
import transatlantic

ROOT = Path(__file__).resolve().parent.parent


def test_holdings_count_and_composition():
    holdings = transatlantic.load_holdings()
    assert len(holdings) == 50, f"Expected 50 holdings, got {len(holdings)}"

    us_stocks = [h for h in holdings if h["region_code"] == "us"]
    eu_stocks = [h for h in holdings if h["region_code"] != "us"]
    assert len(us_stocks) == 25, f"Expected 25 US stocks, got {len(us_stocks)}"
    assert len(eu_stocks) == 25, f"Expected 25 EU stocks, got {len(eu_stocks)}"

    # Check key metrics on all 50 stocks
    for h in holdings:
        assert "symbol" in h and h["symbol"], "Missing symbol"
        assert "name" in h and h["name"], f"Missing name for {h['symbol']}"
        assert "rank" in h and 1 <= h["rank"] <= 50, f"Invalid rank for {h['symbol']}"
        assert "weight" in h and h["weight"] > 0, f"Invalid weight for {h['symbol']}"
        assert "div_yield" in h and h["div_yield"] is not None, f"Missing div_yield for {h['symbol']}"
        assert "net_income" in h and h["net_income"] is not None, f"Missing net_income for {h['symbol']}"
        assert "williams_r" in h and h["williams_r"] is not None, f"Missing williams_r for {h['symbol']}"
        assert "williams_monthly_r" in h and h["williams_monthly_r"] is not None, f"Missing williams_monthly_r for {h['symbol']}"
        assert "roe" in h and h["roe"] is not None, f"Missing roe for {h['symbol']}"


def test_transatlantic_publish():
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir)
        status = transatlantic.publish(output, fx={"EUR": {"rate": 1.08}}, built="2026-10-30T16:00:00Z", compress=False)
        assert status["total"] == 50
        assert "weekly_oversold" in status
        assert "monthly_oversold" in status

        schema_file = output / "schema.json"
        assert schema_file.is_file()
        schema = json.loads(schema_file.read_text())
        assert schema["universe"] == "transatlantic"

        stocks_file = output / "stocks.json"
        assert stocks_file.is_file()
        stocks = json.loads(stocks_file.read_text())
        assert len(stocks["rows"]) == 50

        status_file = output / "status.json"
        assert status_file.is_file()

        technicals_file = output / "technicals.json"
        assert technicals_file.is_file()

        details_dir = output / "details"
        assert details_dir.is_dir()
        detail_files = list(details_dir.glob("*.json"))
        assert len(detail_files) == 50
