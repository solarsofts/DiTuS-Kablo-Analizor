"""Real-x/y multi-cable electro-thermal shadow bridge.

v0.16.6 transfers the route-wide N-core/N-sheath solution into the explicit
installation cross-sections.  It produces two independent steady-state thermal
views for every linked thermal region:

* an analytical self/mutual image-method resistance matrix using every physical
  cable coordinate and its own loss density;
* the existing 2D finite-volume conduction kernel, but with the explicit
  physical-cable coordinates and non-uniform per-cable heat sources.

The module is SHADOW_COMPARE only.  It does not replace locked IEC 60287 or
nodal production results, write project lambda1, or mutate the project model.
"""

from __future__ import annotations

from ucd.calculations.model_applicability import require_production_physics

from dataclasses import dataclass, replace
from math import hypot, log, pi

import numpy as np

from ucd.calculations.iec60287 import ac_resistance_at_temperature_ohm_km, dielectric_loss_w_m
from ucd.calculations.sheath_loss_completeness import resolve_sheath_loss_completeness
from ucd.calculations.multiconductor_global_network import (
    GlobalMulticonductorNetworkResult,
    MulticonductorGlobalInputError,
    solve_global_multiconductor_network,
)
from ucd.calculations.nodal_thermal import (
    NodalThermalInputError,
    _CableLocation,
    _NodalModel,
)
from ucd.calculations.thermal_resistance import (
    ThermalInputError,
    direct_buried_thermal_matrix_km_w,
    mixed_zone_direct_buried_thermal_matrix_km_w,
    resolve_internal_thermal_resistance,
)
from ucd.calculations.thermal_route import resolve_thermal_region
from ucd.models.project import (
    EXTERNAL_THERMAL_MIXED,
    InstallationCrossSectionData,
    PhysicalCableData,
    ProjectData,
    ThermalRegion,
)


MODE = "SHADOW_COMPARE"
COUPLING_MODE = "GLOBAL_EM_LOSS_TO_REAL_XY_THERMAL"
REFERENCE = (
    "IEC 60287-1-1/-1-3 loss scope; IEC 60287-2-1 thermal-resistance scope; "
    "CIGRE TB 797 sheath-bonding architecture; CIGRE TB 880 rating-tool verification; "
    "2D cell-centred finite-volume steady-state conduction"
)


class MulticonductorThermalInputError(ValueError):
    pass


@dataclass(frozen=True)
class MulticonductorThermalIssue:
    severity: str
    code: str
    message: str
    object_id: str = ""


@dataclass(frozen=True)
class MulticonductorThermalCableResult:
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    x_m: float
    depth_m: float
    current_a: complex
    conductor_loss_w_m: float
    sheath_loss_w_m: float
    dielectric_loss_w_m: float
    armour_loss_w_m: float
    total_loss_w_m: float
    analytical_jacket_temperature_c: float
    analytical_conductor_temperature_c: float
    nodal_jacket_temperature_c: float
    nodal_conductor_temperature_c: float
    analytical_equivalent_t4_km_w: float
    nodal_equivalent_t4_km_w: float

    @property
    def key(self) -> str:
        return f"{self.circuit_id}:{self.phase}:P{self.parallel_index}"


@dataclass(frozen=True)
class MulticonductorThermalRegionResult:
    region_id: str
    region_name: str
    cross_section_id: str
    cross_section_name: str
    installation_type: str
    start_m: float
    end_m: float
    analytical_matrix_km_w: tuple[tuple[float, ...], ...]
    analytical_external_source_rise_c: tuple[float, ...]
    cables: tuple[MulticonductorThermalCableResult, ...]
    maximum_analytical_conductor_temperature_c: float
    maximum_nodal_conductor_temperature_c: float
    maximum_method_temperature_difference_c: float
    critical_analytical_cable_id: str
    critical_nodal_cable_id: str
    nodal_mesh_nx: int
    nodal_mesh_ny: int
    nodal_cell_count: int
    nodal_iterations: int
    nodal_converged: bool
    nodal_total_heat_source_w_m: float
    nodal_total_boundary_heat_w_m: float
    nodal_energy_balance_error_percent: float
    nodal_maximum_linear_residual: float
    x_edges_m: tuple[float, ...]
    depth_edges_m: tuple[float, ...]
    temperature_c: tuple[tuple[float, ...], ...]
    material_ids: tuple[tuple[str, ...], ...]
    issues: tuple[MulticonductorThermalIssue, ...]
    trace: tuple[str, ...]
    nodal_computed: bool = True
    dryout_enabled: bool = False
    dryout_converged: bool = True
    dryout_iterations: int = 0
    dryout_cell_count: int = 0
    dryout_eligible_cell_count: int = 0
    dryout_fraction: float = 0.0
    dryout_material_ids: tuple[str, ...] = ()


@dataclass
class MulticonductorThermalResult:
    mode: str
    coupling_mode: str
    reference: str
    global_em_selected_method: str
    regions: tuple[MulticonductorThermalRegionResult, ...]
    critical_analytical_region_id: str
    critical_nodal_region_id: str
    maximum_analytical_conductor_temperature_c: float
    maximum_nodal_conductor_temperature_c: float
    maximum_method_temperature_difference_c: float
    total_region_heat_source_w_m: float
    issues: tuple[MulticonductorThermalIssue, ...]
    trace: tuple[str, ...]
    production_mode: bool = False
    thermal_method: str = "ANALYTIC_AND_NODAL"

    @property
    def final_design_ready(self) -> bool:
        return bool(self.production_mode and all(
            (
                region.nodal_converged
                and (not region.dryout_enabled or region.dryout_converged)
            ) if region.nodal_computed else True
            for region in self.regions
        ))

    def trace_lines(self) -> list[str]:
        label = "Üretim Gerçek x-y Kablo-Kanal Termal Motoru" if self.production_mode else "Gerçek x-y Kablo-Kanal Termal Gölge Motoru"
        lines = [
            f"DiTuS — {label}",
            f"Mod={self.mode}; coupling={self.coupling_mode}",
            f"Referans={self.reference}",
            f"Global EM yöntemi={self.global_em_selected_method}",
            f"Bölge sayısı={len(self.regions)}",
            f"Tcond,max analitik/nodal={self.maximum_analytical_conductor_temperature_c:.4f}/"
            f"{self.maximum_nodal_conductor_temperature_c:.4f} °C",
            f"Maks. yöntem farkı={self.maximum_method_temperature_difference_c:.4f} °C",
            f"Kritik bölge analitik/nodal={self.critical_analytical_region_id}/{self.critical_nodal_region_id}",
        ]
        lines.extend(self.trace)
        lines.extend(f"{item.severity} {item.code}: {item.message}" for item in self.issues)
        for region in self.regions:
            lines.append(
                f"{region.region_id}/{region.cross_section_id}: Tanalitik={region.maximum_analytical_conductor_temperature_c:.4f} °C; "
                f"Tnodal={region.maximum_nodal_conductor_temperature_c:.4f} °C; "
                f"Δ={region.maximum_method_temperature_difference_c:.4f} °C; "
                f"enerji=%{region.nodal_energy_balance_error_percent:.5f}"
            )
        return lines


def _cross_section_for_region(project: ProjectData, region: ThermalRegion) -> InstallationCrossSectionData:
    matches = [
        item for item in project.installation_design.cross_sections
        if region.region_id in set(item.region_ids)
    ]
    if not matches:
        raise MulticonductorThermalInputError(
            f"{region.region_id}: termal bölgeye bağlı fiziksel Kablo-Kanal Düzeni bulunamadı."
        )
    if len(matches) > 1:
        raise MulticonductorThermalInputError(
            f"{region.region_id}: birden fazla fiziksel kesit bağlı; bölge-kesit eşlemesi tekil olmalıdır."
        )
    return matches[0]


def _active_physical_cables(section: InstallationCrossSectionData) -> tuple[PhysicalCableData, ...]:
    circuits = {item.circuit_id for item in section.circuits if item.active}
    values = [
        item for item in section.physical_cables
        if item.active and item.circuit_id in circuits and str(item.phase).upper() in {"A", "B", "C"}
    ]
    values.sort(key=lambda item: (item.circuit_id, "ABC".index(str(item.phase).upper()), int(item.parallel_index)))
    if not values:
        raise MulticonductorThermalInputError(f"{section.cross_section_id}: aktif fiziksel kablo bulunmuyor.")
    return tuple(values)


def _key(item: PhysicalCableData) -> str:
    return f"{item.circuit_id}:{str(item.phase).upper()}:P{int(item.parallel_index)}"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _regional_sheath_losses_w_m(
    global_result: GlobalMulticonductorNetworkResult,
    region: ThermalRegion,
    keys: tuple[str, ...],
) -> dict[str, float]:
    weighted = {key: 0.0 for key in keys}
    covered = 0.0
    for section in global_result.section_results:
        overlap = _overlap(region.start_m, region.end_m, section.start_m, section.end_m)
        length = max(0.0, section.end_m - section.start_m)
        if overlap <= 0.0 or length <= 0.0:
            continue
        covered += overlap
        by_key = {item.key: item for item in section.sheath_results}
        for key in keys:
            item = by_key.get(key)
            if item is not None:
                weighted[key] += overlap * float(item.sheath_metal_loss_w) / length
    if covered <= 0.0:
        raise MulticonductorThermalInputError(
            f"{region.region_id}: global bonding minor section’larıyla zincirleme örtüşme bulunamadı."
        )
    return {key: value / covered for key, value in weighted.items()}




def _regional_core_losses_w_m(
    global_result: GlobalMulticonductorNetworkResult,
    region: ThermalRegion,
    keys: tuple[str, ...],
) -> dict[str, float]:
    weighted = {key: 0.0 for key in keys}
    covered = 0.0
    core_by_key = {item.key: item for item in global_result.core_results}
    for block in global_result.matrix_blocks:
        overlap = _overlap(region.start_m, region.end_m, block.start_m, block.end_m)
        length = max(0.0, float(block.length_m))
        if overlap <= 0.0 or length <= 0.0:
            continue
        covered += overlap
        index_by_key = {key: index for index, key in enumerate(block.core_order)}
        for key in keys:
            index = index_by_key.get(key)
            core = core_by_key.get(key)
            if index is None or core is None:
                continue
            resistance_ohm = float(block.core_resistance_ohm[index])
            weighted[key] += overlap * abs(core.core_current_a) ** 2 * resistance_ohm / length
    if covered <= 0.0:
        raise MulticonductorThermalInputError(
            f"{region.region_id}: sıcaklığa bağlı global core bloklarıyla zincirleme örtüşme bulunamadı."
        )
    return {key: value / covered for key, value in weighted.items()}

def _external_source_temperature_rise(
    cable_positions: tuple[tuple[float, float], ...],
    section: InstallationCrossSectionData,
    native_rho_km_w: float,
) -> tuple[float, ...]:
    result = [0.0 for _ in cable_positions]
    rho = float(native_rho_km_w)
    for source in section.external_heat_sources:
        if not source.active or abs(float(source.heat_w_m)) <= 0.0:
            continue
        sx = float(source.x_m)
        sh = float(source.depth_m)
        for index, (x, h) in enumerate(cable_positions):
            actual = hypot(x - sx, h - sh)
            image = hypot(x - sx, h + sh)
            minimum = max(1e-6, float(source.effective_radius_m))
            if actual <= minimum:
                actual = minimum
            if image > actual:
                result[index] += rho / (2.0 * pi) * log(image / actual) * float(source.heat_w_m)
    return tuple(float(value) for value in result)


def _internal_rise_c(
    t1: float,
    t2: float,
    t3: float,
    conductor_loss: np.ndarray,
    sheath_loss: np.ndarray,
    dielectric_loss: np.ndarray,
    armour_loss: np.ndarray,
) -> np.ndarray:
    # Explicit single-core heat-path form equivalent to the IEC lambda form
    # when Wsh=lambda1*Wc and Warm=lambda2*Wc.
    return (
        conductor_loss * t1
        + 0.5 * dielectric_loss * t1
        + (conductor_loss + sheath_loss + dielectric_loss) * t2
        + (conductor_loss + sheath_loss + armour_loss + dielectric_loss) * t3
    )


def _channel_profile_and_overrides(
    project: ProjectData,
    profile,
    section: InstallationCrossSectionData,
) -> tuple[object, dict[str, object], tuple[MulticonductorThermalIssue, ...]]:
    """Resolve user-edited cable-channel geometry into the shadow 2D profile.

    Legacy-projected sections deliberately retain their locked thermal-template
    profile until the user edits or resets the channel geometry.  This preserves
    numerical parity for old projects while making the new editor authoritative
    for explicitly accepted sections.
    """

    geometry = section.channel_geometry
    source = str(geometry.source_reference or "").strip().upper()
    if not source or source.startswith(("LEGACY_", "MIGRATED_")):
        return profile, {}, ()

    material_map = {item.material_id: item for item in project.thermal_design.materials}
    issues: list[MulticonductorThermalIssue] = []

    def material(material_id: str, fallback, label: str):
        key = str(material_id or "").strip()
        if key and key in material_map:
            return material_map[key]
        issues.append(MulticonductorThermalIssue(
            "WARNING",
            "CHANNEL_MATERIAL_FALLBACK",
            f"{label} malzemesi bulunamadı ({key or 'boş'}); mevcut termal profil malzemesi kullanıldı.",
            section.cross_section_id,
        ))
        return fallback

    physical = [item for item in section.physical_cables if item.active]
    radius = max(1e-4, float(project.cable.overall_diameter_mm) / 2000.0)
    cable_top = min((float(item.depth_m) - radius for item in physical), default=float(profile.burial_depth_m) - radius)
    cable_span = (
        max((float(item.x_m) for item in physical), default=0.0)
        - min((float(item.x_m) for item in physical), default=0.0)
        + 2.0 * radius
    )
    trench_depth = max(0.30, float(geometry.trench_depth_m))
    bedding = max(0.0, min(float(geometry.bedding_thickness_m), trench_depth))
    thermal_backfill = max(0.0, min(float(geometry.thermal_backfill_height_m), trench_depth - bedding))
    backfill_top = max(0.0, trench_depth - bedding - thermal_backfill)
    cable_cover_height = max(0.0, cable_top - backfill_top)
    selected = max(0.0, min(float(geometry.selected_fill_thickness_m), backfill_top))
    surface = max(0.0, min(float(geometry.surface_layer_thickness_m), trench_depth))
    general = max(0.0, trench_depth - bedding - thermal_backfill - selected - surface)
    side_width = max(0.0, (float(geometry.trench_width_m) - cable_span) / 2.0)

    native = material(geometry.native_soil_material_id, profile.native_soil, "Doğal zemin")
    bedding_material = material(geometry.bedding_material_id, profile.bedding, "Yataklama")
    backfill_material = material(geometry.thermal_backfill_material_id, profile.cable_cover, "Termal backfill")
    selected_material = material(geometry.selected_fill_material_id, profile.selected_upper_fill, "Seçilmiş üst dolgu")
    general_material = material(geometry.general_fill_material_id, profile.general_fill, "Genel dolgu")
    surface_material = (
        material(geometry.surface_material_id, profile.surface or profile.general_fill, "Yüzey tabakası")
        if str(geometry.surface_material_id or "").strip() and surface > 0.0
        else profile.surface
    )

    resolved = replace(
        profile,
        installation_type=str(section.installation_type).strip().upper(),
        burial_depth_m=min((float(item.depth_m) for item in physical), default=float(profile.burial_depth_m)),
        trench_width_m=max(0.20, float(geometry.trench_width_m)),
        trench_depth_m=trench_depth,
        bedding_thickness_m=bedding,
        side_backfill_width_m=side_width,
        cable_cover_height_m=cable_cover_height,
        selected_upper_fill_thickness_m=selected,
        general_upper_fill_thickness_m=general,
        surface_layer_thickness_m=surface,
        native_soil=native,
        bedding=bedding_material,
        side_backfill=backfill_material,
        cable_cover=backfill_material,
        selected_upper_fill=selected_material,
        general_fill=general_material,
        surface=surface_material,
        source_reference=(str(profile.source_reference) + "; INSTALLATION_CHANNEL_GEOMETRY:" + source).strip("; "),
        trace=tuple(profile.trace) + (
            f"Kablo-Kanal Düzeni etkin: {section.cross_section_id}; kaynak={source}",
            f"Hendek W/D={geometry.trench_width_m:.3f}/{geometry.trench_depth_m:.3f} m; "
            f"yan eğim H:V={geometry.side_slope_h_to_v:.3f}; "
            f"yatak/backfill/seçilmiş/yüzey={bedding:.3f}/{thermal_backfill:.3f}/{selected:.3f}/{surface:.3f} m; "
            f"özel malzeme bölgesi={len([item for item in section.material_regions if item.active])}",
        ),
    )

    overrides: dict[str, object] = {
        "trench_center_x_m": float(geometry.center_x_m),
        "trench_side_slope_h_to_v": max(0.0, float(geometry.side_slope_h_to_v)),
        "custom_material_regions": tuple(
            {
                "region_id": str(item.region_id),
                "material_id": str(item.material_id),
                "vertices_m": tuple((float(point[0]), float(point[1])) for point in item.vertices_m),
                "priority": int(item.priority),
            }
            for item in sorted(section.material_regions, key=lambda entry: int(entry.priority))
            if item.active and len(item.vertices_m) >= 3
        ),
        "duct_bank_center_x_m": float(geometry.center_x_m),
        "duct_bank_width_m": max(0.10, float(geometry.duct_bank_width_m)),
        "duct_bank_height_m": max(0.10, float(geometry.duct_bank_height_m)),
        "cover_slab_enabled": bool(geometry.cover_slab_enabled),
        "cover_slab_width_m": max(0.0, float(geometry.cover_slab_width_m)),
        "cover_slab_thickness_m": max(0.0, float(geometry.cover_slab_thickness_m)),
        "cover_slab_depth_m": max(0.0, float(geometry.cover_slab_depth_m)),
        "cover_slab_material_id": str(geometry.cover_slab_material_id),
        "grout_material_id": str(
            geometry.hdd_grout_material_id
            if str(section.installation_type).upper() == "HDD"
            else geometry.duct_bank_material_id
        ),
        "trough_inner_width_m": max(0.0, float(geometry.trough_inner_width_m)),
        "trough_inner_height_m": max(0.0, float(geometry.trough_inner_height_m)),
        "trough_wall_thickness_m": max(0.0, float(geometry.trough_wall_thickness_m)),
        "trough_material_id": str(geometry.trough_material_id),
        "tunnel_width_m": max(0.0, float(geometry.tunnel_width_m)),
        "tunnel_height_m": max(0.0, float(geometry.tunnel_height_m)),
        "tunnel_lining_material_id": str(geometry.trough_material_id),
    }
    if str(section.installation_type).upper() == "HDD":
        diameter = max(0.10, float(geometry.hdd_bore_diameter_m))
        overrides["duct_bank_width_m"] = diameter
        overrides["duct_bank_height_m"] = diameter
    if section.duct_slots:
        slot = section.duct_slots[0]
        overrides["duct_inner_diameter_m"] = max(0.001, float(slot.inner_diameter_m))
        overrides["duct_outer_diameter_m"] = max(float(slot.outer_diameter_m), float(slot.inner_diameter_m) + 0.001)

    issues.append(MulticonductorThermalIssue(
        "INFO",
        "INSTALLATION_CHANNEL_GEOMETRY_ACTIVE",
        "Kullanıcı tarafından kabul edilen Kablo-Kanal Düzeni geometri ve malzemeleri 2D gölge modele aktarıldı.",
        section.cross_section_id,
    ))
    if str(section.installation_type).upper() in {"CONCRETE_TROUGH", "TUNNEL"}:
        issues.append(MulticonductorThermalIssue(
            "WARNING",
            "ENCLOSURE_CONDUCTION_EQUIVALENT",
            "Kanal/tünel iç ortamı bu kapıda eşdeğer iletim bölgesi olarak modellenir; doğal/zorlanmış hava akışı henüz çözülmez.",
            section.cross_section_id,
        ))
    return resolved, overrides, tuple(issues)


def _solve_region(
    project: ProjectData,
    region: ThermalRegion,
    section: InstallationCrossSectionData,
    global_result: GlobalMulticonductorNetworkResult,
    *,
    mesh_scale: float,
    max_iterations: int,
    tolerance_c: float,
    fixed_global_losses: bool,
    solve_nodal: bool,
    deenergized_circuit_ids: frozenset[str],
    core_temperatures_c_by_physical: dict[str, float] | None = None,
    sheath_temperatures_c_by_physical: dict[str, float] | None = None,
) -> MulticonductorThermalRegionResult:
    physical = _active_physical_cables(section)
    core_by_key = {item.key: item for item in global_result.core_results}
    keys = tuple(_key(item) for item in physical)
    missing = [key for key in keys if key not in core_by_key]
    if missing:
        raise MulticonductorThermalInputError(
            f"{section.cross_section_id}: global core çözümünde bulunmayan fiziksel yollar: {', '.join(missing)}"
        )
    longitudinal_sheath_w_m = _regional_sheath_losses_w_m(global_result, region, keys)
    core_w_m = _regional_core_losses_w_m(global_result, region, keys) if fixed_global_losses else {}
    profile = resolve_thermal_region(project.thermal_design, region, project.cable)
    if str(section.installation_type).strip():
        profile = replace(profile, installation_type=str(section.installation_type).strip().upper())
    profile, channel_overrides, channel_issues = _channel_profile_and_overrides(project, profile, section)

    positions = tuple((float(item.x_m), float(item.depth_m)) for item in physical)
    try:
        if str(profile.external_thermal_mode).upper() == EXTERNAL_THERMAL_MIXED:
            effective_radius = float(profile.backfill_effective_radius_m)
            if effective_radius <= project.cable.overall_diameter_mm / 2000.0:
                effective_radius = max(project.cable.overall_diameter_mm / 1000.0, profile.phase_spacing_m)
            matrix = mixed_zone_direct_buried_thermal_matrix_km_w(
                positions,
                project.cable.overall_diameter_mm,
                profile.native_soil.thermal_resistivity_km_w,
                profile.cable_cover.thermal_resistivity_km_w,
                effective_radius,
                profile.surface_thermal_correction_km_w,
            )
        else:
            matrix = direct_buried_thermal_matrix_km_w(
                positions,
                project.cable.overall_diameter_mm,
                profile.native_soil.thermal_resistivity_km_w,
            )
    except ThermalInputError as exc:
        raise MulticonductorThermalInputError(str(exc)) from exc

    model = None
    if solve_nodal:
        locations = tuple(
            _CableLocation(
                item.physical_cable_id,
                next((index for index, circuit in enumerate([c for c in section.circuits if c.active], start=1) if circuit.circuit_id == item.circuit_id), 1),
                str(item.phase).upper(),
                int(item.parallel_index),
                float(item.x_m),
                float(item.depth_m),
            )
            for item in physical
        )
        try:
            model = _NodalModel(
                project,
                region,
                profile,
                active_circuit_count=max(1, len({item.circuit_id for item in physical})),
                mesh_scale=mesh_scale,
                explicit_locations=locations,
                value_overrides=channel_overrides,
            )
        except NodalThermalInputError as exc:
            raise MulticonductorThermalInputError(str(exc)) from exc

    internal = resolve_internal_thermal_resistance(project.cable)
    if int(project.cable.conductors_per_cable) != 1:
        raise MulticonductorThermalInputError(
            "Gerçek x-y çoklu-kablo kapısı yalnız tek damarlı fiziksel kablo nesnelerini kabul eder."
        )
    wd = float(dielectric_loss_w_m(project.cable))
    dielectric = np.asarray([
        0.0 if item.circuit_id in deenergized_circuit_ids else wd
        for item in physical
    ], dtype=float)
    currents = np.asarray([core_by_key[key].core_current_a for key in keys], dtype=complex)
    longitudinal_sheath = np.asarray([
        0.0
        if item.circuit_id in deenergized_circuit_ids or abs(current) <= 1e-12
        else longitudinal_sheath_w_m[key]
        for item, key, current in zip(physical, keys, currents)
    ], dtype=float)
    completeness = resolve_sheath_loss_completeness(
        project, section,
        conductor_temperatures_c=core_temperatures_c_by_physical,
        sheath_temperatures_c=sheath_temperatures_c_by_physical,
    )
    eddy_factor_map = completeness.factor_map
    eddy_factors = np.asarray([
        float(eddy_factor_map.get(item.physical_cable_id, 0.0))
        for item in physical
    ], dtype=float)
    sheath = longitudinal_sheath.copy()
    armour_factor = max(0.0, float(project.cable.armour_loss_factor))
    temperatures = np.full(
        len(physical),
        max(-273.149999, float(profile.ambient_temperature_c) + 20.0),
        dtype=float,
    )
    conductor = (
        np.asarray([core_w_m[key] for key in keys], dtype=float)
        if fixed_global_losses
        else np.zeros(len(physical), dtype=float)
    )
    armour = np.zeros(len(physical), dtype=float)
    heat = np.zeros(len(physical), dtype=float)
    jacket = np.full(len(physical), profile.ambient_temperature_c, dtype=float)
    point_sources = tuple(
        (float(item.x_m), float(item.depth_m), float(item.heat_w_m))
        for item in section.external_heat_sources if item.active and abs(float(item.heat_w_m)) > 0.0
    )
    external_rise = _external_source_temperature_rise(
        positions, section, profile.native_soil.thermal_resistivity_km_w
    )
    rmat = np.asarray(matrix, dtype=float)
    field = np.empty((0, 0), dtype=float)
    boundary_heat = balance = residual = 0.0
    dryout_state = None
    converged = False
    iteration = 0
    analytical_jacket = jacket.copy()
    analytical_conductor = temperatures.copy()

    for iteration in range(1, max_iterations + 1):
        if not fixed_global_losses:
            for index, temperature in enumerate(temperatures):
                eval_t = max(-273.149999, min(float(temperature), project.cable.max_temperature_c + 120.0))
                _, rac = ac_resistance_at_temperature_ohm_km(
                    project.cable, eval_t, profile.phase_spacing_m
                )
                conductor[index] = abs(currents[index]) ** 2 * rac / 1000.0
        sheath = longitudinal_sheath + conductor * eddy_factors
        armour = conductor * armour_factor
        heat = conductor + sheath + armour + dielectric
        analytical_jacket = profile.ambient_temperature_c + rmat @ heat + np.asarray(external_rise)
        analytical_conductor = analytical_jacket + _internal_rise_c(
            internal.t1_km_w,
            internal.t2_km_w,
            internal.t3_km_w,
            conductor,
            sheath,
            dielectric,
            armour,
        )
        if solve_nodal:
            assert model is not None
            field, boundary_heat, balance, residual, dryout_state = model.solve_sources_with_dryout(
                heat, point_sources
            )
            jacket = model.cable_jacket_temperatures(field)
            new_t = jacket + _internal_rise_c(
                internal.t1_km_w,
                internal.t2_km_w,
                internal.t3_km_w,
                conductor,
                sheath,
                dielectric,
                armour,
            )
        else:
            jacket = analytical_jacket.copy()
            new_t = analytical_conductor.copy()
        delta = float(np.max(np.abs(new_t - temperatures)))
        if fixed_global_losses:
            temperatures = new_t
            converged = True
            break
        temperatures = 0.55 * new_t + 0.45 * temperatures
        if delta <= tolerance_c:
            temperatures = new_t
            converged = True
            break

    rows: list[MulticonductorThermalCableResult] = []
    for index, item in enumerate(physical):
        total = float(heat[index])
        analytic_t4 = max(0.0, (float(analytical_jacket[index]) - profile.ambient_temperature_c) / max(total, 1e-12))
        nodal_t4 = (
            max(0.0, (float(jacket[index]) - profile.ambient_temperature_c) / max(total, 1e-12))
            if solve_nodal else analytic_t4
        )
        rows.append(MulticonductorThermalCableResult(
            item.physical_cable_id,
            item.circuit_id,
            str(item.phase).upper(),
            int(item.parallel_index),
            float(item.x_m),
            float(item.depth_m),
            complex(currents[index]),
            float(conductor[index]),
            float(sheath[index]),
            float(dielectric[index]),
            float(armour[index]),
            total,
            float(analytical_jacket[index]),
            float(analytical_conductor[index]),
            float(jacket[index]),
            float(temperatures[index]),
            float(analytic_t4),
            float(nodal_t4),
        ))

    max_a = max(item.analytical_conductor_temperature_c for item in rows)
    max_n = max(item.nodal_conductor_temperature_c for item in rows)
    max_diff = max(
        abs(item.analytical_conductor_temperature_c - item.nodal_conductor_temperature_c)
        for item in rows
    ) if solve_nodal else 0.0
    crit_a = max(rows, key=lambda item: item.analytical_conductor_temperature_c).physical_cable_id
    crit_n = max(rows, key=lambda item: item.nodal_conductor_temperature_c).physical_cable_id
    issues: list[MulticonductorThermalIssue] = list(channel_issues)
    issues.append(MulticonductorThermalIssue(
        "INFO" if completeness.complete else "WARNING",
        "SHEATH_LOSS_COMPLETENESS_FULL" if completeness.complete else "SHEATH_LOSS_COMPLETENESS_BLOCKED",
        "; ".join(completeness.notes),
        region.region_id,
    ))
    if armour_factor > 0.0:
        issues.append(MulticonductorThermalIssue(
            "WARNING", "ARMOUR_LEGACY_COEFFICIENT",
            "Zırh kaybı fiziksel zırh ağı yerine kilitli λ2 katsayısıyla ısı kaynağına eklenmiştir.",
            region.region_id,
        ))
    if deenergized_circuit_ids:
        issues.append(MulticonductorThermalIssue(
            "INFO", "DEENERGIZED_CIRCUITS_ZERO_DIELECTRIC_LOSS",
            "Devre dışı devrelerin iletken, kılıf ve dielektrik kayıpları sıfırlandı; fiziksel kablolar termal geometriden çıkarılmadı.",
            region.region_id,
        ))
    if point_sources:
        issues.append(MulticonductorThermalIssue(
            "INFO", "EXTERNAL_HEAT_SOURCE_INCLUDED",
            "Harici doğrusal ısı kaynakları gerçek koordinatlarından hesaba katıldı.",
            region.region_id,
        ))
    if profile.external_thermal_mode.upper() == EXTERNAL_THERMAL_MIXED:
        issues.append(MulticonductorThermalIssue(
            "INFO", "ANALYTICAL_BACKFILL_EQUIVALENT_ZONE",
            "Analitik yöntem dolgu bölgesini eşdeğer yarıçapla temsil eder.",
            region.region_id,
        ))
    if not solve_nodal:
        issues.append(MulticonductorThermalIssue(
            "INFO", "ANALYTICAL_LOSS_VECTOR_PRODUCTION",
            "Dış sıcaklık artışı gerçek x-y Rth matrisi ile fiziksel kablo kayıp vektörünün çarpımından üretildi.",
            region.region_id,
        ))
    elif not converged:
        issues.append(MulticonductorThermalIssue(
            "WARNING", "NODAL_TEMPERATURE_ITERATION_NOT_CONVERGED",
            "Sıcaklığa bağlı iletken kaybı iterasyonu tolerans içinde yakınsamadı.",
            region.region_id,
        ))
    if solve_nodal and dryout_state is not None and dryout_state.enabled:
        issues.append(MulticonductorThermalIssue(
            "INFO" if dryout_state.converged else "WARNING",
            "NODAL_CRITICAL_ISOTHERM_DRYOUT" if dryout_state.converged else "NODAL_DRYOUT_NOT_CONVERGED",
            f"Kritik-izoterm kuruma hücreleri: {dryout_state.dry_cell_count}/{dryout_state.eligible_cell_count} "
            f"(%{100.0 * dryout_state.dry_fraction:.2f}); iterasyon={dryout_state.iterations}.",
            region.region_id,
        ))
    if solve_nodal and balance > 0.5:
        issues.append(MulticonductorThermalIssue(
            "WARNING", "NODAL_ENERGY_BALANCE",
            f"2D enerji dengesi hatası %{balance:.4f}; ağ ve sınır uzaklıkları incelenmelidir.",
            region.region_id,
        ))

    trace = (
        f"region={region.region_id}; cross_section={section.cross_section_id}; installation={profile.installation_type}",
        f"physical_cables={len(physical)}; deenergized={','.join(sorted(deenergized_circuit_ids)) or 'none'}",
        f"losses W/m core/sheath/dielectric/armour={np.sum(conductor):.6f}/{np.sum(sheath):.6f}/"
        f"{np.sum(dielectric):.6f}/{np.sum(armour):.6f}",
        f"analytical matrix={len(matrix)}x{len(matrix)} actual x-y; q-vector={len(heat)}",
        (
            f"nodal mesh={model.nx}x{model.ny}={model.cell_count}; iteration={iteration}; converged={converged}; "
            f"energy=%{balance:.6f}; residual={residual:.3e}"
            if solve_nodal and model is not None else
            f"analytical-only iteration={iteration}; converged={converged}"
        ),
        "Core akımları global N-core süreklilik çözümünden; boyuna kılıf I²R kaybı global ağdan, IEC λ1'' eddy bileşeni ayrı completeness katmanından alınmıştır.",
        f"sheath_loss_authority={completeness.authority}; eddy_source={completeness.eddy_source}; reasons={','.join(completeness.reason_codes) or 'none'}",
        f"core_loss_mode={'GLOBAL_TEMPERATURE_DEPENDENT_FIXED' if fixed_global_losses else 'INTERNAL_RAC_ITERATION'}",
        *(dryout_state.trace if dryout_state is not None else ()),
    )
    return MulticonductorThermalRegionResult(
        region.region_id,
        region.name,
        section.cross_section_id,
        section.name,
        profile.installation_type,
        float(region.start_m),
        float(region.end_m),
        tuple(tuple(float(value) for value in row) for row in matrix),
        tuple(float(value) for value in external_rise),
        tuple(rows),
        float(max_a),
        float(max_n),
        float(max_diff),
        crit_a,
        crit_n,
        int(model.nx if model is not None else 0),
        int(model.ny if model is not None else 0),
        int(model.cell_count if model is not None else 0),
        iteration,
        bool(converged),
        float(np.sum(heat) + sum(item[2] for item in point_sources)),
        float(boundary_heat),
        float(balance),
        float(residual),
        tuple(float(value) for value in model.x_edges) if model is not None else (),
        tuple(float(value) for value in model.y_edges) if model is not None else (),
        tuple(tuple(float(value) for value in row) for row in field) if model is not None else (),
        tuple(tuple(str(value) for value in row) for row in model.material_ids) if model is not None else (),
        tuple(issues),
        trace,
        bool(solve_nodal),
        dryout_enabled=bool(dryout_state.enabled) if dryout_state is not None else False,
        dryout_converged=bool(dryout_state.converged) if dryout_state is not None else True,
        dryout_iterations=int(dryout_state.iterations) if dryout_state is not None else 0,
        dryout_cell_count=int(dryout_state.dry_cell_count) if dryout_state is not None else 0,
        dryout_eligible_cell_count=int(dryout_state.eligible_cell_count) if dryout_state is not None else 0,
        dryout_fraction=float(dryout_state.dry_fraction) if dryout_state is not None else 0.0,
        dryout_material_ids=tuple(dryout_state.material_ids) if dryout_state is not None else (),
    )

def solve_multiconductor_thermal(
    project: ProjectData,
    *,
    mesh_scale: float = 1.5,
    max_iterations: int = 40,
    tolerance_c: float = 0.02,
    global_result: GlobalMulticonductorNetworkResult | None = None,
    fixed_global_losses: bool = False,
    solve_nodal: bool = True,
    deenergized_circuit_ids: tuple[str, ...] = (),
    production_mode: bool = False,
    core_temperatures_c_by_cross_section: dict[str, dict[str, float]] | None = None,
    sheath_temperatures_c_by_cross_section: dict[str, dict[str, float]] | None = None,
) -> MulticonductorThermalResult:
    """Transfer global N-conductor losses into actual-x/y analytical and 2D thermal models."""

    before = project.to_dict()
    if mesh_scale <= 0.0:
        raise MulticonductorThermalInputError("mesh_scale sıfırdan büyük olmalıdır.")
    try:
        em = global_result or solve_global_multiconductor_network(project, production_mode=production_mode)
    except MulticonductorGlobalInputError as exc:
        raise MulticonductorThermalInputError(str(exc)) from exc

    regions: list[MulticonductorThermalRegionResult] = []
    issues: list[MulticonductorThermalIssue] = []
    for region in project.thermal_design.regions:
        if not region.enabled:
            continue
        try:
            section = _cross_section_for_region(project, region)
            result = _solve_region(
                project,
                region,
                section,
                em,
                mesh_scale=mesh_scale,
                max_iterations=max_iterations,
                tolerance_c=tolerance_c,
                fixed_global_losses=fixed_global_losses,
                solve_nodal=solve_nodal,
                deenergized_circuit_ids=frozenset(str(item) for item in deenergized_circuit_ids),
                core_temperatures_c_by_physical=(core_temperatures_c_by_cross_section or {}).get(section.cross_section_id),
                sheath_temperatures_c_by_physical=(sheath_temperatures_c_by_cross_section or {}).get(section.cross_section_id),
            )
        except (MulticonductorThermalInputError, ThermalInputError, NodalThermalInputError) as exc:
            issues.append(MulticonductorThermalIssue("ERROR", "REGION_SOLVE_FAILED", str(exc), region.region_id))
            continue
        regions.append(result)
        issues.extend(result.issues)
    if not regions:
        detail = "; ".join(item.message for item in issues[:5])
        raise MulticonductorThermalInputError(f"Çözülebilir fiziksel termal bölge bulunamadı. {detail}")

    max_a_region = max(regions, key=lambda item: item.maximum_analytical_conductor_temperature_c)
    max_n_region = max(regions, key=lambda item: item.maximum_nodal_conductor_temperature_c)
    max_diff = max(item.maximum_method_temperature_difference_c for item in regions)
    result = MulticonductorThermalResult(
        "PRODUCTION_THERMAL_LOSS_VECTOR" if production_mode else MODE,
        COUPLING_MODE,
        REFERENCE,
        em.selected_method,
        tuple(regions),
        max_a_region.region_id,
        max_n_region.region_id,
        max_a_region.maximum_analytical_conductor_temperature_c,
        max_n_region.maximum_nodal_conductor_temperature_c,
        float(max_diff),
        float(sum(item.nodal_total_heat_source_w_m for item in regions)),
        tuple(issues),
        (
            ("Fiziksel kayıp vektörü üretim elektro-termal çalışma noktasına bağlandı." if production_mode else "Mevcut IEC/nodal üretim sonuçları değiştirilmedi."),
            "Fiziksel kesit x-y koordinatları doğrudan InstallationDesign modelinden kullanıldı.",
            (
                "Global EM kaynaklı sıcaklığa bağlı core/kılıf kayıpları bu termal adım boyunca sabittir; dış kapalı çevrim yeniden çözer."
                if fixed_global_losses
                else "Elektro-termal geri besleme henüz yoktur; global EM akımları bu termal çalışma boyunca sabittir."
            ),
            "Bölgesel analitik ve 2D sonuç farkı model-form uncertainty göstergesidir; uygunluk toleransı değildir.",
        ),
        bool(production_mode),
        "ANALYTIC_AND_NODAL" if solve_nodal else "ANALYTIC_LOSS_VECTOR",
    )
    if project.to_dict() != before:
        raise MulticonductorThermalInputError("Elektro-termal çözüm proje verisini değiştirdi; işlem iptal edildi.")
    return result


def render_multiconductor_thermal(result: MulticonductorThermalResult) -> str:
    lines = result.trace_lines()
    for region in result.regions:
        lines.append("")
        lines.append(f"[{region.region_id} — {region.cross_section_id}]")
        for cable in region.cables:
            lines.append(
                f"{cable.physical_cable_id}: |I|={abs(cable.current_a):.5f} A; "
                f"Wc/Wsh/Wd/Warm={cable.conductor_loss_w_m:.6f}/{cable.sheath_loss_w_m:.6f}/"
                f"{cable.dielectric_loss_w_m:.6f}/{cable.armour_loss_w_m:.6f} W/m; "
                f"Tcond A/N={cable.analytical_conductor_temperature_c:.4f}/"
                f"{cable.nodal_conductor_temperature_c:.4f} °C"
            )
        lines.extend(f"{item.severity} {item.code}: {item.message}" for item in region.issues)
    return "\n".join(lines)
