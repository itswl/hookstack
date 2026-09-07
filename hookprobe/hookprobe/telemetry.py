"""The run's own telemetry, received here instead of only somewhere else.

The bundled CLI emits one OpenTelemetry event per model call and per tool
result, with the durations, token counts and cost a run record cannot show
(see docs/cost.md). Until now the only place that shape was visible was an
external collector: no collector, no waterfall. This module makes the service
itself the CLI's OTLP endpoint — http/json, `POST /otel/v1/{logs,metrics}` —
and keeps a compact, per-run record under `{workdir}/telemetry/`, so the
sessions page can draw where a run spent its time with nothing deployed
beside it. When the operator HAS named a collector (OTEL_EXPORTER_OTLP_ENDPOINT
on the service), every body is forwarded there untouched as well: the local
view and the Grafana one are the same events, not two configurations.

Three rules, each with a reason:

  1. Only a run this service knows about may write telemetry, and only the CLI
     it launched may post it. Events are attributed by the `hookstack.session_key`
     resource attribute the engine stamps on every run, and the receiver takes
     nothing without the per-process header the engine hands the subprocess.
     Anything on the network that knows the port can otherwise invent a run's
     bill.
  2. What is kept is a timing record, not a transcript. Numbers and a short list
     of names survive; the free-text attributes the CLI can attach — prompts,
     tool content, responses, the account identity it stamps on every event —
     do not, whatever the content switches say. The forwarded copy is untouched;
     the operator chose those switches for the collector, not for a file that
     sits beside the case files and outlives them by a retention window.
  3. It never raises into a run. A malformed body, a full disk, a slow
     collector: logged, counted, and the investigation carries on. Telemetry
     that can fail an investigation is a liability, not an instrument.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.telemetry")

# One per process, handed to the CLI as an OTLP header and checked at the door.
# Not a setting: nobody needs to know it, and a value that lives only in memory
# cannot be read out of the environment by the agent's shell — the engine blanks
# the service's secrets from the subprocess for exactly that reason, and this
# one rides in OTEL_EXPORTER_OTLP_HEADERS instead, which the CLI's exporter
# consumes and a Bash step has no reason to print.
INGEST_HEADER = "x-hookstack-otel"
INGEST_TOKEN = secrets.token_urlsafe(24)
SIGNALS = ("logs", "metrics", "traces")

# Attribute keys whose STRING values are kept. Numbers and booleans are always
# kept; every other string is dropped — that is where prompt text, tool output
# and the CLI's account identity live.
_KEPT_STRINGS = frozenset(
    {
        "event.name",
        "event.sequence",
        "model",
        "tool_name",
        "decision",
        "source",
        "success",
        "terminal_reason",
        "stop_reason",
        "query_source",
        "effort",
        "error",
        "status_code",
        "session.id",
        "prompt.id",
        "tool_use_id",
        "type",
        "speed",
        "language",
    }
)
_MAX_STRING = 200
_MAX_LINES_PER_BODY = 2000


def enabled(settings: Settings) -> bool:
    return settings.telemetry_receiver == "on"


def subprocess_env(settings: Settings) -> dict[str, str]:
    """What points the CLI at this service instead of at nothing.

    http/json and the otlp exporters are forced rather than defaulted: the
    receiver speaks exactly that, and an operator's collector — if any — is
    reached by forwarding, not by the CLI. The header carries the process
    token; see INGEST_TOKEN for why it travels this way.
    """
    if not enabled(settings):
        return {}
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{settings.port}/otel",
        "OTEL_EXPORTER_OTLP_HEADERS": f"{INGEST_HEADER}={INGEST_TOKEN}",
    }


def authorized(header_value: str | None) -> bool:
    return bool(header_value) and secrets.compare_digest(str(header_value), INGEST_TOKEN)


# ── OTLP JSON → compact lines ─────────────────────────────────────────────────


def _any_value(value: Any) -> Any:
    """Decode an OTLP AnyValue. Ints arrive as strings on the wire."""
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return str(value["stringValue"])
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return None
    if "doubleValue" in value:
        try:
            return float(value["doubleValue"])
        except (TypeError, ValueError):
            return None
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "arrayValue" in value:
        return [_any_value(v) for v in (value["arrayValue"] or {}).get("values", [])]
    if "kvlistValue" in value:
        return {kv.get("key"): _any_value(kv.get("value")) for kv in (value["kvlistValue"] or {}).get("values", [])}
    return None


def _attributes(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        if not isinstance(item, dict) or "key" not in item:
            continue
        out[str(item["key"])] = _any_value(item.get("value"))
    return out


def _kept(attrs: dict[str, Any]) -> dict[str, Any]:
    """Rule 2: numbers, booleans, and the short list of names."""
    kept: dict[str, Any] = {}
    for key, value in attrs.items():
        if isinstance(value, bool | int | float):
            kept[key] = value
        elif isinstance(value, str) and key in _KEPT_STRINGS:
            kept[key] = value[:_MAX_STRING]
    return kept


def _seconds(nanos: Any, fallback_iso: Any = None) -> float:
    try:
        n = int(nanos)
        if n > 0:
            return n / 1e9
    except (TypeError, ValueError):
        pass
    if isinstance(fallback_iso, str) and fallback_iso:
        try:
            return datetime.fromisoformat(fallback_iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


def session_key_of(resource: Any) -> str:
    attrs = _attributes((resource or {}).get("attributes")) if isinstance(resource, dict) else {}
    return str(attrs.get("hookstack.session_key") or "")


def parse_logs(body: Any) -> dict[str, list[dict[str, Any]]]:
    """OTLP/JSON logs → {session_key: [compact event lines]}.

    The CLI names its events in `event.name` (`api_request`, `tool_result`,
    `tool_decision`, `user_prompt`, `api_error`) and repeats the fully
    qualified name in the body; the short one is kept.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(body, dict):
        return out
    for block in body.get("resourceLogs") or []:
        if not isinstance(block, dict):
            continue
        key = session_key_of(block.get("resource"))
        if not key:
            continue
        lines = out.setdefault(key, [])
        for scope in block.get("scopeLogs") or []:
            for rec in (scope or {}).get("logRecords") or []:
                if not isinstance(rec, dict):
                    continue
                attrs = _attributes(rec.get("attributes"))
                body_value = _any_value(rec.get("body"))
                name = str(attrs.get("event.name") or body_value or "").removeprefix("claude_code.")
                if not name:
                    continue
                lines.append(
                    {
                        "kind": "event",
                        "name": name[:80],
                        "ts": round(
                            _seconds(
                                rec.get("timeUnixNano") or rec.get("observedTimeUnixNano"), attrs.get("event.timestamp")
                            ),
                            3,
                        ),
                        "attrs": _kept({k: v for k, v in attrs.items() if k not in ("event.name", "event.timestamp")}),
                    }
                )
                if len(lines) >= _MAX_LINES_PER_BODY:
                    break
    return out


def parse_metrics(body: Any) -> dict[str, list[dict[str, Any]]]:
    """OTLP/JSON metrics → {session_key: [compact data points]}; sums and gauges only."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(body, dict):
        return out
    for block in body.get("resourceMetrics") or []:
        if not isinstance(block, dict):
            continue
        key = session_key_of(block.get("resource"))
        if not key:
            continue
        lines = out.setdefault(key, [])
        for scope in block.get("scopeMetrics") or []:
            for metric in (scope or {}).get("metrics") or []:
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("name") or "").removeprefix("claude_code.")
                points = ((metric.get("sum") or metric.get("gauge") or {}).get("dataPoints")) or []
                for point in points:
                    if not isinstance(point, dict):
                        continue
                    value = point.get("asDouble", point.get("asInt"))
                    if value is None:
                        continue
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    lines.append(
                        {
                            "kind": "metric",
                            "name": name[:80],
                            "ts": round(_seconds(point.get("timeUnixNano")), 3),
                            "value": number,
                            "attrs": _kept(_attributes(point.get("attributes"))),
                        }
                    )
                    if len(lines) >= _MAX_LINES_PER_BODY:
                        break
    return out


# ── the per-run file ──────────────────────────────────────────────────────────


def telemetry_dir(workdir: Path) -> Path:
    return workdir / "telemetry"


def path_for(workdir: Path, session_key: str) -> Path:
    # Same convention as results/: the session key is the file name. Keys are
    # the pipe's (`probe:inbound:31`) or a patrol's; a slash cannot occur in one
    # that the service accepted, and this guards the file system anyway.
    return telemetry_dir(workdir) / (session_key.replace("/", "_") + ".jsonl")


def append(workdir: Path, session_key: str, lines: list[dict[str, Any]]) -> int:
    """Append compact lines for one run. Never raises (rule 3)."""
    if not lines:
        return 0
    try:
        path = path_for(workdir, session_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(lines)
    except OSError:
        logger.debug("telemetry write failed for %s", session_key, exc_info=True)
        return 0


def read(workdir: Path, session_key: str) -> list[dict[str, Any]]:
    path = path_for(workdir, session_key)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines: list[dict[str, Any]] = []
    for text in raw.splitlines():
        if not text.strip():
            continue
        try:
            line = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict):
            lines.append(line)
    return lines


# ── the shape of a run ────────────────────────────────────────────────────────


def _number(value: Any) -> float:
    """A float from what the wire sent, or 0.0 — a bill line never raises."""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def summarize(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Waterfall items and totals from the compact lines.

    A model call ends at its event's timestamp and lasted `duration_ms`; so does
    a tool call (`tool_result`). Everything between the bars is the run
    thinking, queueing or waiting on the harness — the part no bill itemises,
    which is why it is worth drawing.
    """
    items: list[dict[str, Any]] = []
    models: dict[str, dict[str, Any]] = {}
    cost = 0.0
    model_ms = 0
    tool_ms = 0
    for line in lines:
        if line.get("kind") != "event":
            continue
        attrs = line.get("attrs") or {}
        name = str(line.get("name") or "")
        end = float(line.get("ts") or 0.0)
        duration = attrs.get("duration_ms")
        try:
            duration_ms = int(duration) if duration is not None else 0
        except (TypeError, ValueError):
            duration_ms = 0
        start = end - duration_ms / 1000.0
        if name == "api_request":
            model = str(attrs.get("model") or "model")
            item = {
                "kind": "model",
                "name": model,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_ms": duration_ms,
                "cost_usd": attrs.get("cost_usd"),
                "input_tokens": attrs.get("input_tokens"),
                "output_tokens": attrs.get("output_tokens"),
                "cache_read_tokens": attrs.get("cache_read_tokens"),
                "cache_creation_tokens": attrs.get("cache_creation_tokens"),
            }
            items.append(item)
            model_ms += duration_ms
            call_cost = _number(attrs.get("cost_usd"))
            cost += call_cost
            slot = models.setdefault(model, {"calls": 0, "ms": 0, "cost_usd": 0.0})
            slot["calls"] += 1
            slot["ms"] += duration_ms
            slot["cost_usd"] = round(slot["cost_usd"] + call_cost, 6)
        elif name == "tool_result":
            success = attrs.get("success")
            if isinstance(success, str):
                success = success.lower() == "true"
            items.append(
                {
                    "kind": "tool",
                    "name": str(attrs.get("tool_name") or "tool"),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_ms": duration_ms,
                    "success": success,
                }
            )
            tool_ms += duration_ms
    items.sort(key=lambda i: (i["start"], i["end"]))
    span = (max(i["end"] for i in items) - min(i["start"] for i in items)) if items else 0.0
    errors = sum(1 for line in lines if line.get("kind") == "event" and line.get("name") == "api_error")
    return {
        "waterfall": items,
        "summary": {
            "model_calls": sum(1 for i in items if i["kind"] == "model"),
            "model_ms": model_ms,
            "tool_calls": sum(1 for i in items if i["kind"] == "tool"),
            "tool_ms": tool_ms,
            "api_errors": errors,
            "cost_usd": round(cost, 6),
            "span_s": round(span, 3),
            "models": models,
            "events": sum(1 for line in lines if line.get("kind") == "event"),
            "metric_points": sum(1 for line in lines if line.get("kind") == "metric"),
        },
    }


# ── forwarding ────────────────────────────────────────────────────────────────


def collector() -> str:
    """The operator's collector, if they named one on the SERVICE's environment."""
    return (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip().rstrip("/")


def collector_headers() -> dict[str, str]:
    """OTEL_EXPORTER_OTLP_HEADERS as the spec writes it: `k=v,k2=v2`."""
    out: dict[str, str] = {}
    for part in (os.environ.get("OTEL_EXPORTER_OTLP_HEADERS") or "").split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def forward(signal: str, raw: bytes, endpoint: str, headers: dict[str, str]) -> bool:
    """One untouched OTLP body on to the collector. urllib on purpose — runtime
    code here gets the stdlib or a declared dependency, and httpx is neither
    (see RunService._post_ruling for the CI that taught that). False on any
    failure; the caller logs and moves on."""
    if not endpoint or signal not in SIGNALS:
        return False
    request = urllib.request.Request(  # noqa: S310 — operator URL  # nosec B310
        f"{endpoint}/v1/{signal}",
        data=raw,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310 — operator URL  # nosec B310
            return True
    except (urllib.error.URLError, OSError, ValueError):
        logger.debug("telemetry forward to %s failed", endpoint, exc_info=True)
        return False
