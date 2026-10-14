# SPDX-License-Identifier: Apache-2.0
"""Case-conditioned forecast of further linked cases after Kenya's second confirmed BDBV case.

Question: will Kenya report at least one further laboratory-confirmed case, beyond the two
confirmed by 2026-10-14, that the reporting authority links epidemiologically to them,
confirmed 2026-10-15 to 2026-11-10 and reported by 2026-11-13?

This is a separate, additive forecast. Block 11 (``lovs/forecast/pins_block11.py``) and the
9 October case-conditioned forecast (``lovs/forecast/kenya_conditioned.py``) stay frozen and
are scored as registered; this module imports the 9 October model and changes none of it.

Model, in one paragraph. Every draw of the 9 October model (shared setting parameters
reweighted by the 13 reference importations, the precaution scenarios and the course of
illness of the first case) is kept, and two things are added. First, an observation: the
first case produced exactly one secondary case so far, a listed contact reported on
14 October, and no other by the conditioning time. Under the gamma-Poisson (negative
binomial) offspring model that observation has likelihood

    L = (1 - u) * density_listed(t2) * m * (b / (b + F_c)) ** (k + 1),   b = k / m,

and the first case's remaining secondary cases follow the posterior Gamma(k + 1, b + F_c),
so the chance that none is reported by time t is ((b + F_c) / (b + F_t)) ** (k + 1).
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

MODEL_VERSION = "kenya_second_case-v1.0.0"
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
EVIDENCE_THROUGH_UTC = "2026-10-14T19:00:00Z"
WINDOW_FIRST = dt.date(2026, 10, 15)
WINDOW_LAST = dt.date(2026, 11, 10)
REPORT_BY = dt.date(2026, 11, 13)
T_COND = kc.day_end(CONDITIONING_DATE)
T_CONFIRM_END = kc.day_end(WINDOW_LAST)
T_REPORT_END = T_CONFIRM_END + kc.REPORT_DELAY
CURVE_DATES = [CONDITIONING_DATE + dt.timedelta(days=i)
               for i in range((WINDOW_LAST - CONDITIONING_DATE).days + 1)]

# Second case: priors fixed before the first run.
QUARANTINE = {
    "Q1": {"weight": 0.5, "e_comm": (0.002, 0.02),
           "label": "Quarantine facility: few people near the second case before isolation"},
    "Q2": {"weight": 0.5, "e_comm": (0.01, 0.06),
           "label": "Home quarantine with household members before isolation"},
}
CASE2 = {
    "e_hosp": 1.0, "f_pre": (0.0, 0.05), "pre_level": "STANDARD", "post_level": "VHF",
    "p_death": (0.25, 0.50), "e_fun": 1.0, "funeral_level": "SDB",
    "course_after_isolation": (6.0, 12.0), "funeral_after_end": (0.0, 0.6),
    "unlisted_share": (0.0, 0.10),
}
# Any further case in Kenya, linked or not: further importations confirmed in Kenya.
IMPORTS_PER_WEEK = (0.02, 0.10)

CITATIONS = {
    **{key: kc.CITATIONS[key] for key in ("faye2015", "althaus2015", "dean2016", "macneil2010",
                                          "lovs_priors", "who_don619")},
    "kenya_moh_2026_10_14": ("Kenya Ministry of Health statement of 14 Oct 2026, relayed by the Associated Press, "
                             "People Daily and The Standard: a second confirmed case, a close family member of the "
                             "first, already in quarantine under daily monitoring; onset 13 Oct; isolated at once; "
                             "positive at two national laboratories; the two cases are linked; no evidence of "
                             "wider community spread; 128 contacts of the first case identified, 113 traced, 108 "
                             "in quarantine in Kenya, 5 abroad and notified"),
    "lovs_cfr_2026": "LOVS reconciled counts at INSP SitRep 151 (data 12 Oct 2026): 4,410 confirmed deaths among 9,144 confirmed cases in the DRC",
}
CASE_EVIDENCE = [
    {"fact": "Kenya confirmed a second case, a close family member of the first case, who was already in quarantine under daily monitoring",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "Onset of fever, fatigue and joint pains on 13 Oct; isolated at once and transferred to a designated treatment centre",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "Positive at two national laboratories",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "The ministry states the two cases are linked and reports no evidence of wider community spread. It does not state where or how the second case was infected",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "Contacts of the first case: 128 identified, 113 traced, 108 in quarantine in Kenya with daily checks for 21 days, 5 abroad and notified to their authorities, 15 still being traced. Contacts of the second case, including quarantine staff and health workers, are being listed; no count yet",
     "source": "Kenya Ministry of Health (14 Oct), relayed by the press"},
    {"fact": "The quarantine setting (home or facility) is not stated",
     "source": "absence in every relayed statement checked to the evidence time"},
    {"fact": "No other linked case had been reported by the evidence time, and neither WHO nor ECDC had published on the second case",
     "source": "Freshness check of WHO, WHO AFRO, ECDC, the Kenya Ministry of Health website and press to the evidence time"},
]
DISCLOSURES = [
    "The priors, scenarios, window and sensitivity list were fixed in a plan and timestamped before the first run (wiki reference:bdbv-kenya-second-case-forecast-precommitment-2026-10-14, entry 0).",
    "No source states where or how the second case was infected. The headline treats the second case as a secondary case of the first acquired in Kenya, which makes it evidence about the first case's transmission. The sensitivity row no_case2_update drops that evidence and bounds the effect.",
    "The conditional step, a further case once a first local case is known, cannot be validated against the reference class: the episode features describe imported index cases only. The benchmark beside the forecast mixes episodes where the first local cases were not under quarantine. No validation gain is claimed for this step.",
    "The setting reproduction numbers are Zaire ebolavirus values from Conakry in 2014, inherited from the 9 October model with its misfit factor and transmissibility scale.",
    "Care exposures grow toward the end of the course at the 9 October growth rates for survivors as well as deaths; for a survivor this places too much weight late in the course and slightly delays the curve.",
    "Secondary cases of the first case among the five contacts abroad would be confirmed abroad and would not count, yet they are not removed from the first case's residual. The effect is at most about 4 percent of that residual.",
    "Third-generation cases do not change the answer: any of them requires a further case first, which already resolves the question.",
    "The 80 percent interval covers parameter and scenario uncertainty inside this model, not model-structure error.",
]
PROVENANCE = {
    "clock": "case evidence: kenya_moh_2026_10_14 (onset 13 Oct, isolated at once, reported 14 Oct); the time of day is judgement",
    "quarantine_scenarios": "judgement: the quarantine setting is not stated; Faye's community value covers a whole course, and the pre-isolation phase here is hours of early illness",
    "case2": "judgement anchored on macneil2010 and lovs_cfr_2026 for the probability of death; the precaution levels and the course length are judgement; the funeral level follows the 9 October safe-burial level",
    "imports_per_week": "judgement: the 9 October later-importation range, used only for the secondary any-case quantity",
    "inherited": "source: lovs/forecast/kenya_conditioned.py (the 9 October model, imported unchanged)",
    "citations": "the published sources themselves",
    "provenance": "this map",
}


# ---------------------------------------------------------------------------------------
# Timing of the second case's onward cases.
# ---------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=8)
def case2_timing_tables(seed: int, iso_delay: tuple[float, float] = ISOLATION_DELAY,
                        n: int = N_TIMING) -> dict:
    """Sorted report times of the second case's onward cases, by setting, growth and listing."""
    rng = random.Random(seed + 3)
    out: dict[tuple, list[float]] = {}

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
    for key, exposure in exposures.items():
        for listed, mean in (("listed", kc.LAG_LISTED_MEAN), ("unlisted", kc.LAG_UNLISTED_MEAN)):
            samples = [exposure(rng) + kc._erlang(rng, *kc.INCUBATION) + kc._erlang(rng, 2, 2.0 / mean)
                       + kc.REPORT_DELAY for _ in range(n)]
            samples.sort()
            out[key + (listed,)] = samples
    return out


def _bin_density(samples: Sequence[float], t: float, half: float = OBS_BIN) -> float:
    return (kc._cdf(samples, t + half) - kc._cdf(samples, t - half)) / (2.0 * half)


def _mixture(parts: Mapping[str, float], values: Mapping[str, float]) -> float:
    m = sum(parts.values())
    return sum(part / m * values[s] for s, part in parts.items()) if m > 0 else 0.0


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def run(opts: Mapping[str, Any] | None = None, n: int = N_DRAWS, seed: int = SEED) -> dict:
    opts = dict(opts or {})
    episodes = kc.load_features()
    calibrate = opts.get("calibrate", True)
    use_obs = opts.get("case2_update", True)
    q_weights = opts.get("quarantine_weights", {q: v["weight"] for q, v in QUARANTINE.items()})
    p_death_range = opts.get("p_death", CASE2["p_death"])
    unlisted2 = opts.get("unlisted_share2", CASE2["unlisted_share"])
    post_level2 = opts.get("post_level2", CASE2["post_level"])

    points = [T_COND] + [kc.day_end(d) for d in CURVE_DATES[1:]] + [T_REPORT_END]
    t1 = kc.timing_tables(seed)
    c1 = kc.cdf_points(t1, points)
    dens1 = {key: _bin_density(s, T_OBSERVED) for key, s in t1.items() if key[-1] == "listed"}
    t2 = case2_timing_tables(seed, tuple(opts.get("iso_delay", ISOLATION_DELAY)))
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
        # First case, drawn exactly as the 9 October model draws it.
        scen = rng.choices(s_names, s_w)[0]
        course = rng.choices(c_names, c_w)[0]
        f_pre = kc._uniform(rng, kc.SCENARIOS[scen]["f_pre"])
        parts1 = kc.expected_cases(p, kc._uniform(rng, kc.KENYA["e_comm"]),
                                   kc._uniform(rng, kc.COURSES[course]["e_hosp"]), f_pre,
                                   kc.KENYA["pre_level"], kc.KENYA["post_level"], kc.KENYA["p_death"],
                                   kc._uniform(rng, kc.KENYA["e_fun"]), kc.KENYA["funeral_level"])
        parts1["flight"] = (kc.KENYA["flight_contacts"] * kc._uniform(rng, kc.KENYA["flight_attack_rate"])
                            * kc.KENYA["flight_kenya_attribution"])
        g = rng.choice(kc.GROWTH_RATES)
        u1 = kc._uniform(rng, kc.KENYA["unlisted_share"])

        def key1(s: str) -> tuple:
            return (s, g if s == "hosp" else None)

        f1 = [_mixture(parts1, {s: (1 - u1) * c1[key1(s) + ("listed",)][i] + u1 * c1[key1(s) + ("unlisted",)][i]
                                for s in parts1}) for i in range(len(points))]
        d1 = _mixture(parts1, {s: dens1[key1(s) + ("listed",)] for s in parts1})

        # Second case.
        q = rng.choices(q_names, q_w)[0]
        parts2 = kc.expected_cases(p, kc._uniform(rng, QUARANTINE[q]["e_comm"]), CASE2["e_hosp"],
                                   kc._uniform(rng, CASE2["f_pre"]), CASE2["pre_level"], post_level2,
                                   kc._uniform(rng, p_death_range), CASE2["e_fun"], CASE2["funeral_level"])
        u2 = kc._uniform(rng, unlisted2)
        f2 = [_mixture(parts2, {s: (1 - u2) * c2[key1(s) + ("listed",)][i] + u2 * c2[key1(s) + ("unlisted",)][i]
                                for s in parts2}) for i in range(len(points))]
        lam = kc._uniform(rng, IMPORTS_PER_WEEK) / 7.0
        draws.append({"loglik": loglik, "scenario": scen, "course": course, "quarantine": q,
                      "m1": sum(parts1.values()), "m2": sum(parts2.values()), "k": p["k"],
                      "f1": f1, "d1": d1, "u1": u1, "f2": f2, "lam": lam})

    max_ll = max(d["loglik"] for d in draws)
    for d in draws:
        w_ref = math.exp(d["loglik"] - max_ll) if calibrate else 1.0
        m, k, fc = d["m1"], d["k"], d["f1"][0]
        if m <= 0.0:
            obs = 0.0
        elif use_obs:
            b = k / m
            obs = (1 - d["u1"]) * d["d1"] * m * (b / (b + fc)) ** (k + 1)
        else:
            # The second case is not evidence about the first case's transmission in Kenya:
            # condition only on no further report of the first case by the conditioning time.
            obs = kc.nb_none(m, k, fc)
        d["w"] = w_ref * obs
    return summarise(draws, points, use_obs, full=opts.get("full", True))


def _none_by(d: Mapping[str, Any], i: int, which: str, use_obs: bool) -> float:
    """P(no further linked case reported by points[i]), given the observation through T_COND."""
    one = two = 1.0
    if which in ("both", "case1") and d["m1"] > 0:
        m, k = d["m1"], d["k"]
        if use_obs:
            b = k / m
            one = ((b + d["f1"][0]) / (b + d["f1"][i])) ** (k + 1)
        else:
            one = kc.nb_none(m, k, d["f1"][i]) / kc.nb_none(m, k, d["f1"][0])
    if which in ("both", "case2"):
        two = kc.nb_none(d["m2"], d["k"], d["f2"][i])
    return one * two


def _conditional(draws: Sequence[Mapping[str, Any]], i: int, use_obs: bool,
                 which: str = "both") -> tuple[float, list[float], list[float]]:
    last = len(draws[0]["f1"]) - 1
    num = den = 0.0
    vals, wts = [], []
    for d in draws:
        a_d = _none_by(d, i, which, use_obs)
        a_e = _none_by(d, last, which, use_obs)
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
    return {"probability": (len(two_plus) + 0.5) / (len(with_local) + 1),
            "episodes_with_a_local_case": len(with_local), "episodes_with_two_or_more": len(two_plus),
            "note": ("Jeffreys rate from the frozen reference class. Counts are cumulative local cases, "
                     "so an episode qualifies even when its local cases were confirmed together, and none "
                     "of its first local cases was already in quarantine. A benchmark, not a forecast.")}


def summarise(draws: Sequence[Mapping[str, Any]], points: Sequence[float], use_obs: bool,
              full: bool = True) -> dict:
    wsum = sum(d["w"] for d in draws)
    ess = wsum ** 2 / sum(d["w"] ** 2 for d in draws)
    curve = []
    for i, date in enumerate(CURVE_DATES):
        p, vals, wts = _conditional(draws, i, use_obs)
        row = {"date": date.isoformat(), "p": p}
        if full:
            row.update({"p10": kc._weighted_quantile(vals, wts, 0.10),
                        "p90": kc._weighted_quantile(vals, wts, 0.90),
                        "case1_only": _conditional(draws, i, use_obs, "case1")[0],
                        "case2_only": _conditional(draws, i, use_obs, "case2")[0]})
        curve.append(row)
    out = {"headline": curve[0], "curve": curve, "effective_sample_size": ess, "n_draws": len(draws)}
    if not full:
        return out

    last = len(points) - 1
    weights0 = [d["w"] for d in draws]

    def posterior_share(key: str, value: str) -> float:
        return sum(w for w, d in zip(weights0, draws) if d[key] == value) / wsum

    case1_rows = []
    for s in kc.SCENARIOS:
        case1_rows.append({"scenario": s, "prior_weight": kc.SCENARIOS[s]["weight"],
                           "share_given_observation": posterior_share("scenario", s),
                           "label": kc.SCENARIOS[s]["label"]})
    course_rows = [{"course": c, "prior_weight": kc.COURSES[c]["weight"],
                    "share_given_observation": posterior_share("course", c), "label": kc.COURSES[c]["label"]}
                   for c in kc.COURSES]
    q_rows = []
    for q in QUARANTINE:
        cell = [d for d in draws if d["quarantine"] == q]
        q_rows.append({"quarantine": q, "prior_weight": QUARANTINE[q]["weight"],
                       "p_conditioning_date": _conditional(cell, 0, use_obs)[0],
                       "median_m2": kc._weighted_quantile([d["m2"] for d in cell], [d["w"] for d in cell], 0.5),
                       "label": QUARANTINE[q]["label"]})

    def residual_mean(d: Mapping[str, Any]) -> float:
        if d["m1"] <= 0:
            return 0.0
        b = d["k"] / d["m1"]
        return (d["k"] + 1) / (b + d["f1"][0]) * (1.0 - d["f1"][0]) if use_obs else d["m1"] * (1.0 - d["f1"][0])

    any_case = []
    for i, date in enumerate(CURVE_DATES):
        num = den = 0.0
        elapsed = min(points[i], T_CONFIRM_END) - T_COND
        for d in draws:
            a_d = _none_by(d, i, "both", use_obs) * math.exp(-d["lam"] * elapsed)
            a_e = _none_by(d, last, "both", use_obs) * math.exp(-d["lam"] * (T_CONFIRM_END - T_COND))
            num += d["w"] * a_e
            den += d["w"] * a_d
        any_case.append({"date": date.isoformat(), "p": 1.0 - num / den})

    out.update({
        "case1_scenarios": case1_rows, "case1_courses": course_rows, "quarantine_scenarios": q_rows,
        "expected_cases": {
            "case1_residual_mean": sum(w * residual_mean(d) for w, d in zip(weights0, draws)) / wsum,
            "case2_offspring_mean": sum(w * d["m2"] for w, d in zip(weights0, draws)) / wsum,
            "case1_total_mean_given_observation": sum(w * d["m1"] for w, d in zip(weights0, draws)) / wsum,
        },
        "any_further_case_in_kenya": {"headline": any_case[0]["p"], "curve": any_case},
    })
    return out


# ---------------------------------------------------------------------------------------
# Sensitivity and artifact.
# ---------------------------------------------------------------------------------------
SENSITIVITY = [
    ("no_case2_update", "The second case is not evidence about the first case's transmission in Kenya (for example, infected elsewhere)", {"case2_update": False}),
    ("only_Q1", "Quarantine facility only", {"quarantine_weights": {"Q1": 1.0}}),
    ("only_Q2", "Home quarantine only", {"quarantine_weights": {"Q2": 1.0}}),
    ("k_0.18", "Dispersion fixed at Althaus 2015 k = 0.18", {"k_fixed": 0.18}),
    ("poisson", "No overdispersion (k = 1e6)", {"k_fixed": 1e6}),
    ("literature_only", "No reweighting by the reference class", {"calibrate": False}),
    ("rho_effective_R", "Transmissibility from the LOVS effective-R-under-response prior gamma(4, 3)", {"r_prior": kc.LOVS_EFFECTIVE_R}),
    ("theta_fixed", "No misfit factor (theta = 1)", {"theta_fixed": True}),
    ("death_low", "Second case: probability of death 0.15 to 0.30", {"p_death": (0.15, 0.30)}),
    ("death_high", "Second case: probability of death 0.45 to 0.65", {"p_death": (0.45, 0.65)}),
    ("isolation_slow", "Second case: onset to isolation 0.5 to 1.5 days", {"iso_delay": (0.5, 1.5)}),
    ("unlisted_high", "Second case: unlisted share of onward transmission 0.10 to 0.30", {"unlisted_share2": (0.10, 0.30)}),
    ("care_standard", "Second case: standard precautions for the whole stay, a precaution-failure bound", {"post_level2": "STANDARD"}),
    ("baseline_at_sensitivity_sample", "The primary model at the sensitivity sample size, for a like-with-like comparison", {}),
]
SENSITIVITY_DATES = ("2026-10-14", "2026-10-18", "2026-10-22", "2026-10-29")


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
            "probability": head["p"], "interval_80": [head["p10"], head["p90"]],
            "from_first_case": head["case1_only"], "from_second_case": head["case2_only"],
            "interval_note": ("Weighted 10th to 90th percentile across draws: parameter and scenario uncertainty "
                              "inside this model, not model-structure error, and not a worst case."),
            "benchmarks": {"further_local_case_after_a_first": further_local_benchmark(),
                           "context_not_comparable": {
                               "block11_kenya_price": round(kc.benchmarks()["block11_pooled"], 4),
                               "kenya_2026_10_09_scored_value": 0.163,
                               "note": "Both price a different question, acquisition in Kenya; shown as context only."}}},
        "curve": main["curve"],
        "curve_note": ("Each row: probability of YES given that no further linked case has been reported by the end "
                       "of that date (East Africa Time). The row for 2026-10-14 is the current forecast."),
        "any_further_case_in_kenya": main["any_further_case_in_kenya"],
        "any_further_case_note": ("Secondary quantity, not the question: any further confirmed case in Kenya, linked "
                                  "or not, adding further importations at the 9 October later-importation rate."),
        "case1_scenarios": main["case1_scenarios"],
        "case1_courses": main["case1_courses"],
        "case1_note": ("prior_weight is the 9 October weight; share_given_observation is the weight after "
                       "conditioning on one secondary case reported on 14 October and none other."),
        "quarantine_scenarios": main["quarantine_scenarios"],
        "expected_cases": main["expected_cases"],
        "effective_sample_size": main["effective_sample_size"],
        "sensitivity": list(sensitivity),
        "scoring_plan": {
            "scored_value": ("the curve value for the date this forecast is first shown outside the programme, "
                             "stated with that date; the artifact hash fixes it in advance"),
            "scored_against": ["the further-local-case benchmark from the reference class"],
            "resolution": ("an authority report (Kenya Ministry of Health, WHO or ECDC) of a further laboratory-"
                           "confirmed case in Kenya linked to the first two, confirmed 2026-10-15 to 2026-11-10 "
                           "and reported by 2026-11-13, with a coverage review naming its sources made on or "
                           "after 2026-11-14"),
            "excluded": ["suspected and probable cases", "a retracted report", "a case confirmed outside Kenya",
                         "a case attributed to a separate importation", "a case with no stated link"],
            "ambiguity": "an ambiguous case is decided by someone other than the author of this forecast",
            "if_a_case_occurs": ("score it in public with the interval and drivers as stated; say nothing about "
                                 "where, from whom or in which facility that case was infected"),
            "if_no_case_occurs": ("score it in public, and state that one outcome cannot separate this forecast from "
                                  "the benchmark; draw no conclusion about Kenya's response"),
        },
        "inputs": {
            "provenance": PROVENANCE,
            "clock": {"t0": "2026-10-03T00:00+03:00", "onset": ONSET, "isolation_delay": ISOLATION_DELAY,
                      "observed_report": T_OBSERVED, "observation_bin_half_width": OBS_BIN,
                      "conditioning": T_COND, "confirm_end": T_CONFIRM_END, "report_delay": kc.REPORT_DELAY},
            "quarantine_scenarios": QUARANTINE, "case2": CASE2, "imports_per_week": IMPORTS_PER_WEEK,
            "inherited": {"module": "lovs/forecast/kenya_conditioned.py", "model_version": kc.MODEL_VERSION},
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": DISCLOSURES,
        "method": {"seed": SEED, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS_SENSITIVITY,
                   "n_timing_samples": N_TIMING, "code": "lovs/forecast/kenya_second_case.py"},
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
