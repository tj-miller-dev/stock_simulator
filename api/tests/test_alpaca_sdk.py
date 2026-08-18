"""The wire-compat acceptance test (V1_SPEC 9.1): alpaca-py, pointed at
CuckooTrade via url_override, works unmodified. This is the definition of
done for Alpaca compatibility -- if this fails, the compatibility story is
broken no matter what the other tests say.

Runs a real uvicorn server on a loopback port because alpaca-py speaks real
HTTP; skipped automatically when alpaca-py isn't installed.
"""

import socket
import threading
import time
from datetime import datetime

import pytest

alpaca = pytest.importorskip("alpaca")

from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest  # noqa: E402
from alpaca.data.timeframe import TimeFrame  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    import uvicorn

    from api import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "test server failed to start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/api"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def sdk(base_url):
    return StockHistoricalDataClient(
        api_key="fake", secret_key="fake", url_override=base_url
    )


def test_sdk_fetches_and_parses_bars(sdk):
    request = StockBarsRequest(
        symbol_or_symbols=["AAPL", "CRASH"],
        timeframe=TimeFrame.Day,
        start=datetime(2026, 7, 1),
        end=datetime(2026, 7, 31),
    )
    barset = sdk.get_stock_bars(request)
    assert len(barset["AAPL"]) > 15
    bar = barset["AAPL"][0]
    assert bar.open > 0 and bar.low <= bar.close <= bar.high
    assert bar.volume > 0 and bar.vwap > 0
    crash_closes = [b.close for b in barset["CRASH"]]
    assert min(crash_closes) / max(crash_closes) < 0.82  # the scripted crash survives parsing


def test_sdk_follows_pagination(sdk):
    # ~1650 trading days -> more than one 1000-bar page: the SDK must follow
    # next_page_token internally and reassemble a seamless series.
    request = StockBarsRequest(
        symbol_or_symbols=["MSFT"],
        timeframe=TimeFrame.Day,
        start=datetime(2020, 1, 2),
        end=datetime(2026, 7, 31),
    )
    bars = sdk.get_stock_bars(request)["MSFT"]
    assert len(bars) > 1200
    stamps = [b.timestamp for b in bars]
    assert stamps == sorted(stamps)
    assert len(stamps) == len(set(stamps))  # nothing duplicated at page seams


def test_sdk_latest_bar(sdk):
    latest = sdk.get_stock_latest_bar(StockLatestBarRequest(symbol_or_symbols="SPY"))
    assert latest["SPY"].close > 0
