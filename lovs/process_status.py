"""Status marker convention for `.process/<change-id>/` directories.

A change-id directory MAY carry one of four marker files:

    STATUS.md       active   (default if absent and dir younger than 24h)
    PARKED.md       parked   (work paused intentionally; not held to current
                              sidecar requirements)
    SHIPPED.md      shipped  (code landed in main; audit trail preserved but
                              new changes to the artifacts no longer gated)
    DEPRECATED.md   deprecated (never shipped, no longer planned; excluded
                              from all gates)

If no marker exists and the directory's `plan.md` is older than 24 hours,
``read_status`` returns ``"rot"`` instead. That state is a soft signal
visible to the process-health gate and the operator dashboard so unattended
change-ids surface rather than rotting silently.

Stdlib-only. Atomic write via ``_atomic_write_text`` mirrors the convention
in ``cycle_status.py:83``.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
from typing import Literal

Status = Literal["active", "parked", "shipped", "deprecated", "rot"]

_MARKER_TO_STATUS: dict[str, Status] = {
    "STATUS.md": "active",
    "PARKED.md": "parked",
    "SHIPPED.md": "shipped",
    "DEPRECATED.md": "deprecated",
}

ROT_AGE_SECONDS = 24 * 60 * 60


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Atomic write: tempfile + os.replace + BaseException cleanup.

    Stronger than the deterministic-.tmp pattern at ``cycle_status.py:83``:
    ``mkstemp`` is concurrent-safe and ``BaseException`` covers
    ``KeyboardInterrupt``. Mirrors the durable-write convention from the
    workspace's ``feedback_atomic_csv_writes`` rule.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def read_status(change_dir: pathlib.Path, now: float | None = None) -> Status:
    """Resolve the status of ``change_dir`` from its marker files + age.

    Order of precedence: explicit marker file wins. If none present and the
    directory's `plan.md` mtime is older than 24h, status is ``"rot"``.
    Otherwise the default is ``"active"``.

    ``now`` lets tests inject a deterministic clock; production callers omit.
    """
    for marker, status in _MARKER_TO_STATUS.items():
        if (change_dir / marker).is_file():
            return status
    plan_path = change_dir / "plan.md"
    if not plan_path.is_file():
        return "active"
    mtime = plan_path.stat().st_mtime
    now_ts = now if now is not None else time.time()
    if now_ts - mtime > ROT_AGE_SECONDS:
        return "rot"
    return "active"


def write_status(change_dir: pathlib.Path, status: Status, *, note: str = "") -> pathlib.Path:
    """Write a marker file declaring ``status`` for ``change_dir``.

    Removes any pre-existing marker for the same change_dir to enforce
    single-status semantics. The body is a one-line plain-text declaration
    plus optional operator ``note``. Returns the path of the new marker.
    """
    marker_name = {v: k for k, v in _MARKER_TO_STATUS.items()}.get(status)
    if marker_name is None:
        raise ValueError(f"write_status: status {status!r} has no marker file")
    for existing in _MARKER_TO_STATUS:
        candidate = change_dir / existing
        if candidate.is_file():
            candidate.unlink()
    target = change_dir / marker_name
    body = f"status: {status}\n"
    if note:
        body += f"note: {note}\n"
    _atomic_write_text(target, body)
    return target


def iter_change_dirs(process_root: pathlib.Path) -> list[pathlib.Path]:
    """List every `YYYY-NN-NN-*` subdirectory of `process_root` deterministically."""
    if not process_root.is_dir():
        return []
    out: list[pathlib.Path] = []
    for entry in sorted(process_root.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if len(name) < 11 or name[4] != "-" or name[7] != "-" or name[10] != "-":
            continue
        out.append(entry)
    return out
