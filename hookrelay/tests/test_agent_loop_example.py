"""The shipped agent-loop example stays loadable and reads what it claims.

Examples nothing tests rot — test_extensibility.py says it of the plugins,
and this config carries more than syntax: the `agent-loop` title prefix is
what the weekly-loop-review patrol queries on, and `alertname`/`origin` are
what wire loop events into the judge's reuse and burst machinery. Any of
those drifting silently would leave the example teaching a wiring that no
longer works.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from hookrelay.config import Config
from hookrelay.extract import extract_event

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agent-loops.yaml"

RED_EVAL = {
    "loop": "deploy-eval",
    "outcome": "red",
    "summary": "missed=1 false_quiet=0 — the gate stopped the deploy",
    "actor": "deploy.sh",
    "origin": "hookstack",
}
GREEN_GATE = {
    "loop": "stack-gate",
    "outcome": "green",
    "summary": "every component gate + the stack checks",
    "actor": "gate.sh",
    "origin": "hookstack",
}


def _door():
    return Config.from_dict(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))).sources["agent-loop"]


def test_the_example_loads_through_the_real_config_loader():
    assert _door().name == "agent-loop"


def test_a_red_eval_reads_as_a_high_alert_with_the_loop_as_its_rule():
    read = extract_event(_door(), RED_EVAL)
    assert read["title"] == "agent-loop deploy-eval: red"
    assert read["level"] == "high"
    assert read["fields"]["alertname"] == "deploy-eval", "the loop is the rule; verdicts reuse across its firings"
    assert read["fields"]["origin"] == "hookstack", "burst grouping keys on origin — three failures, one story"


def test_a_green_gate_reads_as_info_and_keeps_the_queryable_prefix():
    read = extract_event(_door(), GREEN_GATE)
    assert read["level"] == "info"
    assert read["title"].startswith("agent-loop "), "the prefix is what /status?q=agent-loop matches on"


def test_a_shape_the_door_does_not_know_still_lands_readable():
    read = extract_event(_door(), {"unrelated": "shape"})
    assert read["title"], "poorly titled, never dropped"
