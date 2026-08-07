"""An OpenAI-compatible endpoint that answers without a key or a bill.

Threaded: the brain may judge several events at once, and a single-threaded
stand-in turns that into timeouts that look like model failures.

Enabled with `--profile stub-ai`. It exists so the PAID routes can be
demonstrated: with no model configured every event lands on the rule floor and
`reuse` never happens, which hides the half of the cost policy that matters.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERDICTS = {
    "支付": {
        "summary": "支付网关 5 分钟内 5xx 达 8.1%，多为上游超时，下单成功率已受影响",
        "importance": "critical",
        "event_type": "business",
        "impact_scope": "下单与充值链路，全量用户",
    },
    "磁盘": {
        "summary": "node-3 /var 剩余 7%，约 4 小时后写满，届时容器日志写入失败",
        "importance": "high",
        "event_type": "infrastructure",
        "impact_scope": "node-3 上的全部工作负载",
    },
    "充值": {
        "summary": "10 分钟内 3 笔大额充值集中在同一用户，合计 2600 元",
        "importance": "high",
        "event_type": "business",
        "impact_scope": "仅触发通知，未见服务影响",
    },
}
DEFAULT = {
    "summary": "桩模型未匹配到预设判断，按中等处理",
    "importance": "medium",
    "event_type": "",
    "impact_scope": "影响范围未知",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        # The USER message only. The system prompt names 支付 and 充值 as
        # severity guidance, so matching against the whole conversation made
        # every alert — including a disk alert — come back as the payment
        # gateway verdict.
        prompt = " ".join(
            str(m.get("content") or "") for m in request.get("messages") or [] if m.get("role") == "user"
        )

        verdict = next((v for k, v in VERDICTS.items() if k in prompt), DEFAULT)
        print(f"[stub-ai] {verdict['importance']:<8} {verdict['summary'][:48]}", flush=True)

        body = json.dumps(
            {
                "model": "stub-4o-mini",
                "choices": [{"message": {"content": json.dumps(verdict, ensure_ascii=False)}}],
                # Real-looking counts so the ledger's pricing is exercised.
                "usage": {"prompt_tokens": 1180, "completion_tokens": 142},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


print("stub-ai listening on :8300", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8300), Handler).serve_forever()
