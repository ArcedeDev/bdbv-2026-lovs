"""Rebuild the declared ring-timing calibration in data/vaccine-field-effectiveness/ring-timing.json.

Approximate Bayesian computation on Zaire ebolavirus data, using
lovs.vaccine_field_effectiveness.ring_cohort_profile:

  targets (acceptance)
    - Guinea trial, vaccinees, onset days 0-9: 11/2111 vs 21/3096
      (Henao-Restrepo 2017 Table 4), exact interval -0.67 to 0.67, simulated
      with the Guinea delay (onset to randomisation mean 9.7 d, sd 5.3;
      Henao-Restrepo 2017 Table 1);
    - WHO/INRB 2019 early-case share 56/71 (0.676 to 0.877), simulated with
      the DRC delay (gamma mean 7.6 d, sd 5.5; median 7 d, IQR 3-10).
  weight
    - Poisson likelihood of the trial's 0/2108 late vaccinee cases,
      exp(-16 x 2108/3075 x (1 - VE_late)), VE_late with the Guinea delay.

Usage:
    python3 tools/calibrate_vaccine_ring_timing.py           # rewrite rows and shares
    python3 tools/calibrate_vaccine_ring_timing.py --check   # compare with the stored file

About a minute (1,500 parameter sets x 2 profiles x 6,000 draws). Deterministic.
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

N_SETS, DRAWS, SEED = 1500, 6000, 11
DELAY_DRC = Param("delay", "gamma_mean_sd", (7.6, 5.5), "derived", "DRC 2018-19 index onset to ring vaccination")
DELAY_GUINEA = Param("delay", "gamma_mean_sd", (9.7, 5.3), "source", "Guinea trial onset to randomisation")
TAU = Param("tau", "gamma_mean_sd", (3.9, 3.0), "derived",
            "Zaire serial interval 15.3 d minus incubation 11.4 d (WHO Ebola Response Team, NEJM 2014)")
TRIAL_0_9 = (-0.67, 0.67)
WHO_EARLY = (0.676, 0.877)
KEEP = ("index_share", "onset_days", "pep_effect", "ve_post", "inc_mean", "inc_sd", "ve_0_9_trial",
        "ve_late_trial", "early_share_drc", "ve_any_drc", "ve_late_drc", "ve_window_drc",
        "after_protection_drc", "trial_late_likelihood", "weight")


def _profile(theta: dict, delay: Param, seed: int, window: tuple[float, float]) -> dict:
    inc = Param("inc", "gamma_mean_sd", (theta["inc_mean"], theta["inc_sd"]), "derived", "calibration")
    return vfe.ring_cohort_profile(theta["ve_post"], delay, Param("o", "fixed", (theta["onset_days"],), "derived", "c"),
                                   TAU, inc, Param("s", "fixed", (theta["index_share"],), "derived", "c"),
                                   draws=DRAWS, seed=seed, pep_effect=theta["pep_effect"], window=window)


def calibrate() -> dict:
    rng = random.Random(SEED)
    accepted = []
    for i in range(N_SETS):
        theta = {"index_share": rng.uniform(0.0, 0.9), "onset_days": rng.uniform(2.0, 14.0),
                 "pep_effect": rng.uniform(0.0, 0.6), "ve_post": rng.uniform(0.85, 1.0),
                 "inc_mean": rng.uniform(8.0, 12.0), "inc_sd": rng.uniform(3.0, 7.0)}
        tg = _profile(theta, DELAY_GUINEA, 1000 + i, (0.0, 10.0))
        dr = _profile(theta, DELAY_DRC, 5000 + i, (3.0, 10.0))
        row = {**theta, "ve_0_9_trial": tg["ve_window"], "ve_late_trial": tg["ve_late"],
               "early_share_drc": dr["early_share"], "ve_any_drc": dr["ve_any"], "ve_late_drc": dr["ve_late"],
               "ve_window_drc": dr["ve_window"], "after_protection_drc": dr["after_protection"],
               "trial_late_likelihood": math.exp(-16 * (2108 / 3075) * (1.0 - tg["ve_late"]))}
        if TRIAL_0_9[0] <= row["ve_0_9_trial"] <= TRIAL_0_9[1] and WHO_EARLY[0] <= row["early_share_drc"] <= WHO_EARLY[1]:
            row["weight"] = row["trial_late_likelihood"]
            accepted.append({k: row[k] for k in KEEP})
    wsum = sum(r["weight"] for r in accepted)
    ess = wsum ** 2 / sum(r["weight"] ** 2 for r in accepted) if accepted else 0.0
    return {"rows": accepted, "passed": len(accepted), "ess": round(ess, 1)}


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
    stored["result"].update(passed=cal["passed"], effective_sample_size=cal["ess"])
    doc.update(_series(cal["rows"]))
    doc["carry_to_bdbv"]["parameters"] = {**rel.RING_REBUILD, "delay_factor": list(rel.RING_REBUILD["delay_factor"])}
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {path.relative_to(REPO_ROOT)}: {cal['passed']} passed, ESS {cal['ess']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
