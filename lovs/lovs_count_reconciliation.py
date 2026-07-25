"""Per-cycle count reconciliation: does the cumulative move by what was notified?

WHY THIS EXISTS. Each SitRep publishes both a cumulative count and the day's notified
count. Those two should agree: cumulative[n] - cumulative[n-1] == notified[n]. When they
do not, one of exactly two things happened, and both are must-know:

  1. THE SOURCE RESTATED. INSP integrated harmonized provincial databases, so the
     cumulative absorbed records from earlier days. SitRep 69 (2026-07-22) did this
     explicitly: +369 confirmed against 97 notified, +236 deaths against 62.
  2. WE TRANSCRIBED WRONG. A promotion carried a stale day-column. SitReps 51-53 did
     this: all three recorded new_confirmed_24h=33 / suspected_cases_day=354, values
     belonging to SitRep 51, while the sources printed 33/63/84 and 354/135/237.

Both corrupt any incidence series derived by differencing the cumulative, which is why
this module exists as a GATE and not merely as a report: see
``correct_incidence_increment`` and the ``reconciliation`` argument threaded into
``lovs_convergence.estimate_growth_rate``.

WHAT THIS MODULE REFUSES TO DO. It never guesses. A cycle whose notified value is absent
is ``unknown_notified``, never treated as zero: 12 promotions (SitRep 15-26) carry no
notified value at all, and coercing those to zero would report the entire cumulative
delta as a restatement for twelve consecutive cycles. A cycle whose prior report is more
than one day earlier is ``undetermined_multi_day``: three transitions in this series span
two days (SR28->30, SR42->44, SR44->46) because SitReps 29, 43 and 45 do not exist, and a
gap measured across a two-day jump is not a restatement signal.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

# Status taxonomy. Ordered loosely from "usable" to "not usable" for trend work.
STATUS_SERIES_START = "series_start"
STATUS_RECONCILED = "reconciled"
STATUS_RESTATED = "restated"
STATUS_UNKNOWN_NOTIFIED = "unknown_notified"
STATUS_UNDETERMINED_MULTI_DAY = "undetermined_multi_day"

#: Statuses whose cumulative increment is NOT a valid one-day incidence observation.
NOT_DIFFERENCEABLE = frozenset(
    {STATUS_RESTATED, STATUS_UNKNOWN_NOTIFIED, STATUS_UNDETERMINED_MULTI_DAY}
)


class CountReconciliationError(ValueError):
    """Raised when a caller differences the cumulative across a boundary it may not."""


def _notified(payload: Mapping[str, Any], key: str) -> tuple[int | None, bool]:
    """Resolve a notified value, preferring the top level over ``figures``.

    Returns ``(value, conflict)``. The corpus stores these inconsistently: 22 promotions
    carry the value only under ``figures``, 17 carry it in both places, 12 carry it
    nowhere. Where both exist and DISAGREE, the top level is authoritative and the
    conflict is surfaced, because that disagreement is the exact signature of the
    SitRep 51-53 stale-``figures`` defect (top level 63/84 correct, ``figures`` 33 stale).
    """
    figures = payload.get("figures")
    fig_val = figures.get(key) if isinstance(figures, Mapping) else None
    top_val = payload.get(key)
    vals = [v for v in (top_val, fig_val) if isinstance(v, int) and not isinstance(v, bool)]
    if not vals:
        return None, False
    conflict = (
        isinstance(top_val, int)
        and isinstance(fig_val, int)
        and not isinstance(top_val, bool)
        and not isinstance(fig_val, bool)
        and top_val != fig_val
    )
    preferred = top_val if isinstance(top_val, int) and not isinstance(top_val, bool) else fig_val
    return preferred, conflict


def _cumulative(payload: Mapping[str, Any], key: str) -> int | None:
    figures = payload.get("figures")
    val = figures.get(key) if isinstance(figures, Mapping) else None
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def reconcile_pair(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile one consecutive promotion pair. Never raises; classifies instead."""
    d0 = date.fromisoformat(str(prior["data_as_of"])[:10])
    d1 = date.fromisoformat(str(current["data_as_of"])[:10])
    day_span = (d1 - d0).days

    rec: dict[str, Any] = {
        "sitrep_number": current.get("sitrep_number"),
        "data_as_of": str(current["data_as_of"])[:10],
        "prior_data_as_of": str(prior["data_as_of"])[:10],
        "prior_sitrep_number": prior.get("sitrep_number"),
        "day_span": day_span,
        "source_declared_harmonization": bool(
            (
                ((current.get("figures") or {}).get("health_zone_table") or {}).get(
                    "reconciliation"
                )
                or {}
            ).get("harmonization_declared")
        ),
    }

    for metric, cum_key, notif_key in (
        ("confirmed", "cumul_cas_confirmes_drc", "new_confirmed_24h"),
        ("deaths", "cumul_deces_parmi_confirmes_drc", "new_confirmed_deaths_24h"),
    ):
        c0 = _cumulative(prior, cum_key)
        c1 = _cumulative(current, cum_key)
        notified, conflict = _notified(current, notif_key)
        delta = (c1 - c0) if (c0 is not None and c1 is not None) else None

        if day_span != 1:
            status = STATUS_UNDETERMINED_MULTI_DAY
        elif delta is None or notified is None:
            status = STATUS_UNKNOWN_NOTIFIED
        elif delta == notified:
            status = STATUS_RECONCILED
        else:
            status = STATUS_RESTATED

        rec[metric] = {
            "cumulative": c1,
            "prior_cumulative": c0,
            "cumulative_delta": delta,
            "notified": notified,
            # `gap` is only meaningful when both terms are known. It is NEVER defaulted
            # to 0, because a missing notified value is not evidence of agreement.
            "gap": (delta - notified) if (delta is not None and notified is not None) else None,
            "status": status,
            "notified_field_conflict": conflict,
            "differenceable": status not in NOT_DIFFERENCEABLE,
        }
    return rec


def reconcile_series(promotions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reconcile the whole ordered promotion series, oldest first.

    The first cycle has no prior report, so it is emitted as ``series_start`` rather
    than omitted: a consumer scanning for un-differenceable boundaries must be able to
    see that the series has a beginning.
    """
    ordered = sorted(
        (p for p in promotions if p.get("data_as_of")),
        key=lambda p: str(p["data_as_of"])[:10],
    )
    if not ordered:
        return []

    first = ordered[0]
    out: list[dict[str, Any]] = [
        {
            "sitrep_number": first.get("sitrep_number"),
            "data_as_of": str(first["data_as_of"])[:10],
            "prior_data_as_of": None,
            "prior_sitrep_number": None,
            "day_span": None,
            "source_declared_harmonization": False,
            **{
                metric: {
                    "cumulative": _cumulative(first, cum_key),
                    "prior_cumulative": None,
                    "cumulative_delta": None,
                    "notified": _notified(first, notif_key)[0],
                    "gap": None,
                    "status": STATUS_SERIES_START,
                    "notified_field_conflict": _notified(first, notif_key)[1],
                    "differenceable": False,
                }
                for metric, cum_key, notif_key in (
                    ("confirmed", "cumul_cas_confirmes_drc", "new_confirmed_24h"),
                    ("deaths", "cumul_deces_parmi_confirmes_drc", "new_confirmed_deaths_24h"),
                )
            },
        }
    ]
    out.extend(reconcile_pair(a, b) for a, b in zip(ordered, ordered[1:]))
    return out


def summarize(records: Iterable[Mapping[str, Any]], metric: str = "confirmed") -> dict[str, Any]:
    """Counts by status plus the restated cycles, for the snapshot and the gate message."""
    recs = list(records)
    by_status: dict[str, int] = {}
    restated: list[dict[str, Any]] = []
    for r in recs:
        block = r.get(metric) or {}
        status = block.get("status")
        by_status[status] = by_status.get(status, 0) + 1
        if status == STATUS_RESTATED:
            restated.append(
                {
                    "sitrep_number": r.get("sitrep_number"),
                    "data_as_of": r.get("data_as_of"),
                    "gap": block.get("gap"),
                    "cumulative_delta": block.get("cumulative_delta"),
                    "notified": block.get("notified"),
                    "source_declared_harmonization": r.get("source_declared_harmonization"),
                    "notified_field_conflict": block.get("notified_field_conflict"),
                }
            )
    return {
        "metric": metric,
        "cycles": len(recs),
        "by_status": by_status,
        "restated_cycles": restated,
        "field_conflict_cycles": [
            r.get("data_as_of")
            for r in recs
            if (r.get(metric) or {}).get("notified_field_conflict")
        ],
        "basis": (
            "cumulative_delta - notified, per consecutive-day pair. A non-zero gap means "
            "EITHER the source restated its cumulative (integrating earlier records) OR a "
            "promotion carried a stale day-column. Both invalidate differencing the "
            "cumulative across that boundary. gap is null, never zero, when the notified "
            "value is absent or the pair spans more than one day."
        ),
    }


def index_by_date(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index reconciliation records by ``data_as_of`` for O(1) lookup during differencing."""
    return {str(r["data_as_of"])[:10]: dict(r) for r in records if r.get("data_as_of")}


def correct_incidence_increment(
    *,
    end_date: str,
    raw_increment: float,
    day_span: int,
    reconciliation: Mapping[str, Mapping[str, Any]] | None,
    metric: str = "confirmed",
) -> tuple[float | None, str]:
    """Return the incidence to use for one increment, plus why.

    ``raw_increment`` is the differenced cumulative (already divided by ``day_span`` by
    the caller's convention is NOT assumed: pass the RAW difference and the span).

    Resolution order:
      * boundary reconciles, or no reconciliation data supplied -> use the raw difference
      * boundary restated AND the notified value is known -> use notified, which is the
        true one-day incidence; the rest of the jump belongs to earlier days
      * boundary restated and notified unknown, or otherwise not differenceable ->
        return ``None``, meaning DROP this increment rather than feed a known-bad value

    Returning ``None`` is deliberate. Dropping one observation degrades an estimate
    slightly; feeding a restatement into it corrupts the estimate outright. At SitRep 69
    the raw difference was 369 against 97 notified, which inflated the floated doubling
    time from 23.7 to 11.5 days and flipped the published regime from slow_growth to
    growing.
    """
    if not reconciliation:
        return raw_increment / (day_span or 1), "raw (no reconciliation data supplied)"
    rec = reconciliation.get(str(end_date)[:10])
    if not rec:
        return raw_increment / (day_span or 1), "raw (no record for this boundary)"
    block = rec.get(metric) or {}
    status = block.get("status")
    if status not in NOT_DIFFERENCEABLE:
        return raw_increment / (day_span or 1), f"raw ({status})"
    if status == STATUS_RESTATED:
        notified = block.get("notified")
        if isinstance(notified, int) and not isinstance(notified, bool):
            return float(notified) / (day_span or 1), (
                f"notified-substituted ({status}: raw delta "
                f"{block.get('cumulative_delta')} vs notified {notified})"
            )
    return None, f"dropped ({status})"


def assert_differenceable(
    records: Iterable[Mapping[str, Any]],
    *,
    since: str | None = None,
    until: str | None = None,
    metric: str = "confirmed",
) -> None:
    """Fail loudly if any boundary in the window may not be differenced.

    For callers that must not silently proceed on a corrupted window (a published growth
    rate, a doubling time, a trend claim). ``correct_incidence_increment`` is the softer
    path for estimators that can drop or substitute individual points.
    """
    bad = []
    for r in records:
        d = str(r.get("data_as_of") or "")[:10]
        if since and d < since:
            continue
        if until and d > until:
            continue
        block = r.get(metric) or {}
        if block.get("status") in NOT_DIFFERENCEABLE:
            bad.append(f"{d} ({block.get('status')}, gap={block.get('gap')})")
    if bad:
        raise CountReconciliationError(
            f"cannot difference the {metric} cumulative across "
            f"{len(bad)} non-reconciling boundar{'y' if len(bad) == 1 else 'ies'}: "
            + "; ".join(bad)
        )
