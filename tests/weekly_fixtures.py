# SPDX-License-Identifier: Apache-2.0
"""Synthetic inputs for the weekly forecast loop tests.

Every number here is generated from a fixed seed. Nothing is copied from a SitRep, a
promotion or an evidence packet, so no source text can reach the public repository
through a test. The shapes follow data/sitrep_promotions/*.json (the reviewed record)
and the evidence packets that tools/extract_operational_series.row_from_packet reads.
"""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

D = dt.timedelta
START = dt.date(2026, 6, 1)
END = dt.date(2026, 11, 15)
MISSING_DAYS = frozenset({dt.date(2026, 7, 29), dt.date(2026, 11, 2)})
EXTRACT_LAST_DAY = dt.date(2026, 11, 7)
PROVINCE_SHARE = (("Ituri", 0.60), ("Nord-Kivu", 0.30), ("Haut-Uele", 0.06), ("Tshopo", 0.04))
R = dt.date(2026, 11, 25)
BUILD_TIME = "2026-11-25T12:31:00Z"
FETCHED = {
    "lovs": {"commit": "1" * 40, "fetched_at": "2026-11-25T12:30:05Z"},
    "mcp": {"commit": "2" * 40, "fetched_at": "2026-11-25T12:30:09Z"},
}
GENERATOR = {"repository": "ArcedeDev/bdbv-2026-lovs", "commit": "3" * 40}


def synthetic_rows(start: dt.date = START, end: dt.date = END, missing=MISSING_DAYS) -> list[dict]:
    """One extract-shaped row per data day, cumulative series rising, rates fluctuating."""
    rng = random.Random(20261125)
    rows, sitrep = [], 40
    cum = {"c": 1000, "d": 400, "r": 300, "z": 20}
    prov = {name: {"c": int(1000 * share), "d": int(400 * share)} for name, share in PROVINCE_SHARE}
    day = start
    while day <= end:
        new_c, new_d, new_r = rng.randint(40, 80), rng.randint(15, 35), rng.randint(5, 25)
        cum["c"] += new_c
        cum["d"] += new_d
        cum["r"] += new_r
        cum["z"] += 1 if rng.random() < 0.15 else 0
        for name, share in PROVINCE_SHARE:
            prov[name]["c"] += int(round(new_c * share + rng.uniform(-0.4, 0.4)))
            prov[name]["d"] += int(round(new_d * share + rng.uniform(-0.4, 0.4)))
        sitrep += 1
        if day not in missing:
            rows.append({
                "sitrep": str(sitrep).zfill(3),
                "published_at": (day + D(days=2)).isoformat(),
                "data_as_of": day.isoformat(),
                "ready_for_model_use": True,
                "confirmed_total": cum["c"],
                "confirmed_deaths_total": cum["d"],
                "new_confirmed_today": new_c,
                "new_confirmed_deaths_today": new_d,
                "health_zones_touched": cum["z"],
                "hospital_isolation_total": rng.randint(800, 1100),
                "cumulative_recovered": cum["r"],
                "contact_followup_percent": round(min(99.9, max(40.0, 80 + rng.uniform(-8, 8))), 1),
                "samples_analyzed": rng.randint(300, 500),
                "samples_positive": new_c,
                "lab_positivity_percent": round(100 * new_c / 400, 1),
                "alerts_reported": 3000 + rng.randint(-600, 600),
                "province_split": {name: dict(v, confirmed=v["c"], confirmed_deaths=v["d"])
                                   for name, v in prov.items()},
            })
        day += D(days=1)
    for row in rows:
        row["province_split"] = {k: {"confirmed": v["confirmed"], "confirmed_deaths": v["confirmed_deaths"]}
                                 for k, v in row["province_split"].items()}
    return rows


def promotion_from_row(row: dict, *, alerts_absent: tuple[str, ...] = (),
                       withheld: tuple[str, ...] = ()) -> dict:
    """A reviewed promotion carrying the row's numbers at the paths the real record uses."""
    provinces = [{"province": p, "new_confirmed_24h": 1, "confirmed": v["confirmed"],
                  "confirmed_deaths": v["confirmed_deaths"], "cfr_percent": 40.0,
                  "health_zones_touched": "10/36 (27,8 %)", "source_page": 2}
                 for p, v in row["province_split"].items()]
    alerts = [{"province": p, "source_page": 4, "alerts_reported": 100 + i, "validated_living": 3}
              for i, (p, _) in enumerate(PROVINCE_SHARE) if p not in alerts_absent]
    figures = {
        "cumul_cas_confirmes_drc": row["confirmed_total"],
        "cumul_deces_parmi_confirmes_drc": row["confirmed_deaths_total"],
        "gueris": row["cumulative_recovered"],
        "patients_en_isolement_hospitalisation": row["hospital_isolation_total"],
        "contact_followup_rate_pct": row["contact_followup_percent"],
        "new_confirmed_24h": row["new_confirmed_today"],
        "new_confirmed_deaths_24h": row["new_confirmed_deaths_today"],
        "province_table": provinces,
        "affected_health_zone_footprint": {
            "affected": row["health_zones_touched"], "total": 178, "provinces": 4,
            "scope_note": "synthetic footprint note that must never be archived"},
        "health_zone_table": {
            "basis": "synthetic", "rows": [
                {"province": "Ituri", "zone": "Bunia", "confirmed": 11, "confirmed_deaths": 1,
                 "cfr_percent": 9.1, "source_page": 2},
                {"province": "Nord-Kivu", "zone": "Beni", "confirmed": 7, "confirmed_deaths": 2,
                 "cfr_percent": 28.6, "source_page": 2}]},
        "operational_tables": {
            "note": "synthetic note",
            "alerts_total": {"alerts_reported": row["alerts_reported"], "source_page": 4},
            "alerts_by_province": alerts,
            "contacts_by_province": [{"province": "Ituri", "contacts_under_follow_up": 900,
                                      "contacts_seen_24h": 800, "followup_rate_pct": 88.9,
                                      "scope_note": "synthetic scope note"}],
            "challenges": [{"topic": "synthetic", "finding": "synthetic free text"}],
        },
        "lab_indicators_24h": {"samples_analyzed": row["samples_analyzed"],
                               "samples_positive": row["samples_positive"],
                               "positivity_percent": row["lab_positivity_percent"],
                               "positivity_percent_basis": "printed"},
        "withheld_source_conflict_fields": list(withheld),
        "narrative": {"challenges": ["synthetic narrative"]},
    }
    for field in withheld:
        if field in figures:
            figures[field] = None
    return {
        "schema_version": "sitrep-promotion/v1",
        "status": "reviewed",
        "sitrep_number": int(row["sitrep"]),
        "data_as_of": row["data_as_of"],
        "published_at": f"{row['published_at']}T12:00:00Z",
        "figures": figures,
        "review": {"ready_for_model_use": True, "source_review_status": "reviewed"},
    }


def packet_from_row(row: dict) -> dict:
    """An evidence-packet stand-in in the shape row_from_packet reads."""
    return {
        "sitrep_label": int(row["sitrep"]),
        "published_at": row["published_at"],
        "data_as_of": row["data_as_of"],
        "ready_for_model_use": True,
        "figures": {
            "headline": {
                "confirmed_total": row["confirmed_total"],
                "confirmed_deaths_total": row["confirmed_deaths_total"],
                "new_confirmed_today": row["new_confirmed_today"],
                "new_confirmed_deaths_today": row["new_confirmed_deaths_today"],
                "health_zones_touched": row["health_zones_touched"],
                "hospital_isolation_total": row["hospital_isolation_total"],
                "cumulative_recovered": row["cumulative_recovered"],
                "contact_followup_percent": row["contact_followup_percent"],
            },
            "laboratory_status": [{"province": "Total", "samples_collected_analyzed": row["samples_analyzed"],
                                   "positive": row["samples_positive"],
                                   "positivity_percent": row["lab_positivity_percent"]}],
            "alert_management_24h": [{"province": "Total", "alerts_reported": row["alerts_reported"]}],
            "province_split": [{"province": p, **v} for p, v in row["province_split"].items()],
        },
    }


def fixture_inputs():
    """Extract rows to EXTRACT_LAST_DAY, promotions after it, a packet for every promoted day."""
    rows = synthetic_rows()
    extract = [r for r in rows if dt.date.fromisoformat(r["data_as_of"]) <= EXTRACT_LAST_DAY]
    later = [r for r in rows if dt.date.fromisoformat(r["data_as_of"]) > EXTRACT_LAST_DAY]
    # Every row from 2026-10-01 also has a promotion, so the extract-against-promotion check runs.
    promoted = [r for r in rows if dt.date.fromisoformat(r["data_as_of"]) >= dt.date(2026, 10, 1)]
    promotions = [promotion_from_row(r, alerts_absent=("Haut-Uele",) if r["data_as_of"] == "2026-11-12" else ())
                  for r in promoted]
    packets = [packet_from_row(r) for r in promoted]
    return {"rows": rows, "extract": extract, "later": later, "promotions": promotions, "packets": packets,
            "extract_last_sitrep": int(extract[-1]["sitrep"])}


def fixture_week(tmp: Path, *, n_paths: int = 300, R_day: dt.date = R):
    """Build and write one fixture week; returns (build, public_dir, private_dir, inputs)."""
    from lovs.forecast.weekly import register, vintage

    inputs = fixture_inputs()
    cutoff = dt.date(2026, 11, 15)
    rows = vintage.build_vintage(inputs["extract"], inputs["promotions"], R_day, cutoff,
                                 extract_last_sitrep=inputs["extract_last_sitrep"])
    build = register.build_week(R_day, rows, cutoff, n_paths=n_paths, taken_seeds=frozenset(),
                                generator=GENERATOR, fetched=FETCHED, build_time=BUILD_TIME,
                                promotions=inputs["promotions"])
    public = tmp / "data" / "weekly-forecasts" / R_day.isoformat()
    private = tmp / "private" / R_day.isoformat()
    register.write_week(build, public, private, promotions=inputs["promotions"], previous_rows=None,
                        extract_rows=inputs["extract"])
    return build, public, private, inputs
