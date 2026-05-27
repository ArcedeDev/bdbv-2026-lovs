# SPDX-License-Identifier: Apache-2.0
"""Canonical source-id helpers shared across contract validators.

Snapshot primaries carry the canonical (suffix-stripped) form of a source id,
while some manifest entries carry a ``-live`` suffix for live captures (the
recurring ECDC outbreak-page pattern is the canonical example). Contract
validators that compare a snapshot primary against a manifest entry, or a
manifest source id against a CSV row, must canonicalise both sides or else
trip on a non-issue when only the live-capture sidecar exists.

This module is the single source of truth for that canonicalisation. Before
it landed, ``publication_clock_contract._find_manifest_entry`` and
``snapshot_contract._canon`` each carried their own copy of the rule, and the
duplication shipped a real defect on the May-27 release cycle.

Stdlib only. No clock, no network. Functions are pure.
"""
from __future__ import annotations

from typing import Any, Iterable


LIVE_SUFFIX = "-live"


def canonical_source_id(source_id: str | None) -> str:
    """Strip the ``-live`` live-capture suffix if present.

    A snapshot primary is always canonical. A manifest entry may be either
    canonical (``ecdc-bdbv-drc-uga-2026-05-27``) or live-suffixed
    (``ecdc-bdbv-drc-uga-2026-05-27-live``). Comparing either side against the
    other requires canonicalising first.

    Returns the empty string for ``None`` / missing input so callers can chain
    without nullability handling.
    """
    if not source_id:
        return ""
    sid = str(source_id)
    if sid.endswith(LIVE_SUFFIX):
        return sid[: -len(LIVE_SUFFIX)]
    return sid


def source_ids_match(a: str | None, b: str | None) -> bool:
    """True when two source ids refer to the same canonical source.

    Handles the live-suffix mismatch transparently in both directions:
    ``("ecdc-x", "ecdc-x-live")`` matches and so does the reverse.
    """
    ca = canonical_source_id(a)
    cb = canonical_source_id(b)
    return bool(ca) and ca == cb


def find_manifest_entry_by_source_id(
    manifest_entries: Iterable[dict[str, Any]], source_id: str | None
) -> dict[str, Any] | None:
    """Find the first manifest entry that matches ``source_id`` canonically.

    Iteration order is preserved so an exact match wins over a suffix-only
    match if both are present (an unusual but possible case during a
    transition cycle where both the live capture and a canonical mirror have
    been archived).
    """
    if not source_id:
        return None
    target = canonical_source_id(source_id)
    exact_match = None
    canonical_match = None
    for entry in manifest_entries:
        manifest_id = entry.get("source_id", "")
        if manifest_id == source_id:
            exact_match = entry
            break
        if canonical_source_id(manifest_id) == target and canonical_match is None:
            canonical_match = entry
    return exact_match if exact_match is not None else canonical_match
