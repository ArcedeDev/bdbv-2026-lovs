"""CDC MMWR mm7522e1 time-based detection-depth derivation.

An INDEPENDENT, time-based estimate of (a) the silent transmission phase before
detection and (b) the total transmission elapsed since the modeled spillover.
This is deliberately distinct from the LOVS branching-process posterior
(``lovs_transmission``), which is COUNT-based (it inverts the confirmed-case
count by a back-projection R). The two estimates are cited SEPARATELY and never
conflated into one number (founder rule + the publication boundary).

Method: the CDC back-projection places spillover in late Jan - mid Feb 2026
(the median moves with the assumed cumulative deaths). Detection (the first
confirmed report) is 14 May 2026. We report durations directly (the robust,
intuitive quantity) and convert to transmission generations via

    generations = elapsed_days / serial_interval_mean_days

using the active serial-interval mean (a published literature value). All inputs
are public (a published CDC date + published serial-interval literature), so the
derived figures are publication-safe.

The constant below is transcribed from CDC MMWR mm7522e1 and MUST stay in lockstep
with the LOVS evidence-store fixture
``projects/lovs-evidence-mcp/fixtures/models/cdc_earlier_start.json``
(a cross-repo equality test asserts this when both repos are checked out).

Stdlib only. No clock (``as_of`` is passed in by the pipeline), deterministic.
"""
from __future__ import annotations

import datetime as _dt

# Detection-era confirmed count anchor (a WHO PHEIC figure, 8 Ituri + 2 Kampala
# as of 16 May 2026, source who-pheic-2026-05-17). A FIXED historical quantity:
# the LOVS count-based silent-generations back-calc anchors to it (NOT the live
# count). Defined here alongside the CDC time-based anchors because both are the
# fixed detection-depth reference points.
DETECTION_ERA_CONFIRMED = 10
DETECTION_ERA_CONFIRMED_SOURCE = "who-pheic-2026-05-17"

# CDC MMWR mm7522e1 spillover back-projection. Transcribed; keep in sync with
# cdc_earlier_start.json. Spillover medians vary with the assumed cumulative
# deaths (more deaths => earlier spillover); the 200-death median is the earliest
# central estimate, the 50-death median the latest.
CDC_MM7522E1: dict[str, object] = {
    "report_id": "CDC MMWR mm7522e1",
    "report_url": "https://www.cdc.gov/mmwr/volumes/75/wr/mm7522e1.htm",
    "report_snapshot_date": "2026-06-02",
    "r0": 2.51,
    "detection_anchor_date": "2026-05-14",
    "spillover_median_50_death": "2026-02-19",
    "spillover_median_100_death": "2026-02-08",
    "spillover_median_200_death": "2026-01-29",
    "spillover_interval_earliest": "2026-01-09",
}

_DAYS_PER_MONTH = 30.4375  # average Gregorian month, for duration-primary display


def _date(iso: str) -> _dt.date:
    return _dt.date.fromisoformat(iso[:10])


def _gens(days: int, serial_interval_mean_days: float) -> float:
    return round(days / serial_interval_mean_days, 1)


def _months(days: int) -> float:
    return round(days / _DAYS_PER_MONTH, 1)


def compute_cdc_detection_depth(
    serial_interval_mean_days: float,
    as_of: str,
) -> dict[str, object]:
    """Derive the CDC time-based silent + elapsed detection-depth figures.

    ``serial_interval_mean_days``: the active serial-interval mean (Stage-Two BDBV
    ~7.27 d). ``as_of``: the snapshot as-of timestamp (ISO); only its date is used.

    Returns a JSON-ready dict (see module docstring). Durations are bands across
    the CDC spillover medians (latest median -> earliest median); generations are
    duration / serial-interval mean. The earliest-interval bound (~9 Jan) is
    reported as a separate upper bound, not folded into the central band.
    """
    detection = _date(str(CDC_MM7522E1["detection_anchor_date"]))
    as_of_date = _date(as_of)

    latest_median = _date(str(CDC_MM7522E1["spillover_median_50_death"]))  # 19 Feb (shortest silence)
    earliest_median = _date(str(CDC_MM7522E1["spillover_median_200_death"]))  # 29 Jan (longest silence)
    earliest_bound = _date(str(CDC_MM7522E1["spillover_interval_earliest"]))  # 9 Jan

    # Silent phase: spillover -> detection (fixed; does not move with the live count).
    silent_short = (detection - latest_median).days
    silent_long = (detection - earliest_median).days
    silent_bound = (detection - earliest_bound).days

    # Elapsed: spillover -> as_of (grows over time).
    elapsed_short = (as_of_date - latest_median).days
    elapsed_long = (as_of_date - earliest_median).days
    elapsed_bound = (as_of_date - earliest_bound).days

    si = serial_interval_mean_days

    return {
        "report_id": CDC_MM7522E1["report_id"],
        "report_url": CDC_MM7522E1["report_url"],
        "report_snapshot_date": CDC_MM7522E1["report_snapshot_date"],
        "r0": CDC_MM7522E1["r0"],
        "detection_anchor_date": CDC_MM7522E1["detection_anchor_date"],
        "serial_interval_mean_days": round(si, 2),
        "spillover_band": {
            "earliest_median": CDC_MM7522E1["spillover_median_200_death"],
            "latest_median": CDC_MM7522E1["spillover_median_50_death"],
            "interval_earliest": CDC_MM7522E1["spillover_interval_earliest"],
        },
        "silent_before_detection": {
            "duration_days": [silent_short, silent_long],
            "duration_months": [_months(silent_short), _months(silent_long)],
            "generations": [_gens(silent_short, si), _gens(silent_long, si)],
            "earliest_bound_days": silent_bound,
            "earliest_bound_generations": _gens(silent_bound, si),
        },
        "elapsed_since_spillover": {
            "as_of": as_of_date.isoformat(),
            "duration_days": [elapsed_short, elapsed_long],
            "duration_months": [_months(elapsed_short), _months(elapsed_long)],
            "generations": [_gens(elapsed_short, si), _gens(elapsed_long, si)],
            "earliest_bound_days": elapsed_bound,
            "earliest_bound_generations": _gens(elapsed_bound, si),
        },
        "caveats": [
            "Modeled, not established. Spillover timing is the CDC MMWR mm7522e1 "
            "back-projection (late January to mid February 2026); generations = "
            "duration / the literature serial-interval mean.",
            "Cited SEPARATELY from the LOVS branching-process detection-depth "
            "posterior (the count-based estimate); the two are never conflated.",
            "Does NOT move the official detection anchor (first confirmed report "
            "14 May 2026); it describes the silence before it.",
        ],
    }
