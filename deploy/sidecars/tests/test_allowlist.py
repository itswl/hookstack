"""What the proxy lets out, and — the part that matters — what it does not.

The allowlist is the whole product. Every one of these is a way it could be
wrong while looking right: a suffix rule that admits a lookalike domain, a port
nobody meant to open, an empty variable read as "allow everything".
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _with(allow: str, ports: str = "80,443"):
    os.environ["EGRESS_ALLOW"] = allow
    os.environ["EGRESS_PORTS"] = ports
    import proxy

    return importlib.reload(proxy)


def test_a_bare_name_is_exact_and_a_lookalike_is_refused() -> None:
    """`endswith("example.com")` would have admitted `notexample.com`, and that
    is the domain somebody registers on purpose."""
    p = _with("example.com")
    assert p.permitted("example.com")
    assert not p.permitted("notexample.com")
    assert not p.permitted("example.com.evil.net")
    assert not p.permitted("sub.example.com"), "a bare rule is the host, not the tree under it"


def test_a_leading_dot_admits_the_tree_and_the_apex() -> None:
    p = _with(".amazonaws.com")
    assert p.permitted("ec2.ap-southeast-1.amazonaws.com")
    assert p.permitted("amazonaws.com")
    assert not p.permitted("notamazonaws.com")
    assert not p.permitted("amazonaws.com.evil.net")


def test_case_and_a_trailing_dot_do_not_get_around_it() -> None:
    """A resolver treats `EXAMPLE.COM.` as `example.com`; so must this, or the
    allowlist is bypassed by a keystroke."""
    p = _with("example.com")
    assert p.permitted("EXAMPLE.com")
    assert p.permitted("example.com.")


def test_an_empty_allowlist_refuses_everything() -> None:
    """The safe direction for a variable that failed to interpolate. An empty
    list read as "allow all" is silent forever; read as "allow nothing" it is
    loud within seconds."""
    p = _with("")
    assert p.ALLOW == ()
    for host in ("example.com", "localhost", "hookrelay", ""):
        assert not p.permitted(host)


def test_only_the_listed_ports_are_opened() -> None:
    """A proxy that will open 22 or 5432 for you is a tunnel, not a boundary."""
    p = _with("example.com", ports="443")
    assert sorted(p.PORTS) == [443]
    assert 22 not in p.PORTS and 5432 not in p.PORTS


@pytest.mark.parametrize("rule", ["  example.com  ", "example.com,", ",example.com"])
def test_whitespace_and_stray_commas_do_not_create_a_blank_rule(rule: str) -> None:
    """A blank entry that matched the empty host would be an allowlist with a
    hole punched by a typo."""
    p = _with(rule)
    assert p.ALLOW == ("example.com",)
    assert not p.permitted("")


def test_bytes_sent_right_behind_the_connect_reach_the_far_side() -> None:
    """A client that starts TLS without waiting for the 200 must not lose its hello.

    `StreamRequestHandler` buffers `rfile` by default, so the first `readline`
    takes the ClientHello off the socket along with the request line and leaves
    it in a buffer the tunnel never reads. The handshake then never arrives and
    the connection hangs on a timeout that reads like a slow upstream — the
    shape this was actually found as, on the twin of this file.
    """
    import socket
    import socketserver
    import threading

    class Echo(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(5)
            self.request.sendall(b"echo:" + self.request.recv(1024))

    upstream = socketserver.TCPServer(("127.0.0.1", 0), Echo)
    port = upstream.server_address[1]
    proxy = _with("127.0.0.1", str(port))
    gateway = proxy.Server(("127.0.0.1", 0), proxy.Handler)
    for server in (upstream, gateway):
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        client = socket.create_connection(("127.0.0.1", gateway.server_address[1]), timeout=5)
        client.sendall(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\n\r\n".encode() + b"early")
        seen = b""
        while b"echo:" not in seen:
            chunk = client.recv(1024)
            if not chunk:
                break
            seen += chunk
        client.close()
        assert seen.startswith(b"HTTP/1.1 200") and seen.endswith(b"echo:early"), seen
    finally:
        for server in (upstream, gateway):
            server.shutdown()
            server.server_close()
