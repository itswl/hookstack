"""The one way out, and a list of where out is allowed to be.

A container boundary stops somebody breaking INTO the host. It does nothing
about the agent reading a credential it is entitled to and posting it
somewhere it is not: measured on this deployment, a Bash step inside the
investigator can read the provider key out of its own environment, and — until
this — reach any address on the internet to send it to.

So the probe loses its route to the world and gets this instead: a forward
proxy on an allowlist. Everything the investigator legitimately talks to is a
name somebody wrote down; everything else is refused and, more importantly,
LOGGED. A refused connection is the signal — it is the only place that can say
"this run tried to reach somewhere nobody listed".

Deliberately stdlib-only and small enough to read in one sitting, like the
bridge beside it. A proxy is now in the path of every call the investigator
makes, including the model call and the report going home; a dependency there
that nobody in this repository can audit would be a worse trade than the hole
it closes.

WHAT THIS IS NOT. It is not a defence against an adversary with code execution
and patience: the allowlist is on HOSTNAMES, and a name on it that serves
attacker-controlled content is a way out (the model gateway itself takes POST
bodies). It bounds the accident and the opportunist, it makes the attempt
visible, and it turns "anywhere" into "somewhere somebody chose".
"""

from __future__ import annotations

import logging
import os
import select
import socket
import socketserver
import sys
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s egress %(message)s")
logger = logging.getLogger("egress")

# Comma-separated. A bare name matches exactly; a leading dot matches any
# subdomain (".amazonaws.com" admits "ec2.ap-southeast-1.amazonaws.com").
# Empty means allow NOTHING, which is the safe direction for a variable that
# failed to interpolate — an empty allowlist is loud within seconds, where an
# empty list meaning "allow all" would be silent forever.
ALLOW = tuple(h.strip().lower() for h in os.environ.get("EGRESS_ALLOW", "").split(",") if h.strip())
PORT = int(os.environ.get("EGRESS_PORT", "8888"))
# Ports a client may CONNECT to. Everything the family speaks is HTTP or HTTPS;
# a proxy that will open 22 or 5432 for you is a tunnel, not a boundary.
PORTS = frozenset(int(p) for p in os.environ.get("EGRESS_PORTS", "80,443,8088,8100,8200,4318").split(",") if p.strip())
IDLE_SECONDS = 300


def permitted(host: str) -> bool:
    host = host.lower().strip(".")
    for rule in ALLOW:
        if rule.startswith("."):
            if host == rule[1:] or host.endswith(rule):
                return True
        elif host == rule:
            return True
    return False


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Move bytes both ways until either end stops. No inspection: this is a
    boundary about WHERE, not about what — reading the traffic would put the
    agent's tool output through one more thing that could log it."""
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


class Handler(socketserver.StreamRequestHandler):
    timeout = 30
    # Unbuffered reads. The default `rfile` is a buffered reader, and its first
    # `readline` pulls up to the buffer size off the socket — so a client that
    # sends its TLS ClientHello immediately behind `CONNECT` without waiting for
    # the 200 has that hello swallowed into a buffer nobody reads again. The
    # tunnel below then forwards from the raw socket, the handshake never
    # arrives, and the connection hangs until a timeout that looks like the far
    # side being slow. Found in this file's twin (2026-09-23) against a model
    # gateway; the same code and the same defect are here.
    rbufsize = 0

    def handle(self) -> None:
        try:
            line = self.rfile.readline(65536).decode("latin-1").strip()
        except OSError:
            return
        parts = line.split()
        if len(parts) < 3:
            self._refuse(400, "not a request line", line[:40])
            return
        if parts[0].upper() != "CONNECT":
            # Absolute-URI HTTP, for the plain-http peers a container on an
            # internal network can no longer reach directly (the pipe, the MCP
            # server, the collector). Checked the same way and against the same
            # list: THIS proxy resolves and connects to the host out of the URI
            # it just checked, so there is no Host-header-versus-destination
            # disagreement to exploit — an earlier version refused these on that
            # reasoning and the reasoning was wrong.
            self._plain(parts)
            return
        target = parts[1]
        host, _, port_text = target.rpartition(":")
        try:
            port = int(port_text)
        except ValueError:
            self._refuse(400, "no port in the CONNECT target", target[:80])
            return
        host = host.strip("[]")
        if port not in PORTS:
            self._refuse(403, f"port {port} is not one this proxy opens", target[:80])
            return
        if not permitted(host):
            self._refuse(403, "not on the egress allowlist", target[:80])
            return
        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except OSError as exc:
            self._refuse(502, f"upstream refused: {exc}", target[:80], level=logging.WARNING)
            return
        # Drain the rest of the request head before splicing.
        while True:
            more = self.rfile.readline(65536)
            if more in (b"", b"\r\n", b"\n"):
                break
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        self.wfile.flush()
        logger.info("allowed %s", target[:80])
        with upstream:
            _pump(self.connection, upstream)

    def _plain(self, parts: list[str]) -> None:
        """One absolute-URI HTTP request, forwarded if its host is listed."""
        method, uri = parts[0], parts[1]
        if not uri.lower().startswith("http://"):
            self._refuse(400, "expected an absolute http:// URI or CONNECT", uri[:80])
            return
        rest = uri[7:]
        authority, slash, path = rest.partition("/")
        host, _, port_text = authority.rpartition(":")
        if not host:
            host, port = authority, 80
        else:
            try:
                port = int(port_text)
            except ValueError:
                self._refuse(400, "unreadable port", authority[:80])
                return
        if port not in PORTS or not permitted(host):
            self._refuse(403, "not on the egress allowlist", f"{host}:{port}")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except OSError as exc:
            self._refuse(502, f"upstream refused: {exc}", f"{host}:{port}")
            return
        # Origin-form on the way out, which is what a server expects, plus the
        # head as the client sent it. Connection: close on both sides — this
        # forwards one request per connection rather than tracking keep-alive
        # state, which is the difference between a boundary and a web server.
        head = [f"{method} /{path if slash else ''} {parts[2]}".encode(), b"Connection: close"]
        while True:
            raw = self.rfile.readline(65536)
            if raw in (b"", b"\r\n", b"\n"):
                break
            if raw.lower().startswith((b"connection:", b"proxy-connection:")):
                continue
            head.append(raw.rstrip(b"\r\n"))
        logger.info("allowed %s %s:%s", method, host, port)
        with upstream:
            upstream.sendall(b"\r\n".join(head) + b"\r\n\r\n")
            _pump(self.connection, upstream)

    def _refuse(self, code: int, why: str, target: str, level: int = logging.WARNING) -> None:
        # WARNING, not INFO: a refusal here is the whole product of this
        # container. It means something inside the investigator tried to reach
        # an address nobody wrote down, and that is worth an operator's eye
        # whether it was a mistake, a dependency nobody declared, or worse.
        logger.log(level, "REFUSED %s — %s", target, why)
        body = f"{why}\n".encode()
        try:
            self.wfile.write(f"HTTP/1.1 {code} Forbidden\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            self.wfile.flush()
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if not ALLOW:
        logger.warning("EGRESS_ALLOW is empty — every destination will be refused")
    logger.info("egress proxy on :%s, %s host rule(s), ports %s", PORT, len(ALLOW), sorted(PORTS))
    for rule in ALLOW:
        logger.info("  allow %s", rule)
    with Server(("0.0.0.0", PORT), Handler) as server:  # nosec B104 — internal network only
        threading.current_thread().name = "egress"
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
