from fastapi.testclient import TestClient

import api as api_module
from api import app

client = TestClient(app)


def setup_function():
    api_module.limiter.reset()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_index_is_self_describing():
    r = client.get("/api")
    body = r.json()
    assert body["synthetic"] is True
    assert body["api_version"] == 1
    assert body["generation"] == 1
    alpaca = body["providers"]["alpaca"]
    assert alpaca["base_url"].endswith("/api/v1/alpaca")
    assert any(
        e["path"] == "/api/v1/alpaca/v2/stocks/bars" for e in alpaca["endpoints"]
    )
    assert any(e["path"] == "/api/v1/stream" for e in body["native_endpoints"])
    assert "CRASH" in body["magic_tickers"]


def test_synthetic_headers_everywhere():
    for path in ("/api", "/api/health", "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL"):
        r = client.get(path)
        assert r.headers["X-Cuckoo-Synthetic"] == "true"
        assert r.headers["X-Cuckoo-Generation"] == "1"


def test_bars_shape_and_determinism():
    url = "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL,CRASH&start=2026-07-01&end=2026-07-15"
    a, b = client.get(url), client.get(url)
    assert a.status_code == 200
    assert a.json() == b.json()
    bars = a.json()["bars"]
    assert set(bars) == {"AAPL", "CRASH"}
    assert set(bars["AAPL"][0]) == {"c", "h", "l", "n", "o", "t", "v", "vw"}
    assert a.json()["next_page_token"] is None


def test_pagination_walk():
    base = "/api/v1/alpaca/v2/stocks/bars?symbols=MSFT,SPY&timeframe=1Day&start=2026-06-01&end=2026-07-31&limit=15"
    all_bars = []
    token, pages = None, 0
    while True:
        url = base + (f"&page_token={token}" if token else "")
        body = client.get(url).json()
        for symbol in sorted(body["bars"]):
            for bar in body["bars"][symbol]:
                all_bars.append((symbol, bar["t"]))
        token = body["next_page_token"]
        pages += 1
        if token is None:
            break
    assert pages > 2
    assert len(all_bars) == len(set(all_bars))  # no duplicates across pages
    full = client.get(
        "/api/v1/alpaca/v2/stocks/bars?symbols=MSFT,SPY&timeframe=1Day&start=2026-06-01&end=2026-07-31&limit=10000"
    ).json()["bars"]
    expected = [(s, b["t"]) for s in sorted(full) for b in full[s]]
    assert all_bars == expected  # pagination loses and reorders nothing


def test_sort_desc():
    body = client.get(
        "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&start=2026-07-01&end=2026-07-15&sort=desc"
    ).json()
    ts = [b["t"] for b in body["bars"]["AAPL"]]
    assert ts == sorted(ts, reverse=True)


def test_single_symbol_route():
    body = client.get("/api/v1/alpaca/v2/stocks/AAPL/bars?start=2026-07-01&end=2026-07-15").json()
    assert body["symbol"] == "AAPL"
    assert isinstance(body["bars"], list) and body["bars"]


def test_latest():
    body = client.get("/api/v1/alpaca/v2/stocks/bars/latest?symbols=AAPL,FLAT").json()
    assert set(body["bars"]) == {"AAPL", "FLAT"}
    assert body["bars"]["FLAT"]["o"] == 100.0


def test_errors_teach():
    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=2Day")
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == 40010001
    assert "1Day" in body["message"] and "example" in body["message"]

    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=" + ",".join(f"S{i}" for i in range(51)))
    assert r.status_code == 400 and "50" in r.json()["message"]

    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&generation=99")
    assert r.status_code == 400 and "generation" in r.json()["message"]

    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&limit=99999")
    assert r.status_code == 400 and "10000" in r.json()["message"]

    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&page_token=garbage")
    assert r.status_code == 400 and "page_token" in r.json()["message"]


def test_ignored_alpaca_params_accepted():
    r = client.get(
        "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&adjustment=raw&feed=iex&asof=2026-07-01&currency=USD"
    )
    assert r.status_code == 200


def test_immutable_cache_header_on_closed_history():
    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&start=2026-06-01&end=2026-06-30")
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    r = client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL")  # open-ended window
    assert "immutable" not in r.headers.get("Cache-Control", "")


def test_rate_limit_and_headers():
    r = client.get("/api")
    assert int(r.headers["RateLimit-Limit"]) == 120
    api_module.limiter.capacity = 5.0
    api_module.limiter.reset()
    try:
        codes = [client.get("/api/health").status_code for _ in range(3)]
        assert all(c == 200 for c in codes)  # health is exempt
        codes = [client.get("/api").status_code for _ in range(8)]
        assert 429 in codes
        blocked = client.get("/api")
        assert int(blocked.headers["RateLimit-Reset"]) >= 1
        assert blocked.json()["code"] == 42910000
    finally:
        api_module.limiter.capacity = 120.0
        api_module.limiter.reset()


def test_removed_debug_endpoints_are_gone():
    for path in ("/api/hello", "/api/random", "/api/somethingspecial", "/api/randomlist"):
        assert client.get(path).status_code == 404


def test_pre_versioning_paths_are_gone():
    # Provider surfaces live at /api/v1/{provider}/...; the unversioned,
    # provider-less spelling never shipped and must not resolve.
    assert client.get("/api/v2/stocks/bars?symbols=AAPL").status_code == 404


def test_sse_demo_events_generator():
    # The stream logic as a unit; wire-level SSE is covered over real HTTP in
    # test_stream_http.py (TestClient can't close an infinite stream cleanly).
    import asyncio

    from stream import _demo_events

    class NeverDisconnects:
        async def is_disconnected(self):
            return False

    async def first_two():
        gen = _demo_events(["CUCKOO"], "", NeverDisconnects())
        try:
            return [await anext(gen), await anext(gen)]
        finally:
            await gen.aclose()

    hello, tick = asyncio.run(first_two())
    assert hello.startswith("event: hello")
    assert '"synthetic":true' in hello
    assert tick.startswith("event: tick")
    assert '"S":"CUCKOO"' in tick


def test_sse_rejects_bad_clock():
    r = client.get("/api/v1/stream?symbols=CUCKOO&clock=warped")
    assert r.status_code == 400 and "clock=demo" in r.json()["message"]
