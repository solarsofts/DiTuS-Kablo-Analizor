"""Closed-loop N-core/N-sheath electro-thermal coordinator.

The same read-only physical kernels support independent shadow comparison and
scenario-resolved production operating points. Project data and legacy λ1 are
never mutated by the coordinator.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from ucd.calculations.multiconductor_global_network import (
    GlobalMulticonductorNetworkResult,
    MulticonductorGlobalInputError,
    solve_global_multiconductor_network,
)
from ucd.calculations.multiconductor_thermal import (
    MulticonductorThermalInputError,
    MulticonductorThermalResult,
    _active_physical_cables,
    _cross_section_for_region,
    solve_multiconductor_thermal,
)
from ucd.calculations.thermal_resistance import resolve_internal_thermal_resistance
from ucd.calculations.thermal_route import resolve_thermal_region
from ucd.models.project import ProjectData


MODE = "SHADOW_COMPARE"
PRODUCTION_MODE = "PRODUCTION_COUPLED"
COUPLING_MODE = "CLOSED_LOOP_GLOBAL_EM_REAL_XY_THERMAL"
REFERENCE = (
    "IEC 60287-1-1/-1-3 temperature-dependent conductor and sheath losses; "
    "IEC 60287-2-1 steady-state thermal network; CIGRE TB 797 bonding architecture; "
    "CIGRE TB 880 calculation-tool verification targets"
)


class ElectroThermalInputError(ValueError):
    pass


@dataclass(frozen=True)
class ElectroThermalIssue:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ElectroThermalIterationResult:
    iteration: int
    relaxation_factor: float
    maximum_temperature_residual_c: float
    maximum_core_current_change_percent: float
    maximum_sheath_current_change_percent: float
    active_loss_change_percent: float
    maximum_conductor_temperature_c: float
    maximum_sheath_temperature_c: float
    maximum_core_current_a: float
    maximum_sheath_current_a: float
    total_core_loss_w: float
    total_sheath_loss_w: float
    lambda1: float
    em_methods_agree: bool
    thermal_regions_converged: bool


@dataclass
class ElectroThermalCoupledResult:
    mode: str
    coupling_mode: str
    reference: str
    converged: bool
    iteration_count: int
    maximum_iterations: int
    temperature_tolerance_c: float
    current_tolerance_percent: float
    loss_tolerance_percent: float
    relaxation_factor: float
    iterations: tuple[ElectroThermalIterationResult, ...]
    final_global_em: GlobalMulticonductorNetworkResult
    final_thermal: MulticonductorThermalResult
    core_temperatures_c_by_cross_section: dict[str, dict[str, float]]
    sheath_temperatures_c_by_cross_section: dict[str, dict[str, float]]
    issues: tuple[ElectroThermalIssue, ...]
    trace: tuple[str, ...]
    production_mode: bool = False
    thermal_method: str = "NODAL"
    scenario_id: str = ""
    loss_vector_fingerprint: str = ""

    @property
    def final_design_ready(self) -> bool:
        return bool(self.production_mode and self.converged and self.final_global_em.methods_agree)

    def trace_lines(self) -> list[str]:
        label = "Üretim Elektro-Termal Kapalı Çevrim" if self.production_mode else "Elektro-Termal Kapalı Çevrim Gölge Çözümü"
        final_temperature = (
            self.final_thermal.maximum_analytical_conductor_temperature_c
            if str(self.thermal_method).upper() == "ANALYTIC"
            else self.final_thermal.maximum_nodal_conductor_temperature_c
        )
        lines = [
            f"DiTuS — {label}",
            f"Mod={self.mode}; coupling={self.coupling_mode}",
            f"Referans={self.reference}",
            f"Yakınsama={'EVET' if self.converged else 'HAYIR'}; iterasyon={self.iteration_count}/{self.maximum_iterations}",
            f"Tolerans T/I/P={self.temperature_tolerance_c:.6f} °C / "
            f"%{self.current_tolerance_percent:.6f} / %{self.loss_tolerance_percent:.6f}",
            f"Relaxation={self.relaxation_factor:.4f}",
            f"Final Tmax={final_temperature:.6f} °C; "
            f"λ1={self.final_global_em.lambda1:.9f}",
        ]
        for item in self.iterations:
            lines.append(
                f"it={item.iteration}: ΔT={item.maximum_temperature_residual_c:.6e} °C; "
                f"ΔIc=%{item.maximum_core_current_change_percent:.6e}; "
                f"ΔIsh=%{item.maximum_sheath_current_change_percent:.6e}; "
                f"ΔP=%{item.active_loss_change_percent:.6e}; "
                f"Tmax={item.maximum_conductor_temperature_c:.6f} °C; λ1={item.lambda1:.9f}"
            )
        lines.extend(self.trace)
        lines.extend(f"{item.severity} {item.code}: {item.message}" for item in self.issues)
        return lines


def _bounded_temperature(value: float, project: ProjectData) -> float:
    return max(-273.149999, min(float(value), float(project.cable.max_temperature_c) + 120.0))


def _initial_temperature_state(
    project: ProjectData,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    weighted_ambient: dict[str, float] = {}
    weighted_length: dict[str, float] = {}
    sections: dict[str, object] = {}
    for region in project.thermal_design.regions:
        if not region.enabled:
            continue
        section = _cross_section_for_region(project, region)
        profile = resolve_thermal_region(project.thermal_design, region, project.cable)
        length = max(0.0, float(region.end_m) - float(region.start_m))
        if length <= 0.0:
            continue
        sections[section.cross_section_id] = section
        weighted_ambient[section.cross_section_id] = (
            weighted_ambient.get(section.cross_section_id, 0.0)
            + length * float(profile.ambient_temperature_c)
        )
        weighted_length[section.cross_section_id] = weighted_length.get(section.cross_section_id, 0.0) + length
    if not sections:
        raise ElectroThermalInputError("Etkin termal bölge ve bağlı fiziksel kesit bulunamadı.")

    core: dict[str, dict[str, float]] = {}
    sheath: dict[str, dict[str, float]] = {}
    for section_id, section in sections.items():
        ambient = weighted_ambient[section_id] / max(weighted_length[section_id], 1e-12)
        core[section_id] = {}
        sheath[section_id] = {}
        for cable in _active_physical_cables(section):
            core[section_id][cable.physical_cable_id] = _bounded_temperature(ambient + 20.0, project)
            sheath[section_id][cable.physical_cable_id] = _bounded_temperature(ambient + 10.0, project)
    return core, sheath


def _temperature_state_from_thermal(
    project: ProjectData,
    thermal: MulticonductorThermalResult,
    *,
    thermal_method: str = "NODAL",
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    internal = resolve_internal_thermal_resistance(project.cable)
    core_sum: dict[str, dict[str, float]] = {}
    sheath_sum: dict[str, dict[str, float]] = {}
    weights: dict[str, dict[str, float]] = {}
    for region in thermal.regions:
        length = max(0.0, float(region.end_m) - float(region.start_m))
        if length <= 0.0:
            continue
        section_id = region.cross_section_id
        core_sum.setdefault(section_id, {})
        sheath_sum.setdefault(section_id, {})
        weights.setdefault(section_id, {})
        for cable in region.cables:
            physical_id = cable.physical_cable_id
            use_nodal = str(thermal_method).upper() == "NODAL" and region.nodal_computed
            conductor_temperature = float(
                cable.nodal_conductor_temperature_c if use_nodal else cable.analytical_conductor_temperature_c
            )
            jacket_temperature = float(
                cable.nodal_jacket_temperature_c if use_nodal else cable.analytical_jacket_temperature_c
            )
            conductor_to_sheath_rise = (
                float(cable.conductor_loss_w_m) + 0.5 * float(cable.dielectric_loss_w_m)
            ) * float(internal.t1_km_w)
            sheath_temperature = conductor_temperature - conductor_to_sheath_rise
            sheath_temperature = max(jacket_temperature, min(conductor_temperature, sheath_temperature))
            core_sum[section_id][physical_id] = core_sum[section_id].get(physical_id, 0.0) + length * conductor_temperature
            sheath_sum[section_id][physical_id] = sheath_sum[section_id].get(physical_id, 0.0) + length * sheath_temperature
            weights[section_id][physical_id] = weights[section_id].get(physical_id, 0.0) + length

    core: dict[str, dict[str, float]] = {}
    sheath: dict[str, dict[str, float]] = {}
    for section_id, values in core_sum.items():
        core[section_id] = {}
        sheath[section_id] = {}
        for physical_id, value in values.items():
            weight = max(weights[section_id][physical_id], 1e-12)
            core[section_id][physical_id] = _bounded_temperature(value / weight, project)
            sheath[section_id][physical_id] = _bounded_temperature(
                sheath_sum[section_id][physical_id] / weight, project
            )
    return core, sheath


def _relax_state(
    old: Mapping[str, Mapping[str, float]],
    new: Mapping[str, Mapping[str, float]],
    factor: float,
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for section_id in sorted(set(old) | set(new)):
        result[section_id] = {}
        old_values = old.get(section_id, {})
        new_values = new.get(section_id, {})
        for physical_id in sorted(set(old_values) | set(new_values)):
            old_value = float(old_values.get(physical_id, new_values.get(physical_id, 20.0)))
            new_value = float(new_values.get(physical_id, old_value))
            result[section_id][physical_id] = factor * new_value + (1.0 - factor) * old_value
    return result


def _maximum_state_difference(
    first: Mapping[str, Mapping[str, float]],
    second: Mapping[str, Mapping[str, float]],
) -> float:
    values = []
    for section_id in set(first) | set(second):
        a = first.get(section_id, {})
        b = second.get(section_id, {})
        for physical_id in set(a) | set(b):
            values.append(abs(float(a.get(physical_id, 0.0)) - float(b.get(physical_id, 0.0))))
    return max(values or [0.0])


def _maximum_relative_change_percent(new: list[complex], old: list[complex] | None) -> float:
    if old is None or len(new) != len(old):
        return float("inf")
    return max(
        (
            100.0 * abs(a - b) / max(abs(b), 1.0)
            for a, b in zip(new, old)
        ),
        default=0.0,
    )


def _sheath_current_vector(result: GlobalMulticonductorNetworkResult) -> list[complex]:
    values: list[complex] = []
    for section in result.section_results:
        values.extend(item.sheath_current_a for item in section.sheath_results)
        values.append(section.gcc_current_a)
    values.extend(item.current_a for item in result.accessory_branches)
    return values


def _loss_vector_fingerprint(thermal: MulticonductorThermalResult) -> str:
    payload = []
    for region in thermal.regions:
        for cable in region.cables:
            payload.append({
                "region": region.region_id,
                "physical": cable.physical_cable_id,
                "current_re": round(cable.current_a.real, 9),
                "current_im": round(cable.current_a.imag, 9),
                "core": round(cable.conductor_loss_w_m, 12),
                "sheath": round(cable.sheath_loss_w_m, 12),
                "dielectric": round(cable.dielectric_loss_w_m, 12),
                "armour": round(cable.armour_loss_w_m, 12),
            })
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "LOSS-" + hashlib.sha256(raw).hexdigest()[:16].upper()


def solve_electrothermal_coupled(
    project: ProjectData,
    *,
    mesh_scale: float = 2.0,
    maximum_iterations: int = 20,
    temperature_tolerance_c: float = 0.05,
    current_tolerance_percent: float = 0.10,
    loss_tolerance_percent: float = 0.10,
    relaxation_factor: float = 0.60,
    selected_em_method: str = "GLOBAL_DIRECT_KKT",
    production_mode: bool = False,
    thermal_method: str = "NODAL",
    deenergized_circuit_ids: tuple[str, ...] = (),
    scenario_id: str = "",
) -> ElectroThermalCoupledResult:
    """Solve the global EM and real-x/y thermal kernels to a fixed point."""

    before = project.to_dict()
    if mesh_scale <= 0.0:
        raise ElectroThermalInputError("2D ağ ölçeği sıfırdan büyük olmalıdır.")
    if maximum_iterations < 2:
        raise ElectroThermalInputError("Kapalı çevrim en az iki iterasyon çalıştırmalıdır.")
    if temperature_tolerance_c <= 0.0:
        raise ElectroThermalInputError("Sıcaklık toleransı sıfırdan büyük olmalıdır.")
    if current_tolerance_percent <= 0.0 or loss_tolerance_percent <= 0.0:
        raise ElectroThermalInputError("Akım ve kayıp toleransları sıfırdan büyük olmalıdır.")
    if not 0.05 <= relaxation_factor <= 1.0:
        raise ElectroThermalInputError("Relaxation faktörü 0.05–1.00 aralığında olmalıdır.")
    thermal_method = str(thermal_method).strip().upper()
    if thermal_method not in {"ANALYTIC", "NODAL"}:
        raise ElectroThermalInputError("thermal_method ANALYTIC veya NODAL olmalıdır.")

    core_state, sheath_state = _initial_temperature_state(project)
    previous_core_currents: list[complex] | None = None
    previous_sheath_currents: list[complex] | None = None
    previous_active_loss: float | None = None
    iterations: list[ElectroThermalIterationResult] = []
    final_em: GlobalMulticonductorNetworkResult | None = None
    final_thermal: MulticonductorThermalResult | None = None
    converged = False

    for iteration in range(1, maximum_iterations + 1):
        gcc_temperature = max(
            (value for section in sheath_state.values() for value in section.values()),
            default=float(project.bonding.gcc_operating_temperature_c),
        )
        try:
            em = solve_global_multiconductor_network(
                project,
                selected_method=selected_em_method,
                core_temperatures_c_by_cross_section=core_state,
                sheath_temperatures_c_by_cross_section=sheath_state,
                gcc_temperature_c=gcc_temperature,
                production_mode=production_mode,
            )
            thermal = solve_multiconductor_thermal(
                project,
                mesh_scale=mesh_scale,
                max_iterations=5,
                tolerance_c=min(temperature_tolerance_c, 0.02),
                global_result=em,
                fixed_global_losses=True,
                solve_nodal=(thermal_method == "NODAL"),
                deenergized_circuit_ids=deenergized_circuit_ids,
                production_mode=production_mode,
                core_temperatures_c_by_cross_section=core_state,
                sheath_temperatures_c_by_cross_section=sheath_state,
            )
        except (MulticonductorGlobalInputError, MulticonductorThermalInputError) as exc:
            raise ElectroThermalInputError(str(exc)) from exc

        solved_core_state, solved_sheath_state = _temperature_state_from_thermal(
            project, thermal, thermal_method=thermal_method
        )
        temperature_residual = max(
            _maximum_state_difference(solved_core_state, core_state),
            _maximum_state_difference(solved_sheath_state, sheath_state),
        )
        core_currents = [item.core_current_a for item in em.core_results]
        sheath_currents = _sheath_current_vector(em)
        core_current_change = _maximum_relative_change_percent(core_currents, previous_core_currents)
        sheath_current_change = _maximum_relative_change_percent(sheath_currents, previous_sheath_currents)
        active_loss = float(em.total_core_metal_loss_w + em.total_sheath_metal_loss_w)
        loss_change = (
            float("inf")
            if previous_active_loss is None
            else 100.0 * abs(active_loss - previous_active_loss) / max(abs(previous_active_loss), 1.0)
        )
        max_sheath_temperature = max(
            (value for values in solved_sheath_state.values() for value in values.values()),
            default=20.0,
        )
        all_thermal_converged = all(
            (
                region.nodal_converged
                and (not getattr(region, "dryout_enabled", False) or getattr(region, "dryout_converged", False))
            ) if thermal_method == "NODAL" else True
            for region in thermal.regions
        )
        selected_max_temperature = (
            thermal.maximum_nodal_conductor_temperature_c
            if thermal_method == "NODAL" else thermal.maximum_analytical_conductor_temperature_c
        )
        iterations.append(ElectroThermalIterationResult(
            iteration,
            float(relaxation_factor),
            float(temperature_residual),
            float(core_current_change),
            float(sheath_current_change),
            float(loss_change),
            float(selected_max_temperature),
            float(max_sheath_temperature),
            float(max((abs(value) for value in core_currents), default=0.0)),
            float(max((abs(value) for value in sheath_currents), default=0.0)),
            float(em.total_core_metal_loss_w),
            float(em.total_sheath_metal_loss_w),
            float(em.lambda1),
            bool(em.methods_agree),
            bool(all_thermal_converged),
        ))
        final_em = em
        final_thermal = thermal

        if (
            iteration >= 2
            and temperature_residual <= temperature_tolerance_c
            and core_current_change <= current_tolerance_percent
            and sheath_current_change <= current_tolerance_percent
            and loss_change <= loss_tolerance_percent
            and em.methods_agree
            and all_thermal_converged
        ):
            core_state = solved_core_state
            sheath_state = solved_sheath_state
            converged = True
            break

        core_state = _relax_state(core_state, solved_core_state, relaxation_factor)
        sheath_state = _relax_state(sheath_state, solved_sheath_state, relaxation_factor)
        previous_core_currents = core_currents
        previous_sheath_currents = sheath_currents
        previous_active_loss = active_loss

    if final_em is None or final_thermal is None:
        raise ElectroThermalInputError("Kapalı çevrim çözümü sonuç üretmedi.")

    issues = [
        ElectroThermalIssue(
            "INFO",
            "PRODUCTION_COUPLED" if production_mode else "SHADOW_ONLY",
            "Kapalı çevrim sonucu senaryo bazlı üretim çalışma noktası ve fiziksel kayıp vektörüdür."
            if production_mode else
            "Kapalı çevrim sonucu mevcut IEC, bonding, termal üretim sonuçlarını ve proje λ1 değerini değiştirmez.",
        ),
        ElectroThermalIssue(
            "INFO",
            "TEMPERATURE_DEPENDENT_CORE_AND_SHEATH_RESISTANCE",
            "Her iterasyonda fiziksel core, metalik kılıf ve varsa GCC dirençleri çözülmüş sıcaklıklardan yeniden oluşturulur.",
        ),
        ElectroThermalIssue(
            "WARNING",
            "PROXIMITY_SCOPE_REMAINS_V0164",
            "Rac proximity bileşeni henüz tam N-kablo x-y proximity integralinden değil v0.16.4 fiziksel parametre kapsamından gelir.",
        ),
        ElectroThermalIssue(
            "WARNING",
            "ARMOUR_PHYSICS_PENDING",
            "Zırh kaybı fiziksel zırh ağı tamamlanana kadar legacy λ2 ile termal kaynağa eklenir.",
        ),
    ]
    if not converged:
        issues.append(ElectroThermalIssue(
            "WARNING",
            "CLOSED_LOOP_NOT_CONVERGED",
            "Kapalı çevrim verilen iterasyon sayısında bütün sıcaklık, akım ve kayıp toleranslarını birlikte sağlamadı.",
        ))

    if project.to_dict() != before:
        raise ElectroThermalInputError("Kapalı çevrim proje verisini değiştirdi; işlem iptal edildi.")

    return ElectroThermalCoupledResult(
        PRODUCTION_MODE if production_mode else MODE,
        COUPLING_MODE,
        REFERENCE,
        converged,
        len(iterations),
        maximum_iterations,
        float(temperature_tolerance_c),
        float(current_tolerance_percent),
        float(loss_tolerance_percent),
        float(relaxation_factor),
        tuple(iterations),
        final_em,
        final_thermal,
        core_state,
        sheath_state,
        tuple(issues),
        (
            "Sıcaklık durumu kesit ve fiziksel kablo kimliğiyle taşınır.",
            "Core sürekliliği, kılıf/link-box/GCC ağı ve gerçek x-y termal alan her dış iterasyonda yeniden çözülür.",
            "Kılıf sıcaklığı, iletken sıcaklığından T1 boyunca conductor + yarım dielektrik kayıp düşümüyle kestirilir ve jacket–conductor aralığına sınırlandırılır.",
            "Yakınsama kapısı sıcaklık sabit-nokta residual’ı, core/kılıf akım değişimi, aktif kayıp değişimi ve iki yöntem anlaşmasını birlikte ister.",
            f"thermal_method={thermal_method}; deenergized={','.join(deenergized_circuit_ids) or 'none'}",
        ),
        bool(production_mode),
        thermal_method,
        str(scenario_id),
        _loss_vector_fingerprint(final_thermal),
    )


def render_electrothermal_coupled(result: ElectroThermalCoupledResult) -> str:
    lines = result.trace_lines()
    lines.extend(["", "Final fiziksel kablo sonuçları:"])
    use_analytic = str(result.thermal_method).upper() == "ANALYTIC"
    for region in result.final_thermal.regions:
        for cable in region.cables:
            jacket = cable.analytical_jacket_temperature_c if use_analytic else cable.nodal_jacket_temperature_c
            conductor = cable.analytical_conductor_temperature_c if use_analytic else cable.nodal_conductor_temperature_c
            lines.append(
                f"{region.region_id}/{cable.physical_cable_id}: |Ic|={abs(cable.current_a):.6f} A; "
                f"Wc/Wsh={cable.conductor_loss_w_m:.6f}/{cable.sheath_loss_w_m:.6f} W/m; "
                f"Tj/Tc={jacket:.4f}/{conductor:.4f} °C"
            )
    return "\n".join(lines)


@dataclass(frozen=True)
class ElectroThermalAmpacityEvaluation:
    factor: float
    maximum_conductor_temperature_c: float
    closed_loop_converged: bool
    closed_loop_iterations: int


@dataclass
class ElectroThermalAmpacityResult:
    mode: str
    reference: str
    converged: bool
    temperature_limit_c: float
    rating_factor: float
    circuit_rating_currents_a: dict[str, float]
    critical_region_id: str
    critical_cable_id: str
    evaluations: tuple[ElectroThermalAmpacityEvaluation, ...]
    final_coupled_result: ElectroThermalCoupledResult
    issues: tuple[ElectroThermalIssue, ...]

    @property
    def final_design_ready(self) -> bool:
        return False


def _scaled_operating_project(project: ProjectData, factor: float) -> ProjectData:
    candidate = deepcopy(project)
    for section in candidate.installation_design.cross_sections:
        for circuit in section.circuits:
            if circuit.active:
                circuit.load_current_a = float(circuit.load_current_a) * float(factor)
    return candidate


def _base_circuit_currents(project: ProjectData) -> dict[str, float]:
    result: dict[str, float] = {}
    for section in project.installation_design.cross_sections:
        for circuit in section.circuits:
            if not circuit.active:
                continue
            value = float(circuit.load_current_a)
            if circuit.circuit_id in result and abs(result[circuit.circuit_id] - value) > 1e-9:
                raise ElectroThermalInputError(
                    f"{circuit.circuit_id}: kesitler arasında devre akım hedefi değişiyor; ortak rating faktörü uygulanamaz."
                )
            result[circuit.circuit_id] = value
    if not result or max(result.values(), default=0.0) <= 0.0:
        raise ElectroThermalInputError("Ampacity dış döngüsü için pozitif devre akımı tabanı bulunamadı.")
    return result


def solve_electrothermal_ampacity(
    project: ProjectData,
    *,
    mesh_scale: float = 2.5,
    maximum_closed_loop_iterations: int = 20,
    maximum_rating_iterations: int = 16,
    temperature_tolerance_c: float = 0.10,
    current_tolerance_a: float = 1.0,
    current_tolerance_percent: float = 0.10,
    loss_tolerance_percent: float = 0.10,
    relaxation_factor: float = 0.60,
    maximum_factor: float = 10.0,
) -> ElectroThermalAmpacityResult:
    """Find a common circuit-current multiplier at the conductor temperature limit."""

    before = project.to_dict()
    base_currents = _base_circuit_currents(project)
    temperature_limit = float(project.cable.max_temperature_c)
    if maximum_rating_iterations < 2:
        raise ElectroThermalInputError("Ampacity bisection en az iki iterasyon çalıştırmalıdır.")
    if current_tolerance_a <= 0.0 or maximum_factor <= 1.0:
        raise ElectroThermalInputError("Ampacity akım toleransı ve maksimum faktörü geçersiz.")

    evaluations: list[ElectroThermalAmpacityEvaluation] = []
    cache: dict[float, ElectroThermalCoupledResult] = {}

    def evaluate(factor: float) -> ElectroThermalCoupledResult:
        key = round(float(factor), 12)
        if key in cache:
            return cache[key]
        candidate = _scaled_operating_project(project, key)
        solved = solve_electrothermal_coupled(
            candidate,
            mesh_scale=mesh_scale,
            maximum_iterations=maximum_closed_loop_iterations,
            temperature_tolerance_c=min(temperature_tolerance_c, 0.08),
            current_tolerance_percent=current_tolerance_percent,
            loss_tolerance_percent=loss_tolerance_percent,
            relaxation_factor=relaxation_factor,
        )
        cache[key] = solved
        evaluations.append(ElectroThermalAmpacityEvaluation(
            key,
            float(solved.final_thermal.maximum_nodal_conductor_temperature_c),
            bool(solved.converged),
            int(solved.iteration_count),
        ))
        return solved

    low_factor = 0.0
    low_result = evaluate(low_factor)
    if low_result.final_thermal.maximum_nodal_conductor_temperature_c > temperature_limit + temperature_tolerance_c:
        raise ElectroThermalInputError(
            "Sıfır iletken akımında dahi dielektrik/harici ısı kaynakları sıcaklık sınırını aşıyor."
        )

    high_factor = 1.0
    high_result = evaluate(high_factor)
    while (
        high_result.final_thermal.maximum_nodal_conductor_temperature_c < temperature_limit
        and high_factor < maximum_factor
    ):
        low_factor, low_result = high_factor, high_result
        high_factor = min(maximum_factor, high_factor * 1.6)
        high_result = evaluate(high_factor)
    if high_result.final_thermal.maximum_nodal_conductor_temperature_c < temperature_limit:
        raise ElectroThermalInputError(
            f"Maksimum rating faktörü {maximum_factor:g} sıcaklık sınırını çevrelemedi; arama aralığı artırılmalıdır."
        )

    best_factor = low_factor
    best_result = low_result
    converged = False
    max_base = max(base_currents.values())
    for _ in range(maximum_rating_iterations):
        mid = 0.5 * (low_factor + high_factor)
        mid_result = evaluate(mid)
        temperature = mid_result.final_thermal.maximum_nodal_conductor_temperature_c
        if temperature <= temperature_limit:
            low_factor, low_result = mid, mid_result
            best_factor, best_result = mid, mid_result
        else:
            high_factor, high_result = mid, mid_result
        bracket_current = (high_factor - low_factor) * max_base
        if abs(temperature - temperature_limit) <= temperature_tolerance_c and bracket_current <= current_tolerance_a:
            best_factor = mid
            best_result = mid_result
            converged = True
            break
        if bracket_current <= current_tolerance_a:
            converged = True
            break

    critical_region = max(
        best_result.final_thermal.regions,
        key=lambda item: item.maximum_nodal_conductor_temperature_c,
    )
    critical_cable = max(
        critical_region.cables,
        key=lambda item: item.nodal_conductor_temperature_c,
    )
    issues = [
        ElectroThermalIssue(
            "INFO",
            "COMMON_CIRCUIT_SCALING_FACTOR",
            "Ampacity, bütün aktif devrelerin mevcut akım hedeflerine aynı çarpanın uygulanmasıyla bulunmuştur.",
        ),
        ElectroThermalIssue(
            "INFO",
            "SHADOW_ONLY",
            "Kapalı çevrim ampacity sonucu mevcut IEC/nodal rating sonuçlarına veya proje girdilerine yazılmaz.",
        ),
    ]
    if not converged:
        issues.append(ElectroThermalIssue(
            "WARNING",
            "AMPACITY_BISECTION_NOT_CONVERGED",
            "Ampacity dış döngüsü verilen iterasyon ve akım toleransı içinde kapanmadı.",
        ))
    if not best_result.converged:
        issues.append(ElectroThermalIssue(
            "WARNING",
            "INNER_CLOSED_LOOP_NOT_CONVERGED",
            "Seçilen rating adayında iç elektro-termal kapalı çevrim bütün toleransları sağlamadı.",
        ))

    if project.to_dict() != before:
        raise ElectroThermalInputError("SHADOW ampacity çözümü proje verisini değiştirdi; işlem iptal edildi.")

    return ElectroThermalAmpacityResult(
        MODE,
        REFERENCE,
        converged,
        temperature_limit,
        float(best_factor),
        {key: float(value * best_factor) for key, value in base_currents.items()},
        critical_region.region_id,
        critical_cable.physical_cable_id,
        tuple(evaluations),
        best_result,
        tuple(issues),
    )


def render_electrothermal_ampacity(result: ElectroThermalAmpacityResult) -> str:
    lines = [
        "DiTuS v0.16.7 — Elektro-Termal Kapalı Çevrim Ampacity Gölge Çözümü",
        f"Yakınsama={'EVET' if result.converged else 'HAYIR'}",
        f"Sıcaklık sınırı={result.temperature_limit_c:.4f} °C",
        f"Rating faktörü={result.rating_factor:.8f}",
        f"Kritik bölge/kablo={result.critical_region_id}/{result.critical_cable_id}",
    ]
    lines.extend(
        f"{circuit_id}: {current:.6f} A"
        for circuit_id, current in sorted(result.circuit_rating_currents_a.items())
    )
    lines.extend(
        f"factor={item.factor:.8f}; Tmax={item.maximum_conductor_temperature_c:.6f} °C; "
        f"inner={'PASS' if item.closed_loop_converged else 'FAIL'}; it={item.closed_loop_iterations}"
        for item in result.evaluations
    )
    lines.extend(f"{item.severity} {item.code}: {item.message}" for item in result.issues)
    return "\n".join(lines)
