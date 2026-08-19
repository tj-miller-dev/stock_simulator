"""scenario=: opt-in transport faults.

Two properties matter more than any individual fault. Faults must be
*reproducible*, or they make flaky tests and nobody runs them in CI. And they
must never fire without being asked for, or a keyless public API starts
serving garbage to people who merely stumbled onto it.
"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

import api as api_module
from api import app
from effects import EffectError, parse_scenario

client = TestClient(app)

BARS = "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=1Day&start=2026-07-01&end=2026-07-10"


def setup_function():
    api_module.limiter.reset()
    api_module.attempts.reset()


# --- grammar --------------------------------------------------------------


def test_parses_units_and_bare_numbers():
    assert parse_scenario("drop:20s", "stream")[0].value == 20
    assert parse_scenario("drop:20", "stream")[0].value == 20
    assert parse_scenario("slow:250ms", "http")[0].value == 250
    assert [e.name for e in parse_scenario("flap:2,slow:10", "http")] == ["flap", "slow"]


@pytest.mark.parametrize(
    "spec,surface,expect",
    [
        ("nonsense", "http", "unknown scenario effect"),
        ("drop:20s", "http", "only applies to the SSE stream"),
        ("flap:2", "stream", "only applies to the bar endpoints"),
        ("flap:0", "http", "out of range"),
        ("flap", "http", "needs a value"),
        ("truncate:3", "http", "takes no value"),
        ("flap:2,flap:3", "http", "given twice"),
        ("status:99", "http", "out of range"),
    ],
)
def test_bad_specs_explain_themselves(spec, surface, expect):
    with pytest.raises(EffectError) as exc:
        parse_scenario(spec, surface)
    assert expect in str(exc.value)


def test_data_effect_names_stay_reserved():
    """V1_SPEC section 2 promised these names for price shapes on arbitrary
    symbols. Until that exists, point at the ticker instead of stealing it."""
    with pytest.raises(EffectError) as exc:
        parse_scenario("crash", "http")
    assert "symbols=CRASH" in str(exc.value)


# --- containment: the one that protects the brand -------------------------


def test_nothing_fires_without_the_parameter():
    for path in ("/api", BARS, "/api/v1/polygon/v2/aggs/ticker/AAPL/prev",
                 "/api/v1/alphavantage/query?function=TIME_SERIES_DAILY&symbol=AAPL"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "X-Cuckoo-Scenario" not in r.headers, path
        assert r.headers.get("Cache-Control") != "no-store", path


# --- HTTP faults ----------------------------------------------------------


def test_status_uses_the_requested_code():
    r = client.get(BARS + "&scenario=status:503")
    assert r.status_code == 503
    assert r.headers["X-Cuckoo-Scenario"] == "status:503"
    assert r.headers["Cache-Control"] == "no-store"
    assert "scenario=status:503" in r.json()["message"]


def test_faults_wear_each_providers_error_shape():
    """An injected fault has to look like that provider's own failures, or it
    exercises the wrong branch of the caller's parser."""
    alpaca = client.get(BARS + "&scenario=status:503").json()
    assert set(alpaca) == {"code", "message"}

    polygon = client.get(
        "/api/v1/polygon/v2/aggs/ticker/AAPL/prev?scenario=status:503"
    ).json()
    assert polygon["status"] == "ERROR" and "request_id" in polygon

    av = client.get(
        "/api/v1/alphavantage/query?function=TIME_SERIES_DAILY&symbol=AAPL"
        "&scenario=status:503"
    ).json()
    assert "Error Message" in av


def test_bad_spec_is_a_400_in_the_providers_shape():
    r = client.get(BARS + "&scenario=nope")
    assert r.status_code == 400
    assert "unknown scenario effect" in r.json()["message"]


def test_flap_fails_exactly_n_times_then_succeeds():
    """The fault that asserts recovery rather than failure: a client with
    retry passes here, one without does not."""
    spec = BARS + "&scenario=flap:2"
    assert [client.get(spec).status_code for _ in range(4)] == [503, 503, 200, 200]


def test_flap_counts_per_request_not_globally():
    a = BARS + "&scenario=flap:1"
    b = BARS.replace("1Day", "1Hour") + "&scenario=flap:1"
    assert client.get(a).status_code == 503
    assert client.get(b).status_code == 503  # its own budget, untouched by a
    assert client.get(a).status_code == 200


def test_flap_carries_the_status_it_was_given():
    spec = BARS + "&scenario=flap:1,status:429"
    assert client.get(spec).status_code == 429
    assert client.get(spec).status_code == 200


def test_slow_actually_delays():
    started = time.perf_counter()
    r = client.get(BARS + "&scenario=slow:300")
    assert r.status_code == 200
    assert time.perf_counter() - started >= 0.3


def test_truncate_leaves_the_body_unparseable(live_base_url):
    """Real HTTP only: the fault is a Content-Length that promises more than
    the socket delivers, which TestClient does not model."""
    url = live_base_url + BARS.removeprefix("/api") + "&scenario=truncate"
    with httpx.Client(timeout=10) as http:
        with pytest.raises(httpx.HTTPError):
            http.get(url).raise_for_status()


# --- stream faults --------------------------------------------------------


def _stream(live_base_url, query, limit=40, timeout=25):
    lines = []
    with httpx.Client(timeout=timeout) as http:
        with http.stream("GET", live_base_url + "/v1/stream?" + query) as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                lines.append(line)
                if len(lines) >= limit:
                    break
    return lines


def test_stream_rejects_http_only_effects(live_base_url):
    with httpx.Client(timeout=10) as http:
        r = http.get(live_base_url + "/v1/stream?symbols=CUCKOO&scenario=flap:2")
    assert r.status_code == 400
    assert "only applies to the bar endpoints" in r.json()["message"]


def test_drop_closes_the_socket_on_schedule(live_base_url):
    """Deterministic where it counts: asked for three seconds, dies at three
    seconds, with no close event and no error frame -- just silence."""
    started = time.perf_counter()
    lines = _stream(live_base_url, "symbols=CUCKOO&scenario=drop:3", limit=10_000)
    elapsed = time.perf_counter() - started
    assert 2.5 <= elapsed < 8, f"dropped at {elapsed:.1f}s"
    assert not any("error" in line.lower() for line in lines)


def test_garbage_frames_are_invalid_json_among_valid_ones(live_base_url):
    import json

    lines = _stream(live_base_url, "symbols=CUCKOO&scenario=garbage:2", limit=30)
    payloads = [line.removeprefix("data: ") for line in lines if line.startswith("data: ")]
    broken = [p for p in payloads if _unparseable(json, p)]
    assert broken, "asked for garbage, got none"
    assert len(broken) < len(payloads), "garbage swamped the stream"


def _unparseable(json, payload):
    try:
        json.loads(payload)
        return False
    except ValueError:
        return True


def test_scenario_header_echoes_on_the_stream(live_base_url):
    with httpx.Client(timeout=10) as http:
        with http.stream(
            "GET", live_base_url + "/v1/stream?symbols=CUCKOO&scenario=garbage:1"
        ) as r:
            assert r.headers["x-cuckoo-scenario"] == "garbage:1"
