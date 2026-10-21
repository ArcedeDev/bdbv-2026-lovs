# SPDX-License-Identifier: Apache-2.0
"""Nigeria v2: one model for the further-case forecast and the nowcast, from this outbreak's facility evidence.

Question (unchanged from the registered v1 forecast): will Nigeria report at least one further
laboratory-confirmed case of this outbreak, beyond the first case confirmed on 2026-10-20, that is
not a new importation, with confirmation dated 2026-10-21 to 2026-11-17 and first reported by an
authority by 2026-11-20? Only reports first published after the v2 registration entry count.

Why v2 exists. On 2026-10-20 the founder ruled that this outbreak's own data should lead, with older
studies used only where it is silent. The v1 forecast took facility transmission from Faye 2015 and
the v1 nowcast from Kikwit 1995 and Dean 2016 attack rates. v2 takes it from this outbreak: the two
destination seeding events (Uganda and Kenya), the destination imports that seeded nothing, and the
health-worker infections of a newly seeded DRC province (Tshopo). Both v1 records stay frozen and are
scored as registered; v2 is the headline, and the v1 values are shown beside it, never averaged.

Model, in one paragraph. The clock is days from 2026-10-07 00:00 WAT; onset is day 0. The first case
was at home on days 0 to 4, in unsuspected facility care from day 4 to T_iso (uniform 12.5 to 13.5),
and isolated after T_iso. A daily infectiousness shape g (survivor and fatal tables mixed by w_fatal,
normalised to a peak day of 1) gives exposure in peak-day units for each window. Expected infections
of health workers (W), other patients, caregivers and visitors (C) and the household and community
(H) come from one of two anchors per draw: A, the late-stage rate per day of unsuspected destination
care in this outbreak, scaled by q (this case's peak day relative to a late-stage day) and an IPC
multiplier; or B, Tshopo's health-worker infections per case, scaled per unsuspected facility day. One
gamma factor Z per draw carries individual heterogeneity. Counts are Poisson, infection times follow
g, onsets add the programme incubation, and listing and detection rules give confirmations and
reports. First-generation cases seed a negative binomial second generation (G2), and a later,
separate importation can cause a local case (P6). Every published quantity, forecast and nowcast,
comes from the same forward-simulated runs, so the two cannot contradict each other.

Stdlib only and deterministic for a fixed seed: every variate is built from random.Random.random()
through the helpers of the frozen modules, which are imported and never changed. Nothing here asserts
that anyone is infected; every count is expected under this model.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
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

REPO = kc.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "nigeria-2026-10-21-v2.json"
MODULE_PATH = Path(__file__).resolve()
MODEL_VERSION = "nigeria_v2-v1.0.0"
SEED = 2026102102
N_DRAWS = 40_000
N_DRAWS_SENSITIVITY = 20_000
RUNS_PER_DRAW = 4

REGISTRATION = {
    "rules_sha256": "acf07202acb0dcf07ab2ac0ebd41a94ef6dc3737817d471540e59e21a44a0a3f",
    "evidence_sha256": "79073bb975d124bfc8ef13c02d6a415c99b46103f328d3274b470a331acd75e2",
    "wiki_page_id": "reference:bdbv-nigeria-v2-precommitment-2026-10-21",
    "entry_0_registered_utc": "2026-10-20T23:41:38Z",
    "note": ("The rules, inputs, benchmarks, seed, draw counts and the 16 sensitivity rows were fixed in a dated rules "
             "file before any v2 code existed or any v2 run. Its sha256 was recorded on the wiki page at the time "
             "given. The rules file is internal; its rules are restated in this artifact's inputs."),
}
# Filled after the first full run, from the precommitment page's Entry 1. The first run's file and payload
# hashes differ from this artifact's, because this block and its disclosure were added afterwards;
# results_sha256 covers every number and is unchanged.
ENTRY_1: dict[str, str] | None = {
    "run_completed_utc": "2026-10-20T23:54:02Z",
    "entry_written_utc": "2026-10-20T23:54:17Z",
    "file_sha256": "159da6d8faa3e542b0f13413910f8f2bc9f0011673da9276120be5aa7649edfb",
    "payload_sha256": "586efa310f69ab187638a988984f99434582442fe984eb05bd028e129d9a37c4",
    "results_sha256": "fbccbe504717e1415ad979aca83a440c40f643c2dedd613bfb58c54d0c85e7d9",
    "module_sha256": "1d63a6cf6c2c29eb581bf0b43129ed53f27e2f2b99768f7358b8bdd92916b352",
    "statement": "Written before anyone had read the numbers; the run printed only hashes.",
}

# ---------------------------------------------------------------------------------------
# Clock (days from 2026-10-07 00:00 WAT; onset is day 0).
# ---------------------------------------------------------------------------------------
T0 = nc.T0
T_HOME_END = 4.0
T_ISO = (12.5, 13.5)
G_END = 15.0                   # day d of the daily table covers [d, d + 1), d = 0 to 14
T_LIST = 13.0                  # the listed-contact detection boundary, as written in the rules
REPORT_DELAY = 0.5
CONDITIONING_DATE = nc.CONDITIONING_DATE
WINDOW_FIRST, WINDOW_LAST, REPORT_BY = nc.WINDOW_FIRST, nc.WINDOW_LAST, nc.REPORT_BY
day_end = nc.day_end
T_WINDOW_OPEN = day_end(WINDOW_FIRST - dt.timedelta(days=1))    # 14.0
T_CONFIRM_END = day_end(WINDOW_LAST)                              # 42.0
T_REPORT_END = day_end(REPORT_BY)                                 # 45.0
T_AS_OF = day_end(CONDITIONING_DATE)                              # 14.0, the end of 20 October
COND_CUTOFF = T_AS_OF - REPORT_DELAY                              # confirmations reported by the end of 20 Oct
CURVE_DATES = list(nc.CURVE_DATES)
ONSET_FIRST, ONSET_LAST = dt.date(2026, 10, 8), dt.date(2026, 11, 17)
N6_DATES = list(CURVE_DATES)

# ---------------------------------------------------------------------------------------
# Registered inputs (rules section 3).
# ---------------------------------------------------------------------------------------
F_SURVIVOR = nn.F_SURVIVOR
F_FATAL = nn.F_FATAL
W_FATAL = (0.0, 0.3)
Q = (0.10, 0.50)
ANCHOR_A = {
    "weight": 0.5,
    "uganda": {"secondary": 5, "health_workers": 4, "days": 3.0},
    "kenya": {"secondary": 8, "health_workers": 3, "days": (2.0, 2.5)},
    "zero_secondary_days_x": (0.0, 10.0),
    "lambda_gamma": "shape 5 + 8 + 0.5 = 13.5, rate 3 + D_K + X",
    "s_w_beta": (8, 7),
    "ipc_multiplier_m_a": (0.6, 1.2),
}
ANCHOR_B = {
    "weight": 0.5,
    "h_gamma_shape_rate": (22.5, 51.0),
    "tshopo": {"health_workers": 22, "confirmed": 73, "date": "2026-10-18"},
    "facility_days_d_t": (2.0, 6.0),
    "case_relative_q_b": (0.4, 1.0),
    "ipc_multiplier_m_b": (0.5, 1.0),
    "health_worker_share_s_b": (0.3, 0.6),
}
M_HOME = (0.8, 1.5)
M_VHF = (0.05, 0.25)
K_R = (1.0, 0.5)               # lognormal median, log-sd
UNLISTED = (0.15, 0.50)        # H and C; W uses half
LAG_LISTED_PRE_MEAN = 1.5      # from day 13, onset before day 13
LAG_LISTED_POST_MEAN = 1.0     # from onset, onset after day 13
LAG_UNLISTED_MEAN = 5.0        # from max(onset, day 13)
P_DETECT = (0.85, 1.0)
SECOND_GENERATION = {"r2_lognormal_median_logsd": (0.8, 0.6), "k": 0.3, "listed_factor_after_day_13": 0.2}
BACKGROUND = {"imports_per_week": nc.BACKGROUND["imports_per_week"], "p_local": nc.BACKGROUND["p_local"],
              "delay_shape": 10, "delay_mean": 16.0, "imports_from": 13.0}
PATHWAYS = {
    "H": "Home and community, days 0 to 4",
    "W": "Health workers at the facility, unsuspected care and after isolation",
    "C": "Other patients, caregivers and visitors at the facility, unsuspected care and after isolation",
    "G2": "Second generation: onward cases from first-generation cases",
    "P6": "Later importation: a case acquired in Nigeria from a later, separate importation",
}
PATHWAY_KEYS = tuple(PATHWAYS)
DEATH = nc.DEATH               # v3 (C8): if death occurs, days 14 to 24 (alive and stable at day 13)
BURIAL_SPAN = nc.BURIAL_SPAN


def incubation_prior() -> tuple[float, float]:
    """The programme's decided Bundibugyo incubation prior, read at run time."""
    return tuple(priors.BUNDIBUGYO_PRIORS_STAGE_TWO.incubation_gamma)


# ---------------------------------------------------------------------------------------
# Infectiousness shape g(t) and exposure in peak-day units.
# ---------------------------------------------------------------------------------------
def profile(w_fatal: float) -> tuple[float, ...]:
    """Daily g on [d, d + 1): the mixed table normalised so that its highest day equals 1."""
    vals = [(1.0 - w_fatal) * a + w_fatal * b for a, b in zip(F_SURVIVOR, F_FATAL)]
    top = max(vals)
    return tuple(v / top for v in vals)


SURVIVOR_UNIT = tuple(v / sum(F_SURVIVOR) for v in F_SURVIVOR)   # a full survivor course integrates to 1


def integral(vals: Sequence[float], a: float, b: float) -> float:
    """Integral of the daily step function over [a, b]."""
    a, b = max(a, 0.0), min(b, float(len(vals)))
    total = 0.0
    d = int(a)
    while d < b:
        lo, hi = max(a, float(d)), min(b, float(d + 1))
        if hi > lo:
            total += vals[d] * (hi - lo)
        d += 1
    return total


def window_exposure(vals: Sequence[float], t_iso: float) -> dict[str, float]:
    return {"home": integral(vals, 0.0, T_HOME_END), "facility": integral(vals, T_HOME_END, t_iso),
            "isolation": integral(vals, t_iso, G_END)}


def _sample_segments(rng: random.Random, segments: Sequence[tuple[float, float, float]]) -> float:
    """A point from the piecewise-constant density given as (a, b, density) segments."""
    total = sum((b - a) * f for a, b, f in segments)
    target = rng.random() * total
    acc = 0.0
    for a, b, f in segments:
        mass = (b - a) * f
        if acc + mass >= target and mass > 0.0:
            return a + (target - acc) / f
        acc += mass
    return segments[-1][1]


def sample_time(rng: random.Random, vals: Sequence[float], a: float, b: float) -> float:
    """An infection time on [a, b] following g."""
    segs = []
    d = int(a)
    while d < b and d < len(vals):
        lo, hi = max(a, float(d)), min(b, float(d + 1))
        if hi > lo and vals[d] > 0.0:
            segs.append((lo, hi, vals[d]))
        d += 1
    if not segs:
        return a
    return _sample_segments(rng, segs)


def onward_segments(onset: float, end: float, listed: bool,
                    t_list: float = T_LIST) -> list[tuple[float, float, float]]:
    """Survivor-shape infectiousness of a first-generation case over its illness, in absolute time.

    From onset to ``end`` (its confirmation, or the end of a full course if never confirmed), capped at a
    full course; for a listed case the part after day 13 is weighted by the monitoring factor.
    """
    span = min(end - onset, G_END)
    cut = t_list - onset if listed else math.inf
    factor = SECOND_GENERATION["listed_factor_after_day_13"]
    segs = []
    if span <= 0.0:
        return segs
    for d in range(int(math.ceil(span))):
        lo, hi = float(d), min(float(d + 1), span)
        if hi <= lo:
            continue
        dens = SURVIVOR_UNIT[d]
        for a, b, f in ((lo, min(hi, cut), 1.0), (max(lo, cut), hi, factor)):
            if b > a:
                segs.append((onset + a, onset + b, dens * f))
    return segs


# ---------------------------------------------------------------------------------------
# Options, parameter draws and one forward run.
# ---------------------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "anchor_a_weight": ANCHOR_A["weight"], "q": Q, "x": ANCHOR_A["zero_secondary_days_x"],
    "m_a": ANCHOR_A["ipc_multiplier_m_a"], "m_b": ANCHOR_B["ipc_multiplier_m_b"], "k_r_fixed": None,
    "unlisted": UNLISTED, "incubation": None, "conditioning": True, "kenya_health_workers_only": False,
    "w_fatal": W_FATAL,
}


def options(overrides: Mapping[str, Any] | None = None) -> dict:
    o = dict(overrides or {})
    unknown = set(o) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown option keys: {sorted(unknown)}")
    opts = {**DEFAULTS, **o}
    if opts["incubation"] is None:
        opts["incubation"] = incubation_prior()
    return opts


def draw_parameters(rng: random.Random, opts: Mapping[str, Any]) -> dict:
    t_iso = kc._uniform(rng, T_ISO)
    w_fatal = kc._uniform(rng, opts["w_fatal"])
    vals = profile(w_fatal)
    g = window_exposure(vals, t_iso)
    q = kc._uniform(rng, opts["q"])
    if opts["kenya_health_workers_only"]:
        d_k, shape, s_w_ab = 2.0, 5 + 3 + 0.5, (8, 2)
    else:
        d_k, shape, s_w_ab = kc._uniform(rng, ANCHOR_A["kenya"]["days"]), 5 + 8 + 0.5, ANCHOR_A["s_w_beta"]
    x = kc._uniform(rng, opts["x"])
    lam = ke._gamma_any(rng, shape) / (ANCHOR_A["uganda"]["days"] + d_k + x)
    s_w = ke._beta(rng, *s_w_ab)
    m_a = kc._uniform(rng, opts["m_a"])
    m_home = kc._uniform(rng, M_HOME)
    h = ke._gamma_any(rng, ANCHOR_B["h_gamma_shape_rate"][0]) / ANCHOR_B["h_gamma_shape_rate"][1]
    d_t = kc._uniform(rng, ANCHOR_B["facility_days_d_t"])
    q_b = kc._uniform(rng, ANCHOR_B["case_relative_q_b"])
    m_b = kc._uniform(rng, opts["m_b"])
    s_b = kc._uniform(rng, ANCHOR_B["health_worker_share_s_b"])
    anchor = "A" if rng.random() < opts["anchor_a_weight"] else "B"
    m_vhf = kc._uniform(rng, M_VHF)
    k_r = opts["k_r_fixed"] if opts["k_r_fixed"] is not None else kc._lognormal(rng, *K_R)
    z = ke._gamma_any(rng, k_r) / k_r
    u = kc._uniform(rng, opts["unlisted"])
    p_detect = kc._uniform(rng, P_DETECT)
    r2 = kc._lognormal(rng, *SECOND_GENERATION["r2_lognormal_median_logsd"])
    lam_bg = kc._uniform(rng, BACKGROUND["imports_per_week"]) / 7.0 * kc._uniform(rng, BACKGROUND["p_local"])

    home = lam * (1.0 - s_w) * q * g["home"] * m_home
    if anchor == "A":
        w_fac = lam * s_w * q * g["facility"] * m_a
        c_fac = lam * (1.0 - s_w) * q * g["facility"] * m_a
        w_iso = lam * s_w * q * g["isolation"] * m_vhf
        c_iso = lam * (1.0 - s_w) * q * g["isolation"] * m_vhf
    else:
        per_case = h * ((t_iso - T_HOME_END) / d_t) * q_b
        w_fac = per_case * m_b
        c_fac = w_fac * (1.0 - s_b) / s_b
        w_iso = per_case * (g["isolation"] / g["facility"]) * m_vhf
        c_iso = w_iso * (1.0 - s_b) / s_b
    means = {"H": home, "W_facility": w_fac, "C_facility": c_fac, "W_isolation": w_iso, "C_isolation": c_iso}
    return {"t_iso": t_iso, "w_fatal": w_fatal, "vals": vals, "g": g, "anchor": anchor, "z": z, "k_r": k_r,
            "means": {key: v * z for key, v in means.items()}, "u": u, "p_detect": p_detect, "r2": r2,
            "lam_bg": lam_bg, "incubation": tuple(opts["incubation"])}


WINDOWS = ("H", "W_facility", "C_facility", "W_isolation", "C_isolation")


def _window(key: str, t_iso: float) -> tuple[float, float]:
    if key == "H":
        return 0.0, T_HOME_END
    if key.endswith("facility"):
        return T_HOME_END, t_iso
    return t_iso, G_END


def confirm_time(rng: random.Random, onset: float, listed: bool, t_list: float = T_LIST) -> float:
    if not listed:
        return max(onset, t_list) + kc._erlang(rng, 2, 2.0 / LAG_UNLISTED_MEAN)
    if onset < t_list:
        return t_list + kc._erlang(rng, 2, 2.0 / LAG_LISTED_PRE_MEAN)
    return onset + kc._erlang(rng, 2, 2.0 / LAG_LISTED_POST_MEAN)


def _first_case(rng: random.Random, p: Mapping[str, Any], key: str, t_inf: float, unlisted: float,
                first: list, second: list) -> None:
    """Onset, listing and confirmation of one first-generation case, then its second-generation cases."""
    inc, u, p_det = p["incubation"], p["u"], p["p_detect"]
    t_list = p.get("t_list", T_LIST)
    k2 = SECOND_GENERATION["k"]
    onset = t_inf + nc._gamma(rng, *inc)
    listed = rng.random() >= unlisted
    conf = confirm_time(rng, onset, listed, t_list) if rng.random() < p_det else math.inf
    first.append((key, t_inf, onset, conf))
    segs = onward_segments(onset, conf, listed, t_list)
    f_share = sum((b - a) * f for a, b, f in segs)
    if f_share <= 0.0:
        return
    for _ in range(nc._poisson(rng, p["r2"] * f_share * ke._gamma_any(rng, k2) / k2)):
        t2 = _sample_segments(rng, segs)
        onset2 = t2 + nc._gamma(rng, *inc)
        listed2 = rng.random() >= u
        conf2 = confirm_time(rng, onset2, listed2, t_list) if rng.random() < p_det else math.inf
        second.append((t2, onset2, conf2))


def simulate_run(rng: random.Random, p: Mapping[str, Any]) -> dict:
    """One forward realisation of a draw: every case's group, infection, onset and confirmation."""
    u = p["u"]
    first, second, local = [], [], []
    for key in WINDOWS:
        lo, hi = _window(key, p["t_iso"])
        unlisted = u / 2.0 if key.startswith("W") else u
        for _ in range(nc._poisson(rng, p["means"][key])):
            _first_case(rng, p, key, sample_time(rng, p["vals"], lo, hi), unlisted, first, second)
    if "p_death" in p and rng.random() < p["p_death"]:          # v3 only (C8): death and a safe burial
        death = kc._uniform(rng, DEATH)
        for _ in range(nc._poisson(rng, p["funeral_mean"])):
            _first_case(rng, p, "funeral", death + kc._uniform(rng, (0.0, BURIAL_SPAN)), u, first, second)
    span = T_CONFIRM_END - BACKGROUND["imports_from"]
    shape = BACKGROUND["delay_shape"]
    for _ in range(nc._poisson(rng, p["lam_bg"] * span)):
        arrival = BACKGROUND["imports_from"] + span * rng.random()
        local.append(arrival + kc._erlang(rng, shape, shape / BACKGROUND["delay_mean"]))
    return {"first": first, "second": second, "local": local}


# ---------------------------------------------------------------------------------------
# Run records and weighted accumulation.
# ---------------------------------------------------------------------------------------
def _group(key: str) -> str:
    return key if key == "funeral" else key[0]


def record(run: Mapping[str, Any], full: bool = True, pathways: Sequence[str] = PATHWAY_KEYS) -> dict:
    """The per-run quantities every summary uses."""
    confs = [(c, _group(k)) for k, _, _, c in run["first"] if c < math.inf]
    confs += [(c, "G2") for _, _, c in run["second"] if c < math.inf]
    confs += [(c, "P6") for c in run["local"]]
    confs.sort()
    t_first = confs[0][0] if confs else math.inf
    qualifying = [(c, pw) for c, pw in confs if T_WINDOW_OPEN < c <= T_CONFIRM_END]
    rec = {"t_first": t_first, "none_by_20": t_first > COND_CUTOFF, "yes": bool(qualifying),
           "gen1_all": len(run["first"]), "gen1_hcw_all": sum(1 for k, *_ in run["first"] if k.startswith("W"))}
    if not full:
        return rec
    by = defaultdict(int)
    after = 0
    for key, t_inf, _, _ in run["first"]:
        if t_inf <= T_AS_OF:
            by[key] += 1
        else:
            after += 1
    rec.update({
        "n1": {"H": by["H"], "W": by["W_facility"] + by["W_isolation"], "C": by["C_facility"] + by["C_isolation"],
               "W_facility": by["W_facility"], "W_isolation": by["W_isolation"],
               "C_facility": by["C_facility"], "C_isolation": by["C_isolation"]},
        "after_20_october": after,
        "n3_gen1": sum(1 for _, _, o, _ in run["first"] if o <= T_AS_OF),
        "n3_gen2": sum(1 for _, o, _ in run["second"] if o <= T_AS_OF),
        "n4": sum(1 for t, _, _ in run["second"] if t <= T_AS_OF),
        "gen2_all": len(run["second"]),
        "onsets_gen1": [o for _, _, o, _ in run["first"]],
        "onsets_gen2": [o for _, o, _ in run["second"]],
        "confs": [c for c, _ in confs],
        "qualifying_by_pathway": {pw: sum(1 for _, q in qualifying if q == pw) for pw in pathways},
        "first_qualifying": qualifying[0][1] if qualifying else None,
        "never_confirmed": sum(1 for *_, c in run["first"] if c == math.inf) + sum(
            1 for *_, c in run["second"] if c == math.inf),
    })
    return rec


class Accumulator:
    """Weighted sums and histograms of the per-run quantities."""

    def __init__(self) -> None:
        self.w = 0.0
        self.hist: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self.sums: dict[str, float] = defaultdict(float)
        n_days = (ONSET_LAST - ONSET_FIRST).days + 1
        self.onsets = {"gen1": [0.0] * n_days, "gen2": [0.0] * n_days}
        self.onset_tails = {"gen1": [0.0, 0.0], "gen2": [0.0, 0.0]}

    def add(self, rec: Mapping[str, Any], w: float) -> None:
        if w == 0.0:
            return
        self.w += w
        h, s = self.hist, self.sums
        total = rec["n1"]["H"] + rec["n1"]["W"] + rec["n1"]["C"]
        h["n1_total"][total] += w
        for g, v in rec["n1"].items():
            h["n1_" + g][v] += w
        h["after_20_october"][rec["after_20_october"]] += w
        h["gen1_all"][rec["gen1_all"]] += w
        h["gen1_hcw_all"][rec["gen1_hcw_all"]] += w
        h["gen2_all"][rec["gen2_all"]] += w
        h["n3_gen1"][rec["n3_gen1"]] += w
        h["n3_gen2"][rec["n3_gen2"]] += w
        h["n3_total"][rec["n3_gen1"] + rec["n3_gen2"]] += w
        h["n4"][rec["n4"]] += w
        s["never_confirmed"] += w * rec["never_confirmed"]
        lo = day_end(ONSET_FIRST) - 1.0
        hi = day_end(ONSET_LAST)
        for gen in ("gen1", "gen2"):
            for o in rec["onsets_" + gen]:
                if o < lo:
                    self.onset_tails[gen][0] += w
                elif o >= hi:
                    self.onset_tails[gen][1] += w
                else:
                    self.onsets[gen][int(o - lo)] += w
        confs = rec["confs"]
        for date in N6_DATES:
            h["n6_" + date.isoformat()][bisect.bisect_right(confs, day_end(date) - REPORT_DELAY)] += w
        h["n6_by_end_of_20_october"][bisect.bisect_right(confs, COND_CUTOFF)] += w
        qual = rec["qualifying_by_pathway"]
        h["qualifying_total"][sum(qual.values())] += w
        for pw, c in qual.items():
            h["qualifying_" + pw][c] += w
        if rec["first_qualifying"] is not None:
            s["first_" + rec["first_qualifying"]] += w


def _mean(hist: Mapping[int, float]) -> float:
    total = sum(hist.values())
    return sum(k * v for k, v in hist.items()) / total if total else 0.0


def _p_at_least(hist: Mapping[int, float], n: int) -> float:
    total = sum(hist.values())
    return sum(v for k, v in hist.items() if k >= n) / total if total else 0.0


def _quantile(hist: Mapping[int, float], q: float) -> int:
    total = sum(hist.values())
    acc = 0.0
    for k in sorted(hist):
        acc += hist[k]
        if acc > q * total:
            return k
    return max(hist) if hist else 0


def _band(hist: Mapping[int, float]) -> dict:
    return {"mean": _mean(hist), "p10": _quantile(hist, 0.1), "p50": _quantile(hist, 0.5),
            "p90": _quantile(hist, 0.9)}


def _distribution(hist: Mapping[int, float], top: int = 10) -> dict:
    total = sum(hist.values())
    out = {str(k): hist.get(k, 0.0) / total for k in range(top)}
    out[f"{top}+"] = sum(v for k, v in hist.items() if k >= top) / total
    return out


def nowcast_summary(acc: Accumulator) -> dict:
    h = acc.hist
    n1 = {"total": {**_band(h["n1_total"]), "distribution": _distribution(h["n1_total"])}}
    for g in ("H", "W", "C"):
        n1[g] = {**_band(h["n1_" + g]), "distribution": _distribution(h["n1_" + g])}
    n1["parts_mean"] = {g: _mean(h["n1_" + g]) for g in ("W_facility", "W_isolation", "C_facility", "C_isolation")}
    n1["after_20_october_mean"] = _mean(h["after_20_october"])
    n1["first_generation_all_time_mean"] = _mean(h["gen1_all"])
    n2 = {"any": _p_at_least(h["n1_total"], 1), **{g: _p_at_least(h["n1_" + g], 1) for g in ("H", "W", "C")},
          "any_health_worker": _p_at_least(h["n1_W"], 1)}
    n3 = {**_band(h["n3_total"]), "p_at_least_one": _p_at_least(h["n3_total"], 1),
          "first_generation_mean": _mean(h["n3_gen1"]), "second_generation_mean": _mean(h["n3_gen2"])}
    n4 = {**_band(h["n4"]), "p_at_least_one": _p_at_least(h["n4"], 1),
          "second_generation_all_time_mean": _mean(h["gen2_all"])}
    days = [ONSET_FIRST + dt.timedelta(days=i) for i in range((ONSET_LAST - ONSET_FIRST).days + 1)]
    n5 = {"by_date": [{"date": d.isoformat(), "first_generation": a / acc.w, "second_generation": b / acc.w}
                      for d, a, b in zip(days, acc.onsets["gen1"], acc.onsets["gen2"])],
          "before_" + ONSET_FIRST.isoformat(): {g: acc.onset_tails[g][0] / acc.w for g in ("gen1", "gen2")},
          "after_" + ONSET_LAST.isoformat(): {g: acc.onset_tails[g][1] / acc.w for g in ("gen1", "gen2")}}
    rows = []
    for date in N6_DATES:
        hist = h["n6_" + date.isoformat()]
        band = _band(hist)
        rows.append({"date": date.isoformat(), "expected_cumulative": band["mean"],
                     "interval_80": [band["p10"], band["p90"]], "p_at_least_one": _p_at_least(hist, 1)})
    n6 = {"by_date": rows, "expected_reports_by_end_of_2026-10-20": _mean(h["n6_by_end_of_20_october"]),
          "expected_never_confirmed": acc.sums["never_confirmed"] / acc.w}
    return {"N1_infected_by_group_by_end_of_20_october": n1, "N2_p_at_least_one_infection": n2,
            "N3_symptomatic_by_end_of_20_october": n3, "N4_second_generation_infected_by_end_of_20_october": n4,
            "N5_onsets": n5, "N6_cumulative_reports": n6, "weight_share": None}


def forecast_summary(acc: Accumulator, pathways: Mapping[str, str] = PATHWAYS) -> dict:
    """Count tiers and the pathway decomposition, for the runs in ``acc``."""
    h = acc.hist
    yes_w = sum(acc.sums["first_" + pw] for pw in pathways)
    return {
        "p_yes": _p_at_least(h["qualifying_total"], 1),
        "p_at_least_3": _p_at_least(h["qualifying_total"], 3),
        "p_at_least_10": _p_at_least(h["qualifying_total"], 10),
        "expected_qualifying_cases": _mean(h["qualifying_total"]),
        "by_pathway": {pw: {"expected_qualifying_cases": _mean(h["qualifying_" + pw]),
                            "p_at_least_one": _p_at_least(h["qualifying_" + pw], 1), "label": pathways[pw]}
                       for pw in pathways},
        "shares_of_yes_by_first_qualifying_case": {pw: (acc.sums["first_" + pw] / yes_w if yes_w else 0.0)
                                                   for pw in pathways},
    }


# ---------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------
def run(overrides: Mapping[str, Any] | None = None, n: int | None = None, seed: int | None = None,
        full: bool = True, runs_per_draw: int = RUNS_PER_DRAW, ruleset: str = "v2") -> dict:
    rs = RULESETS[ruleset]
    n = rs["n_draws"] if n is None else n
    seed = rs["seed"] if seed is None else seed
    opts = rs["options"](overrides)
    draw = rs["draw"]
    pathways = rs["pathways"]
    rng = random.Random(seed)
    t_firsts: list[float] = []
    per_draw: list[tuple[int, int, int]] = []     # (runs with no report by 20 Oct, of them YES, YES unconditioned)
    gen1_any = gen1_sum = hcw_sum = 0
    anchor_a = 0
    acc_u, acc_c = (Accumulator(), Accumulator()) if full else (None, None)
    for _ in range(n):
        p = draw(rng, opts)
        anchor_a += p["anchor"] == "A"
        b = a = y = 0
        for _ in range(runs_per_draw):
            rec = record(simulate_run(rng, p), full, tuple(pathways))
            t_firsts.append(rec["t_first"])
            none = rec["none_by_20"]
            b += none
            a += none and rec["yes"]
            y += rec["yes"]
            gen1_any += rec["gen1_all"] > 0
            gen1_sum += rec["gen1_all"]
            hcw_sum += rec["gen1_hcw_all"]
            if full:
                acc_u.add(rec, 1.0)
                acc_c.add(rec, 1.0 if none else 0.0)
        per_draw.append((b, a, y))
    total_runs = n * runs_per_draw
    sum_b = sum(d[0] for d in per_draw)
    sum_a = sum(d[1] for d in per_draw)
    q_cond = sum_a / sum_b
    q_uncond = sum(d[2] for d in per_draw) / total_runs
    se = math.sqrt(sum((a - q_cond * b) ** 2 for b, a, _ in per_draw)) / sum_b
    headline = q_cond if opts["conditioning"] else q_uncond
    out = {"headline": headline, "conditioned": q_cond, "unconditioned": q_uncond, "monte_carlo_se": se,
           "share_of_runs_with_no_report_by_20_october": sum_b / total_runs,
           "share_of_draws_on_anchor_a": anchor_a / n,
           "first_generation_unconditioned": {"p_at_least_one_infection": gen1_any / total_runs,
                                              "expected_infections": gen1_sum / total_runs,
                                              "expected_health_worker_infections": hcw_sum / total_runs},
           "n_draws": n, "runs_per_draw": runs_per_draw}
    if not full:
        return out
    ordered = sorted(t_firsts)
    beyond = len(ordered) - bisect.bisect_right(ordered, T_CONFIRM_END)
    curve = []
    for date in CURVE_DATES:
        alive = len(ordered) - bisect.bisect_right(ordered, day_end(date) - REPORT_DELAY)
        curve.append({"date": date.isoformat(), "p": 1.0 - beyond / alive if alive else 0.0})
    out["curve"] = curve
    out["forecast_conditioned"] = forecast_summary(acc_c, pathways)
    out["forecast_unconditioned"] = forecast_summary(acc_u, pathways)
    now_u, now_c = nowcast_summary(acc_u), nowcast_summary(acc_c)
    now_u["weight_share"], now_c["weight_share"] = 1.0, acc_c.w / acc_u.w
    out["nowcast"] = {"unconditioned": now_u, "conditioned": now_c}
    return out


# ---------------------------------------------------------------------------------------
# Benchmarks and leave-one-out (rules section 4).
# ---------------------------------------------------------------------------------------
def benchmarks() -> dict:
    return {
        "B1_pooled_destination_import_rate": {"probability": kc._jeffreys(2, 17), "imports": 17, "seeded": 2,
                                              "rule": "Jeffreys over this outbreak's destination imports: (2 + 0.5) / (17 + 1)"},
        "B2_unsuspected_care_destination_subgroup": {"probability": kc._jeffreys(2, 2), "imports": 2, "seeded": 2,
                                                     "rule": "Jeffreys within the unsuspected-care subgroup: (2 + 0.5) / (2 + 1)"},
        "note": "Reference rates, not forecasts for Nigeria, and not used to tune the model.",
    }


def _negbin_mean_tail(shape: float, rate: float, exposure: float, observed: int) -> tuple[float, float]:
    """Poisson-gamma predictive count over ``exposure`` days: mean and P(count >= observed)."""
    p = rate / (rate + exposure)
    cdf = 0.0
    for k in range(observed):
        cdf += math.exp(math.lgamma(shape + k) - math.lgamma(shape) - math.lgamma(k + 1)
                        + shape * math.log(p) + k * math.log1p(-p))
    return shape * exposure / rate, 1.0 - cdf


def leave_one_out(grid: int = 200) -> dict:
    xs = [ANCHOR_A["zero_secondary_days_x"][0] + (i + 0.5) / grid * (ANCHOR_A["zero_secondary_days_x"][1]
                                                                     - ANCHOR_A["zero_secondary_days_x"][0])
          for i in range(grid)]
    dks = [ANCHOR_A["kenya"]["days"][0] + (i + 0.5) / grid * (ANCHOR_A["kenya"]["days"][1] - ANCHOR_A["kenya"]["days"][0])
           for i in range(grid)]
    uga, ken = ANCHOR_A["uganda"], ANCHOR_A["kenya"]
    rows = {}
    m = t = 0.0
    for dk in dks:
        for x in xs:
            mean, tail = _negbin_mean_tail(ken["secondary"] + 0.5, dk + x, uga["days"], uga["secondary"])
            m += mean
            t += tail
    rows["uganda"] = {"observed": uga["secondary"], "exposure_days": uga["days"], "predictive_mean": m / grid ** 2,
                      "p_at_least_observed": t / grid ** 2,
                      "fit_without_it": "lambda_L ~ Gamma(8 + 0.5, rate D_K + X)"}
    m = t = 0.0
    for dk in dks:
        for x in xs:
            mean, tail = _negbin_mean_tail(uga["secondary"] + 0.5, uga["days"] + x, dk, ken["secondary"])
            m += mean
            t += tail
    rows["kenya"] = {"observed": ken["secondary"], "exposure_days": list(ken["days"]), "predictive_mean": m / grid ** 2,
                     "p_at_least_observed": t / grid ** 2,
                     "fit_without_it": "lambda_L ~ Gamma(5 + 0.5, rate 3 + X)"}
    rows["france"] = {"observed": 0, "exposure_days": 0.0, "predictive_mean": 0.0,
                      "statement": ("Isolated at the first report of symptoms, so zero days of unsuspected care and a "
                                    "predicted mean of 0; consistent with no secondary case, and not a test of the rate.")}
    rows["note"] = ("Late-stage case (q = 1), the stated days of unsuspected care, no IPC multiplier, no infectiousness "
                    "shape and no individual heterogeneity Z, so the predictive spread is narrower than the model's. "
                    "D_K and X are integrated on a deterministic grid.")
    return rows


# ---------------------------------------------------------------------------------------
# v1 values shown beside v2 (read from the frozen artifacts, pinned by hash).
# ---------------------------------------------------------------------------------------
V1_FORECAST = {"artifact": "data/case-conditioned/nigeria-2026-10-20-forecast.json",
               "payload_sha256": "9bbed174c5018e6d70ad219afc6c70b0e297b06a60975458b0fa850b1bffdeb8"}
V1_NOWCAST = {"artifact": "data/case-conditioned/nigeria-2026-10-20-nowcast.json",
              "payload_sha256": "b21578ef22fd944447870f65f0f42037df286c52f5cb318eefd496211e45838f"}


def v1_beside() -> dict:
    fc = json.loads((REPO / V1_FORECAST["artifact"]).read_text(encoding="utf-8"))
    now = json.loads((REPO / V1_NOWCAST["artifact"]).read_text(encoding="utf-8"))
    for art, pin in ((fc, V1_FORECAST), (now, V1_NOWCAST)):
        if art["payload_sha256"] != pin["payload_sha256"] or kc.payload_digest(art) != pin["payload_sha256"]:
            raise ValueError(f"v1 artifact changed: {pin['artifact']}")
    n7 = now["results"]["N7_cross_check"]
    return {
        "v1_forecast": {**V1_FORECAST, "model_version": fc["model_version"],
                        "headline": fc["headline"]["probability"], "interval_80": fc["headline"]["interval_80"],
                        "unconditioned": fc["headline"]["unconditioned"]},
        "v1_nowcast": {**V1_NOWCAST, "model_version": now["model_version"],
                       "N7_p_at_least_one_report_by_2026-11-17": n7["p_at_least_one_report_by_2026-11-17"],
                       "N1_expected_infections": now["results"]["N1_infected_by_group"]["total"]["mean"]},
        "note": ("Both v1 records stay frozen and are scored as registered. They are shown beside v2 and are never "
                 "averaged with it. The v1 nowcast value has no conditioning and counts first-generation reports by "
                 "17 November, so it is not the same quantity as either v2 value."),
    }


# ---------------------------------------------------------------------------------------
# Sensitivity rows (rules section 5) and the artifact.
# ---------------------------------------------------------------------------------------
SENSITIVITY = [
    ("R1_anchor_a_only", "Anchor A only", {"anchor_a_weight": 1.0}),
    ("R2_anchor_b_only", "Anchor B only", {"anchor_a_weight": 0.0}),
    ("R3_q_005_015", "q 0.05 to 0.15", {"q": (0.05, 0.15)}),
    ("R4_q_05_10", "q 0.5 to 1.0", {"q": (0.5, 1.0)}),
    ("R5_x_0", "X fixed at 0", {"x": (0.0, 0.0)}),
    ("R6_x_10_30", "X 10 to 30", {"x": (10.0, 30.0)}),
    ("R7_ipc_03_06", "M_A and M_B 0.3 to 0.6 (better precautions than assumed)", {"m_a": (0.3, 0.6), "m_b": (0.3, 0.6)}),
    ("R8_k_r_03", "k_r fixed at 0.3", {"k_r_fixed": 0.3}),
    ("R9_k_r_3", "k_r fixed at 3", {"k_r_fixed": 3.0}),
    ("R10_unlisted_005_015", "u 0.05 to 0.15", {"unlisted": (0.05, 0.15)}),
    ("R11_unlisted_05_08", "u 0.5 to 0.8", {"unlisted": (0.5, 0.8)}),
    ("R12_incubation_mean_10", "Incubation mean 10 days (shape 3, rate 0.3)", {"incubation": (3.0, 0.3)}),
    ("R13_no_conditioning", "No conditioning", {"conditioning": False}),
    ("R14_kenya_health_workers_only", "Kenya's event counted as its 3 health workers only, over 2 days",
     {"kenya_health_workers_only": True}),
    ("R15_survivor_shape_only", "Survivor shape only (w_fatal 0)", {"w_fatal": (0.0, 0.0)}),
    ("R16_baseline_at_sensitivity_sample", "Baseline at the sensitivity sample size", {}),
]


def sensitivity_rows(n: int = N_DRAWS_SENSITIVITY, ruleset: str = "v2") -> list[dict]:
    rows = []
    for name, label, over in RULESETS[ruleset]["sensitivity"]:
        res = run(over, n=n, full=False, ruleset=ruleset)
        rows.append({"variant": name, "label": label, "headline": res["headline"],
                     "unconditioned": res["unconditioned"], "monte_carlo_se": res["monte_carlo_se"],
                     **res["first_generation_unconditioned"]})
    return rows


CITATIONS = {
    "rules": "Internal dated rules file, sha256 in registration; restated in inputs",
    "evidence": "Internal outbreak facility evidence extraction, sha256 in registration; each fact cites its source",
    "uganda_event": ("Uganda's index import in May 2026: not suspected during about 3 days of care, 4 health workers and "
                     "1 transport contact infected (the Director General of Health Services, relayed by national "
                     "media; admission and death dates in WHO Disease Outbreak News and Kayiwa et al., Emerg Infect Dis "
                     "2026;32(10))"),
    "uganda_imports": "WHO Disease Outbreak News DON612 and DON614: 15 imported and 5 locally acquired cases in Uganda",
    "kenya_event": ("Kenya Ministry of Health statements of 17 to 20 October 2026 and Africa CDC: 8 cases linked to the "
                    "first case, 3 of them health workers who managed it in hospital on 3 and 4 October"),
    "france": "WHO DON612 and ECDC, 24 June 2026: one import isolated at first report of symptoms; no secondary case",
    "tshopo": ("INSP SitReps 151 and 157 (12 and 18 October 2026): 22 frontline workers among 73 confirmed cases in "
               "Tshopo; 2 to 5 of 104 facilities above an 80% IPC score"),
    "kivu_2018": "Baller et al. 2022: health workers 27.6% of nosocomial cases, Kivu 2018 to 2020",
    "lanini2015": nn.CITATIONS["lanini2015"],
    "who_ert2014": nn.CITATIONS["who_ert2014"],
    "infectiousness_table": "The registered Nigeria nowcast evidence synthesis, section A3 (judgement on lanini2015 and who_ert2014)",
    "lovs_incubation_prior": nn.CITATIONS["lovs_incubation_prior"],
    "v1_forecast": "lovs/forecast/nigeria_conditioned.py and its artifact, frozen",
    "v1_nowcast": "lovs/forecast/nigeria_nowcast.py and its artifact, frozen",
    "registry": "data/international-events.json, report nga-2026-10-20, which records each source's address",
}
CASE_EVIDENCE = [
    {"fact": "One imported case of this outbreak confirmed by laboratory tests in the Federal Capital Territory, announced on 20 October; the case is in isolation",
     "source": "Coordinating Minister of Health and Social Welfare and WHO Regional Office for Africa, 20 October (registry)"},
    {"fact": "Symptoms began on 7 October (day 0); care was sought at a private facility on 11 October (day 4), where Ebola was not suspected",
     "source": "registry"},
    {"fact": "Laboratory confirmation and isolation on 20 October (T_iso, day 12.5 to 13.5); alive and described as stable that day",
     "source": "registry"},
    {"fact": "44 contacts listed at announcement, including health workers, patients and caregivers", "source": "registry"},
    {"fact": "No authority has stated where the first case was infected, and this model does not assume it", "source": "all sources"},
]
PROVENANCE = {
    "registration": "registered: the rules hash and time recorded on the wiki precommitment page",
    "clock": "case evidence: onset 7 Oct, care 11 Oct, confirmation and isolation 20 Oct; T_iso's one-day width is judgement",
    "profile": "judgement: the one historical shape kept (no Bundibugyo series exists), the A3 table read with day d on [d, d + 1)",
    "q": "judgement: this case's peak day relative to one day of care of a late-stage, severe case; the main transfer assumption",
    "anchor_a": "this outbreak: Uganda's and Kenya's seeding events; X, M_A and D_K's width are judgement",
    "anchor_b": "this outbreak: Tshopo's health workers per confirmed case; D_T, q_B and M_B are judgement; s_B's ends are Kivu 2018 to 2020 and this outbreak's destinations",
    "home": "registered: H from anchor A's non-health-worker rate with a home multiplier (judgement)",
    "after_isolation": "judgement: the programme's VHF precaution multiplier",
    "heterogeneity": "judgement: the two seeding events are too few to estimate it",
    "incubation_gamma": "source: lovs_incubation_prior, read at run time",
    "detection": "judgement: listing, lags and detection probability as in the registered v1 nowcast, with u halved for health workers",
    "second_generation": "judgement: R2 and the monitoring factor; dispersion 0.3",
    "background": "registered: the v1 forecast's later-importation term, unchanged",
    "pathways": "the registered pathway definitions",
    "benchmarks": "the registered benchmark rules, from this outbreak's destination imports",
    "citations": "the published sources themselves",
    "provenance": "this map",
}


def _pct(p: float) -> str:
    return f"{round(100 * p)}%"


def headline_label(p: float) -> str:
    return (f"If no further case is reported in Nigeria by the end of 20 October: about {_pct(p)} under this model that "
            f"Nigeria reports a further confirmed case, not a new importation, confirmed 21 October to 17 November. A "
            f"forecast, not an assessment of the country's response.")


def conditioning_note(unconditioned: float, headline: float) -> str:
    shift = headline - unconditioned
    return (f"Without conditioning, the probability of a qualifying case is {unconditioned:.3f}; given no further case "
            f"reported by the end of 20 October it is {headline:.3f}, a change of {shift:+.3f}. Under the detection "
            f"rule, listed contacts already ill when listing began are confirmed from day 13, so part of the model's "
            f"mass would already have been reported by the end of 20 October.")


DISCLOSURES = [
    ("Registration: the rules (sha256 acf07202acb0dcf07ab2ac0ebd41a94ef6dc3737817d471540e59e21a44a0a3f) and the "
     "evidence extraction (sha256 79073bb975d124bfc8ef13c02d6a415c99b46103f328d3274b470a331acd75e2) were recorded on the "
     "wiki page reference:bdbv-nigeria-v2-precommitment-2026-10-21 at 2026-10-20T23:41:38Z (server time), before any v2 "
     "code existed or any v2 run. The rules file is internal; its rules are restated in the inputs."),
    ("What the author had seen when writing the rules: the v1 forecast headline 0.326 (0.374 unconditioned) and its "
     "pathway shares, and the v1 nowcast's 1.86 expected infections and 0.786. The rules state this."),
    ("Why v2 is the headline: on 2026-10-20 the founder ruled that this outbreak's own data should lead, with older "
     "studies used only where it is silent. The v1 facility terms came from older studies (Faye 2015; Kikwit 1995 and "
     "Dean 2016). Both v1 records stay frozen, are published as registered and are scored as registered."),
    ("Gaps in the rules were closed before any v2 code or run, each by a rule written down first: (V1) each draw is "
     "simulated forward in 4 runs sharing its parameters, and its conditioning weight is the share of runs with no "
     "further report through 20 October, an unbiased estimate of the registered weight, so no per-draw interval is "
     "given; (V2) a report is confirmation plus 0.5 days, the conditioning removes any modelled further case reported "
     "by the end of 20 October, a confirmation on 20 October reported on 21 October neither conditions nor qualifies, "
     "and the curve uses v1's set logic; (V3) onset at day 0.0 exactly; (V4) g is zero from day 15 and normalised per "
     "draw to its own highest day; (V5) after isolation a group's rate per peak-day unit, without its IPC multiplier, "
     "times G_iso and M_VHF, with anchor B's facility mean spread over G_fac; (V6) infection times follow g under both "
     "anchors; (V7) Z multiplies the first-generation means only; (V8) q enters anchor B draws only through H; (V9) "
     "row 14 uses Gamma(8.5, rate 5 + X) and s_W ~ Beta(8, 2), so H falls too; (V10) Kenya's 8 linked cases as "
     "registered, though the evidence table shows 7 through 18 October; (V11) health workers use u/2 in both "
     "windows, a second-generation case is listed with probability 1 - u, and the listed-contact boundary is day "
     "13.0; (V12) F covers the parent's illness from onset to confirmation, a full course if never confirmed, capped "
     "at 1, with the part after day 13 weighted 0.2 for a listed parent, and the child's infection time follows the "
     "same weighted density; (V13) every case is confirmed with probability p_detect, drawn once per draw; (V14) "
     "later importations arrive uniformly on days 13 to 42 and their local cases are always confirmed, with no onward "
     "cases; (V15) N1 and N4 count infections by the end of 20 October, N3 onsets by then, and N6 every modelled "
     "further report; (V16) sensitivity rows show first-generation, unconditioned infection quantities and use the "
     "registered seed; (V17) leave-one-out without Z, IPC multipliers or the infectiousness shape; (V18) the v1 "
     "values are read from their frozen artifacts after their payload hashes are verified."),
    ("q is a transfer assumption. No Bundibugyo viral-load series exists. Both seeding events in this outbreak were "
     "late-stage, wet, fatal patients, and this case was alive and stable at day 13."),
    ("Anchor A rests on two seeding events and on a judged amount of zero-secondary care among the other destination "
     "imports (X), whose days of care are unpublished. Anchor B transfers a DRC province's health-worker burden to a "
     "Nigerian private facility through judged multipliers. The two anchors are weighted equally."),
    ("The infectiousness shape is the one historical input kept: a Zaire and Sudan ebolavirus shape carried across to "
     "Bundibugyo virus by assumption."),
    ("Counts are expected numbers under this model: people probably already infected, never people known to be "
     "infected. Nothing here identifies a person or a facility, and no authority has said where the first case was "
     "infected; nothing here assumes it."),
    ("Third-generation cases are not modelled. The later-importation term (P6) is judgement, small, and reported "
     "separately."),
    ("Monte Carlo standard errors are clustered by draw. Bands and the sensitivity range cover parameter uncertainty "
     "inside this model only, not model-structure error."),
    ("This forecast is additive. Block 11, every Kenya forecast and both Nigeria v1 records stay frozen and are scored "
     "as registered, and this is not a calibration row unless the founder rules otherwise."),
    ("After the first full run only the Entry 1 registration record and this sentence were added. No number, input or "
     "parameter changed: the results sha256 is the one recorded in Entry 1, and the first run's payload sha256, "
     "586efa310f69ab187638a988984f99434582442fe984eb05bd028e129d9a37c4, is kept there beside it."),
]


def results_digest(results: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(results, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_artifact(main_run: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    marks = benchmarks()
    results = kc._rounded({
        "headline": {"probability": main_run["conditioned"], "unconditioned": main_run["unconditioned"],
                     "monte_carlo_se": main_run["monte_carlo_se"],
                     "share_of_runs_with_no_report_by_20_october": main_run["share_of_runs_with_no_report_by_20_october"],
                     "share_of_draws_on_anchor_a": main_run["share_of_draws_on_anchor_a"]},
        "S1_curve": main_run["curve"],
        "S2_count_tiers": {k: main_run["forecast_conditioned"][k] for k in ("p_yes", "p_at_least_3", "p_at_least_10")},
        "S3_decomposition": {"conditioned": main_run["forecast_conditioned"],
                             "unconditioned": main_run["forecast_unconditioned"]},
        "nowcast": main_run["nowcast"],
        "first_generation_unconditioned": main_run["first_generation_unconditioned"],
        "leave_one_out": leave_one_out(),
        "sensitivity": list(sensitivity),
    })
    head = results["headline"]
    payload = {
        "schema": "lovs-case-conditioned-forecast-nowcast/v1",
        "forecast_id": "nigeria-bdbv-2026-10-21-v2-further-case",
        "model_version": MODEL_VERSION,
        "question": ("Will Nigeria report at least one further laboratory-confirmed case of this outbreak, beyond the "
                     "first case confirmed on 2026-10-20, that is not a new importation, with confirmation dated "
                     "2026-10-21 to 2026-11-17 and first reported by an authority by 2026-11-20?"),
        "rules": {
            "same_as_v1": ("Identical to the registered v1 forecast's question, word for word: the authorities, what counts, "
                           "where the case falls ill, the void rule, the ambiguity ruling, scoring by Brier at a coverage "
                           "review between 2026-11-21 and 2026-12-05, and silence never resolving NO."),
            "registration_timing": ("Only reports first published after the v2 registration entry "
                                    "(2026-10-20T23:41:38Z) count for v2. A qualifying report published between the v1 "
                                    "entry and the v2 entry voids v2; none was known at registration."),
            "what_counts_in_the_model": ("Confirmation on days 14.0 to 42.0 (21 October to 17 November WAT), first report "
                                         "by day 45.0 (20 November). First- and second-generation cases from the first "
                                         "case and local cases from a later importation count; new importations do "
                                         "not and are not modelled."),
        },
        "relationship_to_v1": ("v2 is the headline. On 2026-10-20 the founder ruled that this outbreak's own data should "
                               "lead; v1's facility terms came from older studies. Both v1 records stay frozen, are "
                               "published and are scored as registered, and are shown beside v2 without averaging."),
        "registration": {**REGISTRATION, "entry_1_first_run": ENTRY_1},
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {"label": headline_label(head["probability"]), "probability": head["probability"],
                     "unconditioned": head["unconditioned"], "monte_carlo_se": head["monte_carlo_se"],
                     "conditioning_note": conditioning_note(head["unconditioned"], head["probability"]),
                     "benchmarks": marks},
        "v1_beside_v2": v1_beside(),
        "results": results,
        "results_sha256": results_digest(results),
        "curve_note": ("S1: each row is the probability of YES given that no further case has been reported by the end of "
                       "that date (WAT). The headline is the value given no report by the end of 20 October."),
        "quantities": {
            "Q": "the headline, conditioned on no further report through the end of 20 October WAT, and its unconditioned value",
            "S1": "the daily curve, 21 October to 17 November",
            "S2": "P(at least 3) and P(at least 10) further qualifying cases, conditioned",
            "S3": "expected qualifying cases, P(at least one) and first-qualifying-case shares by pathway H, W, C, G2, P6",
            "N1": "people infected by the first case by the end of 20 October, by group H, W, C",
            "N2": "probability of at least one such infection, overall and by group",
            "N3": "infected people with symptoms by the end of 20 October",
            "N4": "second-generation infections by the end of 20 October",
            "N5": "expected onsets by WAT date, 8 October to 17 November",
            "N6": "expected cumulative further reports by the end of each date, 21 October to 17 November, with an 80% band",
            "conditioning": "each nowcast quantity is given unconditioned and conditioned like Q",
        },
        "scoring_plan": {
            "scored_value": "the headline probability for 20 October, fixed by this artifact's hash",
            "scored_by": ("Brier score at a coverage review naming its sources, made between 2026-11-21 and 2026-12-05. "
                          "Silence never resolves NO."),
            "shown_beside": ["the curve value standing on the day of the first qualifying report",
                             "benchmarks B1 and B2", "both v1 values"],
            "published": "whatever the outcome",
            "wording": ("The result is reported as a probability beside the outcome. One outcome is not a verdict on "
                        "the forecast, cannot separate it from the benchmarks or from v1, and says nothing about "
                        "Nigeria's response."),
            "if_a_case_occurs": "say nothing about where, from whom or in which facility that case was infected",
        },
        "inputs": {
            "provenance": PROVENANCE,
            "registration": REGISTRATION,
            "clock": {"t0": "2026-10-07T00:00+01:00", "onset": 0.0, "home_window": [0.0, T_HOME_END],
                      "t_iso_uniform": T_ISO, "profile_end": G_END, "listed_boundary_day": T_LIST,
                      "report_delay": REPORT_DELAY, "window_confirmation": [T_WINDOW_OPEN, T_CONFIRM_END],
                      "report_by": T_REPORT_END, "conditioning_report_cutoff": T_AS_OF},
            "profile": {"f_survivor": F_SURVIVOR, "f_fatal": F_FATAL, "w_fatal_uniform": W_FATAL,
                        "reading": "day d covers [d, d + 1); mixed, then normalised so the highest day equals 1",
                        "exposure": "G_home over [0, 4], G_fac over [4, T_iso], G_iso over [T_iso, 15], in peak-day units"},
            "q": {"uniform": Q, "meaning": "this case's peak-day infectiousness relative to one day of care of a late-stage, severe case"},
            "anchor_a": {**ANCHOR_A, "means": ("W_A = lambda_L s_W q G_fac M_A; C_A = lambda_L (1 - s_W) q G_fac M_A")},
            "anchor_b": {**ANCHOR_B, "means": ("W_B = h ((T_iso - 4) / D_T) q_B M_B; C_B = W_B (1 - s_B) / s_B")},
            "home": {"m_home_uniform": M_HOME, "mean": "H = lambda_L (1 - s_W) q G_home M_home, for both anchors"},
            "after_isolation": {"m_vhf_uniform": M_VHF},
            "heterogeneity": {"z": "Gamma(k_r, scale 1 / k_r), one per draw", "k_r_lognormal_median_logsd": K_R},
            "incubation_gamma": incubation_prior(),
            "detection": {"unlisted_uniform_h_and_c": UNLISTED, "unlisted_w": "u / 2",
                          "listed_onset_before_day_13": f"day 13 + Erlang(2, mean {LAG_LISTED_PRE_MEAN})",
                          "listed_onset_after_day_13": f"onset + Erlang(2, mean {LAG_LISTED_POST_MEAN})",
                          "unlisted": f"max(onset, 13) + Erlang(2, mean {LAG_UNLISTED_MEAN})",
                          "report": "confirmation + 0.5 days", "p_detect_uniform": P_DETECT},
            "second_generation": SECOND_GENERATION,
            "background": BACKGROUND,
            "pathways": PATHWAYS,
            "benchmarks": marks,
            "citations": CITATIONS,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": DISCLOSURES + [conditioning_note(head["unconditioned"], head["probability"])],
        "sensitivity_note": ("Each row moves one registered input (20,000 draws, registered seed). headline is conditioned "
                             "through 20 October (row 13: unconditioned); the infection columns are first-generation, "
                             "over all time and unconditioned."),
        "method": {"seed": SEED, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS_SENSITIVITY,
                   "runs_per_draw": RUNS_PER_DRAW, "code": "lovs/forecast/nigeria_v2.py",
                   "reuses": ["lovs/forecast/nigeria_conditioned.py", "lovs/forecast/nigeria_nowcast.py",
                              "lovs/forecast/kenya_conditioned.py", "lovs/forecast/kenya_escape.py",
                              "lovs/lovs_priors_bundibugyo.py"]},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = kc.payload_digest(payload)
    return payload


# =======================================================================================
# v3: v2 corrected after the red team of the v2 rules (rules-v3.md, changes C1 to C10).
# The v2 code path above is unchanged, so the v2 artifact regenerates byte for byte.
# =======================================================================================
MODEL_VERSION_V3 = "nigeria_v3-v1.0.0"
SEED_V3 = 2026102103
DEFAULT_OUT_V3 = REPO / "data" / "case-conditioned" / "nigeria-2026-10-21-v3.json"
REGISTRATION_V3 = {
    "rules_v3_sha256": "f775452a466ff9a20ab85de5f414e94d77c3512a18476472be2dbb96f29b7566",
    "rules_v2_sha256": REGISTRATION["rules_sha256"],
    "evidence_sha256": REGISTRATION["evidence_sha256"],
    "red_team_of_v2_rules_sha256": "967247150a9c9731ff6129d0c9e79c24b0ce45551aebf48082c521a0651d20df",
    "wiki_page_id": REGISTRATION["wiki_page_id"],
    "entry_2_registered_utc": "2026-10-20T23:54:42Z",
    "note": ("v3 is v2 with ten listed changes answering an independent red team of the v2 rules, written blind to v2 "
             "output. The v3 rules were fixed before any v3 code existed and before their author had seen any v2 "
             "output; their sha256 was recorded on the wiki page at the time given. The rules files are internal; "
             "their rules are restated in this artifact's inputs."),
}
# Filled after the first full v3 run, from the precommitment page's Entry 3.
ENTRY_3: dict[str, str] | None = {
    "run_completed_utc": "2026-10-21T00:07:36Z", "entry_written_utc": "2026-10-21T00:08:25Z",
    "file_sha256": "a832f8db5b2bf689f1c4f3aa22b688a7c077c6f0176cebac871075f838930f58",
    "payload_sha256": "c3324be342cb6dacfed68c777e2c6c359601671ac388210a99dac158794521ed",
    "results_sha256": "eb510ee40157a5cd6d85ebbb18175fc97a0d4a5b12173d24597cee5d34ea8a2f",
    "module_sha256": "c08f7d1b7ed8d507deed5055fda57379fcab83a3dc93b3e2fa046fcff7d0a089",
    "statement": "Written before anyone had read the numbers; the run printed only hashes.",
}

Q_V3 = (0.03, 0.30)
X_V3 = (0.0, 30.0)
INFO_WEIGHT = 2.0 / 7.0          # two seeding events, seven documented facility infections
R_C = (0.2, 1.5)
R_H = (0.5, 2.0)
K_R_V3 = (0.3, 0.5)
P_DETECT_V3 = (0.95, 1.0)
T_LIST_V3 = 13.5
P_DEATH = nc.NIGERIA["p_death"]
ANCHOR_A_V3 = {
    "weight": 0.5,
    "uganda": {"facility_health_workers": 4, "days": 3.0,
               "excluded": "the transport contact, infected in an ambulance and not during care"},
    "kenya": {"linked_cases": 8, "facility_health_workers": 3, "days": (2.0, 2.5),
              "excluded": "the 5 linked cases who are not health workers, whose place of infection is unstated"},
    "zero_secondary_days_x": X_V3,
    "information_weight_w": "2/7: the two events are two observations, not seven",
    "lambda_w_gamma": "shape 7 w + 0.5, rate (3 + D_K + X) w",
    "ipc_multiplier_m_a": ANCHOR_A["ipc_multiplier_m_a"],
    "others_ratio_r_c": R_C,
}
ANCHOR_B_V3 = {
    "weight": 0.5,
    "h_gamma_shape_rate": ANCHOR_B["h_gamma_shape_rate"],
    "tshopo": {"health_workers": 22, "confirmed": 73, "deaths": 25, "fatality": "34%", "date": "2026-10-18"},
    "numerator_discount_a": (0.3, 0.8),
    "facility_days_d_t": (2.0, 6.0),
    "mean_shape_value_g_bar": (0.5, 0.9),
    "case_relative_q_b": (0.3, 0.8),
    "ipc_multiplier_m_b": ANCHOR_B["ipc_multiplier_m_b"],
    "others_ratio_r_c": "as anchor A",
    "selection": "Tshopo is the newly seeded DRC province chosen; it is also the province with the highest health-worker share",
}
FUNERAL_V3 = {"p_death_uniform": P_DEATH, "death_day_uniform": DEATH, "burial_span": BURIAL_SPAN,
              "setting_r_lognormal_median_low_high": kc.SETTING_R["fun"], "safe_burial_multiplier": kc.FUNERAL["SDB"]}
PATHWAYS_V3 = {
    "H": PATHWAYS["H"],
    "W": PATHWAYS["W"],
    "C": PATHWAYS["C"],
    "funeral": "Death and a safe burial, if death occurs (days 14 to 24)",
    "G2": PATHWAYS["G2"],
    "P6": PATHWAYS["P6"],
}

DEFAULTS_V3: dict[str, Any] = {
    "anchor_a_weight": ANCHOR_A_V3["weight"], "q": Q_V3, "x": X_V3, "info_weight": None,
    "m_a": ANCHOR_A["ipc_multiplier_m_a"], "m_b": ANCHOR_B["ipc_multiplier_m_b"], "k_r_fixed": None, "r_c": R_C,
    "unlisted": UNLISTED, "incubation": None, "conditioning": True, "kenya_all_linked": False, "w_fatal": W_FATAL,
}


def options_v3(overrides: Mapping[str, Any] | None = None) -> dict:
    o = dict(overrides or {})
    unknown = set(o) - set(DEFAULTS_V3)
    if unknown:
        raise ValueError(f"unknown option keys: {sorted(unknown)}")
    opts = {**DEFAULTS_V3, **o}
    if opts["incubation"] is None:
        opts["incubation"] = incubation_prior()
    return opts


def draw_parameters_v3(rng: random.Random, opts: Mapping[str, Any]) -> dict:
    t_iso = kc._uniform(rng, T_ISO)
    w_fatal = kc._uniform(rng, opts["w_fatal"])
    vals = profile(w_fatal)
    g = window_exposure(vals, t_iso)
    q = kc._uniform(rng, opts["q"])
    d_k = kc._uniform(rng, ANCHOR_A_V3["kenya"]["days"])
    x = kc._uniform(rng, opts["x"])
    days = ANCHOR_A_V3["uganda"]["days"] + d_k + x
    if opts["kenya_all_linked"]:                  # row 15: the v2 structure for anchor A (W3)
        w = 2.0 / 12.0 if opts["info_weight"] is None else opts["info_weight"]
        lam = ke._gamma_any(rng, 12 * w + 0.5) / (days * w)
        s_w = ke._beta(rng, 8, 6)
        lam_w = lam * s_w
    else:
        w = INFO_WEIGHT if opts["info_weight"] is None else opts["info_weight"]
        lam = lam_w = ke._gamma_any(rng, 7 * w + 0.5) / (days * w)
        s_w = None
    r_c = kc._uniform(rng, opts["r_c"])
    r_h = kc._uniform(rng, R_H)
    m_a = kc._uniform(rng, opts["m_a"])
    m_home = kc._uniform(rng, M_HOME)
    h = ke._gamma_any(rng, ANCHOR_B_V3["h_gamma_shape_rate"][0]) / ANCHOR_B_V3["h_gamma_shape_rate"][1]
    a = kc._uniform(rng, ANCHOR_B_V3["numerator_discount_a"])
    g_t = kc._uniform(rng, ANCHOR_B_V3["facility_days_d_t"]) * kc._uniform(rng, ANCHOR_B_V3["mean_shape_value_g_bar"])
    q_b = kc._uniform(rng, ANCHOR_B_V3["case_relative_q_b"])
    m_b = kc._uniform(rng, opts["m_b"])
    anchor = "A" if rng.random() < opts["anchor_a_weight"] else "B"
    m_vhf = kc._uniform(rng, M_VHF)
    k_r = opts["k_r_fixed"] if opts["k_r_fixed"] is not None else kc._lognormal(rng, *K_R_V3)
    z = ke._gamma_any(rng, k_r) / k_r
    u = kc._uniform(rng, opts["unlisted"])
    p_detect = kc._uniform(rng, P_DETECT_V3)
    r2 = kc._lognormal(rng, *SECOND_GENERATION["r2_lognormal_median_logsd"])
    lam_bg = kc._uniform(rng, BACKGROUND["imports_per_week"]) / 7.0 * kc._uniform(rng, BACKGROUND["p_local"])
    p_death = kc._uniform(rng, P_DEATH)
    funeral = kc._ci_lognormal(rng, kc.SETTING_R["fun"]) * kc._uniform(rng, kc.FUNERAL["SDB"])

    home = lam_w * r_h * q * g["home"] * m_home
    if anchor == "A" and s_w is not None:
        w_fac, c_fac = lam * s_w * q * g["facility"] * m_a, lam * (1.0 - s_w) * q * g["facility"] * m_a
        w_iso, c_iso = lam * s_w * q * g["isolation"] * m_vhf, lam * (1.0 - s_w) * q * g["isolation"] * m_vhf
    elif anchor == "A":
        w_fac = lam_w * q * g["facility"] * m_a
        w_iso = lam_w * q * g["isolation"] * m_vhf
        c_fac, c_iso = w_fac * r_c, w_iso * r_c
    else:
        w_fac = h * a * (g["facility"] / g_t) * q_b * m_b
        w_iso = h * a * (g["isolation"] / g_t) * q_b * m_vhf
        c_fac, c_iso = w_fac * r_c, w_iso * r_c
    means = {"H": home, "W_facility": w_fac, "C_facility": c_fac, "W_isolation": w_iso, "C_isolation": c_iso}
    return {"t_iso": t_iso, "w_fatal": w_fatal, "vals": vals, "g": g, "anchor": anchor, "z": z, "k_r": k_r,
            "means": {key: v * z for key, v in means.items()}, "u": u, "p_detect": p_detect, "r2": r2,
            "lam_bg": lam_bg, "incubation": tuple(opts["incubation"]), "t_list": T_LIST_V3,
            "p_death": p_death, "funeral_mean": funeral * z}


SENSITIVITY_V3 = [
    ("R1_anchor_a_only", "Anchor A only", {"anchor_a_weight": 1.0}),
    ("R2_anchor_b_only", "Anchor B only", {"anchor_a_weight": 0.0}),
    ("R3_q_030_050", "q 0.30 to 0.50", {"q": (0.30, 0.50)}),
    ("R4_q_001_005", "q 0.01 to 0.05", {"q": (0.01, 0.05)}),
    ("R5_x_0_10", "X 0 to 10", {"x": (0.0, 10.0)}),
    ("R6_w_1", "w = 1 (seven independent infections)", {"info_weight": 1.0}),
    ("R7_k_r_1", "k_r fixed at 1.0", {"k_r_fixed": 1.0}),
    ("R8_k_r_015", "k_r fixed at 0.15", {"k_r_fixed": 0.15}),
    ("R9_r_c_15_26", "r_C 1.5 to 2.6", {"r_c": (1.5, 2.6)}),
    ("R10_ipc_03_06", "Better precautions than assumed: M_A and M_B 0.3 to 0.6", {"m_a": (0.3, 0.6), "m_b": (0.3, 0.6)}),
    ("R11_unlisted_005_015", "u 0.05 to 0.15", {"unlisted": (0.05, 0.15)}),
    ("R12_unlisted_05_08", "u 0.5 to 0.8", {"unlisted": (0.5, 0.8)}),
    ("R13_incubation_mean_10", "Incubation mean 10 days (shape 3, rate 0.3)", {"incubation": (3.0, 0.3)}),
    ("R14_no_conditioning", "No conditioning", {"conditioning": False}),
    ("R15_kenya_all_linked", "All 8 Kenya linked cases in anchor A, as in v2", {"kenya_all_linked": True}),
    ("R16_baseline_at_sensitivity_sample", "Baseline at the sensitivity sample size", {}),
]

RULESETS: dict[str, dict[str, Any]] = {
    "v2": {"options": options, "draw": draw_parameters, "pathways": PATHWAYS, "sensitivity": SENSITIVITY,
           "seed": SEED, "n_draws": N_DRAWS},
    "v3": {"options": options_v3, "draw": draw_parameters_v3, "pathways": PATHWAYS_V3, "sensitivity": SENSITIVITY_V3,
           "seed": SEED_V3, "n_draws": N_DRAWS},
}


def leave_one_out_v3(grid: int = 200) -> dict:
    """Health-worker counts only, one observation per event (spec issue W8)."""
    lo_x, hi_x = X_V3
    xs = [lo_x + (i + 0.5) / grid * (hi_x - lo_x) for i in range(grid)]
    lo_k, hi_k = ANCHOR_A_V3["kenya"]["days"]
    dks = [lo_k + (i + 0.5) / grid * (hi_k - lo_k) for i in range(grid)]
    uga, ken = ANCHOR_A_V3["uganda"], ANCHOR_A_V3["kenya"]
    out = {}
    m = t = 0.0
    for dk in dks:
        for x in xs:
            w = 1.0 / ken["facility_health_workers"]
            mean, tail = _negbin_mean_tail(ken["facility_health_workers"] * w + 0.5, (dk + x) * w, uga["days"],
                                           uga["facility_health_workers"])
            m, t = m + mean, t + tail
    out["uganda"] = {"observed_health_workers": uga["facility_health_workers"], "exposure_days": uga["days"],
                     "predictive_mean": m / grid ** 2, "p_at_least_observed": t / grid ** 2,
                     "fit_without_it": "lambda_W ~ Gamma(3 (1/3) + 0.5, rate (D_K + X) / 3)"}
    m = t = 0.0
    for dk in dks:
        for x in xs:
            w = 1.0 / uga["facility_health_workers"]
            mean, tail = _negbin_mean_tail(uga["facility_health_workers"] * w + 0.5, (uga["days"] + x) * w, dk,
                                           ken["facility_health_workers"])
            m, t = m + mean, t + tail
    out["kenya"] = {"observed_health_workers": ken["facility_health_workers"], "exposure_days": list(ken["days"]),
                    "predictive_mean": m / grid ** 2, "p_at_least_observed": t / grid ** 2,
                    "fit_without_it": "lambda_W ~ Gamma(4 (1/4) + 0.5, rate (3 + X) / 4)"}
    out["france"] = leave_one_out(grid=2)["france"]
    out["note"] = ("Health-worker infections only, each held-out event predicted from the other as one observation, "
                   "for a late-stage case (q = 1) over its stated days of unsuspected care, with no IPC multiplier, "
                   "infectiousness shape or heterogeneity Z. X uniform 0 to 30 and D_K are integrated on a "
                   "deterministic grid.")
    return out


V2_ARTIFACT = {"artifact": "data/case-conditioned/nigeria-2026-10-21-v2.json",
               "payload_sha256": "125d3003fa4138d3334f62df5940dba29458d6df534fccf52516ed63e42c660a"}


def earlier_records_beside_v3() -> dict:
    v2art = json.loads((REPO / V2_ARTIFACT["artifact"]).read_text(encoding="utf-8"))
    if v2art["payload_sha256"] != V2_ARTIFACT["payload_sha256"] or kc.payload_digest(v2art) != V2_ARTIFACT["payload_sha256"]:
        raise ValueError(f"v2 artifact changed: {V2_ARTIFACT['artifact']}")
    v1 = v1_beside()
    return {
        "v1_forecast": v1["v1_forecast"], "v1_nowcast": v1["v1_nowcast"],
        "v2": {**V2_ARTIFACT, "model_version": v2art["model_version"], "results_sha256": v2art["results_sha256"],
               "headline": v2art["headline"]["probability"], "unconditioned": v2art["headline"]["unconditioned"]},
        "note": ("Every record stays as registered and is scored as registered. They are shown beside v3 and are never "
                 "averaged with it."),
    }


CITATIONS_V3 = {
    **{k: v for k, v in CITATIONS.items() if k not in ("rules", "kenya_event", "tshopo")},
    "rules": "Internal dated rules files (v2 and v3), sha256 in registration; restated in inputs",
    "red_team": "Internal independent red team of the v2 rules, written blind to v2 output, sha256 in registration",
    "kenya_event": ("Kenya Ministry of Health statements of 17 to 20 October 2026 and Africa CDC: 8 cases linked to the "
                    "first case, 3 of them health workers who managed it in hospital on 3 and 4 October; no source "
                    "states the precautions in use"),
    "tshopo": ("INSP SitReps 151 and 157 (12 and 18 October 2026): 22 frontline workers among 73 confirmed cases in "
               "Tshopo, 25 of them fatal (34%); 2 to 5 of 104 facilities above an 80% IPC score"),
    "yamin2015": "Yamin D et al. Ann Intern Med 2015;162(1):11-17: R 0.66 for survivors against 2.36 for non-survivors",
    "senegal2014": "MMWR 2014;63(39) and Pan Afr Med J 2015;22(Suppl 1):6: a survivor with about 3 unsuspected inpatient days, no secondary case",
    "faye2015": nn.CITATIONS["faye2015"],
    "baller2022": "Baller et al. 2022: patients and visitors outnumbered health workers about 2.6 to 1 among nosocomial cases, Kivu 2018 to 2020",
    "dean2016": nn.CITATIONS["dean2016"],
}
PROVENANCE_V3 = {
    **PROVENANCE,
    "registration": "registered: the v3 rules hash and time recorded on the wiki precommitment page (Entry 2)",
    "q": "judgement on yamin2015, lanini2015 and senegal2014: this case's peak day relative to one day of late-stage, severe care",
    "anchor_a": "this outbreak: the 7 documented facility infections of health workers in Uganda and Kenya, as two events; X, M_A, D_K's width and r_C are judgement",
    "anchor_b": "this outbreak: Tshopo's health workers per confirmed case, scaled by the infectiousness shape; a, D_T, g_bar, q_B and M_B are judgement",
    "home": "registered: H = lambda_W r_H q G_home M_home, with r_H judgement on dean2016",
    "funeral": "registered: the v1 funeral setting R (faye2015) and safe-burial multiplier, with the v1 death range",
    "detection": "judgement: the v2 detection structure, listing from day 13.5 and p_detect 0.95 to 1.0",
    "heterogeneity": "judgement: k_r median 0.3, the programme's dispersion prior median",
}


def _v3_disclosures() -> list[str]:
    return [
        ("Registration: the v3 rules (sha256 f775452a466ff9a20ab85de5f414e94d77c3512a18476472be2dbb96f29b7566) were "
         "recorded on the wiki page reference:bdbv-nigeria-v2-precommitment-2026-10-21 (Entry 2, 2026-10-20T23:54:42Z "
         "server time) before any v3 code existed. They change ten listed points of the v2 rules (sha256 "
         "acf07202acb0dcf07ab2ac0ebd41a94ef6dc3737817d471540e59e21a44a0a3f) and leave the question, the published "
         "quantities, the detection structure, the later-importation term and the conditioning unchanged."),
        ("Why v3: an independent red team of the v2 rules, written blind to v2 output, found that anchor A counted "
         "cases with no documented facility link (the Uganda transport case and Kenya's 5 linked cases who are not "
         "health workers) as a constant daily hazard from 13 Poisson events, and that q, X and the heterogeneity prior "
         "each leaned toward more transmission than this outbreak's own evidence or the survivor literature. Each "
         "change cites a finding and a source; none was chosen to reach a target value."),
        ("What the v3 rules' author had seen: the v1 outputs, the red team and its rough estimate (about 0.8 "
         "infections already caused and about 0.34 for the question), and its estimate that a model built to the v2 "
         "rules would land at about 0.5 to 0.6. No v2 model output. The author of the v3 spec issues had read the v2 "
         "numbers; the spec issues close gaps only and fix no value the rules set."),
        ("Gaps in the v3 rules were closed before the v3 run, each by a rule written down first (W1 to W11 before any "
         "v3 code, W12 after the code was written): (W1) one "
         "module with a ruleset switch, v2 unchanged; (W2) lambda_W ~ Gamma(7 w + 0.5, rate (3 + D_K + X) w) with "
         "w = 2/7, and C = W r_C in the facility and after isolation; (W3) row 15 uses the v2 structure for anchor A, "
         "Gamma(12 w + 0.5, rate (3 + D_K + X) w) with w = 2/12 and s_W ~ Beta(8, 6), the transport case still "
         "excluded; (W4) after isolation under anchor B, h a q_B (G_iso / G_T) M_VHF; (W5) the household-caregiver rule "
         "changes no arithmetic, since H is days 0 to 4 and C from day 4; (W6) death in a run with probability p_death "
         "at a time uniform on days 14 to 24, funeral infections Poisson with mean R_fun M_SDB Z, infection within 0.6 "
         "days of death, listed with probability 1 - u, able to seed a second generation, and without the v1 "
         "transmissibility scale or misfit factor; (W7) day 13.5 replaces day 13.0 everywhere the listing boundary "
         "appears; (W8) leave-one-out on health-worker counts, one observation per event; (W9) v2 is read from its "
         "artifact after its payload hash is verified; (W10) row columns as v2; (W11) the wording fixes below; (W12) "
         "the rules say w leaves the mean of lambda_W unchanged, but with the 0.5 prior shape the mean is "
         "(7 + 0.5 / w) / (3 + D_K + X), about 17% above w = 1; the formula is used as written and row 6 shows the "
         "effect."),
        ("Listing from day 13.5 means every case linked to the first case is confirmed after midday on 20 October, so "
         "the conditioning on no report through the end of 20 October carries almost no information in v3: the "
         "headline and its unconditioned value differ only by Monte Carlo error. The daily curve from 21 October "
         "still falls with each day of silence. A linked case confirmed on the afternoon of 20 October is dated 20 "
         "October and reported on 21 October, so it neither conditions the headline nor qualifies, as the question's "
         "dates require; about 4% of runs hold such a confirmation, and counting it would add about 0.007 to the "
         "headline."),
        ("Wording: no source states the precautions used by the Kenya hospital. Kenya has 8 linked cases, 3 of them "
         "health workers who managed the first case there, and only those 3 enter the facility rate. Tshopo's fatality "
         "is 25 of 73 confirmed cases (34%). Tshopo was chosen as the newly seeded DRC province; it is also the "
         "province with the highest health-worker share, which is selection on the outcome."),
        ("q remains the largest single judgement and a transfer assumption: no Bundibugyo viral-load series exists. "
         "The infectiousness shape is a Zaire and Sudan ebolavirus shape carried across by assumption, used for "
         "timing; q carries the level."),
        ("Counts are expected numbers under this model: people probably already infected, never people known to be "
         "infected. They count infections of the kind that become confirmed cases. Nothing here identifies a person "
         "or a facility, and no authority has said where the first case was infected; nothing here assumes it."),
        ("Second-generation transmission by an infected health worker who keeps working is not modelled separately, "
         "and third-generation cases are not modelled. The later-importation term (P6) is judgement and small."),
        ("Monte Carlo standard errors are clustered by draw. Bands and the sensitivity range cover parameter "
         "uncertainty inside this model only, not model-structure error. The equal anchor weights are a convenience, "
         "bracketed by rows 1 and 2."),
        ("v3 is additive. Block 11, every Kenya forecast, both Nigeria v1 records and v2 stay as registered and are "
         "scored as registered. v3 is the headline: an independent review of its code found no error that changes a "
         "number; this is not a calibration row unless the founder rules otherwise."),
    ]


def conditioning_note_v3(unconditioned: float, headline: float, share: float) -> str:
    return (f"Without conditioning, the probability of a qualifying case is {unconditioned:.3f}; given no further case "
            f"reported by the end of 20 October it is {headline:.3f}, a change of {headline - unconditioned:+.3f}. With "
            f"contact listing from midday on 20 October, every case linked to the first case is confirmed after midday "
            f"that day and reported on 21 October at the earliest, so the conditioning removes almost no runs (share "
            f"of runs with no report by the end of 20 October: {share:.4f}). The curve from 21 October still falls "
            f"with each day of silence.")


TEXT_CORRECTION_V3 = (
    "Text correction after the first run (Entry 4 on the precommitment page): the first-run artifact's generated "
    "conditioning sentence carried v2's wording (contacts confirmed from day 13, so part of the mass already "
    "reported), which is wrong for v3's listing day of 13.5. It was replaced by v3's own sentence after the numbers "
    "had been read. No number changed: the results hash is unchanged, and the first-run payload hash "
    "(c3324be342cb6dacfed68c777e2c6c359601671ac388210a99dac158794521ed) is on record.")

TEXT_CORRECTION_V3_REVIEW = (
    "Text correction after the independent review of the v3 code: the disclosures now describe the reviews and the "
    "author of the spec issues without internal process terms, date W12 after the v3 code and before the v3 run, "
    "state that a linked case confirmed on the afternoon of 20 October neither conditions the headline nor "
    "qualifies, and state that v3 is the headline. No number changed: the results hash is unchanged, and the "
    "previous payload hash (0364d52e72f40679f42d2e8abdf9822f88970b2e05a803b78bf8197b76f5483a) is on record.")


def build_artifact_v3(main_run: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    marks = benchmarks()
    results = kc._rounded({
        "headline": {"probability": main_run["conditioned"], "unconditioned": main_run["unconditioned"],
                     "monte_carlo_se": main_run["monte_carlo_se"],
                     "share_of_runs_with_no_report_by_20_october": main_run["share_of_runs_with_no_report_by_20_october"],
                     "share_of_draws_on_anchor_a": main_run["share_of_draws_on_anchor_a"]},
        "S1_curve": main_run["curve"],
        "S2_count_tiers": {k: main_run["forecast_conditioned"][k] for k in ("p_yes", "p_at_least_3", "p_at_least_10")},
        "S3_decomposition": {"conditioned": main_run["forecast_conditioned"],
                             "unconditioned": main_run["forecast_unconditioned"]},
        "nowcast": main_run["nowcast"],
        "first_generation_unconditioned": main_run["first_generation_unconditioned"],
        "leave_one_out": leave_one_out_v3(),
        "sensitivity": list(sensitivity),
    })
    head = results["headline"]
    note = conditioning_note_v3(head["unconditioned"], head["probability"],
                                head["share_of_runs_with_no_report_by_20_october"])
    disclosures = _v3_disclosures() + [note, TEXT_CORRECTION_V3, TEXT_CORRECTION_V3_REVIEW]
    payload = {
        "schema": "lovs-case-conditioned-forecast-nowcast/v1",
        "forecast_id": "nigeria-bdbv-2026-10-21-v3-further-case",
        "model_version": MODEL_VERSION_V3,
        "question": ("Will Nigeria report at least one further laboratory-confirmed case of this outbreak, beyond the "
                     "first case confirmed on 2026-10-20, that is not a new importation, with confirmation dated "
                     "2026-10-21 to 2026-11-17 and first reported by an authority by 2026-11-20?"),
        "rules": {
            "same_as_v1": ("Identical to the registered v1 forecast's question except the registration-timing clause: the "
                           "authorities, what counts, where the case falls ill, the void rule, the ambiguity ruling, "
                           "scoring by Brier at a coverage review between 2026-11-21 and 2026-12-05, and silence never "
                           "resolving NO."),
            "registration_timing": ("As v2: only reports first published after the v2 registration entry "
                                    "(2026-10-20T23:41:38Z) count. A qualifying report published between the v1 entry and "
                                    "the v2 entry voids v2 and v3 while v1 can still resolve YES; none was known at "
                                    "registration."),
            "what_counts_in_the_model": ("Confirmation on days 14.0 to 42.0 (21 October to 17 November WAT), first report "
                                         "by day 45.0 (20 November). First- and second-generation cases from the first "
                                         "case, including a funeral case if death occurs, and local cases from a later "
                                         "importation count; new importations do not and are not modelled."),
        },
        "relationship_to_earlier_records": ("v3 is the headline: an independent review of its code found no error that "
                                            "changes a number. "
                                            "It corrects the v2 rules after a red team written blind to v2 output. v1 "
                                            "(forecast and nowcast) and v2 stay as registered, are published and are "
                                            "scored as registered, and are shown beside v3 without averaging."),
        "registration": {**REGISTRATION_V3, "entry_3_first_run": ENTRY_3},
        "conditioning_date": CONDITIONING_DATE.isoformat(),
        "headline": {"label": headline_label(head["probability"]), "probability": head["probability"],
                     "unconditioned": head["unconditioned"], "monte_carlo_se": head["monte_carlo_se"],
                     "conditioning_note": note,
                     "benchmarks": marks},
        "earlier_records_beside_v3": earlier_records_beside_v3(),
        "results": results,
        "results_sha256": results_digest(results),
        "curve_note": ("S1: each row is the probability of YES given that no further case has been reported by the end of "
                       "that date (WAT). The headline is the value given no report by the end of 20 October."),
        "quantities": {
            "Q": "the headline, conditioned on no further report through the end of 20 October WAT, and its unconditioned value",
            "S1": "the daily curve, 21 October to 17 November",
            "S2": "P(at least 3) and P(at least 10) further qualifying cases, conditioned",
            "S3": "expected qualifying cases, P(at least one) and first-qualifying-case shares by pathway H, W, C, funeral, G2, P6",
            "N1": "people infected by the first case by the end of 20 October, by group H, W, C",
            "N2": "probability of at least one such infection, overall and by group",
            "N3": "infected people with symptoms by the end of 20 October",
            "N4": "second-generation infections by the end of 20 October",
            "N5": "expected onsets by WAT date, 8 October to 17 November",
            "N6": "expected cumulative further reports by the end of each date, 21 October to 17 November, with an 80% band",
            "conditioning": "each nowcast quantity is given unconditioned and conditioned like Q",
        },
        "scoring_plan": {
            "scored_value": "the headline probability for 20 October, fixed by this artifact's hash",
            "scored_by": ("Brier score at a coverage review naming its sources, made between 2026-11-21 and 2026-12-05. "
                          "Silence never resolves NO."),
            "shown_beside": ["the curve value standing on the day of the first qualifying report",
                             "benchmarks B1 and B2", "both v1 values and the v2 value"],
            "published": "whatever the outcome",
            "wording": ("The result is reported as a probability beside the outcome. One outcome is not a verdict on "
                        "the forecast, cannot separate it from the benchmarks or from the earlier records, and says "
                        "nothing about Nigeria's response."),
            "if_a_case_occurs": "say nothing about where, from whom or in which facility that case was infected",
        },
        "inputs": {
            "provenance": PROVENANCE_V3,
            "registration": REGISTRATION_V3,
            "changes_from_v2": {
                "C1": "anchor A counts only the 7 documented facility infections of health workers, as two events (w = 2/7); C = W r_C",
                "C2": "X uniform 0 to 30",
                "C3": "q uniform 0.03 to 0.30",
                "C4": "k_r lognormal, median 0.3, log-sd 0.5",
                "C5": "anchor B scaled by the infectiousness shape, with a numerator discount; q_B 0.3 to 0.8; C = W r_C",
                "C6": "H = lambda_W r_H q G_home M_home; household caregivers sit in H for days 0 to 4 and in C afterwards",
                "C7": "p_detect uniform 0.95 to 1.0",
                "C8": "the death and safe-burial pathway restored, its own row",
                "C9": "contact listing anchored at day 13.5, midday on 20 October",
                "C10": "wording: no claim about the Kenya hospital's precautions; Tshopo fatality 34%; only Kenya's 3 health workers enter the facility rate",
            },
            "clock": {"t0": "2026-10-07T00:00+01:00", "onset": 0.0, "home_window": [0.0, T_HOME_END],
                      "t_iso_uniform": T_ISO, "profile_end": G_END, "listed_boundary_day": T_LIST_V3,
                      "report_delay": REPORT_DELAY, "window_confirmation": [T_WINDOW_OPEN, T_CONFIRM_END],
                      "report_by": T_REPORT_END, "conditioning_report_cutoff": T_AS_OF},
            "profile": {"f_survivor": F_SURVIVOR, "f_fatal": F_FATAL, "w_fatal_uniform": W_FATAL,
                        "reading": "day d covers [d, d + 1); mixed, then normalised so the highest day equals 1",
                        "exposure": "G_home over [0, 4], G_fac over [4, T_iso], G_iso over [T_iso, 15], in peak-day units",
                        "role": "a timing shape; q carries the level"},
            "q": {"uniform": Q_V3, "meaning": "this case's peak-day infectiousness relative to one day of care of a late-stage, severe case"},
            "anchor_a": {**ANCHOR_A_V3, "means": "W_A = lambda_W q G_fac M_A; C_A = W_A r_C"},
            "anchor_b": {**ANCHOR_B_V3, "means": "W_B = h a (G_fac / G_T) q_B M_B with G_T = D_T g_bar; C_B = W_B r_C"},
            "home": {"m_home_uniform": M_HOME, "r_h_uniform": R_H, "mean": "H = lambda_W r_H q G_home M_home, for both anchors"},
            "after_isolation": {"m_vhf_uniform": M_VHF},
            "funeral": FUNERAL_V3,
            "heterogeneity": {"z": "Gamma(k_r, scale 1 / k_r), one per draw", "k_r_lognormal_median_logsd": K_R_V3},
            "incubation_gamma": incubation_prior(),
            "detection": {"unlisted_uniform_h_c_and_funeral": UNLISTED, "unlisted_w": "u / 2",
                          "listed_onset_before_day_13_5": f"day 13.5 + Erlang(2, mean {LAG_LISTED_PRE_MEAN})",
                          "listed_onset_after_day_13_5": f"onset + Erlang(2, mean {LAG_LISTED_POST_MEAN})",
                          "unlisted": f"max(onset, 13.5) + Erlang(2, mean {LAG_UNLISTED_MEAN})",
                          "report": "confirmation + 0.5 days", "p_detect_uniform": P_DETECT_V3},
            "second_generation": {**SECOND_GENERATION, "monitoring_from_day": T_LIST_V3},
            "background": BACKGROUND,
            "pathways": PATHWAYS_V3,
            "benchmarks": marks,
            "citations": CITATIONS_V3,
        },
        "case_evidence": CASE_EVIDENCE,
        "disclosures": disclosures,
        "sensitivity_note": ("Each row moves one registered input (20,000 draws, seed 2026102103). headline is conditioned "
                             "through 20 October (row 14: unconditioned); the infection columns are first-generation, "
                             "over all time and unconditioned."),
        "method": {"seed": SEED_V3, "n_draws": N_DRAWS, "n_draws_sensitivity": N_DRAWS_SENSITIVITY,
                   "runs_per_draw": RUNS_PER_DRAW, "code": "lovs/forecast/nigeria_v2.py (ruleset v3)",
                   "reuses": ["lovs/forecast/nigeria_conditioned.py", "lovs/forecast/nigeria_nowcast.py",
                              "lovs/forecast/kenya_conditioned.py", "lovs/forecast/kenya_escape.py",
                              "lovs/lovs_priors_bundibugyo.py"]},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = kc.payload_digest(payload)
    return payload


def build(n: int = N_DRAWS, n_sensitivity: int = N_DRAWS_SENSITIVITY, ruleset: str = "v2") -> dict:
    if ruleset == "v3":
        return build_artifact_v3(run(n=n, ruleset="v3"), sensitivity_rows(n=n_sensitivity, ruleset="v3"))
    return build_artifact(run(n=n), sensitivity_rows(n=n_sensitivity))


def render(artifact: Mapping[str, Any]) -> str:
    return json.dumps(artifact, indent=1, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ruleset", choices=tuple(RULESETS), default="v2")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = DEFAULT_OUT_V3 if args.ruleset == "v3" else DEFAULT_OUT
    artifact = build(ruleset=args.ruleset)
    text = render(artifact)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"out": args.out.name, "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      "payload_sha256": artifact["payload_sha256"], "results_sha256": artifact["results_sha256"],
                      "module_sha256": hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
