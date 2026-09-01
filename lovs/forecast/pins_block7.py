"""The Block 7 pin set: structural questions, on series Block 6 does not touch.

Separate module from `pins.py` so Block 6's generation path stays byte-stable:
a pinned block's probabilities must regenerate exactly, and sharing a generator
would couple them.

THREE DESIGN CHOICES, each with a reason.

1. DIFFERENT SERIES FROM BLOCK 6. Block 6 pins contact follow-up, lab
   positivity, isolation occupancy, new confirmed, health zones and alerts.
   Block 7 pins publication lag, per-province isolation, lab throughput,
   cumulative recovered and confirmed deaths per day. No pin in either block
   resolves from a series the other uses, so the two blocks are independent
   evidence rather than two readings of one system.

2. THREE METHODS, PINNED AGAINST EACH OTHER. Ten pins come from the block
   bootstrap in `opsforecast.py`. Two come from `cadenceintegrity.py`, whose
   bootstrap runs over inter-arrival GAPS rather than levels. One comes from
   `termination.py`, whose bootstrap carries a reflecting boundary at zero.
   Resolving all thirteen together scores three approaches on the same window,
   which no single-method block can do.

3. TWO LOW-BAND BIAS TESTS AGAIN, from different series and different methods
   (`recovered-above-1900` from the level bootstrap, `first-zero-day` from the
   termination bootstrap). Block 6 already carries two. Four low-band pins
   across two blocks is the smallest set that can say anything about whether
   the measured overconfidence below 0.15 is a property of the method or of
   one series.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

from lovs.forecast import cadenceintegrity as ci
from lovs.forecast import opsforecast as of
from lovs.forecast import termination as tm

PINNED_AT = "2026-09-01"
RESOLVES_AT = "2026-10-01T23:59:59Z"
HORIZON_DAYS = 30
AS_OF = dt.date(2026, 9, 1)


def publication_lag_series(rows) -> list[of.Observation]:
    """Days between a packet's data day and its publication day.

    Not in the frozen extract as a column, so it is derived here. The cadence
    monitor found this drifting: first fifty packets mean 1.120 days, second
    fifty 1.540, permutation p = 0.0005. It is the only degradation signal that
    is visible while the feed is still delivering everything.
    """
    seen: dict[dt.date, of.Observation] = {}
    for row in rows:
        pub, day = row.get("published_at"), row.get("data_as_of")
        if not pub or not day:
            continue
        p = dt.date.fromisoformat(str(pub)[:10])
        d = dt.date.fromisoformat(str(day)[:10])
        seen.setdefault(d, of.Observation(d, float((p - d).days)))
    return sorted(seen.values(), key=lambda o: o.date)


def province_isolation_series(rows, province: str) -> list[of.Observation]:
    """End-of-day isolation census for one province.

    Per-province rather than national deliberately: the isolation module found
    roughly two thirds of the NATIONAL series' day-over-day variance is province
    panel churn rather than ward movement. A single province's census does not
    carry that artifact.
    """
    seen: dict[dt.date, of.Observation] = {}
    for row in rows:
        block, day = row.get("isolation_by_province") or {}, row.get("data_as_of")
        if not block or not day:
            continue
        d = dt.date.fromisoformat(str(day)[:10])
        for name, value in block.items():
            if province.lower() in name.lower() and value is not None:
                seen.setdefault(d, of.Observation(d, float(value)))
    return sorted(seen.values(), key=lambda o: o.date)


@dataclass(frozen=True, slots=True)
class BootSpec:
    key: str
    series_name: str
    shape: str
    threshold: float
    question: str
    rationale: str
    seed: int
    lo: float | None = None
    hi: float | None = None
    bias_test: bool = False

    def event(self) -> Callable[[list[float]], bool]:
        return {"ends_above": of.ends_above, "ends_below": of.ends_below,
                "ever_above": of.ever_above}[self.shape](self.threshold)


BOOT_SPECS: tuple[BootSpec, ...] = (
    BootSpec("recovered-above-1900", "cumulative_recovered", "ends_above", 1900,
             "On the last SitRep data day on or before 2026-10-01, is cumulative recovered "
             "at or above 1900?",
             "LOW-BAND BIAS TEST, from a different series and a different shape to Block 6's "
             "pair. Recovered stands at 1327 and is monotone; 1900 needs 573 in 30 days "
             "against a recent rate well below that.",
             seed=7011, lo=1327.0, bias_test=True),
    BootSpec("deaths-below-30", "new_confirmed_deaths_today", "ends_below", 30,
             "On the last SitRep data day on or before 2026-10-01, are new confirmed deaths "
             "for that day at or below 30?",
             "Mortality deceleration. Daily confirmed deaths stand at 38.",
             seed=7013, lo=0.0),
    BootSpec("lag-ends-above-2", "__publication_lag", "ends_above", 2,
             "Is the publication lag of the last SitRep on or before 2026-10-01 two days or "
             "more?",
             "Reporting-stream degradation. Mean lag drifted from 1.120 to 1.540 days between "
             "the first and second fifty packets, permutation p = 0.0005. This asks whether "
             "the drift shows up at the horizon.",
             seed=7001, lo=0.0),
    BootSpec("lag-ever-above-4", "__publication_lag", "ever_above", 4,
             "Does any SitRep published between 2026-09-01 and 2026-10-01 carry a publication "
             "lag of four days or more?",
             "Tail-risk companion to lag-ends-above-2. The record's worst lag is 4 days, seen "
             "twice, so this asks whether the tail recurs rather than whether the middle moves.",
             seed=7002, lo=0.0),
    BootSpec("deaths-below-50", "new_confirmed_deaths_today", "ends_below", 50,
             "On the last SitRep data day on or before 2026-10-01, are new confirmed deaths "
             "for that day at or below 50?",
             "Nested with deaths-below-30; the pair traces the distribution.",
             seed=7014, lo=0.0),
    BootSpec("lab-throughput-700", "samples_analyzed", "ends_above", 700,
             "On the last SitRep data day on or before 2026-10-01, were 700 or more samples "
             "analysed that day?",
             "Lab capacity. Throughput stands at 569. The ramp library found this pillar grew "
             "in batches, not smoothly: mean step +5.74 per day but median -4.00, with 30 days "
             "up against 35 down.",
             seed=7009, lo=0.0),
    BootSpec("nk-isolation-300", "__nordkivu_isolation", "ends_above", 300,
             "On the last SitRep data day on or before 2026-10-01, is the Nord-Kivu isolation "
             "census at or above 300?",
             "Nord-Kivu is the province the CFR instrument flags as most likely under-"
             "ascertaining, and the isolation module ranks it second on pressure with a slope "
             "of +3.67 per day against a census of 236.",
             seed=7003, lo=0.0),
    BootSpec("nk-isolation-280", "__nordkivu_isolation", "ends_above", 280,
             "On the last SitRep data day on or before 2026-10-01, is the Nord-Kivu isolation "
             "census at or above 280?",
             "Nested with nk-isolation-300.",
             seed=7004, lo=0.0),
    BootSpec("lab-throughput-600", "samples_analyzed", "ends_above", 600,
             "On the last SitRep data day on or before 2026-10-01, were 600 or more samples "
             "analysed that day?",
             "Nested with lab-throughput-700.",
             seed=7008, lo=0.0),
    BootSpec("recovered-above-1700", "cumulative_recovered", "ends_above", 1700,
             "On the last SitRep data day on or before 2026-10-01, is cumulative recovered "
             "at or above 1700?",
             "The high end of the ladder. Block 6 tops out at 0.744 and could not test whether "
             "the forecaster is calibrated above that; this pin can.",
             seed=7012, lo=1327.0),
)


def _series_for(rows, name: str) -> list[of.Observation]:
    if name == "__publication_lag":
        return publication_lag_series(rows)
    if name == "__nordkivu_isolation":
        return province_isolation_series(rows, "Nord-Kivu")
    return of.series(rows, name)


def build_pins(rows=None) -> list[dict]:
    """Generate Block 7 deterministically. No probability here is authored."""
    rows = rows if rows is not None else of.load_rows()
    out: list[dict] = []

    for spec in BOOT_SPECS:
        obs = _series_for(rows, spec.series_name)
        p = of.probability(obs, HORIZON_DAYS, spec.event(), seed=spec.seed,
                           lo=spec.lo, hi=spec.hi)
        out.append({
            "pin_id": f"operational-pin:bdbv-uga-cod-2026:30d:{spec.key}",
            "method": "level_bootstrap",
            "metric": spec.series_name.lstrip("_") or spec.series_name,
            "shape": spec.shape, "threshold": spec.threshold,
            "public_question": spec.question, "rationale": spec.rationale,
            "bias_test": spec.bias_test, "probability": round(p, 4),
            "horizon_days": HORIZON_DAYS,
            "observations_at_pin": len(obs),
            "last_observed_value": obs[-1].value,
            "last_observed_date": obs[-1].date.isoformat(),
            "generator": {"seed": spec.seed, "paths": of.DEFAULT_PATHS,
                          "block": of.DEFAULT_BLOCK,
                          "method": "stationary block bootstrap over day-over-day changes"},
        })

    cf = ci.continuity_forecast(rows, as_of=AS_OF, horizon_days=HORIZON_DAYS)
    for key, value, question, why in (
        ("cadence-no-silence-over-2d", cf.p_no_longer_silence,
         "Between 2026-09-01 and 2026-10-01, does the SitRep feed avoid any silence longer "
         "than two consecutive days?",
         "Feed continuity. The last 30 days ran 29 arrivals with no gap over 2, which is the "
         "top of this feed's range with no headroom. Method is a gap bootstrap, not a level "
         "bootstrap, so this pin scores a different approach on the same window."),
        ("cadence-arrivals-at-least-29", cf.p_volume_at_least_recent,
         "Do at least 29 SitReps arrive between 2026-09-01 and 2026-10-01?",
         "Delivery volume, benchmarked on the last 30 days rather than chosen. The gap "
         "bootstrap structurally cannot draw a silence longer than the 3 days it resamples, "
         "so it prices variation inside a regime that has never broken and cannot price the "
         "regime breaking. That is the exact failure that cost this programme 58 days, and "
         "it is why this pin is worth having."),
    ):
        out.append({
            "pin_id": f"operational-pin:bdbv-uga-cod-2026:30d:{key}",
            "method": "gap_bootstrap", "metric": "sitrep_arrivals",
            "shape": "derived", "threshold": None,
            "public_question": question, "rationale": why,
            "bias_test": False, "probability": round(float(value), 4),
            "horizon_days": HORIZON_DAYS,
            "observations_at_pin": cf.n_reference_gaps,
            "last_observed_value": float(cf.recent_arrivals),
            "last_observed_date": "2026-08-28",
            "generator": {"seed": cf.seed, "paths": cf.n_paths,
                          "block": cf.block, "method": cf.method},
        })

    obs = of.series(rows, "new_confirmed_today")
    state = tm.countdown(obs, as_of=AS_OF)
    horizon = tm.forecast_horizon(obs, state, HORIZON_DAYS)["reflecting"]
    out.append({
        "pin_id": "operational-pin:bdbv-uga-cod-2026:30d:first-zero-case-day",
        "method": "termination_bootstrap", "metric": "new_confirmed_today",
        "shape": "derived", "threshold": 0,
        "public_question": ("Does any SitRep data day between 2026-09-01 and 2026-10-01 "
                            "report zero new confirmed cases?"),
        "rationale": ("LOW-BAND BIAS TEST from a third method. The outbreak runs at 82 "
                      "confirmed cases a day and has never recorded a zero day, so this is "
                      "genuinely unlikely rather than artificially low. Reported on the "
                      "reflecting variant: a plain difference bootstrap bounces off zero, "
                      "which is why the module brackets rather than gives one number. "
                      "Declaration inside this window is a STRUCTURAL zero, not a small "
                      "probability, since 43 case-free days do not fit in 30, and is "
                      "therefore not pinned."),
        "bias_test": True, "probability": round(float(horizon["p_first_zero_day"]), 4),
        "horizon_days": HORIZON_DAYS,
        "observations_at_pin": int(horizon["n_observations"]),
        "last_observed_value": float(horizon["last_value"]),
        "last_observed_date": str(horizon["anchor"]),
        "generator": {"seed": int(horizon["seed"]), "paths": int(horizon["n_paths"]),
                      "block": int(horizon["block"]),
                      "method": "termination bootstrap, reflecting boundary at zero"},
    })

    out.sort(key=lambda p: p["probability"])
    return out
