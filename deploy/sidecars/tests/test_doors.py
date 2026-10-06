"""One process, three doors: the entrypoint binds the proxy, the gate and
every ingress route before anything serves, from one environment."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_entrypoint_binds_all_three_from_one_environment(monkeypatch):
    ports = [_free_port() for _ in range(4)]
    monkeypatch.setenv("EGRESS_PORT", str(ports[0]))
    monkeypatch.setenv("EGRESS_ALLOW", "example.test")
    monkeypatch.setenv("MCPGATE_PORT", str(ports[1]))
    monkeypatch.setenv("MCPGATE_UPSTREAM", "http://127.0.0.1:1/mcp/")
    monkeypatch.setenv("MCPGATE_CLIENT_WATCH_TOKEN", "watch-token-long-enough")
    monkeypatch.setenv("MCPGATE_CLIENT_WATCH_TOOLS", "chat.list_*")
    monkeypatch.setenv("INGRESS_FORWARD", f"{ports[2]}:probe-plan:8088,{ports[3]}:probe-watch:8088")
    for name in ("proxy", "gate", "forward", "doors"):
        sys.modules.pop(name, None)  # each reads its environment at import or at build
    import doors

    bound = doors.servers()
    try:
        assert sorted(server.server_address[1] for server in bound) == sorted(ports)
        assert len(bound) == 4
    finally:
        for server in bound:
            server.server_close()
