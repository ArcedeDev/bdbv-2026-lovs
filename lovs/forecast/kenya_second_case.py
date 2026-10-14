# SPDX-License-Identifier: Apache-2.0
"""Case-conditioned forecast of further linked cases after Kenya's second confirmed BDBV case.

Question: will Kenya report at least one further laboratory-confirmed case, beyond the two
confirmed by 2026-10-14, that the reporting authority links epidemiologically to them,
confirmed 2026-10-15 to 2026-11-10 and reported by 2026-11-13?

This is a separate, additive forecast. Block 11 (``lovs/forecast/pins_block11.py``) and the
9 October case-conditioned forecast (``lovs/forecast/kenya_conditioned.py``) stay frozen and
are scored as registered; this module imports the 9 October model and changes none of it.

Model, in one paragraph. The first case is drawn from the same priors as the 9 October model
(shared setting parameters reweighted by the 13 reference importations, the care-setting
scenarios and the course of illness), and two things are added. First, an observation, under
the reading that the second case was infected by the first in Kenya: the first case produced
exactly one secondary case so far, a listed contact infected outside care or at the funeral and
reported on 14 October, and no other by the conditioning time. Under the gamma-Poisson
(negative binomial) offspring model that observation has likelihood

    L = (1 - u) * density_listed(t2) * m * (b / (b + F_c)) ** (k + 1),   b = k / m,

and the first case's remaining secondary cases follow the posterior Gamma(k + 1, b + F_c),
so the chance that none is reported by time t is ((b + F_c) / (b + F_t)) ** (k + 1). Under
the other reading, infection elsewhere, the first case has no secondary case in Kenya so far
and is conditioned only on none reported. The two readings are weighted scenarios.
Second, a new index: the second case, whose expected onward cases come from the same
setting model (a short pre-isolation exposure under quarantine, care under Ebola-grade
precautions from admission, a probability of death and a safe burial) with its own timing
from its 13 October onset. The two sources are independent given the parameters, which
gives the daily curve P(YES | no further linked case reported by the end of date D).

Stdlib only and deterministic for a fixed seed, as the 9 October module is. The priors were
fixed in a plan before the first run; the artifact's ``disclosures`` list any later change.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import kenya_conditioned as kc

REPO = kc.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-14-forecast.json"

MODEL_VERSION = "kenya_second_case-v1.1.0"
SEED = 20261014
N_DRAWS = 40_000
N_DRAWS_SENSITIVITY = 20_000
N_TIMING = 60_000

# Clock: days from 2026-10-03 00:00 East Africa Time, the 9 October clock.
ONSET = (10.0, 11.0)          # second case: onset on 13 October, time of day not stated
ISOLATION_DELAY = (0.05, 0.5)  # onset to isolation: under daily monitoring, isolated at once
T_OBSERVED = 11.6             # public report of the second case, afternoon of 14 October
OBS_BIN = 0.5                 # half-width of the density bin around the observed report time
CONDITIONING_DATE = dt.date(2026, 10, 14)
EVIDENCE_THROUGH_UTC = "2026-10-14T19:50:00Z"
WINDOW_FIRST = dt.date(2026, 10, 15)
WINDOW_LAST = dt.date(2026, 11, 10)
REPORT_BY = dt.date(2026, 11, 13)
T_COND = kc.day_end(CONDITIONING_DATE)
T_CONFIRM_END = kc.day_end(WINDOW_LAST)
T_REPORT_END = T_CONFIRM_END + kc.REPORT_DELAY
CURVE_DATES = [CONDITIONING_DATE + dt.timedelta(days=i)
               for i in range((WINDOW_LAST - CONDITIONING_DATE).days + 1)]

# Second case: priors fixed before the first run; the quarantine weights were revised in the evidence
# pass of 14 October (disclosed).
QUARANTINE = {
    "Q1": {"weight": 0.75, "e_comm": (0.002, 0.02),
           "label": "Fewer people nearby before isolation (setting not stated)"},
    "Q2": {"weight": 0.25, "e_comm": (0.01, 0.06),
           "label": "More people nearby before isolation (setting not stated)"},
}
CASE2 = {
    "e_hosp": 1.0, "f_pre": (0.0, 0.05), "pre_level": "STANDARD", "post_level": "VHF",
    "p_death": (0.25, 0.50), "e_fun": 1.0, "funeral_level": "SDB",
    "course_after_isolation": (6.0, 12.0), "funeral_after_end": (0.0, 0.6),
    "unlisted_share": (0.0, 0.10),
}
# Where the second case was infected is not stated. Under "kenya" it is a secondary case of the
# first in Kenya, and so evidence about the first case's transmission; under "elsewhere" it is not.
ACQUIRED_IN_KENYA_WEIGHT = 0.90
# Settings in which a family member already listed as a contact could have been infected in Kenya.
OBS_SETTINGS = ("comm", "fun")
# Passengers who stayed in Kenya and are confirmed there count here wherever infected.
FLIGHT_ATTRIBUTION = 1.0
# The 9 October care-setting scenarios of the first case, unchanged, labelled by exposure.
CASE1_SCENARIO_LABELS = {
    "S1": "Lowest care-setting exposure",
    "S2": "Intermediate care-setting exposure",
    "S3": "Highest care-setting exposure (upper scenario)",
}

CITATIONS = {
    **{key: kc.CITATIONS[key] for key in ("faye2015", "althaus2015", "dean2016", "macneil2010", "who_ert2014",
                                          "lovs_priors", "who_don619", "who_afro_2026_10_06")},
    "citizen_2026_10_07": ("Citizen Digital, 7 Oct 2026: as of Wednesday morning, 10 people had been admitted to "
                           "quarantine facilities"),
    "lovs_priors_zaire": "LOVS lovs/lovs_priors_bundibugyo.py ZAIRE_PRIORS_STAGE_ONE: incubation gamma(4, 0.4), mean 10 days",
    "kenya_moh_2026_10_14": ("Kenya Ministry of Health statement of 14 Oct 2026, relayed by the Associated Press, "
                             "People Daily and The Standard: a second confirmed case, a listed contact of the first, "
                             "already in quarantine under daily monitoring; onset 13 Oct; isolated at once; "
                             "positive at two national laboratories; the two cases are linked; no evidence of "
                             "wider community spread; 128 contacts of the first case identified, 113 traced, 108 "
                             "in quarantine in Kenya, 5 abroad and notified"),
    "lovs_cfr_2026": "LOVS reconciled counts at INSP SitRep 151 (data 12 Oct 2026): 4,410 confirmed deaths among 9,144 confirmed cases in the DRC",
}
CASE_EVIDENCE = [
    {"fact": "Kenya confirmed a second case, a listed contact of the first case, already in quarantine under daily monitoring",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "Onset 13 Oct; isolated at once and transferred to a designated treatment centre",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "Positive at two national laboratories",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "The ministry states the two cases are linked and reports no evidence of wider community spread. It does not state where or how the second case was infected",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "Contacts of the first case: 128 identified, 113 traced, 108 in quarantine in Kenya with daily checks for 21 days, 5 abroad and notified to their authorities, 15 still being traced. Contacts of the second case are being listed; no count yet",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "By the morning of 7 October, 10 people had been admitted to quarantine facilities. The setting of the second case's quarantine is not stated",
     "source": "citizen_2026_10_07; Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "No account of the first case's journey from the DRC to Nairobi mentions a travelling companion; companions are not published either way",
     "source": "who_afro_2026_10_06, who_don619 and press accounts of the journey, checked to the evidence time"},
    {"fact": "No other linked case had been reported by the evidence time, and neither WHO nor ECDC had published on the second case",
     "source": "Freshness check of WHO, WHO AFRO, ECDC, the Kenya Ministry of Health website and press to the evidence time"},
]
DISCLOSURES = [
    "The priors, scenarios, window and sensitivity list were fixed in a plan before the first run: sha256 9e75e7f14796a6cc1f9a79343589071a3dbc69c81fd987b4c8b485a1cf535cd3, recorded at 2026-10-14T18:54:20Z.",
    "Changes after the first run, made on independent review before any publication: (1) where the second case was infected, a single assumption in the plan, became two weighted readings, infected by the first case in Kenya (0.75) or before arrival (0.25), and the plan's no-update row became the acquired_elsewhere row; (2) the observation counts only exposure outside care settings and at the funeral, the settings in which a family member could have been infected in Kenya, instead of every setting (the old form is the obs_any_setting row); (3) the second case's community exposure scales with the time before isolation, so the isolation_slow row now tests exposure as well as timing; (4) flight passengers who stayed in Kenya count fully, since this question does not depend on where infection happened; (5) the plan's secondary quantity, any further case in Kenya linked or not, is not published, because it is not the question and is not scored; (6) the posterior shares of the first case's care-setting scenarios are not published, and those scenarios are labelled by exposure; (7) the benchmark is labelled a reference rate.",
    "Changes after an evidence pass on 14 October, before any publication: (8) the weight on infection by the first case in Kenya rose from 0.75 to 0.90 (see the place_of_infection provenance); (9) the quarantine weights moved from 0.5 and 0.5 to 0.75 and 0.25 toward fewer people nearby, after a report that contacts were admitted to quarantine facilities; (10) three incubation rows were added and the incubation period is named beside the headline. Together (8) and (9) moved the headline from 0.255 to 0.267. Variants examined in the pass and not adopted, each of which moved the headline by less than one percentage point at the earlier weights: other settings for the observed infection (the widest is the obs_any_setting row), other timings of the first case's exposures, and other weights on its care-setting scenarios (neither is a run option).",
    "No source states where the second case was infected. Both readings are stated beside the headline, and the 0.90 weight on infection in Kenya is judgement.",
    "The incubation period is the largest structural driver. The primary is the programme's Bundibugyo prior, anchored on the 2007 Uganda outbreak (Erlang shape 4, rate 0.6, mean about 7 days). Estimates for Zaire ebolavirus run longer (West Africa, mean 11.4 days; the programme's Zaire prior, mean 10 days). What matters here is the share of incubation periods longer than the 8 to 11 days that had passed since the first case's exposures in Kenya: a longer mean leaves more of his contacts still incubating on 14 October and raises the forecast most (rows incubation_mean_8 and incubation_mean_10); a heavier tail at the same mean raises it slightly (row incubation_shape_2). The 80 percent interval holds the incubation period fixed. The second case's onset, 7 to 10 days after the first case's exposures in Kenya, fits every one of these.",
    "The conditional step, a further case once a first local case is known, cannot be validated against the reference class: the episode features describe imported index cases only. No validation gain is claimed for this step.",
    "The setting reproduction numbers are Zaire ebolavirus values from Conakry in 2014, inherited from the 9 October model with its misfit factor and transmissibility scale.",
    "Care exposures grow toward the end of the course at the 9 October growth rates for survivors as well as deaths; for a survivor this places too much weight late in the course and slightly delays the curve.",
    "In the scenarios that allow some care before Ebola-grade precautions, the model times that care, like the rest of the stay, toward its end, although it would come first; this slightly overstates the first case's remaining part.",
    "The second case's onward cases are given independent timings within a draw, although they share one onset and one course; this slightly overstates the chance of no report in the middle of the window.",
    "Two pathways that count are not modelled: secondary cases of the first case among the five contacts abroad are not removed from the residual although they would be confirmed abroad (at most about 4 percent of it), and people exposed with the first case before arrival who are later confirmed in Kenya are not added. The second is small at this stage and would raise the forecast.",
    "Third-generation cases do not change the answer: any of them requires a further case first, which already resolves the question.",
    "The 80 percent interval covers parameter and scenario uncertainty inside this model, not model-structure error.",
    "The author and Arcede hold no position in any prediction market on this outbreak and will take none before this question resolves.",
]
PROVENANCE = {
    "clock": "case evidence: kenya_moh_2026_10_14 (onset 13 Oct, isolated at once, reported 14 Oct); the time of day is judgement",
    "place_of_infection": "judgement on case evidence: family members were among the contacts listed in Kenya in the first days, and no account of the journey mentions a travelling companion, though companions are not published either way; 0.90 on infection by the first case in Kenya. The timing of the second case's onset cannot separate the readings, since the journey ended on the day of arrival. Infection before arrival is not ruled out by any statement",
    "observation_settings": "judgement: a family member already listed as a contact is taken to have been infected outside care settings or at the funeral, not in care or in flight",
    "quarantine_scenarios": "judgement on case evidence: citizen_2026_10_07 reports 10 people admitted to quarantine facilities by the morning of 7 October, so fewer people nearby is weighted 0.75; the second case's setting is not stated; Faye's community value covers a whole course, and the pre-isolation phase here is hours of early illness",
    "incubation": "source: lovs_priors (Bundibugyo, Erlang shape 4, rate 0.6, anchored on macneil2010) for the primary; the sensitivity rows use a mean of 8 days, lovs_priors_zaire (mean 10 days, against who_ert2014's 11.4) and the primary's mean with a heavier tail (Erlang shape 2, rate 0.3)",
    "case2": "judgement anchored on macneil2010 and lovs_cfr_2026 for the probability of death; the precaution levels and the course length are judgement; the funeral level follows the 9 October safe-burial level",
    "inherited": "source: lovs/forecast/kenya_conditioned.py (the 9 October model, imported unchanged)",
    "citations": "the published sources themselves",
    "provenance": "this map",
}


# ---------------------------------------------------------------------------------------
# Timing: report times of each case's onward cases.
# ---------------------------------------------------------------------------------------
def _report_tables(rng: random.Random, exposures: Mapping[tuple, Any], incubation: tuple[int, float],
                   n: int) -> dict:
    """Sorted report times by (setting, growth, listing): exposure, incubation, onset to confirmation, report."""
    out: dict[tuple, list[float]] = {}
    for key, exposure in exposures.items():
        for listed, mean in (("listed", kc.LAG_LISTED_MEAN), ("unlisted", kc.LAG_UNLISTED_MEAN)):
            samples = [exposure(rng) + kc._erlang(rng, *incubation) + kc._erlang(rng, 2, 2.0 / mean)
                       + kc.REPORT_DELAY for _ in range(n)]
            samples.sort()
            out[key + (listed,)] = samples
    return out


def _case1_exposures() -> dict:
    """The first case's exposure times by setting, as defined by the 9 October model."""
    exposures = {("hosp", g): (lambda r, g=g: kc._hospital_exposure(r, g)) for g in kc.GROWTH_RATES}
    exposures[("comm", None)] = lambda r: kc._uniform(r, kc.T_COMM)
    exposures[("fun", None)] = lambda r: kc._uniform(r, kc.T_BURIAL)
    exposures[("flight", None)] = lambda r: kc.T_FLIGHT
    return exposures


@functools.lru_cache(maxsize=4)
def case1_timing_tables(seed: int, incubation: tuple[int, float] = kc.INCUBATION) -> dict:
    """The 9 October tables, rebuilt from its exposure definitions only for an alternative incubation."""
    if incubation == kc.INCUBATION:
        return kc.timing_tables(seed)
    return _report_tables(random.Random(seed + 1), _case1_exposures(), incubation, kc.N_TIMING)


@functools.lru_cache(maxsize=8)
def case2_timing_tables(seed: int, iso_delay: tuple[float, float] = ISOLATION_DELAY,
                        incubation: tuple[int, float] = kc.INCUBATION, n: int = N_TIMING) -> dict:
    """Sorted report times of the second case's onward cases, by setting, growth and listing."""
    rng = random.Random(seed + 3)

    def course(r: random.Random) -> tuple[float, float]:
        onset = kc._uniform(r, ONSET)
        return onset, onset + kc._uniform(r, iso_delay)

    def comm(r: random.Random) -> float:
        onset, iso = course(r)
        return kc._uniform(r, (onset, iso))

    def hosp(r: random.Random, g: float) -> float:
        _, iso = course(r)
        span = kc._uniform(r, CASE2["course_after_isolation"])
        u = r.random()
        return iso + span + math.log(u + (1.0 - u) * math.exp(-g * span)) / g

    def fun(r: random.Random) -> float:
        _, iso = course(r)
        return iso + kc._uniform(r, CASE2["course_after_isolation"]) + kc._uniform(r, CASE2["funeral_after_end"])

    exposures = {("hosp", g): (lambda r, g=g: hosp(r, g)) for g in kc.GROWTH_RATES}
    exposures[("comm", None)] = comm
    exposures[("fun", None)] = fun
    return _report_tables(rng, exposures, incubation, n)


def _bin_density(samples: Sequence[float], t: float, half: float = OBS_BIN) -> float:
    return (kc._cdf(samples, t + half) - kc._cdf(samples, t - half)) / (2.0 * half)


def _mixture(parts: Mapping[str, float], values: Mapping[str, float]) -> float:
    m = sum(parts.values())
    return sum(part / m * values[s] for s, part in parts.items()) if m > 0 else 0.0


def obs_likelihood(m: float, k: float, f_c: float, density: float, unlisted: float) -> float:
    """Likelihood of exactly one secondary case, listed and reported at the observed time, and no other by f_c.

    ``density`` is the report-time density of one offspring, already restricted to the settings the
    observation allows; ``f_c`` is the reported fraction of all offspring by the conditioning time.
    """
    if m <= 0.0:
        return 0.0
    b = k / m
    return (1.0 - unlisted) * density * m * (b / (b + f_c)) ** (k + 1)


def residual_none(m: float, k: float, f_c: float, f_t: float) -> float:
    """P(no further report by f_t) after exactly one by f_c: the posterior Gamma(k + 1, b + f_c)."""
    b = k / m
    return ((b + f_c) / (b + f_t)) ** (k + 1)


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def run(opts: Mapping[str, Any] | None = None, n: int = N_DRAWS, seed: int = SEED) -> dict:
    opts = dict(opts or {})
    episodes = kc.load_features()
    calibrate = opts.get("calibrate", True)
    kenya_weight = opts.get("acquired_in_kenya_weight", ACQUIRED_IN_KENYA_WEIGHT)
    obs_settings = opts.get("obs_settings", OBS_SETTINGS)
    iso_delay = tuple(opts.get("iso_delay", ISOLATION_DELAY))
    incubation = tuple(opts.get("incubation", kc.INCUBATION))
    # Time before isolation scales the second case's community exposure.
    exposure_scale = sum(iso_delay) / sum(ISOLATION_DELAY)
    q_weights = opts.get("quarantine_weights", {q: v["weight"] for q, v in QUARANTINE.items()})
    p_death_range = opts.get("p_death", CASE2["p_death"])
    unlisted2 = opts.get("unlisted_share2", CASE2["unlisted_share"])
    post_level2 = opts.get("post_level2", CASE2["post_level"])

    points = [T_COND] + [kc.day_end(d) for d in CURVE_DATES[1:]] + [T_REPORT_END]
    t1 = case1_timing_tables(seed, incubation)
    c1 = kc.cdf_points(t1, points)
    dens1 = {key: _bin_density(s, T_OBSERVED) for key, s in t1.items() if key[-1] == "listed"}
    t2 = case2_timing_tables(seed, iso_delay, incubation)
    c2 = kc.cdf_points(t2, points)

    rng = random.Random(seed)
    s_names = list(kc.SCENARIOS)
    s_w = [kc.SCENARIOS[s]["weight"] for s in s_names]
    c_names = list(kc.COURSES)
    c_w = [kc.COURSES[c]["weight"] for c in c_names]
    q_names = list(q_weights)
    q_w = [q_weights[q] for q in q_names]
    draws = []
    for _ in range(n):
        p = kc.draw_shared(rng, opts)
        loglik = 0.0
        for ep in episodes:
            prob = min(max(kc.episode_probability(rng, p, ep), 1e-12), 1.0 - 1e-12)
            loglik += math.log(prob if ep["outcome"] else 1.0 - prob)
        # First case, from the 9 October priors.
        scen = rng.choices(s_names, s_w)[0]
        course = rng.choices(c_names, c_w)[0]
        f_pre = kc._uniform(rng, kc.SCENARIOS[scen]["f_pre"])
        parts1 = kc.expected_cases(p, kc._uniform(rng, kc.KENYA["e_comm"]),
                                   kc._uniform(rng, kc.COURSES[course]["e_hosp"]), f_pre,
                                   kc.KENYA["pre_level"], kc.KENYA["post_level"], kc.KENYA["p_death"],
                                   kc._uniform(rng, kc.KENYA["e_fun"]), kc.KENYA["funeral_level"])
        parts1["flight"] = (kc.KENYA["flight_contacts"] * kc._uniform(rng, kc.KENYA["flight_attack_rate"])
                            * FLIGHT_ATTRIBUTION)
        g = rng.choice(kc.GROWTH_RATES)
        u1 = kc._uniform(rng, kc.KENYA["unlisted_share"])

        def key1(s: str) -> tuple:
            return (s, g if s == "hosp" else None)

        f1 = [_mixture(parts1, {s: (1 - u1) * c1[key1(s) + ("listed",)][i] + u1 * c1[key1(s) + ("unlisted",)][i]
                                for s in parts1}) for i in range(len(points))]
        d1 = _mixture(parts1, {s: dens1[key1(s) + ("listed",)] if s in obs_settings else 0.0 for s in parts1})

        # Second case.
        q = rng.choices(q_names, q_w)[0]
        parts2 = kc.expected_cases(p, exposure_scale * kc._uniform(rng, QUARANTINE[q]["e_comm"]), CASE2["e_hosp"],
                                   kc._uniform(rng, CASE2["f_pre"]), CASE2["pre_level"], post_level2,
                                   kc._uniform(rng, p_death_range), CASE2["e_fun"], CASE2["funeral_level"])
        u2 = kc._uniform(rng, unlisted2)
        f2 = [_mixture(parts2, {s: (1 - u2) * c2[key1(s) + ("listed",)][i] + u2 * c2[key1(s) + ("unlisted",)][i]
                                for s in parts2}) for i in range(len(points))]
        draws.append({"loglik": loglik, "scenario": scen, "course": course, "quarantine": q,
                      "m1": sum(parts1.values()), "m2": sum(parts2.values()), "k": p["k"],
                      "f1": f1, "d1": d1, "u1": u1, "f2": f2})

    # Every draw is evaluated under both readings of where the second case was infected, and each
    # reading's weights are scaled to its scenario weight.
    max_ll = max(d["loglik"] for d in draws)
    weighted = []
    for reading, share in (("kenya", kenya_weight), ("elsewhere", 1.0 - kenya_weight)):
        if share <= 0.0:
            continue
        group = []
        for d in draws:
            w_ref = math.exp(d["loglik"] - max_ll) if calibrate else 1.0
            if reading == "kenya":
                obs = obs_likelihood(d["m1"], d["k"], d["f1"][0], d["d1"], d["u1"])
            else:
                # Not evidence about the first case's transmission in Kenya: condition only on
                # no report of a secondary case of the first case by the conditioning time.
                obs = kc.nb_none(d["m1"], d["k"], d["f1"][0])
            group.append({**d, "reading": reading, "w": w_ref * obs})
        total = sum(g["w"] for g in group)
        for g in group:
            g["w"] *= share / total
        weighted += group
    return summarise(weighted, points, full=opts.get("full", True))


def _none_by(d: Mapping[str, Any], i: int, which: str) -> float:
    """P(no further linked case reported by points[i]), given the observation through T_COND."""
    one = two = 1.0
    if which in ("both", "case1") and d["m1"] > 0:
        m, k = d["m1"], d["k"]
        if d["reading"] == "kenya":
            one = residual_none(m, k, d["f1"][0], d["f1"][i])
        else:
            one = kc.nb_none(m, k, d["f1"][i]) / kc.nb_none(m, k, d["f1"][0])
    if which in ("both", "case2"):
        two = kc.nb_none(d["m2"], d["k"], d["f2"][i])
    return one * two


def _conditional(draws: Sequence[Mapping[str, Any]], i: int, which: str = "both") -> tuple[float, list[float], list[float]]:
    last = len(draws[0]["f1"]) - 1
    num = den = 0.0
    vals, wts = [], []
    for d in draws:
        a_d = _none_by(d, i, which)
        a_e = _none_by(d, last, which)
        w = d["w"] * a_d
        num += d["w"] * a_e
        den += w
        vals.append(1.0 - a_e / a_d if a_d > 0 else 1.0)
        wts.append(w)
    return 1.0 - num / den, vals, wts


def further_local_benchmark() -> dict:
    """Among reference importations with a first local case, the share that reached at least two."""
    reference = json.loads(kc.REFERENCE.read_text(encoding="utf-8"))["episodes"]
    with_local = [e for e in reference if (e.get("local_confirmed_cases") or 0) >= 1]
    two_plus = [e for e in with_local if e["local_confirmed_cases"] >= 2]
    n, k = len(with_local), len(two_plus)
    firm = [e for e in with_local if e["episode_id"] not in kc.DISPUTED_YES]
    firm_k = sum(e["local_confirmed_cases"] >= 2 for e in firm)
    return {"reference_rate": (k + 0.5) / (n + 1), "is_forecast": False,
            "episodes_with_a_local_case": n, "episodes_with_two_or_more": k,
            "reference_rate_without_disputed": (firm_k + 0.5) / (len(firm) + 1),
            "note": (f"Share of {n} past importations with a local case that went on to two or more ({k} of {n}, "
                     f"Jeffreys-smoothed). {n - len(firm)} of the {n} are the episodes whose local transmission "
                     "the 9 October model marks contestable; without them the rate is "
                     f"{(firm_k + 0.5) / (len(firm) + 1):.2f}. Counts are cumulative, so an episode qualifies even "
                     "when its local cases were confirmed together, and the reference class does not record whether "
                     "a first local case was already in quarantine. It applies only if the second case was infected "
                     "in Kenya. A reference rate, not a forecast for Kenya.")}


def summarise(draws: Sequence[Mapping[str, Any]], points: Sequence[float], full: bool = True) -> dict:
    wsum = sum(d["w"] for d in draws)
    ess = wsum ** 2 / sum(d["w"] ** 2 for d in draws)
    curve = []
    for i, date in enumerate(CURVE_DATES):
        p, vals, wts = _conditional(draws, i)
        row = {"date": date.isoformat(), "p": p}
        if full:
            row.update({"p10": kc._weighted_quantile(vals, wts, 0.10),
                        "p90": kc._weighted_quantile(vals, wts, 0.90),
                        "case1_only": _conditional(draws, i, "case1")[0],
                        "case2_only": _conditional(draws, i, "case2")[0]})
        curve.append(row)
    out = {"headline": curve[0], "curve": curve, "effective_sample_size": ess, "n_draws": len(draws)}
    if not full:
        return out

    weights0 = [d["w"] for d in draws]

    def posterior_share(key: str, value: str) -> float:
        return sum(w for w, d in zip(weights0, draws) if d[key] == value) / wsum

    case1_rows = [{"scenario": s, "prior_weight": kc.SCENARIOS[s]["weight"], "label": CASE1_SCENARIO_LABELS[s]}
                  for s in kc.SCENARIOS]
    course_rows = [{"course": c, "prior_weight": kc.COURSES[c]["weight"],
                    "share_given_observation": posterior_share("course", c), "label": kc.COURSES[c]["label"]}
                   for c in kc.COURSES]
    q_rows = []
    for q in QUARANTINE:
        cell = [d for d in draws if d["quarantine"] == q]
        q_rows.append({"quarantine": q, "prior_weight": QUARANTINE[q]["weight"],
                       "p_conditioning_date": _conditional(cell, 0)[0],
                       "median_m2": kc._weighted_quantile([d["m2"] for d in cell], [d["w"] for d in cell], 0.5),
                       "label": QUARANTINE[q]["label"]})

    def residual_mean(d: Mapping[str, Any]) -> float:
        if d["m1"] <= 0:
            return 0.0
        b = d["k"] / d["m1"]
        if d["reading"] == "kenya":
            return (d["k"] + 1) / (b + d["f1"][0]) * (1.0 - d["f1"][0])
        return d["m1"] * (1.0 - d["f1"][0])

    out.update({
        "case1_scenarios": case1_rows, "case1_courses": course_rows, "quarantine_scenarios": q_rows,
        "expected_cases": {
            "case1_residual_mean": sum(w * residual_mean(d) for w, d in zip(weights0, draws)) / wsum,
            "case2_offspring_mean": sum(w * d["m2"] for w, d in zip(weights0, draws)) / wsum,
            "case1_offspring_rate_given_observation": sum(w * d["m1"] for w, d in zip(weights0, draws)) / wsum,
        },
    })
    return out


# ---------------------------------------------------------------------------------------
# Sensitivity and artifact.
# ---------------------------------------------------------------------------------------
SENSITIVITY = [
    ("acquired_in_kenya", "The second case was infected by the first in Kenya (weight 1)", {"acquired_in_kenya_weight": 1.0}),
    ("acquired_elsewhere", "The second case was infected before arrival in Kenya, so it is not evidence about the first case's transmission there (weight 1)", {"acquired_in_kenya_weight": 0.0}),
    ("obs_any_setting", "The observed secondary case could have been infected in any setting, care and flight included", {"obs_settings": ("comm", "hosp", "fun", "flight")}),
    ("only_Q1", "Fewer people nearby only", {"quarantine_weights": {"Q1": 1.0}}),
    ("only_Q2", "More people nearby only", {"quarantine_weights": {"Q2": 1.0}}),
    ("k_0.18", "Dispersion fixed at Althaus 2015 k = 0.18", {"k_fixed": 0.18}),
    ("poisson", "No overdispersion (k = 1e6)", {"k_fixed": 1e6}),
    ("literature_only", "No reweighting by the reference class", {"calibrate": False}),
    ("rho_effective_R", "Transmissibility from the LOVS effective-R-under-response prior gamma(4, 3)", {"r_prior": kc.LOVS_EFFECTIVE_R}),
    ("theta_fixed", "No misfit factor (theta = 1)", {"theta_fixed": True}),
    ("death_low", "Second case: probability of death 0.15 to 0.30", {"p_death": (0.15, 0.30)}),
    ("death_high", "Second case: probability of death 0.45 to 0.65", {"p_death": (0.45, 0.65)}),
    ("incubation_mean_8", "Incubation period mean 8 days (Erlang shape 4, rate 0.5) instead of the Bundibugyo prior's mean of about 7", {"incubation": (4, 0.5)}),
    ("incubation_mean_10", "Incubation period mean 10 days (Erlang shape 4, rate 0.4), the programme's Zaire ebolavirus prior", {"incubation": (4, 0.4)}),
    ("incubation_shape_2", "Incubation period with the Bundibugyo prior's mean of about 7 days and a heavier tail (Erlang shape 2, rate 0.3)", {"incubation": (2, 0.3)}),
    ("isolation_slow", "Second case: onset to isolation 0.5 to 1.5 days, community exposure scaled with the time before isolation", {"iso_delay": (0.5, 1.5)}),
    ("unlisted_high", "Second case: unlisted share of onward transmission 0.10 to 0.30", {"unlisted_share2": (0.10, 0.30)}),
    ("care_standard", "Second case: care-setting exposure at the standard-precautions level throughout (upper bound)", {"post_level2": "STANDARD"}),
    ("baseline_at_sensitivity_sample", "The primary model at the sensitivity sample size, for a like-with-like comparison", {}),
]
SENSITIVITY_DATES = ("2026-10-14", "2026-10-18", "2026-10-22", "2026-10-29")


def _pct(p: float) -> str:
    return f"{round(100 * p)}%"


def headline_label(head: Mapping[str, Any]) -> str:
    """The short conditional statement that travels with the number."""
    return (f"If no further linked case is reported in Kenya by the end of 14 October: about {_pct(head['p'])} "
            f"(80% interval "
            f"{_pct(head['p10'])[:-1]} to {_pct(head['p90'])}) that Kenya reports a further linked case confirmed by "
            f"10 November. A forecast, not an assessment of the country's response.")


def named_driver(sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    """The two largest drivers, stated beside the headline once the sensitivity rows exist."""
    col = f"p_{CONDITIONING_DATE.isoformat()}"
    rows = {r["variant"]: r[col] for r in sensitivity}
    if not all(v in rows for v in ("incubation_mean_10", "acquired_in_kenya", "acquired_elsewhere")):
        return {}
    return {"named_driver": {
        "incubation": {
            "primary_mean_days": round(kc.INCUBATION[0] / kc.INCUBATION[1], 1), "if_mean_10_days": rows["incubation_mean_10"],
            "note": (f"The model uses a Bundibugyo incubation period anchored on the 2007 Uganda outbreak, a mean of about "
                     f"7 days. With a mean of 10 days, as in the programme's Zaire ebolavirus prior, more of the first "
                     f"case's contacts would still be incubating and the forecast is about "
                     f"{_pct(rows['incubation_mean_10'])}. The 80% interval holds the incubation period fixed. One "
                     f"case's onset cannot separate the two.")},
        "place_of_infection": {
            "weight_in_kenya": ACQUIRED_IN_KENYA_WEIGHT,
            "if_in_kenya": rows["acquired_in_kenya"], "if_elsewhere": rows["acquired_elsewhere"],
            "note": (f"No source yet states where the second case was infected. If it was infected by the first case "
                     f"in Kenya, about {_pct(rows['acquired_in_kenya'])}; if before arrival, about "
                     f"{_pct(rows['acquired_elsewhere'])}. The headline weights the two readings "
                     f"{ACQUIRED_IN_KENYA_WEIGHT:.2f} and {1 - ACQUIRED_IN_KENYA_WEIGHT:.2f}.")}}}


def payload_digest(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_artifact(main: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    head = main["headline"]
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "kenya-bdbv-2026-10-14-further-linked-case",
        "model_version": MODEL_VERSION,
        "question": ("Will Kenya report at least one further laboratory-confirmed Bundibugyo case, beyond the two "
                     "confirmed by 2026-10-14, that the reporting authority links epidemiologically to them, "
                     "confirmed 2026-10-15 to 2026-11-10 and reported by 2026-11-13? Suspected and probable "
                     "cases, retracted reports, cases confirmed outside Kenya, cases attributed to a separate "
                     "importation and cases with no stated link do not count."),
        "relationship_to_earlier_forecasts": (
            "Additive. Block 11 (block11:kenya-further-case, price 0.393) and the 9 October case-conditioned "
            "forecast (kenya-bdbv-2026-10-09-case-conditioned, scored value 0.163) ask a different question, "
            "acquisition in Kenya, and stay frozen and scored as registered. This forecast imports the 9 October "
            "model unchanged and adds the second case."),
        "evidence_through_utc": EVIDENCE_THROUGH_UTC,
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {
            "label": headline_label(head),
            "probability": head["p"], "interval_80": [head["p10"], head["p90"]],
            "from_first_case": head["case1_only"], "from_second_case": head["case2_only"],
            "interval_note": ("Weighted 10th to 90th percentile across draws: parameter and scenario uncertainty "
                              "inside this model, not model-structure error, and not a worst case."),
            "benchmarks": {"further_local_case_after_a_first": further_local_benchmark(),
                           "context_not_comparable": {
                               "block11_kenya_price": round(kc.benchmarks()["block11_pooled"], 4),
                               "kenya_2026_10_09_scored_value": 0.163,
                               "note": "Both price a different question, acquisition in Kenya; shown as context only."}}},
        **named_driver(sensitivity),
        "curve": main["curve"],
        "curve_note": ("Each row: probability of YES given that no further linked case has been reported by the end "
                       "of that date (East Africa Time). The row for 2026-10-14 is the current forecast."),
        "case1_scenarios": main["case1_scenarios"],
        "case1_courses": main["case1_courses"],
        "case1_note": ("prior_weight is the 9 October weight. For the courses, share_given_observation is the "
                       "weight after the observation, both readings of where the second case was infected combined."),
        "quarantine_scenarios": main["quarantine_scenarios"],
        "expected_cases": main["expected_cases"],
        "effective_sample_size": main["effective_sample_size"],
        "sensitivity": list(sensitivity),
        "scoring_plan": {
            "scored_value": ("the curve value for the date this forecast is first shown outside the programme, "
                             "stated with that date; the artifact hash fixes it in advance"),
            "scored_against": ["the further-local-case reference rate, stated with its caveats; it applies only "
                               "if the second case was infected in Kenya, and one outcome cannot separate the two"],
            "resolution": ("an authority report (Kenya Ministry of Health, WHO or ECDC) of a further laboratory-"
                           "confirmed case in Kenya linked to the first two, confirmed 2026-10-15 to 2026-11-10 "
                           "and reported by 2026-11-13, with a coverage review naming its sources made on or "
                           "after 2026-11-14"),
            "excluded": ["suspected and probable cases", "a retracted report", "a case confirmed outside Kenya",
                         "a case attributed to a separate importation", "a case with no stated link"],
            "ambiguity": "an ambiguous case is decided by someone other than the author of this forecast",
            "if_a_case_occurs": ("score it in public with the interval and drivers as stated, and beside the scored "
                                 "value report the curve value standing on the day the case was reported (the row for "
                                 "the last whole day before the report); say nothing about where, from whom or in "
                                 "which facility that case was infected"),
            "if_no_case_occurs": ("score it in public, and state that one outcome cannot separate this forecast from "
                                  "the benchmark; draw no conclusion about Kenya's response"),
        },
        "inputs": {
            "provenance": PROVENANCE,
            "clock": {"t0": "2026-10-03T00:00+03:00", "onset": ONSET, "isolation_delay": ISOLATION_DELAY,
                      "observed_report": T_OBSERVED, "observation_bin_half_width": OBS_BIN,
                      "conditioning": T_COND, "confirm_end": T_CONFIRM_END, "report_delay": kc.REPORT_DELAY,
                      "note": ("The observation uses the public report time of the second case, with a density bin "
                               "of plus or minus half a day, rather than its stated onset.")},
            "acquired_in_kenya_weight": ACQUIRED_IN_KENYA_WEIGHT, "observation_settings": OBS_SETTINGS,
            "incubation": kc.INCUBATION,
            "flight_attribution": FLIGHT_ATTRIBUTION,
            "quarantine_scenarios": QUARANTINE, "case2": CASE2,
            "inherited": {"module": "lovs/forecast/kenya_conditioned.py", "model_version": kc.MODEL_VERSION},
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": DISCLOSURES,
        "method": {"seed": SEED, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS_SENSITIVITY,
                   "n_timing_samples": N_TIMING, "code": "lovs/forecast/kenya_second_case.py",
                   "regeneration": ("the test suite regenerates the main run and checks it against this file; the "
                                    "sensitivity rows are carried over there and recomputed only by the module")},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


def sensitivity_rows(n: int = N_DRAWS_SENSITIVITY) -> list[dict]:
    rows = []
    for name, label, opts in SENSITIVITY:
        res = run({**opts, "full": False}, n=n)
        by_date = {c["date"]: c["p"] for c in res["curve"]}
        rows.append({"variant": name, "label": label, **{f"p_{d}": by_date[d] for d in SENSITIVITY_DATES}})
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-sensitivity", action="store_true")
    args = parser.parse_args(argv)
    main_run = run()
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
