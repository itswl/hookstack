"""The ingress forwards, and it forwards only where it was told to.

The route spec is the whole product: one wrong entry and a console is either
dark or pointing at the wrong node. A malformed entry stops the container
instead of listening on a port that goes nowhere.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import forward  # noqa: E402


def test_a_route_spec_is_read_as_written():
    assert forward.parse("8089:probe-watch:8088") == [(8089, "probe-watch", 8088)]
    assert forward.parse(" 8088:a:1 , 8090:b:2 ") == [(8088, "a", 1), (8090, "b", 2)]
    assert forward.parse("") == []


@pytest.mark.parametrize("spec", ["8089", "8089:probe-watch", "eight:probe-watch:8088", "8089:probe-watch:http"])
def test_a_spec_that_would_listen_on_nothing_stops_the_container(spec):
    with pytest.raises(SystemExit):
        forward.parse(spec)


def test_bytes_go_both_ways_and_the_far_end_closing_ends_it():
    """A pump, not a protocol: the console streams events, and anything that
    parsed the bytes could hold them."""
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    _, target_port = upstream.getsockname()

    def echo_once():
        conn, _ = upstream.accept()
        with conn:
            conn.sendall(b"hello ")
            conn.sendall(conn.recv(100).upper())

    threading.Thread(target=echo_once, daemon=True).start()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    _, listen_port = listener.getsockname()
    listener.close()
    threading.Thread(target=forward.serve, args=(listen_port, "127.0.0.1", target_port), daemon=True).start()
    for _ in range(50):
        try:
            client = socket.create_connection(("127.0.0.1", listen_port), timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    else:
        pytest.fail("the forwarder never started listening")
    with client:
        client.sendall(b"world")
        got = b""
        client.settimeout(3)
        while len(got) < len("hello WORLD"):
            chunk = client.recv(100)
            if not chunk:
                break
            got += chunk
    assert got == b"hello WORLD"
