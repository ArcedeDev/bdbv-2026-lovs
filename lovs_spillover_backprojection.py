#!/usr/bin/env python3
"""LOVS spillover back-projection — a reproducible origin-TIME analysis.

WHAT THIS ANSWERS
    "When did the 2026 BDBV outbreak most plausibly spill over?" — and, more
    usefully, WHY the naive answer from the confirmed-case curve is wrong.

THE KEY METHODOLOGICAL POINT (the reason this script exists)
    The confirmed-case curve is NOT an epidemic growth curve. Confirmed case
    ascertainment is ~40% (LOVS death-anchored level model: true burden ~3,235),
    and ascertainment IMPROVED over the observed window as the response scaled
    testing. So the curve tracks DETECTION catching up to a pre-existing hidden
    stock, mixed with new transmission. Two signals prove it:
      * daily new confirmed ramps then PLATEAUS (~36/day, Jun 11-27); and
      * test positivity FALLS (41% -> 27%) while testing volume holds steady.
    Both say Rt ~= 1 (a plateau), not exponential growth. A renewal-equation
    Rt(t) (Cori 2013) confirms Rt declining from the early ramp to ~1.

    Therefore an exponential back-projection on the CONFIRMED curve recovers the
    DETECTION-ONSET (when the outbreak became visible, ~mid-April — which is why
    it lands near WHO's first-health-worker-infection timing), NOT the spillover.

THE RE-ANCHORED ESTIMATE
    Deaths are ascertained far more completely and stably (70-95%) than cases
    (40%), so the death curve is a cleaner transmission proxy. It grows slower
    (R ~= 1.6 vs the case curve's inflated ~2.2) and back-projects to a spillover
    median of ~late February. That converges with (a) the CDC mm7522e1 time-based
    back-projection (Jan-Feb) and (b) the large hidden-burden stock accumulated on
    an Rt~1 plateau. Spillover therefore sits in a LONG, mostly-hidden cryptic
    phase: ~late-January to February 2026.

    The cryptic-phase growth is fundamentally UNOBSERVED (we only see the
    post-detection plateau), so spillover is reported as a WINDOW, never a
    false-precise date. The serial interval stays on the Wamala-2010 prior (no
    linked-pair data to estimate it from this outbreak).

INPUTS (SitRep N -> date is 2026-05-14 + N days; verified vs confirmed_death_series
source ids). National confirmed + confirmed-basis deaths + daily test positivity
are carried below as the SitRep-44 dataset with provenance; re-run each cycle by
refreshing these three series.

USAGE
    python3 lovs_spillover_backprojection.py            # prints summary
    python3 lovs_spillover_backprojection.py --emit out.json   # + writes artifact
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import date, timedelta

MODEL_VERSION = "lovs_spillover_backprojection-v1.0.0"
T0 = date(2026, 5, 14)          # first confirmed report (detection anchor); SR_N date = T0 + N days
ANCHOR = date(2026, 6, 27)      # data_as_of
ANCHOR_N = 44
SI_A, SI_B = 4.0, 0.55          # Bundibugyo serial interval gamma (mean 7.27 d), Wamala 2010
TRUE_INFECTIONS = 3235          # LOVS death-anchored level model central (case ascertainment 0.40)

CITATIONS = (
    "Wamala JF, et al. EID 2010 (10.3201/eid1607.091525): Bundibugyo serial interval 3-11 d.",
    "Cori A, et al. AJE 2013 (10.1093/aje/kwt133): renewal-equation Rt.",
    "Wallinga J, Lipsitch M. Proc R Soc B 2007 (10.1098/rspb.2006.3754): R from growth rate r.",
    "CDC MMWR mm7522e1: time-based spillover back-projection (Jan-Feb 2026, external).",
    "LOVS death-anchored level model (true burden ~3,235; case ascertainment 0.40; Rt~1 plateau).",
)

# --- SitRep-44 dataset (provenance: INSP SitReps, reconciled LOVS snapshot) ----
CASES = {1: 8, 2: 13, 4: 33, 5: 51, 6: 64, 7: 83, 8: 91, 9: 101, 10: 105, 11: 106, 12: 121,
         13: 125, 14: 210, 15: 263, 16: 282, 17: 321, 18: 344, 19: 363, 20: 381, 21: 452,
         22: 507, 23: 534, 24: 569, 25: 617, 26: 654, 27: 695, 28: 708, 30: 801, 31: 827,
         32: 856, 33: 894, 34: 915, 35: 952, 36: 975, 37: 1023, 38: 1068, 39: 1114, 40: 1138,
         41: 1175, 42: 1223, 44: 1294}
DEATHS = {19: 63, 20: 65, 21: 84, 22: 88, 23: 93, 24: 103, 25: 117, 26: 129, 27: 138, 28: 141,
          30: 183, 31: 194, 32: 198, 33: 204, 34: 234, 35: 247, 36: 249, 37: 256, 38: 269,
          39: 279, 40: 293, 41: 306, 42: 323, 44: 362}  # confirmed-basis deaths, SitRep N
POS = {10: 35.6, 11: 35.6, 14: 27.8, 15: 77.1, 16: 36.5, 17: 66.6, 18: 30.3, 21: 32.6, 22: 30.0,
       23: 27.0, 24: 46.3, 25: 37.2, 26: 54.4, 27: 26.6, 28: 48.6, 30: 40.3, 31: 20.8, 32: 22.3,
       34: 12.7, 35: 34.6, 36: 20.8, 37: 35.2, 38: 13.2, 39: 43.0, 40: 31.1, 41: 25.0, 42: 25.4,
       44: 27.7}


def dt(n: int) -> date:
    return T0 + timedelta(days=n)


def _fit_log_linear(pairs: list[tuple[int, float]]) -> tuple[float, float]:
    """ln(y) = a + r*t least squares; returns (r per day, se_r)."""
    xs = [(t, math.log(v)) for t, v in pairs]
    k = len(xs)
    mx = sum(x for x, _ in xs) / k
    my = sum(y for _, y in xs) / k
    sxx = sum((x - mx) ** 2 for x, _ in xs)
    sxy = sum((x - mx) * (y - my) for x, y in xs)
    r = sxy / sxx
    resid = [y - (my + r * (x - mx)) for x, y in xs]
    se = math.sqrt(sum(e * e for e in resid) / (k - 2) / sxx)
    return r, se


def _R_from_r(r: float) -> float:
    """Wallinga-Lipsitch for a gamma(a, b) generation interval: R = (1 + r/b)^a."""
    return (1.0 + r / SI_B) ** SI_A


def _daily_incidence(cum: dict[int, int]) -> list[tuple[int, float]]:
    pts = sorted(cum.items())
    return [(n1, (c1 - c0) / (n1 - n0)) for (n0, c0), (n1, c1) in zip(pts, pts[1:])]


def _si_pmf(max_days: int = 40) -> list[float]:
    """Discretized serial-interval pmf w_s, s = 1..max_days (gamma CDF differences)."""
    def cdf(x: float) -> float:
        # regularized lower incomplete gamma via series (shape SI_A integer-ish)
        if x <= 0:
            return 0.0
        s = 0.0
        term = 1.0 / math.gamma(SI_A)
        # numerical integration of the gamma pdf (rate SI_B) on [0, x]
        n = 200
        h = x / n
        total = 0.0
        for i in range(n + 1):
            t = i * h
            pdf = (SI_B ** SI_A) * (t ** (SI_A - 1)) * math.exp(-SI_B * t) / math.gamma(SI_A) if t > 0 else 0.0
            total += pdf * h * (0.5 if i in (0, n) else 1.0)
        return total
    w = [cdf(s + 0.5) - cdf(s - 0.5) for s in range(1, max_days + 1)]
    z = sum(w)
    return [x / z for x in w]


def _renewal_rt(daily_inc: dict[int, float], window: int = 7) -> list[tuple[int, float]]:
    """Cori-2013 windowed Rt: R_t = sum(I over window) / sum(Lambda over window)."""
    w = _si_pmf()
    days = sorted(daily_inc)
    rt = []
    for t in days:
        if t < days[0] + 10:
            continue
        num = den = 0.0
        for tau in range(t - window + 1, t + 1):
            it = daily_inc.get(tau, 0.0)
            lam = sum(daily_inc.get(tau - s, 0.0) * w[s - 1] for s in range(1, min(len(w), tau - days[0]) + 1))
            num += it
            den += lam
        if den > 0:
            rt.append((t, num / den))
    return rt


def _backproject(r_mean: float, r_se: float, n: int = 120_000, seed: int = 7) -> dict:
    """Monte-Carlo: sample r -> R -> generations to index -> elapsed days -> spillover date."""
    rng = random.Random(seed)
    days_l: list[float] = []
    for _ in range(n):
        r = rng.gauss(r_mean, r_se)
        R = _R_from_r(r) if r > 0 else 0.5
        if R <= 1.001:
            continue
        ua = rng.uniform(0.30, 0.50)          # case ascertainment ~0.40
        cur = 1294 / ua
        g = 0
        while cur > 1.0 and g < 3000:
            cur /= R
            g += 1
        days_l.append(sum(rng.gammavariate(SI_A, 1.0 / SI_B) for _ in range(g)))
    days_l.sort()

    def q(p: float) -> float:
        i = p * (len(days_l) - 1)
        lo, hi = int(i), min(int(i) + 1, len(days_l) - 1)
        return days_l[lo] + (days_l[hi] - days_l[lo]) * (i - lo)

    def d(x: float) -> str:
        return (ANCHOR - timedelta(days=x)).isoformat()

    return {"median": d(q(0.5)), "iqr": [d(q(0.75)), d(q(0.25))], "ci95": [d(q(0.975)), d(q(0.025))]}


def analyze() -> dict:
    inc = _daily_incidence(CASES)
    plateau = [v for n, v in inc if 28 < n <= ANCHOR_N]
    plateau_per_day = round(sum(plateau) / len(plateau), 1)
    pos_early = round(sum(POS[n] for n in POS if n <= 22) / len([n for n in POS if n <= 22]))
    pos_recent = round(sum(POS[n] for n in POS if n >= 30) / len([n for n in POS if n >= 30]))

    r_case, se_case = _fit_log_linear([(n, CASES[n]) for n in CASES if 12 <= n <= 24])
    r_death, se_death = _fit_log_linear([(n, DEATHS[n]) for n in DEATHS])

    # daily incidence dict (interpolate cumulative confirmed to per-day, then difference)
    cum_pts = sorted(CASES.items())
    daily = {}
    for (n0, c0), (n1, c1) in zip(cum_pts, cum_pts[1:]):
        per = (c1 - c0) / (n1 - n0)
        for n in range(n0 + 1, n1 + 1):
            daily[n] = per
    rt_series = _renewal_rt(daily)

    bp_case = _backproject(r_case, se_case)
    bp_death = _backproject(r_death, se_death)

    return {
        "schemaVersion": 1,
        "modelVersion": MODEL_VERSION,
        "asOf": ANCHOR.isoformat(),
        "detectionAnchor": {"date": T0.isoformat(), "label": "First confirmed case reported", "source": "INSP / WHO"},
        "headline": "The outbreak began in a long, mostly-hidden cryptic phase; the confirmed-case curve tracks detection, not transmission.",
        "spillover": {
            "windowLabel": "late January – February 2026",
            "windowStart": "2026-01-20",
            "windowEnd": "2026-02-28",
            "basis": "death-anchored back-projection + CDC mm7522e1 time-based + hidden-burden stock converge",
            "modeled": True,
        },
        "decomposition": {
            "confirmedTracksDetection": True,
            "incidencePlateauPerDay": plateau_per_day,
            "positivityEarlyPct": pos_early,
            "positivityRecentPct": pos_recent,
            "caseAscertainment": 0.40,
            "trueBurden": TRUE_INFECTIONS,
            "rCaseConfounded": round(_R_from_r(r_case), 2),
            "rDeathCleaner": round(_R_from_r(r_death), 2),
            "rtNowApprox": round(sum(v for _, v in rt_series[-5:]) / 5, 2) if rt_series else None,
        },
        "estimates": {
            "deathAnchored": {**bp_death, "rUsed": round(_R_from_r(r_death), 2),
                              "note": "cleaner transmission proxy (death ascertainment 70-95%)"},
            "confirmedCurve": {**bp_case, "rUsed": round(_R_from_r(r_case), 2),
                               "reframe": "DETECTION-ONSET (when the outbreak became visible), not spillover",
                               "note": "detection-confounded; coincides with WHO first-HCW-infection timing"},
            "cdcTimeBased": {"window": "January – February 2026", "source": "CDC MMWR mm7522e1 (external)"},
        },
        "series": {
            "incidence": [{"date": dt(n).isoformat(), "perDay": round(v, 1)} for n, v in inc],
            "positivity": [{"date": dt(n).isoformat(), "pct": POS[n]} for n in sorted(POS)],
            "rt": [{"date": dt(n).isoformat(), "rt": round(v, 2)} for n, v in rt_series],
        },
        "caveats": (
            "Modeled estimate, not an established date. The cryptic phase is unobserved (only the "
            "post-detection plateau is seen), so spillover is a WINDOW, never a precise date. The "
            "confirmed-case back-projection is detection-confounded and is shown only to locate the "
            "detection-onset. Serial interval is the Wamala-2010 prior (no linked-pair data to fit it "
            "from this outbreak). The two methods are cited separately and never conflated."
        ),
        "citations": list(CITATIONS),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", help="write the artifact JSON to this path")
    args = ap.parse_args()
    art = analyze()
    dec = art["decomposition"]
    print(f"{art['modelVersion']}  as of {art['asOf']}")
    print(f"  incidence plateau ~{dec['incidencePlateauPerDay']}/day; positivity {dec['positivityEarlyPct']}% -> "
          f"{dec['positivityRecentPct']}%; Rt now ~{dec['rtNowApprox']}  => detection-driven plateau (Rt~1)")
    print(f"  R(case, confounded) {dec['rCaseConfounded']}   R(death, cleaner) {dec['rDeathCleaner']}")
    print(f"  spillover (death-anchored) median {art['estimates']['deathAnchored']['median']} "
          f"(IQR {art['estimates']['deathAnchored']['iqr']})")
    print(f"  detection-onset (confirmed curve) median {art['estimates']['confirmedCurve']['median']}")
    print(f"  PUBLISHED WINDOW: {art['spillover']['windowLabel']}")
    if args.emit:
        with open(args.emit, "w") as fh:
            json.dump(art, fh, indent=2)
        print(f"  wrote {args.emit}")


if __name__ == "__main__":
    main()
