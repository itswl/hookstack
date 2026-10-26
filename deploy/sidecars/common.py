"""What the four sidecars share, in one file beside them.

The egress proxy, the MCP gate, the watch signer and the console ingress are
single-file containers: `python:3.14-slim` with a script bind-mounted and no
image to build, which is what lets each of them be read in one sitting and
edited without a rebuild. Until 2026-10-06 the plumbing they had in common was
COPIED into each file and pinned identical by scripts/assert_copies.py — five
copies of a constant-time comparison, two ledgers, two byte pumps, two HTTP
handlers with the same timeout, the same answer, the same refusal. The copies
were the price of mounting one file; mounting this directory instead costs
nothing, and the copies end here.

What belongs in this file is plumbing every sidecar has and none of them
decides: how a bearer is compared, how a ledger line is written, how bytes
are pumped between two sockets, how an HTTP answer is sent and a bad length
refused. What a sidecar DOES — which hosts, which tools, which signals — stays
in its own file, where its docstring is the decision record.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import select
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("sidecar")


def constant_time_eq(expected: str, provided: str | None) -> bool:
    """Compare two header-derived strings without leaking length by timing.

    Wraps hmac.compare_digest because that function raises TypeError on a
    str holding non-ASCII, and http.server decodes header bytes as latin-1 —
    so a single 0xF6 byte in the bearer killed a handler with no 401 and no
    ledger row. Comparing the utf-8 bytes keeps the constant-time property and
    answers the way it should. The same copy the family's three doors carry,
    pinned by scripts/assert_copies.py.
    """
    return hmac.compare_digest(expected.encode("utf-8"), (provided or "").encode("utf-8"))


def sign_timestamped(secret: str, body: bytes, *, now: float | None = None) -> dict[str, str]:
    """Headers for an outbound family delivery; empty when unsigned."""
    if not secret:
        return {}
    stamp = str(int(time.time() if now is None else now))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Hook-Timestamp": stamp, "X-Hook-Signature": digest}


class Ledger:
    """Append-only JSONL, one line per event, best-effort.

    A refusal here is the product of the sidecar that writes it — the one place
    that can say "a run asked for a tool nobody gave it" or "a round tried to
    post about something nobody handed it" — and it is best-effort on purpose:
    a sidecar that stopped forwarding because a disk filled would take the
    watcher down with it.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def write(self, **fields: Any) -> None:
        if self.path is None:
            return
        line = json.dumps({"ts": round(time.time(), 3), **fields}, ensure_ascii=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:  # noqa: BLE001 — never fail a call because the ledger cannot be written
            logger.warning("ledger write failed: %s", exc)


def pump(a: socket.socket, b: socket.socket, idle_seconds: float) -> None:
    """Move bytes both ways until either end stops, or nothing moves for
    `idle_seconds`. No inspection: the proxy and the ingress are boundaries
    about WHERE, not about what — reading the traffic would put an agent's
    tool output through one more thing that could log it."""
    sockets = [a, b]
    try:
        while True:
            ready, _, bad = select.select(sockets, [], sockets, idle_seconds)
            if bad or not ready:
                return
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (b if source is a else a).sendall(data)
    except OSError:
        return


class LoggedErrors:
    """socketserver's own handle_error prints a bare traceback to stderr: no
    time, no door, no peer. A handler that raised then left a line nobody could
    place next to the ledger. This sends it through the door's logger instead,
    which has the time, and names the peer."""

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ARG002 — socketserver's signature
        handler = getattr(self, "RequestHandlerClass", None)
        name = getattr(handler, "server_version", "") or getattr(handler, "log_name", "") or "sidecar"
        logging.getLogger(name).error("unhandled error serving %s", client_address, exc_info=True)


class Server(LoggedErrors, socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HttpServer(LoggedErrors, ThreadingHTTPServer):
    """The HTTP doors' server (the gate, the signer), with the same error log."""


_CONTROL_CHARS = getattr(BaseHTTPRequestHandler, "_control_char_table", None)


def plain(text: str) -> str:
    """A request line as the log may show it: control characters escaped, as
    the stdlib does since 3.12, so a path carrying a carriage return or an ANSI
    sequence cannot rewrite the line above it on a terminal."""
    return text.translate(_CONTROL_CHARS) if _CONTROL_CHARS else repr(text)[1:-1]


class HttpHandler(BaseHTTPRequestHandler):
    """The HTTP sidecars' base: keep-alive, a timeout, an answer, a refusal.

    A peer that declares a body and stops sending holds a thread for exactly
    `timeout` seconds; a peer that resets the socket mid-exchange (the MCP
    client does, at every session start, right after the 405 on its GET) has
    nobody to answer and gets no traceback that reads like a real failure.
    """

    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.getLogger(self.server_version).info("%s", plain(fmt % args))

    def handle(self) -> None:
        with contextlib.suppress(ConnectionResetError, BrokenPipeError):
            super().handle()

    def handle_one_request(self) -> None:
        # Waiting for a request line: a timeout from here until one arrives is the
        # connection idling, not a request going wrong (see log_error)
        self._between_requests = True
        self._answer_started = False
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            raise  # nobody to answer; handle() lets these go quietly
        except Exception as exc:  # noqa: BLE001 — a request that fails is still answered and recorded
            # Anything a door did not plan for used to end the thread: no answer,
            # so the caller waited out its own timeout, and no ledger line, so the
            # record of what was asked had a hole exactly where something odd was
            # asked. A second deployment running copies of these files found it
            # with a query string carrying raw non-ASCII bytes.
            self.close_connection = True
            logging.getLogger(self.server_version).error(
                "unexpected failure on %s %s",
                getattr(self, "command", None) or "-",
                plain(getattr(self, "path", "") or "")[:80],
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                self.unexpected(exc)
            if not self._answer_started:
                with contextlib.suppress(OSError):
                    body = json.dumps({"error": f"this door failed on the request: {type(exc).__name__}"})
                    self._answer(502, body.encode())
                    self.wfile.flush()

    def send_response(self, code: int, message: str | None = None) -> None:
        self._answer_started = True
        super().send_response(code, message)

    def unexpected(self, exc: BaseException) -> None:
        """The door's own ledger line for a request that failed in a way nobody
        planned. A door with a ledger overrides this; the log line is written
        either way."""

    def parse_request(self) -> bool:
        self._between_requests = False
        parsed = super().parse_request()
        if parsed:
            self._last_request = {
                "method": self.command,
                "path": plain(self.path),
                "version": self.request_version,
                "content_length": self.headers.get("Content-Length"),
                "header_names": sorted(self.headers.keys()),
            }
            self._request_count = getattr(self, "_request_count", 0) + 1
        return parsed

    def log_error(self, fmt: str, *args: Any) -> None:
        """A timeout says which kind it was, the peer, and what the connection
        had carried.

        The stdlib's line is `Request timed out: TimeoutError('timed out')`
        and nothing else, which cannot tell a keep-alive connection that idled
        past `timeout` from a peer that declared a body and stopped sending it.
        Where the read was tells them apart. Waiting for the next request line
        after at least one answer is the idle close: an MCP client holds a
        connection open for its session and goes quiet between calls, and
        closing it is the timeout doing its job — at WARNING, those lines were
        most of the log, and a real failure had to be picked out from among
        them. So that one goes to DEBUG. Everything else stays one line at
        WARNING, the count and the last request line saying which it was:
        nothing seen is a socket that connected and never spoke; a request
        carrying a Content-Length is the one whose body may not have arrived.
        A failure on the far side of a sidecar is that sidecar's to log (the
        gate's upstream failures are), never this line.
        """
        if fmt == "Request timed out: %r":
            peer = self.client_address[0] if self.client_address else "unknown"
            port = self.client_address[1] if self.client_address else "unknown"
            seen = getattr(self, "_request_count", 0)
            if getattr(self, "_between_requests", False) and seen:
                logging.getLogger(self.server_version).debug(
                    "idle keep-alive closed peer=%s:%s timeout=%ss requests_seen=%s",
                    peer,
                    port,
                    self.timeout,
                    seen,
                )
                return
            logging.getLogger(self.server_version).warning(
                "request timeout peer=%s:%s timeout=%ss requests_seen=%s last_request=%s cause=%s",
                peer,
                port,
                self.timeout,
                seen,
                getattr(self, "_last_request", None) or "none",
                plain(fmt % args),
            )
            return
        super().log_error(fmt, *args)

    def _answer(self, status: int, body: bytes, content_type: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self, status: int, body: bytes) -> None:
        """An answer given before the request body was read: the connection
        closes with it, or on keep-alive the unread body is parsed as the next
        request line."""
        self.close_connection = True
        self._answer(status, body)

    def body_length(self, limit: int, too_large: bytes) -> int | None:
        """The declared Content-Length, or None with a 400 or 413 already sent:
        not a number, negative, or past `limit` is answered, never read."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._refuse(400, b'{"error":"Content-Length is not a number"}')
            return None
        if length < 0:
            self._refuse(400, b'{"error":"Content-Length is negative"}')
            return None
        if length > limit:
            self._refuse(413, too_large)
            return None
        return length
