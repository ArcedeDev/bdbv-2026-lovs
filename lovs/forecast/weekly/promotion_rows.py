# SPDX-License-Identifier: Apache-2.0
"""Vintage rows and archive records from LOVS's reviewed SitRep promotions.

Numbers only, through spec.ARCHIVE_FIELDS. No string a promotion carries is read except
the province and zone names, which must be on the frozen lists, and the dates. Basis
notes become codes derived from structured fields, never from the promotion's text.
The row shape is the 11-07 extract's (tools/extract_operational_series.row_from_packet).
"""
from __future__ import annotations

from lovs.forecast.weekly import spec


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def publication_day(value) -> str | None:
    return str(value)[:10] if value else None


def basis_flags(promo: dict) -> list[str]:
    f = _dict(promo.get("figures"))
    flags = []
    alerts = _list(_dict(f.get("operational_tables")).get("alerts_by_province"))
    if alerts:
        printed = {r.get("province") for r in alerts if isinstance(r, dict)}
        if any(p not in printed for p in spec.TRACKED_PROVINCES):
            flags.append(spec.FLAG_ALERTS_PROVINCE_ABSENT)
    if _dict(f.get("lab_indicators_24h")).get("positivity_percent_basis") == "withheld_source_conflict":
        flags.append(spec.FLAG_LAB_DENOMINATOR_WITHHELD)
    for field in _list(f.get("withheld_source_conflict_fields")):
        if field in spec.WITHHELD_FIELD_MAP:
            flags.append(f"withheld:{spec.WITHHELD_FIELD_MAP[field]}")
    return sorted(set(flags))


def row_from_promotion(promo: dict) -> dict:
    """One vintage row. Keys and values match row_from_packet's on every shared field."""
    f = _dict(promo.get("figures"))
    ot = _dict(f.get("operational_tables"))
    lab = _dict(f.get("lab_indicators_24h"))
    split = {}
    for r in _list(f.get("province_table")):
        if isinstance(r, dict) and r.get("province") in spec.PROVINCE_NAMES:
            split[r["province"]] = {k: _num(r.get(k)) for k in spec.PROVINCE_SPLIT_FIELDS}
    row = {
        "sitrep": str(promo.get("sitrep_number")).zfill(3),
        "data_as_of": promo.get("data_as_of"),
        "published_at": publication_day(promo.get("published_at")),
        "confirmed_total": _num(f.get("cumul_cas_confirmes_drc")),
        "confirmed_deaths_total": _num(f.get("cumul_deces_parmi_confirmes_drc")),
        "cumulative_recovered": _num(f.get("gueris")),
        "health_zones_touched": _num(_dict(f.get("affected_health_zone_footprint")).get("affected")),
        "contact_followup_percent": _num(f.get("contact_followup_rate_pct")),
        "alerts_reported": _num(_dict(ot.get("alerts_total")).get("alerts_reported")),
        "new_confirmed_today": _num(f.get("new_confirmed_24h")),
        "new_confirmed_deaths_today": _num(f.get("new_confirmed_deaths_24h")),
        "hospital_isolation_total": _num(f.get("patients_en_isolement_hospitalisation")),
        "samples_analyzed": _num(lab.get("samples_analyzed")),
        "samples_positive": _num(lab.get("samples_positive")),
        "lab_positivity_percent": _num(lab.get("positivity_percent")),
        "province_split": dict(sorted(split.items())),
        "flags": basis_flags(promo),
        "source": "promotion",
    }
    return row


def row_from_extract(row: dict) -> dict:
    """An extract row reduced to the vintage allowlist (numbers, dates, frozen names)."""
    split = {}
    for name, v in sorted(_dict(row.get("province_split")).items()):
        if name in spec.PROVINCE_NAMES:
            split[name] = {k: _num(_dict(v).get(k)) for k in spec.PROVINCE_SPLIT_FIELDS}
    out = {"sitrep": str(row.get("sitrep")).zfill(3), "data_as_of": row.get("data_as_of"),
           "published_at": publication_day(row.get("published_at"))}
    out.update({k: _num(row.get(k)) for k in spec.VINTAGE_FIELDS})
    out["province_split"] = split
    out["flags"] = []
    out["source"] = "extract"
    return out


def compare_rows(a: dict, b: dict) -> list[str]:
    """Shared numeric fields (both non-null) whose values differ, by path."""
    out = []
    for k in spec.VINTAGE_FIELDS:
        x, y = _num(a.get(k)), _num(b.get(k))
        if x is not None and y is not None and abs(float(x) - float(y)) > 1e-9:
            out.append(k)
    pa, pb = _dict(a.get("province_split")), _dict(b.get("province_split"))
    for name in sorted(set(pa) & set(pb)):
        for k in spec.PROVINCE_SPLIT_FIELDS:
            x, y = _num(_dict(pa[name]).get(k)), _num(_dict(pb[name]).get(k))
            if x is not None and y is not None and abs(float(x) - float(y)) > 1e-9:
                out.append(f"province_split.{name}.{k}")
    if a.get("data_as_of") != b.get("data_as_of"):
        out.append("data_as_of")
    return out


def zone_records(promo: dict) -> list[dict]:
    f = _dict(promo.get("figures"))
    out, seen = [], set()
    for r in _list(_dict(f.get("health_zone_table")).get("rows")):
        if not isinstance(r, dict) or r.get("zone") not in spec.ZONE_NAMES or r.get("province") not in spec.PROVINCE_NAMES:
            continue
        if (r["province"], r["zone"]) in seen:
            continue  # a zone printed twice keeps its first row, as source_value reads it
        seen.add((r["province"], r["zone"]))
        rec = {"sitrep": str(promo.get("sitrep_number")).zfill(3), "data_as_of": promo.get("data_as_of"),
               "province": r["province"], "zone": r["zone"]}
        rec.update({k: _num(r.get(k)) for k in spec.ZONE_FIELDS})
        out.append(rec)
    return out


def covariate_records(promo: dict) -> list[dict]:
    f = _dict(promo.get("figures"))
    ot = _dict(f.get("operational_tables"))
    base = {"sitrep": str(promo.get("sitrep_number")).zfill(3), "data_as_of": promo.get("data_as_of")}
    out = []
    for table, fields in spec.PROVINCE_TABLE_FIELDS.items():
        seen = set()
        for r in _list(ot.get(table)):
            if isinstance(r, dict) and r.get("province") in spec.PROVINCE_NAMES and r["province"] not in seen:
                seen.add(r["province"])  # first row per province, as source_value reads it
                values = {k: _num(r.get(k)) for k in fields}
                if any(v is not None for v in values.values()):
                    out.append({**base, "table": table, "province": r["province"], **values})
    for table, fields in spec.NATIONAL_TABLE_FIELDS.items():
        values = {k: _num(_dict(ot.get(table)).get(k)) for k in fields}
        if any(v is not None for v in values.values()):
            out.append({**base, "table": table, **values})
    return out


def source_value(promo: dict, record: dict, key: str):
    """The promotion's value for one archived field of a zone or covariate record."""
    f = _dict(promo.get("figures"))
    if "zone" in record:
        for r in _list(_dict(f.get("health_zone_table")).get("rows")):
            if isinstance(r, dict) and r.get("zone") == record["zone"] and r.get("province") == record["province"]:
                return _num(r.get(key))
        return None
    table = record.get("table")
    ot = _dict(f.get("operational_tables"))
    if "province" in record:
        for r in _list(ot.get(table)):
            if isinstance(r, dict) and r.get("province") == record["province"]:
                return _num(r.get(key))
        return None
    return _num(_dict(ot.get(table)).get(key))
