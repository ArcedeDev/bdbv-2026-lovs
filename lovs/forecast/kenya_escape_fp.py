# SPDX-License-Identifier: Apache-2.0
"""Kenya escape forecast, first-principles version: inputs triangulated from evidence.

A second reading of the 17 October escape question, built beside the first
(`lovs/forecast/kenya_escape.py`) and reusing its machinery. It changes four things, each by a
rule fixed in a plan before the evidence was read (see DISCLOSURES):

- R1. The share of the first case's transmission that reached people not under follow-up is
  drawn from a Beta fitted to a reference class of recognised filovirus importations, not
  from a range set before the cluster was seen.
- R2. The delay from onset to report for a case outside follow-up is drawn from a range, not
  fixed at the 2014 West Africa value. The rule takes the range of verified urban delays; only
  one was verified, so its registered fallback of 2 to 5 days applies.
- R3 and R4. The incubation posterior allows onsets up to two more days before confirmation
  (RT-PCR can be negative early in illness) and allows that two relatives were infected by
  case 2 rather than the first case, as a mixture in the likelihood.
- R5 and the questions. A new importation is a separate question (B) from a case outside
  follow-up that is not a new importation (A, the headline). The first forecast's combined
  question is reported for comparison.

Stdlib only and deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import kenya_escape as ke
from lovs.forecast import kenya_incubation as ki

REPO = ke.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-17-escape-forecast-fp.json"
MODEL_VERSION = "kenya_escape_fp-v1.0.0"
PLAN_SHA256 = "4d02b4b84c86568ed618465cb1b1936e6343c87ee63712d3ec5ab47910c4f856"
SEED = ke.SEED
N_DRAWS = ke.N_DRAWS

# ---------------------------------------------------------------------------------------
# Evidence-derived inputs (rules R1 to R6 of the plan; sources in REFERENCE_CLASS and EVIDENCE).
# ---------------------------------------------------------------------------------------
# R1 and R6: recognised filovirus importations into functioning health systems that had at
# least one secondary case. Each row: (episode, secondary cases, of which not under follow-up
# at onset, source keys).
# Excluded: importations never recognised (Bamako 2014), spread within a country (Goma 2019,
# Kampala 2022 and 2025), a zoonotic index (Kigali 2024), Uganda 2026 (whether each secondary
# case was under follow-up is not published) and Kenya 2026 itself, which the model conditions on.
REFERENCE_CLASS: tuple[tuple[str, int, int, tuple[str, ...]], ...] = (
    ("Nigeria, Lagos, 2014 (EBOV): 19 secondary cases; one listed contact evaded follow-up and a health "
     "worker he infected was never listed", 19, 2, ("shuaib2014", "fasina2014")),
    ("United States, Dallas, 2014 (EBOV): 2 health workers; the first was not yet on active monitoring at onset",
     2, 1, ("chevalier2014",)),
    ("Spain, Madrid, 2014 (EBOV, repatriated patient): 1 health worker, under self-monitoring at onset",
     1, 0, ("lopaz2015",)),
)
# Recognised importations with no secondary case: they do not enter R1 or R6.
REFERENCE_CLASS_NO_SECONDARY: tuple[str, ...] = ("Mali, Kayes, 2014", "Uganda, Kasese, 2019", "France, Paris, 2026")
# R2: VERIFIED onset-to-isolation delays (days) for cases detected outside follow-up in urban
# settings with high awareness. One value is verified (a health worker who reported fever at once), so
# the registered fallback range of 2 to 5 days applies. Secondary-source delays (Goma 2019: 5 and
# 8 days; several cases found only at death) point longer and are covered by row D2.
URBAN_DELAYS: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("United States, Dallas, 2014: first health worker, isolated the day of onset", 0.5, ("chevalier2014",)),
)
# R3: extra days by which onsets of cases 3 to 7 may precede the inferred windows.
ONSET_WIDEN_DAYS = 2.0
# R4: probability that each of the two relatives confirmed on 16 and 17 October was infected by
# case 2; the rule picks 0.5, 0.1 or 0.3 from what the evidence says about the quarantine setting.
P_QUARANTINE = 0.3
P_QUARANTINE_BASIS = ("The ministry quarantined the relatives in a health facility; whether they were housed together "
                      "is not published, so the rule's middle value applies.")
# R5: importation scaling from the DRC trend (1.0 unless the rule fires).
IMPORT_SCALE = 1.0
IMPORT_SCALE_BASIS = ("DRC confirmed cases, weeks ending 29 September to 13 October 2026 (1,393) against the three weeks "
                      "before (1,570): ratio 0.89, inside the rule's band, so no scaling.")
EVIDENCE: dict[str, str] = {
    "shuaib2014": "Shuaib F, et al. Ebola virus disease outbreak, Nigeria, July to September 2014. MMWR 2014;63(39):867-72",
    "fasina2014": "Fasina FO, et al. Transmission dynamics and control of Ebola virus disease outbreak in Nigeria, July to September 2014. Euro Surveill 2014;19(40):20920",
    "chevalier2014": "Chevalier MS, et al. Ebola virus disease cluster in the United States, Dallas County, Texas, 2014. MMWR 2014;63(46):1087-8",
    "lopaz2015": "Lopaz MA, et al. First secondary case of Ebola outside Africa: epidemiological characteristics and contact monitoring, Spain, September to November 2014. Euro Surveill 2015;20(1):21003",
    "dowell1999": "Dowell SF, et al. Transmission of Ebola hemorrhagic fever: a study of risk factors in family members, Kikwit, 1995. J Infect Dis 1999;179 Suppl 1:S87-91",
    "who_ert2014": kc.CITATIONS["who_ert2014"],
    "cdc_testing": "US CDC, Ebola testing guidance: a negative RT-PCR within 72 hours of symptom onset does not rule out infection",
}


def r1_beta(rows: Sequence[tuple[str, int, int, tuple[str, ...]]] = None) -> tuple[float, float, int, int]:
    rows = REFERENCE_CLASS if rows is None else rows
    n = sum(r[1] for r in rows)
    x = sum(r[2] for r in rows)
    return x + 0.5, n - x + 0.5, n, x


def r1_weight(n: int) -> float:
    """Weight on the reference-class Beta; below 10 secondary cases it shares with v1's range."""
    return 1.0 if n >= 10 else 0.5


def r2_lag_grid(delays: Sequence[tuple[str, float, tuple[str, ...]]] = None) -> tuple[float, ...]:
    values = sorted(v for _, v, _ in (URBAN_DELAYS if delays is None else delays))
    lo, hi = (values[0], values[-1]) if len(values) >= 2 else (2.0, 5.0)
    lo, hi = math.floor(lo * 2) / 2, math.ceil(hi * 2) / 2
    return tuple(lo + 0.5 * i for i in range(int(round((hi - lo) / 0.5)) + 1))


def r6_benchmark() -> dict:
    episodes = len(REFERENCE_CLASS)
    with_escape = sum(1 for r in REFERENCE_CLASS if r[2] > 0)
    return {"episodes_with_secondary_cases": episodes, "episodes_with_a_case_outside_follow_up": with_escape,
            "probability": (with_escape + 0.5) / (episodes + 1.0) if episodes else None,
            "note": ("Jeffreys mean over recognised importations with secondary cases. It is unconditional: "
                     "it ignores that no case outside follow-up had been reported in Kenya by 17 October.")}


# ---------------------------------------------------------------------------------------
# Incubation posterior with widened onset windows and a quarantine-source mixture (R3, R4).
# ---------------------------------------------------------------------------------------
CASE2_EXPOSURE = ki.ONSET_CASE2  # relatives' exposure to case 2: from its onset to its isolation


def _widen(window: Sequence[float], days: float) -> tuple[float, float]:
    return (window[0] - days, window[1])


def _p_case(k: float, mu: float, sources: Sequence[tuple[float, Sequence[float]]], window: Sequence[float],
            t_obs: float) -> float:
    """P(onset in window | onset observable by t_obs) for one case whose exposure is a mixture.

    `sources` holds (prior weight, exposure window) pairs. Selection on being observed acts on
    the mixture, so this is a ratio of mixtures, not a mixture of per-source ratios.
    """
    hit = observable = 0.0
    for weight, exposure in sources:
        if weight <= 0.0:
            continue
        observable += weight * ki.onset_cdf(k, mu, exposure, t_obs)
        hit += weight * (ki.onset_cdf(k, mu, exposure, window[1]) - ki.onset_cdf(k, mu, exposure, window[0]))
    return max(hit, 0.0) / observable if observable > 0.0 else 0.0


def log_likelihood(k: float, mu: float, widen: float = ONSET_WIDEN_DAYS, p_q: float = P_QUARANTINE) -> float:
    t = ki.T_OBS
    hospital = ((1.0, ki.HOSP_EXPOSURE),)
    first_case = ((1.0, ki.RELATIVES_EXPOSURE),)
    mixed = ((1.0 - p_q, ki.RELATIVES_EXPOSURE), (p_q, CASE2_EXPOSURE))
    terms = [_p_case(k, mu, hospital, _widen(ki.ONSET_17OCT, widen), t)] * 3
    terms.append(_p_case(k, mu, first_case, ki.ONSET_CASE2, t))
    for window in (ki.ONSET_16OCT, ki.ONSET_17OCT):
        terms.append(_p_case(k, mu, mixed, _widen(window, widen), t))
    if min(terms) <= 0.0:
        return -math.inf
    return sum(math.log(v) for v in terms)


def posterior(widen: float = ONSET_WIDEN_DAYS, p_q: float = P_QUARANTINE) -> tuple[tuple[float, float, float], ...]:
    if not 0.0 <= p_q <= 1.0:
        raise ValueError(f"p_q must be a probability, got {p_q}")
    prior = ki.prior_weights()
    logs = {key: log_likelihood(*key, widen=widen, p_q=p_q) + math.log(w) for key, w in prior.items()}
    top = max(logs.values())
    if top == -math.inf:
        raise ValueError("incubation posterior has no mass")
    raw = {key: math.exp(v - top) for key, v in logs.items()}
    total = sum(raw.values())
    return tuple((k, mu, raw[(k, mu)] / total) for k in ki.K_GRID for mu in ki.MU_GRID)


def incubation_summary(post: Sequence[tuple[float, float, float]]) -> dict:
    return {"mean_days_10_50_90": ki._marginal_quantiles(post, 1),
            "predictive_mean_days": ki.predictive(post, lambda k, mu: mu),
            "predictive_p_over_21_days": ki.predictive(post, lambda k, mu: 1.0 - ki.cdf(k, mu, 21.0)),
            "p_onset_after_release_if_still_incubating": {g: ki.release_risk(post, g) for g in ki.RELEASE}}


# ---------------------------------------------------------------------------------------
# Runs and summaries.
# ---------------------------------------------------------------------------------------
QUESTIONS = {"A": ke.CHAIN, "B": ("importation",), "combined": ke.PATHWAYS}


def options(overrides: Mapping[str, Any] | None = None) -> dict:
    """The v2 inputs as options of the first forecast's run, with any row's overrides."""
    o = dict(overrides or {})
    a, b, n, _ = r1_beta(o.pop("reference_class", None))
    lag_grid = tuple(o.pop("lag_grid", r2_lag_grid()))
    if not lag_grid or min(lag_grid) <= 0.0:
        raise ValueError(f"lag grid must hold positive values, got {lag_grid}")
    opts: dict[str, Any] = {
        "incubation_grid": posterior(o.pop("widen", ONSET_WIDEN_DAYS), o.pop("p_q", P_QUARANTINE)),
        "unlisted_lag_grid": lag_grid,
        "unlisted_share_beta": (a, b),
        "unlisted_share_beta_weight": r1_weight(n),
        "import_multiplier": IMPORT_SCALE,
    }
    if o.pop("v1_unlisted_prior", False):
        del opts["unlisted_share_beta"]
    if "incubation" in o:
        opts["incubation_grid"] = ke.incubation_source(o.pop("incubation"))
    opts.update(o)
    return opts


def summarise(draws: Sequence[Mapping[str, Any]]) -> dict:
    curves = {name: [] for name in QUESTIONS}
    for i, date in enumerate(ke.CURVE_DATES):
        for name, which in QUESTIONS.items():
            row = {"date": date.isoformat(), **ke.conditional(draws, i, which, quantiles=True)}
            curves[name].append(row)
    head = {name: curves[name][0] for name in QUESTIONS}
    pathways = {p: ke.conditional(draws, 0, (p,))["p"] for p in ke.PATHWAYS}
    return {"headline": head, "curves": curves, "pathways_alone": pathways}


SENSITIVITY = [
    ("D1_v1_unlisted_prior", "Unlisted share from the first forecast's range, 5 to 25 percent", {"v1_unlisted_prior": True}),
    ("D2_lag_5_days", "Detection lag fixed at 5 days (2014 West Africa)", {"lag_grid": (5.0,)}),
    ("D3_lag_2_days", "Detection lag fixed at 2 days", {"lag_grid": (2.0,)}),
    ("D4_no_quarantine_infection", "Both relatives infected by the first case", {"p_q": 0.0}),
    ("D5_quarantine_infection_likely", "Each relative infected by case 2 with probability 0.9", {"p_q": 0.9}),
    ("D6_no_onset_widening", "Onset windows as inferred, not widened", {"widen": 0.0}),
    ("D7_programme_prior", "Incubation at the programme prior, mean 6.67 days", {"incubation": "programme_prior"}),
    ("D8_largest_episode_left_out", "Reference class without its largest episode", {"leave_out_largest": True}),
    ("D9_lagos_four_outside", "Added at review: Lagos with 4 rather than 2 cases outside follow-up (2 is a lower bound)", {"lagos_outside": 4}),
    ("D10_lag_secondary_sources", "Added at review: lag range from verified and secondary-source urban delays, 0.5 to 8 days", {"lag_grid": tuple(0.5 * i for i in range(1, 17))}),
]


def sensitivity_rows(n: int = N_DRAWS) -> list[dict]:
    rows = []
    for name, label, over in SENSITIVITY:
        over = dict(over)
        if over.pop("leave_out_largest", False):
            largest = max(REFERENCE_CLASS, key=lambda r: r[1])
            over["reference_class"] = tuple(r for r in REFERENCE_CLASS if r is not largest)
        if "lagos_outside" in over:
            lagos = over.pop("lagos_outside")
            over["reference_class"] = tuple((r[0], r[1], lagos, r[3]) if r[0].startswith("Nigeria") else r
                                            for r in REFERENCE_CLASS)
        draws = ke.run(options(over), n=n)
        rows.append({"variant": name, "label": label,
                     **{f"p_{q}": ke.conditional(draws, 0, which)["p"] for q, which in QUESTIONS.items()}})
    return rows


# ---------------------------------------------------------------------------------------
# Artifact.
# ---------------------------------------------------------------------------------------
DISCLOSURES = [
    f"The rules that turn evidence into inputs (R1 to R6), the questions and the sensitivity rows were fixed in a plan before the evidence was read: sha256 {PLAN_SHA256}, written 2026-10-18 at 00:14Z.",
    "Prior sight: this forecast was built after the first escape forecast (0.546 on the combined question) and a review of it had been seen, and after an unregistered scratch check of the detection lag. That is why it exists; the plan fixed its rules before its evidence or its own results were seen.",
    "Question A excludes new importations and question B is only new importations; the first forecast's combined question is shown for comparison and equals A or B.",
    "R1's reference class is three recognised importations with secondary cases (22 secondary cases, 3 outside follow-up at onset). It is small and one episode holds most of it; row D8 drops that episode. Two of the three cases outside follow-up were an escaped listed contact and a health worker not yet on active monitoring, so the share covers more than unlisted contacts, and it overlaps the untraced pathway slightly.",
    "R2: only one onset-to-isolation delay for a case outside follow-up in a high-awareness city was verified in a primary source, so the registered fallback of 2 to 5 days applies. Secondary sources (5 and 8 days in Goma in 2019, and several cases found only at death) point longer; row D2 fixes 5 days.",
    "R4: the evidence shows facility quarantine but not whether relatives were housed together, so each of the two relatives confirmed on 16 and 17 October is infected by case 2 with probability 0.3 in the incubation likelihood. This is a modelling reading only; no authority has said where any case was infected. Relatives infected by case 2 are counted with the contacts of cases 2 to 7 rather than re-deriving their follow-up end.",
    "R3: the onset windows of cases 3 to 7 start two days earlier than in the first forecast, because a negative RT-PCR test early in illness can delay confirmation.",
    "The incubation posterior here differs from the published incubation analysis only by R3 and R4; that analysis is unchanged.",
    "The benchmark for question A is a loose upper reference, not a fair test. It counts any case outside follow-up over a whole episode rather than a 4-week window, ignores that none had been seen in Kenya by 17 October, rests on three episodes, and moves from 0.375 (the Dallas health worker counted as under follow-up) to 0.833 (Madrid left out). In the evidence, cases outside follow-up arose when the index case was recognised late or a contact evaded follow-up; Kenya's first case was isolated on arrival.",
    "Choices made in applying R1, disclosed at review: Madrid 2014 (a repatriated patient) is counted as an importation; without it the benchmark is 0.833 and question A barely moves. Kenya 2026 is left out because the model already conditions on its cases; with it, the Beta mean falls from 0.15 to 0.12. Uganda 2026 is left out because whether each secondary case was under follow-up is not published. Spread within a country (Goma 2019, Kampala 2022 and 2025), a zoonotic index (Kigali 2024) and an importation never recognised (Bamako 2014) fall outside the rule.",
    "Lagos 2014 had at least 2 cases outside follow-up; the sources do not give the exact number, so the share is biased low. Row D9, added at review, uses 4 and raises question A substantially.",
    "R2's word verified was read as verified in a primary source. Row D10, added at review, also counts secondary-source delays (0.5 to 8 days). The evidence suggests unlisted cases in high-awareness cities were isolated 5 to 8 days after onset, longer than the fallback.",
    "Not calibrated against past outcomes. The 80 percent intervals cover parameter uncertainty inside the model only. Nothing here identifies a person or a facility, and a YES on either question is not an outbreak in Kenya.",
]


def build_artifact(main_run: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]], v1_combined: float) -> dict:
    post = posterior()
    inc99 = ke.incubation_quantile(post, 0.99)
    a, b, n, x = r1_beta()
    head = main_run["headline"]
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "kenya-bdbv-2026-10-17-escape-first-principles",
        "model_version": MODEL_VERSION,
        "questions": {
            "A": ("Will Kenya report at least one laboratory-confirmed BDBV case, confirmed 2026-10-18 to 2026-11-14 and "
                  "reported by 2026-11-17, that was not under follow-up as a known contact when its symptoms began and "
                  "is not a new importation (as assigned under the resolution rule)?"),
            "B": ("Will Kenya report at least one laboratory-confirmed new importation of BDBV, confirmed 2026-10-18 to "
                  "2026-11-14 and reported by 2026-11-17, that was not under follow-up as a known contact when its "
                  "symptoms began?"),
            "combined": "A or B: the first escape forecast's question, shown for comparison.",
        },
        "scored": "A",
        "evidence_through_utc": ke.EVIDENCE_THROUGH_UTC,
        "conditioning_date": ke.CONDITIONING_DATE.isoformat(),
        "headline": {q: {"probability": h["p"], "interval_80": [h["p10"], h["p90"]]} for q, h in head.items()},
        "triangulation": {
            "model_A": head["A"]["p"],
            "reference_class_benchmark_A": r6_benchmark(),
            "first_forecast_combined": v1_combined,
            "this_forecast_combined": head["combined"]["p"],
        },
        "curves": main_run["curves"],
        "curve_note": ("Each row: probability given no qualifying report by the end of that date (East Africa Time); "
                       "the 2026-10-17 row's window opens with cases confirmed on 2026-10-18."),
        "pathways_alone": main_run["pathways_alone"],
        "sensitivity": list(sensitivity),
        "inputs": {
            "R1_unlisted_share_beta": {"a": a, "b": b, "secondary_cases": n, "outside_follow_up": x,
                                       "weight": r1_weight(n), "episodes": [r[0] for r in REFERENCE_CLASS],
                                       "episode_sources": {r[0]: list(r[3]) for r in REFERENCE_CLASS},
                                       "recognised_without_secondary_cases": list(REFERENCE_CLASS_NO_SECONDARY)},
            "R2_detection_lag_grid_days": list(r2_lag_grid()),
            "R2_verified_delays": [{"case": c, "days": d, "sources": list(src)} for c, d, src in URBAN_DELAYS],
            "R3_onset_widening_days": ONSET_WIDEN_DAYS,
            "R4_p_infected_by_case_2": P_QUARANTINE, "R4_basis": P_QUARANTINE_BASIS,
            "R5_importation_scale": IMPORT_SCALE, "R5_basis": IMPORT_SCALE_BASIS,
            "attack_rate_context": ("No household member without direct physical contact was infected in Kikwit in 1995 "
                                    "(0 of 78; dowell1999), which is why contacts with no physical contact carry little risk."),
            "incubation": incubation_summary(post),
            "other_inputs": "as in data/case-conditioned/kenya-2026-10-17-escape-forecast.json",
            "citations": EVIDENCE,
        },
        "exposure_gaps": ke.exposure_gaps(inc99),
        "resolution": ("as for the first escape forecast, including its clarifications. A qualifying case counts for B "
                       "when an authority reports a stay, in the 21 days before onset, in an area with a confirmed BDBV "
                       "case in the 42 days before that onset; it counts for A when an authority links it to a Kenyan "
                       "case or describes local transmission. Any other qualifying case is assigned at the coverage "
                       "review by the reviewer who is not the forecast's author"),
        "disclosures": DISCLOSURES,
        "method": {"seed": SEED, "n_draws": N_DRAWS, "code": "lovs/forecast/kenya_escape_fp.py",
                   "reuses": "lovs/forecast/kenya_escape.py"},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = kc.payload_digest(payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    main_run = summarise(ke.run(options()))
    v1 = json.loads(ke.DEFAULT_OUT.read_text(encoding="utf-8"))["headline"]["probability"]
    artifact = build_artifact(main_run, sensitivity_rows(), v1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out.name, "headline": artifact["headline"],
                      "payload_sha256": artifact["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
