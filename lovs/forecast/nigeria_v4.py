# SPDX-License-Identifier: Apache-2.0
"""Nigeria v4: the further-case forecast rebuilt from first principles as a contact model.

Question (unchanged from v1 to v3): will Nigeria report at least one further laboratory-confirmed case
of this outbreak, beyond the first case confirmed on 2026-10-20, that is not a new importation, with
confirmation dated 2026-10-21 to 2026-11-17 and first reported by an authority by 2026-11-20? Only
reports first published after the v4 registration entry count.

Why v4 exists. v3 reached its value mainly through two judgements: the case's infectiousness relative
to a dying, late-stage patient, and zero-transmission care days from other imports diluting the
facility rate. The first case had about 13 days of symptoms before isolation, about 9 of them in
unsuspected facility care, and 44 listed contacts. v4 models those contacts one by one: who was
exposed, for how many days, at what point in the illness, and with what per-day hazard, calibrated on
this outbreak's two seeding events (Kenya and Uganda) and on older studies only where the outbreak is
silent. v1, v2 and v3 stay registered, are published and are scored as registered.

Model, in one paragraph. The clock is days from 2026-10-07 00:00 WAT; onset is day 0. The case was at
home until admission S (day 4 with probability 0.75, otherwise 5 to 9) and in unprotected facility
care until T (12.5 to 14.0). Each contact's infection probability is 1 - exp(-lambda_g X), where X is
its presence-weighted exposure in reference days (one reference day is a day with a fatal index in
its last three days, the state of both anchors). Infectiousness is psi r times the registered survivor
shape (time-point reading), or, if the case later dies, psi r_fatal times the fatal shape held at its
peak; psi is a per-case gamma multiplier. Groups: listed health workers, the case's own caregivers,
listed other patients and their caregivers, unlisted facility staff, other unlisted contacts, and, if
the case dies, terminal-care staff. Travel contacts carry no hazard (no symptoms during travel).
Infected contacts pass through the registered incubation prior and detection (listed: confirmed about
a day after onset or listing; unlisted: later, and not always). Unlisted cases and listed cases ill
before listing seed a second generation. The v2 later-importation term is added. Runs in which an
ill listed contact would already have been reported at the registration check are excluded.

Stdlib only and deterministic for a fixed seed: every variate is built from random.Random.random(), through
the helpers of the frozen modules (imported and never changed) or three small local helpers (integer and
log-uniform draws and a choice). Nothing here asserts
that anyone is infected; every count is expected under this model.
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
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs import lovs_priors_bundibugyo as priors
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import kenya_escape as ke
from lovs.forecast import nigeria_conditioned as nc
from lovs.forecast import nigeria_nowcast as nn
from lovs.forecast import nigeria_v2 as nv2

REPO = kc.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "nigeria-2026-10-21-v4.json"
MODULE_PATH = Path(__file__).resolve()
MODEL_VERSION = "nigeria_v4-v1.0.0"
SEED = 2026102104
DIAGNOSTIC_SEED = SEED + 1            # diagnostics draw their own variates so the main stream is untouched
N_DRAWS = 40_000
N_DRAWS_SENSITIVITY = 20_000
RUNS_PER_DRAW = 4

REGISTRATION = {
    "rules_v4_sha256": "82ffd6424f9f2d2f6a05b5eead54ce8aa950ff529924b991f38c90213e3e8a62",
    "validation_sha256": "b2aad8e5821af878fa3a9eb6a40f96e42f97c15d564ff19b6193602530cf9280",
    "red_team_of_v4_rules_sha256": "4b18828e1bfaec9b306fc502c4498fe0d0cb09b1588e63a1657ad23d7e720d91",
    "source_checks_sha256": "3519c1dd9a620fe57f842c271778dbe432ab94194e8a06d8cd009c7fca719797",
    "wiki_page_id": nv2.REGISTRATION["wiki_page_id"],
    "entry_6_registered_utc": "2026-10-21T03:23:27Z",
    "registration_check_completed_utc": "2026-10-21T03:22:35Z",
    "note": ("The v4 rules were fixed after an independent red team written blind to every earlier Nigeria output, and "
             "before any v4 code existed; their sha256 was recorded on the wiki page at the time given. The rules, "
             "validation table and evidence extractions are internal; every rule is restated in this artifact's "
             "inputs."),
}
# The first full v4 run, as recorded on the precommitment page's Entry 7 before any number was read.
ENTRY_7: dict[str, str] | None = {
    "run_started_utc": "2026-10-21T03:33:52Z",
    "run_completed_utc": "2026-10-21T03:34:43Z",
    "entry_7_recorded_utc": "2026-10-21T03:35:47Z",
    "file_sha256": "a7460bd3a3b8bda5ed4188a0e3b91f3bc7f6806e43143e11ada9284d7d1cf208",
    "payload_sha256": "0feee1a57924a8a44abc1322480a7bf25b5ccc401a3ff216c50a121f6e5e39a3",
    "results_sha256": "cf2106f327c444c79df72ed59c598965dbd45758e291ee3ca1c1a55ecae22d47",
    "module_sha256": "bcd24537cbde8ad3ae7083d2611f265a49991a97cf4e84aab52f16fa1359fbe2",
}

# ---------------------------------------------------------------------------------------
# Question and clock (imported from the registered v1 module, never copied).
# ---------------------------------------------------------------------------------------
day_end = nc.day_end
T_WINDOW_OPEN = nc.T_WINDOW_OPEN      # 14.0: confirmations after the end of 20 October count
T_CONFIRM_END = nc.T_CONFIRM_END      # 42.0: ... up to the end of 17 November
T_REPORT_END = nc.T_REPORT_END        # 45.0: first report by the end of 20 November
CONDITIONING_DATE = nc.CONDITIONING_DATE
CURVE_DATES = list(nc.CURVE_DATES)
REPORT_DELAY = nv2.REPORT_DELAY       # 0.5 days from confirmation to report
COND_CUTOFF = day_end(CONDITIONING_DATE) - REPORT_DELAY
T_LIST = 14.0                         # listing: the end of 20 October
EARLY_BY = day_end(dt.date(2026, 10, 23))

# ---------------------------------------------------------------------------------------
# Registered inputs (rules sections 2 to 6; validation rows in brackets).
# ---------------------------------------------------------------------------------------
P_ADMIT_DAY4 = 0.75                   # [A3, A4]
ADMIT_DAY4 = 4.0
ADMIT_LATE = (5.0, 9.0)
T_END = (12.5, 14.0)                  # [A5] end of unprotected exposure
P_DEATH = (0.03, 0.20)                # [A7]
DEATH = nc.DEATH                      # [A8] (14, 24)
TERMINAL_DAYS = 2.43                  # [A8] the anchors' terminal window
N_TERMINAL = (5, 20)                  # [A8]
K_IND = (0.4, 1.0)                    # [B6]
R_SURVIVOR = (0.07, 0.47)             # [B4] log-uniform
R_FATAL = (0.3, 1.0)                  # [B5]
READING = nn.READING                  # [B2] "time_point"
LISTED_TOTAL = 44                     # [C1]
E_TRAVEL = (5, 20)                    # [C3] uniform integers
B_CAREGIVERS = (1, 4)
U_HCW_SHARE = (0.4, 0.9)
V_UNLISTED = (0.5, 2.0)               # [C4]
W_UNLISTED_STAFF = (0.3, 1.0)         # [C8]
PRESENCE = {"A": (0.6, 1.0), "B_home": (0.5, 1.0), "B_facility": (0.5, 1.0), "C": (0.5, 1.0),
            "NH": (0.1, 0.4), "NO": (0.5, 1.0)}   # [C5 to C8], relative to the anchors' duty fraction
P_BRANCH_K = 0.5                      # [D3]
LAMBDA_A_K = (3.5, 51.03)             # [D1] Gamma(shape, rate): 3 of 21 over 2.43 days, Jeffreys
LAMBDA_A_U = {"c": (2, 3, 4), "days": 3.0, "h_u": (60.0, 100.0)}   # [D2] Gamma(0.5 + c, 3 H_U)
LAMBDA_B = (0.05, 0.25)               # [D4] log-uniform, Dean 2016 nursing care
LAMBDA_C = {"k": (0, 1), "days": 2.43, "h_c": (40.0, 79.0)}        # [D5] Gamma(0.5 + k, 2.43 H_C)
LAG_LISTED_MEAN = 1.0                 # [E2]
LAG_UNLISTED_MEAN = (3.0, 7.0)        # [E3]
P_DETECT_UNLISTED = (0.85, 1.0)
R_SECOND = (0.1, 0.8)                 # [E7]
UNDETECTED_SPAN = 10.0
Q_REPORTED_BY_CHECK = (0.1, 0.4)      # [E10]
INCUBATION_TAIL_DAYS = 26.0           # interval: confirmation by day 42 needs onset by about day 41
BACKGROUND = nv2.BACKGROUND           # [E8] the v2 and v3 later-importation term
GROUPS = ("A", "B", "C", "NH", "NO", "I")
GROUP_LABELS = {
    "A": "Listed health workers at the facility",
    "B": "The case's own bedside caregivers, at home and at the facility",
    "C": "Listed other patients and their caregivers at the facility",
    "NH": "Unlisted facility staff",
    "NO": "Other unlisted contacts, at home or at the facility",
    "I": "Terminal-care staff in isolation, only if the case dies",
    "G2": "Second generation: onward cases from first-generation cases",
    "P6": "Later importation: a case acquired in Nigeria from a later, separate importation",
}


def incubation_prior() -> tuple[float, float]:
    """The programme's decided Bundibugyo incubation prior, read at run time."""
    return tuple(priors.BUNDIBUGYO_PRIORS_STAGE_TWO.incubation_gamma)


# ---------------------------------------------------------------------------------------
# Infectiousness shapes [B2, B3, B5]: a point mass at t = 0 and step pieces (t0, t1, density),
# in reference-day units before the level (r or r_fatal) and psi.
# ---------------------------------------------------------------------------------------
Profile = tuple[float, tuple[tuple[float, float, float], ...]]


def shape_pieces(table: Sequence[float], reading: str = READING) -> Profile:
    """The registered table on the continuous clock, divided by its highest day."""
    top = max(table)
    if reading == "time_point":         # f(0) a point mass at onset, f(d) spread over (d - 1, d]
        return table[0] / top, tuple((float(d - 1), float(d), table[d] / top) for d in range(1, len(table)))
    if reading == "day_of_illness":     # f(d) spread over [d, d + 1)
        return 0.0, tuple((float(d), float(d + 1), table[d] / top) for d in range(len(table)))
    raise ValueError(f"unknown reading {reading!r}")


def held_after_peak(prof: Profile) -> Profile:
    """The fatal shape held at its highest level from the end of its last peak piece [B5]."""
    mass, pieces = prof
    last = max(i for i, (_, _, d) in enumerate(pieces) if d >= 1.0 - 1e-12)
    return mass, tuple((t0, t1, 1.0 if i > last else d) for i, (t0, t1, d) in enumerate(pieces))


def flat_pieces() -> Profile:
    """Sensitivity row 22: the survivor course's mean day, spread evenly over (0, 14]."""
    return 0.0, ((0.0, 14.0, (1.0 / len(nn.F_SURVIVOR)) / max(nn.F_SURVIVOR)),)


def profiles(opts: Mapping[str, Any]) -> tuple[Profile, Profile]:
    reading = opts["reading"]
    surv = flat_pieces() if opts["survivor_profile"] == "flat" else shape_pieces(nn.F_SURVIVOR, reading)
    fatal = shape_pieces(nn.F_FATAL, reading)
    if opts["fatal_profile"] == "held":
        fatal = held_after_peak(fatal)
    return surv, fatal


def integral(prof: Profile, a: float, b: float) -> float:
    """Integral of the shape over [a, b]; the onset point mass counts when 0 lies in [a, b]."""
    if b < a:
        return 0.0
    mass, pieces = prof
    total = mass if a <= 0.0 <= b else 0.0
    for t0, t1, d in pieces:
        lo, hi = max(a, t0), min(b, t1)
        if hi > lo:
            total += d * (hi - lo)
    return total


def sample_time(rng: random.Random, prof: Profile, a: float, b: float) -> float:
    """A time in [a, b] drawn in proportion to the shape."""
    mass, pieces = prof
    parts = [(mass, 0.0, 0.0)] if a <= 0.0 <= b and mass > 0.0 else []
    for t0, t1, d in pieces:
        lo, hi = max(a, t0), min(b, t1)
        if hi > lo and d > 0.0:
            parts.append((d * (hi - lo), lo, hi))
    u = rng.random() * sum(w for w, _, _ in parts)
    for w, lo, hi in parts:
        if u <= w:
            return lo + (hi - lo) * (u / w)
        u -= w
    return parts[-1][2]


# ---------------------------------------------------------------------------------------
# Small numerical helpers.
# ---------------------------------------------------------------------------------------
def _int_uniform(rng: random.Random, lo_hi: Sequence[int]) -> int:
    lo, hi = lo_hi
    return lo + min(int(rng.random() * (hi - lo + 1)), hi - lo)


def _log_uniform(rng: random.Random, lo_hi: Sequence[float]) -> float:
    lo, hi = lo_hi
    if lo == hi:
        return lo
    return math.exp(math.log(lo) + (math.log(hi) - math.log(lo)) * rng.random())


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _choice(rng: random.Random, values: Sequence[Any]) -> Any:
    return values[min(int(rng.random() * len(values)), len(values) - 1)]


def _digamma(x: float) -> float:
    acc = 0.0
    while x < 6.0:
        acc -= 1.0 / x
        x += 1.0
    f = 1.0 / (x * x)
    return acc + math.log(x) - 0.5 / x - f * (1 / 12 - f * (1 / 120 - f * (1 / 252 - f * (1 / 240 - f / 132))))


def _gamma_cdf(shape: float, rate: float, t: float) -> float:
    """Regularised lower incomplete gamma P(shape, rate t), by series."""
    x = rate * t
    if x <= 0.0:
        return 0.0
    term = 1.0 / shape
    total, n = term, 0
    while abs(term) > 1e-15 * abs(total) and n < 10_000:
        n += 1
        term *= x / (shape + n)
        total += term
    return min(1.0, total * math.exp(-x + shape * math.log(x) - math.lgamma(shape)))


def hierarchical_mean_log_lambda_a() -> float:
    """Sensitivity row 4: the mean of the two branch posterior log means, E[log Gamma(a, b)] = psi0(a) - log b."""
    k = _digamma(LAMBDA_A_K[0]) - math.log(LAMBDA_A_K[1])
    u = _digamma(0.5 + 3) - math.log(LAMBDA_A_U["days"] * 80.0)
    return 0.5 * (k + u)


def implied_k(mean: float, p_any: float) -> float | None:
    """The negative binomial dispersion with this mean and P(at least one), by bisection on log k."""
    if mean <= 0.0 or not 0.0 < p_any < 1.0:
        return None
    target = math.log(1.0 - p_any)
    f = lambda k: -k * math.log1p(mean / k) - target   # noqa: E731, decreasing toward -mean as k grows
    if f(1e6) > 0.0:
        return None
    lo, hi = 1e-4, 1e6
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if f(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


# ---------------------------------------------------------------------------------------
# Options and parameter draws.
# ---------------------------------------------------------------------------------------
BASE_OPTIONS: dict[str, Any] = {
    "branch_k_weight": P_BRANCH_K, "hierarchical": False, "r": R_SURVIVOR, "psi_fixed": False, "k_ind": K_IND,
    "q": Q_REPORTED_BY_CHECK, "p_admit_day4": P_ADMIT_DAY4, "t_end": T_END, "p_death": P_DEATH,
    "lambda_b": "dean", "unlisted_v": V_UNLISTED, "u_hcw": U_HCW_SHARE, "e_travel": E_TRAVEL,
    "survivor_profile": "registered", "fatal_profile": "held", "reading": READING,
    "incubation": None, "lambda_a_k": LAMBDA_A_K, "diagnostics": False,
}


def options(overrides: Mapping[str, Any] | None = None) -> dict:
    opts = dict(BASE_OPTIONS)
    for key, value in (overrides or {}).items():
        if key not in opts:
            raise KeyError(f"unknown option {key!r}")
        opts[key] = value
    return opts


def _hazards(rng: random.Random, opts: Mapping[str, Any]) -> dict:
    branch = "K" if rng.random() < opts["branch_k_weight"] else "U"
    if opts["hierarchical"]:
        sigma = abs(kc._normal(rng))
        lam_a = math.exp(hierarchical_mean_log_lambda_a() + sigma * kc._normal(rng))
        branch = "H"
    elif branch == "K":
        shape, rate = opts["lambda_a_k"]
        lam_a = ke._gamma_any(rng, shape) / rate
    else:
        c = _choice(rng, LAMBDA_A_U["c"])
        lam_a = ke._gamma_any(rng, 0.5 + c) / (LAMBDA_A_U["days"] * kc._uniform(rng, LAMBDA_A_U["h_u"]))
    if opts["lambda_b"] == "dean":
        lam_b = _log_uniform(rng, LAMBDA_B)
    else:                                   # row 16: lambda_B = lambda_A m_B
        lam_b = lam_a * kc._uniform(rng, (1.0, 4.0))
    k = _choice(rng, LAMBDA_C["k"])
    lam_c = ke._gamma_any(rng, 0.5 + k) / (LAMBDA_C["days"] * kc._uniform(rng, LAMBDA_C["h_c"]))
    return {"branch": branch, "A": lam_a, "B": lam_b, "C": lam_c, "NH": lam_a, "NO": lam_c, "I": lam_a}


def draw_parameters(rng: random.Random, opts: Mapping[str, Any], shapes: tuple[Profile, Profile]) -> dict:
    """Every draw-level quantity, then each contact's segments and infection probability in both branches."""
    lam = _hazards(rng, opts)
    if opts["psi_fixed"]:
        psi = 1.0
    else:
        k_ind = kc._uniform(rng, opts["k_ind"])
        psi = ke._gamma_any(rng, k_ind) / k_ind
    r = _log_uniform(rng, opts["r"])
    r_fatal = kc._uniform(rng, R_FATAL)
    s = ADMIT_DAY4 if rng.random() < opts["p_admit_day4"] else kc._uniform(rng, ADMIT_LATE)
    t = kc._uniform(rng, opts["t_end"])
    p_death = kc._uniform(rng, opts["p_death"])
    q = kc._uniform(rng, opts["q"])
    m_u = kc._uniform(rng, LAG_UNLISTED_MEAN)
    p_det = kc._uniform(rng, P_DETECT_UNLISTED)
    r_u = kc._uniform(rng, R_SECOND)
    lam_bg = kc._uniform(rng, BACKGROUND["imports_per_week"]) / 7.0 * kc._uniform(rng, BACKGROUND["p_local"])
    e = _int_uniform(rng, opts["e_travel"])
    b = _int_uniform(rng, B_CAREGIVERS)
    a = _round_half_up(kc._uniform(rng, opts["u_hcw"]) * (LISTED_TOTAL - e - b))
    c = LISTED_TOTAL - e - b - a
    n = _round_half_up(kc._uniform(rng, opts["unlisted_v"]) * (LISTED_TOTAL - e))
    n_h = min(n, _round_half_up(kc._uniform(rng, W_UNLISTED_STAFF) * a))
    n_o = n - n_h
    n_i = _int_uniform(rng, N_TERMINAL)

    surv, fatal = shapes
    fac_s, fac_f = integral(surv, s, t), integral(fatal, s, t)
    home_s, home_f = integral(surv, 0.0, s), integral(fatal, 0.0, s)
    all_s, all_f = integral(surv, 0.0, t), integral(fatal, 0.0, t)
    lvl_s, lvl_f = psi * r, psi * r_fatal
    contacts = []                           # (group, listed, segments, p_survivor, p_fatal)

    def add(group: str, listed: bool, segs: list[tuple[float, float, float]], xs: float, xf: float) -> None:
        contacts.append((group, listed, tuple(segs),
                         1.0 - math.exp(-lam[group] * lvl_s * xs), 1.0 - math.exp(-lam[group] * lvl_f * xf)))

    for _ in range(a):
        w = kc._uniform(rng, PRESENCE["A"])
        add("A", True, [(s, t, w)], w * fac_s, w * fac_f)
    for _ in range(b):
        wh, wf = kc._uniform(rng, PRESENCE["B_home"]), kc._uniform(rng, PRESENCE["B_facility"])
        add("B", True, [(0.0, s, wh), (s, t, wf)], wh * home_s + wf * fac_s, wh * home_f + wf * fac_f)
    for _ in range(c):
        w = kc._uniform(rng, PRESENCE["C"])
        add("C", True, [(s, t, w)], w * fac_s, w * fac_f)
    for _ in range(n_h):
        w = kc._uniform(rng, PRESENCE["NH"])
        add("NH", False, [(s, t, w)], w * fac_s, w * fac_f)
    for _ in range(n_o):
        w = kc._uniform(rng, PRESENCE["NO"])
        add("NO", False, [(0.0, t, w)], w * all_s, w * all_f)
    inc = opts["incubation"] or incubation_prior()
    return {"lam": lam, "psi": psi, "r": r, "r_fatal": r_fatal, "s": s, "t": t, "p_death": p_death, "q": q,
            "m_u": m_u, "p_det": p_det, "r_u": r_u, "lam_bg": lam_bg, "sizes": {"E": e, "A": a, "B": b, "C": c,
            "NH": n_h, "NO": n_o, "I": n_i}, "contacts": contacts, "shapes": shapes, "incubation": tuple(inc)}


# ---------------------------------------------------------------------------------------
# One forward run.
# ---------------------------------------------------------------------------------------
def _confirm(rng: random.Random, p: Mapping[str, Any], onset: float, listed: bool) -> float:
    if listed:
        return max(onset, T_LIST) + kc._erlang(rng, 2, 2.0 / LAG_LISTED_MEAN)
    if rng.random() >= p["p_det"]:
        return math.inf
    return max(onset, T_LIST) + kc._erlang(rng, 2, 2.0 / p["m_u"])


def simulate_run(rng: random.Random, p: Mapping[str, Any]) -> dict:
    """Every first-generation, second-generation and later-importation case of one realisation."""
    fatal = rng.random() < p["p_death"]
    prof = p["shapes"][1] if fatal else p["shapes"][0]
    inc = p["incubation"]
    first: list[tuple[str, float, float, float]] = []     # (group, infection, onset, confirmation)
    second: list[tuple[float, float, float]] = []         # (infection, onset, confirmation)
    excluded = False

    def case(group: str, listed: bool, t_inf: float) -> None:
        nonlocal excluded
        onset = t_inf + nc._gamma(rng, *inc)
        conf = _confirm(rng, p, onset, listed)
        first.append((group, t_inf, onset, conf))
        if listed and onset < T_LIST and group in ("A", "B", "C") and rng.random() < p["q"]:
            excluded = True                   # [E10] this ill contact would already have been reported
        if not listed:
            lo, hi = onset, (conf if conf < math.inf else onset + UNDETECTED_SPAN)
            n2 = nc._poisson(rng, p["r_u"])
        elif onset < T_LIST:
            lo, hi = onset, T_LIST
            n2 = nc._poisson(rng, p["r_u"] * min(1.0, (T_LIST - onset) / p["m_u"]))
        else:
            n2 = 0
        for _ in range(n2):
            t2 = lo + (hi - lo) * rng.random()
            onset2 = t2 + nc._gamma(rng, *inc)
            second.append((t2, onset2, _confirm(rng, p, onset2, False)))

    for group, listed, segs, p_s, p_f in p["contacts"]:
        if rng.random() < (p_f if fatal else p_s):
            weights = [w * integral(prof, a, b) for a, b, w in segs]
            u = rng.random() * sum(weights)
            k = 0
            while k < len(segs) - 1 and u > weights[k]:
                u -= weights[k]
                k += 1
            a, b, _ = segs[k]
            case(group, listed, sample_time(rng, prof, a, b))
    death = math.inf
    if fatal:
        death = kc._uniform(rng, DEATH)
        lo = max(T_LIST, death - TERMINAL_DAYS)
        p_i = 1.0 - math.exp(-p["lam"]["I"] * p["psi"] * (death - lo))
        for _ in range(p["sizes"]["I"]):
            if rng.random() < p_i:
                case("I", True, lo + (death - lo) * rng.random())
    local = []
    span = T_CONFIRM_END - BACKGROUND["imports_from"]
    shape = BACKGROUND["delay_shape"]
    for _ in range(nc._poisson(rng, p["lam_bg"] * span)):
        arrival = BACKGROUND["imports_from"] + span * rng.random()
        local.append(arrival + kc._erlang(rng, shape, shape / BACKGROUND["delay_mean"]))
    return {"fatal": fatal, "death": death, "first": first, "second": second, "local": local, "excluded": excluded}


def record(run: Mapping[str, Any]) -> dict:
    confs = [(c, g) for g, _, _, c in run["first"] if c < math.inf]
    confs += [(c, "G2") for _, _, c in run["second"] if c < math.inf]
    confs += [(c, "P6") for c in run["local"]]
    confs.sort()
    t_first = confs[0][0] if confs else math.inf
    qualifying = [(c, g) for c, g in confs if T_WINDOW_OPEN < c <= T_CONFIRM_END]
    by = defaultdict(int)
    for g, *_ in run["first"]:
        by[g] += 1
    return {"t_first": t_first, "kept": (not run["excluded"]) and t_first > COND_CUTOFF,
            "yes": bool(qualifying), "n_qual": len(qualifying),
            "first_qualifying": qualifying[0][1] if qualifying else None,
            "first_qualifying_time": qualifying[0][0] if qualifying else math.inf,
            "gen1": dict(by), "gen1_total": len(run["first"]), "gen2": len(run["second"]),
            "p6_qualifying": sum(1 for c, g in qualifying if g == "P6"), "fatal": run["fatal"]}


# ---------------------------------------------------------------------------------------
# Semi-analytic per-draw probability (rules section 7.2) and the diagnostics (7.8).
# ---------------------------------------------------------------------------------------
def _erlang_cdf(shape: int, rate: float, t: float) -> float:
    if t <= 0.0:
        return 0.0
    x = rate * t
    term, total = 1.0, 1.0
    for k in range(1, shape):
        term *= x / k
        total += term
    return 1.0 - math.exp(-x) * total


def background_mean_in_window(lam_bg: float) -> float:
    """Expected later-importation confirmations in the window: lam_bg times a fixed quadrature."""
    return lam_bg * _background_window_integral()


@functools.lru_cache(maxsize=None)
def _background_window_integral(steps: int = 290) -> float:
    lo, hi = BACKGROUND["imports_from"], T_CONFIRM_END
    shape = BACKGROUND["delay_shape"]
    rate = shape / BACKGROUND["delay_mean"]
    h = (hi - lo) / steps
    acc = 0.0
    for i in range(steps):
        a = lo + (i + 0.5) * h
        acc += _erlang_cdf(shape, rate, T_CONFIRM_END - a) - _erlang_cdf(shape, rate, T_WINDOW_OPEN - a)
    return acc * h


def draw_probability(p: Mapping[str, Any]) -> float:
    tail = _gamma_cdf(p["incubation"][0], p["incubation"][1], INCUBATION_TAIL_DAYS)
    surv = fatal = 1.0
    for _, listed, _, p_s, p_f in p["contacts"]:
        d = tail * (1.0 if listed else p["p_det"])
        surv *= 1.0 - p_s * d
        fatal *= 1.0 - p_f * d
    p_i = 1.0 - math.exp(-p["lam"]["I"] * p["psi"] * TERMINAL_DAYS)   # mid death time 19: a full terminal window
    fatal *= (1.0 - p_i * tail) ** p["sizes"]["I"]
    none = math.exp(-background_mean_in_window(p["lam_bg"])) * ((1.0 - p["p_death"]) * surv + p["p_death"] * fatal)
    return 1.0 - none


def _p_contact(p: Mapping[str, Any], group: str) -> float | None:
    for g, _, _, p_s, p_f in p["contacts"]:
        if g == group:
            return (1.0 - p["p_death"]) * p_s + p["p_death"] * p_f
    return None


def diagnostics_for_draw(drng: random.Random, p: Mapping[str, Any]) -> dict:
    """F1, F4 and the A10 bound, survivor branch, at this draw's hazards and level."""
    surv = p["shapes"][0]
    lvl = p["psi"] * p["r"]
    lam = p["lam"]

    def pr(group: str, x: float) -> float:
        return 1.0 - math.exp(-lam[group] * lvl * x)

    # F1: a Senegal-shaped contact set.
    t_s = kc._uniform(drng, (11.0, 13.0))
    nb = _int_uniform(drng, (2, 6))
    h_f = _int_uniform(drng, (10, 25))
    log_none = 0.0
    for _ in range(nb):
        log_none += math.log1p(-pr("B", kc._uniform(drng, PRESENCE["B_home"]) * integral(surv, 0.0, t_s)))
    for _ in range(34 - nb):
        log_none += math.log1p(-pr("C", kc._uniform(drng, PRESENCE["C"]) * integral(surv, 0.0, t_s)))
    for _ in range(h_f):
        log_none += math.log1p(-pr("A", kc._uniform(drng, PRESENCE["A"]) * integral(surv, 10.0, t_s)))
    for _ in range(33 - h_f):
        log_none += math.log1p(-pr("A", 0.1 * integral(surv, 2.0, 10.0)))
    # F4: four days of unsuspected inpatient care, two placements in the illness.
    f4 = {}
    for key, (a, b) in (("days_3_to_7", (3.0, 7.0)), ("days_6_to_10", (6.0, 10.0))):
        staff = _int_uniform(drng, (20, 50))
        ps = [pr("A", kc._uniform(drng, PRESENCE["A"]) * integral(surv, a, b)) for _ in range(staff)]
        f4[key] = {"expected": sum(ps), "p_any": 1.0 - math.prod(1.0 - x for x in ps)}
    # A10: isolation staff in the survivor branch, the shape extended past day 14 by x0.75 per day.
    last = surv[1][-1][2] if surv[1] else 0.0
    ext = integral(surv, T_LIST, 15.0) + sum(last * 0.75 ** k for k in range(1, 8))   # (14, 15] then to day 21
    n_iso = _int_uniform(drng, N_TERMINAL)
    a10 = n_iso * (1.0 - math.exp(-lam["A"] * lvl * ext))
    return {"f1_p_none": math.exp(log_none), "f4": f4, "a10_expected": a10}


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def _quantiles(values: Sequence[float], qs: Sequence[float]) -> list[float]:
    xs = sorted(values)
    out = []
    for q in qs:
        pos = q * (len(xs) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(xs) - 1)
        out.append(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))
    return out


def _p_at_least(hist: Mapping[int, float], n: int) -> float:
    total = sum(hist.values())
    return sum(v for k, v in hist.items() if k >= n) / total if total else 0.0


def _mean(hist: Mapping[int, float]) -> float:
    total = sum(hist.values())
    return sum(k * v for k, v in hist.items()) / total if total else 0.0


def curve(t_firsts: Sequence[float]) -> list[dict]:
    """S1 by the v2 and v3 rule: P(YES | no report by the end of each date), over the runs given (kept runs only)."""
    ordered = sorted(t_firsts)
    beyond = len(ordered) - bisect.bisect_right(ordered, T_CONFIRM_END)
    out = []
    for date in CURVE_DATES:
        alive = len(ordered) - bisect.bisect_right(ordered, day_end(date) - REPORT_DELAY)
        out.append({"date": date.isoformat(), "p": 1.0 - beyond / alive if alive else 0.0})
    return out


def run(overrides: Mapping[str, Any] | None = None, n: int | None = None, seed: int | None = None,
        full: bool = True, runs_per_draw: int = RUNS_PER_DRAW) -> dict:
    opts = options(overrides)
    n = N_DRAWS if n is None else n
    rng = random.Random(SEED if seed is None else seed)
    drng = random.Random(DIAGNOSTIC_SEED if seed is None else seed + 1)
    shapes = profiles(opts)
    per_draw: list[tuple[int, int]] = []
    t_firsts: list[float] = []
    hist_qual: dict[int, float] = defaultdict(float)
    gen1_sum: dict[str, float] = defaultdict(float)
    first_group: dict[str, float] = defaultdict(float)
    sums = defaultdict(float)
    surv_runs = surv_any = surv_total = 0
    draw_ps, hcw_ps, care_ps = [], [], []
    diag = defaultdict(float)
    total_runs = n * runs_per_draw
    for _ in range(n):
        p = draw_parameters(rng, opts, shapes)
        b = a = 0
        for _ in range(runs_per_draw):
            rec = record(simulate_run(rng, p))
            sums["yes_all"] += rec["yes"]
            sums["gen1_any"] += rec["gen1_total"] > 0
            sums["gen1_total"] += rec["gen1_total"]
            if not full:
                b += rec["kept"]
                a += rec["kept"] and rec["yes"]
                continue
            for g, v in rec["gen1"].items():
                gen1_sum[g] += v
            sums["G2"] += rec["gen2"]
            sums["P6"] += rec["p6_qualifying"]
            if not rec["fatal"]:
                surv_runs += 1
                surv_any += rec["gen1_total"] > 0
                surv_total += rec["gen1_total"]
            if rec["kept"]:
                b += 1
                a += rec["yes"]
                t_firsts.append(rec["t_first"])
                hist_qual[rec["n_qual"]] += 1.0
                if rec["first_qualifying"] is not None:
                    first_group[rec["first_qualifying"]] += 1.0
                    sums["early"] += rec["first_qualifying_time"] <= EARLY_BY
        per_draw.append((b, a))
        if full:
            draw_ps.append(draw_probability(p))
            hcw, care = _p_contact(p, "A"), _p_contact(p, "B")
            if hcw is not None:
                hcw_ps.append(hcw)
            if care is not None:
                care_ps.append(care)
            if opts["diagnostics"]:
                d = diagnostics_for_draw(drng, p)
                diag["f1_p_none"] += d["f1_p_none"]
                diag["a10_expected"] += d["a10_expected"]
                for key, v in d["f4"].items():
                    diag[f"f4_{key}_expected"] += v["expected"]
                    diag[f"f4_{key}_p_any"] += v["p_any"]
    sum_b = sum(x[0] for x in per_draw)
    sum_a = sum(x[1] for x in per_draw)
    headline = sum_a / sum_b if sum_b else 0.0
    se = math.sqrt(sum((a - headline * b) ** 2 for b, a in per_draw)) / sum_b if sum_b else 0.0
    out = {"headline": headline, "unconditioned": sums["yes_all"] / total_runs, "monte_carlo_se": se,
           "share_of_runs_kept": sum_b / total_runs,
           "first_generation_unconditioned": {"p_at_least_one_infection": sums["gen1_any"] / total_runs,
                                              "expected_infections": sums["gen1_total"] / total_runs},
           "n_draws": n, "runs_per_draw": runs_per_draw}
    if not full:
        return out
    yes_kept = sum(first_group.values())
    p10, p50, p90 = _quantiles(draw_ps, (0.1, 0.5, 0.9))
    mean_draw = sum(draw_ps) / len(draw_ps)
    out.update({
        "epistemic_interval": {"p10": p10, "median": p50, "p90": p90, "mean": mean_draw,
                               "mean_minus_headline": mean_draw - headline},
        "curve": curve(t_firsts),
        "count_tiers": {"p_at_least_3": _p_at_least(hist_qual, 3), "p_at_least_10": _p_at_least(hist_qual, 10),
                        "expected_qualifying_cases": _mean(hist_qual)},
        "by_group": {
            "expected_first_generation_infections_unconditioned": {g: gen1_sum[g] / total_runs for g in GROUPS},
            "expected_second_generation_unconditioned": sums["G2"] / total_runs,
            "expected_later_importation_qualifying_unconditioned": sums["P6"] / total_runs,
            "share_of_first_qualifying_case_conditioned": {g: (first_group[g] / yes_kept if yes_kept else 0.0)
                                                          for g in GROUPS + ("G2", "P6")},
        },
        "early_share": {"p_first_qualifying_by_end_of_23_october": sums["early"] / sum_b if sum_b else 0.0,
                        "share_of_headline": (sums["early"] / sum_a) if sum_a else 0.0},
        "per_contact": {g: dict(zip(("p10", "median", "p90"), _quantiles(vals, (0.1, 0.5, 0.9))))
                        for g, vals in (("listed_health_worker", hcw_ps), ("caregiver", care_ps)) if vals},
    })
    surv_mean = surv_total / surv_runs if surv_runs else 0.0
    surv_p = surv_any / surv_runs if surv_runs else 0.0
    out["implied_k"] = {"survivor_branch_mean_infections": surv_mean, "survivor_branch_p_at_least_one": surv_p,
                        "implied_k": implied_k(surv_mean, surv_p), "yamin_survivors_reference": 0.4}
    if opts["diagnostics"]:
        out["diagnostics"] = {
            "D7_drc_exposed_health_worker_yield": 0.046,
            "D7_listed_health_worker_median": out["per_contact"].get("listed_health_worker", {}).get("median"),
            "F1_senegal_shaped_mean_p_no_infection": diag["f1_p_none"] / n,
            "F4_uganda_second_import": {
                key: {"expected_health_worker_infections": diag[f"f4_{key}_expected"] / n,
                      "p_at_least_one": diag[f"f4_{key}_p_any"] / n, "observed": "0 to 2"}
                for key in ("days_3_to_7", "days_6_to_10")},
            "A10_isolation_staff_expected_infections_survivor_branch": diag["a10_expected"] / n,
        }
    return out


# ---------------------------------------------------------------------------------------
# Sensitivity rows (rules section 8).
# ---------------------------------------------------------------------------------------
SENSITIVITY: list[tuple[str, str, dict]] = [
    ("branch_k_only", "Branch K (Kenya) only", {"branch_k_weight": 1.0}),
    ("branch_u_only", "Branch U (Uganda) only", {"branch_k_weight": 0.0}),
    ("branch_k_weight_0_7", "Branch K weight 0.7", {"branch_k_weight": 0.7}),
    ("hierarchical_lambda_a", "Hierarchical lognormal health-worker hazard", {"hierarchical": True}),
    ("r_low", "r log-uniform 0.07 to 0.15", {"r": (0.07, 0.15)}),
    ("r_high", "r log-uniform 0.25 to 0.47", {"r": (0.25, 0.47)}),
    ("psi_off", "No per-case multiplier (psi = 1)", {"psi_fixed": True}),
    ("k_ind_0_4", "Per-case multiplier shape 0.4", {"k_ind": (0.4, 0.4)}),
    ("q_0", "No early-onset conditioning (q = 0)", {"q": (0.0, 0.0)}),
    ("q_0_3_to_0_8", "Early-onset reporting q 0.3 to 0.8", {"q": (0.3, 0.8)}),
    ("q_1", "Full early-onset conditioning (q = 1)", {"q": (1.0, 1.0)}),
    ("admit_day_4", "Admitted on 11 October with certainty", {"p_admit_day4": 1.0}),
    ("admit_late", "Admitted 12 to 16 October with certainty", {"p_admit_day4": 0.0}),
    ("suspected_earlier", "Unprotected care ends on 18 to 19 October (T 11 to 13)", {"t_end": (11.0, 13.0)}),
    ("p_death_high", "Probability of death 0.15 to 0.35", {"p_death": (0.15, 0.35)}),
    ("lambda_b_linked", "Caregiver hazard = health-worker hazard times 1 to 4", {"lambda_b": "linked"}),
    ("no_unlisted", "No unlisted contacts", {"unlisted_v": (0.0, 0.0)}),
    ("unlisted_2_to_3", "Unlisted contacts 2 to 3 times the non-travel list", {"unlisted_v": (2.0, 3.0)}),
    ("hcw_share_high", "Health-worker share 0.7 to 0.9", {"u_hcw": (0.7, 0.9)}),
    ("travel_1_to_8", "Travel contacts 1 to 8", {"e_travel": (1, 8)}),
    ("travel_15_to_25", "Travel contacts 15 to 25", {"e_travel": (15, 25)}),
    ("flat_profile", "Flat survivor infectiousness", {"survivor_profile": "flat"}),
    ("incubation_mean_10", "Incubation gamma mean 10 days (shape 3)", {"incubation": (3.0, 0.3)}),
    ("kenya_4_of_22", "Kenya anchor 4 of 22 (the attendant included)", {"lambda_a_k": (4.5, 53.46)}),
    ("fatal_declining", "Fatal branch with the declining tail (no hold)", {"fatal_profile": "declining"}),
    ("day_of_illness_reading", "Day-of-illness reading for both shapes", {"reading": "day_of_illness"}),
    ("baseline", "Baseline at the sensitivity sample size", {}),
]


def sensitivity_rows(n: int = N_DRAWS_SENSITIVITY) -> list[dict]:
    rows = []
    for key, label, overrides in SENSITIVITY:
        res = run(overrides, n=n, full=False)
        rows.append({"row": key, "label": label, "headline": res["headline"], "unconditioned": res["unconditioned"],
                     "monte_carlo_se": res["monte_carlo_se"], "share_of_runs_kept": res["share_of_runs_kept"],
                     **res["first_generation_unconditioned"]})
    return rows


# ---------------------------------------------------------------------------------------
# The artifact.
# ---------------------------------------------------------------------------------------
QUESTION = (
    "Will Nigeria report at least one further laboratory-confirmed case of this outbreak, beyond the first case "
    "confirmed on 2026-10-20, that is not a new importation, with confirmation dated 2026-10-21 to 2026-11-17 and "
    "first reported by an authority by 2026-11-20?")

CASE_EVIDENCE = [
    {"fact": "One imported case of this outbreak confirmed by laboratory tests in the Federal Capital Territory, announced on 20 October; the case is in isolation",
     "source": "Coordinating Minister of Health and Social Welfare and WHO Regional Office for Africa, 20 October (registry)"},
    {"fact": "Symptoms began on 7 October (day 0); no symptoms during travel were reported, so travel contacts carry no hazard in this model",
     "source": "WHO Regional Office for Africa and the Coordinating Minister, 20 October"},
    {"fact": "Care was sought at a private facility on 11 October (day 4) and the diagnosis was confirmed on 20 October; continuous admission from 11 October is not stated, so the model's admission day ranges from day 4 to day 9",
     "source": "WHO Regional Office for Africa and the Federal Ministry of Health statement, 20 October"},
    {"fact": "When Ebola was first suspected, and the date the sample was taken, have not been stated", "source": "all sources to the registration check"},
    {"fact": "Described as stable on 20 October", "source": "the Coordinating Minister, 20 October"},
    {"fact": "44 contacts identified, including health workers, patients, caregivers and travel contacts; isolated at a designated facility for at least three weeks",
     "source": "Federal Ministry of Health statement and the Coordinating Minister on national television, 20 October"},
    {"fact": "No further case, and no ill or tested contact, reported by the registration check (2026-10-21T03:22Z)",
     "source": "all sources searched"},
    {"fact": "No authority has established where the first case was infected, and this model does not assume it", "source": "all sources"},
]

CITATIONS = {
    "rules": "Internal dated rules file and validation table, sha256 in registration; restated in inputs",
    "kenya_event": ("Kenya Ministry of Health statements of 6 to 20 October 2026, WHO Regional Office for Africa Kenya "
                    "situation report week 41, ECDC 20 October: 3 of 21 quarantined health workers infected after "
                    "managing the first case on 3 and 4 October; the case presented for care on the day of arrival in Kenya and died about 2.4 days later"),
    "kenya_residual": "The same sources: 135 contacts by 20 October; 0 to 1 infections outside the first-listed groups",
    "uganda_event": ("Uganda's index import in May 2026: about 3 days of unsuspected care with resuscitation; 2 to 4 "
                     "health workers and 1 driver infected; 127 contacts, mostly health workers (the Director General "
                     "of Health Services via national media; WHO DON602 and DON613; Kayiwa et al., Emerg Infect Dis "
                     "2026;32(10))"),
    "uganda_second_import": ("A second Uganda import treated unsuspected for 4 days at a private hospital (10 to 14 May "
                             "2026) and discharged stable; whether it infected 2 of the 4 health workers is unresolved "
                             "(Uganda Ministry of Health via AllAfrica, 26 May, and Pulse Uganda, 23 May 2026)"),
    "lanini2015": "Lanini et al., J Clin Invest 2015;125(12):4692-8 (viral load by outcome and day)",
    "yamin2015": "Yamin et al., Ann Intern Med 2015;162(1):11-7 (R by outcome: survivors 0.66, nonsurvivors 2.36)",
    "dowell1999": "Dowell et al., J Infect Dis 1999, as reported by Brainard et al., Int J Epidemiol 2016 (early and late illness)",
    "dean2016": "Dean et al., Clin Infect Dis 2016 (household nursing-care attack rate 47.9%)",
    "macneil2010": "MacNeil et al., Emerg Infect Dis 2010 (Bundibugyo incubation mean 6.3 days)",
    "wanyana2024": "Wanyana et al., Int J Infect Dis 2024 (detection delay by previous contact status, Sudan virus)",
    "senegal2014": "Mirkovic et al., MMWR 2014;63(39); Breakwell et al., MMWR Suppl 2016 (Senegal import, no secondary case)",
    "lagos2014": "Lagos State health authorities, 28 July 2014, via national media: 44 hospital contacts, 38 of them health workers and 6 laboratory staff",
    "infectiousness_table": "The registered Nigeria nowcast synthesis, section A3 (survivor and fatal shapes)",
    "lovs_incubation_prior": nn.CITATIONS["lovs_incubation_prior"],
    "v1_v2_v3": "lovs/forecast/nigeria_conditioned.py, nigeria_nowcast.py and nigeria_v2.py and their artifacts, frozen",
    "registry": "data/international-events.json, report nga-2026-10-20",
}

PROVENANCE = {
    "clock": "case evidence for onset, care and confirmation; the admission rule and T's width are judgement",
    "profile": "the registered nowcast shapes, time-point reading; the fatal hold is a rule (nonsurvivors decay little before death)",
    "r": "judgement bounded by three indicative lines: the viral-load gap, Yamin's per-case ratio, Dowell's stage effect",
    "psi": "judgement: individual spread from Lanini's survivor peaks and Yamin's survivors",
    "groups": "judgement: no split of the 44 is published; Lagos 2014 informs the health-worker share",
    "hazards": "this outbreak: Kenya and Uganda health workers and Kenya's residual; caregivers from Dean 2016",
    "detection": "Kenya's quarantined contacts (about a day from onset to confirmation) and Wanyana 2024 for the unlisted",
    "second_generation": "judgement",
    "background": "registered: the v2 and v3 later-importation term, unchanged",
    "conditioning": "case evidence: no ill contact reported at the registration check; the reporting probability q is judgement",
}

DISCLOSURES = [
    ("Prior sight: the author of the v4 rules had seen every v1 to v3 output and had written a rough estimate in "
     "conversation (about 1.3 to 2.5 expected infections, 50 to 80%) before the rules existed."),
    ("The rules were red-teamed in fresh context by a reviewer blind to every earlier output. Its re-implementation of "
     "the pre-review draft gave about 0.58, and it expected about 0.40 to 0.50 after its fixes; the author saw both "
     "before registering. Its findings were taken whichever way they moved the number, including the Critical one "
     "(a per-case infectiousness multiplier), which lowers it, with two taken only in part: the Kenya and Uganda "
     "branches keep equal weight where the reviewer leaned toward Kenya (rows give a 0.7 weight on Kenya and a "
     "hierarchical model), and the early-onset reporting probability q is 0.1 to 0.4, below the reviewer's partial "
     "value (rows give q 0.3 to 0.8 and q = 1)."),
    ("No Bundibugyo viral-load series and no Bundibugyo per-contact attack-rate study exist. The infectiousness shape, "
     "the survivor level and the caregiver hazard are extrapolated from Ebola (Zaire) and Sudan virus evidence."),
    ("Kenya's and Uganda's events disagree severalfold per contact-day. They are kept as two branches of equal weight, "
     "not averaged; rows give each alone, a 0.7 weight on Kenya, and a hierarchical model."),
    ("The 44 contacts are not split by group in any published source; the split is a registered judgement, and the "
     "unlisted contacts are a named group because the list is expected to grow."),
    ("The conditioning on no report by the end of 20 October never binds here, because no contact could be confirmed "
     "before the listing that evening. The conditioning that matters is the absence of any reported ill contact at the "
     "registration check; each listed contact ill before 20 October would have been reported by then with probability "
     "q (0.1 to 0.4), and rows give q = 0, 0.3 to 0.8 and 1."),
    ("Two statements in the registered v3 rules were found to be inaccurate while grounding v4; v3 is not edited. The "
     "Senegal 2014 import had about 8 to 9 days of unsuspected care (outpatient, then inpatient), not about 3. The "
     "Kenya hospital's chief executive did state that staff wore full protective equipment, as quoted in the press "
     "(a facility statement, not an authority statement)."),
    ("The three Kenya health-worker infections may share a single procedure and so be less than three independent "
     "events; row kenya_4_of_22 adds the patient attendant."),
    ("Per-contact probabilities are read, in each draw, for the first listed health worker and the first caregiver, "
     "mixed over the probability of death; the published band is across draws. Their means across draws, computed "
     "after registration from the same draws and outside the hashed results, are about 4.5% per listed health worker "
     "and about 15% per caregiver. The mean, not the median of 1.6%, is the figure comparable with the DRC's pooled "
     "4.6% yield among exposed health workers."),
    ("In S3, G2 counts every second-generation infection, while P6 counts only later-importation confirmations "
     "inside the window."),
    ("The fatal shape is held at its peak from the end of its last peak piece: day 8 on the registered time-point "
     "reading, day 9 on the day-of-illness reading (row day_of_illness_reading)."),
    ("Diagnostics use their own seed (2026102105), so they never change the main sample."),
]


def results_digest(results: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(results, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_artifact(main_run: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    results = kc._rounded({
        "headline": {"probability": main_run["headline"], "unconditioned": main_run["unconditioned"],
                     "monte_carlo_se": main_run["monte_carlo_se"], "share_of_runs_kept": main_run["share_of_runs_kept"]},
        "epistemic_interval": main_run["epistemic_interval"],
        "S1_curve": main_run["curve"],
        "S2_count_tiers": main_run["count_tiers"],
        "S3_by_group": main_run["by_group"],
        "first_generation_unconditioned": main_run["first_generation_unconditioned"],
        "early_share": main_run["early_share"],
        "per_contact": main_run["per_contact"],
        "implied_k": main_run["implied_k"],
        "diagnostics": main_run.get("diagnostics"),
        "sensitivity": list(sensitivity),
    })
    head = results["headline"]
    registration = dict(REGISTRATION)
    if ENTRY_7 is not None:
        registration["entry_7_first_run"] = ENTRY_7
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "nigeria-bdbv-2026-10-21-v4-further-case",
        "model_version": MODEL_VERSION,
        "question": QUESTION,
        "rules": {
            "same_as_v1": ("Identical to the registered v1 forecast's question except the registration-timing clause: the "
                           "authorities, what counts, where the case falls ill, the void rule, the ambiguity ruling, "
                           "scoring by Brier at a coverage review between 2026-11-21 and 2026-12-05, and silence never "
                           "resolving NO."),
            "registration_timing": ("Only reports first published after the v4 registration entry (2026-10-21T03:23Z) "
                                    "count for v4. None was known at registration."),
            "what_counts_in_the_model": ("Confirmation on days 14.0 to 42.0 (21 October to 17 November WAT), first report "
                                         "by day 45.0 (20 November). First- and second-generation cases from the first "
                                         "case and cases acquired in Nigeria from a later importation count; new importations do not "
                                         "and are not modelled."),
        },
        "relationship_to_earlier_records": ("v4 is a new record, registered beside v1, v2 and v3, which stay as registered, "
                                            "are published and are scored as registered. No record is averaged with "
                                            "another. v4 leads only after an independent review of its code and the "
                                            "founder's approval to publish it."),
        "registration": registration,
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {"probability": head["probability"], "unconditioned": head["unconditioned"],
                     "monte_carlo_se": head["monte_carlo_se"]},
        "results": results,
        "results_sha256": results_digest(results),
        "curve_note": ("S1: each row is the probability of YES given that no further case has been reported by the end of "
                       "that date (WAT), over runs consistent with the registration check."),
        "scoring_plan": {
            "scored_value": "the headline probability, fixed by this artifact's results hash",
            "scored_by": ("Brier score at a coverage review naming its sources, made between 2026-11-21 and 2026-12-05. "
                          "Silence never resolves NO."),
            "shown_beside": ["the curve value standing on the day of the first qualifying report",
                             "the v1, v2 and v3 values"],
            "published": "whatever the outcome",
            "wording": ("The result is reported as a probability beside the outcome. One outcome is not a verdict on "
                        "the forecast and says nothing about Nigeria's response."),
            "if_a_case_occurs": "say nothing about where, from whom or in which facility that case was infected",
        },
        "inputs": {
            "clock": {"t0": "2026-10-07T00:00+01:00", "onset": 0.0, "p_admit_day_4": P_ADMIT_DAY4,
                      "admit_late_uniform": ADMIT_LATE, "t_end_uniform": T_END, "listing_day": T_LIST,
                      "report_delay": REPORT_DELAY, "window_confirmation": [T_WINDOW_OPEN, T_CONFIRM_END],
                      "report_by": T_REPORT_END, "conditioning_report_cutoff": day_end(CONDITIONING_DATE)},
            "infectiousness": {"f_survivor": nn.F_SURVIVOR, "f_fatal": nn.F_FATAL, "reading": READING,
                               "survivor": "psi r f_S(t) / max(F_S)", "r_log_uniform": R_SURVIVOR,
                               "fatal": "psi r_fatal f_F(t) / max(F_F), held at 1 after its peak", "r_fatal_uniform": R_FATAL,
                               "psi": "Gamma(k_ind, rate k_ind) per draw", "k_ind_uniform": K_IND,
                               "reference_day": "one day with a fatal index in its last three days"},
            "course": {"p_death_uniform": P_DEATH, "death_uniform": DEATH, "terminal_days": TERMINAL_DAYS,
                       "terminal_staff_uniform_integer": N_TERMINAL, "funeral": "none (safe burial)",
                       "survivor_after_isolation": "no exposure modelled; bound reported (A10)"},
            "contacts": {"listed_total": LISTED_TOTAL, "travel_uniform_integer": E_TRAVEL, "travel_hazard": 0.0,
                         "caregivers_uniform_integer": B_CAREGIVERS, "health_worker_share_uniform": U_HCW_SHARE,
                         "unlisted_multiplier_uniform": V_UNLISTED, "unlisted_staff_share_of_a_uniform": W_UNLISTED_STAFF,
                         "presence_uniform": PRESENCE,
                         "segments": {"A": "[S, T]", "B": "[0, S) and [S, T]", "C": "[S, T]", "NH": "[S, T]",
                                      "NO": "[0, T]", "I": "[max(14, death - 2.43), death]"},
                         "infection_probability": "1 - exp(-lambda_g X), X = sum of presence times the integral of s"},
            "hazards_per_reference_day": {"branch_k_weight": P_BRANCH_K, "lambda_a_k_gamma_shape_rate": LAMBDA_A_K,
                                          "lambda_a_u": {"shape": "0.5 + c", "rate": "3 H_U", **LAMBDA_A_U},
                                          "lambda_b_log_uniform": LAMBDA_B,
                                          "lambda_c": {"shape": "0.5 + k", "rate": "2.43 H_C", **LAMBDA_C},
                                          "groups": {"A": "lambda_A", "B": "lambda_B", "C": "lambda_C",
                                                     "NH": "lambda_A", "NO": "lambda_C", "I": "lambda_A"}},
            "incubation_gamma": incubation_prior(),
            "detection": {"listed": f"max(onset, 14.0) + Erlang(2, mean {LAG_LISTED_MEAN}); always detected",
                          "unlisted": "max(onset, 14.0) + Erlang(2, mean m_u)", "m_u_uniform": LAG_UNLISTED_MEAN,
                          "p_detect_unlisted_uniform": P_DETECT_UNLISTED, "report": "confirmation + 0.5 days"},
            "second_generation": {"r_u_uniform": R_SECOND,
                                  "unlisted": "Poisson(R_u), infection uniform on [onset, confirmation] (or onset + 10)",
                                  "listed_ill_before_listing": "Poisson(R_u min(1, (14 - onset) / m_u)) on [onset, 14]",
                                  "third_generation": "none"},
            "conditioning_at_registration_check": {"q_uniform": Q_REPORTED_BY_CHECK,
                                                   "rule": "a run is excluded if any listed A, B or C case with onset before day 14 is reported, each with probability q"},
            "background": BACKGROUND,
            "groups": GROUP_LABELS,
            "provenance": PROVENANCE,
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": DISCLOSURES,
        "sensitivity_note": ("Each row moves one registered input (20,000 draws, seed 2026102104). headline is conditioned "
                             "as the main result; the infection columns are first-generation, unconditioned."),
        "method": {"seed": SEED, "diagnostic_seed": DIAGNOSTIC_SEED, "n_draws": N_DRAWS,
                   "n_draws_sensitivity": N_DRAWS_SENSITIVITY, "runs_per_draw": RUNS_PER_DRAW,
                   "code": "lovs/forecast/nigeria_v4.py",
                   "reuses": ["lovs/forecast/nigeria_conditioned.py", "lovs/forecast/nigeria_nowcast.py",
                              "lovs/forecast/nigeria_v2.py", "lovs/forecast/kenya_conditioned.py",
                              "lovs/forecast/kenya_escape.py", "lovs/lovs_priors_bundibugyo.py"]},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = kc.payload_digest(payload)
    return payload


def build(n: int = N_DRAWS, n_sensitivity: int = N_DRAWS_SENSITIVITY) -> dict:
    return build_artifact(run({"diagnostics": True}, n=n), sensitivity_rows(n=n_sensitivity))


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
                      "payload_sha256": artifact["payload_sha256"], "results_sha256": artifact["results_sha256"],
                      "module_sha256": hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
