"""Polygon-compatible surface, checked against live api.polygon.io response
captures (Aug 2026): envelope key order, bar key order, ms timestamps, empty
windows omitting results/count, prev's "T" field, cursor pagination, and the
{"status": "ERROR", ...} error shape."""

from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi.testclient import TestClient

import api as api_module
from api import app

client = TestClient(app)

PG = "/api/v1/polygon"
JULY = f"{PG}/v2/aggs/ticker/MSFT/range/1/day/2026-07-01/2026-08-01"


def setup_function():
    api_module.limiter.reset()


def test_aggs_envelope_matches_polygon():
    r = client.get(f"{JULY}?adjusted=true&sort=asc&apiKey=anything")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == [
        "ticker", "queryCount", "resultsCount", "adjusted",
        "results", "status", "request_id", "count",
    ]
    assert body["ticker"] == "MSFT"
    assert body["status"] == "OK"
    assert body["adjusted"] is True
    assert body["queryCount"] == body["resultsCount"] == body["count"] == len(body["results"])
    bar = body["results"][0]
    assert list(bar) == ["v", "vw", "o", "c", "h", "l", "t", "n"]
    # Daily t is midnight ET expressed in Unix ms (04:00 or 05:00 UTC).
    assert isinstance(bar["t"], int)
    assert bar["t"] % 86_400_000 in (4 * 3_600_000, 5 * 3_600_000)
    stamps = [b["t"] for b in body["results"]]
    assert stamps == sorted(stamps)


def test_aggs_determinism_including_request_id():
    a, b = client.get(JULY), client.get(JULY)
    assert a.json() == b.json()  # request_id included: identical bytes, forever
    assert a.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_aggs_matches_alpaca_surface():
    """One world: the same symbol and day must serve identical OHLCV through
    every provider surface."""
    poly = {b["t"]: b for b in client.get(JULY).json()["results"]}
    alpaca = client.get(
        "/api/v1/alpaca/v2/stocks/MSFT/bars?timeframe=1Day&start=2026-07-01&end=2026-08-01"
    ).json()["bars"]
    assert len(alpaca) == len(poly)
    for bar in alpaca:
        ms = int(datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).timestamp() * 1000)
        assert poly[ms]["o"] == bar["o"] and poly[ms]["c"] == bar["c"]
        assert poly[ms]["v"] == bar["v"] and poly[ms]["n"] == bar["n"]


def test_pagination_via_next_url():
    first = client.get(f"{JULY}?limit=5").json()
    assert first["count"] == 5
    assert first["next_url"].startswith("https://cuckootrade.com/api/v1/polygon/")
    parsed = urlparse(first["next_url"])
    second = client.get(f"{parsed.path}?{parsed.query}").json()
    combined = first["results"] + second["results"]
    full = client.get(JULY).json()["results"]
    assert combined == full[: len(combined)]  # seamless, no duplicates or gaps


def test_ms_timestamps_accepted_as_bounds():
    full = client.get(JULY).json()["results"]
    frm, to = full[0]["t"], full[-1]["t"]
    by_ms = client.get(
        f"{PG}/v2/aggs/ticker/MSFT/range/1/day/{frm}/{to}"
    ).json()["results"]
    assert by_ms == full


def test_empty_window_omits_results_and_count():
    # 2026-01-03/04 is a weekend: no sessions, exactly like live polygon.
    body = client.get(f"{PG}/v2/aggs/ticker/MSFT/range/1/day/2026-01-03/2026-01-04").json()
    assert body["queryCount"] == 0 and body["resultsCount"] == 0
    assert "results" not in body and "count" not in body
    assert body["status"] == "OK"


def test_prev_close_carries_ticker_field():
    body = client.get(f"{PG}/v2/aggs/ticker/AAPL/prev").json()
    assert body["count"] == 1
    bar = body["results"][0]
    assert list(bar) == ["T", "v", "vw", "o", "c", "h", "l", "t", "n"]
    assert bar["T"] == "AAPL"


def test_timespan_grammar():
    for timespan, mult in (("minute", 15), ("hour", 1), ("week", 1), ("month", 1), ("quarter", 1), ("year", 1)):
        r = client.get(f"{PG}/v2/aggs/ticker/AAPL/range/{mult}/{timespan}/2024-01-01/2026-08-01?limit=10")
        assert r.status_code == 200, timespan
        assert r.json()["status"] == "OK", timespan


def test_errors_are_polygon_shaped():
    r = client.get(f"{PG}/v2/aggs/ticker/MSFT/range/1/fortnight/2026-07-01/2026-08-01")
    assert r.status_code == 400
    body = r.json()
    assert list(body) == ["status", "request_id", "error"]
    assert body["status"] == "ERROR"
    # Polygon's own message for an unknown timespan, verbatim.
    assert body["error"] == (
        "Invalid time span. The only supported resolutions are "
        "minute|hour|day|week|month|quarter|year"
    )

    cases = [
        f"{PG}/v2/aggs/ticker/MSFT/range/99/day/2026-07-01/2026-08-01",   # bad multiplier
        f"{PG}/v2/aggs/ticker/MSFT/range/1/day/2026-08-01/2026-07-01",    # from > to
        f"{PG}/v2/aggs/ticker/MSFT/range/1/day/notadate/2026-08-01",      # bad bound
        f"{PG}/v2/aggs/ticker/MSFT/range/1/day/2026-07-01/2026-08-01?limit=999999",
        f"{PG}/v2/aggs/ticker/MSFT/range/1/day/2026-07-01/2026-08-01?generation=99",
        f"{PG}/v2/aggs/ticker/!!!/range/1/day/2026-07-01/2026-08-01",     # bad ticker
    ]
    for url in cases:
        r = client.get(url)
        assert r.status_code == 400, url
        assert r.json()["status"] == "ERROR", url


def test_scenario_ticker_and_seed():
    crash = client.get(f"{PG}/v2/aggs/ticker/CRASH/range/1/day/2026-07-01/2026-08-01").json()
    closes = [b["c"] for b in crash["results"]]
    assert min(closes) / max(closes) < 0.82
    seeded = client.get(f"{JULY}?seed=alt").json()
    canonical = client.get(JULY).json()
    assert seeded["results"] != canonical["results"]
