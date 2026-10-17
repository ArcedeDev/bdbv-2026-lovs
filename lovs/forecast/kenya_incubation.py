# SPDX-License-Identifier: Apache-2.0
"""What Kenya's 2026 Bundibugyo cluster says about the incubation period.

Kenya's six linked cases are the first Bundibugyo cases since 2012 whose exposure windows and
onsets are both published. Three health workers managed the imported first case in hospital
on 3 and 4 October; three relatives were exposed between the first case's arrival on 3 October
and death on 5 October. All six were in quarantine when symptoms began, on 13 to 17 October.

Method, in one paragraph. The incubation period is gamma with shape k and mean mu, evaluated
on a grid. For a group of contacts sharing one exposure window, infections are a Poisson
number, each with onset at exposure plus incubation, and onsets are seen only up to the
observation time T. With the count profiled out, the group's likelihood is

    prod_i P(onset in W_i) / P(onset <= T) ** n,

where each probability integrates the gamma CDF over a uniform exposure window. Dividing by
P(onset <= T) is what makes the absence of earlier onsets count as evidence. The Kenya
likelihood is reported alone (flat on the grid) and multiplied by a prior that summarises
the pre-Kenya Bundibugyo evidence (the 14 October fit). Bayes factors compare four named
incubation distributions.

Stdlib only and deterministic: everything is numerical integration, no sampling.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import kenya_conditioned as kc

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-17-incubation.json"
MODEL_VERSION = "kenya_incubation-v1.0.0"

# Numerical tables: the gamma CDF F and its running integral I on a fine grid of days.
DT = 0.01
T_MAX = 90.0
N_TABLE = int(round(T_MAX / DT)) + 1

# Registered grid (plan 2026-10-17-bdbv-kenya-escape-forecast, sha256 cf4365e4...).
K_GRID = (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0)
MU_GRID = tuple(round(5.0 + 0.25 * i, 2) for i in range(45))

# Model clock: days from 2026-10-03 00:00 East Africa Time (the 9 and 14 October clock).
T_OBS = 15.0                      # end of 17 October
HOSP_EXPOSURE = (0.6, 2.0)        # health workers managed the first case on 3 and 4 October
RELATIVES_EXPOSURE = (0.5, 2.98)      # relatives: from arrival on 3 October to death on 5 October
ONSET_CASE2 = (10.0, 11.0)        # onset 13 October, published
ONSET_16OCT = (11.0, 14.0)        # confirmed 16 October; onset 14 to 16 October, inferred
ONSET_17OCT = (12.0, 15.0)        # announced 17 October; onset 15 to 17 October, inferred
RELEASE = {"hospital": 23.0, "relatives": 24.0}   # 21 days after each group's last exposure

# Pre-Kenya evidence: marginals of the 14 October fit (median, 10th, 90th percentile).
PRIOR_MU = (7.25, 6.5, 8.25)
PRIOR_K = (3.48, 2.37, 4.22)
Z90 = 1.2816

CANDIDATES = {
    "programme_prior": {"k": 4.0, "mu": 4.0 / 0.6,
                        "label": "LOVS Bundibugyo prior, gamma(4, rate 0.6), mean 6.67 days"},
    "decided_prior": {"k": 3.0, "mu": 3.0 / 0.406,
                      "label": "Decided Bundibugyo prior of 14 October, gamma(3, rate 0.406), mean 7.39 days"},
    "zaire_prior": {"k": 4.0, "mu": 10.0,
                    "label": "LOVS Zaire prior, gamma(4, rate 0.4), mean 10 days"},
    "west_africa_2014": {"k": (9.1 / 7.3) ** 2, "mu": 9.1,
                         "label": "West Africa 2014, gamma fitted to single-day exposures, mean 9.1 days, standard deviation 7.3 (WHO Ebola Response Team, Table 2)"},
}

CITATIONS = {
    "who_ert2014": "WHO Ebola Response Team. Ebola virus disease in West Africa, the first 9 months of the epidemic and forward projections. N Engl J Med 2014;371:1481-95. doi:10.1056/NEJMoa1411100",
    "macneil2010": "MacNeil A et al. Proportion of deaths and clinical features in Bundibugyo Ebola virus infection, Uganda. Emerg Infect Dis 2010;16(12):1969-72. doi:10.3201/eid1612.100627",
    "wamala2010": "Wamala JF et al. Ebola hemorrhagic fever associated with novel virus strain, Uganda, 2007-2008. Emerg Infect Dis 2010;16(7):1087-92. doi:10.3201/eid1607.091525",
    "roddy2012": "Roddy P et al. Clinical manifestations and case management of Ebola haemorrhagic fever caused by a newly identified virus strain, Bundibugyo, Uganda, 2007-2008. PLoS One 2012;7(12):e52986. doi:10.1371/journal.pone.0052986",
    "kratz2015": "Kratz T et al. Ebola virus disease outbreak in Isiro, Democratic Republic of the Congo, 2012. PLoS One 2015;10(6):e0129333. doi:10.1371/journal.pone.0129333",
    "kenya_moh_2026_10_14": "Kenya Ministry of Health, 14 Oct 2026, relayed by the press: second case, a listed contact in quarantine, onset 13 Oct",
    "who_afro_2026_10_16": "WHO AFRO, 16 Oct 2026: second and third cases confirmed on 14 and 16 October, both close family members of the first case and linked to it",
    "kenya_moh_2026_10_17": "Kenya Ministry of Health, 17 Oct 2026, reported by Africa CDC and the press: four more cases, a relative in quarantine since 6 October and three health workers who managed the first case on 3 and 4 October, in quarantine since 9 October; all four developed fever while in quarantine",
}


# ---------------------------------------------------------------------------------------
# Gamma distribution machinery.
# ---------------------------------------------------------------------------------------
def gamma_pdf(x: float, k: float, mu: float) -> float:
    if x <= 0.0:
        return 0.0
    rate = k / mu
    return math.exp(k * math.log(rate) + (k - 1.0) * math.log(x) - rate * x - math.lgamma(k))


def gamma_cdf_exact(x: float, k: float, mu: float) -> float:
    """Regularised lower incomplete gamma by series or continued fraction (for tests)."""
    if x <= 0.0:
        return 0.0
    z = x * k / mu
    log_pre = -z + k * math.log(z) - math.lgamma(k)
    if z < k + 1.0:
        term = total = 1.0 / k
        n = 1
        while abs(term) > 1e-15 * abs(total):
            term *= z / (k + n)
            total += term
            n += 1
        return math.exp(log_pre) * total
    b = z + 1.0 - k
    c, d = 1e300, 1.0 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - k)
        b += 2.0
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        step = d * c
        h *= step
        if abs(step - 1.0) < 1e-15:
            break
    return 1.0 - math.exp(log_pre) * h


@functools.lru_cache(maxsize=1024)
def tables(k: float, mu: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """CDF F and its running integral I on [0, T_MAX] at step DT.

    F is anchored at exact values every 10 days and filled by trapezoidal integration of the
    density in between, which keeps the error near 5e-6 or below for shapes of 1.5 and above.
    """
    f = [0.0] * N_TABLE
    anchor_every = int(round(10.0 / DT))
    prev_pdf = gamma_pdf(0.0, k, mu)
    for i in range(1, N_TABLE):
        x = i * DT
        pdf = gamma_pdf(x, k, mu)
        if x < 1.0 or i % anchor_every == 0:
            # exact near zero, where the density is steep, and at every anchor
            f[i] = gamma_cdf_exact(x, k, mu)
        else:
            f[i] = f[i - 1] + 0.5 * (prev_pdf + pdf) * DT
        prev_pdf = pdf
    integral = [0.0] * N_TABLE
    for i in range(1, N_TABLE):
        integral[i] = integral[i - 1] + 0.5 * (f[i - 1] + f[i]) * DT
    return tuple(f), tuple(integral)


def _interp(table: Sequence[float], x: float, tail_slope: float | None = None) -> float:
    if x <= 0.0:
        return 0.0
    pos = x / DT
    i = int(pos)
    if i >= N_TABLE - 1:
        last = table[-1]
        return last + (tail_slope if tail_slope is not None else 0.0) * (x - T_MAX)
    frac = pos - i
    return table[i] + frac * (table[i + 1] - table[i])


def cdf(k: float, mu: float, x: float) -> float:
    return _interp(tables(k, mu)[0], x)


def onset_cdf(k: float, mu: float, exposure: Sequence[float], t: float) -> float:
    """P(onset <= t) for exposure uniform on [a, b]: (I(t - a) - I(t - b)) / (b - a)."""
    a, b = exposure
    integral = tables(k, mu)[1]
    if b - a <= 0.0:
        return cdf(k, mu, t - a)
    return (_interp(integral, t - a, 1.0) - _interp(integral, t - b, 1.0)) / (b - a)


# ---------------------------------------------------------------------------------------
# Observations and likelihood.
# ---------------------------------------------------------------------------------------
def observations(variant: str = "primary") -> list[dict]:
    """Groups of cases sharing an exposure window, each with its onset windows and truncation."""
    hosp_exp, rel_exp, t_obs, shift = HOSP_EXPOSURE, RELATIVES_EXPOSURE, T_OBS, 0.0
    if variant == "A2_relatives_before_isolation":
        rel_exp = (0.5, 1.0)
    if variant == "A3_onsets_one_day_earlier":
        shift = -1.0
    if variant == "A4_truncation_14":
        t_obs = 14.0
    if variant == "A5_hospital_exposure_to_3":
        hosp_exp = (0.6, 3.0)

    def window(w: Sequence[float]) -> tuple[float, float]:
        lo, hi = w[0] + shift, min(w[1] + shift, t_obs)
        return (lo, hi)

    groups = [{"group": "hospital", "exposure": hosp_exp, "t_obs": t_obs,
               "onsets": [window(ONSET_17OCT)] * 3}]
    if variant == "A1_case2_as_source":
        groups.append({"group": "relatives", "exposure": rel_exp, "t_obs": t_obs,
                       "onsets": [window(ONSET_CASE2)]})
        groups.append({"group": "relatives_via_case2", "exposure": ONSET_CASE2, "t_obs": t_obs,
                       "onsets": [window(ONSET_16OCT), window(ONSET_17OCT)]})
    else:
        groups.append({"group": "relatives", "exposure": rel_exp, "t_obs": t_obs,
                       "onsets": [window(ONSET_CASE2), window(ONSET_16OCT), window(ONSET_17OCT)]})
    return groups


def log_likelihood(k: float, mu: float, groups: Sequence[Mapping[str, Any]]) -> float:
    total = 0.0
    for g in groups:
        observable = onset_cdf(k, mu, g["exposure"], g["t_obs"])
        if observable <= 0.0:
            return -math.inf
        for lo, hi in g["onsets"]:
            p = onset_cdf(k, mu, g["exposure"], hi) - onset_cdf(k, mu, g["exposure"], lo)
            if p <= 0.0:
                return -math.inf
            total += math.log(p) - math.log(observable)
    return total


def _cell_widths(values: Sequence[float], log: bool) -> list[float]:
    xs = [math.log(v) for v in values] if log else list(values)
    edges = [xs[0] - (xs[1] - xs[0]) / 2]
    edges += [(xs[i] + xs[i + 1]) / 2 for i in range(len(xs) - 1)]
    edges.append(xs[-1] + (xs[-1] - xs[-2]) / 2)
    return [edges[i + 1] - edges[i] for i in range(len(xs))]


def _lognormal_logpdf_of_log(x: float, spec: Sequence[float]) -> float:
    median, lo, hi = spec
    sd = (math.log(hi) - math.log(lo)) / (2 * Z90)
    z = (math.log(x) - math.log(median)) / sd
    return -0.5 * z * z


def prior_weights() -> dict[tuple[float, float], float]:
    """Pre-Kenya prior mass per grid point: lognormal in mu and in k, times cell widths."""
    wk = dict(zip(K_GRID, _cell_widths(K_GRID, log=True)))
    wm = dict(zip(MU_GRID, _cell_widths(MU_GRID, log=False)))
    raw = {}
    for k in K_GRID:
        for mu in MU_GRID:
            # density of log mu times d(log mu) = density of log mu times d mu / mu
            raw[(k, mu)] = math.exp(_lognormal_logpdf_of_log(mu, PRIOR_MU)
                                    + _lognormal_logpdf_of_log(k, PRIOR_K)) * wk[k] * wm[mu] / mu
    total = sum(raw.values())
    return {key: v / total for key, v in raw.items()}


def posterior(variant: str = "primary", combined: bool = True) -> list[tuple[float, float, float]]:
    """Normalised posterior mass as (k, mu, weight) over the grid."""
    groups = observations(variant)
    prior = prior_weights() if combined else None
    logs = {}
    for k in K_GRID:
        for mu in MU_GRID:
            ll = log_likelihood(k, mu, groups)
            logs[(k, mu)] = ll + (math.log(prior[(k, mu)]) if prior else 0.0)
    top = max(logs.values())
    raw = {key: math.exp(v - top) for key, v in logs.items()}
    total = sum(raw.values())
    if not math.isfinite(total) or total <= 0.0:
        raise ValueError(f"posterior has no mass for variant {variant!r}")
    return [(k, mu, raw[(k, mu)] / total) for k in K_GRID for mu in MU_GRID]


# ---------------------------------------------------------------------------------------
# Summaries.
# ---------------------------------------------------------------------------------------
def _marginal_quantiles(post: Sequence[tuple[float, float, float]], index: int,
                        qs: Sequence[float] = (0.1, 0.5, 0.9)) -> list[float]:
    mass: dict[float, float] = {}
    for row in post:
        mass[row[index]] = mass.get(row[index], 0.0) + row[2]
    out = []
    for q in qs:
        acc = 0.0
        for value in sorted(mass):
            acc += mass[value]
            if acc >= q - 1e-12:
                out.append(value)
                break
    return out


def predictive(post: Sequence[tuple[float, float, float]], fn) -> float:
    return sum(w * fn(k, mu) for k, mu, w in post)


def release_risk(post: Sequence[tuple[float, float, float]], group: str) -> float:
    """Posterior predictive P(onset after release | infected, no onset by the end of 17 October).

    A ratio of posterior averages, not an average of per-distribution ratios: conditioning on
    no onset by T also reweights the incubation distributions toward the longer ones.
    """
    exposure = HOSP_EXPOSURE if group == "hospital" else RELATIVES_EXPOSURE
    after_release = predictive(post, lambda k, mu: 1.0 - onset_cdf(k, mu, exposure, RELEASE[group]))
    after_t = predictive(post, lambda k, mu: 1.0 - onset_cdf(k, mu, exposure, T_OBS))
    return after_release / after_t if after_t > 0.0 else 0.0


def summarise(post: Sequence[tuple[float, float, float]]) -> dict:
    return {
        "mean_days_10_50_90": _marginal_quantiles(post, 1),
        "shape_10_50_90": _marginal_quantiles(post, 0),
        "predictive_mean_days": predictive(post, lambda k, mu: mu),
        "predictive_p_over_21_days": predictive(post, lambda k, mu: 1.0 - cdf(k, mu, 21.0)),
        "predictive_p_over_28_days": predictive(post, lambda k, mu: 1.0 - cdf(k, mu, 28.0)),
        "observable_share_by_17_october": {
            "hospital": predictive(post, lambda k, mu: onset_cdf(k, mu, HOSP_EXPOSURE, T_OBS)),
            "relatives": predictive(post, lambda k, mu: onset_cdf(k, mu, RELATIVES_EXPOSURE, T_OBS))},
        "p_onset_after_release_if_still_incubating": {g: release_risk(post, g) for g in RELEASE},
    }


def candidate_rows(groups: Sequence[Mapping[str, Any]]) -> dict:
    base = log_likelihood(CANDIDATES["programme_prior"]["k"], CANDIDATES["programme_prior"]["mu"], groups)
    rows = {}
    for name, c in CANDIDATES.items():
        ll = log_likelihood(c["k"], c["mu"], groups)
        rows[name] = {"k": c["k"], "mean_days": c["mu"], "label": c["label"],
                      "log_likelihood": ll, "bayes_factor_vs_programme_prior": math.exp(ll - base),
                      "p_over_21_days": 1.0 - cdf(c["k"], c["mu"], 21.0)}
    return rows


SENSITIVITY = (
    ("A1_case2_as_source", "Case 3 and the relative confirmed on 17 October were infected by case 2 rather than the first case (exposure 13 October)"),
    ("A2_relatives_before_isolation", "The relatives' exposure fell only before the first case's hospital isolation (3 October)"),
    ("A3_onsets_one_day_earlier", "Every onset window one day earlier"),
    ("A4_truncation_14", "Onsets observable only to the end of 16 October; onset windows capped there"),
    ("A5_hospital_exposure_to_3", "The health workers' exposure extended through 5 October"),
)

DISCLOSURES = [
    "The method, grid, observation windows, prior summary and sensitivity rows were fixed in a plan before the first run: sha256 cf4365e4b81bcc3a16deb111eeff46f6e9200faf60b77a88ea38c1b25db645b1, written 2026-10-17 at about 21:31Z.",
    "Prior sight: before the plan the author computed rough tail shares under the programme prior and saw that six onsets 8 to 14 days after exposure are unlikely under it. The direction of the result was therefore expected; the method was fixed before any run.",
    "Only case 2's onset (13 October) is published. The other five onset windows are inferred from the dates of confirmation, on the reading that contacts in quarantine are tested on the day fever appears. Row A3 moves every window a day earlier.",
    "Who infected whom is not published. The primary reading has the first case infecting all six. Row A1 has case 2, rather than the first case, infecting two relatives, which would shorten their incubation and is the strongest alternative.",
    "Row A4 caps the onset windows at its earlier truncation time so that no onset is credited beyond the observation limit. This reading is the implementation's, made when the row was coded.",
    "Contacts may under-report early symptoms to avoid isolation, which would make recorded onsets late. Monitoring in quarantine should limit this for fever, the first symptom the ministry reports for four of the six; that it does is judgement.",
    "The pre-Kenya prior is a summary (independent lognormals fitted to the 10th, 50th and 90th percentiles) of the 14 October fit to MacNeil 2010, Wamala 2010 and Kratz 2015, not that fit's full grid.",
    "Correction at validation, before publication: the plan named a West Africa candidate with mean 11.4 and standard deviation 8.1 days. Checking the source showed that 11.4 is the observed mean for multi-day exposures (its standard deviation is not given) and 8.1 belongs to another cell, so the pair cannot be cited. The candidate is now the paper's own gamma fit for single-day exposures, mean 9.1 and standard deviation 7.3. It is a comparison only and does not enter the posterior or the escape forecast.",
    "Wamala 2010 enters only through the 14 October fit that the prior summarises; it was not re-read for this analysis. Roddy 2012 (PLoS One, the same 2007 outbreak) states that incubation periods were not measured. No other Bundibugyo incubation source was found.",
    "Label changed at review, before publication: the relatives' group was first labelled household. The authorities say only relative, so it is now labelled relatives; no number changed.",
    "Read after the first run: the Kenya data alone bound the mean from below, not from above. Onsets seen only up to 17 October cannot rule out a very long incubation, so the Kenya-only posterior rises to the grid's upper edge (16 days) and its upper quantiles reflect the grid, not the data. It also piles up at the largest shape on the grid (8). The combined posterior is the one used downstream.",
    "Correction at review, before publication: the chance of onset after release was first computed as the average, over incubation distributions, of each one's conditional chance. The quantity described is the posterior predictive chance for a contact still incubating, a ratio of averages, which gives more weight to the longer incubations that survive to 17 October. It is now computed that way; the earlier values were lower by about one percentage point.",
    "The Bayes factors depend on the reading: the ratio for the 10-day candidate against the programme prior is about 120 under the primary reading and ranges from about 22 to 200 across the sensitivity rows, which give each value.",
    "Six cases from one cluster share one index case and its course of illness. They are not independent draws of the incubation period, and a heavy viral load in the late stage could itself shorten or lengthen incubation.",
]


def _rounded(obj: Any, nd: int = 5) -> Any:
    if isinstance(obj, float):
        return float(f"{obj:.{nd}g}") if obj != 0.0 else 0.0
    if isinstance(obj, dict):
        return {k: _rounded(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_rounded(v, nd) for v in obj]
    return obj


payload_digest = kc.payload_digest


def build_artifact() -> dict:
    groups = observations()
    kenya_only = posterior(combined=False)
    combined = posterior(combined=True)
    payload = {
        "schema": "lovs-incubation-evidence/v1",
        "analysis_id": "kenya-bdbv-2026-10-17-incubation",
        "model_version": MODEL_VERSION,
        "question": ("What do the exposure windows and onsets of Kenya's six linked Bundibugyo cases say "
                     "about the incubation period, and is 21-day follow-up long enough for this cluster?"),
        "evidence_through_utc": "2026-10-17T21:30:00Z",
        "observations": {"groups": groups, "clock": "days from 2026-10-03 00:00 East Africa Time",
                         "sources": ["kenya_moh_2026_10_14", "who_afro_2026_10_16", "kenya_moh_2026_10_17"]},
        "candidates": candidate_rows(groups),
        "kenya_only": summarise(kenya_only),
        "combined": summarise(combined),
        "sensitivity": [{"variant": name, "label": label,
                         "kenya_only": summarise(posterior(name, combined=False)),
                         "combined": summarise(posterior(name, combined=True)),
                         "candidates": {n: r["bayes_factor_vs_programme_prior"]
                                        for n, r in candidate_rows(observations(name)).items()}}
                        for name, label in SENSITIVITY],
        "prior": {"mean_days_median_10_90": PRIOR_MU, "shape_median_10_90": PRIOR_K,
                  "source": "marginals of the 14 October Bundibugyo incubation fit (macneil2010, wamala2010, kratz2015)"},
        "grid": {"shape": K_GRID, "mean_days": MU_GRID},
        "release_days": RELEASE,
        "disclosures": DISCLOSURES,
        "citations": CITATIONS,
        "method": {"code": "lovs/forecast/kenya_incubation.py", "table_step_days": DT, "table_max_days": T_MAX},
    }
    payload = _rounded(payload)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    artifact = build_artifact()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out.name, "payload_sha256": artifact["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
