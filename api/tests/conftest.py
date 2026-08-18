import socket
import threading
import time

import pytest


@pytest.fixture(scope="session")
def live_base_url():
    """A real uvicorn server on a loopback port, for tests that need real
    HTTP semantics (alpaca-py speaks real HTTP; SSE streaming can't be
    exercised through starlette's TestClient). Ephemeral and test-only."""
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
