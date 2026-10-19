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

WHAT THE LIST IS MADE OF. Three kinds of rule, comma-separated in EGRESS_ALLOW:
a bare name matches exactly; a leading dot matches the tree under it and the
apex; and two named shapes, `@aws-api` and `@aws-s3`, stand for "an AWS service
API endpoint, any service, any region" and "an S3 bucket" —
`<service>.amazonaws.com`, `<service>.<region>.amazonaws.com`,
`<bucket>.s3[.<region>].amazonaws.com`. The shapes replaced `.amazonaws.com` on
2026-10-19: that suffix also admitted every hostname anyone can get under
amazonaws.com — an EC2 instance's public name, a load balancer, an API Gateway
stage, somebody else's EKS cluster — which is a destination an attacker controls
sitting inside the allowlist. A read-only investigation needs the service APIs
and the buckets; a cluster endpoint it is given is one more bare name in the
operator's list, and the shapes do not match it on purpose (the id in front is
anyone's). Found on a second deployment running a copy of this file, and
verified against this file's own matcher. A shape the proxy does not know stops
the container at start rather than admitting nothing in silence.

WHERE A NAME IS ALLOWED TO RESOLVE. The list is on names; the connection is to
an address, and those are two claims. A listed name that resolves to a loopback,
link-local, private or reserved address — or to the address a desktop container
runtime maps the host machine to — is refused, and the proxy connects to the
address it vetted rather than looking the name up a second time. The names of
this compose's own peers (EGRESS_INTERNAL: the pipe, the sink) are the exception
and the inverse: they MUST resolve to a private or loopback address, because a
peer whose name has fallen through to the host's resolver is not the peer. The
name is checked before any lookup, so a name is not a DNS exfiltration channel.
A name that does not resolve is a 502, not a 403: the agent is told to read 403
as "refused, do not retry", and a resolver hiccup is not that.

WHAT THIS IS NOT. It is not a defence against an adversary with code execution
and patience: a name on the list that serves attacker-controlled content is a
way out (the model gateway itself takes POST bodies, and anyone can create an
S3 bucket). It bounds the accident and the opportunist, it makes the attempt
visible, and it turns "anywhere" into "somewhere somebody chose".
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import socketserver
import sys
import threading

from common import Server, pump

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("egress")

_AWS_REGION = r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]"
# The two shapes an `@` rule may name. Full matches, lower-case hosts.
SHAPES: dict[str, re.Pattern[str]] = {
    "@aws-api": re.compile(rf"[a-z0-9][a-z0-9-]*(?:\.{_AWS_REGION})?\.amazonaws\.com"),
    "@aws-s3": re.compile(rf"[a-z0-9][a-z0-9.-]*\.s3(?:\.dualstack)?(?:[.-]{_AWS_REGION})?\.amazonaws\.com"),
}


def _rules(text: str) -> tuple[str, ...]:
    return tuple(h.strip().lower().rstrip(".") for h in text.split(",") if h.strip())


# Comma-separated. Empty means allow NOTHING, which is the safe direction for a
# variable that failed to interpolate — an empty allowlist is loud within
# seconds, where an empty list meaning "allow all" would be silent forever.
ALLOW = _rules(os.environ.get("EGRESS_ALLOW", ""))
# This compose's own peers, by name: admitted without being on ALLOW, and held
# to resolving inside the compose network.
INTERNAL = frozenset(_rules(os.environ.get("EGRESS_INTERNAL", "")))
PORT = int(os.environ.get("EGRESS_PORT", "8888"))
# Ports a client may CONNECT to. Everything the family speaks is HTTP or HTTPS;
# a proxy that will open 22 or 5432 for you is a tunnel, not a boundary.
PORTS = frozenset(int(p) for p in os.environ.get("EGRESS_PORTS", "80,443,8088,8100,8200,4318").split(",") if p.strip())
IDLE_SECONDS = 300
CONNECT_TIMEOUT = 20
# Where a desktop container runtime maps the host machine, inside a range that
# is otherwise "this network" and that the stdlib does not call private.
HOST_MAPPED = ipaddress.ip_network("0.250.250.0/24")
# Where a compose peer may resolve: the bridge networks' private ranges, and
# loopback for the one case of a peer that is this container itself.
INTERNAL_NETS = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128")
)

for _rule in ALLOW:
    if _rule.startswith("@") and _rule not in SHAPES:
        sys.exit(f"egress: EGRESS_ALLOW names a shape this proxy does not know: {_rule} (known: {', '.join(SHAPES)})")


def permitted(host: str) -> bool:
    host = host.lower().strip(".")
    if not host:
        return False
    for rule in ALLOW:
        if rule.startswith("@"):
            if SHAPES[rule].fullmatch(host):
                return True
        elif rule.startswith("."):
            if host == rule[1:] or host.endswith(rule):
                return True
        elif host == rule:
            return True
    return False


def internal(host: str) -> bool:
    return host.lower().strip(".") in INTERNAL


class Refused(Exception):
    """A destination this proxy will not connect to, with the status the client gets."""

    def __init__(self, code: int, why: str) -> None:
        super().__init__(why)
        self.code, self.why = code, why


def _unwrapped(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """An IPv4 address carried inside IPv6 is the IPv4 address."""
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped is not None else ip


def public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    candidate = _unwrapped(ip)
    if candidate.version == 4 and candidate in HOST_MAPPED:
        return False
    return candidate.is_global


def inside_compose(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    candidate = _unwrapped(ip)
    return any(candidate in net for net in INTERNAL_NETS)


def resolve(host: str, port: int, kind: str) -> list[tuple]:
    """Every address the name gives, each vetted for where that kind of name may
    point: `external` must be public, `internal` must be inside the compose."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Refused(502, f"did not resolve: {exc}") from None
    vetted: list[tuple] = []
    for family, type_, proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        if kind == "internal" and not inside_compose(ip):
            raise Refused(403, f"a compose peer resolved outside the compose network ({ip})")
        if kind == "external" and not public(ip):
            raise Refused(403, f"resolves to a non-public address ({ip})")
        vetted.append((family, type_, proto, sockaddr))
    if not vetted:
        raise Refused(502, "did not resolve")
    return vetted


def decide(host: str, port: int) -> list[tuple]:
    """Name first, address second. The list is consulted before any lookup, then
    every address the name resolves to is vetted; what comes back is what
    `connect` dials, with no second lookup in between."""
    host = host.lower().strip(".")
    if port not in PORTS:
        raise Refused(403, f"port {port} is not one this proxy opens")
    if internal(host):
        return resolve(host, port, "internal")
    if not permitted(host):
        raise Refused(403, "not on the egress allowlist")
    return resolve(host, port, "external")


def connect(infos: list[tuple]) -> socket.socket:
    """A socket to the first vetted address that answers."""
    error: OSError | None = None
    for family, type_, proto, sockaddr in infos:
        sock = socket.socket(family, type_, proto)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError("no address to connect to")


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
        try:
            infos = decide(host, port)
        except Refused as refused:
            self._refuse(refused.code, refused.why, target[:80])
            return
        try:
            upstream = connect(infos)
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
            pump(self.connection, upstream, IDLE_SECONDS)

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
        host = host.strip("[]")
        try:
            infos = decide(host, port)
        except Refused as refused:
            self._refuse(refused.code, refused.why, f"{host}:{port}")
            return
        try:
            upstream = connect(infos)
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
            pump(self.connection, upstream, IDLE_SECONDS)

    def _refuse(self, code: int, why: str, target: str, level: int = logging.WARNING) -> None:
        # WARNING, not INFO: a refusal here is the whole product of this
        # container. It means something inside the investigator tried to reach
        # an address nobody wrote down, and that is worth an operator's eye
        # whether it was a mistake, a dependency nobody declared, or worse.
        logger.log(level, "REFUSED %s — %s", target, why)
        body = f"{why}\n".encode()
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(code, "Forbidden")
        try:
            self.wfile.write(f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            self.wfile.flush()
        except OSError:
            pass


def build() -> Server:
    """The proxy, bound and not yet serving: doors.py runs it beside the gate
    and the ingress in one process; main() runs it alone."""
    if not ALLOW:
        logger.warning("EGRESS_ALLOW is empty — every destination will be refused")
    logger.info(
        "egress proxy on :%s, %s host rule(s), %s compose peer(s), ports %s",
        PORT,
        len(ALLOW),
        len(INTERNAL),
        sorted(PORTS),
    )
    for rule in ALLOW:
        logger.info("  allow %s", rule)
    for name in sorted(INTERNAL):
        logger.info("  peer  %s (must resolve inside the compose network)", name)
    return Server(("0.0.0.0", PORT), Handler)  # nosec B104 — internal network only


def main() -> int:
    with build() as server:
        threading.current_thread().name = "egress"
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
