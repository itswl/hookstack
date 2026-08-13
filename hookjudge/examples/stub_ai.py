"""An OpenAI-compatible endpoint that answers without a key or a bill.

Threaded: the brain may judge several events at once, and a single-threaded
stand-in turns that into timeouts that look like model failures.

Enabled with `--profile stub-ai`. It exists so the PAID routes can be
demonstrated: with no model configured every event lands on the rule floor and
`reuse` never happens, which hides the half of the cost policy that matters.
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERDICTS = {
    "gateway": {
        "summary": "Payment gateway 5xx reached 8.1% over five minutes, mostly upstream timeouts; checkout success is already affected",
        "importance": "critical",
        "event_type": "business",
        "impact_scope": "checkout and top-up paths, all users",
    },
    "disk": {
        "summary": "node-3 /var has 7% free and fills in about four hours, after which container logging fails",
        "importance": "high",
        "event_type": "infrastructure",
        "impact_scope": "every workload on node-3",
    },
    "top-up": {
        "summary": "Three large top-ups from one account within ten minutes, 2600 in total",
        "importance": "high",
        "event_type": "business",
        "impact_scope": "notification only, no service impact seen",
    },
}
DEFAULT = {
    "summary": "the stub matched no canned verdict; treating this as medium",
    "importance": "medium",
    "event_type": "",
    "impact_scope": "unknown",
}


def _fallback(prompt: str) -> dict:
    """Echo the inbound level instead of inventing a downgrade.

    The judge's prompt carries the event as JSON, level included. A stand-in
    that answers "medium" to an alert that arrived as high is demonstrating a
    silent downgrade nobody chose — an investigator's report called that out
    in a live run. Unknown levels still land on medium.
    """
    match = re.search(r'"level":\s*"([a-z]+)"', prompt)
    level = {"warning": "medium"}.get(match.group(1), match.group(1)) if match else ""
    if level in ("critical", "high", "medium", "low"):
        return {
            "summary": f"the stub matched no canned verdict; keeping the inbound level {level}",
            "importance": level,
            "event_type": "",
            "impact_scope": "unknown",
        }
    return DEFAULT


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        # The USER message only. The system prompt names payments and top-ups as
        # severity guidance, so matching against the whole conversation made
        # every alert — including a disk alert — come back as the payment
        # gateway verdict.
        prompt = " ".join(
            str(m.get("content") or "") for m in request.get("messages") or [] if m.get("role") == "user"
        ).lower()

        verdict = next((v for k, v in VERDICTS.items() if k in prompt), None) or _fallback(prompt)
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
ThreadingHTTPServer(("0.0.0.0", 8300), Handler).serve_forever()  # noqa: S104  # nosec B104
