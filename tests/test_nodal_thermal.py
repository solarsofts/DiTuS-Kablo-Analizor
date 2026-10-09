from __future__ import annotations

from copy import deepcopy

from ucd.calculations.nodal_thermal import (
    _NodalModel,
    check_mesh_convergence,
    solve_nodal_region,
    solve_nodal_route,
)
from ucd.calculations.thermal_route import resolve_thermal_region, solve_thermal_route
from ucd.models.project import ProjectData


def _region_result(
    project: ProjectData,
    *,
    current_a: float = 800.0,
    active_circuits: int = 1,
    lambda1: float = 0.05,
    region_id: str = "TR-01",
):
    project.cable.design_current_a = current_a
    project.design_basis.design_current_per_circuit_a = current_a
    iec_study = solve_thermal_route(project)
    iec_region = next(item for item in iec_study.active.regions if item.region_id == region_id)
    return solve_nodal_region(
        project,
        region_id,
        current_a,
        active_circuits,
        lambda1,
        iec_region.iec,
        calculate_ampacity=False,
    )


def test_symmetric_trefoil_lower_cables_have_nearly_equal_temperature() -> None:
    result = _region_result(ProjectData())
    phase = {item.phase: item.conductor_temperature_c for item in result.cables}
    assert abs(phase["B"] - phase["C"]) < 0.15


def test_energy_balance_and_linear_residual_are_small() -> None:
    result = _region_result(ProjectData())
    assert result.energy_balance_error_percent < 1e-6
    assert result.maximum_linear_residual < 1e-8
    assert result.converged


def test_better_thermal_backfill_reduces_conductor_temperature() -> None:
    baseline_project = ProjectData()
    improved_project = deepcopy(baseline_project)
    material = next(
        item for item in improved_project.thermal_design.materials
        if item.material_id == "MAT-TB-01"
    )
    material.thermal_resistivity_km_w = 0.45
    baseline = _region_result(baseline_project)
    improved = _region_result(improved_project)
    assert improved.maximum_conductor_temperature_c < baseline.maximum_conductor_temperature_c


def test_deeper_burial_increases_temperature_when_far_boundary_expands() -> None:
    shallow_project = ProjectData()
    deep_project = deepcopy(shallow_project)
    section = deep_project.installation_design.cross_sections[0]
    for cable in section.physical_cables:
        cable.depth_m += 0.80
    section.channel_geometry.trench_depth_m += 0.80
    shallow = _region_result(shallow_project)
    deep = _region_result(deep_project)
    assert deep.maximum_conductor_temperature_c > shallow.maximum_conductor_temperature_c


def test_second_active_physical_circuit_increases_mutual_heating() -> None:
    from ucd.calculations.installation import generate_standard_cross_section

    one_project = ProjectData()
    two_project = ProjectData()
    original = two_project.installation_design.cross_sections[0]
    replacement = generate_standard_cross_section(
        cross_section_id=original.cross_section_id,
        name=original.name,
        arrangement="TREFOIL",
        circuit_count=2,
        parallel_cables_per_phase=1,
        phase_orders=["ABC", "ABC"],
        circuit_load_currents_a=[800.0, 800.0],
        phase_spacing_m=two_project.cable.overall_diameter_mm / 1000.0,
        circuit_spacing_m=0.45,
        burial_depth_m=1.20,
        outer_diameter_m=two_project.cable.overall_diameter_mm / 1000.0,
    )
    replacement.region_ids = ["TR-01"]
    replacement.channel_geometry = deepcopy(original.channel_geometry)
    two_project.installation_design.cross_sections[0] = replacement
    two_project.design_basis.circuit_count = 2
    two_project.design_basis.active_circuit_count = 2
    one = _region_result(one_project, active_circuits=1)
    two = _region_result(two_project, active_circuits=2)
    assert len(two.cables) == 6
    assert two.maximum_conductor_temperature_c > one.maximum_conductor_temperature_c


def test_higher_sheath_loss_factor_increases_temperature() -> None:
    project = ProjectData()
    no_sheath_loss = _region_result(project, lambda1=0.0)
    high_sheath_loss = _region_result(project, lambda1=0.20)
    assert high_sheath_loss.maximum_conductor_temperature_c > no_sheath_loss.maximum_conductor_temperature_c


def test_mesh_refinement_converges_within_two_percent() -> None:
    project = ProjectData()
    project.cable.design_current_a = 800.0
    project.design_basis.design_current_per_circuit_a = 800.0
    iec_region = solve_thermal_route(project).active.regions[0]
    check = check_mesh_convergence(
        project,
        "TR-01",
        800.0,
        1,
        iec_region.regional_lambda1,
        iec_region.iec,
        tolerance_percent=2.0,
    )
    assert check.refined_cells > check.coarse_cells
    assert check.passed


def test_route_ampacity_is_minimum_of_nodal_regions() -> None:
    project = ProjectData()
    project.design_basis.normal_current_per_active_circuit_a = 700.0
    project.design_basis.design_current_per_circuit_a = 800.0
    result = solve_nodal_route(project)
    active = result.active
    minimum = min(item.ampacity_per_cable_a for item in active.regions)
    assert active.route_ampacity_per_cable_a == minimum
    assert active.critical_region_id in {item.region_id for item in active.regions}


def test_v011_migration_adds_nodal_defaults_and_missing_duct_materials() -> None:
    legacy = ProjectData().to_dict()
    legacy["schema_version"] = "0.11"
    legacy["thermal_design"]["materials"] = [
        item for item in legacy["thermal_design"]["materials"]
        if item["material_id"] not in {"MAT-DUCT-01", "MAT-AIR-01"}
    ]
    for template in legacy["thermal_design"]["templates"]:
        for key in list(template):
            if key.startswith("nodal_") or key.startswith("duct_") or key in {
                "surface_boundary_type", "surface_temperature_c", "deep_soil_temperature_c",
                "surface_heat_transfer_w_m2k", "cable_effective_conductivity_w_mk",
                "groundwater_conductivity_multiplier", "parallel_cable_spacing_m",
                "grout_material_id",
            }:
                template.pop(key, None)
    loaded = ProjectData.from_dict(legacy)
    assert loaded.schema_version == "0.16.4"
    ids = {item.material_id for item in loaded.thermal_design.materials}
    assert {"MAT-DUCT-01", "MAT-AIR-01"} <= ids
    assert loaded.thermal_design.templates[0].nodal_enabled
    assert loaded.thermal_design.templates[0].nodal_base_step_m > 0


def test_zero_degree_boundary_temperatures_are_preserved_when_explicit() -> None:
    project = ProjectData()
    template = project.thermal_design.templates[0]
    template.surface_temperature_c = 0.0
    template.deep_soil_temperature_c = 0.0
    template.surface_temperature_uses_ambient = False
    template.deep_soil_temperature_uses_ambient = False
    region = project.thermal_design.regions[0]
    profile = resolve_thermal_region(project.thermal_design, region, project.cable)
    model = _NodalModel(project, region, profile, 1, mesh_scale=2.0)
    assert model._boundary_temperatures() == (0.0, 0.0)


def test_boundary_temperature_ambient_choice_is_explicit() -> None:
    project = ProjectData()
    region = project.thermal_design.regions[0]
    profile = resolve_thermal_region(project.thermal_design, region, project.cable)
    model = _NodalModel(project, region, profile, 1, mesh_scale=2.0)
    assert model._boundary_temperatures() == (
        profile.ambient_temperature_c,
        profile.ambient_temperature_c,
    )
