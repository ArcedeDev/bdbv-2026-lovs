# SPDX-License-Identifier: Apache-2.0
"""Kenya Bundibugyo incubation evidence, version 2: case 1 or an earlier quarantine-mate.

Version 1 (`kenya_incubation.py`) reads every later case in Kenya's cluster as infected by the
first case. If some were infected inside quarantine by an earlier case, that reading makes their
incubation look too long. Version 2 was registered on 20 October, before any further onset date
or housing statement was published, and runs once on evidence frozen 24 hours after its trigger.

Each case j has two possible sources. Source A is the first case, with exposure uniform over the
registered window. Source B is an earlier Kenya case housed with j, with exposure uniform from
that case's onset to its isolation. The weight on source B, w_j, is fixed by what the authorities
state about housing: 0 if j was housed apart or is a health worker with no shared site stated,
1 if j shared housing with an earlier ill case, and 0.5 otherwise. Each case's likelihood is

    (1 - w_j) L_A + w_j L_B,    L_X = P(onset in W_j | X) / P(onset <= T | X),

which is version 1's right truncation applied case by case; with every w_j = 0 it is version 1.
The gamma machinery, grid, prior and summaries are version 1's, imported unchanged.

Stdlib only and deterministic: everything is numerical integration, no sampling.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from lovs.forecast import kenya_incubation as v1

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-21-incubation-v2.json"
MODEL_VERSION = "kenya_incubation-v2.0.0"

PLAN_SHA256 = "3cb955643151c3c913f3ad3c22bcf1e7cd839c6f95a30f1079cb9404666f1f91"
INTERPRETATION_SHA256 = "f036bf10f5aba69b6d6944dbf8cf130315b4ef2aab7f764fe32cf33ae153a064"

# Model clock: days from 2026-10-03 00:00 East Africa Time, as version 1.
CLOCK_ZERO = datetime(2026, 10, 3, tzinfo=timezone(timedelta(hours=3)))
TRIGGER_UTC = datetime(2026, 10, 20, 15, 20, 16, tzinfo=timezone.utc)
FREEZE_UTC = TRIGGER_UTC + timedelta(hours=24)
T_FREEZE = (FREEZE_UTC - CLOCK_ZERO).total_seconds() / 86400.0

RELATIVES_EXPOSURE = v1.RELATIVES_EXPOSURE      # 3 to 5 October
HEALTH_WORKER_EXPOSURE = v1.HOSP_EXPOSURE       # 3 and 4 October
HEALTH_WORKER_RULE = ("health worker", "patient attendant")

# Frozen inputs: (case, role in the authorities' words, October date of confirmation,
# October date of an authority-stated onset or None). Every case after the first that the
# authorities link to it, as of the freeze.
CASES = (
    (2, "relative", 14, 13),
    (3, "relative", 16, None),
    (4, "relative", 17, None),
    (5, "health worker", 17, None),
    (6, "health worker", 17, None),
    (7, "health worker", 17, None),
    (8, "patient attendant", 18, None),
    (9, "relative", 19, None),
)


def day(october_date: int) -> float:
    """Start of an October date on the model clock; the date covers [day, day + 1)."""
    return float(october_date - 3)


def onset_window(confirmed: int, stated_onset: int | None, t_obs: float) -> tuple[float, float]:
    """A stated onset date, otherwise confirmation minus a lag of 0 to 2 days, capped at T."""
    if stated_onset is not None:
        return (day(stated_onset), day(stated_onset) + 1.0)
    return (day(confirmed) - 2.0, min(day(confirmed) + 1.0, t_obs))


def source_b_window(confirmed: int, stated_onset: int | None) -> tuple[float, float]:
    """From the earlier case's onset (or confirmation minus 1 day) to its isolation.

    No isolation date is stated for any case, so isolation is the confirmation date.
    """
    start = stated_onset if stated_onset is not None else confirmed - 1
    return (day(start), day(confirmed) + 1.0)


def build_cases(variant: str = "main", t_obs: float = T_FREEZE) -> list[dict]:
    """Apply the registered rules to the frozen inputs."""
    rows = []
    for case, role, confirmed, stated in CASES:
        health_rule = role in HEALTH_WORKER_RULE
        exposure = HEALTH_WORKER_EXPOSURE if health_rule else RELATIVES_EXPOSURE
        if variant == "check_case8_exposure_to_death" and case == 8:
            exposure = (HEALTH_WORKER_EXPOSURE[0], RELATIVES_EXPOSURE[1])
        # Candidates: earlier-confirmed cases on the same side of the health-worker rule, since no
        # authority states that a relative and a health worker shared a site.
        candidates = [(c, source_b_window(cc, cs)) for c, r, cc, cs in CASES
                      if cc < confirmed and (r in HEALTH_WORKER_RULE) == health_rule]
        w = 0.0 if health_rule else 0.5
        if variant == "all_w0":
            w = 0.0
        elif variant == "all_w1":
            w = 1.0
        rows.append({"case": case, "role": role,
                     "onset_window": onset_window(confirmed, stated, t_obs),
                     "onset_basis": "stated onset" if stated is not None else "confirmation minus 0 to 2 days",
                     "source_a_exposure": exposure, "w": w,
                     "source_b": [{"case": c, "exposure": win} for c, win in candidates]})
    return rows


def _conditional_window_probability(k: float, mu: float, exposure: Sequence[float],
                                    window: Sequence[float], t_obs: float) -> float:
    observable = v1.onset_cdf(k, mu, exposure, t_obs)
    if observable <= 0.0:
        return 0.0
    p = v1.onset_cdf(k, mu, exposure, window[1]) - v1.onset_cdf(k, mu, exposure, window[0])
    return max(p, 0.0) / observable


def case_likelihood(k: float, mu: float, case: dict, t_obs: float) -> float:
    """(1 - w) L_A + w L_B, with L_B the equal-weight average over the source B candidates."""
    window = case["onset_window"]
    la = _conditional_window_probability(k, mu, case["source_a_exposure"], window, t_obs)
    if not case["source_b"]:
        return la      # no earlier case to be infected by: source A only, whatever w is
    lb = sum(_conditional_window_probability(k, mu, b["exposure"], window, t_obs)
             for b in case["source_b"]) / len(case["source_b"])
    return (1.0 - case["w"]) * la + case["w"] * lb


def log_likelihood(k: float, mu: float, cases: Sequence[dict], t_obs: float) -> float:
    total = 0.0
    for case in cases:
        lik = case_likelihood(k, mu, case, t_obs)
        if lik <= 0.0:
            return -math.inf
        total += math.log(lik)
    return total


def posterior(cases: Sequence[dict], t_obs: float, combined: bool) -> list[tuple[float, float, float]]:
    """Normalised posterior mass as (k, mu, weight) over version 1's grid."""
    prior = v1.prior_weights() if combined else None
    logs = {}
    for k in v1.K_GRID:
        for mu in v1.MU_GRID:
            ll = log_likelihood(k, mu, cases, t_obs)
            logs[(k, mu)] = ll + (math.log(prior[(k, mu)]) if prior else 0.0)
    top = max(logs.values())
    if not math.isfinite(top):
        raise ValueError("posterior has no mass")
    raw = {key: math.exp(v - top) for key, v in logs.items()}
    total = sum(raw.values())
    return [(k, mu, raw[(k, mu)] / total) for k in v1.K_GRID for mu in v1.MU_GRID]


def summarise(post: Sequence[tuple[float, float, float]]) -> dict:
    top_mu = v1.MU_GRID[-1]
    return {
        "mean_days_10_50_90": v1._marginal_quantiles(post, 1),
        "shape_10_50_90": v1._marginal_quantiles(post, 0),
        "posterior_mean_days": v1.predictive(post, lambda k, mu: mu),
        "predictive_p_over_21_days": v1.predictive(post, lambda k, mu: 1.0 - v1.cdf(k, mu, 21.0)),
        "predictive_p_over_28_days": v1.predictive(post, lambda k, mu: 1.0 - v1.cdf(k, mu, 28.0)),
        "mass_at_grid_upper_mean": sum(w for _, mu, w in post if mu == top_mu),
    }


READING_THRESHOLD_DAYS = 10.0


def reading(main_mean: float, w1_mean: float) -> dict:
    """The plan's pre-stated reading, applied to the Kenya-only posterior means."""
    if main_mean < READING_THRESHOLD_DAYS:
        branch = "below_10"
        text = ("The Kenya-only posterior mean under the main weights is below 10 days: the Kenya data no "
                "longer argue for a heavier incubation tail than the decided prior, gamma(3.0, rate 0.406).")
    elif w1_mean >= READING_THRESHOLD_DAYS:
        branch = "10_or_more_main_and_w1"
        text = ("The Kenya-only posterior mean is 10 days or more under the main weights and with every "
                "weight on quarantine set to 1: the Kenya data still argue for a heavier incubation tail. "
                "That is evidence for a review of the prior, not an automatic change to gamma(3.0, rate 0.406).")
    else:
        branch = "10_or_more_main_below_10_w1"
        text = ("The Kenya-only posterior mean is 10 days or more under the main weights but below 10 days "
                "with every weight on quarantine set to 1. Neither stated branch applies: the Kenya data argue "
                "for a heavier tail only if there was little or no transmission inside quarantine.")
    return {"rule": ("Kenya-only posterior mean below 10 days under the main weights: no longer argues for a "
                     "heavier tail. 10 days or more under the main weights and the w = 1 row: still argues, as "
                     "evidence for a prior review, not an automatic change."),
            "main_kenya_only_mean_days": main_mean, "w1_kenya_only_mean_days": w1_mean,
            "branch": branch, "statement": text,
            "unchanged": "The escape forecast, the 9 and 14 October forecasts and Block 11 are not changed by this result."}


ROWS = (
    ("main", "Main weights: relatives 0.5, health workers and the patient attendant 0", True),
    ("all_w0", "Every weight on quarantine set to 0: version 1's reading, on version 2's cases and truncation", True),
    ("all_w1", "Every weight on quarantine set to 1, wherever an earlier case could have been the source", True),
    ("check_case8_exposure_to_death",
     "Interpretation check, not registered and not used in the reading: the patient attendant exposed to the "
     "first case through 5 October, not only 3 and 4 October", False),
)

CITATIONS = {
    **{key: v1.CITATIONS[key] for key in ("macneil2010", "wamala2010", "kratz2015",
                                          "kenya_moh_2026_10_14", "kenya_moh_2026_10_17")},
    "who_afro_2026_10_16": "WHO AFRO, 16 Oct 2026: second and third cases confirmed on 14 and 16 October, both relatives of the first case and linked to it",
    "kenya_moh_2026_10_18": "Kenya Ministry of Health, 18 Oct 2026, response update: eighth case confirmed, not a healthcare worker",
    "ecdc_2026_10_20": "ECDC, Ebola outbreak page, updated 20 Oct 2026: according to the Director-General for Health, the eighth case was not a healthcare worker but a patient attendant",
    "kenya_moh_2026_10_20": "Kenya Ministry of Health, Director-General for Health, 20 Oct 2026, relayed by the press: ninth case, a relative of the first case and a known contact in quarantine, fell ill in quarantine and tested positive on 19 Oct; the same statement describes quarantine of contacts in facilities",
}

DISCLOSURES = [
    f"Registered method: plan sha256 {PLAN_SHA256}, written 2026-10-20 at 01:27:39 UTC before any further onset date or housing statement was published. The plan is not published because it names details of the cluster that this repository keeps out of public files; its rules are restated in this artifact's inputs and method.",
    "Trigger: the plan runs once, at the first of (1) authority onset dates for at least three of cases 3 to 8, (2) an authority statement of where the quarantined relatives were housed, or (3) the escape-forecast review. A ministry statement of 20 October, first published at 15:20:16 UTC, describes the quarantine cohort that the relatives belong to as held in quarantine facilities. That the cohort is the relatives' is the ruling's reading of the ministry's and the registry's dates. The statement was ruled to meet trigger 2, and the founder confirmed the ruling before the freeze. Evidence froze 24 hours later, at 2026-10-21T15:20:16Z, and nothing published after that is used.",
    f"Points the plan leaves open were settled in writing before the freeze and before any code was written (sha256 {INTERPRETATION_SHA256}): the date convention, which cases can be a source for which, how several possible sources combine, the patient attendant's exposure window, and how the reading is applied.",
    "Only case 2's onset (13 October) is stated by an authority. Every other onset window is the confirmation date minus 0 to 2 days, as version 1. The eighth case's onset date appears only through an unnamed ministry source and is not used.",
    "No authority states an isolation date, so each earlier case's isolation is its confirmation date, as the plan says. Wording without a date, such as isolated at once, is read as not stated.",
    "A source B candidate for case j is any case confirmed on an earlier date on the same side of the health-worker rule. Onsets are not stated, so the plan's condition that the earlier case's onset precede j's is applied through the order of confirmation, and the likelihood gives no weight to exposure after j's onset window. Candidates stay on one side of the health-worker rule because no authority states that a relative and a health worker shared a site, which is also why the health workers' weight is 0. With several candidates their likelihoods are averaged with equal weight. Case 2 has no earlier case, so it has source A only whatever its weight.",
    "The plan's rule for the patient attendant is the health-worker rule, read as both weight 0 and the health workers' exposure window of 3 and 4 October. The check row gives the attendant exposure through 5 October; it is not registered and does not enter the reading.",
    "With every weight 0 the method is version 1's. The tests confirm this on version 1's own inputs, where version 1's posterior is reproduced exactly. The w = 0 row here differs from the version 1 artifact because it has two more cases and truncation at the freeze instead of 17 October.",
    "The truncation time is the freeze. An onset before the freeze that was not yet confirmed by it would be missed, as in version 1.",
    "Version 1's chance of onset after release is not reproduced: the plan does not register it.",
    "Eight cases from one cluster share one index case and its course of illness. They are not independent draws of the incubation period.",
    "Read after the run: unlike version 1, the Kenya-only posterior does not rise to the grid's upper edge, because onsets are now observed to 21 October. Some mass still sits at the 16-day edge, and the 90th percentile of the shape is the grid's largest shape, so the Kenya-only upper tail partly reflects the grid.",
]


def _block(cases: Sequence[dict]) -> dict:
    return {"kenya_only": summarise(posterior(cases, T_FREEZE, combined=False)),
            "combined": summarise(posterior(cases, T_FREEZE, combined=True))}


def build_artifact() -> dict:
    rows = {name: _block(build_cases(name)) for name, _, _ in ROWS}
    payload = {
        "schema": "lovs-incubation-evidence/v2",
        "analysis_id": "kenya-bdbv-2026-10-21-incubation-v2",
        "model_version": MODEL_VERSION,
        "version_1": "data/case-conditioned/kenya-2026-10-17-incubation.json",
        "question": ("Do the Kenya cluster's late onsets argue for a heavier Bundibugyo incubation tail than the "
                     "decided prior gamma(3.0, rate 0.406), once transmission inside quarantine is allowed for?"),
        "trigger": "authority statement on where the quarantined relatives were housed",
        "trigger_utc": TRIGGER_UTC.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence_through_utc": FREEZE_UTC.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clock": "days from 2026-10-03 00:00 East Africa Time",
        "truncation_day": T_FREEZE,
        "cases": build_cases("main"),
        "rows": [{"variant": name, "label": label, "registered": registered, **rows[name]}
                 for name, label, registered in ROWS],
        "reading": reading(rows["main"]["kenya_only"]["posterior_mean_days"],
                           rows["all_w1"]["kenya_only"]["posterior_mean_days"]),
        "prior": {"mean_days_median_10_90": v1.PRIOR_MU, "shape_median_10_90": v1.PRIOR_K,
                  "source": "version 1's summary of the 14 October Bundibugyo incubation fit (macneil2010, wamala2010, kratz2015)"},
        "grid": {"shape": v1.K_GRID, "mean_days": v1.MU_GRID},
        "registration": {"plan_sha256": PLAN_SHA256, "interpretation_sha256": INTERPRETATION_SHA256},
        "disclosures": DISCLOSURES,
        "citations": CITATIONS,
        "method": {"code": "lovs/forecast/kenya_incubation_v2.py", "reuses": "lovs/forecast/kenya_incubation.py",
                   "table_step_days": v1.DT, "table_max_days": v1.T_MAX},
    }
    payload = v1._rounded(payload)
    payload["payload_sha256"] = v1.payload_digest(payload)
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
