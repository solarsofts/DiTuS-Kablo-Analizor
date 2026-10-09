"""General N-core / N-sheath bonding-network shadow solver.

This additive v0.16.5.1 gate extends the local arbitrary-x/y multiconductor
kernel over the existing minor-section, link-box and grounding graph.  It does
not replace the locked production bonding, IEC 60287 or thermal result paths.

Engineering scope of this gate:

* every active physical cable sheath is a separate longitudinal conductor,
* route/thermal regions select their explicit physical cross-section,
* each minor section integrates all intersected route/cross-section blocks,
* local N-core phase-current sharing supplies the distributed induced sources,
* link-box cross connections preserve circuit and parallel identity,
* sheath/GCC node voltages and branch currents are solved independently by
  complex CIM/MNA and Node-Voltage elimination,
* all outputs remain SHADOW_COMPARE and are not written back to project lambda1.

The local core-current solution is deliberately sectional.  A future gate will
solve core continuity and sheath topology in one global constrained network.
"""

from __future__ import annotations

import cmath
from copy import deepcopy
from dataclasses import dataclass
from math import pi, sqrt
from typing import Iterable

import numpy as np
from scipy.sparse import bmat, block_diag, csc_matrix, diags, lil_matrix
from scipy.sparse.linalg import spsolve

from ucd.calculations.installation import phase_angle_deg
from ucd.calculations.multiconductor_em import (
    MODE_SHADOW_COMPARE,
    SHEATH_OPEN,
    MulticonductorEMInputError,
    _build_primitives,
    solve_multiconductor_em,
)
from ucd.calculations.primitive_cim import (
    _actual_link_impedance,
    _cross_mapping,
)
from ucd.models.project import (
    BONDING_CROSS,
    BONDING_SINGLE_POINT,
    BondingMinorSection,
    BondingSystemData,
    InstallationCrossSectionData,
    ProjectData,
    RouteSection,
)


REFERENCE = (
    "IEC 60287-1-3:2023 parallel single-core current/circulating-loss scope; "
    "IEEE 575-2014 / P575 sheath-bonding architecture; CIGRE TB 797; "
    "simplified-Carson primitive impedance at power frequency"
)

ENGINE_MODE = MODE_SHADOW_COMPARE
CORE_SHARING_MODE = "SECTION_LOCAL_OPEN_SHEATH"


class MulticonductorBondingInputError(ValueError):
    pass


@dataclass(frozen=True)
class MulticonductorBondingIssue:
    severity: str
    code: str
    message: str
    object_id: str = ""


@dataclass(frozen=True)
class MulticonductorBondingMethodResult:
    method: str
    node_voltages_v: tuple[complex, ...]
    branch_currents_a: tuple[complex, ...]
    matrix_condition_number: float
    equation_residual: float
    kcl_residual: float


@dataclass(frozen=True)
class MulticonductorSheathResult:
    section_id: str
    major_index: int
    circuit_id: str
    phase: str
    parallel_index: int
    physical_cable_id: str
    sheath_current_a: complex
    start_voltage_to_earth_v: complex
    end_voltage_to_earth_v: complex
    integrated_open_emf_v: complex
    sheath_metal_loss_w: float

    @property
    def key(self) -> str:
        return f"{self.circuit_id}:{self.phase}:P{self.parallel_index}"


@dataclass(frozen=True)
class MulticonductorBondingSectionResult:
    section_id: str
    section_name: str
    major_index: int
    start_m: float
    end_m: float
    route_cross_sections: tuple[str, ...]
    sheath_results: tuple[MulticonductorSheathResult, ...]
    gcc_current_a: complex
    gcc_start_voltage_v: complex
    gcc_end_voltage_v: complex
    sheath_metal_loss_w: float
    gcc_metal_loss_w: float
    earth_return_equivalent_loss_w: float
    core_metal_loss_w: float
    maximum_sheath_current_a: float
    maximum_sheath_to_earth_voltage_v: float
    maximum_sheath_to_sheath_voltage_v: float


@dataclass(frozen=True)
class MulticonductorBondingBranchResult:
    branch_id: str
    branch_type: str
    from_label: str
    to_label: str
    current_a: complex
    impedance_ohm: complex
    active_loss_w: float


@dataclass(frozen=True)
class MulticonductorBondingMatrixBlock:
    section_id: str
    route_name: str
    cross_section_id: str
    start_m: float
    end_m: float
    length_m: float
    conductor_order: tuple[str, ...]
    unknown_impedance_ohm: tuple[tuple[complex, ...], ...]
    induced_source_v: tuple[complex, ...]
    local_core_currents_a: tuple[complex, ...]
    local_lambda1: float


@dataclass
class MulticonductorBondingNetworkResult:
    mode: str
    core_sharing_mode: str
    reference: str
    sheath_order: tuple[str, ...]
    node_labels: tuple[str, ...]
    matrix_blocks: tuple[MulticonductorBondingMatrixBlock, ...]
    section_results: tuple[MulticonductorBondingSectionResult, ...]
    accessory_branches: tuple[MulticonductorBondingBranchResult, ...]
    cim: MulticonductorBondingMethodResult
    nv: MulticonductorBondingMethodResult
    selected_method: str
    methods_agree: bool
    maximum_method_voltage_difference_v: float
    maximum_method_current_difference_a: float
    maximum_sheath_current_a: float
    maximum_sheath_to_earth_voltage_v: float
    maximum_sheath_to_sheath_voltage_v: float
    maximum_gcc_current_a: float
    total_core_metal_loss_w: float
    total_sheath_metal_loss_w: float
    total_gcc_metal_loss_w: float
    total_earth_return_equivalent_loss_w: float
    total_accessory_loss_w: float
    lambda1: float
    node_count: int
    branch_current_count: int
    issues: tuple[MulticonductorBondingIssue, ...]
    trace: tuple[str, ...]

    @property
    def final_design_ready(self) -> bool:
        return False

    def trace_lines(self) -> list[str]:
        lines = [
            "DiTuS v0.16.5.1 — Genel N-İletken Bonding Ağı (SHADOW_COMPARE)",
            f"Referans: {self.reference}",
            f"Core paylaşım modu: {self.core_sharing_mode}",
            f"Fiziksel kılıf sayısı: {len(self.sheath_order)}",
            f"Düğüm/dal akımı: {self.node_count}/{self.branch_current_count}",
            f"CIM cond={self.cim.matrix_condition_number:.6g}; residual={self.cim.equation_residual:.3e}; KCL={self.cim.kcl_residual:.3e}",
            f"NV cond={self.nv.matrix_condition_number:.6g}; residual={self.nv.equation_residual:.3e}; KCL={self.nv.kcl_residual:.3e}",
            f"CIM↔NV: ΔV={self.maximum_method_voltage_difference_v:.6e} V; ΔI={self.maximum_method_current_difference_a:.6e} A",
            f"Maks. |Ish|={self.maximum_sheath_current_a:.6f} A; "
            f"maks. |Vsh-earth|={self.maximum_sheath_to_earth_voltage_v:.6f} V; "
            f"maks. |Vsh-sh|={self.maximum_sheath_to_sheath_voltage_v:.6f} V",
            f"Pcore/Psheath/Pgcc/Pearth/Pacc={self.total_core_metal_loss_w:.6f}/"
            f"{self.total_sheath_metal_loss_w:.6f}/{self.total_gcc_metal_loss_w:.6f}/"
            f"{self.total_earth_return_equivalent_loss_w:.6f}/{self.total_accessory_loss_w:.6f} W",
            f"λ1(shadow network)={self.lambda1:.8f}",
        ]
        for section in self.section_results:
            lines.append(
                f"{section.section_id} M{section.major_index} {section.start_m:.2f}-{section.end_m:.2f} m; "
                f"kesit={','.join(section.route_cross_sections)}; |Ish|max={section.maximum_sheath_current_a:.4f} A; "
                f"|Vsh-e|max={section.maximum_sheath_to_earth_voltage_v:.3f} V; "
                f"|Vsh-sh|max={section.maximum_sheath_to_sheath_voltage_v:.3f} V; "
                f"Psh={section.sheath_metal_loss_w:.4f} W"
            )
        lines.extend(self.trace)
        lines.extend(f"{item.severity} {item.code}: {item.message}" for item in self.issues)
        return lines


@dataclass(frozen=True)
class _SheathKey:
    circuit_id: str
    phase: str
    parallel_index: int

    @property
    def label(self) -> str:
        return f"{self.circuit_id}:{self.phase}:P{self.parallel_index}"


@dataclass
class _Branch:
    branch_id: str
    branch_type: str
    start_nodes: tuple[int, ...]
    end_nodes: tuple[int | None, ...]
    z: np.ndarray
    e: np.ndarray
    labels: tuple[str, ...]
    section_id: str = ""
    metallic_r_diag: tuple[float, ...] = ()
    core_loss_w: float = 0.0


@dataclass
class _NetworkModel:
    node_labels: list[str]
    branches: list[_Branch]
    g_shunt: np.ndarray
    j_injection: np.ndarray
    section_branch_index: dict[str, int]
    endpoints: dict[str, tuple[dict[str, int], dict[str, int]]]
    matrix_blocks: list[MulticonductorBondingMatrixBlock]
    sheath_keys: tuple[_SheathKey, ...]
    key_to_physical_id: dict[str, str]


def _angle(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


def _route_ranges(routes: Iterable[RouteSection]) -> list[tuple[float, float, RouteSection]]:
    result: list[tuple[float, float, RouteSection]] = []
    cursor = 0.0
    for route in routes:
        length = float(route.length_m)
        if length <= 0.0:
            raise MulticonductorBondingInputError(f"{route.name}: güzergâh uzunluğu sıfırdan büyük olmalı.")
        result.append((cursor, cursor + length, route))
        cursor += length
    if not result:
        raise MulticonductorBondingInputError("N-iletken bonding ağı için güzergâh bölümü bulunmuyor.")
    return result


def _node_positions(bonding: BondingSystemData) -> dict[str, float]:
    positions = {item.node_id: float(item.position_m) for item in bonding.nodes}
    if len(positions) != len(bonding.nodes):
        raise MulticonductorBondingInputError("Bonding düğüm kimlikleri benzersiz olmalı.")
    return positions


def _minor_bounds(minor: BondingMinorSection, positions: dict[str, float]) -> tuple[float, float]:
    if minor.start_node_id not in positions or minor.end_node_id not in positions:
        raise MulticonductorBondingInputError(f"{minor.section_id}: düğüm referansı bulunamadı.")
    start = positions[minor.start_node_id]
    end = positions[minor.end_node_id]
    if end <= start:
        raise MulticonductorBondingInputError(f"{minor.section_id}: başlangıç/bitiş konumu geçersiz.")
    return start, end


def _canonical_key_order(section: InstallationCrossSectionData) -> tuple[_SheathKey, ...]:
    circuit_order = {item.circuit_id: index for index, item in enumerate(section.circuits) if item.active}
    keys = {
        _SheathKey(item.circuit_id, str(item.phase).upper(), int(item.parallel_index))
        for item in section.physical_cables
        if item.active and item.circuit_id in circuit_order
    }
    if not keys:
        raise MulticonductorBondingInputError(f"{section.cross_section_id}: aktif fiziksel kablo bulunmuyor.")
    phase_order = {"A": 0, "B": 1, "C": 2}
    return tuple(sorted(keys, key=lambda k: (circuit_order[k.circuit_id], phase_order.get(k.phase, 99), k.parallel_index)))


def _section_key_maps(section: InstallationCrossSectionData) -> tuple[dict[_SheathKey, str], dict[str, _SheathKey]]:
    circuits = {item.circuit_id for item in section.circuits if item.active}
    by_key: dict[_SheathKey, str] = {}
    by_id: dict[str, _SheathKey] = {}
    for item in section.physical_cables:
        if not item.active or item.circuit_id not in circuits:
            continue
        key = _SheathKey(item.circuit_id, str(item.phase).upper(), int(item.parallel_index))
        if key in by_key:
            raise MulticonductorBondingInputError(
                f"{section.cross_section_id}: mükerrer fiziksel kablo anahtarı {key.label}."
            )
        by_key[key] = item.physical_cable_id
        by_id[item.physical_cable_id] = key
    return by_key, by_id


def _phase_voltage(project: ProjectData, phase: str) -> complex:
    magnitude = float(project.cable.voltage_kv) * 1000.0 / sqrt(3.0)
    return cmath.rect(magnitude, phase_angle_deg(phase) * pi / 180.0)


def _cross_section_for_route(
    project: ProjectData,
    route: RouteSection,
    chainage_m: float,
) -> InstallationCrossSectionData:
    sections = project.installation_design.cross_sections
    if not sections:
        raise MulticonductorBondingInputError("Fiziksel kurulum kesiti bulunmuyor.")

    region_id = str(route.thermal_region_id or "").strip()
    if not region_id:
        region = next(
            (item for item in project.thermal_design.regions
             if item.enabled and float(item.start_m) - 1e-9 <= chainage_m <= float(item.end_m) + 1e-9),
            None,
        )
        if region is not None:
            region_id = region.region_id
    if region_id:
        found = next((item for item in sections if region_id in item.region_ids), None)
        if found is not None:
            return found

    cross_id = str(route.cross_section_id or "").strip()
    if cross_id:
        found = next((item for item in sections if item.cross_section_id == cross_id), None)
        if found is not None:
            return found

    active_id = str(project.installation_design.active_cross_section_id or "").strip()
    found = next((item for item in sections if item.cross_section_id == active_id), None)
    if found is not None:
        return found
    return sections[0]




def _split_by_thermal_regions(
    project: ProjectData,
    start_m: float,
    end_m: float,
) -> tuple[tuple[float, float], ...]:
    boundaries = {float(start_m), float(end_m)}
    for region in project.thermal_design.regions:
        if not region.enabled:
            continue
        for value in (float(region.start_m), float(region.end_m)):
            if start_m + 1e-9 < value < end_m - 1e-9:
                boundaries.add(value)
    ordered = sorted(boundaries)
    return tuple((a, b) for a, b in zip(ordered, ordered[1:]) if b > a + 1e-12)


def _local_kernel(
    project: ProjectData,
    section: InstallationCrossSectionData,
    canonical_keys: tuple[_SheathKey, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, ...], float, float, tuple[str, ...], dict[str, str]]:
    section_keys = set(_section_key_maps(section)[0])
    if section_keys != set(canonical_keys):
        missing = sorted(key.label for key in set(canonical_keys) - section_keys)
        extra = sorted(key.label for key in section_keys - set(canonical_keys))
        raise MulticonductorBondingInputError(
            f"{section.cross_section_id}: güzergâh boyunca fiziksel kablo anahtarları değişiyor; "
            f"eksik={missing}, fazla={extra}. Kablo ekleme/çıkarma joint modeli henüz bu gölge kapıda yok."
        )
    try:
        kernel_project = project
        if any(
            float(item.current_override_a) > 0.0 or item.current_angle_override_deg is not None
            for item in section.physical_cables if item.active
        ):
            kernel_project = deepcopy(project)
            kernel_section = next(
                item for item in kernel_project.installation_design.cross_sections
                if item.cross_section_id == section.cross_section_id
            )
            for item in kernel_section.physical_cables:
                item.current_override_a = 0.0
                item.current_angle_override_deg = None
        result = solve_multiconductor_em(
            kernel_project,
            cross_section_id=section.cross_section_id,
            sheath_mode=SHEATH_OPEN,
        )
        primitives, gcc, _issues, _trace = _build_primitives(project, section)
    except MulticonductorEMInputError as exc:
        raise MulticonductorBondingInputError(str(exc)) from exc

    local_keys = tuple(
        _SheathKey(item.circuit_id, item.phase, item.parallel_index)
        for item in result.cable_results
    )
    if set(local_keys) != set(canonical_keys):
        missing = sorted(key.label for key in set(canonical_keys) - set(local_keys))
        extra = sorted(key.label for key in set(local_keys) - set(canonical_keys))
        raise MulticonductorBondingInputError(
            f"{section.cross_section_id}: güzergâh boyunca fiziksel kablo anahtarları değişiyor; "
            f"eksik={missing}, fazla={extra}. Kablo ekleme/çıkarma joint modeli henüz bu gölge kapıda yok."
        )
    local_index = {key: idx for idx, key in enumerate(local_keys)}
    perm = [local_index[key] for key in canonical_keys]
    nc = len(local_keys)
    full = np.asarray(result.primitive_impedance_ohm_km, dtype=complex)
    has_gcc = full.shape[0] == 2 * nc + 1
    unknown_local = list(range(nc, 2 * nc)) + ([2 * nc] if has_gcc else [])
    core_local = list(range(nc))
    unknown_perm = [nc + idx for idx in perm] + ([2 * nc] if has_gcc else [])
    core_perm = perm
    z_uu = full[np.ix_(unknown_perm, unknown_perm)]
    z_uc = full[np.ix_(unknown_perm, core_perm)]
    core_currents_local = np.asarray([item.core_current_a for item in result.cable_results], dtype=complex)
    core_currents = core_currents_local[core_perm]
    source = z_uc @ core_currents

    primitive_by_key = {
        _SheathKey(item.circuit_id, item.phase, item.parallel_index): item
        for item in primitives
    }
    metal_r = [primitive_by_key[key].sheath.resistance_ohm_km for key in canonical_keys]
    if has_gcc:
        if gcc is None:
            raise MulticonductorBondingInputError("GCC matris sırası ile primitive kaydı uyuşmuyor.")
        metal_r.append(gcc.resistance_ohm_km)
    key_to_id, _ = _section_key_maps(section)
    physical_ids = {key.label: key_to_id[key] for key in canonical_keys}
    labels = tuple(key.label for key in canonical_keys) + (("GCC",) if has_gcc else ())
    return z_uu, source, core_currents, tuple(float(v) for v in metal_r), result.total_core_loss_w_km, result.lambda1, labels, physical_ids


def _integrate_minor(
    project: ProjectData,
    routes: list[RouteSection],
    minor: BondingMinorSection,
    start_m: float,
    end_m: float,
    canonical_keys: tuple[_SheathKey, ...],
    cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, ...], float, float, tuple[str, ...], dict[str, str]]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, ...], float, list[MulticonductorBondingMatrixBlock], tuple[str, ...], dict[str, str]]:
    n_unknown = len(canonical_keys) + (1 if bool(project.bonding.gcc_enabled) else 0)
    z_total = np.zeros((n_unknown, n_unknown), dtype=complex)
    e_total = np.zeros(n_unknown, dtype=complex)
    y_total = np.zeros(len(canonical_keys), dtype=complex)
    metal_r_length = np.zeros(n_unknown, dtype=float)
    core_loss = 0.0
    blocks: list[MulticonductorBondingMatrixBlock] = []
    used_sections: list[str] = []
    physical_ids: dict[str, str] = {}

    omega = 2.0 * pi * float(project.cable.frequency_hz)
    c_f_per_km = max(0.0, float(project.cable.capacitance_uf_km)) * 1e-6
    y_per_km = complex(
        omega * c_f_per_km * max(0.0, float(project.cable.dielectric_loss_tan_delta)),
        omega * c_f_per_km,
    )
    charging = bool(getattr(project.bonding, "include_dielectric_charging", True))

    for route_start, route_end, route in _route_ranges(routes):
        overlap_start = max(start_m, route_start)
        overlap_end = min(end_m, route_end)
        if overlap_end <= overlap_start:
            continue
        for segment_start, segment_end in _split_by_thermal_regions(project, overlap_start, overlap_end):
            length_m = segment_end - segment_start
            section = _cross_section_for_route(project, route, (segment_start + segment_end) / 2.0)
            if section.cross_section_id not in cache:
                cache[section.cross_section_id] = _local_kernel(project, section, canonical_keys)
            z_km, e_km, core_i, metal_r_km, core_loss_km, local_lambda1, labels, local_ids = cache[section.cross_section_id]
            scale = length_m / 1000.0
            z_total += z_km * scale
            e_total += e_km * scale
            metal_r_length += np.asarray(metal_r_km, dtype=float) * scale
            core_loss += float(core_loss_km) * scale
            if charging:
                y_total += y_per_km * scale
            physical_ids.update(local_ids)
            used_sections.append(section.cross_section_id)
            blocks.append(MulticonductorBondingMatrixBlock(
                minor.section_id,
                route.name,
                section.cross_section_id,
                segment_start,
                segment_end,
                length_m,
                labels,
                tuple(tuple(complex(v * scale) for v in row) for row in z_km),
                tuple(complex(v * scale) for v in e_km),
                tuple(complex(v) for v in core_i),
                float(local_lambda1),
            ))
    if not blocks:
        raise MulticonductorBondingInputError(f"{minor.section_id}: güzergâhla kesişmiyor.")
    return (
        z_total,
        e_total,
        y_total,
        tuple(float(v) for v in metal_r_length),
        float(core_loss),
        blocks,
        tuple(dict.fromkeys(used_sections)),
        physical_ids,
    )


def _build_network(project: ProjectData) -> _NetworkModel:
    routes = list(project.route_sections)
    positions = _node_positions(project.bonding)
    minors = sorted(project.bonding.minor_sections, key=lambda item: _minor_bounds(item, positions)[0])
    if not minors:
        raise MulticonductorBondingInputError("N-iletken bonding ağı için minor section gerekli.")
    if project.bonding.scheme == BONDING_CROSS and len(minors) % 3 != 0:
        raise MulticonductorBondingInputError("Cross-bonding minor section sayısı üçün katı olmalı.")

    first_route = _route_ranges(routes)[0][2]
    first_section = _cross_section_for_route(project, first_route, 0.5 * float(first_route.length_m))
    canonical_keys = _canonical_key_order(first_section)
    key_labels = tuple(key.label for key in canonical_keys)
    has_gcc = bool(project.bonding.gcc_enabled)
    unknown_labels = key_labels + (("GCC",) if has_gcc else ())

    node_labels: list[str] = []
    node_index: dict[str, int] = {}

    def node(label: str) -> int:
        if label not in node_index:
            node_index[label] = len(node_labels)
            node_labels.append(label)
        return node_index[label]

    branches: list[_Branch] = []
    endpoints: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
    section_branch_index: dict[str, int] = {}
    matrix_blocks: list[MulticonductorBondingMatrixBlock] = []
    g_dynamic: list[complex] = []
    j_dynamic: list[complex] = []
    cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, ...], float, float, tuple[str, ...], dict[str, str]]] = {}
    key_to_physical_id: dict[str, str] = {}

    def ensure_vectors() -> None:
        while len(g_dynamic) < len(node_labels):
            g_dynamic.append(0j)
            j_dynamic.append(0j)

    for minor in minors:
        start_m, end_m = _minor_bounds(minor, positions)
        z, e, y, metal_r, core_loss, blocks, used_sections, physical_ids = _integrate_minor(
            project, routes, minor, start_m, end_m, canonical_keys, cache
        )
        key_to_physical_id.update(physical_ids)
        matrix_blocks.extend(blocks)
        start_nodes = {label: node(f"{minor.section_id}:S:{label}") for label in unknown_labels}
        end_nodes = {label: node(f"{minor.section_id}:E:{label}") for label in unknown_labels}
        ensure_vectors()
        if bool(getattr(project.bonding, "include_dielectric_charging", True)):
            for idx, key in enumerate(canonical_keys):
                y_half = y[idx] / 2.0
                for nidx in (start_nodes[key.label], end_nodes[key.label]):
                    g_dynamic[nidx] += y_half
                    j_dynamic[nidx] += y_half * _phase_voltage(project, key.phase)
        branches.append(_Branch(
            f"SEC:{minor.section_id}",
            "N_CABLE_SECTION",
            tuple(start_nodes[label] for label in unknown_labels),
            tuple(end_nodes[label] for label in unknown_labels),
            z,
            e,
            unknown_labels,
            minor.section_id,
            metal_r,
            core_loss,
        ))
        section_branch_index[minor.section_id] = len(branches) - 1
        endpoints[minor.section_id] = (start_nodes, end_nodes)

    node_by_id = {item.node_id: item for item in project.bonding.nodes}

    def add_scalar(branch_id: str, kind: str, a: int, b: int | None, z: complex, label: str) -> None:
        branches.append(_Branch(
            branch_id,
            kind,
            (a,),
            (b,),
            np.asarray([[z]], dtype=complex),
            np.asarray([0j], dtype=complex),
            (label,),
            "",
            (max(float(z.real), 0.0),),
            0.0,
        ))

    for prev, nxt in zip(minors, minors[1:]):
        joint_id = prev.end_node_id
        if joint_id != nxt.start_node_id:
            raise MulticonductorBondingInputError(f"Minor section zinciri kopuk: {prev.section_id}→{nxt.section_id}")
        prev_end = endpoints[prev.section_id][1]
        next_start = endpoints[nxt.section_id][0]
        joint = node_by_id.get(joint_id)
        grounded_boundary = bool(joint and joint.grounded)
        if project.bonding.scheme == BONDING_CROSS and grounded_boundary:
            bus = node(f"BUS:{joint_id}")
            ensure_vectors()
            z_bond = complex(max(float(project.bonding.ground_bus_contact_resistance_mohm) / 1000.0, 1e-7), 0.0)
            for side_name, side in (("L", prev_end), ("R", next_start)):
                for key in canonical_keys:
                    add_scalar(f"BOND:{joint_id}:{side_name}:{key.label}", "SOLID_BOND", side[key.label], bus, z_bond, key.label)
                if has_gcc and bool(project.bonding.gcc_ground_at_major_boundaries):
                    add_scalar(f"BOND:{joint_id}:{side_name}:GCC", "GCC_BOND", side["GCC"], bus, z_bond, "GCC")
            earth_r = max(float(joint.earth_resistance_ohm), 1e-4)
            add_scalar(f"EARTH:{joint_id}", "EARTH", bus, None, complex(earth_r, 0.0), "EARTH")
        else:
            mapping = _cross_mapping(project.bonding, joint_id) if project.bonding.scheme == BONDING_CROSS else {p: p for p in "ABC"}
            z_link = _actual_link_impedance(project.cable, project.bonding, joint_id)
            key_set = set(canonical_keys)
            for key in canonical_keys:
                target = _SheathKey(key.circuit_id, mapping[key.phase], key.parallel_index)
                if target not in key_set:
                    raise MulticonductorBondingInputError(
                        f"{joint_id}: {key.label} için hedef kılıf bulunamadı: {target.label}."
                    )
                kind = "CROSS_LINK" if key.phase != target.phase else "STRAIGHT_LINK"
                add_scalar(
                    f"LINK:{joint_id}:{key.label}>{target.label}",
                    kind,
                    prev_end[key.label],
                    next_start[target.label],
                    z_link,
                    f"{key.label}>{target.label}",
                )
            if has_gcc:
                add_scalar(
                    f"GCCLINK:{joint_id}", "GCC_LINK", prev_end["GCC"], next_start["GCC"],
                    _actual_link_impedance(project.cable, project.bonding, joint_id, straight_gcc=True), "GCC"
                )
                if bool(project.bonding.gcc_ground_at_link_boxes):
                    bus = node(f"GCCBUS:{joint_id}")
                    ensure_vectors()
                    add_scalar(f"GCCBOND:{joint_id}", "GCC_BOND", next_start["GCC"], bus, complex(1e-5, 0.0), "GCC")
                    earth_r = max(float(joint.earth_resistance_ohm if joint else 0.2), 1e-4)
                    add_scalar(f"GCCEARTH:{joint_id}", "EARTH", bus, None, complex(earth_r, 0.0), "EARTH")

    first = minors[0]
    last = minors[-1]
    for terminal_id, side, side_name in (
        (first.start_node_id, endpoints[first.section_id][0], "START"),
        (last.end_node_id, endpoints[last.section_id][1], "END"),
    ):
        terminal = node_by_id.get(terminal_id)
        should_ground = bool(terminal and terminal.grounded)
        if project.bonding.scheme == BONDING_SINGLE_POINT and side_name == "END":
            should_ground = False
        if should_ground:
            bus = node(f"BUS:{terminal_id}")
            ensure_vectors()
            z_bond = complex(max(float(project.bonding.ground_bus_contact_resistance_mohm) / 1000.0, 1e-7), 0.0)
            for key in canonical_keys:
                add_scalar(f"TBOND:{terminal_id}:{key.label}", "SOLID_BOND", side[key.label], bus, z_bond, key.label)
            if has_gcc:
                add_scalar(f"TBOND:{terminal_id}:GCC", "GCC_BOND", side["GCC"], bus, z_bond, "GCC")
            earth_r = max(float(terminal.earth_resistance_ohm), 1e-4)
            add_scalar(f"EARTH:{terminal_id}", "EARTH", bus, None, complex(earth_r, 0.0), "EARTH")

    ensure_vectors()
    return _NetworkModel(
        node_labels,
        branches,
        np.asarray(g_dynamic, dtype=complex),
        np.asarray(j_dynamic, dtype=complex),
        section_branch_index,
        endpoints,
        matrix_blocks,
        canonical_keys,
        key_to_physical_id,
    )


def _assemble(model: _NetworkModel) -> tuple[csc_matrix, csc_matrix, csc_matrix, np.ndarray, np.ndarray, list[slice]]:
    n_nodes = len(model.node_labels)
    sizes = [len(item.start_nodes) for item in model.branches]
    total = sum(sizes)
    a = lil_matrix((n_nodes, total), dtype=complex)
    z_blocks: list[np.ndarray] = []
    e = np.zeros(total, dtype=complex)
    slices: list[slice] = []
    cursor = 0
    for branch, size in zip(model.branches, sizes):
        sl = slice(cursor, cursor + size)
        slices.append(sl)
        for local in range(size):
            a[branch.start_nodes[local], cursor + local] += 1.0
            end = branch.end_nodes[local]
            if end is not None:
                a[end, cursor + local] -= 1.0
        z_blocks.append(branch.z)
        e[sl] = branch.e
        cursor += size
    return (
        a.tocsc(),
        block_diag(z_blocks, format="csc", dtype=complex),
        diags(model.g_shunt, format="csc", dtype=complex),
        model.j_injection.copy(),
        e,
        slices,
    )


def _condition(matrix: csc_matrix) -> float:
    return float(np.linalg.cond(matrix.toarray())) if matrix.shape[0] <= 420 else float("nan")


def _residual(matrix: csc_matrix, x: np.ndarray, rhs: np.ndarray) -> float:
    return float(np.linalg.norm(matrix @ x - rhs) / max(float(np.linalg.norm(rhs)), 1.0))


def _kcl(a: csc_matrix, g: csc_matrix, v: np.ndarray, i: np.ndarray, j: np.ndarray) -> float:
    return float(np.linalg.norm(g @ v + a @ i - j) / max(float(np.linalg.norm(j)), 1.0))


def _solve_cim(model: _NetworkModel) -> tuple[MulticonductorBondingMethodResult, list[slice]]:
    a, z, g, j, e, slices = _assemble(model)
    matrix = bmat([[g, a], [a.T, -z]], format="csc")
    rhs = np.concatenate([j, e])
    solution = np.asarray(spsolve(matrix, rhs), dtype=complex)
    if not np.all(np.isfinite(solution)):
        raise MulticonductorBondingInputError("N-iletken CIM çözümü sonlu olmayan değer üretti.")
    n = len(model.node_labels)
    v, i = solution[:n], solution[n:]
    return MulticonductorBondingMethodResult(
        "N_CONDUCTOR_CIM",
        tuple(complex(x) for x in v),
        tuple(complex(x) for x in i),
        _condition(matrix),
        _residual(matrix, solution, rhs),
        _kcl(a, g, v, i, j),
    ), slices


def _solve_nv(model: _NetworkModel) -> tuple[MulticonductorBondingMethodResult, list[slice]]:
    a, z, g, j, e, slices = _assemble(model)
    try:
        inverses = [np.linalg.inv(branch.z) for branch in model.branches]
    except np.linalg.LinAlgError as exc:
        raise MulticonductorBondingInputError(f"N-iletken dal empedans bloğu tekil: {exc}") from exc
    yb = block_diag(inverses, format="csc", dtype=complex)
    matrix = (g + a @ yb @ a.T).tocsc()
    rhs = j + a @ (yb @ e)
    v = np.asarray(spsolve(matrix, rhs), dtype=complex)
    if not np.all(np.isfinite(v)):
        raise MulticonductorBondingInputError("N-iletken NV çözümü sonlu olmayan değer üretti.")
    i = np.asarray(yb @ (a.T @ v - e), dtype=complex)
    return MulticonductorBondingMethodResult(
        "N_CONDUCTOR_NODE_VOLTAGE",
        tuple(complex(x) for x in v),
        tuple(complex(x) for x in i),
        _condition(matrix),
        _residual(matrix, v, rhs),
        _kcl(a, g, v, i, j),
    ), slices


def _pairwise_max(values: list[complex]) -> float:
    if len(values) < 2:
        return 0.0
    return max(abs(values[i] - values[j]) for i in range(len(values)) for j in range(i + 1, len(values)))


def solve_multiconductor_bonding_network(
    project: ProjectData,
    *,
    selected_method: str = "N_CONDUCTOR_CIM",
) -> MulticonductorBondingNetworkResult:
    """Solve the whole explicit sheath/link-box graph in shadow mode."""

    before = project.to_dict()
    model = _build_network(project)
    cim, slices = _solve_cim(model)
    nv, _ = _solve_nv(model)
    selected = str(selected_method).strip().upper()
    active = nv if selected in {"N_CONDUCTOR_NODE_VOLTAGE", "NODE_VOLTAGE", "NV"} else cim
    selected_name = active.method
    v = np.asarray(active.node_voltages_v, dtype=complex)
    currents = np.asarray(active.branch_currents_a, dtype=complex)

    dv = max((abs(a - b) for a, b in zip(cim.node_voltages_v, nv.node_voltages_v)), default=0.0)
    di = max((abs(a - b) for a, b in zip(cim.branch_currents_a, nv.branch_currents_a)), default=0.0)
    tol_v = max(1e-5, 1e-7 * max((abs(x) for x in active.node_voltages_v), default=1.0))
    tol_i = max(1e-6, 1e-7 * max((abs(x) for x in active.branch_currents_a), default=1.0))

    positions = _node_positions(project.bonding)
    minor_by_id = {item.section_id: item for item in project.bonding.minor_sections}
    blocks_by_section: dict[str, list[MulticonductorBondingMatrixBlock]] = {}
    for block in model.matrix_blocks:
        blocks_by_section.setdefault(block.section_id, []).append(block)

    section_results: list[MulticonductorBondingSectionResult] = []
    total_core = total_sheath = total_gcc = total_earth = 0.0
    max_ish = max_vg = max_vss = max_gcc = 0.0
    n_sheath = len(model.sheath_keys)
    for section_id, branch_index in model.section_branch_index.items():
        branch = model.branches[branch_index]
        sl = slices[branch_index]
        i_vec = currents[sl]
        start_nodes, end_nodes = model.endpoints[section_id]
        minor = minor_by_id[section_id]
        start_m, end_m = _minor_bounds(minor, positions)
        sheath_rows: list[MulticonductorSheathResult] = []
        start_values: list[complex] = []
        end_values: list[complex] = []
        sheath_loss = 0.0
        for idx, key in enumerate(model.sheath_keys):
            sv = complex(v[start_nodes[key.label]])
            ev = complex(v[end_nodes[key.label]])
            current = complex(i_vec[idx])
            loss = abs(current) ** 2 * branch.metallic_r_diag[idx]
            sheath_loss += loss
            start_values.append(sv)
            end_values.append(ev)
            sheath_rows.append(MulticonductorSheathResult(
                section_id,
                minor.major_index,
                key.circuit_id,
                key.phase,
                key.parallel_index,
                model.key_to_physical_id.get(key.label, key.label),
                current,
                sv,
                ev,
                complex(branch.e[idx]),
                float(loss),
            ))
        gcc_i = complex(i_vec[n_sheath]) if len(i_vec) > n_sheath else 0j
        gcc_sv = complex(v[start_nodes["GCC"]]) if "GCC" in start_nodes else 0j
        gcc_ev = complex(v[end_nodes["GCC"]]) if "GCC" in end_nodes else 0j
        gcc_loss = abs(gcc_i) ** 2 * branch.metallic_r_diag[n_sheath] if len(i_vec) > n_sheath else 0.0
        total_branch_active = float(np.real(np.conjugate(i_vec) @ branch.z @ i_vec))
        earth_loss = max(0.0, total_branch_active - sheath_loss - gcc_loss)
        section_vg = max([abs(x) for x in start_values + end_values] or [0.0])
        section_vss = max(_pairwise_max(start_values), _pairwise_max(end_values))
        section_ish = max([abs(x.sheath_current_a) for x in sheath_rows] or [0.0])
        total_core += branch.core_loss_w
        total_sheath += sheath_loss
        total_gcc += gcc_loss
        total_earth += earth_loss
        max_ish = max(max_ish, section_ish)
        max_vg = max(max_vg, section_vg)
        max_vss = max(max_vss, section_vss)
        max_gcc = max(max_gcc, abs(gcc_i))
        section_results.append(MulticonductorBondingSectionResult(
            section_id,
            minor.name,
            minor.major_index,
            start_m,
            end_m,
            tuple(dict.fromkeys(block.cross_section_id for block in blocks_by_section.get(section_id, []))),
            tuple(sheath_rows),
            gcc_i,
            gcc_sv,
            gcc_ev,
            float(sheath_loss),
            float(gcc_loss),
            float(earth_loss),
            float(branch.core_loss_w),
            float(section_ish),
            float(section_vg),
            float(section_vss),
        ))

    accessory: list[MulticonductorBondingBranchResult] = []
    accessory_loss = 0.0
    for idx, branch in enumerate(model.branches):
        if branch.branch_type == "N_CABLE_SECTION":
            continue
        sl = slices[idx]
        for local, value in enumerate(currents[sl]):
            z = complex(branch.z[local, local])
            loss = abs(value) ** 2 * max(z.real, 0.0)
            accessory_loss += loss
            start_label = model.node_labels[branch.start_nodes[local]]
            end_node = branch.end_nodes[local]
            end_label = "GROUND" if end_node is None else model.node_labels[end_node]
            accessory.append(MulticonductorBondingBranchResult(
                branch.branch_id if len(branch.start_nodes) == 1 else f"{branch.branch_id}:{local}",
                branch.branch_type,
                start_label,
                end_label,
                complex(value),
                z,
                float(loss),
            ))

    if project.to_dict() != before:
        raise RuntimeError("SHADOW_COMPARE çözümü proje nesnesini değiştirdi; işlem iptal edildi.")

    issues = [
        MulticonductorBondingIssue(
            "INFO", "SHADOW_ONLY",
            "N-iletken bonding ağı proje sonucunu, λ1 girdisini veya IEC/termal motorları değiştirmez."
        ),
        MulticonductorBondingIssue(
            "WARNING", "SECTION_LOCAL_CORE_SHARING",
            "Paralel core akımları her fiziksel kesitte OPEN_SHEATH yerel çözümle belirlenir. "
            "Core sürekliliği ve kılıf ağı tek global sistemde henüz birlikte çözülmez."
        ),
        MulticonductorBondingIssue(
            "INFO", "DERIVED_MULTI_CIRCUIT_CROSS_MAPPING",
            "Mevcut A→B→C link-box permütasyonu her devre ve paralel indeks için ayrı uygulanır; "
            "explicit kablo-terminal bağlantı editörü sonraki veri-modeli kapısıdır."
        ),
    ]
    if not bool(getattr(project.bonding, "include_dielectric_charging", True)):
        issues.append(MulticonductorBondingIssue(
            "INFO", "DIELECTRIC_CHARGING_DISABLED", "Core-kılıf kapasitif pi şöntleri kullanıcı ayarıyla devre dışı."
        ))

    trace = (
        "sheath_order=" + ",".join(key.label for key in model.sheath_keys),
        f"bonding_scheme={project.bonding.scheme}",
        f"minor_sections={len(section_results)}",
        f"matrix_blocks={len(model.matrix_blocks)}",
        f"gcc_enabled={project.bonding.gcc_enabled}",
    )
    lambda1 = total_sheath / total_core if total_core > 1e-15 else 0.0
    return MulticonductorBondingNetworkResult(
        ENGINE_MODE,
        CORE_SHARING_MODE,
        REFERENCE,
        tuple(key.label for key in model.sheath_keys),
        tuple(model.node_labels),
        tuple(model.matrix_blocks),
        tuple(section_results),
        tuple(accessory),
        cim,
        nv,
        selected_name,
        dv <= tol_v and di <= tol_i,
        float(dv),
        float(di),
        float(max_ish),
        float(max_vg),
        float(max_vss),
        float(max_gcc),
        float(total_core),
        float(total_sheath),
        float(total_gcc),
        float(total_earth),
        float(accessory_loss),
        float(lambda1),
        len(model.node_labels),
        len(active.branch_currents_a),
        tuple(issues),
        trace,
    )


def render_multiconductor_bonding_network(result: MulticonductorBondingNetworkResult) -> str:
    lines = result.trace_lines()
    lines.extend(["", "Minor section / fiziksel kılıf sonuçları:"])
    for section in result.section_results:
        for item in section.sheath_results:
            lines.append(
                f"{item.section_id} {item.key}: |Ish|={abs(item.sheath_current_a):.6f}∠{_angle(item.sheath_current_a):.3f}° A; "
                f"Vstart={abs(item.start_voltage_to_earth_v):.6f}∠{_angle(item.start_voltage_to_earth_v):.3f}° V; "
                f"Vend={abs(item.end_voltage_to_earth_v):.6f}∠{_angle(item.end_voltage_to_earth_v):.3f}° V; "
                f"Eopen={abs(item.integrated_open_emf_v):.6f}∠{_angle(item.integrated_open_emf_v):.3f}° V; "
                f"Psh={item.sheath_metal_loss_w:.6f} W"
            )
    lines.extend(["", "Link-box / bonding / earth dalları:"])
    for item in result.accessory_branches:
        lines.append(
            f"{item.branch_id} [{item.branch_type}] {item.from_label} → {item.to_label}: "
            f"I={abs(item.current_a):.6f}∠{_angle(item.current_a):.3f}° A; "
            f"Z={item.impedance_ohm.real:.8f}+j{item.impedance_ohm.imag:.8f} Ω; P={item.active_loss_w:.6f} W"
        )
    return "\n".join(lines)
