"""LOVS Module C2: active-queue lab-yield projection.

A SIBLING diagnostic to Module C / C1 (the reporting-completeness nowcast in
``lovs_visibility.py``), NOT a replacement and NOT an input to it. C2 estimates,
for the already-known operational active-suspected queue (cases under
investigation plus those in isolation), how many will laboratory-confirm, using
the recent observed lab positivity from reviewed SitRep lab indicators:

    positivity ~ Beta(1 + Sigma positive, 1 + Sigma (analyzed - positive))   # flat prior
    expected_confirmations_50  = active_suspected_total * positivity_50
    confirmable_active_queue_50 = confirmed + expected_confirmations_50

C2 deliberately does NOT estimate reporting completeness, hidden community
incidence, deaths, or future spread. It NEVER writes ``reporting_completeness``
and NEVER changes ``DATA_TERM_WEIGHT`` (which stays 0.0 in lovs_visibility): this
module must not import or mutate the C1 visibility nowcast. Emitting ``None`` (no
reviewed lab indicators at or before the as-of date) must leave the C1 visibility
block byte-identical.

Inputs come ONLY from reviewed, ready-for-model-use SitRep promotions
(``sitrep_promotions.reviewed_promotions_by_number``); the candidate/staging path
is never read here, so unreviewed lab figures cannot publish through C2. The
positivity window accumulates across every eligible reviewed SitRep that carries
its own ``figures.lab_indicators_24h`` up to the as-of date, so the projection
extends from a single point to a multi-date band automatically as #016/#017 gain
reviewed lab indicators, with no code change.

Stdlib only; deterministic (analytic incomplete-beta inversion, no sampling).
"""
from __future__ import annotations

import math
from typing import Any


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction expansion for the incomplete beta (Numerical Recipes)."""
    MAXIT = 300
    EPS = 3.0e-14
    FPMIN = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _beta_quantile(a: float, b: float, p: float) -> float:
    """Inverse of I_x(a, b) = p by bisection. Deterministic, no RNG."""
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _betai(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def c2_active_queue_projection(
    reviewed_promotions_by_number: dict[int, dict[str, Any]],
    *,
    as_of: str,
    confirmed_active_total: int,
    active_suspected_total: int,
    suspected_under_investigation: int | None = None,
    suspected_in_isolation: int | None = None,
    n_samples: int = 1000,  # reserved for the sampling fallback; analytic path ignores it
    seed: int | None = None,
) -> dict[str, Any] | None:
    """Compute the C2 active-queue lab-yield projection for ``as_of``.

    Accumulates reviewed lab indicators from every reviewed promotion with
    ``figures.lab_indicators_24h`` whose ``data_as_of`` is on or before ``as_of``.
    Returns the c2_* projection dict, or ``None`` when no eligible reviewed lab
    indicators exist (a graceful no-op that leaves the C1 visibility block
    untouched). Today only SitRep #018 carries reviewed lab indicators, so this
    returns a single 2026-06-01 projection; it becomes a band the moment #016/#017
    gain reviewed lab figures, no code change.
    """
    eligible: list[tuple[str, int, dict[str, Any], dict[str, Any]]] = []
    for number, payload in reviewed_promotions_by_number.items():
        review = payload.get("review", {}) or {}
        # Re-assert the reviewed gate per record even though the loader already
        # filters with require_reviewed=True. Unreviewed lab figures must never
        # reach C2.
        if payload.get("status") != "reviewed":
            continue
        if review.get("source_review_status") != "reviewed":
            continue
        if review.get("ready_for_model_use") is not True:
            continue
        data_as_of = str(payload.get("data_as_of", ""))
        if not data_as_of or data_as_of > as_of:
            continue
        lab = (payload.get("figures", {}) or {}).get("lab_indicators_24h")
        if not isinstance(lab, dict):
            continue
        if "samples_analyzed" not in lab or "samples_positive" not in lab:
            continue
        eligible.append((data_as_of, int(number), payload, lab))

    if not eligible:
        return None

    eligible.sort(key=lambda row: (row[0], row[1]))
    analyzed = sum(int(lab["samples_analyzed"]) for _, _, _, lab in eligible)
    positive = sum(int(lab["samples_positive"]) for _, _, _, lab in eligible)
    if analyzed <= 0 or positive < 0 or positive > analyzed:
        return None

    # Flat Beta(1,1) prior -> posterior Beta(1 + positive, 1 + analyzed - positive).
    alpha = 1.0 + positive
    beta = 1.0 + (analyzed - positive)
    positivity_lower = _beta_quantile(alpha, beta, 0.25)
    positivity_upper = _beta_quantile(alpha, beta, 0.75)
    # Observed point estimate of recent lab positivity (directly observed share).
    positivity_point = round(positive / analyzed, 4)

    expected_lower = round(active_suspected_total * positivity_lower)
    expected_upper = round(active_suspected_total * positivity_upper)
    confirmable_lower = confirmed_active_total + expected_lower
    confirmable_upper = confirmed_active_total + expected_upper

    window_dates = [data_as_of for data_as_of, _, _, _ in eligible]
    evidence_chain_ids = [
        str(payload["review"]["evidence_chain_id"])
        for _, _, payload, _ in eligible
        if (payload.get("review") or {}).get("evidence_chain_id")
    ]

    # Carry-back C2 series. Apply the most-recent reviewed positivity to EACH
    # reviewed promotion's own reported active-suspected queue + country-scope
    # confirmed total, for the dates where the active-suspected split is reported
    # (eligible_since 2026-05-30; SitRep #015 / May 29 carries only a cumulative
    # suspected count, so it is excluded and stays confirmed-only downstream).
    # The reviewed-positivity date uses the measured positivity directly; earlier
    # dated windows carry it back and are flagged "carried_back" (their lab work
    # was not separately reviewed). This is a known-queue yield BY DATE, never a
    # per-date lab measurement and never a reporting-completeness estimate.
    SERIES_SINCE = "2026-05-30"
    reviewed_window_date = window_dates[-1]
    per_date_windows: list[dict[str, Any]] = []
    for _number, payload in reviewed_promotions_by_number.items():
        review = payload.get("review", {}) or {}
        if payload.get("status") != "reviewed":
            continue
        if review.get("source_review_status") != "reviewed":
            continue
        if review.get("ready_for_model_use") is not True:
            continue
        d = str(payload.get("data_as_of", ""))
        if not d or d < SERIES_SINCE or d > as_of:
            continue
        fig = payload.get("figures", {}) or {}
        confirmed_d = fig.get("country_scope_confirmed_total")
        queue_d = fig.get("suspected_active_total")
        if not isinstance(confirmed_d, int) or not isinstance(queue_d, int):
            continue
        series_exp_lo = round(queue_d * positivity_lower)
        series_exp_hi = round(queue_d * positivity_upper)
        per_date_windows.append({
            "date": d,
            "confirmed": confirmed_d,
            "active_suspected_total": queue_d,
            "expected_active_queue_confirmations_50": [series_exp_lo, series_exp_hi],
            "confirmable_active_queue_50": [
                confirmed_d + series_exp_lo,
                confirmed_d + series_exp_hi,
            ],
            "positivity_basis": "reviewed" if d == reviewed_window_date else "carried_back",
        })
    per_date_windows.sort(key=lambda w: w["date"])

    inputs: dict[str, Any] = {
        "confirmed": confirmed_active_total,
        "active_suspected_total": active_suspected_total,
    }
    if suspected_under_investigation is not None:
        inputs["suspected_under_investigation"] = int(suspected_under_investigation)
    if suspected_in_isolation is not None:
        inputs["suspected_in_isolation"] = int(suspected_in_isolation)

    # Canonical nested schema shared by every consumer (website sync, public
    # workbook export, dependency audit). C2 is a SIBLING diagnostic to the C1
    # reporting-completeness nowcast: a known-active-queue lab yield, never an
    # input to C1 and never an estimate of reporting completeness, hidden
    # community incidence, deaths, or future spread.
    return {
        "status": "active",
        "method_basis": (
            "reviewed SitRep lab indicators (accumulated), flat Beta(1,1) "
            "positivity applied to the operational active-suspected queue "
            "(cases under investigation plus cases in isolation); a sibling "
            "diagnostic to C1 reporting-completeness that never feeds it"
        ),
        "formula": "confirmed + active suspected queue * recent lab positivity",
        "scope": "national",
        "eligible_since": "2026-05-30",
        "evidence_chain_id": evidence_chain_ids[-1] if evidence_chain_ids else "",
        # Full accumulated provenance for the audit/export layer. The public
        # export layer MUST withhold the raw chain ids (sensitive 'ec:lovs:'
        # needle) and emit a count instead.
        "evidence_chain_ids": evidence_chain_ids,
        "not_estimating": [
            "reporting completeness",
            "hidden community incidence",
            "deaths",
            "future spread",
        ],
        "limitations": [
            "Known-queue yield only: the active suspected queue is a "
            "response-system stock, not a denominator for total community "
            "infections.",
            "Single reviewed lab window today (SitRep #018); extends to a "
            "multi-date band automatically as earlier SitReps gain reviewed "
            "lab indicators, with no code change.",
        ],
        "inputs": inputs,
        "primary_window": {
            "confirmable_active_queue_50": [confirmable_lower, confirmable_upper],
            "expected_active_queue_confirmations_50": [expected_lower, expected_upper],
            "positivity_50": [round(positivity_lower, 4), round(positivity_upper, 4)],
            "positivity_point": positivity_point,
            "samples_analyzed": analyzed,
            "samples_positive": positive,
            "date_start": window_dates[0],
            "date_end": window_dates[-1],
        },
        "per_date_windows": per_date_windows,
        "positivity_basis_note": (
            f"Positivity is the most recent reviewed lab window ({positive}/{analyzed} "
            f"on {reviewed_window_date}). That date uses the reviewed positivity "
            "directly; earlier dated windows carry it back onto each date's reported "
            "active-suspected queue (those dates' lab work was not separately "
            "reviewed) and are flagged carried_back."
        ),
        "review_status": "reviewed",
    }


if __name__ == "__main__":  # standalone calibration self-test
    promotions = {
        18: {
            "status": "reviewed",
            "source_id": "inrb-sitrep-018-2026-06-01",
            "data_as_of": "2026-06-01",
            "figures": {
                "lab_indicators_24h": {"samples_analyzed": 76, "samples_positive": 23},
            },
            "review": {
                "ready_for_model_use": True,
                "source_review_status": "reviewed",
                "evidence_chain_id": "ec:lovs:data:inrb-sitrep-018-headline-promotion:2026-06-01",
            },
        },
    }
    result = c2_active_queue_projection(
        promotions,
        as_of="2026-06-01",
        confirmed_active_total=355,
        active_suspected_total=289,
        suspected_under_investigation=116,
        suspected_in_isolation=173,
    )
    assert result is not None
    assert result["status"] == "active", result["status"]
    window = result["primary_window"]
    targets = {
        "positivity_50_lower": (window["positivity_50"][0], 0.2715),
        "positivity_50_upper": (window["positivity_50"][1], 0.3421),
        "positivity_point": (window["positivity_point"], 0.3026),
        "expected_lower": (window["expected_active_queue_confirmations_50"][0], 78),
        "expected_upper": (window["expected_active_queue_confirmations_50"][1], 99),
        "confirmable_lower": (window["confirmable_active_queue_50"][0], 433),
        "confirmable_upper": (window["confirmable_active_queue_50"][1], 454),
    }
    for name, (got, expected) in targets.items():
        ok = abs(got - expected) <= (0.0006 if isinstance(expected, float) else 0)
        print(f"  {name}: got {got} expected {expected} {'OK' if ok else 'MISMATCH'}")
        assert ok, f"{name}: {got} != {expected}"
    assert result["inputs"] == {
        "confirmed": 355,
        "active_suspected_total": 289,
        "suspected_under_investigation": 116,
        "suspected_in_isolation": 173,
    }, result["inputs"]
    print(
        "C2 calibration self-test PASSED:",
        window["confirmable_active_queue_50"][0],
        "-",
        window["confirmable_active_queue_50"][1],
    )
