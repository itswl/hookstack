#!/usr/bin/env python3
"""hookstack doctor — the first-run check for a one-operator deployment.

The three locks that kept pilot zero from ever pressing a button were not bugs
in a service: no approve in `card_actions`, no reachable console link, and an
empty remediation allowlist. Each is invisible until somebody reads three config
files and two containers at once, which is the work this script does.

It is deliberately host-side and stdlib-only, like deploy_preflight.py: it reads
the same `.env`, the deployed pipe config and the compose file, then optionally
asks the running services. It does not write anything and does not start a
container.

    python3 scripts/doctor.py
    python3 scripts/doctor.py --env .env --config deploy/work.yaml --json
    python3 scripts/doctor.py --no-http          # files only

Exit 0 when every MUST check passes (warnings allowed), 1 when one does not.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MUST, SHOULD, OK, INFO = "must", "should", "ok", "info"


@dataclass
class Check:
    level: str
    name: str
    detail: str = ""
    fix: str = ""


def load_env(path: Path) -> dict[str, str]:
    """KEY=VALUE from a .env, with the process environment filling the gaps.

    The file is the deployment's truth on a laptop; the process environment is
    the truth inside a container. A doctor that only read one of them would be
    confidently wrong half the time.
    """
    found: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            found[key.strip()] = value.strip().strip("'\"")
    for key, value in os.environ.items():
        if not found.get(key):
            found[key] = value
    return found


def _nonempty(env: dict[str, str], *names: str) -> tuple[bool, str]:
    for name in names:
        if str(env.get(name) or "").strip():
            return True, name
    return False, ""


def _allowlist_rows(path: Path) -> int:
    try:
        return sum(
            1
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    except OSError:
        return 0


def _resolve(path_text: str, env_file: Path) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    # A deployed .env usually writes these relative to the repository root; a
    # systemd/crontab process has a different cwd, so try both before failing.
    for base in (env_file.parent, ROOT, Path.cwd()):
        candidate = (base / path).resolve()
        if candidate.exists():
            return candidate
    return (ROOT / path).resolve()


def file_checks(env: dict[str, str], config_text: str, env_file: Path) -> list[Check]:
    checks: list[Check] = []

    ok, used = _nonempty(env, "HOOKRELAY_READ_TOKEN", "WORK_READ_TOKEN")
    checks.append(
        Check(
            OK if ok else MUST,
            "read token",
            f"using {used}" if used else "no read token in .env",
            "set HOOKRELAY_READ_TOKEN (or WORK_READ_TOKEN for the work compose)" if not ok else "",
        )
    )
    ok, used = _nonempty(env, "HOOKRELAY_ADMIN_TOKEN", "WORK_ADMIN_TOKEN")
    checks.append(
        Check(
            OK if ok else SHOULD,
            "admin token",
            f"using {used}" if used else "no admin token; the boards cannot silence or retry",
            "set HOOKRELAY_ADMIN_TOKEN / WORK_ADMIN_TOKEN" if not ok else "",
        )
    )
    ok, _ = _nonempty(env, "HOOKPROBE_TOKEN")
    checks.append(
        Check(
            OK if ok else MUST,
            "investigator token",
            "set" if ok else "HOOKPROBE_TOKEN is empty",
            "set HOOKPROBE_TOKEN; the investigator refuses to start without auth in the real composes"
            if not ok
            else "",
        )
    )
    ok, used = _nonempty(env, "HOOKPROBE_ACTION_SECRET", "WORK_EVENT_SECRET", "HOOKPROBE_EVENT_SECRET")
    checks.append(
        Check(
            OK if ok else MUST,
            "card-action secret",
            f"using {used}" if used else "no secret; approve will answer 503",
            "set HOOKPROBE_ACTION_SECRET (or let it fall back to HOOKPROBE_EVENT_SECRET)" if not ok else "",
        )
    )
    ok, _ = _nonempty(env, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HOOKPROBE_API_KEY")
    checks.append(
        Check(
            OK if ok else MUST,
            "model credential",
            "set" if ok else "no Anthropic-dialect credential",
            "set ANTHROPIC_API_KEY (or the gateway's AUTH_TOKEN pair) or the investigator cannot run" if not ok else "",
        )
    )
    ok, _ = _nonempty(env, "HOOKPROBE_PUBLIC_URL")
    checks.append(
        Check(
            OK if ok else SHOULD,
            "console link",
            "set" if ok else "HOOKPROBE_PUBLIC_URL is empty; cards carry no link to the console",
            "set it to the browser-reachable address (a tailnet https URL for the phone)" if not ok else "",
        )
    )

    allow = str(env.get("HOOKPROBE_REMEDIATION_ALLOWLIST") or "").strip()
    if not allow:
        checks.append(
            Check(
                MUST,
                "remediation allowlist",
                "HOOKPROBE_REMEDIATION_ALLOWLIST is empty",
                "point it at the allowlist file; without one every approve is refused",
            )
        )
    else:
        path = _resolve(allow, env_file)
        rows = _allowlist_rows(path)
        checks.append(
            Check(
                OK if rows else MUST,
                "remediation allowlist",
                f"{path} ({rows} rule(s))",
                "" if rows else f"create {path} with one full-match command per line, or approve always refuses",
            )
        )

    if re.search(r"(?m)^\s+approve:\s*$", config_text):
        checks.append(Check(OK, "approve button", "card_actions has approve"))
    else:
        checks.append(
            Check(
                MUST,
                "approve button",
                "card_actions has no approve entry",
                "add `card_actions: {approve: {forward_to: <channel>}}`; a report can propose, but no button is drawn",
            )
        )
    if re.search(r"(?m)^\s+useful:\s*$", config_text) and re.search(r"(?m)^\s+useless:\s*$", config_text):
        checks.append(Check(OK, "ruling buttons", "card_actions has useful/useless"))
    else:
        checks.append(
            Check(
                SHOULD,
                "ruling buttons",
                "card_actions lacks useful and/or useless",
                "add both; without them the attention numbers never get a human ruling",
            )
        )

    backup = str(env.get("BACKUP_OFFSHOST_DEST") or "").strip()
    checks.append(
        Check(
            OK if backup else SHOULD,
            "off-host backup",
            backup or "BACKUP_OFFSHOST_DEST is empty",
            "set BACKUP_OFFSHOST_DEST to an encrypted rsync target; the tarball otherwise shares a disk with the ledger"
            if not backup
            else "",
        )
    )
    return checks


def _get(url: str, token: str, timeout: float = 3.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "X-Read-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw)
            except ValueError:
                return response.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def http_checks(env: dict[str, str], relay: str, probe: str) -> list[Check]:
    checks: list[Check] = []
    relay_token = str(env.get("HOOKRELAY_READ_TOKEN") or env.get("WORK_READ_TOKEN") or "")
    probe_token = str(env.get("HOOKPROBE_TOKEN") or "")
    for name, base, token, path, must in (
        ("pipe", relay, relay_token, "/healthz", True),
        ("investigator", probe, probe_token, "/healthz", True),
    ):
        if not base:
            continue
        status, body = _get(f"{base.rstrip('/')}{path}", token)
        if status == 200:
            checks.append(Check(OK, f"{name} reachable", base))
        else:
            checks.append(
                Check(
                    MUST if must else SHOULD,
                    f"{name} reachable",
                    f"{base} answered {status or body}",
                    "start the stack, or pass the address/port this deployment actually uses",
                )
            )
    if relay:
        status, body = _get(f"{relay.rstrip('/')}/status", relay_token)
        if status == 200 and isinstance(body, dict):
            sil = len(body.get("silences") or [])
            checks.append(Check(OK, "pipe status", f"{sil} active silence(s)"))
        elif status:
            checks.append(
                Check(
                    SHOULD, "pipe status", f"answered {status}; read token may be wrong", "check HOOKRELAY_READ_TOKEN"
                )
            )
    if probe:
        status, body = _get(f"{probe.rstrip('/')}/v1/agent", probe_token)
        if status == 200 and isinstance(body, dict):
            runtime = body.get("runtime") or {}
            checks.append(
                Check(
                    OK,
                    "investigator identity",
                    (
                        f"{body.get('name') or 'hookprobe'} · adapter={runtime.get('adapter')} · "
                        f"model={runtime.get('model')}"
                    ),
                )
            )
        elif status:
            checks.append(
                Check(
                    SHOULD, "investigator identity", f"answered {status}; token may be wrong", "check HOOKPROBE_TOKEN"
                )
            )
        status, body = _get(f"{probe.rstrip('/')}/v1/posture", probe_token)
        if status == 200 and isinstance(body, dict):
            verdict = str(body.get("verdict") or body.get("status") or "")
            level = OK if verdict in ("readonly-confirmed", "no-credentials", "recorded") else SHOULD
            checks.append(Check(level, "posture", f"{verdict or 'no verdict'} · {body.get('detail') or ''}"[:180]))
        elif status:
            checks.append(
                Check(
                    SHOULD,
                    "posture",
                    f"answered {status}; no measured posture on this node",
                    "the readonly claim is measured, not assumed — check the startup log for posture",
                )
            )
    return checks


def render(checks: list[Check], *, as_json: bool) -> int:
    must = [c for c in checks if c.level == MUST]
    should = [c for c in checks if c.level == SHOULD]
    if as_json:
        print(
            json.dumps(
                {"checks": [asdict(c) for c in checks], "must_fix": len(must), "should_fix": len(should)},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        icons = {OK: "OK  ", MUST: "FAIL", SHOULD: "WARN", INFO: "INFO"}
        print("hookstack doctor\n")
        for check in checks:
            print(f"  {icons.get(check.level, check.level):4} {check.name}: {check.detail}")
            if check.fix and check.level in (MUST, SHOULD):
                print(f"       fix: {check.fix}")
        print(f"\n{len(must)} must fix · {len(should)} should fix")
        if must:
            print("A deployment with a must-fix is not ready for a person to press anything.")
    return 1 if must else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", default=str(ROOT / ".env"), help="deployment .env (default: repo root)")
    parser.add_argument("--config", default=str(ROOT / "deploy" / "work.yaml"), help="deployed pipe config text")
    parser.add_argument("--relay", default=os.environ.get("HOOKRELAY_URL", "http://127.0.0.1:8100"))
    parser.add_argument("--probe", default=os.environ.get("HOOKPROBE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--no-http", action="store_true", help="files only; no running services required")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    env_file = Path(args.env).expanduser()
    env = load_env(env_file)
    config_path = Path(args.config).expanduser()
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError:
        config_text = ""

    checks = file_checks(env, config_text, env_file)
    if not config_text:
        checks.append(
            Check(
                SHOULD, "pipe config", f"{config_path} is not readable", "pass --config with the deployed config.yaml"
            )
        )
    if not args.no_http:
        checks.extend(http_checks(env, args.relay, args.probe))
    return render(checks, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
