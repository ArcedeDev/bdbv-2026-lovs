# SPDX-License-Identifier: Apache-2.0
"""Spec v1 of the weekly forecast loop: every rule the loop is judged by, in one place.

A change to anything here (series, steps, horizons, methods, the ladder set, the pool,
statuses, the calendar, settle and anomaly rules, the archive allowlist or the closed
vocabulary) is a spec revision: founder-approved, with a new SPEC_VERSION, a new pinned
digest in tests/test_weekly_hygiene.py and new method ids. Candidates never join the
ladder set or the pool.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass

SPEC_VERSION = "wk1"
OUTBREAK = "bdbv-uga-cod-2026"
ID_PREFIX = "bdbv-2026-wk-"
# The store's file names, public and private (DESIGN.md 2.7).
PUBLIC_FILES = ("manifest.json", "pins.csv", "commitment.json", "vintage-delta.jsonl", "zones-delta.jsonl",
                "province-covariates-delta.jsonl")
PRIVATE_FILES = ("forecasts.json",)
REPOSITORY = "ArcedeDev/bdbv-2026-lovs"

# The 23 hub quantile levels (FluSight, the US and European hubs).
QUANTILE_LEVELS: tuple[float, ...] = (
    0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
    0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.975, 0.99,
)
HORIZONS: tuple[int, ...] = (7, 14, 21, 28)
PIN_LEVELS: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)
COVERAGE_LEVELS: tuple[float, ...] = (0.5, 0.8, 0.9, 0.95)

PATHS = 20000
BLOCK = 5
DRIFT_DAYS = 28
LEVELS_DAYS = 21
PERSISTENCE_WEEKS = 8
TRAJECTORIES_HASHED = 100

LAG_CAP = 14
LAG_HISTORY_DAYS = 14          # N7 methods read lags of data days up to R - 14
CUTOFF_MIN_LAG = 3             # the cutoff is at most R - 3
RECENCY_DAYS = 3               # a series with no observation in the 3 days before the cutoff is skipped
MEAN_MIN_DAYS = 5              # N5 and N6 need 5 of 7 reported days
SETTLE_DAYS = 14               # settled value: origin/main at target end + 14 days, 23:59:59Z
UNSCOREABLE_AFTER_DAYS = 28    # a boundary still missing at end + 28 is unscoreable_span
ANOMALY_REL = 0.05             # settled against first-published: over 5% and over one pin step
PROVINCE_ACTIVE_DAYS = 28
PROVINCE_ACTIVE_MIN_CASES = 20
MIN_HISTORY_OBS = 30

BUILD_LIMIT_UTC = dt.time(20, 0)
GO_REQUEST_ALARM_UTC = dt.time(18, 30)
REGISTER_BY_UTC = dt.time(23, 15)


@dataclass(frozen=True, slots=True)
class Series:
    id: str
    label: str
    field: str          # vintage row field, or "publication_lag"
    kind: str           # flow | endlevel | mean | lag
    step: int           # pin threshold step
    cls: str            # flow (A drift, B level) | level (A level, B recent levels)
    bound: str          # flow | cumulative | percent | count | lag
    province: str | None = None
    measure: str | None = None


def _p(sid: str, province: str, measure: str, step: int) -> Series:
    what = "new confirmed cases" if measure == "confirmed" else "new confirmed deaths"
    return Series(sid, f"{what}, {province}", f"province:{province}:{measure}", "flow", step, "flow", "flow",
                  province, measure)


SERIES: tuple[Series, ...] = (
    Series("N1", "new confirmed cases", "confirmed_total", "flow", 10, "flow", "flow"),
    Series("N2", "new confirmed deaths", "confirmed_deaths_total", "flow", 5, "flow", "flow"),
    Series("N3", "new recoveries", "cumulative_recovered", "flow", 5, "flow", "flow"),
    Series("N4", "affected health zones", "health_zones_touched", "endlevel", 1, "flow", "cumulative"),
    Series("N5", "contact follow-up percent", "contact_followup_percent", "mean", 1, "level", "percent"),
    Series("N6", "alerts reported", "alerts_reported", "mean", 100, "level", "count"),
    Series("N7", "publication lag in days", "publication_lag", "lag", 1, "level", "lag"),
    _p("P1", "Ituri", "confirmed", 5), _p("P2", "Ituri", "confirmed_deaths", 5),
    _p("P3", "Nord-Kivu", "confirmed", 5), _p("P4", "Nord-Kivu", "confirmed_deaths", 5),
    _p("P5", "Haut-Uele", "confirmed", 5), _p("P6", "Haut-Uele", "confirmed_deaths", 5),
    _p("P7", "Tshopo", "confirmed", 1), _p("P8", "Tshopo", "confirmed_deaths", 1),
)
SERIES_BY_ID = {s.id: s for s in SERIES}
NATIONAL = tuple(s.id for s in SERIES if s.id.startswith("N"))
PROVINCIAL = tuple(s.id for s in SERIES if s.id.startswith("P"))
TRACKED_PROVINCES = ("Ituri", "Nord-Kivu", "Haut-Uele", "Tshopo")

# Frozen method ids. The prefix marks the floored weekly behaviour, which differs from the
# monthly blocks' methods of the same base name.
M_DRIFT = f"{SPEC_VERSION}.drift_bootstrap_28d"
M_LEVEL = f"{SPEC_VERSION}.level_bootstrap"
M_RECENT = f"{SPEC_VERSION}.recent_levels_21d"
M_PERSIST = f"{SPEC_VERSION}.persistence_8w"
M_CLIM = f"{SPEC_VERSION}.climatology_asof"
M_POOL = f"{SPEC_VERSION}.ens_pool2"
METHOD_IDS: tuple[str, ...] = (M_DRIFT, M_LEVEL, M_RECENT, M_PERSIST, M_CLIM, M_POOL)
CLASS_PAIRS = {"flow": (M_DRIFT, M_LEVEL), "level": (M_LEVEL, M_RECENT)}
# Roles label only the headline and the scoreboard; v2 reads method ids, never roles.
INITIAL_ROLES = {"flow": {"record": M_DRIFT, "challenger": M_LEVEL},
                 "level": {"record": M_LEVEL, "challenger": M_RECENT}}


def pair(series: Series) -> tuple[str, str]:
    return CLASS_PAIRS[series.cls]


def ladder_set(series: Series) -> tuple[str, str, str, str]:
    a, b = pair(series)
    return (a, b, M_PERSIST, M_CLIM)


def methods_for(series: Series) -> tuple[str, ...]:
    return ladder_set(series) + (M_POOL,)


def price_column(method: str) -> str:
    return "p_" + method.split(".", 1)[1]


def score_column(prefix: str, method: str) -> str:
    return f"{prefix}_" + method.split(".", 1)[1]


def pin_id(R: dt.date, n: int) -> str:
    return f"{ID_PREFIX}{R:%Y%m%d}-{n:03d}"


def forecast_id(R: dt.date, n: int) -> str:
    return f"{ID_PREFIX}{R:%Y%m%d}-q{n:03d}"


# Statuses. Pins take the resolver's own set plus the weekly ones; targets one of these.
PIN_STATUSES = ("resolved_yes", "resolved_no", "pending", "pending_boundary", "unscoreable_span",
                "void_decided_before", "held_basis_break")
TARGET_STATUSES = ("pending", "pending_boundary", "observed", "unscoreable_span", "held_basis_break")
VALUE_KINDS = ("first_published", "settled")
PHASES = ("running", "ready", "go_requested", "pushed", "skipped", "missed", "abandoned")
SKIP_CAUSES = ("skipped_app_closed", "skipped_no_go_request", "skipped_no_go", "skipped_gate")
ANOMALY_CODES = ("cumulative_fall", "withheld_field", "settle_revision")
SKIP_SERIES_CODES = ("no_recent_observation", "province_inactive", "short_history", "cells_incomplete")

# Basis flags carried on vintage rows, as codes only. A promotion's free text is never read.
FLAG_ALERTS_PROVINCE_ABSENT = "alerts_province_absent"
FLAG_LAB_DENOMINATOR_WITHHELD = "lab_denominator_withheld"
WITHHELD_FIELD_MAP = {
    "cumul_cas_confirmes_drc": "confirmed_total",
    "cumul_deces_parmi_confirmes_drc": "confirmed_deaths_total",
    "gueris": "cumulative_recovered",
    "patients_en_isolement_hospitalisation": "hospital_isolation_total",
    "contact_followup_rate_pct": "contact_followup_percent",
    "new_confirmed_24h": "new_confirmed_today",
    "new_confirmed_deaths_24h": "new_confirmed_deaths_today",
}

# The archive allowlist (DESIGN.md 2.5, 2.8). Numeric leaves only, from LOVS promotions.
VINTAGE_FIELDS: tuple[str, ...] = (
    "confirmed_total", "confirmed_deaths_total", "cumulative_recovered", "health_zones_touched",
    "contact_followup_percent", "alerts_reported", "new_confirmed_today", "new_confirmed_deaths_today",
    "hospital_isolation_total", "samples_analyzed", "samples_positive", "lab_positivity_percent",
)
PROVINCE_SPLIT_FIELDS: tuple[str, ...] = ("confirmed", "confirmed_deaths")
ZONE_FIELDS: tuple[str, ...] = ("confirmed", "confirmed_deaths", "cfr_percent")
PROVINCE_TABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "contacts_by_province": ("contacts_under_follow_up", "contacts_seen_24h", "followup_rate_pct"),
    "alerts_by_province": ("alerts_reported", "alerts_investigated", "investigation_rate_pct",
                           "validated_living", "validated_deaths", "invalidated_living", "invalidated_deaths",
                           "suspects_investigated", "suspects_transferred", "alerts_live", "alerts_deceased"),
    "patient_movement_by_province": ("admissions_24h", "total_exits_24h", "patients_in_isolation_end_day",
                                     "recovered_24h", "deaths_24h"),
    "care_capacity_by_province": ("patients", "beds", "occupancy_percent"),
    "laboratory_by_province": ("samples_analyzed", "samples_positive", "positivity_percent",
                               "new_confirmations"),
    "poe_poc_by_province": ("reporting_complete", "reporting_expected", "travelers_passed",
                            "screening_rate_pct", "alerts_notified"),
}
NATIONAL_TABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "alerts_total": ("alerts_reported", "alerts_investigated", "investigation_rate_pct", "suspects_investigated",
                     "suspects_transferred", "alerts_live", "alerts_deceased"),
    "contacts_total": ("contacts_under_follow_up", "contacts_seen_24h", "followup_rate_pct"),
}
ARCHIVE_FIELDS = {
    "vintage": VINTAGE_FIELDS,
    "province_split": PROVINCE_SPLIT_FIELDS,
    "zones": ZONE_FIELDS,
    "province_tables": PROVINCE_TABLE_FIELDS,
    "national_tables": NATIONAL_TABLE_FIELDS,
}

# Place names, frozen from the reviewed record on 2026-11-18. Allowed only as row keys.
PROVINCE_NAMES: tuple[str, ...] = (
    "Bas-Uele", "Haut-Uele", "Ituri", "Nord-Kivu", "Nord-Ubangi", "Sud-Kivu", "Sud-Ubangi", "Tshopo",
)
ZONE_NAMES: tuple[str, ...] = (
    "A ventiler", "Adja", "Alimbongo", "Ariwara", "Aru", "Aungba", "Autres ZS (donnees non ventilees)",
    "Autres ZS (non ventilees)", "Bafwasende", "Bambu", "Beni", "Biena", "Boga", "Boma Mangbetu", "Bulu",
    "Bunia", "Buta", "Butembo", "Dakwa", "Damas", "Drodro", "Dungu", "Fataki", "Ganga", "Gethy", "Gety",
    "Goma", "Gombari", "Isiro", "Kabondo", "Kalunguta", "Kambala", "Katwa", "Kayna", "Kilo", "Komanda",
    "Kyondo", "Lita", "Logo", "Lolwa", "Lubero", "Lubunga", "Mabalako", "Mahagi", "Makiso-Kisangani",
    "Mambasa", "Mandima", "Mangala", "Mangobo", "Manguredjipa", "Masereka", "Miti-Murhesa", "Mongbwalu",
    "Musienene", "Mutwanga", "Nia Nia", "Nia-Nia", "Nizi", "Nyankunde", "Oicha", "Pawa", "Rimba", "Rungu",
    "Rwampara", "Tchomia", "Tshopo", "Viadana", "Vuhovi", "Wamba", "Wanie-Rukula", "Wapinda", "Watsa",
    "Yakoma",
)
ARCHIVE_SOURCES = ("extract", "promotion")
EXTRACT_SOURCE_PATH = "data/operational-series-2026-11-07.json"


def spec_document() -> dict:
    """Every rule above as one canonical document; its sha256 is the spec digest."""
    return {
        "spec_version": SPEC_VERSION, "outbreak": OUTBREAK, "id_prefix": ID_PREFIX,
        "quantile_levels": QUANTILE_LEVELS, "horizons": HORIZONS, "pin_levels": PIN_LEVELS,
        "coverage_levels": COVERAGE_LEVELS, "paths": PATHS, "block": BLOCK, "drift_days": DRIFT_DAYS,
        "levels_days": LEVELS_DAYS, "persistence_weeks": PERSISTENCE_WEEKS,
        "trajectories_hashed": TRAJECTORIES_HASHED, "lag_cap": LAG_CAP, "lag_history_days": LAG_HISTORY_DAYS,
        "cutoff_min_lag": CUTOFF_MIN_LAG, "recency_days": RECENCY_DAYS, "mean_min_days": MEAN_MIN_DAYS,
        "settle_days": SETTLE_DAYS, "unscoreable_after_days": UNSCOREABLE_AFTER_DAYS,
        "anomaly_rel": ANOMALY_REL, "province_active_days": PROVINCE_ACTIVE_DAYS,
        "province_active_min_cases": PROVINCE_ACTIVE_MIN_CASES, "min_history_obs": MIN_HISTORY_OBS,
        "build_limit_utc": BUILD_LIMIT_UTC.isoformat(), "go_request_alarm_utc": GO_REQUEST_ALARM_UTC.isoformat(),
        "register_by_utc": REGISTER_BY_UTC.isoformat(),
        "series": [asdict(s) for s in SERIES], "methods": METHOD_IDS, "class_pairs": CLASS_PAIRS,
        "pool": {"id": M_POOL, "members": "class pair A and B", "weights": [0.5, 0.5]},
        "statuses": {"pin": PIN_STATUSES, "target": TARGET_STATUSES, "value_kinds": VALUE_KINDS,
                     "anomaly": ANOMALY_CODES},
        "flags": {"alerts": FLAG_ALERTS_PROVINCE_ABSENT, "lab": FLAG_LAB_DENOMINATOR_WITHHELD,
                  "withheld": WITHHELD_FIELD_MAP},
        "archive_fields": ARCHIVE_FIELDS, "province_names": PROVINCE_NAMES, "zone_names": ZONE_NAMES,
        "extract_source_path": EXTRACT_SOURCE_PATH,
        # Review fixes before the first registration (review/WEEKLY-CODE-REVIEW.md I2, I3).
        "floors": {"flow": "every method's weekly flow draws are at least 0, climatology included",
                   "cumulative": "at least the last observed level"},
        "lag_scoring": {"value_kinds": ["settled"],
                        "end_day_absent_at_settle": "observed at lag_cap"},
    }


def spec_sha256() -> str:
    canonical = json.dumps(spec_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
