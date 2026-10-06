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
tool call the family makes. The plumbing the sidecars share is in common.py.

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
    A response the client posts back to a request the server made inside a
    POST's own stream (a message with `result` or `error` and no `method`)
    carries no call either, and is passed as it is.
  * Not a stream in either direction, then: an answer is read whole before it is
    passed on, and an answer larger than ANSWER_MAX is reported as a failure
    rather than buffered. The chat server closes its SSE answer at the end of
    each POST, so that is one read; UPSTREAM_TIMEOUT is an idle timeout, per
    socket operation, so a server that keeps sending is never cut and one that
    goes silent that long is reported as a failure rather than hanging forever.
  * Not an audit of what the chat server did: `call.forwarded` is written after
    the relay answered and `call.failed` when it did not, so a row says what
    reached the server, not what this gate meant to send.
"""

from __future__ import annotations

import fnmatch
import http.client
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

from common import HttpHandler, Ledger, constant_time_eq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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
# Idle, per socket operation — see the module docstring.
UPSTREAM_TIMEOUT = 60.0
# A JSON-RPC request is a few KB and a listing of 34 tools is a few tens; this
# container has 128 MB, and a body is held about three times over while it is
# filtered. Past either bound nothing here should buffer it.
BODY_MAX = 1024 * 1024
ANSWER_MAX = 8 * 1024 * 1024


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
        if not token.isascii():
            # A header arrives as latin-1 and is compared as bytes: a token with
            # a non-ASCII character could never match, and every call a 401.
            raise SystemExit(f"mcp-gate: {name}'s token must be ASCII")
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


def _filtered(item: Any, listings: set[str], client: Client) -> Any:
    """One JSON-RPC message, filtered when it answers a listing this client asked
    for and passed through otherwise — including when it is not an object at all,
    which a `null` keepalive frame or a batch is."""
    if isinstance(item, dict) and str(item.get("id")) in listings:
        return permitted_names(item, client)
    return item


def _dumps(obj: Any) -> str:
    """JSON as the client will decode it. A lone surrogate the chat server sent
    escaped cannot be encoded raw, so that one case falls back to ASCII escapes
    rather than dropping the whole answer."""
    text = json.dumps(obj, ensure_ascii=False)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(obj, ensure_ascii=True)
    return text


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
        if isinstance(parsed, list):
            return _dumps([_filtered(item, listings, client) for item in parsed]).encode("utf-8")
        return _dumps(_filtered(parsed, listings, client)).encode("utf-8")
    # Split on "\n" alone. str.splitlines also breaks on U+2028 and its kin,
    # which a tool description may carry, and a message split in two is a
    # message the filter never sees — a write tool would stay advertised.
    lines = text.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        try:
            parsed = json.loads(stripped[5:].strip())
        except ValueError:
            continue
        lines[index] = "data: " + _dumps(_filtered(parsed, listings, client))
    return "\n".join(lines).encode("utf-8")


class Gate(HttpHandler):
    server_version = "mcp-gate"

    # Set by main().
    upstream = ""
    upstream_headers: dict[str, str] = {}  # noqa: RUF012 — plain class config, not a dataclass field
    clients: list[Client] = []  # noqa: RUF012
    ledger = Ledger("")

    # ── who is asking ──
    def _caller(self) -> Client | None:
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        found = None
        for client in self.clients:
            # Every client is compared, and the comparison is constant time: a
            # loop that returns early leaks which prefix was right.
            if constant_time_eq(client.token, token):
                found = client
        return found if token else None

    def _authorized(self) -> Client | None:
        """The caller, or None with the 401 already answered and recorded: one
        prelude for every method, so no path reaches the chat server without a
        token and no refusal is missing from the ledger."""
        client = self._caller()
        if client is None:
            self.ledger.write(event="call.unauthorized", method=self.command, path=self.path[:80])
            self._refuse(401, b'{"error":"a bearer token this gateway knows is required"}')
        return client

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
        client = self._authorized()
        if client is None:
            return
        self._relay(b"", method="DELETE", client=client)

    def do_POST(self) -> None:  # noqa: N802
        # The token first, then the length, then the body: nothing is read or
        # held for a caller this gate does not know, and a declared length that
        # is not a number, negative, or past BODY_MAX is answered, not parsed.
        client = self._authorized()
        if client is None:
            return
        length = self.body_length(BODY_MAX, b'{"error":"a request this large is not a tool call"}')
        if length is None:
            return
        body = self.rfile.read(length) if length else b""
        try:
            message = json.loads(body or b"null")
        except ValueError:
            self._answer(400, b'{"error":"not JSON-RPC"}')
            return
        items = message if isinstance(message, list) else [message]
        refused: dict[str, dict[str, Any]] = {}
        listings: set[str] = set()
        permitted: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or "method" not in item:
                # A response to a request the chat server made (result or error,
                # no method), which the client posts back. It carries no call.
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
                    permitted.append(call)
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
            # half it got away with. The permitted half is recorded as withheld,
            # not as forwarded — it never reached the server.
            for call in permitted:
                self.ledger.write(event="call.withheld", **call)
            answers = [
                refused.get(str(item.get("id")))
                or error(item.get("id"), -32600, "not forwarded: sent with a refused request")
                for item in items
                if isinstance(item, dict)
                and "method" in item
                and not str(item.get("method") or "").startswith("notifications/")
            ]
            payload = answers if isinstance(message, list) else (answers[0] if answers else {})
            self._answer(200, json.dumps(payload, ensure_ascii=False).encode())
            return
        self._relay(body, method="POST", client=client, listings=listings, calls=permitted)

    def _relay(
        self,
        body: bytes,
        *,
        method: str,
        client: Client,
        listings: set[str] | None = None,
        calls: list[dict[str, Any]] | None = None,
    ) -> None:
        calls = calls or []
        headers = {name: value for name in FORWARDED if (value := self.headers.get(name))}
        headers.update(self.upstream_headers)
        request = urllib.request.Request(self.upstream, data=body or None, headers=headers, method=method)
        # No proxy handler and no redirects: this gate reaches exactly the one
        # address it was configured with, or it fails.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

        def failed(reason: str) -> None:
            self.ledger.write(
                event="call.failed", client=client.name, method=method, tools=[c["tool"] for c in calls], reason=reason
            )
            self._answer(502, b'{"error":"the chat server did not answer"}')

        try:
            with opener.open(request, timeout=UPSTREAM_TIMEOUT) as answer:
                raw, status = answer.read(ANSWER_MAX + 1), answer.status
                content_type = answer.headers.get("Content-Type", "application/json")
                session = answer.headers.get("Mcp-Session-Id")
                declared = answer.headers.get("Content-Length", "")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(ANSWER_MAX + 1)
            except (OSError, http.client.HTTPException):
                raw = b""
            status, session = exc.code, None
            content_type = exc.headers.get("Content-Type", "text/plain")
            declared = exc.headers.get("Content-Length", "")
        except (OSError, http.client.HTTPException) as exc:
            # HTTPException is not an OSError: a chunked answer cut mid-chunk is
            # this branch, not a crash with no answer and no row.
            failed(str(exc)[:200])
            return
        if len(raw) > ANSWER_MAX:
            failed(f"the answer is larger than {ANSWER_MAX} bytes")
            return
        if declared.strip().isdigit() and len(raw) < int(declared):
            # A bounded read returns what arrived and raises nothing when the
            # server closes early; the declared length is what says it did.
            failed("the chat server cut its answer short")
            return
        try:
            out = filter_body(raw, listings or set(), client)
        except Exception as exc:  # noqa: BLE001 — a body the filter cannot read must still get an answer
            failed(f"the answer could not be filtered: {type(exc).__name__}")
            return
        for call in calls:
            self.ledger.write(event="call.forwarded", status=status, **call)
        self._answer(status, out, content_type, {"Mcp-Session-Id": session} if session else None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        return None


def build() -> ThreadingHTTPServer:
    """The gate, configured from the environment and bound, not yet serving:
    doors.py runs it beside the proxy and the ingress; main() runs it alone."""
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
    return ThreadingHTTPServer(("0.0.0.0", port), Gate)  # noqa: S104 — the container's own network only


def main() -> int:
    build().serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
