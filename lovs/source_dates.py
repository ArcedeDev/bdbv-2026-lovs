"""Source date semantics shared by release, export, and website sync.

LOVS snapshots have three distinct clocks:

- data/report date: the date the epidemiological observation describes.
- publication date: the date the source made the observation available.
- retrieval date: the date the archive captured the source bytes.

Charts should use the data/report date when a source exposes one. Snapshot
cadence should use publication date, because a snapshot is a knowledge-state
artifact. Retrieval date is only archive latency.
"""
from __future__ import annotations

from typing import Any


DATA_DATE_FIELDS: tuple[str, ...] = (
    "data_as_of",
    "as_of_date",
    "date_rapportage",
    "report_date",
    "reporting_date",
    "evidence_as_of",
)

PUBLICATION_DATE_FIELDS: tuple[str, ...] = (
    "publication_date",
    "date_publication",
    "published_at",
)


def date_part(value: Any) -> str | None:
    """Return YYYY-MM-DD from a bare date or ISO-like timestamp."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    token = value[:10]
    if (
        len(token) == 10
        and token[4] == "-"
        and token[7] == "-"
        and token[:4].isdigit()
        and token[5:7].isdigit()
        and token[8:10].isdigit()
    ):
        return token
    return None


def source_report_date(entry: dict[str, Any]) -> str | None:
    """Explicit data/report date, if the source exposes one."""
    normalized = entry.get("normalized_content") or {}
    for field in DATA_DATE_FIELDS:
        value = date_part(normalized.get(field))
        if value:
            return value
    return None


def source_data_date(entry: dict[str, Any]) -> str | None:
    """Best data/report date for plotting and source-row as-of fields."""
    report_date = source_report_date(entry)
    if report_date:
        return report_date
    normalized = entry.get("normalized_content") or {}
    if any(field in normalized for field in DATA_DATE_FIELDS):
        # Some structured sources expose the report-date field but explicitly
        # return null. In that case publication is a freshness clock, not a
        # safe substitute for the epidemiologic data/report date.
        return None
    return source_publication_date(entry)


def source_publication_date(entry: dict[str, Any]) -> str | None:
    """Best source-publication date for snapshot cadence and availability."""
    normalized = entry.get("normalized_content") or {}
    for field in PUBLICATION_DATE_FIELDS:
        value = date_part(normalized.get(field))
        if value:
            return value
    return date_part(entry.get("published_at"))


def source_retrieval_date(entry: dict[str, Any]) -> str | None:
    return date_part(entry.get("retrieved_at"))


def source_availability_date(entry: dict[str, Any]) -> str | None:
    """Date a source was knowably available to the archive pipeline."""
    return source_publication_date(entry) or source_retrieval_date(entry)


def source_available_by_snapshot(entry: dict[str, Any], snapshot_date: str | None) -> bool:
    """True when a source belongs on public surfaces for a snapshot date.

    Snapshots are knowledge-state artifacts keyed by publication availability.
    Undated sources are excluded from dated public surfaces until reviewed.
    """
    cutoff = date_part(snapshot_date)
    if not cutoff:
        return True
    available = source_availability_date(entry)
    return available is not None and available <= cutoff


def entries_for_snapshot(
    entries: list[dict[str, Any]],
    snapshot_date: str | None,
) -> list[dict[str, Any]]:
    return [
        entry
        for entry in entries
        if source_available_by_snapshot(entry, snapshot_date)
    ]
