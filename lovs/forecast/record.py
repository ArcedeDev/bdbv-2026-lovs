"""The unified scored-forecast record: one table, every system.

WHY THIS EXISTS. Three forecasting systems now run in this programme and each
stores its scored outcomes in its own schema: the corridor ledger, the
operational ledger, and the IDB Track B record in a separate repository. Every
one of them can report its own Brier. None of them can be pooled, and pooling is
the precondition for everything worth doing next.

The reason is not tidiness. A calibration map -- the function from what a
forecaster SAYS to what actually HAPPENS -- can only be fitted on the pooled
corpus, because no single system will reach the sample size on its own for
months. The Track B scoring already showed the shape that map would correct:
resolution 0.173 against reliability 0.166, which is a forecaster whose ranking
carries real information and whose prices are wrong. Recalibration converts
almost all of a reliability loss straight into skill. It does not need a better
forecaster; it needs the one we have, repriced.

WHAT A ROW IS. One resolved forecast: the probability as registered, the binary
outcome, and the context needed to ask WHEN the forecaster is trustworthy rather
than only whether it is on average. The context columns are the point. A pooled
Brier is a number; a pooled Brier sliced by method, band, series length and
question shape is a model of your own reliability.

APPEND-ONLY, AND NEVER REWRITTEN. A row enters when its forecast resolves and is
never edited afterwards. The probability is the one that was registered before
the outcome was known; that is the entire value of the corpus and the one thing
that cannot be reconstructed later.

Stdlib only. Deterministic. No clock of its own.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CORRIDOR_LEDGER = REPO / "data" / "calibration-ledger.json"
OPERATIONAL_LEDGER = REPO / "data" / "operational-calibration-ledger.json"
# The IDB harness lives in a sibling repository. Absence is normal and must not
# be an error: the record is built from whatever systems are reachable, and says
# which ones it used.
IDB_TRACK_B = (REPO.parent.parent / "idb-validation" / "harness" / "data"
               / "track-b-record.json")
RESEARCH_STORE = REPO / "data" / "research-store-resolved-2026-09-01.json"


@dataclass(frozen=True, slots=True)
class ScoredForecast:
    """One resolved forecast, in the schema every system maps onto."""

    system: str
    block_id: str
    forecast_id: str
    probability: float
    outcome: int
    registered_at: str
    resolves_at: str
    horizon_days: int | None
    method: str
    metric: str
    question_shape: str
    n_observations_at_pin: int | None
    bias_test: bool
    geography_class: str | None

    @property
    def brier(self) -> float:
        return (self.probability - self.outcome) ** 2

    @property
    def band(self) -> str:
        """Decile label. The unit a calibration map is read in."""
        return f"{int(min(self.probability, 0.999) * 10) / 10:.1f}"


def _corridor_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for block in doc.get("blocks", []):
        for point in block.get("points", []):
            if "outcome" not in point:
                continue
            lo, hi = point["risk_adj_50"]
            out.append(ScoredForecast(
                system="corridor", block_id=block["block_id"],
                forecast_id=point["hypothesis_id"],
                probability=(lo + hi) / 2.0, outcome=int(point["outcome"]),
                registered_at=block["pinned_at"], resolves_at=block["resolves_at"],
                horizon_days=point.get("horizon_days"),
                method="corridor_monte_carlo", metric="target_first_confirmation",
                question_shape="ever_in_window",
                n_observations_at_pin=None, bias_test=False,
                geography_class=point.get("geography_class"),
            ))
    return out


def _operational_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for block in doc.get("blocks", []):
        for pin in block.get("points", []):
            if "outcome" not in pin:
                continue
            out.append(ScoredForecast(
                system="operational", block_id=block["block_id"],
                forecast_id=pin["pin_id"],
                probability=float(pin["probability"]), outcome=int(pin["outcome"]),
                registered_at=block["pinned_at"], resolves_at=block["resolves_at"],
                horizon_days=pin.get("horizon_days"),
                method=pin.get("method", "level_bootstrap"),
                metric=pin["metric"], question_shape=pin.get("shape", "unknown"),
                n_observations_at_pin=pin.get("observations_at_pin"),
                bias_test=bool(pin.get("bias_test")), geography_class=None,
            ))
    return out


def _track_b_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries", [])
    if not entries:
        return []
    # The record is append-only and each entry re-scores the whole set, so the
    # last entry is the current view. Earlier entries are history, not extra
    # observations, and pooling them would count one forecast many times.
    latest = entries[-1]
    return [
        ScoredForecast(
            system="idb_track_b", block_id=f"track-b:{latest['scored_at']}",
            forecast_id=row["hypothesis_id"],
            probability=float(row["confidence"]), outcome=int(row["outcome"]),
            registered_at="2026-05-19", resolves_at=latest["scored_at"],
            horizon_days=None, method=f"engine_{row.get('tier', 'unknown')}",
            metric=row.get("hypothesis_class", "unknown"),
            question_shape="engine_hypothesis", n_observations_at_pin=None,
            bias_test=False, geography_class=None,
        )
        for row in latest.get("per_hypothesis", [])
    ]


def _research_store_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [
        ScoredForecast(
            system="research_store", block_id=row["scope_id"],
            forecast_id=row["hypothesis_id"],
            probability=float(row["probability"]), outcome=int(row["outcome"]),
            registered_at=str(row.get("created_at") or "")[:10],
            resolves_at=str(row.get("resolves_at") or "")[:10],
            horizon_days=None,
            method=f"engine_{row.get('resolution_type', 'unknown')}",
            metric=row.get("hypothesis_class") or "unknown",
            question_shape="engine_hypothesis",
            n_observations_at_pin=None, bias_test=False, geography_class=None,
        )
        for row in doc.get("rows", [])
    ]


def build(corridor: Path | None = None, operational: Path | None = None,
          track_b: Path | None = None, research_store: Path | None = None) -> dict:
    """Pool every reachable system into one table.

    Reports which sources were found, so a thin corpus is never mistaken for a
    complete one.
    """
    sources = {
        "corridor": corridor or CORRIDOR_LEDGER,
        "operational": operational or OPERATIONAL_LEDGER,
        "idb_track_b": track_b or IDB_TRACK_B,
        "research_store": research_store or RESEARCH_STORE,
    }
    # Order matters: the first source to claim a forecast_id wins. The research
    # store is the live authority and goes first, because the IDB Track B
    # snapshot was CAPTURED FROM it on 2026-05-19 and re-states the same
    # hypotheses under the same ids. Pooling both without dedup would
    # systematically double-count exactly the rows the two systems share, and
    # would do it silently -- the pooled n would simply look larger.
    candidates = (_research_store_rows(sources["research_store"])
                  + _corridor_rows(sources["corridor"])
                  + _operational_rows(sources["operational"])
                  + _track_b_rows(sources["idb_track_b"]))
    rows: list[ScoredForecast] = []
    seen: set[str] = set()
    duplicates: list[dict] = []
    for row in candidates:
        if row.forecast_id in seen:
            duplicates.append({"forecast_id": row.forecast_id, "dropped_from": row.system})
            continue
        seen.add(row.forecast_id)
        rows.append(row)
    rows.sort(key=lambda r: (r.registered_at, r.forecast_id))
    seen = {name: sources[name].exists() for name in sources}
    return {
        "_meta": {
            "schema_version": 1,
            "purpose": ("Pooled append-only record of every resolved forecast across the "
                        "programme's systems. The substrate for calibration fitting, which "
                        "no single system will have the sample size for on its own."),
            "sources_found": seen,
            "sources_missing": [k for k, v in seen.items() if not v],
            "row_count": len(rows),
            "systems": sorted({r.system for r in rows}),
            "duplicates_dropped": len(duplicates),
            "duplicate_detail": duplicates,
            "dedup_note": ("Deduplicated by forecast_id. The IDB Track B snapshot was captured "
                           "from the research store, so the two overlap by construction; the "
                           "research store wins as the live authority."),
        },
        "rows": [asdict(r) for r in rows],
    }


def summary(record: dict) -> dict:
    """Pooled counts and Brier, sliced the ways a calibration map is read."""
    rows = [ScoredForecast(**r) for r in record["rows"]]
    if not rows:
        return {"n": 0}
    base = sum(r.outcome for r in rows) / len(rows)

    def slice_by(key):
        out: dict[str, dict] = {}
        for row in rows:
            bucket = out.setdefault(str(getattr(row, key)), {"n": 0, "brier": 0.0, "hits": 0})
            bucket["n"] += 1
            bucket["brier"] += row.brier
            bucket["hits"] += row.outcome
        for bucket in out.values():
            bucket["brier"] = round(bucket["brier"] / bucket["n"], 4)
            bucket["observed_rate"] = round(bucket["hits"] / bucket["n"], 4)
        return dict(sorted(out.items()))

    return {
        "n": len(rows),
        "base_rate": round(base, 4),
        "brier": round(sum(r.brier for r in rows) / len(rows), 4),
        "base_rate_brier": round(sum((base - r.outcome) ** 2 for r in rows) / len(rows), 4),
        "by_system": slice_by("system"),
        "by_method": slice_by("method"),
        "by_band": slice_by("band"),
    }
