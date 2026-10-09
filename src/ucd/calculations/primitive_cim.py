"""Primitive power-frequency sheath-bonding network solver.

The model is deliberately explicit:

* three phase conductors are known-current / known-voltage drivers,
* three metallic sheaths and an optional GCC/ECC are unknown longitudinal
  conductors,
* every minor section is a multi-conductor branch with a primitive complex
  impedance block,
* link-box cross connections, solid bonds and earth electrodes are explicit
  branches,
* dielectric core-to-sheath admittance is represented by a distributed pi
  approximation,
* the same physical network is solved by an augmented Complex Impedance
  Matrix (CIM/MNA) formulation and by a Node-Voltage (NV) elimination.

The earth-return terms use the classic simplified-Carson equivalent-depth
approximation.  This is a working power-frequency engineering model and a
large step beyond the v0.7 loop-equivalent matrix, but it is not yet the full
Pollaczek/Wedepohl-Wilcox wideband implementation or an EMT model.
"""

from __future__ import annotations

from ucd.calculations.phase_geometry import PhaseGeometryError, normalize_arrangement, normalize_phase_order, phase_slot_offsets_m
from ucd.calculations.cable_physical_parameters import (
    PhysicalParameterInputError,
    material_resistivity_20_ohm_m,
)

import cmath
from dataclasses import dataclass
from math import log, pi, sqrt
from typing import Iterable, Sequence

import numpy as np
from scipy.sparse import bmat, block_diag, csc_matrix, diags, lil_matrix
from scipy.sparse.linalg import spsolve

from ucd.calculations.iec60287 import ac_resistance_at_temperature_ohm_km
from ucd.models.project import (
    BONDING_CROSS,
    BONDING_SINGLE_POINT,
    BONDING_SOLID_BOTH_END,
    BondingLinkBox,
    BondingMinorSection,
    BondingNode,
    BondingSystemData,
    CableData,
    RouteSection,
)


REFERENCE = (
    "CIGRE TB 797 primitive-conductor / complex-network design architecture; "
    "IEEE 575 Annex D/F cross-check; simplified Carson earth return at 50/60 Hz"
)


class PrimitiveNetworkError(ValueError):
    pass


@dataclass(frozen=True)
class PrimitiveConductor:
    name: str
    kind: str  # CORE / SHEATH / GCC
    phase: str
    x_m: float
    depth_m: float
    gmr_m: float
    resistance_ohm_km: float


@dataclass(frozen=True)
class PrimitiveMatrixBlock:
    section_id: str
    route_name: str
    start_m: float
    end_m: float
    length_m: float
    conductor_names: tuple[str, ...]
    full_impedance_ohm_km: tuple[tuple[complex, ...], ...]
    unknown_impedance_ohm: tuple[tuple[complex, ...], ...]
    induced_source_v: tuple[complex, ...]
    earth_equivalent_depth_m: float


@dataclass(frozen=True)
class PrimitiveSectionResult:
    section_id: str
    major_index: int
    start_m: float
    end_m: float
    sheath_currents_a: tuple[complex, complex, complex]
    gcc_current_a: complex
    start_sheath_voltages_v: tuple[complex, complex, complex]
    end_sheath_voltages_v: tuple[complex, complex, complex]
    sheath_metal_loss_w: float
    gcc_metal_loss_w: float
    earth_return_loss_w: float

    @property
    def max_sheath_current_a(self) -> float:
        return max(abs(value) for value in self.sheath_currents_a)

    @property
    def max_sheath_voltage_v(self) -> float:
        return max(
            *(abs(value) for value in self.start_sheath_voltages_v),
            *(abs(value) for value in self.end_sheath_voltages_v),
        )


@dataclass(frozen=True)
class PrimitiveBranchResult:
    branch_id: str
    branch_type: str
    from_label: str
    to_label: str
    current_a: complex
    impedance_ohm: complex
    active_loss_w: float


@dataclass(frozen=True)
class PrimitiveMethodResult:
    method: str
    node_voltages_v: tuple[complex, ...]
    branch_currents_a: tuple[complex, ...]
    matrix_condition_number: float
    equation_residual: float
    kcl_residual: float


@dataclass(frozen=True)
class PrimitiveNetworkResult:
    selected_method: str
    reference: str
    conductor_order: tuple[str, ...]
    node_labels: tuple[str, ...]
    matrix_blocks: tuple[PrimitiveMatrixBlock, ...]
    section_results: tuple[PrimitiveSectionResult, ...]
    accessory_branches: tuple[PrimitiveBranchResult, ...]
    cim: PrimitiveMethodResult
    nv: PrimitiveMethodResult
    methods_agree: bool
    maximum_method_voltage_difference_v: float
    maximum_method_current_difference_a: float
    maximum_sheath_current_a: float
    maximum_sheath_voltage_v: float
    maximum_gcc_current_a: float
    total_sheath_metal_loss_w: float
    total_gcc_metal_loss_w: float
    total_earth_return_loss_w: float
    total_accessory_loss_w: float
    node_count: int
    branch_current_count: int
    notes: tuple[str, ...]
    trace: tuple[str, ...]

    def trace_lines(self) -> list[str]:
        lines = [
            f"Primitive ağ referansı: {self.reference}",
            f"Seçili çözüm: {self.selected_method}",
            f"Düğüm sayısı: {self.node_count}",
            f"Dal akımı bilinmeyeni: {self.branch_current_count}",
            f"CIM cond={self.cim.matrix_condition_number:.6g}; residual={self.cim.equation_residual:.3e}",
            f"NV cond={self.nv.matrix_condition_number:.6g}; residual={self.nv.equation_residual:.3e}",
            f"CIM↔NV maks. ΔV={self.maximum_method_voltage_difference_v:.6e} V; "
            f"maks. ΔI={self.maximum_method_current_difference_a:.6e} A",
            f"Maks. metalik kılıf akımı={self.maximum_sheath_current_a:.6f} A",
            f"Maks. metalik kılıf-toprak gerilimi={self.maximum_sheath_voltage_v:.6f} V",
            f"Maks. GCC/ECC akımı={self.maximum_gcc_current_a:.6f} A",
            f"Metalik kılıf kaybı={self.total_sheath_metal_loss_w:.6f} W",
            f"GCC/ECC metal kaybı={self.total_gcc_metal_loss_w:.6f} W",
            f"Toprak dönüş eşdeğer kaybı={self.total_earth_return_loss_w:.6f} W",
            f"Aksesuar/ground branch kaybı={self.total_accessory_loss_w:.6f} W",
        ]
        for section in self.section_results:
            ish = "/".join(f"{abs(v):.4f}∠{_angle(v):.1f}°" for v in section.sheath_currents_a)
            lines.append(
                f"{section.section_id} M{section.major_index} {section.start_m:.2f}-{section.end_m:.2f} m: "
                f"Ish(A/B/C)={ish} A; Igcc={abs(section.gcc_current_a):.4f} A; "
                f"Vmax={section.max_sheath_voltage_v:.3f} V; "
                f"Psh={section.sheath_metal_loss_w:.4f} W"
            )
        lines.extend(f"Not: {note}" for note in self.notes)
        return lines


@dataclass
class _BranchBlock:
    branch_id: str
    branch_type: str
    start_nodes: tuple[int, ...]
    end_nodes: tuple[int | None, ...]
    z: np.ndarray
    e: np.ndarray
    conductor_labels: tuple[str, ...]
    section_id: str = ""
    metallic_r_diag: tuple[float, ...] = ()


@dataclass
class _NetworkModel:
    node_labels: list[str]
    branches: list[_BranchBlock]
    g_shunt: np.ndarray
    j_injection: np.ndarray
    section_branch_index: dict[str, int]
    section_endpoint_nodes: dict[str, tuple[dict[str, int], dict[str, int]]]
    matrix_blocks: list[PrimitiveMatrixBlock]
    unknown_labels: tuple[str, ...]


# ---------------------------------------------------------------------------
# Electrical primitives
# ---------------------------------------------------------------------------


def _angle(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


def _positive(name: str, value: float, allow_zero: bool = False) -> float:
    if (allow_zero and value < 0) or (not allow_zero and value <= 0):
        raise PrimitiveNetworkError(f"{name} {'negatif olamaz' if allow_zero else 'sıfırdan büyük olmalı'}: {value}")
    return float(value)


def _material_resistivity_20(material: str) -> float:
    try:
        return material_resistivity_20_ohm_m(material)
    except PhysicalParameterInputError as exc:
        raise PrimitiveNetworkError(str(exc)) from exc


def _resistance_from_area_ohm_km(material: str, area_mm2: float, temperature_c: float, alpha: float) -> float:
    area = _positive("İletken kesiti", area_mm2)
    r20 = _material_resistivity_20(material) * 1e9 / area
    # Automatic material alpha is synchronized at ProjectData/calculation-policy
    # level. Preserve the scalar here to retain explicit field-level overrides.
    effective_alpha = _positive("Sıcaklık katsayısı", alpha, allow_zero=True)
    return r20 * (1.0 + effective_alpha * (temperature_c - 20.0))


def _phase_currents(current_a: float) -> dict[str, complex]:
    current = _positive("Faz akımı", current_a)
    return {
        "A": cmath.rect(current, 0.0),
        "B": cmath.rect(current, -2.0 * pi / 3.0),
        "C": cmath.rect(current, 2.0 * pi / 3.0),
    }


def _phase_voltages(voltage_ll_kv: float) -> dict[str, complex]:
    magnitude = _positive("Hatlar arası gerilim", voltage_ll_kv) * 1000.0 / sqrt(3.0)
    return {
        "A": cmath.rect(magnitude, 0.0),
        "B": cmath.rect(magnitude, -2.0 * pi / 3.0),
        "C": cmath.rect(magnitude, 2.0 * pi / 3.0),
    }


def _phase_positions(arrangement: str, spacing_m: float, phase_order: str, burial_depth_m: float) -> dict[str, tuple[float, float]]:
    depth = _positive("Gömülme derinliği", burial_depth_m)
    try:
        normalized = normalize_arrangement(arrangement)
        if normalized == "SINGLE":
            raise PhaseGeometryError(
                "BONDING_SINGLE_REQUIRES_RETURN_PATH_GEOMETRY: SINGLE primitive ağ için "
                "gidiş/dönüş yolu veya üç fazlı açık koordinat geometrisi gereklidir."
            )
        slots = phase_slot_offsets_m(normalized, spacing_m, phase_order)
    except PhaseGeometryError as exc:
        raise PrimitiveNetworkError(str(exc)) from exc
    return {phase: (x, depth + y) for phase, (x, y) in slots.items()}


def _core_gmr_m(cable: CableData) -> float:
    explicit = float(getattr(cable, "conductor_gmr_mm", 0.0))
    if explicit > 0:
        return explicit / 1000.0
    radius = sqrt(_positive("İletken kesiti", cable.conductor_area_mm2) * 1e-6 / pi)
    return 0.7788 * radius


def _sheath_gmr_m(cable: CableData) -> float:
    explicit = float(getattr(cable, "sheath_gmr_mm", 0.0))
    if explicit > 0:
        return explicit / 1000.0
    return _positive("Metalik kılıf ortalama yarıçapı", cable.sheath_mean_diameter_mm / 2000.0)


def _gcc_resistance_ohm_km(bonding: BondingSystemData) -> float:
    entered = float(getattr(bonding, "gcc_dc_resistance_20_ohm_km", 0.0))
    alpha = float(getattr(bonding, "gcc_temperature_coefficient_20_per_c", 0.00393))
    temperature = float(getattr(bonding, "gcc_operating_temperature_c", 60.0))
    if entered > 0:
        return entered * (1.0 + alpha * (temperature - 20.0))
    return _resistance_from_area_ohm_km(
        getattr(bonding, "gcc_material", "Cu"),
        getattr(bonding, "gcc_area_mm2", 240.0),
        temperature,
        alpha,
    )


def _sheath_resistance_ohm_km(cable: CableData) -> float:
    entered = float(cable.sheath_dc_resistance_20_ohm_km)
    alpha = float(cable.sheath_temperature_coefficient_20_per_c)
    temperature = float(cable.sheath_operating_temperature_c)
    if entered > 0:
        return entered * (1.0 + alpha * (temperature - 20.0))
    return _resistance_from_area_ohm_km(
        cable.sheath_material, cable.sheath_cross_section_mm2, temperature, alpha
    )


def _primitive_conductors(
    cable: CableData,
    bonding: BondingSystemData,
    route: RouteSection,
    phase_order: str,
) -> tuple[PrimitiveConductor, ...]:
    raw_positions = getattr(route, "phase_positions_m", None)
    if isinstance(raw_positions, dict) and all(phase in raw_positions for phase in "ABC"):
        try:
            order = normalize_phase_order(phase_order)
            base = []
            for slot_phase in "ABC":
                value = raw_positions[slot_phase]
                if not isinstance(value, (list, tuple)) or len(value) < 2:
                    raise ValueError(f"{slot_phase} koordinatı geçersiz")
                base.append((float(value[0]), float(value[1])))
            positions = {order[index]: base[index] for index in range(3)}
        except (TypeError, ValueError, PhaseGeometryError) as exc:
            raise PrimitiveNetworkError(f"Açık faz koordinatları geçersiz: {exc}") from exc
    else:
        positions = _phase_positions(
            getattr(route, "resolved_arrangement", "") or cable.arrangement,
            route.phase_spacing_m if route.phase_spacing_m > 0 else bonding.phase_spacing_m,
            phase_order,
            route.burial_depth_m,
        )
    spacing = route.phase_spacing_m if route.phase_spacing_m > 0 else bonding.phase_spacing_m
    _rdc, core_r = ac_resistance_at_temperature_ohm_km(cable, cable.max_temperature_c, spacing)
    sheath_r = _sheath_resistance_ohm_km(cable)
    core_gmr = _core_gmr_m(cable)
    sheath_gmr = _sheath_gmr_m(cable)
    conductors: list[PrimitiveConductor] = []
    for phase in "ABC":
        x, depth = positions[phase]
        conductors.append(PrimitiveConductor(f"C{phase}", "CORE", phase, x, depth, core_gmr, core_r))
    for phase in "ABC":
        x, depth = positions[phase]
        conductors.append(PrimitiveConductor(f"S{phase}", "SHEATH", phase, x, depth, sheath_gmr, sheath_r))
    if bool(getattr(bonding, "gcc_enabled", False)):
        x = float(getattr(bonding, "gcc_x_offset_m", 0.0))
        depth = route.burial_depth_m + float(getattr(bonding, "gcc_depth_offset_m", 0.30))
        gmr = float(getattr(bonding, "gcc_gmr_mm", 0.0)) / 1000.0
        if gmr <= 0:
            radius = sqrt(_positive("GCC kesiti", getattr(bonding, "gcc_area_mm2", 240.0)) * 1e-6 / pi)
            gmr = 0.7788 * radius
        conductors.append(
            PrimitiveConductor("GCC", "GCC", "G", x, max(depth, 0.05), gmr, _gcc_resistance_ohm_km(bonding))
        )
    return tuple(conductors)


def primitive_impedance_matrix_ohm_km(
    conductors: Sequence[PrimitiveConductor],
    frequency_hz: float,
    earth_resistivity_ohm_m: float,
    sheath_mean_radius_m: float,
) -> tuple[np.ndarray, float]:
    """Build a symmetric primitive series-impedance matrix.

    Simplified Carson equivalent-depth form (natural logarithms):

      Zii = Ri + Re + j Xc ln(De/GMRi)
      Zij =      Re + j Xc ln(De/Dij)

    where Re = pi² f 1e-4 ohm/km, Xc = 4 pi f 1e-4 ohm/km,
    and De = 658.37 sqrt(rho/f) m.
    """

    f = _positive("Frekans", frequency_hz)
    rho = _positive("Toprak özdirenci", earth_resistivity_ohm_m)
    de = 658.37 * sqrt(rho / f)
    re = pi * pi * f * 1e-4
    xc = 4.0 * pi * f * 1e-4
    n = len(conductors)
    z = np.zeros((n, n), dtype=complex)
    for i, ci in enumerate(conductors):
        if de <= ci.gmr_m:
            raise PrimitiveNetworkError("Carson eşdeğer derinliği conductor GMR'den büyük olmalı.")
        z[i, i] = complex(ci.resistance_ohm_km + re, xc * log(de / ci.gmr_m))
        for j in range(i):
            cj = conductors[j]
            dx = ci.x_m - cj.x_m
            dy = ci.depth_m - cj.depth_m
            distance = sqrt(dx * dx + dy * dy)
            if distance < 1e-9 and {ci.kind, cj.kind} == {"CORE", "SHEATH"} and ci.phase == cj.phase:
                distance = sheath_mean_radius_m
            if distance <= 0:
                raise PrimitiveNetworkError(f"Primitive conductor mesafesi sıfır: {ci.name}-{cj.name}")
            value = complex(re, xc * log(de / distance))
            z[i, j] = value
            z[j, i] = value
    return z, de


# ---------------------------------------------------------------------------
# Route integration and network construction
# ---------------------------------------------------------------------------


def _route_ranges(routes: Iterable[RouteSection]) -> list[tuple[float, float, RouteSection]]:
    result: list[tuple[float, float, RouteSection]] = []
    cursor = 0.0
    for route in routes:
        length = _positive(f"{route.name} uzunluğu", route.length_m)
        result.append((cursor, cursor + length, route))
        cursor += length
    if not result:
        raise PrimitiveNetworkError("Primitive ağ için güzergâh bölümü gerekli.")
    return result


def _node_positions(bonding: BondingSystemData) -> dict[str, float]:
    positions = {node.node_id: float(node.position_m) for node in bonding.nodes}
    if len(positions) != len(bonding.nodes):
        raise PrimitiveNetworkError("Bonding düğüm kimlikleri benzersiz olmalı.")
    return positions


def _minor_bounds(minor: BondingMinorSection, positions: dict[str, float]) -> tuple[float, float]:
    if minor.start_node_id not in positions or minor.end_node_id not in positions:
        raise PrimitiveNetworkError(f"{minor.section_id} düğüm referansı bulunamadı.")
    start = positions[minor.start_node_id]
    end = positions[minor.end_node_id]
    if end <= start:
        raise PrimitiveNetworkError(f"{minor.section_id} başlangıç/bitiş konumu geçersiz.")
    return start, end


def _integrate_minor_block(
    cable: CableData,
    bonding: BondingSystemData,
    routes: list[RouteSection],
    minor: BondingMinorSection,
    start_m: float,
    end_m: float,
    phase_currents_a: dict[str, complex] | None = None,
    include_dielectric_charging: bool | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[PrimitiveMatrixBlock], tuple[str, ...], tuple[float, ...]]:
    route_ranges = _route_ranges(routes)
    core_currents_map = phase_currents_a or _phase_currents(cable.design_current_a)
    if set(core_currents_map) != set("ABC"):
        raise PrimitiveNetworkError("Faz akım haritası A, B ve C fazlarını içermeli.")
    core_currents = np.array([core_currents_map[p] for p in "ABC"], dtype=complex)
    unknown_names = ("SA", "SB", "SC", "GCC") if bool(getattr(bonding, "gcc_enabled", False)) else ("SA", "SB", "SC")
    n_unknown = len(unknown_names)
    z_total = np.zeros((n_unknown, n_unknown), dtype=complex)
    e_total = np.zeros(n_unknown, dtype=complex)
    y_total = np.zeros(3, dtype=complex)
    blocks: list[PrimitiveMatrixBlock] = []
    metal_r_length = np.zeros(n_unknown, dtype=float)
    for route_start, route_end, route in route_ranges:
        overlap_start = max(start_m, route_start)
        overlap_end = min(end_m, route_end)
        if overlap_end <= overlap_start:
            continue
        length_m = overlap_end - overlap_start
        conductors = _primitive_conductors(cable, bonding, route, minor.phase_order)
        full_z, de = primitive_impedance_matrix_ohm_km(
            conductors,
            cable.frequency_hz,
            getattr(bonding, "earth_resistivity_ohm_m", 100.0),
            cable.sheath_mean_diameter_mm / 2000.0,
        )
        unknown_indices = [3, 4, 5] + ([6] if len(conductors) == 7 else [])
        z_uu = full_z[np.ix_(unknown_indices, unknown_indices)] * (length_m / 1000.0)
        z_uc = full_z[np.ix_(unknown_indices, [0, 1, 2])] * (length_m / 1000.0)
        e = z_uc @ core_currents
        z_total += z_uu
        e_total += e
        for local, idx in enumerate(unknown_indices):
            metal_r_length[local] += conductors[idx].resistance_ohm_km * length_m / 1000.0
        charging_enabled = (
            bool(getattr(bonding, "include_dielectric_charging", True))
            if include_dielectric_charging is None else bool(include_dielectric_charging)
        )
        if charging_enabled:
            c_f_per_km = _positive("Kapasitans", cable.capacitance_uf_km, allow_zero=True) * 1e-6
            omega = 2.0 * pi * cable.frequency_hz
            y_per_km = complex(omega * c_f_per_km * cable.dielectric_loss_tan_delta, omega * c_f_per_km)
            y_total += y_per_km * (length_m / 1000.0)
        blocks.append(
            PrimitiveMatrixBlock(
                minor.section_id,
                route.name,
                overlap_start,
                overlap_end,
                length_m,
                tuple(c.name for c in conductors),
                tuple(tuple(complex(v) for v in row) for row in full_z),
                tuple(tuple(complex(v) for v in row) for row in z_uu),
                tuple(complex(v) for v in e),
                de,
            )
        )
    if not blocks:
        raise PrimitiveNetworkError(f"{minor.section_id} güzergâhla kesişmiyor.")
    return z_total, e_total, y_total, blocks, unknown_names, tuple(float(v) for v in metal_r_length)


def _link_box_by_joint(bonding: BondingSystemData) -> dict[str, BondingLinkBox]:
    return {box.joint_node_id: box for box in bonding.link_boxes}


def _cross_mapping(bonding: BondingSystemData, joint_id: str) -> dict[str, str]:
    box = _link_box_by_joint(bonding).get(joint_id)
    result: dict[str, str] = {}
    for connection in bonding.connections:
        if connection.connection_type.upper() != "CROSS":
            continue
        same = connection.node_id == joint_id or (box is not None and connection.link_box_id == box.link_box_id)
        if same:
            result[connection.from_sheath.upper()] = connection.to_sheath.upper()
    if not result:
        return {p: p for p in "ABC"}
    if set(result) != set("ABC") or set(result.values()) != set("ABC"):
        raise PrimitiveNetworkError(f"{joint_id} cross mapping tam permütasyon değil: {result}")
    return result


def _actual_link_impedance(cable: CableData, bonding: BondingSystemData, joint_id: str, straight_gcc: bool = False) -> complex:
    box = _link_box_by_joint(bonding).get(joint_id)
    length = 0.0 if box is None else max(0.0, float(box.lead_length_m))
    contact = max(0.0, float(bonding.link_box_contact_resistance_mohm))
    fixed = max(0.0, float(bonding.bonding_lead_resistance_mohm))
    per_m = max(0.0, float(bonding.bonding_lead_resistance_mohm_per_m))
    r = (2.0 * contact + fixed + per_m * length) / 1000.0
    if straight_gcc:
        r *= 0.25
    base_l = max(0.0, float(getattr(bonding, "bonding_lead_inductance_uh_per_m", 0.35)))
    contact_l = max(0.0, float(getattr(bonding, "link_box_contact_inductance_uh", 0.10)))
    lead_type = "COAXIAL" if box is None else box.lead_type.strip().upper()
    multiplier = {"COAXIAL": 0.35, "TWISTED_PAIR": 0.65, "SINGLE": 1.0}.get(lead_type, 1.0)
    inductance_h = (contact_l + base_l * length * multiplier) * 1e-6
    minimum = max(1e-9, float(getattr(bonding, "minimum_branch_impedance_ohm", 1e-6)))
    return complex(max(r, minimum), 2.0 * pi * cable.frequency_hz * inductance_h)


def _build_network(
    cable: CableData,
    bonding: BondingSystemData,
    routes: list[RouteSection],
    phase_currents_a: dict[str, complex] | None = None,
    phase_voltages_v: dict[str, complex] | None = None,
    include_dielectric_charging: bool | None = None,
) -> _NetworkModel:
    positions = _node_positions(bonding)
    minors = sorted(bonding.minor_sections, key=lambda m: _minor_bounds(m, positions)[0])
    if not minors:
        raise PrimitiveNetworkError("Primitive ağ için minor section gerekli.")
    if bonding.scheme == BONDING_CROSS and len(minors) % 3 != 0:
        raise PrimitiveNetworkError("Sectionalized cross-bonding için minor section sayısı üçün katı olmalı.")

    node_labels: list[str] = []
    node_index: dict[str, int] = {}

    def node(label: str) -> int:
        if label not in node_index:
            node_index[label] = len(node_labels)
            node_labels.append(label)
        return node_index[label]

    branches: list[_BranchBlock] = []
    section_branch_index: dict[str, int] = {}
    endpoints: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
    matrix_blocks: list[PrimitiveMatrixBlock] = []
    unknown_labels = ("A", "B", "C", "GCC") if bool(getattr(bonding, "gcc_enabled", False)) else ("A", "B", "C")
    g_diag_dynamic: list[complex] = []
    j_dynamic: list[complex] = []

    def ensure_vectors() -> None:
        while len(g_diag_dynamic) < len(node_labels):
            g_diag_dynamic.append(0j)
            j_dynamic.append(0j)

    phase_v = phase_voltages_v or _phase_voltages(cable.voltage_kv)
    if set(phase_v) != set("ABC"):
        raise PrimitiveNetworkError("Faz gerilim haritası A, B ve C fazlarını içermeli.")
    charging_enabled = (
        bool(getattr(bonding, "include_dielectric_charging", True))
        if include_dielectric_charging is None else bool(include_dielectric_charging)
    )

    # Multi-conductor cable branches.
    for minor in minors:
        start_m, end_m = _minor_bounds(minor, positions)
        z, e, y_phase, blocks, names, metal_r = _integrate_minor_block(
            cable, bonding, routes, minor, start_m, end_m,
            phase_currents_a=phase_currents_a,
            include_dielectric_charging=charging_enabled,
        )
        matrix_blocks.extend(blocks)
        start_nodes = {label: node(f"{minor.section_id}:S:{label}") for label in unknown_labels}
        end_nodes = {label: node(f"{minor.section_id}:E:{label}") for label in unknown_labels}
        ensure_vectors()
        if charging_enabled:
            for phase_idx, phase in enumerate("ABC"):
                y_half = y_phase[phase_idx] / 2.0
                for nidx in (start_nodes[phase], end_nodes[phase]):
                    g_diag_dynamic[nidx] += y_half
                    j_dynamic[nidx] += y_half * phase_v[phase]
        branch = _BranchBlock(
            f"SEC:{minor.section_id}",
            "CABLE_SECTION",
            tuple(start_nodes[label] for label in unknown_labels),
            tuple(end_nodes[label] for label in unknown_labels),
            z,
            e,
            tuple(f"S{label}" if label in "ABC" else label for label in unknown_labels),
            minor.section_id,
            metal_r,
        )
        section_branch_index[minor.section_id] = len(branches)
        branches.append(branch)
        endpoints[minor.section_id] = (start_nodes, end_nodes)

    node_by_id = {n.node_id: n for n in bonding.nodes}

    def add_scalar_branch(branch_id: str, kind: str, a: int, b: int | None, z: complex, label: str) -> None:
        branches.append(
            _BranchBlock(
                branch_id, kind, (a,), (b,), np.array([[z]], dtype=complex),
                np.array([0j], dtype=complex), (label,), "", (max(z.real, 0.0),)
            )
        )

    # Internal connections between successive minor sections.
    for prev, nxt in zip(minors, minors[1:]):
        joint_id = prev.end_node_id
        if joint_id != nxt.start_node_id:
            raise PrimitiveNetworkError(f"Minor section zinciri kopuk: {prev.section_id}→{nxt.section_id}")
        prev_end = endpoints[prev.section_id][1]
        next_start = endpoints[nxt.section_id][0]
        joint = node_by_id.get(joint_id)
        grounded_boundary = bool(joint and joint.grounded)
        if bonding.scheme == BONDING_CROSS and grounded_boundary:
            bus = node(f"BUS:{joint_id}")
            ensure_vectors()
            z_bond = complex(max(float(getattr(bonding, "ground_bus_contact_resistance_mohm", 0.20)) / 1000.0, 1e-7), 0.0)
            for side_name, side in (("L", prev_end), ("R", next_start)):
                for phase in "ABC":
                    add_scalar_branch(f"BOND:{joint_id}:{side_name}:{phase}", "SOLID_BOND", side[phase], bus, z_bond, phase)
                if "GCC" in side and bool(getattr(bonding, "gcc_ground_at_major_boundaries", True)):
                    add_scalar_branch(f"BOND:{joint_id}:{side_name}:GCC", "GCC_BOND", side["GCC"], bus, z_bond, "GCC")
            earth_r = max(float(joint.earth_resistance_ohm), 1e-4)
            add_scalar_branch(f"EARTH:{joint_id}", "EARTH", bus, None, complex(earth_r, 0.0), "EARTH")
        else:
            mapping = _cross_mapping(bonding, joint_id) if bonding.scheme == BONDING_CROSS else {p: p for p in "ABC"}
            for from_phase, to_phase in mapping.items():
                add_scalar_branch(
                    f"LINK:{joint_id}:{from_phase}>{to_phase}", "CROSS_LINK" if from_phase != to_phase else "STRAIGHT_LINK",
                    prev_end[from_phase], next_start[to_phase],
                    _actual_link_impedance(cable, bonding, joint_id), f"{from_phase}>{to_phase}",
                )
            if "GCC" in prev_end:
                add_scalar_branch(
                    f"GCCLINK:{joint_id}", "GCC_LINK", prev_end["GCC"], next_start["GCC"],
                    _actual_link_impedance(cable, bonding, joint_id, straight_gcc=True), "GCC",
                )
                if bool(getattr(bonding, "gcc_ground_at_link_boxes", False)):
                    bus = node(f"GCCBUS:{joint_id}")
                    ensure_vectors()
                    add_scalar_branch(f"GCCBOND:{joint_id}", "GCC_BOND", next_start["GCC"], bus, complex(1e-5, 0), "GCC")
                    earth_r = max(float(joint.earth_resistance_ohm if joint else 0.2), 1e-4)
                    add_scalar_branch(f"GCCEARTH:{joint_id}", "EARTH", bus, None, complex(earth_r, 0), "EARTH")

    # Termination bonding/grounding.
    first = minors[0]
    last = minors[-1]
    terminal_sides = [
        (first.start_node_id, endpoints[first.section_id][0], "START"),
        (last.end_node_id, endpoints[last.section_id][1], "END"),
    ]
    for terminal_id, side, side_name in terminal_sides:
        terminal = node_by_id.get(terminal_id)
        should_ground = bool(terminal and terminal.grounded)
        if bonding.scheme == BONDING_SINGLE_POINT and side_name == "END":
            should_ground = False
        if should_ground:
            bus = node(f"BUS:{terminal_id}")
            ensure_vectors()
            z_bond = complex(max(float(getattr(bonding, "ground_bus_contact_resistance_mohm", 0.20)) / 1000.0, 1e-7), 0.0)
            for phase in "ABC":
                add_scalar_branch(f"TBOND:{terminal_id}:{phase}", "SOLID_BOND", side[phase], bus, z_bond, phase)
            if "GCC" in side:
                add_scalar_branch(f"TBOND:{terminal_id}:GCC", "GCC_BOND", side["GCC"], bus, z_bond, "GCC")
            earth_r = max(float(terminal.earth_resistance_ohm), 1e-4)
            add_scalar_branch(f"EARTH:{terminal_id}", "EARTH", bus, None, complex(earth_r, 0.0), "EARTH")

    ensure_vectors()
    return _NetworkModel(
        node_labels,
        branches,
        np.array(g_diag_dynamic, dtype=complex),
        np.array(j_dynamic, dtype=complex),
        section_branch_index,
        endpoints,
        matrix_blocks,
        unknown_labels,
    )


# ---------------------------------------------------------------------------
# CIM and NV solutions
# ---------------------------------------------------------------------------


def _assemble(model: _NetworkModel) -> tuple[csc_matrix, csc_matrix, csc_matrix, np.ndarray, np.ndarray, list[slice]]:
    n_nodes = len(model.node_labels)
    branch_sizes = [len(branch.start_nodes) for branch in model.branches]
    total_currents = sum(branch_sizes)
    a = lil_matrix((n_nodes, total_currents), dtype=complex)
    z_blocks: list[np.ndarray] = []
    e = np.zeros(total_currents, dtype=complex)
    slices: list[slice] = []
    cursor = 0
    for branch, size in zip(model.branches, branch_sizes):
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
    z = block_diag(z_blocks, format="csc", dtype=complex)
    g = diags(model.g_shunt, format="csc", dtype=complex)
    return a.tocsc(), z, g, model.j_injection.copy(), e, slices


def _condition_number(matrix: csc_matrix) -> float:
    n = matrix.shape[0]
    if n <= 360:
        return float(np.linalg.cond(matrix.toarray()))
    return float("nan")


def _residual_norm(matrix: csc_matrix, x: np.ndarray, rhs: np.ndarray) -> float:
    denominator = max(float(np.linalg.norm(rhs)), 1.0)
    return float(np.linalg.norm(matrix @ x - rhs) / denominator)


def _kcl_residual(a: csc_matrix, g: csc_matrix, v: np.ndarray, i: np.ndarray, j: np.ndarray) -> float:
    denominator = max(float(np.linalg.norm(j)), 1.0)
    return float(np.linalg.norm(g @ v + a @ i - j) / denominator)


def _solve_cim(model: _NetworkModel) -> tuple[PrimitiveMethodResult, list[slice]]:
    a, z, g, j, e, slices = _assemble(model)
    zero = csc_matrix((z.shape[0], z.shape[0]), dtype=complex)
    matrix = bmat([[g, a], [a.T, -z]], format="csc")
    rhs = np.concatenate([j, e])
    solution = np.asarray(spsolve(matrix, rhs), dtype=complex)
    n = len(model.node_labels)
    v, i = solution[:n], solution[n:]
    return PrimitiveMethodResult(
        "PRIMITIVE_CIM",
        tuple(complex(x) for x in v),
        tuple(complex(x) for x in i),
        _condition_number(matrix),
        _residual_norm(matrix, solution, rhs),
        _kcl_residual(a, g, v, i, j),
    ), slices


def _solve_nv(model: _NetworkModel) -> tuple[PrimitiveMethodResult, list[slice]]:
    a, z, g, j, e, slices = _assemble(model)
    inverse_blocks = [np.linalg.inv(branch.z) for branch in model.branches]
    yb = block_diag(inverse_blocks, format="csc", dtype=complex)
    matrix = (g + a @ yb @ a.T).tocsc()
    rhs = j + a @ (yb @ e)
    v = np.asarray(spsolve(matrix, rhs), dtype=complex)
    i = np.asarray(yb @ (a.T @ v - e), dtype=complex)
    return PrimitiveMethodResult(
        "NODE_VOLTAGE",
        tuple(complex(x) for x in v),
        tuple(complex(x) for x in i),
        _condition_number(matrix),
        _residual_norm(matrix, v, rhs),
        _kcl_residual(a, g, v, i, j),
    ), slices


def solve_primitive_network(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection],
    selected_method: str | None = None,
    phase_currents_a: dict[str, complex] | None = None,
    phase_voltages_v: dict[str, complex] | None = None,
    include_dielectric_charging: bool | None = None,
) -> PrimitiveNetworkResult:
    routes = list(route_sections)
    model = _build_network(
        cable, bonding, routes,
        phase_currents_a=phase_currents_a,
        phase_voltages_v=phase_voltages_v,
        include_dielectric_charging=include_dielectric_charging,
    )
    cim, slices = _solve_cim(model)
    nv, _ = _solve_nv(model)
    selected = (selected_method or bonding.solver_mode).strip().upper()
    if selected not in {"PRIMITIVE_CIM", "NODE_VOLTAGE"}:
        selected = "PRIMITIVE_CIM"
    active = cim if selected == "PRIMITIVE_CIM" else nv
    v = np.array(active.node_voltages_v, dtype=complex)
    currents = np.array(active.branch_currents_a, dtype=complex)

    dv = max((abs(a - b) for a, b in zip(cim.node_voltages_v, nv.node_voltages_v)), default=0.0)
    di = max((abs(a - b) for a, b in zip(cim.branch_currents_a, nv.branch_currents_a)), default=0.0)
    tolerance_v = max(1e-5, 1e-7 * max((abs(x) for x in active.node_voltages_v), default=1.0))
    tolerance_i = max(1e-6, 1e-7 * max((abs(x) for x in active.branch_currents_a), default=1.0))

    positions = _node_positions(bonding)
    minor_by_id = {minor.section_id: minor for minor in bonding.minor_sections}
    section_results: list[PrimitiveSectionResult] = []
    total_sheath = 0.0
    total_gcc = 0.0
    total_earth = 0.0
    max_gcc = 0.0
    for section_id, branch_idx in model.section_branch_index.items():
        branch = model.branches[branch_idx]
        sl = slices[branch_idx]
        i_vec = currents[sl]
        start_nodes, end_nodes = model.section_endpoint_nodes[section_id]
        sheath_i = tuple(complex(i_vec[k]) for k in range(3))
        gcc_i = complex(i_vec[3]) if len(i_vec) > 3 else 0j
        start_v = tuple(complex(v[start_nodes[p]]) for p in "ABC")
        end_v = tuple(complex(v[end_nodes[p]]) for p in "ABC")
        sheath_loss = sum(abs(i_vec[k]) ** 2 * branch.metallic_r_diag[k] for k in range(3))
        gcc_loss = abs(gcc_i) ** 2 * branch.metallic_r_diag[3] if len(i_vec) > 3 else 0.0
        total_branch_loss = float(np.real(np.conjugate(i_vec) @ branch.z @ i_vec))
        earth_loss = max(0.0, total_branch_loss - sheath_loss - gcc_loss)
        total_sheath += sheath_loss
        total_gcc += gcc_loss
        total_earth += earth_loss
        max_gcc = max(max_gcc, abs(gcc_i))
        minor = minor_by_id[section_id]
        start_m, end_m = _minor_bounds(minor, positions)
        section_results.append(
            PrimitiveSectionResult(
                section_id,
                minor.major_index,
                start_m,
                end_m,
                sheath_i,
                gcc_i,
                start_v,
                end_v,
                sheath_loss,
                gcc_loss,
                earth_loss,
            )
        )

    accessory_results: list[PrimitiveBranchResult] = []
    accessory_loss = 0.0
    for idx, branch in enumerate(model.branches):
        if branch.branch_type == "CABLE_SECTION":
            continue
        sl = slices[idx]
        for local, value in enumerate(currents[sl]):
            z = complex(branch.z[local, local])
            loss = abs(value) ** 2 * max(z.real, 0.0)
            accessory_loss += loss
            start_label = model.node_labels[branch.start_nodes[local]]
            end_node = branch.end_nodes[local]
            end_label = "GROUND" if end_node is None else model.node_labels[end_node]
            accessory_results.append(
                PrimitiveBranchResult(
                    branch.branch_id if len(branch.start_nodes) == 1 else f"{branch.branch_id}:{local}",
                    branch.branch_type,
                    start_label,
                    end_label,
                    complex(value),
                    z,
                    loss,
                )
            )

    maximum_sheath_current = max((s.max_sheath_current_a for s in section_results), default=0.0)
    maximum_sheath_voltage = max((s.max_sheath_voltage_v for s in section_results), default=0.0)
    notes = [
        "CIM ve Node-Voltage aynı primitive branch ağı üzerinde bağımsız olarak çözülür; yöntem farkı kabul kapısıdır.",
        "Her minor section için 3 faz iletkeni + 3 metalik kılıf ve etkinse GCC/ECC primitive Z matrisi kurulmuştur.",
        "Toprak dönüşü simplified-Carson eşdeğer derinlik yaklaşımıdır; tam Pollaczek/Wedepohl-Wilcox integrali değildir.",
        "İletken–metalik-kılıf kapasitansı dağıtılmış pi yaklaşımıyla düğüm admitansı ve bilinen iletken gerilimi enjeksiyonu olarak eklenir.",
        "SVL normal güç frekansında iletime geçmeyen açık kol kabul edilir; fault-TOV/EMT ayrı çözümdür.",
    ]
    if not bool(getattr(bonding, "gcc_enabled", False)):
        notes.append("GCC/ECC devre dışıdır; saha projesinde mevcutsa etkinleştirilmeden fault/EPR hükmü verilmemelidir.")
    if maximum_sheath_current < 1e-8:
        notes.append("Çok küçük akım ideal model iptali olabilir; tolerans, aksesuar ve as-built sapmaları ayrıca çalışılmalıdır.")
    trace = (
        f"earth_rho={getattr(bonding, 'earth_resistivity_ohm_m', 100.0):.6g} ohm.m",
        f"dielectric_charging={bool(getattr(bonding, 'include_dielectric_charging', True)) if include_dielectric_charging is None else bool(include_dielectric_charging)}",
        f"gcc_enabled={bool(getattr(bonding, 'gcc_enabled', False))}",
        f"minor_count={len(section_results)}",
        f"matrix_block_count={len(model.matrix_blocks)}",
    )
    return PrimitiveNetworkResult(
        selected,
        REFERENCE,
        model.unknown_labels,
        tuple(model.node_labels),
        tuple(model.matrix_blocks),
        tuple(section_results),
        tuple(accessory_results),
        cim,
        nv,
        dv <= tolerance_v and di <= tolerance_i,
        float(dv),
        float(di),
        float(maximum_sheath_current),
        float(maximum_sheath_voltage),
        float(max_gcc),
        float(total_sheath),
        float(total_gcc),
        float(total_earth),
        float(accessory_loss),
        len(model.node_labels),
        len(active.branch_currents_a),
        tuple(notes),
        trace,
    )
