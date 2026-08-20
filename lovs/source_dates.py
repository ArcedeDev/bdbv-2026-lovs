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

from collections.abc import Iterable
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

NON_TRIGGER_MODEL_USES: frozenset[str] = frozenset({
    "context_only",
    "not_model_input",
})

NON_TRIGGER_CLAIM_STATUSES: frozenset[str] = frozenset({
    "published_context_signal_unarchived_bytes",
    "unconfirmed_by_public_health_authority",
})

NON_TRIGGER_TABLE_STATUSES: frozenset[str] = frozenset({
    "superseded_capture_not_model_input",
})

# Entries that ANNOUNCE new data without BEING it. The WordPress capture of a SitRep post
# is a detection record: it stamps its own post date as its data date, so it may legitimately
# advance the publication route (its whole job is to say "a new edition exists") but it must
# never define how far our analytic coverage reaches. Letting it do so reads coverage as the
# post date rather than the SitRep's report date, which is a day later and silently hides the
# edition the detection is pointing at.
NON_COVERAGE_MODEL_USES: frozenset[str] = frozenset({
    "detection_and_private_staging_only_until_reviewed_sitrep_promotion_json",
})


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


def source_triggers_snapshot(entry: dict[str, Any]) -> bool:
    """True when a source publication date may advance snapshot cadence.

    Dated context/watch/cross-check evidence belongs in the archive, but it
    must not by itself create a new public publication-state route.
    """
    normalized = entry.get("normalized_content") or {}
    if normalized.get("snapshot_trigger") is False:
        return False
    if normalized.get("model_use") in NON_TRIGGER_MODEL_USES:
        return False
    if normalized.get("claim_status") in NON_TRIGGER_CLAIM_STATUSES:
        return False
    if normalized.get("table_semantics_status") in NON_TRIGGER_TABLE_STATUSES:
        return False
    if entry.get("source_tier") == "aggregator":
        return False
    return source_publication_date(entry) is not None


def source_defines_data_coverage(entry: dict[str, Any]) -> bool:
    """True when this entry's data date may define how far analytic coverage reaches.

    Detection/staging captures are excluded: see NON_COVERAGE_MODEL_USES.
    """
    normalized = entry.get("normalized_content") or {}
    if normalized.get("model_use") in NON_COVERAGE_MODEL_USES:
        return False
    return source_data_date(entry) is not None


def data_coverage_through(entries: Iterable[dict[str, Any]], through_date: str) -> str:
    """Newest data date already covered by sources available at ``through_date``.

    This is the analytic reach of the snapshot cut on ``through_date``: the most recent
    day any coverage-defining source actually reports on. It is NOT the snapshot's own
    publication clock, which runs a day or more ahead of the data it carries.
    """
    dates = [
        d
        for e in entries
        if source_triggers_snapshot(e)
        and source_defines_data_coverage(e)
        and (p := source_publication_date(e))
        and p <= through_date
        and (d := source_data_date(e))
    ]
    return max(dates) if dates else ""


def source_adds_data_beyond(entry: dict[str, Any], coverage_through: str) -> bool:
    """True when this entry carries data the given coverage does not already reach.

    A source re-publishing a cut we have already ingested teaches us nothing, however
    recently it was published. A source with no data date at all keeps the historical
    behaviour and stays informative (the publication-clock-only pattern; see
    lovs.publication_clock_contract).
    """
    data_date = source_data_date(entry)
    if not data_date or not coverage_through:
        return True
    return data_date > coverage_through


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
