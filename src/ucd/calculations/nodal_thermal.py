from __future__ import annotations

from ucd.calculations.result_status import aggregate_binary_status

from ucd.calculations.soil_dryout import SoilDryoutInputError, SoilDryoutProfile, material_dryout_profile

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from math import sqrt
from typing import Any

import numpy as np
from scipy.sparse import coo_matrix, csc_matrix
from scipy.sparse.linalg import factorized

from ucd.calculations.installation import physical_cable_contact_tolerance_m, resolved_physical_cables
from ucd.calculations.installation_coupling import (
    channel_profile_and_overrides,
    cross_section_for_region as linked_cross_section_for_region,
)
from ucd.calculations.iec60287 import (
    Iec60287SectionResult,
    ac_resistance_at_temperature_ohm_km,
    dielectric_loss_w_m,
    solve_section,
)
from ucd.calculations.thermal_resistance import (
    cable_positions_m,
    resolve_internal_thermal_resistance,
)
from ucd.calculations.thermal_route import (
    EffectiveThermalProfile,
    ThermalRouteStudyResult,
    resolve_thermal_region,
    solve_thermal_route,
)
from ucd.models.project import (
    CableData,
    ProjectData,
    RouteSection,
    EXTERNAL_THERMAL_MANUAL,
    ThermalMaterialData,
    ThermalRegion,
    THERMAL_INSTALL_DIRECT_BURIED,
    THERMAL_INSTALL_DUCT_BANK,
    THERMAL_INSTALL_HDD,
    THERMAL_INSTALL_CONCRETE_TROUGH,
    THERMAL_INSTALL_TUNNEL,
)


NODAL_THERMAL_REFERENCE = (
    "2D cell-centred finite-volume steady-state conduction solver; route-section integration with IEC 60287 and primitive bonding losses"
)


class NodalThermalInputError(ValueError):
    pass


@dataclass(frozen=True)
class NodalDryoutState:
    enabled: bool
    converged: bool
    iterations: int
    dry_cell_count: int
    eligible_cell_count: int
    dry_fraction: float
    material_ids: tuple[str, ...]
    maximum_temperature_c: float
    trace: tuple[str, ...] = ()


def _point_in_polygon(x: float, y: float, vertices: tuple[tuple[float, float], ...]) -> bool:
    """Ray-casting point-in-polygon test including boundary tolerance."""

    if len(vertices) < 3:
        return False
    inside = False
    j = len(vertices) - 1
    for i, (xi, yi) in enumerate(vertices):
        xj, yj = vertices[j]
        # Boundary check first.
        dx, dy = xj - xi, yj - yi
        cross = (x - xi) * dy - (y - yi) * dx
        if abs(cross) <= 1e-10:
            dot = (x - xi) * (x - xj) + (y - yi) * (y - yj)
            if dot <= 1e-10:
                return True
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


@dataclass(frozen=True)
class NodalCableResult:
    cable_id: str
    circuit_index: int
    phase: str
    parallel_index: int
    x_m: float
    depth_m: float
    current_a: float
    conductor_temperature_c: float
    jacket_temperature_c: float
    conductor_loss_w_m: float
    sheath_loss_w_m: float
    armour_loss_w_m: float
    dielectric_loss_w_m: float
    total_loss_w_m: float
    equivalent_external_t4_km_w: float


@dataclass(frozen=True)
class NodalRegionResult:
    region_id: str
    region_name: str
    start_m: float
    end_m: float
    installation_type: str
    scenario_id: str
    design_current_per_cable_a: float
    active_circuit_count: int
    ampacity_per_cable_a: float
    maximum_conductor_temperature_c: float
    maximum_jacket_temperature_c: float
    temperature_limit_c: float
    status: str
    iec_ampacity_per_cable_a: float
    difference_from_iec_percent: float
    regional_lambda1: float
    mesh_nx: int
    mesh_ny: int
    mesh_cell_count: int
    minimum_cell_size_m: float
    maximum_cell_size_m: float
    solver_iterations: int
    converged: bool
    total_heat_source_w_m: float
    total_boundary_heat_w_m: float
    energy_balance_error_percent: float
    maximum_linear_residual: float
    x_edges_m: tuple[float, ...]
    depth_edges_m: tuple[float, ...]
    temperature_c: tuple[tuple[float, ...], ...]
    material_ids: tuple[tuple[str, ...], ...]
    cables: tuple[NodalCableResult, ...]
    warnings: tuple[str, ...]
    trace: tuple[str, ...]
    present_circuit_count: int = 0
    solution_scope_id: str = "SCENARIO_COMBINED"
    solution_scope_name: str = "Senaryo birlikte"
    energized_circuit_ids: tuple[str, ...] = ()
    dryout_enabled: bool = False
    dryout_converged: bool = True
    dryout_iterations: int = 0
    dryout_cell_count: int = 0
    dryout_eligible_cell_count: int = 0
    dryout_fraction: float = 0.0
    dryout_material_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodalRouteScenarioResult:
    scenario_id: str
    scenario_name: str
    current_per_cable_a: float
    active_circuit_count: int
    regions: tuple[NodalRegionResult, ...]
    critical_region_id: str
    critical_region_name: str
    route_ampacity_per_cable_a: float
    maximum_conductor_temperature_c: float
    status: str
    trace: tuple[str, ...]
    solution_scope_id: str = "SCENARIO_COMBINED"
    solution_scope_name: str = "Senaryo birlikte"
    energized_circuit_ids: tuple[str, ...] = ()
    present_circuit_count: int = 0


@dataclass(frozen=True)
class NodalRouteStudyResult:
    reference: str
    scenarios: tuple[NodalRouteScenarioResult, ...]
    active_scenario_id: str
    iec_route_result: ThermalRouteStudyResult
    circuit_scope_scenarios: tuple[NodalRouteScenarioResult, ...] = ()
    method_validation: Any | None = None

    @property
    def active(self) -> NodalRouteScenarioResult:
        for scenario in self.scenarios:
            if scenario.scenario_id == self.active_scenario_id:
                return scenario
        return self.scenarios[-1]

    def scopes_for_scenario(self, scenario_id: str) -> tuple[NodalRouteScenarioResult, ...]:
        primary = tuple(item for item in self.scenarios if item.scenario_id == scenario_id)
        extras = tuple(item for item in self.circuit_scope_scenarios if item.scenario_id == scenario_id)
        return primary + extras

    def scope_result(self, scenario_id: str, scope_id: str) -> NodalRouteScenarioResult | None:
        return next((
            item for item in self.scopes_for_scenario(scenario_id)
            if item.solution_scope_id == scope_id
        ), None)


@dataclass(frozen=True)
class MeshConvergenceResult:
    region_id: str
    current_a: float
    coarse_cells: int
    refined_cells: int
    coarse_max_temperature_c: float
    refined_max_temperature_c: float
    difference_c: float
    difference_percent: float
    passed: bool
    coarse_ampacity_a: float = 0.0
    refined_ampacity_a: float = 0.0
    ampacity_difference_a: float = 0.0
    ampacity_difference_percent: float = 0.0
    solutions_converged: bool = True


@dataclass(frozen=True)
class _CableLocation:
    cable_id: str
    circuit_index: int
    phase: str
    parallel_index: int
    x_m: float
    depth_m: float


def _section_circuit_ids(section) -> tuple[str, ...]:
    """Physical circuit order, including standby/out-of-service circuits."""

    physical_ids = {item.circuit_id for item in section.physical_cables if item.active}
    ordered = tuple(item.circuit_id for item in section.circuits if item.circuit_id in physical_ids)
    if ordered:
        return ordered
    return tuple(dict.fromkeys(
        item.circuit_id for item in section.physical_cables if item.active
    ))


def _section_active_circuit_ids(section) -> tuple[str, ...]:
    present = set(_section_circuit_ids(section))
    active = tuple(item.circuit_id for item in section.circuits if item.active and item.circuit_id in present)
    return active or tuple(_section_circuit_ids(section))


def _all_section_physical_cables(section):
    present = set(_section_circuit_ids(section))
    values = [
        item for item in section.physical_cables
        if item.active and item.circuit_id in present and str(item.phase).upper() in {"A", "B", "C"}
    ]
    order = {circuit_id: index for index, circuit_id in enumerate(_section_circuit_ids(section))}
    values.sort(key=lambda item: (
        order.get(item.circuit_id, 9999),
        "ABC".index(str(item.phase).upper()),
        int(item.parallel_index),
    ))
    return tuple(values)


def _physical_current_factors(
    project: ProjectData,
    section,
    locations: tuple[_CableLocation, ...],
    reference_current_per_cable_a: float,
    energized_circuit_ids: tuple[str, ...],
) -> tuple[float, ...]:
    """Return per-cable multipliers relative to the scenario reference current.

    Kablo-Kanal circuit load, circuit load factor, physical-cable load factor and
    explicit current overrides remain authoritative for current sharing.  The
    resolved design currents are normalized to the electrical scenario current
    so DESIGN/N-1 scaling is retained without discarding per-circuit asymmetry.
    Cables outside the selected thermal scope remain in the material map but
    receive zero electrical/dielectric heat.
    """

    energized = set(energized_circuit_ids)
    resolved = {item.physical_cable_id: item for item in resolved_physical_cables(section, include_inactive=True)}
    parallel_count = max(1, int(project.cable.parallel_cables_per_phase or 1))
    nominal_total = float(project.design_basis.design_current_per_circuit_a or 0.0)
    nominal_per_cable = nominal_total / parallel_count if nominal_total > 0 else 0.0
    reference = max(0.0, float(reference_current_per_cable_a))
    if nominal_per_cable <= 0:
        positive_currents = [item.current_a for item in resolved.values() if item.current_a > 0]
        nominal_per_cable = sum(positive_currents) / len(positive_currents) if positive_currents else reference
    denominator = max(nominal_per_cable, 1e-12)

    factors: list[float] = []
    location_to_circuit = {
        item.physical_cable_id: item.circuit_id
        for item in section.physical_cables
    }
    for location in locations:
        circuit_id = location_to_circuit.get(location.cable_id, "")
        if circuit_id not in energized:
            factors.append(0.0)
            continue
        item = resolved.get(location.cable_id)
        design_current = float(item.current_a) if item is not None else 0.0
        factor = design_current / denominator if design_current > 0 else 1.0
        factors.append(max(0.0, factor))
    return tuple(factors)


def _positive(name: str, value: float, *, allow_zero: bool = False) -> float:
    value = float(value)
    if (allow_zero and value < 0) or (not allow_zero and value <= 0):
        raise NodalThermalInputError(
            f"{name} {'negatif olamaz' if allow_zero else 'sıfırdan büyük olmalıdır'}: {value}"
        )
    return value


def _template_values(project: ProjectData, region: ThermalRegion) -> dict[str, Any]:
    template = next((item for item in project.thermal_design.templates if item.template_id == region.template_id), None)
    if template is None:
        raise NodalThermalInputError(f"{region.region_id}: kesit şablonu bulunamadı: {region.template_id}")
    values = asdict(template)
    for key, value in dict(region.overrides or {}).items():
        if key in values and value not in (None, ""):
            values[key] = value
    return values


def _material_map(project: ProjectData) -> dict[str, ThermalMaterialData]:
    return {item.material_id: item for item in project.thermal_design.materials}


def _axis_edges(
    minimum: float,
    maximum: float,
    base_step: float,
    refined_step: float,
    refinement_centres: list[float],
    refinement_radius: float,
    exact_boundaries: list[float],
) -> np.ndarray:
    if maximum <= minimum:
        raise NodalThermalInputError("Nodal ağ eksen sınırları geçersiz.")
    points: set[float] = {float(minimum), float(maximum)}
    count = max(1, int(np.ceil((maximum - minimum) / base_step)))
    points.update(float(v) for v in np.linspace(minimum, maximum, count + 1))
    for centre in refinement_centres:
        lo = max(minimum, centre - refinement_radius)
        hi = min(maximum, centre + refinement_radius)
        if hi <= lo:
            continue
        n = max(1, int(np.ceil((hi - lo) / refined_step)))
        points.update(float(v) for v in np.linspace(lo, hi, n + 1))
    for value in exact_boundaries:
        if minimum < value < maximum:
            points.add(float(value))
    result = np.array(sorted(points), dtype=float)
    # Merge nearly coincident edges that can create ill-conditioned tiny cells.
    merged = [float(result[0])]
    min_gap = min(refined_step, base_step) * 0.15
    for value in result[1:]:
        if value - merged[-1] < min_gap and value < maximum - 1e-12:
            continue
        merged.append(float(value))
    if merged[-1] != maximum:
        merged[-1] = maximum
    return np.asarray(merged, dtype=float)


def _base_phase_positions(cable: CableData, profile: EffectiveThermalProfile) -> tuple[tuple[float, float], ...]:
    local = replace(cable, arrangement=profile.arrangement)
    section = RouteSection(
        name=profile.region_name,
        length_m=profile.length_m,
        burial_depth_m=profile.burial_depth_m,
        phase_spacing_m=profile.phase_spacing_m,
    )
    return cable_positions_m(local, section)


def _expanded_cable_locations(
    project: ProjectData,
    profile: EffectiveThermalProfile,
    active_circuit_count: int,
) -> tuple[_CableLocation, ...]:
    base = _base_phase_positions(project.cable, profile)
    phase_names = ("A", "B", "C") if len(base) == 3 else ("A",)
    values = _template_values(project, next(r for r in project.thermal_design.regions if r.region_id == profile.region_id))
    parallel_count = max(1, int(project.cable.parallel_cables_per_phase))
    parallel_spacing = _positive("Paralel kablo aralığı", values.get("parallel_cable_spacing_m", 0.20))
    active_circuit_count = max(1, int(active_circuit_count))
    circuit_spacing = _positive("Devreler arası mesafe", profile.circuit_spacing_m)
    circuit_offsets = [
        (index - (active_circuit_count - 1) / 2.0) * circuit_spacing
        for index in range(active_circuit_count)
    ]
    parallel_offsets = [
        (index - (parallel_count - 1) / 2.0) * parallel_spacing
        for index in range(parallel_count)
    ]
    locations: list[_CableLocation] = []
    for circuit_index, circuit_offset in enumerate(circuit_offsets, start=1):
        for phase, (x, depth) in zip(phase_names, base):
            for parallel_index, parallel_offset in enumerate(parallel_offsets, start=1):
                locations.append(_CableLocation(
                    f"C{circuit_index}-{phase}-{parallel_index}",
                    circuit_index,
                    phase,
                    parallel_index,
                    x + circuit_offset + parallel_offset,
                    depth,
                ))

    diameter = project.cable.overall_diameter_mm / 1000.0
    for i, first in enumerate(locations):
        for second in locations[i + 1:]:
            distance = sqrt((first.x_m - second.x_m) ** 2 + (first.depth_m - second.depth_m) ** 2)
            if distance < diameter - physical_cable_contact_tolerance_m(diameter):
                raise NodalThermalInputError(
                    f"{profile.region_id}: kablolar fiziksel olarak çakışıyor: {first.cable_id} / {second.cable_id}; "
                    f"eksen mesafesi={distance:.4f} m, dış çap={diameter:.4f} m"
                )
    return tuple(locations)


def _harmonic(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        raise NodalThermalInputError("Isıl iletkenlik sıfır veya negatif olamaz.")
    return 2.0 * a * b / (a + b)


class _NodalModel:
    def __init__(
        self,
        project: ProjectData,
        region: ThermalRegion,
        profile: EffectiveThermalProfile,
        active_circuit_count: int,
        mesh_scale: float = 1.0,
        explicit_locations: tuple[_CableLocation, ...] | None = None,
        value_overrides: dict[str, object] | None = None,
    ) -> None:
        self.project = project
        self.region = region
        self.profile = profile
        self.values = _template_values(project, region)
        if value_overrides:
            self.values.update(dict(value_overrides))
        self.materials = _material_map(project)
        self.locations = (
            tuple(explicit_locations)
            if explicit_locations is not None
            else _expanded_cable_locations(project, profile, active_circuit_count)
        )
        if not self.locations:
            raise NodalThermalInputError(f"{region.region_id}: fiziksel kablo konumu bulunmuyor.")
        self.active_circuit_count = active_circuit_count
        self.cable_radius_m = _positive("Kablo dış çapı", project.cable.overall_diameter_mm) / 2000.0

        half_width = _positive("Nodal alan yarı genişliği", self.values.get("nodal_domain_half_width_m", 4.0))
        depth = _positive("Nodal alan derinliği", self.values.get("nodal_domain_depth_m", 6.0))
        deepest_cable = max(item.depth_m for item in self.locations) + self.cable_radius_m
        # Fixed-temperature far boundaries must remain genuinely far from the
        # cable cluster. Otherwise a deeper cable can become artificially cooler
        # merely because it moves closer to the bottom boundary. The automatic
        # expansion below preserves the intended semi-infinite-soil behaviour.
        vertical_far_margin = max(3.5, 2.5 * profile.burial_depth_m)
        depth = max(depth, deepest_cable + vertical_far_margin)
        farthest_x = max(abs(item.x_m) for item in self.locations) + self.cable_radius_m
        horizontal_far_margin = max(2.5, 2.0 * profile.burial_depth_m)
        half_width = max(half_width, farthest_x + horizontal_far_margin)
        base_step = _positive("Temel ağ adımı", self.values.get("nodal_base_step_m", 0.20)) * mesh_scale
        refined_step = _positive("İnceltilmiş ağ adımı", self.values.get("nodal_refined_step_m", 0.05)) * mesh_scale
        if refined_step > base_step:
            refined_step = base_step
        refine_radius = _positive("Ağ inceltme yarıçapı", self.values.get("nodal_refinement_radius_m", 0.40))

        trench_center = float(self.values.get("trench_center_x_m", 0.0) or 0.0)
        trench_side_slope = max(0.0, float(self.values.get("trench_side_slope_h_to_v", 0.0) or 0.0))
        trench_bottom_half = profile.trench_width_m / 2.0
        trench_top_half = trench_bottom_half + trench_side_slope * profile.trench_depth_m
        trench_left = trench_center - trench_top_half
        trench_right = trench_center + trench_top_half
        cable_depths = [item.depth_m for item in self.locations]
        cable_xs = [item.x_m for item in self.locations]
        cable_top = min(cable_depths) - self.cable_radius_m
        cover_top = max(0.0, cable_top - profile.cable_cover_height_m)
        selected_top = max(0.0, cover_top - profile.selected_upper_fill_thickness_m)
        bedding_top = max(0.0, profile.trench_depth_m - profile.bedding_thickness_m)
        mean_depth = float(np.mean(cable_depths))
        bank_width = _positive("Duct bank genişliği", self.values.get("duct_bank_width_m", 0.90))
        bank_height = _positive("Duct bank yüksekliği", self.values.get("duct_bank_height_m", 0.55))
        bank_center = float(self.values.get("duct_bank_center_x_m", trench_center) or trench_center)
        bank_left, bank_right = bank_center - bank_width / 2.0, bank_center + bank_width / 2.0
        bank_top, bank_bottom = mean_depth - bank_height / 2.0, mean_depth + bank_height / 2.0
        slab_enabled = bool(self.values.get("cover_slab_enabled", False))
        slab_width = max(0.0, float(self.values.get("cover_slab_width_m", 0.0) or 0.0))
        slab_thickness = max(0.0, float(self.values.get("cover_slab_thickness_m", 0.0) or 0.0))
        slab_depth = max(0.0, float(self.values.get("cover_slab_depth_m", 0.0) or 0.0))
        trough_inner_width = max(0.0, float(self.values.get("trough_inner_width_m", 0.0) or 0.0))
        trough_inner_height = max(0.0, float(self.values.get("trough_inner_height_m", 0.0) or 0.0))
        trough_wall = max(0.0, float(self.values.get("trough_wall_thickness_m", 0.0) or 0.0))
        tunnel_width = max(0.0, float(self.values.get("tunnel_width_m", 0.0) or 0.0))
        tunnel_height = max(0.0, float(self.values.get("tunnel_height_m", 0.0) or 0.0))
        lining_thickness = max(0.0, float(self.values.get("tunnel_lining_thickness_m", 0.10) or 0.10))

        x_boundaries = [
            trench_left, trench_right,
            trench_center - trench_bottom_half, trench_center + trench_bottom_half,
            bank_left, bank_right,
        ]
        y_boundaries = [
            profile.surface_layer_thickness_m,
            selected_top,
            cover_top,
            bedding_top,
            profile.trench_depth_m,
            bank_top,
            bank_bottom,
            profile.groundwater_depth_m,
        ]
        if slab_enabled and slab_width > 0 and slab_thickness > 0:
            x_boundaries.extend([trench_center - slab_width / 2.0, trench_center + slab_width / 2.0])
            y_boundaries.extend([slab_depth - slab_thickness / 2.0, slab_depth + slab_thickness / 2.0])
        if trough_inner_width > 0 and trough_inner_height > 0 and trough_wall > 0:
            outer_w = trough_inner_width + 2.0 * trough_wall
            outer_h = trough_inner_height + 2.0 * trough_wall
            trough_bottom = min(profile.trench_depth_m, max(cable_depths) + self.cable_radius_m + trough_wall)
            trough_top = max(0.0, trough_bottom - outer_h)
            x_boundaries.extend([trench_center - outer_w / 2.0, trench_center + outer_w / 2.0])
            y_boundaries.extend([trough_top, trough_bottom, trough_top + trough_wall, trough_bottom - trough_wall])
        if tunnel_width > 0 and tunnel_height > 0:
            tunnel_bottom = min(profile.trench_depth_m, max(cable_depths) + self.cable_radius_m + lining_thickness)
            tunnel_top = max(0.0, tunnel_bottom - tunnel_height)
            x_boundaries.extend([trench_center - tunnel_width / 2.0, trench_center + tunnel_width / 2.0])
            y_boundaries.extend([tunnel_top, tunnel_bottom, tunnel_top + lining_thickness, tunnel_bottom - lining_thickness])
        custom_regions_raw = tuple(self.values.get("custom_material_regions", ()) or ())
        for region_data in custom_regions_raw:
            for point in region_data.get("vertices_m", ()):
                if len(point) >= 2:
                    x_boundaries.append(float(point[0]))
                    y_boundaries.append(float(point[1]))
        duct_slots_raw = tuple(self.values.get("duct_slots", ()) or ())
        for slot in duct_slots_raw:
            if not isinstance(slot, dict):
                continue
            slot_x = float(slot.get("x_m", 0.0) or 0.0)
            slot_depth = float(slot.get("depth_m", 0.0) or 0.0)
            slot_outer_radius = max(0.0005, float(slot.get("outer_diameter_m", 0.16) or 0.16) / 2.0)
            x_boundaries.extend([slot_x - slot_outer_radius, slot_x + slot_outer_radius])
            y_boundaries.extend([slot_depth - slot_outer_radius, slot_depth + slot_outer_radius])
        for location in self.locations:
            x_boundaries.extend([location.x_m - self.cable_radius_m, location.x_m + self.cable_radius_m])
            y_boundaries.extend([location.depth_m - self.cable_radius_m, location.depth_m + self.cable_radius_m])

        # Preserve the locked legacy domain unless an explicitly edited slope
        # or custom polygon actually extends beyond it. Groundwater and other
        # analytical boundaries may intentionally lie far outside the 2D box
        # and must not inflate the finite-volume mesh.
        required_half = max(abs(trench_left), abs(trench_right)) + 0.25
        if required_half > half_width:
            half_width = required_half
        if custom_regions_raw:
            custom_x = [float(point[0]) for raw in custom_regions_raw for point in raw.get("vertices_m", ()) if len(point) >= 2]
            custom_y = [float(point[1]) for raw in custom_regions_raw for point in raw.get("vertices_m", ()) if len(point) >= 2]
            if custom_x:
                half_width = max(half_width, max(abs(value) for value in custom_x) + 0.50)
            if custom_y:
                depth = max(depth, max(custom_y) + 0.75)
        self.x_edges = _axis_edges(-half_width, half_width, base_step, refined_step, cable_xs, refine_radius, x_boundaries)
        self.y_edges = _axis_edges(0.0, depth, base_step, refined_step, cable_depths, refine_radius, y_boundaries)
        self.x_centres = 0.5 * (self.x_edges[:-1] + self.x_edges[1:])
        self.y_centres = 0.5 * (self.y_edges[:-1] + self.y_edges[1:])
        self.dx = np.diff(self.x_edges)
        self.dy = np.diff(self.y_edges)
        self.nx = len(self.x_centres)
        self.ny = len(self.y_centres)
        self.cell_count = self.nx * self.ny
        max_cells = int(self.values.get("nodal_max_cells", 30000))
        if self.cell_count > max_cells:
            raise NodalThermalInputError(
                f"{region.region_id}: ağ hücre sayısı {self.cell_count}, sınır {max_cells}. "
                "Ağ adımını büyütün veya çözüm alanını küçültün."
            )

        self.material_ids, self.kx, self.ky, self.cable_masks = self._build_material_grid(
            trench_left, trench_right, cover_top, selected_top, bedding_top,
            bank_left, bank_right, bank_top, bank_bottom,
            trench_center, trench_bottom_half, trench_side_slope, custom_regions_raw,
            duct_slots_raw,
        )
        self._wet_kx = self.kx.copy()
        self._wet_ky = self.ky.copy()
        self._groundwater_depth_m = float(profile.groundwater_depth_m)
        self._dryout_profiles: dict[str, SoilDryoutProfile] = {}
        for material_id, material in self.materials.items():
            try:
                resolved = material_dryout_profile(material)
            except SoilDryoutInputError as exc:
                raise NodalThermalInputError(str(exc)) from exc
            if resolved is not None:
                if resolved.critical_temperature_c <= float(profile.ambient_temperature_c):
                    raise NodalThermalInputError(
                        f"{material_id}: kritik kuruma sıcaklığı ortam sıcaklığından büyük olmalıdır."
                    )
                self._dryout_profiles[material_id] = resolved
        self._last_dryout_state = NodalDryoutState(False, True, 0, 0, 0, 0.0, (), 0.0, ())
        self.matrix, self.boundary_rhs = self._assemble_matrix()
        self._factor = factorized(self.matrix)

    def _get_material(self, material_id: str, label: str) -> ThermalMaterialData:
        material = self.materials.get(material_id)
        if material is None:
            raise NodalThermalInputError(f"{self.region.region_id}: {label} malzemesi bulunamadı: {material_id}")
        if material.thermal_resistivity_km_w <= 0:
            raise NodalThermalInputError(f"{material.name}: ısıl özdirenç sıfırdan büyük olmalıdır.")
        return material

    def _build_material_grid(
        self,
        trench_left: float,
        trench_right: float,
        cover_top: float,
        selected_top: float,
        bedding_top: float,
        bank_left: float,
        bank_right: float,
        bank_top: float,
        bank_bottom: float,
        trench_center: float,
        trench_bottom_half: float,
        trench_side_slope: float,
        custom_regions_raw: tuple[object, ...],
        duct_slots_raw: tuple[object, ...],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[np.ndarray, ...]]:
        p = self.profile
        v = self.values
        native = p.native_soil
        bedding = p.bedding
        side = p.side_backfill
        cover = p.cable_cover
        selected = p.selected_upper_fill
        general = p.general_fill
        surface = p.surface
        duct = self._get_material(str(v.get("duct_material_id", "MAT-DUCT-01")), "Duct")
        duct_fill = self._get_material(str(v.get("duct_fill_material_id", "MAT-AIR-01")), "Duct içi")
        grout = self._get_material(str(v.get("grout_material_id", "MAT-CONCRETE-01")), "Grout")
        cover_slab_id = str(v.get("cover_slab_material_id", "MAT-CONCRETE-01") or "MAT-CONCRETE-01")
        cover_slab = self._get_material(cover_slab_id, "Koruma plakası")
        trough_material_id = str(v.get("trough_material_id", "MAT-CONCRETE-01") or "MAT-CONCRETE-01")
        trough_material = self._get_material(trough_material_id, "Beton kanal")
        tunnel_lining_id = str(v.get("tunnel_lining_material_id", trough_material_id) or trough_material_id)
        tunnel_lining = self._get_material(tunnel_lining_id, "Tünel kaplaması")
        void_material_id = str(v.get("channel_void_material_id", "MAT-AIR-01") or "MAT-AIR-01")
        void_material = self._get_material(void_material_id, "Kanal/tünel iç ortamı")
        duct_rho = float(v.get("duct_thermal_resistivity_km_w", 0.0) or 0.0)
        duct_fill_rho = float(v.get("duct_fill_thermal_resistivity_km_w", 0.0) or 0.0)
        grout_rho = float(v.get("grout_thermal_resistivity_km_w", 0.0) or 0.0)
        if duct_rho > 0:
            duct = replace(duct, thermal_resistivity_km_w=duct_rho)
        if duct_fill_rho > 0:
            duct_fill = replace(duct_fill, thermal_resistivity_km_w=duct_fill_rho)
        if grout_rho > 0:
            grout = replace(grout, thermal_resistivity_km_w=grout_rho)

        material_ids = np.empty((self.ny, self.nx), dtype=object)
        kx = np.empty((self.ny, self.nx), dtype=float)
        ky = np.empty((self.ny, self.nx), dtype=float)
        surface_thickness = max(0.0, p.surface_layer_thickness_m)
        groundwater_depth = p.groundwater_depth_m
        gw_multiplier = max(1.0, float(v.get("groundwater_conductivity_multiplier", 1.25)))
        installation = p.installation_type.upper()
        duct_inner = _positive("Duct iç çapı", v.get("duct_inner_diameter_m", 0.13)) / 2.0
        duct_outer = _positive("Duct dış çapı", v.get("duct_outer_diameter_m", 0.16)) / 2.0
        slab_enabled = bool(v.get("cover_slab_enabled", False))
        slab_width = max(0.0, float(v.get("cover_slab_width_m", 0.0) or 0.0))
        slab_thickness = max(0.0, float(v.get("cover_slab_thickness_m", 0.0) or 0.0))
        slab_depth = max(0.0, float(v.get("cover_slab_depth_m", 0.0) or 0.0))
        slab_center = float(v.get("trench_center_x_m", 0.0) or 0.0)
        trough_inner_width = max(0.0, float(v.get("trough_inner_width_m", 0.0) or 0.0))
        trough_inner_height = max(0.0, float(v.get("trough_inner_height_m", 0.0) or 0.0))
        trough_wall = max(0.0, float(v.get("trough_wall_thickness_m", 0.0) or 0.0))
        tunnel_width = max(0.0, float(v.get("tunnel_width_m", 0.0) or 0.0))
        tunnel_height = max(0.0, float(v.get("tunnel_height_m", 0.0) or 0.0))
        tunnel_lining_thickness = max(0.01, float(v.get("tunnel_lining_thickness_m", 0.10) or 0.10))
        cable_depth_values = [item.depth_m for item in self.locations]
        trough_outer_width = trough_inner_width + 2.0 * trough_wall
        trough_outer_height = trough_inner_height + 2.0 * trough_wall
        trough_bottom = min(p.trench_depth_m, max(cable_depth_values) + self.cable_radius_m + trough_wall)
        trough_top = max(0.0, trough_bottom - trough_outer_height)
        tunnel_bottom = min(p.trench_depth_m, max(cable_depth_values) + self.cable_radius_m + tunnel_lining_thickness)
        tunnel_top = max(0.0, tunnel_bottom - tunnel_height)
        if duct_outer <= duct_inner:
            raise NodalThermalInputError("Duct dış çapı iç çapından büyük olmalıdır.")

        custom_regions: list[tuple[int, tuple[tuple[float, float], ...], ThermalMaterialData]] = []
        for raw in custom_regions_raw:
            if not isinstance(raw, dict):
                continue
            material_id = str(raw.get("material_id", ""))
            vertices = tuple(
                (float(point[0]), float(point[1]))
                for point in raw.get("vertices_m", ())
                if isinstance(point, (list, tuple)) and len(point) >= 2
            )
            if len(vertices) < 3:
                continue
            custom_regions.append((
                int(raw.get("priority", 100)),
                vertices,
                self._get_material(material_id, f"Özel malzeme bölgesi {raw.get('region_id', '')}"),
            ))
        custom_regions.sort(key=lambda item: item[0])

        duct_slots: list[tuple[float, float, float, float]] = []
        for raw in duct_slots_raw:
            if not isinstance(raw, dict):
                continue
            inner_radius = max(
                0.0005,
                float(raw.get("inner_diameter_m", duct_inner * 2.0) or duct_inner * 2.0) / 2.0,
            )
            outer_radius = max(
                inner_radius + 0.0005,
                float(raw.get("outer_diameter_m", duct_outer * 2.0) or duct_outer * 2.0) / 2.0,
            )
            duct_slots.append((
                float(raw.get("x_m", 0.0) or 0.0),
                float(raw.get("depth_m", 0.0) or 0.0),
                inner_radius,
                outer_radius,
            ))

        def assign_material(x: float, y: float) -> ThermalMaterialData:
            material = native
            local_half = trench_bottom_half + trench_side_slope * max(0.0, p.trench_depth_m - y)
            if abs(x - trench_center) <= local_half and 0.0 <= y <= p.trench_depth_m:
                if y >= bedding_top:
                    material = bedding
                elif y >= cover_top:
                    material = side
                elif y >= selected_top:
                    material = cover
                else:
                    material = selected if p.selected_upper_fill_thickness_m > 0 else general
            for _priority, vertices, region_material in custom_regions:
                if _point_in_polygon(x, y, vertices):
                    material = region_material
            if installation in {THERMAL_INSTALL_DUCT_BANK, THERMAL_INSTALL_HDD}:
                if bank_left <= x <= bank_right and bank_top <= y <= bank_bottom:
                    material = grout
                slot_geometry = duct_slots or [
                    (location.x_m, location.depth_m, duct_inner, duct_outer)
                    for location in self.locations
                ]
                for slot_x, slot_depth, slot_inner, slot_outer in slot_geometry:
                    r = sqrt((x - slot_x) ** 2 + (y - slot_depth) ** 2)
                    if slot_inner < r <= slot_outer:
                        material = duct
                    elif r <= slot_inner:
                        material = duct_fill
            elif installation == THERMAL_INSTALL_CONCRETE_TROUGH and trough_outer_width > 0 and trough_outer_height > 0:
                outer = (
                    abs(x - slab_center) <= trough_outer_width / 2.0
                    and trough_top <= y <= trough_bottom
                )
                inner = (
                    abs(x - slab_center) <= trough_inner_width / 2.0
                    and trough_top + trough_wall <= y <= trough_bottom - trough_wall
                )
                if outer:
                    material = void_material if inner else trough_material
            elif installation == THERMAL_INSTALL_TUNNEL and tunnel_width > 0 and tunnel_height > 0:
                outer = (
                    abs(x - slab_center) <= tunnel_width / 2.0
                    and tunnel_top <= y <= tunnel_bottom
                )
                inner = (
                    abs(x - slab_center) <= max(0.0, tunnel_width / 2.0 - tunnel_lining_thickness)
                    and tunnel_top + tunnel_lining_thickness <= y <= tunnel_bottom - tunnel_lining_thickness
                )
                if outer:
                    material = void_material if inner else tunnel_lining
            if surface is not None and surface_thickness > 0 and y <= surface_thickness:
                material = surface
            if (
                slab_enabled and slab_width > 0 and slab_thickness > 0
                and abs(x - slab_center) <= slab_width / 2.0
                and abs(y - slab_depth) <= slab_thickness / 2.0
            ):
                material = cover_slab
            return material

        cable_masks: list[np.ndarray] = []
        for _ in self.locations:
            cable_masks.append(np.zeros((self.ny, self.nx), dtype=bool))

        cable_k = _positive("Kablo eşdeğer iletkenliği", v.get("cable_effective_conductivity_w_mk", 12.0))
        for iy, y in enumerate(self.y_centres):
            for ix, x in enumerate(self.x_centres):
                cable_index = None
                for index, location in enumerate(self.locations):
                    if (x - location.x_m) ** 2 + (y - location.depth_m) ** 2 <= self.cable_radius_m**2:
                        cable_index = index
                        break
                if cable_index is not None:
                    material_ids[iy, ix] = "CABLE"
                    kx[iy, ix] = cable_k
                    ky[iy, ix] = cable_k
                    cable_masks[cable_index][iy, ix] = True
                    continue
                material = assign_material(float(x), float(y))
                rho = float(material.thermal_resistivity_km_w)
                k = 1.0 / rho
                anisotropy = max(1e-6, float(material.anisotropy_ratio or 1.0))
                kx_value = k * sqrt(anisotropy)
                ky_value = k / sqrt(anisotropy)
                if y >= groundwater_depth and material.category in {
                    "NATIVE_SOIL", "THERMAL_BACKFILL", "GENERAL_FILL", "CONCRETE_GROUT"
                }:
                    kx_value *= gw_multiplier
                    ky_value *= gw_multiplier
                material_ids[iy, ix] = material.material_id
                kx[iy, ix] = kx_value
                ky[iy, ix] = ky_value

        # Guarantee at least one source cell for each cable even on a coarse mesh.
        for index, (mask, location) in enumerate(zip(cable_masks, self.locations)):
            if not np.any(mask):
                ix = int(np.argmin(np.abs(self.x_centres - location.x_m)))
                iy = int(np.argmin(np.abs(self.y_centres - location.depth_m)))
                mask[iy, ix] = True
                material_ids[iy, ix] = "CABLE"
                kx[iy, ix] = cable_k
                ky[iy, ix] = cable_k
        return material_ids, kx, ky, tuple(cable_masks)

    def _assemble_matrix(self) -> tuple[csc_matrix, np.ndarray]:
        rows: list[int] = []
        cols: list[int] = []
        data: list[float] = []
        rhs = np.zeros(self.cell_count, dtype=float)
        surface_type = str(self.values.get("surface_boundary_type", "FIXED_TEMPERATURE")).upper()
        surface_temp = float(self.values.get("surface_temperature_c", self.profile.ambient_temperature_c))
        if abs(surface_temp) < 1e-12:
            surface_temp = self.profile.ambient_temperature_c
        deep_temp = float(self.values.get("deep_soil_temperature_c", self.profile.ambient_temperature_c))
        if abs(deep_temp) < 1e-12:
            deep_temp = self.profile.ambient_temperature_c
        h_surface = _positive(
            "Yüzey ısı geçiş katsayısı",
            self.values.get("surface_heat_transfer_w_m2k", 12.0),
        )

        def index(iy: int, ix: int) -> int:
            return iy * self.nx + ix

        for iy in range(self.ny):
            for ix in range(self.nx):
                p = index(iy, ix)
                diagonal = 0.0
                # West/east faces.
                if ix > 0:
                    distance = self.x_centres[ix] - self.x_centres[ix - 1]
                    g = _harmonic(self.kx[iy, ix], self.kx[iy, ix - 1]) * self.dy[iy] / distance
                    rows.append(p); cols.append(index(iy, ix - 1)); data.append(-g)
                    diagonal += g
                else:
                    g = self.kx[iy, ix] * self.dy[iy] / (self.dx[ix] / 2.0)
                    diagonal += g; rhs[p] += g * deep_temp
                if ix < self.nx - 1:
                    distance = self.x_centres[ix + 1] - self.x_centres[ix]
                    g = _harmonic(self.kx[iy, ix], self.kx[iy, ix + 1]) * self.dy[iy] / distance
                    rows.append(p); cols.append(index(iy, ix + 1)); data.append(-g)
                    diagonal += g
                else:
                    g = self.kx[iy, ix] * self.dy[iy] / (self.dx[ix] / 2.0)
                    diagonal += g; rhs[p] += g * deep_temp

                # North/top and south/bottom faces.
                if iy > 0:
                    distance = self.y_centres[iy] - self.y_centres[iy - 1]
                    g = _harmonic(self.ky[iy, ix], self.ky[iy - 1, ix]) * self.dx[ix] / distance
                    rows.append(p); cols.append(index(iy - 1, ix)); data.append(-g)
                    diagonal += g
                else:
                    if surface_type == "CONVECTIVE":
                        resistance = self.dy[iy] / (2.0 * self.ky[iy, ix]) + 1.0 / h_surface
                        g = self.dx[ix] / resistance
                    else:
                        g = self.ky[iy, ix] * self.dx[ix] / (self.dy[iy] / 2.0)
                    diagonal += g; rhs[p] += g * surface_temp
                if iy < self.ny - 1:
                    distance = self.y_centres[iy + 1] - self.y_centres[iy]
                    g = _harmonic(self.ky[iy, ix], self.ky[iy + 1, ix]) * self.dx[ix] / distance
                    rows.append(p); cols.append(index(iy + 1, ix)); data.append(-g)
                    diagonal += g
                else:
                    g = self.ky[iy, ix] * self.dx[ix] / (self.dy[iy] / 2.0)
                    diagonal += g; rhs[p] += g * deep_temp

                rows.append(p); cols.append(p); data.append(diagonal)

        matrix = coo_matrix((data, (rows, cols)), shape=(self.cell_count, self.cell_count)).tocsc()
        return matrix, rhs

    def _apply_dryout_mask(self, dry_mask: np.ndarray) -> None:
        self.kx = self._wet_kx.copy()
        self.ky = self._wet_ky.copy()
        if not np.any(dry_mask):
            return
        for material_id, profile in self._dryout_profiles.items():
            mask = dry_mask & (self.material_ids == material_id)
            if not np.any(mask):
                continue
            material = self.materials[material_id]
            k = 1.0 / profile.dry_thermal_resistivity_km_w
            anisotropy = max(1e-6, float(material.anisotropy_ratio or 1.0))
            self.kx[mask] = k * sqrt(anisotropy)
            self.ky[mask] = k / sqrt(anisotropy)

    def _solve_sources_with_current_matrix(
        self,
        cable_heat_w_m: np.ndarray,
        point_heat_sources: tuple[tuple[float, float, float], ...] = (),
    ) -> tuple[np.ndarray, float, float, float]:
        self.matrix, self.boundary_rhs = self._assemble_matrix()
        self._factor = factorized(self.matrix)
        return self.solve_sources(cable_heat_w_m, point_heat_sources)

    def solve_sources_with_dryout(
        self,
        cable_heat_w_m: np.ndarray,
        point_heat_sources: tuple[tuple[float, float, float], ...] = (),
        *,
        maximum_iterations: int = 20,
    ) -> tuple[np.ndarray, float, float, float, NodalDryoutState]:
        """Solve nonlinear critical-isotherm dryout by monotonic mask expansion.

        A cell changes from its moist material conductivity to the material's
        dry-state conductivity when its steady-state temperature reaches the
        tested critical dryout temperature. Cells at/below a declared water
        table are kept moist. The dry zone is monotonic during the fixed-point
        iteration, which avoids threshold oscillation and matches the intended
        steady dry-zone interpretation.
        """
        if not self._dryout_profiles:
            field, boundary, balance, residual = self.solve_sources(cable_heat_w_m, point_heat_sources)
            state = NodalDryoutState(
                False, True, 0, 0, 0, 0.0, (), float(np.max(field)),
                ("Kritik-izoterm kuruma malzemesi tanımlı değil.",),
            )
            self._last_dryout_state = state
            return field, boundary, balance, residual, state

        self._apply_dryout_mask(np.zeros((self.ny, self.nx), dtype=bool))
        field, boundary, balance, residual = self._solve_sources_with_current_matrix(
            cable_heat_w_m, point_heat_sources
        )
        dry_mask = np.zeros((self.ny, self.nx), dtype=bool)
        eligible = np.zeros((self.ny, self.nx), dtype=bool)
        depth_grid = self.y_centres[:, None]
        for material_id in self._dryout_profiles:
            eligible |= (self.material_ids == material_id) & (depth_grid < self._groundwater_depth_m)

        converged = False
        iteration = 0
        for iteration in range(1, maximum_iterations + 1):
            candidate = dry_mask.copy()
            for material_id, profile in self._dryout_profiles.items():
                candidate |= (
                    (self.material_ids == material_id)
                    & (depth_grid < self._groundwater_depth_m)
                    & (field >= profile.critical_temperature_c)
                )
            candidate &= eligible
            if np.array_equal(candidate, dry_mask):
                converged = True
                break
            dry_mask = candidate
            self._apply_dryout_mask(dry_mask)
            field, boundary, balance, residual = self._solve_sources_with_current_matrix(
                cable_heat_w_m, point_heat_sources
            )
        if not converged:
            # One final consistency check is useful when the last iteration just
            # reached a stable mask.
            check = dry_mask.copy()
            for material_id, profile in self._dryout_profiles.items():
                check |= (
                    (self.material_ids == material_id)
                    & (depth_grid < self._groundwater_depth_m)
                    & (field >= profile.critical_temperature_c)
                )
            converged = bool(np.array_equal(check & eligible, dry_mask))

        dry_count = int(np.count_nonzero(dry_mask))
        eligible_count = int(np.count_nonzero(eligible))
        material_ids = tuple(sorted({
            str(value) for value in self.material_ids[dry_mask].tolist()
        })) if dry_count else ()
        state = NodalDryoutState(
            True,
            converged,
            iteration,
            dry_count,
            eligible_count,
            dry_count / max(eligible_count, 1),
            material_ids,
            float(np.max(field)),
            (
                f"Kritik-izoterm nodal kuruma: eligible={eligible_count}; dry={dry_count}; "
                f"fraction={dry_count / max(eligible_count, 1):.6f}; iterations={iteration}; converged={converged}.",
                "Yeraltı su seviyesindeki/altındaki hücreler kuruma maskesine alınmadı.",
            ),
        )
        self._last_dryout_state = state
        return field, boundary, balance, residual, state

    def solve_sources(
        self,
        cable_heat_w_m: np.ndarray,
        point_heat_sources: tuple[tuple[float, float, float], ...] = (),
    ) -> tuple[np.ndarray, float, float, float]:
        if len(cable_heat_w_m) != len(self.locations):
            raise NodalThermalInputError("Kablo ısı kaynağı sayısı ile geometri uyuşmuyor.")
        rhs = self.boundary_rhs.copy()
        for heat, mask in zip(cable_heat_w_m, self.cable_masks):
            cells = np.flatnonzero(mask.ravel())
            if cells.size == 0:
                raise NodalThermalInputError("Kablo ısı kaynağı için hücre bulunamadı.")
            areas = np.repeat(self.dy, self.nx).reshape(self.ny, self.nx)[mask] * np.tile(self.dx, self.ny).reshape(self.ny, self.nx)[mask]
            weights = areas / np.sum(areas)
            rhs[cells] += float(heat) * weights
        point_total = 0.0
        for x_m, depth_m, heat_w_m in point_heat_sources:
            heat = float(heat_w_m)
            if abs(heat) <= 0.0:
                continue
            ix = int(np.argmin(np.abs(self.x_centres - float(x_m))))
            iy = int(np.argmin(np.abs(self.y_centres - float(depth_m))))
            rhs[iy * self.nx + ix] += heat
            point_total += heat
        solution = np.asarray(self._factor(rhs), dtype=float)
        residual = float(np.max(np.abs(self.matrix.dot(solution) - rhs)))
        field = solution.reshape((self.ny, self.nx))
        boundary_heat = self._boundary_heat(field)
        source_heat = float(np.sum(cable_heat_w_m)) + point_total
        balance = abs(boundary_heat - source_heat) / max(abs(source_heat), 1e-12) * 100.0
        return field, boundary_heat, balance, residual

    def _boundary_heat(self, field: np.ndarray) -> float:
        surface_type = str(self.values.get("surface_boundary_type", "FIXED_TEMPERATURE")).upper()
        surface_temp = float(self.values.get("surface_temperature_c", self.profile.ambient_temperature_c))
        if abs(surface_temp) < 1e-12:
            surface_temp = self.profile.ambient_temperature_c
        deep_temp = float(self.values.get("deep_soil_temperature_c", self.profile.ambient_temperature_c))
        if abs(deep_temp) < 1e-12:
            deep_temp = self.profile.ambient_temperature_c
        h_surface = float(self.values.get("surface_heat_transfer_w_m2k", 12.0))
        total = 0.0
        for iy in range(self.ny):
            gw = self.kx[iy, 0] * self.dy[iy] / (self.dx[0] / 2.0)
            ge = self.kx[iy, -1] * self.dy[iy] / (self.dx[-1] / 2.0)
            total += gw * (field[iy, 0] - deep_temp)
            total += ge * (field[iy, -1] - deep_temp)
        for ix in range(self.nx):
            if surface_type == "CONVECTIVE":
                resistance = self.dy[0] / (2.0 * self.ky[0, ix]) + 1.0 / h_surface
                gn = self.dx[ix] / resistance
            else:
                gn = self.ky[0, ix] * self.dx[ix] / (self.dy[0] / 2.0)
            gs = self.ky[-1, ix] * self.dx[ix] / (self.dy[-1] / 2.0)
            total += gn * (field[0, ix] - surface_temp)
            total += gs * (field[-1, ix] - deep_temp)
        return float(total)

    def cable_jacket_temperatures(self, field: np.ndarray) -> np.ndarray:
        values = []
        for mask in self.cable_masks:
            values.append(float(np.mean(field[mask])))
        return np.asarray(values, dtype=float)


def _section_for_profile(profile: EffectiveThermalProfile) -> RouteSection:
    return RouteSection(
        name=f"{profile.region_id} {profile.region_name}",
        length_m=profile.length_m,
        section_type=profile.installation_type,
        burial_depth_m=profile.burial_depth_m,
        soil_thermal_resistivity_km_w=profile.native_soil.thermal_resistivity_km_w,
        ambient_temperature_c=profile.ambient_temperature_c,
        phase_spacing_m=profile.phase_spacing_m,
    )


def _internal_thermal_chains(cable: CableData, lambda1: float) -> tuple[float, float]:
    """IEC 60287-1-1 internal chains between conductor and jacket.

    Returns (internal_chain, dielectric_internal_chain) so that
    theta_c - theta_jacket = Wc * internal_chain + Wd * dielectric_internal_chain.
    """

    internal = resolve_internal_thermal_resistance(cable)
    n = max(1, int(cable.conductors_per_cable))
    lambda2 = max(0.0, float(cable.armour_loss_factor))
    internal_chain = (
        internal.t1_km_w
        + n * (1.0 + lambda1) * internal.t2_km_w
        + n * (1.0 + lambda1 + lambda2) * internal.t3_km_w
    )
    dielectric_internal_chain = 0.5 * internal.t1_km_w + n * (internal.t2_km_w + internal.t3_km_w)
    return internal_chain, dielectric_internal_chain


def _solve_at_current(
    model: _NodalModel,
    current_a: float,
    lambda1: float,
    max_iterations: int = 40,
    tolerance_c: float = 0.02,
    point_heat_sources: tuple[tuple[float, float, float], ...] = (),
    current_factors: tuple[float, ...] | None = None,
) -> tuple[np.ndarray, tuple[NodalCableResult, ...], int, bool, float, float, float, float]:
    cable = model.project.cable
    current = _positive("Nodal tasarım akımı", current_a, allow_zero=True)
    if current_factors is None:
        factors = np.ones(len(model.locations), dtype=float)
    else:
        if len(current_factors) != len(model.locations):
            raise NodalThermalInputError(
                f"{model.region.region_id}: akım çarpanı sayısı fiziksel kablo sayısıyla eşleşmiyor."
            )
        factors = np.asarray(current_factors, dtype=float)
        if np.any(factors < 0):
            raise NodalThermalInputError("Nodal kablo akım çarpanları negatif olamaz.")
    cable_currents = current * factors
    wd = dielectric_loss_w_m(cable)
    dielectric_losses = np.where(factors > 0.0, wd, 0.0)
    lambda2 = max(0.0, float(cable.armour_loss_factor))
    internal_chain, dielectric_internal_chain = _internal_thermal_chains(cable, lambda1)
    temperatures = np.full(len(model.locations), max(20.0, model.profile.ambient_temperature_c + 20.0), dtype=float)
    field = np.full((model.ny, model.nx), model.profile.ambient_temperature_c, dtype=float)
    conductor_losses = np.zeros(len(model.locations), dtype=float)
    sheath_losses = np.zeros(len(model.locations), dtype=float)
    armour_losses = np.zeros(len(model.locations), dtype=float)
    heat_sources = np.zeros(len(model.locations), dtype=float)
    boundary_heat = balance = residual = 0.0
    converged = False

    for iteration in range(1, max_iterations + 1):
        for index, temperature in enumerate(temperatures):
            eval_temperature = max(-273.149999, min(float(temperature), cable.max_temperature_c + 120.0))
            _, rac_km = ac_resistance_at_temperature_ohm_km(
                cable, eval_temperature, model.profile.phase_spacing_m
            )
            conductor_losses[index] = cable_currents[index] ** 2 * rac_km / 1000.0
        sheath_losses = conductor_losses * max(0.0, lambda1)
        armour_losses = conductor_losses * lambda2
        heat_sources = conductor_losses + sheath_losses + armour_losses + dielectric_losses
        field, boundary_heat, balance, residual, _dryout_state = model.solve_sources_with_dryout(
            heat_sources, point_heat_sources
        )
        jacket = model.cable_jacket_temperatures(field)
        internal_rise = conductor_losses * internal_chain + dielectric_losses * dielectric_internal_chain
        new_temperatures = jacket + internal_rise
        delta = float(np.max(np.abs(new_temperatures - temperatures)))
        temperatures = 0.55 * new_temperatures + 0.45 * temperatures
        if delta <= tolerance_c:
            temperatures = new_temperatures
            converged = True
            break

    jacket = model.cable_jacket_temperatures(field)
    cable_results: list[NodalCableResult] = []
    for index, location in enumerate(model.locations):
        total = heat_sources[index]
        t4 = (
            max(0.0, (jacket[index] - model.profile.ambient_temperature_c) / total)
            if total > 1e-12 else 0.0
        )
        cable_results.append(NodalCableResult(
            location.cable_id,
            location.circuit_index,
            location.phase,
            location.parallel_index,
            location.x_m,
            location.depth_m,
            float(cable_currents[index]),
            float(temperatures[index]),
            float(jacket[index]),
            float(conductor_losses[index]),
            float(sheath_losses[index]),
            float(armour_losses[index]),
            float(dielectric_losses[index]),
            float(total),
            float(t4),
        ))
    return (
        field,
        tuple(cable_results),
        iteration,
        converged,
        float(np.sum(heat_sources)),
        boundary_heat,
        balance,
        residual,
    )


_NONCONVERGED_RETRY_ITERATIONS = 120


def _solve_at_current_with_retry(
    model: _NodalModel,
    current_a: float,
    lambda1: float,
    *,
    max_iterations: int,
    tolerance_c: float,
    point_heat_sources: tuple[tuple[float, float, float], ...] = (),
    current_factors: tuple[float, ...] | None = None,
) -> tuple[tuple[np.ndarray, tuple[NodalCableResult, ...], int, bool, float, float, float, float], int]:
    """Solve once; repeat a non-converged evaluation with a larger iteration budget.

    Returns the solution tuple of ``_solve_at_current`` and the number of solves.
    The caller must still check the ``converged`` flag (index 3).
    """

    solved = _solve_at_current(
        model, current_a, lambda1, max_iterations=max_iterations, tolerance_c=tolerance_c,
        point_heat_sources=point_heat_sources, current_factors=current_factors,
    )
    if solved[3] or max_iterations >= _NONCONVERGED_RETRY_ITERATIONS:
        return solved, 1
    retried = _solve_at_current(
        model, current_a, lambda1, max_iterations=_NONCONVERGED_RETRY_ITERATIONS, tolerance_c=tolerance_c,
        point_heat_sources=point_heat_sources, current_factors=current_factors,
    )
    return retried, 2


def _find_ampacity(
    model: _NodalModel,
    lambda1: float,
    design_current_a: float,
    iec_ampacity_a: float,
    point_heat_sources: tuple[tuple[float, float, float], ...] = (),
    current_factors: tuple[float, ...] | None = None,
) -> tuple[float, int]:
    limit = model.project.cable.max_temperature_c
    low = 0.0
    high = max(100.0, design_current_a * 1.25, iec_ampacity_a * 1.20)
    evaluations = 0

    def evaluate(current_a: float) -> float:
        nonlocal evaluations
        solved, solves = _solve_at_current_with_retry(
            model, current_a, lambda1, max_iterations=25, tolerance_c=0.04,
            point_heat_sources=point_heat_sources, current_factors=current_factors,
        )
        evaluations += solves
        if not solved[3]:
            raise NodalThermalInputError(
                f"{model.region.region_id}: {current_a:.1f} A akımda nodal sıcaklık iterasyonu "
                f"{solved[2]} iterasyonda yakınsamadı; ampacity yakınsamamış sıcaklıkla belirlenemez."
            )
        return max(item.conductor_temperature_c for item in solved[1])

    for _ in range(10):
        if evaluate(high) >= limit:
            break
        high *= 1.5
    else:
        raise NodalThermalInputError(
            f"{model.region.region_id}: ampacity üst sınırı bulunamadı; {high:.1f} A değerinde bile sıcaklık limiti aşılmadı."
        )

    for _ in range(22):
        mid = 0.5 * (low + high)
        if evaluate(mid) > limit:
            high = mid
        else:
            low = mid
        if high - low <= max(0.2, high * 2e-4):
            break
    return 0.5 * (low + high), evaluations


def solve_nodal_region(
    project: ProjectData,
    region_id: str,
    design_current_per_cable_a: float,
    active_circuit_count: int,
    regional_lambda1: float,
    iec_result: Iec60287SectionResult,
    scenario_id: str = "DESIGN",
    mesh_scale: float = 1.0,
    calculate_ampacity: bool = True,
    energized_circuit_ids: tuple[str, ...] | None = None,
    solution_scope_id: str = "SCENARIO_COMBINED",
    solution_scope_name: str = "Senaryo birlikte",
) -> NodalRegionResult:
    region = next((item for item in project.thermal_design.regions if item.region_id == region_id), None)
    if region is None:
        raise NodalThermalInputError(f"Termal bölge bulunamadı: {region_id}")
    profile = resolve_thermal_region(project.thermal_design, region, project.cable)
    values = _template_values(project, region)
    if not bool(values.get("nodal_enabled", True)):
        raise NodalThermalInputError(f"{region_id}: 2D nodal çözüm şablonda devre dışı.")

    explicit_locations = None
    value_overrides: dict[str, object] = {}
    point_heat_sources: tuple[tuple[float, float, float], ...] = ()
    current_factors: tuple[float, ...] | None = None
    linked_section = linked_cross_section_for_region(project, region_id)
    present_circuit_ids: tuple[str, ...]

    if linked_section is not None:
        profile, value_overrides = channel_profile_and_overrides(project, profile, linked_section)
        # Every active physical cable remains in the 2D material map.  Thermal
        # scope controls only which circuits generate heat; it never deletes a
        # neighbouring cable body from the channel geometry.
        physical = _all_section_physical_cables(linked_section)
        present_circuit_ids = _section_circuit_ids(linked_section)
        if not physical or not present_circuit_ids:
            raise NodalThermalInputError(
                f"{region_id}: üretim bağlı Kablo-Kanal kesitinde aktif fiziksel kablo/devre bulunmuyor."
            )
        if energized_circuit_ids is None:
            active_ids = _section_active_circuit_ids(linked_section)
            selected = active_ids[:max(1, min(int(active_circuit_count), len(active_ids)))]
        else:
            selected = tuple(item for item in energized_circuit_ids if item in set(present_circuit_ids))
        if not selected:
            raise NodalThermalInputError(
                f"{region_id}: seçili termal çözüm kapsamında enerjilenecek devre bulunmuyor."
            )
        energized_circuit_ids = tuple(dict.fromkeys(selected))
        circuit_index = {circuit_id: index for index, circuit_id in enumerate(present_circuit_ids, start=1)}
        explicit_locations = tuple(
            _CableLocation(
                item.physical_cable_id,
                circuit_index.get(item.circuit_id, 1),
                str(item.phase).upper(),
                int(item.parallel_index),
                float(item.x_m),
                float(item.depth_m),
            )
            for item in physical
        )
        current_factors = _physical_current_factors(
            project, linked_section, explicit_locations,
            design_current_per_cable_a, energized_circuit_ids,
        )
        if not any(value > 0.0 for value in current_factors):
            raise NodalThermalInputError(
                f"{region_id}: seçili devrelerin çözülebilir kablo akımı bulunmuyor."
            )
        point_heat_sources = tuple(
            (float(item.x_m), float(item.depth_m), float(item.heat_w_m))
            for item in linked_section.external_heat_sources
            if item.active and abs(float(item.heat_w_m)) > 0.0
        )
    else:
        present_count = max(1, int(active_circuit_count))
        present_circuit_ids = tuple(f"C{index}" for index in range(1, present_count + 1))
        energized_circuit_ids = present_circuit_ids

    present_circuit_count = len(present_circuit_ids)
    energized_count = len(energized_circuit_ids)
    model = _NodalModel(
        project, region, profile, present_circuit_count, mesh_scale,
        explicit_locations=explicit_locations, value_overrides=value_overrides,
    )
    solved, _ = _solve_at_current_with_retry(
        model, design_current_per_cable_a, regional_lambda1, max_iterations=40, tolerance_c=0.02,
        point_heat_sources=point_heat_sources, current_factors=current_factors,
    )
    field, cables, iterations, converged, source_heat, boundary_heat, balance, residual = solved
    if calculate_ampacity:
        ampacity, ampacity_evaluations = _find_ampacity(
            model, regional_lambda1, design_current_per_cable_a, iec_result.ampacity_a,
            point_heat_sources=point_heat_sources, current_factors=current_factors,
        )
    else:
        ampacity = iec_result.ampacity_a
        ampacity_evaluations = 0
    max_cond = max(item.conductor_temperature_c for item in cables)
    max_jacket = max(item.jacket_temperature_c for item in cables)
    difference = (ampacity - iec_result.ampacity_a) / max(iec_result.ampacity_a, 1e-12) * 100.0
    status = "UYGUN" if design_current_per_cable_a <= ampacity and max_cond <= project.cable.max_temperature_c else "UYGUN DEĞİL"
    warnings: list[str] = []
    if balance > 0.5:
        warnings.append(f"Enerji dengesi hatası %{balance:.3f}; ağ/sınır koşullarını inceleyin.")
    if not converged:
        warnings.append(f"Sıcaklığa bağlı iletken kaybı iterasyonu {iterations} iterasyonda yakınsamadı.")
    if energized_count < present_circuit_count:
        warnings.append(
            "Seçili kapsam izole/eksik-devre termal çözümüdür; diğer fiziksel kablolar pasif ısıl cisim olarak korunmuş, elektriksel kayıpları sıfırlanmıştır."
        )
    if solution_scope_id != "SCENARIO_COMBINED":
        warnings.append(
            "Bu sonuç ek devre-termal etkileşim incelemesidir. Bölgesel lambda1 ve IEC karşılaştırma değeri seçili ana elektrik senaryosundan alınır; farklı bir işletme/bonding senaryosu için birleşik hesap ayrıca çalıştırılmalıdır."
        )
    if profile.installation_type.upper() == THERMAL_INSTALL_HDD:
        warnings.append("HDD kesiti 2D orta-kesit modelidir; giriş/çıkış ve eksenel 3D etkiler ayrıca doğrulanmalıdır.")
    if any(item.source_type.upper() == "PRELIMINARY_ASSUMPTION" for item in (
        profile.native_soil, profile.bedding, profile.side_backfill, profile.cable_cover
    )):
        warnings.append("En az bir termal malzeme ön tasarım varsayımıdır; TESTED/AS_BUILT veri gereklidir.")

    dryout_state = model._last_dryout_state
    if dryout_state.enabled:
        if not dryout_state.converged:
            warnings.append("Kritik-izoterm toprak kuruma iterasyonu yakınsamadı.")
        if any(
            str(item.data_state).upper() not in {"TESTED", "AS_BUILT"}
            for item in (profile.native_soil, profile.bedding, profile.side_backfill, profile.cable_cover)
            if float(item.critical_dryout_temperature_c or 0.0) > 0.0
        ):
            warnings.append("Kuruma kritik sıcaklığı/kuru ρ en az bir malzemede proje testiyle doğrulanmamıştır.")

    trace = (
        f"Referans = {NODAL_THERMAL_REFERENCE}",
        f"Bölge = {region_id}, {profile.start_m:.3f}-{profile.end_m:.3f} m, kurulum={profile.installation_type}",
        f"Çözüm kapsamı = {solution_scope_id} / {solution_scope_name}",
        f"Fiziksel devre = {present_circuit_count}; enerjili devre = {energized_count}: {', '.join(energized_circuit_ids)}",
        f"Ağ = {model.nx} x {model.ny} = {model.cell_count} hücre",
        f"Hücre aralığı min/max = {min(np.min(model.dx), np.min(model.dy)):.5f} / {max(np.max(model.dx), np.max(model.dy)):.5f} m",
        f"Kablo sayısı = {len(cables)}; referans akım/kablo = {design_current_per_cable_a:.3f} A",
        f"Geometri kaynağı = {'Kablo-Kanal gerçek x-y — tüm fiziksel kablolar' if linked_section is not None else 'termal şablon dizilimi'}",
        f"Akım kaynağı = {'Kablo-Kanal devre yükleri/override + senaryo ölçeği' if linked_section is not None else 'senaryo eşit kablo akımı'}",
        f"Isı kaynağı / sınırdan çıkan = {source_heat:.6f} / {boundary_heat:.6f} W/m",
        f"Enerji dengesi hatası = %{balance:.6f}; lineer residual={residual:.3e}",
        f"Tcond,max = {max_cond:.3f} °C; nodal ampacity={ampacity:.3f} A; IEC={iec_result.ampacity_a:.3f} A",
        f"Ampacity arama çözüm sayısı = {ampacity_evaluations}",
        "Sınır koşulu, malzemeler, kanal katmanları ve fiziksel kablo konumları üretim bağlı kesit/proje kayıtlarından alınmıştır.",
        *(dryout_state.trace if dryout_state.enabled else ()),
    )
    return NodalRegionResult(
        region.region_id,
        region.name,
        profile.start_m,
        profile.end_m,
        profile.installation_type,
        scenario_id,
        design_current_per_cable_a,
        energized_count,
        ampacity,
        max_cond,
        max_jacket,
        project.cable.max_temperature_c,
        status,
        iec_result.ampacity_a,
        difference,
        regional_lambda1,
        model.nx,
        model.ny,
        model.cell_count,
        float(min(np.min(model.dx), np.min(model.dy))),
        float(max(np.max(model.dx), np.max(model.dy))),
        iterations,
        converged,
        source_heat,
        boundary_heat,
        balance,
        residual,
        tuple(float(v) for v in model.x_edges),
        tuple(float(v) for v in model.y_edges),
        tuple(tuple(float(v) for v in row) for row in field),
        tuple(tuple(str(v) for v in row) for row in model.material_ids),
        cables,
        tuple(warnings),
        trace,
        present_circuit_count,
        solution_scope_id,
        solution_scope_name,
        tuple(energized_circuit_ids),
        dryout_enabled=dryout_state.enabled,
        dryout_converged=dryout_state.converged,
        dryout_iterations=dryout_state.iterations,
        dryout_cell_count=dryout_state.dry_cell_count,
        dryout_eligible_cell_count=dryout_state.eligible_cell_count,
        dryout_fraction=dryout_state.dry_fraction,
        dryout_material_ids=dryout_state.material_ids,
    )


def _active_circuits_for_scenario(project: ProjectData, scenario_id: str) -> int:
    active = max(1, int(project.design_basis.active_circuit_count))
    if scenario_id == "N_MINUS_ONE" and project.design_basis.n_minus_one_enabled:
        return max(1, active - 1)
    if scenario_id == "DESIGN" and project.design_basis.n_minus_one_enabled:
        normal = float(project.design_basis.normal_current_per_active_circuit_a or 0.0)
        n1 = float(project.design_basis.n1_current_per_circuit_a or 0.0)
        if n1 > normal + 1e-9:
            return max(1, active - 1)
    return active


def _route_scope_result(
    iec_scenario,
    regions: list[NodalRegionResult],
    *,
    solution_scope_id: str,
    solution_scope_name: str,
) -> NodalRouteScenarioResult:
    if not regions:
        raise NodalThermalInputError(
            f"{solution_scope_name}: çözülebilir Kablo-Kanal bölgesi bulunamadı."
        )
    critical = min(regions, key=lambda item: item.ampacity_per_cable_a)
    max_temp = max(item.maximum_conductor_temperature_c for item in regions)
    status = aggregate_binary_status(tuple(item.status for item in regions))
    energized = tuple(dict.fromkeys(
        circuit_id for region in regions for circuit_id in region.energized_circuit_ids
    ))
    present_count = max((item.present_circuit_count for item in regions), default=1)
    return NodalRouteScenarioResult(
        iec_scenario.scenario_id,
        iec_scenario.scenario_name,
        iec_scenario.design_current_per_cable_a,
        max((item.active_circuit_count for item in regions), default=1),
        tuple(regions),
        critical.region_id,
        critical.region_name,
        critical.ampacity_per_cable_a,
        max_temp,
        status,
        (
            f"Senaryo = {iec_scenario.scenario_name}",
            f"Termal kapsam = {solution_scope_id} / {solution_scope_name}",
            f"Fiziksel devre = {present_count}; enerjili devre = {len(energized)}: {', '.join(energized)}",
            f"Referans kablo akımı = {iec_scenario.design_current_per_cable_a:.3f} A",
            f"2D çözülen bölge = {len(regions)}",
            f"Hat nodal ampacity = {critical.ampacity_per_cable_a:.3f} A/kablo",
            f"Kritik bölge = {critical.region_id} / {critical.region_name}",
            f"Durum = {status}",
        ),
        solution_scope_id,
        solution_scope_name,
        energized,
        present_count,
    )


def _solve_route_scope(
    project: ProjectData,
    iec_scenario,
    active_circuits: int,
    *,
    solution_scope_id: str,
    solution_scope_name: str,
    energized_circuit_ids: tuple[str, ...] | None,
) -> NodalRouteScenarioResult:
    regions = [
        solve_nodal_region(
            project,
            iec_region.region_id,
            iec_scenario.design_current_per_cable_a,
            active_circuits,
            iec_region.regional_lambda1,
            iec_region.iec,
            iec_scenario.scenario_id,
            energized_circuit_ids=energized_circuit_ids,
            solution_scope_id=solution_scope_id,
            solution_scope_name=solution_scope_name,
        )
        for iec_region in iec_scenario.regions
    ]
    return _route_scope_result(
        iec_scenario, regions,
        solution_scope_id=solution_scope_id,
        solution_scope_name=solution_scope_name,
    )


def _solve_channel_interaction_scope(
    project: ProjectData,
    iec_scenario,
    *,
    solution_scope_id: str,
    solution_scope_name: str,
    circuit_id: str | None = None,
) -> NodalRouteScenarioResult | None:
    """Solve only multi-circuit Kablo-Kanal sections for interaction review.

    Route projects may contain a mixture of linked and legacy thermal regions,
    or different physical circuit sets along the route.  Interaction scopes
    therefore remain region-local: a scope lists only the linked sections in
    which the selected circuit exists.  This avoids suppressing the useful
    two-circuit view merely because another route region has a different or
    legacy cross-section.
    """

    regions: list[NodalRegionResult] = []
    for iec_region in iec_scenario.regions:
        section = linked_cross_section_for_region(project, iec_region.region_id)
        if section is None:
            continue
        local_ids = _section_circuit_ids(section)
        if len(local_ids) < 2:
            continue
        if circuit_id is None:
            selected = local_ids
        elif circuit_id in local_ids:
            selected = (circuit_id,)
        else:
            continue
        regions.append(solve_nodal_region(
            project,
            iec_region.region_id,
            iec_scenario.design_current_per_cable_a,
            len(selected),
            iec_region.regional_lambda1,
            iec_region.iec,
            iec_scenario.scenario_id,
            energized_circuit_ids=selected,
            solution_scope_id=solution_scope_id,
            solution_scope_name=solution_scope_name,
        ))
    if not regions:
        return None
    return _route_scope_result(
        iec_scenario, regions,
        solution_scope_id=solution_scope_id,
        solution_scope_name=solution_scope_name,
    )


def _scenario_with_nodal_model_scope_seeds(project: ProjectData, iec_scenario):
    """Complete the nodal source list for non-physical model-scope failures.

    Nodal field equations do not need an authoritative analytical T4.  A
    provisional IEC object is used only for internal-loss properties and the
    initial ampacity bracket; the nodal result remains subject to FAZ 4.2
    quality gates and the analytical value is never reported as authoritative.
    """
    failed = [outcome for outcome in iec_scenario.region_outcomes if not outcome.success]
    model_scope_codes = {
        "ANALYTIC_LAYERED_GEOMETRY_REQUIRES_NODAL",
        "ANALYTIC_MODEL_SCOPE_REQUIRES_NODAL",
        "CUSTOM_POSITIONS_REQUIRED",
    }
    unacceptable = [
        outcome for outcome in failed
        if outcome.physical_rejection
        or (
            str(outcome.error_class).upper() != "MODEL_SCOPE"
            and str(outcome.error_code).upper() not in model_scope_codes
        )
    ]
    if unacceptable:
        labels = ", ".join(f"{item.region_id}:{item.error_code}" for item in unacceptable)
        raise NodalThermalInputError(
            "2D nodal çözüm yalnız model-kapsam kaynaklı analitik eksikleri devralabilir: " + labels
        )
    if not failed:
        return iec_scenario

    from types import SimpleNamespace
    from ucd.calculations.installation_coupling import (
        attach_resolved_geometry_to_route_sections,
        physical_positions_for_region,
    )
    from ucd.calculations.project_geometry_runtime import materialize_project_route_sections

    sections, _ = materialize_project_route_sections(project, strict=False, mutate_project=False)
    fallback_sections = attach_resolved_geometry_to_route_sections(
        project, deepcopy(project.route_sections)
    )
    section_by_region = {str(item.thermal_region_id): item for item in fallback_sections}
    section_by_region.update({str(item.thermal_region_id): item for item in sections})
    seeds = list(iec_scenario.regions)
    for outcome in failed:
        section = section_by_region.get(str(outcome.region_id))
        if section is None:
            raise NodalThermalInputError(
                f"{outcome.region_id}: nodal model-kapsam çözümü için güzergâh bölümü bulunamadı."
            )
        provisional_t4 = float(section.external_thermal_resistance_t4_km_w or 0.0)
        if provisional_t4 <= 0.0:
            provisional_t4 = 1.0
        seed_section = replace(
            section,
            external_thermal_mode=EXTERNAL_THERMAL_MANUAL,
            external_thermal_resistance_t4_km_w=provisional_t4,
        )
        seed = solve_section(
            project.cable, seed_section,
            physical_positions_for_region(project, str(outcome.region_id)),
        )
        seeds.append(SimpleNamespace(
            region_id=str(outcome.region_id),
            region_name=str(outcome.region_name),
            regional_lambda1=float(project.cable.sheath_loss_factor),
            iec=seed,
            nodal_seed_only=True,
            nodal_seed_trace=(
                f"{outcome.error_code}: analitik kapsam dışında; T4={provisional_t4:.6f} yalnız nodal başlangıç bracket'ı için kullanıldı.",
            ),
        ))
    order = {outcome.region_id: index for index, outcome in enumerate(iec_scenario.region_outcomes)}
    seeds.sort(key=lambda item: order.get(str(item.region_id), 10**9))
    return replace(iec_scenario, regions=tuple(seeds))


def solve_nodal_route(
    project: ProjectData,
    bonding_result: Any | None = None,
    active_scenario_id: str = "DESIGN",
) -> NodalRouteStudyResult:
    iec_study = solve_thermal_route(project, bonding_result, active_scenario_id)
    nodal_iec_scenarios = tuple(
        _scenario_with_nodal_model_scope_seeds(project, item)
        for item in iec_study.scenarios
    )
    scenarios: list[NodalRouteScenarioResult] = []
    for iec_scenario in nodal_iec_scenarios:
        active_circuits = _active_circuits_for_scenario(project, iec_scenario.scenario_id)
        scenarios.append(_solve_route_scope(
            project, iec_scenario, active_circuits,
            solution_scope_id="SCENARIO_COMBINED",
            solution_scope_name=f"Senaryo birlikte ({active_circuits} devre enerjili)",
            energized_circuit_ids=None,
        ))
    if not scenarios:
        raise NodalThermalInputError("2D nodal çözüm için yük senaryosu bulunamadı.")
    if active_scenario_id not in {item.scenario_id for item in scenarios}:
        active_scenario_id = scenarios[-1].scenario_id

    # Circuit-interaction scopes are calculated for the selected active load
    # scenario.  Primary production scenarios remain unchanged for transient,
    # optimization, reports and existing integrations.
    scope_scenarios: list[NodalRouteScenarioResult] = []
    active_iec = next(
        (item for item in nodal_iec_scenarios if item.scenario_id == active_scenario_id),
        nodal_iec_scenarios[-1],
    )
    scenario_active_count = _active_circuits_for_scenario(project, active_iec.scenario_id)
    linked_circuit_ids: list[str] = []
    linked_circuit_names: dict[str, str] = {}
    maximum_present_count = 0
    for iec_region in active_iec.regions:
        section = linked_cross_section_for_region(project, iec_region.region_id)
        if section is None:
            continue
        local_ids = _section_circuit_ids(section)
        local_names = {item.circuit_id: item.name for item in section.circuits}
        maximum_present_count = max(maximum_present_count, len(local_ids))
        for circuit_id in local_ids:
            if circuit_id not in linked_circuit_ids:
                linked_circuit_ids.append(circuit_id)
            linked_circuit_names.setdefault(circuit_id, local_names.get(circuit_id, circuit_id))

    if maximum_present_count > scenario_active_count:
        combined_scope = _solve_channel_interaction_scope(
            project, active_iec,
            solution_scope_id="ALL_CIRCUITS_COMBINED",
            solution_scope_name="Kanalın tüm devreleri birlikte",
        )
        if combined_scope is not None:
            scope_scenarios.append(combined_scope)
    if maximum_present_count >= 2:
        for circuit_id in linked_circuit_ids:
            circuit_name = linked_circuit_names.get(circuit_id, circuit_id)
            isolated_scope = _solve_channel_interaction_scope(
                project, active_iec,
                solution_scope_id=f"ISOLATED::{circuit_id}",
                solution_scope_name=f"Yalnız {circuit_name} enerjili (diğerleri pasif)",
                circuit_id=circuit_id,
            )
            if isolated_scope is not None:
                scope_scenarios.append(isolated_scope)

    result = NodalRouteStudyResult(
        NODAL_THERMAL_REFERENCE, tuple(scenarios), active_scenario_id, iec_study, tuple(scope_scenarios)
    )
    # FAZ 4.2 produces an immediate comparison record.  Without mesh evidence
    # it remains ANALYTIC_PREVIEW/NODAL_QUALITY_PENDING and is not binding.
    from ucd.calculations.thermal_method_validation import evaluate_thermal_method_authority
    return replace(result, method_validation=evaluate_thermal_method_authority(project, result))


def check_mesh_convergence(
    project: ProjectData,
    region_id: str,
    current_per_cable_a: float,
    active_circuit_count: int,
    regional_lambda1: float,
    iec_result: Iec60287SectionResult,
    tolerance_percent: float = 1.0,
    energized_circuit_ids: tuple[str, ...] | None = None,
    solution_scope_id: str = "SCENARIO_COMBINED",
    solution_scope_name: str = "Senaryo birlikte",
) -> MeshConvergenceResult:
    coarse = solve_nodal_region(
        project, region_id, current_per_cable_a, active_circuit_count, regional_lambda1,
        iec_result, mesh_scale=1.25, calculate_ampacity=True,
        energized_circuit_ids=energized_circuit_ids,
        solution_scope_id=solution_scope_id, solution_scope_name=solution_scope_name,
    )
    refined = solve_nodal_region(
        project, region_id, current_per_cable_a, active_circuit_count, regional_lambda1,
        iec_result, mesh_scale=0.75, calculate_ampacity=True,
        energized_circuit_ids=energized_circuit_ids,
        solution_scope_id=solution_scope_id, solution_scope_name=solution_scope_name,
    )
    difference = abs(refined.maximum_conductor_temperature_c - coarse.maximum_conductor_temperature_c)
    percent = difference / max(abs(refined.maximum_conductor_temperature_c), 1e-12) * 100.0
    ampacity_difference = refined.ampacity_per_cable_a - coarse.ampacity_per_cable_a
    ampacity_percent = ampacity_difference / max(abs(refined.ampacity_per_cable_a), 1e-12) * 100.0
    solutions_converged = bool(coarse.converged and refined.converged)
    passed = difference <= 1.0 and abs(ampacity_percent) <= tolerance_percent and solutions_converged
    return MeshConvergenceResult(
        region_id,
        current_per_cable_a,
        coarse.mesh_cell_count,
        refined.mesh_cell_count,
        coarse.maximum_conductor_temperature_c,
        refined.maximum_conductor_temperature_c,
        difference,
        percent,
        passed,
        coarse.ampacity_per_cable_a,
        refined.ampacity_per_cable_a,
        ampacity_difference,
        ampacity_percent,
        solutions_converged,
    )
