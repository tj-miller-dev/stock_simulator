"""SSE over real HTTP: headers, event framing, and clean client disconnect."""

import httpx


def test_sse_demo_stream_over_http(live_base_url):
    with httpx.Client(timeout=10) as client:
        with client.stream("GET", live_base_url + "/v1/stream?symbols=CUCKOO,CRASH") as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            assert r.headers["x-cuckoo-synthetic"] == "true"
            lines = []
            for line in r.iter_lines():
                lines.append(line)
                if len(lines) >= 7:
                    break
    assert lines[0] == "event: hello"
    assert '"clock":"demo"' in lines[1]
    ticks = [line for line in lines if line == "event: tick"]
    assert len(ticks) >= 2  # one per symbol, immediately on connect


def test_sse_identical_across_connections(live_base_url):
    """Two clients see the same price for the same second -- the demo clock
    is a pure function of wall time, which is the whole stateless trick."""

    def first_tick():
        with httpx.Client(timeout=10) as client:
            with client.stream("GET", live_base_url + "/v1/stream?symbols=CUCKOO") as r:
                for line in r.iter_lines():
                    if line.startswith("data") and '"p":' in line:
                        return line
        return None

    import json

    a, b = first_tick(), first_tick()
    pa = json.loads(a.removeprefix("data: "))
    pb = json.loads(b.removeprefix("data: "))
    # Same second -> identical price; consecutive seconds -> near-identical.
    if pa["t"] == pb["t"]:
        assert pa["p"] == pb["p"]
    else:
        assert abs(pa["p"] - pb["p"]) / pa["p"] < 0.01
