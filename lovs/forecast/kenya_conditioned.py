# SPDX-License-Identifier: Apache-2.0
"""Case-conditioned forecast of onward transmission from Kenya's first imported BDBV case.

Question (the same as Block 11's Kenya question, ``lovs/forecast/pins_block11.py``): will
Kenya report at least one laboratory-confirmed case that the authority states was acquired in
Kenya, confirmed 2026-10-08 to 2026-11-04 (reports up to 2026-11-07 count)?

Block 11 priced it at 0.393 from 13 past importations and ignored the case by design, so the
monthly series stays comparable. This module is a separate, additive forecast that uses the
case: where the patient spent the infectious, terminal phase, the transmission settings, the
precautions in place, the LOVS Bundibugyo priors and the days that have passed.

Model, in one paragraph. The expected number of secondary symptomatic cases acquired in a
destination country is

    m = theta * rho * [ R_comm * E_comm
                      + R_hosp * E_hosp * (f_pre * P_pre + (1 - f_pre) * P_post)
                      + R_fun  * p_death * E_fun * P_fun ] + m_flight

where R_comm, R_hosp and R_fun are setting-specific reproduction numbers before infection
control (Faye 2015), rho scales them by the LOVS uncontrolled-R prior, the E terms are the
shares of each setting's exposure that happened in the destination (case timeline), the P
terms are residual-transmission multipliers for the precautions in place, and theta is a
misfit factor. Secondary cases are negative binomial with dispersion k (Althaus 2015). The
same coded model is applied to the 13 reference importations; their outcomes reweight the
literature priors (importance weighting) and leave-one-out predictions validate it. Timing
uses the LOVS incubation prior plus onset-to-report lags, which turns the forecast into a
daily curve P(YES | no Kenya-acquired case reported by the end of date D). A small background
term covers a Kenya-acquired case caused by a later, separate importation, which the
question also counts.

Stdlib only (CI runs Python 3.11 without numpy). Deterministic for a fixed seed: the
continuous variates are built from ``random.Random.random()`` rather than the interpreter's
gamma or normal samplers, and the two discrete picks use ``random.Random.choice`` and
``choices``; the whole run was checked to give identical output on Python 3.9 and 3.14.

Inputs divide into published values and judgement calls. The artifact's ``inputs.provenance``
map labels every input as one or the other, and the judgement calls are the ones the
sensitivity table moves.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import functools
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
FEATURES = REPO / "data" / "case-conditioned" / "reference-episode-features.json"
REFERENCE = REPO / "data" / "importation-reference-class.json"
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-09-forecast.json"

MODEL_VERSION = "kenya_conditioned-v1.0.0"
SEED = 20261009
N_DRAWS = 40_000
N_DRAWS_SENSITIVITY = 20_000
N_TIMING = 60_000

# Clock: days from 2026-10-03 00:00 East Africa Time (UTC+3).
T0 = dt.date(2026, 10, 3)
T_FLIGHT = 0.50            # arrival in Nairobi on 3 Oct, early afternoon (Kenya MoH)
T_HOSPITAL = 0.60          # taken directly to hospital on arrival and isolated (WHO DON619)
T_DEATH = 2.98             # night of 5 Oct (WHO DON619; Kenya MoH release says 6 Oct)
T_BURIAL = (2.98, 3.60)    # body handling to the safe burial on 6 Oct (WHO DON619)
T_COMM = (0.55, 1.00)      # arrival formalities, travel to the hospital, any contact before isolation
WINDOW_FIRST = dt.date(2026, 10, 8)
WINDOW_LAST = dt.date(2026, 11, 4)
REPORT_DELAY = 0.5         # confirmation to public report, days
CONDITIONING_DATE = dt.date(2026, 10, 8)   # last whole day with no Kenya-acquired case reported
EVIDENCE_THROUGH_UTC = "2026-10-09T11:30:00Z"
T_EVIDENCE = 6.604         # the evidence time on the model clock (9 Oct 14:30 East Africa Time)
IDX_OPEN, IDX_EVIDENCE, IDX_CURVE = 0, 1, 2   # column layout of the conditioning points


def day_end(date: dt.date) -> float:
    return float((date - T0).days + 1)


T_CONFIRM_END = day_end(WINDOW_LAST)            # confirmation must be <= end of 4 Nov
T_REPORT_END = T_CONFIRM_END + REPORT_DELAY     # equivalently, report <= this time
CURVE_DATES = [WINDOW_FIRST + dt.timedelta(days=i) for i in range((WINDOW_LAST - WINDOW_FIRST).days + 1)]
T_OPEN = day_end(WINDOW_FIRST - dt.timedelta(days=1)) + REPORT_DELAY   # nothing reported from before the window

# ---------------------------------------------------------------------------------------
# Priors, pre-registered in the plan before any run. Published values carry a citation key;
# the rest are judgement calls, labelled as such in PROVENANCE below.
# ---------------------------------------------------------------------------------------
CITATIONS = {
    "faye2015": "Faye O et al. Chains of transmission and control of Ebola virus disease in Conakry, Guinea, in 2014. Lancet Infect Dis 2015;15(3):320-6. doi:10.1016/S1473-3099(14)71075-8",
    "althaus2015": "Althaus CL. Ebola superspreading. Lancet Infect Dis 2015;15(5):507-8. doi:10.1016/S1473-3099(15)70135-0",
    "dean2016": "Dean NE et al. Transmissibility and pathogenicity of Ebola virus: household secondary attack rate. Clin Infect Dis 2016;62(10):1277-86. doi:10.1093/cid/ciw114",
    "bower2016": "Bower H et al. Exposure-specific and age-specific attack rates for Ebola virus disease, Sierra Leone. Emerg Infect Dis 2016;22(8):1403-11. doi:10.3201/eid2208.160163",
    "who_ert2014": "WHO Ebola Response Team. Ebola virus disease in West Africa, the first 9 months. N Engl J Med 2014;371:1481-95. doi:10.1056/NEJMoa1411100",
    "macneil2010": "MacNeil A et al. Proportion of deaths and clinical features in Bundibugyo Ebola virus infection, Uganda. Emerg Infect Dis 2010;16(12):1969-72. doi:10.3201/eid1612.100627",
    "chevalier2014": "Chevalier MS et al. Ebola virus disease cluster in the United States, Dallas County, Texas, 2014. MMWR 2014;63(46):1087-8",
    "shuaib2014": "Shuaib F et al. Ebola virus disease outbreak, Nigeria, July-September 2014. MMWR 2014;63(39):867-72",
    "kayiwa2026": "Kayiwa J et al. Early action review, cross-border Bundibugyo virus disease outbreak, Uganda, 2026. Emerg Infect Dis 2026;32(10):1543-50. doi:10.3201/eid3210.261411",
    "lovs_priors": "LOVS lovs/lovs_priors_bundibugyo.py BUNDIBUGYO_PRIORS_STAGE_TWO: incubation gamma(4, 0.6); back-projection R gamma(4, 2) truncated R>1; effective R gamma(4, 3)",
    "who_don619": "WHO Disease Outbreak News 2026-DON619, 8 Oct 2026: taken directly to hospital on arrival and isolated there; sample collected 5 Oct, positive the same day; died 5 Oct; a safe and dignified burial has been conducted; infection presumed while seeking care for another illness",
    "who_afro_2026_10_06": "WHO AFRO, 6 Oct 2026: road through Beni to Kampala 2 Oct; flight to Nairobi 3 Oct; quickly isolated; 28 contacts plus 27 flight passengers and crew",
    "kenya_moh_2026_10_07": "Kenya MoH, 7 Oct 2026: 57 contacts identified, 10 quarantined; 5 laboratories; 652,584 travellers screened; 4,971 health workers trained; 15 isolation units",
}

# Faye 2015, March 2014 (before infection control): median, 95% CI low, high.
SETTING_R = {"comm": (1.4, 0.9, 2.2), "hosp": (0.4, 0.1, 0.9), "fun": (0.5, 0.2, 1.0)}
FAYE_TOTAL_R = 2.3
LOVS_BACKPROJECTION_R = (4, 2.0, 1.0)     # Erlang shape 4, rate 2, truncated R > 1
LOVS_EFFECTIVE_R = (4, 3.0, 0.0)          # Erlang shape 4, rate 3, no truncation
K_PRIOR = (0.3, 0.6)                      # lognormal median, log-sd (Althaus 2015 anchor 0.18)
THETA_PRIOR = (1.0, 0.5)                  # lognormal median, log-sd
LEVELS = {"NONE": (0.8, 1.0), "STANDARD": (0.4, 0.8), "VHF": (0.05, 0.25), "HLIU": (0.0, 0.05)}
FUNERAL = {"TRADITIONAL": (0.5, 1.0), "SDB": (0.02, 0.15)}
INCUBATION = (4, 0.6)                     # LOVS Bundibugyo, Erlang shape 4, rate 0.6
LAG_LISTED_MEAN = 1.0                     # onset to confirmation, listed contacts, Erlang shape 2
LAG_UNLISTED_MEAN = 5.0                   # onset to confirmation, unlisted contacts, Erlang shape 2
GROWTH_RATES = (0.2, 0.4, 0.6)            # infectiousness growth toward death, per day

# Kenya case: precaution scenarios for the hospital stay and the course of illness.
SCENARIOS = {
    "S1": {"weight": 0.40, "f_pre": (0.0, 0.05),
           "label": "Higher protection: Ebola-grade precautions from the start of care, consistent with isolation on arrival"},
    "S2": {"weight": 0.40, "f_pre": (0.05, 0.35),
           "label": "Intermediate protection: a short early phase under standard precautions, then Ebola-grade precautions"},
    "S3": {"weight": 0.20, "f_pre": (0.45, 0.80),
           "label": "Lower protection: standard precautions for most of the stay, until laboratory confirmation on 5 October"},
}
COURSES = {
    "D1": {"weight": 0.85, "e_hosp": (0.75, 1.0),
           "label": "Typical course: infection acquired while seeking care for another illness (WHO DON619), so the late stage of illness fell in Kenya"},
    "D2": {"weight": 0.15, "e_hosp": (0.4, 0.8),
           "label": "The month-long illness was itself this disease, so part of the late stage fell in DRC facilities"},
}
KENYA = {
    "e_comm": (0.01, 0.10), "pre_level": "STANDARD", "post_level": "VHF", "p_death": 1.0,
    "e_fun": (1.0, 1.0), "funeral_level": "SDB",
    "flight_contacts": 27, "flight_attack_rate": (0.0, 0.002), "flight_kenya_attribution": 0.5,
    "unlisted_share": (0.05, 0.25),
}
BACKGROUND = {"imports_per_week": (0.02, 0.10), "p_local": (0.05, 0.30),
              "delay_mean": 16.0, "delay_shape": 10, "imports_from": 6.0}
CASE_EVIDENCE = [
    {"fact": "Illness of about a month and medical care in the DRC before travel; WHO presumes infection while seeking care for another illness; it is unclear if or when the patient was symptomatic during the travel",
     "source": "WHO 2026-DON619 (8 Oct); Kenya Ministry of Health (7 Oct)"},
    {"fact": "Road travel from the DRC through Beni to Kampala on 2 Oct, one night in Uganda, then a flight to Nairobi arriving 3 Oct",
     "source": "WHO AFRO (6 Oct); ministry statement relayed 6 Oct and 8 Oct"},
    {"fact": "Taken directly to hospital on arrival in Nairobi and isolated there",
     "source": "WHO 2026-DON619"},
    {"fact": "Care began in an emergency area before transfer to an isolation facility. This is a minister's statement relayed by the press, not a WHO or ministry release, and it is the basis for the intermediate scenario",
     "source": "Minister relayed by Citizen Digital (7 Oct)"},
    {"fact": "The treating hospital states that staff caring for the patient used full protective equipment under supervision; health workers were placed in quarantine although infection prevention measures were used",
     "source": "Hospital statement relayed by Capital FM (7 Oct); UN News (7 Oct)"},
    {"fact": "A sample collected on 5 Oct was positive the same day at two national laboratories; the ministry announced the case on 6 Oct",
     "source": "WHO 2026-DON619"},
    {"fact": "Death on 5 Oct; one ministry release gives 6 Oct, a conflict that does not move the forecast",
     "source": "WHO 2026-DON619; Kenya Ministry of Health (7 Oct)"},
    {"fact": "A safe and dignified burial has been conducted. WHO states the burial; the date of 6 Oct comes from press reports, not from WHO",
     "source": "WHO 2026-DON619 (the burial); press reports (the date)"},
    {"fact": "Contacts: 28 listed on 6 Oct, including family members and health workers, plus 27 flight passengers and crew; 57 identified with 10 in quarantine on 7 Oct; 66 accounted for on 8 Oct. The counts for health workers and family members differ between statements and are used only as an order-of-magnitude check",
     "source": "WHO AFRO (6 Oct); Kenya Ministry of Health (7 Oct); minister relayed by the press (7 and 8 Oct)"},
    {"fact": "No suspected, probable or confirmed case acquired in Kenya had been reported by the evidence time. One suspected case elsewhere in Kenya tested negative on preliminary testing and had onset in the DRC, so it would have been an importation, not a Kenya-acquired case",
     "source": "Freshness check of WHO Disease Outbreak News, WHO AFRO, Kenya Ministry of Health, ECDC, Africa CDC and press to the evidence time"},
    {"fact": "Response capacity as stated by the ministry: 652,584 travellers screened, 267 samples tested, five laboratories able to test, 4,971 health workers trained, 15 isolation and treatment units",
     "source": "Kenya Ministry of Health (7 Oct)"},
]
DISCLOSURES = [
    "The reference-episode features were coded by the forecast author after reading every outcome. They were committed before any validation score was computed (git history of data/case-conditioned/reference-episode-features.json), but the coding cannot be blind, so the validation scores are optimistic.",
    "Every prior, scenario weight and range was fixed in the plan before the first run. No parameter was changed after seeing model output.",
    "Changes made after an independent review saw the first run, each disclosed here and none of which moved the headline probability: the scenario table now reports a calibrated weighted median rather than a prior median and a share conditioned on no report; the validation now carries paired score differences with standard errors; five sensitivity variants were added (disputed-outcome episodes dropped from the calibration, an earlier start for the later-importation term, a multiple-index adjustment, a per-death funeral denominator, and a baseline row at the sensitivity sample size); an input provenance map was added; identifying and non-material case detail was removed; and the interval and window-open notes were corrected.",
    "The setting reproduction numbers come from Zaire ebolavirus in Conakry in 2014. No Bundibugyo setting-specific estimate exists, which is why a misfit factor, the reference-class reweighting and a sensitivity on the transmissibility scale are carried.",
    "The setting-R priors are lognormals centred on each published point estimate with a log standard deviation taken from the published interval treated as symmetric. This puts the prior means slightly above the published values (hospital 0.467 against 0.4, funeral 0.545 against 0.5) and the upper tail above the published upper bound. It was pre-registered and is kept; a lognormal fitted to the interval endpoints instead gives about 0.171 in place of 0.182.",
    "The 80% interval covers parameter and scenario uncertainty inside this model. It does not cover model-structure error, and both base rates (0.393 and 0.55) lie outside it.",
    "The calibration lifts the forecast from 0.144 (literature priors alone) to 0.182. That lift depends on three episodes whose YES is contestable: two 2014 and 2016 Liberian episodes coded YES on a reading of WHO wording, and the 2026 Kampala episode, which merges 15 imported cases into one unit. Dropping all three from the calibration gives about 0.145.",
    "The episode feature multi_import is recorded but does not enter the primary likelihood: the Kampala episode's YES arose from many imported cases but is scored as one index chain. This pushes the fitted transmissibility scale up. The multiple-index sensitivity row and the dropped-episode row bound the effect.",
    "The later-importation term counts arrivals from 9 October onward. An importation that arrived unnoticed in late September could still produce a confirmed Kenya-acquired case inside the window; starting the term on 25 September instead gives about 0.198.",
    "Faye's setting reproduction numbers are averages over all cases, including survivors, while the funeral term here multiplies by the probability of death. For a fatal case the funeral term is therefore understated; dividing by the reported case fatality instead is a sensitivity row. Under a safe burial the effect is small.",
    "The flight term overlaps slightly with the community term, which also covers arrival, and an infection acquired in flight might not be stated as acquired in Kenya. It contributes about 0.014 of an expected 0.32 cases, so it changes little either way.",
    "The validation set is the same 13 episodes that inform the calibration. Leave-one-out scores are reported for that reason. Only the difference against the pooled base rate is larger than its standard error; the model cannot be distinguished from the responder or traveller subgroup rate, or from the uncalibrated literature model.",
    "The headline conditions on no Kenya-acquired case reported through the end of 8 October, which is one whole reporting day behind the evidence time. Conditioning on the evidence time instead gives about 0.17, so the headline is the conservative, more easily verified of the two, and both are reported.",
]
PROVENANCE = {
    "setting_R_faye2015": "source: faye2015",
    "faye_total_R": "source: faye2015",
    "lovs_backprojection_R": "source: lovs_priors (WHO 2014 and Wamala 2010 bridged; BDBV early R is a literature gap)",
    "lovs_effective_R": "source: lovs_priors",
    "k_prior_lognormal_median_logsd": "judgement anchored on althaus2015 (k 0.18 marginal over mixed settings); the median is raised to 0.3 because conditioning on known settings should leave less heterogeneity. Sensitivity rows carry k = 0.18 and the Poisson limit",
    "theta_prior_lognormal_median_logsd": "judgement: an uninformative misfit factor for transferring Zaire-species setting values to this case",
    "precaution_levels": "judgement informed by dean2016 (nursing care 47.9% against no direct contact 0.8%), chevalier2014 and shuaib2014; no published multiplier for residual transmission by precaution level exists",
    "funeral_levels": "judgement informed by bower2016 (83% for touching a corpse) and the effect of safe burial programmes",
    "incubation_erlang": "source: lovs_priors and macneil2010",
    "lag_listed_mean": "judgement: contacts under daily follow-up are sampled quickly; a 2-day variant is a sensitivity row",
    "lag_unlisted_mean": "judgement: who_ert2014 gives a mean of 5.0 days from onset to hospitalisation for unmonitored patients",
    "growth_rates": "judgement: infectiousness rises toward death; the three rates are averaged over",
    "scenarios": "judgement: the precaution scenarios and their weights, which the per-scenario rows and the weight variants bound",
    "courses": "judgement: the two readings of the month-long illness and their weights",
    "kenya": "case evidence for the exposure shares and contact counts (see case_evidence); the per-contact flight attack rate and the unlisted share are judgement",
    "background": "judgement: there is no published rate of further importations into Kenya; the ranges are wide and the term is reported separately in every curve row",
    "clock": "source: who_don619, who_afro_2026_10_06 and kenya_moh_2026_10_07",
    "citations": "the published sources themselves",
    "provenance": "this map",
}
# Episodes whose YES is contestable: the two Liberian episodes rest on a reading of WHO wording
# (recorded in the reference class's own uncertainties) and Kampala merges 15 imported cases.
DISPUTED_YES = ("lbr-2014-03-lofa", "lbr-2016-03-monrovia", "uga-2026-05-kampala")
MULTI_INDEX_EFFECTIVE = 3.0   # effective index chains for a merged, multiple-importation episode
CONAKRY_CFR = 0.708           # who_ert2014: case fatality among cases with known outcome


def _jeffreys(yes: int, n: int) -> float:
    return (yes + 0.5) / (n + 1)


def benchmarks(episodes: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """Base rates derived from the frozen reference class rather than hard-coded."""
    eps = list(episodes if episodes is not None else load_features())
    travellers = [e for e in eps if not e["responder"]]
    responders = [e for e in eps if e["responder"]]
    return {"block11_pooled": _jeffreys(sum(1 for e in eps if e["outcome"]), len(eps)),
            "traveller_subgroup": _jeffreys(sum(1 for e in travellers if e["outcome"]), len(travellers)),
            "responder_subgroup": _jeffreys(sum(1 for e in responders if e["outcome"]), len(responders))}


# ---------------------------------------------------------------------------------------
# Random variates: continuous ones from rng.random() so they are stable across interpreters.
# ---------------------------------------------------------------------------------------
def _uniform(rng: random.Random, lo_hi: Sequence[float]) -> float:
    lo, hi = lo_hi
    return lo + (hi - lo) * rng.random()


def _normal(rng: random.Random) -> float:
    u1 = 1.0 - rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * rng.random())


def _lognormal(rng: random.Random, median: float, log_sd: float) -> float:
    return median * math.exp(log_sd * _normal(rng))


def _erlang(rng: random.Random, shape: int, rate: float) -> float:
    return -sum(math.log(1.0 - rng.random()) for _ in range(shape)) / rate


def _ci_lognormal(rng: random.Random, spec: tuple[float, float, float]) -> float:
    median, lo, hi = spec
    return _lognormal(rng, median, (math.log(hi) - math.log(lo)) / (2 * 1.96))


def _scale_rho(rng: random.Random, r_prior: tuple[int, float, float]) -> float:
    shape, rate, minimum = r_prior
    while True:
        r = _erlang(rng, shape, rate)
        if r > minimum:
            return r / FAYE_TOTAL_R


def nb_none(m: float, k: float, f: float = 1.0) -> float:
    """Probability that none of NegBin(mean m, dispersion k) cases fall within a fraction f."""
    if m <= 0.0 or f <= 0.0:
        return 1.0
    return (1.0 + m * f / k) ** (-k)


# ---------------------------------------------------------------------------------------
# Shared parameters and the expected number of secondary cases.
# ---------------------------------------------------------------------------------------
def draw_shared(rng: random.Random, opts: Mapping[str, Any]) -> dict:
    p = {s: _ci_lognormal(rng, SETTING_R[s]) for s in ("comm", "hosp", "fun")}
    p["rho"] = _scale_rho(rng, opts.get("r_prior", LOVS_BACKPROJECTION_R))
    p["k"] = opts["k_fixed"] if opts.get("k_fixed") else _lognormal(rng, *K_PRIOR)
    p["theta"] = 1.0 if opts.get("theta_fixed") else _lognormal(rng, *THETA_PRIOR)
    p["levels"] = {name: _uniform(rng, rng_range) for name, rng_range in LEVELS.items()}
    p["funeral"] = {name: _uniform(rng, rng_range) for name, rng_range in FUNERAL.items()}
    if opts.get("funeral_per_death"):
        # Faye's funeral value averages over all cases, survivors included; for a fatal case
        # the per-death value is higher by the reciprocal of the case fatality.
        p["fun"] /= CONAKRY_CFR
    return p


def expected_cases(p: Mapping[str, Any], e_comm: float, e_hosp: float, f_pre: float,
                   pre: str, post: str, p_death: float, e_fun: float, funeral: str) -> dict:
    scale = p["theta"] * p["rho"]
    hosp_mult = f_pre * p["levels"][pre] + (1.0 - f_pre) * p["levels"][post]
    return {"comm": scale * p["comm"] * e_comm,
            "hosp": scale * p["hosp"] * e_hosp * hosp_mult,
            "fun": scale * p["fun"] * p_death * e_fun * p["funeral"][funeral]}


def episode_probability(rng: random.Random, p: Mapping[str, Any], ep: Mapping[str, Any],
                        multi_index: bool = False) -> float:
    parts = expected_cases(p, _uniform(rng, ep["e_comm"]), _uniform(rng, ep["e_hosp"]),
                           _uniform(rng, ep["f_pre"]), ep["pre_level"], ep["post_level"],
                           ep["p_death"], _uniform(rng, ep["e_fun"]), ep["funeral_level"])
    none = nb_none(sum(parts.values()), p["k"])
    if multi_index and ep.get("multi_import"):
        # A merged episode held several imported index cases, so its chance of at least one
        # locally acquired case is that of several independent chains.
        none = none ** MULTI_INDEX_EFFECTIVE
    return 1.0 - none


# ---------------------------------------------------------------------------------------
# Timing: CDF of the public-report time of a secondary case, by exposure setting.
# ---------------------------------------------------------------------------------------
def _hospital_exposure(rng: random.Random, g: float) -> float:
    # density proportional to exp(g * (t - T_DEATH)) on [T_HOSPITAL, T_DEATH], by inversion
    span = T_DEATH - T_HOSPITAL
    u = rng.random()
    return T_DEATH + math.log(u + (1.0 - u) * math.exp(-g * span)) / g


@functools.lru_cache(maxsize=8)
def timing_tables(seed: int, n: int = N_TIMING, listed_mean: float = LAG_LISTED_MEAN,
                  unlisted_mean: float = LAG_UNLISTED_MEAN) -> dict:
    """Sorted samples of report times for each (setting, growth, listed/unlisted) combination."""
    rng = random.Random(seed + 1)
    out: dict[tuple, list[float]] = {}
    exposures = {("hosp", g): (lambda r, g=g: _hospital_exposure(r, g)) for g in GROWTH_RATES}
    exposures[("comm", None)] = lambda r: _uniform(r, T_COMM)
    exposures[("fun", None)] = lambda r: _uniform(r, T_BURIAL)
    exposures[("flight", None)] = lambda r: T_FLIGHT
    for key, exposure in exposures.items():
        for listed, mean in (("listed", listed_mean), ("unlisted", unlisted_mean)):
            samples = [exposure(rng) + _erlang(rng, *INCUBATION) + _erlang(rng, 2, 2.0 / mean)
                       + REPORT_DELAY for _ in range(n)]
            samples.sort()
            out[key + (listed,)] = samples
    return out


def _cdf(samples: Sequence[float], t: float) -> float:
    return bisect.bisect_right(samples, t) / len(samples)


def cdf_points(tables: Mapping[tuple, Sequence[float]], points: Sequence[float]) -> dict:
    return {key: [_cdf(s, t) for t in points] for key, s in tables.items()}


@functools.lru_cache(maxsize=2)
def _background_tables(seed: int, n: int = N_TIMING) -> tuple[float, ...]:
    rng = random.Random(seed + 2)
    delays = sorted(_erlang(rng, BACKGROUND["delay_shape"],
                            BACKGROUND["delay_shape"] / BACKGROUND["delay_mean"]) for _ in range(n))
    return tuple(delays)


def _background_intensity(delays: Sequence[float], t: float,
                          imports_from: float | None = None) -> float:
    """Integral over arrival a in [imports_from, T_CONFIRM_END] of P(a + delay <= t), per unit rate."""
    lo = BACKGROUND["imports_from"] if imports_from is None else imports_from
    hi = T_CONFIRM_END
    steps = 54
    h = (hi - lo) / steps
    total = 0.0
    for i in range(steps):
        a = lo + (i + 0.5) * h
        total += _cdf(delays, t - a) * h
    return total


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def load_features(path: Path = FEATURES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["episodes"]


def _weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def run(opts: Mapping[str, Any] | None = None, n: int = N_DRAWS, seed: int = SEED,
        episodes: Sequence[Mapping[str, Any]] | None = None) -> dict:
    opts = dict(opts or {})
    episodes = list(episodes if episodes is not None else load_features())
    scen_weights = opts.get("scenario_weights", {s: v["weight"] for s, v in SCENARIOS.items()})
    course_weights = opts.get("course_weights", {c: v["weight"] for c, v in COURSES.items()})
    calibrate = opts.get("calibrate", True)
    calib_ids = set(opts.get("calibration_ids", [e["episode_id"] for e in episodes]))
    e_hosp_mult = opts.get("e_hosp_mult", 1.0)
    e_comm_mult = opts.get("e_comm_mult", 1.0)
    unlisted = opts.get("unlisted_share", KENYA["unlisted_share"])
    bg_on = opts.get("background", True)
    bg_imports = opts.get("imports_per_week", BACKGROUND["imports_per_week"])
    bg_plocal = opts.get("p_local", BACKGROUND["p_local"])
    bg_from = opts.get("imports_from", BACKGROUND["imports_from"])
    multi_index = opts.get("multi_index", False)
    funeral_per_death = opts.get("funeral_per_death", False)

    points = [T_OPEN, T_EVIDENCE] + [day_end(d) for d in CURVE_DATES] + [T_REPORT_END]
    tables = timing_tables(seed, listed_mean=opts.get("lag_listed_mean", LAG_LISTED_MEAN))
    cdfs = cdf_points(tables, points)
    delays = _background_tables(seed)
    bg_unit = [_background_intensity(delays, t, bg_from) for t in points]

    rng = random.Random(seed)
    s_names, s_w = list(scen_weights), [scen_weights[s] for s in scen_weights]
    c_names, c_w = list(course_weights), [course_weights[c] for c in course_weights]
    draws = []
    for _ in range(n):
        p = draw_shared(rng, opts)
        loglik, own = 0.0, []
        for ep in episodes:
            prob = min(max(episode_probability(rng, p, ep, multi_index), 1e-12), 1.0 - 1e-12)
            ll = math.log(prob if ep["outcome"] else 1.0 - prob)
            own.append((prob, ll))
            if ep["episode_id"] in calib_ids:
                loglik += ll
        scen = rng.choices(s_names, s_w)[0]
        course = rng.choices(c_names, c_w)[0]
        f_pre = _uniform(rng, SCENARIOS[scen]["f_pre"])
        parts = expected_cases(p, e_comm_mult * _uniform(rng, KENYA["e_comm"]),
                               min(1.0, e_hosp_mult * _uniform(rng, COURSES[course]["e_hosp"])), f_pre,
                               KENYA["pre_level"], KENYA["post_level"], KENYA["p_death"],
                               _uniform(rng, KENYA["e_fun"]), KENYA["funeral_level"])
        parts["flight"] = (KENYA["flight_contacts"] * _uniform(rng, KENYA["flight_attack_rate"])
                           * KENYA["flight_kenya_attribution"])
        m = sum(parts.values())
        g = rng.choice(GROWTH_RATES)
        u = _uniform(rng, unlisted)
        lam = _uniform(rng, bg_imports) / 7.0 * _uniform(rng, bg_plocal) if bg_on else 0.0
        fcurve = []
        for i in range(len(points)):
            f = 0.0
            for setting, part in parts.items():
                key = (setting, g if setting == "hosp" else None)
                f += (part / m if m > 0 else 0.0) * ((1 - u) * cdfs[key + ("listed",)][i]
                                                     + u * cdfs[key + ("unlisted",)][i])
            fcurve.append(f)
        draws.append({"loglik": loglik, "own": own, "scenario": scen, "course": course,
                      "m": m, "parts": parts, "k": p["k"], "f": fcurve, "lam": lam})

    max_ll = max(d["loglik"] for d in draws)
    for d in draws:
        d["w"] = math.exp(d["loglik"] - max_ll) if calibrate else 1.0
    return summarise(draws, episodes, points, bg_unit, calibrate, full=opts.get("full", True),
                     calib_ids=frozenset(calib_ids))


def _conditional(draws: Sequence[Mapping[str, Any]], points: Sequence[float], bg_unit: Sequence[float],
                 i: int, which: str = "both") -> tuple[float, list[float], list[float]]:
    """P(YES | nothing reported by points[i]); also per-draw values and their weights."""
    last = len(points) - 1
    num = den = 0.0
    vals, wts = [], []
    for d in draws:
        a_d = nb_none(d["m"], d["k"], d["f"][i]) if which != "background" else 1.0
        a_e = nb_none(d["m"], d["k"], d["f"][last]) if which != "background" else 1.0
        b_d = math.exp(-d["lam"] * bg_unit[i]) if which != "index" else 1.0
        b_e = math.exp(-d["lam"] * bg_unit[last]) if which != "index" else 1.0
        w = d["w"] * a_d * b_d
        num += d["w"] * a_e * b_e
        den += w
        vals.append(1.0 - (a_e * b_e) / (a_d * b_d))
        wts.append(w)
    return 1.0 - num / den, vals, wts


def summarise(draws: Sequence[Mapping[str, Any]], episodes: Sequence[Mapping[str, Any]],
              points: Sequence[float], bg_unit: Sequence[float], calibrate: bool, full: bool = True,
              calib_ids: frozenset[str] | None = None) -> dict:
    """Posterior summaries. Point index 0 is the window opening, 1..28 the curve dates, last the end."""
    calib_ids = calib_ids if calib_ids is not None else frozenset(e["episode_id"] for e in episodes)
    wsum = sum(d["w"] for d in draws)
    ess = wsum ** 2 / sum(d["w"] ** 2 for d in draws)
    curve = []
    for i, date in enumerate(CURVE_DATES, start=IDX_CURVE):
        p, vals, wts = _conditional(draws, points, bg_unit, i)
        row = {"date": date.isoformat(), "p": p}
        if full:
            row.update({"p10": _weighted_quantile(vals, wts, 0.10), "p90": _weighted_quantile(vals, wts, 0.90),
                        "index_only": _conditional(draws, points, bg_unit, i, "index")[0],
                        "background_only": _conditional(draws, points, bg_unit, i, "background")[0]})
        curve.append(row)
    head = next(c for c in curve if c["date"] == CONDITIONING_DATE.isoformat())
    m_mean = sum(d["w"] * d["m"] for d in draws) / wsum
    out = {"headline": head, "curve": curve, "effective_sample_size": ess, "n_draws": len(draws),
           "window_open": _conditional(draws, points, bg_unit, IDX_OPEN)[0],
           "at_evidence_time": _conditional(draws, points, bg_unit, IDX_EVIDENCE)[0],
           "expected_secondary_cases": {"mean_m": m_mean}}
    if not full:
        return out

    i0 = CURVE_DATES.index(CONDITIONING_DATE) + IDX_CURVE
    cond_total = sum(d["w"] * nb_none(d["m"], d["k"], d["f"][i0]) * math.exp(-d["lam"] * bg_unit[i0])
                     for d in draws)
    cond_total = sum(d["w"] * nb_none(d["m"], d["k"], d["f"][i0]) * math.exp(-d["lam"] * bg_unit[i0])
                     for d in draws)
    scen_rows = []
    for s in SCENARIOS:
        for c in COURSES:
            cell = [d for d in draws if d["scenario"] == s and d["course"] == c]
            if not cell:
                continue
            cond_w = [d["w"] * nb_none(d["m"], d["k"], d["f"][i0]) * math.exp(-d["lam"] * bg_unit[i0])
                      for d in cell]
            scen_rows.append({
                "scenario": s, "course": c,
                "prior_weight": SCENARIOS[s]["weight"] * COURSES[c]["weight"],
                "share_given_no_report": sum(cond_w) / cond_total,
                "p_window_open": _conditional(cell, points, bg_unit, IDX_OPEN)[0],
                "p_conditioning_date": _conditional(cell, points, bg_unit, i0)[0],
                "median_m_calibrated": _weighted_quantile([d["m"] for d in cell],
                                                          [d["w"] for d in cell], 0.5),
                "label": SCENARIOS[s]["label"] + "; " + COURSES[c]["label"]})
    by_setting = {key: sum(d["w"] * d["parts"][key] for d in draws) / wsum
                  for key in ("hosp", "comm", "fun", "flight")}
    log_k = sum(d["w"] * math.log(d["k"]) for d in draws) / wsum
    p_any = sum(d["w"] * (1.0 - nb_none(d["m"], d["k"])) for d in draws) / wsum
    out["expected_secondary_cases"] = {"mean_m": m_mean, "by_setting": by_setting,
                                       "k_geometric_mean": math.exp(log_k),
                                       "p_any_secondary_case_ever": p_any}
    out["scenarios"] = scen_rows
    out["scenarios_note"] = ("prior_weight is the registered weight. share_given_no_report is that "
                             "cell's weight once no Kenya-acquired case has been reported by the "
                             "conditioning date; the scenarios do not enter the reference-class "
                             "likelihood, so the two differ only through that conditioning. "
                             "median_m_calibrated is the calibrated median expected number of "
                             "secondary cases in the cell.")
    out["scenarios_note"] = ("prior_weight is the registered weight; share_given_no_report is the "
                             "weight of that cell once no Kenya-acquired case has been reported by "
                             "the conditioning date. The scenarios do not enter the reference-class "
                             "likelihood, so the two differ only through the conditioning. "
                             "median_m_calibrated is the calibrated median expected number of "
                             "secondary cases in that cell.")

    # Validation: literature-only and calibrated leave-one-out predictions per episode.
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
        brier = sum((r[key] - r["outcome"]) ** 2 for r in rows) / n_ep
        logs = sum(math.log(r[key] if r["outcome"] else 1.0 - r[key]) for r in rows) / n_ep
        return {"brier": brier, "log_score": logs,
                "mean_predicted": sum(r[key] for r in rows) / n_ep}

    keys = ("calibrated_loo", "literature_only", "base_rate_loo", "subgroup_loo", "calibrated_in_sample")
    travellers = [r for r in rows if not r["responder"]]

    def paired(key: str, against: str) -> dict:
        diffs = [(r[key] - r["outcome"]) ** 2 - (r[against] - r["outcome"]) ** 2 for r in rows]
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
        se = math.sqrt(var / len(diffs))
        return {"mean_brier_difference": mean, "standard_error": se,
                "distinguishable": abs(mean) > 2 * se}

    out["validation"] = {
        "episodes": rows, "observed_rate": yes_total / n_ep,
        "scores": {k: scores(k) for k in keys},
        "travellers_only_brier": {k: sum((r[k] - r["outcome"]) ** 2 for r in travellers) / len(travellers)
                                  for k in keys},
        "paired_differences_vs_calibrated": {k: paired("calibrated_loo", k)
                                             for k in ("base_rate_loo", "subgroup_loo", "literature_only")},
        "note": ("Negative mean_brier_difference favours the calibrated model. A difference smaller "
                 "than twice its standard error is not evidence of a better forecaster: with 13 "
                 "episodes only the comparison against the pooled base rate clears that bar. The "
                 "episode features were coded by the author, who knew the outcomes, so even the "
                 "leave-one-out scores are optimistic."),
    }
    return out


# ---------------------------------------------------------------------------------------
# Sensitivity and artifact.
# ---------------------------------------------------------------------------------------
SENSITIVITY = [
    ("literature_only", "No reweighting by the reference class (literature priors only)", {"calibrate": False}),
    ("k_0.18", "Dispersion fixed at Althaus 2015 k = 0.18", {"k_fixed": 0.18}),
    ("poisson", "No overdispersion (k = 1e6)", {"k_fixed": 1e6}),
    ("rho_effective_R", "Transmissibility from the LOVS effective-R-under-response prior gamma(4, 3)", {"r_prior": LOVS_EFFECTIVE_R}),
    ("theta_fixed", "No misfit factor (theta = 1)", {"theta_fixed": True}),
    ("only_S1", "Precaution scenario S1 only (PPE from arrival)", {"scenario_weights": {"S1": 1.0}}),
    ("only_S2", "Precaution scenario S2 only (emergency-room phase)", {"scenario_weights": {"S2": 1.0}}),
    ("only_S3", "Precaution scenario S3 only (standard precautions until 5 Oct)", {"scenario_weights": {"S3": 1.0}}),
    ("weights_optimistic", "Scenario weights 0.6 / 0.3 / 0.1", {"scenario_weights": {"S1": 0.6, "S2": 0.3, "S3": 0.1}}),
    ("weights_pessimistic", "Scenario weights 0.2 / 0.4 / 0.4", {"scenario_weights": {"S1": 0.2, "S2": 0.4, "S3": 0.4}}),
    ("only_D2", "The month-long illness was Bundibugyo (course D2 only)", {"course_weights": {"D2": 1.0}}),
    ("hosp_share_half", "Half the hospital-phase share in Kenya", {"e_hosp_mult": 0.5}),
    ("comm_share_double", "Double the community share in Kenya", {"e_comm_mult": 2.0}),
    ("lag_2d", "Listed contacts: onset to confirmation 2 days on average", {"lag_listed_mean": 2.0}),
    ("unlisted_high", "Unlisted share of transmission 25 to 50 percent", {"unlisted_share": (0.25, 0.50)}),
    ("no_background", "No later-importation background term", {"background": False}),
    ("background_high", "Background: 0.1 to 0.3 importations per week, 0.2 to 0.5 local", {"imports_per_week": (0.10, 0.30), "p_local": (0.20, 0.50)}),
    ("drop_disputed_yes", "Calibrate without the three episodes whose YES is contestable (two Liberian episodes read from WHO wording, and Kampala, which merges 15 importations)",
     {"calibration_ids": "exclude_disputed"}),
    ("multi_index_merged_episodes", f"Score a merged, multiple-importation episode as {MULTI_INDEX_EFFECTIVE:g} index chains rather than one",
     {"multi_index": True}),
    ("background_from_25_sept", "The later-importation term starts on 25 September, so an unnoticed late-September arrival can still report inside the window",
     {"imports_from": -8.0}),
    ("funeral_per_death", "Funeral transmission taken per death (divided by the reported case fatality) rather than per case",
     {"funeral_per_death": True}),
    ("baseline_at_sensitivity_sample", "The primary model at the sensitivity sample size, for a like-with-like comparison", {}),
]
SENSITIVITY_DATES = ("2026-10-08", "2026-10-11", "2026-10-15", "2026-10-22")


def _rounded(obj: Any, nd: int = 4) -> Any:
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _rounded(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_rounded(v, nd) for v in obj]
    return obj


def payload_digest(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_artifact(main: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "kenya-bdbv-2026-10-09-case-conditioned",
        "model_version": MODEL_VERSION,
        "question": ("Will Kenya report at least one laboratory-confirmed case that the reporting authority "
                     "states was acquired in Kenya, confirmed 2026-10-08 to 2026-11-04, reported by 2026-11-07? "
                     "The imported case confirmed on 2026-10-06 does not count; a new imported case does not count; "
                     "a Kenya-acquired case caused by a later importation does count. Same question as Block 11 "
                     "(block11:kenya-further-case)."),
        "relationship_to_block11": ("Additive. Block 11 (0.393) is a fixed-method benchmark and stays frozen and "
                                    "resolved as registered; this forecast does not modify it."),
        "evidence_through_utc": EVIDENCE_THROUGH_UTC,
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {"probability": main["headline"]["p"],
                     "interval_80": [main["headline"]["p10"], main["headline"]["p90"]],
                     "index_linked": main["headline"]["index_only"],
                     "background_later_importation": main["headline"]["background_only"],
                     "at_window_open": main["window_open"],
                     "at_window_open_note": ("Conditioned only on no Kenya-acquired case confirmed before "
                                             "2026-10-08, that is no report by midday on 8 October, which is "
                                             "the void rule in the Block 11 runbook."),
                     "at_evidence_time": main["at_evidence_time"],
                     "at_evidence_time_note": ("Conditioned on the evidence time itself, "
                                               + EVIDENCE_THROUGH_UTC + ". The headline conditions on the end "
                                               "of the previous whole reporting day instead, so it is the "
                                               "higher and the more easily verified of the two."),
                     "interval_note": ("Weighted 10th to 90th percentile across draws. It covers parameter "
                                       "and scenario uncertainty inside this model, not model-structure error, "
                                       "and it is not a worst case. Both base rates lie outside it."),
                     "benchmarks": benchmarks()},
        "curve": main["curve"],
        "curve_note": ("Each row: probability of YES given that no Kenya-acquired case has been reported by the end "
                       "of that date (East Africa Time). The value for 2026-10-08 is the current forecast."),
        "scenarios": main["scenarios"],
        "scenarios_note": main["scenarios_note"],
        "expected_secondary_cases": main["expected_secondary_cases"],
        "validation": main["validation"],
        "effective_sample_size": main["effective_sample_size"],
        "sensitivity": list(sensitivity),
        "scoring_plan": {
            "scored_value": ("the curve value for the date this forecast is first shown outside the "
                             "programme, stated with that date; the artifact hash fixes it in advance"),
            "scored_against": ["the Block 11 pooled benchmark 0.393", "the traveller subgroup rate 0.55"],
            "resolution": ("the Block 11 runbook and lovs/forecast/opsresolver.py: an authority statement "
                           "of acquisition in Kenya, confirmed 2026-10-08 to 2026-11-04, reported by "
                           "2026-11-07, with a coverage review naming its sources"),
            "excluded": ["suspected and probable cases", "a retracted report",
                         "a further imported case", "a case with no stated place of acquisition"],
            "ambiguity": "an ambiguous case is decided by someone other than the author of this forecast",
            "if_a_case_occurs": ("score it in public as a miss, with the interval and the drivers as they "
                                 "were stated; say nothing about where or from whom that case was acquired"),
            "if_no_case_occurs": ("score it in public, and state that one outcome cannot separate 18 per cent "
                                  "from 10 or 39 per cent; draw no conclusion about Kenya's response"),
        },
        "inputs": {
            "provenance": PROVENANCE,
            "setting_R_faye2015": SETTING_R, "faye_total_R": FAYE_TOTAL_R,
            "lovs_backprojection_R": LOVS_BACKPROJECTION_R, "lovs_effective_R": LOVS_EFFECTIVE_R,
            "k_prior_lognormal_median_logsd": K_PRIOR, "theta_prior_lognormal_median_logsd": THETA_PRIOR,
            "precaution_levels": LEVELS, "funeral_levels": FUNERAL, "incubation_erlang": INCUBATION,
            "lag_listed_mean": LAG_LISTED_MEAN, "lag_unlisted_mean": LAG_UNLISTED_MEAN,
            "growth_rates": GROWTH_RATES, "scenarios": SCENARIOS, "courses": COURSES, "kenya": KENYA,
            "background": BACKGROUND,
            "clock": {"t0": "2026-10-03T00:00+03:00", "flight": T_FLIGHT, "hospital": T_HOSPITAL,
                      "death": T_DEATH, "burial": T_BURIAL, "community": T_COMM, "report_delay": REPORT_DELAY},
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": DISCLOSURES,
        "uganda_transit": {"forecast": None,
                           "reason": ("Not forecast. Uganda's exposures (road travel from Beni, one night in Kampala, "
                                      "the drive to Entebbe, the airport) are real, but contact numbers, travel "
                                      "companions and symptom status in Uganda are unpublished; Uganda's ministry "
                                      "reported normal temperature at departure and no linked case as of 7 October.")},
        "method": {"seed": SEED, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS_SENSITIVITY,
                   "n_timing_samples": N_TIMING, "code": "lovs/forecast/kenya_conditioned.py",
                   "episode_features": "data/case-conditioned/reference-episode-features.json"},
    }
    payload = _rounded(payload)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


def sensitivity_rows(n: int = N_DRAWS_SENSITIVITY) -> list[dict]:
    rows = []
    for name, label, opts in SENSITIVITY:
        opts = dict(opts)
        if opts.get("calibration_ids") == "exclude_disputed":
            opts["calibration_ids"] = [e["episode_id"] for e in load_features()
                                       if e["episode_id"] not in DISPUTED_YES]
        res = run({**opts, "full": False}, n=n)
        by_date = {c["date"]: c["p"] for c in res["curve"]}
        rows.append({"variant": name, "label": label,
                     **{f"p_{d}": by_date[d] for d in SENSITIVITY_DATES},
                     "p_at_evidence_time": res["at_evidence_time"],
                     "mean_m": res["expected_secondary_cases"]["mean_m"]})
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
    print(json.dumps({"out": str(args.out.name), "probability": artifact["headline"]["probability"],
                      "interval_80": artifact["headline"]["interval_80"],
                      "payload_sha256": artifact["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
