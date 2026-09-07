#!/usr/bin/env python3
"""Refuse to deploy a production stack whose doors would come up unsigned.

The composes say `${NAME:-}` for most secrets, deliberately: an empty default
lets the quickstart and a laptop boot with nothing configured. On the production
host that same default is a hazard — a variable missing from .env does not fail
the deploy, it opens the door, quietly, at boot. deploy.sh runs this before the
build, against the real .env, so the production baseline the compose comments
describe ("set BOTH in production") is enforced rather than remembered.

Two rules, both read from the files rather than from a list kept beside them:

  1. Every `secret:` under `sources:` in the deployed pipe config is a ${NAME}
     reference AND NAME is non-empty in .env. A literal "" on a door is an
     unsigned door; it used to be allowed for two in-network return hops, and
     stopped being allowed the day that network was shared with other stacks.
  2. Every ${NAME} or ${NAME:-...} in the deployed compose files whose name ends
     in _SECRET or _TOKEN is non-empty in .env.

DEPLOY_ALLOW_EMPTY=NAME,NAME lists variables that may be empty on this host,
each a decision written where the deploy reads it. Exit 0 when clean, 1 with
every finding listed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
COMPOSE_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*|:\?[^}]*)?\}")
SENSITIVE = re.compile(r"_(SECRET|TOKEN)$")


def load_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, quotes stripped, comments and blanks ignored."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def check_pipe_config(path: Path, env: dict[str, str], allow: set[str]) -> list[str]:
    problems: list[str] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"{path}: unreadable — {exc}"]
    for source in raw.get("sources") or []:
        name, secret = source.get("name"), source.get("secret")
        if secret is None:
            problems.append(f"door {name!r}: no secret key at all")
            continue
        text = str(secret).strip()
        if text == "":
            problems.append(
                f"door {name!r}: secret is a literal empty string — an unsigned door on a shared network"
            )
            continue
        match = ENV_REF.match(text)
        if not match:
            problems.append(f"door {name!r}: secret is not a ${{NAME}} reference")
            continue
        var = match.group(1)
        if var not in allow and not env.get(var):
            problems.append(
                f"door {name!r}: ${{{var}}} is empty or missing in .env — the door would come up unsigned"
            )
    return problems


def check_compose(path: Path, env: dict[str, str], allow: set[str]) -> list[str]:
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: unreadable — {exc}"]
    for var in sorted(set(COMPOSE_REF.findall(text))):
        if SENSITIVE.search(var) and var not in allow and not env.get(var):
            problems.append(f"{path.name}: ${{{var}}} is empty or missing in .env")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    env = load_env(root / ".env")
    allow = {
        v.strip()
        for v in os.environ.get("DEPLOY_ALLOW_EMPTY", "").split(",")
        if v.strip()
    }
    problems = check_pipe_config(root / "deploy" / "shadow.yaml", env, allow)
    for compose in (
        "deploy/docker-compose.shadow.yml",
        "hookprobe/deploy/docker-compose.prod.yml",
    ):
        problems += check_compose(root / compose, env, allow)
    if problems:
        print(
            "deploy preflight: REFUSING — this .env would bring up an unsigned or unauthenticated hop:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "  Set the variable in .env, or record the exception: DEPLOY_ALLOW_EMPTY=NAME ./scripts/deploy.sh",
            file=sys.stderr,
        )
        return 1
    print(
        f"deploy preflight: every door signed and every secret set ({len(env)} variables in .env"
        + (f", {len(allow)} allowed empty" if allow else "")
        + ")"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
