"""Removing or relinking a protected path from the shell is a write like any other.

`cp` and `mv` into the case files or the skills were refused while `rm` out of
them and `ln -s` into them passed: the evidence the next run is told to read
could be deleted, and a skill planted whose content lives wherever the run can
write. Both postures, because the protection is not about what a runner may
change in the world — it is about what steers and evidences the next run.
"""

from __future__ import annotations

import pytest

from hookprobe import gate
from hookprobe.guard import DANGER_ONLY, READONLY


def _refusal(tmp_path, command: str, mode: str):
    return gate.deny_reason(
        "Bash", {"command": command.format(w=tmp_path)}, bash_mode=mode, workdir=tmp_path, home=tmp_path
    )


@pytest.mark.parametrize("mode", [READONLY, DANGER_ONLY])
@pytest.mark.parametrize(
    "command",
    [
        "rm {w}/results/probe:alert:1.json",
        "rm -f {w}/CLAUDE.md",
        "unlink {w}/results/a.json",
        "rmdir {w}/.claude/skills/old",
        "shred -u {w}/results/a.json",
        "ln -sf /tmp/evil {w}/.claude/skills/evil",
        "touch {w}/.claude/skills/new/SKILL.md",
        "find {w}/results -name '*.json' -delete",
        "find {w}/results -name '*.json' -exec rm {{}} +",
        "cd {w} && rm results/a.json",
    ],
)
def test_removing_or_relinking_a_protected_path_is_refused(tmp_path, command: str, mode: str) -> None:
    assert _refusal(tmp_path, command, mode) is not None, (command, mode)


@pytest.mark.parametrize(
    "command",
    [
        "rm -f /tmp/scratch.txt",
        "rm {w}/scratch/old.log",
        "ln -s {w}/scratch/a /tmp/b",
        "touch /tmp/marker",
        "find {w}/results -name '*.json'",
        "find {w}/results -name '*.json' -exec cat {{}} +",
        "cat {w}/results/probe:alert:1.json",
    ],
)
def test_the_same_commands_elsewhere_or_reading_are_not_refused(tmp_path, command: str) -> None:
    assert _refusal(tmp_path, command, READONLY) is None, command
