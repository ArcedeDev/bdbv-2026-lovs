# SPDX-License-Identifier: Apache-2.0
"""Will Kenya report a Bundibugyo case that was outside follow-up when it fell ill?

Question: will Kenya report at least one laboratory-confirmed BDBV case, confirmed 2026-10-18
to 2026-11-14 and reported by 2026-11-17, that was not under the authority's follow-up as a
known contact (quarantine, isolation or active monitoring) when its symptoms began?

By 17 October Kenya had seven cases: the imported first case and six linked cases, every one
already in quarantine when symptoms began. More cases among quarantined contacts are expected
and do not count here. What counts is a case that got past follow-up: a contact never listed,
a listed contact never traced, a contact whose illness began after follow-up ended, a case
the authority calls unlinked, or a new importation.

Model, in one paragraph. Each Monte Carlo draw takes an incubation distribution from the
posterior in ``lovs/forecast/kenya_incubation.py`` and a set of judgement parameters. The
first case's infections among listed contacts follow from the six observed (a Gamma
posterior on the expected number, given the share of onsets observable by 17 October). The
unlisted infections are that number scaled by the unlisted share, the untraced ones by the
attack rate, and the onsets after release come from the incubation tail. Every pathway is a
Poisson process given the draw, with report times built by discrete convolution of exposure,
incubation and detection lags on a 0.1-day grid. The daily curve is the probability of YES
given no qualifying report by the end of each date.

Stdlib only and deterministic for a fixed seed, as the 9 and 14 October modules are.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import kenya_incubation as ki

REPO = kc.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-17-escape-forecast.json"
MODEL_VERSION = "kenya_escape-v1.0.0"
SEED = 20261017
N_DRAWS = 20_000
EVIDENCE_THROUGH_UTC = "2026-10-17T21:30:00Z"

# Clock: days from 2026-10-03 00:00 East Africa Time.
CONDITIONING_DATE = dt.date(2026, 10, 17)
WINDOW_FIRST = dt.date(2026, 10, 18)
WINDOW_LAST = dt.date(2026, 11, 14)
CURVE_DATES = [CONDITIONING_DATE + dt.timedelta(days=i)
               for i in range((WINDOW_LAST - CONDITIONING_DATE).days + 1)]
REPORT_DELAY = kc.REPORT_DELAY                  # confirmation to public report, 0.5 days
T_OPEN = kc.day_end(WINDOW_FIRST - dt.timedelta(days=1)) + REPORT_DELAY  # first qualifying report time
T_END = kc.day_end(WINDOW_LAST) + REPORT_DELAY  # last qualifying report time
POINTS = [kc.day_end(d) for d in CURVE_DATES] + [T_OPEN, T_END]
I_OPEN, I_END = len(POINTS) - 2, len(POINTS) - 1

# Discrete grid for convolutions.
H = 0.1
HORIZON = 46.0
N_BINS = int(round(HORIZON / H)) + 1

# Registered inputs (plan 2026-10-17-bdbv-kenya-escape-forecast, sha256 cf4365e4...).
OBSERVED = {"hospital": 3, "relatives": 3}
EXPOSURE = {"hospital": ki.HOSP_EXPOSURE, "relatives": ki.RELATIVES_EXPOSURE}
RELEASE = ki.RELEASE
UNLISTED_EXPOSURE = (0.5, 2.0)
CONTAINED_ONSETS = [ki.ONSET_CASE2, ki.ONSET_16OCT] + [ki.ONSET_17OCT] * 4
LISTED_TOTAL = 130
UNTRACED = 2
LAG_LISTED = 1.0
LAG_RELEASED = 3.0
LAG_UNLISTED = 5.0
PRIORS = {
    "extension_share": (0.1, 0.7),
    "unlisted_share": (0.05, 0.25),
    "untraced_relative_attack": (0.25, 1.0),
    "p_detect": (0.8, 1.0),
    "r2": (0.5, 2.0),
    "second_generation_shift": (2.0, 8.0),
    "r_comm_faye2015": (1.4, 0.9, 2.2),
    "e_comm_contained": (0.002, 0.06),
    "unlisted_share_contained": (0.0, 0.10),
    "import_rate_gamma_shape_rate_per_day": (1.5, 155.0),
    "import_arrivals_from_day": 8.0,
    "import_arrival_to_report": (0.0, 7.0),
}
PATHWAYS = ("unlisted", "untraced", "after_release", "second_generation", "contained_onward", "importation")
CHAIN = ("unlisted", "untraced", "after_release", "second_generation", "contained_onward")


# ---------------------------------------------------------------------------------------
# Deterministic variates from rng.random().
# ---------------------------------------------------------------------------------------
def _gamma(rng: random.Random, shape: float, rate: float) -> float:
    """Marsaglia and Tsang, shape >= 1, built on rng.random() only."""
    d = shape - 1.0 / 3.0
    c = 1.0 / math.sqrt(9.0 * d)
    while True:
        x = kc._normal(rng)
        v = (1.0 + c * x) ** 3
        if v <= 0.0:
            continue
        u = 1.0 - rng.random()
        if math.log(u) < 0.5 * x * x + d - d * v + d * math.log(v):
            return d * v / rate


# ---------------------------------------------------------------------------------------
# Discrete distributions: mass at x_i = i * H.
# ---------------------------------------------------------------------------------------
def _from_cdf(cdf) -> list[float]:
    out, prev = [], 0.0
    for i in range(N_BINS):
        cur = cdf(i * H + H / 2)
        out.append(max(cur - prev, 0.0))
        prev = cur
    return out


def uniform_mass(a: float, b: float) -> list[float]:
    return _from_cdf(lambda x: min(max((x - a) / (b - a), 0.0), 1.0))


@functools.lru_cache(maxsize=8)
def erlang2_mass(mean: float) -> tuple[float, ...]:
    return tuple(_from_cdf(lambda x: ki.gamma_cdf_exact(x, 2.0, mean)))


@functools.lru_cache(maxsize=512)
def incubation_mass(k: float, mu: float) -> tuple[float, ...]:
    return tuple(_from_cdf(lambda x: ki.cdf(k, mu, x)))


def convolve(a: Sequence[float], b: Sequence[float]) -> list[float]:
    out = [0.0] * N_BINS
    nz_b = [(j, v) for j, v in enumerate(b) if v > 1e-13]
    for i, va in enumerate(a):
        if va <= 1e-13:
            continue
        for j, vb in nz_b:
            if i + j >= N_BINS:
                break
            out[i + j] += va * vb
    return out


def shift(a: Sequence[float], days: float) -> list[float]:
    n = int(round(days / H))
    return [0.0] * n + list(a[:N_BINS - n])


def cdf_at(mass: Sequence[float], points: Sequence[float]) -> list[float]:
    """Cumulative mass at each point, linear within a bin (bin i spans x_i +/- H/2)."""
    running, acc = [], 0.0
    for m in mass:
        acc += m
        running.append(acc)
    out = []
    for t in points:
        pos = t / H + 0.5
        i = int(math.floor(pos))
        frac = pos - i
        lo = running[i - 1] if 0 < i <= N_BINS else (0.0 if i <= 0 else running[-1])
        hi = running[i] if 0 <= i < N_BINS else running[-1]
        out.append(lo + frac * (hi - lo) if i < N_BINS else running[-1])
    return out


@functools.lru_cache(maxsize=512)
def timing(k: float, mu: float) -> dict:
    """Report-time CDFs at POINTS for every pathway, per unit intensity, for one incubation."""
    inc = incubation_mass(k, mu)
    lag_u, lag_r, lag_l = erlang2_mass(LAG_UNLISTED), erlang2_mass(LAG_RELEASED), erlang2_mass(LAG_LISTED)
    out: dict[str, Any] = {}
    unlisted_onset = convolve(uniform_mass(*UNLISTED_EXPOSURE), inc)
    out["unlisted"] = cdf_at(shift(convolve(unlisted_onset, lag_u), REPORT_DELAY), POINTS)
    child_onset = convolve(convolve(unlisted_onset, uniform_mass(*PRIORS["second_generation_shift"])), inc)
    out["second_generation"] = cdf_at(shift(convolve(child_onset, lag_u), REPORT_DELAY), POINTS)
    contained = [0.0] * N_BINS
    for w in CONTAINED_ONSETS:
        contained = [c + m / len(CONTAINED_ONSETS) for c, m in zip(contained, uniform_mass(*w))]
    out["contained_onward_cases_2_to_7"] = cdf_at(
        shift(convolve(convolve(contained, inc), lag_u), REPORT_DELAY), POINTS)
    t_bin = int(round(ki.T_OBS / H))
    for group, exposure in EXPOSURE.items():
        onset = convolve(uniform_mass(*exposure), inc)
        r_bin = int(round(RELEASE[group] / H))
        after = [m if i >= r_bin else 0.0 for i, m in enumerate(onset)]
        followed = [m if t_bin <= i < r_bin else 0.0 for i, m in enumerate(onset)]
        later = [m if i >= t_bin else 0.0 for i, m in enumerate(onset)]
        out[f"after_release_{group}"] = cdf_at(shift(convolve(after, lag_r), REPORT_DELAY), POINTS)
        out[f"contained_onward_{group}"] = cdf_at(
            shift(convolve(convolve(followed, inc), lag_u), REPORT_DELAY), POINTS)
        out[f"listed_reports_{group}"] = cdf_at(shift(convolve(later, lag_l), REPORT_DELAY), POINTS)
        out[f"observable_{group}"] = ki.onset_cdf(k, mu, exposure, ki.T_OBS)
    return out


def importation_unit(t: float, start: float, lag: Sequence[float]) -> float:
    """Integral over arrivals a >= start of P(a + U(lag) <= t), per unit rate per day."""
    lo, hi = lag
    steps = 70
    total = 0.0
    for i in range(steps):
        u = lo + (hi - lo) * (i + 0.5) / steps
        total += max(0.0, t - start - u)
    return total / steps


# ---------------------------------------------------------------------------------------
# Incubation sources.
# ---------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=8)
def incubation_source(name: str) -> tuple[tuple[float, float, float], ...]:
    if name == "combined":
        return tuple(ki.posterior("primary", combined=True))
    if name == "kenya_only":
        return tuple(ki.posterior("primary", combined=False))
    if name == "A1_combined":
        return tuple(ki.posterior("A1_case2_as_source", combined=True))
    if name == "programme_prior":
        return ((4.0, 4.0 / 0.6, 1.0),)
    if name == "decided_prior":
        return ((3.0, 3.0 / 0.406, 1.0),)
    raise ValueError(name)


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def draw_intensities(rng: random.Random, grid: Sequence[tuple[float, float, float]],
                     opts: Mapping[str, Any]) -> dict:
    pri = {**PRIORS, **opts.get("priors", {})}
    k, mu, _ = rng.choices(grid, [w for _, _, w in grid])[0]
    tm = timing(k, mu)
    lam = {g: _gamma(rng, OBSERVED[g] + 0.5, tm[f"observable_{g}"]) for g in OBSERVED}
    listed = sum(lam.values())
    e = 0.0 if opts.get("no_extension") else kc._uniform(rng, pri["extension_share"])
    u = kc._uniform(rng, pri["unlisted_share"])
    p_det = kc._uniform(rng, pri["p_detect"])
    r2 = kc._uniform(rng, pri["r2"])
    lam_u = listed * u / (1.0 - u)
    lam_ut = UNTRACED * listed / LISTED_TOTAL * kc._uniform(rng, pri["untraced_relative_attack"])
    m_c = kc._ci_lognormal(rng, pri["r_comm_faye2015"]) * kc._uniform(rng, pri["e_comm_contained"]) \
        * kc._uniform(rng, pri["unlisted_share_contained"])
    shape, rate = pri["import_rate_gamma_shape_rate_per_day"]
    if "import_rate_per_week" in opts:
        r_imp = kc._uniform(rng, opts["import_rate_per_week"]) / 7.0
    else:
        r_imp = _gamma(rng, shape, rate)
    r_imp *= opts.get("import_multiplier", 1.0)
    if opts.get("no_importation"):
        r_imp = 0.0
    lam_imp = [r_imp * importation_unit(t, pri["import_arrivals_from_day"], pri["import_arrival_to_report"])
               for t in POINTS]
    paths = {
        "unlisted": [lam_u * p_det * c for c in tm["unlisted"]],
        "untraced": [lam_ut * p_det * c for c in tm["unlisted"]],
        "after_release": [sum(lam[g] * (1.0 - e) * p_det * tm[f"after_release_{g}"][i] for g in lam)
                          for i in range(len(POINTS))],
        "second_generation": [(lam_u + lam_ut) * (1.0 - p_det) * r2 * p_det * c for c in tm["second_generation"]],
        "contained_onward": [m_c * p_det * (len(CONTAINED_ONSETS) * tm["contained_onward_cases_2_to_7"][i]
                                            + sum(lam[g] * tm[f"contained_onward_{g}"][i] for g in lam))
                             for i in range(len(POINTS))],
        "importation": lam_imp,
    }
    listed_reports = [sum(lam[g] * tm[f"listed_reports_{g}"][i] for g in lam) for i in range(len(POINTS))]
    return {"paths": paths, "listed_reports": listed_reports, "remaining_listed":
            sum(lam[g] * (1.0 - tm[f"observable_{g}"]) for g in lam), "k": k, "mu": mu}


def run(opts: Mapping[str, Any] | None = None, n: int = N_DRAWS, seed: int = SEED) -> list[dict]:
    opts = dict(opts or {})
    grid = incubation_source(opts.get("incubation", "combined"))
    rng = random.Random(seed)
    return [draw_intensities(rng, grid, opts) for _ in range(n)]


def _total(d: Mapping[str, Any], i: int, which: Sequence[str]) -> float:
    return sum(d["paths"][p][i] for p in which)


def _start(i: int) -> int:
    """Index from which reports qualify: cases confirmed before the window opens never count."""
    return I_OPEN if POINTS[i] < T_OPEN else i


def conditional(draws: Sequence[Mapping[str, Any]], i: int, which: Sequence[str] = PATHWAYS,
                quantiles: bool = False) -> dict:
    """P(YES | no report of a case outside follow-up by POINTS[i]) for the pathways in `which`.

    Each draw is weighted by its chance of no report by POINTS[i]; YES needs a report in
    (max(POINTS[i], T_OPEN), T_END].
    """
    start = _start(i)
    floor = min(_total(d, i, which) for d in draws)
    num = den = 0.0
    vals, wts = [], []
    for d in draws:
        w = math.exp(-(_total(d, i, which) - floor))
        p_yes = 1.0 - math.exp(-(_total(d, I_END, which) - _total(d, start, which)))
        num += w * p_yes
        den += w
        if quantiles:
            vals.append(p_yes)
            wts.append(w)
    out = {"p": num / den}
    if quantiles:
        out["p10"] = kc._weighted_quantile(vals, wts, 0.10)
        out["p90"] = kc._weighted_quantile(vals, wts, 0.90)
    return out


def summarise(draws: Sequence[Mapping[str, Any]], full: bool = True) -> dict:
    i0 = 0  # POINTS[0] is the end of the conditioning date
    s0 = _start(i0)
    head = conditional(draws, i0, quantiles=full)
    out: dict[str, Any] = {"headline": head}
    if not full:
        out["chain_only"] = conditional(draws, i0, CHAIN)["p"]
        out["importation_only"] = conditional(draws, i0, ("importation",))["p"]
        return out
    curve = []
    for i, date in enumerate(CURVE_DATES):
        row = {"date": date.isoformat(), **conditional(draws, i, quantiles=True),
               "chain_only": conditional(draws, i, CHAIN)["p"],
               "importation_only": conditional(draws, i, ("importation",))["p"]}
        curve.append(row)
    weights = [math.exp(-_total(d, i0, PATHWAYS)) for d in draws]
    wsum = sum(weights)
    decomposition = {}
    for p in PATHWAYS:
        decomposition[p] = {
            "p_alone": conditional(draws, i0, (p,))["p"],
            "expected_count_in_window": sum(w * (d["paths"][p][I_END] - d["paths"][p][s0])
                                            for w, d in zip(weights, draws)) / wsum}
    chain_share_under_1pct = next((row["date"] for row in curve if row["chain_only"] < 0.01 * row["p"]), None)
    chain_under_1pct = next((row["date"] for row in curve if row["chain_only"] < 0.01), None)
    further_linked = sum(w * (1.0 - math.exp(-(d["listed_reports"][I_END] - d["listed_reports"][s0])))
                         for w, d in zip(weights, draws)) / wsum
    remaining = sum(w * d["remaining_listed"] for w, d in zip(weights, draws)) / wsum
    out.update({
        "curve": curve,
        "chain_only": conditional(draws, i0, CHAIN)["p"],
        "importation_only": conditional(draws, i0, ("importation",))["p"],
        "decomposition": decomposition,
        "secondary": {"p_further_linked_case_reported_in_window": further_linked,
                      "expected_remaining_listed_infections": remaining,
                      "first_date_chain_under_1_percent_of_residual": chain_share_under_1pct,
                      "first_date_chain_probability_below_1_percent": chain_under_1pct},
    })
    return out


SENSITIVITY = [
    ("C1_programme_prior", "Incubation fixed at the programme prior, gamma(4, rate 0.6), mean 6.67 days", {"incubation": "programme_prior"}),
    ("C2_decided_prior", "Incubation fixed at the decided prior, gamma(3, rate 0.406), mean 7.39 days", {"incubation": "decided_prior"}),
    ("C3_kenya_only", "Incubation from the Kenya cluster alone (flat prior on the grid); a lower bound, set by the grid's upper edge", {"incubation": "kenya_only"}),
    ("C4_case2_as_source", "Incubation from the reading in which case 2, rather than the first case, infected two relatives", {"incubation": "A1_combined"}),
    ("C5_unlisted_high", "Unlisted share of the first case's transmission 25 to 50 percent", {"priors": {"unlisted_share": (0.25, 0.50)}}),
    ("C6_extension_high", "Follow-up extended for 70 to 100 percent of contacts still incubating", {"priors": {"extension_share": (0.7, 1.0)}}),
    ("C7_no_extension", "No contact's follow-up is extended beyond 21 days", {"no_extension": True}),
    ("C8_import_9_october_range", "Importations 0.02 to 0.10 per week (the 9 October range)", {"import_rate_per_week": (0.02, 0.10)}),
    ("C9_import_doubled", "Importation rate doubled", {"import_multiplier": 2.0}),
    ("C10_r2_high", "Second-generation reproduction 1 to 3", {"priors": {"r2": (1.0, 3.0)}}),
    ("C11_detection_low", "Detection probability 0.5 to 0.8", {"priors": {"p_detect": (0.5, 0.8)}}),
    ("C12_no_importation", "No importation term (the first case's chain and the contained cases only)", {"no_importation": True}),
]
SENSITIVITY_DATES = ("2026-10-17", "2026-10-24", "2026-10-31", "2026-11-07")


def sensitivity_rows(n: int = N_DRAWS) -> list[dict]:
    rows = []
    for name, label, opts in SENSITIVITY:
        draws = run(opts, n=n)
        by_date = {}
        for i, date in enumerate(CURVE_DATES):
            if date.isoformat() in SENSITIVITY_DATES:
                by_date[f"p_{date.isoformat()}"] = conditional(draws, i)["p"]
        rows.append({"variant": name, "label": label, **by_date,
                     "chain_only": conditional(draws, 0, CHAIN)["p"],
                     "importation_only": conditional(draws, 0, ("importation",))["p"]})
    return rows


# ---------------------------------------------------------------------------------------
# Exposure-gap inventory (published facts only).
# ---------------------------------------------------------------------------------------
def incubation_quantile(grid: Sequence[tuple[float, float, float]], q: float) -> float:
    lo, hi = 0.0, 80.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(w * ki.cdf(k, mu, mid) for k, mu, w in grid) < q:
            lo = mid
        else:
            hi = mid
    return hi


def _closes(exposure_end: float, inc99: float, lag: float) -> str:
    return (kc.T0 + dt.timedelta(days=math.ceil(exposure_end + inc99 + lag) - 1)).isoformat()


def exposure_gaps(inc99: float) -> list[dict]:
    rows = [
        ("journey_before_kenya", "Road travel from the DRC through Beni to Kampala on 2 October, one night in Uganda, and the flight to Nairobi on 3 October",
         "WHO AFRO (6 October)", (-1.0, 0.5),
         "Uganda's ministry is following its contacts; flight contacts were listed in Kenya (27 passengers and crew on 6 October)",
         "Only if a person exposed on the journey is confirmed in Kenya", LAG_UNLISTED),
        ("arrival_and_care_before_isolation", "Taken to hospital on arrival and isolated there; care began in an emergency area before transfer to an isolation facility",
         "WHO 2026-DON619; minister relayed by the press (7 October)", (0.5, 2.0),
         "People present before isolation other than staff are the main group that may not all be listed; not published",
         "Yes: an unlisted person who falls ill", LAG_UNLISTED),
        ("hospital_staff", "Three health workers who managed the first case on 3 and 4 October are confirmed cases; they were in quarantine from 9 October",
         "Kenya Ministry of Health (17 October), Africa CDC", (0.6, 2.0),
         "Listed and in quarantine; other staff, sample handling and cleaning not published",
         "Only staff not under follow-up at onset", LAG_LISTED),
        ("relatives_and_visitors", "Three relatives are confirmed cases",
         "Kenya Ministry of Health (14, 16 and 17 October); WHO AFRO (16 October)", (0.5, 2.98),
         "Listed and in quarantine from 6 October", "No, while in quarantine", LAG_LISTED),
        ("body_and_burial", "Safe and dignified burial conducted", "WHO 2026-DON619", (2.98, 3.6),
         "Burial teams follow Ebola protocols; their listing is not published", "Only a person not under follow-up", LAG_UNLISTED),
        ("untraced_contacts", "2 of 130 listed contacts not yet traced on 17 October", "Kenya Ministry of Health (17 October), Africa CDC",
         (0.5, 3.6), "Listed, not traced", "Yes", LAG_UNLISTED),
        ("contacts_abroad", "5 listed contacts were outside Kenya on 14 October and notified to their countries", "ECDC, citing the ministry (14 October)",
         (0.5, 2.98), "Followed by other countries", "Only if confirmed in Kenya", LAG_UNLISTED),
        ("contacts_of_cases_2_to_7", "All six were in quarantine when symptoms began and were isolated at once",
         "Kenya Ministry of Health (14, 16 and 17 October)", (10.0, 15.0),
         "Their contacts are not published", "Only a contact not under follow-up", LAG_UNLISTED),
        ("release_while_incubating", "Follow-up of the first case's contacts runs 21 days from the last exposure, to about 25 and 26 October; the ministry says more cases among quarantined contacts are possible until then",
         "Kenya Ministry of Health (17 October)", (2.0, 3.6), "Listed; released after 21 days unless extended",
         "Yes: onset after release", LAG_RELEASED),
        ("new_importation", "Travellers from affected areas; 652,584 travellers screened by 7 October", "Kenya Ministry of Health (7 October)",
         None, "Not a contact", "Yes", None),
    ]
    out = []
    for gap_id, facts, source, window, listing, counts, lag in rows:
        out.append({"gap": gap_id, "facts": facts, "source": source,
                    "exposure_window_model_days": window, "listing": listing, "counts_here": counts,
                    "risk_window_closes": _closes(window[1], inc99, lag) if window else "open while the outbreak continues"})
    return out


# ---------------------------------------------------------------------------------------
# Artifact.
# ---------------------------------------------------------------------------------------
CITATIONS = {
    **{key: kc.CITATIONS[key] for key in ("faye2015", "who_ert2014", "who_don619", "who_afro_2026_10_06", "kenya_moh_2026_10_07")},
    **ki.CITATIONS,
    "lovs_incubation_2026_10_17": "lovs/forecast/kenya_incubation.py and data/case-conditioned/kenya-2026-10-17-incubation.json",
    "lovs_kenya_2026_10_09": "lovs/forecast/kenya_conditioned.py: unlisted share 5 to 25 percent, registered 9 October",
    "lovs_kenya_2026_10_14": "lovs/forecast/kenya_second_case.py: quarantine exposure 0.002 to 0.06 and unlisted share 0 to 10 percent, registered 14 October",
}
PROVENANCE = {
    "incubation": "source: lovs_incubation_2026_10_17 (combined posterior); C1 to C4 vary it",
    "listed_infections": "source: the six published cases and their exposure windows; Jeffreys-type Gamma posterior (judgement on the prior form)",
    "release_days": "source: 21-day follow-up from the last exposure (kenya_moh_2026_10_17); the extension share is judgement",
    "unlisted_share": "judgement registered on 9 October (lovs_kenya_2026_10_09)",
    "untraced": "source: 2 of 130 untraced (kenya_moh_2026_10_17); their relative attack rate is judgement",
    "detection": "judgement for the detection probability; onset-to-hospital lag of 5 days from who_ert2014; 1 and 3 days for listed and released contacts are judgement",
    "second_generation": "judgement anchored on faye2015 community and funeral values before control (cases other than health workers, March 2014)",
    "contained_onward": "source: faye2015 community value for cases other than health workers, March 2014, before infection control; exposure and unlisted ranges registered on 14 October (lovs_kenya_2026_10_14)",
    "importation": "source: one detected importation into Kenya in 155 days (data/international-events.json), Jeffreys prior; constant rate and arrival-to-report lag are judgement",
    "clock": "source: who_don619, who_afro_2026_10_06 and the ministry statements",
}
DISCLOSURES = [
    "The question, priors, windows, sensitivity rows and decision rules were fixed in a plan before the first run: sha256 cf4365e4b81bcc3a16deb111eeff46f6e9200faf60b77a88ea38c1b25db645b1, written 2026-10-17 at about 21:31Z.",
    "The incubation analysis was run before this forecast and its results were seen. The plan fixed in advance that this forecast uses its combined posterior.",
    "No reference class of escapes after a contained cluster has been coded, so the model is not calibrated against past outcomes. The importation-only benchmark is scored beside it to test whether modelling the chain adds anything.",
    "The importation term assumes a constant rate equal to Kenya's own history: one detected importation in the 155 days since the outbreak was declared. The DRC epidemic has grown and moved since, which this does not capture; rows C8, C9 and C12 bound it, and the chain and importation parts are stated separately.",
    "The setting values for onward spread come from Zaire ebolavirus in Conakry in 2014 (Faye 2015). No Bundibugyo setting-specific estimate exists.",
    "Where any Kenyan case was infected is not stated by any authority. The authorities say the six linked cases were contacts of the first case and were in quarantine when their symptoms began; nothing here says more. Nothing here identifies a person or a facility.",
    "A YES is not an outbreak in Kenya: a single new importation, isolated at once, would resolve it, and about 0.22 of the forecast comes from importation alone. The forecast says nothing about the quality of Kenya's response. It is the programme's own estimate, not an official one, and it is not calibrated against past outcomes.",
    "The 80 percent interval covers parameter uncertainty inside this model, not model-structure error.",
    "Correction at review, before publication: the registered curve formula conditioned on no report by the end of each date and counted every report after it. For the 17 October row that let in reports of cases confirmed on 17 October, which the question excludes. The window now opens with reports of cases confirmed on 18 October; rows from 18 October on are unchanged. The headline fell from 0.562 to the value shown.",
    "Correction at review, before publication: the plan registered the first date the first case's chain is under 1 percent of the residual probability. The first run reported instead the first date its own probability falls below 1 percent. Both are now given; under the registered reading no date in the window qualifies.",
    "Clarified at review, before publication: the plan's resolution rule left four points open. The coverage review now closes on 2026-12-05; follow-up by any public-health authority counts, including another country's and the workplace monitoring of health workers caring for cases; a listed contact with no stated status at onset does not count, and a case described neither as a listed contact nor as under follow-up counts; the result is published whatever the outcome. None of these changes the model or any number. The relatives' group, first labelled household, is now labelled relatives, since the authorities say only relative.",
    "Read at review: C3 inherits the incubation grid's upper edge. With a longer grid the Kenya-only row would be higher, so its value is a lower bound.",
    "Read at review: the detection probability also scales the after-release and contained-onward pathways, and onsets after an extended follow-up are not counted as contained cases that can spread. The draw of listed infections and the incubation fit treat the six cases slightly differently. Each of these moves the headline by less than half a percentage point.",
    "Correction after the first run, before any publication: the onset mixture of cases 2 to 7 was summed rather than averaged, so their onward term counted each case six times. Fixing this coding error moved the headline from 0.586 to 0.562; no prior or rule changed.",
]


payload_digest = kc.payload_digest


def build_artifact(main_run: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    inc99 = incubation_quantile(incubation_source("combined"), 0.99)
    head = main_run["headline"]
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "kenya-bdbv-2026-10-17-escape",
        "model_version": MODEL_VERSION,
        "question": ("Will Kenya report at least one laboratory-confirmed BDBV case, confirmed 2026-10-18 to "
                     "2026-11-14 and reported by 2026-11-17, that was not under the authority's follow-up as a "
                     "known contact (quarantine, isolation or active monitoring) when its symptoms began?"),
        "counts": ["an unlisted contact", "a listed contact who was never traced",
                   "a contact whose onset came after their follow-up ended", "a new importation",
                   "a case the authority describes as unlinked or as community transmission"],
        "does_not_count": ["a case among contacts in quarantine, isolation or active monitoring at onset",
                           "suspected or probable cases", "cases confirmed outside Kenya", "a retracted report"],
        "evidence_through_utc": EVIDENCE_THROUGH_UTC,
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {"probability": head["p"], "interval_80": [head["p10"], head["p90"]],
                     "chain_only": main_run["chain_only"], "importation_only": main_run["importation_only"],
                     "interval_note": ("Weighted 10th to 90th percentile across draws; parameter uncertainty "
                                       "inside this model only.")},
        "benchmark": {"importation_only": main_run["importation_only"],
                      "note": "Kenya's own importation rate alone, with the same term as the forecast."},
        "curve": main_run["curve"],
        "curve_note": ("Each row: probability of YES given no qualifying case reported by the end of that date "
                       "(East Africa Time). The 2026-10-17 row is the current forecast; its window opens with cases confirmed on 2026-10-18."),
        "decomposition": main_run["decomposition"],
        "secondary": main_run["secondary"],
        "secondary_note": "Not scored. The further linked cases are among listed contacts and do not count for the question.",
        "sensitivity": list(sensitivity),
        "exposure_gaps": exposure_gaps(inc99),
        "exposure_gaps_note": ("Risk window closes: the end of exposure plus the 99th percentile of the combined "
                               "incubation posterior ({:.1f} days) plus the mean detection lag for that group.".format(inc99)),
        "scoring_plan": {
            "scored_value": "the 2026-10-17 curve value, fixed by the artifact hash before publication",
            "scored_against": ["the importation-only benchmark"],
            "resolution": ("an authority report (Kenya Ministry of Health, WHO, Africa CDC, ECDC or US CDC) of a "
                           "qualifying case, with a coverage review naming its sources made between 2026-11-21 and "
                           "2026-12-05"),
            "follow_up": ("follow-up by any public-health authority counts, including another country's and the "
                          "workplace monitoring of health workers caring for cases"),
            "ambiguity": ("a case the authorities call a listed contact, with no statement of its status at onset, "
                          "does not count; a case the authorities describe neither as a listed contact nor as in "
                          "quarantine, isolation or active monitoring at onset counts. Anything else unclear at the "
                          "coverage review is decided by a reviewer other than the forecast's author, named by role "
                          "in the review"),
            "publication": ("published whatever the outcome; if first published after 2026-10-17, the curve value "
                            "standing on the publication date is shown beside the scored value"),
            "if_a_case_occurs": "score it in public; say nothing about the person beyond the authority's words",
            "if_no_case_occurs": "score it in public and state that one outcome cannot separate nearby probabilities",
        },
        "inputs": {"provenance": PROVENANCE, "priors": PRIORS, "observed": OBSERVED,
                   "exposure": EXPOSURE, "release_days": RELEASE, "unlisted_exposure": UNLISTED_EXPOSURE,
                   "contained_onsets": CONTAINED_ONSETS, "listed_total": LISTED_TOTAL, "untraced": UNTRACED,
                   "lags": {"listed": LAG_LISTED, "released": LAG_RELEASED, "unlisted": LAG_UNLISTED,
                            "report_delay": REPORT_DELAY},
                   "clock": "days from 2026-10-03 00:00 East Africa Time", "citations": CITATIONS},
        "disclosures": DISCLOSURES,
        "method": {"seed": SEED, "n_draws": N_DRAWS, "grid_step_days": H, "code": "lovs/forecast/kenya_escape.py",
                   "incubation": "lovs/forecast/kenya_incubation.py"},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-sensitivity", action="store_true")
    args = parser.parse_args(argv)
    main_run = summarise(run())
    sens = [] if args.no_sensitivity else sensitivity_rows()
    artifact = build_artifact(main_run, sens)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out.name, "probability": artifact["headline"]["probability"],
                      "interval_80": artifact["headline"]["interval_80"],
                      "payload_sha256": artifact["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
