# SPDX-License-Identifier: Apache-2.0
"""Bidirectional bridge between LOVS canonical zone_ids and INRB-UMIE canonical Noms.

This module is the single source of truth for cross-vocabulary zone alignment
between this repo and the INRB-UMIE Bundibugyo Ebola consortium release at
https://github.com/INRB-UMIE/Ebola_DRC_2026.

Two-stage alias resolution pipeline:

1. Upstream INRB-UMIE collapse: apply `data/aliases.csv` in the INRB-UMIE
   tarball to fold spelling variants into the INRB canonical Nom. For example
   the raw INSP row labelled `Nyankunde` collapses to canonical `Nyakunde`.
   This stage is the responsibility of the INSP loader, which reads
   `aliases.csv` from the tarball it parses.

2. LOVS bridge (this module): map INRB canonical Nom to LOVS canonical
   zone_id. For example INRB canonical `Nyakunde` maps to LOVS canonical
   `nyankunde` (LOVS chose the WHO-Sitrep spelling at first ingest).

The pipeline order MATTERS: applying the LOVS bridge before the upstream
collapse would silently lose data on a raw `Nyankunde` row that the bridge
does not know about.

Stdlib only. No clock, no network. Functions are pure.
"""
from __future__ import annotations

import json
import pathlib
from typing import Iterable, Mapping


SCHEMA_VERSION = "lovs-inrb-zone-bridge/v1"

DEFAULT_BRIDGE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "data"
    / "lovs_zone_alias_bridge.json"
)


class ZoneAliasBridgeError(ValueError):
    """Raised when the alias bridge cannot be constructed or queried safely."""


class ZoneAliasBridge:
    """Immutable bidirectional zone-id bridge.

    The instance is constructed from a `lovs_to_inrb` mapping; the inverse
    is derived automatically. Both directions are dict lookups.

    A vendored copy of the INRB-UMIE upstream `aliases.csv` (Stage 1
    collapse) is carried alongside the bridge, because the upstream
    GitHub-release tarball does NOT ship that file. Callers can override
    via the `upstream_aliases` constructor argument.
    """

    __slots__ = ("_lovs_to_inrb", "_inrb_to_lovs", "_inrb_upstream_aliases")

    def __init__(
        self,
        lovs_to_inrb: Mapping[str, str],
        *,
        inrb_upstream_aliases: Mapping[str, str] | None = None,
    ) -> None:
        if not lovs_to_inrb:
            raise ZoneAliasBridgeError("lovs_to_inrb must be non-empty")
        # Detect duplicate INRB targets which would break the inverse map.
        inverse: dict[str, str] = {}
        for lovs_id, inrb_nom in lovs_to_inrb.items():
            if not isinstance(lovs_id, str) or not lovs_id:
                raise ZoneAliasBridgeError(f"lovs_id must be non-empty str: {lovs_id!r}")
            if not isinstance(inrb_nom, str) or not inrb_nom:
                raise ZoneAliasBridgeError(
                    f"inrb_nom must be non-empty str for lovs_id={lovs_id!r}"
                )
            if inrb_nom in inverse:
                raise ZoneAliasBridgeError(
                    f"duplicate INRB Nom {inrb_nom!r} maps to both "
                    f"{inverse[inrb_nom]!r} and {lovs_id!r}; bridge must be 1-to-1"
                )
            inverse[inrb_nom] = lovs_id
        self._lovs_to_inrb: Mapping[str, str] = dict(lovs_to_inrb)
        self._inrb_to_lovs: Mapping[str, str] = inverse
        self._inrb_upstream_aliases: Mapping[str, str] = dict(
            inrb_upstream_aliases or {}
        )

    @classmethod
    def load_default(cls, path: pathlib.Path | None = None) -> "ZoneAliasBridge":
        """Load the bridge from the maintained data file (default location)."""
        bridge_path = path if path is not None else DEFAULT_BRIDGE_PATH
        try:
            raw = json.loads(bridge_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ZoneAliasBridgeError(
                f"alias bridge file not found at {bridge_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ZoneAliasBridgeError(
                f"alias bridge file at {bridge_path} is not valid JSON: {exc}"
            ) from exc
        schema = raw.get("_schema")
        if schema != SCHEMA_VERSION:
            raise ZoneAliasBridgeError(
                f"alias bridge at {bridge_path} declares schema {schema!r}; "
                f"this module supports {SCHEMA_VERSION!r}"
            )
        return cls(
            raw.get("lovs_to_inrb") or {},
            inrb_upstream_aliases=raw.get("inrb_upstream_aliases") or {},
        )

    def inrb_for(self, lovs_id: str) -> str | None:
        """Return the INRB canonical Nom for a LOVS zone_id, or None if absent."""
        return self._lovs_to_inrb.get(lovs_id)

    def lovs_for(self, inrb_nom: str) -> str | None:
        """Return the LOVS zone_id for an INRB canonical Nom, or None if absent."""
        return self._inrb_to_lovs.get(inrb_nom)

    def all_lovs_ids(self) -> tuple[str, ...]:
        """Sorted tuple of all LOVS zone_ids known to the bridge."""
        return tuple(sorted(self._lovs_to_inrb))

    def all_inrb_noms(self) -> tuple[str, ...]:
        """Sorted tuple of all INRB Noms known to the bridge."""
        return tuple(sorted(self._inrb_to_lovs))

    def inrb_upstream_aliases(self) -> Mapping[str, str]:
        """Vendored snapshot of INRB-UMIE upstream `aliases.csv`.

        Used by the INSP loader for Stage 1 collapse when the artifact does
        not ship its own `data/aliases.csv` (the GitHub-release tarball
        does not). Returns a dict copy each call so callers cannot mutate
        the bridge's internal state.
        """
        return dict(self._inrb_upstream_aliases)

    def round_trip_ok(self, lovs_ids: Iterable[str] | None = None) -> bool:
        """True if every LOVS id round-trips via INRB and back to itself.

        Useful as a self-check in tests and as a defensive guard before
        feeding the bridge into a per-zone loader.
        """
        targets = tuple(lovs_ids) if lovs_ids is not None else self.all_lovs_ids()
        for lovs_id in targets:
            inrb = self.inrb_for(lovs_id)
            if inrb is None:
                return False
            if self.lovs_for(inrb) != lovs_id:
                return False
        return True
