#!/usr/bin/env python3
"""Every deployed config's GRAPH holds, not just its schema.

`assert_shadow_config.py` answers "does this file boot". This answers the
question after it: does the shape it describes have the three defects that no
schema can see —

  wildcard_fallthrough  a door whose walk can reach a `source: "*"` route before
                        anything is guaranteed to stop it. Legitimate on a front
                        door; on a node's RETURN door it is the loop that feeds a
                        brain its own output. Four separate comments across two
                        config files exist to stop somebody re-introducing it,
                        which is exactly the sign that a comment was the wrong
                        mechanism.
  starved_exit          a channel no route and no card button reaches. The pipe's
                        version of the alert stack's "no starved brain": a node
                        configured, credentialed, and unreachable.
  unreachable_door      a source no route can match, so every event through it
                        ends no_route.

hookrelay's own `/topology` computes all three and REPORTS them, deliberately —
a config reload that could be refused is one nobody can iterate an orchestration
behind. A gate is the opposite situation: here is exactly where it should be
loud, and where a sanctioned exception costs a line and a reason.

    hookrelay/.venv/bin/python scripts/assert_topology.py [path ...]

Needs hookrelay's deps (yaml + the package), like assert_shadow_config.py, so
gate.sh calls it with the pipe's venv rather than the system interpreter.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Same two entries and the same reason as assert_shadow_config.py: hookrelay is
# a sibling package in a checkout and /app/hookrelay in the image.
sys.path[:0] = [str(ROOT / "hookrelay"), str(Path.cwd())]

import yaml  # noqa: E402

from hookrelay.config import Config, ConfigError  # noqa: E402
from hookrelay.topology import render  # noqa: E402

ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# A URL-shaped stand-in, because the pipe refuses an empty channel url at load
# (loudly, by design) and this check must run with no .env and no secret. The
# reserved .invalid TLD cannot resolve, so a placeholder that escaped into a
# request would fail rather than reach somebody.
PLACEHOLDER = "https://placeholder.invalid/"

DEFAULTS = (ROOT / "deploy" / "shadow.yaml", ROOT / "deploy" / "work.yaml")

# (config stem, warning kind, subject) -> why it is allowed to stand.
#
# A front door SHOULD be able to fall through to a wildcard — that is how
# `examples/stack.yaml` feeds every door to the brain — so a deployment that
# grows one puts it here with the reason, rather than the check growing a
# heuristic about which doors are "front" ones. This file cannot know that; the
# person adding the route can.
SANCTIONED: dict[tuple[str, str, str], str] = {}


def _placeholders(text: str) -> dict[str, str]:
    """Every ${NAME} the file itself references.

    Derived, never listed. The stack smoke kept a hand-written list of the names
    its composes required and it fell behind three times, each one turning main
    red until somebody appended another export — and a `${NAME}` in the file is
    already the declaration that the name is needed.
    """
    return {name: PLACEHOLDER for name in sorted(set(ENV_REF.findall(text)))}


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    seeded = _placeholders(text)
    # Forced, not defaulted: if this checkout happens to export the real
    # WATCH_FEISHU_WEBHOOK, a structural check has no business reading it and
    # its output has no business carrying it.
    saved = {name: os.environ.get(name) for name in seeded}
    os.environ.update(seeded)
    try:
        config = Config.from_dict(yaml.safe_load(text) or {})
    except (ConfigError, ValueError) as error:
        return [f"{path.name}: does not load — {error}"]
    finally:
        # Restored per file, so a name that moves between configs still has to
        # be declared in the one that now uses it.
        for name, old in saved.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

    graph = render(config)
    problems: list[str] = []
    sanctioned = 0
    for warning in graph["warnings"]:
        kind = str(warning["kind"])
        subject = str(warning.get("door") or warning.get("exit") or "")
        if (path.stem, kind, subject) in SANCTIONED:
            sanctioned += 1
            continue
        problems.append(f"{path.name}: {kind} — {warning['detail']}")

    doors = len(graph["doors"])
    exits = len(graph["exits"])
    stages = ", ".join(stage["name"] for stage in graph["pipeline"])
    note = f", {sanctioned} sanctioned" if sanctioned else ""
    print(f"  {path.name}: {doors} door(s), {exits} exit(s), pipeline [{stages}]{note}")
    return problems


def main() -> int:
    paths = [Path(arg) for arg in sys.argv[1:]] or list(DEFAULTS)
    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            problems.append(f"{path}: no such config")
            continue
        problems.extend(check(path))

    for problem in problems:
        print(f"  FAIL  {problem}")
    if problems:
        print(
            f"\n{len(problems)} topology problem(s). A wildcard a RETURN door can reach is the "
            "loop that hands a brain its own output; a starved exit is a node nothing can\n"
            "reach. If one of these is deliberate, add it to SANCTIONED in this file with the "
            "reason — the exception is meant to cost a sentence, not a heuristic."
        )
        return 1
    print(f"topology: {len(paths)} deployed config(s), no unreachable door, starved exit or wildcard fallthrough")
    return 0


if __name__ == "__main__":
    sys.exit(main())
