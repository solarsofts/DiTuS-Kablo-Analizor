"""General N-conductor power-frequency electromagnetic shadow solver.

This module is additive.  It consumes the explicit physical installation model
introduced in v0.16.3 and reuses the validated simplified-Carson primitive
impedance kernel.  Existing IEC 60287, bonding CIM/NV and fault result paths are
not replaced in v0.16.5.

The first production gate solves:

* any number of active single-core physical cables,
* any number of circuits and parallel cables per phase,
* arbitrary x/depth coordinates from ``InstallationCrossSectionData``,
* constrained phase-current sharing between parallel cores,
* open-sheath or local solid-both-end sheath boundary conditions,
* the same physical system by a direct KKT matrix and a Schur-complement
  reduction as an internal agreement gate.

Cross-bonding link boxes over multiple minor sections remain in the locked
``primitive_cim`` network.  Their general N-conductor integration is the next
v0.16.5 sub-gate; this module must therefore remain ``SHADOW_COMPARE``.
"""

from __future__ import annotations

from ucd.calculations.model_applicability import require_production_physics

import cmath
from dataclasses import dataclass, field
from math import pi, sqrt
from typing import Iterable, Mapping

import numpy as np

from ucd.calculations.cable_physical_parameters import (
    PhysicalParameterInputError,
    geometry_dc_resistance_20_ohm_km,
    solve_cable_physical_parameters,
)
from ucd.calculations.installation import (
    InstallationInputError,
    active_cross_section,
    phase_angle_deg,
    resolved_physical_cables,
    validate_installation_design,
)
from ucd.calculations.primitive_cim import (
    PrimitiveConductor,
    primitive_impedance_matrix_ohm_km,
)
from ucd.models.project import (
    CableData,
    InstallationCrossSectionData,
    ProjectData,
    RouteSection,
)


REFERENCE = (
    "IEC 60287-1-3:2023 parallel single-core phase-current and circulating-loss scope; "
    "IEEE 575-2014 / P575 sheath-bonding architecture; CIGRE TB 797; "
    "simplified-Carson primitive impedance at power frequency"
)

MODE_SHADOW_COMPARE = "SHADOW_COMPARE"
SHEATH_OPEN = "OPEN_SHEATH"
SHEATH_SOLID_BOTH_END = "SOLID_BOTH_END_SECTION"
SUPPORTED_SHEATH_MODES = {SHEATH_OPEN, SHEATH_SOLID_BOTH_END}


class MulticonductorEMInputError(ValueError):
    pass


@dataclass(frozen=True)
class MulticonductorEMIssue:
    severity: str
    code: str
    message: str
    object_id: str = ""


@dataclass(frozen=True)
class MulticonductorMethodResult:
    method: str
    core_currents_a: tuple[complex, ...]
    sheath_currents_a: tuple[complex, ...]
    group_voltage_drops_v_km: tuple[complex, ...]
    gcc_current_a: complex
    matrix_condition_number: float
    equation_residual: float
    phase_constraint_residual_a: float
    sheath_voltage_residual_v_km: float


@dataclass(frozen=True)
class MulticonductorCableResult:
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    x_m: float
    depth_m: float
    core_current_a: complex
    sheath_current_a: complex
    equal_share_preview_a: complex
    current_share_percent: float
    core_loss_w_km: float
    sheath_loss_w_km: float
    open_sheath_emf_v_km: complex

    @property
    def current_difference_from_equal_share_a(self) -> float:
        return abs(self.core_current_a - self.equal_share_preview_a)


@dataclass(frozen=True)
class MulticonductorGroupResult:
    group_id: str
    circuit_id: str
    phase: str
    parallel_count: int
    target_current_a: complex
    solved_current_a: complex
    voltage_drop_v_km: complex
    maximum_current_a: float
    minimum_current_a: float
    imbalance_percent: float
    current_sum_residual_a: float


@dataclass
class MulticonductorEMResult:
    cross_section_id: str
    cross_section_name: str
    mode: str
    sheath_mode: str
    reference: str
    conductor_order: tuple[str, ...]
    group_order: tuple[str, ...]
    primitive_impedance_ohm_km: tuple[tuple[complex, ...], ...]
    earth_equivalent_depth_m: float
    direct: MulticonductorMethodResult
    schur: MulticonductorMethodResult
    methods_agree: bool
    maximum_method_current_difference_a: float
    maximum_method_voltage_difference_v_km: float
    cable_results: tuple[MulticonductorCableResult, ...]
    group_results: tuple[MulticonductorGroupResult, ...]
    total_core_loss_w_km: float
    total_sheath_loss_w_km: float
    gcc_current_a: complex
    gcc_loss_w_km: float
    lambda1: float
    maximum_equal_share_difference_a: float
    maximum_current_imbalance_percent: float
    issues: tuple[MulticonductorEMIssue, ...] = ()
    trace: tuple[str, ...] = ()

    @property
    def core_count(self) -> int:
        return len(self.cable_results)

    @property
    def sheath_count(self) -> int:
        return len(self.cable_results)

    @property
    def final_design_ready(self) -> bool:
        return False

    def trace_lines(self) -> list[str]:
        lines = [
            "DiTuS v0.16.5 — Genel N-İletken EM Motoru (SHADOW_COMPARE)",
            f"Kesit: {self.cross_section_id} — {self.cross_section_name}",
            f"Kılıf sınır koşulu: {self.sheath_mode}",
            f"Primitive iletken: {len(self.conductor_order)}; core={self.core_count}; sheath={self.sheath_count}",
            f"KKT cond={self.direct.matrix_condition_number:.6g}; residual={self.direct.equation_residual:.3e}",
            f"Schur cond={self.schur.matrix_condition_number:.6g}; residual={self.schur.equation_residual:.3e}",
            f"Yöntem farkı: ΔI={self.maximum_method_current_difference_a:.6e} A; "
            f"ΔV={self.maximum_method_voltage_difference_v_km:.6e} V/km",
            f"Core kaybı={self.total_core_loss_w_km:.6f} W/km; "
            f"kılıf kaybı={self.total_sheath_loss_w_km:.6f} W/km; λ1={self.lambda1:.8f}",
            f"GCC/ECC akımı={abs(self.gcc_current_a):.6f} A; kaybı={self.gcc_loss_w_km:.6f} W/km",
            f"Maks. eşit-pay farkı={self.maximum_equal_share_difference_a:.6f} A; "
            f"maks. grup dengesizliği=%{self.maximum_current_imbalance_percent:.6f}",
        ]
        lines.extend(self.trace)
        lines.extend(f"{issue.severity} {issue.code}: {issue.message}" for issue in self.issues)
        return lines


@dataclass(frozen=True)
class _PhysicalCablePrimitive:
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    x_m: float
    depth_m: float
    core: PrimitiveConductor
    sheath: PrimitiveConductor


@dataclass(frozen=True)
class _LinearSolution:
    core: np.ndarray
    sheath: np.ndarray
    gcc: complex
    voltage: np.ndarray
    condition: float
    residual: float
    constraint_residual: float
    sheath_residual: float


def _angle_deg(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


def _route_for_section(project: ProjectData, section: InstallationCrossSectionData) -> RouteSection:
    region_ids = set(section.region_ids)
    for route in project.route_sections:
        if route.thermal_region_id and route.thermal_region_id in region_ids:
            return route
    if project.route_sections:
        return project.route_sections[0]
    raise MulticonductorEMInputError("N-iletken çözümü için güzergâh bölümü bulunmuyor.")


def _validate_snapshot(project: ProjectData, value: str, physical_id: str) -> None:
    key = str(value or "").strip()
    if not key:
        return
    accepted = {
        str(project.cable.snapshot_id or "").strip(),
        str(project.cable.cable_id or "").strip(),
    }
    accepted.discard("")
    if key not in accepted:
        raise MulticonductorEMInputError(
            f"{physical_id}: cable_snapshot_id={key!r} aktif proje kablosuyla çözülemiyor. "
            "Çoklu kablo snapshot registry sonraki kapıdır; sessiz kablo eşleştirmesi yapılmadı."
        )


def _sheath_resistance_at_temperature(cable: CableData, r20_ohm_km: float) -> float:
    return float(r20_ohm_km) * (
        1.0
        + max(0.0, float(cable.sheath_temperature_coefficient_20_per_c))
        * (float(cable.sheath_operating_temperature_c) - 20.0)
    )


def _build_primitives(
    project: ProjectData,
    section: InstallationCrossSectionData,
    *,
    core_temperature_c_by_physical_id: Mapping[str, float] | None = None,
    sheath_temperature_c_by_physical_id: Mapping[str, float] | None = None,
    gcc_temperature_c: float | None = None,
) -> tuple[tuple[_PhysicalCablePrimitive, ...], PrimitiveConductor | None, tuple[MulticonductorEMIssue, ...], tuple[str, ...]]:
    validation_errors = [
        issue for issue in validate_installation_design(project)
        if issue.severity == "ERROR" and issue.cross_section_id == section.cross_section_id
    ]
    if validation_errors:
        detail = "; ".join(f"{item.code}: {item.message}" for item in validation_errors[:8])
        raise MulticonductorEMInputError(f"Fiziksel kesit doğrulaması başarısız: {detail}")

    route = _route_for_section(project, section)
    try:
        params = solve_cable_physical_parameters(
            project.cable,
            route,
            target_temperature_c=project.cable.max_temperature_c,
            mode="PRODUCTION_PHYSICAL_RAC",
        )
    except (PhysicalParameterInputError, InstallationInputError) as exc:
        raise MulticonductorEMInputError(str(exc)) from exc

    issues: list[MulticonductorEMIssue] = []
    if params.supported_for_ac_resistance and params.physical_ac_resistance_ohm_km > 0.0:
        core_r = params.physical_ac_resistance_ohm_km
        core_r_source = "IEC_60287_CONSTRUCTION_RAC"
    else:
        core_r = params.legacy_ac_resistance_ohm_km
        core_r_source = "LEGACY_RAC_FALLBACK"
        issues.append(MulticonductorEMIssue(
            "WARNING",
            "PHYSICAL_RAC_UNAVAILABLE",
            "İletken yapısı için IEC ks/kp Rac çözülemedi; legacy ys/yp yalnız açık uyumluluk fallback olarak kullanıldı.",
        ))

    core_temperature_map = {str(key): float(value) for key, value in (core_temperature_c_by_physical_id or {}).items()}
    sheath_temperature_map = {str(key): float(value) for key, value in (sheath_temperature_c_by_physical_id or {}).items()}
    temperature_feedback_active = bool(core_temperature_map or sheath_temperature_map or gcc_temperature_c is not None)
    temperature_parameter_cache: dict[float, tuple[float, str]] = {}

    def core_resistance_at_temperature(temperature_c: float) -> tuple[float, str]:
        evaluation_temperature = max(-273.149999, min(float(temperature_c), project.cable.max_temperature_c + 120.0))
        cache_key = round(evaluation_temperature, 8)
        cached = temperature_parameter_cache.get(cache_key)
        if cached is not None:
            return cached
        local = solve_cable_physical_parameters(
            project.cable,
            route,
            target_temperature_c=evaluation_temperature,
            mode="PRODUCTION_PHYSICAL_RAC",
        )
        if local.supported_for_ac_resistance and local.physical_ac_resistance_ohm_km > 0.0:
            value = (float(local.physical_ac_resistance_ohm_km), "TEMPERATURE_DEPENDENT_PHYSICAL_RAC")
        else:
            value = (float(local.legacy_ac_resistance_ohm_km), "TEMPERATURE_DEPENDENT_LEGACY_RAC_FALLBACK")
        temperature_parameter_cache[cache_key] = value
        return value
    issues.append(MulticonductorEMIssue(
        "WARNING",
        "ARBITRARY_XY_PROXIMITY_NOT_YET_GENERALIZED",
        "Rac içindeki proximity faktörü v0.16.4 güzergâh faz aralığına dayanır; gerçek N-kablo x-y proximity kaybı henüz genelleştirilmedi.",
    ))

    sheath_r = _sheath_resistance_at_temperature(project.cable, params.sheath_resistance_basis_ohm_km)
    if sheath_r <= 0.0:
        raise MulticonductorEMInputError("Metalik kılıf direnci çözülemedi.")
    core_gmr = params.conductor_gmr_mm / 1000.0
    sheath_gmr = params.sheath_gmr_mm / 1000.0
    if core_gmr <= 0.0 or sheath_gmr <= 0.0:
        raise MulticonductorEMInputError("Core/sheath GMR sıfırdan büyük olmalıdır.")

    circuits = {item.circuit_id: item for item in section.circuits if item.active}
    records: list[_PhysicalCablePrimitive] = []
    for physical in section.physical_cables:
        if not physical.active:
            continue
        circuit = circuits.get(physical.circuit_id)
        if circuit is None:
            continue
        phase = str(physical.phase).strip().upper()
        if phase not in {"A", "B", "C"}:
            raise MulticonductorEMInputError(f"{physical.physical_cable_id}: faz A/B/C olmalı.")
        _validate_snapshot(project, physical.cable_snapshot_id or circuit.cable_snapshot_id, physical.physical_cable_id)
        if float(physical.depth_m) <= 0.0:
            raise MulticonductorEMInputError(f"{physical.physical_cable_id}: derinlik sıfırdan büyük olmalı.")
        physical_id = str(physical.physical_cable_id)
        local_core_r = core_r
        if temperature_feedback_active:
            core_temperature = core_temperature_map.get(physical_id, project.cable.max_temperature_c)
            local_core_r, _local_source = core_resistance_at_temperature(core_temperature)
        local_sheath_temperature = sheath_temperature_map.get(
            physical_id, float(project.cable.sheath_operating_temperature_c)
        )
        local_sheath_temperature = max(-273.149999, min(local_sheath_temperature, project.cable.max_temperature_c + 120.0))
        local_sheath_r = float(params.sheath_resistance_basis_ohm_km) * (
            1.0
            + max(0.0, float(project.cable.sheath_temperature_coefficient_20_per_c))
            * (local_sheath_temperature - 20.0)
        )
        if local_sheath_r <= 0.0:
            raise MulticonductorEMInputError(
                f"{physical.physical_cable_id}: kılıf sıcaklık düzeltmesi sıfır/negatif direnç üretti "
                f"({local_sheath_r:.9g} ohm/km, T={local_sheath_temperature:.6g} °C)."
            )
        core = PrimitiveConductor(
            f"C:{physical.physical_cable_id}", "CORE", phase,
            float(physical.x_m), float(physical.depth_m), core_gmr, local_core_r,
        )
        sheath = PrimitiveConductor(
            f"S:{physical.physical_cable_id}", "SHEATH", phase,
            float(physical.x_m), float(physical.depth_m), sheath_gmr, local_sheath_r,
        )
        records.append(_PhysicalCablePrimitive(
            physical.physical_cable_id,
            physical.circuit_id,
            phase,
            int(physical.parallel_index),
            float(physical.x_m),
            float(physical.depth_m),
            core,
            sheath,
        ))
    if not records:
        raise MulticonductorEMInputError("Aktif fiziksel kablo bulunmuyor.")

    gcc: PrimitiveConductor | None = None
    if bool(getattr(project.bonding, "gcc_enabled", False)):
        area = max(0.0, float(getattr(project.bonding, "gcc_area_mm2", 0.0)))
        if area <= 0.0:
            raise MulticonductorEMInputError("GCC/ECC etkin ancak kesiti sıfır veya eksik.")
        r20 = max(0.0, float(getattr(project.bonding, "gcc_dc_resistance_20_ohm_km", 0.0)))
        if r20 <= 0.0:
            try:
                r20 = geometry_dc_resistance_20_ohm_km(project.bonding.gcc_material, area)
            except PhysicalParameterInputError as exc:
                raise MulticonductorEMInputError(str(exc)) from exc
            issues.append(MulticonductorEMIssue(
                "WARNING", "GCC_RDC_GEOMETRY_DERIVED",
                "GCC/ECC üretici/test direnci yok; kesit ve malzemeden ön direnç türetildi.", "GCC"
            ))
        gcc_eval_temperature = (
            float(gcc_temperature_c)
            if gcc_temperature_c is not None
            else float(project.bonding.gcc_operating_temperature_c)
        )
        gcc_eval_temperature = max(-273.149999, min(gcc_eval_temperature, project.cable.max_temperature_c + 120.0))
        gcc_r = r20 * (
            1.0 + max(0.0, float(project.bonding.gcc_temperature_coefficient_20_per_c))
            * (gcc_eval_temperature - 20.0)
        )
        if gcc_r <= 0.0:
            raise MulticonductorEMInputError(
                f"GCC/ECC sıcaklık düzeltmesi sıfır/negatif direnç üretti "
                f"({gcc_r:.9g} ohm/km, T={gcc_eval_temperature:.6g} °C)."
            )
        gcc_gmr = max(0.0, float(project.bonding.gcc_gmr_mm)) / 1000.0
        if gcc_gmr <= 0.0:
            gcc_gmr = 0.7788 * sqrt(area * 1e-6 / pi)
        mean_depth = sum(item.depth_m for item in records) / len(records)
        gcc = PrimitiveConductor(
            "GCC", "GCC", "G", float(project.bonding.gcc_x_offset_m),
            max(0.05, mean_depth + float(project.bonding.gcc_depth_offset_m)),
            gcc_gmr, gcc_r,
        )
        issues.append(MulticonductorEMIssue(
            "INFO", "LOCAL_GCC_BOUNDARY",
            "GCC/ECC, SOLID_BOTH_END_SECTION modunda yerel iki uçtan sıfır boyuna gerilim sınırıyla çözülür.", "GCC"
        ))

    resolved_core_values = [item.core.resistance_ohm_km for item in records]
    resolved_sheath_values = [item.sheath.resistance_ohm_km for item in records]
    trace = (
        f"route={route.name}",
        f"core_rac={core_r:.9f} ohm/km ({core_r_source})",
        f"core_rac_range={min(resolved_core_values):.9f}..{max(resolved_core_values):.9f} ohm/km; temperature_feedback={temperature_feedback_active}",
        f"sheath_r={sheath_r:.9f} ohm/km @ {project.cable.sheath_operating_temperature_c:.2f} C",
        f"sheath_r_range={min(resolved_sheath_values):.9f}..{max(resolved_sheath_values):.9f} ohm/km",
        f"gmr_core/sheath={core_gmr:.9f}/{sheath_gmr:.9f} m",
        f"gcc_enabled={gcc is not None}",
    )
    return tuple(records), gcc, tuple(issues), trace


def _groups(
    section: InstallationCrossSectionData,
    records: tuple[_PhysicalCablePrimitive, ...],
) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
    active_circuits = [item for item in section.circuits if item.active]
    record_keys = {(item.circuit_id, item.phase) for item in records}
    group_keys: list[tuple[str, str]] = []
    targets: list[complex] = []
    for circuit in active_circuits:
        for phase in "ABC":
            if (circuit.circuit_id, phase) not in record_keys:
                raise MulticonductorEMInputError(
                    f"{circuit.circuit_id}:{phase} için aktif fiziksel kablo bulunmuyor."
                )
            magnitude = max(0.0, float(circuit.load_current_a))
            targets.append(cmath.rect(magnitude, phase_angle_deg(phase) * pi / 180.0))
            group_keys.append((circuit.circuit_id, phase))
    if not group_keys:
        raise MulticonductorEMInputError("Aktif devre/faz grubu bulunmuyor.")
    b = np.zeros((len(records), len(group_keys)), dtype=complex)
    lookup = {key: index for index, key in enumerate(group_keys)}
    for row, record in enumerate(records):
        b[row, lookup[(record.circuit_id, record.phase)]] = 1.0
    labels = tuple(f"{circuit}:{phase}" for circuit, phase in group_keys)
    return labels, b, np.asarray(targets, dtype=complex)


def _relative_residual(matrix: np.ndarray, x: np.ndarray, rhs: np.ndarray) -> float:
    return float(np.linalg.norm(matrix @ x - rhs) / max(float(np.linalg.norm(rhs)), 1.0))


def _condition(matrix: np.ndarray) -> float:
    return float(np.linalg.cond(matrix))


def _direct_solution(
    zcc: np.ndarray,
    zcu: np.ndarray,
    zuc: np.ndarray,
    zuu: np.ndarray,
    b: np.ndarray,
    target: np.ndarray,
    sheath_mode: str,
    sheath_count: int,
) -> _LinearSolution:
    nc = zcc.shape[0]
    nu = zuu.shape[0]
    ng = b.shape[1]
    if sheath_mode == SHEATH_OPEN:
        matrix = np.block([
            [zcc, -b],
            [b.T, np.zeros((ng, ng), dtype=complex)],
        ])
        rhs = np.concatenate([np.zeros(nc, dtype=complex), target])
        x = np.linalg.solve(matrix, rhs)
        core = x[:nc]
        metallic = np.zeros(nu, dtype=complex)
        voltage = x[nc:]
        sheath_residual = 0.0
    else:
        matrix = np.block([
            [zcc, zcu, -b],
            [zuc, zuu, np.zeros((nu, ng), dtype=complex)],
            [b.T, np.zeros((ng, nu), dtype=complex), np.zeros((ng, ng), dtype=complex)],
        ])
        rhs = np.concatenate([np.zeros(nc + nu, dtype=complex), target])
        x = np.linalg.solve(matrix, rhs)
        core = x[:nc]
        metallic = x[nc:nc + nu]
        voltage = x[nc + nu:]
        sheath_residual = float(np.linalg.norm(zuc @ core + zuu @ metallic))
    constraint = float(np.max(np.abs(b.T @ core - target)))
    gcc = complex(metallic[sheath_count]) if nu > sheath_count else 0j
    return _LinearSolution(
        core,
        metallic[:sheath_count],
        gcc,
        voltage,
        _condition(matrix),
        _relative_residual(matrix, x, rhs),
        constraint,
        sheath_residual,
    )

def _schur_solution(
    zcc: np.ndarray,
    zcu: np.ndarray,
    zuc: np.ndarray,
    zuu: np.ndarray,
    b: np.ndarray,
    target: np.ndarray,
    sheath_mode: str,
    sheath_count: int,
) -> _LinearSolution:
    nc = zcc.shape[0]
    nu = zuu.shape[0]
    if sheath_mode == SHEATH_OPEN:
        zeff = zcc
        zuu_inv_zuc = np.zeros_like(zuc)
    else:
        zuu_inv_zuc = np.linalg.solve(zuu, zuc)
        zeff = zcc - zcu @ zuu_inv_zuc
    zeff_inv_b = np.linalg.solve(zeff, b)
    group_matrix = b.T @ zeff_inv_b
    voltage = np.linalg.solve(group_matrix, target)
    core = zeff_inv_b @ voltage
    metallic = np.zeros(nu, dtype=complex) if sheath_mode == SHEATH_OPEN else -(zuu_inv_zuc @ core)
    constraint = float(np.max(np.abs(b.T @ core - target)))
    sheath_residual = float(np.linalg.norm(zuc @ core + zuu @ metallic)) if sheath_mode != SHEATH_OPEN else 0.0
    equation = zeff @ core - b @ voltage
    residual = float(np.linalg.norm(equation) / max(float(np.linalg.norm(b @ voltage)), 1.0))
    gcc = complex(metallic[sheath_count]) if nu > sheath_count else 0j
    return _LinearSolution(
        core,
        metallic[:sheath_count],
        gcc,
        voltage,
        _condition(group_matrix),
        residual,
        constraint,
        sheath_residual,
    )

def _method_result(name: str, value: _LinearSolution) -> MulticonductorMethodResult:
    return MulticonductorMethodResult(
        name,
        tuple(complex(item) for item in value.core),
        tuple(complex(item) for item in value.sheath),
        tuple(complex(item) for item in value.voltage),
        complex(value.gcc),
        value.condition,
        value.residual,
        value.constraint_residual,
        value.sheath_residual,
    )


def solve_multiconductor_em(
    project: ProjectData,
    *,
    cross_section_id: str = "",
    sheath_mode: str = SHEATH_SOLID_BOTH_END,
) -> MulticonductorEMResult:
    """Solve the active physical cross-section without replacing locked engines."""

    try:
        require_production_physics(project.cable, engine_label="çok iletkenli EM")
    except ValueError as exc:
        raise MulticonductorEMInputError(str(exc)) from exc

    mode = str(sheath_mode).strip().upper()
    if mode not in SUPPORTED_SHEATH_MODES:
        raise MulticonductorEMInputError(f"Desteklenmeyen kılıf sınır koşulu: {sheath_mode}")

    if cross_section_id:
        section = next(
            (item for item in project.installation_design.cross_sections if item.cross_section_id == cross_section_id),
            None,
        )
        if section is None:
            raise MulticonductorEMInputError(f"Fiziksel kesit bulunamadı: {cross_section_id}")
    else:
        section = active_cross_section(project)

    if any(float(item.current_override_a) > 0.0 or item.current_angle_override_deg is not None for item in section.physical_cables if item.active):
        raise MulticonductorEMInputError(
            "Standalone kesit EM önizlemesi fiziksel kablo override'larını çözmez; "
            "FAZ 6 production global network kullanılmalıdır."
        )

    records, gcc, issues, trace = _build_primitives(project, section)
    group_labels, b, target = _groups(section, records)
    conductors = tuple(item.core for item in records) + tuple(item.sheath for item in records) + ((gcc,) if gcc is not None else ())
    try:
        z, de = primitive_impedance_matrix_ohm_km(
            conductors,
            project.cable.frequency_hz,
            project.bonding.earth_resistivity_ohm_m,
            project.cable.sheath_mean_diameter_mm / 2000.0,
        )
    except ValueError as exc:
        raise MulticonductorEMInputError(str(exc)) from exc

    nc = len(records)
    zcc = z[:nc, :nc]
    zcu = z[:nc, nc:]
    zuc = z[nc:, :nc]
    zuu = z[nc:, nc:]
    try:
        direct_value = _direct_solution(zcc, zcu, zuc, zuu, b, target, mode, nc)
        schur_value = _schur_solution(zcc, zcu, zuc, zuu, b, target, mode, nc)
    except np.linalg.LinAlgError as exc:
        raise MulticonductorEMInputError(f"N-iletken matris çözümü tekil/kararsız: {exc}") from exc

    di = float(max(
        np.max(np.abs(direct_value.core - schur_value.core)),
        np.max(np.abs(direct_value.sheath - schur_value.sheath)),
        abs(direct_value.gcc - schur_value.gcc),
    ))
    dv = float(np.max(np.abs(direct_value.voltage - schur_value.voltage)))
    tolerance_i = max(1e-7, 1e-8 * max(float(np.max(np.abs(direct_value.core))), 1.0))
    tolerance_v = max(1e-7, 1e-8 * max(float(np.max(np.abs(direct_value.voltage))), 1.0))

    equal_map = {
        item.physical_cable_id: cmath.rect(item.current_a, item.current_angle_deg * pi / 180.0)
        for item in resolved_physical_cables(section)
    }
    group_index = {label: index for index, label in enumerate(group_labels)}
    group_members: dict[str, list[int]] = {label: [] for label in group_labels}
    for index, record in enumerate(records):
        group_members[f"{record.circuit_id}:{record.phase}"].append(index)

    open_emf_all = zuc @ direct_value.core
    open_emf = open_emf_all[:nc]
    core_r = np.asarray([item.core.resistance_ohm_km for item in records], dtype=float)
    sheath_r = np.asarray([item.sheath.resistance_ohm_km for item in records], dtype=float)
    cable_results: list[MulticonductorCableResult] = []
    total_core_loss = 0.0
    total_sheath_loss = 0.0
    max_equal_delta = 0.0
    for index, record in enumerate(records):
        group_id = f"{record.circuit_id}:{record.phase}"
        members = group_members[group_id]
        denominator = sum(abs(direct_value.core[item]) for item in members)
        share = 100.0 * abs(direct_value.core[index]) / denominator if denominator > 1e-15 else 0.0
        pc = abs(direct_value.core[index]) ** 2 * core_r[index]
        ps = abs(direct_value.sheath[index]) ** 2 * sheath_r[index]
        preview = equal_map.get(record.physical_cable_id, 0j)
        max_equal_delta = max(max_equal_delta, abs(direct_value.core[index] - preview))
        total_core_loss += pc
        total_sheath_loss += ps
        cable_results.append(MulticonductorCableResult(
            record.physical_cable_id,
            record.circuit_id,
            record.phase,
            record.parallel_index,
            record.x_m,
            record.depth_m,
            complex(direct_value.core[index]),
            complex(direct_value.sheath[index]),
            complex(preview),
            float(share),
            float(pc),
            float(ps),
            complex(open_emf[index]),
        ))

    group_results: list[MulticonductorGroupResult] = []
    max_imbalance = 0.0
    for group_id, members in group_members.items():
        gidx = group_index[group_id]
        magnitudes = [abs(direct_value.core[index]) for index in members]
        mean = sum(magnitudes) / len(magnitudes)
        imbalance = 100.0 * (max(magnitudes) - min(magnitudes)) / mean if mean > 1e-15 else 0.0
        max_imbalance = max(max_imbalance, imbalance)
        solved_sum = sum((direct_value.core[index] for index in members), 0j)
        circuit_id, phase = group_id.split(":", 1)
        group_results.append(MulticonductorGroupResult(
            group_id,
            circuit_id,
            phase,
            len(members),
            complex(target[gidx]),
            complex(solved_sum),
            complex(direct_value.voltage[gidx]),
            float(max(magnitudes)),
            float(min(magnitudes)),
            float(imbalance),
            float(abs(solved_sum - target[gidx])),
        ))

    gcc_loss = abs(direct_value.gcc) ** 2 * gcc.resistance_ohm_km if gcc is not None else 0.0
    lambda1 = total_sheath_loss / total_core_loss if total_core_loss > 1e-15 else 0.0
    additional_issues = list(issues)
    additional_issues.extend([
        MulticonductorEMIssue(
            "INFO",
            "SHADOW_ONLY",
            "Sonuç mevcut bonding/IEC/nodal motorlara aktarılmaz; proje sonuçlarını veya λ1 girdisini değiştirmez.",
        ),
        MulticonductorEMIssue(
            "INFO",
            "LOCAL_SECTION_BOUNDARY",
            "SOLID_BOTH_END_SECTION yerel, uniform kesit sınır koşuludur; explicit cross-bonding link-box grafiğinin yerine geçmez.",
        ),
    ])
    if mode == SHEATH_OPEN:
        additional_issues.append(MulticonductorEMIssue(
            "INFO", "OPEN_SHEATH_BOUNDARY", "Kılıf boyuna akımları sıfır kabul edildi; açık-devre indüklenen EMF ayrıca raporlandı."
        ))

    trace_full = tuple(trace) + (
        f"section={section.cross_section_id}",
        f"core_count={nc}; group_count={len(group_labels)}",
        f"earth_rho={project.bonding.earth_resistivity_ohm_m:.6g} ohm.m; De={de:.6f} m",
        "core_order=" + ",".join(item.core.name for item in records),
        "sheath_order=" + ",".join(item.sheath.name for item in records),
    )

    return MulticonductorEMResult(
        section.cross_section_id,
        section.name,
        MODE_SHADOW_COMPARE,
        mode,
        REFERENCE,
        tuple(item.name for item in conductors),
        group_labels,
        tuple(tuple(complex(value) for value in row) for row in z),
        float(de),
        _method_result("DIRECT_KKT", direct_value),
        _method_result("SCHUR_COMPLEMENT", schur_value),
        di <= tolerance_i and dv <= tolerance_v,
        di,
        dv,
        tuple(cable_results),
        tuple(group_results),
        float(total_core_loss),
        float(total_sheath_loss),
        complex(direct_value.gcc),
        float(gcc_loss),
        float(lambda1),
        float(max_equal_delta),
        float(max_imbalance),
        tuple(additional_issues),
        trace_full,
    )


def render_multiconductor_em(result: MulticonductorEMResult) -> str:
    lines = result.trace_lines()
    lines.extend(["", "Fiziksel kablo sonuçları:"])
    for item in result.cable_results:
        lines.append(
            f"{item.physical_cable_id}: Ic={abs(item.core_current_a):.6f}∠{_angle_deg(item.core_current_a):.3f}° A; "
            f"pay=%{item.current_share_percent:.4f}; "
            f"Ish={abs(item.sheath_current_a):.6f}∠{_angle_deg(item.sheath_current_a):.3f}° A; "
            f"Pc/Psh={item.core_loss_w_km:.6f}/{item.sheath_loss_w_km:.6f} W/km; "
            f"Eopen={abs(item.open_sheath_emf_v_km):.6f}∠{_angle_deg(item.open_sheath_emf_v_km):.3f}° V/km"
        )
    lines.extend(["", "Devre/faz toplamları:"])
    for item in result.group_results:
        lines.append(
            f"{item.group_id}: hedef={abs(item.target_current_a):.6f} A; "
            f"toplam={abs(item.solved_current_a):.6f} A; residual={item.current_sum_residual_a:.3e} A; "
            f"dengesizlik=%{item.imbalance_percent:.6f}"
        )
    return "\n".join(lines)
