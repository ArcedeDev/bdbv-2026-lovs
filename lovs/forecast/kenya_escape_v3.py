# SPDX-License-Identifier: Apache-2.0
"""Kenya escape forecast v3: the Kenya cluster as a natural experiment, triangulated.

A third reading of the 17 October escape question, built beside the first
(`lovs/forecast/kenya_escape.py`) and the second (`lovs/forecast/kenya_escape_fp.py`) and
reusing both. The second forecast's question A, a case outside follow-up that is not a new
importation, was sensitive to two inputs, and this version changes only those two, by rules
fixed in a plan before the new evidence was read (see DISCLOSURES):

- The first case's transmission to people never listed is estimated two independent ways, and
  each draw uses one of them with equal probability.
  - V1, reference class: one episode from recognised filovirus importations is picked at random
    per draw, and the share outside follow-up is drawn from its own Beta. Counts known only as
    a range are drawn within it. No single episode dominates.
  - V2, mechanism: Kenya's listed contacts are a natural experiment. Exposure dates are known,
    every contact was checked daily, and groups with and without close contact have observed
    outcomes. Unlisted infections are the number of unlisted exposed people times the attack
    rate of their contact level, with each group's still-incubating share discounted.
- V4: the onset-to-report lag of a case outside follow-up is bracketed between Kenya's own floor
  (onset to confirmation under daily checks, plus a day to seek care) and a ceiling from this
  epidemic, or the 2014 West Africa mean when this epidemic has published none (as now).

Incubation, importation and the questions are the second forecast's. Stdlib only and
deterministic.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import random
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import kenya_escape as ke
from lovs.forecast import kenya_escape_fp as fp
from lovs.forecast import kenya_incubation as ki

REPO = ke.REPO
DEFAULT_OUT = REPO / "data" / "case-conditioned" / "kenya-2026-10-18-escape-forecast-v3.json"
MODEL_VERSION = "kenya_escape_v3-v1.0.0"
PLAN_SHA256 = "7dba114db2d27baf9ded47de77efe8eaf1a2ada7af4f8a412b57efefbfef0fd7"
PLAN_REGISTERED_UTC = "2026-10-18T01:29:53Z"
SEED = ke.SEED
N_DRAWS = ke.N_DRAWS
QUESTIONS = fp.QUESTIONS

# ---------------------------------------------------------------------------------------
# Evidence-derived inputs (rules V1 to V7 of the plan).
# ---------------------------------------------------------------------------------------
# V1: recognised filovirus importations with at least one secondary case. Each row: (episode,
# secondary cases n, fewest and most of them outside follow-up at onset, source keys).
EPISODES: tuple[tuple[str, int, int, int, tuple[str, ...]], ...] = (
    ("Nigeria, Lagos and Port Harcourt, 2014 (EBOV)", 19, 3, 19, ("shuaib2014", "fasina2014", "musa2015", "who_nigeria2014")),
    ("United States, Dallas, 2014 (EBOV)", 2, 1, 1, ("chevalier2014",)),
    ("Spain, Madrid, 2014 (EBOV, repatriated patient)", 1, 0, 0, ("lopaz2015",)),
)
EPISODES_BASIS: tuple[str, ...] = (
    ("Nigeria: of 19 secondary cases, one is documented as outside follow-up at onset (a health worker in Port Harcourt "
     "who was never listed) and two are inferred from onset dates (Port Harcourt cases that fell ill on or before that "
     "city's first laboratory confirmation): 3 at least. The sources suggest at most 5, but the status of 14 cases is "
     "not published, so under the rule the upper end is all 19."),
    ("Uganda 2026 (BDBV, 5 secondary cases) does not enter: no source states whether any secondary case was under "
     "follow-up when symptoms began, and the rule admits it only with case-level status or a stated bound."),
)
UGANDA_2026 = ("Uganda, Kampala, 2026 (BDBV)", 5, 0, 3, ("kayiwa2026", "who_don612"))
# V2: Kenya's listed contacts of the first case. Each row: (group, contact level, listed,
# confirmed cases, exposure window in model days). Generic labels only.
KENYA_GROUPS: tuple[tuple[str, str, int, int, tuple[float, float]], ...] = (
    ("close-contact group: relatives", "high", 10, 3, ki.RELATIVES_EXPOSURE),
    ("close-contact group: health workers", "high", 21, 3, ki.HOSP_EXPOSURE),
    ("low-contact group: travel contacts", "low", 27, 0, ke.UNLISTED_EXPOSURE),
    ("low-contact group: other listed contacts", "low", 72, 0, ke.UNLISTED_EXPOSURE),
)
KENYA_GROUPS_BASIS = ("No category split of the 130 listed contacts is published after 6 October, when relatives and health "
                      "workers were reported as one combined count, so the plan's fallback applies: relatives as quarantined "
                      "on 7 October (10), 21 health workers, 27 travel contacts, and the remaining 72. The remaining 72 "
                      "include the 2 contacts not traced by 17 October, whose outcome is not observed; counting them as "
                      "observed changes the low-contact attack rate negligibly. Travel contacts take the plan's exposure "
                      "window from arrival to isolation; exposure before arrival would change their observable share from "
                      "about 0.90 to 0.92.")
KENYA_GROUPS_SOURCES = ("kenya_moh2026", "kenya_media2026", "dowell1999")
NO_TOUCH_HOUSEHOLD = 78                # Kikwit 1995: household members without physical contact, none infected
UNLISTED_LOW_FRACTION = (0.0, 1.0)     # f: unlisted low-contact people per listed low-contact person
UNLISTED_HIGH = (0.0, 1.0)             # unknown people with close contact
W_REFERENCE_CLASS = 0.5                # V3: probability that a draw uses V1
# V4: lag bracket.
LAG_LO, LAG_HI = 2.0, 5.0
LAG_BASIS = ("Floor: case 2, confirmed one day after onset under daily checks, the only published delay for a Kenyan case "
             "under follow-up, plus one day to seek care: max(2.0, 1.0 + 1.0) = 2.0. Ceiling: no observed mean or median "
             "onset-to-isolation delay is published for any BDBV 2026 population, so the WHO 2014 mean of 5.0 applies. The "
             "one published Bundibugyo 2026 delay, more than 14 days for Uganda's first, unrecognised imported case, is a "
             "single case rather than a mean; a 2-day mean used in one 2026 model is an assumption, not an observation.")
LAG_SOURCES = ("kenya_moh2026", "who_ert2014", "nsawotebba2026")
EVIDENCE: dict[str, str] = {
    **fp.EVIDENCE,
    "musa2015": "Musa et al. Pan Afr Med J 2015;21:331, doi:10.11604/pamj.2015.21.331.5834",
    "who_nigeria2014": "WHO. Nigeria is now free of Ebola virus transmission. Situation assessment, 20 October 2014",
    "kayiwa2026": "Kayiwa et al. Emerg Infect Dis 2026;32(10), doi:10.3201/eid3210.261411",
    "who_don612": "WHO. Disease Outbreak News 612, Bundibugyo virus disease, 3 July 2026",
    "kenya_moh2026": "Kenya Ministry of Health press statements, 6 and 13 October 2026",
    "kenya_media2026": "Ministry figures for 16 and 17 October 2026 as reported by Al Jazeera, AP and The Star",
    "nsawotebba2026": "Nsawotebba et al. Nat Med 2026;32(8):2786-9, doi:10.1038/s41591-026-04510-7",
}


# ---------------------------------------------------------------------------------------
# V1 and V2 samplers.
# ---------------------------------------------------------------------------------------
def lag_grid(lo: float | None = None, hi: float | None = None) -> tuple[float, ...]:
    lo = LAG_LO if lo is None else lo
    hi = LAG_HI if hi is None else hi
    if lo <= 0.0 or hi < lo:
        raise ValueError(f"lag bracket must be positive and ordered, got {lo}, {hi}")
    return tuple(lo + 0.5 * i for i in range(int(round((hi - lo) / 0.5)) + 1))


def reference_share(rng: random.Random, episodes: Sequence[tuple[str, int, int, int, tuple[str, ...]]],
                    upper: bool = False) -> float:
    """V1: pick an episode with equal probability, a count within its range, then the Jeffreys Beta."""
    _, n, lo, hi, _ = episodes[min(int(rng.random() * len(episodes)), len(episodes) - 1)]
    x = hi if upper else lo + min(int(rng.random() * (hi - lo + 1)), hi - lo)
    return min(ke._beta(rng, x + 0.5, n - x + 0.5), 1.0 - 1e-12)


@functools.lru_cache(maxsize=4096)
def observable(k: float, mu: float, exposure: tuple[float, float]) -> float:
    """Probability that an infection in this exposure window shows symptoms by 17 October."""
    return ki.onset_cdf(k, mu, exposure, ki.T_OBS)


def attack_shapes(k: float, mu: float, level: str, groups: Sequence[tuple] | None = None,
                  no_touch: int = NO_TOUCH_HOUSEHOLD) -> tuple[float, float]:
    """Jeffreys Beta shapes for one contact level, with incubating contacts discounted."""
    rows = [g for g in (KENYA_GROUPS if groups is None else groups) if g[1] == level]
    cases = sum(g[3] for g in rows)
    trials = sum(g[2] * observable(k, mu, g[4]) for g in rows) + (no_touch if level == "low" else 0)
    return cases + 0.5, max(0.5, trials - cases + 0.5)


def mechanistic_lambda(rng: random.Random, k: float, mu: float, cfg: Mapping[str, Any]) -> float:
    """V2: expected infections among people exposed to the first case and never listed."""
    groups = cfg["groups"]
    a_low = ke._beta(rng, *attack_shapes(k, mu, "low", groups, cfg["no_touch"]))
    a_high = ke._beta(rng, *attack_shapes(k, mu, "high", groups))
    listed_low = sum(g[2] for g in groups if g[1] == "low")
    return (kc._uniform(rng, cfg["low_fraction"]) * listed_low * a_low
            + kc._uniform(rng, cfg["high"]) * a_high)


def _make_hook(cfg: Mapping[str, Any], record: list | None = None) -> Callable:
    """The unlisted-intensity hook for the first forecast's run (V3 mixes V1 and V2)."""
    def hook(rng: random.Random, k: float, mu: float, listed: float) -> float:
        if rng.random() < cfg["w_reference"]:
            u = reference_share(rng, cfg["episodes"], cfg["upper"])
            lam, branch = listed * u / (1.0 - u), "V1"
        else:
            lam, branch = mechanistic_lambda(rng, k, mu, cfg), "V2"
        if record is not None:
            record.append((lam, listed, branch))
        return lam
    return hook


OVERRIDE_KEYS = frozenset({"v2_inputs", "episodes", "groups", "no_touch", "low_fraction", "high", "w_reference",
                           "upper", "lag_grid"})


def _check(cfg: Mapping[str, Any]) -> None:
    for name, n, lo, hi, _ in cfg["episodes"]:
        if not 0 <= lo <= hi <= n or n < 1:
            raise ValueError(f"episode {name!r}: need 0 <= x_lo <= x_hi <= n and n >= 1")
    for name, level, listed, cases, window in cfg["groups"]:
        if level not in ("low", "high") or not 0 <= cases <= listed or window[1] < window[0]:
            raise ValueError(f"group {name!r}: bad level, counts or window")
    for key in ("low_fraction", "high"):
        lo, hi = cfg[key]
        if not 0.0 <= lo <= hi:
            raise ValueError(f"{key} must be an ordered non-negative range")
    if not 0.0 <= cfg["w_reference"] <= 1.0 or cfg["no_touch"] < 0:
        raise ValueError("w_reference must be in [0, 1] and no_touch non-negative")


def options(overrides: Mapping[str, Any] | None = None, record: list | None = None) -> dict:
    """The v3 inputs as options of the first forecast's run, with any row's overrides."""
    o = dict(overrides or {})
    unknown = set(o) - OVERRIDE_KEYS
    if unknown:
        raise ValueError(f"unknown option keys: {sorted(unknown)}")
    if o.pop("v2_inputs", False):
        return fp.options()
    cfg = {"episodes": tuple(o.pop("episodes", EPISODES)), "groups": tuple(o.pop("groups", KENYA_GROUPS)),
           "no_touch": o.pop("no_touch", NO_TOUCH_HOUSEHOLD), "low_fraction": o.pop("low_fraction", UNLISTED_LOW_FRACTION),
           "high": o.pop("high", UNLISTED_HIGH), "w_reference": o.pop("w_reference", W_REFERENCE_CLASS),
           "upper": o.pop("upper", False)}
    if not cfg["episodes"]:
        raise ValueError("reference class is empty")
    _check(cfg)
    grid = tuple(o.pop("lag_grid", lag_grid()))
    opts = fp.options({"lag_grid": grid})
    opts.pop("unlisted_share_beta", None)
    opts["unlisted_lambda"] = _make_hook(cfg, record)
    return opts


def _interval(values: Sequence[float], lo: float = 0.1, hi: float = 0.9) -> list[float]:
    ordered = sorted(values)
    pick = lambda q: ordered[min(int(q * len(ordered)), len(ordered) - 1)]
    return [pick(lo), pick(0.5), pick(hi)]


def reference_weight_after_silence(draws: Sequence[Mapping[str, Any]], record: Sequence[tuple]) -> float:
    """Share of question A's conditioning weight carried by draws that used V1."""
    totals = [ke._total(d, 0, QUESTIONS["A"]) for d in draws]
    floor = min(totals)
    weights = [math.exp(-(t - floor)) for t in totals]
    return sum(w for w, r in zip(weights, record) if r[2] == "V1") / sum(weights)


def triangulation(n: int = N_DRAWS) -> dict:
    """V1 alone against V2 alone: unlisted infections and implied share, before conditioning."""
    out = {}
    for name, w in (("V1_reference_class", 1.0), ("V2_kenya_mechanism", 0.0)):
        rec: list = []
        ke.run(options({"w_reference": w}, rec), n=n)
        out[name] = {"unlisted_infections_10_50_90": _interval([lam for lam, _, _ in rec]),
                     "implied_share_10_50_90": _interval([lam / (lam + listed) for lam, listed, _ in rec])}
    a, b = out["V1_reference_class"]["unlisted_infections_10_50_90"], out["V2_kenya_mechanism"]["unlisted_infections_10_50_90"]
    out["intervals_overlap"] = a[0] <= b[2] and b[0] <= a[2]
    return out


SENSITIVITY = [
    ("E1_reference_class_only", "Unlisted transmission from the reference class alone (V1)", {"w_reference": 1.0}),
    ("E2_kenya_mechanism_only", "Unlisted transmission from the Kenya mechanism alone (V2)", {"w_reference": 0.0}),
    ("E3_more_unlisted_bystanders", "Up to three unlisted low-contact people per listed one", {"low_fraction": (0.0, 3.0)}),
    ("E4_no_unknown_close_contact", "No unknown person with close contact", {"high": (0.0, 0.0)}),
    ("E5_lag_floor", "Detection lag fixed at the bracket's floor", {"lag_grid": "lo"}),
    ("E6_lag_ceiling", "Detection lag fixed at the bracket's ceiling", {"lag_grid": "hi"}),
    ("E7_second_forecast_inputs", "The second forecast's inputs, reproducing its question A", {"v2_inputs": True}),
    ("E8_no_kikwit_term", "Low-contact attack rate from Kenya's groups alone", {"no_touch": 0}),
    ("E9_range_counts_high", "Reference-class counts known as a range taken at their upper ends", {"upper": True}),
    ("E10_without_uganda_2026", "Reference class without Uganda 2026", {"drop_uganda": True}),
    ("E11_with_uganda_2026", "Added after the evidence was read: Uganda 2026 entered with 0 to 3 of 5 outside follow-up", {"episodes": EPISODES + (UGANDA_2026,)}),
    ("E12_nigeria_strictest", "Added after the evidence was read: Nigeria at its strictest documented range, 1 to 19", {"episodes": (("Nigeria, strictest", 19, 1, 19, ()),) + EPISODES[1:]}),
    ("E13_nigeria_suggested", "Added at review: Nigeria at the range the sources suggest, 3 to 5", {"episodes": (("Nigeria, suggested", 19, 3, 5, ()),) + EPISODES[1:]}),
]


def sensitivity_rows(n: int = N_DRAWS) -> list[dict]:
    rows = []
    for name, label, over in SENSITIVITY:
        over = dict(over)
        if over.get("lag_grid") in ("lo", "hi"):
            over["lag_grid"] = (LAG_LO,) if over["lag_grid"] == "lo" else (LAG_HI,)
        if over.pop("drop_uganda", False):
            kept = tuple(e for e in EPISODES if not e[0].startswith("Uganda"))
            if len(kept) == len(EPISODES):
                rows.append({"variant": name, "label": label, "note": "Uganda 2026 did not enter the reference class"})
                continue
            over["episodes"] = kept
        draws = ke.run(options(over), n=n)
        rows.append({"variant": name, "label": label,
                     **{f"p_{q}": ke.conditional(draws, 0, which)["p"] for q, which in QUESTIONS.items()}})
    return rows


JUDGEMENT_ROWS = ("E1_reference_class_only", "E2_kenya_mechanism_only", "E3_more_unlisted_bystanders",
                  "E4_no_unknown_close_contact", "E5_lag_floor", "E6_lag_ceiling")


def judgement_range(sensitivity: Sequence[Mapping[str, Any]]) -> dict:
    """Question A across the rows that move the judgement inputs (weight, f, close contacts, lag)."""
    vals = {r["variant"]: r["p_A"] for r in sensitivity if r["variant"] in JUDGEMENT_ROWS}
    return {"low": min(vals.values()), "high": max(vals.values()), "rows": sorted(vals),
            "note": ("The 80 percent interval covers parameter uncertainty inside the model. This range shows how far "
                     "the headline moves when one judgement input is set to an alternative.")}


def benchmark() -> dict:
    """V7: Jeffreys mean of episodes with a case outside follow-up, over V1's episodes."""
    with_escape = sum(1 for e in EPISODES if e[2] > 0)
    return {"episodes_with_secondary_cases": len(EPISODES), "episodes_with_a_case_outside_follow_up": with_escape,
            "probability": (with_escape + 0.5) / (len(EPISODES) + 1.0),
            "note": ("Unconditional and over whole episodes: it ignores that no case outside follow-up had been "
                     "reported in Kenya by 17 October, so it is a loose upper reference, not a fair test.")}


DISCLOSURES = [
    f"The rules that turn evidence into inputs (V1 to V7), the questions and rows E1 to E10 were fixed in a plan before the new evidence was read: sha256 {PLAN_SHA256}, registered {PLAN_REGISTERED_UTC}. Rows E11 and E12 were added after the evidence was read, and row E13 at review; each is labelled so.",
    "Prior sight: this forecast was built after the first (0.546 on the combined question) and second (0.323 on question A) forecasts and their reviews, and after seeing that every Uganda 2026 case was reported as linked to a known case. Linked is not the same as under follow-up at onset, which no source states for Uganda.",
    "Question A excludes new importations and question B is only new importations; the combined question equals A or B and is the first forecast's question. Incubation, importation and all other inputs are the second forecast's; only the unlisted transmission and the detection lag changed.",
    "Unlisted transmission is estimated two independent ways and each draw uses one with probability 0.5. The triangulation block gives each alone, before the silence to 17 October is applied: the reference class (V1) puts the median share of transmission outside follow-up more than ten times higher than the Kenya mechanism (V2). Their 80 percent intervals overlap only because the reference class is wide. This is the main tension in the evidence.",
    "Why the two disagree: in the reference class, cases outside follow-up arose around an index recognised late, as in Nigeria, where a health worker treated a symptomatic contact privately and was never listed, or when protected health workers were not yet monitored (Dallas). Kenya's first case was isolated on arrival; its six secondary cases were all among relatives and health workers, and none among about 99 listed contacts without close contact, a pattern the mechanism carries directly.",
    "V1 weights its three episodes equally, so no episode dominates, but two of them have only one or two secondary cases, which makes the share very uncertain. The silence to 17 October counts against the draws with a high share, so after conditioning the reference class carries less than its prior half of the weight (reference_class_weight_after_silence in the triangulation block), and question A sits below the midpoint of rows E1 and E2. For the same reason question A is not monotone in the reference-class counts: Nigeria at its upper end lowers it by about 0.01 (row E9) and the narrower suggested range raises it by about 0.01 (row E13). Seed-to-seed noise is about 0.002.",
    "V2 rests on two judgements: unlisted people with low contact number at most as many as the listed low-contact contacts (row E3 allows three times as many), and at most one unknown person had close contact (row E4 removes them). Contacts still incubating on 17 October are discounted by the drawn incubation. The low-contact rate also uses Kikwit 1995, where no household member without physical contact was infected (0 of 78); row E8 drops it.",
    "Kenya's contact split after 6 October is not published, so the plan's fallback applies: 10 relatives, 21 health workers, 27 travel contacts and 72 others, all of the first case. The category of the two contacts not traced on 17 October is not published; they stay in the untraced pathway.",
    "Nigeria 2014 enters with 3 to 19 of 19 secondary cases outside follow-up at onset, drawn per draw. One is documented (a health worker never listed) and two are inferred from onset dates on or before that city's first confirmation. The sources suggest at most 5 (row E13), but the status of 14 cases is not published, so the registered rule sets the upper end at 19. A first version of this forecast used 3 to 5; review found that this departed from the rule, and the rule's reading is the headline. The strictest documented range, 1 to 19, is row E12.",
    "Uganda 2026, the same virus in this epidemic, does not enter the reference class: no source read states whether any of its 5 secondary cases was under follow-up when symptoms began. Row E11 enters it at 0 to 3 of 5, the range the sources make most plausible.",
    "The detection lag is drawn from 2 to 5 days. The floor is Kenya's own: case 2 was confirmed a day after onset under daily checks, plus a day to seek care for someone not checked. No observed onset-to-isolation delay is published for any Bundibugyo 2026 population, so the ceiling falls back to the 2014 West Africa mean of 5.0 days. Rows E5 and E6 fix each end.",
    "Judgement inputs move the headline more than its interval suggests: question A runs across the range in A_across_judgement_rows when the V1 weight, the unlisted bystander count, unknown close contacts or the lag is set to an alternative.",
    "The values for single pathways are each conditioned on that pathway's silence only, so they are not additive; the second-generation pathway alone exceeds question A itself, because its own silence rules out few of the draws that the joint silence rejects.",
    "The benchmark for question A is a loose upper reference, not a fair test: it counts whole episodes, ignores that no case outside follow-up had been seen in Kenya by 17 October, and rests on three episodes.",
    "Not calibrated against past outcomes. The 80 percent intervals cover parameter uncertainty inside the model only. Nothing here identifies a person or a facility, and a YES on either question is not an outbreak in Kenya.",
]


def build_artifact(main_run: Mapping[str, Any], tri: Mapping[str, Any], sensitivity: Sequence[Mapping[str, Any]],
                   earlier: Mapping[str, float], v1_weight: float) -> dict:
    head = main_run["headline"]
    v2q = json.loads(fp.DEFAULT_OUT.read_text(encoding="utf-8"))["questions"]
    payload = {
        "schema": "lovs-case-conditioned-forecast/v1",
        "forecast_id": "kenya-bdbv-2026-10-18-escape-v3",
        "model_version": MODEL_VERSION,
        "questions": v2q,
        "scored": "A",
        "evidence_through_utc": PLAN_REGISTERED_UTC,
        "conditioning_date": ke.CONDITIONING_DATE.isoformat(),
        "headline": {q: {"probability": h["p"], "interval_80": [h["p10"], h["p90"]]} for q, h in head.items()},
        "triangulation": {
            "unlisted_transmission": {**tri, "reference_class_weight_after_silence": v1_weight},
            "reference_class_benchmark_A": benchmark(),
            "A_across_judgement_rows": judgement_range(sensitivity),
            "first_forecast_combined": earlier["v1_combined"],
            "second_forecast_A": earlier["v2_A"],
            "this_forecast_A": head["A"]["p"],
            "this_forecast_combined": head["combined"]["p"],
        },
        "curves": main_run["curves"],
        "curve_note": ("Each row: probability given no qualifying report by the end of that date (East Africa Time); "
                       "the 2026-10-17 row's window opens with cases confirmed on 2026-10-18."),
        "pathways_alone": main_run["pathways_alone"],
        "sensitivity": list(sensitivity),
        "inputs": {
            "V1_reference_class": {"episodes": [{"episode": e[0], "secondary_cases": e[1], "outside_follow_up_range": [e[2], e[3]],
                                                 "sources": list(e[4])} for e in EPISODES],
                                   "basis": list(EPISODES_BASIS)},
            "V2_kenya_groups": {"groups": [{"group": g[0], "contact_level": g[1], "listed": g[2], "cases": g[3]} for g in KENYA_GROUPS],
                                "basis": KENYA_GROUPS_BASIS, "sources": list(KENYA_GROUPS_SOURCES), "no_touch_household_term": NO_TOUCH_HOUSEHOLD,
                                "unlisted_low_fraction": list(UNLISTED_LOW_FRACTION), "unknown_close_contacts": list(UNLISTED_HIGH)},
            "V3_weight_on_reference_class": W_REFERENCE_CLASS,
            "V4_detection_lag_grid_days": list(lag_grid()), "V4_basis": LAG_BASIS, "V4_sources": list(LAG_SOURCES),
            "V5_unchanged": "incubation (rules R3 and R4), importation (R5) and other inputs as in the second forecast",
            "incubation": fp.incubation_summary(fp.posterior()),
            "citations": EVIDENCE,
        },
        "resolution": json.loads(fp.DEFAULT_OUT.read_text(encoding="utf-8"))["resolution"],
        "disclosures": DISCLOSURES,
        "method": {"seed": SEED, "n_draws": N_DRAWS, "code": "lovs/forecast/kenya_escape_v3.py",
                   "reuses": ["lovs/forecast/kenya_escape.py", "lovs/forecast/kenya_escape_fp.py"]},
    }
    payload = kc._rounded(payload)
    payload["payload_sha256"] = kc.payload_digest(payload)
    return payload


def _earlier_results() -> dict:
    v1 = json.loads(ke.DEFAULT_OUT.read_text(encoding="utf-8"))
    v2 = json.loads(fp.DEFAULT_OUT.read_text(encoding="utf-8"))
    return {"v1_combined": v1["headline"]["probability"], "v2_A": v2["headline"]["A"]["probability"]}


def build() -> dict:
    record: list = []
    draws = ke.run(options({}, record))
    return build_artifact(fp.summarise(draws), triangulation(), sensitivity_rows(), _earlier_results(),
                          reference_weight_after_silence(draws, record))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    artifact = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out.name, "headline": artifact["headline"],
                      "payload_sha256": artifact["payload_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
