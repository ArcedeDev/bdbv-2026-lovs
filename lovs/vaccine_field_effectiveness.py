"""LOVS vaccine field-effectiveness decomposition (review tier, scenario lane).

Question: how much of a vaccine's trial efficacy survives as protection in
the field during the 2026 Bundibugyo virus (BDBV) outbreak in the DRC.

The trial anchor is a Zaire ebolavirus (EBOV) estimate. It is never a BDBV
figure. Every step from it to field protection is a named factor with a
declared distribution and a basis:

  "source"      a published number (citation in ``source``)
  "sitrep"      a value INSP prints, refreshed each release
  "assumption"  a labelled hypothesis with a declared range; the conclusion
                is reported over the range, never at a point

The chain, per Monte Carlo draw:

  e0  trial efficacy against EBOV, cases with onset >= 10 days (source)
  e1  = e0 * strain_match         (BDBV / EBOV relative protection; assumption)
  e2  = e1 * potency              (doses given that were still potent; assumption)
  e3  = e2 * durability           (protection retained over the horizon; source range)
  e4g = e3 * timing_g             (share of group g's exposure that falls after
                                   immune onset; derived from delays)
  e5g = e4g * coverage_g          (share of group g's infections averted directly;
                                   coverage = printed coverage * denominator
                                   completeness)
  e6  = sum_g case_share_g * e5g  (share of all cases averted directly)

Indirect (herd, chain-breaking) effects are NOT in e6. They are not
identifiable from what INSP prints; ``indirect_multiplier`` exists only for a
labelled scenario and is reported separately.

Two estimators are included because they are what a real-time field estimate
would need and because they are validated against published numbers:

  * ``ve_from_counts``   cohort or trial VE with an exact conditional
                         (Clopper-Pearson) interval;
  * ``screening_ve``     Farrington's screening method: field VE from the
                         vaccinated share of cases (PCV) and of the source
                         population (PPV).

This is situational evidence, not clinical or vaccine advice.

Stdlib only. Deterministic for a given seed.
"""
from __future__ import annotations

import dataclasses
import math
import random
from typing import Callable, Iterable, Mapping, Sequence

MODEL_VERSION = "lovs_vaccine_field_effectiveness-v0.1.0"

BASES = ("source", "sitrep", "assumption", "derived")


# ---------------------------------------------------------------------------
# Exact intervals (stdlib)
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularised incomplete beta (Numerical Recipes)."""
    max_iter, eps, fpmin = 400, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Inverse of ``beta_cdf`` by bisection (adequate for interval work)."""
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Exact two-sided binomial interval for k successes in n trials."""
    if n <= 0 or k < 0 or k > n:
        raise ValueError("need 0 <= k <= n and n > 0")
    alpha = 1.0 - level
    lo = 0.0 if k == 0 else beta_ppf(alpha / 2.0, k, n - k + 1)
    hi = 1.0 if k == n else beta_ppf(1.0 - alpha / 2.0, k + 1, n - k)
    return lo, hi


@dataclasses.dataclass(frozen=True)
class VEEstimate:
    point: float
    lower: float
    upper: float
    method: str


def ve_from_counts(cases_v: int, denom_v: float, cases_u: int, denom_u: float,
                   level: float = 0.95) -> VEEstimate:
    """VE = 1 - RR with an exact conditional interval.

    Conditional on the total number of cases, the number among the vaccinated
    is binomial with p = r*w / (r*w + 1), where r is the rate ratio and
    w = denom_v / denom_u (people or person-time). A Clopper-Pearson interval
    on p maps to an interval on r, then on VE = 1 - r. With zero cases among
    the vaccinated the point estimate is 1 and the lower bound is finite.
    """
    if denom_v <= 0 or denom_u <= 0:
        raise ValueError("denominators must be positive")
    total = cases_v + cases_u
    if total == 0:
        raise ValueError("no cases: VE is not estimable")
    w = denom_v / denom_u
    p_lo, p_hi = clopper_pearson(cases_v, total, level)

    def rr(p: float) -> float:
        if p >= 1.0:
            return math.inf
        return p / ((1.0 - p) * w)

    point_rr = (cases_v / denom_v) / (cases_u / denom_u) if cases_u else math.inf
    return VEEstimate(
        point=1.0 - point_rr,
        lower=1.0 - rr(p_hi),
        upper=1.0 - rr(p_lo),
        method="exact conditional binomial (Clopper-Pearson), VE = 1 - RR",
    )


def screening_ve(pcv: float, ppv: float) -> float:
    """Farrington screening method: VE = 1 - [PCV/(1-PCV)] * [(1-PPV)/PPV].

    pcv: share of cases who were vaccinated (protected period only).
    ppv: share of the source population vaccinated at the same time.
    """
    if not (0.0 <= pcv < 1.0) or not (0.0 < ppv < 1.0):
        raise ValueError("need 0 <= pcv < 1 and 0 < ppv < 1")
    return 1.0 - (pcv / (1.0 - pcv)) * ((1.0 - ppv) / ppv)


def screening_ve_interval(cases_vaccinated: int, cases_total: int, ppv: float,
                          level: float = 0.95) -> VEEstimate:
    """Screening-method VE with an exact interval on PCV (PPV held fixed)."""
    p_lo, p_hi = clopper_pearson(cases_vaccinated, cases_total, level)
    pcv = cases_vaccinated / cases_total
    hi_ve = screening_ve(p_lo, ppv)
    lo_ve = screening_ve(min(p_hi, 1.0 - 1e-12), ppv)
    return VEEstimate(point=screening_ve(pcv, ppv), lower=lo_ve, upper=hi_ve,
                      method="Farrington screening method, exact PCV interval, PPV fixed")


# ---------------------------------------------------------------------------
# Parameters with a declared basis
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Param:
    """One model input: a distribution, a basis and where it comes from."""

    name: str
    kind: str                 # fixed | uniform | triangular | beta | gamma_mean_sd | ve_posterior
    args: tuple
    basis: str                # one of BASES
    source: str               # citation, SitRep locator, or "ASSUMPTION: ..."
    updatable_each_release: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"{self.name}: unknown basis {self.basis!r}")
        if self.basis == "assumption" and not self.source.startswith("ASSUMPTION"):
            raise ValueError(f"{self.name}: an assumption must be labelled ASSUMPTION")

    def sample(self, rng: random.Random) -> float:
        k, a = self.kind, self.args
        if k == "fixed":
            return float(a[0])
        if k == "uniform":
            return rng.uniform(a[0], a[1])
        if k == "triangular":
            lo, mode, hi = a
            return rng.triangular(lo, hi, mode)
        if k == "beta":
            return rng.betavariate(a[0], a[1])
        if k == "gamma_mean_sd":
            mean, sd = a
            shape = (mean / sd) ** 2
            return rng.gammavariate(shape, mean / shape)
        if k == "empirical":
            # Resample a stored set of values (e.g. accepted calibration draws).
            return float(a[rng.randrange(len(a))])
        if k == "ve_posterior":
            # Posterior VE from trial counts: p ~ Beta(k_v + 0.5, k_u + 0.5)
            # (Jeffreys), r = p / ((1 - p) * w), VE = 1 - r, floored at 0.
            k_v, k_u, w = a
            p = rng.betavariate(k_v + 0.5, k_u + 0.5)
            return max(0.0, 1.0 - p / ((1.0 - p) * w))
        raise ValueError(f"{self.name}: unknown kind {k!r}")


# ---------------------------------------------------------------------------
# Timing: how much of a group's exposure falls after immune onset
# ---------------------------------------------------------------------------

def pre_exposure_timing_share(onset_days: float, horizon_days: float) -> float:
    """Constant hazard over [0, horizon] from vaccination: share after onset."""
    if horizon_days <= 0:
        raise ValueError("horizon must be positive")
    return max(0.0, horizon_days - onset_days) / horizon_days


def ring_timing_share(delay_to_vaccination: float, onset_days: float,
                      tau_index: float, incubation: float, tau_next: float,
                      index_share: float) -> float:
    """Share of a ring contact's infection risk that falls after protection.

    Times are days after the index case's symptom onset.
      delay_to_vaccination  index onset -> contact vaccinated
      tau_index             index onset -> transmission to this contact
      incubation, tau_next  a later exposure, from a secondary case in the
                            ring: tau_index + incubation + tau_next
      index_share           share of the contact's risk that is from the index

    Returns 0/1 contributions weighted by index_share for one draw.
    """
    protected_from = delay_to_vaccination + onset_days
    first = 1.0 if tau_index > protected_from else 0.0
    later = 1.0 if (tau_index + incubation + tau_next) > protected_from else 0.0
    return index_share * first + (1.0 - index_share) * later


# ---------------------------------------------------------------------------
# Groups and the Monte Carlo chain
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Group:
    """A vaccinated target group in one area."""

    name: str
    mode: str                      # "pre_exposure" | "ring" | "given" | "mixture"
    coverage: Param                # printed or derived coverage of the target list
    denominator_completeness: Param  # share of the truly exposed group on that list
    case_share: Param              # share of the area's cases in this group
    timing: Param | None = None    # mode "given": protected share supplied directly
                                   # (e.g. from a calibrated ring profile; it already
                                   # includes any post-exposure effect). Mode "mixture":
                                   # the ring-like component.
    pre_exposure_weight: Param | None = None  # mode "mixture": share of the group
                                   # vaccinated ahead of exposure (rest ring-like)


@dataclasses.dataclass(frozen=True)
class ModelInputs:
    ve_zaire: Param
    strain_match: Param
    potency: Param
    durability: Param
    onset_days: Param
    horizon_days: Param
    delay_to_vaccination: Param
    tau_index: Param
    incubation: Param
    tau_next: Param
    index_share: Param
    pep_effect: Param              # protection against pre-protection infection; default 0
    indirect_multiplier: Param     # scenario only, never in e6

    def params(self) -> list[Param]:
        return [getattr(self, f.name) for f in dataclasses.fields(self)]


def _quantiles(xs: Sequence[float], qs: Iterable[float] = (0.05, 0.5, 0.95)) -> dict:
    s = sorted(xs)
    n = len(s)
    out = {}
    for q in qs:
        pos = q * (n - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, n - 1)
        out[f"p{int(round(q * 100)):02d}"] = s[lo] + (s[hi] - s[lo]) * (pos - lo)
    out["mean"] = sum(s) / n
    return out


def first_order_share(x: Sequence[float], y: Sequence[float], bins: int = 20) -> float:
    """Var(E[Y|X]) / Var(Y) by quantile binning: a first-order sensitivity index."""
    n = len(y)
    mean_y = sum(y) / n
    var_y = sum((v - mean_y) ** 2 for v in y) / n
    if var_y == 0.0:
        return 0.0
    order = sorted(range(n), key=lambda i: x[i])
    acc = 0.0
    for b in range(bins):
        idx = order[b * n // bins:(b + 1) * n // bins]
        if not idx:
            continue
        m = sum(y[i] for i in idx) / len(idx)
        acc += len(idx) * (m - mean_y) ** 2
    return (acc / n) / var_y


def simulate(inputs: ModelInputs, groups: Sequence[Group], draws: int = 20000,
             seed: int = 20261121) -> dict:
    """Run the chain. Returns quantiles per stage and per group, plus sensitivity."""
    if not groups:
        raise ValueError("need at least one group")
    rng = random.Random(seed)
    stage: dict[str, list[float]] = {k: [] for k in ("e0", "e1", "e2", "e3", "e6", "e6_indirect_scenario")}
    per_group: dict[str, dict[str, list[float]]] = {
        g.name: {"timing": [], "e4": [], "coverage_effective": [], "e5": []} for g in groups}
    sampled: dict[str, list[float]] = {p.name: [] for p in inputs.params()}
    case_share_total: list[float] = []

    for _ in range(draws):
        v = {p.name: p.sample(rng) for p in inputs.params()}
        for k, x in v.items():
            sampled[k].append(x)
        e0 = v["ve_zaire"]
        e1 = e0 * v["strain_match"]
        e2 = e1 * v["potency"]
        e3 = e2 * v["durability"]
        e6 = 0.0
        shares = 0.0
        for g in groups:
            if g.mode == "pre_exposure":
                timing = pre_exposure_timing_share(v["onset_days"], v["horizon_days"])
            elif g.mode == "ring":
                timing = ring_timing_share(v["delay_to_vaccination"], v["onset_days"],
                                           v["tau_index"], v["incubation"], v["tau_next"],
                                           v["index_share"])
            elif g.mode == "given":
                if g.timing is None:
                    raise ValueError(f"group {g.name}: mode 'given' needs timing")
                timing = g.timing.sample(rng)
            elif g.mode == "mixture":
                if g.timing is None or g.pre_exposure_weight is None:
                    raise ValueError(f"group {g.name}: mode 'mixture' needs timing and pre_exposure_weight")
                m = g.pre_exposure_weight.sample(rng)
                pre = pre_exposure_timing_share(v["onset_days"], v["horizon_days"])
                pre = pre + (1.0 - pre) * v["pep_effect"]
                timing = m * pre + (1.0 - m) * g.timing.sample(rng)
                sampled.setdefault(f"{g.name}.pre_exposure_weight", []).append(m)
            else:
                raise ValueError(f"unknown group mode {g.mode!r}")
            # A vaccinated person infected before protection is spared only by
            # any post-exposure effect (pep_effect, default 0). A "given" timing
            # already includes it.
            if g.mode in ("given", "mixture"):
                protected_share = timing
            else:
                protected_share = timing + (1.0 - timing) * v["pep_effect"]
            e4 = e3 * protected_share
            c_raw = g.coverage.sample(rng)
            q = g.denominator_completeness.sample(rng)
            cov = min(1.0, c_raw * q)
            e5 = e4 * cov
            cs = g.case_share.sample(rng)
            for key, val in ((f"{g.name}.coverage", c_raw), (f"{g.name}.denominator_completeness", q),
                             (f"{g.name}.case_share", cs), (f"{g.name}.timing", timing)):
                sampled.setdefault(key, []).append(val)
            shares += cs
            e6 += cs * e5
            pg = per_group[g.name]
            pg["timing"].append(timing)
            pg["e4"].append(e4)
            pg["coverage_effective"].append(cov)
            pg["e5"].append(e5)
        if shares > 1.0 + 1e-9:
            raise ValueError("group case shares sum above 1 in a draw")
        case_share_total.append(shares)
        for k, x in (("e0", e0), ("e1", e1), ("e2", e2), ("e3", e3), ("e6", e6)):
            stage[k].append(x)
        stage["e6_indirect_scenario"].append(min(1.0, e6 * v["indirect_multiplier"]))

    sensitivity = {
        name: round(first_order_share(xs, stage["e6"]), 4)
        for name, xs in sampled.items()
        if len(set(xs)) > 1
    }
    sensitivity_individual = {
        name: round(first_order_share(xs, stage["e3"]), 4)
        for name, xs in sampled.items()
        if len(set(xs)) > 1
    }
    return {
        "model_version": MODEL_VERSION,
        "draws": draws,
        "seed": seed,
        "stages": {k: _quantiles(v) for k, v in stage.items()},
        "groups": {g: {k: _quantiles(v) for k, v in d.items()} for g, d in per_group.items()},
        "case_share_covered": _quantiles(case_share_total),
        "sensitivity_first_order_e6": dict(sorted(sensitivity.items(), key=lambda kv: -kv[1])),
        "sensitivity_first_order_e3": dict(sorted(sensitivity_individual.items(), key=lambda kv: -kv[1])),
        "inputs": [dataclasses.asdict(p) for p in inputs.params()],
        "lane": "scenario",
        "tier": "review",
        "caveat": ("Zaire-anchored decomposition with a labelled strain-match assumption; "
                   "not a measured BDBV effectiveness; indirect effects excluded from e6; "
                   "situational evidence, not clinical or vaccine advice."),
    }


def ring_cohort_profile(ve_post: float, delay: Param, onset: Param, tau: Param,
                        incubation: Param, index_share: Param, draws: int = 40000,
                        seed: int = 20261121, early_window_days: float = 10.0,
                        pep_effect: float = 0.0,
                        window: tuple[float, float] = (3.0, 10.0)) -> dict:
    """Simulate a ring the way it is observed, for validation against cohorts.

    Each draw is one infected ring member, infected at t (days after index
    onset) by the index (tau) or by a secondary ring case (tau + inc + tau).
    Their own onset is t + incubation. A member is vaccinated at D only if
    still without symptoms at D (onset > D); symptomatic members are cases,
    not vaccinees. A vaccinee infected after D + onset_days is spared with
    probability ve_post; a vaccinee infected before then is spared with
    probability pep_effect; everyone else becomes a case.

    Returns, for vaccinees:
      early_share       share of vaccinee cases with onset < early_window after D
      ve_late           VE counting only cases with onset >= early_window
      ve_any            VE counting every vaccinee case
      ve_window         VE among members whose (counterfactual) onset falls
                        window[0] to window[1] days after vaccination
    and, for the population view:
      after_protection  share of all ring infections (vaccinated or not, before
                        or after D) that happen after D + onset_days
    All VE figures compare vaccinees with the same members unvaccinated,
    i.e. the counterfactual of the same cohort.
    """
    rng = random.Random(seed)
    n_vacc = cases_vacc = early_cases = late_cases = 0
    late_cf = early_cf = 0
    win_cf = win_cases = 0
    after = 0
    for _ in range(draws):
        d = delay.sample(rng)
        t_on = onset.sample(rng)
        t = tau.sample(rng)
        if rng.random() >= index_share.sample(rng):
            t = t + incubation.sample(rng) + tau.sample(rng)
        onset_c = t + incubation.sample(rng)
        if t > d + t_on:
            after += 1
        if onset_c <= d:
            continue  # already a case at vaccination: never a vaccinee
        n_vacc += 1
        early = (onset_c - d) < early_window_days
        # counterfactual (unvaccinated) cohort: every infected member is a case
        if early:
            early_cf += 1
        else:
            late_cf += 1
        in_window = window[0] <= (onset_c - d) < window[1]
        if in_window:
            win_cf += 1
        if t > d + t_on:
            protected = rng.random() < ve_post
        else:
            protected = rng.random() < pep_effect
        if not protected:
            if in_window:
                win_cases += 1
            cases_vacc += 1
            if early:
                early_cases += 1
            else:
                late_cases += 1
    if n_vacc == 0 or cases_vacc == 0:
        raise ValueError("degenerate ring profile")
    return {
        "early_share": early_cases / cases_vacc,
        "ve_late": 1.0 - late_cases / late_cf if late_cf else float("nan"),
        "ve_any": 1.0 - cases_vacc / n_vacc,
        "ve_window": 1.0 - win_cases / win_cf if win_cf else float("nan"),
        "after_protection": after / draws,
        "vaccinee_fraction_of_infected": n_vacc / draws,
    }


def coverage_from_vaccination_record(row: Mapping) -> dict:
    """Read one per-province row of the LOVS vaccination record into a coverage input.

    The record (review tier, values as INSP prints them, no national total) is
    owned by the SitRep promotion; this function only reads it. Field names
    follow the SR189 candidate shape: province, vaccinated_cumulative,
    pre_registered, coverage_pct, doses_received, doses_remaining, doses_used.

    Order of preference:
      1. vaccinated_cumulative / pre_registered (a printed denominator);
      2. coverage_pct / 100 (printed rate, denominator possibly not printed);
      3. None: coverage not estimable (e.g. Ituri, counts without a target).
    Doses used against received is returned when both are printed.
    """
    prov = row.get("province")
    vac = row.get("vaccinated_cumulative")
    pre = row.get("pre_registered")
    pct = row.get("coverage_pct")
    out: dict = {"province": prov, "coverage": None, "basis": "not estimable",
                 "doses_used_share": None}
    if vac is not None and pre:
        ratio = vac / pre
        out.update(coverage=min(1.0, ratio), basis="vaccinated_cumulative / pre_registered")
        if ratio > 1.0:
            out["basis"] += " (above 1 as printed; clipped to 1)"
    elif pct is not None:
        out.update(coverage=min(1.0, pct / 100.0), basis="coverage_pct as printed (denominator may be unprinted)")
        if pct > 100.0:
            out["basis"] += " (above 100 as printed; clipped to 1)"
    rec, rem, used = row.get("doses_received"), row.get("doses_remaining"), row.get("doses_used")
    if rec:
        if used is None and rem is not None:
            used = rec - rem
        if used is not None:
            out["doses_used_share"] = used / rec
    return out


def conditional_on(inputs: ModelInputs, groups: Sequence[Group], name: str,
                   values: Sequence[float], draws: int = 5000,
                   seed: int = 20261121) -> list[dict]:
    """Re-run with one parameter fixed at each value (for unidentified inputs)."""
    out = []
    for val in values:
        p = getattr(inputs, name)
        fixed = dataclasses.replace(p, kind="fixed", args=(val,))
        res = simulate(dataclasses.replace(inputs, **{name: fixed}), groups, draws, seed)
        out.append({name: val, "e3": res["stages"]["e3"], "e6": res["stages"]["e6"],
                    "groups": {g: d["e5"] for g, d in res["groups"].items()}})
    return out
