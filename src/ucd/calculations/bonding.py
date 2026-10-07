from __future__ import annotations

from ucd.calculations.phase_geometry import PhaseGeometryError, normalize_arrangement, normalize_phase_order, phase_slot_offsets_m
from ucd.calculations.cable_physical_parameters import (
    PhysicalParameterInputError,
    material_resistivity_20_ohm_m,
)

import cmath
from dataclasses import dataclass, replace
from math import ceil, log, pi, sqrt
from typing import Any, Iterable

import numpy as np

from ucd.models.project import (
    BONDING_CROSS,
    BONDING_SINGLE_POINT,
    BONDING_SOLID_BOTH_END,
    BondingConnection,
    BondingLinkBox,
    BondingMinorSection,
    BondingNode,
    BondingSystemData,
    CableData,
    RouteSection,
)


REFERENCE = (
    "IEEE Std 575-2014 Annex D/F complex-algebra engineering implementation; "
    "v0.8 provides primitive core/sheath/optional GCC network CIM and Node-Voltage; "
    "earth return is simplified-Carson and full Pollaczek/wideband EMT remains pending"
)
MU0_OVER_2PI = 2.0e-7  # H/m


class BondingInputError(ValueError):
    pass


@dataclass(frozen=True)
class RouteContribution:
    route_name: str
    start_m: float
    end_m: float
    length_m: float
    phase_spacing_m: float
    sheath_voltage_v: tuple[complex, complex, complex]
    phase_positions_m: tuple[tuple[str, float, float], ...] = ()
    geometry_fingerprint: str = ""


@dataclass(frozen=True)
class MinorSectionInducedVoltage:
    section_id: str
    section_name: str
    major_index: int
    start_m: float
    end_m: float
    length_m: float
    phase_order: str
    sheath_voltage_v: tuple[complex, complex, complex]
    route_contributions: tuple[RouteContribution, ...]

    @property
    def max_open_circuit_voltage_v(self) -> float:
        return max(abs(value) for value in self.sheath_voltage_v)


@dataclass(frozen=True)
class BondingLoopResult:
    major_index: int
    loop_name: str
    sheath_path: tuple[str, ...]
    residual_emf_v: complex
    current_a: complex
    loop_impedance_ohm: complex
    sheath_loss_w: float
    max_minor_open_circuit_voltage_v: float

    @property
    def residual_emf_magnitude_v(self) -> float:
        return abs(self.residual_emf_v)

    @property
    def current_magnitude_a(self) -> float:
        return abs(self.current_a)


@dataclass(frozen=True)
class MajorMatrixResult:
    major_index: int
    loop_names: tuple[str, str, str]
    sheath_paths: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
    source_vector_v: tuple[complex, complex, complex]
    impedance_matrix_ohm: tuple[tuple[complex, complex, complex], ...]
    current_vector_a: tuple[complex, complex, complex]
    condition_number: float


@dataclass(frozen=True)
class StandingVoltagePoint:
    chainage_m: float
    voltage_v: float
    label: str


@dataclass(frozen=True)
class BondingResult:
    scheme: str
    system_name: str
    total_length_m: float
    major_section_count: int
    minor_results: tuple[MinorSectionInducedVoltage, ...]
    loop_results: tuple[BondingLoopResult, ...]
    major_matrix_results: tuple[MajorMatrixResult, ...]
    primitive_network_result: Any | None
    standing_voltage_profile: tuple[StandingVoltagePoint, ...]
    max_standing_voltage_v: float
    total_sheath_loss_w: float
    total_conductor_loss_w: float
    lambda1: float
    sheath_resistance_20_ohm_km: float
    sheath_resistance_operating_ohm_km: float
    sheath_loop_reactance_ohm_km: float
    solver_mode: str
    maximum_matrix_condition_number: float
    ideal_cancellation: bool
    voltage_limit_v: float
    voltage_limit_ok: bool
    lead_length_ok: bool
    notes: tuple[str, ...]
    trace: tuple[str, ...]

    def trace_lines(self) -> list[str]:
        lines = [
            f"Bonding sistemi: {self.system_name}",
            f"Şema: {self.scheme}",
            f"Referans durumu: {REFERENCE}",
            f"Toplam güzergâh = {self.total_length_m:.3f} m",
            f"Major section sayısı = {self.major_section_count}",
            f"Metalik kılıf R20 = {self.sheath_resistance_20_ohm_km:.6f} ohm/km",
            f"Metalik kılıf Rop = {self.sheath_resistance_operating_ohm_km:.6f} ohm/km",
            f"Metalik kılıf çevrim X = {self.sheath_loop_reactance_ohm_km:.6f} ohm/km",
            f"Çözüm modu = {self.solver_mode}",
            f"Maksimum matris koşul sayısı = {self.maximum_matrix_condition_number:.6g}",
            f"İdeal iptal göstergesi = {'EVET' if self.ideal_cancellation else 'HAYIR'}",
            f"Normal standing-voltage kriteri = {self.voltage_limit_v:.3f} V",
        ]
        for minor in self.minor_results:
            values = " / ".join(
                f"{phase}:{abs(value):.3f} V ∠{_angle_deg(value):.1f}°"
                for phase, value in zip("ABC", minor.sheath_voltage_v)
            )
            lines.append(
                f"M{minor.major_index}/{minor.section_name}: km={minor.start_m:.3f}-{minor.end_m:.3f}, "
                f"L={minor.length_m:.3f} m, faz sırası={minor.phase_order}, E(A/B/C)={values}"
            )
            for contribution in minor.route_contributions:
                c_values = "/".join(f"{abs(v):.3f}" for v in contribution.sheath_voltage_v)
                lines.append(
                    f"  ↳ {contribution.route_name}: {contribution.start_m:.3f}-{contribution.end_m:.3f} m, "
                    f"L={contribution.length_m:.3f} m, S={contribution.phase_spacing_m:.4f} m, |EABC|={c_values} V"
                )
        for loop in self.loop_results:
            lines.append(
                f"{loop.loop_name}: metalik kılıf yolu={'→'.join(loop.sheath_path)}, "
                f"|Eres|={loop.residual_emf_magnitude_v:.3f} V, |Ish|={loop.current_magnitude_a:.3f} A, "
                f"Z={loop.loop_impedance_ohm.real:.5f}+j{loop.loop_impedance_ohm.imag:.5f} ohm, "
                f"Psh={loop.sheath_loss_w:.3f} W"
            )
        if self.primitive_network_result is not None:
            lines.extend(self.primitive_network_result.trace_lines())
        for matrix in self.major_matrix_results:
            lines.append(
                f"M{matrix.major_index} bağlı kompleks loop matrisi; cond={matrix.condition_number:.6g}"
            )
            for row_index, row in enumerate(matrix.impedance_matrix_ohm):
                formatted = " | ".join(f"{value.real:.6f}+j{value.imag:.6f}" for value in row)
                lines.append(f"  Z[{row_index + 1},:] = {formatted} ohm")
            source = " / ".join(f"{abs(v):.6f}∠{_angle_deg(v):.2f}°" for v in matrix.source_vector_v)
            current = " / ".join(f"{abs(i):.6f}∠{_angle_deg(i):.2f}°" for i in matrix.current_vector_a)
            lines.append(f"  E = {source} V")
            lines.append(f"  I = {current} A")
        lines.extend(
            [
                f"Maksimum standing gerilim = {self.max_standing_voltage_v:.3f} V "
                f"({'PASS' if self.voltage_limit_ok else 'FAIL'})",
                f"Bonding lead uzunluk kontrolü = {'PASS' if self.lead_length_ok else 'FAIL'}",
                f"Toplam metalik kılıf kaybı = {self.total_sheath_loss_w:.3f} W",
                f"Toplam iletken kaybı = {self.total_conductor_loss_w:.3f} W",
                f"lambda1 = {self.lambda1:.8f}",
                *(f"Not: {note}" for note in self.notes),
            ]
        )
        return lines


@dataclass(frozen=True)
class DesignIteration:
    iteration: int
    major_index: int
    boundary_1_m: float
    boundary_2_m: float
    objective: float
    max_standing_voltage_v: float
    max_residual_emf_v: float


@dataclass(frozen=True)
class CrossBondingDesignResult:
    bonding: BondingSystemData
    calculation: BondingResult
    minor_section_count: int
    major_section_count: int
    initial_boundaries_m: tuple[float, ...]
    optimized_boundaries_m: tuple[float, ...]
    iterations: tuple[DesignIteration, ...]
    notes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Electrical primitives
# ---------------------------------------------------------------------------

def _angle_deg(value: complex) -> float:
    if abs(value) < 1e-15:
        return 0.0
    return cmath.phase(value) * 180.0 / pi


def _positive(name: str, value: float, allow_zero: bool = False) -> float:
    if (allow_zero and value < 0) or (not allow_zero and value <= 0):
        comparator = "negatif olamaz" if allow_zero else "sıfırdan büyük olmalı"
        raise BondingInputError(f"{name} {comparator}: {value}")
    return float(value)


def _material_resistivity_20_ohm_m(material: str) -> float:
    try:
        return material_resistivity_20_ohm_m(material)
    except PhysicalParameterInputError as exc:
        raise BondingInputError(str(exc)) from exc


def sheath_resistance_ohm_km(cable: CableData) -> tuple[float, float]:
    if cable.sheath_dc_resistance_20_ohm_km > 0:
        r20 = float(cable.sheath_dc_resistance_20_ohm_km)
    else:
        area = _positive("Metalik kılıf/ekran kesiti", cable.sheath_cross_section_mm2)
        r20 = _material_resistivity_20_ohm_m(cable.sheath_material) * 1e9 / area
    # The project-level calculation-policy migration resolves automatic material
    # defaults before production use. The scalar remains authoritative here so
    # a field-level MANUAL_OVERRIDE equal to the historical 0.00393 value is not erased.
    alpha = _positive(
        "Metalik kılıf sıcaklık katsayısı",
        cable.sheath_temperature_coefficient_20_per_c,
        allow_zero=True,
    )
    temperature = float(cable.sheath_operating_temperature_c)
    if temperature <= -273.15:
        raise BondingInputError("Metalik kılıf işletme sıcaklığı mutlak sıfırın üzerinde olmalıdır.")
    corrected = r20 * (1.0 + alpha * (temperature - 20.0))
    if corrected <= 0.0:
        raise BondingInputError(
            f"Metalik kılıf sıcaklık düzeltmesi sıfır/negatif direnç üretti: {corrected:.9g} ohm/km."
        )
    return r20, corrected


def _phase_positions(arrangement: str, spacing_m: float, phase_order: str) -> dict[str, complex]:
    try:
        normalized = normalize_arrangement(arrangement)
        if normalized == "SINGLE":
            raise PhaseGeometryError(
                "BONDING_SINGLE_REQUIRES_RETURN_PATH_GEOMETRY: SINGLE bonding çözümü için "
                "gidiş/dönüş yolu veya üç fazlı açık koordinat geometrisi gereklidir."
            )
        slots = phase_slot_offsets_m(normalized, spacing_m, phase_order)
    except PhaseGeometryError as exc:
        raise BondingInputError(str(exc)) from exc
    return {phase: complex(x, y) for phase, (x, y) in slots.items()}




def _explicit_phase_positions(
    raw: Any,
    phase_order: str,
) -> dict[str, complex] | None:
    if not isinstance(raw, dict) or not all(phase in raw for phase in "ABC"):
        return None
    try:
        order = normalize_phase_order(phase_order)
        base = []
        for slot_phase in "ABC":
            value = raw[slot_phase]
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                return None
            base.append(complex(float(value[0]), float(value[1])))
        return {order[index]: base[index] for index in range(3)}
    except (TypeError, ValueError, PhaseGeometryError) as exc:
        raise BondingInputError(f"Açık faz koordinatları geçersiz: {exc}") from exc


def _positions_for_route(
    cable: CableData,
    bonding: BondingSystemData,
    route: RouteSection | None,
    phase_order: str,
    spacing_m: float | None = None,
) -> dict[str, complex]:
    explicit = _explicit_phase_positions(getattr(route, "phase_positions_m", None), phase_order) if route is not None else None
    if explicit is not None:
        return explicit
    spacing = bonding.phase_spacing_m if spacing_m is None else spacing_m
    arrangement = getattr(route, "resolved_arrangement", "") or cable.arrangement
    return _phase_positions(arrangement, spacing, phase_order)


def _balanced_phase_currents(current_a: float) -> dict[str, complex]:
    current = _positive("Tasarım akımı", current_a)
    return {
        "A": cmath.rect(current, 0.0),
        "B": cmath.rect(current, -2.0 * pi / 3.0),
        "C": cmath.rect(current, 2.0 * pi / 3.0),
    }


def induced_sheath_voltage_per_m(
    cable: CableData,
    bonding: BondingSystemData,
    phase_order: str,
    phase_spacing_m: float | None = None,
    phase_positions_m: dict[str, list[float]] | None = None,
) -> dict[str, complex]:
    """Longitudinal open-circuit sheath EMF for a balanced three-phase set.

    This implementation follows the logarithmic geometric form used by IEEE
    575 Annex D for screening/design calculations.  Piecewise route integration
    is performed by ``integrate_minor_voltage``.
    """

    spacing = bonding.phase_spacing_m if phase_spacing_m is None else phase_spacing_m
    if phase_positions_m:
        positions = _explicit_phase_positions(phase_positions_m, phase_order)
        if positions is None:
            raise BondingInputError("Açık faz koordinatları A/B/C için eksiksiz olmalıdır.")
    else:
        positions = _phase_positions(cable.arrangement, spacing, phase_order)
    currents = _balanced_phase_currents(cable.design_current_a)
    radius = _positive("Metalik kılıf ortalama yarıçapı", cable.sheath_mean_diameter_mm / 2000.0)
    omega = 2.0 * pi * _positive("Frekans", cable.frequency_hz)
    result: dict[str, complex] = {}
    for sheath_phase in "ABC":
        total = currents[sheath_phase] * log(1.0 / radius)
        for source_phase in "ABC":
            if source_phase == sheath_phase:
                continue
            distance = abs(positions[sheath_phase] - positions[source_phase])
            if distance <= radius:
                raise BondingInputError("Faz eksen aralığı metalik kılıf yarıçapından büyük olmalı.")
            total += currents[source_phase] * log(1.0 / distance)
        result[sheath_phase] = 1j * omega * MU0_OVER_2PI * total
    return result


def _equivalent_spacing(cable: CableData, bonding: BondingSystemData) -> float:
    positions = _phase_positions(cable.arrangement, bonding.phase_spacing_m, "ABC")
    distances = [
        abs(positions["A"] - positions["B"]),
        abs(positions["B"] - positions["C"]),
        abs(positions["C"] - positions["A"]),
    ]
    return (distances[0] * distances[1] * distances[2]) ** (1.0 / 3.0)


def sheath_loop_reactance_ohm_km(cable: CableData, bonding: BondingSystemData) -> float:
    d_eq = _equivalent_spacing(cable, bonding)
    radius = _positive("Metalik kılıf ortalama yarıçapı", cable.sheath_mean_diameter_mm / 2000.0)
    if d_eq <= radius:
        raise BondingInputError("Eşdeğer faz aralığı sheath yarıçapından büyük olmalı.")
    omega = 2.0 * pi * _positive("Frekans", cable.frequency_hz)
    return omega * MU0_OVER_2PI * log(d_eq / radius) * 1000.0


def physical_sheath_impedance_matrix_ohm_km(
    cable: CableData,
    bonding: BondingSystemData,
    phase_spacing_m: float,
    phase_positions_m: dict[str, list[float]] | None = None,
) -> np.ndarray:
    """Return the v0.7 relative-geometry 3x3 sheath impedance matrix.

    The diagonal contains sheath resistance plus self differential reactance.
    Off-diagonal terms represent relative magnetic coupling between physical
    sheath positions. Common earth-return impedance is intentionally excluded;
    therefore this is a coupled loop matrix preview, not the full primitive CIM.
    """

    _r20, r_op = sheath_resistance_ohm_km(cable)
    if phase_positions_m:
        positions = _explicit_phase_positions(phase_positions_m, "ABC")
        if positions is None:
            raise BondingInputError("Açık faz koordinatları A/B/C için eksiksiz olmalıdır.")
    else:
        positions = _phase_positions(cable.arrangement, phase_spacing_m, "ABC")
    distances = {
        (a, b): abs(positions[a] - positions[b])
        for a in "ABC" for b in "ABC" if a != b
    }
    d_eq = (distances[("A", "B")] * distances[("B", "C")] * distances[("C", "A")]) ** (1.0 / 3.0)
    radius = _positive("Metalik kılıf ortalama yarıçapı", cable.sheath_mean_diameter_mm / 2000.0)
    if d_eq <= radius:
        raise BondingInputError("Eşdeğer faz aralığı sheath yarıçapından büyük olmalı.")
    omega = 2.0 * pi * _positive("Frekans", cable.frequency_hz)
    coefficient = omega * MU0_OVER_2PI * 1000.0
    matrix = np.zeros((3, 3), dtype=complex)
    for i, phase_i in enumerate("ABC"):
        matrix[i, i] = complex(r_op, coefficient * log(d_eq / radius))
        for j, phase_j in enumerate("ABC"):
            if i == j:
                continue
            if bonding.sheath_mutual_coupling_enabled:
                matrix[i, j] = complex(0.0, coefficient * log(d_eq / distances[(phase_i, phase_j)]))
    return matrix


def _minor_loop_impedance_contribution(
    cable: CableData,
    bonding: BondingSystemData,
    minor: MinorSectionInducedVoltage,
    path_phases: tuple[str, str, str],
) -> np.ndarray:
    phase_index = {phase: index for index, phase in enumerate("ABC")}
    total = np.zeros((3, 3), dtype=complex)
    contributions = minor.route_contributions or (
        RouteContribution(
            "Bonding varsayılan geometrisi", minor.start_m, minor.end_m, minor.length_m,
            bonding.phase_spacing_m, minor.sheath_voltage_v,
        ),
    )
    for contribution in contributions:
        explicit = {phase: [x, depth] for phase, x, depth in contribution.phase_positions_m}
        physical = physical_sheath_impedance_matrix_ohm_km(
            cable, bonding, contribution.phase_spacing_m, explicit or None
        )
        for loop_i, phase_i in enumerate(path_phases):
            for loop_j, phase_j in enumerate(path_phases):
                total[loop_i, loop_j] += (
                    physical[phase_index[phase_i], phase_index[phase_j]]
                    * contribution.length_m / 1000.0
                )
    return total


def coupled_major_loop_impedance_matrix(
    cable: CableData,
    bonding: BondingSystemData,
    minor_group: tuple[MinorSectionInducedVoltage, ...],
    paths: tuple[tuple[str, ...], ...],
    internal_joint_ids: tuple[str, str],
) -> np.ndarray:
    if len(minor_group) != 3 or len(paths) != 3:
        raise BondingInputError("Bağlı loop matrisi üç minor ve üç sheath yolu gerektirir.")
    matrix = np.zeros((3, 3), dtype=complex)
    for minor_index, minor in enumerate(minor_group):
        occupied = tuple(path[minor_index] for path in paths)
        matrix += _minor_loop_impedance_contribution(cable, bonding, minor, occupied)
    connection_r = _connection_resistance_ohm(bonding, internal_joint_ids)
    matrix += np.eye(3, dtype=complex) * connection_r
    return matrix


# ---------------------------------------------------------------------------
# Route-aware integration
# ---------------------------------------------------------------------------

def _route_ranges(route_sections: Iterable[RouteSection] | None, total_fallback_m: float, default_spacing_m: float) -> list[tuple[float, float, str, float, RouteSection | None]]:
    sections = list(route_sections or [])
    if not sections:
        return [(0.0, total_fallback_m, "Bonding varsayılan geometrisi", default_spacing_m, None)]
    ranges: list[tuple[float, float, str, float, RouteSection | None]] = []
    cursor = 0.0
    for section in sections:
        length = _positive(f"{section.name} uzunluğu", section.length_m)
        spacing = section.phase_spacing_m if section.phase_spacing_m > 0 else default_spacing_m
        ranges.append((cursor, cursor + length, section.name, spacing, section))
        cursor += length
    return ranges


def integrate_minor_voltage(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection] | None,
    start_m: float,
    end_m: float,
    phase_order: str,
) -> tuple[tuple[complex, complex, complex], tuple[RouteContribution, ...]]:
    if end_m <= start_m:
        raise BondingInputError(f"Minor section zincirleme konumları geçersiz: {start_m}–{end_m} m")
    route_ranges = _route_ranges(route_sections, end_m, bonding.phase_spacing_m)
    totals = {phase: 0j for phase in "ABC"}
    contributions: list[RouteContribution] = []
    covered = 0.0
    for r_start, r_end, route_name, spacing, route in route_ranges:
        overlap_start = max(start_m, r_start)
        overlap_end = min(end_m, r_end)
        if overlap_end <= overlap_start:
            continue
        length = overlap_end - overlap_start
        explicit = getattr(route, "phase_positions_m", None) if route is not None else None
        per_m = induced_sheath_voltage_per_m(cable, bonding, phase_order, spacing, explicit)
        values = tuple(per_m[phase] * length for phase in "ABC")
        for phase, value in zip("ABC", values):
            totals[phase] += value
        covered += length
        positions_tuple = tuple(
            (phase, float(value[0]), float(value[1]))
            for phase, value in sorted((explicit or {}).items())
            if isinstance(value, (list, tuple)) and len(value) >= 2
        )
        contributions.append(
            RouteContribution(
                route_name, overlap_start, overlap_end, length, spacing, values,
                positions_tuple, str(getattr(route, "geometry_fingerprint", "") if route is not None else ""),
            )
        )
    if covered + 1e-6 < end_m - start_m:
        # Extend the final known/default geometry so a manual bonding length can
        # still be evaluated while clearly recording the assumption.
        missing_start = start_m + covered
        missing = end_m - start_m - covered
        per_m = induced_sheath_voltage_per_m(cable, bonding, phase_order, bonding.phase_spacing_m)
        values = tuple(per_m[phase] * missing for phase in "ABC")
        for phase, value in zip("ABC", values):
            totals[phase] += value
        contributions.append(
            RouteContribution("Güzergâh dışı varsayılan uzatma", missing_start, end_m, missing, bonding.phase_spacing_m, values)
        )
    return tuple(totals[phase] for phase in "ABC"), tuple(contributions)


def _node_map(bonding: BondingSystemData) -> dict[str, BondingNode]:
    mapping = {node.node_id: node for node in bonding.nodes}
    if len(mapping) != len(bonding.nodes):
        raise BondingInputError("Bonding düğüm ID'leri benzersiz olmalı.")
    return mapping


def _minor_results(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection] | None,
) -> tuple[MinorSectionInducedVoltage, ...]:
    if not bonding.minor_sections:
        raise BondingInputError("Bonding sisteminde minor section bulunmuyor.")
    nodes = _node_map(bonding)
    output: list[MinorSectionInducedVoltage] = []
    previous_end = -float("inf")
    for index, section in enumerate(bonding.minor_sections):
        if section.start_node_id not in nodes or section.end_node_id not in nodes:
            raise BondingInputError(f"{section.name} başlangıç/bitiş düğümü bulunamadı.")
        start = float(nodes[section.start_node_id].position_m)
        end = float(nodes[section.end_node_id].position_m)
        if start + 1e-6 < previous_end:
            raise BondingInputError("Minor section'lar güzergâh boyunca sıralı olmalı.")
        length = _positive(f"{section.name} uzunluğu", end - start)
        values, contributions = integrate_minor_voltage(
            cable, bonding, route_sections, start, end, section.phase_order
        )
        major_index = section.major_index if section.major_index > 0 else index // 3 + 1
        output.append(
            MinorSectionInducedVoltage(
                section.section_id,
                section.name,
                major_index,
                start,
                end,
                length,
                section.phase_order.upper(),
                values,
                contributions,
            )
        )
        previous_end = end
    return tuple(output)


# ---------------------------------------------------------------------------
# Explicit phase-sheath cross-bond graph
# ---------------------------------------------------------------------------

def _linkbox_by_joint(bonding: BondingSystemData) -> dict[str, BondingLinkBox]:
    result: dict[str, BondingLinkBox] = {}
    for link_box in bonding.link_boxes:
        result[link_box.joint_node_id] = link_box
    return result


def _connection_mapping_at_joint(bonding: BondingSystemData, joint_node_id: str) -> dict[str, str]:
    link_by_joint = _linkbox_by_joint(bonding)
    link_box = link_by_joint.get(joint_node_id)
    mapping: dict[str, str] = {}
    for connection in bonding.connections:
        same_joint = connection.node_id == joint_node_id
        same_box = link_box is not None and connection.link_box_id == link_box.link_box_id
        if connection.connection_type.strip().upper() != "CROSS" or not (same_joint or same_box):
            continue
        mapping[connection.from_sheath.upper()] = connection.to_sheath.upper()
    if set(mapping) != set("ABC") or set(mapping.values()) != set("ABC"):
        raise BondingInputError(
            f"{joint_node_id} için cross-bond bağlantıları A/B/C faz sheath'leri arasında tam permütasyon oluşturmuyor."
        )
    return mapping


def resolve_major_paths(bonding: BondingSystemData, group: tuple[BondingMinorSection, ...]) -> tuple[tuple[str, ...], ...]:
    if len(group) != 3:
        raise BondingInputError("Bir sectionalized cross-bonding major section tam üç minor section içermeli.")
    internal_joint_ids = [group[0].end_node_id, group[1].end_node_id]
    mappings = [_connection_mapping_at_joint(bonding, node_id) for node_id in internal_joint_ids]
    paths: list[tuple[str, ...]] = []
    for start_phase in "ABC":
        current = start_phase
        path = [current]
        for mapping in mappings:
            current = mapping[current]
            path.append(current)
        paths.append(tuple(path))
    if len(set(paths)) != 3:
        raise BondingInputError("Cross-bond bağlantı grafiği üç bağımsız faz-sheath yolu oluşturmuyor.")
    return tuple(paths)


# Backward-compatible private alias used by older development code.
_build_major_paths = resolve_major_paths


def _connection_resistance_ohm(bonding: BondingSystemData, joint_ids: Iterable[str]) -> float:
    contact_mohm = _positive(
        "Link box temas direnci", bonding.link_box_contact_resistance_mohm, allow_zero=True
    )
    fixed_lead_mohm = _positive(
        "Bonding lead sabit direnci", bonding.bonding_lead_resistance_mohm, allow_zero=True
    )
    per_m_mohm = _positive(
        "Bonding lead birim direnç", bonding.bonding_lead_resistance_mohm_per_m, allow_zero=True
    )
    by_joint = _linkbox_by_joint(bonding)
    total_mohm = 0.0
    for joint_id in joint_ids:
        link_box = by_joint.get(joint_id)
        lead_length = 0.0 if link_box is None else _positive(
            f"{link_box.name} bonding lead uzunluğu", link_box.lead_length_m, allow_zero=True
        )
        # A cross connection passes through two terminations/contacts and a pair
        # of bonding-lead conductors.  This is still a lumped 50/60 Hz preview.
        total_mohm += 2.0 * contact_mohm + fixed_lead_mohm + per_m_mohm * lead_length
    return total_mohm / 1000.0


def _lead_length_status(bonding: BondingSystemData) -> tuple[bool, list[str]]:
    limit = _positive(
        "Maksimum bonding lead uzunluğu", bonding.maximum_bonding_lead_length_m, allow_zero=True
    )
    warnings = [
        f"{box.name}: bonding lead {box.lead_length_m:.2f} m > kriter {limit:.2f} m"
        for box in bonding.link_boxes
        if box.lead_length_m > limit + 1e-9
    ]
    return not warnings, warnings


def _cumulative_standing_profile(
    minors: tuple[MinorSectionInducedVoltage, ...],
    bonding: BondingSystemData,
) -> tuple[StandingVoltagePoint, ...]:
    """Legacy/open-circuit standing-voltage envelope with phasor accumulation.

    For a cross-bonded major section the metallic-sheath path changes physical
    phase at each sectionalizing joint.  The voltage at a joint is therefore
    the *complex cumulative sum* of the induced EMFs already traversed on each
    of the three resolved sheath paths; it is not the magnitude of the latest
    minor section alone.  The envelope is reset only at the grounded major
    boundary.

    This remains the correct diagnostic profile for the legacy loop models.
    PRIMITIVE_CIM/NODE_VOLTAGE production solutions use the solved node
    voltages instead (see ``_primitive_standing_profile``).
    """
    if not minors:
        return ()

    phase_index = {phase: index for index, phase in enumerate("ABC")}
    scheme = bonding.scheme.strip().upper()
    points: list[StandingVoltagePoint] = []

    if scheme == BONDING_CROSS:
        if len(minors) % 3 != 0:
            raise BondingInputError(
                "Sectionalized CROSS_BONDED standing-voltage profili için minor section sayısı üçün katı olmalı."
            )
        for major_zero in range(len(minors) // 3):
            offset = major_zero * 3
            group = minors[offset:offset + 3]
            model_group = tuple(bonding.minor_sections[offset:offset + 3])
            paths = resolve_major_paths(bonding, model_group)
            cumulative = [0j, 0j, 0j]
            points.append(
                StandingVoltagePoint(group[0].start_m, 0.0, f"Major {major_zero + 1} başlangıç ground")
            )
            for i, minor in enumerate(group):
                for path_index, path in enumerate(paths):
                    physical_phase = path[i]
                    cumulative[path_index] += minor.sheath_voltage_v[phase_index[physical_phase]]
                points.append(
                    StandingVoltagePoint(
                        minor.end_m,
                        max(abs(value) for value in cumulative),
                        f"Major {major_zero + 1} / {minor.section_name} kümülatif",
                    )
                )
            # The end link box/termination grounds the major-section sheath path.
            points.append(
                StandingVoltagePoint(group[-1].end_m, 0.0, f"Major {major_zero + 1} ground")
            )
        return tuple(points)

    # Single-point / solid-bonded diagnostic envelope: physical phase path does
    # not transpose through cross-bond joints, so accumulate A/B/C directly.
    cumulative = [0j, 0j, 0j]
    points.append(StandingVoltagePoint(minors[0].start_m, 0.0, "Başlangıç"))
    for minor in minors:
        for index in range(3):
            cumulative[index] += minor.sheath_voltage_v[index]
        points.append(
            StandingVoltagePoint(
                minor.end_m,
                max(abs(value) for value in cumulative),
                f"{minor.section_name} kümülatif",
            )
        )
    return tuple(points)


def _primitive_standing_profile(primitive_result: Any) -> tuple[StandingVoltagePoint, ...]:
    """Envelope of solved sheath-to-earth node voltages from the production network."""
    sections = sorted(
        tuple(getattr(primitive_result, "section_results", ()) or ()),
        key=lambda item: (item.start_m, item.end_m, item.section_id),
    )
    if not sections:
        return ()

    by_chainage: dict[float, float] = {}
    labels: dict[float, str] = {}
    for section in sections:
        start_v = max(abs(value) for value in section.start_sheath_voltages_v)
        end_v = max(abs(value) for value in section.end_sheath_voltages_v)
        by_chainage[section.start_m] = max(by_chainage.get(section.start_m, 0.0), start_v)
        by_chainage[section.end_m] = max(by_chainage.get(section.end_m, 0.0), end_v)
        labels.setdefault(section.start_m, f"{section.section_id} başlangıç — solved network")
        labels[section.end_m] = f"{section.section_id} sonu — solved network"
    return tuple(
        StandingVoltagePoint(chainage, by_chainage[chainage], labels[chainage])
        for chainage in sorted(by_chainage)
    )


def route_sheath_loop_reactance_ohm_km(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection] | None,
) -> float:
    routes = list(route_sections or ())
    if not routes:
        return sheath_loop_reactance_ohm_km(cable, bonding)
    radius = _positive("Metalik kılıf ortalama yarıçapı", cable.sheath_mean_diameter_mm / 2000.0)
    omega = 2.0 * pi * _positive("Frekans", cable.frequency_hz)
    weighted = 0.0
    length_total = 0.0
    for route in routes:
        length = _positive(f"{route.name} uzunluğu", route.length_m)
        positions = _positions_for_route(cable, bonding, route, "ABC", route.phase_spacing_m)
        distances = (abs(positions["A"]-positions["B"]), abs(positions["B"]-positions["C"]), abs(positions["C"]-positions["A"]))
        d_eq = (distances[0] * distances[1] * distances[2]) ** (1.0/3.0)
        if d_eq <= radius:
            raise BondingInputError("Eşdeğer faz aralığı sheath yarıçapından büyük olmalı.")
        weighted += omega * MU0_OVER_2PI * log(d_eq / radius) * 1000.0 * length
        length_total += length
    return weighted / length_total


def solve_bonding(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection] | None = None,
) -> BondingResult:
    scheme = bonding.scheme.strip().upper()
    if scheme not in {BONDING_CROSS, BONDING_SINGLE_POINT, BONDING_SOLID_BOTH_END}:
        raise BondingInputError(
            f"Bonding şeması {BONDING_CROSS}, {BONDING_SINGLE_POINT} veya {BONDING_SOLID_BOTH_END} olmalı."
        )

    minors = _minor_results(cable, bonding, route_sections)
    total_length = sum(item.length_m for item in minors)
    r20, r_op = sheath_resistance_ohm_km(cable)
    x_per_km = route_sheath_loop_reactance_ohm_km(cable, bonding, route_sections)
    phase_index = {phase: index for index, phase in enumerate("ABC")}
    solver_mode = bonding.solver_mode.strip().upper() if scheme == BONDING_CROSS else scheme

    loops: list[BondingLoopResult] = []
    major_matrices: list[MajorMatrixResult] = []
    max_standing = max((minor.max_open_circuit_voltage_v for minor in minors), default=0.0)
    total_sheath_loss = 0.0

    primitive_result = None
    if scheme == BONDING_CROSS:
        if len(minors) % 3 != 0:
            raise BondingInputError(
                "Sectionalized CROSS_BONDED çözümünde minor section sayısı üçün katı olmalı. "
                "Otomatik tasarım veya modified/continuous topoloji kullanın."
            )
        major_count = len(minors) // 3
        allowed_modes = {
            "PRIMITIVE_CIM", "NODE_VOLTAGE",
            "COUPLED_LOOP_MATRIX", "INDEPENDENT_LOOP_PREVIEW",
        }
        if solver_mode not in allowed_modes:
            raise BondingInputError(
                "Bonding solver modu PRIMITIVE_CIM, NODE_VOLTAGE, "
                "COUPLED_LOOP_MATRIX veya INDEPENDENT_LOOP_PREVIEW olmalı."
            )

        if solver_mode in {"PRIMITIVE_CIM", "NODE_VOLTAGE"}:
            from ucd.calculations.primitive_cim import solve_primitive_network

            primitive_result = solve_primitive_network(
                cable, bonding, list(route_sections or ()), selected_method=solver_mode
            )
            total_sheath_loss = primitive_result.total_sheath_metal_loss_w
            max_standing = primitive_result.maximum_sheath_voltage_v
            section_by_id = {item.section_id: item for item in primitive_result.section_results}
            r20, r_op = sheath_resistance_ohm_km(cable)
            for major_zero in range(major_count):
                m_start = major_zero * 3
                minor_group = minors[m_start:m_start + 3]
                model_group = tuple(bonding.minor_sections[m_start:m_start + 3])
                paths = resolve_major_paths(bonding, model_group)
                loop_names = tuple(f"M{major_zero + 1}-Loop {phase}" for phase in "ABC")
                source = [
                    sum(minor_group[i].sheath_voltage_v[phase_index[path[i]]] for i in range(3))
                    for path in paths
                ]
                for loop_index, path in enumerate(paths):
                    candidate_currents: list[complex] = []
                    loss = 0.0
                    for i, model_minor in enumerate(model_group):
                        section_result = section_by_id[model_minor.section_id]
                        physical_index = phase_index[path[i]]
                        current = section_result.sheath_currents_a[physical_index]
                        candidate_currents.append(current)
                        section_r = r_op * section_result.end_m / 1000.0 - r_op * section_result.start_m / 1000.0
                        loss += abs(current) ** 2 * max(section_r, 0.0)
                    representative = max(candidate_currents, key=abs, default=0j)
                    residual = complex(source[loop_index])
                    impedance = residual / representative if abs(representative) > 1e-12 else complex(float("inf"), 0.0)
                    loops.append(
                        BondingLoopResult(
                            major_zero + 1, loop_names[loop_index], path, residual, representative,
                            impedance, loss,
                            max(abs(minor_group[i].sheath_voltage_v[phase_index[path[i]]]) for i in range(3)),
                        )
                    )
        else:
            for major_zero in range(major_count):
                m_start = major_zero * 3
                minor_group = minors[m_start:m_start + 3]
                model_group = tuple(bonding.minor_sections[m_start:m_start + 3])
                paths = resolve_major_paths(bonding, model_group)
                internal_joints = (model_group[0].end_node_id, model_group[1].end_node_id)
                source = np.array([
                    sum(minor_group[i].sheath_voltage_v[phase_index[path[i]]] for i in range(3))
                    for path in paths
                ], dtype=complex)

                if solver_mode == "COUPLED_LOOP_MATRIX":
                    matrix = coupled_major_loop_impedance_matrix(
                        cable, bonding, minor_group, paths, internal_joints
                    )
                else:
                    group_length = sum(item.length_m for item in minor_group)
                    r_metal = r_op * group_length / 1000.0
                    x_metal = x_per_km * group_length / 1000.0
                    connection_r = _connection_resistance_ohm(bonding, internal_joints)
                    matrix = np.eye(3, dtype=complex) * complex(r_metal + connection_r, x_metal)

                condition_number = float(np.linalg.cond(matrix))
                if not np.isfinite(condition_number) or condition_number > 1e12:
                    raise BondingInputError(
                        f"Major {major_zero + 1} sheath-loop empedans matrisi tekil/kötü koşullu: "
                        f"cond={condition_number:.6g}"
                    )
                currents = np.linalg.solve(matrix, source)
                loop_names = tuple(f"M{major_zero + 1}-Loop {phase}" for phase in "ABC")
                major_matrices.append(
                    MajorMatrixResult(
                        major_zero + 1, loop_names, paths,
                        tuple(complex(v) for v in source),
                        tuple(tuple(complex(value) for value in row) for row in matrix),
                        tuple(complex(value) for value in currents), condition_number,
                    )
                )
                group_length = sum(item.length_m for item in minor_group)
                r_metal = r_op * group_length / 1000.0
                for loop_index, path in enumerate(paths):
                    residual = complex(source[loop_index])
                    current = complex(currents[loop_index])
                    loss = abs(current) ** 2 * r_metal
                    total_sheath_loss += loss
                    loops.append(
                        BondingLoopResult(
                            major_zero + 1, loop_names[loop_index], path, residual, current,
                            complex(matrix[loop_index, loop_index]), loss,
                            max(abs(minor_group[i].sheath_voltage_v[phase_index[path[i]]]) for i in range(3)),
                        )
                    )
    else:
        major_count = 1
        paths = tuple(tuple(phase for _ in minors) for phase in "ABC")
        for loop_index, path in enumerate(paths):
            emfs = [minor.sheath_voltage_v[phase_index[path[i]]] for i, minor in enumerate(minors)]
            residual = sum(emfs, 0j)
            max_minor = max(abs(value) for value in emfs)
            if scheme == BONDING_SINGLE_POINT:
                impedance = complex(float("inf"), 0.0)
                current = 0j
                loss = 0.0
            else:
                # Balanced solid bonding: circulating currents close through the other
                # phase sheaths, not the termination earth electrodes (IEC 60287-1-1 lambda1').
                r_metal = r_op * total_length / 1000.0
                x_metal = x_per_km * total_length / 1000.0
                impedance = complex(r_metal, x_metal)
                current = residual / impedance
                loss = abs(current) ** 2 * r_metal
            total_sheath_loss += loss
            loops.append(
                BondingLoopResult(1, f"Loop {chr(ord('A') + loop_index)}", path, residual, current, impedance, loss, max_minor)
            )

    from ucd.calculations.iec60287 import ac_resistance_at_temperature_ohm_km

    _, rac = ac_resistance_at_temperature_ohm_km(
        cable, cable.max_temperature_c, max(float(bonding.phase_spacing_m), 1e-9)
    )
    total_conductor_loss = 3.0 * cable.design_current_a**2 * rac * total_length / 1000.0
    if total_conductor_loss <= 0:
        raise BondingInputError("Toplam iletken kaybı pozitif olmalı.")
    lambda1 = total_sheath_loss / total_conductor_loss

    standing_profile = (
        _primitive_standing_profile(primitive_result)
        if primitive_result is not None
        else _cumulative_standing_profile(minors, bonding)
    )
    if primitive_result is None:
        max_standing = max((point.voltage_v for point in standing_profile), default=0.0)

    voltage_limit = _positive(
        "Normal sheath standing-voltage kriteri", bonding.normal_sheath_voltage_limit_v
    )
    lead_ok, lead_warnings = _lead_length_status(bonding)
    notes = [
        "Cross-bond yolları link-box bağlantı grafiğinden faz sheath'leri arasında açıkça A→B→C / B→C→A / C→A→B olarak çözülür.",
        "Her minor section EMF'si, güzergâh bölümlerindeki faz aralığı değişimleri parça parça entegre edilerek hesaplanır.",
        "v0.8 PRIMITIVE_CIM/NODE_VOLTAGE modlarında core, sheath, opsiyonel GCC/ECC, bonding lead ve grounding dallarını explicit primitive ağda çözer.",
        "CIM/MNA ve Node-Voltage aynı fiziksel ağ üzerinde bağımsız çözülür ve sonuç farkı doğrulama kapısıdır.",
        "Toprak dönüşü simplified-Carson eşdeğer derinlik yaklaşımıdır; tam Pollaczek/Wedepohl-Wilcox ve wideband EMT hâlâ ayrı doğrulama katmanıdır.",
        "Legacy COUPLED_LOOP_MATRIX ve INDEPENDENT_LOOP_PREVIEW modları regresyon ve hızlı ön inceleme için korunur.",
        "Standing-voltage profili production primitive ağda çözülmüş sheath-to-earth düğüm gerilimlerinden; legacy modlarda ise cross-bond yolu boyunca kümülatif kompleks fazör toplamından üretilir.",
    ]
    notes.extend(lead_warnings)
    if scheme == BONDING_SOLID_BOTH_END:
        notes.append(
            "SOLID_BOTH_END legacy çevrimi: dengeli sistemde dolaşım akımı diğer faz kılıfları üzerinden kapanır; "
            "terminasyon topraklama direnci faz çevrim empedansına eklenmez (Z = Rs·L + jX·L, IEC 60287-1-1 λ1′)."
        )
    if cable.sheath_dc_resistance_20_ohm_km <= 0:
        notes.append("Sheath R20 metal malzemesi ve toplam metalik kesitten türetildi; üretici değeri tercih edilmelidir.")
    if scheme == BONDING_CROSS:
        for major in range(1, major_count + 1):
            group = [m for m in minors if m.major_index == major]
            if not group:
                group = list(minors[(major - 1) * 3:major * 3])
            lengths = [m.length_m for m in group]
            imbalance = (max(lengths) - min(lengths)) / (sum(lengths) / len(lengths)) if lengths else 0.0
            notes.append(f"Major {major} fiziksel minor-section uzunluk dengesizliği = %{imbalance * 100.0:.3f}.")

    max_condition = (
        max((matrix.condition_number for matrix in major_matrices), default=1.0)
        if primitive_result is None
        else max(primitive_result.cim.matrix_condition_number, primitive_result.nv.matrix_condition_number)
    )
    max_current = max((loop.current_magnitude_a for loop in loops), default=0.0)
    max_residual = max((loop.residual_emf_magnitude_v for loop in loops), default=0.0)
    ideal_cancellation = (
        scheme == BONDING_CROSS
        and max_current < 1e-9
        and max_residual < 1e-9
    )
    if ideal_cancellation:
        notes.insert(0,
            "Dolaşım akımı sayısal olarak ideal iptal seviyesindedir; bu, saha toleransları dahil gerçek akımın sıfır olduğu anlamına gelmez."
        )

    trace = (
        f"arrangement={cable.arrangement}",
        f"default_phase_spacing={bonding.phase_spacing_m:.6f} m (legacy fallback only)",
        f"route_geometry_segments={len(list(route_sections or ())) if route_sections is not None else 0}",
        f"sheath_mean_radius={cable.sheath_mean_diameter_mm / 2000.0:.6f} m",
        f"minor_count={len(minors)}",
        f"major_count={major_count}",
        f"link_box_count={len(bonding.link_boxes)}",
    )

    return BondingResult(
        scheme=scheme,
        system_name=bonding.name,
        total_length_m=total_length,
        major_section_count=major_count,
        minor_results=minors,
        loop_results=tuple(loops),
        major_matrix_results=tuple(major_matrices),
        primitive_network_result=primitive_result,
        standing_voltage_profile=standing_profile,
        max_standing_voltage_v=max_standing,
        total_sheath_loss_w=total_sheath_loss,
        total_conductor_loss_w=total_conductor_loss,
        lambda1=lambda1,
        sheath_resistance_20_ohm_km=r20,
        sheath_resistance_operating_ohm_km=r_op,
        sheath_loop_reactance_ohm_km=x_per_km,
        solver_mode=solver_mode,
        maximum_matrix_condition_number=max_condition,
        ideal_cancellation=ideal_cancellation,
        voltage_limit_v=voltage_limit,
        voltage_limit_ok=max_standing <= voltage_limit + 1e-9,
        lead_length_ok=lead_ok,
        notes=tuple(notes),
        trace=trace,
    )


# ---------------------------------------------------------------------------
# Iterative automatic cross-bonding design
# ---------------------------------------------------------------------------

def _route_weight_ranges(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection],
) -> list[tuple[float, float, float]]:
    ranges = _route_ranges(route_sections, 0.0, bonding.phase_spacing_m)
    weighted: list[tuple[float, float, float]] = []
    for start, end, _name, spacing, route in ranges:
        per_m = induced_sheath_voltage_per_m(
            cable, bonding, "ABC", spacing, getattr(route, "phase_positions_m", None) if route is not None else None
        )
        weight = max(abs(per_m[p]) for p in "ABC")
        weighted.append((start, end, weight))
    return weighted


def _position_at_weight(weighted: list[tuple[float, float, float]], target: float) -> float:
    accumulated = 0.0
    for start, end, weight in weighted:
        segment_weight = (end - start) * weight
        if accumulated + segment_weight >= target - 1e-15:
            if weight <= 0:
                return start
            return start + (target - accumulated) / weight
        accumulated += segment_weight
    return weighted[-1][1]


def _equal_electrical_boundaries(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: list[RouteSection],
    minor_count: int,
) -> list[float]:
    weighted = _route_weight_ranges(cable, bonding, route_sections)
    total_weight = sum((end - start) * weight for start, end, weight in weighted)
    if total_weight <= 0:
        total_length = sum(section.length_m for section in route_sections)
        return [total_length * i / minor_count for i in range(minor_count + 1)]
    boundaries = [weighted[0][0]]
    for i in range(1, minor_count):
        boundaries.append(_position_at_weight(weighted, total_weight * i / minor_count))
    boundaries.append(weighted[-1][1])
    return boundaries


def _cyclic_path_residuals(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: list[RouteSection],
    start: float,
    b1: float,
    b2: float,
    end: float,
) -> tuple[list[complex], float]:
    intervals = ((start, b1), (b1, b2), (b2, end))
    values = [
        integrate_minor_voltage(cable, bonding, route_sections, a, b, "ABC")[0]
        for a, b in intervals
    ]
    index = {phase: i for i, phase in enumerate("ABC")}
    paths = (("A", "B", "C"), ("B", "C", "A"), ("C", "A", "B"))
    residuals = [sum(values[i][index[path[i]]] for i in range(3)) for path in paths]
    max_standing = max(abs(v) for item in values for v in item)
    return residuals, max_standing


def _objective(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: list[RouteSection],
    start: float,
    b1: float,
    b2: float,
    end: float,
    limit_v: float,
) -> tuple[float, float, float]:
    if not (start + 1.0 <= b1 <= b2 - 1.0 and b2 <= end - 1.0):
        return float("inf"), float("inf"), float("inf")
    residuals, max_standing = _cyclic_path_residuals(
        cable, bonding, route_sections, start, b1, b2, end
    )
    max_residual = max(abs(value) for value in residuals)
    residual_term = sum((abs(value) / max(limit_v, 1e-9)) ** 2 for value in residuals)
    voltage_term = 0.0 if max_standing <= limit_v else 100.0 * ((max_standing / limit_v) - 1.0) ** 2
    lengths = (b1 - start, b2 - b1, end - b2)
    mean_length = sum(lengths) / 3.0
    length_regularization = 0.002 * sum(((length / mean_length) - 1.0) ** 2 for length in lengths)
    return residual_term + voltage_term + length_regularization, max_standing, max_residual


def _optimize_major_boundaries(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: list[RouteSection],
    major_index: int,
    start: float,
    initial_b1: float,
    initial_b2: float,
    end: float,
    limit_v: float,
) -> tuple[float, float, list[DesignIteration]]:
    b1, b2 = initial_b1, initial_b2
    objective, max_v, max_residual = _objective(
        cable, bonding, route_sections, start, b1, b2, end, limit_v
    )
    step = max((end - start) / 10.0, bonding.optimization_snap_m)
    iterations: list[DesignIteration] = [
        DesignIteration(0, major_index, b1, b2, objective, max_v, max_residual)
    ]
    iteration_no = 0
    max_iterations = max(1, int(bonding.optimization_max_iterations))
    snap = max(0.1, float(bonding.optimization_snap_m))

    while iteration_no < max_iterations and step >= snap / 2.0:
        improved = False
        candidates = [
            (b1 - step, b2), (b1 + step, b2),
            (b1, b2 - step), (b1, b2 + step),
            (b1 - step, b2 - step), (b1 + step, b2 + step),
        ]
        for c1, c2 in candidates:
            c1 = round(c1 / snap) * snap
            c2 = round(c2 / snap) * snap
            candidate = _objective(cable, bonding, route_sections, start, c1, c2, end, limit_v)
            if candidate[0] + 1e-12 < objective:
                b1, b2 = c1, c2
                objective, max_v, max_residual = candidate
                iteration_no += 1
                iterations.append(
                    DesignIteration(iteration_no, major_index, b1, b2, objective, max_v, max_residual)
                )
                improved = True
                break
        if not improved:
            step /= 2.0
            iteration_no += 1
            iterations.append(
                DesignIteration(iteration_no, major_index, b1, b2, objective, max_v, max_residual)
            )
    return b1, b2, iterations


def build_cross_bonding_system(
    boundaries_m: Iterable[float],
    template: BondingSystemData | None = None,
) -> BondingSystemData:
    boundaries = [float(value) for value in boundaries_m]
    if len(boundaries) < 4 or (len(boundaries) - 1) % 3 != 0:
        raise BondingInputError("Cross-bonding sınır listesi üç minor section'ın katlarını oluşturmalı.")
    if any(b <= a for a, b in zip(boundaries, boundaries[1:])):
        raise BondingInputError("Cross-bonding sınırları kesin artan olmalı.")
    base = template or BondingSystemData()
    nodes: list[BondingNode] = []
    link_boxes: list[BondingLinkBox] = []
    minors: list[BondingMinorSection] = []
    connections: list[BondingConnection] = []

    nodes.append(BondingNode("T1", "Başlangıç Terminasyonu", boundaries[0], "TERMINATION", 0.20, True))
    internal_node_ids: list[str] = []
    for i, position in enumerate(boundaries[1:-1], start=1):
        node_id = f"J{i}"
        internal_node_ids.append(node_id)
        major_boundary = i % 3 == 0
        nodes.append(
            BondingNode(
                node_id,
                f"{'Major Ground Joint' if major_boundary else 'Sectionalizing Joint'} {i}",
                position,
                "SECTIONALIZING_JOINT",
                0.20 if major_boundary else 0.0,
                major_boundary,
            )
        )
        link_box_id = f"LB{i}"
        link_boxes.append(
            BondingLinkBox(
                link_box_id,
                f"Link Box {i}",
                node_id,
                position,
                3.0,
                "COAXIAL",
                not major_boundary,
                True,
            )
        )
        if major_boundary:
            for phase in "ABC":
                connections.append(BondingConnection(link_box_id, node_id, phase, "G", "SOLID_GROUND"))
        else:
            connections.extend(
                [
                    BondingConnection(link_box_id, node_id, "A", "B", "CROSS"),
                    BondingConnection(link_box_id, node_id, "B", "C", "CROSS"),
                    BondingConnection(link_box_id, node_id, "C", "A", "CROSS"),
                ]
            )
    nodes.append(BondingNode("T2", "Bitiş Terminasyonu", boundaries[-1], "TERMINATION", 0.20, True))

    ordered_node_ids = ["T1", *internal_node_ids, "T2"]
    for i, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        major_index = (i - 1) // 3 + 1
        minors.append(
            BondingMinorSection(
                f"MS{i}", f"Minor Section {i}", ordered_node_ids[i - 1], ordered_node_ids[i],
                end - start, "ABC", "", major_index,
            )
        )

    return replace(
        base,
        name=f"Cross-Bonding — {len(minors) // 3} Major Section",
        scheme=BONDING_CROSS,
        nodes=nodes,
        link_boxes=link_boxes,
        minor_sections=minors,
        connections=connections,
    )


def optimize_cross_bonding(
    cable: CableData,
    route_sections: Iterable[RouteSection],
    template: BondingSystemData,
    voltage_limit_v: float | None = None,
) -> CrossBondingDesignResult:
    routes = list(route_sections)
    if not routes:
        raise BondingInputError("Otomatik cross-bonding tasarımı için güzergâh bölümü gerekli.")
    limit = template.normal_sheath_voltage_limit_v if voltage_limit_v is None else voltage_limit_v
    limit = _positive("Normal sheath standing-voltage kriteri", limit)

    weighted = _route_weight_ranges(cable, template, routes)
    total_weight = sum((end - start) * weight for start, end, weight in weighted)
    required_minor = max(1, int(ceil(total_weight / limit)))
    minor_count = max(3, int(ceil(required_minor / 3.0)) * 3)
    if minor_count > 60:
        raise BondingInputError(
            f"Gerilim kriteri {minor_count} minor section gerektiriyor; 60 sınırını aşıyor. "
            "Girdileri veya bonding topolojisini gözden geçirin."
        )

    initial = _equal_electrical_boundaries(cable, template, routes, minor_count)
    optimized = list(initial)
    all_iterations: list[DesignIteration] = []
    major_count = minor_count // 3
    for major_zero in range(major_count):
        idx = major_zero * 3
        start, b1, b2, end = optimized[idx:idx + 4]
        ob1, ob2, iterations = _optimize_major_boundaries(
            cable, template, routes, major_zero + 1, start, b1, b2, end, limit
        )
        optimized[idx + 1] = ob1
        optimized[idx + 2] = ob2
        all_iterations.extend(iterations)

    designed = build_cross_bonding_system(optimized, template)
    designed.normal_sheath_voltage_limit_v = limit
    calculation = solve_bonding(cable, designed, routes)
    notes = [
        f"Toplam elektriksel standing-voltage ağırlığı {total_weight:.3f} V; başlangıç minor sayısı {minor_count}.",
        "Başlangıç sınırları eşit fiziksel uzunlukla değil, güzergâh boyunca entegre edilen sheath-voltage gradyanıyla oluşturuldu.",
        "Her major section içinde iki joint konumu, üç faz-sheath loop residual EMF'lerini azaltmak için koordinat aramasıyla iteratif ayarlandı.",
        "Joint/link-box uygulanabilirlik bölgeleri ve makara boyu henüz CAD aday listesiyle sınırlandırılmadı; sonraki CAD bağında sınırlar aday noktalara snap edilecek.",
    ]
    return CrossBondingDesignResult(
        designed,
        calculation,
        minor_count,
        major_count,
        tuple(initial),
        tuple(optimized),
        tuple(all_iterations),
        tuple(notes),
    )
