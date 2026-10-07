from __future__ import annotations

import os
from copy import deepcopy

import pytest

from ucd.calculations.application_orchestration import (
    BondingProductionRun,
    run_bonding_production,
)
from ucd.calculations.bonding import (
    sheath_loop_reactance_ohm_km,
    sheath_resistance_ohm_km,
    solve_bonding,
)
from ucd.calculations.iec60287 import ac_resistance_at_temperature_ohm_km
from ucd.calculations.primitive_cim import solve_primitive_network
from ucd.calculations.production_bonding import (
    ProductionBondingScenarioResult,
    ProductionBondingStudyResult,
    governing_lambda1,
    lambda1_criterion_warning,
)
from ucd.models.project import BONDING_CROSS, BONDING_SOLID_BOTH_END, ProjectData

EARTH_RESISTANCES_OHM = (0.0, 0.2, 1.0, 5.0)


def _solid_project(earth_resistance_ohm: float) -> ProjectData:
    project = ProjectData()
    project.bonding.scheme = BONDING_SOLID_BOTH_END
    terminations = [node for node in project.bonding.nodes if node.node_type.upper() == "TERMINATION"]
    assert len(terminations) == 2
    for node in terminations:
        node.earth_resistance_ohm = earth_resistance_ohm
    return project


def _iec_closed_form(project: ProjectData) -> tuple[float, float, float, float]:
    """IEC 60287-1-1 solid-bonded trefoil lambda1' = (Rs/R) / (1 + (Rs/X)^2)."""
    cable, bonding = project.cable, project.bonding
    assert cable.arrangement.strip().upper() == "TREFOIL"
    assert all(route.phase_spacing_m == bonding.phase_spacing_m for route in project.route_sections)
    assert all(not getattr(route, "phase_positions_m", None) for route in project.route_sections)
    _r20, rs = sheath_resistance_ohm_km(cable)
    x = sheath_loop_reactance_ohm_km(cable, bonding)
    _rdc, r = ac_resistance_at_temperature_ohm_km(cable, cable.max_temperature_c, bonding.phase_spacing_m)
    return (rs / r) / (1.0 + (rs / x) ** 2), rs, x, r


@pytest.mark.parametrize("earth_resistance_ohm", EARTH_RESISTANCES_OHM)
def test_legacy_solid_bonded_lambda1_matches_iec_closed_form(earth_resistance_ohm: float) -> None:
    project = _solid_project(earth_resistance_ohm)
    expected, rs, x, _r = _iec_closed_form(project)
    result = solve_bonding(project.cable, project.bonding, project.route_sections)
    assert result.lambda1 == pytest.approx(expected, rel=1e-9)
    length_km = result.total_length_m / 1000.0
    for loop in result.loop_results:
        assert loop.loop_impedance_ohm.real == pytest.approx(rs * length_km, rel=1e-12)
        assert loop.loop_impedance_ohm.imag == pytest.approx(x * length_km, rel=1e-12)
    assert any("terminasyon topraklama direnci" in note for note in result.notes)


@pytest.mark.parametrize("earth_resistance_ohm", EARTH_RESISTANCES_OHM)
def test_production_primitive_solid_bonded_sheath_loss_ratio_matches_closed_form(earth_resistance_ohm: float) -> None:
    project = _solid_project(earth_resistance_ohm)
    expected, _rs, _x, r = _iec_closed_form(project)
    length_m = sum(route.length_m for route in project.route_sections)
    assert length_m == pytest.approx(sum(minor.length_m for minor in project.bonding.minor_sections), rel=1e-12)
    conductor_loss_w = 3.0 * project.cable.design_current_a ** 2 * r * length_m / 1000.0
    primitive = solve_primitive_network(project.cable, project.bonding, project.route_sections)
    ratio = primitive.total_sheath_metal_loss_w / conductor_loss_w
    assert ratio == pytest.approx(expected, rel=0.02)


def test_legacy_and_production_solid_bonded_lambda1_agree_in_application_chain() -> None:
    project = ProjectData()
    project.bonding.scheme = BONDING_SOLID_BOTH_END
    run = run_bonding_production(project)
    production = [item.lambda1 for item in run.production.scenarios if item.lambda1 is not None]
    assert production
    assert run.legacy_diagnostic.lambda1 == pytest.approx(max(production), rel=0.05)


def _scenario(scenario_id: str, lambda1: float | None) -> ProductionBondingScenarioResult:
    return ProductionBondingScenarioResult(
        scenario_id, scenario_id, (("C1", 800.0),), (), True, True,
        1.0, 1.0, 1.0, 0.0, 1.0, lambda1, "fp",
    )


def _study(*rows: tuple[str, float | None]) -> ProductionBondingStudyResult:
    return ProductionBondingStudyResult(tuple(_scenario(scenario_id, value) for scenario_id, value in rows))


def test_lambda1_warning_is_triggered_by_production_scenario_when_legacy_is_low() -> None:
    study = _study(("DESIGN", 0.2), ("N_MINUS_ONE_C1_OUT", None))
    message = lambda1_criterion_warning(1e-6, study, 0.05)
    assert message == "λ1=0.200000 (üretim senaryosu DESIGN), proje kriteri 0.050000 üzerinde."


def test_lambda1_warning_reports_legacy_source_when_it_governs() -> None:
    study = _study(("DESIGN", 0.01), ("N_MINUS_ONE_C1_OUT", None))
    message = lambda1_criterion_warning(0.3, study, 0.05)
    assert message == "λ1=0.300000 (legacy tanısal üç-loop), proje kriteri 0.050000 üzerinde."


def test_lambda1_warning_picks_largest_production_scenario_and_ignores_missing_values() -> None:
    study = _study(("NORMAL", 0.06), ("DESIGN", 0.09), ("N_MINUS_ONE_C1_OUT", None), ("BAD", float("nan")))
    assert governing_lambda1(0.01, study) == (0.09, "üretim senaryosu DESIGN")
    assert governing_lambda1(0.09, study) == (0.09, "üretim senaryosu DESIGN")
    assert governing_lambda1(None, None) is None
    assert governing_lambda1(None, _study(("DESIGN", None))) is None
    assert governing_lambda1(0.02, None) == (0.02, "legacy tanısal üç-loop")


def test_lambda1_warning_is_silent_when_all_sources_are_within_criterion() -> None:
    study = _study(("DESIGN", 0.05), ("N_MINUS_ONE_C1_OUT", None))
    assert lambda1_criterion_warning(0.04, study, 0.05) is None
    assert lambda1_criterion_warning(None, None, 0.05) is None


def test_main_window_lambda1_warning_considers_production_scenarios(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    import ucd.ui.main_window as main_window_module

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = main_window_module.MainWindow(tmp_path)
    try:
        assert window.project.bonding.scheme == BONDING_CROSS
        real = run_bonding_production(deepcopy(window.project))
        assert real.legacy_diagnostic.lambda1 < window.project.bonding.maximum_lambda1
        production = _study(("DESIGN", 0.2), ("N_MINUS_ONE_C1_OUT", None))
        monkeypatch.setattr(
            main_window_module,
            "run_bonding_production",
            lambda _project: BondingProductionRun(real.electrothermal, production, real.legacy_diagnostic),
        )
        monkeypatch.setattr(window, "_confirm_engine_precheck", lambda _engine_id: True)
        window.run_bonding_solver()
        text = window.warning_list.toPlainText()
        assert "λ1=0.200000 (üretim senaryosu DESIGN), proje kriteri" in text
    finally:
        window.dirty = False
        window.close()
        app.processEvents()
