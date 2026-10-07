from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from ucd.calculations import nodal_thermal
from ucd.calculations.iec60287 import dielectric_loss_w_m
from ucd.calculations.nodal_thermal import (
    NodalThermalInputError,
    _find_ampacity,
    _internal_thermal_chains,
    _NodalModel,
    _solve_at_current,
    check_mesh_convergence,
    solve_nodal_region,
)
from ucd.calculations.thermal_method_validation import (
    QUALITY_FAIL,
    ThermalMethodToleranceProfile,
    _quality,
)
from ucd.calculations.thermal_resistance import resolve_internal_thermal_resistance
from ucd.calculations.thermal_route import resolve_thermal_region, solve_thermal_route
from ucd.calculations.transient_thermal import (
    TransientThermalInputError,
    _cable_internal_step,
    _capacity_vector,
    _conductor_heat_capacity_j_mk,
    _constant_profile,
    _initial_state,
    _simulate,
    _TransientState,
)
from ucd.models.project import TRANSIENT_INITIAL_STEADY, ProjectData


def _quick_project() -> ProjectData:
    project = ProjectData()
    project.thermal_design.regions = project.thermal_design.regions[:1]
    project.thermal_design.route_length_m = project.thermal_design.regions[0].end_m
    project.route_sections = project.route_sections[:1]
    template = project.thermal_design.templates[0]
    template.nodal_base_step_m = 0.35
    template.nodal_refined_step_m = 0.12
    template.nodal_refinement_radius_m = 0.25
    template.nodal_max_cells = 10000
    project.transient_study.time_step_minutes = 60.0
    return project


def _quick_model(project: ProjectData) -> _NodalModel:
    region = project.thermal_design.regions[0]
    geometry = resolve_thermal_region(project.thermal_design, region, project.cable)
    return _NodalModel(project, region, geometry, 1, 1.8)


def _step_inputs(cable, lambda1: float) -> dict[str, float]:
    internal = resolve_internal_thermal_resistance(cable)
    chain, dielectric_chain = _internal_thermal_chains(cable, lambda1)
    return {
        "wd": dielectric_loss_w_m(cable),
        "lambda1": lambda1,
        "lambda2": max(0.0, float(cable.armour_loss_factor)),
        "internal_chain": chain,
        "dielectric_internal_chain": dielectric_chain,
        "r_internal": internal.t1_km_w + internal.t2_km_w + internal.t3_km_w,
        "c_core": 4000.0,
    }


@pytest.mark.parametrize("conductors", [1, 3])
def test_internal_chain_matches_iec_60287_expression(conductors: int) -> None:
    cable = ProjectData().cable
    cable.conductors_per_cable = conductors
    cable.armour_loss_factor = 0.25
    internal = resolve_internal_thermal_resistance(cable)
    t1, t2, t3 = internal.t1_km_w, internal.t2_km_w, internal.t3_km_w
    chain, dielectric_chain = _internal_thermal_chains(cable, 0.6)
    assert chain == pytest.approx(t1 + conductors * 1.6 * t2 + conductors * 1.85 * t3, rel=1e-12)
    assert dielectric_chain == pytest.approx(0.5 * t1 + conductors * (t2 + t3), rel=1e-12)


@pytest.mark.parametrize(("lambda1", "armour"), [(0.05, 0.0), (1.3, 0.0), (0.4, 0.3)])
def test_core_update_converges_to_iec_internal_chain(lambda1: float, armour: float) -> None:
    cable = ProjectData().cable
    cable.armour_loss_factor = armour
    inputs = _step_inputs(cable, lambda1)
    jacket = np.array([55.0, 57.5])
    conductor_loss = np.array([12.2, 15.0])
    expected = (
        jacket
        + conductor_loss * inputs["internal_chain"]
        + inputs["wd"] * inputs["dielectric_internal_chain"]
    )

    core, _ = _cable_internal_step(
        np.array([20.0, 20.0]), jacket, conductor_loss, dt_s=1e15, **inputs
    )
    np.testing.assert_allclose(core, expected, rtol=0.0, atol=1e-8)

    core = np.array([20.0, 20.0])
    for _ in range(400):
        core, _ = _cable_internal_step(core, jacket, conductor_loss, dt_s=600.0, **inputs)
    np.testing.assert_allclose(core, expected, rtol=0.0, atol=1e-6)
    # The lumped R = T1+T2+T3 limit is lower by the sheath/armour and dielectric terms.
    lumped = jacket + inputs["r_internal"] * (conductor_loss + 0.5 * inputs["wd"])
    assert np.all(core >= lumped - 1e-9)


def test_steady_heat_delivered_to_soil_equals_total_loss() -> None:
    cable = ProjectData().cable
    cable.armour_loss_factor = 0.3
    inputs = _step_inputs(cable, 0.7)
    jacket = np.array([60.0])
    conductor_loss = np.array([14.0])
    _, q_outer = _cable_internal_step(
        np.array([85.0]), jacket, conductor_loss, dt_s=1e15, **inputs
    )
    total = conductor_loss * (1.0 + inputs["lambda1"] + inputs["lambda2"]) + inputs["wd"]
    np.testing.assert_allclose(q_outer, total, rtol=1e-9)


def test_heat_flowing_back_to_conductor_is_not_clipped() -> None:
    cable = ProjectData().cable
    inputs = _step_inputs(cable, 0.3)
    dt_s = 1800.0
    old_core = np.array([20.0])
    jacket = np.array([60.0])
    conductor_loss = np.array([0.0])
    core, q_outer = _cable_internal_step(old_core, jacket, conductor_loss, dt_s=dt_s, **inputs)
    q_transfer = q_outer - 0.5 * inputs["wd"]
    assert q_transfer[0] < 0.0
    stored = inputs["c_core"] / dt_s * (core - old_core)
    generated = conductor_loss * (1.0 + inputs["lambda1"] + inputs["lambda2"]) + inputs["wd"]
    np.testing.assert_allclose(stored + q_outer, generated, rtol=0.0, atol=1e-9)


@pytest.mark.parametrize(("lambda1", "armour"), [(1.0, 0.0), (0.3, 0.4)])
def test_long_constant_load_transient_converges_to_nodal_steady_state(lambda1: float, armour: float) -> None:
    project = _quick_project()
    project.cable.armour_loss_factor = armour
    model = _quick_model(project)
    current = 1000.0
    field, cables, _, converged, *_ = _solve_at_current(
        model, current, lambda1, max_iterations=300, tolerance_c=1e-5
    )
    assert converged
    field = np.asarray(field, dtype=float)
    nodal_core = np.asarray([item.conductor_temperature_c for item in cables], dtype=float)
    jacket = model.cable_jacket_temperatures(field)
    profile = _constant_profile(72.0)

    held = _simulate(
        project, model, profile, current, lambda1,
        _TransientState(field.copy(), nodal_core.copy()), record_points=False,
    )
    np.testing.assert_allclose(held.state.conductor_c, nodal_core, rtol=0.0, atol=0.02)

    displaced = _simulate(
        project, model, profile, current, lambda1,
        _TransientState(field.copy(), jacket.copy()), record_points=False,
    )
    assert float(np.min(nodal_core - jacket)) > 5.0
    np.testing.assert_allclose(displaced.state.conductor_c, nodal_core, rtol=0.0, atol=0.2)


def test_transient_step_conserves_energy_when_heat_flows_back_to_conductor() -> None:
    project = _quick_project()
    project.transient_study.time_step_minutes = 30.0
    model = _quick_model(project)
    lambda1 = 0.3
    field, *_ = _solve_at_current(model, 1200.0, lambda1, max_iterations=300, tolerance_c=1e-5)
    field = np.asarray(field, dtype=float)
    cold_core = np.full(len(model.locations), float(model.profile.ambient_temperature_c))
    output = _simulate(
        project, model, _constant_profile(0.5), 0.0, lambda1,
        _TransientState(field.copy(), cold_core.copy()), record_points=False,
    )
    capacity, *_ = _capacity_vector(project, model)
    dt_s = 1800.0
    stored = (
        float(np.sum(capacity * (output.state.field_c.ravel() - field.ravel())))
        + _conductor_heat_capacity_j_mk(project) * float(np.sum(output.state.conductor_c - cold_core))
    ) / dt_s
    boundary = float(np.sum(model.matrix.dot(output.state.field_c.ravel()) - model.boundary_rhs))
    generated = len(model.locations) * dielectric_loss_w_m(project.cable)
    assert boundary > 50.0
    assert stored == pytest.approx(generated - boundary, abs=1e-6 * boundary)


def _patch_solver(monkeypatch, *, converge_from: float, only_current: float | None = None) -> list[tuple[float, int]]:
    original = nodal_thermal._solve_at_current
    calls: list[tuple[float, int]] = []

    def fake(model, current_a, lambda1, max_iterations=40, tolerance_c=0.02, **kwargs):
        calls.append((float(current_a), int(max_iterations)))
        solved = original(
            model, current_a, lambda1, max_iterations=max_iterations, tolerance_c=tolerance_c, **kwargs
        )
        if max_iterations >= converge_from:
            return solved
        if only_current is not None and abs(float(current_a) - only_current) > 1e-9:
            return solved
        # Under-relaxed, non-converged iterate: optimistic (cold) temperatures.
        cold = tuple(
            replace(item, conductor_temperature_c=item.conductor_temperature_c - 15.0) for item in solved[1]
        )
        return (solved[0], cold, solved[2], False, *solved[4:])

    monkeypatch.setattr(nodal_thermal, "_solve_at_current", fake)
    return calls


def test_ampacity_search_retries_non_converged_evaluations(monkeypatch) -> None:
    project = _quick_project()
    model = _quick_model(project)
    reference, reference_evaluations = _find_ampacity(model, 0.05, 800.0, 1000.0)
    calls = _patch_solver(monkeypatch, converge_from=120)
    ampacity, evaluations = _find_ampacity(model, 0.05, 800.0, 1000.0)
    assert ampacity == pytest.approx(reference, abs=1e-9)
    assert evaluations == 2 * reference_evaluations
    assert [item[1] for item in calls] == [25, 120] * reference_evaluations


def test_ampacity_search_rejects_persistently_non_converged_evaluation(monkeypatch) -> None:
    project = _quick_project()
    model = _quick_model(project)
    calls = _patch_solver(monkeypatch, converge_from=float("inf"))
    with pytest.raises(NodalThermalInputError, match="TR-01: .* A akımda nodal sıcaklık iterasyonu .*yakınsamadı"):
        _find_ampacity(model, 0.05, 800.0, 1000.0)
    assert [item[1] for item in calls] == [25, 120]


def _iec_region(project: ProjectData, current: float):
    project.cable.design_current_a = current
    project.design_basis.design_current_per_circuit_a = current
    study = solve_thermal_route(project)
    return next(item for item in study.active.regions if item.region_id == "TR-01").iec


def test_design_current_solve_retries_and_surfaces_non_convergence(monkeypatch) -> None:
    project = ProjectData()
    iec = _iec_region(project, 800.0)
    calls = _patch_solver(monkeypatch, converge_from=float("inf"))
    result = solve_nodal_region(project, "TR-01", 800.0, 1, 0.05, iec, calculate_ampacity=False)
    assert [item[1] for item in calls] == [40, 120]
    assert result.converged is False
    assert any("yakınsamadı" in item for item in result.warnings)


def test_mesh_convergence_fails_when_design_solve_does_not_converge(monkeypatch) -> None:
    project = ProjectData()
    iec = _iec_region(project, 800.0)
    baseline = check_mesh_convergence(project, "TR-01", 800.0, 1, 0.05, iec)
    assert baseline.passed and baseline.solutions_converged
    _patch_solver(monkeypatch, converge_from=float("inf"), only_current=800.0)
    mesh = check_mesh_convergence(project, "TR-01", 800.0, 1, 0.05, iec)
    assert mesh.passed is False
    assert mesh.solutions_converged is False
    region = SimpleNamespace(
        scenario_id="DESIGN", region_id="TR-01", converged=True,
        energy_balance_error_percent=0.0, maximum_linear_residual=0.0,
    )
    evidence = _quality(region, mesh, ThermalMethodToleranceProfile())
    assert evidence.status == QUALITY_FAIL
    assert any("Mesh duyarlılığı nodal çözümlerinden" in item for item in evidence.reasons)


def test_transient_initial_state_rejects_non_converged_steady_solution(monkeypatch) -> None:
    project = _quick_project()
    project.transient_study.initial_condition_mode = TRANSIENT_INITIAL_STEADY
    model = _quick_model(project)
    _patch_solver(monkeypatch, converge_from=float("inf"))
    with pytest.raises(TransientThermalInputError, match="TR-01: .*yakınsamadı"):
        _initial_state(project, model, _constant_profile(6.0), 900.0, 0.05)
