"""The Block 6 pin set: operational-capacity and ascertainment commitments.

Every probability here is GENERATED from the frozen operational series by the
bootstrap in opsforecast.py, never authored. `test_operational_ledger.py`
re-runs this module and asserts the committed ledger matches, so a hand-edited
probability fails the suite.

Why these questions and not others. The corridor model saturated -- union hazard
1.000000 for every eligible target -- so it can no longer discriminate. The
operational indicators can: contact follow-up carries 81 observations and moved
7 -> 87, lab positivity 66 and fell 46 -> 14 while confirmed cases grew sixtyfold.

Why these thresholds. A walk-forward backtest of 450 forecasts (see
`docs/operational-forecaster-validation.md`) measured the bootstrap's skill at
+0.198 overall and +0.119 in the 0.15-0.85 band, and measured a real
overconfidence bias at the low end: forecasts priced 0.05 occurred 23% of the
time, forecasts priced 0.15 occurred 42% of the time. Pins are therefore drawn
from the band where skill is measured, plus two deliberate LOW-BAND BIAS TESTS
that pin the model's own low numbers so the next resolution measures whether
that bias is real and persistent rather than an artifact of a small backtest.

Question shapes are chosen by measured Brier, not taste: ends_above scored
0.174 and ends_below 0.188 across the backtest, while sustained-excursion
questions scored 0.237, so the ladder leans on end-of-window levels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from lovs.forecast import opsforecast as of

PINNED_AT = "2026-09-01"
RESOLVES_AT = "2026-10-01T23:59:59Z"
HORIZON_DAYS = 30
SEED = 6001


@dataclass(frozen=True, slots=True)
class PinSpec:
    key: str
    metric: str
    shape: str
    threshold: float
    question: str
    rationale: str
    lo: float | None = None
    hi: float | None = None
    bias_test: bool = False

    def event(self) -> Callable[[list[float]], bool]:
        if self.shape == "ends_above":
            return of.ends_above(self.threshold)
        if self.shape == "ends_below":
            return of.ends_below(self.threshold)
        if self.shape == "ever_above":
            return of.ever_above(self.threshold)
        if self.shape == "sustained7_below":
            return of.days_below_at_least(self.threshold, 7)
        raise ValueError(f"unknown shape {self.shape!r}")


PCT = dict(lo=0.0, hi=100.0)
NONNEG = dict(lo=0.0)

SPECS: tuple[PinSpec, ...] = (
    PinSpec("iso-below-850", "hospital_isolation_total", "ends_below", 850,
            "On the last SitRep data day on or before 2026-10-01, is national "
            "hospital isolation occupancy at or below 850?",
            "LOW-BAND BIAS TEST. Occupancy stands at its all-time high of 896 and has "
            "never fallen back. The backtest says the forecaster is overconfident below "
            "0.15; this pins that claim so the bias is measured rather than assumed.",
            bias_test=True, **NONNEG),
    PinSpec("cft-below-70", "contact_followup_percent", "ends_below", 70,
            "On the last SitRep data day on or before 2026-10-01, is the national "
            "contact follow-up rate at or below 70 percent?",
            "LOW-BAND BIAS TEST. Follow-up has held 80-87 percent through August with a "
            "standard deviation of 2.7, but fell to 28.4 percent in a single day on "
            "2026-06-11, so a collapse is not unprecedented.",
            bias_test=True, **PCT),
    PinSpec("cft-below-75", "contact_followup_percent", "ends_below", 75,
            "On the last SitRep data day on or before 2026-10-01, is the national "
            "contact follow-up rate at or below 75 percent?",
            "Capacity-ramp question. 75 percent is roughly the July floor; a return to it "
            "would mark the first sustained regression since the system stabilised.",
            **PCT),
    PinSpec("lab-below-12", "lab_positivity_percent", "ends_below", 12,
            "On the last SitRep data day on or before 2026-10-01, is national lab "
            "positivity at or below 12 percent?",
            "Ascertainment question. Positivity fell 46 -> 14 percent while confirmed "
            "cases grew sixtyfold, which is testing outrunning transmission. Continued "
            "decline means ascertainment is still improving.",
            **PCT),
    PinSpec("new-below-60", "new_confirmed_today", "ends_below", 60,
            "On the last SitRep data day on or before 2026-10-01, are new confirmed "
            "cases for that day at or below 60?",
            "Deceleration question. The daily count sits at 82 against a range of 17-125.",
            **NONNEG),
    PinSpec("cft-sustained-80", "contact_followup_percent", "sustained7_below", 80,
            "Between 2026-09-01 and 2026-10-01, do at least 7 SitRep data days record a "
            "national contact follow-up rate at or below 80 percent?",
            "Sustained-degradation question. Distinguishes a real capacity regression from "
            "the single-day dips the series produces routinely.",
            **PCT),
    PinSpec("new-above-100", "new_confirmed_today", "ends_above", 100,
            "On the last SitRep data day on or before 2026-10-01, are new confirmed "
            "cases for that day at or above 100?",
            "Re-acceleration question, the mirror of new-below-60. Both can be wrong; only "
            "one can be right.",
            **NONNEG),
    PinSpec("lab-above-20", "lab_positivity_percent", "ends_above", 20,
            "On the last SitRep data day on or before 2026-10-01, is national lab "
            "positivity at or above 20 percent?",
            "Ascertainment reversal. Positivity climbing back toward 20 percent while "
            "testing volume holds would mean detection is losing ground to transmission.",
            **PCT),
    PinSpec("iso-above-1000", "hospital_isolation_total", "ends_above", 1000,
            "On the last SitRep data day on or before 2026-10-01, is national hospital "
            "isolation occupancy at or above 1000?",
            "Capacity-ceiling question. Occupancy has risen monotonically to 896; 1000 is "
            "the next round threshold and a plausible bed-capacity constraint.",
            **NONNEG),
    PinSpec("alerts-above-2200", "alerts_reported", "ends_above", 2200,
            "On the last SitRep data day on or before 2026-10-01, are alerts reported in "
            "the preceding 24 hours at or above 2200?",
            "Surveillance-throughput question. Alert volume is the input to detection; if "
            "it falls while cases persist, the system is going quiet rather than the "
            "outbreak receding.",
            **NONNEG),
    PinSpec("zones-above-70", "health_zones_touched", "ends_above", 70,
            "On the last SitRep data day on or before 2026-10-01, is the cumulative count "
            "of affected health zones at or above 70?",
            "Spatial-expansion question. Zones grew 25 -> 60 in 106 days, about 0.33 per "
            "day; 70 in 30 days is close to trend, so this tests whether expansion holds "
            "its rate.",
            **NONNEG),
    PinSpec("alerts-above-2000", "alerts_reported", "ends_above", 2000,
            "On the last SitRep data day on or before 2026-10-01, are alerts reported in "
            "the preceding 24 hours at or above 2000?",
            "Nested with alerts-above-2200: the pair traces the distribution rather than "
            "testing one point, and both resolve from the same published figure but at "
            "different thresholds, so they are distinct events.",
            **NONNEG),
)


def build_pins(rows=None) -> list[dict]:
    """Generate the pin set from the frozen series. Deterministic."""
    rows = rows if rows is not None else of.load_rows()
    out: list[dict] = []
    for i, spec in enumerate(SPECS):
        fc = of.forecast(
            rows, spec.metric, spec.question, HORIZON_DAYS, spec.event(),
            seed=SEED + i,
            method=(f"stationary block bootstrap over day-over-day changes, "
                    f"block={of.DEFAULT_BLOCK}, paths={of.DEFAULT_PATHS}"),
            lo=spec.lo, hi=spec.hi,
        )
        out.append({
            "pin_id": f"operational-pin:bdbv-uga-cod-2026:30d:{spec.key}",
            "metric": spec.metric,
            "shape": spec.shape,
            "threshold": spec.threshold,
            "public_question": spec.question,
            "rationale": spec.rationale,
            "bias_test": spec.bias_test,
            "probability": fc.probability,
            "horizon_days": HORIZON_DAYS,
            "observations_at_pin": fc.n_observations,
            "last_observed_value": fc.last_value,
            "last_observed_date": fc.last_date,
            "generator": {"seed": fc.seed, "paths": fc.n_paths,
                          "block": fc.block, "method": fc.method},
        })
    out.sort(key=lambda p: p["probability"])
    return out
