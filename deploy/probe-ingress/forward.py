"""The consoles stay reachable after the probes lose their route off the network.

A docker network with `internal: true` is how a container stops being able to
reach the host — which is the boundary the MCP gate needs to be worth anything
(deploy/mcp-gate/gate.py). It also stops a PUBLISHED PORT working, measured
before this was written rather than after: a container on an internal-only
network answers nothing on 127.0.0.1, because there is no path in either
direction.

So ingress becomes a thing that sits on both sides. This is it, and it is a byte
pump rather than an HTTP proxy on purpose: the consoles stream events (SSE), and
every bug this repository has had in a proxy was in the HTTP layer — a buffered
read that swallowed a handshake, a response body held until a buffer filled. A
forwarder that never looks at the bytes cannot have those.

One direction only. Nothing here dials out on behalf of a probe; a probe's way
out is the egress proxy and its allowlist, unchanged.

Every listener is bound before any of them serves. The first shape of this
served the first route on the main thread and the rest on daemon threads, so a
second route that could not bind — a port already taken, a duplicate in the
spec, a number past 65535 — died in its thread with a traceback while the
container stayed up and that console stayed dark. A malformed entry stops the
container; so must a port that cannot be had.
"""

from __future__ import annotations

import logging
import os
import select
import socket
import socketserver
import sys
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ingress %(message)s")
logger = logging.getLogger("ingress")

# `<listen port>:<host>:<port>` entries, comma-separated. Empty forwards
# nothing, which is the safe direction for a variable that failed to
# interpolate: no listener is loud the first time somebody opens a console.
ROUTES = os.environ.get("INGRESS_FORWARD", "")
# On the select loop AND on both sockets: a peer that stops reading parks the
# pump in sendall, where the select timeout cannot reach it, so the sockets
# carry the same limit. The egress proxy's twin loop uses 300; the consoles
# ping every 20 s, so a browser tab that is alive is never idle this long, and
# a console page left open through a lunch break must not be cut.
IDLE_SECONDS = 900


def parse(spec: str) -> list[tuple[int, str, int]]:
    routes: list[tuple[int, str, int]] = []
    for entry in (part.strip() for part in spec.split(",") if part.strip()):
        listen, _, target = entry.partition(":")
        host, _, port = target.rpartition(":")
        if not (listen.isdigit() and host and port.isdigit()):
            raise SystemExit(f"probe-ingress: {entry!r} is not <listen>:<host>:<port>")
        if not (0 < int(listen) < 65536 and 0 < int(port) < 65536):
            raise SystemExit(f"probe-ingress: {entry!r} names a port outside 1-65535")
        if int(listen) in {r[0] for r in routes}:
            raise SystemExit(f"probe-ingress: {entry!r} listens on a port an earlier entry already took")
        routes.append((int(listen), host, int(port)))
    return routes


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Both ways until either end stops. The same loop as the egress proxy's,
    and no inspection for the same reason: this is about WHERE, not what."""
    sockets = [a, b]
    try:
        while True:
            ready, _, bad = select.select(sockets, [], sockets, IDLE_SECONDS)
            if bad or not ready:
                return
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (b if source is a else a).sendall(data)
    except OSError:
        return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def bind(listen: int, host: str, port: int) -> Server:
    """A listener on `listen`, bound now, pumping each connection to host:port.
    Raises OSError when the port cannot be had, which main() lets stop the
    container — see the module docstring."""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            try:
                upstream = socket.create_connection((host, port), timeout=10)
            except OSError as exc:
                # The console is down or renamed; say which, because "the page
                # does not load" is otherwise indistinguishable from a bad route.
                logger.warning("no route to %s:%d for :%d — %s", host, port, listen, exc)
                return
            # The connect timeout would otherwise stay on the upstream socket
            # for the pump's whole life and cut a stalled send at ten seconds;
            # the client socket would have none and hold a stalled send forever.
            upstream.settimeout(IDLE_SECONDS)
            self.request.settimeout(IDLE_SECONDS)
            with upstream:
                _pump(self.request, upstream)

    server = Server(("0.0.0.0", listen), Handler)  # noqa: S104 — the container's own network only
    logger.info("127.0.0.1:%d reaches %s:%d", listen, host, port)
    return server


def serve(listen: int, host: str, port: int) -> None:
    bind(listen, host, port).serve_forever()


def main() -> int:
    routes = parse(ROUTES)
    if not routes:
        raise SystemExit("probe-ingress: set INGRESS_FORWARD to <listen>:<host>:<port>[,...]")
    servers = [bind(*route) for route in routes]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
