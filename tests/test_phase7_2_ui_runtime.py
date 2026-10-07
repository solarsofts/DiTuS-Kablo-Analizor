import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _project():
    from ucd.models.project import ProjectData
    raw = json.loads((ROOT / "examples" / "synthetic_20km_line.ucd.json").read_text(encoding="utf-8"))
    return ProjectData.from_dict(raw)


def test_headless_bonding_orchestration_uses_one_production_chain():
    from ucd.calculations.application_orchestration import run_bonding_production
    run = run_bonding_production(_project())
    assert run.production.scenarios
    assert run.electrothermal.scenarios
    assert run.legacy_diagnostic is not None


def test_headless_thermal_preprocessor_keeps_partial_preview_contract():
    from ucd.calculations.application_orchestration import run_thermal_preprocessor
    run = run_thermal_preprocessor(_project())
    assert run.materialized_section_count >= 1
    assert len(run.results) >= 1


def test_main_window_constructs_in_offscreen_qt(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path)
    assert window.windowTitle().startswith("DiTuS Kablo Analizör")
    assert window.act_bonding is not None
    assert window.act_thermal is not None
    window.close()
    app.processEvents()


def test_main_window_no_longer_owns_production_bonding_sequence():
    source = (ROOT / "src/ucd/ui/main_window.py").read_text(encoding="utf-8")
    method = source[source.index("    def run_bonding_solver"):source.index("    def _bonding_result_selection_changed")]
    assert "run_bonding_production(self.project)" in method
    assert "solve_production_electrothermal_study(self.project)" not in method
    assert "solve_project_bonding(self.project)" not in method
