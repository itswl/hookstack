#!/usr/bin/env python3
"""One operation as a Markdown document an auditor can be handed.

Joins the two accountability records the family already keeps: the pipe's
`GET /audit/{event_id}` (every hop of the chain to its root, every delivery and
return with cost, every human press, bodies as digests) and, for each hop the
investigator produced, its own `GET /v1/runs/{session_key}/audit` (the posture
the run held, every tool call, every refusal the guards made). Nothing here is
computed; it is the two ledgers, side by side, in the order things happened.

    HOOKRELAY_READ_TOKEN=... python3 scripts/audit_export.py 111
    HOOKRELAY_READ_TOKEN=... HOOKPROBE_TOKEN=... python3 scripts/audit_export.py 111 \\
        --relay http://127.0.0.1:8100 --probe http://127.0.0.1:8088 > operation-111.md

Without --probe (or a token for it) the investigator's half is omitted and the
document says so — an audit that silently drops a section is worse than a
shorter one that names what it could not read. Exit 0 with the document on
stdout, 1 when the pipe's record could not be fetched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


MISSING = "missing"  # the far end answered 404: no such run — not an error, not a hop of the investigator


def _get(url: str, token: str) -> dict | str | None:
    req = urllib.request.Request(
        url, headers={"X-Read-Token": token, "Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        return MISSING if exc.code == 404 else None
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def _when(ts: float | None) -> str:
    return (
        time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(float(ts))) if ts else "—"
    )


def _money(value: object) -> str:
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def _run_key(hop: dict, record: dict) -> str | None:
    """The investigator run behind a hop. From the door's extraction when the
    config carries `fields.session_key`; otherwise derived the way hookprobe
    names runs — `probe:<source of the event it was handed>:<that event's id>`
    — from the event this hop quoted, so operations recorded before the doors
    extracted the key still get their investigator half."""
    key = (hop.get("fields") or {}).get("session_key")
    if key:
        return str(key)
    quoted = str(hop.get("quotes") or "")
    quoted_id = quoted.removeprefix("hr-")
    if not quoted_id.isdigit() or not str(hop.get("source") or "").endswith("-notify"):
        return None
    for other in record.get("hops") or []:
        if str(other.get("id")) == quoted_id:
            return f"probe:{other.get('source')}:{quoted_id}"
    return None


def _startup_posture(sp: object) -> str:
    if not isinstance(sp, dict):
        return "not recorded"
    kube, aws = sp.get("kube") or {}, sp.get("aws") or {}
    bits = [f"verdict `{sp.get('verdict')}`", _when(sp.get("checked_at"))]
    if kube.get("present"):
        bits.append(
            f"kube: {len(kube.get('mutating') or [])} mutating rule(s)"
            + (f", {len(kube['errors'])} unanswered" if kube.get("errors") else "")
        )
    if aws.get("present"):
        bits.append(
            f"aws `{aws.get('identity') or '?'}`: {len(aws.get('allowed') or [])} dangerous action(s) allowed"
            + (" (unverifiable)" if aws.get("unverifiable") else "")
        )
    return " · ".join(bits)


def render(record: dict, runs: dict[str, dict | None]) -> str:
    hops = record.get("hops") or []
    totals = record.get("totals") or {}
    origin = hops[0] if hops else {}
    t0 = float(origin.get("received_at") or 0)
    out = [
        f"# Operation #{record.get('operation')} — {origin.get('title') or '(untitled)'}",
        "",
        f"- **Origin**: `{origin.get('source')}` at {_when(origin.get('received_at'))}, level `{origin.get('level')}`",
        f"- **Hops**: {totals.get('hops')} · **Cost**: {_money(totals.get('cost_usd'))} · "
        f"**Human actions**: {totals.get('human_actions')} · **End to end**: {round(float(totals.get('span_seconds') or 0))} s",
        f"- **Record generated**: {_when(record.get('generated_at'))}; bodies appear as sha256 + size — the bytes are under the pipe's `/trace`",
        "",
        "## Timeline",
        "",
        "| +s | hop | door | outcome | cost | payload | deliveries |",
        "| ---: | --- | --- | --- | ---: | --- | --- |",
    ]
    for h in hops:
        f = h.get("fields") or {}
        pl = h.get("payload") or {}
        dl = "<br>".join(
            f"{d.get('channel')} · {d.get('status')}"
            + (
                f" · {d['sent']['bytes']} B `{str(d['sent']['sha256'])[:12]}`"
                if d.get("sent")
                else ""
            )
            for d in h.get("deliveries") or []
        )
        outcome = h.get("outcome") or ""
        if h.get("skip_code"):
            outcome += f" · {h['skip_code']}"
        out.append(
            f"| {round(float(h.get('received_at') or 0) - t0)} | #{h.get('id')} | `{h.get('source')}` | {outcome} | "
            f"{_money(f.get('cost_usd')) if f.get('cost_usd') else ''} | "
            f"{(str(pl.get('bytes')) + ' B `' + str(pl.get('sha256'))[:12] + '`') if pl else ''} | {dl} |"
        )
    human = record.get("human_actions") or []
    out += ["", "## What a person did", ""]
    if human:
        out += ["| +s | action | outcome | actor |", "| ---: | --- | --- | --- |"]
        out += [
            f"| {round(float(a.get('pressed_at') or 0) - t0)} | {a.get('kind')} | {a.get('outcome') or ''} | `{a.get('actor') or ''}` |"
            for a in human
        ]
    else:
        out.append("Nobody pressed anything on this operation.")
    out += ["", "## What the investigator did", ""]
    any_run = False
    by_run: dict[str, list[int]] = {}
    for h in hops:
        key = _run_key(h, record)
        if key and runs.get(key) != MISSING:
            by_run.setdefault(key, []).append(int(h.get("id")))
    for key, hop_ids in by_run.items():
        any_run = True
        run = runs.get(key)
        out.append(f"### Run `{key}` — hops {', '.join('#' + str(i) for i in hop_ids)}")
        if run is None:
            out += [
                "",
                "_Not read: no investigator URL/token given, or the investigator could not be reached._",
                "",
            ]
            continue
        assert isinstance(run, dict)
        posture = run.get("posture")
        posture_line = (
            f"bash guard `{posture.get('bash_guard')}`, MCP tools allowed: {len(posture.get('mcp_tools') or [])}"
            if isinstance(posture, dict)
            else "not recorded — this run predates posture stamping (2026-09-07)"
        )
        out += [
            "",
            f"- **Status**: {run.get('status')} · **Model**: `{run.get('model')}` · **Run record cost**: {_money(run.get('cost_usd'))} (the pipe's per-hop costs above are what was billed)",
            f"- **Posture**: {posture_line}",
            f"- **Credential check at startup**: {_startup_posture(run.get('startup_posture'))}",
            f"- **Ruling**: {(run.get('ruling') or {}).get('verdict') or 'none'}",
            f"- **Tool calls**: {len(run.get('tool_calls') or [])} · **Refused by the guards**: {len(run.get('denied') or [])}",
        ]
        denied = run.get("denied") or []
        if denied:
            out += [
                "",
                "| guard | what it tried | why refused |",
                "| --- | --- | --- |",
            ]
            out += [
                f"| {d.get('guard')} | `{str(d.get('detail') or d.get('tool'))[:120]}` | {str(d.get('reason') or '')[:160]} |"
                for d in denied
            ]
        out.append("")
    if not any_run:
        out.append(
            "No investigator run is part of this operation"
            + (
                " (or none could be read: pass --probe and HOOKPROBE_TOKEN)."
                if not runs
                else "."
            )
        )
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("event_id", type=int)
    ap.add_argument(
        "--relay", default=os.environ.get("HOOKRELAY_URL", "http://127.0.0.1:8100")
    )
    ap.add_argument("--probe", default=os.environ.get("HOOKPROBE_URL", ""))
    args = ap.parse_args()
    relay_token = os.environ.get("HOOKRELAY_READ_TOKEN", "")
    probe_token = os.environ.get("HOOKPROBE_TOKEN", "")

    record = _get(f"{args.relay.rstrip('/')}/audit/{args.event_id}", relay_token)
    if record is None:
        print(
            f"could not fetch {args.relay}/audit/{args.event_id} (token? url?)",
            file=sys.stderr,
        )
        return 1
    runs: dict[str, dict | None] = {}
    if args.probe and probe_token:
        for h in record.get("hops") or []:
            key = _run_key(h, record)
            if key:
                runs[key] = _get(
                    f"{args.probe.rstrip('/')}/v1/runs/{urllib.parse.quote(key, safe='')}/audit",
                    probe_token,
                )
    sys.stdout.write(render(record, runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
