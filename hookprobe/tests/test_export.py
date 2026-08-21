"""Runbooks packaged to leave — what travels, what does not, and what to read first.

The hazard here is not a crash, it is a successful publication of somebody's
internal detail. A real production runbook contains a brand, an internal
hostname, a session key and an exact figure; every one of those lives on the
CASES side of `distill.CASES_MARKER`, which is why the export cuts there instead
of running a regex over model prose.
"""

from __future__ import annotations

from pathlib import Path

from hookprobe import export
from hookprobe.distill import CASES_MARKER

CONSOLIDATED = f"""---
name: datasourcenodata
description: What previous investigations of "DatasourceNoData" checked.
---

# DatasourceNoData

## 排查步骤（按顺序）

1. Read the payload's `valueString`. `value=null` means Grafana entered a NoData
   state; it is not a reading of zero.

## 常见结论与处置

Alarm-test threshold rules with no data behind them.

## Investigations

{CASES_MARKER}

<!-- case:start 1787122639 -->
### 2026-08-19 06:57 · session `hook:deep-analysis:grafana:SESSION-UUID`

examplecorp 平台（grafana.examplecorp.local）退信率达到 10.3%，联系 ops@examplecorp.example。
Checked 192.0.2.10 and https://internal.examplecorp.local/dashboard.
<!-- case:end -->
"""

AUTO_WRITTEN = f"""---
name: ses-bounce-spike
description: What previous investigations of "[SES] Bounce spike" checked.
---

# [SES] Bounce spike

## Investigations

{CASES_MARKER}

<!-- case:start 1787107285 -->
### 2026-08-19 02:41 · session `hook:deep-analysis:grafana:SESSION-UUID`

examplecorp reputation at 10.3% on grafana.examplecorp.local.
<!-- case:end -->
"""


def _skills(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / ".claude" / "skills"
    for name, text in files.items():
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(text, encoding="utf-8")
    return root


def test_the_cases_do_not_travel_and_the_procedure_does(tmp_path: Path) -> None:
    """Every identifying detail in a real runbook was on the cases side."""
    bundle = export.bundle(_skills(tmp_path, datasourcenodata=CONSOLIDATED))

    assert [row["name"] for row in bundle["exported"]] == ["datasourcenodata"]
    content = bundle["exported"][0]["content"]

    # The procedure travels, in whatever language it was written in.
    assert "排查步骤" in content and "value=null" in content

    # And none of the case detail does. These are the exact shapes production has.
    for leaked in (
        "examplecorp",
        "grafana.examplecorp.local",
        "hook:deep-analysis:grafana:cce3ff2e",
        "10.3%",
        "ops@examplecorp.example",
        "192.0.2.10",
    ):
        assert leaked not in content, f"{leaked!r} left the building"

    assert bundle["exported"][0]["cases_dropped"] == 1
    # The heading that introduces the cases goes with them.
    assert "Investigations" not in content
    assert CASES_MARKER not in content


def test_an_unconsolidated_runbook_is_named_rather_than_shipped_empty(tmp_path: Path) -> None:
    """On production, six of eight runbooks are case piles with a heading.

    Cutting the cases out of one leaves frontmatter and a title — a file whose
    only real content was just removed. Shipping that would look like a runbook
    and teach nothing, so they are named instead, with their case count, which is
    also the list of runbooks worth consolidating.
    """
    bundle = export.bundle(_skills(tmp_path, sesbouncespike=AUTO_WRITTEN, datasourcenodata=CONSOLIDATED))

    assert [row["name"] for row in bundle["exported"]] == ["datasourcenodata"]
    omitted = {row["name"]: row for row in bundle["omitted"]}
    assert set(omitted) == {"sesbouncespike"}
    assert omitted["sesbouncespike"]["cases"] == 1
    assert "not consolidated" in omitted["sesbouncespike"]["reason"]


def test_what_survives_the_cut_is_reported_not_silently_trusted(tmp_path: Path) -> None:
    """The module does not get to call anything safe.

    A hostname in a procedure might be an example or an internal name, and only
    somebody who knows the deployment can say. So identifying shapes in the
    EXPORTED text are listed for a person to read, never rewritten — the one
    exception being credentials, which are redacted below.
    """
    leaky = CONSOLIDATED.replace(
        "1. Read the payload's `valueString`.",
        "1. Read the payload on grafana.examplecorp.local (192.0.2.10), then valueString.",
    )
    bundle = export.bundle(_skills(tmp_path, datasourcenodata=leaky))
    content = bundle["exported"][0]["content"]

    # Still present — reported, not scrubbed.
    assert "grafana.examplecorp.local" in content
    kinds = {row["kind"]: row for row in bundle["review"]}
    assert "internal hostname" in kinds and "ip address" in kinds
    assert "grafana.examplecorp.local" in kinds["internal hostname"]["examples"]
    assert all(row["skill"] == "datasourcenodata" for row in bundle["review"])


def test_a_credential_in_a_procedure_is_redacted_not_reported(tmp_path: Path) -> None:
    """The one thing that is not a judgement call. A missed hostname is
    embarrassing; a missed token is an incident, so it does not wait for review."""
    with_secret = CONSOLIDATED.replace(
        "1. Read the payload's `valueString`.",
        "1. Run `curl -H 'Authorization: Bearer sk-live-abcdef1234567890' https://api.example/x`.",
    )
    bundle = export.bundle(_skills(tmp_path, datasourcenodata=with_secret))
    content = bundle["exported"][0]["content"]

    assert "sk-live-abcdef1234567890" not in content, "a credential left the building"
    assert "curl" in content, "the step itself survives; only the secret goes"


def test_nothing_consolidated_is_an_answer_not_an_error(tmp_path: Path) -> None:
    bundle = export.bundle(_skills(tmp_path, sesbouncespike=AUTO_WRITTEN))
    assert bundle["exported"] == []
    assert bundle["review"] == []
    assert len(bundle["omitted"]) == 1


def test_the_export_route_is_matched_before_the_name_route(tmp_path: Path) -> None:
    """FastAPI matches in declaration order, so /v1/skills/export registered
    after /v1/skills/{name} is read as a runbook called "export" — a 404 that
    looks like a missing feature rather than a routing mistake."""
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    _skills(tmp_path, datasourcenodata=CONSOLIDATED)
    settings = make_settings(tmp_path, workdir=tmp_path)
    client = TestClient(create_app(settings, RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))))

    answer = client.get("/v1/skills/export", headers={"Authorization": f"Bearer {settings.token}"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert [row["name"] for row in body["exported"]] == ["datasourcenodata"]
    assert "review" in body and "note" in body

    # And it is still guarded, like every other route on this service.
    assert client.get("/v1/skills/export").status_code == 401
