"""The plumbing every sidecar shares: what a timeout says about the peer."""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common  # noqa: E402


class Echo(common.HttpHandler):
    timeout = 0.3

    def do_POST(self) -> None:  # noqa: N802
        length = self.body_length(1024, b"too big")
        if length is None:
            return
        self.rfile.read(length)
        self._answer(200, b"{}")


@pytest.fixture
def server():
    srv = common.Server(("127.0.0.1", 0), Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address
    srv.shutdown()
    srv.server_close()


def wait_for(caplog, text: str) -> logging.LogRecord:
    for _ in range(50):
        for record in caplog.records:
            if text in record.getMessage():
                return record
        time.sleep(0.05)
    raise AssertionError(f"no log line containing {text!r}; got {[r.getMessage() for r in caplog.records]}")


def test_a_peer_that_never_speaks_is_logged_as_such(server, caplog):
    caplog.set_level(logging.WARNING)
    with socket.create_connection(server):
        record = wait_for(caplog, "request timeout")
    assert record.levelno == logging.WARNING
    assert "requests_seen=0" in record.getMessage() and "last_request=none" in record.getMessage()


def test_an_idle_keep_alive_closes_at_debug_not_as_a_warning(server, caplog):
    """A client that holds its connection between calls — an MCP client does,
    for its session — is closed by the timeout doing its job. At WARNING those
    closes were most of the log, and a peer with a real problem was a line among
    them."""
    caplog.set_level(logging.DEBUG)
    with socket.create_connection(server) as sock:
        sock.sendall(b"POST /v1/thing HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
        assert sock.recv(4096).startswith(b"HTTP/1.1 200")
        record = wait_for(caplog, "idle keep-alive closed")
    assert record.levelno == logging.DEBUG
    assert "requests_seen=1" in record.getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], "an idle close is not a warning"


def test_a_body_that_never_arrives_names_the_request_it_belonged_to(server, caplog):
    caplog.set_level(logging.WARNING)
    with socket.create_connection(server) as sock:
        sock.sendall(b"POST /v1/thing HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n{}")
        record = wait_for(caplog, "request timeout")
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "requests_seen=1" in message
    assert "'method': 'POST'" in message and "'path': '/v1/thing'" in message
    assert "'content_length': '10'" in message
