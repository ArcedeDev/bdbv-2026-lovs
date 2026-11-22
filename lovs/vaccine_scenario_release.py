"""Per-release vaccine field-effectiveness scenario artifact (review tier, scenario lane).

Builds one JSON artifact per INSP SitRep from ``lovs.vaccine_field_effectiveness``.
The artifact answers, for each province where it can be computed: how much
protection would follow for each assumed strain match S (the share of Zaire
protection that carries over to Bundibugyo virus, unidentified), and what
share of infections that protection averts directly. The uniform-prior
summary is set by the ignorance prior on S, not by data.

Three kinds of input, kept apart:

  * method inputs (this module): the Zaire trial anchor, onset, durability,
    potency and case-share assumptions. They change only with a new method
    version, never with a release.
  * ring timing (``data/vaccine-field-effectiveness/ring-timing.json``): a
    declared calibration of ring-vaccination timing on Zaire data, carried
    to BDBV as a bounded scenario. Release-independent.
  * release inputs (``data/vaccine-field-effectiveness/inputs/sr<N>.json``):
    values as INSP prints them, each with its SitRep locator. When the
    reviewed SitRep promotion for that release carries a per-province
    vaccination record, its rows are used and must agree with the inputs
    file on every field both hold.

Output: ``data/vaccine-field-effectiveness/scenario-sr<N>.json``, in the
read-only model-artifact shape (id, label, artifactType, subject, dataDate,
evidenceClass, values, methodBasis, citations, limitations).

Never computed: a national total, a measured BDBV effectiveness, or a share
of all cases where the vaccinated group's make-up is not printed.

This is situational evidence, not clinical or vaccine advice.

Usage:
    python3 -m lovs.vaccine_scenario_release --sitrep 189          # write
    python3 -m lovs.vaccine_scenario_release --sitrep 189 --check  # verify committed file
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import random
import sys
from typing import Mapping

from lovs import vaccine_field_effectiveness as vfe
from lovs.vaccine_field_effectiveness import Group, ModelInputs, Param

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "vaccine-field-effectiveness"
PROMOTION_DIR = REPO_ROOT / "data" / "sitrep_promotions"

ARTIFACT_TYPE = "vaccine_field_effectiveness_scenario"
RELEASE_VERSION = "lovs_vaccine_scenario_release-v0.1.0"
STRAIN_MATCH_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0)
DRAWS = 20000
CONDITIONAL_DRAWS = 8000
SEED = 20261121

# ---------------------------------------------------------------------------
# Method inputs (release-independent)
# ---------------------------------------------------------------------------

TRIAL_SOURCE = (
    "Henao-Restrepo AM et al. Lancet 2017;389:505-18 (10.1016/S0140-6736(16)32621-6), Table 3 "
    "primary analysis: 0/2108 vaccinated in immediate rings vs 16/3075 eligible in delayed rings, "
    "onset >= 10 d after randomisation; VE 100% (95% CI 68.9-100). Zaire ebolavirus only. "
    "Posterior uses Jeffreys Beta on the conditional binomial split, without the trial's cluster adjustment.")
TRIAL_COUNTS = (0, 16, 2108 / 3075)
BDBV_INCUBATION = (3.0 / 0.406, 3.0 ** 0.5 / 0.406)


def method_inputs(**over: Param) -> ModelInputs:
    """The release-independent inputs, each with its basis and source."""
    kw = dict(
        ve_zaire=Param("ve_zaire", "ve_posterior", TRIAL_COUNTS, "source", TRIAL_SOURCE),
        strain_match=Param("strain_match", "uniform", (0.0, 1.0), "assumption",
                           "ASSUMPTION: no human or field estimate of Ervebo protection against BDBV. "
                           "Macaque lethal-challenge studies are small and mixed (from their abstracts): one "
                           "rVSV-ZEBOV dose gave 75% survival after BDBV challenge against 25% in controls "
                           "(Falzarano 2011, 10.1093/infdis/jir350); one dose of a blended rVSV-SUDV/ZEBOV "
                           "vaccine left 1 of 3 surviving, as in controls, while a heterologous prime-boost "
                           "protected all (Mire 2013, 10.1371/journal.pntd.0002600). Survival after high-dose "
                           "challenge is not protection against infection, so S keeps a uniform ignorance "
                           "prior and results are also reported conditional on fixed values"),
        potency=Param("potency", "uniform", (0.80, 1.00), "assumption",
                      "ASSUMPTION: share of given doses still potent; SitReps record stock-outs, generator "
                      "failures, idle cold-chain units, thawed and expired doses (SR114-SR186) but never "
                      "measure potency of doses given"),
        durability=Param("durability", "uniform", (0.95, 1.0), "assumption",
                         "ASSUMPTION: protection kept over a 90-day horizon; 79.5% antibody responders at "
                         "12 months (PREVAIL I, Kennedy 2017, 10.1056/NEJMoa1614067), binding antibody flat "
                         "over years 1-5 (Huttner 2023, 10.1016/j.cmi.2023.08.026), 76.8% of DRC health "
                         "workers seropositive after more than 3 years (Kahusu 2025, "
                         "10.1016/j.vaccine.2025.127537); no correlate of protection, so waning cannot be "
                         "quantified"),
        onset_days=Param("onset_days", "uniform", (3.0, 10.0), "assumption",
                         "ASSUMPTION: days to protection 3-10. Upper end set by the trial's 0 vaccinee "
                         "cases with onset >= 10 d (Henao-Restrepo 2017). Lower end: 2.5% responders at "
                         "week 1 (PREVAIL I, Kennedy 2017), 19/20 seroconverted by day 14 (Regules 2017, "
                         "10.1056/NEJMoa1414216), macaques 2/3 protected when vaccinated 3 d before "
                         "challenge (Marzi 2015, 10.1126/science.aab3920)"),
        horizon_days=Param("horizon_days", "fixed", (90.0,), "assumption",
                           "ASSUMPTION: protection is counted over the 90 days after vaccination, with exposure "
                           "spread evenly over that window; this sets the pre-exposure timing share"),
        delay_to_vaccination=Param("delay_to_vaccination", "gamma_mean_sd", (7.6, 5.5), "derived",
                                   "gamma fitted to DRC 2018-19 index onset to ring vaccination, median 7 d "
                                   "IQR 3-10 (WHO/INRB 2019); used only by the uncalibrated ring mode"),
        tau_index=Param("tau_index", "gamma_mean_sd", (6.0, 4.0), "assumption",
                        "ASSUMPTION: index onset to transmission, mean 6 d (sd 4); bounded by BDBV onset-to-"
                        "death mean 11.4 d, Rosello 2015 eLife 10.7554/eLife.09015 Table 5 (Isiro 2012)"),
        incubation=Param("incubation", "gamma_mean_sd", BDBV_INCUBATION, "source",
                         "LOVS BDBV incubation prior gamma(shape 3.0, rate 0.406), "
                         "lovs/lovs_priors_bundibugyo.py v0.2.0 (Wamala 2010, MacNeil 2010, Kratz 2015)"),
        tau_next=Param("tau_next", "gamma_mean_sd", (6.0, 4.0), "assumption",
                       "ASSUMPTION: as tau_index, for a secondary case inside the ring"),
        index_share=Param("index_share", "uniform", (0.4, 0.9), "assumption",
                          "ASSUMPTION: share of a contact's risk from the index rather than later ring cases"),
        pep_effect=Param("pep_effect", "fixed", (0.0,), "assumption",
                         "ASSUMPTION: no protection against infection acquired before immune onset, for "
                         "pre-exposure rows. Ring and mixed-timing rows differ: their ring component "
                         "(ring_protected_share) carries post-exposure credit from the calibration prior, "
                         "0 to 0.6; the share without that credit is reported alongside"),
        indirect_multiplier=Param("indirect_multiplier", "uniform", (1.0, 2.0), "assumption",
                                  "ASSUMPTION (scenario only, never in the share of all cases averted): chain-breaking multiplier"),
    )
    kw.update(over)
    return ModelInputs(**kw)


CASE_SHARES = {
    "frontline_workers": Param(
        "case_share", "uniform", (0.02, 0.10), "assumption",
        "ASSUMPTION: frontline workers' share of future cases; DRC 2018-2020 179/3,481 = 5.1% "
        "(Baller 2022, 10.1016/j.ijid.2021.11.039); not printed by INSP in 2026"),
    "contacts": Param(
        "case_share", "uniform", (0.2, 0.6), "assumption",
        "ASSUMPTION: share of future cases arising among listed contacts; not printed"),
    "unassigned": Param(
        "case_share", "fixed", (0.0,), "derived",
        "not assigned: the list's make-up is not printed, so no share of cases is given and no share of "
        "all cases averted is computed"),
}
LIST_COMPLETENESS = Param(
    "denominator_completeness", "uniform", (0.7, 1.0), "assumption",
    "ASSUMPTION: share of the truly exposed group on the target list")
LISTED_CONTACTS_COMPLETE = Param(
    "denominator_completeness", "fixed", (1.0,), "derived", "coverage defined on listed contacts")

# ---------------------------------------------------------------------------
# Ring timing (release-independent; declared calibration)
# ---------------------------------------------------------------------------

RING_REBUILD = {"n_samples": 200, "draws": 4000, "seed": 29, "delay_factor": (1.0, 2.0)}


def ring_protected_shares(rows: list[Mapping], *, with_pep: bool, n_samples: int, draws: int,
                          seed: int, delay_factor: tuple[float, float]) -> list[float]:
    """Carry the Zaire-calibrated ring structure to BDBV.

    Resamples accepted calibration rows by their trial-likelihood weight, keeps
    each row's index share, immune onset, post-exposure effect and VE after
    onset, swaps in the LOVS BDBV incubation, and stretches the DRC 2018-19
    delay by a factor drawn from ``delay_factor`` (ASSUMPTION: 2026 rings are
    no faster than 2018-19). Returns the protected share of a ring's
    infections; with ``with_pep`` it adds post-exposure credit for vaccinees
    infected before protection only,
    after + (vaccinated - after) * min(1, pep / ve_post),
    where vaccinated is the share of infected members still without symptoms
    at vaccination. Members already ill at vaccination are never vaccinated
    and get no credit.
    """
    out = []
    for a, v, pep, ve_post in _ring_profiles(rows, n_samples=n_samples, draws=draws, seed=seed,
                                             delay_factor=delay_factor):
        out.append(a + (v - a) * min(1.0, pep / ve_post) if with_pep else a)
    return out


def ring_vaccinated_fractions(rows: list[Mapping], *, n_samples: int, draws: int, seed: int,
                              delay_factor: tuple[float, float]) -> list[float]:
    """Share of infected ring members still without symptoms at vaccination, so vaccinated."""
    return [v for _, v, _, _ in _ring_profiles(rows, n_samples=n_samples, draws=draws, seed=seed,
                                               delay_factor=delay_factor)]


def _ring_profiles(rows: list[Mapping], *, n_samples: int, draws: int, seed: int,
                   delay_factor: tuple[float, float]) -> list[tuple[float, float, float, float]]:
    rng = random.Random(seed)
    picked = rng.choices(rows, weights=[r["weight"] for r in rows], k=n_samples)
    inc = Param("inc", "gamma_mean_sd", BDBV_INCUBATION, "source", "LOVS BDBV incubation prior")
    tau = Param("tau", "gamma_mean_sd", (3.9, 3.0), "assumption",
                "ASSUMPTION: Zaire onset-to-transmission carried")
    out = []
    for i, row in enumerate(picked):
        f = rng.uniform(*delay_factor)
        delay = Param("d", "gamma_mean_sd", (7.6 * f, 5.5 * f), "assumption",
                      "ASSUMPTION: stretched DRC delay")
        prof = vfe.ring_cohort_profile(
            row["ve_post"], delay, Param("o", "fixed", (row["onset_days"],), "derived", "calibration"),
            tau, inc, Param("s", "fixed", (row["index_share"],), "derived", "calibration"),
            draws=draws, seed=seed + i, pep_effect=row["pep_effect"])
        out.append((prof["after_protection"], prof["vaccinee_fraction_of_infected"],
                    row["pep_effect"], row["ve_post"]))
    return out


def load_ring_timing(path: pathlib.Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "ring-timing.json").read_text())


def ring_timing_param(ring: Mapping) -> Param:
    return Param("ring_protected_share", "empirical", tuple(ring["protected_share_with_pep"]), "assumption",
                 "ASSUMPTION: ring-vaccination timing from a declared Zaire calibration (trial-likelihood "
                 "weighted), carried to BDBV with three further assumptions: the DRC 2018-19 index-onset-to-ring-"
                 "vaccination delay (median 7 d, IQR 3-10; WHO/INRB preliminary results, April 2019) stretched "
                 "1-2x, the Zaire onset-to-transmission gamma(mean 3.9 d, sd 3.0; serial interval 15.3 d minus "
                 "incubation 11.4 d, WHO Ebola Response Team 2014, 10.1056/NEJMoa1411100) kept, and post-exposure "
                 "credit from the calibration prior (0 to 0.6, with index share 0 to 0.9 and immune onset "
                 "2 to 14 d only partly constrained by the fit). A bounded scenario: the calibration does not "
                 "reproduce the trial's 0 late vaccinee cases well")

# ---------------------------------------------------------------------------
# Release inputs
# ---------------------------------------------------------------------------


class ReleaseInputError(ValueError):
    pass


def load_release_inputs(sitrep: int, data_dir: pathlib.Path | None = None) -> dict:
    path = (data_dir or DATA_DIR) / "inputs" / f"sr{sitrep}.json"
    doc = json.loads(path.read_text())
    if doc.get("sitrep_number") != sitrep:
        raise ReleaseInputError(f"{path.name}: sitrep_number {doc.get('sitrep_number')!r} != {sitrep}")
    return doc


def promotion_rows(sitrep: int, promotion_dir: pathlib.Path | None = None) -> list[dict] | None:
    """Per-province vaccination rows from the reviewed promotion.

    None when no promotion file exists for the SitRep; an empty list when the
    promotion exists but carries no per-province vaccination record.
    """
    matches = sorted((promotion_dir or PROMOTION_DIR).glob(f"sitrep-{sitrep:03d}-*.json"))
    if not matches:
        return None
    if len(matches) > 1:
        raise ReleaseInputError(f"more than one promotion file for SitRep {sitrep}: {[m.name for m in matches]}")
    doc = json.loads(matches[0].read_text())
    rows = (doc.get("figures", {}).get("operational_tables", {}) or {}).get("vaccination_by_province")
    return list(rows or [])


RECORD_FIELDS = ("vaccinated_cumulative", "pre_registered", "coverage_pct",
                 "doses_received", "doses_remaining", "doses_used")
# Names the reviewed promotions use for a printed denominator. The first one
# present is read as the denominator; two that disagree stop the build.
DENOMINATOR_FIELDS = ("pre_registered", "vaccination_expected", "target")


def _normalise_row(row: Mapping) -> dict:
    out = dict(row)
    present = [(f, row[f]) for f in DENOMINATOR_FIELDS if row.get(f) is not None]
    if len({v for _, v in present}) > 1:
        raise ReleaseInputError(f"{row.get('province')}: denominators disagree {present}")
    out["pre_registered"] = present[0][1] if present else None
    out["denominator_field"] = present[0][0] if present else None
    return out


def _province_rows(rows: list[Mapping], where: str) -> tuple[dict[str, dict], dict[str, str]]:
    """Province-total rows keyed by province; zone-level rows are set aside with a reason."""
    out: dict[str, dict] = {}
    set_aside: dict[str, str] = {}
    for r in rows:
        prov, geo = r["province"], r.get("geography")
        if geo and geo != prov:
            set_aside[f"{prov} ({geo})"] = "zone-level row, not a province total"
            continue
        if prov in out:
            raise ReleaseInputError(f"{prov}: more than one province row in the {where}")
        out[prov] = _normalise_row(r)
    return out, set_aside


def vaccination_rows(inputs: Mapping, promoted: list[dict] | None) -> tuple[dict[str, dict], str, dict[str, str]]:
    """Province rows to use, and where they came from.

    The reviewed promotion's record wins when it has rows. Every record field
    both hold must agree, and the inputs file may not supply a value the
    reviewed record leaves empty, so the two never drift apart silently. An
    inputs row the record does not hold is allowed only when every record
    field is empty: it supplies no value, only the note that the province
    prints no count, and it is listed as not computed.
    """
    printed, printed_aside = _province_rows(inputs.get("vaccination_rows_as_printed", []), "inputs file")
    n = inputs["sitrep_number"]
    if promoted is None:
        return printed, f"release inputs file (no reviewed promotion file for SitRep {n} yet)", printed_aside
    if not promoted:
        return (printed, f"release inputs file (the reviewed SitRep {n} promotion has no per-province "
                         "vaccination record)", printed_aside)
    rows, set_aside = _province_rows(promoted, "promotion record")
    for prov, mine in printed.items():
        theirs = rows.get(prov)
        if theirs is None:
            if any(mine.get(f) is not None for f in RECORD_FIELDS):
                raise ReleaseInputError(f"{prov}: in the inputs file but not in the promotion record")
            rows[prov] = mine
            continue
        for f in RECORD_FIELDS:
            if mine.get(f) is not None and theirs.get(f) is not None and mine[f] != theirs[f]:
                raise ReleaseInputError(f"{prov}.{f}: inputs file {mine[f]} != promotion record {theirs[f]}")
            if theirs.get(f) is None and mine.get(f) is not None:
                raise ReleaseInputError(f"{prov}.{f}: the inputs file holds {mine[f]} but the reviewed record "
                                        "leaves it empty; correct the record or drop the value")
        for k in ("source", "source_page"):
            if mine.get(k) and not theirs.get(k):
                theirs[k] = mine[k]
    for r in rows.values():
        if not r.get("source"):
            pages = r.get("source_pages") or ([r["source_page"]] if r.get("source_page") else [])
            where = f", page{'s' if len(pages) > 1 else ''} {', '.join(map(str, pages))}" if pages else ""
            r["source"] = f"INSP SitRep {n}{where} (reviewed promotion record)"
    return rows, "reviewed SitRep promotion record, checked against the release inputs file", set_aside


NOT_ESTIMABLE = "coverage not estimable: no printed denominator or rate"


def _coverage_param(spec: Mapping, rows: Mapping[str, dict], province: str) -> tuple[Param | None, dict]:
    kind = spec["coverage"]["from"]
    if kind == "record":
        row = rows.get(province)
        if row is None:
            return None, {"reason": "no vaccination record row"}
        cov = vfe.coverage_from_vaccination_record(row)
        if cov["coverage"] is None:
            return None, {"reason": NOT_ESTIMABLE}
        where = row.get("source", "vaccination record")
        if row.get("denominator_field") not in (None, "pre_registered") and "pre_registered" in cov["basis"]:
            cov = {**cov, "basis": cov["basis"].replace(
                "pre_registered", f"{row['denominator_field']} (the printed denominator)")}
        return (Param("coverage", "fixed", (cov["coverage"],), "sitrep", f"{where}; {cov['basis']}",
                      updatable_each_release=True), {"coverage_basis": cov["basis"]})
    if kind == "printed_ratio":
        c = spec["coverage"]
        num, den = c["numerator"], c["denominator"]
        if not den:
            return None, {"reason": "printed denominator missing"}
        if not str(c.get("denominator_assumption", "")).startswith("ASSUMPTION"):
            raise ReleaseInputError(f"{province}.{spec['name']}: a ratio built from two printed values needs an "
                                    "ASSUMPTION denominator_assumption (that the denominator still describes "
                                    "the same people)")
        lo_f, hi_f = c.get("denominator_factor_range", (None, None))
        if not lo_f or not hi_f or not lo_f <= 1.0 <= hi_f:
            raise ReleaseInputError(f"{province}.{spec['name']}: a ratio of printed values needs "
                                    "denominator_factor_range [lo, hi] around 1 for its assumed denominator")
        lo, hi = min(1.0, num / (den * hi_f)), min(1.0, num / (den * lo_f))
        text = c["denominator_assumption"].removeprefix("ASSUMPTION:").strip()
        basis = (f"ASSUMPTION: coverage uniform between {lo:.4f} and {hi:.4f}, i.e. {num:,} "
                 f"({c['numerator_source']}) over {den:,} ({c['denominator_source']}) scaled by a factor "
                 f"{lo_f} to {hi_f}; {text}")
        return (Param("coverage", "uniform", (lo, hi), "assumption", basis, updatable_each_release=True),
                {"coverage_basis": basis})
    if kind == "assumption":
        c = spec["coverage"]
        if not str(c["source"]).startswith("ASSUMPTION"):
            raise ReleaseInputError(f"{province}.{spec['name']}: an assumed coverage must be labelled ASSUMPTION")
        return Param("coverage", "uniform", tuple(c["range"]), "assumption", c["source"]), {}
    raise ReleaseInputError(f"{province}.{spec['name']}: unknown coverage source {kind!r}")


def build_groups(inputs: Mapping, rows: Mapping[str, dict], ring: Param) -> tuple[dict, dict, dict]:
    """Groups per province, what cannot be computed (with why), and each group's timing-mode source."""
    computed: dict[str, list[Group]] = {}
    skipped: dict[str, str] = {}
    mode_sources: dict[tuple[str, str], str | None] = {}
    for province, specs in inputs["groups"].items():
        groups = []
        for spec in specs:
            cov, note = _coverage_param(spec, rows, province)
            if cov is None:
                skipped[f"{province}.{spec['name']}"] = note["reason"]
                continue
            role = spec["role"]
            completeness = LISTED_CONTACTS_COMPLETE if role == "contacts" else LIST_COMPLETENESS
            kw = {}
            if spec["mode"] == "pre_exposure" and not str(spec.get("mode_source", "")).startswith("ASSUMPTION"):
                raise ReleaseInputError(f"{province}.{spec['name']}: mode pre_exposure needs an ASSUMPTION "
                                        "mode_source (everyone vaccinated ahead of exposure)")
            if spec["mode"] in ("given", "mixture"):
                kw["timing"] = ring
            if spec["mode"] == "mixture":
                w = spec["pre_exposure_weight"]
                if not str(w["source"]).startswith("ASSUMPTION"):
                    raise ReleaseInputError(f"{province}.{spec['name']}: pre_exposure_weight must be ASSUMPTION")
                kw["pre_exposure_weight"] = Param("pre_exposure_weight", "uniform", tuple(w["range"]),
                                                  "assumption", w["source"])
            groups.append(Group(spec["name"], spec["mode"], cov, completeness, CASE_SHARES[role], **kw))
            mode_sources[(province, spec["name"])] = spec.get("mode_source")
        if groups:
            computed[province] = groups
    for row_prov, row in rows.items():
        if row_prov not in inputs["groups"]:
            cov = vfe.coverage_from_vaccination_record(row)
            skipped[row_prov] = ("no target group defined" if cov["coverage"] is not None
                                 else NOT_ESTIMABLE)
    return computed, skipped, mode_sources

# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


# Inputs used only by the uncalibrated "ring" timing mode. When no group uses
# that mode they are listed apart and kept out of the sensitivity tables, so a
# reader never takes them for the timing behind ring or mixed rows.
RING_MODE_ONLY = ("delay_to_vaccination", "tau_index", "tau_next", "index_share", "incubation")
# Individual protection before timing (e3) depends on these alone; anything
# else in its sensitivity table would be estimator noise.
UPSTREAM_OF_INDIVIDUAL = ("ve_zaire", "strain_match", "potency", "durability")


def _r(q: Mapping) -> dict:
    return {k: round(v, 4) for k, v in q.items()}


PROTECTION_DEFINITIONS = {
    "pre_exposure": ("protection of a vaccinated group member, averaged over when exposure falls in the "
                     "horizon; per vaccinee"),
    "given": ("protection over all infections in the group's rings, counting members already ill at "
              "vaccination (never vaccinated) as unprotected; a vaccinated contact's own protection is about "
              "this value divided by ring_protected_share.vaccinated_fraction"),
    "mixture": ("a blend: per vaccinee for the share vaccinated ahead of exposure, and over all ring "
                "infections (counting members never vaccinated) for the rest. The record's coverage counts "
                "everyone listed, so members already ill at vaccination may lower both the coverage and the "
                "ring share; this likely understates protection somewhat"),
    "ring": "protection over all infections in the group's rings, from the uncalibrated ring timing mode",
}


def _drop(sens: Mapping, unused: set[str]) -> dict:
    return {k: v for k, v in sens.items() if k not in unused}


def _province_result(groups: list[Group], report_e6: bool, mode_sources: Mapping[str, str | None],
                     draws: int, cond_draws: int, seed: int) -> dict:
    base = method_inputs()
    unused = set() if any(g.mode == "ring" for g in groups) else set(RING_MODE_ONLY)
    prior = vfe.simulate(base, groups, draws=draws, seed=seed)
    sens_e3 = {k: v for k, v in prior["sensitivity_first_order_e3"].items() if k in UPSTREAM_OF_INDIVIDUAL}
    sens_e6 = _drop(prior["sensitivity_first_order_e6"], unused | {"indirect_multiplier"})
    conditional = []
    for s in STRAIN_MATCH_VALUES:
        fixed = dataclasses.replace(base.strain_match, kind="fixed", args=(s,))
        res = vfe.simulate(dataclasses.replace(base, strain_match=fixed), groups, draws=cond_draws, seed=seed)
        conditional.append({
            "strain_match": s,
            "individual_protection_before_timing": _r(res["stages"]["e3"]),
            "groups": {g: {"protection": _r(d["e4"]),
                           "share_of_group_infections_averted": _r(d["e5"])}
                       for g, d in res["groups"].items()},
            "share_of_all_cases_averted": _r(res["stages"]["e6"]) if report_e6 else None,
        })
    out = {
        "share_of_all_cases_computed": report_e6,
        "groups": {g.name: {"mode": g.mode, "mode_source": mode_sources.get(g.name),
                            "protection_definition": PROTECTION_DEFINITIONS[g.mode]} for g in groups},
        "conditional_on_strain_match": conditional,
        "uniform_prior_on_strain_match": {
            "note": ("set by the uniform ignorance prior on strain match, not by data; the conditional rows "
                     "are the result"),
            "individual_protection_before_timing": _r(prior["stages"]["e3"]),
            "groups": {g: {"timing_protected_share": _r(d["timing"]),
                           "protection": _r(d["e4"]),
                           "effective_coverage": _r(d["coverage_effective"]),
                           "share_of_group_infections_averted": _r(d["e5"])}
                       for g, d in prior["groups"].items()},
            "share_of_all_cases_averted": _r(prior["stages"]["e6"]) if report_e6 else None,
            "indirect_effect_scenario_not_an_estimate": _r(prior["stages"]["e6_indirect_scenario"]) if report_e6 else None,
        },
        "largest_driver_individual": next(iter(sens_e3), None),
        "sensitivity_first_order_individual": sens_e3,
    }
    if report_e6:
        out["share_of_all_cases_note"] = (
            "computed under labelled assumptions: each group's share of future cases is not printed by INSP; "
            "the contacts' coverage is assumed; any coverage built from values printed in different releases "
            "carries an assumed range on its denominator; the frontline and contact split is the latest "
            "printed one; frontline workers are assumed vaccinated ahead of exposure; the contacts' ring timing "
            "is a bounded scenario from a Zaire calibration; target lists are assumed 70 % to 100 % complete; "
            "cases outside the groups listed get no direct protection")
        out["sensitivity_first_order_all_cases"] = sens_e6
    for g in groups:
        out["groups"][g.name]["inputs"] = [dataclasses.asdict(p) for p in
                                           (g.coverage, g.denominator_completeness, g.case_share,
                                            g.timing, g.pre_exposure_weight) if p is not None]
        for p in out["groups"][g.name]["inputs"]:
            if p["kind"] == "empirical":
                p["args"] = f"{len(p['args'])} stored values (data/vaccine-field-effectiveness/ring-timing.json)"
    return out


def build_artifact(inputs: Mapping, *, ring: Mapping | None = None, promoted: list[dict] | None = None,
                   draws: int = DRAWS, cond_draws: int = CONDITIONAL_DRAWS, seed: int = SEED) -> dict:
    ring = ring if ring is not None else load_ring_timing()
    rows, record_source, set_aside = vaccination_rows(inputs, promoted)
    computed, skipped, mode_sources = build_groups(inputs, rows, ring_timing_param(ring))
    skipped.update(set_aside)
    report_e6 = set(inputs.get("report_share_of_all_cases", []))
    for province in report_e6 & set(computed):
        if any(g.case_share is CASE_SHARES["unassigned"] for g in computed[province]):
            raise ReleaseInputError(f"{province}: a group with an unassigned case share cannot enter a "
                                    "share of all cases")
    for province in report_e6 - set(computed):
        raise ReleaseInputError(f"{province} is in report_share_of_all_cases but has no computable groups")
    for key in skipped:
        province = key.split(".", 1)[0]
        if province in report_e6:
            raise ReleaseInputError(f"{key} cannot be computed ({skipped[key]}), so a share of all cases in "
                                    f"{province} would cover only part of its groups; drop {province} from "
                                    "report_share_of_all_cases or supply the coverage")
    n = inputs["sitrep_number"]
    provinces = {p: _province_result(g, p in report_e6, {name: src for (prov, name), src in mode_sources.items()
                                                         if prov == p}, draws, cond_draws, seed)
                 for p, g in computed.items()}
    for p, res in provinces.items():
        if not res["share_of_all_cases_computed"]:
            res["share_of_all_cases_note"] = ("not computed: the make-up of the vaccinated list is not printed, "
                                              "so no share of cases can be assigned to it even as an assumption")
    uses_ring_mode = any(g.mode == "ring" for gs in computed.values() for g in gs)
    return {
        "id": f"artifact:vaccine-field-effectiveness:sr{n}",
        "label": f"Vaccine field-effectiveness scenario: INSP SitRep {n}, data {inputs['data_as_of']}",
        "artifactType": ARTIFACT_TYPE,
        "subject": "bdbv-2026",
        "dataDate": inputs["data_as_of"],
        "evidenceClass": "model_output",
        "values": {
            "sitrep_number": n,
            "source_id": inputs.get("source_id"),
            "model_version": vfe.MODEL_VERSION,
            "release_version": RELEASE_VERSION,
            "lane": "scenario",
            "tier": "review",
            "strain_match_identified": False,
            "trial_anchor": {"counts": "0/2108 vs 16/3075", "species": "Zaire ebolavirus only",
                             "source": TRIAL_SOURCE},
            "draws": draws, "conditional_draws": cond_draws, "seed": seed,
            "vaccination_record_source": record_source,
            "provinces": provinces,
            "not_computed": dict(sorted(skipped.items())),
            "ring_protected_share": {
                "with_post_exposure_credit": _r(vfe._quantiles(ring["protected_share_with_pep"])),
                "without_post_exposure_credit": _r(vfe._quantiles(ring["protected_share_without_pep"])),
                "vaccinated_fraction": _r(vfe._quantiles(ring["vaccinated_fraction"])),
                "status": "bounded scenario from a declared Zaire calibration, not an estimate",
            },
            "method_inputs": [dataclasses.asdict(p) for p in method_inputs().params()
                              if uses_ring_mode or p.name not in RING_MODE_ONLY],
            "method_inputs_unused": ([] if uses_ring_mode else
                                     [{"name": n, "note": "used only by the uncalibrated ring timing mode, "
                                       "which no group uses; ring and mixed rows use ring_protected_share, "
                                       "which carries its own BDBV incubation"}
                                      for n in RING_MODE_ONLY]),
            "release_inputs_notes": inputs.get("notes", []),
        },
        "methodBasis": (
            "Decomposition of trial efficacy into field protection: Zaire trial efficacy x strain match x "
            "potency x durability x timing x effective coverage, summed over groups by case share for a "
            "direct effect only. Strain match is unidentified, so every result is given conditional on it "
            "and under a uniform prior, which summarises the prior and is not a data-driven estimate. "
            "Coverage comes from a printed rate, a ratio of values printed in the same release, a ratio of "
            "values printed in different releases (an assumption with a range on the denominator), or a "
            "labelled assumption (named in each group's inputs, with the SitRep each value comes from); every other "
            "input is a sourced value, a derived quantity or a labelled assumption."),
        "citations": [
            {"label": "Henao-Restrepo et al., Lancet 2017 (Guinea ring trial, Zaire ebolavirus)",
             "doi": "10.1016/S0140-6736(16)32621-6"},
            {"label": f"INSP SitRep {n} and the earlier SitReps named in each input's source"},
            {"label": "Falzarano et al., J Infect Dis 2011 (rVSV-ZEBOV, macaques, BDBV challenge)",
             "doi": "10.1093/infdis/jir350"},
            {"label": "Mire et al., PLoS Negl Trop Dis 2013 (rVSV vaccines, macaques, BDBV challenge)",
             "doi": "10.1371/journal.pntd.0002600"},
            {"label": "Kennedy et al., N Engl J Med 2017 (PREVAIL I)", "doi": "10.1056/NEJMoa1614067"},
            {"label": "Regules et al., N Engl J Med 2017", "doi": "10.1056/NEJMoa1414216"},
            {"label": "Marzi et al., Science 2015", "doi": "10.1126/science.aab3920"},
            {"label": "Huttner et al., Clin Microbiol Infect 2023", "doi": "10.1016/j.cmi.2023.08.026"},
            {"label": "Kahusu et al., Vaccine 2025", "doi": "10.1016/j.vaccine.2025.127537"},
            {"label": "Baller et al., Int J Infect Dis 2022", "doi": "10.1016/j.ijid.2021.11.039"},
            {"label": "Rosello et al., eLife 2015", "doi": "10.7554/eLife.09015"},
            {"label": "Muyembe et al., N Engl J Med 2024 (DRC ring vaccination, calibration check)",
             "doi": "10.1056/NEJMoa1904387"},
            {"label": "WHO Ebola Response Team, N Engl J Med 2014 (serial interval and incubation)",
             "doi": "10.1056/NEJMoa1411100"},
            {"label": "WHO and INRB, preliminary results on rVSV-ZEBOV-GP ring vaccination in the DRC, "
                      "April 2019 (index onset to ring vaccination delay, early-case share)"},
        ],
        "limitations": [
            "A scenario, not a measured effectiveness: no field or human estimate of protection against "
            "Bundibugyo virus exists, and strain match is not identified by any SitRep.",
            "The trial anchor is a Zaire ebolavirus estimate; it is never a Bundibugyo figure.",
            "Indirect (chain-breaking) effects are excluded from the share of cases averted; the indirect "
            "row is a labelled scenario only.",
            "Ring-vaccination timing comes from a calibration that fits Zaire data poorly; treat ring and "
            "mixed-timing rows as bounds.",
            "No national total is computed. A province is computed only for the groups defined for it, each "
            "with a printed or a labelled assumed coverage; other record rows are listed as not computed, and "
            "zone-level rows are set aside, never summed into a province.",
            "Situational evidence, not clinical or vaccine advice.",
        ],
    }


def artifact_path(sitrep: int, data_dir: pathlib.Path | None = None) -> pathlib.Path:
    return (data_dir or DATA_DIR) / f"scenario-sr{sitrep}.json"


def render(artifact: Mapping) -> str:
    return json.dumps(artifact, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sitrep", type=int, required=True)
    ap.add_argument("--check", action="store_true", help="fail if the committed artifact differs from a rebuild")
    args = ap.parse_args(argv)
    inputs = load_release_inputs(args.sitrep)
    text = render(build_artifact(inputs, promoted=promotion_rows(args.sitrep)))
    path = artifact_path(args.sitrep)
    if args.check:
        if not path.exists() or path.read_text() != text:
            print(f"{path.relative_to(REPO_ROOT)} differs from a rebuild", file=sys.stderr)
            return 1
        print(f"{path.relative_to(REPO_ROOT)} matches a rebuild")
        return 0
    path.write_text(text)
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
