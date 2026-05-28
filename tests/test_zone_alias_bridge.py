# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.zone_alias_bridge.

The bridge is the LOVS-side half of the two-stage alias pipeline; the
INRB-UMIE-side half is handled by the INSP loader, which applies the
upstream `aliases.csv` BEFORE consulting the bridge. See Spike C in
.process/2026-05-28-insp-per-zone-and-pcr-capacity-poc/validation.md.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from lovs.zone_alias_bridge import (
    DEFAULT_BRIDGE_PATH,
    SCHEMA_VERSION,
    ZoneAliasBridge,
    ZoneAliasBridgeError,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT_CONTRACT_PATH = REPO_ROOT / "data" / "snapshot_contract.json"


class TestZoneAliasBridgeConstruction:
    def test_empty_mapping_is_rejected(self) -> None:
        with pytest.raises(ZoneAliasBridgeError, match="non-empty"):
            ZoneAliasBridge({})

    def test_duplicate_inrb_target_is_rejected(self) -> None:
        with pytest.raises(ZoneAliasBridgeError, match="duplicate INRB Nom"):
            ZoneAliasBridge({"a": "X", "b": "X"})

    def test_empty_string_keys_are_rejected(self) -> None:
        with pytest.raises(ZoneAliasBridgeError, match="non-empty"):
            ZoneAliasBridge({"": "X"})
        with pytest.raises(ZoneAliasBridgeError, match="non-empty"):
            ZoneAliasBridge({"a": ""})

    def test_non_string_keys_are_rejected(self) -> None:
        with pytest.raises(ZoneAliasBridgeError):
            ZoneAliasBridge({1: "X"})  # type: ignore[arg-type]


class TestDefaultBridge:
    def test_default_bridge_file_exists(self) -> None:
        assert DEFAULT_BRIDGE_PATH.exists(), (
            f"alias bridge data file is missing at {DEFAULT_BRIDGE_PATH}"
        )

    def test_default_bridge_loads(self) -> None:
        bridge = ZoneAliasBridge.load_default()
        assert bridge.round_trip_ok()

    def test_default_bridge_declares_supported_schema(self) -> None:
        raw = json.loads(DEFAULT_BRIDGE_PATH.read_text())
        assert raw["_schema"] == SCHEMA_VERSION

    def test_load_from_nonexistent_path_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ZoneAliasBridgeError, match="not found"):
            ZoneAliasBridge.load_default(tmp_path / "missing.json")

    def test_load_from_invalid_json_raises(self, tmp_path: pathlib.Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        with pytest.raises(ZoneAliasBridgeError, match="not valid JSON"):
            ZoneAliasBridge.load_default(bad)

    def test_load_from_unsupported_schema_raises(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "wrong_schema.json"
        path.write_text(json.dumps({"_schema": "future/v99", "lovs_to_inrb": {"a": "A"}}))
        with pytest.raises(ZoneAliasBridgeError, match="schema"):
            ZoneAliasBridge.load_default(path)


class TestDefaultBridgeCoversSnapshotSourceZones:
    """Every current LOVS source zone must be covered by the bridge."""

    def test_every_source_zone_has_an_inrb_mapping(self) -> None:
        snapshot = json.loads(SNAPSHOT_CONTRACT_PATH.read_text())
        lovs_zones = snapshot["corridor_watchlist"]["source_zones"]
        bridge = ZoneAliasBridge.load_default()
        missing = [z for z in lovs_zones if bridge.inrb_for(z) is None]
        assert missing == [], (
            f"LOVS source zones {missing} have no INRB mapping in the bridge; "
            "either add them to data/lovs_zone_alias_bridge.json or remove them "
            "from snapshot_contract.json"
        )


class TestRoundTrip:
    def test_round_trip_default(self) -> None:
        bridge = ZoneAliasBridge.load_default()
        for lovs_id in bridge.all_lovs_ids():
            inrb = bridge.inrb_for(lovs_id)
            assert inrb is not None
            assert bridge.lovs_for(inrb) == lovs_id

    def test_round_trip_reports_false_on_unknown_lovs_id(self) -> None:
        bridge = ZoneAliasBridge({"a": "A"})
        assert bridge.round_trip_ok() is True
        assert bridge.round_trip_ok(["unknown"]) is False


class TestKnownMappings:
    """Anchor the LOVS-canonical-vs-INRB-canonical asymmetries.

    These two pairs are the ones most likely to silently regress, since
    LOVS adopted the WHO-Sitrep spelling for both at first ingest.
    """

    def test_nyankunde_maps_to_inrb_nyakunde(self) -> None:
        bridge = ZoneAliasBridge.load_default()
        assert bridge.inrb_for("nyankunde") == "Nyakunde"
        assert bridge.lovs_for("Nyakunde") == "nyankunde"

    def test_mongbwalu_maps_to_inrb_mongbalu(self) -> None:
        bridge = ZoneAliasBridge.load_default()
        assert bridge.inrb_for("mongbwalu") == "Mongbalu"
        assert bridge.lovs_for("Mongbalu") == "mongbwalu"

    def test_goma_cod_maps_to_inrb_goma(self) -> None:
        bridge = ZoneAliasBridge.load_default()
        assert bridge.inrb_for("goma-cod") == "Goma"
        assert bridge.lovs_for("Goma") == "goma-cod"


class TestUpstreamAliasesVendoring:
    def test_default_bridge_carries_vendored_upstream_aliases(self) -> None:
        bridge = ZoneAliasBridge.load_default()
        aliases = bridge.inrb_upstream_aliases()
        # The two upstream aliases that affect LOVS source zones
        assert aliases.get("Nyankunde") == "Nyakunde"
        assert aliases.get("Mongbwalu") == "Mongbalu"

    def test_inrb_upstream_aliases_returns_a_copy(self) -> None:
        bridge = ZoneAliasBridge({"a": "A"}, inrb_upstream_aliases={"X": "Y"})
        first = bridge.inrb_upstream_aliases()
        first["X"] = "Z"
        second = bridge.inrb_upstream_aliases()
        assert second["X"] == "Y", "mutation of returned dict must not affect bridge"

    def test_construction_without_upstream_aliases_returns_empty(self) -> None:
        bridge = ZoneAliasBridge({"a": "A"})
        assert bridge.inrb_upstream_aliases() == {}


class TestAccessors:
    def test_all_lovs_ids_is_sorted(self) -> None:
        bridge = ZoneAliasBridge({"c": "C", "a": "A", "b": "B"})
        assert bridge.all_lovs_ids() == ("a", "b", "c")

    def test_all_inrb_noms_is_sorted(self) -> None:
        bridge = ZoneAliasBridge({"c": "Z", "a": "X", "b": "Y"})
        assert bridge.all_inrb_noms() == ("X", "Y", "Z")

    def test_unknown_lookups_return_none(self) -> None:
        bridge = ZoneAliasBridge({"a": "A"})
        assert bridge.inrb_for("nope") is None
        assert bridge.lovs_for("Nope") is None
