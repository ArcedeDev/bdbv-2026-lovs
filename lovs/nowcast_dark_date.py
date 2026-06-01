"""Dark-date detector for the standing scored nowcast.

Spec: ``labs/lovs-public-goods/latency-nowcast-spec.md`` v0.4, section 11.

A "dark date" is the most recent calendar date for which no archived
authoritative edition has yet reported a `data_as_of` value matching that
date. It is the natural target of today's standing nowcast: every published
nowcast claims a point + intervals for the dark-date's headline count and
auto-resolves when an edition carrying that `data_as_of` arrives.

This is the Phase 1 scaffold per the engineering-plan in
``.process/2026-05-26-pre-refresh-decisions/follow-up-after-pushback.md``.
The function signature, the data-as-of source-of-truth read, and the
basic detector loop are implemented; Phase 2 adds the recency-weighted
delay distribution that informs the resolution deadline, and Phase 3
adds the website surface.

No model fitting is done here; this is purely a calendar-walk over the
manifest's per-edition `data_as_of` values.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from collections.abc import Iterable
from typing import Final

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_PATH: Final = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"
NOWCAST_LEDGER_PATH: Final = REPO_ROOT / "data" / "nowcast-ledger.json"


def load_published_data_as_of_dates(manifest_path: pathlib.Path = MANIFEST_PATH) -> set[dt.date]:
    """Read every archived edition's `data_as_of` from the manifest.

    The manifest's per-entry `normalized_content.data_as_of` is the source of truth
    for the date a number is true. Editions that do not carry a `data_as_of` value
    (e.g. landing pages without a dated tuple) are skipped silently here; the spec
    section 5.1 normalization is a separate concern.
    """
    if not manifest_path.exists():
        return set()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: set[dt.date] = set()
    for entry in manifest.get("entries", []):
        normalized = entry.get("normalized_content") or {}
        raw = normalized.get("data_as_of")
        if not raw:
            continue
        try:
            out.add(dt.date.fromisoformat(raw[:10]))
        except (TypeError, ValueError):
            continue
    return out


def detect_dark_date(
    *,
    as_of: dt.date,
    published_dates: Iterable[dt.date] | None = None,
    lookback_days: int = 7,
) -> dt.date | None:
    """Return the most recent calendar date <= as_of with no published edition.

    Walks backwards from ``as_of`` for up to ``lookback_days`` days. Returns the
    first date whose ``data_as_of`` does not appear in ``published_dates``, which
    is the dark date for today's nowcast. Returns ``None`` if every date in the
    window is already covered (no dark date this cycle).

    The lookback bound prevents the detector from running off into the deep past
    if the manifest has a long sparse gap; per spec section 11, dark dates older
    than a few days are no longer the live nowcast target, they would just be
    resolved by their own backlog of editions.
    """
    if as_of is None:
        raise ValueError("as_of is required")
    covered = set(published_dates) if published_dates is not None else load_published_data_as_of_dates()
    for offset in range(lookback_days + 1):
        candidate = as_of - dt.timedelta(days=offset)
        if candidate not in covered:
            return candidate
    return None


def main() -> int:
    """CLI: print the dark date for today's snapshot."""
    today = dt.date.today()
    published = load_published_data_as_of_dates()
    dark = detect_dark_date(as_of=today, published_dates=published)
    print(f"as_of: {today.isoformat()}")
    print(f"published data_as_of count: {len(published)}")
    print(f"most recent published: {max(published).isoformat() if published else 'none'}")
    print(f"dark date: {dark.isoformat() if dark else 'none (all covered)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
