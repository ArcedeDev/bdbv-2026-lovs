"""CDC data-as-of fidelity gate.

Forward-looking tripwire that re-parses the archived raw HTML for each CDC
current-situation manifest entry and asserts the manifest's stored
``data_as_of`` still matches what the canonical ingest parser produces. It
catches three classes of drift that would otherwise be invisible:

  - a hand-edit of ``normalized_content.data_as_of`` that did not re-run ingest
  - a parser regression that altered which "As of <date>" phrase the regex
    selects (CDC pages currently expose two such phrases per page, with
    distinct semantic roles, see ``lovs.lovs_live_ingest``)
  - a partial re-archive where the manifest entry was updated but the raw
    bytes were not, or vice versa

Findings are split into two buckets so the gate is useful both today and as
retention coverage widens:

  - ``mismatches``: the parser produced a ``data_as_of`` that differs from
    the manifest's stored value. Fails the release gate.
  - ``unverifiable``: the raw HTML is not on disk, or the parser could not
    extract a ``data_as_of`` from the bytes it did find. Reported as info,
    not a failure, because historical CDC archives may not have been retained
    and re-archiving cdc.gov today would fetch updated rather than original
    content.

The new entry checked for every release locks the contract going forward:
once raw bytes for a CDC entry are on disk and its manifest data_as_of has
been agreed, a later silent drift in either direction surfaces here.

The ``normalized_content.raw_retention_required`` per-entry contract closes
the obvious bypass. Once an entry's raw bytes have been archived, set this
flag to ``true`` on the manifest entry; the gate will then refuse to demote
a missing raw file to ``unverifiable`` and instead escalate it to
``mismatches`` (release fails). Entries without the flag (or with it set to
``false``) preserve the legacy behavior so historical CDC entries that were
never archived stay info-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from lovs.lovs_live_ingest import _extract_visible_text, extract_cdc_current_situation_counts

CDC_SOURCE_PREFIX = "cdc-current-situation-"


def _iter_cdc_entries(
    manifest: dict,
) -> Iterator[tuple[str, str | None, bool]]:
    """Yield ``(source_id, stored_data_as_of, raw_retention_required)``.

    ``stored_data_as_of`` may legitimately be ``None`` for very old entries;
    the caller treats that as ``unverifiable`` rather than ``mismatch``.

    ``raw_retention_required`` reflects the per-entry retention contract on
    ``normalized_content.raw_retention_required``. When ``True``, the caller
    must escalate a missing raw file to ``mismatches`` instead of demoting
    it to ``unverifiable``. The check is strict identity (``is True``) so
    only the JSON literal ``true`` triggers escalation; a string ``"true"``,
    integer ``1``, or any other truthy non-bool is treated as unset. This is
    the conservative reading: a hand-edited manifest typo cannot accidentally
    enroll an entry into the contract. Defaults to ``False`` so legacy
    entries that were never archived keep their info-only behavior.
    """
    for entry in manifest.get("entries", []):
        source_id = str(entry.get("source_id", ""))
        if not source_id.startswith(CDC_SOURCE_PREFIX):
            continue
        normalized = entry.get("normalized_content") or {}
        stored = normalized.get("data_as_of")
        retention_required = normalized.get("raw_retention_required") is True
        yield (
            source_id,
            stored if isinstance(stored, str) else None,
            retention_required,
        )


def check_cdc_data_as_of_matches_raw(
    manifest_path: Path,
    sources_dir: Path,
) -> dict:
    """Run the fidelity check across every CDC entry in the manifest.

    Returns a dict ``{"checked": int, "mismatches": list[str], "unverifiable":
    list[str]}``. The caller is expected to fail closed when ``mismatches`` is
    non-empty and to print ``unverifiable`` as informational lines.

    The function reads on-disk state only (manifest, raw HTML) and performs no
    network or time-dependent work, so it is deterministic across runs of the
    same repository state.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    checked = 0
    mismatches: list[str] = []
    unverifiable: list[str] = []
    for source_id, stored, retention_required in _iter_cdc_entries(manifest):
        raw_path = Path(sources_dir) / f"{source_id}.html"
        if not raw_path.is_file():
            if retention_required:
                mismatches.append(
                    f"{source_id}: raw_retention_required=true but raw HTML "
                    f"missing at {raw_path}"
                )
            else:
                unverifiable.append(
                    f"{source_id}: no retained raw HTML at {raw_path}"
                )
            continue
        try:
            raw_bytes = raw_path.read_bytes()
            text = _extract_visible_text(raw_bytes)
            parsed = extract_cdc_current_situation_counts(text)
        except (OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
            unverifiable.append(
                f"{source_id}: parser raised {type(exc).__name__} on {raw_path}: {exc}"
            )
            continue
        parsed_data_as_of = parsed.get("data_as_of") if isinstance(parsed, dict) else None
        if not isinstance(parsed_data_as_of, str):
            unverifiable.append(
                f"{source_id}: parser produced no data_as_of from {raw_path}"
            )
            continue
        checked += 1
        if stored is None:
            mismatches.append(
                f"{source_id}: stored data_as_of is null but parser produced {parsed_data_as_of}"
            )
            continue
        if parsed_data_as_of != stored:
            mismatches.append(
                f"{source_id}: stored={stored}, parsed={parsed_data_as_of}"
            )
    return {"checked": checked, "mismatches": mismatches, "unverifiable": unverifiable}
