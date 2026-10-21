# SPDX-License-Identifier: Apache-2.0
"""Nigeria nowcast: infections the first case has probably already caused, as of 20 October 2026.

A descriptive nowcast, not a calibration row. It is a second, independent method beside the
registered Nigeria case-conditioned forecast (``lovs/forecast/nigeria_conditioned.py``) and
changes nothing in it: the forecast uses setting reproduction numbers, this nowcast counts
infections by exposure group and attack rate. The rules were registered before any code or run
(``PLAN_SHA256``, ``PLAN_REGISTERED_UTC``) and are restated in the artifact's ``inputs``.

Model, in one paragraph. The clock is days from 2026-10-07 00:00 WAT (UTC+1); onset is day 0.
The first case spent days 0 to 4 in the community, days 4 to T_iso in a facility where Ebola was
not suspected, and was isolated at T_iso (uniform 12.5 to 13.5) under VHF-level precautions. An
infectiousness profile f(d) over days 0 to 14 (a survivor table, mixed per draw with a fatal
table at weight w_fatal) gives each window's share s. In each draw, group g with n_g people
exposed has Binomial(n_g, AR_g x s_g x M_g x v) infections, where AR_g is a whole-course attack
rate for that exposure type, M_g the precaution multiplier and v a survivor viraemia factor. The
groups are household and community (H), health workers (W), the case's own bedside caregivers
(C) and other patients and visitors (O). Infection times are drawn from f within each window and
incubation from the programme prior read at run time. Second-generation infections caused before
T_iso are Poisson(R2 x F_sym). Listing, detection and reporting lags turn first-generation onsets
into reports by date (N6), the checkable quantity.

The attack rates are Zaire-species (EBOV) values transferred to Bundibugyo virus, and no
Bundibugyo viral-load series exists, so f(d) is an EBOV and SUDV shape carried across by
assumption. Nothing here asserts that anyone is infected; every count is expected under this
model.

Stdlib only and deterministic for a fixed seed: every variate is built from
``random.Random.random()`` through the helpers of ``kenya_conditioned``.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import kenya_conditioned as kc
from lovs.lovs_priors_bundibugyo import BUNDIBUGYO_PRIORS_STAGE_TWO

REPO = kc.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "nigeria-2026-10-20-nowcast.json"
MODEL_VERSION = "nigeria_nowcast-v1.0.0"
PLAN_SHA256 = "c814e4ddffdc986414d5e3932df38469a940a05b18fba4685e28e651c19c1d6d"
EVIDENCE_SHA256 = "5b3fec30a8da4f05ca456f6d729d3cd400b80f6e95fc1df6dc9e5d716ff9a6b2"
PLAN_REGISTERED_UTC = "2026-10-20T23:21:21Z"
PRECOMMITMENT_PAGE = "reference:bdbv-nigeria-nowcast-precommitment-2026-10-20"
SEED = 20261021
N_DRAWS = 40_000

# ---------------------------------------------------------------------------------------
# Clock (days from 2026-10-07 00:00 WAT; onset is day 0).
# ---------------------------------------------------------------------------------------
DATE0 = dt.date(2026, 10, 7)
T_COMMUNITY_END = 4.0            # care at the facility from 11 October
T_ISO = (12.5, 13.5)             # laboratory confirmation and isolation on 20 October
AS_OF = dt.date(2026, 10, 20)    # all quantities as of the end of this date
REPORT_DELAY = 0.5
ONSET_FIRST, ONSET_LAST = dt.date(2026, 10, 8), dt.date(2026, 11, 17)     # N5
REPORT_FIRST, REPORT_LAST = dt.date(2026, 10, 21), dt.date(2026, 11, 17)  # N6, N7


def day_end(date: dt.date) -> float:
    """Model time at the end of a WAT calendar date."""
    return float((date - DATE0).days + 1)


T_AS_OF = day_end(AS_OF)

# ---------------------------------------------------------------------------------------
# Infectiousness profile, evidence.md section A3 (judgement on Lanini 2015 and WHO ERT 2014).
# ---------------------------------------------------------------------------------------
F_SURVIVOR = (0.01, 0.02, 0.04, 0.07, 0.10, 0.12, 0.12, 0.11, 0.10, 0.08, 0.07, 0.05, 0.04, 0.04, 0.03)
F_FATAL = (0.01, 0.02, 0.03, 0.05, 0.07, 0.09, 0.10, 0.11, 0.11, 0.10, 0.09, 0.08, 0.06, 0.05, 0.03)
# How the daily table sits on the continuous clock (spec issue S1). "time_point": the cumulative
# column is F at clock time d, so f(0) is a point mass at onset and f(d) is spread over (d-1, d];
# this reproduces evidence.md's stated shares and ends the profile at the end of 20 October.
# "day_of_illness": f(d) is spread over [d, d+1); shown only as an unregistered row.
READINGS = ("time_point", "day_of_illness")
READING = "time_point"


def knots(table: Sequence[float], reading: str = READING) -> tuple[tuple[float, float], ...]:
    """Piecewise-linear cumulative F as (time, F) knots; F is 0 before the first knot."""
    if reading not in READINGS:
        raise ValueError(f"unknown reading {reading!r}")
    cum, out = 0.0, []
    if reading == "day_of_illness":
        out.append((0.0, 0.0))
    for d, f in enumerate(table):
        cum += f
        out.append((float(d + 1 if reading == "day_of_illness" else d), cum))
    return tuple((t, F / cum) for t, F in out)


def mix(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]], w: float) -> tuple[tuple[float, float], ...]:
    return tuple((ta, (1.0 - w) * fa + w * fb) for (ta, fa), (_, fb) in zip(a, b))


def cdf(kn: Sequence[tuple[float, float]], t: float) -> float:
    if t < kn[0][0]:
        return 0.0
    if t >= kn[-1][0]:
        return 1.0
    for (t0, f0), (t1, f1) in zip(kn, kn[1:]):
        if t < t1:
            return f0 + (f1 - f0) * (t - t0) / (t1 - t0)
    return 1.0


def window_shares(kn: Sequence[tuple[float, float]], t_iso: float) -> dict[str, float]:
    """Shares of infectiousness in the community, unsuspected-facility and isolation windows."""
    f4, fi = cdf(kn, T_COMMUNITY_END), cdf(kn, t_iso)
    return {"community": f4, "facility": fi - f4, "isolation": 1.0 - fi}


def invert(kn: Sequence[tuple[float, float]], u: float) -> float:
    """Smallest t with F(t) >= u."""
    if u <= kn[0][1]:
        return kn[0][0]
    for (t0, f0), (t1, f1) in zip(kn, kn[1:]):
        if u <= f1:
            return t0 + (t1 - t0) * (u - f0) / (f1 - f0)
    return kn[-1][0]


def infection_time(rng: random.Random, kn: Sequence[tuple[float, float]], lo: float | None, hi: float | None) -> float:
    """A time drawn from f restricted to the window (lo, hi]; lo None means from the start."""
    f_lo = 0.0 if lo is None else cdf(kn, lo)
    f_hi = 1.0 if hi is None else cdf(kn, hi)
    return invert(kn, f_lo + (f_hi - f_lo) * rng.random())


# ---------------------------------------------------------------------------------------
# Registered inputs (plan section 2).
# ---------------------------------------------------------------------------------------
W_FATAL = (0.0, 0.3)
V_SURVIVOR = (0.5, 1.0)
GROUPS = {
    "H_direct": {"n": (1, 6), "ar": (0.25, 0.48)},     # direct care or body fluids, Dean 2016 nursing care
    "H_other": {"n": (0, 10), "ar": (0.002, 0.01)},    # no direct contact, Dean 2016
    "W": {"n": (8, 35), "ar": (0.04, 0.20)},           # Tomori 1999 Kikwit no-barrier rates
    "C": {"n": (1, 3), "ar": (0.25, 0.48)},            # Dean 2016 nursing care
    "O": {"n": (5, 40), "ar": (0.002, 0.02)},          # judgement
}
SCENARIOS = {"N1": {"weight": 0.6, "level": "NONE", "multiplier": kc.LEVELS["NONE"]},
             "N2": {"weight": 0.4, "level": "STANDARD", "multiplier": kc.LEVELS["STANDARD"]}}
M_VHF = kc.LEVELS["VHF"]
R2 = (0.8, 0.6)                  # lognormal median, log-sd
UNLISTED = (0.15, 0.50)
P_DETECT = (0.85, 1.0)
LAG_LISTED_PRE_MEAN = 1.5        # from T_iso, onset before T_iso, Erlang shape 2
LAG_LISTED_POST_MEAN = 1.0       # from onset, onset after T_iso
LAG_UNLISTED_MEAN = 5.0          # from max(onset, T_iso)
LAG_SHAPE = 2
AR_W_CAP = 0.4


def incubation_prior() -> tuple[int, float]:
    """The programme incubation prior v0.2.0 as (shape, rate), read at run time."""
    shape, rate = BUNDIBUGYO_PRIORS_STAGE_TWO.incubation_gamma
    if float(shape) != int(shape):
        raise ValueError(f"incubation shape {shape} is not an integer; the stdlib Erlang sampler would be wrong")
    return int(shape), float(rate)


def _randint(rng: random.Random, lo_hi: Sequence[int]) -> int:
    lo, hi = lo_hi
    return lo + min(int(rng.random() * (hi - lo + 1)), hi - lo)


def _binomial(rng: random.Random, n: int, p: float) -> int:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p}")
    return sum(1 for _ in range(n) if rng.random() < p)


def _poisson(rng: random.Random, lam: float) -> int:
    n, term, u = 0, math.exp(-lam), rng.random()
    acc = term
    while u > acc:
        n += 1
        term *= lam / n
        acc += term
        if term == 0.0 and n > lam:
            break
    return n


def _erlang_mean(rng: random.Random, mean: float, shape: int = LAG_SHAPE) -> float:
    return kc._erlang(rng, shape, shape / mean)


DEFAULTS: dict[str, Any] = {
    "reading": READING, "w_fatal": W_FATAL, "v": V_SURVIVOR, "ar_w_scale": 1.0, "n_w": GROUPS["W"]["n"],
    "scenario": None, "incubation_rate": None, "unlisted": UNLISTED,
}


def options(overrides: Mapping[str, Any] | None = None) -> dict:
    o = dict(overrides or {})
    unknown = set(o) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown option keys: {sorted(unknown)}")
    opts = {**DEFAULTS, **o}
    if opts["scenario"] not in (None, "N1", "N2"):
        raise ValueError("scenario must be None, 'N1' or 'N2'")
    return opts


# ---------------------------------------------------------------------------------------
# One draw.
# ---------------------------------------------------------------------------------------
def draw(rng: random.Random, opts: Mapping[str, Any], inc: tuple[int, float],
         surv: Sequence[tuple[float, float]], fatal: Sequence[tuple[float, float]]) -> dict:
    t_iso = kc._uniform(rng, T_ISO)
    w = kc._uniform(rng, opts["w_fatal"])
    v = kc._uniform(rng, opts["v"])
    kn = mix(surv, fatal, w)
    s = window_shares(kn, t_iso)
    scen = opts["scenario"] or ("N1" if rng.random() < SCENARIOS["N1"]["weight"] else "N2")
    m_fac = kc._uniform(rng, SCENARIOS[scen]["multiplier"])
    m_vhf = kc._uniform(rng, M_VHF)
    n = {g: _randint(rng, opts["n_w"] if g == "W" else spec["n"]) for g, spec in GROUPS.items()}
    ar = {g: kc._uniform(rng, spec["ar"]) for g, spec in GROUPS.items()}
    ar["W"] = min(ar["W"] * opts["ar_w_scale"], AR_W_CAP)
    r2 = kc._lognormal(rng, *R2)
    u = kc._uniform(rng, opts["unlisted"])
    p_detect = kc._uniform(rng, P_DETECT)

    x = {
        "H_direct": _binomial(rng, n["H_direct"], ar["H_direct"] * s["community"] * v),
        "H_other": _binomial(rng, n["H_other"], ar["H_other"] * s["community"] * v),
        "W_facility": _binomial(rng, n["W"], ar["W"] * s["facility"] * m_fac * v),
        "C_facility": _binomial(rng, n["C"], ar["C"] * s["facility"] * v),
        "O_facility": _binomial(rng, n["O"], ar["O"] * s["facility"] * m_fac * v),
    }
    x["W_isolation"] = _binomial(rng, n["W"] - x["W_facility"], ar["W"] * s["isolation"] * m_vhf * v)
    x["C_isolation"] = _binomial(rng, n["C"] - x["C_facility"], ar["C"] * s["isolation"] * m_vhf * v)
    windows = {"H_direct": (None, T_COMMUNITY_END), "H_other": (None, T_COMMUNITY_END),
               "W_facility": (T_COMMUNITY_END, t_iso), "C_facility": (T_COMMUNITY_END, t_iso),
               "O_facility": (T_COMMUNITY_END, t_iso), "W_isolation": (t_iso, None), "C_isolation": (t_iso, None)}

    shape, rate = inc
    onsets, reports, second = [], [], 0
    for key in ("H_direct", "H_other", "W_facility", "C_facility", "O_facility", "W_isolation", "C_isolation"):
        lo, hi = windows[key]
        for _ in range(x[key]):
            onset = infection_time(rng, kn, lo, hi) + kc._erlang(rng, shape, rate)
            onsets.append(onset)
            if onset < t_iso:
                second += _poisson(rng, r2 * cdf(surv, t_iso - onset))
            listed = rng.random() >= u
            if rng.random() < p_detect:
                if not listed:
                    confirm = max(onset, t_iso) + _erlang_mean(rng, LAG_UNLISTED_MEAN)
                elif onset < t_iso:
                    confirm = t_iso + _erlang_mean(rng, LAG_LISTED_PRE_MEAN)
                else:
                    confirm = onset + _erlang_mean(rng, LAG_LISTED_POST_MEAN)
                reports.append(confirm + REPORT_DELAY)
    groups = {"H": x["H_direct"] + x["H_other"], "W": x["W_facility"] + x["W_isolation"],
              "C": x["C_facility"] + x["C_isolation"], "O": x["O_facility"]}
    return {"t_iso": t_iso, "w_fatal": w, "shares": s, "scenario": scen, "parts": x, "groups": groups,
            "total": sum(groups.values()), "onsets": sorted(onsets), "reports": sorted(reports),
            "second_generation": second}


def run(overrides: Mapping[str, Any] | None = None, n: int = N_DRAWS, seed: int = SEED) -> list[dict]:
    opts = options(overrides)
    shape, rate = incubation_prior()
    if opts["incubation_rate"] is not None:
        rate = float(opts["incubation_rate"])
    surv, fatal = knots(F_SURVIVOR, opts["reading"]), knots(F_FATAL, opts["reading"])
    rng = random.Random(seed)
    return [draw(rng, opts, (shape, rate), surv, fatal) for _ in range(n)]


# ---------------------------------------------------------------------------------------
# Summaries N1 to N7.
# ---------------------------------------------------------------------------------------
def _band(values: Sequence[float]) -> dict:
    ordered = sorted(values)
    pick = lambda q: ordered[min(int(q * len(ordered)), len(ordered) - 1)]
    return {"mean": sum(ordered) / len(ordered), "p10": pick(0.1), "p50": pick(0.5), "p90": pick(0.9)}


def _distribution(values: Sequence[int], top: int = 10) -> dict:
    n = len(values)
    out = {str(k): sum(1 for x in values if x == k) / n for k in range(top)}
    out[f"{top}+"] = sum(1 for x in values if x >= top) / n
    return out


def _p_any(values: Sequence[int]) -> float:
    return sum(1 for x in values if x > 0) / len(values)


def _dates(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def reports_by(d: Mapping[str, Any], t: float) -> int:
    return bisect.bisect_right(d["reports"], t)


def summarise(draws: Sequence[Mapping[str, Any]]) -> dict:
    n = len(draws)
    groups = ("H", "W", "C", "O")
    totals = [d["total"] for d in draws]
    n1 = {"total": {**_band(totals), "distribution": _distribution(totals)}}
    for g in groups:
        vals = [d["groups"][g] for d in draws]
        n1[g] = {**_band(vals), "distribution": _distribution(vals)}
    n1["parts_mean"] = {k: sum(d["parts"][k] for d in draws) / n for k in draws[0]["parts"]}
    n2 = {"any": _p_any(totals), **{g: _p_any([d["groups"][g] for d in draws]) for g in groups},
          "any_health_worker": _p_any([d["groups"]["W"] for d in draws])}
    n3_vals = [bisect.bisect_right(d["onsets"], T_AS_OF) for d in draws]
    n3 = {**_band(n3_vals), "p_at_least_one": _p_any(n3_vals)}
    n4_vals = [d["second_generation"] for d in draws]
    n4 = {**_band(n4_vals), "p_at_least_one": _p_any(n4_vals)}

    onset_days = _dates(ONSET_FIRST, ONSET_LAST)
    lo_t, hi_t = day_end(ONSET_FIRST) - 1.0, day_end(ONSET_LAST)
    per_day = [0.0] * len(onset_days)
    before = after = 0
    for d in draws:
        for t in d["onsets"]:
            if t < lo_t:
                before += 1
            elif t >= hi_t:
                after += 1
            else:
                per_day[int(t - lo_t)] += 1
    n5 = {"by_date": [{"date": day.isoformat(), "expected_onsets": c / n} for day, c in zip(onset_days, per_day)],
          "before_" + ONSET_FIRST.isoformat(): before / n, "after_" + ONSET_LAST.isoformat(): after / n}

    n6_rows = []
    for day in _dates(REPORT_FIRST, REPORT_LAST):
        counts = [reports_by(d, day_end(day)) for d in draws]
        band = _band(counts)
        n6_rows.append({"date": day.isoformat(), "expected_cumulative": band["mean"],
                        "interval_80": [band["p10"], band["p90"]], "p_at_least_one": _p_any(counts)})
    n6 = {"by_date": n6_rows,
          "expected_reports_modelled_by_end_of_" + AS_OF.isoformat(): sum(reports_by(d, T_AS_OF) for d in draws) / n,
          "expected_never_confirmed": sum(d["total"] - len(d["reports"]) for d in draws) / n}
    last = n6_rows[-1]
    n7 = {"p_at_least_one_report_by_" + REPORT_LAST.isoformat(): last["p_at_least_one"],
          "expected_reports": last["expected_cumulative"], "interval_80": last["interval_80"]}
    shares = {k: sum(d["shares"][k] for d in draws) / n for k in ("community", "facility", "isolation")}
    scen = {s: sum(1 for d in draws if d["scenario"] == s) / n for s in SCENARIOS}
    return {"N1_infected_by_group": n1, "N2_p_at_least_one_infection": n2,
            "N3_onset_by_end_of_20_october": n3, "N4_second_generation_by_isolation": n4,
            "N5_first_generation_onsets": n5, "N6_cumulative_reports": n6, "N7_cross_check": n7,
            "mean_window_shares": shares, "scenario_frequency": scen}


def row_summary(draws: Sequence[Mapping[str, Any]]) -> dict:
    """The sensitivity table's columns."""
    n = len(draws)
    t_end = day_end(REPORT_LAST)
    counts = [reports_by(d, t_end) for d in draws]
    return {"expected_first_generation": sum(d["total"] for d in draws) / n,
            "p_any_infection": _p_any([d["total"] for d in draws]),
            "p_any_health_worker": _p_any([d["groups"]["W"] for d in draws]),
            "expected_onset_by_20_october": sum(bisect.bisect_right(d["onsets"], T_AS_OF) for d in draws) / n,
            "expected_second_generation": sum(d["second_generation"] for d in draws) / n,
            "expected_reports_by_17_november": sum(counts) / n,
            "p_report_by_17_november": _p_any(counts)}


SENSITIVITY = [
    ("S1_survivor_only", "Survivor profile only (w_fatal 0)", {"w_fatal": (0.0, 0.0)}),
    ("S2_fatal_only", "Fatal profile only", {"w_fatal": (1.0, 1.0)}),
    ("S3_hw_rate_halved", "Health-worker attack rate halved", {"ar_w_scale": 0.5}),
    ("S4_hw_rate_doubled", "Health-worker attack rate doubled, capped at 0.4", {"ar_w_scale": 2.0}),
    ("S5_hw_exposed_5_12", "Health workers exposed 5 to 12", {"n_w": (5, 12)}),
    ("S6_hw_exposed_30_60", "Health workers exposed 30 to 60", {"n_w": (30, 60)}),
    ("S7_scenario_n1_only", "Scenario N1 only (no precautions)", {"scenario": "N1"}),
    ("S8_scenario_n2_only", "Scenario N2 only (standard precautions)", {"scenario": "N2"}),
    ("S9_v_fixed_1", "Survivor viraemia factor v fixed at 1", {"v": (1.0, 1.0)}),
    ("S10_incubation_mean_10", "Incubation mean 10 days (shape 3)", {"incubation_rate": 0.3}),
    ("S11_unlisted_05_15", "Unlisted share 0.05 to 0.15", {"unlisted": (0.05, 0.15)}),
    ("S12_unlisted_50_80", "Unlisted share 0.5 to 0.8", {"unlisted": (0.5, 0.8)}),
]
UNREGISTERED_SENSITIVITY = [
    ("U1_day_of_illness_reading",
     ("Not a registered row; fixed before the first run (spec issue S1). The daily table read as day of "
      "illness, f(d) spread over [d, d+1), instead of evidence.md's time-point reading"),
     {"reading": "day_of_illness"}),
]


def sensitivity_rows(rows: Sequence[tuple[str, str, Mapping[str, Any]]] = SENSITIVITY, n: int = N_DRAWS) -> list[dict]:
    return [{"variant": name, "label": label, **row_summary(run(over, n=n))} for name, label, over in rows]


# ---------------------------------------------------------------------------------------
# Artifact.
# ---------------------------------------------------------------------------------------
CITATIONS = {
    "dean2016": "Dean NE et al. Clin Infect Dis 2016;62(10):1277-86, doi:10.1093/cid/ciw114",
    "tomori1999": "Tomori O et al. J Infect Dis 1999;179(Suppl 1):S98-101",
    "lanini2015": "Lanini S et al. J Clin Invest 2015;125(12):4692-8, doi:10.1172/JCI83111",
    "towner2004": "Towner JS et al. J Virol 2004;78(8):4330-41, doi:10.1128/JVI.78.8.4330-4341.2004",
    "who_ert2014": "WHO Ebola Response Team. N Engl J Med 2014;371:1481-95, doi:10.1056/NEJMoa1411100",
    "faye2015": "Faye O et al. Lancet Infect Dis 2015;15(3):320-6, doi:10.1016/S1473-3099(14)71075-8",
    "shuaib2014": "Shuaib F et al. MMWR 2014;63(39):867-72",
    "chevalier2014": "Chevalier MS et al. MMWR 2014;63(46):1087-8",
    "lopaz2015": "Lopaz MA et al. Euro Surveill 2015;20(1):21003, doi:10.2807/1560-7917.ES2015.20.1.21003",
    "macneil2010": "MacNeil A et al. Emerg Infect Dis 2010;16(12):1969-72, doi:10.3201/eid1612.100627",
    "lovs_incubation_prior": "lovs/lovs_priors_bundibugyo.py BUNDIBUGYO_PRIORS_STAGE_TWO v0.2.0 (LOVS PR #121)",
    "lovs_precaution_levels": "lovs/forecast/kenya_conditioned.py LEVELS",
    "who_afro_2026_10_20": "WHO Regional Office for Africa, Nigeria confirms imported case of Ebola Bundibugyo virus disease, 20 October 2026",
    "nigeria_ministry_2026_10_20": "Federal Ministry of Health and Social Welfare of Nigeria, statement of 20 October 2026",
}
CASE_EVIDENCE = [
    {"fact": "symptom onset 7 October 2026 (day 0)", "sources": ["who_afro_2026_10_20", "nigeria_ministry_2026_10_20"]},
    {"fact": "care sought at a private facility from 11 October (day 4), with Ebola not suspected",
     "sources": ["who_afro_2026_10_20", "nigeria_ministry_2026_10_20"]},
    {"fact": "laboratory confirmation and isolation on 20 October (T_iso, day 12.5 to 13.5)",
     "sources": ["who_afro_2026_10_20", "nigeria_ministry_2026_10_20"]},
    {"fact": "alive and described as stable on 20 October", "sources": ["nigeria_ministry_2026_10_20"]},
    {"fact": "44 contacts listed on 20 October", "sources": ["who_afro_2026_10_20", "nigeria_ministry_2026_10_20"]},
]
PROVENANCE = {
    "clock_and_windows": {"basis": "published", "note": "onset, start of facility care and isolation dates from the 20 October statements; T_iso's one-day width is judgement"},
    "infectiousness_profile_f": {"basis": "judgement", "note": "evidence.md A3, constructed on Lanini 2015 peak timing and decay and the WHO ERT 2014 serial interval minus incubation; no Bundibugyo series exists"},
    "w_fatal": {"basis": "judgement", "note": "0 to 0.3 because the case is alive and stable at day 13"},
    "v_survivor_viraemia": {"basis": "judgement", "note": "Lanini 2015: survivor peak about 1 log10 lower"},
    "H_direct": {"basis": "judgement", "note": "count 1 to 6 is judgement; attack rate anchored on the published Dean 2016 nursing-care rate 47.9%"},
    "H_other": {"basis": "judgement", "note": "count 0 to 10 is judgement; attack rate anchored on the published Dean 2016 no-direct-contact rate 0.8%"},
    "W": {"basis": "judgement", "note": "count 8 to 35 is judgement from Lagos, Dallas and Madrid exposure densities; attack rate anchored on the published Tomori 1999 Kikwit no-barrier rates (nurses 10%, technicians 11%, physicians 31%, others 4%)"},
    "C": {"basis": "judgement", "note": "count 1 to 3 is judgement; attack rate anchored on the published Dean 2016 nursing-care rate"},
    "O": {"basis": "judgement", "note": "count and attack rate are judgement between Dean 2016's no-direct-contact rate and shared-ward exposure"},
    "precaution_multipliers": {"basis": "judgement", "note": "programme LEVELS; no published multiplier exists"},
    "scenario_weights": {"basis": "judgement", "note": "N1 none 0.6, N2 standard 0.4"},
    "incubation": {"basis": "judgement", "note": "programme decided prior v0.2.0, gamma(3.0, 0.406), mean 7.39 days, read at run time"},
    "R2": {"basis": "judgement", "note": "lognormal median 0.8, log-sd 0.6; Faye 2015 published community R 1.4 times a survivor factor"},
    "unlisted_share": {"basis": "judgement", "note": "0.15 to 0.50, the same range as the registered forecast"},
    "detection_lags": {"basis": "judgement", "note": "Erlang shape 2; the unlisted mean of 5 days is anchored on the published WHO ERT 2014 onset-to-hospital interval"},
    "p_detect": {"basis": "judgement", "note": "0.85 to 1.0; some cases are mild or never tested"},
}
WORDING = ("Counts are expected numbers under this model: people probably already infected, never people known to be "
           "infected. The attack rates are Zaire-species (EBOV) values transferred to Bundibugyo virus, and no "
           "Bundibugyo viral-load series exists.")
DISCLOSURES = [
    f"Rules fixed in a plan before any code or run: sha256 {PLAN_SHA256}, registered {PLAN_REGISTERED_UTC} on the precommitment page {PRECOMMITMENT_PAGE}, with the evidence synthesis sha256 {EVIDENCE_SHA256}. No parameter changed after the first full run.",
    "Descriptive context, not a calibration row. It is a second, independent method beside the registered Nigeria case-conditioned forecast and changes nothing in it. The author had not seen any output of that forecast before this nowcast's first run and its precommitment entry; its headline was read afterwards only to fill the N7 cross-check. The two are shown side by side and are not averaged.",
    WORDING,
    "Spec issue S1, resolved before the first run: the daily f(d) table is read with its cumulative column as F at clock time d, so f(0) is a point mass at onset and f(d) is spread over (d-1, d]. This reproduces the evidence synthesis's stated shares for this case (community about 0.24, unsuspected facility about 0.73, after isolation about 0.03 at T_iso 13) and ends the profile at the end of 20 October. The day-of-illness reading, f(d) over [d, d+1), gives about 0.14, 0.79 and 0.07; it is shown as row U1, outside the registered list of 12.",
    "Spec issue S2: isolation-period infections of health workers and caregivers are drawn among those not already infected in the facility window, so no person is counted twice.",
    "Spec issue S3: N4 counts second-generation infections caused before the first case's isolation (T_iso), as the plan's model section defines it, rather than to the end of 20 October.",
    "Spec issue S4: N6 and N7 count confirmed first-generation cases only. The plan gives no timing, listing or detection rules for second-generation infections, so the N4 infections are reported separately and are not in N6, which is low in that respect. A report the model places on or before 20 October is counted from the 21 October row onward; the expected number of such reports is given beside N6. No conditioning on the absence of a further report by 20 October is applied.",
    "Spec issue S5: drawn once per draw are T_iso, w_fatal, v, the precaution scenario and its multiplier (shared by health workers and other patients and visitors), the VHF multiplier (shared by health workers and caregivers), R2, the unlisted share and the detection probability; infection time, incubation, listing, detection and lag are drawn per infected person.",
    "Spec issue S6: no contact was under follow-up before listing began on 20 October, so every first-generation case with onset before T_iso adds second-generation infections under N4.",
    "Spec issue S7: integer ranges are inclusive; the incubation shape (3) is an integer so the Erlang sampler is exact; row S4 is min(2 x U(0.04, 0.20), 0.4); row S10 keeps shape 3 with rate 0.3; N5 tail masses before 8 October and after 17 November are reported separately; 80% bands are the 10th and 90th percentiles of counts across draws; sensitivity rows use the registered seed and 40,000 draws.",
    "Machinery tests were run at small sample sizes before the first full run; they do not produce the registered numbers.",
    "N6 is compared with what Nigeria reports at the registered forecast's coverage review. The comparison is described as a comparison and is never called a hit or a miss; one outcome cannot separate nearby probabilities.",
    "Every exposure count is judgement. The model's mean number of people exposed (about 55 across the groups) is close to the 44 contacts listed on 20 October; the evidence synthesis expects the list to grow, so the exposure counts may be low.",
    "The 80% bands cover parameter uncertainty inside this model only, not model-structure error. Nothing here identifies a person or a facility.",
]


def results_digest(results: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(results, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# Filled after the first full run, from the precommitment page's Entry 1 (see DISCLOSURES). The
# first run's file and payload hashes differ from this artifact's, because this block and the N7
# cross-check were added afterwards; results_sha256 covers the numbers and is unchanged.
ENTRY_1: dict[str, str] | None = {
    "run_completed_utc": "2026-10-20T23:31:21Z",
    "entry_written_utc": "2026-10-20T23:31:39Z",
    "file_sha256": "9acc8a6098024ce91782479f74655ebb2043b524bb58f4f9cdc2d674bafeac70",
    "payload_sha256": "921ecfda9d315b08413a32520d83efaab1c9936e88864f48e218793fc91b6f01",
    "results_sha256": "675ae61d9b038a298e88679d1daab6cd585c8c65bbe9eebe40546599bb1ec322",
    "module_sha256": "a963a3059378c5826ef2cf7560a68a02cadd3538bc776e4231edb5751d205ace",
    "statement": "Written before any reviewer had seen the numbers; the run printed only hashes.",
}
# Filled after Entry 1: the registered forecast's headline, read for the N7 cross-check.
FORECAST_HEADLINE: dict[str, Any] | None = {
    "artifact": "data/case-conditioned/nigeria-2026-10-20-forecast.json",
    "forecast_id": "nigeria-bdbv-2026-10-20-further-case",
    "model_version": "nigeria_conditioned-v1.0.0",
    "payload_sha256": "9bbed174c5018e6d70ad219afc6c70b0e297b06a60975458b0fa850b1bffdeb8",
    "read_utc": "2026-10-20T23:32:12Z",
    "probability": 0.3262,
    "interval_80": [0.2084, 0.4579],
    "unconditioned": 0.3737,
    "definitions_differ": [
        "The forecast's headline is conditioned on no further case reported by the end of 20 October; this nowcast "
        "applies no conditioning, so the forecast's unconditioned value is the closer like-for-like figure.",
        "The forecast counts confirmations dated 21 October to 17 November and reported by 20 November; this nowcast "
        "counts reports by the end of 17 November, including any it places on or before 20 October.",
        "This nowcast counts first-generation cases only; the forecast's chain also covers later generations and a "
        "small background term for a case from a later importation.",
    ],
}


def build_artifact(main_draws: Sequence[Mapping[str, Any]], sensitivity: Sequence[Mapping[str, Any]],
                   unregistered: Sequence[Mapping[str, Any]]) -> dict:
    shape, rate = incubation_prior()
    results = kc._rounded({**summarise(main_draws), "sensitivity": list(sensitivity),
                           "sensitivity_unregistered": list(unregistered)})
    payload = {
        "schema": "lovs-case-conditioned-nowcast/v1",
        "nowcast_id": "nigeria-bdbv-2026-10-20-nowcast",
        "model_version": MODEL_VERSION,
        "as_of": f"end of {AS_OF.isoformat()} WAT (UTC+1)",
        "description": ("Infections the first case in Nigeria has probably already caused, by exposure group, and the "
                        "confirmed reports they would produce by date. Descriptive; not a calibration row."),
        "wording": WORDING,
        "quantities": {
            "N1": "expected number and distribution of people infected by the first case, by group: H household and community (days 0 to 4), W health workers at the facility (day 4 to isolation, plus isolation), C the case's own bedside caregivers at the facility, O other patients and visitors",
            "N2": "probability of at least one infection, overall and by group; any_health_worker is group W",
            "N3": "expected number of those infected whose symptoms began by the end of 20 October, before follow-up could detect them",
            "N4": "expected number of second-generation infections caused before the first case's isolation",
            "N5": "expected first-generation onsets by WAT calendar date, 8 October to 17 November",
            "N6": "expected cumulative further confirmed cases reported by the end of each date, 21 October to 17 November, with an 80% band; the checkable quantity",
            "N7": "probability of at least one further confirmed case reported by 17 November under this method, beside the registered forecast's headline",
        },
        "results": results,
        "results_sha256": results_digest(results),
        "cross_check": {"this_nowcast_N7": results["N7_cross_check"],
                        "registered_forecast_headline": FORECAST_HEADLINE,
                        "note": ("Shown side by side. The two methods are not averaged, and neither replaces the other. "
                                 "The forecast's value is filled only after this nowcast's precommitment entry.")},
        "limitations": [
            "Second-generation infections after 20 October are not projected; N6 counts first-generation cases only.",
            "The infectiousness profile and every attack rate are EBOV or SUDV values carried across to Bundibugyo virus by assumption.",
            "Exposure counts are judgement; the true facility denominator is unknown.",
        ],
        "inputs": {
            "provenance": PROVENANCE,
            "clock": {"t0": "2026-10-07T00:00+01:00", "community_end": T_COMMUNITY_END, "t_iso_uniform": T_ISO,
                      "as_of_model_time": T_AS_OF, "report_delay": REPORT_DELAY},
            "profile": {"f_survivor": F_SURVIVOR, "f_fatal": F_FATAL, "reading": READING, "w_fatal_uniform": W_FATAL},
            "v_survivor_viraemia_uniform": V_SURVIVOR,
            "groups": GROUPS, "scenarios": SCENARIOS, "m_vhf_uniform": M_VHF, "ar_w_cap": AR_W_CAP,
            "incubation_gamma_shape_rate": [shape, rate],
            "r2_lognormal_median_logsd": R2,
            "unlisted_share_uniform": UNLISTED, "p_detect_uniform": P_DETECT,
            "lags_erlang_shape": LAG_SHAPE,
            "lag_means": {"listed_onset_before_t_iso_from_t_iso": LAG_LISTED_PRE_MEAN,
                          "listed_onset_after_t_iso_from_onset": LAG_LISTED_POST_MEAN,
                          "unlisted_from_max_onset_t_iso": LAG_UNLISTED_MEAN},
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": DISCLOSURES,
        "registration": {"plan_sha256": PLAN_SHA256, "plan_registered_utc": PLAN_REGISTERED_UTC,
                         "evidence_sha256": EVIDENCE_SHA256, "precommitment_page": PRECOMMITMENT_PAGE,
                         "entry_1_first_run": ENTRY_1},
        "method": {"seed": SEED, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS,
                   "code": "lovs/forecast/nigeria_nowcast.py",
                   "reuses": ["lovs/forecast/kenya_conditioned.py", "lovs/lovs_priors_bundibugyo.py"]},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = kc.payload_digest(payload)
    return payload


def build(n: int = N_DRAWS) -> dict:
    return build_artifact(run(n=n), sensitivity_rows(n=n), sensitivity_rows(UNREGISTERED_SENSITIVITY, n=n))


def render(artifact: Mapping[str, Any]) -> str:
    return json.dumps(artifact, indent=1, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    artifact = build()
    text = render(artifact)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"out": args.out.name, "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      "payload_sha256": artifact["payload_sha256"], "results_sha256": artifact["results_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
