"""What this runner can actually touch, checked against the credentials it holds.

`HOOKPROBE_BASH_GUARD=readonly` is a *declaration*: the shell refuses mutating
verbs. What bounds the runner is the credentials mounted into it, and those are
files an operator drops in — a kubeconfig, an AWS profile — whose scope nothing
here has ever verified. docs/containment.md lists thirteen boundaries and each
is a description of a mechanism; this is the one place a description is turned
into a measurement, at the moment it matters: before the first run.

At startup, for every credential present, ask the far side what it would allow:

  kubectl auth can-i --list           every rule granted to this identity here
  kubectl auth can-i <verb> <res> -A  four cluster-wide mutating questions
  aws sts get-caller-identity         who am I
  aws iam simulate-principal-policy   would these dangerous actions be allowed

and compare with the declared posture. A runner declared read-only whose
credentials can `delete pods` is WIDER THAN DECLARED, and under the default
(`enforce`) it refuses to start — a boundary that exists only in a README is
not one, and a runner that starts anyway would spend its first run proving it.
A `danger-only` runner is allowed to write by design, so its check only
RECORDS the blast radius; that record is what an audit later cites.

Everything measured is written to `posture.json` in the workdir and served on
`GET /v1/posture`, so "this ran read-only" in an audit is backed by what the
cluster and the account said on the day, not by a variable.

Unverifiable is a verdict, not an error: a read-only AWS identity often may not
call SimulatePrincipalPolicy on itself. That is reported as such — loudly — and
never mistaken for confirmed.

Unmeasurable is the verdict for a credential whose measuring tool is not in the
image, and it is a different silence: the far side was never asked. It happened.
The work stack's three probes share one image tag, one of their builds passed no
kubectl version, and from 2026-09-29 to 10-09 the planner and the work node
started every day with `kubectl: not found` in their records — the planner
`unverifiable`, the writer `recorded` with an empty radius — and nothing paged,
because both verdicts start and the hourly check only asks "wider?". Now a
readonly node refuses to start under `enforce`, a writing node starts and pages
through the alarm channel, and the hourly selftest fails until the tool is back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("hookprobe.posture")

MODES = ("enforce", "warn", "off")
FILE = "posture.json"

# What a read-only identity must not be able to do. `*` is a wildcard grant.
MUTATING_VERBS = frozenset({"create", "update", "patch", "delete", "deletecollection", "*"})
# Every authenticated subject may create these; they are how `can-i` itself
# works and they mutate nothing that matters. Listing them as a violation would
# make every cluster look wider than declared.
ALWAYS_ALLOWED_RESOURCES = frozenset({"selfsubjectaccessreviews", "selfsubjectrulesreviews", "selfsubjectreviews"})
# Cluster-wide questions `--list` cannot answer (it is namespace-scoped).
KUBE_QUESTIONS = (("delete", "pods"), ("create", "deployments.apps"), ("patch", "nodes"), ("delete", "namespaces"))
# A short list of actions no read-only identity should hold. Not exhaustive on
# purpose: it is a probe of the identity's shape, not an audit of IAM.
DANGEROUS_AWS_ACTIONS = (
    "ec2:TerminateInstances",
    "ec2:StopInstances",
    "rds:DeleteDBInstance",
    "s3:DeleteBucket",
    "s3:PutObject",
    "iam:CreateUser",
    "iam:AttachUserPolicy",
    "eks:DeleteCluster",
    "lambda:UpdateFunctionCode",
    "secretsmanager:PutSecretValue",
)
TIMEOUT_SECONDS = 25.0

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


class PostureViolation(RuntimeError):
    """The posture cannot be held as declared under enforce: the credentials are
    wider than it, or could not be measured at all."""


async def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """(exit code, stdout, stderr); a missing binary or a timeout is a non-zero exit."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT_SECONDS)
        return int(process.returncode or 0), out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except TimeoutError:
        return 124, "", f"{' '.join(argv[:3])}: timed out after {TIMEOUT_SECONDS:.0f}s"
    except OSError as exc:
        return 1, "", str(exc)


def kube_mutating_rules(can_i_list: str) -> list[str]:
    """The rules in `kubectl auth can-i --list` output that grant a mutating verb.

    The table is `Resources  Non-Resource URLs  Resource Names  Verbs`; the last
    bracketed column is the verbs. A row reads as a violation when it grants a
    mutating verb on anything but the self-review resources.
    """
    found: list[str] = []
    for raw in can_i_list.splitlines()[1:]:
        line = raw.strip()
        if not line or line.startswith("Resources"):
            continue
        # Verbs are the last [...] group; the resource is the first token.
        if "[" not in line:
            continue
        resource = line.split()[0]
        verbs = {v.strip() for v in line.rsplit("[", 1)[1].rstrip("]").split() if v.strip()}
        if resource.split(".")[0] in ALWAYS_ALLOWED_RESOURCES:
            continue
        if verbs & MUTATING_VERBS:
            found.append(f"{resource}: {' '.join(sorted(verbs & MUTATING_VERBS))}")
    return found


def aws_allowed(simulation_json: str) -> list[str]:
    """Actions SimulatePrincipalPolicy says would be allowed."""
    try:
        results = json.loads(simulation_json).get("EvaluationResults") or []
    except (json.JSONDecodeError, AttributeError):
        return []
    return [str(r.get("EvalActionName")) for r in results if str(r.get("EvalDecision", "")).lower() == "allowed"]


def role_arn(caller_arn: str) -> str:
    """The IAM principal to simulate: an assumed role's session ARN names the
    session, SimulatePrincipalPolicy wants the role."""
    if ":assumed-role/" in caller_arn:
        head, tail = caller_arn.split(":assumed-role/", 1)
        account = head.rsplit(":", 1)[-1]  # "arn:aws:sts::<account>" — the region slot is empty for STS
        return f"arn:aws:iam::{account}:role/{tail.split('/', 1)[0]}"
    return caller_arn


def kubeconfig_path() -> Path | None:
    raw = os.environ.get("KUBECONFIG")
    path = Path(raw.split(os.pathsep)[0]) if raw else Path.home() / ".kube" / "config"
    return path if path.is_file() else None


def aws_configured() -> bool:
    # A profile NAME alone is not a source: it names a section of a file, and
    # the files are what the loop below looks for. An instance metadata service
    # is not configured at all — it is asked (see metadata_reachable).
    if any(
        os.environ.get(name)
        for name in (
            "AWS_ACCESS_KEY_ID",
            "AWS_ROLE_ARN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        )
    ):
        return True
    for var, default in (("AWS_SHARED_CREDENTIALS_FILE", "~/.aws/credentials"), ("AWS_CONFIG_FILE", "~/.aws/config")):
        if Path(os.environ.get(var) or default).expanduser().is_file():
            return True
    return False


async def check_kube(run: Runner) -> dict[str, Any]:
    path = kubeconfig_path()
    if path is None:
        return {"present": False}
    record: dict[str, Any] = {"present": True, "kubeconfig": str(path), "mutating": [], "errors": []}
    code, out, err = await run(["kubectl", "auth", "can-i", "--list"])
    if code == 127:
        # The binary is not in the image. Four more questions of it would be
        # four more copies of this line; the verdict needs the one.
        record["tool_missing"] = "kubectl"
        record["errors"].append(f"can-i --list: {(err or out).strip()[:200]}")
        return record
    if code != 0:
        record["errors"].append(f"can-i --list: {(err or out).strip()[:200]}")
    else:
        record["mutating"].extend(kube_mutating_rules(out))
    for verb, resource in KUBE_QUESTIONS:
        code, out, err = await run(["kubectl", "auth", "can-i", verb, resource, "--all-namespaces"])
        answer = out.strip().lower()
        if answer.startswith("yes"):
            record["mutating"].append(f"{resource} (cluster-wide): {verb}")
        elif not answer.startswith("no"):
            record["errors"].append(f"can-i {verb} {resource}: {(err or out).strip()[:120]}")
    record["mutating"] = sorted(set(record["mutating"]))
    return record


def metadata_reachable(timeout: float = 1.0) -> bool:
    """Whether an instance metadata service answers here, asked directly.

    When nothing names a credential, this is the source left: the AWS CLI asks
    the same address for an instance role. Any HTTP answer counts, a refusal
    included, because something is there. No proxy, on purpose: the probes'
    egress proxy answers for addresses it refuses, and the question is whether
    this container reaches the service at all. On a laptop or on the probes'
    internal network the address has no route, and that is the common case.
    """
    base = os.environ.get("AWS_EC2_METADATA_SERVICE_ENDPOINT") or (
        "http://[fd00:ec2::254]"
        if os.environ.get("AWS_EC2_METADATA_SERVICE_ENDPOINT_MODE", "").lower() == "ipv6"
        else "http://169.254.169.254"
    )
    request = urllib.request.Request(
        base.rstrip("/") + "/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout):  # nosec B310
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


async def check_aws(run: Runner) -> dict[str, Any]:
    if not aws_configured():
        # Asked, not assumed. Assuming made every laptop and every probe on an
        # internal network `unverifiable`, and a writing node with a declared
        # radius refused to start over a service that cannot exist there.
        if os.environ.get("AWS_EC2_METADATA_DISABLED", "").lower() == "true":
            return {"present": False}
        if not await asyncio.to_thread(metadata_reachable):
            return {"present": False}
        # A service answered: its credentials are measured like any other source.
    record: dict[str, Any] = {"present": True, "identity": None, "allowed": [], "errors": [], "unverifiable": False}
    code, out, err = await run(["aws", "sts", "get-caller-identity", "--output", "json"])
    if code != 0 and "Unable to locate credentials" in (err or out):
        # The CLI looked everywhere it looks and found nothing: an instance with
        # no role, say. That is an absence, not a measurement that failed.
        return {"present": False}
    if code != 0:
        record["errors"].append(f"get-caller-identity: {(err or out).strip()[:200]}")
        record["unverifiable"] = True
        if code == 127:
            record["tool_missing"] = "aws"
        return record
    try:
        arn = str(json.loads(out).get("Arn") or "")
    except json.JSONDecodeError:
        arn = ""
    record["identity"] = arn
    if not arn:
        record["unverifiable"] = True
        return record
    code, out, err = await run(
        [
            "aws",
            "iam",
            "simulate-principal-policy",
            "--policy-source-arn",
            role_arn(arn),
            "--action-names",
            *DANGEROUS_AWS_ACTIONS,
            "--output",
            "json",
        ]
    )
    if code != 0:
        # Typically AccessDenied on iam:SimulatePrincipalPolicy — the honest
        # answer is "could not verify", never "confirmed".
        record["errors"].append(f"simulate-principal-policy: {(err or out).strip()[:200]}")
        record["unverifiable"] = True
    else:
        try:
            results = json.loads(out)["EvaluationResults"]
            if not isinstance(results, list):
                raise ValueError("invalid results")
            record["allowed"] = sorted(
                {
                    str(item.get("EvalActionName"))
                    for item in results
                    if isinstance(item, dict) and item.get("EvalDecision") == "allowed" and item.get("EvalActionName")
                }
            )
            if any(
                not isinstance(item, dict)
                or item.get("EvalDecision") not in {"allowed", "explicitDeny", "implicitDeny"}
                for item in results
            ):
                raise ValueError("invalid decision")
            decisions = {str(item["EvalActionName"]) for item in results if item.get("EvalDecision")}
            if not set(DANGEROUS_AWS_ACTIONS).issubset(decisions):
                raise ValueError("incomplete simulation")
        except (ValueError, KeyError, TypeError, AttributeError):
            record["errors"].append("simulate-principal-policy: invalid or incomplete results")
            record["unverifiable"] = True
    return record


def declared_radius(path: Path | None) -> set[str] | None:
    """The blast radius an operator accepted for a WRITING node, or None.

    One measured line per entry, exactly as `/v1/posture` reports it, `#` for
    comments. The workflow is deliberately "pin what you saw": start the node,
    read the measurement, paste the lines you meant to grant. From then on a
    credential that gains anything new refuses to start under `enforce`.

    None — no file — leaves the writing posture exactly as it was, `recorded`.
    An upgrade must not brick a node that was running yesterday, the same rule
    a proposal stamped before the freshness cursor existed gets.
    """
    if path is None:
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        # Unreadable is NOT "no declaration": an operator who wrote a file and
        # a typo in its path would silently get the unjudged posture back.
        return set()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def beyond(kube: dict[str, Any], aws: dict[str, Any], declared: set[str] | None) -> list[str]:
    """What the credential can do that nobody wrote down. Empty when undeclared."""
    if declared is None:
        return []
    measured = list(kube.get("mutating") or []) + list(aws.get("allowed") or [])
    return sorted(item for item in measured if item not in declared)


def tools_missing(kube: dict[str, Any], aws: dict[str, Any]) -> list[str]:
    """The measuring tools a mounted credential could not be asked with."""
    return [
        str(record["tool_missing"]) for record in (kube, aws) if record.get("present") and record.get("tool_missing")
    ]


def verdict(bash_guard: str, kube: dict[str, Any], aws: dict[str, Any], declared: set[str] | None = None) -> str:
    # A credential nobody could measure is not a posture of any kind. What WAS
    # measured still outranks it: a violation found on one credential is a
    # violation whether or not the other one's tool is installed.
    missing = tools_missing(kube, aws)
    if bash_guard != "readonly":
        # A writing posture is ALLOWED to write — the question is never "can it"
        # but "is it wider than what somebody agreed to". Undeclared, there is
        # nothing to answer that with and this stays an observation, which is
        # what it was for a year. Declared, `enforce` means the same thing on
        # both postures: the credential may not exceed the declaration.
        if beyond(kube, aws, declared):
            return "wider-than-declared"
        if missing:
            return "unmeasurable"
        if kube.get("errors") or aws.get("unverifiable"):
            return "unverifiable"
        return "recorded" if declared is None else "within-declared-radius"
    if kube.get("mutating") or aws.get("allowed"):
        return "wider-than-declared"
    if missing:
        return "unmeasurable"
    if kube.get("errors") or aws.get("unverifiable"):
        return "unverifiable"
    if not kube.get("present") and not aws.get("present"):
        return "no-credentials"
    return "readonly-confirmed"


async def check(bash_guard: str, run: Runner = run_cli, declared: set[str] | None = None) -> dict[str, Any]:
    kube, aws = await check_kube(run), await check_aws(run)
    record = {
        "checked_at": round(time.time(), 3),
        "bash_guard": bash_guard,
        "kube": kube,
        "aws": aws,
        "verdict": verdict(bash_guard, kube, aws, declared),
    }
    if declared is not None:
        # On the record, not only in the refusal: an operator reading
        # /v1/posture needs to see the size of the declaration they pinned and
        # exactly what exceeded it.
        record["declared_radius"] = len(declared)
        record["beyond_declared"] = beyond(kube, aws, declared)
    return record


def summary(record: dict[str, Any]) -> str:
    kube, aws = record.get("kube") or {}, record.get("aws") or {}
    parts = [f"posture={record.get('bash_guard')}", f"verdict={record.get('verdict')}"]
    if kube.get("present"):
        parts.append(
            f"kube mutating={len(kube.get('mutating') or [])}"
            + (f" errors={len(kube['errors'])}" if kube.get("errors") else "")
            + (f" tool-missing={kube['tool_missing']}" if kube.get("tool_missing") else "")
        )
    if aws.get("present"):
        parts.append(
            f"aws identity={aws.get('identity') or '?'} dangerous-allowed={len(aws.get('allowed') or [])}"
            + (" unverifiable" if aws.get("unverifiable") else "")
            + (f" tool-missing={aws['tool_missing']}" if aws.get("tool_missing") else "")
        )
    return " · ".join(parts)


def write(workdir: Path, record: dict[str, Any]) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / FILE).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def read(workdir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((workdir / FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


async def on_startup(
    workdir: Path,
    bash_guard: str,
    mode: str,
    run: Runner = run_cli,
    radius: Path | None = None,
    alarm: Callable[[str], Awaitable[bool]] | None = None,
) -> dict[str, Any] | None:
    """Check, record, announce — and under `enforce`, refuse to start a runner
    whose credentials are wider than it declares.

    `radius` is the declaration a WRITING node makes about itself. Without it a
    `danger-only` runner has always started whatever it held, because "allowed
    to write" left nothing to compare against; with it, `enforce` means the same
    sentence on both postures.

    `alarm` is the node's road around the pipe (`HOOKPROBE_ALARM_URL`). A
    credential that could not be measured is said there at once, on both
    postures: the readonly node is about to refuse and loop, and a writing node
    is about to start with a radius nobody measured.
    """
    if mode not in MODES:
        mode = "enforce"  # a typo in the switch that decides whether to refuse must fail closed
    record: dict[str, Any]
    if mode == "off":
        record = {"checked_at": round(time.time(), 3), "bash_guard": bash_guard, "verdict": "skipped", "mode": mode}
        write(workdir, record)
        return record
    declared = declared_radius(radius)
    record = await check(bash_guard, run, declared)
    record["mode"] = mode
    write(workdir, record)
    line = summary(record)
    if record["verdict"] == "wider-than-declared":
        # For a declared writing node the useful detail is the EXCESS, not the
        # whole measurement: "these four are new" is a paste away from fixed,
        # where a list of everything it holds is a list nobody reads.
        detail = record.get("beyond_declared") or (
            (record["kube"].get("mutating") or []) + (record["aws"].get("allowed") or [])
        )
        if mode == "enforce":
            logger.error("REFUSING TO START — credentials wider than declared posture: %s · %s", line, detail)
            raise PostureViolation(f"credentials wider than declared posture {bash_guard!r}: {detail}")
        logger.warning("credentials wider than declared posture: %s · %s", line, detail)
    elif record["verdict"] == "unmeasurable":
        missing = ", ".join(tools_missing(record["kube"], record["aws"]))
        text = f"posture cannot be measured: {missing} not in this image · {line}"
        if alarm is not None:
            with contextlib.suppress(Exception):  # the page is a courtesy; the verdict below is the decision
                await alarm(f"posture UNMEASURABLE — {text}")
        if (bash_guard == "readonly" or declared is not None) and mode == "enforce":
            logger.error("REFUSING TO START — %s", text)
            raise PostureViolation(
                f"posture cannot be measured as declared {bash_guard!r}: {missing} not in this image"
            )
        logger.error("%s", text)
    elif record["verdict"] == "unverifiable":
        if alarm is not None:
            with contextlib.suppress(Exception):
                await alarm(f"posture UNVERIFIABLE — {line}")
        if bash_guard != "readonly" and declared is not None and mode == "enforce":
            raise PostureViolation(f"declared writing posture could not be verified: {line}")
        logger.warning(
            "posture could not be fully verified: %s · %s",
            line,
            (record["kube"].get("errors") or []) + (record["aws"].get("errors") or []),
        )
    else:
        logger.info("posture check: %s", line)
    return record
