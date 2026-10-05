#!/usr/bin/env python3
"""Helpers copied between services must not drift apart in silence.

Three services, each a self-contained deployable with its own image, its own
lock file and a weight ceiling on two of them. That is a deliberate choice and
it forbids the obvious fix: a shared package would give hookrelay and hookjudge
a build-order dependency and a fourth thing to version, to remove a hundred
lines of duplication from services whose whole point is to stay small enough to
read in one sitting. Copying is the cheaper trade.

The cost of copying is drift, and drift here is not hypothetical:

  * `Live.watcher_count` exists in two of the three copies. Harmless, and
    nobody chose it.
  * `verify_signature` stripped its timestamp in hookrelay and not in
    hookjudge, so a padded X-Hook-Timestamp verified at one door of the family
    and failed at the other. No sender in the family pads, so it waited.

So the copies that are meant to be one implementation are named here and
compared by their PARSED BODY: docstrings and comments are excluded, because
each copy should explain itself in its own context, and formatting is excluded
because ruff already owns that. What is pinned is behaviour.

A symbol listed here and missing from a service it is expected in is also a
failure — a helper that quietly stops existing in one copy is the same drift
arriving from the other direction.

    python3 scripts/assert_copies.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVICES = ("hookrelay", "hookjudge", "hookprobe")

# (symbol, module, services expected to carry an identical copy)
#
# Not every same-named symbol belongs here. app.py, store.py and settings.py
# share names across services and are supposed to differ — they are what each
# service IS. These are the utilities that happen to be copied.
PINNED = (
    # The one where drift is dangerous rather than untidy: three doors that must
    # agree on what a valid credential comparison is.
    ("constant_time_eq", {"hookrelay": "security.py", "hookjudge": "app.py", "hookprobe": "wire.py"}),
    # The SSE fan-out behind all three boards. `watcher_count` is deliberately
    # absent from hookprobe (see KNOWN_DIFFERENCES) so it is not pinned here.
    ("Live.__init__", {s: "live.py" for s in SERVICES}),
    ("Live.watch", {s: "live.py" for s in SERVICES}),
    ("Live.unwatch", {s: "live.py" for s in SERVICES}),
    ("Live.changed", {s: "live.py" for s in SERVICES}),
    ("Live.stream", {s: "live.py" for s in SERVICES}),
    # Env parsing. A service that reads a bad integer differently from its
    # sibling starts with a different config than the operator wrote.
    ("_int", {"hookrelay": "settings.py", "hookjudge": "settings.py", "hookprobe": "settings.py"}),
    # In live.py, not settings.py: it is the SSE framing, not an env parser.
    ("_line", {s: "live.py" for s in SERVICES}),
    ("_float", {"hookjudge": "settings.py", "hookprobe": "settings.py"}),
    ("now_ts", {"hookrelay": "store.py", "hookjudge": "store.py"}),
    ("SelfAlarm.__init__", {"hookrelay": "alarm.py", "hookjudge": "alarm.py"}),
    ("SelfAlarm.enabled", {"hookrelay": "alarm.py", "hookjudge": "alarm.py"}),
    # The deploy sidecars: single-file containers that mount one script each,
    # so a shared module is not even an option for them — a copy is the only
    # shape, and these are the copies where drift already cost something. The
    # two new doors compared their bearer with a bare compare_digest and died
    # on a non-ASCII byte, exactly what constant_time_eq above was written for.
    (
        "constant_time_eq",
        {
            "hookrelay": "security.py",
            "hookjudge": "app.py",
            "hookprobe": "wire.py",
            "mcp-gate": "deploy/mcp-gate/gate.py",
            "watch-signer": "deploy/watch-signer/signer.py",
        },
    ),
    ("sign_timestamped", {"hookprobe": "wire.py", "watch-signer": "deploy/watch-signer/signer.py"}),
    ("Ledger.__init__", {"mcp-gate": "deploy/mcp-gate/gate.py", "watch-signer": "deploy/watch-signer/signer.py"}),
    ("Ledger.write", {"mcp-gate": "deploy/mcp-gate/gate.py", "watch-signer": "deploy/watch-signer/signer.py"}),
    # The byte pump behind the egress proxy and the console ingress. Its
    # rbufsize lesson is recorded in the proxy; the second copy is pinned so the
    # next lesson lands in both.
    ("_pump", {"egress-proxy": "deploy/egress-proxy/proxy.py", "probe-ingress": "deploy/probe-ingress/forward.py"}),
)

# Copies that differ ON PURPOSE. Listed so the difference is a decision on the
# record, and so nobody "fixes" one back into the other.
KNOWN_DIFFERENCES = {
    "Live.watcher_count": (
        "hookrelay and hookjudge expose it on their status pages; hookprobe's "
        "console counts its own per-session watchers in service.py instead"
    ),
    "verify_signature": (
        "hookrelay carries require_timestamp and a configurable max_skew so a "
        "door can refuse the legacy body-only form during a migration; the "
        "judge's door is fed by the pipe over a private network and adding two "
        "knobs to a service under a weight ceiling buys nothing. The WIRE "
        "SCHEME is identical and that is the part that has to be"
    ),
    "IDLE_SECONDS": (
        "the egress proxy cuts an idle tunnel at 300 s; probe-ingress at 900 s, "
        "because a console page left open through a lunch break must come back "
        "without a reload, and the consoles ping every 20 s so a live tab is "
        "never idle. The _pump loop they share reads the constant, and the "
        "constant is the difference"
    ),
}


def bodies(symbol: str, where: dict[str, str]) -> dict[str, str | None]:
    """The parsed body of `symbol` in each service, or None if it is absent."""
    want_class, _, want_func = symbol.rpartition(".")
    found: dict[str, str | None] = {}
    for service, module in where.items():
        # A module naming a path (deploy/<sidecar>/<file>.py) is a sidecar
        # outside the three service packages; the rest resolve as before.
        path = Path(module) if "/" in module else Path(service, service, module)
        found[service] = None
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes: list[ast.AST] = [tree]
        if want_class:
            scopes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == want_class]
        for scope in scopes:
            for node in getattr(scope, "body", []):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == want_func:
                    body = node.body
                    # Drop the docstring: each copy earns its own explanation.
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                        body = body[1:]
                    found[service] = "\n".join(ast.unparse(stmt) for stmt in body)
    return found


def main() -> int:
    problems: list[str] = []
    for symbol, where in PINNED:
        found = bodies(symbol, where)
        missing = sorted(s for s, body in found.items() if body is None)
        if missing:
            problems.append(f"{symbol}: expected in {sorted(where)} but absent from {missing}")
            continue
        distinct = {body for body in found.values()}
        if len(distinct) > 1:
            groups: dict[str, list[str]] = {}
            for service, body in found.items():
                groups.setdefault(str(body), []).append(service)
            shape = " vs ".join("+".join(sorted(v)) for v in groups.values())
            problems.append(f"{symbol}: copies have drifted apart ({shape})")

    if problems:
        print("copies have drifted:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nThese are copies on purpose — three self-contained deployables, two under a\n"
            "weight ceiling — so the fix is to make them agree again, in every copy, not to\n"
            "extract a shared package. If the difference is deliberate, move the symbol from\n"
            "PINNED to KNOWN_DIFFERENCES in this file with the reason, so the next reader\n"
            "finds a decision instead of a surprise.",
            file=sys.stderr,
        )
        return 1

    print(
        f"copies: {len(PINNED)} pinned helpers identical across their services, "
        f"{len(KNOWN_DIFFERENCES)} differences on the record"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
