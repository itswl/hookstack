#!/usr/bin/env python3
"""Refuse to deploy a production stack whose doors would come up unsigned.

The composes say `${NAME:-}` for most secrets, deliberately: an empty default
lets the quickstart and a laptop boot with nothing configured. On the production
host that same default is a hazard — a variable missing from .env does not fail
the deploy, it opens the door, quietly, at boot. deploy.sh runs this before the
build, against the real .env, so the production baseline the compose comments
describe ("set BOTH in production") is enforced rather than remembered.

Three rules, all read from the files rather than from a list kept beside them:

  1. Every `secret:` under `sources:` in the deployed pipe config is a ${NAME}
     reference AND NAME is non-empty in .env. A literal "" on a door is an
     unsigned door; it used to be allowed for two in-network return hops, and
     stopped being allowed the day that network was shared with other stacks.
  2. Every ${NAME} or ${NAME:-...} in the deployed compose files whose name ends
     in _SECRET or _TOKEN is non-empty in .env.
  4. REPORTED, never refused: a variable set in .env that no deployed compose
     passes through. There is no `env_file`, deliberately (2026-09-02), so the
     `environment:` block IS the complete list of what a container can see —
     which means a variable the operator took the trouble to set and no compose
     names does nothing at all, silently. That happened: `HOOKPROBE_PRICE_*`
     shipped as settings the code read and no compose declared, so setting the
     rates in .env changed nothing and the ledger went on pricing from the
     runtime's own table. A note rather than a refusal, because .env is shared
     with the crontab and the patrols, and a variable meant for those is not a
     mistake.

  3. A variable that decides WHICH MODEL answers — any name ending `_MODEL` or
     `_BASE_URL` — is set in .env whenever the compose supplies a NON-EMPTY
     default for it. Rules 1 and 2 catch a hop that would come up open; this one
     catches a hop that would come up POINTED SOMEWHERE ELSE, which is quieter.

     It exists because that happened, twice over, on 2026-09-09. The shadow
     compose defaulted its brain to `deepseek-chat` at `api.deepseek.com` — a
     provider this deployment had left — and the day the two comparison arms
     were retired, that fallback stopped belonging to an arm and became the ONLY
     brain's. A variable dropped from .env would have put every verdict on a
     vendor nobody chose, and `judge.py` does not error on a bad AI config: it
     falls through to its rule route and keeps answering, so the symptom is
     suspiciously rule-shaped verdicts rather than a failure. The compose was
     fixed to `${VAR:?}` the same day; this rule is the mechanism, and it still
     has a live target in the investigator's production compose
     (`HOOKPROBE_MODEL` defaults to a Claude id on a stack whose gateway wants
     its own).

     A non-empty default is the whole condition. `${VAR:-}` substitutes nothing
     and `${VAR:?}` refuses to start, so neither can quietly answer as somebody
     else; and the defaults themselves stay where they are, because they are
     what makes the quickstart runnable. What this refuses is inheriting one on
     a production host.

DEPLOY_ALLOW_EMPTY=NAME,NAME lists variables that may be empty on this host,
each a decision written where the deploy reads it. Exit 0 when clean, 1 with
every finding listed.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path

ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
COMPOSE_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*|:\?[^}]*)?\}")
SENSITIVE = re.compile(r"_(SECRET|TOKEN)$")
# Rule 3's two halves: which names decide which model answers, and the defaults
# they can silently inherit. `:-` only — a `:?` default cannot substitute
# anything, because compose refuses to start instead.
#
# Deliberately not `_AI_MODEL`: that was the first spelling of this pattern and
# it matched hookjudge's knobs and missed `HOOKPROBE_MODEL`, which is the same
# hazard on the investigator and the only one still live. A rule shaped around
# the one service whose incident prompted it is a rule that catches that service.
PROVIDER = re.compile(r"_(MODEL|BASE_URL)$")
COMPOSE_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")


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


def pipe_doors(text: str) -> list[tuple[str, str | None]]:
    """(name, secret) for every door under `sources:`, read from the text.

    Line-based on purpose: the host's python has no yaml, the file is ours and
    flat, and a check that needs a dependency the deploy host lacks is a check
    that gets skipped. A door with no `secret:` line reports None.
    """
    doors: list[tuple[str, str | None]] = []
    in_sources = False
    name: str | None = None
    secret: str | None = None
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not stripped:
            continue
        if re.match(r"^[A-Za-z_]+:\s*$", stripped):  # a top-level section
            if name is not None:
                doors.append((name, secret))
                name, secret = None, None
            in_sources = stripped.startswith("sources:")
            continue
        if not in_sources:
            continue
        item = re.match(r"^\s*-\s*name:\s*(.+)$", stripped)
        if item:
            if name is not None:
                doors.append((name, secret))
            name, secret = item.group(1).strip().strip("'\""), None
            continue
        sec = re.match(r"^\s+secret:\s*(.*)$", stripped)
        if sec and name is not None:
            secret = sec.group(1).strip().strip("'\"")
    if name is not None:
        doors.append((name, secret))
    return doors


def check_pipe_config(path: Path, env: dict[str, str], allow: set[str]) -> list[str]:
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: unreadable — {exc}"]
    for name, secret in pipe_doors(text):
        if secret is None:
            problems.append(f"door {name!r}: no secret key at all")
            continue
        if secret == "":
            problems.append(f"door {name!r}: secret is a literal empty string — an unsigned door on a shared network")
            continue
        match = ENV_REF.match(secret)
        if not match:
            problems.append(f"door {name!r}: secret is not a ${{NAME}} reference")
            continue
        var = match.group(1)
        if var not in allow and not env.get(var):
            problems.append(f"door {name!r}: ${{{var}}} is empty or missing in .env — the door would come up unsigned")
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
    for var, default in sorted(set(COMPOSE_DEFAULT.findall(text))):
        if not PROVIDER.search(var) or var in allow or env.get(var):
            continue
        fallback = default.strip()
        # A nested default (`${A:-${B:-c}}`) resolves to another variable, not to
        # a vendor name, and an empty one substitutes nothing.
        if not fallback or fallback.startswith("${"):
            continue
        problems.append(
            f"{path.name}: ${{{var}}} is missing from .env, so this host would run the compose "
            f"default {fallback!r} — a vendor chosen by a file rather than by this deployment"
        )
    return problems


# What the composes are expected to carry. Scoped so the note lists this stack's
# own variables and not the operator's shell.
OURS = re.compile(r"^(HOOKPROBE|HOOKJUDGE|HOOKRELAY|LARK|SHADOW|WW)_")


def unreachable_vars(env: dict[str, str], texts: list[str]) -> list[str]:
    """Variables set in .env that no deployed compose passes into a container.

    Not a failure — .env is also read by the crontab and the patrols — but the
    one signal that separates "configured" from "configured and reaching the
    process", which are the two things a deploy is otherwise unable to tell
    apart. See rule 4.
    """
    joined = "\n".join(texts)
    return sorted(name for name, value in env.items() if value and OURS.match(name) and name not in joined)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    env = load_env(root / ".env")
    allow = {v.strip() for v in os.environ.get("DEPLOY_ALLOW_EMPTY", "").split(",") if v.strip()}
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
    texts = []
    for compose in ("deploy/docker-compose.shadow.yml", "hookprobe/deploy/docker-compose.prod.yml"):
        with contextlib.suppress(OSError):
            texts.append((root / compose).read_text(encoding="utf-8"))
    stranded = unreachable_vars(env, texts)
    print(
        f"deploy preflight: every door signed, every secret set and every brain pointed by .env "
        f"({len(env)} variables in .env" + (f", {len(allow)} allowed empty" if allow else "") + ")"
    )
    if stranded:
        print(
            f"  note: {len(stranded)} variable(s) set in .env that no deployed compose passes through, "
            "so they reach no container — fine if they are for the crontab or the patrols, a silent "
            "no-op if they were meant for a service:"
        )
        for name in stranded:
            print(f"    {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
