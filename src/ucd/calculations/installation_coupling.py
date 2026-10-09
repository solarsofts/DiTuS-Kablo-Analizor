"""Single-source production coupling for installation geometry.

FAZ 4 makes the accepted physical cross-section the geometry authority.  The
legacy scalar fields remain deterministic caches/fallbacks for old projects;
engines that accept coordinates receive the same phase-labelled x-y snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from math import hypot, log, pi
from statistics import median
from typing import Any, Iterable, Mapping
import json

from ucd.models.project import (
    EXTERNAL_THERMAL_AUTO,
    EXTERNAL_THERMAL_MIXED,
    INSTALLATION_COUPLING_PRODUCTION_LINKED,
    INSTALLATION_STATE_VERIFIED,
    InstallationCrossSectionData,
    PhysicalCableData,
    ProjectData,
    RouteSection,
    THERMAL_INSTALL_DIRECT_BURIED,
)


class InstallationCouplingError(ValueError):
    pass


PRODUCTION_GEOMETRY_ENGINE_IDS: tuple[str, ...] = (
    "iec60287", "thermal_route", "nodal", "thermal_method_validation", "bonding", "fault_epr", "svl",
    "transient", "iteration", "report", "procurement",
)

GEOMETRY_BASIS_PHYSICAL = "PHYSICAL_ACCEPTED"
GEOMETRY_BASIS_TEMPLATE = "TEMPLATE_DERIVED"
GEOMETRY_BASIS_LEGACY = "LEGACY_SCALAR"
MATERIAL_HOMOGENEOUS = "HOMOGENEOUS"
MATERIAL_LAYERED = "LAYERED"
MATERIAL_COMPLEX_REGIONS = "COMPLEX_REGIONS"
MATERIAL_GROUNDWATER_BOUNDED = "GROUNDWATER_BOUNDED"

RESULT_IEC_ANALYTIC = "IEC_ANALYTIC"
RESULT_NODAL = "NODAL"
RESULT_ENGINEERING_APPROXIMATION = "ENGINEERING_APPROXIMATION"
RESULT_DERIVED_FROM_SCALAR = "DERIVED_FROM_SCALAR"

AUTH_METHOD_ANALYTIC = "ANALYTIC"
AUTH_METHOD_NODAL = "NODAL"
AUTH_METHOD_LEGACY = "LEGACY_SCALAR"

# Legacy aliases are intentionally kept only for API compatibility with older
# callers. New provenance uses material_field_class + result_authority.
REDUCTION_HOMOGENEOUS = MATERIAL_HOMOGENEOUS
REDUCTION_LAYERED = MATERIAL_LAYERED
REDUCTION_NON_REDUCIBLE = MATERIAL_COMPLEX_REGIONS
REDUCTION_MANUAL_OR_NODAL = "MANUAL_OR_NODAL"


@dataclass(frozen=True)
class PhaseGroupGeometry:
    circuit_id: str
    parallel_index: int
    positions_by_phase: tuple[tuple[str, float, float], ...]
    resolved_arrangement: str
    phase_order: str
    canonical_spacing_m: float
    adjacent_distances_m: tuple[float, ...]
    triangle_distances_m: tuple[float, float, float]
    tolerance_m: float
    classification_residual_m: float

    def positions_dict(self) -> dict[str, tuple[float, float]]:
        return {phase: (x, depth) for phase, x, depth in self.positions_by_phase}


@dataclass(frozen=True)
class ResolvedRegionGeometry:
    region_id: str
    cross_section_id: str
    geometry_basis: str
    installation_type: str
    burial_depth_m: float
    phase_groups: tuple[PhaseGroupGeometry, ...]
    geometry_fingerprint: str
    projection: Mapping[str, Any]
    trace: tuple[str, ...]

    def group(self, circuit_id: str, parallel_index: int) -> PhaseGroupGeometry | None:
        exact = next((g for g in self.phase_groups if g.circuit_id == circuit_id and g.parallel_index == parallel_index), None)
        if exact is not None:
            return exact
        return self.phase_groups[0] if self.phase_groups else None


@dataclass(frozen=True)
class ResolvedInstallationGeometry:
    regions: tuple[ResolvedRegionGeometry, ...]
    fingerprint: str

    def for_region(self, region_id: str) -> ResolvedRegionGeometry | None:
        return next((item for item in self.regions if item.region_id == region_id), None)


def production_coupling_active(project: ProjectData) -> bool:
    return str(project.installation_design.solver_coupling_mode).upper() == INSTALLATION_COUPLING_PRODUCTION_LINKED


def cross_section_for_region(project: ProjectData, region_id: str) -> InstallationCrossSectionData | None:
    if not production_coupling_active(project):
        return None
    matches = [item for item in project.installation_design.cross_sections if region_id in set(item.region_ids)]
    return matches[0] if len(matches) == 1 else None


def _active_circuits(section: InstallationCrossSectionData, limit: int | None = None) -> list[str]:
    ids = [str(item.circuit_id) for item in section.circuits if item.active]
    if not ids:
        ids = sorted({str(item.circuit_id) for item in section.physical_cables if item.active})
    if limit is not None:
        ids = ids[: max(1, int(limit))]
    return ids


def active_physical_cables(section: InstallationCrossSectionData, active_circuit_count: int | None = None) -> tuple[PhysicalCableData, ...]:
    circuit_ids = set(_active_circuits(section, active_circuit_count))
    values = [
        item for item in section.physical_cables
        if item.active and str(item.circuit_id) in circuit_ids and str(item.phase).upper() in {"A", "B", "C"}
    ]
    values.sort(key=lambda item: (str(item.circuit_id), int(item.parallel_index), "ABC".index(str(item.phase).upper())))
    return tuple(values)


def physical_positions_for_region(project: ProjectData, region_id: str, active_circuit_count: int | None = None) -> tuple[tuple[float, float], ...] | None:
    section = cross_section_for_region(project, region_id)
    if section is None:
        return None
    values = active_physical_cables(section, active_circuit_count)
    return tuple((float(item.x_m), float(item.depth_m)) for item in values) if values else None


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _phase_groups(section: InstallationCrossSectionData, cable_outer_diameter_m: float) -> tuple[PhaseGroupGeometry, ...]:
    grouped: dict[tuple[str, int], dict[str, tuple[float, float]]] = {}
    for item in active_physical_cables(section):
        grouped.setdefault((str(item.circuit_id), int(item.parallel_index)), {})[str(item.phase).upper()] = (
            float(item.x_m), float(item.depth_m)
        )
    output: list[PhaseGroupGeometry] = []
    diameter = max(0.001, float(cable_outer_diameter_m))
    for (circuit_id, parallel_index), points in sorted(grouped.items()):
        if set(points) != {"A", "B", "C"}:
            continue
        a, b, c = points["A"], points["B"], points["C"]
        triangle = (_distance(a, b), _distance(b, c), _distance(c, a))
        mean_triangle = sum(triangle) / 3.0
        tolerance = max(0.003, 0.02 * diameter, 0.02 * mean_triangle)
        sorted_x = sorted(points.items(), key=lambda item: item[1][0])
        sorted_d = sorted(points.items(), key=lambda item: item[1][1])
        x_span = max(p[0] for p in points.values()) - min(p[0] for p in points.values())
        d_span = max(p[1] for p in points.values()) - min(p[1] for p in points.values())
        horizontal_adj = tuple(_distance(sorted_x[i][1], sorted_x[i + 1][1]) for i in range(2))
        vertical_adj = tuple(_distance(sorted_d[i][1], sorted_d[i + 1][1]) for i in range(2))
        tri_residual = max(triangle) - min(triangle)
        if tri_residual <= tolerance:
            arrangement = "TREFOIL"
            adjacent = triangle
            spacing = mean_triangle
            order = "".join(phase for phase, _ in sorted(points.items(), key=lambda item: (item[1][1], item[1][0])))
            residual = tri_residual
        elif d_span <= tolerance and abs(horizontal_adj[0] - horizontal_adj[1]) <= tolerance:
            arrangement = "FLAT"
            adjacent = horizontal_adj
            spacing = sum(horizontal_adj) / 2.0
            order = "".join(phase for phase, _ in sorted_x)
            residual = max(d_span, abs(horizontal_adj[0] - horizontal_adj[1]))
        elif x_span <= tolerance and abs(vertical_adj[0] - vertical_adj[1]) <= tolerance:
            arrangement = "VERTICAL"
            adjacent = vertical_adj
            spacing = sum(vertical_adj) / 2.0
            order = "".join(phase for phase, _ in sorted_d)
            residual = max(x_span, abs(vertical_adj[0] - vertical_adj[1]))
        else:
            arrangement = "CUSTOM"
            adjacent = tuple(sorted(triangle)[:2])
            spacing = (triangle[0] * triangle[1] * triangle[2]) ** (1.0 / 3.0)
            order = "".join(phase for phase, _ in sorted(points.items(), key=lambda item: (item[1][0], item[1][1])))
            residual = max(tri_residual, x_span, d_span)
        if str(section.arrangement_label or "").strip().upper() == "CUSTOM":
            arrangement = "CUSTOM"
        output.append(PhaseGroupGeometry(
            circuit_id, parallel_index,
            tuple((phase, float(points[phase][0]), float(points[phase][1])) for phase in "ABC"),
            arrangement, order, max(diameter, spacing), tuple(adjacent), triangle,
            tolerance, residual,
        ))
    return tuple(output)


def equivalent_phase_spacing_m(section: InstallationCrossSectionData, cable_outer_diameter_m: float) -> float:
    """Legacy/UI summary only; production bonding uses per-group coordinates."""
    samples = [item.canonical_spacing_m for item in _phase_groups(section, cable_outer_diameter_m)]
    return max(float(cable_outer_diameter_m), float(median(samples))) if samples else max(float(cable_outer_diameter_m), 0.001)


def equivalent_circuit_spacing_m(section: InstallationCrossSectionData, fallback: float) -> float:
    centroids: list[tuple[float, float]] = []
    for circuit_id in _active_circuits(section):
        points = [item for item in section.physical_cables if item.active and str(item.circuit_id) == circuit_id]
        if points:
            centroids.append((sum(float(x.x_m) for x in points) / len(points), sum(float(x.depth_m) for x in points) / len(points)))
    distances = [_distance(a, b) for index, a in enumerate(centroids) for b in centroids[index + 1:]]
    return max(0.001, float(median(distances)) if distances else float(fallback))


def equivalent_parallel_spacing_m(section: InstallationCrossSectionData, fallback: float) -> float:
    centres: dict[tuple[str, int], tuple[float, float]] = {}
    for circuit_id in _active_circuits(section):
        indexes = sorted({int(item.parallel_index) for item in section.physical_cables if item.active and str(item.circuit_id) == circuit_id})
        for parallel in indexes:
            points = [item for item in section.physical_cables if item.active and str(item.circuit_id) == circuit_id and int(item.parallel_index) == parallel]
            if points:
                centres[(circuit_id, parallel)] = (sum(float(x.x_m) for x in points) / len(points), sum(float(x.depth_m) for x in points) / len(points))
    distances: list[float] = []
    for circuit_id in _active_circuits(section):
        keys = sorted((key for key in centres if key[0] == circuit_id), key=lambda key: key[1])
        distances.extend(_distance(centres[a], centres[b]) for a, b in zip(keys, keys[1:]))
    return max(0.001, float(median(distances)) if distances else float(fallback))


def _arrangement(section: InstallationCrossSectionData) -> str:
    """Backward-compatible arrangement projection used by FAZ 3.2 tests/UI."""
    diameter = 0.001
    groups = _phase_groups(section, diameter)
    return _arrangement_summary(groups, section)


def _geometry_basis(section: InstallationCrossSectionData) -> str:
    if active_physical_cables(section):
        return GEOMETRY_BASIS_PHYSICAL if str(section.data_state).upper() == INSTALLATION_STATE_VERIFIED else GEOMETRY_BASIS_TEMPLATE
    return GEOMETRY_BASIS_LEGACY


def _arrangement_summary(groups: Iterable[PhaseGroupGeometry], section: InstallationCrossSectionData) -> str:
    values = {item.resolved_arrangement for item in groups}
    if len(values) == 1:
        return next(iter(values)).title()
    if values:
        return "Custom"
    raw = str(section.arrangement_label or "CUSTOM").strip().upper()
    if raw in {"TREFOIL", "FLAT", "VERTICAL", "SINGLE", "CUSTOM"}:
        return raw.title()
    raise InstallationCouplingError(f"ARRANGEMENT_UNSUPPORTED: Geometrisiz/bilinmeyen faz formasyonu: {section.arrangement_label}")


def _material_map(project: ProjectData) -> dict[str, Any]:
    return {str(item.material_id): item for item in project.thermal_design.materials}


def _rho(materials: Mapping[str, Any], material_id: str, fallback: float = 0.0) -> float:
    item = materials.get(str(material_id))
    value = float(getattr(item, "thermal_resistivity_km_w", fallback) or fallback)
    return value


def _layered_reduction(project: ProjectData, section: InstallationCrossSectionData, physical: tuple[PhysicalCableData, ...]) -> dict[str, Any]:
    """Project physical section to an analytical-preview provenance payload.

    The routine may still derive a fast mixed-zone preview, but it never grants
    IEC analytical authority to a layered/complex physical field. Authority is
    classified orthogonally from the material field and geometry basis.
    """
    geometry = section.channel_geometry
    materials = _material_map(project)
    diameter = max(0.001, float(project.cable.overall_diameter_mm) / 1000.0)
    radius = diameter / 2.0
    trench_depth = max(0.30, float(geometry.trench_depth_m))
    trench_width = max(0.20, float(geometry.trench_width_m))
    bedding = max(0.0, min(float(geometry.bedding_thickness_m), trench_depth))
    thermal_backfill = max(0.0, min(float(geometry.thermal_backfill_height_m), trench_depth - bedding))
    near_top = max(0.0, trench_depth - bedding - thermal_backfill)
    selected = max(0.0, min(float(geometry.selected_fill_thickness_m), near_top))
    surface = max(0.0, min(float(geometry.surface_layer_thickness_m), near_top))
    general = max(0.0, near_top - selected - surface)
    trace: list[str] = []
    complex_reasons: list[str] = []
    if any(item.active and len(item.vertices_m) >= 3 for item in section.material_regions):
        complex_reasons.append("aktif özel malzeme poligonu")
    if bool(geometry.cover_slab_enabled):
        complex_reasons.append("koruma plakası/slabı")

    if str(section.installation_type).upper() != THERMAL_INSTALL_DIRECT_BURIED:
        return {
            "material_field_class": MATERIAL_COMPLEX_REGIONS,
            "analytical_preview_allowed": False,
            "authoritative_method": AUTH_METHOD_NODAL,
            "analytic_result_authority": RESULT_ENGINEERING_APPROXIMATION,
            "authority_reason_code": "ANALYTIC_INSTALLATION_SCOPE_REQUIRES_NODAL",
            "layer_reduction_trace": ("Doğrudan gömülü olmayan kurulum; jenerik mixed-zone preview uygulanmadı.",),
            "backfill_effective_radius_m": 0.0,
            "surface_thermal_correction_km_w": 0.0,
            "surface_correction_raw_km_w": 0.0,
            "surface_correction_clamped": False,
        }

    if not physical:
        return {
            "material_field_class": MATERIAL_HOMOGENEOUS,
            "analytical_preview_allowed": True,
            "authoritative_method": AUTH_METHOD_LEGACY,
            "analytic_result_authority": RESULT_DERIVED_FROM_SCALAR,
            "authority_reason_code": "LEGACY_SCALAR_GEOMETRY_AUTHORITY_LIMIT",
            "layer_reduction_trace": ("Fiziksel kesit yok; legacy skaler geometri analitik preview olarak kullanılıyor.",),
            "backfill_effective_radius_m": 0.0,
            "surface_thermal_correction_km_w": 0.0,
            "surface_correction_raw_km_w": 0.0,
            "surface_correction_clamped": False,
        }

    near_material_ids = {str(geometry.bedding_material_id), str(geometry.thermal_backfill_material_id)}
    near_rhos = {_rho(materials, item) for item in near_material_ids if item}
    if len({round(value, 9) for value in near_rhos if value > 0}) > 1:
        complex_reasons.append("kabloyu çevreleyen yatak ve termal dolgu farklı rho değerlerinde")

    left = float(geometry.center_x_m) - trench_width / 2.0
    right = float(geometry.center_x_m) + trench_width / 2.0
    clearances: list[float] = []
    for item in physical:
        x, depth = float(item.x_m), float(item.depth_m)
        clearances.extend((x - left, right - x, depth - near_top, trench_depth - depth))
    effective_radius = min(clearances) if clearances else 0.0
    if effective_radius <= radius + 1e-9:
        trace.append(
            "Kablo zarfı parametrik hendek sınırına temas ediyor/aşıyor; hızlı mixed-zone preview yarıçapı muhafazakâr minimuma sınırlandı."
        )
        effective_radius = radius * 1.05

    native_id = str(geometry.native_soil_material_id)
    native_rho = _rho(materials, native_id, 1.2)
    layers: list[tuple[str, float, float]] = []
    if selected > 0:
        layers.append((str(geometry.selected_fill_material_id), selected, _rho(materials, str(geometry.selected_fill_material_id), native_rho)))
    if general > 0:
        layers.append((str(geometry.general_fill_material_id), general, _rho(materials, str(geometry.general_fill_material_id), native_rho)))
    if surface > 0:
        if not str(geometry.surface_material_id):
            complex_reasons.append("yüzey katmanı var fakat yüzey malzemesi tanımsız")
        else:
            layers.append((str(geometry.surface_material_id), surface, _rho(materials, str(geometry.surface_material_id), native_rho)))

    top_depth = min(float(item.depth_m) for item in physical)
    correction = 0.0
    bottom = near_top
    for material_id, thickness, layer_rho in layers:
        upper = max(0.0, bottom - thickness)
        far = max(top_depth - upper, radius * 1.01)
        near = max(top_depth - bottom, radius * 1.01)
        if far > near:
            correction += (layer_rho - native_rho) / (2.0 * pi) * log(far / near)
        trace.append(f"Üst katman {material_id}: t={thickness:.4f} m, rho={layer_rho:.4f}")
        bottom = upper
    raw_correction = correction
    clamped = correction < 0.0
    if clamped:
        trace.append(f"Hesaplanan yüzey düzeltmesi {correction:.6f} K·m/W; muhafazakâr preview politikasıyla 0'a sınırlandı.")
        correction = 0.0

    backfill_rho = _rho(materials, str(geometry.thermal_backfill_material_id), native_rho)
    materially_layered = bool(layers) or abs(backfill_rho - native_rho) > 1e-12
    if complex_reasons:
        material_class = MATERIAL_COMPLEX_REGIONS
        reason_code = "ANALYTIC_COMPLEX_REGIONS_REQUIRES_NODAL"
    elif materially_layered:
        material_class = MATERIAL_LAYERED
        reason_code = "ANALYTIC_LAYERED_GEOMETRY_REQUIRES_NODAL"
    else:
        material_class = MATERIAL_HOMOGENEOUS
        reason_code = ""

    trace.extend((
        f"Malzeme alanı={material_class}.",
        f"Bağlı yakın alan sınırı: top={near_top:.4f} m, bottom={trench_depth:.4f} m.",
        f"Eşdeğer dolgu preview yarıçapı={effective_radius:.6f} m.",
        f"Yüzey preview düzeltmesi={correction:.6f} K·m/W.",
    ))
    return {
        "material_field_class": material_class,
        "analytical_preview_allowed": True,
        "authoritative_method": AUTH_METHOD_ANALYTIC if material_class == MATERIAL_HOMOGENEOUS else AUTH_METHOD_NODAL,
        "analytic_result_authority": RESULT_IEC_ANALYTIC if material_class == MATERIAL_HOMOGENEOUS else RESULT_ENGINEERING_APPROXIMATION,
        "authority_reason_code": reason_code,
        "authority_reason_message": "; ".join(complex_reasons) if complex_reasons else "",
        "layer_reduction_trace": tuple(trace),
        "backfill_effective_radius_m": max(radius * 1.05, effective_radius),
        "surface_thermal_correction_km_w": correction,
        "surface_correction_raw_km_w": raw_correction,
        "surface_correction_clamped": clamped,
    }


def _fingerprint_payload(project: ProjectData, section: InstallationCrossSectionData, groups: tuple[PhaseGroupGeometry, ...], reduction: Mapping[str, Any]) -> dict[str, Any]:
    geometry = section.channel_geometry
    return {
        "cross_section_id": section.cross_section_id,
        "region_ids": sorted(section.region_ids),
        "installation_type": str(section.installation_type).upper(),
        "data_state": str(section.data_state).upper(),
        "cable_od_mm": round(float(project.cable.overall_diameter_mm), 9),
        "physical_cables": [
            {
                "id": item.physical_cable_id, "circuit": item.circuit_id, "parallel": int(item.parallel_index),
                "phase": str(item.phase).upper(), "x": round(float(item.x_m), 9), "depth": round(float(item.depth_m), 9),
                "duct_slot": item.duct_slot_id, "active": bool(item.active),
                "current_override": round(float(item.current_override_a), 9),
                "current_angle": item.current_angle_override_deg,
            }
            for item in active_physical_cables(section)
        ],
        "groups": [
            {"circuit": g.circuit_id, "parallel": g.parallel_index, "arrangement": g.resolved_arrangement,
             "positions": g.positions_by_phase, "spacing": round(g.canonical_spacing_m, 9)} for g in groups
        ],
        "channel_geometry": {
            key: getattr(geometry, key) for key in geometry.__dataclass_fields__
            if key not in {"notes"}
        },
        "material_regions": [
            {"id": item.region_id, "material": item.material_id, "vertices": item.vertices_m, "priority": item.priority}
            for item in section.material_regions if item.active
        ],
        "reduction": {key: value for key, value in reduction.items() if key not in {"layer_reduction_trace"}},
    }


def _geometry_projection(project: ProjectData, section: InstallationCrossSectionData) -> dict[str, Any]:
    geometry = section.channel_geometry
    physical = active_physical_cables(section)
    diameter = max(0.001, float(project.cable.overall_diameter_mm) / 1000.0)
    radius = diameter / 2.0
    groups = _phase_groups(section, diameter)
    cable_top = min((float(item.depth_m) - radius for item in physical), default=1.0)
    cable_span = max((float(item.x_m) for item in physical), default=0.0) - min((float(item.x_m) for item in physical), default=0.0) + diameter
    trench_depth = max(0.30, float(geometry.trench_depth_m))
    bedding = max(0.0, min(float(geometry.bedding_thickness_m), trench_depth))
    thermal_backfill = max(0.0, min(float(geometry.thermal_backfill_height_m), trench_depth - bedding))
    backfill_top = max(0.0, trench_depth - bedding - thermal_backfill)
    cable_cover = max(0.0, cable_top - backfill_top)
    selected = max(0.0, min(float(geometry.selected_fill_thickness_m), backfill_top))
    surface = max(0.0, min(float(geometry.surface_layer_thickness_m), backfill_top))
    general = max(0.0, backfill_top - selected - surface)
    reduction = _layered_reduction(project, section, physical)
    geometry_basis = _geometry_basis(section)
    if geometry_basis == GEOMETRY_BASIS_LEGACY:
        reduction["authoritative_method"] = AUTH_METHOD_LEGACY
        reduction["analytic_result_authority"] = RESULT_DERIVED_FROM_SCALAR
        reduction["authority_reason_code"] = "LEGACY_SCALAR_GEOMETRY_AUTHORITY_LIMIT"
    fingerprint = sha256(json.dumps(_fingerprint_payload(project, section, groups, reduction), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    arrangement = _arrangement_summary(groups, section)
    projection: dict[str, Any] = {
        "installation_type": str(section.installation_type).upper(),
        "arrangement": arrangement,
        "burial_depth_m": (
            median([sum(depth for _phase, _x, depth in g.positions_by_phase) / len(g.positions_by_phase)
                    for g in groups if g.resolved_arrangement == "TREFOIL" and g.positions_by_phase])
            if any(g.resolved_arrangement == "TREFOIL" and g.positions_by_phase for g in groups)
            else min((float(item.depth_m) for item in physical), default=project.design_basis.burial_depth_m)
        ),
        "phase_spacing_m": equivalent_phase_spacing_m(section, diameter),
        "circuit_spacing_m": equivalent_circuit_spacing_m(section, project.design_basis.circuit_spacing_m),
        "parallel_cable_spacing_m": equivalent_parallel_spacing_m(section, 0.20),
        "trench_width_m": max(0.20, float(geometry.trench_width_m)),
        "trench_depth_m": trench_depth,
        "bedding_thickness_m": bedding,
        "side_backfill_width_m": max(0.0, (float(geometry.trench_width_m) - cable_span) / 2.0),
        "cable_cover_height_m": cable_cover,
        "selected_upper_fill_thickness_m": selected,
        "general_upper_fill_thickness_m": general,
        "surface_layer_thickness_m": surface,
        "native_soil_material_id": str(geometry.native_soil_material_id),
        "bedding_material_id": str(geometry.bedding_material_id),
        "side_backfill_material_id": str(geometry.thermal_backfill_material_id),
        "cable_cover_material_id": str(geometry.thermal_backfill_material_id),
        "selected_upper_fill_material_id": str(geometry.selected_fill_material_id),
        "general_fill_material_id": str(geometry.general_fill_material_id),
        "surface_material_id": str(geometry.surface_material_id),
        "trench_center_x_m": float(geometry.center_x_m),
        "trench_side_slope_h_to_v": max(0.0, float(geometry.side_slope_h_to_v)),
        "cross_section_id": str(section.cross_section_id),
        "geometry_basis": geometry_basis,
        "geometry_fingerprint": fingerprint,
        "phase_groups": groups,
        **reduction,
        "duct_bank_center_x_m": float(geometry.center_x_m),
        "duct_bank_width_m": max(0.10, float(geometry.duct_bank_width_m)),
        "duct_bank_height_m": max(0.10, float(geometry.duct_bank_height_m)),
        "cover_slab_enabled": bool(geometry.cover_slab_enabled),
        "cover_slab_width_m": max(0.0, float(geometry.cover_slab_width_m)),
        "cover_slab_thickness_m": max(0.0, float(geometry.cover_slab_thickness_m)),
        "cover_slab_depth_m": max(0.0, float(geometry.cover_slab_depth_m)),
        "cover_slab_material_id": str(geometry.cover_slab_material_id),
        "grout_material_id": str(geometry.hdd_grout_material_id if str(section.installation_type).upper() == "HDD" else geometry.duct_bank_material_id),
        "trough_inner_width_m": max(0.0, float(geometry.trough_inner_width_m)),
        "trough_inner_height_m": max(0.0, float(geometry.trough_inner_height_m)),
        "trough_wall_thickness_m": max(0.0, float(geometry.trough_wall_thickness_m)),
        "trough_material_id": str(geometry.trough_material_id),
        "tunnel_width_m": max(0.0, float(geometry.tunnel_width_m)),
        "tunnel_height_m": max(0.0, float(geometry.tunnel_height_m)),
        "tunnel_lining_material_id": str(geometry.trough_material_id),
        "duct_slots": tuple({
            "slot_id": str(item.slot_id), "x_m": float(item.x_m), "depth_m": float(item.depth_m),
            "inner_diameter_m": max(0.001, float(item.inner_diameter_m)),
            "outer_diameter_m": max(float(item.outer_diameter_m), float(item.inner_diameter_m) + 0.001),
            "occupied_by": tuple(c.physical_cable_id for c in section.physical_cables if c.active and c.duct_slot_id == item.slot_id),
        } for item in section.duct_slots if item.active),
        "custom_material_regions": tuple({
            "region_id": str(item.region_id), "material_id": str(item.material_id),
            "vertices_m": tuple((float(point[0]), float(point[1])) for point in item.vertices_m),
            "priority": int(item.priority),
        } for item in sorted(section.material_regions, key=lambda entry: int(entry.priority)) if item.active and len(item.vertices_m) >= 3),
    }
    return projection


def resolve_installation_geometry(project: ProjectData) -> ResolvedInstallationGeometry:
    regions: list[ResolvedRegionGeometry] = []
    if production_coupling_active(project):
        for section in project.installation_design.cross_sections:
            projection = _geometry_projection(project, section)
            groups = tuple(projection.get("phase_groups", ()))
            trace = (
                f"Geometri temeli={projection['geometry_basis']}",
                f"Kesit={section.cross_section_id}; fiziksel kablo={len(active_physical_cables(section))}",
                f"Malzeme alanı={projection.get('material_field_class', '')}; analitik yetki={projection.get('analytic_result_authority', '')}; otorite={projection.get('authoritative_method', '')}",
                *tuple(projection.get("layer_reduction_trace", ())),
            )
            for region_id in section.region_ids:
                regions.append(ResolvedRegionGeometry(
                    str(region_id), str(section.cross_section_id), str(projection["geometry_basis"]),
                    str(projection["installation_type"]), float(projection["burial_depth_m"]),
                    groups, str(projection["geometry_fingerprint"]), projection, trace,
                ))
    combined = sha256("|".join(sorted(item.geometry_fingerprint for item in regions)).encode("utf-8")).hexdigest()
    return ResolvedInstallationGeometry(tuple(regions), combined)


def _target_group(project: ProjectData, region: ResolvedRegionGeometry) -> PhaseGroupGeometry | None:
    return region.group(str(getattr(project.bonding, "target_circuit_id", "C1") or "C1"), int(getattr(project.bonding, "target_parallel_index", 1) or 1))


def attach_resolved_geometry_to_route_sections(project: ProjectData, sections: Iterable[RouteSection]) -> list[RouteSection]:
    resolved = resolve_installation_geometry(project)
    output: list[RouteSection] = []
    for section in sections:
        region = resolved.for_region(str(section.thermal_region_id or ""))
        if region is None:
            section.geometry_basis = GEOMETRY_BASIS_LEGACY
            section.geometry_trace = ["Fiziksel kesit eşleşmesi yok; legacy skaler geometri."]
            output.append(section)
            continue
        group = _target_group(project, region)
        section.geometry_basis = region.geometry_basis
        section.geometry_fingerprint = region.geometry_fingerprint
        section.cross_section_id = region.cross_section_id
        section.burial_depth_m = region.burial_depth_m
        section.geometry_trace = list(region.trace)
        if group is not None:
            section.resolved_arrangement = group.resolved_arrangement
            section.phase_spacing_m = group.canonical_spacing_m
            section.bonding_circuit_id = group.circuit_id
            section.bonding_parallel_index = group.parallel_index
            section.phase_positions_m = {phase: [x, depth] for phase, x, depth in group.positions_by_phase}
            section.geometry_trace.append(
                f"Bonding hedefi={group.circuit_id}/P{group.parallel_index}; formasyon={group.resolved_arrangement}; faz sırası={group.phase_order}"
            )
        output.append(section)
    return output


def synchronize_installation_geometry(project: ProjectData) -> tuple[str, ...]:
    if not production_coupling_active(project):
        return ()
    changed_regions: list[str] = []
    region_map = {item.region_id: item for item in project.thermal_design.regions}
    profile_by_region: dict[str, dict[str, Any]] = {}
    for section in project.installation_design.cross_sections:
        projection = _geometry_projection(project, section)
        scalar_projection = {key: value for key, value in projection.items() if key not in {"phase_groups", "layer_reduction_trace"}}
        for region_id in section.region_ids:
            profile_by_region[str(region_id)] = projection
            region = region_map.get(region_id)
            if region is None:
                continue
            overrides = dict(region.overrides or {})
            for legacy_key in (
                "layer_reduction_status", "far_field_effective_rho_km_w",
                "analytic_scope_error_code", "analytic_scope_error_message",
            ):
                overrides.pop(legacy_key, None)
            overrides.update(scalar_projection)
            region.overrides = overrides
            region.source_reference = f"INSTALLATION_PRODUCTION_LINK:{section.cross_section_id}"
            changed_regions.append(region_id)
    for index, route in enumerate(project.route_sections):
        region_id = str(route.thermal_region_id or "")
        if not region_id and index < len(project.thermal_design.regions):
            region_id = str(project.thermal_design.regions[index].region_id)
        projection = profile_by_region.get(region_id)
        if projection is None:
            continue
        route.thermal_region_id = region_id
        route.section_type = str(projection["installation_type"])
        route.burial_depth_m = float(projection["burial_depth_m"])
        route.phase_spacing_m = float(projection["phase_spacing_m"])
        route.cross_section_id = str(projection["cross_section_id"])
        route.backfill_effective_radius_m = float(projection.get("backfill_effective_radius_m", route.backfill_effective_radius_m))
        route.surface_thermal_correction_km_w = float(projection.get("surface_thermal_correction_km_w", route.surface_thermal_correction_km_w))
        material = _material_map(project).get(str(projection["native_soil_material_id"]))
        if material is not None:
            route.soil_thermal_resistivity_km_w = float(material.thermal_resistivity_km_w)
        backfill = _material_map(project).get(str(projection["cable_cover_material_id"]))
        if backfill is not None:
            route.backfill_thermal_resistivity_km_w = float(backfill.thermal_resistivity_km_w)
    project.route_sections = attach_resolved_geometry_to_route_sections(project, project.route_sections)
    all_groups = [g for region in resolve_installation_geometry(project).regions for g in region.phase_groups]
    arrangements = {g.resolved_arrangement for g in all_groups}
    if len(arrangements) == 1:
        project.cable.arrangement = next(iter(arrangements)).title()
    elif arrangements:
        project.cable.arrangement = "Custom"
    if len(profile_by_region) == 1:
        representative = next(iter(profile_by_region.values()))
        project.design_basis.burial_depth_m = float(representative["burial_depth_m"])
        project.design_basis.phase_spacing_m = float(representative["phase_spacing_m"])
        project.design_basis.circuit_spacing_m = float(representative["circuit_spacing_m"])
    # Deliberately do not overwrite bonding.phase_spacing_m from a random region.
    project.installation_design.model_revision = "0.16.9.4.34"
    return tuple(dict.fromkeys(changed_regions))


def project_with_synchronized_installation_geometry(project: ProjectData) -> ProjectData:
    projected = deepcopy(project)
    synchronize_installation_geometry(projected)
    return projected


def channel_profile_and_overrides(project: ProjectData, profile: Any, section: InstallationCrossSectionData) -> tuple[Any, dict[str, object]]:
    projection = _geometry_projection(project, section)
    material_map = _material_map(project)
    def material(key: str, fallback: Any) -> Any:
        return material_map.get(str(projection[key]), fallback)
    resolved = replace(
        profile,
        installation_type=str(projection["installation_type"]), arrangement=str(projection["arrangement"]),
        burial_depth_m=float(projection["burial_depth_m"]), phase_spacing_m=float(projection["phase_spacing_m"]),
        circuit_spacing_m=float(projection["circuit_spacing_m"]), trench_width_m=float(projection["trench_width_m"]),
        trench_depth_m=float(projection["trench_depth_m"]), bedding_thickness_m=float(projection["bedding_thickness_m"]),
        side_backfill_width_m=float(projection["side_backfill_width_m"]), cable_cover_height_m=float(projection["cable_cover_height_m"]),
        selected_upper_fill_thickness_m=float(projection["selected_upper_fill_thickness_m"]),
        general_upper_fill_thickness_m=float(projection["general_upper_fill_thickness_m"]),
        surface_layer_thickness_m=float(projection["surface_layer_thickness_m"]),
        native_soil=material("native_soil_material_id", profile.native_soil),
        bedding=material("bedding_material_id", profile.bedding),
        side_backfill=material("side_backfill_material_id", profile.side_backfill),
        cable_cover=material("cable_cover_material_id", profile.cable_cover),
        selected_upper_fill=material("selected_upper_fill_material_id", profile.selected_upper_fill),
        general_fill=material("general_fill_material_id", profile.general_fill),
        surface=(material("surface_material_id", profile.surface) if str(projection["surface_material_id"]) else profile.surface),
        backfill_effective_radius_m=float(projection.get("backfill_effective_radius_m", profile.backfill_effective_radius_m)),
        surface_thermal_correction_km_w=float(projection.get("surface_thermal_correction_km_w", profile.surface_thermal_correction_km_w)),
        source_reference=(str(profile.source_reference) + f"; INSTALLATION_PRODUCTION_LINK:{section.cross_section_id}:{projection['geometry_fingerprint'][:12]}").strip("; "),
        trace=tuple(profile.trace) + (
            f"Üretim geometri bağı aktif: {section.cross_section_id}",
            f"Geometri hash={projection['geometry_fingerprint'][:16]}; temel={projection['geometry_basis']}",
            f"Malzeme alanı={projection.get('material_field_class', '')}; analitik yetki={projection.get('analytic_result_authority', '')}; otorite={projection.get('authoritative_method', '')}",
            *tuple(projection.get("layer_reduction_trace", ())),
        ),
    )
    overrides = {key: value for key, value in projection.items() if key in {
        "trench_center_x_m", "trench_side_slope_h_to_v", "custom_material_regions", "duct_slots",
        "duct_bank_center_x_m", "duct_bank_width_m", "duct_bank_height_m", "cover_slab_enabled",
        "cover_slab_width_m", "cover_slab_thickness_m", "cover_slab_depth_m", "cover_slab_material_id",
        "grout_material_id", "trough_inner_width_m", "trough_inner_height_m", "trough_wall_thickness_m",
        "trough_material_id", "tunnel_width_m", "tunnel_height_m", "tunnel_lining_material_id",
    }}
    if section.duct_slots:
        slot = next((item for item in section.duct_slots if item.active), section.duct_slots[0])
        overrides["duct_inner_diameter_m"] = max(0.001, float(slot.inner_diameter_m))
        overrides["duct_outer_diameter_m"] = max(float(slot.outer_diameter_m), float(slot.inner_diameter_m) + 0.001)
    return resolved, overrides
