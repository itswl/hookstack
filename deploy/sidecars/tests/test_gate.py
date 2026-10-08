"""What reaches the chat server through this gate, and what does not.

The list is the whole product, so every test here is a way it could be wrong
while looking right: a write tool admitted by a careless glob, a refusal that
still forwarded the rest of its batch, a `tools/list` that advertises what
`tools/call` would refuse, a config that failed to interpolate and opened the
door instead of shutting it, a ledger row that says "forwarded" about a call the
server never saw, a bearer with one byte the comparison could not read, a
declared body length that was never going to arrive.

The upstream in these tests is a real HTTP server on a loopback port that
records what it was asked, so "the gate did not forward it" is asserted about
the server that would have run the tool.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gate as gate_module  # noqa: E402

WATCH_TOKEN = "watch-token-long-enough"
PLAN_TOKEN = "plan-token-long-enough"


class Upstream(BaseHTTPRequestHandler):
    seen: list[dict] = []  # noqa: RUF012
    answer: bytes = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    content_type = "application/json"
    # Declare a body and send only part of it: the shape of a chat server that
    # died mid-answer.
    truncate = False

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def _record(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen.append(
            {
                "method": method,
                "body": body.decode() if body else "",
                "auth": self.headers.get("Authorization"),
                "upstream_header": self.headers.get("X-Chat-Key"),
                "path": self.path,
                "headers": {name.lower(): value for name, value in self.headers.items()},
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.answer) + (100 if self.truncate else 0)))
        self.send_header("Mcp-Session-Id", "sess-1")
        self.end_headers()
        self.wfile.write(self.answer)
        if self.truncate:
            self.close_connection = True

    def do_POST(self) -> None:  # noqa: N802
        self._record("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._record("GET")

    def do_DELETE(self) -> None:  # noqa: N802
        self._record("DELETE")


@pytest.fixture
def stack(tmp_path):
    """A gate in front of a recording upstream, both on loopback, with a ledger."""
    Upstream.seen = []
    Upstream.answer = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    Upstream.content_type = "application/json"
    Upstream.truncate = False
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    gate_module.Gate.upstream = f"http://127.0.0.1:{upstream.server_port}/mcp/"
    gate_module.Gate.upstream_headers = {"X-Chat-Key": "the-credential"}
    gate_module.Gate.routes = {}
    gate_module.Gate.clients = [
        gate_module.Client("watch", WATCH_TOKEN, ("chat.list_*", "chat.search_chat_records")),
        gate_module.Client("plan", PLAN_TOKEN, ("chat.list_mentions",)),
    ]
    gate_module.Gate.ledger = gate_module.Ledger(str(tmp_path / "calls.jsonl"))
    gate = ThreadingHTTPServer(("127.0.0.1", 0), gate_module.Gate)
    threading.Thread(target=gate.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{gate.server_port}/"
    finally:
        gate.shutdown()
        upstream.shutdown()


def rows() -> list[dict]:
    path = gate_module.Gate.ledger.path
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def call(url: str, payload, token: str = WATCH_TOKEN, method: str = "POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as answer:
            return answer.status, json.loads(answer.read() or b"null"), dict(answer.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null"), dict(exc.headers)


def raw(url: str, head: str) -> bytes:
    """One request written by hand, for the headers urllib would not send."""
    host, port = url.split("//", 1)[1].split("/", 1)[0].split(":")
    chunks = []
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(head.encode("latin-1"))
        sock.settimeout(2)
        try:
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        except TimeoutError:
            pass
    return b"".join(chunks)


def _tool(name: str, rid: int = 1):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": {"q": "x"}}}


def test_a_listed_tool_is_forwarded_and_the_credential_is_the_gates(stack):
    status, body, headers = call(stack, _tool("chat.list_chat_folders"))
    assert status == 200 and body["result"] == {"ok": True}
    assert headers.get("Mcp-Session-Id") == "sess-1", "the session the chat server minted reaches the client"
    assert len(Upstream.seen) == 1
    sent = Upstream.seen[0]
    assert sent["upstream_header"] == "the-credential"
    assert sent["auth"] is None, "the client's token stops here and never reaches the chat server"
    assert sent["headers"]["user-agent"] == "mcp-gate", "not urllib's own, which Cloudflare refuses (error 1010)"
    forwarded = [r for r in rows() if r["event"] == "call.forwarded"]
    assert forwarded and forwarded[0]["tool"] == "chat.list_chat_folders" and forwarded[0]["status"] == 200


def test_a_write_tool_never_reaches_the_chat_server(stack):
    """The finding this gate was built for: `send_message` was reachable from
    the investigator's container with no credential at all (2026-09-30)."""
    status, body, _ = call(stack, _tool("chat.send_message"))
    assert status == 200 and body["error"]["code"] == -32602
    assert "send_message" in body["error"]["message"]
    assert Upstream.seen == [], "nothing was asked of the chat server"


def test_a_refused_request_takes_its_whole_batch_with_it_and_the_ledger_says_so(stack):
    batch = [_tool("chat.list_mentions", 1), _tool("chat.send_message", 2)]
    status, body, _ = call(stack, batch)
    assert status == 200 and isinstance(body, list) and len(body) == 2
    assert body[0]["error"]["code"] == -32600 and body[1]["error"]["code"] == -32602
    assert Upstream.seen == [], "the permitted half of a refused batch is not a consolation prize"
    # And the ledger does not claim the permitted half was forwarded: it was
    # withheld, and a row that said otherwise would be a row about a call the
    # chat server never saw.
    assert [(r["event"], r["tool"]) for r in rows()] == [
        ("call.refused", "chat.send_message"),
        ("call.withheld", "chat.list_mentions"),
    ]


def test_each_token_gets_its_own_list(stack):
    assert call(stack, _tool("chat.list_mentions"), token=PLAN_TOKEN)[1]["result"] == {"ok": True}
    refused = call(stack, _tool("chat.list_chat_folders"), token=PLAN_TOKEN)[1]
    assert refused["error"]["code"] == -32602, "the planner's list is not the watcher's"


def test_no_token_and_a_wrong_token_reach_nothing(stack):
    for token in ("", "not-the-token-but-long", "t\xf6ken-with-a-high-byte"):
        status, _, _ = call(stack, _tool("chat.list_mentions"), token=token)
        assert status == 401, token
    assert Upstream.seen == []
    # The third carries a byte the old comparison raised on: it used to kill the
    # handler with no answer and no row, which is the one place a probing agent
    # was meant to leave a trace.
    assert [r["event"] for r in rows()] == ["call.unauthorized"] * 3
    assert call(stack, None, token="", method="DELETE")[0] == 401
    assert rows()[-1]["method"] == "DELETE", "a teardown without a token is refused where the ledger can see it"


def test_a_method_this_gateway_does_not_know_is_refused(stack):
    status, body, _ = call(stack, {"jsonrpc": "2.0", "id": 9, "method": "roots/list"})
    assert status == 200 and body["error"]["code"] == -32601
    assert Upstream.seen == []
    # The handshake and the keepalive still pass.
    assert call(stack, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})[0] == 200
    assert len(Upstream.seen) == 1


def test_a_response_the_client_posts_back_is_passed_as_it_is(stack):
    """A message with `result` and no `method` answers a request the chat server
    made inside a stream; it carries no call, and refusing it (as "" is not a
    method) meant no server-to-client request could ever be answered."""
    status, body, _ = call(stack, {"jsonrpc": "2.0", "id": 5, "result": {"roots": []}})
    assert status == 200 and body == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert len(Upstream.seen) == 1 and '"roots"' in Upstream.seen[0]["body"]


def test_a_listing_advertises_only_what_a_call_would_be_allowed(stack):
    Upstream.answer = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "chat.list_mentions"}, {"name": "chat.send_message"}]},
        }
    ).encode()
    _, body, _ = call(stack, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert [t["name"] for t in body["result"]["tools"]] == ["chat.list_mentions"]


def test_a_listing_is_filtered_in_an_sse_answer_too(stack):
    Upstream.content_type = "text/event-stream"
    listing = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "chat.list_mentions"}, {"name": "chat.send_message", "description": "a b"}]},
    }
    # A keepalive frame that is not an object, a batch frame, and a description
    # carrying a line separator: each one used to drop the whole answer, or let
    # the write tool through unfiltered.
    Upstream.answer = (
        b"data: null\n\n"
        + b"event: message\ndata: "
        + json.dumps(listing, ensure_ascii=False).encode()
        + b"\n\n"
        + b'data: [{"jsonrpc":"2.0","method":"notifications/x"}]\n\n'
    )
    request = urllib.request.Request(
        stack,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {WATCH_TOKEN}"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as answer:
        text = answer.read().decode()
    assert "list_mentions" in text and "send_message" not in text
    assert text.startswith("data: null\n\nevent: message"), "still an SSE stream the client can parse"
    assert 'data: [{"jsonrpc": "2.0", "method": "notifications/x"}]' in text


def test_the_server_to_client_stream_is_declined_not_relayed(stack):
    """Relaying it would hold a thread and a socket open per session, for as
    long as the session lasts, to carry notifications nothing here reads. A
    client reads 405 as "no stream" and works POST-only."""
    status, body, _ = call(stack, None, method="GET")
    assert status == 405 and "POST" in body["error"]
    assert Upstream.seen == []


def test_health_needs_no_token_and_says_nothing(stack):
    request = urllib.request.Request(stack + "healthz")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as answer:
        assert json.loads(answer.read()) == {"ok": True}


def test_a_chat_server_that_is_not_there_is_a_502_not_a_hang(stack):
    gate_module.Gate.upstream = "http://127.0.0.1:1/mcp/"
    assert call(stack, _tool("chat.list_mentions"))[0] == 502
    failed = rows()[-1]
    assert failed["event"] == "call.failed" and failed["tools"] == ["chat.list_mentions"]
    assert not any(r["event"] == "call.forwarded" for r in rows()), "nothing was forwarded, so nothing says it was"


def test_a_chat_server_that_dies_mid_answer_is_a_502_not_a_dropped_socket(stack):
    """http.client.IncompleteRead is not an OSError. It used to escape the
    relay, kill the handler, and leave the client with a closed socket and the
    ledger with a `forwarded` row for a call that got no answer."""
    Upstream.truncate = True
    assert call(stack, _tool("chat.list_mentions"))[0] == 502
    assert rows()[-1]["event"] == "call.failed"


def test_a_body_that_cannot_be_a_call_is_refused_before_it_is_read(stack):
    head = f"POST / HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {WATCH_TOKEN}\r\nContent-Length: abc\r\n\r\n"
    assert raw(stack, head).startswith(b"HTTP/1.1 400")
    huge = f"POST / HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {WATCH_TOKEN}\r\nContent-Length: 99999999\r\n\r\n"
    answer = raw(stack, huge)
    assert answer.startswith(b"HTTP/1.1 413") and b"Content-Length" in answer
    # And none of it needs a token to be answered — or is read for one that is
    # missing: the 401 comes first, with the connection closed behind it.
    nobody = "POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999\r\n\r\n"
    assert raw(stack, nobody).startswith(b"HTTP/1.1 401")
    assert Upstream.seen == []


@pytest.mark.parametrize(
    ("environ", "why"),
    [
        ({}, "no clients at all"),
        ({"MCPGATE_CLIENT_WATCH_TOKEN": "short", "MCPGATE_CLIENT_WATCH_TOOLS": "a"}, "a token anyone could guess"),
        ({"MCPGATE_CLIENT_WATCH_TOKEN": WATCH_TOKEN}, "a variable that failed to interpolate into the tool list"),
        (
            {"MCPGATE_CLIENT_WATCH_TOKEN": "töken-long-enough-16", "MCPGATE_CLIENT_WATCH_TOOLS": "a"},
            "a token no header could ever match",
        ),
        (
            {
                "MCPGATE_CLIENT_A_TOKEN": WATCH_TOKEN,
                "MCPGATE_CLIENT_A_TOOLS": "x",
                "MCPGATE_CLIENT_B_TOKEN": WATCH_TOKEN,
                "MCPGATE_CLIENT_B_TOOLS": "y",
            },
            "two clients that cannot be told apart",
        ),
    ],
)
def test_a_config_that_would_open_the_door_stops_the_gate_instead(environ, why):
    with pytest.raises(SystemExit):
        gate_module.load_clients(environ)


def test_the_ledger_records_both_halves(tmp_path):
    ledger = gate_module.Ledger(str(tmp_path / "calls.jsonl"))
    ledger.write(event="call.forwarded", client="watch", tool="chat.list_mentions")
    ledger.write(event="call.refused", client="watch", tool="chat.send_message")
    rows_ = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows_] == ["call.forwarded", "call.refused"]
    assert all(isinstance(r["ts"], float) for r in rows_)
    # A ledger that cannot be written never stops a call.
    gate_module.Ledger(str(tmp_path / "calls.jsonl" / "nope")).write(event="call.forwarded")


# ── several MCP servers on one port ─────────────────────────────────────────


class ChatUpstream(Upstream):
    seen: list[dict] = []  # noqa: RUF012


class JiraUpstream(Upstream):
    seen: list[dict] = []  # noqa: RUF012


@pytest.fixture
def routed(tmp_path):
    """One gate, two recording MCP servers behind it, each on its own path with
    its own credential. The watcher may use both; the planner only the chat."""
    servers = []
    for upstream in (ChatUpstream, JiraUpstream):
        upstream.seen = []
        upstream.answer = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        upstream.content_type = "application/json"
        upstream.truncate = False
        server = ThreadingHTTPServer(("127.0.0.1", 0), upstream)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        servers.append(server)
    chat, jira = (f"http://127.0.0.1:{server.server_port}/mcp/" for server in servers)
    gate_module.Gate.routes = {
        "chat": gate_module.Route("chat", chat, {"X-Chat-Key": "chat-credential"}),
        "jira": gate_module.Route("jira", jira, {"Authorization": "Bearer jira-credential", "User-Agent": "jira-says"}),
    }
    gate_module.Gate.clients = [
        gate_module.Client("watch", WATCH_TOKEN, {"chat": ("chat.list_*",), "jira": ("jira.get_issue",)}),
        gate_module.Client("plan", PLAN_TOKEN, {"chat": ("chat.list_mentions",)}),
    ]
    gate_module.Gate.ledger = gate_module.Ledger(str(tmp_path / "calls.jsonl"))
    gate = ThreadingHTTPServer(("127.0.0.1", 0), gate_module.Gate)
    threading.Thread(target=gate.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{gate.server_port}/"
    finally:
        gate_module.Gate.routes = {}
        gate.shutdown()
        for server in servers:
            server.shutdown()


def _names(upstream: type[Upstream]) -> list[str]:
    return [json.loads(seen["body"])["params"]["name"] for seen in upstream.seen]


def test_one_port_reaches_each_mcp_server_by_its_path_with_its_own_credential(routed):
    assert call(routed + "chat/", _tool("chat.list_mentions"))[1]["result"] == {"ok": True}
    assert call(routed + "jira", _tool("jira.get_issue"))[1]["result"] == {"ok": True}
    assert _names(ChatUpstream) == ["chat.list_mentions"] and _names(JiraUpstream) == ["jira.get_issue"]
    chat, jira = ChatUpstream.seen[0]["headers"], JiraUpstream.seen[0]["headers"]
    assert chat.get("x-chat-key") == "chat-credential" and "authorization" not in chat
    assert jira.get("authorization") == "Bearer jira-credential", "the route's credential, never the client's token"
    assert "x-chat-key" not in jira, "one server's credential never reaches another"
    assert [(r["event"], r["route"]) for r in rows()] == [("call.forwarded", "chat"), ("call.forwarded", "jira")]


def test_a_tool_listed_on_one_route_is_refused_on_another(routed):
    status, body, _ = call(routed + "jira/", _tool("chat.list_mentions"))
    assert status == 200 and body["error"]["code"] == -32602
    assert ChatUpstream.seen == [] and JiraUpstream.seen == []
    refused = rows()[-1]
    assert (refused["event"], refused["route"], refused["tool"]) == ("call.refused", "jira", "chat.list_mentions")


def test_a_token_with_nothing_on_a_route_cannot_even_shake_hands_there(routed):
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert call(routed + "jira/", hello, token=PLAN_TOKEN)[0] == 403
    assert call(routed + "jira/", None, token=PLAN_TOKEN, method="DELETE")[0] == 403
    assert JiraUpstream.seen == []
    assert [(r["event"], r["known"]) for r in rows()] == [("route.refused", True)] * 2
    assert call(routed + "chat/", hello, token=PLAN_TOKEN)[0] == 200, "its own route still answers"


def test_a_path_that_names_no_route_reaches_nothing(routed):
    for path in ("", "mcp/", "nope/", "chatx/", "healthzz/"):
        assert call(routed + path, _tool("chat.list_mentions"))[0] == 404, path
    assert call(routed + "nope/", _tool("chat.list_mentions"), token="")[0] == 401, "the token is asked for first"
    assert ChatUpstream.seen == [] and JiraUpstream.seen == []


def test_the_rest_of_the_path_never_reaches_the_server(routed):
    """The path picks a route and stops there: each route reaches exactly the
    address it was configured with, so a caller cannot walk the server's tree."""
    call(routed + "chat/../../admin/tools?drop=1", _tool("chat.list_mentions"))
    assert [seen["path"] for seen in ChatUpstream.seen] == ["/mcp/"]


def test_a_listing_on_a_route_shows_only_what_that_route_permits(routed):
    tools = [{"name": "jira.get_issue"}, {"name": "jira.create_issue"}, {"name": "chat.list_mentions"}]
    JiraUpstream.answer = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"tools": tools}}).encode()
    _, body, _ = call(routed + "jira/", {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert [tool["name"] for tool in body["result"]["tools"]] == ["jira.get_issue"]


def test_a_routes_own_user_agent_wins_and_the_default_is_the_gates(routed):
    call(routed + "chat/", _tool("chat.list_mentions"))
    call(routed + "jira/", _tool("jira.get_issue"))
    assert ChatUpstream.seen[0]["headers"]["user-agent"] == "mcp-gate"
    assert JiraUpstream.seen[0]["headers"]["user-agent"] == "jira-says"


@pytest.mark.parametrize(
    ("environ", "why"),
    [
        ({"MCPGATE_ROUTE_JIRA_SERVER": "http://j/"}, "a name the variable cannot carry unambiguously"),
        ({"MCPGATE_ROUTE_HEALTHZ": "http://j/"}, "the gate's own path"),
        ({"MCPGATE_ROUTE_JIRA": "ftp://j/"}, "not an MCP server's URL"),
        (
            {"MCPGATE_ROUTE_JIRA": "http://j/", "MCPGATE_ROUTE_JIRO_HEADER_Authorization": "Bearer k"},
            "a credential for a route that is not there",
        ),
    ],
)
def test_a_route_config_that_could_misroute_stops_the_gate(environ, why):
    with pytest.raises(SystemExit):
        gate_module.load_routes(environ)


@pytest.mark.parametrize(
    ("tools", "why"),
    [
        ("chat.list_mentions", "a bare name, which could be meant for either server"),
        ("jira:", "a route with no tool"),
        ("jirra:jira.get_issue", "a route that is not configured"),
    ],
)
def test_with_several_routes_every_listed_tool_names_its_route(tools, why):
    routes = gate_module.load_routes({"MCPGATE_ROUTE_CHAT": "http://c/", "MCPGATE_ROUTE_JIRA": "http://j/"})
    with pytest.raises(SystemExit):
        gate_module.load_clients(
            {"MCPGATE_CLIENT_WATCH_TOKEN": WATCH_TOKEN, "MCPGATE_CLIENT_WATCH_TOOLS": tools},
            routes,
        )


def test_routes_and_their_lists_come_from_the_environment(monkeypatch, tmp_path):
    for key in [key for key in os.environ if key.startswith("MCPGATE_")]:
        monkeypatch.delenv(key)
    for key, value in {
        "MCPGATE_PORT": "0",
        "MCPGATE_LEDGER": str(tmp_path / "calls.jsonl"),
        "MCPGATE_ROUTE_CHAT": "http://host.docker.internal:52222/mcp/",
        "MCPGATE_ROUTE_WIKI": "https://wiki.example/mcp",
        "MCPGATE_ROUTE_WIKI_HEADER_Authorization": "Bearer wiki-key",
        "MCPGATE_CLIENT_WATCH_TOKEN": WATCH_TOKEN,
        "MCPGATE_CLIENT_WATCH_TOOLS": "chat:chat.list_*, wiki:wiki.search",
    }.items():
        monkeypatch.setenv(key, value)
    server = gate_module.build()
    try:
        routes = gate_module.Gate.routes
        assert sorted(routes) == ["chat", "wiki"]
        assert routes["wiki"].headers == {"Authorization": "Bearer wiki-key"} and routes["chat"].headers == {}
        assert gate_module.Gate.clients[0].tools == {"chat": ("chat.list_*",), "wiki": ("wiki.search",)}
    finally:
        server.server_close()
        gate_module.Gate.routes = {}


def test_one_upstream_and_several_do_not_mix(monkeypatch):
    for key in [key for key in os.environ if key.startswith("MCPGATE_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("MCPGATE_UPSTREAM", "http://c/")
    monkeypatch.setenv("MCPGATE_ROUTE_JIRA", "http://j/")
    monkeypatch.setenv("MCPGATE_CLIENT_WATCH_TOKEN", WATCH_TOKEN)
    monkeypatch.setenv("MCPGATE_CLIENT_WATCH_TOOLS", "jira:jira.get_issue")
    with pytest.raises(SystemExit):
        gate_module.build()
