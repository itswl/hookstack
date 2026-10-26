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


def _with(allow: str, ports: str = "80,443", internal: str = "", ledger: str = ""):
    os.environ["EGRESS_ALLOW"] = allow
    os.environ["EGRESS_PORTS"] = ports
    os.environ["EGRESS_INTERNAL"] = internal
    os.environ["EGRESS_LEDGER"] = ledger
    import proxy

    return importlib.reload(proxy)


def _resolving_to(monkeypatch, module, *addresses: str):
    """A resolver that answers every name with these addresses, and counts."""
    calls: list[str] = []

    def fake(host, port, *args, **kwargs):
        calls.append(host)
        return [(2, 1, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(module.socket, "getaddrinfo", fake)
    return calls


def test_a_bare_name_is_exact_and_a_lookalike_is_refused() -> None:
    """`endswith("example.com")` would have admitted `notexample.com`, and that
    is the domain somebody registers on purpose."""
    p = _with("example.com")
    assert p.permitted("example.com")
    assert not p.permitted("notexample.com")
    assert not p.permitted("example.com.evil.net")
    assert not p.permitted("sub.example.com"), "a bare rule is the host, not the tree under it"


def test_a_leading_dot_admits_the_tree_and_the_apex() -> None:
    p = _with(".example.net")
    assert p.permitted("api.eu.example.net")
    assert p.permitted("example.net")
    assert not p.permitted("notexample.net")
    assert not p.permitted("example.net.evil.net")


def test_the_aws_api_shape_admits_service_endpoints_and_nothing_anyone_can_register() -> None:
    """`.amazonaws.com` admitted every hostname anyone can get under it. The shape
    admits `<service>[.<region>].amazonaws.com` and refuses the forms whose first
    label is somebody's: an instance, a load balancer, an API Gateway stage, an
    EKS cluster — a cluster the operator means goes on the list by name."""
    p = _with("@aws-api")
    for host in (
        "sts.amazonaws.com",
        "iam.amazonaws.com",
        "ec2.ap-southeast-1.amazonaws.com",
        "logs.us-gov-west-1.amazonaws.com",
        "ssmmessages.eu-central-1.amazonaws.com",
    ):
        assert p.permitted(host), host
    for host in (
        "amazonaws.com",
        "notamazonaws.com",
        "sts.amazonaws.com.evil.net",
        "ec2-1-2-3-4.ap-southeast-1.compute.amazonaws.com",
        "my-lb-123456.ap-southeast-1.elb.amazonaws.com",
        "abc123.execute-api.ap-southeast-1.amazonaws.com",
        "0123456789abcdef0123456789abcdef.gr7.ap-southeast-1.eks.amazonaws.com",
        "my-bucket.s3.ap-southeast-1.amazonaws.com",
    ):
        assert not p.permitted(host), host


def test_the_s3_shape_admits_buckets_in_every_spelling() -> None:
    p = _with("@aws-s3")
    for host in (
        "my-bucket.s3.amazonaws.com",
        "my.dotted.bucket.s3.ap-southeast-1.amazonaws.com",
        "my-bucket.s3-ap-southeast-1.amazonaws.com",
        "my-bucket.s3.dualstack.ap-southeast-1.amazonaws.com",
    ):
        assert p.permitted(host), host
    for host in (
        "s3.amazonaws.com",
        "evil.com",
        "my-bucket.s3.amazonaws.com.evil.net",
        "ec2.ap-southeast-1.amazonaws.com",
    ):
        assert not p.permitted(host), host


def test_a_shape_the_proxy_does_not_know_stops_it_at_start() -> None:
    """A typo in a rule must not become a rule that admits nothing in silence."""
    with pytest.raises(SystemExit, match="shape"):
        _with("@aws-apii")
    _with("example.com")  # and the module imports again afterwards


def test_a_listed_name_may_not_resolve_to_a_private_or_host_address(monkeypatch) -> None:
    """The list is on names and the connection is to an address. A listed name
    whose answer is loopback, private, link-local or the host machine's mapped
    address is refused, including an IPv4 answer wrapped in IPv6."""
    p = _with("example.com")
    for address in (
        "127.0.0.1",
        "10.0.0.5",
        "172.18.0.9",
        "192.168.1.2",
        "169.254.169.254",
        "0.250.250.254",
        "::ffff:127.0.0.1",
        "::1",
    ):
        _resolving_to(monkeypatch, p, address)
        with pytest.raises(p.Refused) as refused:
            p.decide("example.com", 443)
        assert refused.value.code == 403 and "non-public" in refused.value.why, address
    _resolving_to(monkeypatch, p, "93.184.216.34")
    assert p.decide("example.com", 443)[0][3] == ("93.184.216.34", 443)


def test_one_bad_answer_refuses_the_whole_name(monkeypatch) -> None:
    p = _with("example.com")
    _resolving_to(monkeypatch, p, "93.184.216.34", "127.0.0.1")
    with pytest.raises(p.Refused):
        p.decide("example.com", 443)


def test_a_compose_peer_must_resolve_inside_the_compose(monkeypatch) -> None:
    """A peer name that has fallen through to the host's resolver is not the peer."""
    p = _with("", internal="hookrelay")
    _resolving_to(monkeypatch, p, "172.18.0.5")
    assert p.decide("hookrelay", 443)[0][3] == ("172.18.0.5", 443)
    _resolving_to(monkeypatch, p, "127.0.0.1")
    assert p.decide("hookrelay", 443), "loopback is this container itself"
    for address in ("93.184.216.34", "0.250.250.254"):
        _resolving_to(monkeypatch, p, address)
        with pytest.raises(p.Refused) as refused:
            p.decide("hookrelay", 443)
        assert refused.value.code == 403 and "compose" in refused.value.why, address


def test_the_name_is_checked_before_any_lookup(monkeypatch) -> None:
    """Otherwise a name is a DNS exfiltration channel: the lookup itself leaves."""
    p = _with("example.com")
    calls = _resolving_to(monkeypatch, p, "93.184.216.34")
    with pytest.raises(p.Refused) as refused:
        p.decide("evil.example.org", 443)
    assert refused.value.code == 403 and calls == []


def test_a_name_that_does_not_resolve_is_a_502_not_a_refusal(monkeypatch) -> None:
    """The agent is told 403 means do not retry; a resolver hiccup must not read as that."""
    import socket as socket_module

    p = _with("example.com")

    def failing(*args, **kwargs):
        raise socket_module.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(p.socket, "getaddrinfo", failing)
    with pytest.raises(p.Refused) as refused:
        p.decide("example.com", 443)
    assert refused.value.code == 502


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


def test_a_refusal_is_written_to_the_ledger_when_one_is_named(tmp_path) -> None:
    """A container's log leaves with the container; the refusal log is the product."""
    import json
    import socket
    import threading

    ledger = tmp_path / "egress.jsonl"
    proxy = _with("example.com", ledger=str(ledger))
    gateway = proxy.Server(("127.0.0.1", 0), proxy.Handler)
    threading.Thread(target=gateway.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", gateway.server_address[1]), timeout=5) as client:
            client.sendall(b"CONNECT evil.test:443 HTTP/1.1\r\nHost: evil.test\r\n\r\n")
            first = client.recv(200).decode("latin-1").splitlines()[0]
    finally:
        gateway.shutdown()
        gateway.server_close()
    assert first.startswith("HTTP/1.1 403")
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["event"] == "egress.refused" and row["target"] == "evil.test:443" and row["code"] == 403
    assert "allowlist" in row["why"]


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
    # Loopback is where a compose peer may live (this container itself), never
    # where a listed external name may point.
    proxy = _with("", str(port), internal="127.0.0.1")
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


def test_a_failure_nobody_planned_for_is_refused_and_recorded(tmp_path, monkeypatch) -> None:
    """Anything but a Refused used to end the thread: no answer, no ledger line."""
    import json
    import socket
    import threading

    ledger = tmp_path / "egress.jsonl"
    proxy = _with("example.com", ledger=str(ledger))

    def boom(host, port):
        raise RuntimeError("nobody planned for this")

    monkeypatch.setattr(proxy, "decide", boom)
    gateway = proxy.Server(("127.0.0.1", 0), proxy.Handler)
    threading.Thread(target=gateway.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", gateway.server_address[1]), timeout=5) as client:
            client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
            first = client.recv(200).decode("latin-1").splitlines()[0]
    finally:
        gateway.shutdown()
        gateway.server_close()
    assert first.startswith("HTTP/1.1 502")
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["event"] == "egress.refused" and row["target"] == "example.com:443" and row["code"] == 502
    assert "RuntimeError" in row["why"]


def test_a_plain_request_line_reaches_the_peer_byte_for_byte() -> None:
    """The line is read as Latin-1, so it must leave as Latin-1: a client that
    does not percent-encode sends raw UTF-8, and UTF-8 on the way out doubled it."""
    import socket
    import socketserver
    import threading

    seen: list[bytes] = []

    class Capture(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                data += chunk
            seen.append(data.split(b"\r\n", 1)[0])
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    upstream = socketserver.TCPServer(("127.0.0.1", 0), Capture)
    port = upstream.server_address[1]
    proxy = _with("", str(port), internal="127.0.0.1")
    gateway = proxy.Server(("127.0.0.1", 0), proxy.Handler)
    for server in (upstream, gateway):
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    path = "/trace/告警?q=磁盘".encode()
    try:
        with socket.create_connection(("127.0.0.1", gateway.server_address[1]), timeout=5) as client:
            client.sendall(b"GET http://127.0.0.1:%d%s HTTP/1.1\r\nHost: x\r\n\r\n" % (port, path))
            answer = b""
            while chunk := client.recv(4096):
                answer += chunk
    finally:
        for server in (upstream, gateway):
            server.shutdown()
            server.server_close()
    assert answer.startswith(b"HTTP/1.1 200"), answer[:80]
    assert seen == [b"GET " + path + b" HTTP/1.1"]
