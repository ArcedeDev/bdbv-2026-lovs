# SPDX-License-Identifier: Apache-2.0
"""Spec v2 (wk2) of the weekly forecast loop: every rule the loop is judged by, in one place.

wk2 is a core revision of wk1. It changes one rule: the randomised PIT is taken from the
empirical CDF of the method's own draws instead of the CDF interpolated through the 23
quantiles, which was not uniform for calibrated count and lattice-mean forecasts.

Two digests. The core digest (core_sha256) covers everything the loop is judged on: series,
steps, horizons, the target calendar, the six methods, the ladder set, ens_pool2's members
and combiner, statuses, settle and anomaly rules, the scorer, the truth reader, the archive
allowlist, the privacy gate's closed vocabulary, and the source code of every module that
prices, resolves, scores or gates a week and of the build driver (CODE_MODULES, each file's
bytes, read without running it). A
change to any of these, a bug fix in one of those modules included, is a core revision:
founder-approved, with a new SPEC_VERSION, new method ids and new pinned digests in
tests/test_weekly_hygiene.py and tests/test_weekly_round2.py; it ends the v2
pre-registration at that week. The full spec digest (spec_sha256) is the core plus the candidate
registry. Adding a candidate changes only the full digest: candidates are priced beside
the frozen set, never join the ladder set or the pool, and never change what v2 is judged on.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SPEC_VERSION = "wk2"
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
# "censored": an N7 cell whose end-day promotion is not on main at settle (never a lag of the cap).
PIN_STATUSES = ("resolved_yes", "resolved_no", "pending", "pending_boundary", "unscoreable_span",
                "void_decided_before", "held_basis_break", "censored")
TARGET_STATUSES = ("pending", "pending_boundary", "observed", "unscoreable_span", "held_basis_break", "censored")
VALUE_KINDS = ("first_published", "settled")
PHASES = ("running", "ready", "go_requested", "pushed", "skipped", "missed", "abandoned")
SKIP_CAUSES = ("skipped_app_closed", "skipped_no_go_request", "skipped_no_go", "skipped_gate")
ANOMALY_CODES = ("cumulative_fall", "withheld_field", "settle_revision", "lag_censored")
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

# The candidate registry (DESIGN.md 2.4, D9): methods priced beside the frozen set, one dict
# each. Outside the core digest; it never joins the ladder set or the pool.
CANDIDATES: tuple[dict, ...] = ()


# The code v2 is judged by: every module that prices, resolves, scores or gates a week, the import
# closure of the core modules, and the build driver (__main__), which picks the SitRep versions and
# arrival times, sets the cutoff and writes the results files; the driver's export and usefulness
# imports write only tables and reports v2 never reads (tests/test_weekly_round3.py checks the list).
# Their source bytes are in the core digest, so any change to one of these files, a bug fix included,
# is a core revision.
CODE_MODULES = (
    "lovs.forecast.weekly.__main__",
    "lovs.forecast.weekly.methods", "lovs.forecast.weekly.privacy", "lovs.forecast.weekly.promotion_rows",
    "lovs.forecast.weekly.register", "lovs.forecast.weekly.resolve", "lovs.forecast.weekly.score",
    "lovs.forecast.weekly.source", "lovs.forecast.weekly.spec", "lovs.forecast.weekly.targets",
    "lovs.forecast.weekly.truth", "lovs.forecast.weekly.vintage",
    "lovs.forecast.backtest", "lovs.forecast.cadenceintegrity", "lovs.forecast.opsforecast",
    "lovs.forecast.pins_block7", "lovs.forecast.pins_block8", "lovs.forecast.pins_block13",
    "lovs.forecast.termination", "lovs.forecast_scoring",
)


def module_source(name: str) -> bytes:
    """The source bytes of a core module, read from the file the import system loads it from. find_spec
    never runs the module, so hashing the build driver never imports it."""
    return Path(importlib.util.find_spec(name).origin).read_bytes()


def code_digests() -> dict[str, str]:
    return {name: hashlib.sha256(module_source(name)).hexdigest() for name in CODE_MODULES}


def _canonical_sha256(doc: dict) -> str:
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def core_document() -> dict:
    """Every rule the loop is judged on, as one canonical document; its sha256 is the core digest."""
    from lovs.forecast.weekly import privacy, score   # both import this module
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
        "pool": {"id": M_POOL, "members": "class pair A and B", "weights": [0.5, 0.5],
                 "combiner": "equal linear pool: the members' draws concatenated, quantiles of the union"},
        "statuses": {"pin": PIN_STATUSES, "target": TARGET_STATUSES, "value_kinds": VALUE_KINDS,
                     "anomaly": ANOMALY_CODES},
        "flags": {"alerts": FLAG_ALERTS_PROVINCE_ABSENT, "lab": FLAG_LAB_DENOMINATOR_WITHHELD,
                  "withheld": WITHHELD_FIELD_MAP},
        "archive_fields": ARCHIVE_FIELDS, "province_names": PROVINCE_NAMES, "zone_names": ZONE_NAMES,
        "extract_source_path": EXTRACT_SOURCE_PATH,
        # Review fixes before the first registration (review/WEEKLY-CODE-REVIEW.md I2, I3).
        "floors": {"flow": "every method's weekly flow draws are at least 0, climatology included",
                   "cumulative": "at least the last observed level"},
        # Round-2 review fix R2-3: the lag is read from the promotion, never from its arrival on main.
        "lag_scoring": {"value_kinds": ["settled"],
                        "lag": "end data day to the UTC date of the promotion's published_at, capped at lag_cap",
                        "end_day_absent_at_settle": "censored", "anomaly": "lag_censored",
                        "primary_endpoint": "censored cells are outside it and reported"},
        "scorer": {"version": score.SCORER_VERSION, "brier": "round((p - outcome)^2, 6)",
                   "log": "round(log(q if outcome else 1 - q), 6), q = p clipped to [0.01, 0.99]",
                   "log_clip": score.LOG_CLIP,
                   "pit": "G(y-) + u (G(y) - G(y-)), G the empirical CDF of the method's draws and y each "
                          "rounded to 6 decimals, u = Random(forecast_id|pit); the week's draws are rebuilt "
                          "from its archive and must reproduce forecasts.json byte for byte"},
        "truth_reader": {
            "first_published": "the first version on origin/main, timed by the activity record of refs/heads/main, "
                               "else by its pull request's merge time; commit dates never read",
            "settled": "the version on origin/main at target end + settle_days, 23:59:59Z, by the same arrival times",
            "rows": "reviewed promotions ready for model use only; a boundary day once its packet is on main too",
            "duplicates": "a SitRep's files by arrival, then file name"},
        "privacy_allowlist": {
            "keys": sorted(privacy.KEYS), "codes": sorted(privacy.CODES), "place_keys": sorted(privacy.PLACE_KEYS),
            "date_keys": {k: list(v) for k, v in sorted(privacy.DATE_KEYS.items())},
            "hex40_keys": sorted(privacy.HEX40_KEYS), "hex64_keys": sorted(privacy.HEX64_KEYS),
            "id_keys": sorted(privacy.ID_KEYS), "store_files": sorted(privacy.STORE_FILES),
            "max_integer": privacy.MAX_INTEGER,
            "patterns": [privacy._DATE.pattern, privacy._TIME.pattern, privacy._HEX40.pattern, privacy._HEX64.pattern,
                         privacy._WEEKLY_ID.pattern, privacy._SITREP.pattern, privacy._BRANCH.pattern,
                         privacy._STORE_PATH.pattern]},
        # Round-3 prereg review fix R3-I1: the core includes the code, so no code change can alter a
        # v2 result without ending v2.
        "code": code_digests(),
    }


def core_sha256() -> str:
    return _canonical_sha256(core_document())


def spec_document() -> dict:
    """The core and the candidate registry; its sha256 is the full spec digest."""
    return {"core": core_document(), "candidates": [dict(c) for c in CANDIDATES]}


def spec_sha256() -> str:
    return _canonical_sha256(spec_document())
