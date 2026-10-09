"""Power-frequency fault, EPR and SVL-TOV study on the primitive CIM/NV network.

The solver intentionally reuses the exact same physical sheath/GCC/grounding
network as the normal-load primitive calculation.  The phase-conductor current
phasors are replaced by the selected fault current set and dielectric charging
is normally disabled for the short-duration fault duty.

This is a power-frequency network study.  It is not an EMT travelling-wave,
arc, nonlinear MOV or frequency-dependent Pollaczek/Wedepohl-Wilcox model.
"""

from __future__ import annotations

from ucd.calculations.model_applicability import require_production_physics

import cmath
from dataclasses import dataclass
from math import pi
from typing import Iterable

from ucd.calculations.primitive_cim import (
    PrimitiveNetworkError,
    PrimitiveNetworkResult,
    solve_primitive_network,
)
from ucd.models.project import (
    FAULT_PHASE_PHASE,
    FAULT_SINGLE_PHASE_GROUND,
    FAULT_THREE_PHASE,
    BondingSystemData,
    CableData,
    FaultScenario,
    FaultStudyData,
    RouteSection,
    SvlSystemData,
)

REFERENCE = (
    "CIGRE TB 797 power-frequency bonding-network architecture; "
    "IEEE 575 Annex E fault-voltage classification; primitive CIM/NV model"
)


class FaultStudyError(ValueError):
    pass


@dataclass(frozen=True)
class FaultGroundPointResult:
    node_id: str
    bus_label: str
    earth_current_a: complex
    epr_v: complex
    earth_resistance_ohm: float


@dataclass(frozen=True)
class FaultScenarioResult:
    scenario_id: str
    name: str
    fault_type: str
    fault_current_a: float
    duration_s: float
    phase_currents_a: tuple[complex, complex, complex]
    maximum_sheath_current_a: float
    maximum_gcc_current_a: float
    maximum_sheath_to_local_ground_v: float
    maximum_sectionalizing_interrupt_v: float
    maximum_epr_v: float
    maximum_earth_electrode_current_a: float
    total_earth_electrode_current_magnitude_a: float
    total_sheath_metal_loss_w: float
    total_gcc_metal_loss_w: float
    total_earth_return_loss_w: float
    methods_agree: bool
    cim_nv_voltage_difference_v: float
    cim_nv_current_difference_a: float
    ground_points: tuple[FaultGroundPointResult, ...]
    primitive: PrimitiveNetworkResult
    notes: tuple[str, ...]

    @property
    def governing_tov_rms_v(self) -> float:
        return max(self.maximum_sheath_to_local_ground_v, self.maximum_sectionalizing_interrupt_v)


@dataclass(frozen=True)
class FaultStudyResult:
    reference: str
    selected_method: str
    scenario_results: tuple[FaultScenarioResult, ...]
    governing_scenario_id: str
    governing_scenario_name: str
    governing_tov_rms_v: float
    governing_duration_s: float
    maximum_epr_v: float
    maximum_sheath_current_a: float
    maximum_gcc_current_a: float
    all_methods_agree: bool
    notes: tuple[str, ...]

    def trace_lines(self) -> list[str]:
        lines = [
            f"Arıza/EPR referansı: {self.reference}",
            f"Çözüm yöntemi: {self.selected_method}",
            f"Yönetici senaryo: {self.governing_scenario_name} [{self.governing_scenario_id}]",
            f"Yönetici power-frequency TOV={self.governing_tov_rms_v:.3f} V rms / "
            f"{self.governing_duration_s:.3f} s",
            f"Maksimum EPR={self.maximum_epr_v:.3f} V",
            f"Maksimum sheath akımı={self.maximum_sheath_current_a:.3f} A",
            f"Maksimum GCC/ECC akımı={self.maximum_gcc_current_a:.3f} A",
            f"CIM↔NV tüm senaryolar={'PASS' if self.all_methods_agree else 'FAIL'}",
        ]
        for result in self.scenario_results:
            currents = "/".join(f"{abs(v):.1f}∠{_angle(v):.1f}°" for v in result.phase_currents_a)
            lines.append(
                f"{result.scenario_id} {result.name}: Iabc={currents} A; "
                f"Ish,max={result.maximum_sheath_current_a:.3f} A; "
                f"IGCC,max={result.maximum_gcc_current_a:.3f} A; "
                f"Vsh-g,max={result.maximum_sheath_to_local_ground_v:.3f} V; "
                f"Vinterrupt,max={result.maximum_sectionalizing_interrupt_v:.3f} V; "
                f"EPR,max={result.maximum_epr_v:.3f} V"
            )
            for point in result.ground_points:
                lines.append(
                    f"  {point.node_id}: Iearth={abs(point.earth_current_a):.3f} A; "
                    f"EPR={abs(point.epr_v):.3f} V; Rg={point.earth_resistance_ohm:.4f} Ω"
                )
        lines.extend(f"Not: {note}" for note in self.notes)
        return lines


def _angle(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


def _phase_set(scenario: FaultScenario) -> dict[str, complex]:
    magnitude = float(scenario.fault_current_a)
    if magnitude <= 0:
        raise FaultStudyError(f"{scenario.scenario_id} arıza akımı sıfırdan büyük olmalı.")
    kind = scenario.fault_type.strip().upper()
    phase = scenario.faulted_phase.strip().upper()
    second = scenario.second_phase.strip().upper()
    if phase not in "ABC" or second not in "ABC":
        raise FaultStudyError(f"{scenario.scenario_id} fazları A/B/C olmalı.")
    if kind == FAULT_THREE_PHASE:
        return {
            "A": cmath.rect(magnitude, 0.0),
            "B": cmath.rect(magnitude, -2.0 * pi / 3.0),
            "C": cmath.rect(magnitude, 2.0 * pi / 3.0),
        }
    if kind == FAULT_PHASE_PHASE:
        if phase == second:
            raise FaultStudyError(f"{scenario.scenario_id} faz-faz arızasında iki farklı faz gerekli.")
        values = {"A": 0j, "B": 0j, "C": 0j}
        values[phase] = complex(magnitude, 0.0)
        values[second] = complex(-magnitude, 0.0)
        return values
    if kind == FAULT_SINGLE_PHASE_GROUND:
        values = {"A": 0j, "B": 0j, "C": 0j}
        values[phase] = complex(magnitude, 0.0)
        return values
    raise FaultStudyError(f"Desteklenmeyen arıza tipi: {scenario.fault_type}")


def _active_voltages(result: PrimitiveNetworkResult) -> dict[str, complex]:
    method = result.cim if result.selected_method == "PRIMITIVE_CIM" else result.nv
    return dict(zip(result.node_labels, method.node_voltages_v))


def _ground_points(result: PrimitiveNetworkResult, bonding: BondingSystemData) -> tuple[FaultGroundPointResult, ...]:
    voltage_map = _active_voltages(result)
    node_map = {node.node_id: node for node in bonding.nodes}
    points: list[FaultGroundPointResult] = []
    for branch in result.accessory_branches:
        if branch.branch_type != "EARTH":
            continue
        label = branch.from_label
        node_id = label.split(":", 1)[1] if ":" in label else label
        node = node_map.get(node_id)
        earth_r = float(node.earth_resistance_ohm) if node is not None else max(branch.impedance_ohm.real, 0.0)
        points.append(
            FaultGroundPointResult(
                node_id=node_id,
                bus_label=label,
                earth_current_a=branch.current_a,
                epr_v=voltage_map.get(label, branch.current_a * branch.impedance_ohm),
                earth_resistance_ohm=earth_r,
            )
        )
    return tuple(points)


def _local_epr(node_id: str, voltage_map: dict[str, complex]) -> complex:
    for label in (f"BUS:{node_id}", f"GCCBUS:{node_id}"):
        if label in voltage_map:
            return voltage_map[label]
    return 0j


def _voltage_duties(
    primitive: PrimitiveNetworkResult,
    bonding: BondingSystemData,
) -> tuple[float, float]:
    """Return maximum sheath-local-ground and joint-interrupt rms voltage."""

    minor_map = {minor.section_id: minor for minor in bonding.minor_sections}
    section_map = {section.section_id: section for section in primitive.section_results}
    voltage_map = _active_voltages(primitive)
    max_to_ground = 0.0
    ordered = sorted(
        bonding.minor_sections,
        key=lambda item: next(
            (node.position_m for node in bonding.nodes if node.node_id == item.start_node_id), 0.0
        ),
    )
    for minor in ordered:
        section = section_map.get(minor.section_id)
        if section is None:
            continue
        start_epr = _local_epr(minor.start_node_id, voltage_map)
        end_epr = _local_epr(minor.end_node_id, voltage_map)
        max_to_ground = max(
            max_to_ground,
            *(abs(value - start_epr) for value in section.start_sheath_voltages_v),
            *(abs(value - end_epr) for value in section.end_sheath_voltages_v),
        )

    max_interrupt = 0.0
    for left, right in zip(ordered, ordered[1:]):
        if left.end_node_id != right.start_node_id:
            continue
        lres = section_map.get(left.section_id)
        rres = section_map.get(right.section_id)
        if lres is None or rres is None:
            continue
        max_interrupt = max(
            max_interrupt,
            *(abs(lres.end_sheath_voltages_v[i] - rres.start_sheath_voltages_v[i]) for i in range(3)),
        )
    return float(max_to_ground), float(max_interrupt)


def solve_fault_study(
    cable: CableData,
    bonding: BondingSystemData,
    route_sections: Iterable[RouteSection],
    study: FaultStudyData,
) -> FaultStudyResult:
    try:
        require_production_physics(cable, engine_label="arıza/EPR")
    except ValueError as exc:
        raise FaultStudyError(str(exc)) from exc
    routes = list(route_sections)
    scenarios = [scenario for scenario in study.scenarios if scenario.enabled]
    if not scenarios:
        raise FaultStudyError("Etkin arıza senaryosu yok.")
    selected = study.solver_mode.strip().upper()
    if selected not in {"PRIMITIVE_CIM", "NODE_VOLTAGE"}:
        raise FaultStudyError("Arıza çözümü PRIMITIVE_CIM veya NODE_VOLTAGE olmalı.")

    results: list[FaultScenarioResult] = []
    for scenario in scenarios:
        if scenario.duration_s <= 0:
            raise FaultStudyError(f"{scenario.scenario_id} arıza süresi sıfırdan büyük olmalı.")
        phase_currents = _phase_set(scenario)
        try:
            primitive = solve_primitive_network(
                cable,
                bonding,
                routes,
                selected_method=selected,
                phase_currents_a=phase_currents,
                phase_voltages_v={"A": 0j, "B": 0j, "C": 0j},
                include_dielectric_charging=study.include_dielectric_charging_during_fault,
            )
        except PrimitiveNetworkError as exc:
            raise FaultStudyError(str(exc)) from exc
        points = _ground_points(primitive, bonding)
        max_to_ground, max_interrupt = _voltage_duties(primitive, bonding)
        notes = [
            "Core fault currents are imposed as known longitudinal phasors over the modeled cable route.",
            "Ground electrodes are lumped resistances to remote earth; soil surface potential gradients/touch-step contours are not yet solved.",
            "The result is power-frequency TOV/EPR duty, not high-frequency travelling-wave or nonlinear SVL energy duty.",
        ]
        if scenario.fault_type == FAULT_SINGLE_PHASE_GROUND and not bonding.gcc_enabled:
            notes.append("Tek faz-toprak sonucunda explicit GCC/ECC yok; dönüş akımı metalik kılıflar ve toplu toprak dalları üzerinden paylaşılır.")
        results.append(
            FaultScenarioResult(
                scenario.scenario_id,
                scenario.name,
                scenario.fault_type,
                float(scenario.fault_current_a),
                float(scenario.duration_s),
                tuple(phase_currents[p] for p in "ABC"),
                primitive.maximum_sheath_current_a,
                primitive.maximum_gcc_current_a,
                max_to_ground,
                max_interrupt,
                max((abs(point.epr_v) for point in points), default=0.0),
                max((abs(point.earth_current_a) for point in points), default=0.0),
                sum(abs(point.earth_current_a) for point in points),
                primitive.total_sheath_metal_loss_w,
                primitive.total_gcc_metal_loss_w,
                primitive.total_earth_return_loss_w,
                primitive.methods_agree,
                primitive.maximum_method_voltage_difference_v,
                primitive.maximum_method_current_difference_a,
                points,
                primitive,
                tuple(notes),
            )
        )

    governing = max(results, key=lambda item: item.governing_tov_rms_v)
    notes = (
        "SVL TOV aktarımı, metalik-kılıf–yerel-toprak ve sectionalizing-interrupt güç frekansı rms gerilimlerinin büyüğünü kullanır.",
        "Fault clearing duration remains a project/protection input; the optional multiplier is applied only when transferring duty to SVL selection.",
        "Full Pollaczek/Wedepohl-Wilcox earth return, distributed grounding-grid EPR and EMT remain later validation layers.",
    )
    return FaultStudyResult(
        REFERENCE,
        selected,
        tuple(results),
        governing.scenario_id,
        governing.name,
        governing.governing_tov_rms_v,
        governing.duration_s,
        max(result.maximum_epr_v for result in results),
        max(result.maximum_sheath_current_a for result in results),
        max(result.maximum_gcc_current_a for result in results),
        all(result.methods_agree for result in results),
        notes,
    )


def transfer_fault_tov_to_svl(
    study: FaultStudyData,
    result: FaultStudyResult,
    svl: SvlSystemData,
) -> tuple[float, float]:
    multiplier = float(study.tov_duration_multiplier)
    if multiplier <= 0:
        raise FaultStudyError("SVL TOV süre çarpanı sıfırdan büyük olmalı.")
    svl.fault_tov_rms_v = float(result.governing_tov_rms_v)
    svl.fault_tov_duration_s = float(result.governing_duration_s * multiplier)
    return svl.fault_tov_rms_v, svl.fault_tov_duration_s
