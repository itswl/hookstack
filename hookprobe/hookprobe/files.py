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

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hookprobe.settings import Settings


def system_prompt_path(settings: Settings) -> Path:
    """The appended methodology's file: the configured path, or the convention one."""
    return settings.system_prompt_append or (settings.workdir / "system-prompt.md")


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `path` for a read-modify-write cycle.

    Every read-modify-write in this service was correct only because it was
    synchronous and one event loop serialised it — an invariant nothing wrote
    down and nothing enforced. It breaks the moment there is a second writer:
    `uvicorn --workers 2`, or a second replica on the same volume. The
    suggestions queue is the clearest loss — resolve() reads every row, drops
    one and writes the rest back, so an append that landed in between is simply
    gone.

    A neighbouring `.lock` file rather than the file itself, because the cycle
    replaces its target by rename: locking an inode that is about to be unlinked
    protects nothing after the rename. flock is advisory and per-open-file, which
    is exactly the scope wanted here, and it releases when the process dies —
    a crash mid-cycle must not leave the queue permanently unwritable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = path.with_name(path.name + ".lock")
    with guard.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, raw: bytes) -> None:
    """Replace `path` with `raw` in one rename, leaving no partial file behind.

    The .tmp is removed on failure and the error re-raised: callers disagree on
    whether a failed write is fatal (an operator's PUT, which owes them a 500) or
    best-effort (a run record persisting itself, where the in-memory copy still
    serves), but neither of them wants the litter left on the volume.
    """
    # The scratch name carries the pid: `path.with_suffix(".tmp")` gave every
    # writer of one path the SAME scratch file, so two of them interleaved their
    # bytes there and whichever renamed second published the mixture.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(raw)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
