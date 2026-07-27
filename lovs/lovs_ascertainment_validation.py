"""Is the ascertainment correction identifiable from the sources we have?

WHY THIS EXISTS. ``lovs_convergence.ascertainment_confounding`` establishes that the
confirmed-case growth rate is biased DOWNWARD when positivity rises: confirmed is exactly
``tests x positivity``, so ``r_confirmed = r_true + d(ln a)/dt`` and a falling detected
fraction ``a`` drags the reported rate below the true one. That diagnostic is a SIGN
statement. It says "the doubling time is an upper bound" and nothing about how loose the
bound is.

Turning the sign into a magnitude needs a deflator: an ascertainment series ``a_t`` such
that ``I_hat_t = confirmed_t / a_t``. The scope for that work
(``.process/2026-07-27-bdbv-ascertainment-corrected-growth``) sets one guardrail above all
others: DO NOT publish a corrected central until it is validated against a signal with
different ascertainment dynamics, because a wrong central is worse than an honest bound.

This module is that validation step, run as a standing check rather than a one-off
analysis. It answers a single question each cycle -- can this outbreak's own surveillance
streams identify the correction? -- and it is built to FLIP if the answer ever changes.
The verdict is data-driven, so a future cycle in which the funnel de-saturates would
reopen the scope automatically instead of relying on anyone remembering to re-ask.

WHAT IT CHECKS. Three independent conditions, all of which must hold before a corrected
central is defensible:

  1. WINDOW STABILITY. The first-order correction ``r_true ~ r_confirmed + r_positivity``
     must not depend on the trailing-window length. If the implied doubling time swings by
     more than ``IDENTIFIABILITY_SPREAD_LIMIT`` across plausible windows, the "correction"
     is measuring the window, not the epidemic.
  2. BOUND COHERENCE. Every window must give ``r_true >= r_confirmed``. A correction that
     lands ABOVE the published upper bound on doubling time (i.e. a slower epidemic than
     the bound permits) contradicts the framing it is supposed to tighten, which is proof
     the deflator is not measuring ascertainment.
  3. CORROBORATION. At least one signal whose detection pathway differs from PCR
     confirmation must grow FASTER than confirmed. If every partially-independent signal
     grows more SLOWLY, the sources cannot distinguish "ascertainment is falling" from
     "the whole detection funnel is saturated", and the magnitude is unidentified.

WHAT THIS MODULE REFUSES TO DO. It never emits a corrected central. It reports whether one
could be defended, and by what evidence. When the verdict is ``not_identifiable`` the
caller keeps publishing the bound: that is the correct output, not a degraded one.

WHY DEATHS ARE NOT THE VALIDATOR. Community deaths in this outbreak are swab-confirmed,
so the death series passes through the same PCR gate as the case series. It is carried
below as a signal because its lag structure differs, but it is marked
``testing_gated=True`` and cannot on its own satisfy condition (3).

Stdlib only. Deterministic.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

#: Trailing windows the correction must agree across. 21 days is the published estimator's
#: window (the operational active-case horizon); the others bracket it. A correction that
#: only exists at one of these is a window artifact.
DEFAULT_WINDOWS_DAYS: tuple[int, ...] = (14, 21, 28, 35, 42)

#: Maximum ratio between the longest and shortest implied doubling time across
#: ``DEFAULT_WINDOWS_DAYS`` before the correction is called window-unstable. 2.0 is already
#: permissive: it allows the corrected central to say "8 days" or "16 days" about the same
#: epidemic and still pass.
IDENTIFIABILITY_SPREAD_LIMIT = 2.0

#: A signal must beat r_confirmed in strictly more than this fraction of the windows it can
#: be measured on before it counts as corroborating. Bare majority.
CORROBORATION_MAJORITY = 0.5

#: Minimum usable observations in a window before a growth rate is estimated at all.
MIN_POINTS_PER_WINDOW = 4


class SignalSpec:
    """One corroborating signal, with the argument for why it is partially independent.

    ``detection_pathway`` is not decoration. Condition (3) is only meaningful if the signal
    reaches the count through a different gate than PCR confirmation does, and that claim
    has to be written down next to the number so it can be argued with.

    ``zero_means_missing`` guards the silent-drop trap. Six promotions record
    ``admissions_24h`` as 0 because the SitRep published no patient-movement table at all
    (each carries a ``data_gap_note`` saying so); with 85% bed occupancy and 100+
    confirmations a day, a true zero-admission day is not a thing that happened. Reading
    those as real zeros would drag the admissions growth rate toward a fabricated collapse.
    """

    __slots__ = ("key", "table", "label", "detection_pathway", "testing_gated",
                 "zero_means_missing", "kind")

    def __init__(
        self,
        key: str,
        table: str | None,
        label: str,
        detection_pathway: str,
        *,
        testing_gated: bool = False,
        zero_means_missing: bool = False,
        kind: str = "flow",
    ) -> None:
        self.key = key
        self.table = table
        self.label = label
        self.detection_pathway = detection_pathway
        self.testing_gated = testing_gated
        self.zero_means_missing = zero_means_missing
        self.kind = kind


#: The candidate validators named in the scope, plus the two the scope rules out, so the
#: ruled-out ones stay visible in the output rather than being silently absent.
CORROBORATING_SIGNALS: tuple[SignalSpec, ...] = (
    SignalSpec(
        "admissions_24h", "patient_movement_total", "isolation admissions (Tableau 6)",
        "Admission to a CTE/CT/CI is triggered by clinical suspicion after alert "
        "investigation, BEFORE laboratory confirmation. It is gated by alert-response "
        "reach and by bed capacity, not by PCR throughput.",
        zero_means_missing=True,
    ),
    SignalSpec(
        "alerts_validated_deaths", "alerts_total", "validated death alerts (EDS/burial)",
        "A community death alert is raised by burial teams and community reporting, and is "
        "validated on circumstance before any swab result. The alert count itself is "
        "independent of laboratory capacity even though the subsequent confirmation is not.",
    ),
    SignalSpec(
        "alerts_validated_living", "alerts_total", "validated living-case alerts",
        "Alert validation applies the clinical case definition to a reported alert. It is "
        "gated by investigation-team capacity rather than by laboratory capacity.",
    ),
    SignalSpec(
        "alerts_investigated", "alerts_total", "alert investigations",
        "Raw investigation throughput: how many reported alerts the response actually "
        "reached. A pure effort measure, and therefore the cleanest read on whether the "
        "detection funnel is saturating.",
    ),
    SignalSpec(
        "suspected_cases_day", "alerts_total", "suspected cases notified per day",
        "Entry into the suspect queue on the clinical case definition, upstream of the "
        "laboratory.",
    ),
    SignalSpec(
        "new_confirmed_deaths_24h", None, "confirmed deaths notified",
        "RULED OUT as an independent validator: community deaths in this outbreak are "
        "swab-confirmed, so this series passes through the same PCR gate as the case "
        "series. Carried for its differing lag structure only.",
        testing_gated=True,
    ),
)


def _figure(promotion: Mapping[str, Any], spec: SignalSpec) -> Any:
    """Resolve a signal's value from one promotion, honoring its zero-means-missing rule."""
    figures = promotion.get("figures")
    if not isinstance(figures, Mapping):
        return None
    if spec.table is None:
        value = figures.get(spec.key)
        if value is None:
            value = promotion.get(spec.key)
    else:
        tables = figures.get("operational_tables")
        table = tables.get(spec.table) if isinstance(tables, Mapping) else None
        value = table.get(spec.key) if isinstance(table, Mapping) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if value == 0 and spec.zero_means_missing:
        return None
    return float(value)


def load_corroborating_signals(
    promotions: Iterable[Mapping[str, Any]],
    specs: Sequence[SignalSpec] = CORROBORATING_SIGNALS,
) -> dict[str, list[dict[str, Any]]]:
    """Build per-signal daily flow series from reviewed SitRep promotions.

    Mirrors the ``_testing_series`` construction in ``refresh_pipeline`` so the validator
    reads the same reviewed corpus the published growth rate does.
    """
    out: dict[str, list[dict[str, Any]]] = {spec.key: [] for spec in specs}
    for promo in promotions:
        as_of = str(promo.get("data_as_of") or "")[:10]
        if not as_of:
            continue
        for spec in specs:
            value = _figure(promo, spec)
            if value is not None:
                out[spec.key].append({"date": as_of, "value": value})
    for rows in out.values():
        rows.sort(key=lambda r: r["date"])
    return out


def half_window_log_growth(
    series: Sequence[Mapping[str, Any]],
    as_of: str,
    *,
    window_days: int,
) -> dict[str, Any] | None:
    """Growth rate of a DAILY FLOW series, by the published estimator's own method.

    ``r = ln(mean_2nd_half / mean_1st_half) / (window_days / 2)``

    This is deliberately identical to the comparison inside
    ``lovs_convergence.estimate_growth_rate`` so that a corroborating signal and the
    confirmed series are never compared across two different estimators. The difference is
    only the input: that function differences a CUMULATIVE series into incidence (and needs
    the reconciliation guard to do it safely), whereas these signals are published as
    per-day flows already and are not differenced.

    Not floored at zero. A declining corroborating signal is information, and flooring it
    would hide exactly the saturation this module exists to detect.
    """
    target = date.fromisoformat(as_of[:10])
    pts: list[tuple[int, float]] = []
    for row in series:
        raw = row.get("date")
        value = row.get("value")
        if not raw or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if value <= 0:
            continue
        day = date.fromisoformat(str(raw)[:10])
        if 0 <= (target - day).days <= window_days:
            pts.append((day.toordinal(), float(value)))
    if len(pts) < MIN_POINTS_PER_WINDOW:
        return None
    mid = target.toordinal() - window_days / 2.0
    first = [v for o, v in pts if o < mid]
    second = [v for o, v in pts if o >= mid]
    if not first or not second:
        return None
    m1 = sum(first) / len(first)
    m2 = sum(second) / len(second)
    if m1 <= 0 or m2 <= 0:
        return None
    return {
        "r_per_day": math.log(m2 / m1) / (window_days / 2.0),
        "window_days": window_days,
        "n_first_half": len(first),
        "n_second_half": len(second),
        "mean_first_half": round(m1, 2),
        "mean_second_half": round(m2, 2),
    }


def correction_window_stability(
    confirmed_growth_by_window: Mapping[int, float | None],
    positivity_growth_by_window: Mapping[int, float | None],
) -> dict[str, Any]:
    """Condition (1) and (2): is the first-order correction stable, and bound-coherent?

    The correction under test is the one ``ascertainment_confounding`` currently emits as
    ``first_order_r_uplift_per_day``: under the heuristic ``a ~ 1/p``,
    ``d(ln a)/dt = -r_positivity`` and therefore ``r_true ~ r_confirmed + r_positivity``.

    Both failure modes are checked here because they share the same arithmetic:

      * unstable  -- implied doubling times disagree by more than the spread limit
      * incoherent -- some window gives ``r_positivity < 0``, so the "corrected" epidemic
        is SLOWER than the published upper bound allows. An upper bound the central
        estimate violates is not a bound.
    """
    per_window: list[dict[str, Any]] = []
    doublings: list[float] = []
    incoherent: list[int] = []
    for window in sorted(confirmed_growth_by_window):
        r_conf = confirmed_growth_by_window.get(window)
        r_pos = positivity_growth_by_window.get(window)
        if r_conf is None or r_pos is None:
            per_window.append({"window_days": window, "status": "insufficient_data"})
            continue
        r_true = r_conf + r_pos
        bound_doubling = (math.log(2.0) / r_conf) if r_conf > 0 else None
        corrected_doubling = (math.log(2.0) / r_true) if r_true > 0 else None
        # r_positivity < 0 means the deflator says the epidemic is slower than the bound.
        coherent = r_true >= r_conf
        if not coherent:
            incoherent.append(window)
        if corrected_doubling is not None:
            doublings.append(corrected_doubling)
        per_window.append({
            "window_days": window,
            "status": "estimated",
            "r_confirmed_per_day": round(r_conf, 5),
            "r_positivity_per_day": round(r_pos, 5),
            "r_true_first_order_per_day": round(r_true, 5),
            "published_bound_doubling_days": (
                round(bound_doubling, 1) if bound_doubling is not None else None
            ),
            "corrected_doubling_days": (
                round(corrected_doubling, 1) if corrected_doubling is not None else None
            ),
            "bound_coherent": coherent,
        })
    spread = (max(doublings) / min(doublings)) if len(doublings) >= 2 and min(doublings) > 0 else None
    return {
        "per_window": per_window,
        "corrected_doubling_min_days": round(min(doublings), 1) if doublings else None,
        "corrected_doubling_max_days": round(max(doublings), 1) if doublings else None,
        "corrected_doubling_spread_ratio": round(spread, 2) if spread is not None else None,
        "spread_limit": IDENTIFIABILITY_SPREAD_LIMIT,
        "window_stable": bool(spread is not None and spread <= IDENTIFIABILITY_SPREAD_LIMIT),
        "bound_coherent": not incoherent,
        "bound_incoherent_windows": incoherent,
    }


def corroboration(
    signal_growth: Mapping[str, Mapping[int, float | None]],
    confirmed_growth_by_window: Mapping[int, float | None],
    specs: Sequence[SignalSpec] = CORROBORATING_SIGNALS,
) -> dict[str, Any]:
    """Condition (3): does any partially-independent signal grow FASTER than confirmed?

    A signal that grows faster corroborates the claim that confirmed understates the truth,
    and its excess is a first handle on the magnitude. A signal that grows more slowly is
    consistent with an entirely different story -- that the detection funnel as a whole is
    saturating -- under which the confirmed rate's bias is unsigned rather than downward.

    ``testing_gated`` signals are scored but excluded from the verdict: they share the PCR
    gate with the case series and so cannot break the degeneracy.
    """
    by_signal: list[dict[str, Any]] = []
    corroborating: list[str] = []
    for spec in specs:
        rates = signal_growth.get(spec.key) or {}
        windows_scored = 0
        windows_faster = 0
        detail: list[dict[str, Any]] = []
        for window in sorted(confirmed_growth_by_window):
            r_sig = rates.get(window)
            r_conf = confirmed_growth_by_window.get(window)
            if r_sig is None or r_conf is None:
                detail.append({"window_days": window, "status": "insufficient_data"})
                continue
            windows_scored += 1
            faster = r_sig > r_conf
            if faster:
                windows_faster += 1
            detail.append({
                "window_days": window,
                "status": "estimated",
                "r_signal_per_day": round(r_sig, 5),
                "excess_over_confirmed_per_day": round(r_sig - r_conf, 5),
                "faster_than_confirmed": faster,
            })
        fraction = (windows_faster / windows_scored) if windows_scored else 0.0
        is_corroborating = (
            not spec.testing_gated
            and windows_scored > 0
            and fraction > CORROBORATION_MAJORITY
        )
        if is_corroborating:
            corroborating.append(spec.key)
        by_signal.append({
            "signal": spec.key,
            "label": spec.label,
            "detection_pathway": spec.detection_pathway,
            "testing_gated": spec.testing_gated,
            "eligible_as_validator": not spec.testing_gated,
            "windows_scored": windows_scored,
            "windows_faster_than_confirmed": windows_faster,
            "fraction_faster": round(fraction, 3) if windows_scored else None,
            "corroborates_downward_bias": is_corroborating,
            "per_window": detail,
        })
    return {
        "signals": by_signal,
        "corroborating_signals": corroborating,
        "corroborated": bool(corroborating),
        "majority_threshold": CORROBORATION_MAJORITY,
    }


def validate_ascertainment_correction(
    *,
    as_of: str,
    confirmed_incidence_series: Sequence[Mapping[str, Any]],
    testing_series: Sequence[Mapping[str, Any]] | None,
    signal_series: Mapping[str, Sequence[Mapping[str, Any]]],
    windows_days: Sequence[int] = DEFAULT_WINDOWS_DAYS,
    specs: Sequence[SignalSpec] = CORROBORATING_SIGNALS,
) -> dict[str, Any]:
    """Run all three conditions and return a verdict on publishing a corrected central.

    ``confirmed_incidence_series`` is the NOTIFIED per-day confirmed flow (``value`` per
    ``date``), not a cumulative series: the notified count is the reconciliation-safe
    incidence observation, so using it here sidesteps the restatement problem that
    ``lovs_count_reconciliation`` exists to catch on the cumulative surface.

    The verdict is ``identifiable`` only when window stability, bound coherence AND
    corroboration all hold. Anything else is ``not_identifiable`` and the caller must keep
    publishing the bound.
    """
    windows = sorted(set(int(w) for w in windows_days))

    def _rates(series: Sequence[Mapping[str, Any]] | None) -> dict[int, float | None]:
        rates: dict[int, float | None] = {}
        for window in windows:
            est = half_window_log_growth(series or [], as_of, window_days=window)
            rates[window] = est["r_per_day"] if est else None
        return rates

    confirmed_rates = _rates(confirmed_incidence_series)
    positivity_rates = _rates(
        [{"date": r.get("date"), "value": r.get("positivity_pct")} for r in (testing_series or [])]
    )
    tests_rates = _rates(
        [{"date": r.get("date"), "value": r.get("tests")} for r in (testing_series or [])]
    )
    signal_rates = {spec.key: _rates(signal_series.get(spec.key) or []) for spec in specs}

    stability = correction_window_stability(confirmed_rates, positivity_rates)
    corrob = corroboration(signal_rates, confirmed_rates, specs)

    identifiable = bool(
        stability["window_stable"] and stability["bound_coherent"] and corrob["corroborated"]
    )
    blockers: list[str] = []
    if not stability["window_stable"]:
        blockers.append("window_unstable")
    if not stability["bound_coherent"]:
        blockers.append("bound_incoherent")
    if not corrob["corroborated"]:
        blockers.append("uncorroborated")

    return {
        "as_of": as_of[:10],
        "verdict": "identifiable" if identifiable else "not_identifiable",
        "publish_corrected_central": identifiable,
        "blockers": blockers,
        "windows_days": windows,
        "confirmed_growth_by_window": {
            w: (round(v, 5) if v is not None else None) for w, v in confirmed_rates.items()
        },
        "tests_growth_by_window": {
            w: (round(v, 5) if v is not None else None) for w, v in tests_rates.items()
        },
        "positivity_growth_by_window": {
            w: (round(v, 5) if v is not None else None) for w, v in positivity_rates.items()
        },
        "window_stability": stability,
        "corroboration": corrob,
        "method": (
            "three-condition identifiability check on the first-order ascertainment "
            "correction: window stability of r_confirmed + r_positivity, coherence with the "
            "published upper bound, and corroboration by a signal whose detection pathway "
            "differs from PCR confirmation"
        ),
        "provenance": "lovs",
        "note": (
            "A 'not_identifiable' verdict is a finding, not a failure. It means the "
            "published doubling time stays an UPPER BOUND with no symmetric interval, which "
            "is the correct output given these sources. The check reruns each cycle and "
            "flips on its own if the surveillance streams ever separate."
            if not identifiable
            else "All three identifiability conditions hold this cycle. A corrected central "
            "is defensible; it still requires review before it is served."
        ),
    }
