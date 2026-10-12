#!/usr/bin/env python3
"""Services that build the same image tag must build it the same way.

A compose builds every service that has a `build:` block, and two services that
name the same `image:` both tag their result with it — so the tag ends up
holding whichever build ran last, and nothing says which. The work stack's
three probes share `hookprobe:work`; probe-plan's build passed a kubectl version
and probe-watch's did not, so from 2026-09-29 the tag was the kubectl-less build,
and the planner and the work node started every day with `kubectl: not found`
in their posture records, measured nothing on their cluster side, and nothing
paged (hookprobe/posture.py says what that cost).

    python3 scripts/assert_shared_images_build_alike.py          # every compose
    python3 scripts/assert_shared_images_build_alike.py --list   # what builds what

Two services may share a tag only when their build context, dockerfile and args
are the same TEXT — the same `${VAR:-default}` expression, not merely the same
value today. Compared across every compose in the repository, because a tag is
global to the engine, not to a file.

Parsed by indentation rather than a YAML library: this runs under the system
interpreter in CI, which has none, and the composes here are written in the one
shape (two-space indents, `services:` at the top) that makes a small reader
enough. A floor keeps the reader honest — fewer build blocks than the composes
are known to hold means the parse lost a file, and that fails rather than
passing on a short list.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSES = sorted(ROOT.glob("deploy/docker-compose*.yml")) + sorted(ROOT.glob("docker-compose*.yml"))
# What the composes hold today: 15 build blocks, two tags built by more than one
# service (`hookprobe:work` by three, `lark-bridge:shadow` by two). A parse that
# finds fewer has lost a file. Lower these on purpose, with the reason, when a
# compose stops building something.
FLOOR_BUILDS = 8
FLOOR_SHARED = 2


def _strip(raw: str) -> str:
    """The line without its comment; a `#` counts only at the start or after a space."""
    line = raw.rstrip("\n")
    if line.lstrip().startswith("#"):
        return ""
    head, _, _ = line.partition(" #")
    return head.rstrip()


def services_of(text: str) -> dict[str, dict]:
    """Each service's `image:` and `build:` block, by name. Only the keys this
    check compares are read; everything else in a service is skipped."""
    out: dict[str, dict] = {}
    in_services = False
    service: str | None = None
    build: dict | None = None
    for raw in text.splitlines():
        line = _strip(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if indent == 0:
            in_services = body == "services:"
            service, build = None, None
            continue
        if not in_services:
            continue
        if indent == 2 and body.endswith(":"):
            service = body[:-1]
            out[service] = {"image": None, "build": None}
            build = None
            continue
        if service is None:
            continue
        key, _, value = body.partition(":")
        value = value.strip()
        if indent == 4:
            if key == "image":
                out[service]["image"] = value
            elif key == "build":
                build = {"context": value or None, "dockerfile": None, "args": {}}
                out[service]["build"] = build
            else:
                build = None  # left the build block
        elif build is not None and indent == 6 and key in ("context", "dockerfile"):
            build[key] = value
        elif build is not None and indent == 8:
            build["args"][key.strip()] = value
    return out


def builders() -> dict[str, list[tuple[str, str, dict]]]:
    """image tag → [(compose file, service, build spec)] for every service that builds."""
    found: dict[str, list[tuple[str, str, dict]]] = {}
    for compose in COMPOSES:
        for name, spec in services_of(compose.read_text(encoding="utf-8")).items():
            if spec["image"] and spec["build"] is not None:
                found.setdefault(spec["image"], []).append((str(compose.relative_to(ROOT)), name, spec["build"]))
    return found


def differences(a: dict, b: dict) -> list[str]:
    out = [f"{key}: {a.get(key)!r} vs {b.get(key)!r}" for key in ("context", "dockerfile") if a.get(key) != b.get(key)]
    for arg in sorted(set(a["args"]) | set(b["args"])):
        if a["args"].get(arg) != b["args"].get(arg):
            out.append(f"args.{arg}: {a['args'].get(arg)!r} vs {b['args'].get(arg)!r}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print every image tag and the services that build it")
    args = parser.parse_args()
    found = builders()
    total = sum(len(v) for v in found.values())
    shared = {tag: who for tag, who in found.items() if len(who) > 1}
    if args.list:
        for tag, who in sorted(found.items()):
            print(f"{tag}")
            for compose, service, spec in who:
                print(f"    {compose} · {service} · context={spec['context']} args={spec['args']}")
        return 0
    if total < FLOOR_BUILDS or len(shared) < FLOOR_SHARED:
        print(
            f"assert_shared_images_build_alike: parsed {total} build block(s) and {len(shared)} shared tag(s); "
            f"the floor is {FLOOR_BUILDS} and {FLOOR_SHARED} — the reader has lost a compose, which is not a pass",
            file=sys.stderr,
        )
        return 1
    problems: list[str] = []
    for tag, who in sorted(shared.items()):
        first_compose, first_service, first_spec = who[0]
        for compose, service, spec in who[1:]:
            for diff in differences(first_spec, spec):
                problems.append(f"{tag}: {first_compose}/{first_service} vs {compose}/{service} — {diff}")
    if problems:
        print("services that share an image tag build it differently; the tag is whichever ran last:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"shared images: {len(shared)} tag(s) built by {sum(len(v) for v in shared.values())} service(s), all alike")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
