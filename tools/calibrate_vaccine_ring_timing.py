"""Rebuild the declared ring-timing calibration in data/vaccine-field-effectiveness/ring-timing.json.

Approximate Bayesian computation on Zaire ebolavirus data, using
lovs.vaccine_field_effectiveness.ring_cohort_profile:

  targets (acceptance)
    - Guinea trial, onset days 0-9: 11/2111 vaccinated in immediate rings vs
      21/3096 eligible in delayed rings, not yet vaccinated (Henao-Restrepo
      2017 Table 4), exact interval -0.67 to 0.67, simulated
      with the Guinea delay (onset to randomisation mean 9.7 d, sd 5.3;
      Henao-Restrepo 2017 Table 1);
    - WHO/INRB 2019 early-case share 56/71 (0.676 to 0.877), simulated with
      the DRC delay (gamma mean 7.6 d, sd 5.5; median 7 d, IQR 3-10).
  parameters drawn per set
    - index share U(0, 0.9), immune onset U(2, 14) d, post-exposure effect
      U(0, 0.6), VE after onset U(0.85, 1), incubation mean U(9.1, 11.4) d (the
      span of the four means in WHO Ebola Response Team 2014 Table 2) and sd
      U(3, 7) d. The onset-to-transmission mean is not drawn: it is the Zaire
      serial interval, 15.3 d, minus the set's incubation mean (sd 3.0; see
      lovs.vaccine_scenario_release.SERIAL_INTERVAL_ZAIRE).
  acceptance
    - screened at 6,000 draws against both windows widened by 0.08, then
      confirmed at 40,000 draws against the exact windows; the stored row
      holds the confirmed values.
  weight
    - Poisson likelihood of the trial's 0/2108 late vaccinee cases,
      exp(-16 x 2108/3075 x (1 - VE_late)), VE_late with the Guinea delay.

Usage:
    python3 tools/calibrate_vaccine_ring_timing.py           # rewrite rows and shares
    python3 tools/calibrate_vaccine_ring_timing.py --check   # compare with the stored file

About 16 minutes (25,000 parameter sets x 2 profiles x 6,000 draws, then each screened set again at
40,000 draws). Deterministic.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lovs import vaccine_field_effectiveness as vfe  # noqa: E402
from lovs import vaccine_scenario_release as rel  # noqa: E402
from lovs.vaccine_field_effectiveness import Param  # noqa: E402

N_SETS, DRAWS, SEED = 25000, 6000, 11
# Each set is screened at DRAWS against windows widened by SCREEN_MARGIN, then
# confirmed at CONFIRM_DRAWS against the exact windows. At 6,000 draws a set near
# a window edge can pass by simulation noise alone (about 30% of the sets that
# passed a single 6,000-draw stage failed at 40,000 draws).
CONFIRM_DRAWS, SCREEN_MARGIN = 40000, 0.08
DELAY_DRC = Param("delay", "gamma_mean_sd", (7.6, 5.5), "derived", "DRC 2018-19 index onset to ring vaccination")
DELAY_GUINEA = Param("delay", "gamma_mean_sd", (9.7, 5.3), "source", "Guinea trial onset to randomisation")
TRIAL_0_9 = (-0.67, 0.67)
WHO_EARLY = (0.676, 0.877)
KEEP = ("set", "index_share", "onset_days", "pep_effect", "ve_post", "inc_mean", "inc_sd", "tau_mean", "ve_0_9_trial",
        "ve_late_trial", "early_share_drc", "ve_any_drc", "ve_late_drc", "ve_window_drc",
        "after_protection_drc", "trial_late_likelihood", "weight")


def _profile(theta: dict, delay: Param, seed: int, window: tuple[float, float], draws: int = DRAWS) -> dict:
    inc = Param("inc", "gamma_mean_sd", (theta["inc_mean"], theta["inc_sd"]), "derived", "calibration")
    return vfe.ring_cohort_profile(theta["ve_post"], delay, Param("o", "fixed", (theta["onset_days"],), "derived", "c"),
                                   rel.tau_param(theta["tau_mean"]), inc, Param("s", "fixed", (theta["index_share"],), "derived", "c"),
                                   draws=draws, seed=seed, pep_effect=theta["pep_effect"], window=window)


def _inside(value: float, window: tuple[float, float], margin: float = 0.0) -> bool:
    return window[0] - margin <= value <= window[1] + margin


# Seed bases per stage and target (trial, DRC). Each base is far enough from the
# next that no two seeds coincide for up to a million sets.
SEED_BASES = {"screen": (1_000_000, 2_000_000), "confirm": (3_000_000, 4_000_000)}


def profile_set(theta: dict, i: int, stage: str) -> dict:
    """Both target profiles of set i at the stage's precision, and the row they give."""
    if N_SETS > 1_000_000:
        raise ValueError("seed bases cover at most 1,000,000 sets")
    draws = DRAWS if stage == "screen" else CONFIRM_DRAWS
    seeds = tuple(base + i for base in SEED_BASES[stage])
    tg = _profile(theta, DELAY_GUINEA, seeds[0], (0.0, 10.0), draws)
    dr = _profile(theta, DELAY_DRC, seeds[1], (3.0, 10.0), draws)
    return {**theta, "set": i, "ve_0_9_trial": tg["ve_window"], "ve_late_trial": tg["ve_late"],
            "early_share_drc": dr["early_share"], "ve_any_drc": dr["ve_any"], "ve_late_drc": dr["ve_late"],
            "ve_window_drc": dr["ve_window"], "after_protection_drc": dr["after_protection"],
            "trial_late_likelihood": math.exp(-16 * (2108 / 3075) * (1.0 - tg["ve_late"]))}


def calibrate() -> dict:
    rng = random.Random(SEED)
    accepted, screened = [], 0
    for i in range(N_SETS):
        theta = {"index_share": rng.uniform(0.0, 0.9), "onset_days": rng.uniform(2.0, 14.0),
                 "pep_effect": rng.uniform(0.0, 0.6), "ve_post": rng.uniform(0.85, 1.0),
                 "inc_mean": rng.uniform(*rel.ZAIRE_INCUBATION_MEAN_RANGE), "inc_sd": rng.uniform(3.0, 7.0)}
        theta["tau_mean"] = rel.SERIAL_INTERVAL_ZAIRE - theta["inc_mean"]
        row = profile_set(theta, i, "screen")
        if not (_inside(row["ve_0_9_trial"], TRIAL_0_9, SCREEN_MARGIN)
                and _inside(row["early_share_drc"], WHO_EARLY, SCREEN_MARGIN)):
            continue
        screened += 1
        row = profile_set(theta, i, "confirm")
        if _inside(row["ve_0_9_trial"], TRIAL_0_9) and _inside(row["early_share_drc"], WHO_EARLY):
            row["weight"] = row["trial_late_likelihood"]
            accepted.append({k: row[k] for k in KEEP})
    wsum = sum(r["weight"] for r in accepted)
    ess = wsum ** 2 / sum(r["weight"] ** 2 for r in accepted) if accepted else 0.0
    return {"rows": accepted, "passed": len(accepted), "screened": screened, "ess": round(ess, 1)}


def _series(rows: list[dict]) -> dict:
    """The stored series derived from the accepted rows."""
    return {
        "protected_share_with_pep": [round(x, 6) for x in
                                     rel.ring_protected_shares(rows, with_pep=True, **rel.RING_REBUILD)],
        "protected_share_without_pep": [round(x, 6) for x in
                                        rel.ring_protected_shares(rows, with_pep=False, **rel.RING_REBUILD)],
        "vaccinated_fraction": [round(x, 6) for x in rel.ring_vaccinated_fractions(rows, **rel.RING_REBUILD)],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    path = rel.DATA_DIR / "ring-timing.json"
    doc = json.loads(path.read_text())
    cal = calibrate()
    stored = doc["calibration"]
    same = (cal["passed"] == stored["result"]["passed"] and cal["ess"] == stored["result"]["effective_sample_size"]
            and stored["result"]["of"] == N_SETS and stored["result"].get("screened") == cal["screened"]
            and all(math.isclose(a[k], b[k], rel_tol=1e-12, abs_tol=1e-12)
                    for a, b in zip(cal["rows"], stored["accepted_rows"]) for k in KEEP)
            and len(cal["rows"]) == len(stored["accepted_rows"]))
    if args.check:
        series = _series(stored["accepted_rows"])
        params = {**rel.RING_REBUILD, "delay_factor": list(rel.RING_REBUILD["delay_factor"])}
        same = same and all(doc[k] == series[k] for k in series) and doc["carry_to_bdbv"]["parameters"] == params
        print("matches the stored calibration and series" if same else "differs from the stored file")
        return 0 if same else 1
    stored["accepted_rows"] = cal["rows"]
    stored["result"].update(passed=cal["passed"], screened=cal["screened"], of=N_SETS,
                            effective_sample_size=cal["ess"])
    doc.update(_series(cal["rows"]))
    doc["carry_to_bdbv"]["parameters"] = {**rel.RING_REBUILD, "delay_factor": list(rel.RING_REBUILD["delay_factor"])}
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {path.relative_to(REPO_ROOT)}: {cal['passed']} passed, ESS {cal['ess']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
