#!/usr/bin/env python3
"""Extract a dated operational series from the evidence MCP's SitRep packets.

The forecasters in lovs/forecast/ read one tidy row per SitRep. The 2026-09-01 extract
is frozen (it is the substrate the Block 6/7 pins were generated from); a resolution
needs a NEW dated extract that reaches the resolution date. This writes one, with the
row shape of the frozen file, from data/sitrep-packets/sitrep-*.json.

Usage:
  python3 tools/extract_operational_series.py --packets <lovs-evidence-mcp>/data/sitrep-packets \
      --through 2026-10-01 --out data/operational-series-2026-10-01.json

Rows are sorted by data date then SitRep number. Only model-ready packets are kept.
Missing values stay null; nothing is carried forward or summed. Stdlib only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import tempfile
from pathlib import Path


def _total(rows, key="province"):
    return next((r for r in rows or [] if (r.get(key) or "").lower() == "total"), None)


def row_from_packet(packet: dict) -> dict:
    f = packet.get("figures") or {}
    h = f.get("headline") or {}
    lab = _total(f.get("laboratory_status")) or {}
    alerts = _total(f.get("alert_management_24h")) or {}
    iso = (f.get("patient_movement") or {}).get("patients_in_isolation_end_of_day")
    split = {
        r["province"]: {"confirmed": r.get("confirmed"), "confirmed_deaths": r.get("confirmed_deaths")}
        for r in f.get("province_split") or []
        if r.get("province") and r["province"].lower() != "total"
    }
    return {
        "sitrep": str(packet.get("sitrep_label")).zfill(3),
        "published_at": packet.get("published_at"),
        "data_as_of": packet.get("data_as_of"),
        "ready_for_model_use": bool(packet.get("ready_for_model_use")),
        "confirmed_total": h.get("confirmed_total"),
        "confirmed_deaths_total": h.get("confirmed_deaths_total"),
        "confirmed_cfr_percent": h.get("confirmed_cfr_percent"),
        "new_confirmed_today": h.get("new_confirmed_today"),
        "new_confirmed_deaths_today": h.get("new_confirmed_deaths_today"),
        "new_suspects_today": h.get("new_suspects_today"),
        "health_zones_touched": h.get("health_zones_touched"),
        "provinces_touched": h.get("provinces_touched"),
        "hospital_isolation_total": h.get("hospital_isolation_total"),
        "cumulative_recovered": h.get("cumulative_recovered"),
        "contact_followup_percent": h.get("contact_followup_percent"),
        "samples_analyzed": lab.get("samples_collected_analyzed"),
        "samples_positive": lab.get("positive"),
        "lab_positivity_percent": lab.get("positivity_percent"),
        "lab_pending": lab.get("pending_results"),
        "alerts_reported": alerts.get("alerts_reported"),
        "alerts_investigated": alerts.get("alerts_investigated"),
        "alert_investigation_rate_percent": alerts.get("investigation_rate_percent"),
        "isolation_by_province": dict(sorted(iso.items())) if isinstance(iso, dict) else iso,
        "province_split": split,
    }


def extract(packets_dir: str, through: str) -> list[dict]:
    rows = []
    for path in glob.glob(os.path.join(packets_dir, "sitrep-*.json")):
        packet = json.loads(Path(path).read_text())
        if not packet.get("ready_for_model_use") or not packet.get("data_as_of"):
            continue
        if packet["data_as_of"] > through:
            continue
        rows.append(row_from_packet(packet))
    rows.sort(key=lambda r: (r["data_as_of"], int(re.sub(r"\D", "", r["sitrep"]) or 0)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--packets", required=True)
    parser.add_argument("--through", required=True, help="last data day to include (YYYY-MM-DD)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = extract(args.packets, args.through)
    doc = {
        "_meta": {
            "schema_version": 1,
            "purpose": ("Dated tidy extract of per-SitRep operational indicators for the BDBV 2026 outbreak, for "
                        "resolving the operational calibration blocks. The 2026-09-01 extract stays frozen."),
            "source": "projects/lovs-evidence-mcp/data/sitrep-packets/sitrep-*.json (INSP/INRB SitRep promotion packets)",
            "extracted_at": args.through,
            "extractor": "tools/extract_operational_series.py",
            "source_packet_count": len(rows),
            "row_count": len(rows),
            "licensing": "CC BY 4.0 (schema and extraction); underlying figures are INSP/INRB SitRep publications.",
            "reading_note": ("data_as_of is the DATA day, not the publication day. Clamp any time-since estimator to "
                             "the last data day: a reporting gap otherwise reads as observed improvement."),
        },
        "rows": rows,
    }
    out = Path(args.out)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=out.name, suffix=".tmp")
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, out)
    print(f"wrote {out} ({len(rows)} rows, through {rows[-1]['data_as_of'] if rows else 'none'})")


if __name__ == "__main__":
    main()
