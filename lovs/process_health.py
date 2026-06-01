"""Process-health gate.

Walks `.process/<change-id>/` directories across one or more repos and
returns structured findings:

  - hard findings (fail the release gate):
      * missing required findings sidecar (plan.md, validation.md, review.md
        each require a `.findings.json` companion) for any ``active`` change-id
      * em-dash character in any `.md` artifact under an ``active`` or ``rot``
        change-id (forward-looking rule; the pre-commit hook is the primary
        catcher at commit time, this is the release-time safety net)

  - soft findings (informational; printed but do not fail):
      * change-id with status ``rot`` (no marker and plan.md older than 24h)
      * em-dashes in ``parked`` change-ids (preserved but flagged)

Shipped and deprecated change-ids are skipped entirely (closed-book audit
trail; em-dashes that landed before the gate existed are not retroactively
required to be swept).

Stdlib-only.
"""

from __future__ import annotations

import pathlib

from lovs.process_status import iter_change_dirs, read_status

EM_DASH = "—"

_SIDECAR_REQUIRED_FOR: tuple[str, ...] = ("plan.md", "validation.md", "review.md")


def _scan_em_dashes(change_dir: pathlib.Path) -> list[str]:
    findings: list[str] = []
    for md_path in sorted(change_dir.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        count = text.count(EM_DASH)
        if count > 0:
            findings.append(
                f"{md_path.relative_to(change_dir.parent.parent)}: {count} em-dash"
                + ("es" if count != 1 else "")
            )
    return findings


def _scan_sidecars(change_dir: pathlib.Path) -> list[str]:
    findings: list[str] = []
    for required in _SIDECAR_REQUIRED_FOR:
        md = change_dir / required
        if not md.is_file():
            continue
        sidecar = change_dir / f"{required}.findings.json"
        if not sidecar.is_file():
            findings.append(
                f"{md.relative_to(change_dir.parent.parent)}: missing sidecar "
                f"{sidecar.name}"
            )
    return findings


def check_process_health(process_roots: list[pathlib.Path]) -> dict:
    """Scan every `.process/` root and return ``{hard, soft, scanned}``.

    ``hard`` causes the release gate to fail. ``soft`` is info-only.
    ``scanned`` is the count of change-id directories inspected.
    """
    hard: list[str] = []
    soft: list[str] = []
    scanned = 0
    for root in process_roots:
        root = pathlib.Path(root)
        for change_dir in iter_change_dirs(root):
            scanned += 1
            status = read_status(change_dir)
            if status in ("shipped", "deprecated"):
                continue
            em_findings = _scan_em_dashes(change_dir)
            if status in ("active", "rot"):
                hard.extend(em_findings)
                if status == "active":
                    hard.extend(_scan_sidecars(change_dir))
                if status == "rot":
                    soft.append(
                        f"{change_dir.name}: no STATUS marker and plan.md older than 24h"
                    )
            elif status == "parked":
                soft.extend(em_findings)
    return {"hard": hard, "soft": soft, "scanned": scanned}
