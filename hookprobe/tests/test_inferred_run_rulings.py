"""A patrol can fill the worth column, and the column says it was inferred."""

from __future__ import annotations

import pytest

from hookprobe import run_rulings
from hookprobe.runs import INFERRED_BY_PREFIX


def test_a_proposed_ruling_is_lifted_out_of_the_report() -> None:
    """Same posture as AI-RULING and MEMORY-SUGGESTION: the patrol proposes in
    prose and the service files. The marker line must not survive into the
    report, because the report travels to a chat card and a machine-to-service
    line rendered there is noise."""
    report = (
        "Read nine unruled runs.\n\n"
        'RUN-RULING: {"sessionKey": "hook:deep-analysis:a", "ruling": "useless", "why": "no evidence beyond payload"}\n'
        'RUN-RULING: {"sessionKey": "hook:deep-analysis:b", "ruling": "useful", "why": "named the failing broker"}\n'
        "\nBacklog: 135 still unruled.\n"
    )

    stripped, filed = run_rulings.extract(report)

    assert [row["sessionKey"] for row in filed] == ["hook:deep-analysis:a", "hook:deep-analysis:b"]
    assert "RUN-RULING" not in stripped
    assert "run rulings filed" in stripped, "a section that silently loses its contents reads like an ignored prompt"
    assert "Backlog: 135 still unruled." in stripped


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ('RUN-RULING: {"sessionKey": "a", "ruling": "brilliant", "why": "x"}', "unknown ruling"),
        ('RUN-RULING: {"sessionKey": "a", "ruling": "useful"}', "no reason given"),
        ('RUN-RULING: {"ruling": "useful", "why": "x"}', "no run named"),
        ("RUN-RULING: not json at all", "not parseable"),
    ],
)
def test_a_malformed_ruling_files_nothing_and_is_still_stripped(line: str, reason: str) -> None:
    """Dropped here rather than filed and refused later: a report that says it
    ruled three and ruled none is the failure worth avoiding. Stripped either
    way, or the broken line rides out to a card."""
    stripped, filed = run_rulings.extract(f"body\n{line}\n")

    assert filed == [], reason
    assert "RUN-RULING" not in stripped, "a malformed marker must not reach a chat card"


def test_one_verdict_per_run_and_a_bounded_batch() -> None:
    """Twenty is the cap, higher than AI-RULING's five because the backlog is the
    point — 144 runs were unruled the day the write path landed, and a patrol
    that clears five a week never catches up."""
    duplicated = "\n".join(
        f'RUN-RULING: {{"sessionKey": "k", "ruling": "useful", "why": "reason {n}"}}' for n in range(3)
    )
    many = "\n".join(f'RUN-RULING: {{"sessionKey": "k{n}", "ruling": "useful", "why": "reason"}}' for n in range(30))

    _, deduped = run_rulings.extract(duplicated)
    _, capped = run_rulings.extract(many)

    assert [row["why"] for row in deduped] == ["reason 0"], "the first verdict on a run wins"
    assert len(capped) == 20


def test_an_inferred_verdict_is_counted_apart_from_a_person_s(tmp_path) -> None:
    """The whole reason the patrol is allowed to do this.

    A worth figure is quoted at somebody deciding whether to pay for the service.
    If a patrol's inference and a person's judgement land in one number, that
    figure is this system's opinion of itself wearing a human's voice — which is
    worse than the `0` it replaced, because `0` at least read as an absence.
    """
    from hookprobe.runs import COMPLETED, Run, RunStore

    store = RunStore(tmp_path)
    now = 1_000_000.0
    for key in ("by-person", "by-patrol"):
        run = Run(run_id=key, session_key=key, status=COMPLETED)
        run.turns = [{"finished_at": now, "cost_usd": 0.4}]
        store.checkpoint(run)

    person = store.get("by-person")
    assert person is not None
    person.ruling = "useful"
    person.ruled_by = "ou_abc123"
    store.annotate(person)

    patrol = store.get("by-patrol")
    assert patrol is not None
    patrol.ruling = "useful"
    patrol.ruled_by = f"{INFERRED_BY_PREFIX}hook:patrol:1"
    patrol.ruled_why = "named the failing broker"
    store.annotate(patrol)

    investigations, useful, useless, inferred = store.rulings_since(now - 3600)

    assert (investigations, useful, useless) == (2, 2, 0)
    assert inferred == 1, "a patrol's verdict counts as ruled AND as inferred, never as a person's"


def test_the_reason_survives_a_restart(tmp_path) -> None:
    """An inferred verdict is only worth having if it can be audited later, and
    that means the reason has to be on disk, not in the object that wrote it."""
    from hookprobe.runs import COMPLETED, Run, RunStore

    store = RunStore(tmp_path)
    run = Run(run_id="k", session_key="k", status=COMPLETED)
    run.turns = [{"finished_at": 1_000_000.0, "cost_usd": 0.1}]
    run.ruling = "useless"
    run.ruled_by = "patrol:x"
    run.ruled_why = "no evidence gathered beyond the payload"
    store.checkpoint(run)

    fresh = RunStore(tmp_path).get("k")

    assert fresh is not None
    assert fresh.ruled_why == "no evidence gathered beyond the payload"
    assert fresh.ruled_by == "patrol:x"


def test_a_report_whose_every_marker_is_broken_still_loses_them() -> None:
    """The regex matches the MARKER rather than valid JSON precisely so a
    malformed line cannot ride out to a chat card — but that only held when
    something else parsed, because the "nothing filed" path returned early with
    the text untouched. The same early return was in the condition-ruling module
    and no test covered either.
    """
    from hookprobe import rulings

    broken = "body\nAI-RULING: not json at all\nRUN-RULING: also not json\n"

    ai_stripped, ai_filed = rulings.extract(broken)
    run_stripped, run_filed = run_rulings.extract(broken)

    assert (ai_filed, run_filed) == ([], [])
    assert "AI-RULING" not in ai_stripped
    assert "could not file" in ai_stripped, "silence reads like a model that ignored the instruction"
    assert "RUN-RULING" not in run_stripped
    assert "could not file" in run_stripped
