"""The chat's tools, on a list, behind a door the agent cannot walk around.

The investigator's MCP allowlist (`HOOKPROBE_MCP_TOOLS`) is enforced where the
agent asks for a TOOL. It says nothing about a socket. Measured on the work
deployment on 2026-09-30: from inside probe-watch, one plain HTTP request to the
chat client's own port reached its MCP endpoint with no credential at all, listed
34 tools — 20 of which write, including sending a message as the operator — and
successfully called one the allowlist does not name. The read-only bash guard
does not refuse that request, because refusing "a POST to a port" is not what it
is about. And probe-watch reads colleagues' messages for a living, which is the
family's own definition of attacker-influenced text.

So the tool list stops being advice and becomes a place. The probes lose their
route off the container network; this sits on both sides, holds the only reach
to the chat client, and forwards a `tools/call` only when the tool's name is on
the calling client's list. A direct request from a probe now has nowhere to go,
and a request here for an unlisted tool is refused and recorded.

Streamable HTTP only (the MCP transport the chat client speaks): a POST carrying
JSON-RPC, answered as JSON or as an SSE stream.

Deliberately stdlib-only and small enough to read in one sitting, like the egress
proxy beside it, and for the same reason: it is now in the path of every chat
tool call the family makes.

WHAT THIS IS NOT.
  * Not an argument check. `search_chat_records` with any query is one call to
    this gate; the list is names, not intent. What it buys is that the names
    that WRITE are not reachable at all.
  * Not a guarantee about what a permitted read returns. Everything read here
    still goes to the model provider, as it did before.
  * Not protection from a client token that leaks. The token lives in the
    agent's own MCP config by construction; it admits exactly the tools the
    agent was already allowed, and nothing else — which is the whole point of
    the list being HERE rather than in the agent's config.
  * Not a server-to-client stream. A GET is answered 405 here rather than
    forwarded: the chat server holds that stream open, and a gate that relays it
    holds a thread and a socket open for as long, per session, to carry
    notifications this family does not read. MCP clients treat 405 as "this
    server has no stream" and work POST-only, which is what the investigator
    already does. Session teardown (DELETE) is forwarded; it carries no call.
  * Not a stream in either direction, then: an answer is read whole before it is
    passed on. The chat server closes its SSE answer at the end of each POST, so
    that is one read; a server that did not would hit the timeout below and be
    reported as a failure rather than hanging forever.
"""

from __future__ import annotations

import fnmatch
import hmac
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s mcp-gate %(message)s")
logger = logging.getLogger("mcp-gate")

# Methods that carry no tool and are forwarded as they are: the handshake, the
# keepalive, and the listings. Anything not here and not `tools/call` is refused
# rather than passed — a gateway that forwards methods it has never heard of is
# a gateway that will forward the next one somebody adds.
PASSED = frozenset(
    {
        "initialize",
        "ping",
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "prompts/list",
        "completion/complete",
        "logging/setLevel",
    }
)
# Headers a client may influence. The upstream's own headers are added after
# these, so a client cannot supply its way into a different credential.
FORWARDED = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
# How much of a call's arguments the ledger keeps. Enough to see what was asked,
# short enough that the ledger is not a copy of the chat.
ARGUMENTS_KEPT = 500
UPSTREAM_TIMEOUT = 60.0


class Client:
    """One caller: a token, and the tool names it may reach."""

    def __init__(self, name: str, token: str, tools: tuple[str, ...]) -> None:
        self.name, self.token, self.tools = name, token, tools

    def permits(self, tool: str) -> bool:
        # Case-sensitive globs. `chat.list_*` admits a family; a bare name
        # admits one tool. Nothing admits everything unless somebody writes `*`.
        return any(fnmatch.fnmatchcase(tool, rule) for rule in self.tools)


def load_clients(environ: dict[str, str]) -> list[Client]:
    """Every `MCPGATE_CLIENT_<NAME>_TOKEN` in the environment, with its list.

    Config in the environment rather than a file because the tokens belong in
    the deployment's `.env` with every other secret, and because a config file
    mounted into this container would be one more thing to keep in step with the
    compose that already names the clients.
    """
    clients: list[Client] = []
    for key in sorted(environ):
        if not (key.startswith("MCPGATE_CLIENT_") and key.endswith("_TOKEN")):
            continue
        name = key[len("MCPGATE_CLIENT_") : -len("_TOKEN")].lower()
        token = environ[key].strip()
        listed = environ.get(f"MCPGATE_CLIENT_{name.upper()}_TOOLS", "")
        tools = tuple(t.strip() for t in listed.split(",") if t.strip())
        if len(token) < 16:
            raise SystemExit(f"mcp-gate: {name}'s token is shorter than 16 characters, or empty")
        if not tools:
            # An empty list would mean "this client may call nothing", which is
            # indistinguishable from a variable that failed to interpolate. Say so.
            raise SystemExit(f"mcp-gate: {name} has a token and no tools; set MCPGATE_CLIENT_{name.upper()}_TOOLS")
        clients.append(Client(name, token, tools))
    if not clients:
        raise SystemExit("mcp-gate: no clients configured; set MCPGATE_CLIENT_<NAME>_TOKEN and _TOOLS")
    if len({c.token for c in clients}) != len(clients):
        # Two clients on one token cannot be told apart in the ledger, and the
        # narrower list silently becomes the wider one.
        raise SystemExit("mcp-gate: two clients share a token")
    return clients


def error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


class Ledger:
    """Append-only JSONL of every call, permitted or not.

    The refusals are the point: they are the only place that can say "this run
    asked for a tool nobody gave it". Best-effort — a gateway that stops
    forwarding because a disk filled would take the watcher down with it.
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


def permitted_names(payload: Any, allowed: Client) -> Any:
    """A `tools/list` answer, with the tools this client cannot call removed.

    Cosmetic by itself — `tools/call` is where the refusal happens — and worth
    doing anyway: an agent shown a tool it cannot use spends a turn discovering
    that, and a model told "you have send_message" is a model that will try.
    """
    if not isinstance(payload, dict):
        return payload
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("tools"), list):
        result["tools"] = [
            tool for tool in result["tools"] if isinstance(tool, dict) and allowed.permits(str(tool.get("name") or ""))
        ]
    return payload


def filter_body(body: bytes, listings: set[str], client: Client) -> bytes:
    """Apply `permitted_names` to whichever answers were `tools/list` asks.

    Two shapes, because the transport has two: a JSON body, or an SSE stream
    whose `data:` lines each carry one JSON-RPC message.
    """
    if not listings:
        return body
    text = body.decode("utf-8", "replace")
    if text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except ValueError:
            return body
        items = parsed if isinstance(parsed, list) else [parsed]
        out = [permitted_names(item, client) if str(item.get("id")) in listings else item for item in items]
        return json.dumps(out if isinstance(parsed, list) else out[0], ensure_ascii=False).encode()
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[5:].strip()
            try:
                parsed = json.loads(payload)
            except ValueError:
                lines.append(line)
                continue
            if str(parsed.get("id")) in listings:
                parsed = permitted_names(parsed, client)
            lines.append("data: " + json.dumps(parsed, ensure_ascii=False) + "\n")
        else:
            lines.append(line)
    return "".join(lines).encode()


class Gate(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mcp-gate"

    # Set by main().
    upstream = ""
    upstream_headers: dict[str, str] = {}  # noqa: RUF012 — plain class config, not a dataclass field
    clients: list[Client] = []  # noqa: RUF012
    ledger = Ledger("")

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s", fmt % args)

    # ── who is asking ──
    def _caller(self) -> Client | None:
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        found = None
        for client in self.clients:
            # Every client is compared, and the comparison is constant time: a
            # loop that returns early leaks which prefix was right.
            if hmac.compare_digest(token, client.token):
                found = client
        return found if token else None

    def _answer(self, status: int, body: bytes, content_type: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path == "/healthz":
            # No token: it says the gate is up and nothing about who may use it.
            self._answer(200, b'{"ok":true}')
            return
        # The server-to-client stream, declined rather than relayed — see the
        # module docstring. Recorded, because a client that keeps asking is
        # worth seeing.
        client = self._caller()
        self.ledger.write(event="stream.declined", client=client.name if client else None)
        self._answer(405, b'{"error":"this gateway has no server-to-client stream; use POST"}')

    def do_DELETE(self) -> None:  # noqa: N802
        self._relay(b"", method="DELETE")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        client = self._caller()
        if client is None:
            self.ledger.write(event="call.unauthorized", path=self.path[:80])
            self._answer(401, b'{"error":"a bearer token this gateway knows is required"}')
            return
        try:
            message = json.loads(body or b"null")
        except ValueError:
            self._answer(400, b'{"error":"not JSON-RPC"}')
            return
        items = message if isinstance(message, list) else [message]
        refused: dict[str, dict[str, Any]] = {}
        listings: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            method = str(item.get("method") or "")
            rid = str(item.get("id"))
            if method.startswith("notifications/"):
                continue
            if method == "tools/call":
                params = item.get("params") if isinstance(item.get("params"), dict) else {}
                tool = str(params.get("name") or "")
                call = {
                    "client": client.name,
                    "tool": tool,
                    "arguments": json.dumps(params.get("arguments"), ensure_ascii=False)[:ARGUMENTS_KEPT],
                }
                if client.permits(tool):
                    self.ledger.write(event="call.forwarded", **call)
                else:
                    self.ledger.write(event="call.refused", **call)
                    refused[rid] = error(item.get("id"), -32602, f"{tool} is not a tool this gateway forwards")
            elif method not in PASSED:
                refused[rid] = error(item.get("id"), -32601, f"{method} is not forwarded by this gateway")
            elif method == "tools/list":
                listings.add(rid)
        if refused:
            # Nothing in a batch that asks for a refused thing is forwarded: a
            # gateway that forwarded the rest would let a caller learn which
            # half it got away with.
            answers = [
                refused.get(str(item.get("id")))
                or error(item.get("id"), -32600, "not forwarded: sent with a refused request")
                for item in items
                if isinstance(item, dict) and not str(item.get("method") or "").startswith("notifications/")
            ]
            payload = answers if isinstance(message, list) else (answers[0] if answers else {})
            self._answer(200, json.dumps(payload, ensure_ascii=False).encode())
            return
        self._relay(body, method="POST", listings=listings, client=client)

    def _relay(self, body: bytes, *, method: str, listings: set[str] | None = None, client: Client | None = None):
        if client is None:
            client = self._caller()
            if client is None:
                self._answer(401, b'{"error":"a bearer token this gateway knows is required"}')
                return
        headers = {name: value for name in FORWARDED if (value := self.headers.get(name))}
        headers.update(self.upstream_headers)
        request = urllib.request.Request(self.upstream, data=body or None, headers=headers, method=method)
        # No proxy handler and no redirects: this gate reaches exactly the one
        # address it was configured with, or it fails.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=UPSTREAM_TIMEOUT) as answer:
                raw, status = answer.read(), answer.status
                content_type = answer.headers.get("Content-Type", "application/json")
                session = answer.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as exc:
            raw, status, session = exc.read(), exc.code, None
            content_type = exc.headers.get("Content-Type", "text/plain")
        except OSError as exc:
            self.ledger.write(event="call.failed", client=client.name, reason=str(exc)[:200])
            self._answer(502, b'{"error":"the chat server did not answer"}')
            return
        out = filter_body(raw, listings or set(), client)
        self._answer(status, out, content_type, {"Mcp-Session-Id": session} if session else None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        return None


def main() -> int:
    upstream = os.environ.get("MCPGATE_UPSTREAM", "").strip()
    if not upstream.startswith(("http://", "https://")):
        raise SystemExit("mcp-gate: set MCPGATE_UPSTREAM to the chat server's MCP URL")
    Gate.upstream = upstream
    Gate.clients = load_clients(dict(os.environ))
    Gate.upstream_headers = {
        name.split("MCPGATE_UPSTREAM_HEADER_", 1)[1].replace("_", "-"): value
        for name, value in os.environ.items()
        if name.startswith("MCPGATE_UPSTREAM_HEADER_")
    }
    Gate.ledger = Ledger(os.environ.get("MCPGATE_LEDGER", "/data/calls.jsonl"))
    port = int(os.environ.get("MCPGATE_PORT", "8097"))
    logger.info(
        "up on :%d for %s, %d client(s): %s",
        port,
        upstream.split("://", 1)[1].split("/", 1)[0],
        len(Gate.clients),
        ", ".join(f"{c.name}({len(c.tools)})" for c in Gate.clients),
    )
    ThreadingHTTPServer(("0.0.0.0", port), Gate).serve_forever()  # noqa: S104 — the container's own network only
    return 0


if __name__ == "__main__":
    sys.exit(main())
