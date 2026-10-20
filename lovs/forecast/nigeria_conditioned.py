# SPDX-License-Identifier: Apache-2.0
"""Case-conditioned forecast of a further case in Nigeria after its first confirmed BDBV case.

Question (registered before any model code existed; see REGISTRATION): will Nigeria report at
least one further laboratory-confirmed case of this outbreak, beyond the first case confirmed on
2026-10-20, that is not a new importation, with confirmation dated 2026-10-21 to 2026-11-17 and
first reported by an authority by 2026-11-20?

This is a separate, additive forecast. Block 11 and every Kenya forecast stay frozen and are
scored as registered. The module imports the 9 October Kenya model
(``lovs/forecast/kenya_conditioned.py``) and changes none of it.

Model, in one paragraph. The expected number of first-generation symptomatic cases acquired in
Nigeria is

    m = theta * rho * [ R_comm * E_comm
                      + R_hosp * E_hosp * (f_pre * P_pre + (1 - f_pre) * P_post)
                      + R_fun  * p_death * E_fun * P_fun ]

with the 9 October priors for every shared quantity, Nigeria's case inputs, and negative
binomial offspring with dispersion k. The hospital term is split between health workers (P2)
and other patients and caregivers (P3). Each first-generation case seeds negative binomial
onward cases (P5) with mean R2 = rho * R_comm * E2 * P2, where P2 is the Ebola-grade level for
listed contacts and no precautions for unlisted ones. A later, separate importation can also
cause a case acquired in Nigeria (P6). The 13 frozen reference importations plus Kenya 2026
reweight the literature priors, and leave-one-out predictions validate the reweighting. Given
a draw, first-generation cases are a gamma-mixed Poisson process, so the chance that nothing is
confirmed in a set S is nb_none(m, k, H(S)), where H(S) is the chance that one first-generation
case, or any of its offspring, is confirmed in S. That gives the daily curve
P(YES | no further case reported by the end of date D) in closed form. Count tiers, the pathway
decomposition and the pathway shares come from a forward simulation of the same weighted draws.

Stdlib only and deterministic for a fixed seed: continuous variates come from
``random.Random.random()``. Inputs are labelled published or judgement in ``inputs.provenance``.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import functools
import hashlib
import json
import math
import operator
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs import lovs_priors_bundibugyo as priors
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import kenya_escape as ke

REPO = kc.REPO
EPISODES_FILE = REPO / "data" / "case-conditioned" / "nigeria-reference-episodes.json"
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "nigeria-2026-10-20-forecast.json"

MODEL_VERSION = "nigeria_conditioned-v1.0.0"
SEED = 20261020
N_DRAWS = 40_000
N_DRAWS_SENSITIVITY = 20_000
N_TIMING = 60_000
N_GEN2_PARENTS = 12_000     # first-generation timing samples used for the onward-case term
N_GEN2_OFFSETS = 4_000      # onset-to-confirmation samples of an onward case, per parent-onset node
SIM_REPLICATES = 2          # forward simulations per parameter draw, for the count quantities

REGISTRATION = {
    "plan_sha256": "02aba2506065add1189d8a6a695209f0ce4dd47e2273ef8c4effbadb82912b2b",
    "wiki_page_id": "reference:bdbv-nigeria-forecast-precommitment-2026-10-20",
    "registered_utc": "2026-10-20T22:50:33Z",
    "evidence_read_through_utc": "2026-10-20T22:45:00Z",
    "note": ("The question, priors, case inputs, benchmarks, seeds and sensitivity rows were fixed in a plan "
             "before any model code existed. Its sha256 was recorded on the wiki page at the time given. The "
             "plan is internal; its rules are restated in this artifact's inputs."),
}

# ---------------------------------------------------------------------------------------
# Clock: days from 2026-10-07 00:00 West Africa Time (UTC+1). Onset is day 0.
# ---------------------------------------------------------------------------------------
T0 = dt.date(2026, 10, 7)
ONSET = (0.0, 1.0)              # time of day of onset, uniform across the day
T_CARE = 4.0                    # care sought at a private facility on 11 October
T_PRECAUTION = (12.0, 14.0)     # Ebola-grade precautions from about 19 to 20 October
POST_SPAN = (3.0, 10.0)         # days of care under Ebola-grade precautions that carry exposure
DEATH = (14.0, 24.0)            # if death occurs: alive and stable at day 13
BURIAL_SPAN = 0.6               # the 9 October model's span from death to safe burial
T_LIST = 13.0                   # contact listing began about 20 October
REPORT_DELAY = kc.REPORT_DELAY  # confirmation to public report, days
LAG_LISTED_MEAN = kc.LAG_LISTED_MEAN
LAG_UNLISTED_MEAN = kc.LAG_UNLISTED_MEAN
WINDOW_FIRST = dt.date(2026, 10, 21)
WINDOW_LAST = dt.date(2026, 11, 17)
REPORT_BY = dt.date(2026, 11, 20)
CONDITIONING_DATE = dt.date(2026, 10, 20)
EVIDENCE_THROUGH_UTC = REGISTRATION["evidence_read_through_utc"]


def day_end(date: dt.date) -> float:
    return float((date - T0).days + 1)


T_WINDOW_OPEN = day_end(WINDOW_FIRST - dt.timedelta(days=1))   # confirmations after this count
T_CONFIRM_END = day_end(WINDOW_LAST)                             # ... up to the end of 17 November
T_REPORT_END = day_end(REPORT_BY)
CURVE_DATES = [WINDOW_FIRST + dt.timedelta(days=i) for i in range((WINDOW_LAST - WINDOW_FIRST).days + 1)]
SENSITIVITY_DATES = ("2026-10-20", "2026-10-24", "2026-10-31", "2026-11-07")
INF = float("inf")


def report_cutoff(date: dt.date) -> float:
    """Latest confirmation time whose public report falls by the end of ``date``."""
    return day_end(date) - REPORT_DELAY


# Sets of confirmation times, each a tuple of half-open intervals (a, b].
def _set_catalogue() -> dict[str, tuple[tuple[float, float], ...]]:
    c20 = (-INF, report_cutoff(CONDITIONING_DATE))
    window = (T_WINDOW_OPEN, T_CONFIRM_END)
    sets = {"none_before": ((-INF, -INF),), "window": (window,), "cond_and_window": (c20, window),
            "through_window_end": ((-INF, T_CONFIRM_END),)}
    for date in [CONDITIONING_DATE] + CURVE_DATES:
        sets[f"by_{date.isoformat()}"] = ((-INF, report_cutoff(date)),)
    return sets


SETS = _set_catalogue()
SET_NAMES = tuple(SETS)
SENSITIVITY_SETS = ("none_before", "window", "cond_and_window", "through_window_end") + tuple(
    f"by_{d}" for d in SENSITIVITY_DATES)

# ---------------------------------------------------------------------------------------
# Inputs fixed in the registered plan; gaps are filled as disclosed (see DISCLOSURES).
# ---------------------------------------------------------------------------------------
SCENARIOS = {
    "N1": {"weight": 0.6, "pre_level": "NONE", "f_pre": (0.85, 1.0),
           "label": "Unsuspected care, with Ebola-grade precautions only from about 19 to 20 October"},
    "N2": {"weight": 0.4, "pre_level": "STANDARD", "f_pre": (0.6, 0.95),
           "label": "Standard or contact precautions for a febrile patient, with some suspicion before confirmation"},
}
NIGERIA = {
    "e_comm": (0.20, 0.45), "e_hosp": (0.55, 1.0), "post_level": "VHF",
    "p_death": (0.10, 0.35), "e_fun": 1.0, "funeral_level": "SDB",
    "travel_attack_rate": 0.0, "unlisted_share": (0.15, 0.50),
    "patient_share_of_hospital_term": (0.3, 0.6),
}
SECOND_GENERATION = {"e2": (0.3, 1.0), "listed_level": "VHF", "unlisted_level": "NONE",
                     "infection_after_parent_onset": ke.PRIORS["second_generation_shift"]}
BACKGROUND = {"imports_per_week": (0.01, 0.06), "p_local": (0.05, 0.30),
              "delay_mean": kc.BACKGROUND["delay_mean"], "delay_shape": kc.BACKGROUND["delay_shape"],
              "imports_from": 13.0}
PATHWAYS = {
    "P1": "Community: household and community exposure from onset on 7 October to care on 11 October",
    "P2": "Health workers at the facility from 11 October to Ebola-grade isolation",
    "P3": "Other patients and caregivers at the facility over the same period",
    "P4": "Travel contacts: the travel was before onset, so this pathway is structurally zero",
    "P5": "Second generation: onward cases from first-generation cases",
    "P6": "Later importation: a case acquired in Nigeria from a later, separate importation",
    "funeral": "Funeral or body handling, if death occurs, under a safe burial (a model term with no registered pathway)",
}
DISPUTED_YES = kc.DISPUTED_YES
B2_RULE = {"pre_level": "NONE", "f_pre_lower_bound_at_least": 0.8}
SHARE_NOTE_THRESHOLD = 0.05   # relative shift below which the registration-day conditioning is "little information"


def incubation_prior() -> tuple[float, float]:
    """The programme's decided Bundibugyo incubation prior, read at run time."""
    return tuple(priors.BUNDIBUGYO_PRIORS_STAGE_TWO.incubation_gamma)


CITATIONS = {
    **{key: kc.CITATIONS[key] for key in ("faye2015", "althaus2015", "dean2016", "bower2016", "who_ert2014",
                                          "macneil2010", "shuaib2014")},
    "lovs_priors_v020": ("LOVS lovs/lovs_priors_bundibugyo.py BUNDIBUGYO_PRIORS_STAGE_TWO, version 0.2.0: incubation "
                         "gamma(3.0, 0.406), mean 7.39 days; back-projection R gamma(4, 2) truncated R > 1"),
    "kenya_conditioned": ("LOVS lovs/forecast/kenya_conditioned.py, the 9 October Kenya model (setting R priors, "
                          "dispersion, misfit factor, precaution and funeral levels, growth rates, lags, the "
                          "later-importation delay structure)"),
    "kenya_escape": ("LOVS lovs/forecast/kenya_escape.py: onward infection 2 to 8 days after the parent's onset"),
    "von_2026_10_20": ("Voice of Nigeria, 20 Oct 2026: the Coordinating Minister of Health and Social Welfare at a press "
                       "conference; captured sha256 d453bcb13f26dd67f306e4302c6bed78bb95b993d2d145db3ee1e67f2a9192c4"),
    "premium_times_2026_10_20": ("Premium Times, 20 Oct 2026: a statement signed by the Coordinating Minister of Health "
                                 "and Social Welfare; captured sha256 "
                                 "b0e8eafedb27bbb63bcec0c34af47a07864b5e56b0ceeb5f5cc4d3377e64a29a"),
    "ap_abc_2026_10_20": ("Associated Press, carried by ABC News, 20 Oct 2026: the Health Ministry; captured sha256 "
                          "451eb0eb2e18a743aa668b3f7c3f47ea638bfca4c91df8fd0863dfea39715e9c"),
    "who_afro_2026_10_20": ("WHO Regional Office for Africa, 20 Oct 2026: Nigeria confirms an imported case of "
                            "Bundibugyo virus disease; captured sha256 "
                            "e4dc146db5b422266a420a3e10d467f32161c09f123a2e314abbea592aad483f"),
    "uganda_moh_2026_10_20": ("Uganda Ministry of Health public statement, 20 Oct 2026; statement image sha256 "
                              "d88c332f9c74ab95836301b3e866f150d9aeb52112efc6d4707443200532eff7"),
    "registry": "data/international-events.json, report nga-2026-10-20, which records each source's address",
}
CASE_EVIDENCE = [
    {"fact": "One imported case of this outbreak confirmed by laboratory tests in the Federal Capital Territory, in a traveller from Uganda, announced on 20 October; the case is in isolation",
     "source": "Coordinating Minister of Health and Social Welfare, 20 Oct (von_2026_10_20; premium_times_2026_10_20; ap_abc_2026_10_20)"},
    {"fact": "Symptoms began on 7 October; care was sought at a private facility in the Federal Capital Territory on 11 October",
     "source": "von_2026_10_20; ap_abc_2026_10_20; who_afro_2026_10_20"},
    {"fact": "The first case was treated at the facility for several days before the diagnosis was confirmed, and was described as stable on 20 October",
     "source": "Press accounts of the 20 October ministry statement, as read for the registered plan"},
    {"fact": "44 contacts listed at announcement, including health workers, patients, caregivers and travel contacts",
     "source": "von_2026_10_20; premium_times_2026_10_20; who_afro_2026_10_20"},
    {"fact": "A laboratory test confirmed Bundibugyo virus on 20 October. Symptoms began four days after arrival in Nigeria, so the travel was before onset. Investigators are reconstructing the travel and exposure history",
     "source": "who_afro_2026_10_20 (added after registration; no input changed)"},
    {"fact": "Preliminary information: the traveller had been in the eastern DRC and transited Uganda in early October without symptoms. Travel through Uganda does not establish infection there; the location of exposure is under investigation; Uganda has no confirmed active case",
     "source": "uganda_moh_2026_10_20 (added after registration; context, not a Nigeria count; no input changed)"},
    {"fact": "No authority has stated where the first case was infected, and this forecast does not assume it",
     "source": "All sources above, read to the evidence time"},
    {"fact": "No further case in Nigeria had been reported by the evidence time",
     "source": "Freshness check of the ministry, NCDC, WHO, WHO AFRO, Africa CDC, ECDC and the national press to the evidence time"},
]
PROVENANCE = {
    "registration": "registered plan: the hash and time recorded on the wiki precommitment page",
    "clock": "case evidence (onset 7 Oct, care 11 Oct, precautions about 19 to 20 Oct, listing about 20 Oct); the precaution span, the exposure spans after precautions and the death window are judgement",
    "scenarios": "judgement: the registered precaution scenarios N1 and N2 and their weights",
    "nigeria": "judgement on case evidence: E_comm, E_hosp, p_death, the unlisted share and the hospital split are the registered judgement ranges; the travel term is zero because travel was before onset (case evidence)",
    "second_generation": "judgement: E2 and the residual-precaution levels are registered; onward infection 2 to 8 days after the parent's onset is source: kenya_escape",
    "background": "judgement: the registered importation and local-case ranges; the delay structure is source: kenya_conditioned",
    "incubation_gamma": "source: lovs_priors_v020, read at run time",
    "lags": "source: kenya_conditioned (listed mean 1 day, unlisted mean 5 days, Erlang shape 2)",
    "inherited_priors": "source: kenya_conditioned (faye2015 setting R, the LOVS back-projection R scale, k and theta priors, precaution and funeral levels, growth rates)",
    "reference_class": "case evidence for each episode; the 13 frozen episodes are copied verbatim and Kenya 2026 is coded from the 9 October forecast's own inputs",
    "pathways": "the registered pathway definitions, with the funeral term reported as its own row",
    "citations": "the published sources themselves",
    "provenance": "this map",
}


# ---------------------------------------------------------------------------------------
# Variates and the reference class.
# ---------------------------------------------------------------------------------------
def _gamma(rng: random.Random, shape: float, rate: float) -> float:
    """Gamma variate from rng.random() only: Erlang for an integer shape, else Marsaglia and Tsang."""
    if float(shape).is_integer():
        return kc._erlang(rng, int(shape), rate)
    return ke._gamma_any(rng, shape) / rate


def _poisson(rng: random.Random, lam: float) -> int:
    """Poisson variate by inversion, in chunks of mean at most 30 so exp(-lam) never underflows."""
    total = 0
    while lam > 0.0:
        part = min(lam, 30.0)
        lam -= part
        n, term, u = 0, math.exp(-part), rng.random()
        acc = term
        while u > acc and n < 1000:
            n += 1
            term *= part / n
            acc += term
        total += n
    return total


def load_episodes(path: Path = EPISODES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["episodes"]


def benchmarks(episodes: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """B1 and B2, derived from the coded features of the 14-episode class."""
    eps = list(episodes if episodes is not None else load_episodes())
    yes = sum(1 for e in eps if e["outcome"])
    closest = [e for e in eps if e["pre_level"] == B2_RULE["pre_level"]
               and e["f_pre"][0] >= B2_RULE["f_pre_lower_bound_at_least"]]
    c_yes = sum(1 for e in closest if e["outcome"])
    return {
        "B1_pooled_reference_rate": {"probability": kc._jeffreys(yes, len(eps)), "episodes": len(eps), "yes": yes,
                                     "rule": "Jeffreys over the 14 episodes: (yes + 0.5) / (n + 1)"},
        "B2_closest_analogue": {"probability": kc._jeffreys(c_yes, len(closest)), "episodes": len(closest),
                                "yes": c_yes, "episode_ids": [e["episode_id"] for e in closest],
                                "disputed_yes_among_them": sorted(e["episode_id"] for e in closest
                                                                  if e["episode_id"] in DISPUTED_YES),
                                "rule": ("unsuspected care in a destination facility: pre_level NONE and the lower bound "
                                         "of the coded f_pre range at least 0.8, Jeffreys within the subgroup; fixed in "
                                         "the registered plan")},
        "context_nigeria_2014_lagos": ("Named as context only, not a benchmark: the index was in hospital for about 5 days "
                                       "unsuspected and the episode produced 19 confirmed and 1 probable case "
                                       "(shuaib2014)."),
    }


# ---------------------------------------------------------------------------------------
# Timing: confirmation times of first-generation cases and of their onward cases.
# ---------------------------------------------------------------------------------------
TIMING_CLASSES = ("comm",) + tuple(("hosp_pre", g) for g in kc.GROWTH_RATES) + ("hosp_post", "fun")
LISTINGS = (("listed", LAG_LISTED_MEAN), ("unlisted", LAG_UNLISTED_MEAN))
O_STEP, O_MAX = 0.25, 90.0
G_NODES = 32


def _exposure(rng: random.Random, cls: Any) -> float:
    if cls == "comm":
        onset = kc._uniform(rng, ONSET)
        return kc._uniform(rng, (onset, T_CARE))
    if cls == "hosp_post":
        start = kc._uniform(rng, T_PRECAUTION)
        return start + kc._uniform(rng, (0.0, kc._uniform(rng, POST_SPAN)))
    if cls == "fun":
        return kc._uniform(rng, DEATH) + kc._uniform(rng, (0.0, BURIAL_SPAN))
    _, g = cls
    end = kc._uniform(rng, T_PRECAUTION)
    span = end - T_CARE
    u = rng.random()
    return end + math.log(u + (1.0 - u) * math.exp(-g * span)) / g


def _confirm(onset: float, lag: float, t_list: float) -> float:
    return max(onset, t_list) + lag


def _prob_in(sorted_samples: Sequence[float], intervals: Sequence[tuple[float, float]]) -> float:
    n = len(sorted_samples)
    total = 0
    for a, b in intervals:
        if b <= a:
            continue
        lo = 0 if a == -INF else bisect.bisect_right(sorted_samples, a)
        hi = bisect.bisect_right(sorted_samples, b)
        total += hi - lo
    return total / n


def _in(x: float, intervals: Sequence[tuple[float, float]]) -> bool:
    return any(a < x <= b for a, b in intervals)


@functools.lru_cache(maxsize=6)
def timing_tables(seed: int, incubation: tuple[float, float], t_list: float, n: int = N_TIMING) -> dict:
    """First-generation (onset, confirmation) samples and the onward-case term, for every timing class.

    Returns, per (class, listing): ``pairs`` (onsets and confirmations, unsorted, aligned), ``own``
    (P(confirmed in S) for every named set) and ``gen2`` (per set, weights over the G nodes of the
    reported fraction of a parent's offspring, restricted to parents not themselves confirmed in S).
    """
    rng = random.Random(seed + 1)
    out: dict[Any, dict] = {}
    for cls in TIMING_CLASSES:
        for listing, mean in LISTINGS:
            onsets, confs = [], []
            for _ in range(n):
                onset = _exposure(rng, cls) + _gamma(rng, *incubation)
                onsets.append(onset)
                confs.append(_confirm(onset, kc._erlang(rng, 2, 2.0 / mean), t_list))
            ordered = sorted(confs)
            out[(cls, listing)] = {"onsets": onsets, "confs": confs,
                                   "own": {s: _prob_in(ordered, SETS[s]) for s in SET_NAMES}}
    g_of_onset = _onward_fraction_grid(seed, incubation, t_list)
    m = min(N_GEN2_PARENTS, n)
    for (cls, listing), tab in out.items():
        grid = g_of_onset[listing]
        tab["gen2"] = {}
        for s in SET_NAMES:
            weights = [0.0] * (G_NODES + 1)
            intervals = SETS[s]
            for onset, conf in zip(tab["onsets"][:m], tab["confs"][:m]):
                if _in(conf, intervals):
                    continue
                x = min(max(onset, 0.0), O_MAX) / O_STEP
                i = min(int(x), len(grid[s]) - 2)
                frac = x - i
                g = (1.0 - frac) * grid[s][i] + frac * grid[s][i + 1]
                y = g * G_NODES
                j = min(int(y), G_NODES - 1)
                fy = y - j
                weights[j] += (1.0 - fy) / m
                weights[j + 1] += fy / m
            tab["gen2"][s] = weights
    return out


def _onward_fraction_grid(seed: int, incubation: tuple[float, float], t_list: float) -> dict:
    """Per listing and set: P(an onward case is confirmed in S | parent onset o) on a grid of o."""
    rng = random.Random(seed + 3)
    shift = SECOND_GENERATION["infection_after_parent_onset"]
    base = [(kc._uniform(rng, shift) + _gamma(rng, *incubation), kc._erlang(rng, 2, 2.0))
            for _ in range(N_GEN2_OFFSETS)]
    nodes = [i * O_STEP for i in range(int(round(O_MAX / O_STEP)) + 1)]
    out = {}
    for listing, mean in LISTINGS:
        grid = {s: [] for s in SET_NAMES}
        for o in nodes:
            ordered = sorted(_confirm(o + d, e * mean, t_list) for d, e in base)
            for s in SET_NAMES:
                grid[s].append(_prob_in(ordered, SETS[s]))
        out[listing] = grid
    return out


@functools.lru_cache(maxsize=2)
def background_units(seed: int) -> dict:
    """Per set: integral over arrivals from imports_from to the window end of P(local confirmation in S)."""
    delays = kc._background_tables(seed)
    lo, hi, steps = BACKGROUND["imports_from"], T_CONFIRM_END, 58
    h = (hi - lo) / steps
    out = {}
    for s in SET_NAMES:
        total = 0.0
        for i in range(steps):
            a = lo + (i + 0.5) * h
            total += sum(kc._cdf(delays, b - a) - (0.0 if x == -INF else kc._cdf(delays, x - a))
                         for x, b in SETS[s] if b > x) * h
        out[s] = total
    return out


def _onward_hit(r: float, k: float) -> list[float]:
    """1 - P(no offspring in a set) at each G node, for offspring NegBin(mean r, dispersion k)."""
    return [1.0 - math.exp(-k * math.log1p(r * (j / G_NODES) / k)) for j in range(G_NODES + 1)]


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def run(opts: Mapping[str, Any] | None = None, n: int = N_DRAWS, seed: int = SEED,
        episodes: Sequence[Mapping[str, Any]] | None = None) -> dict:
    opts = dict(opts or {})
    full = opts.get("full", True)
    episodes = list(episodes if episodes is not None else load_episodes())
    calibrate = opts.get("calibrate", True)
    calib_ids = set(opts.get("calibration_ids", [e["episode_id"] for e in episodes]))
    scen_weights = opts.get("scenario_weights", {s: v["weight"] for s, v in SCENARIOS.items()})
    incubation = tuple(opts.get("incubation", incubation_prior()))
    t_list = opts.get("t_list", T_LIST)
    unlisted = opts.get("unlisted_share", NIGERIA["unlisted_share"])
    e_hosp_range = opts.get("e_hosp", NIGERIA["e_hosp"])
    p_death_range = opts.get("p_death", NIGERIA["p_death"])
    sets = SET_NAMES if full else SENSITIVITY_SETS

    tables = timing_tables(seed, incubation, t_list)
    bg_unit = background_units(seed)
    rng = random.Random(seed)
    s_names = list(scen_weights)
    s_w = [scen_weights[s] for s in s_names]
    mul = operator.mul
    draws = []
    for _ in range(n):
        p = kc.draw_shared(rng, opts)
        loglik, own = 0.0, []
        for ep in episodes:
            prob = min(max(kc.episode_probability(rng, p, ep), 1e-12), 1.0 - 1e-12)
            ll = math.log(prob if ep["outcome"] else 1.0 - prob)
            own.append((prob, ll))
            if ep["episode_id"] in calib_ids:
                loglik += ll
        scen = rng.choices(s_names, s_w)[0]
        pre = SCENARIOS[scen]["pre_level"]
        post = NIGERIA["post_level"]
        f_pre = kc._uniform(rng, SCENARIOS[scen]["f_pre"])
        parts = kc.expected_cases(p, kc._uniform(rng, NIGERIA["e_comm"]), kc._uniform(rng, e_hosp_range), f_pre,
                                  pre, post, kc._uniform(rng, p_death_range), NIGERIA["e_fun"],
                                  NIGERIA["funeral_level"])
        patient_share = kc._uniform(rng, NIGERIA["patient_share_of_hospital_term"])
        g = rng.choice(kc.GROWTH_RATES)
        u = kc._uniform(rng, unlisted)
        e2 = kc._uniform(rng, SECOND_GENERATION["e2"])
        lam = kc._uniform(rng, BACKGROUND["imports_per_week"]) / 7.0 * kc._uniform(rng, BACKGROUND["p_local"])
        r2 = {"listed": p["rho"] * p["comm"] * e2 * p["levels"][SECOND_GENERATION["listed_level"]],
              "unlisted": p["rho"] * p["comm"] * e2 * p["levels"][SECOND_GENERATION["unlisted_level"]]}
        pre_mult = f_pre * p["levels"][pre]
        pre_frac = pre_mult / (pre_mult + (1.0 - f_pre) * p["levels"][post])
        m = sum(parts.values())
        coef = {"comm": parts["comm"] / m, ("hosp_pre", g): parts["hosp"] / m * pre_frac,
                "hosp_post": parts["hosp"] / m * (1.0 - pre_frac), "fun": parts["fun"] / m}
        k = p["k"]
        hit = {lst: _onward_hit(r2[lst], k) for lst, _ in LISTINGS}
        share = {"listed": 1.0 - u, "unlisted": u}
        nb = {}
        for s in sets:
            h = 0.0
            for cls, c in coef.items():
                for lst, _ in LISTINGS:
                    tab = tables[(cls, lst)]
                    h += c * share[lst] * (tab["own"][s] + sum(map(mul, tab["gen2"][s], hit[lst])))
            nb[s] = kc.nb_none(m, k, h)
        draws.append({"loglik": loglik, "own": own, "scenario": scen, "m": m, "k": k, "parts": parts,
                      "patient_share": patient_share, "pre_frac": pre_frac, "g": g, "u": u, "r2": r2,
                      "lam": lam, "nb": nb})

    max_ll = max(d["loglik"] for d in draws)
    for d in draws:
        d["w"] = math.exp(d["loglik"] - max_ll) if calibrate else 1.0
    out = summarise(draws, bg_unit, full)
    if full:
        out["validation"] = validation(draws, episodes, calib_ids, calibrate)
        out["simulation"] = simulate(draws, tables, seed, incubation=incubation, t_list=t_list)
    return out


def _none(d: Mapping[str, Any], s: str, bg_unit: Mapping[str, float], which: str) -> float:
    a = d["nb"][s] if which != "background" else 1.0
    b = math.exp(-d["lam"] * bg_unit[s]) if which != "index" else 1.0
    return a * b


def conditional(draws: Sequence[Mapping[str, Any]], bg_unit: Mapping[str, float], cond: str, joint: str,
                which: str = "both") -> tuple[float, list[float], list[float]]:
    """P(YES | nothing in ``cond``) = 1 - P(nothing in ``joint``) / P(nothing in ``cond``).

    ``joint`` is the union of the conditioning set and the window. Also per-draw values and their weights.
    """
    num = den = 0.0
    vals, wts = [], []
    for d in draws:
        a_c = _none(d, cond, bg_unit, which)
        a_j = _none(d, joint, bg_unit, which)
        num += d["w"] * a_j
        den += d["w"] * a_c
        vals.append(1.0 - a_j / a_c if a_c > 0 else 1.0)
        wts.append(d["w"] * a_c)
    return 1.0 - num / den, vals, wts


def _headline_sets() -> tuple[str, str]:
    return f"by_{CONDITIONING_DATE.isoformat()}", "cond_and_window"


def _date_sets(date: str) -> tuple[str, str]:
    if date == CONDITIONING_DATE.isoformat():
        return _headline_sets()
    return f"by_{date}", "through_window_end"


def summarise(draws: Sequence[Mapping[str, Any]], bg_unit: Mapping[str, float], full: bool = True) -> dict:
    wsum = sum(d["w"] for d in draws)
    ess = wsum ** 2 / sum(d["w"] ** 2 for d in draws)
    cond, joint = _headline_sets()
    p, vals, wts = conditional(draws, bg_unit, cond, joint)
    head = {"date": CONDITIONING_DATE.isoformat(), "p": p}
    unconditioned = conditional(draws, bg_unit, "none_before", "window")[0]
    m_mean = sum(d["w"] * d["m"] for d in draws) / wsum
    out = {"effective_sample_size": ess, "n_draws": len(draws), "unconditioned": unconditioned,
           "mean_m": m_mean}
    if not full:
        out["by_date"] = {date: conditional(draws, bg_unit, *_date_sets(date))[0] for date in SENSITIVITY_DATES}
        out["headline"] = head
        return out
    head.update({"p10": kc._weighted_quantile(vals, wts, 0.10), "p90": kc._weighted_quantile(vals, wts, 0.90),
                 "index_linked": conditional(draws, bg_unit, cond, joint, "index")[0],
                 "background_later_importation": conditional(draws, bg_unit, cond, joint, "background")[0]})
    curve = []
    for date in CURVE_DATES:
        c, j = _date_sets(date.isoformat())
        p_d, v_d, w_d = conditional(draws, bg_unit, c, j)
        curve.append({"date": date.isoformat(), "p": p_d,
                      "p10": kc._weighted_quantile(v_d, w_d, 0.10), "p90": kc._weighted_quantile(v_d, w_d, 0.90),
                      "index_linked": conditional(draws, bg_unit, c, j, "index")[0],
                      "background_later_importation": conditional(draws, bg_unit, c, j, "background")[0]})
    out.update({"headline": head, "curve": curve})

    cond_w = [d["w"] * _none(d, cond, bg_unit, "both") for d in draws]
    cond_total = sum(cond_w)
    scen_rows = []
    for s in SCENARIOS:
        cell = [d for d in draws if d["scenario"] == s]
        if not cell:
            continue
        scen_rows.append({
            "scenario": s, "prior_weight": SCENARIOS[s]["weight"],
            "share_given_no_report": sum(cw for cw, d in zip(cond_w, draws) if d["scenario"] == s) / cond_total,
            "p_conditioning_date": conditional(cell, bg_unit, cond, joint)[0],
            "median_m_calibrated": kc._weighted_quantile([d["m"] for d in cell], [d["w"] for d in cell], 0.5),
            "label": SCENARIOS[s]["label"]})
    out["scenarios"] = scen_rows

    def wmean(f) -> float:
        return sum(d["w"] * f(d) for d in draws) / wsum

    out["expected_first_generation"] = {
        "mean_m": m_mean,
        "by_pathway_ever": {
            "P1": wmean(lambda d: d["parts"]["comm"]),
            "P2": wmean(lambda d: d["parts"]["hosp"] * (1.0 - d["patient_share"])),
            "P3": wmean(lambda d: d["parts"]["hosp"] * d["patient_share"]),
            "P4": 0.0,
            "funeral": wmean(lambda d: d["parts"]["fun"]),
        },
        "mean_r2_listed": wmean(lambda d: d["r2"]["listed"]),
        "mean_r2_unlisted": wmean(lambda d: d["r2"]["unlisted"]),
        "k_geometric_mean": math.exp(wmean(lambda d: math.log(d["k"]))),
        "note": ("Calibrated means over all time, before conditioning and without the window: first-generation "
                 "cases by pathway, and the mean onward reproduction number of a listed and of an unlisted case."),
    }
    return out


def validation(draws: Sequence[Mapping[str, Any]], episodes: Sequence[Mapping[str, Any]],
               calib_ids: set[str], calibrate: bool) -> dict:
    """Leave-one-out over the 14 episodes, against the pooled Jeffreys rate, as in the Kenya module."""
    wsum = sum(d["w"] for d in draws)
    n_ep = len(episodes)
    yes_total = sum(1 for e in episodes if e["outcome"])
    rows = []
    for j, ep in enumerate(episodes):
        prior_p = sum(d["own"][j][0] for d in draws) / len(draws)
        if calibrate:
            own_ll = (lambda d: d["own"][j][1]) if ep["episode_id"] in calib_ids else (lambda d: 0.0)
            ref = max(d["loglik"] - own_ll(d) for d in draws)
            lw = [math.exp(d["loglik"] - own_ll(d) - ref) for d in draws]
            loo_p = sum(w * d["own"][j][0] for w, d in zip(lw, draws)) / sum(lw)
            fit_p = sum(d["w"] * d["own"][j][0] for d in draws) / wsum
        else:
            loo_p = fit_p = prior_p
        y = 1 if ep["outcome"] else 0
        group = [e for e in episodes if e["responder"] == ep["responder"] and e is not ep]
        rows.append({"episode_id": ep["episode_id"], "outcome": bool(y), "responder": ep["responder"],
                     "literature_only": prior_p, "calibrated_loo": loo_p, "calibrated_in_sample": fit_p,
                     "base_rate_loo": (yes_total - y + 0.5) / n_ep,
                     "subgroup_loo": (sum(1 for e in group if e["outcome"]) + 0.5) / (len(group) + 1)})

    def scores(key: str) -> dict:
        return {"brier": sum((r[key] - r["outcome"]) ** 2 for r in rows) / n_ep,
                "log_score": sum(math.log(r[key] if r["outcome"] else 1.0 - r[key]) for r in rows) / n_ep,
                "mean_predicted": sum(r[key] for r in rows) / n_ep}

    def paired(against: str) -> dict:
        diffs = [(r["calibrated_loo"] - r["outcome"]) ** 2 - (r[against] - r["outcome"]) ** 2 for r in rows]
        mean = sum(diffs) / len(diffs)
        se = math.sqrt(sum((x - mean) ** 2 for x in diffs) / (len(diffs) - 1) / len(diffs))
        return {"mean_brier_difference": mean, "standard_error": se, "distinguishable": abs(mean) > 2 * se}

    keys = ("calibrated_loo", "literature_only", "base_rate_loo", "subgroup_loo", "calibrated_in_sample")
    return {
        "episodes": rows, "observed_rate": yes_total / n_ep,
        "scores": {k: scores(k) for k in keys},
        "paired_differences_vs_calibrated": {k: paired(k) for k in ("base_rate_loo", "subgroup_loo", "literature_only")},
        "note": ("Negative mean_brier_difference favours the calibrated model; a difference smaller than twice its "
                 "standard error is not evidence of a better forecaster. The episode features were coded by people "
                 "who knew the outcomes, so even the leave-one-out scores are optimistic."),
    }


# ---------------------------------------------------------------------------------------
# Forward simulation: count tiers, decomposition by pathway and pathway shares.
# ---------------------------------------------------------------------------------------
SIM_PATHWAYS = ("P1", "P2", "P3", "P4", "funeral", "P5", "P6")


def simulate(draws: Sequence[Mapping[str, Any]], tables: Mapping[Any, Mapping[str, Any]], seed: int,
             replicates: int = SIM_REPLICATES, incubation: tuple[float, float] | None = None,
             t_list: float = T_LIST) -> dict:
    """Simulate counts for every draw, keep runs with no report by the end of 20 October, weight by draw."""
    incubation = tuple(incubation or incubation_prior())
    rng = random.Random(seed + 5)
    cutoff = report_cutoff(CONDITIONING_DATE)
    shift = SECOND_GENERATION["infection_after_parent_onset"]
    delays = kc._background_tables(seed)
    bg_span = T_CONFIRM_END - BACKGROUND["imports_from"]
    kept_w = total_w = 0.0
    exp_count = {p: 0.0 for p in SIM_PATHWAYS}
    any_from = {p: 0.0 for p in SIM_PATHWAYS}
    first_from = {p: 0.0 for p in SIM_PATHWAYS}
    tiers = {1: 0.0, 3: 0.0, 10: 0.0}
    for d in draws:
        k, m = d["k"], d["m"]
        parts = d["parts"]
        path_w = [parts["comm"], parts["hosp"] * (1.0 - d["patient_share"]), parts["hosp"] * d["patient_share"],
                  parts["fun"]]
        path_names = ("P1", "P2", "P3", "funeral")
        for _ in range(replicates):
            total_w += d["w"]
            events = []
            n1 = _poisson(rng, m * ke._gamma_any(rng, k) / k)
            for _ in range(n1):
                path = rng.choices(path_names, path_w)[0]
                if path == "P1":
                    cls = "comm"
                elif path == "funeral":
                    cls = "fun"
                else:
                    cls = ("hosp_pre", d["g"]) if rng.random() < d["pre_frac"] else "hosp_post"
                listing = "listed" if rng.random() >= d["u"] else "unlisted"
                tab = tables[(cls, listing)]
                j = min(int(rng.random() * len(tab["confs"])), len(tab["confs"]) - 1)
                onset1 = tab["onsets"][j]
                events.append((tab["confs"][j], path))
                mean = LAG_LISTED_MEAN if listing == "listed" else LAG_UNLISTED_MEAN
                for _ in range(_poisson(rng, d["r2"][listing] * ke._gamma_any(rng, k) / k)):
                    onset2 = onset1 + kc._uniform(rng, shift) + _gamma(rng, *incubation)
                    events.append((_confirm(onset2, kc._erlang(rng, 2, 2.0 / mean), t_list), "P5"))
            for _ in range(_poisson(rng, d["lam"] * bg_span)):
                arrival = BACKGROUND["imports_from"] + bg_span * rng.random()
                events.append((arrival + delays[min(int(rng.random() * len(delays)), len(delays) - 1)], "P6"))
            if any(t <= cutoff for t, _ in events):
                continue
            kept_w += d["w"]
            qualifying = sorted((t, p) for t, p in events if T_WINDOW_OPEN < t <= T_CONFIRM_END)
            for tier in tiers:
                if len(qualifying) >= tier:
                    tiers[tier] += d["w"]
            for p in SIM_PATHWAYS:
                c = sum(1 for _, q in qualifying if q == p)
                exp_count[p] += d["w"] * c
                if c:
                    any_from[p] += d["w"]
            if qualifying:
                first_from[qualifying[0][1]] += d["w"]
    yes = tiers[1]
    return {
        "p_yes_simulated": yes / kept_w,
        "p_at_least_3": tiers[3] / kept_w,
        "p_at_least_10": tiers[10] / kept_w,
        "expected_qualifying_cases": sum(exp_count.values()) / kept_w,
        "decomposition": {p: {"expected_qualifying_cases": exp_count[p] / kept_w,
                              "p_at_least_one": any_from[p] / kept_w, "label": PATHWAYS[p]} for p in SIM_PATHWAYS},
        "shares_of_yes_by_first_qualifying_case": {p: (first_from[p] / yes if yes > 0 else 0.0) for p in SIM_PATHWAYS},
        "share_of_simulations_kept": kept_w / total_w,
        "replicates_per_draw": replicates,
        "note": ("Forward simulation of the same weighted draws on a separate random stream, kept when nothing is "
                 "reported by the end of 20 October. Counts are further qualifying cases confirmed 21 October to 17 "
                 "November. Expected numbers by pathway sum to the total. p_yes_simulated cross-checks the "
                 "closed-form headline and differs from it only by Monte Carlo and grid error."),
    }


# ---------------------------------------------------------------------------------------
# Sensitivity and artifact.
# ---------------------------------------------------------------------------------------
SENSITIVITY = [
    ("incubation_mean_8", "Incubation mean 8 days (gamma shape 3, rate 0.375; same coefficient of variation)", {"incubation": (3.0, 0.375)}),
    ("incubation_mean_10", "Incubation mean 10 days (gamma shape 3, rate 0.3; same coefficient of variation)", {"incubation": (3.0, 0.3)}),
    ("only_N1", "Scenario N1 only (unsuspected care)", {"scenario_weights": {"N1": 1.0}}),
    ("only_N2", "Scenario N2 only (standard or contact precautions)", {"scenario_weights": {"N2": 1.0}}),
    ("calibration_without_kenya_2026", "Calibration on the 13 frozen episodes, without Kenya 2026", {"calibration_ids": "frozen_13"}),
    ("literature_only", "Literature priors only (no calibration)", {"calibrate": False}),
    ("k_0.18", "Dispersion fixed at k = 0.18", {"k_fixed": 0.18}),
    ("poisson", "Poisson limit (k = 1e6)", {"k_fixed": 1e6}),
    ("unlisted_low", "Unlisted share 0.05 to 0.15", {"unlisted_share": (0.05, 0.15)}),
    ("unlisted_high", "Unlisted share 0.5 to 0.8", {"unlisted_share": (0.5, 0.8)}),
    ("e_hosp_low", "E_hosp 0.3 to 0.6 (more of the late stage after isolation)", {"e_hosp": (0.3, 0.6)}),
    ("p_death_high", "Probability of death 0.4 to 0.6", {"p_death": (0.4, 0.6)}),
    ("listing_day_14", "Contact listing from day 14 instead of day 13", {"t_list": 14.0}),
    ("drop_disputed_yes", "Calibration without the Kenya module's three disputed-YES episodes", {"calibration_ids": "exclude_disputed"}),
    ("baseline_at_sensitivity_sample", "Baseline at the sensitivity sample size", {}),
]


def _resolve_opts(opts: Mapping[str, Any]) -> dict:
    opts = dict(opts)
    ids = [e["episode_id"] for e in load_episodes()]
    if opts.get("calibration_ids") == "frozen_13":
        frozen = {e["episode_id"] for e in kc.load_features()}
        opts["calibration_ids"] = [i for i in ids if i in frozen]
    elif opts.get("calibration_ids") == "exclude_disputed":
        opts["calibration_ids"] = [i for i in ids if i not in DISPUTED_YES]
    return opts


def sensitivity_rows(n: int = N_DRAWS_SENSITIVITY) -> list[dict]:
    rows = []
    for name, label, opts in SENSITIVITY:
        res = run({**_resolve_opts(opts), "full": False}, n=n)
        rows.append({"variant": name, "label": label,
                     **{f"p_{d}": res["by_date"][d] for d in SENSITIVITY_DATES},
                     "p_unconditioned": res["unconditioned"], "mean_m": res["mean_m"]})
    return rows


def _pct(p: float) -> str:
    return f"{round(100 * p)}%"


def headline_label(head: Mapping[str, Any]) -> str:
    return (f"If no further case is reported in Nigeria by the end of 20 October: about {_pct(head['p'])} (80% interval "
            f"{_pct(head['p10'])[:-1]} to {_pct(head['p90'])}) that Nigeria reports a further confirmed case, not a new "
            f"importation, confirmed 21 October to 17 November. A forecast, not an assessment of the country's response.")


def conditioning_note(unconditioned: float, headline: float) -> str:
    """Wording generated from the two computed values, by a rule fixed before the first run."""
    shift = headline - unconditioned
    rel = abs(shift) / unconditioned if unconditioned > 0 else 0.0
    verdict = ("so the registration-day conditioning carries little information, as the registered plan expected"
               if rel < SHARE_NOTE_THRESHOLD else
               "so the registration-day conditioning carries more information than the registered plan expected: under "
               "the registered detection rule, listed contacts with onset before day 13 are confirmed from day 13, and "
               "part of that mass would already have been reported by the end of 20 October")
    return (f"Without conditioning, the probability of a qualifying case is {unconditioned:.3f}; given no further case "
            f"reported by the end of 20 October it is {headline:.3f}, a change of {shift:+.3f} ({100 * rel:.1f} percent of the "
            f"unconditioned value), "
            f"{verdict}. No qualifying report can exist before the window opens on 21 October, so the conditioning is "
            f"on no report of a further case that would otherwise qualify, as in the Kenya forecasts.")


DISCLOSURES = [
    ("Registration: the question, priors, case inputs, benchmarks, seed, draw counts and the 15 sensitivity rows were "
     "fixed in a plan before any model code existed (sha256 02aba2506065add1189d8a6a695209f0ce4dd47e2273ef8c4effbadb82912b2b, "
     "recorded on the wiki page reference:bdbv-nigeria-forecast-precommitment-2026-10-20 at 2026-10-20T22:50:33Z). The "
     "plan is internal; its rules are restated in the inputs. No parameter was changed after seeing model output."),
    ("Gaps in the plan were filled before the first run, each by a rule written down first: (1) Kenya 2026's e_hosp "
     "0.4 to 1.0, pre_level STANDARD and e_fun 1.0, from the 9 October forecast's own inputs by the same hull rule the "
     "plan uses for f_pre; (2) the funeral term, which has no registered pathway, is its own row, with death if any on "
     "days 14 to 24; (3) onward infection 2 to 8 days after the parent's onset, the Kenya escape module's shift, for the "
     "plan's time to the infectiousness peak; (4) an onward case takes its parent's listing status; (5) offspring of "
     "each case are negative binomial with the draw's k; (6) Ebola-grade precautions start uniformly on days 12 to 14, "
     "with care before them weighted toward their start at the Kenya growth rates and care after them spread over 3 to "
     "10 days; (7) community exposure is uniform from onset to day 4; (8) the unlisted share applies to every first-"
     "generation case; (9) later importations arrive from day 13 (20 October); (10) the incubation rows keep shape 3; "
     "(11) B2 uses the lower bound of the coded f_pre range; (12) the listing-day row moves only the detection rule."),
    ("The plan writes R2 = rho * R_comm * E2 * P2 without the misfit factor theta. It is implemented as written, so the "
     "calibrated theta shapes the first generation only."),
    ("Evidence added after registration, with no input changed: WHO's Regional Office for Africa states that a "
     "laboratory test confirmed Bundibugyo virus on 20 October and that symptoms began four days after arrival in "
     "Nigeria, which agrees with the plan's travel-before-onset inputs; Uganda's Ministry of Health states, as "
     "preliminary information, that the traveller had been in the eastern DRC and transited Uganda without symptoms, "
     "and that the location of exposure is under investigation. WHO's species statement effectively settles the void "
     "condition about a different ebolavirus species; the void rule is kept as registered."),
    ("No authority has said where the first case was infected, and nothing here assumes it. Nothing here identifies a "
     "person or a facility."),
    ("The 13 frozen episodes were coded after their outcomes were known, and Kenya 2026 was added knowing its outcome, "
     "although its features are the 9 October forecast's inputs, fixed before any secondary case was reported. The "
     "validation is over the same episodes that inform the calibration, so its scores are optimistic."),
    ("The setting reproduction numbers are Zaire ebolavirus values from Conakry in 2014, inherited from the 9 October "
     "model with its misfit factor and transmissibility scale; no Bundibugyo setting-specific estimate exists."),
    ("The 80 percent interval covers parameter and scenario uncertainty inside this model, with the incubation period "
     "fixed. It does not cover model-structure error and is not a worst case."),
    ("Count tiers, the decomposition by pathway and the pathway shares come from a forward simulation of the same "
     "weighted draws on a separate random stream; its probability of YES is reported beside the closed-form headline as "
     "a check. The onward-case part of the closed form is computed on grids (parent onset every 0.25 days, 33 nodes in "
     "the reported fraction of a parent's offspring) with linear interpolation, an error far below the Monte Carlo "
     "error."),
    ("Third-generation cases are not modelled: one needs a second-generation case first, which would itself resolve "
     "the question in nearly every timing."),
    ("The later-importation term (P6) is judgement, small, and reported separately."),
    ("Correction after the first run, before any number was shown to anyone: the conditioning note printed the change "
     "from the unconditioned value with the wrong sign. The text was corrected; no number, input or parameter changed. "
     "The first run's payload sha256, 2f2fced947373a6432e3b0632b26447a4687a376e798670f23450783af1a6e4a, is recorded on "
     "the wiki precommitment page beside this version's."),
    ("This forecast is additive. Block 11 and every Kenya forecast stay frozen and are scored as registered, and this is "
     "not a calibration row unless the founder rules otherwise."),
]


def payload_digest(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_artifact(main: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    head = main["headline"]
    sim = main["simulation"]
    marks = benchmarks()
    b2 = marks["B2_closest_analogue"]
    disclosures = DISCLOSURES + [
        conditioning_note(main["unconditioned"], head["p"]),
        (f"Benchmark B2 rests on {b2['episodes']} episodes ({', '.join(b2['episode_ids'])}), "
         f"{len(b2['disputed_yes_among_them'])} of them among the three episodes whose YES the Kenya module marks "
         f"contestable. It is a reference rate, not a forecast for Nigeria."),
        (f"The forward simulation gives {sim['p_yes_simulated']:.3f} for the headline quantity against the closed form's "
         f"{head['p']:.3f}."),
    ]
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "nigeria-bdbv-2026-10-20-further-case",
        "model_version": MODEL_VERSION,
        "question": ("Will Nigeria report at least one further laboratory-confirmed case of this outbreak, beyond the "
                     "first case confirmed on 2026-10-20, that is not a new importation, with confirmation dated "
                     "2026-10-21 to 2026-11-17 and first reported by an authority by 2026-11-20?"),
        "rules": {
            "authorities": ("Nigeria's Federal Ministry of Health and Social Welfare, NCDC, WHO (Disease Outbreak News, "
                            "regional office, Director-General), Africa CDC, ECDC or US CDC. A press report alone does not "
                            "count; a state broadcaster or national news agency carrying a minister's or NCDC official's "
                            "statement does."),
            "what_counts": ("Any further confirmed case in Nigeria counts unless an authority reports a stay, in the 21 "
                            "days before onset, in an area with a confirmed case in the 42 days before that onset (a new "
                            "importation). A contact of the first case counts, and so does a case of unknown source "
                            "acquired in Nigeria."),
            "where_the_case_falls_ill": ("A case confirmed in another country after travel from Nigeria counts only if "
                                         "the authority states it was infected in Nigeria."),
            "ambiguity": ("Anything else unclear is decided by a reviewer other than the author, citing the authority "
                          "statement, and recorded as an attribution in data/international-events.json."),
            "void": ("Void if, by 2026-11-20, an authority states that the first case is not a case of this outbreak, "
                     "for example a different ebolavirus species or a discarded result."),
            "void_status_note": ("WHO's Regional Office for Africa stated on 20 October that a laboratory test confirmed "
                                 "Bundibugyo virus, which effectively settles the species condition; the rule is kept "
                                 "as registered."),
            "registration_timing": ("Only reports first published after the registration entry count. If a qualifying "
                                    "report was published between 2026-10-20 22:45 UTC and that entry, the question is "
                                    "void."),
        },
        "relationship_to_earlier_forecasts": ("Additive. Block 11 and every Kenya forecast stay frozen and are scored as "
                                              "registered. This forecast imports the 9 October Kenya model unchanged."),
        "registration": REGISTRATION,
        "evidence_through_utc": EVIDENCE_THROUGH_UTC,
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {
            "label": headline_label(head),
            "probability": head["p"], "interval_80": [head["p10"], head["p90"]],
            "index_linked": head["index_linked"], "background_later_importation": head["background_later_importation"],
            "unconditioned": main["unconditioned"],
            "conditioning_note": conditioning_note(main["unconditioned"], head["p"]),
            "interval_note": ("Weighted 10th to 90th percentile across draws: parameter and scenario uncertainty inside "
                              "this model, with the incubation period fixed; not model-structure error and not a worst "
                              "case."),
            "benchmarks": marks,
        },
        "curve": main["curve"],
        "curve_note": ("Each row: probability of YES given that no further case has been reported by the end of that "
                       "date (West Africa Time). The headline is the value for 20 October."),
        "count_tiers": {"p_at_least_1_simulated": sim["p_yes_simulated"], "p_at_least_3": sim["p_at_least_3"],
                        "p_at_least_10": sim["p_at_least_10"],
                        "note": "Further qualifying cases confirmed by 2026-11-17, given no report by the end of 20 October."},
        "decomposition": {"by_pathway": sim["decomposition"],
                          "expected_qualifying_cases_total": sim["expected_qualifying_cases"],
                          "shares_of_yes": sim["shares_of_yes_by_first_qualifying_case"],
                          "simulation": {k: sim[k] for k in ("p_yes_simulated", "share_of_simulations_kept",
                                                             "replicates_per_draw", "note")},
                          "first_generation": main["expected_first_generation"]},
        "scenarios": main["scenarios"],
        "validation": main["validation"],
        "effective_sample_size": main["effective_sample_size"],
        "sensitivity": list(sensitivity),
        "sensitivity_note": ("Each row moves one registered input; p columns are P(YES | no further case reported by the "
                             "end of that date), p_unconditioned has no conditioning."),
        "scoring_plan": {
            "scored_value": "the headline probability for 20 October, fixed by this artifact's hash",
            "scored_by": ("Brier score at a coverage review naming its sources, made between 2026-11-21 and 2026-12-05. "
                          "Silence never resolves NO."),
            "shown_beside": ["the curve value standing on the day of the first qualifying report (the row for the last "
                             "whole day before the report)", "benchmarks B1 and B2"],
            "published": "whatever the outcome",
            "wording": ("The result is reported as a probability beside the outcome and is never called a hit or a "
                        "miss. One outcome cannot separate this forecast from the benchmarks, and the result says "
                        "nothing about Nigeria's response."),
            "if_a_case_occurs": "say nothing about where, from whom or in which facility that case was infected",
        },
        "inputs": {
            "provenance": PROVENANCE,
            "registration": REGISTRATION,
            "clock": {"t0": "2026-10-07T00:00+01:00", "onset": ONSET, "care": T_CARE, "precaution_start": T_PRECAUTION,
                      "care_after_precautions": POST_SPAN, "death_if_any": DEATH, "burial_span": BURIAL_SPAN,
                      "listing_day": T_LIST, "report_delay": REPORT_DELAY, "window_confirmation": [T_WINDOW_OPEN, T_CONFIRM_END],
                      "report_by": T_REPORT_END, "conditioning_report_cutoff": day_end(CONDITIONING_DATE),
                      "detection_rule": ("confirmation = max(onset, listing day) + Erlang shape 2 lag (listed mean 1 day, "
                                         "unlisted mean 5 days); report = confirmation + 0.5 days")},
            "scenarios": SCENARIOS, "nigeria": NIGERIA, "second_generation": SECOND_GENERATION,
            "background": BACKGROUND, "incubation_gamma": incubation_prior(),
            "lags": {"listed_mean": LAG_LISTED_MEAN, "unlisted_mean": LAG_UNLISTED_MEAN, "erlang_shape": 2},
            "inherited_priors": {"module": "lovs/forecast/kenya_conditioned.py", "model_version": kc.MODEL_VERSION,
                                 "setting_R_faye2015": kc.SETTING_R, "lovs_backprojection_R": kc.LOVS_BACKPROJECTION_R,
                                 "k_prior_lognormal_median_logsd": kc.K_PRIOR,
                                 "theta_prior_lognormal_median_logsd": kc.THETA_PRIOR, "precaution_levels": kc.LEVELS,
                                 "funeral_levels": kc.FUNERAL, "growth_rates": kc.GROWTH_RATES},
            "reference_class": {"file": "data/case-conditioned/nigeria-reference-episodes.json",
                                "episodes": len(load_episodes()), "disputed_yes": list(DISPUTED_YES)},
            "pathways": PATHWAYS,
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": disclosures,
        "method": {"seed": SEED, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS_SENSITIVITY,
                   "n_timing_samples": N_TIMING, "code": "lovs/forecast/nigeria_conditioned.py",
                   "regeneration": ("the test suite regenerates the main run and checks it against this file; the "
                                    "sensitivity rows are carried over there and recomputed only by the module")},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


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
    print(json.dumps({"out": args.out.name, "payload_sha256": artifact["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
