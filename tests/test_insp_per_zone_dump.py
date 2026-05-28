# SPDX-License-Identifier: Apache-2.0
"""Tests for the diagnostic CLI tools.insp_per_zone_dump.

Network-dependent paths are NOT tested here (the --release-tag download
codepath); the local --source path is exercised end-to-end against the
inline fixture from test_insp_per_zone_loader and against the real
e40bc9e tarball when available.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import date

import pytest

# Import the dump module so we can call main() directly without subprocess
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.insp_per_zone_dump import main  # noqa: E402


LOCAL_E40BC9E_TARBALL = pathlib.Path("/tmp/inrb-e40bc9e/build.tar.gz")


def _build_fixture_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Re-use the fixture shape from test_insp_per_zone_loader, plus PCR tables."""
    d = tmp_path / "fixture"
    long_dir = d / "build" / "long"
    long_dir.mkdir(parents=True)

    def per_zone(stem: str, metric: str, rows: list[tuple[str, str, int]]) -> None:
        body = "nom,date," + metric + "\n"
        for nom, dt, value in rows:
            body += f"{nom},{dt},{value}\n"
        (long_dir / f"{stem}.csv").write_text(body)

    def national(stem: str, metric: str, value: int) -> None:
        body = "nom,date," + metric + "\n"
        for nom in ("ZoneA", "Bunia", "Goma"):
            body += f"{nom},2026-05-26,{value}\n"
        (long_dir / f"{stem}.csv").write_text(body)

    per_zone(
        "insp_sitrep__cumulative_confirmed_cases",
        "cumulative_confirmed_cases",
        [("Bunia", "26/05/2026", 36), ("Goma", "26/05/2026", 1)],
    )
    per_zone(
        "insp_sitrep__cumulative_suspected_cases",
        "cumulative_suspected_cases",
        [("Bunia", "26/05/2026", 279), ("Goma", "26/05/2026", 0)],
    )
    per_zone(
        "insp_sitrep__cumulative_confirmed_deaths",
        "cumulative_confirmed_deaths",
        [("Bunia", "26/05/2026", 2)],
    )
    per_zone(
        "insp_sitrep__cumulative_suspected_deaths",
        "cumulative_suspected_deaths",
        [("Bunia", "26/05/2026", 18)],
    )
    national("insp_sitrep__national_cumulative_confirmed_cases", "national_cumulative_confirmed_cases", 37)
    national("insp_sitrep__national_cumulative_suspected_cases", "national_cumulative_suspected_cases", 279)
    national("insp_sitrep__national_cumulative_confirmed_deaths", "national_cumulative_confirmed_deaths", 2)
    national("insp_sitrep__national_cumulative_suspected_deaths", "national_cumulative_suspected_deaths", 18)
    # PCR tables
    (long_dir / "testing_capacity__pcr_machines.csv").write_text(
        "nom,pcr_machines\nBunia,10\nGoma,2\n"
    )
    (long_dir / "testing_capacity__pcr_tests.csv").write_text(
        "nom,pcr_tests\nBunia,5000\nGoma,2000\n"
    )
    # Upstream aliases
    (d / "data").mkdir()
    (d / "data" / "aliases.csv").write_text(
        "observed_name,canonical_nom,source_dataset,notes\n"
    )
    return d


class TestCLIWithLocalSource:
    def test_text_report_runs_e2e(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = _build_fixture_dir(tmp_path)
        rc = main([
            "--source", str(src),
            "--as-of", "2026-05-26",
        ])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "INRB-UMIE INSP per-zone diagnostic report" in captured
        assert "as_of=2026-05-26" in captured
        assert "method_basis: INRB_UMIE_INSP_per_zone_v1" in captured
        assert "bunia" in captured
        assert "PCR modulator coverage" in captured

    def test_json_report_runs_e2e(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = _build_fixture_dir(tmp_path)
        rc = main([
            "--source", str(src),
            "--as-of", "2026-05-26",
            "--json",
        ])
        assert rc == 0
        captured = capsys.readouterr().out
        payload = json.loads(captured)
        assert payload["schema"] == "poc-insp-runner/v1"
        assert payload["as_of"] == "2026-05-26"
        assert payload["national"]["confirmed"] == 37
        # Bunia gets a modulated band (saturation 5000/279 >> 1)
        assert payload["by_lovs_zone"]["bunia"]["pcr_band"] is not None
        bunia_lo = payload["by_lovs_zone"]["bunia"]["pcr_band"]["lo"]
        assert bunia_lo > 0.3
        # PCR coverage stats reported
        assert payload["pcr_modulator_coverage"]["modulated_zones"] >= 1

    def test_write_atomically_creates_file(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = _build_fixture_dir(tmp_path)
        out_file = tmp_path / "out" / "report.json"
        rc = main([
            "--source", str(src),
            "--as-of", "2026-05-26",
            "--json",
            "--write", str(out_file),
        ])
        assert rc == 0
        assert out_file.exists()
        assert json.loads(out_file.read_text())["as_of"] == "2026-05-26"

    def test_missing_required_arg_exits_nonzero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            main(["--as-of", "2026-05-26"])


class TestSubprocessInvocation:
    def test_module_runs_via_python_dash_m(self, tmp_path: pathlib.Path) -> None:
        src = _build_fixture_dir(tmp_path)
        result = subprocess.run(
            [
                sys.executable, "-m", "tools.insp_per_zone_dump",
                "--source", str(src), "--as-of", "2026-05-26",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "INRB-UMIE INSP per-zone diagnostic report" in result.stdout


@pytest.mark.skipif(
    not LOCAL_E40BC9E_TARBALL.exists(),
    reason="canonical e40bc9e tarball not locally cached",
)
class TestCLIAgainstRealTarball:
    def test_real_tarball_text_report(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main([
            "--source", str(LOCAL_E40BC9E_TARBALL),
            "--as-of", "2026-05-26",
        ])
        assert rc == 0
        captured = capsys.readouterr().out
        # Sanity-check key numerics surface in the text report
        assert "confirmed=121" in captured
        assert "suspected=1077" in captured
        # Bunia in present_with_data
        assert "bunia" in captured

    def test_real_tarball_json_unallocated_residual(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main([
            "--source", str(LOCAL_E40BC9E_TARBALL),
            "--as-of", "2026-05-26",
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["unallocated_residual"]["confirmed"] == 10
        assert payload["unallocated_residual"]["suspected"] == 14
        assert payload["unallocated_residual"]["confirmed_deaths"] == 12
        assert payload["unallocated_residual"]["suspected_deaths"] == 0
        # Plan A 2026-05-28 bridge expansion: 18 LOVS source zones reported
        # (existing 11 plus aru, damas, karisimbi-cod, komanda, mambasa, oicha,
        # rimba). The loader's residual stays invariant to bridge size because
        # it is `national - sum(ALL INRB zones)`, NOT bridge-filtered.
        assert len(payload["by_lovs_zone"]) == 18
