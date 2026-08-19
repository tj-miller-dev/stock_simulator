"""The access line is the only view of who is calling a keyless API, so the
things that make it useful -- real client address, the requested symbols, no
health-check flood -- are worth pinning down."""

import logging

import pytest
from fastapi.testclient import TestClient

import api as api_module
from api import app

client = TestClient(app)


def setup_function():
    api_module.limiter.reset()


class _Captured:
    """caplog.records rebuilds its list on each access, so the fixture has to
    hand back something that reads it lazily rather than a snapshot."""

    def __init__(self, caplog):
        self._caplog = caplog

    @property
    def messages(self):
        return [r.getMessage() for r in self._caplog.records]

    def __len__(self):
        return len(self._caplog.records)


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger="cuckoo.access")
    # The logger deliberately doesn't propagate (so uvicorn's startup
    # reconfiguration can't detach it), which is also what caplog hooks into --
    # so re-enable it just for the test.
    api_module.access_log.propagate = True
    yield _Captured(caplog)
    api_module.access_log.propagate = False


def test_health_checks_are_not_logged(lines):
    client.get("/api/health")
    assert lines.messages == []


def test_request_is_logged_with_status_and_query(lines):
    client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=1Day")
    assert len(lines) == 1
    message = lines.messages[0]
    assert "status=200" in message
    assert "GET /api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=1Day" in message


def test_logs_forwarded_client_address_not_the_proxy(lines):
    # The ALB appends the address it saw to any inbound X-Forwarded-For, so the
    # last entry is the trustworthy one -- a caller sending a forged chain must
    # not be able to attribute their traffic to someone else.
    client.get(
        "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL",
        headers={"x-forwarded-for": "1.1.1.1, 203.0.113.7"},
    )
    assert "ip=203.0.113.7" in lines.messages[0]


def test_rate_limited_requests_are_logged(lines):
    api_module.limiter.capacity = 1.0
    try:
        for _ in range(3):
            client.get("/api/v1/alpaca/v2/stocks/bars?symbols=AAPL")
    finally:
        api_module.limiter.capacity = 120.0
        api_module.limiter.reset()
    assert any("status=429" in m for m in lines.messages)


def test_user_agent_cannot_forge_log_lines(lines):
    client.get(
        "/api",
        headers={"user-agent": "evil\r\nip=9.9.9.9 status=200 fake"},
    )
    message = lines.messages[0]
    assert len(message.splitlines()) == 1
    assert "\r" not in message and "\n" not in message
