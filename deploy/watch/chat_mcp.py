#!/usr/bin/env python3
"""聊天 MCP 的最小客户端：一次 initialize，之后直接 tools/call，中间不放 agent。

为什么不经过 agent：这几个调用没有一处需要判断。列会话、比时间戳、按窗口取
消息，都是确定的动作。让模型代跑一遍，代价不是 token，是「没有新消息的那一轮
本该不花钱」——它也得把整个扫描走完才知道没事。

三件试出来的事，都在这里一次性挡掉：

1. 服务端用 SSE 回包（`event: message` + `data: {...}`），不是裸 JSON。直接
   json.loads 会在第一个字符上炸。
2. 真正的工具名是 `<prefix>.<tool>`。`mcp__<prefix>__<prefix>_xxx` 那种双下划线形式
   是 Claude 侧加的，服务端不认，回的是一句 `MCP error -32602: Tool ... not
   found`——而且它放在 result 里，不是 JSON-RPC 的 error 里，所以只看 error
   会把「工具不存在」当成调用成功。
3. 结构化结果在 `structuredContent`，`content[0].text` 只是给人看的摘要。
   search_chat_records 的那句摘要只有 "Found 6 message(s)."，消息体一条都不在
   里面。只认前者。

另外两件，2026-10-12 从另一处跑着这份客户端的部署带回来：

4. 服务端在 initialize 的回包里给了 Mcp-Session-Id 的话，之后每次调用都带上。
   协议要求如此；直连聊天客户端时它不给，走网关时网关按它找上游的会话。
5. HTTP 错误把回包正文带进错误里。网关拒绝一个工具时，原因就写在正文里（哪个
   工具不在名单上）；只有状态码的话，盯守群里看到的是一串 403 和一个猜测。

prefix 走环境变量而不是写死：这个聊天工具的名字是公司内部的，和别的名字守同
一条规矩（见 scripts/assert_no_estate_identifiers.py）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

URL = os.environ.get("CHAT_MCP_URL") or "http://host.docker.internal:52222/mcp/"
PREFIX = (os.environ.get("CHAT_TOOL_PREFIX") or "").strip()
TIMEOUT = float(os.environ.get("CHAT_MCP_TIMEOUT") or 60)


class McpError(RuntimeError):
    """取不到就是取不到。

    调用方必须把它当成「这个源不可达」，而不是「这个源很安静」——两者长得一样，
    只有一个是缺陷，而这整套东西就是为了分开这两件事。
    """


class Chat:
    """一个进程一个会话。initialize 只做一次，懒执行——窗口外的那一轮连它都不发。

    地址、前缀、超时默认取上面的环境变量；测试时传参，不碰环境。
    """

    def __init__(self, url: str = "", prefix: str | None = None, timeout: float | None = None) -> None:
        self.url = url or URL
        self.prefix = PREFIX if prefix is None else prefix.strip()
        self.timeout = TIMEOUT if timeout is None else timeout
        self._id = 0
        self._ready = False
        self._session = ""

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(self.url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                self._session = resp.headers.get("Mcp-Session-Id") or self._session
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace").strip() if exc.fp else ""
            raise McpError(f"HTTP {exc.code}: {detail[:160] or exc.reason}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise McpError(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
        # SSE 一帧可能有多行；要的是 `data: ` 那行。回退到 raw 是为了万一哪天服务端
        # 改回裸 JSON，这里不用跟着改。
        line = next((ln[6:] for ln in raw.splitlines() if ln.startswith("data: ")), raw)
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpError(f"回包不是 JSON：{raw[:160]!r}") from exc

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        if not self._ready:
            self._rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "watch-scan", "version": "1"},
                },
            )
            self._ready = True

        name = f"{self.prefix}.{tool}" if self.prefix else tool
        d = self._rpc("tools/call", {"name": name, "arguments": args})
        if "error" in d:
            raise McpError(f"{tool}: {json.dumps(d['error'], ensure_ascii=False)[:200]}")

        result = d.get("result") or {}
        text = str(((result.get("content") or [{}])[0]).get("text") or "")
        # 两个失败面：协议级的 isError，和上面第 2 点那种「200 里装着一句错误」。
        if result.get("isError") or text.startswith("MCP error"):
            raise McpError(f"{tool}: {text[:200]}")

        structured = result.get("structuredContent")
        if structured is None:
            raise McpError(f"{tool}: 没有 structuredContent，只有摘要 {text[:120]!r}")
        return structured
