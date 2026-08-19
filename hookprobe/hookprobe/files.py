"""Where the volume's editable files live, and how a write lands on one.

Two facts several modules have to agree on, and used to each restate.

The first is the appended methodology's path: the configured one wins, otherwise
the convention path {workdir}/system-prompt.md. That expression appeared six
times — in the engine that loads the file, the endpoints that read and write it,
and the config view that reports where it is — so moving the convention meant
moving it in six places, and a read path that disagreed with the load path would
have looked exactly like an operator's edit failing to apply.

The second is that a write to any of these files replaces it whole: bytes into a
sibling .tmp, then one rename. A reader sees either the old file or the new one,
never a half-written prompt — and a truncated CLAUDE.md is the dangerous case,
because nothing about it looks like an error to the run that loads it.
"""

from __future__ import annotations

from pathlib import Path

from hookprobe.settings import Settings


def system_prompt_path(settings: Settings) -> Path:
    """The appended methodology's file: the configured path, or the convention one."""
    return settings.system_prompt_append or (settings.workdir / "system-prompt.md")


def atomic_write(path: Path, raw: bytes) -> None:
    """Replace `path` with `raw` in one rename, leaving no partial file behind.

    The .tmp is removed on failure and the error re-raised: callers disagree on
    whether a failed write is fatal (an operator's PUT, which owes them a 500) or
    best-effort (a run record persisting itself, where the in-memory copy still
    serves), but neither of them wants the litter left on the volume.
    """
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(raw)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
