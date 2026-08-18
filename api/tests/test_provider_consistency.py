"""Cross-provider coherence: every provider surface reshapes the same
deterministic world, so the same (symbol, day) must carry identical OHLCV
through Alpaca, Alpha Vantage, and Polygon wire formats."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

import api as api_module
from api import app

client = TestClient(app)


def setup_function():
    api_module.limiter.reset()


def test_same_day_identical_across_all_surfaces():
    # Window semantics differ by provider, faithfully: Alpaca's `end` is a
    # timestamp (midnight cuts the last day off), Polygon's `to` date is
    # inclusive. Aligning them: alpaca end=...T23:59:59Z ~ polygon to=...-31.
    alpaca = client.get(
        "/api/v1/alpaca/v2/stocks/AAPL/bars"
        "?timeframe=1Day&start=2026-07-01&end=2026-07-31T23:59:59Z"
    ).json()["bars"]
    by_date_alpaca = {bar["t"][:10]: bar for bar in alpaca}

    av = client.get(
        "/api/v1/alphavantage/query?function=TIME_SERIES_DAILY&symbol=AAPL"
    ).json()["Time Series (Daily)"]

    poly = client.get(
        "/api/v1/polygon/v2/aggs/ticker/AAPL/range/1/day/2026-07-01/2026-07-31"
    ).json()["results"]
    by_date_poly = {
        datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"): bar
        for bar in poly
    }

    assert by_date_alpaca, "window unexpectedly empty"
    assert set(by_date_alpaca) == set(by_date_poly)
    for day, bar in by_date_alpaca.items():
        assert day in av, f"{day} missing from the Alpha Vantage series"
        av_bar, pg_bar = av[day], by_date_poly[day]
        assert float(av_bar["1. open"]) == bar["o"] == pg_bar["o"]
        assert float(av_bar["4. close"]) == bar["c"] == pg_bar["c"]
        assert float(av_bar["2. high"]) == bar["h"] == pg_bar["h"]
        assert float(av_bar["3. low"]) == bar["l"] == pg_bar["l"]
        assert int(av_bar["5. volume"]) == bar["v"] == pg_bar["v"]


def test_index_lists_all_providers():
    providers = client.get("/api").json()["providers"]
    assert set(providers) >= {"alpaca", "alphavantage", "polygon"}
    for name, entry in providers.items():
        assert entry["status"] == "available"
        assert entry["base_url"] == f"https://cuckootrade.com/api/v1/{name}"
        assert entry["endpoints"], name
