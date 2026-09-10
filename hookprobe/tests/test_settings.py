"""What the process reads out of its environment, and what an absent value means.

Split from the suites that USE settings because the question here is different:
not "does the service behave" but "does a deployment's silence mean what the
code thinks it means".
"""

from __future__ import annotations


def test_empty_is_the_same_as_unset(monkeypatch) -> None:
    """A compose passing `${NAME:-}` sets the variable to an EMPTY STRING when
    .env is silent — not to nothing. So a default written as
    `os.environ.get(NAME, default)` never applies inside a container: the key
    exists and its value is "".

    Pinned across every knob a compose can pass empty, rather than one at a
    time, after adding 34 passthroughs on 2026-09-10 made the hazard general.
    hookprobe already held the invariant everywhere — `(get() or "").strip() or
    default` is its house style — and this keeps it that way; hookjudge did not,
    and an empty `HOOKJUDGE_AI_MODEL` reached its provider as a request with no
    model in it.
    """
    import dataclasses
    import re
    from pathlib import Path

    from hookprobe.settings import Settings

    root = Path(__file__).resolve().parent.parent.parent
    composes = [c.read_text(encoding="utf-8") for c in root.glob("**/docker-compose*.yml") if ".venv" not in c.parts]
    names = sorted({n for text in composes for n in re.findall(r"\$\{(HOOKPROBE_[A-Z0-9_]+):-\}", text)})
    assert len(names) >= 20, f"only {len(names)} emptyable knobs found; the compose parse has lost the files"

    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOOKPROBE_WORKDIR", "/tmp/hookprobe-empty-test")
    unset = dataclasses.asdict(Settings.load())
    for name in names:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("HOOKPROBE_WORKDIR", "/tmp/hookprobe-empty-test")
    empty = dataclasses.asdict(Settings.load())

    # The agent bearer is generated per process when unset, so it differs by
    # design on every load — that is the point of it.
    moved = {k: (unset[k], empty[k]) for k in unset if unset[k] != empty[k] and k != "agent_token"}
    assert not moved, f"passing these knobs empty is not the same as leaving them unset: {moved}"
