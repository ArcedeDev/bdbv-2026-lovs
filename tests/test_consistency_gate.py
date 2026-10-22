# SPDX-License-Identifier: Apache-2.0
"""Tests for the consistency release gate (lovs/consistency_gate.py) and its
wiring in release_snapshot._run_consistency_gate.

Promotions are synthetic dicts fed through a monkeypatched loader; the
private PDF layer is a tmp_path directory and its text is monkeypatched, so no
test needs pdftotext or any private file.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

import release_snapshot
from lovs import consistency_gate, sitrep_promotions
from lovs.consistency import bdbv

SHA = 'f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0'
BLOCK = consistency_gate.BLOCK_FROM_EDITION


def payload(number: int, *, rate: float = 96.0, conflicts: list | None = None, sha: str | None = None) -> dict:
    out = {
        "sitrep_number": number,
        "figures": {
            "operational_tables": {
                "alerts_total": {"validated_as_suspect": 100, "suspects_investigated": 96, "investigation_rate_pct": rate},
            },
            "preserved_conflicts": conflicts or [],
        },
    }
    if sha is not None:
        out["source_receipt"] = {"sha256": sha}
    return out


@pytest.fixture
def promotions(monkeypatch):
    """Install a list of synthetic payloads as the reviewed promotions."""
    holder: dict = {"rows": [], "dirs": []}

    def fake_load(directory=None):
        holder["dirs"].append(directory)
        return list(holder["rows"])

    monkeypatch.setattr(consistency_gate.sitrep_promotions, "load_reviewed_promotions", fake_load)
    return holder


@pytest.fixture
def no_private(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "private-raw-missing"


# ---------------------------------------------------------------------------
# Mode


def test_mode_default_is_report(monkeypatch):
    monkeypatch.delenv(consistency_gate.ENV, raising=False)
    assert consistency_gate.mode_from_env() == "report"
    monkeypatch.setenv(consistency_gate.ENV, "  ")
    assert consistency_gate.mode_from_env() == "report"


@pytest.mark.parametrize("raw, mode", [("off", "off"), ("OFF", "off"), (" block ", "block"), ("report", "report")])
def test_mode_values(monkeypatch, raw: str, mode: str):
    monkeypatch.setenv(consistency_gate.ENV, raw)
    assert consistency_gate.mode_from_env() == mode


def test_mode_bogus_raises(monkeypatch):
    monkeypatch.setenv(consistency_gate.ENV, "bogus")
    with pytest.raises(consistency_gate.ConsistencyGateError):
        consistency_gate.mode_from_env()
    with pytest.raises(consistency_gate.ConsistencyGateError):
        consistency_gate.evaluate(pathlib.Path("unused"), mode="bogus")
    with pytest.raises(consistency_gate.ConsistencyGateError):
        consistency_gate.evaluate(pathlib.Path("unused"), mode="")


# ---------------------------------------------------------------------------
# evaluate


def test_report_mode_warns_but_passes(promotions, no_private):
    promotions["rows"] = [payload(BLOCK - 2, rate=96.0), payload(BLOCK, rate=95.0)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="report", private_raw=no_private)
    assert result["ok"] is True
    assert result["mode"] == "report"
    assert result["editions"] == 2
    assert result["relations"] == 2
    assert len(result["warnings"]) == 1
    assert result["warnings"][0].startswith(f"SR{BLOCK} national investigation_rate_pct")
    assert "withhold the rate" in result["warnings"][0]
    assert result["blocking"] == result["warnings"]
    assert result["key_messages"] == "not evaluable"
    assert promotions["dirs"] == [pathlib.Path("unused")]


def test_mode_read_from_env_when_not_given(promotions, no_private, monkeypatch):
    monkeypatch.setenv(consistency_gate.ENV, "block")
    promotions["rows"] = [payload(BLOCK, rate=95.0)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), private_raw=no_private)
    assert result["mode"] == "block" and result["ok"] is False


def test_block_mode_fails_on_undisclosed_mismatch_from_block_edition(promotions, no_private):
    promotions["rows"] = [payload(BLOCK - 1), payload(BLOCK, rate=95.0)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="block", private_raw=no_private)
    assert result["ok"] is False
    assert len(result["blocking"]) == 1 and result["blocking"][0].startswith(f"SR{BLOCK} ")


def test_block_mode_passes_when_only_older_editions_mismatch(promotions, no_private):
    promotions["rows"] = [payload(BLOCK - 1, rate=95.0), payload(BLOCK)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="block", private_raw=no_private)
    assert result["ok"] is True
    assert len(result["warnings"]) == 1 and result["blocking"] == []


def test_block_mode_passes_when_mismatch_is_disclosed(promotions, no_private):
    note = "Tableau 3 imprime 95,5 % ; recalcule 96 % a partir des comptes."
    promotions["rows"] = [payload(BLOCK, rate=95.5, conflicts=[note])]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="block", private_raw=no_private)
    assert result["ok"] is True and result["warnings"] == []


def test_off_mode_does_not_scan(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("off mode must not load promotions")

    monkeypatch.setattr(consistency_gate.sitrep_promotions, "load_reviewed_promotions", boom)
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="off")
    assert result == {"mode": "off", "ok": True, "editions": 0, "relations": 0, "warnings": [], "blocking": []}


def test_off_mode_from_env(monkeypatch):
    monkeypatch.setenv(consistency_gate.ENV, "off")
    monkeypatch.setattr(consistency_gate.sitrep_promotions, "load_reviewed_promotions",
                        lambda *a, **k: pytest.fail("scanned"))
    assert consistency_gate.evaluate(pathlib.Path("unused"))["ok"] is True


def test_zero_editions_raises(promotions, no_private):
    promotions["rows"] = [{"sitrep_number": 5, "figures": {}}]
    with pytest.raises(consistency_gate.ConsistencyGateError, match="zero editions"):
        consistency_gate.evaluate(pathlib.Path("unused"), mode="report", private_raw=no_private)


def test_empty_promotions_dir_fails_in_the_real_loader(tmp_path: pathlib.Path, no_private):
    with pytest.raises(sitrep_promotions.SitRepPromotionError):
        consistency_gate.evaluate(tmp_path, mode="report", private_raw=no_private)


def test_key_messages_evaluable_count_with_private_dir(promotions, tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / SHA).write_bytes(b"synthetic")
    monkeypatch.setattr(consistency_gate.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(consistency_gate, "_pdf_text",
                        lambda path: "MESSAGES CLES 90 cas suspects sur 100 (90,0 %) ont été investigués.")
    promotions["rows"] = [payload(BLOCK, sha=SHA), payload(BLOCK + 1)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="report", private_raw=raw)
    # One edition read (a contradiction), one with no receipt is reported as not read.
    assert result["key_messages"] == "1 read, 1 not read"
    assert any("key message vs Tableau 3" in w for w in result["warnings"])


def test_key_messages_not_evaluable_without_pdftotext(promotions, tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(consistency_gate.shutil, "which", lambda name: None)
    promotions["rows"] = [payload(BLOCK)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="report", private_raw=raw)
    assert result["key_messages"] == "not evaluable"


# ---------------------------------------------------------------------------
# key_message_finding and _pdf_text


def _raw_with(tmp_path: pathlib.Path, sha: str = SHA) -> pathlib.Path:
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    (raw / sha).write_bytes(b"synthetic")
    return raw


def test_key_message_finding_raises_on_unparseable(tmp_path, monkeypatch):
    raw = _raw_with(tmp_path)
    monkeypatch.setattr(consistency_gate, "_pdf_text",
                        lambda path: "MESSAGES CLES Les cas suspects ont été investigués dans 4 zones.")
    with pytest.raises(consistency_gate.ConsistencyGateError, match="unparseable"):
        consistency_gate.key_message_finding(payload(BLOCK, sha=SHA), raw)


def test_key_message_finding_not_read_without_text_or_receipt(tmp_path, monkeypatch):
    raw = _raw_with(tmp_path)
    not_read = consistency_gate.NOT_READ
    monkeypatch.setattr(consistency_gate, "_pdf_text", lambda path: None)
    assert consistency_gate.key_message_finding(payload(BLOCK, sha=SHA), raw) == not_read
    monkeypatch.setattr(consistency_gate, "_pdf_text", lambda path: pytest.fail("no file to read"))
    assert consistency_gate.key_message_finding(payload(BLOCK), raw) == not_read
    assert consistency_gate.key_message_finding(payload(BLOCK, sha="e" * 64), raw) == not_read
    # A receipt sha that is not 64 hex characters is never joined onto the path.
    assert consistency_gate.key_message_finding(payload(BLOCK, sha="../" + SHA), raw) == not_read


def test_block_mode_fails_when_recent_private_pdf_unreadable(promotions, tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(consistency_gate.shutil, "which", lambda name: "/usr/bin/" + name)
    promotions["rows"] = [payload(BLOCK - 1), payload(BLOCK)]
    result = consistency_gate.evaluate(pathlib.Path("unused"), mode="block", private_raw=raw)
    assert result["key_messages"] == "0 read, 2 not read"
    assert result["blocking"] == [f"SR{BLOCK}: private PDF not readable; key message not checked"]
    assert result["ok"] is False


def test_key_message_disclosure_needs_every_differing_value(tmp_path, monkeypatch):
    raw = _raw_with(tmp_path)
    monkeypatch.setattr(consistency_gate, "_pdf_text", lambda path: (
        "MESSAGES CLES 96 cas suspects sur 100 (96,0 %) ont été investigués. "
        "26 cas suspects restent non investigués en Ituri."))
    counts_only = "Les messages cles impriment 96 sur 100."
    finding = consistency_gate.key_message_finding(payload(BLOCK, sha=SHA, conflicts=[counts_only]), raw)
    assert finding.unexplained
    full = "Les messages cles impriment 26 non investigues en Ituri ; le Tableau 3 en donne 4."
    finding = consistency_gate.key_message_finding(payload(BLOCK, sha=SHA, conflicts=[full]), raw)
    assert finding.covered_by == full


def test_key_message_finding_none_when_absent_or_matching(tmp_path, monkeypatch):
    raw = _raw_with(tmp_path)
    monkeypatch.setattr(consistency_gate, "_pdf_text", lambda path: "Pas de bloc de messages.")
    assert consistency_gate.key_message_finding(payload(BLOCK, sha=SHA), raw) is None
    monkeypatch.setattr(consistency_gate, "_pdf_text",
                        lambda path: "MESSAGES CLES 96 cas suspects sur 100 (96,0 %) ont été investigués.")
    assert consistency_gate.key_message_finding(payload(BLOCK, sha=SHA), raw) is None


def test_key_message_finding_mismatch_and_disclosure(tmp_path, monkeypatch):
    raw = _raw_with(tmp_path)
    monkeypatch.setattr(consistency_gate, "_pdf_text",
                        lambda path: "MESSAGES CLES 506 cas suspects sur 513 (98,6 %) ont été investigués.")
    finding = consistency_gate.key_message_finding(payload(BLOCK, sha=SHA), raw)
    assert finding is not None and finding.unexplained
    assert finding.edition == BLOCK and finding.section == "MESSAGES CLES"
    assert finding.printed == {"inv": 506, "val": 513, "pct": 98.6}
    assert finding.expected == {"val": 100, "inv": 96, "not_inv": 4}
    note = "Les messages cles impriment 506 sur 513 ; le Tableau 3 donne 96 sur 100."
    disclosed = consistency_gate.key_message_finding(payload(BLOCK, sha=SHA, conflicts=[note]), raw)
    assert disclosed.covered_by == note and not disclosed.unexplained
    partial = "Les messages cles impriment 506 cas investigues."
    assert consistency_gate.key_message_finding(payload(BLOCK, sha=SHA, conflicts=[partial]), raw).unexplained


def test_key_message_finding_rate_only_uses_pct_for_disclosure(tmp_path, monkeypatch):
    raw = _raw_with(tmp_path)
    monkeypatch.setattr(consistency_gate, "_pdf_text",
                        lambda path: "MESSAGES CLES Ce jour, 97,3 % des cas suspects ont été investigués.")
    note = "Les messages cles impriment 97,3 % contre 96,0 % au Tableau 3."
    finding = consistency_gate.key_message_finding(payload(BLOCK, sha=SHA, conflicts=[note]), raw)
    assert finding.printed == {"pct": 97.3}
    assert finding.covered_by == note


def test_pdf_text_without_pdftotext(monkeypatch, tmp_path):
    monkeypatch.setattr(consistency_gate.shutil, "which", lambda name: None)
    monkeypatch.setattr(consistency_gate.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    assert consistency_gate._pdf_text(tmp_path / "x.pdf") is None


def test_pdf_text_with_stubbed_pdftotext(monkeypatch, tmp_path):
    monkeypatch.setattr(consistency_gate.shutil, "which", lambda name: "/usr/bin/pdftotext")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="texte synthetique", stderr="")

    monkeypatch.setattr(consistency_gate.subprocess, "run", fake_run)
    assert consistency_gate._pdf_text(tmp_path / "x.pdf") == "texte synthetique"
    assert calls[0][0] == "pdftotext" and calls[0][-1] == "-"
    monkeypatch.setattr(consistency_gate.subprocess, "run",
                        lambda args, **k: subprocess.CompletedProcess(args, 1, stdout="", stderr="err"))
    assert consistency_gate._pdf_text(tmp_path / "x.pdf") is None


def test_main_exit_codes(promotions, no_private, capsys):
    promotions["rows"] = [payload(BLOCK, rate=95.0)]
    argv = ["--promotions-dir", "unused", "--private-raw", str(no_private)]
    assert consistency_gate.main(argv + ["--mode", "report"]) == 0
    assert consistency_gate.main(argv + ["--mode", "block"]) == 1
    promotions["rows"] = []
    assert consistency_gate.main(argv + ["--mode", "report"]) == 1
    assert "[FAIL] consistency gate" in capsys.readouterr().err


def test_private_raw_default_points_at_private_layer():
    parts = consistency_gate.PRIVATE_RAW.parts
    assert parts[-2:] == ("private", "raw")
    assert bdbv.LANE == "bdbv"


# ---------------------------------------------------------------------------
# release_snapshot wiring


def test_release_gate_false_on_gate_error(monkeypatch, capsys):
    def raise_error(*args, **kwargs):
        raise consistency_gate.ConsistencyGateError("zero editions scanned")

    monkeypatch.setattr(release_snapshot.consistency_gate, "evaluate", raise_error)
    assert release_snapshot._run_consistency_gate() is False
    assert "zero editions scanned" in capsys.readouterr().err


def test_release_gate_false_on_promotion_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise sitrep_promotions.SitRepPromotionError("no payloads")

    monkeypatch.setattr(release_snapshot.consistency_gate, "evaluate", raise_error)
    assert release_snapshot._run_consistency_gate() is False


def test_release_gate_true_in_off_mode(monkeypatch, capsys):
    monkeypatch.setattr(release_snapshot.consistency_gate, "evaluate",
                        lambda *a, **k: {"mode": "off", "ok": True, "editions": 0, "relations": 0,
                                         "warnings": [], "blocking": []})
    assert release_snapshot._run_consistency_gate() is True
    assert "consistency gate off" in capsys.readouterr().out


def test_release_gate_report_and_block_results(monkeypatch, capsys):
    report = {"mode": "report", "ok": True, "editions": 2, "relations": 5, "key_messages": "not evaluable",
              "warnings": ["SR1 national x: printed 1, expected 2; add a preserved_conflicts line"], "blocking": []}
    monkeypatch.setattr(release_snapshot.consistency_gate, "evaluate", lambda *a, **k: report)
    assert release_snapshot._run_consistency_gate() is True
    out = capsys.readouterr().out
    assert "[warn] SR1 national x" in out and "consistency gate report OK" in out
    blocked = dict(report, mode="block", ok=False, blocking=["SR999 national x"])
    monkeypatch.setattr(release_snapshot.consistency_gate, "evaluate", lambda *a, **k: blocked)
    assert release_snapshot._run_consistency_gate() is False
