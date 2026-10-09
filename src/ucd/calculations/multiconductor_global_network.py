"""Global N-core / N-sheath route-network shadow solver.

v0.16.5.2 adds one global core-current vector to the explicit sheath/link-box
network introduced in v0.16.5.1.  Parallel cores are therefore continuous over
all route/minor-section blocks and their current sharing is solved together
with the actual sheath, link-box, earth and optional GCC/ECC network.

This module is additive and SHADOW_COMPARE only.  It does not write project
lambda1, replace the locked production primitive-CIM path, or feed IEC/thermal
results.
"""

from __future__ import annotations

from ucd.calculations.model_applicability import require_production_physics

import cmath
from dataclasses import dataclass
from math import pi
from typing import Mapping

import numpy as np
from scipy.sparse import bmat, csc_matrix
from scipy.sparse.linalg import spsolve

from ucd.calculations.multiconductor_bonding_network import (
    ENGINE_MODE,
    REFERENCE,
    MulticonductorBondingBranchResult,
    MulticonductorBondingInputError,
    MulticonductorBondingIssue,
    MulticonductorBondingSectionResult,
    MulticonductorSheathResult,
    _SheathKey,
    _assemble,
    _build_network,
    _condition,
    _minor_bounds,
    _node_positions,
    _pairwise_max,
    _section_key_maps,
)
from ucd.calculations.multiconductor_em import (
    MulticonductorEMInputError,
    _build_primitives,
)
from ucd.calculations.primitive_cim import primitive_impedance_matrix_ohm_km
from ucd.models.project import InstallationCrossSectionData, ProjectData


CORE_SHARING_MODE = "GLOBAL_CORE_CONTINUITY_COUPLED_SHEATH"


class MulticonductorGlobalInputError(ValueError):
    pass


@dataclass(frozen=True)
class GlobalNetworkMethodResult:
    method: str
    core_currents_a: tuple[complex, ...]
    sheath_node_voltages_v: tuple[complex, ...]
    branch_currents_a: tuple[complex, ...]
    group_voltage_drops_v: tuple[complex, ...]
    matrix_condition_number: float
    equation_residual: float
    phase_constraint_residual_a: float
    sheath_kcl_residual_a: float
    sheath_branch_residual_v: float
    core_voltage_residual_v: float


@dataclass(frozen=True)
class GlobalCoreCableResult:
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    core_current_a: complex
    equal_share_current_a: complex
    current_share_percent: float
    current_difference_from_equal_share_a: float
    route_voltage_drop_v: complex
    core_metal_loss_w: float

    @property
    def key(self) -> str:
        return f"{self.circuit_id}:{self.phase}:P{self.parallel_index}"


@dataclass(frozen=True)
class GlobalCoreGroupResult:
    group_id: str
    circuit_id: str
    phase: str
    parallel_count: int
    target_current_a: complex
    solved_current_a: complex
    route_voltage_drop_v: complex
    maximum_current_a: float
    minimum_current_a: float
    imbalance_percent: float
    current_sum_residual_a: float


@dataclass(frozen=True)
class GlobalMatrixBlock:
    section_id: str
    cross_section_id: str
    start_m: float
    end_m: float
    length_m: float
    core_order: tuple[str, ...]
    unknown_order: tuple[str, ...]
    zcc_ohm: tuple[tuple[complex, ...], ...]
    zcu_ohm: tuple[tuple[complex, ...], ...]
    zuc_ohm: tuple[tuple[complex, ...], ...]
    zuu_ohm: tuple[tuple[complex, ...], ...]
    core_resistance_ohm: tuple[float, ...]
    unknown_metallic_resistance_ohm: tuple[float, ...]


@dataclass
class GlobalMulticonductorNetworkResult:
    mode: str
    core_sharing_mode: str
    reference: str
    core_order: tuple[str, ...]
    sheath_order: tuple[str, ...]
    group_order: tuple[str, ...]
    matrix_blocks: tuple[GlobalMatrixBlock, ...]
    core_results: tuple[GlobalCoreCableResult, ...]
    group_results: tuple[GlobalCoreGroupResult, ...]
    section_results: tuple[MulticonductorBondingSectionResult, ...]
    accessory_branches: tuple[MulticonductorBondingBranchResult, ...]
    direct: GlobalNetworkMethodResult
    reduced: GlobalNetworkMethodResult
    selected_method: str
    methods_agree: bool
    maximum_method_core_current_difference_a: float
    maximum_method_sheath_current_difference_a: float
    maximum_method_voltage_difference_v: float
    maximum_core_current_imbalance_percent: float
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
    production_mode: bool = False

    @property
    def final_design_ready(self) -> bool:
        return bool(self.production_mode and self.methods_agree)

    def trace_lines(self) -> list[str]:
        label = "Üretim Global N-Core + N-Kılıf Ağı" if self.production_mode else "Global N-Core + N-Kılıf Ağı (SHADOW_COMPARE)"
        lines = [
            f"DiTuS — {label}",
            f"Referans: {self.reference}",
            f"Core paylaşım modu: {self.core_sharing_mode}",
            f"Core/kılıf/grup: {len(self.core_order)}/{len(self.sheath_order)}/{len(self.group_order)}",
            f"Düğüm/dal akımı: {self.node_count}/{self.branch_current_count}",
            f"Direct cond={self.direct.matrix_condition_number:.6g}; residual={self.direct.equation_residual:.3e}; "
            f"IΣ={self.direct.phase_constraint_residual_a:.3e}; KCL={self.direct.sheath_kcl_residual_a:.3e}",
            f"Reduced cond={self.reduced.matrix_condition_number:.6g}; residual={self.reduced.equation_residual:.3e}; "
            f"IΣ={self.reduced.phase_constraint_residual_a:.3e}; KCL={self.reduced.sheath_kcl_residual_a:.3e}",
            f"Direct↔Reduced: ΔIc={self.maximum_method_core_current_difference_a:.6e} A; "
            f"ΔIsh={self.maximum_method_sheath_current_difference_a:.6e} A; "
            f"ΔV={self.maximum_method_voltage_difference_v:.6e} V",
            f"Pcore/Psheath/Pgcc/Pearth/Pacc={self.total_core_metal_loss_w:.6f}/"
            f"{self.total_sheath_metal_loss_w:.6f}/{self.total_gcc_metal_loss_w:.6f}/"
            f"{self.total_earth_return_equivalent_loss_w:.6f}/{self.total_accessory_loss_w:.6f} W",
            f"λ1({'üretim fiziksel kayıp oranı' if self.production_mode else 'global shadow'})={self.lambda1:.8f}; core dengesizliği maks.=%{self.maximum_core_current_imbalance_percent:.6f}",
        ]
        for group in self.group_results:
            lines.append(
                f"{group.group_id}: hedef={abs(group.target_current_a):.6f} A; "
                f"çözülen={abs(group.solved_current_a):.6f} A; residual={group.current_sum_residual_a:.3e} A; "
                f"ΔV={abs(group.route_voltage_drop_v):.6f} V; dengesizlik=%{group.imbalance_percent:.6f}"
            )
        lines.extend(self.trace)
        lines.extend(f"{item.severity} {item.code}: {item.message}" for item in self.issues)
        return lines


@dataclass(frozen=True)
class _FullKernel:
    zcc: np.ndarray
    zcu: np.ndarray
    zuc: np.ndarray
    zuu: np.ndarray
    core_r_ohm_km: tuple[float, ...]
    unknown_r_ohm_km: tuple[float, ...]
    core_labels: tuple[str, ...]
    unknown_labels: tuple[str, ...]
    physical_ids: dict[str, str]


@dataclass(frozen=True)
class _IntegratedSection:
    zcc: np.ndarray
    zcu: np.ndarray
    zuc: np.ndarray
    zuu: np.ndarray
    core_r_ohm: tuple[float, ...]
    unknown_r_ohm: tuple[float, ...]


@dataclass(frozen=True)
class _GlobalModel:
    sheath_model: object
    a: csc_matrix
    z: csc_matrix
    g: csc_matrix
    j: np.ndarray
    slices: list[slice]
    b: np.ndarray
    target: np.ndarray
    group_labels: tuple[str, ...]
    zcc_total: np.ndarray
    h: csc_matrix
    c: csc_matrix
    core_r_ohm: np.ndarray
    physical_ids: dict[str, str]
    global_blocks: tuple[GlobalMatrixBlock, ...]


def _relative_residual(matrix: csc_matrix | np.ndarray, x: np.ndarray, rhs: np.ndarray) -> float:
    value = matrix @ x - rhs
    return float(np.linalg.norm(value) / max(float(np.linalg.norm(rhs)), 1.0))


def _angle(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


def _section_by_id(project: ProjectData, section_id: str) -> InstallationCrossSectionData:
    found = next((item for item in project.installation_design.cross_sections if item.cross_section_id == section_id), None)
    if found is None:
        raise MulticonductorGlobalInputError(f"Fiziksel kesit bulunamadı: {section_id}")
    return found


def _group_system(
    section: InstallationCrossSectionData,
    keys: tuple[_SheathKey, ...],
) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
    """Build phase-current constraints without applying cyclic load factors.

    Explicit per-physical-cable overrides become their own equality constraints;
    the remaining parallel cores share the residual circuit/phase current through
    the global impedance solution.  This keeps the physical current vector in the
    calculation layer instead of projecting it in the UI.
    """

    from ucd.calculations.installation import phase_angle_deg

    circuits = [item for item in section.circuits if item.active]
    circuit_by_id = {item.circuit_id: item for item in circuits}
    physical_by_key = {
        _SheathKey(item.circuit_id, str(item.phase).upper(), int(item.parallel_index)): item
        for item in section.physical_cables
        if item.active and item.circuit_id in circuit_by_id and str(item.phase).upper() in {"A", "B", "C"}
    }
    rows = {key: index for index, key in enumerate(keys)}
    columns: list[tuple[str, tuple[_SheathKey, ...], complex]] = []

    for circuit in circuits:
        for phase in "ABC":
            members = tuple(key for key in keys if key.circuit_id == circuit.circuit_id and key.phase == phase)
            if not members:
                raise MulticonductorGlobalInputError(f"{circuit.circuit_id}:{phase} için aktif fiziksel core bulunmuyor.")
            phase_target = cmath.rect(
                max(0.0, float(circuit.load_current_a)),
                phase_angle_deg(phase) * pi / 180.0,
            )
            residual = phase_target
            free: list[_SheathKey] = []
            for key in members:
                item = physical_by_key.get(key)
                if item is None:
                    raise MulticonductorGlobalInputError(f"Fiziksel kablo eşlemesi bulunamadı: {key.label}")
                explicit = float(item.current_override_a) > 0.0 or item.current_angle_override_deg is not None
                if not explicit:
                    free.append(key)
                    continue
                magnitude = max(0.0, float(item.current_override_a))
                angle = (
                    float(item.current_angle_override_deg)
                    if item.current_angle_override_deg is not None
                    else phase_angle_deg(phase)
                )
                value = cmath.rect(magnitude, angle * pi / 180.0)
                columns.append((f"OVERRIDE:{key.label}", (key,), value))
                residual -= value
            if free:
                columns.append((f"{circuit.circuit_id}:{phase}", tuple(free), residual))
            # If every parallel core is explicitly overridden, the physical
            # overrides are the authoritative phase-current vector.  The
            # circuit scalar remains a legacy/UI summary and is not enforced.

    if not columns:
        raise MulticonductorGlobalInputError("Aktif devre/faz akım kısıtı bulunmuyor.")
    b = np.zeros((len(keys), len(columns)), dtype=complex)
    target: list[complex] = []
    labels: list[str] = []
    for column, (label, members, value) in enumerate(columns):
        labels.append(label)
        target.append(value)
        for key in members:
            b[rows[key], column] = 1.0
    return tuple(labels), b, np.asarray(target, dtype=complex)


def _full_kernel(
    project: ProjectData,
    section: InstallationCrossSectionData,
    keys: tuple[_SheathKey, ...],
    *,
    core_temperature_c_by_physical_id: Mapping[str, float] | None = None,
    sheath_temperature_c_by_physical_id: Mapping[str, float] | None = None,
    gcc_temperature_c: float | None = None,
) -> _FullKernel:
    try:
        primitives, gcc, _issues, _trace = _build_primitives(
            project,
            section,
            core_temperature_c_by_physical_id=core_temperature_c_by_physical_id,
            sheath_temperature_c_by_physical_id=sheath_temperature_c_by_physical_id,
            gcc_temperature_c=gcc_temperature_c,
        )
    except MulticonductorEMInputError as exc:
        raise MulticonductorGlobalInputError(str(exc)) from exc
    primitive_by_key = {
        _SheathKey(item.circuit_id, item.phase, item.parallel_index): item
        for item in primitives
    }
    if set(primitive_by_key) != set(keys):
        missing = sorted(key.label for key in set(keys) - set(primitive_by_key))
        extra = sorted(key.label for key in set(primitive_by_key) - set(keys))
        raise MulticonductorGlobalInputError(
            f"{section.cross_section_id}: güzergâh boyunca core anahtarları değişiyor; eksik={missing}, fazla={extra}."
        )
    ordered = [primitive_by_key[key] for key in keys]
    conductors = tuple(item.core for item in ordered) + tuple(item.sheath for item in ordered) + ((gcc,) if gcc is not None else ())
    try:
        matrix, _de = primitive_impedance_matrix_ohm_km(
            conductors,
            project.cable.frequency_hz,
            project.bonding.earth_resistivity_ohm_m,
            project.cable.sheath_mean_diameter_mm / 2000.0,
        )
    except ValueError as exc:
        raise MulticonductorGlobalInputError(str(exc)) from exc
    nc = len(keys)
    zcc = np.asarray(matrix[:nc, :nc], dtype=complex)
    zcu = np.asarray(matrix[:nc, nc:], dtype=complex)
    zuc = np.asarray(matrix[nc:, :nc], dtype=complex)
    zuu = np.asarray(matrix[nc:, nc:], dtype=complex)
    labels = tuple(key.label for key in keys)
    unknown = labels + (("GCC",) if gcc is not None else ())
    ids, _ = _section_key_maps(section)
    unknown_r = tuple(float(item.sheath.resistance_ohm_km) for item in ordered) + (
        (float(gcc.resistance_ohm_km),) if gcc is not None else ()
    )
    return _FullKernel(
        zcc,
        zcu,
        zuc,
        zuu,
        tuple(float(item.core.resistance_ohm_km) for item in ordered),
        unknown_r,
        labels,
        unknown,
        {key.label: ids[key] for key in keys},
    )


def _build_global_model(
    project: ProjectData,
    *,
    core_temperatures_c_by_cross_section: Mapping[str, Mapping[str, float]] | None = None,
    sheath_temperatures_c_by_cross_section: Mapping[str, Mapping[str, float]] | None = None,
    gcc_temperature_c: float | None = None,
) -> _GlobalModel:
    try:
        sheath_model = _build_network(project)
    except MulticonductorBondingInputError as exc:
        raise MulticonductorGlobalInputError(str(exc)) from exc

    keys = sheath_model.sheath_keys
    if not keys:
        raise MulticonductorGlobalInputError("Global çözüm için fiziksel core/kılıf anahtarı bulunmuyor.")
    first_section = _section_by_id(project, sheath_model.matrix_blocks[0].cross_section_id)
    group_labels, b, target = _group_system(first_section, keys)

    for section in project.installation_design.cross_sections:
        section_keys = set(_section_key_maps(section)[0])
        if section_keys != set(keys):
            continue
        labels_check, _b_check, target_check = _group_system(section, keys)
        if labels_check != group_labels or np.max(np.abs(target_check - target)) > 1e-9:
            raise MulticonductorGlobalInputError(
                f"{section.cross_section_id}: güzergâh boyunca devre/faz yük hedefleri değişiyor. "
                "Global core sürekliliği kapısı sabit terminal akım hedefi gerektirir."
            )

    core_temperature_map = {
        str(section_id): {str(key): float(value) for key, value in values.items()}
        for section_id, values in (core_temperatures_c_by_cross_section or {}).items()
    }
    sheath_temperature_map = {
        str(section_id): {str(key): float(value) for key, value in values.items()}
        for section_id, values in (sheath_temperatures_c_by_cross_section or {}).items()
    }
    temperature_feedback_active = bool(core_temperature_map or sheath_temperature_map or gcc_temperature_c is not None)

    nc = len(keys)
    zcc_total = np.zeros((nc, nc), dtype=complex)
    cache: dict[tuple[object, ...], _FullKernel] = {}
    integrated: dict[str, _IntegratedSection] = {}
    global_blocks: list[GlobalMatrixBlock] = []
    physical_ids: dict[str, str] = {}

    section_branch_by_id = sheath_model.section_branch_index
    for block in sheath_model.matrix_blocks:
        section = _section_by_id(project, block.cross_section_id)
        core_by_id = core_temperature_map.get(block.cross_section_id, {})
        sheath_by_id = sheath_temperature_map.get(block.cross_section_id, {})
        fingerprint = (
            block.cross_section_id,
            tuple(sorted((key, round(value, 8)) for key, value in core_by_id.items())),
            tuple(sorted((key, round(value, 8)) for key, value in sheath_by_id.items())),
            None if gcc_temperature_c is None else round(float(gcc_temperature_c), 8),
        )
        kernel = cache.get(fingerprint)
        if kernel is None:
            kernel = _full_kernel(
                project,
                section,
                keys,
                core_temperature_c_by_physical_id=core_by_id,
                sheath_temperature_c_by_physical_id=sheath_by_id,
                gcc_temperature_c=gcc_temperature_c,
            )
            cache[fingerprint] = kernel
        physical_ids.update(kernel.physical_ids)
        scale = float(block.length_m) / 1000.0
        zcc_b = kernel.zcc * scale
        zcu_b = kernel.zcu * scale
        zuc_b = kernel.zuc * scale
        zuu_b = kernel.zuu * scale
        core_r_b = tuple(float(value * scale) for value in kernel.core_r_ohm_km)
        unknown_r_b = tuple(float(value * scale) for value in kernel.unknown_r_ohm_km)
        zcc_total += zcc_b
        prior = integrated.get(block.section_id)
        if prior is None:
            integrated[block.section_id] = _IntegratedSection(
                zcc_b.copy(), zcu_b.copy(), zuc_b.copy(), zuu_b.copy(), core_r_b, unknown_r_b
            )
        else:
            integrated[block.section_id] = _IntegratedSection(
                prior.zcc + zcc_b,
                prior.zcu + zcu_b,
                prior.zuc + zuc_b,
                prior.zuu + zuu_b,
                tuple(float(a0 + b0) for a0, b0 in zip(prior.core_r_ohm, core_r_b)),
                tuple(float(a0 + b0) for a0, b0 in zip(prior.unknown_r_ohm, unknown_r_b)),
            )
        global_blocks.append(GlobalMatrixBlock(
            block.section_id,
            block.cross_section_id,
            block.start_m,
            block.end_m,
            block.length_m,
            kernel.core_labels,
            kernel.unknown_labels,
            tuple(tuple(complex(v) for v in row) for row in zcc_b),
            tuple(tuple(complex(v) for v in row) for row in zcu_b),
            tuple(tuple(complex(v) for v in row) for row in zuc_b),
            tuple(tuple(complex(v) for v in row) for row in zuu_b),
            core_r_b,
            unknown_r_b,
        ))

    if temperature_feedback_active:
        for section_id, values in integrated.items():
            branch_index = section_branch_by_id[section_id]
            branch = sheath_model.branches[branch_index]
            branch.z = values.zuu.copy()
            branch.metallic_r_diag = tuple(values.unknown_r_ohm)

    try:
        a, z, g, j, _legacy_e, slices = _assemble(sheath_model)
    except MulticonductorBondingInputError as exc:
        raise MulticonductorGlobalInputError(str(exc)) from exc

    total_branch = z.shape[0]
    h = np.zeros((nc, total_branch), dtype=complex)
    c = np.zeros((total_branch, nc), dtype=complex)
    core_r = np.zeros(nc, dtype=float)
    for block in global_blocks:
        core_r += np.asarray(block.core_resistance_ohm, dtype=float)

    for section_id, values in integrated.items():
        branch_index = section_branch_by_id[section_id]
        sl = slices[branch_index]
        if sl.stop - sl.start != values.zcu.shape[1]:
            raise MulticonductorGlobalInputError(f"{section_id}: core-kılıf blok boyutu ağ dalıyla uyuşmuyor.")
        h[:, sl] = values.zcu
        c[sl, :] = values.zuc
        branch_z = np.asarray(sheath_model.branches[branch_index].z, dtype=complex)
        if np.max(np.abs(branch_z - values.zuu)) > 1e-8:
            raise MulticonductorGlobalInputError(
                f"{section_id}: sheath ağ bloğu ile sıcaklığa bağlı global primitive bloğu uyuşmuyor."
            )

    return _GlobalModel(
        sheath_model,
        a,
        z,
        g,
        j,
        slices,
        b,
        target,
        group_labels,
        zcc_total,
        csc_matrix(h),
        csc_matrix(c),
        core_r,
        physical_ids,
        tuple(global_blocks),
    )


def _direct_solve(model: _GlobalModel) -> GlobalNetworkMethodResult:
    nc = model.zcc_total.shape[0]
    nn = model.a.shape[0]
    nb = model.z.shape[0]
    ng = model.b.shape[1]
    zero_cn = csc_matrix((nc, nn), dtype=complex)
    zero_gn = csc_matrix((ng, nn), dtype=complex)
    zero_gb = csc_matrix((ng, nb), dtype=complex)
    zero_gg = csc_matrix((ng, ng), dtype=complex)
    zero_ng = csc_matrix((nn, ng), dtype=complex)
    zero_bg = csc_matrix((nb, ng), dtype=complex)
    matrix = bmat([
        [csc_matrix(model.zcc_total), zero_cn, model.h, -csc_matrix(model.b)],
        [csc_matrix(model.b.T), zero_gn, zero_gb, zero_gg],
        [csc_matrix((nn, nc), dtype=complex), model.g, model.a, zero_ng],
        [-model.c, model.a.T, -model.z, zero_bg],
    ], format="csc")
    rhs = np.concatenate([
        np.zeros(nc, dtype=complex),
        model.target,
        model.j,
        np.zeros(nb, dtype=complex),
    ])
    x = np.asarray(spsolve(matrix, rhs), dtype=complex)
    if not np.all(np.isfinite(x)):
        raise MulticonductorGlobalInputError("Global direct çözüm sonlu olmayan değer üretti.")
    core = x[:nc]
    v = x[nc:nc + nn]
    currents = x[nc + nn:nc + nn + nb]
    group_v = x[nc + nn + nb:]
    return _method_result("GLOBAL_DIRECT_KKT", model, matrix, x, rhs, core, v, currents, group_v)


def _reduced_solve(model: _GlobalModel) -> GlobalNetworkMethodResult:
    nc = model.zcc_total.shape[0]
    nn = model.a.shape[0]
    nb = model.z.shape[0]
    sheath_matrix = bmat([
        [model.g, model.a],
        [model.a.T, -model.z],
    ], format="csc")
    rhs0 = np.concatenate([model.j, np.zeros(nb, dtype=complex)])
    x0 = np.asarray(spsolve(sheath_matrix, rhs0), dtype=complex)
    if not np.all(np.isfinite(x0)):
        raise MulticonductorGlobalInputError("Global reduced sheath tabanı sonlu olmayan değer üretti.")
    coupling_rhs = np.vstack([
        np.zeros((nn, nc), dtype=complex),
        model.c.toarray(),
    ])
    gain = np.column_stack([
        np.asarray(spsolve(sheath_matrix, coupling_rhs[:, index]), dtype=complex)
        for index in range(nc)
    ])
    current0 = x0[nn:]
    current_gain = gain[nn:, :]
    zeff = model.zcc_total + model.h.toarray() @ current_gain
    rhs_core = -(model.h.toarray() @ current0)
    reduced = np.block([
        [zeff, -model.b],
        [model.b.T, np.zeros((model.b.shape[1], model.b.shape[1]), dtype=complex)],
    ])
    reduced_rhs = np.concatenate([rhs_core, model.target])
    solution = np.linalg.solve(reduced, reduced_rhs)
    core = solution[:nc]
    group_v = solution[nc:]
    sheath_state = x0 + gain @ core
    v = sheath_state[:nn]
    currents = sheath_state[nn:]
    # Report residual against the full coupled system, while condition belongs to reduced KKT.
    full_matrix = bmat([
        [csc_matrix(model.zcc_total), csc_matrix((nc, nn)), model.h, -csc_matrix(model.b)],
        [csc_matrix(model.b.T), csc_matrix((model.b.shape[1], nn)), csc_matrix((model.b.shape[1], nb)), csc_matrix((model.b.shape[1], model.b.shape[1]))],
        [csc_matrix((nn, nc)), model.g, model.a, csc_matrix((nn, model.b.shape[1]))],
        [-model.c, model.a.T, -model.z, csc_matrix((nb, model.b.shape[1]))],
    ], format="csc")
    full_x = np.concatenate([core, v, currents, group_v])
    full_rhs = np.concatenate([np.zeros(nc), model.target, model.j, np.zeros(nb)]).astype(complex)
    return _method_result(
        "GLOBAL_SHEATH_SCHUR",
        model,
        full_matrix,
        full_x,
        full_rhs,
        core,
        v,
        currents,
        group_v,
        condition_override=float(np.linalg.cond(reduced)),
    )


def _method_result(
    name: str,
    model: _GlobalModel,
    matrix: csc_matrix,
    x: np.ndarray,
    rhs: np.ndarray,
    core: np.ndarray,
    v: np.ndarray,
    currents: np.ndarray,
    group_v: np.ndarray,
    *,
    condition_override: float | None = None,
) -> GlobalNetworkMethodResult:
    phase_residual = float(np.max(np.abs(model.b.T @ core - model.target)))
    kcl = float(np.linalg.norm(model.g @ v + model.a @ currents - model.j))
    branch = float(np.linalg.norm(model.a.T @ v - model.z @ currents - model.c @ core))
    core_voltage = float(np.linalg.norm(model.zcc_total @ core + model.h @ currents - model.b @ group_v))
    condition = condition_override if condition_override is not None else _condition(matrix)
    return GlobalNetworkMethodResult(
        name,
        tuple(complex(value) for value in core),
        tuple(complex(value) for value in v),
        tuple(complex(value) for value in currents),
        tuple(complex(value) for value in group_v),
        float(condition),
        _relative_residual(matrix, x, rhs),
        phase_residual,
        kcl,
        branch,
        core_voltage,
    )


def solve_global_multiconductor_network(
    project: ProjectData,
    *,
    selected_method: str = "GLOBAL_DIRECT_KKT",
    core_temperatures_c_by_cross_section: Mapping[str, Mapping[str, float]] | None = None,
    sheath_temperatures_c_by_cross_section: Mapping[str, Mapping[str, float]] | None = None,
    gcc_temperature_c: float | None = None,
    production_mode: bool = False,
) -> GlobalMulticonductorNetworkResult:
    """Solve route-wide core continuity and the explicit sheath network jointly."""

    try:
        require_production_physics(project.cable, engine_label="global bonding/CIM")
    except ValueError as exc:
        raise MulticonductorGlobalInputError(str(exc)) from exc

    before = project.to_dict()
    model = _build_global_model(
        project,
        core_temperatures_c_by_cross_section=core_temperatures_c_by_cross_section,
        sheath_temperatures_c_by_cross_section=sheath_temperatures_c_by_cross_section,
        gcc_temperature_c=gcc_temperature_c,
    )
    direct = _direct_solve(model)
    reduced = _reduced_solve(model)
    selected = str(selected_method).strip().upper()
    active = reduced if selected in {"GLOBAL_SHEATH_SCHUR", "REDUCED", "SCHUR"} else direct

    core_d = np.asarray(direct.core_currents_a, dtype=complex)
    core_r = np.asarray(reduced.core_currents_a, dtype=complex)
    branch_d = np.asarray(direct.branch_currents_a, dtype=complex)
    branch_r = np.asarray(reduced.branch_currents_a, dtype=complex)
    v_d = np.asarray(direct.sheath_node_voltages_v, dtype=complex)
    v_r = np.asarray(reduced.sheath_node_voltages_v, dtype=complex)
    di_core = float(np.max(np.abs(core_d - core_r)))
    di_sheath = float(np.max(np.abs(branch_d - branch_r)))
    dv = float(np.max(np.abs(v_d - v_r)))
    tol_core = max(1e-6, 1e-8 * max(float(np.max(np.abs(core_d))), 1.0))
    tol_branch = max(1e-6, 1e-8 * max(float(np.max(np.abs(branch_d))), 1.0))
    tol_v = max(1e-5, 1e-8 * max(float(np.max(np.abs(v_d))), 1.0))

    keys = model.sheath_model.sheath_keys
    group_index = {label: idx for idx, label in enumerate(model.group_labels)}
    group_members: dict[str, list[int]] = {label: [] for label in model.group_labels}
    b_matrix = np.asarray(model.b, dtype=complex)
    for group_id, column in group_index.items():
        group_members[group_id] = [
            row for row in range(len(keys)) if abs(b_matrix[row, column]) > 0.5
        ]

    core_results: list[GlobalCoreCableResult] = []
    group_results: list[GlobalCoreGroupResult] = []
    max_imbalance = 0.0
    group_v = np.asarray(active.group_voltage_drops_v, dtype=complex)
    core = np.asarray(active.core_currents_a, dtype=complex)
    for group_id, members in group_members.items():
        gidx = group_index[group_id]
        solved = sum((core[index] for index in members), 0j)
        magnitudes = [abs(core[index]) for index in members]
        mean = sum(magnitudes) / len(magnitudes)
        imbalance = 100.0 * (max(magnitudes) - min(magnitudes)) / mean if mean > 1e-15 else 0.0
        max_imbalance = max(max_imbalance, imbalance)
        if group_id.startswith("OVERRIDE:"):
            _tag, circuit_id, phase, _parallel = group_id.split(":", 3)
        else:
            circuit_id, phase = group_id.split(":", 1)
        group_results.append(GlobalCoreGroupResult(
            group_id,
            circuit_id,
            phase,
            len(members),
            complex(model.target[gidx]),
            complex(solved),
            complex(group_v[gidx]),
            float(max(magnitudes)),
            float(min(magnitudes)),
            float(imbalance),
            float(abs(solved - model.target[gidx])),
        ))
        denominator = sum(magnitudes)
        equal = model.target[gidx] / len(members)
        for index in members:
            key = keys[index]
            share = 100.0 * abs(core[index]) / denominator if denominator > 1e-15 else 0.0
            core_results.append(GlobalCoreCableResult(
                model.physical_ids.get(key.label, key.label),
                key.circuit_id,
                key.phase,
                key.parallel_index,
                complex(core[index]),
                complex(equal),
                float(share),
                float(abs(core[index] - equal)),
                complex(group_v[gidx]),
                float(abs(core[index]) ** 2 * model.core_r_ohm[index]),
            ))
    core_results.sort(key=lambda item: (item.circuit_id, "ABC".index(item.phase), item.parallel_index))

    sheath_model = model.sheath_model
    currents = np.asarray(active.branch_currents_a, dtype=complex)
    v = np.asarray(active.sheath_node_voltages_v, dtype=complex)
    positions = _node_positions(project.bonding)
    minor_by_id = {item.section_id: item for item in project.bonding.minor_sections}
    blocks_by_section: dict[str, list[GlobalMatrixBlock]] = {}
    for block in model.global_blocks:
        blocks_by_section.setdefault(block.section_id, []).append(block)

    section_results: list[MulticonductorBondingSectionResult] = []
    total_core = sum(item.core_metal_loss_w for item in core_results)
    total_sheath = total_gcc = total_earth = 0.0
    max_ish = max_vg = max_vss = max_gcc = 0.0
    n_sheath = len(keys)
    for section_id, branch_index in sheath_model.section_branch_index.items():
        branch = sheath_model.branches[branch_index]
        sl = model.slices[branch_index]
        i_vec = currents[sl]
        start_nodes, end_nodes = sheath_model.endpoints[section_id]
        minor = minor_by_id[section_id]
        start_m, end_m = _minor_bounds(minor, positions)
        sheath_rows: list[MulticonductorSheathResult] = []
        start_values: list[complex] = []
        end_values: list[complex] = []
        sheath_loss = 0.0
        open_emf = model.c[sl, :] @ core
        for idx, key in enumerate(keys):
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
                model.physical_ids.get(key.label, key.label),
                current,
                sv,
                ev,
                complex(open_emf[idx]),
                float(loss),
            ))
        gcc_i = complex(i_vec[n_sheath]) if len(i_vec) > n_sheath else 0j
        gcc_sv = complex(v[start_nodes["GCC"]]) if "GCC" in start_nodes else 0j
        gcc_ev = complex(v[end_nodes["GCC"]]) if "GCC" in end_nodes else 0j
        gcc_loss = abs(gcc_i) ** 2 * branch.metallic_r_diag[n_sheath] if len(i_vec) > n_sheath else 0.0
        total_branch_active = float(np.real(np.conjugate(i_vec) @ branch.z @ i_vec))
        earth_loss = max(0.0, total_branch_active - sheath_loss - gcc_loss)
        section_vg = max([abs(value) for value in start_values + end_values] or [0.0])
        section_vss = max(_pairwise_max(start_values), _pairwise_max(end_values))
        section_ish = max([abs(item.sheath_current_a) for item in sheath_rows] or [0.0])
        section_core_loss = 0.0
        for block in blocks_by_section.get(section_id, []):
            section_core_loss += sum(
                abs(core[index]) ** 2 * block.core_resistance_ohm[index]
                for index in range(len(keys))
            )
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
            float(section_core_loss),
            float(section_ish),
            float(section_vg),
            float(section_vss),
        ))

    accessory: list[MulticonductorBondingBranchResult] = []
    accessory_loss = 0.0
    for idx, branch in enumerate(sheath_model.branches):
        if branch.branch_type == "N_CABLE_SECTION":
            continue
        sl = model.slices[idx]
        for local, value in enumerate(currents[sl]):
            impedance = complex(branch.z[local, local])
            loss = abs(value) ** 2 * max(impedance.real, 0.0)
            accessory_loss += loss
            start_label = sheath_model.node_labels[branch.start_nodes[local]]
            end_node = branch.end_nodes[local]
            end_label = "GROUND" if end_node is None else sheath_model.node_labels[end_node]
            accessory.append(MulticonductorBondingBranchResult(
                branch.branch_id if len(branch.start_nodes) == 1 else f"{branch.branch_id}:{local}",
                branch.branch_type,
                start_label,
                end_label,
                complex(value),
                impedance,
                float(loss),
            ))

    if project.to_dict() != before:
        raise RuntimeError("SHADOW_COMPARE global çözümü proje nesnesini değiştirdi; işlem iptal edildi.")

    temperature_feedback_active = bool(
        core_temperatures_c_by_cross_section
        or sheath_temperatures_c_by_cross_section
        or gcc_temperature_c is not None
    )
    issues = [
        MulticonductorBondingIssue(
            "INFO", "PRODUCTION_COUPLED" if production_mode else "SHADOW_ONLY",
            "Global N-core/N-kılıf sonucu üretim elektro-termal çalışma noktasının fiziksel kayıp vektörüdür."
            if production_mode else
            "Global N-core/N-kılıf sonucu mevcut bonding, IEC, termal sonuçlarını veya proje λ1 değerini değiştirmez."
        ),
        MulticonductorBondingIssue(
            "INFO", "GLOBAL_CORE_CONTINUITY_ACTIVE",
            "Her fiziksel paralel core için tek güzergâh akımı çözülür; bütün minor-section bloklarında aynı akım kullanılır."
        ),
        MulticonductorBondingIssue(
            "INFO", "CORE_SHEATH_COUPLED_GLOBAL_SYSTEM",
            "Core akım paylaşımı ile sheath/link-box/GCC ağı aynı kompleks kısıtlı sistemde birlikte çözülür."
        ),
        MulticonductorBondingIssue(
            "WARNING", "FIXED_PHASE_VOLTAGE_CHARGING_APPROXIMATION",
            "Dielektrik pi şöntleri mevcut üretim ağındaki nominal faz gerilimi referansını korur; explicit core-node kapasitif ağ sonraki doğrulama kapısıdır."
        ),
        MulticonductorBondingIssue(
            "INFO", "EXPLICIT_TERMINAL_CONNECTION_MATRIX_PENDING",
            "Link-box eşlemesi devre/faz/paralel kimliğine uygulanan mevcut A→B→C permütasyonundan türetilir."
        ),
    ]
    if temperature_feedback_active:
        issues.append(MulticonductorBondingIssue(
            "INFO", "TEMPERATURE_DEPENDENT_RESISTANCE_ACTIVE",
            "Core, metalik kılıf ve varsa GCC dirençleri dış elektro-termal sıcaklık durumundan yeniden oluşturuldu."
        ))
    lambda1 = total_sheath / total_core if total_core > 1e-15 else 0.0
    trace = (
        "core_order=" + ",".join(key.label for key in keys),
        "group_order=" + ",".join(model.group_labels),
        f"minor_sections={len(section_results)}",
        f"matrix_blocks={len(model.global_blocks)}",
        f"global_unknowns={len(direct.core_currents_a) + len(direct.sheath_node_voltages_v) + len(direct.branch_currents_a) + len(direct.group_voltage_drops_v)}",
        f"gcc_enabled={project.bonding.gcc_enabled}",
        f"temperature_feedback_active={temperature_feedback_active}",
    )
    return GlobalMulticonductorNetworkResult(
        "PRODUCTION_GLOBAL_NETWORK" if production_mode else ENGINE_MODE,
        CORE_SHARING_MODE,
        REFERENCE,
        tuple(key.label for key in keys),
        tuple(key.label for key in keys),
        model.group_labels,
        model.global_blocks,
        tuple(core_results),
        tuple(group_results),
        tuple(section_results),
        tuple(accessory),
        direct,
        reduced,
        active.method,
        di_core <= tol_core and di_sheath <= tol_branch and dv <= tol_v,
        di_core,
        di_sheath,
        dv,
        float(max_imbalance),
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
        len(sheath_model.node_labels),
        len(active.branch_currents_a),
        tuple(issues),
        trace,
        bool(production_mode),
    )


def render_global_multiconductor_network(result: GlobalMulticonductorNetworkResult) -> str:
    lines = result.trace_lines()
    lines.extend(["", "Global fiziksel core akımları:"])
    for item in result.core_results:
        lines.append(
            f"{item.key} ({item.physical_cable_id}): "
            f"Ic={abs(item.core_current_a):.6f}∠{_angle(item.core_current_a):.3f}° A; "
            f"pay=%{item.current_share_percent:.6f}; eşit-pay ΔI={item.current_difference_from_equal_share_a:.6f} A; "
            f"ΔVroute={abs(item.route_voltage_drop_v):.6f}∠{_angle(item.route_voltage_drop_v):.3f}° V; "
            f"Pcore={item.core_metal_loss_w:.6f} W"
        )
    lines.extend(["", "Minor section / global kılıf sonuçları:"])
    for section in result.section_results:
        for item in section.sheath_results:
            lines.append(
                f"{item.section_id} {item.key}: |Ish|={abs(item.sheath_current_a):.6f}∠{_angle(item.sheath_current_a):.3f}° A; "
                f"Vstart={abs(item.start_voltage_to_earth_v):.6f} V; Vend={abs(item.end_voltage_to_earth_v):.6f} V; "
                f"Eopen={abs(item.integrated_open_emf_v):.6f} V; Psh={item.sheath_metal_loss_w:.6f} W"
            )
    return "\n".join(lines)
