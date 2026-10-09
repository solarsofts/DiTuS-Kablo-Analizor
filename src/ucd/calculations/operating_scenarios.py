"""Scenario-resolved physical-cable operating points for production coupling.

FAZ 6.1/6.2 separates physical presence, energization and RMS current.  The
module is intentionally model-schema neutral: it derives immutable operating
scenarios from the existing project and applies them to deep-copied solver
inputs without mutating the project.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from math import pi
from typing import Iterable

from ucd.calculations.installation import phase_angle_deg
from ucd.models.project import ProjectData


class OperatingScenarioInputError(ValueError):
    pass


@dataclass(frozen=True)
class CircuitOperatingState:
    circuit_id: str
    physically_present: bool
    energized: bool
    phase_current_a: float
    source: str


@dataclass(frozen=True)
class PhysicalCableOperatingPoint:
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    physically_present: bool
    energized: bool
    current_phasor_a: complex
    source: str

    @property
    def key(self) -> str:
        return f"{self.circuit_id}:{self.phase}:P{self.parallel_index}"


@dataclass(frozen=True)
class ResolvedOperatingScenario:
    scenario_id: str
    scenario_name: str
    circuit_states: tuple[CircuitOperatingState, ...]
    physical_cable_points: tuple[PhysicalCableOperatingPoint, ...]
    equivalent_scenario_ids: tuple[str, ...] = ()
    equivalent_scenario_names: tuple[str, ...] = ()
    target_circuit_ids: tuple[str, ...] = ()
    scale_mode: str = "OPERATING_POINT"
    trace: tuple[str, ...] = ()

    @property
    def deenergized_circuit_ids(self) -> tuple[str, ...]:
        return tuple(item.circuit_id for item in self.circuit_states if not item.energized)

    @property
    def fingerprint(self) -> str:
        payload = {
            "scenario_id": self.scenario_id,
            "circuit_states": [
                {
                    "circuit_id": item.circuit_id,
                    "present": item.physically_present,
                    "energized": item.energized,
                    "current": round(item.phase_current_a, 9),
                }
                for item in self.circuit_states
            ],
            "physical": [
                {
                    "id": item.physical_cable_id,
                    "key": item.key,
                    "present": item.physically_present,
                    "energized": item.energized,
                    "current_re": round(item.current_phasor_a.real, 9),
                    "current_im": round(item.current_phasor_a.imag, 9),
                }
                for item in self.physical_cable_points
            ],
            "target": list(self.target_circuit_ids),
            "scale_mode": self.scale_mode,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "OPS-" + hashlib.sha256(raw).hexdigest()[:16].upper()


def _circuit_ids(project: ProjectData) -> tuple[str, ...]:
    values: list[str] = []
    for section in project.installation_design.cross_sections:
        for circuit in section.circuits:
            key = str(circuit.circuit_id).strip()
            if circuit.active and key and key not in values:
                values.append(key)
    if not values:
        raise OperatingScenarioInputError("Etkin fiziksel devre bulunamadı.")
    return tuple(values)


def _base_circuit_currents(project: ProjectData, circuit_ids: Iterable[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for section in project.installation_design.cross_sections:
        for circuit in section.circuits:
            if not circuit.active or circuit.circuit_id not in circuit_ids:
                continue
            current = max(0.0, float(circuit.load_current_a))
            prior = values.get(circuit.circuit_id)
            if prior is not None and abs(prior - current) > 1e-9:
                raise OperatingScenarioInputError(
                    f"{circuit.circuit_id}: kesitler arasında devre akımı değişiyor ({prior:g}/{current:g} A)."
                )
            values[circuit.circuit_id] = current
    fallback = max(0.0, float(project.cable.design_current_a))
    return {key: values.get(key, fallback) for key in circuit_ids}


def _physical_points(
    project: ProjectData,
    states: dict[str, CircuitOperatingState],
) -> tuple[PhysicalCableOperatingPoint, ...]:
    canonical: dict[str, PhysicalCableOperatingPoint] = {}
    for section in project.installation_design.cross_sections:
        circuits = {item.circuit_id: item for item in section.circuits if item.active}
        members: dict[tuple[str, str], list[object]] = {}
        for item in section.physical_cables:
            if not item.active or item.circuit_id not in circuits:
                continue
            phase = str(item.phase).upper()
            if phase not in {"A", "B", "C"}:
                continue
            members.setdefault((item.circuit_id, phase), []).append(item)
        for (circuit_id, phase), cables in members.items():
            state = states[circuit_id]
            default_share = state.phase_current_a / max(1, len(cables))
            for item in cables:
                override = float(item.current_override_a)
                explicit_override = override > 0.0 or item.current_angle_override_deg is not None
                magnitude = override if explicit_override else default_share
                angle = (
                    float(item.current_angle_override_deg)
                    if item.current_angle_override_deg is not None
                    else phase_angle_deg(phase)
                )
                if not state.energized:
                    magnitude = 0.0
                point = PhysicalCableOperatingPoint(
                    str(item.physical_cable_id),
                    circuit_id,
                    phase,
                    int(item.parallel_index),
                    True,
                    state.energized,
                    complex(magnitude * __import__("math").cos(angle * pi / 180.0), magnitude * __import__("math").sin(angle * pi / 180.0)),
                    "PHYSICAL_OVERRIDE" if explicit_override else state.source,
                )
                prior = canonical.get(point.physical_cable_id)
                if prior is not None and abs(prior.current_phasor_a - point.current_phasor_a) > 1e-8:
                    raise OperatingScenarioInputError(
                        f"{point.physical_cable_id}: kesitler arasında fiziksel kablo akım hedefi değişiyor."
                    )
                canonical[point.physical_cable_id] = point
    return tuple(sorted(canonical.values(), key=lambda item: (item.circuit_id, item.phase, item.parallel_index, item.physical_cable_id)))


def _build_scenario(
    project: ProjectData,
    scenario_id: str,
    scenario_name: str,
    currents: dict[str, float],
    energized: dict[str, bool],
    source: str,
    *,
    target_circuit_ids: tuple[str, ...] = (),
    scale_mode: str = "OPERATING_POINT",
) -> ResolvedOperatingScenario:
    ids = _circuit_ids(project)
    states = {
        circuit_id: CircuitOperatingState(
            circuit_id,
            True,
            bool(energized.get(circuit_id, True)),
            max(0.0, float(currents.get(circuit_id, 0.0))) if energized.get(circuit_id, True) else 0.0,
            source,
        )
        for circuit_id in ids
    }
    points = _physical_points(project, states)
    trace = (
        f"Senaryo={scenario_id}; ölçek={scale_mode}",
        "Devre durumları=" + ", ".join(
            f"{item.circuit_id}:{'ENERGIZED' if item.energized else 'DEENERGIZED'}:{item.phase_current_a:.6f}A"
            for item in states.values()
        ),
        "load_factor kararlı durum akımına uygulanmadı; fiziksel akım alanları RMS çalışma noktasıdır.",
    )
    return ResolvedOperatingScenario(
        scenario_id,
        scenario_name,
        tuple(states[key] for key in ids),
        points,
        (scenario_id,),
        (scenario_name,),
        tuple(target_circuit_ids),
        scale_mode,
        trace,
    )


def resolve_operating_scenarios(project: ProjectData) -> tuple[ResolvedOperatingScenario, ...]:
    """Resolve NORMAL, DESIGN and explicit circuit-out N-1 operating vectors."""

    ids = _circuit_ids(project)
    base = _base_circuit_currents(project, ids)
    basis = project.design_basis
    normal_scalar = max(0.0, float(basis.normal_current_per_active_circuit_a or 0.0))
    design_scalar = max(0.0, float(basis.design_current_per_circuit_a or project.cable.design_current_a or 0.0))
    n1_scalar = max(0.0, float(basis.n1_current_per_circuit_a or design_scalar or normal_scalar or 0.0))

    normal_currents = {key: (normal_scalar if normal_scalar > 0.0 else base[key]) for key in ids}
    design_currents = {key: (design_scalar if design_scalar > 0.0 else base[key]) for key in ids}
    energized_all = {key: True for key in ids}
    raw: list[ResolvedOperatingScenario] = [
        _build_scenario(project, "NORMAL", "Normal işletme", normal_currents, energized_all, "NORMAL_OPERATING_CURRENT"),
        _build_scenario(project, "DESIGN", "Tasarım-marjlı", design_currents, energized_all, "DESIGN_OPERATING_CURRENT"),
    ]
    for outage in ids:
        currents = {key: (0.0 if key == outage else n1_scalar) for key in ids}
        energized = {key: key != outage for key in ids}
        raw.append(_build_scenario(
            project,
            f"N_MINUS_ONE_{outage}_OUT",
            f"N-1 — {outage} devre dışı",
            currents,
            energized,
            "N_MINUS_ONE_TRANSFER_CURRENT",
            target_circuit_ids=tuple(key for key in ids if key != outage),
            scale_mode="TARGET_CIRCUIT_SCALE",
        ))

    # Deduplicate only physically identical full vectors.  Circuit-out scenarios
    # retain their identity because their energization maps differ.
    priority = {"NORMAL": 1, "DESIGN": 2}
    groups: dict[tuple[object, ...], list[ResolvedOperatingScenario]] = {}
    for scenario in raw:
        signature = tuple(
            (item.circuit_id, item.energized, round(item.phase_current_a, 9))
            for item in scenario.circuit_states
        )
        groups.setdefault(signature, []).append(scenario)
    result: list[ResolvedOperatingScenario] = []
    for scenarios in groups.values():
        canonical = max(scenarios, key=lambda item: priority.get(item.scenario_id, 3))
        aliases = tuple(item.scenario_id for item in scenarios)
        names = tuple(item.scenario_name for item in scenarios)
        result.append(ResolvedOperatingScenario(
            canonical.scenario_id,
            canonical.scenario_name,
            canonical.circuit_states,
            canonical.physical_cable_points,
            aliases,
            names,
            canonical.target_circuit_ids,
            canonical.scale_mode,
            canonical.trace + (("Eşdeğer çalışma noktaları=" + ", ".join(aliases)) if len(aliases) > 1 else "Eşdeğer çalışma noktası yok.",),
        ))
    order = {"NORMAL": 0, "DESIGN": 1}
    result.sort(key=lambda item: (order.get(item.scenario_id, 2), item.scenario_id))
    return tuple(result)


def apply_operating_scenario(project: ProjectData, scenario: ResolvedOperatingScenario) -> ProjectData:
    """Return an isolated solver project with scenario RMS currents applied."""

    candidate = deepcopy(project)
    states = {item.circuit_id: item for item in scenario.circuit_states}
    point_by_id = {item.physical_cable_id: item for item in scenario.physical_cable_points}
    for section in candidate.installation_design.cross_sections:
        for circuit in section.circuits:
            state = states.get(circuit.circuit_id)
            if state is None or not circuit.active:
                continue
            circuit.load_current_a = float(state.phase_current_a)
            # Legacy load_factor is deliberately preserved but ignored by
            # steady-state production physics. IEC 60853 μ belongs to the
            # transient load profile, not to this RMS operating point.
        for cable in section.physical_cables:
            point = point_by_id.get(cable.physical_cable_id)
            if point is None or point.source != "PHYSICAL_OVERRIDE":
                continue
            cable.current_override_a = abs(point.current_phasor_a)
            cable.current_angle_override_deg = (
                __import__("math").degrees(__import__("cmath").phase(point.current_phasor_a))
                if abs(point.current_phasor_a) > 1e-15 else phase_angle_deg(point.phase)
            )
    active_currents = [item.phase_current_a for item in scenario.circuit_states if item.energized]
    candidate.cable.design_current_a = max(active_currents or [0.0])
    return candidate
