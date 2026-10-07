from __future__ import annotations

"""Production scenario × physical-cable electro-thermal coordinator.

This module promotes the existing independently validated global N-core/N-
sheath and real-x/y thermal kernels into a read-only production operating-point
workflow.  It does not mutate project λ1; λ1 is derived from physical loss
components and is not defined for zero conductor loss.
"""

from dataclasses import dataclass, replace
from typing import Iterable

from ucd.calculations.electrothermal_coupled import (
    ElectroThermalCoupledResult,
    ElectroThermalInputError,
    _loss_vector_fingerprint,
    solve_electrothermal_coupled,
)
from ucd.calculations.multiconductor_thermal import solve_multiconductor_thermal
from ucd.calculations.soil_dryout import SoilDryoutInputError, material_dryout_profile
from ucd.calculations.thermal_route import ThermalRouteInputError, resolve_thermal_region
from ucd.calculations.sheath_loss_completeness import AUTHORITY_FULL, resolve_sheath_loss_completeness
from ucd.calculations.operating_scenarios import (
    CircuitOperatingState,
    ResolvedOperatingScenario,
    apply_operating_scenario,
    resolve_operating_scenarios,
)
from ucd.models.project import ProjectData


class ProductionElectroThermalInputError(ValueError):
    pass


@dataclass(frozen=True)
class ProductionCableOperatingResult:
    region_id: str
    cross_section_id: str
    physical_cable_id: str
    circuit_id: str
    phase: str
    parallel_index: int
    current_a: complex
    conductor_temperature_c: float
    sheath_temperature_c: float
    conductor_loss_w_m: float
    sheath_loss_w_m: float
    armour_loss_w_m: float
    dielectric_loss_w_m: float
    total_loss_w_m: float
    lambda1: float | None
    mutual_external_temperature_rise_c: float


@dataclass(frozen=True)
class ProductionRegionOperatingResult:
    region_id: str
    cross_section_id: str
    maximum_conductor_temperature_c: float
    critical_physical_cable_id: str
    loss_vector_w_m: tuple[float, ...]
    external_temperature_rise_vector_c: tuple[float, ...]
    cables: tuple[ProductionCableOperatingResult, ...]


@dataclass(frozen=True)
class ProductionScenarioResult:
    scenario: ResolvedOperatingScenario
    converged: bool
    completion_status: str
    suitability_status: str
    maximum_conductor_temperature_c: float | None
    critical_region_id: str
    critical_physical_cable_id: str
    global_lambda1: float | None
    loss_vector_fingerprint: str
    regions: tuple[ProductionRegionOperatingResult, ...]
    coupled_result: ElectroThermalCoupledResult | None
    error_code: str = ""
    error_message: str = ""
    trace: tuple[str, ...] = ()
    thermal_method: str = "ANALYTIC"
    dryout_material_ids: tuple[str, ...] = ()
    network_sheath_loss_ratio: float | None = None
    lambda1_eddy: float | None = None
    lambda1_rating: float | None = None
    sheath_loss_authority: str = "UNKNOWN"
    sheath_loss_reason_codes: tuple[str, ...] = ()
    sheath_loss_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductionElectroThermalStudyResult:
    scenarios: tuple[ProductionScenarioResult, ...]
    active_scenario_id: str
    reference: str

    @property
    def active(self) -> ProductionScenarioResult:
        for item in self.scenarios:
            if item.scenario.scenario_id == self.active_scenario_id:
                return item
        return self.scenarios[-1]




@dataclass(frozen=True)
class ProductionThermalMethodComparison:
    scenario_id: str
    loss_vector_fingerprint: str
    analytical_maximum_temperature_c: float
    nodal_maximum_temperature_c: float
    temperature_difference_c: float
    nodal_energy_balance_error_percent: float
    nodal_maximum_linear_residual: float
    same_loss_vector: bool
    validation_status: str
    trace: tuple[str, ...] = ()

@dataclass(frozen=True)
class ProductionAmpacityEvaluation:
    factor: float
    maximum_conductor_temperature_c: float
    converged: bool


@dataclass(frozen=True)
class ProductionAmpacityResult:
    scenario_id: str
    scale_mode: str
    target_circuit_ids: tuple[str, ...]
    converged: bool
    rating_factor: float
    circuit_rating_currents_a: tuple[tuple[str, float], ...]
    critical_region_id: str
    critical_physical_cable_id: str
    evaluations: tuple[ProductionAmpacityEvaluation, ...]
    final_result: ProductionScenarioResult


REFERENCE = (
    "IEC 60287-1-1/-1-3 physical losses; IEC 60287-2-1 real-x/y thermal matrix; "
    "IEEE 575 route bonding; scenario-resolved closed-loop production operating point"
)


def _scenario_from_states(
    base: ResolvedOperatingScenario,
    states: Iterable[CircuitOperatingState],
    *,
    scenario_id: str | None = None,
    scenario_name: str | None = None,
) -> ResolvedOperatingScenario:
    state_tuple = tuple(states)
    by_circuit = {item.circuit_id: item for item in state_tuple}
    points = tuple(
        replace(
            item,
            energized=by_circuit[item.circuit_id].energized,
            current_phasor_a=(
                0j
                if not by_circuit[item.circuit_id].energized
                else item.current_phasor_a
                * (
                    by_circuit[item.circuit_id].phase_current_a
                    / max(next(s.phase_current_a for s in base.circuit_states if s.circuit_id == item.circuit_id), 1e-12)
                )
            ),
        )
        for item in base.physical_cable_points
    )
    return replace(
        base,
        scenario_id=scenario_id or base.scenario_id,
        scenario_name=scenario_name or base.scenario_name,
        circuit_states=state_tuple,
        physical_cable_points=points,
        equivalent_scenario_ids=(scenario_id or base.scenario_id,),
        equivalent_scenario_names=(scenario_name or base.scenario_name,),
    )


def _project_requires_nodal_dryout(
    project: ProjectData,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    material_ids: set[str] = set()
    unresolved: list[str] = []
    for region in project.thermal_design.regions:
        if not region.enabled:
            continue
        try:
            profile = resolve_thermal_region(project.thermal_design, region, project.cable)
        except (ThermalRouteInputError, ValueError, TypeError) as exc:
            # An unresolved region cannot prove that it has no dryout data, so
            # it must not let the analytical preview through silently.
            unresolved.append(f"DRYOUT_GATE_REGION_UNRESOLVED[{region.region_id}]: {exc}")
            continue
        for material in (
            profile.native_soil, profile.bedding, profile.side_backfill, profile.cable_cover,
            profile.selected_upper_fill, profile.general_fill, profile.surface,
        ):
            if material is None:
                continue
            try:
                dryout = material_dryout_profile(material)
            except SoilDryoutInputError:
                # Validation will surface the detailed input error. Force nodal
                # selection so the analytical preview cannot silently ignore it.
                return True, (str(material.material_id),), tuple(unresolved)
            if dryout is not None:
                material_ids.add(str(material.material_id))
    return bool(material_ids or unresolved), tuple(sorted(material_ids)), tuple(unresolved)


def _region_results(coupled: ElectroThermalCoupledResult) -> tuple[ProductionRegionOperatingResult, ...]:
    sheath_temp_by_section = coupled.sheath_temperatures_c_by_cross_section
    rows: list[ProductionRegionOperatingResult] = []
    use_nodal = str(coupled.thermal_method).upper() == "NODAL"
    for region in coupled.final_thermal.regions:
        rmat = region.analytical_matrix_km_w
        losses = tuple(float(item.total_loss_w_m) for item in region.cables)
        external = tuple(
            sum(float(rmat[i][j]) * losses[j] for j in range(len(losses)))
            + float(region.analytical_external_source_rise_c[i])
            for i in range(len(losses))
        )
        cables: list[ProductionCableOperatingResult] = []
        for index, item in enumerate(region.cables):
            core_loss = float(item.conductor_loss_w_m)
            sheath_loss = float(item.sheath_loss_w_m)
            lambda1 = sheath_loss / core_loss if core_loss > 1e-15 else None
            cables.append(ProductionCableOperatingResult(
                region.region_id,
                region.cross_section_id,
                item.physical_cable_id,
                item.circuit_id,
                item.phase,
                item.parallel_index,
                item.current_a,
                float(
                    item.nodal_conductor_temperature_c
                    if use_nodal and region.nodal_computed
                    else item.analytical_conductor_temperature_c
                ),
                float(sheath_temp_by_section.get(region.cross_section_id, {}).get(
                    item.physical_cable_id,
                    item.nodal_jacket_temperature_c
                    if use_nodal and region.nodal_computed
                    else item.analytical_jacket_temperature_c
                )),
                core_loss,
                sheath_loss,
                float(item.armour_loss_w_m),
                float(item.dielectric_loss_w_m),
                float(item.total_loss_w_m),
                lambda1,
                float(external[index]),
            ))
        critical = max(cables, key=lambda item: item.conductor_temperature_c)
        rows.append(ProductionRegionOperatingResult(
            region.region_id,
            region.cross_section_id,
            float(critical.conductor_temperature_c),
            critical.physical_cable_id,
            losses,
            external,
            tuple(cables),
        ))
    return tuple(rows)


def _sheath_completeness_summary(project: ProjectData, coupled: ElectroThermalCoupledResult) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    authorities = []
    reasons: list[str] = []
    sources: list[str] = []
    core_maps = coupled.core_temperatures_c_by_cross_section
    sheath_maps = coupled.sheath_temperatures_c_by_cross_section
    for section in project.installation_design.cross_sections:
        if not any(item.active for item in section.physical_cables):
            continue
        decision = resolve_sheath_loss_completeness(
            project, section,
            conductor_temperatures_c=core_maps.get(section.cross_section_id),
            sheath_temperatures_c=sheath_maps.get(section.cross_section_id),
        )
        authorities.append(decision.authority)
        reasons.extend(decision.reason_codes)
        sources.append(decision.eddy_source)
    authority = AUTHORITY_FULL if authorities and all(item == AUTHORITY_FULL for item in authorities) else "BLOCKED"
    return authority, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(sources))


def solve_production_operating_scenario(
    project: ProjectData,
    scenario: ResolvedOperatingScenario,
    *,
    maximum_iterations: int = 20,
    temperature_tolerance_c: float = 0.05,
    current_tolerance_percent: float = 0.10,
    loss_tolerance_percent: float = 0.10,
    relaxation_factor: float = 0.60,
    thermal_method: str = "AUTO",
) -> ProductionScenarioResult:
    candidate = apply_operating_scenario(project, scenario)
    requested_method = str(thermal_method).strip().upper()
    requires_dryout_nodal, dryout_material_ids, dryout_gate_reasons = _project_requires_nodal_dryout(candidate)
    if requested_method == "AUTO":
        resolved_method = "NODAL" if requires_dryout_nodal else "ANALYTIC"
    elif requested_method in {"ANALYTIC", "NODAL"}:
        resolved_method = requested_method
    else:
        raise ProductionElectroThermalInputError("thermal_method AUTO, ANALYTIC veya NODAL olmalıdır.")
    if requires_dryout_nodal and resolved_method == "ANALYTIC":
        return ProductionScenarioResult(
            scenario, False, "FAILED", "INDETERMINATE", None, "", "", None, "", (), None,
            "ANALYTIC_DRYOUT_REQUIRES_NODAL",
            "Kritik-izoterm kuruma verisi çok kablolu üretim geometrisinde nodal çözüm gerektirir."
            + (
                " Termal bölge çözülemediği için kuruma verisi doğrulanamadı: " + "; ".join(dryout_gate_reasons)
                if dryout_gate_reasons else ""
            ),
            scenario.trace + (f"dryout_materials={','.join(dryout_material_ids)}",) + dryout_gate_reasons,
            thermal_method=resolved_method,
            dryout_material_ids=dryout_material_ids,
        )
    try:
        coupled = solve_electrothermal_coupled(
            candidate,
            maximum_iterations=maximum_iterations,
            temperature_tolerance_c=temperature_tolerance_c,
            current_tolerance_percent=current_tolerance_percent,
            loss_tolerance_percent=loss_tolerance_percent,
            relaxation_factor=relaxation_factor,
            production_mode=True,
            thermal_method=resolved_method,
            deenergized_circuit_ids=scenario.deenergized_circuit_ids,
            scenario_id=scenario.scenario_id,
        )
    except ElectroThermalInputError as exc:
        return ProductionScenarioResult(
            scenario,
            False,
            "FAILED",
            "INDETERMINATE",
            None,
            "",
            "",
            None,
            "",
            (),
            None,
            "COUPLED_OPERATING_POINT_FAILED",
            str(exc),
            scenario.trace + (f"Kapalı çevrim hatası: {exc}",),
            thermal_method=resolved_method,
            dryout_material_ids=dryout_material_ids,
        )
    regions = _region_results(coupled)
    critical = max(regions, key=lambda item: item.maximum_conductor_temperature_c)
    maximum = float(critical.maximum_conductor_temperature_c)
    suitability = "UYGUN" if maximum <= float(project.cable.max_temperature_c) + 1e-9 else "UYGUN_DEGIL"
    total_core_loss = sum(
        cable.conductor_loss_w_m * max(0.0, region_end.end_m - region_end.start_m)
        for region_end in coupled.final_thermal.regions
        for cable in region_end.cables
    )
    total_sheath_loss = sum(
        cable.sheath_loss_w_m * max(0.0, region_end.end_m - region_end.start_m)
        for region_end in coupled.final_thermal.regions
        for cable in region_end.cables
    )
    lambda1 = float(total_sheath_loss / total_core_loss) if total_core_loss > 1e-15 else None
    network_ratio = float(coupled.final_global_em.lambda1) if total_core_loss > 1e-15 else None
    eddy_ratio = (max(0.0, float(lambda1 - network_ratio)) if lambda1 is not None and network_ratio is not None else None)
    sheath_authority, sheath_reasons, sheath_sources = _sheath_completeness_summary(candidate, coupled)
    return ProductionScenarioResult(
        scenario,
        bool(coupled.converged),
        "COMPLETE" if coupled.converged else "PARTIAL",
        suitability if coupled.converged or suitability == "UYGUN_DEGIL" else "INDETERMINATE",
        maximum,
        critical.region_id,
        critical.critical_physical_cable_id,
        lambda1,
        coupled.loss_vector_fingerprint,
        regions,
        coupled,
        "" if coupled.converged else "COUPLED_OPERATING_POINT_NOT_CONVERGED",
        "" if coupled.converged else "Elektro-termal çalışma noktası bütün yakınsama kapılarını sağlamadı.",
        scenario.trace + (
            f"thermal_method={resolved_method}",
            f"dryout_materials={','.join(dryout_material_ids) or 'none'}",
            f"loss_vector_fingerprint={coupled.loss_vector_fingerprint}",
            f"Tmax={maximum:.6f} °C; limit={project.cable.max_temperature_c:.6f} °C",
            f"network_sheath_loss_ratio={network_ratio if network_ratio is not None else 'N/A'}; lambda1_eddy={eddy_ratio if eddy_ratio is not None else 'N/A'}; lambda1_rating={lambda1 if lambda1 is not None else 'N/A'}",
            f"sheath_loss_authority={sheath_authority}; reasons={','.join(sheath_reasons) or 'none'}; sources={','.join(sheath_sources) or 'none'}",
        ),
        thermal_method=resolved_method,
        dryout_material_ids=dryout_material_ids,
        network_sheath_loss_ratio=network_ratio,
        lambda1_eddy=eddy_ratio,
        lambda1_rating=lambda1,
        sheath_loss_authority=sheath_authority,
        sheath_loss_reason_codes=sheath_reasons,
        sheath_loss_sources=sheath_sources,
    )


def solve_production_electrothermal_study(
    project: ProjectData,
    *,
    active_scenario_id: str = "DESIGN",
    thermal_method: str = "AUTO",
    maximum_iterations: int = 20,
) -> ProductionElectroThermalStudyResult:
    scenarios = resolve_operating_scenarios(project)
    results = tuple(
        solve_production_operating_scenario(
            project,
            scenario,
            thermal_method=thermal_method,
            maximum_iterations=maximum_iterations,
        )
        for scenario in scenarios
    )
    if not results:
        raise ProductionElectroThermalInputError("Üretim elektro-termal senaryosu bulunamadı.")
    ids = {item.scenario.scenario_id for item in results}
    if active_scenario_id not in ids:
        owner = next((
            item.scenario.scenario_id
            for item in results
            if active_scenario_id in item.scenario.equivalent_scenario_ids
        ), None)
        active_scenario_id = owner or results[-1].scenario.scenario_id
    return ProductionElectroThermalStudyResult(results, active_scenario_id, REFERENCE)




def validate_production_thermal_methods(
    project: ProjectData,
    scenario_result: ProductionScenarioResult,
    *,
    mesh_scale: float = 1.5,
) -> ProductionThermalMethodComparison:
    """Compare analytical and nodal temperatures using one frozen physical loss vector."""

    if scenario_result.coupled_result is None:
        raise ProductionElectroThermalInputError("Yöntem doğrulaması için tamamlanmış üretim çalışma noktası gerekir.")
    scenario = scenario_result.scenario
    candidate = apply_operating_scenario(project, scenario)
    thermal = solve_multiconductor_thermal(
        candidate,
        mesh_scale=mesh_scale,
        global_result=scenario_result.coupled_result.final_global_em,
        fixed_global_losses=True,
        solve_nodal=True,
        deenergized_circuit_ids=scenario.deenergized_circuit_ids,
        production_mode=True,
    )
    fingerprint = _loss_vector_fingerprint(thermal)
    same = fingerprint == scenario_result.loss_vector_fingerprint
    max_balance = max((item.nodal_energy_balance_error_percent for item in thermal.regions), default=float("inf"))
    max_residual = max((item.nodal_maximum_linear_residual for item in thermal.regions), default=float("inf"))
    difference = float(
        thermal.maximum_analytical_conductor_temperature_c
        - thermal.maximum_nodal_conductor_temperature_c
    )
    dryout_enabled = any(bool(getattr(item, "dryout_enabled", False)) for item in thermal.regions)
    dryout_ok = all(
        not bool(getattr(item, "dryout_enabled", False)) or bool(getattr(item, "dryout_converged", False))
        for item in thermal.regions
    )
    quality_ok = bool(
        same
        and all(item.nodal_converged for item in thermal.regions)
        and dryout_ok
        and max_balance <= 0.5
        and max_residual <= 1e-7
    )
    if dryout_enabled:
        status = "NODAL_DRYOUT_BINDING" if quality_ok else "QUALITY_PENDING"
    else:
        status = "PASS" if quality_ok else "QUALITY_PENDING"
    return ProductionThermalMethodComparison(
        scenario.scenario_id,
        fingerprint,
        float(thermal.maximum_analytical_conductor_temperature_c),
        float(thermal.maximum_nodal_conductor_temperature_c),
        difference,
        float(max_balance),
        float(max_residual),
        same,
        status,
        (
            "Analitik ve nodal sıcaklıklar aynı sabit fiziksel akım/kayıp vektörüyle çözüldü.",
            f"loss_vector_fingerprint={fingerprint}",
            f"ΔT(analitik-nodal)={difference:.6f} °C",
            f"dryout={'enabled' if dryout_enabled else 'disabled'}; dryout_converged={dryout_ok}",
        ),
    )


def solve_production_coupled_ampacity(
    project: ProjectData,
    scenario: ResolvedOperatingScenario,
    *,
    scale_mode: str = "TARGET_CIRCUIT_SCALE",
    target_circuit_ids: tuple[str, ...] = (),
    maximum_factor: float = 5.0,
    maximum_iterations: int = 14,
    current_tolerance_a: float = 1.0,
    temperature_tolerance_c: float = 0.10,
) -> ProductionAmpacityResult:
    """Find a current-vector multiplier at the conductor temperature limit."""

    scale_mode = str(scale_mode).upper()
    if scale_mode not in {"COMMON_SCALE", "TARGET_CIRCUIT_SCALE"}:
        raise ProductionElectroThermalInputError("scale_mode COMMON_SCALE veya TARGET_CIRCUIT_SCALE olmalıdır.")
    targets = tuple(target_circuit_ids or scenario.target_circuit_ids)
    if scale_mode == "TARGET_CIRCUIT_SCALE" and not targets:
        energized = tuple(item.circuit_id for item in scenario.circuit_states if item.energized)
        if len(energized) != 1:
            raise ProductionElectroThermalInputError("Hedef devre ölçeklemesi için target_circuit_ids gereklidir.")
        targets = energized
    base = {item.circuit_id: item for item in scenario.circuit_states}
    evaluations: list[ProductionAmpacityEvaluation] = []
    cache: dict[float, ProductionScenarioResult] = {}

    def evaluate(factor: float) -> ProductionScenarioResult:
        key = round(float(factor), 10)
        if key in cache:
            return cache[key]
        states = []
        for item in scenario.circuit_states:
            scale = factor if (scale_mode == "COMMON_SCALE" or item.circuit_id in targets) else 1.0
            states.append(replace(item, phase_current_a=item.phase_current_a * scale))
        candidate_scenario = _scenario_from_states(
            scenario,
            states,
            scenario_id=f"{scenario.scenario_id}@{key:g}",
            scenario_name=f"{scenario.scenario_name} × {key:g}",
        )
        solved = solve_production_operating_scenario(
            project,
            candidate_scenario,
            maximum_iterations=20,
            temperature_tolerance_c=0.05,
            thermal_method="ANALYTIC",
        )
        if solved.maximum_conductor_temperature_c is None:
            raise ProductionElectroThermalInputError(solved.error_message or "Ampacity değerlendirmesi sonuç üretmedi.")
        if solved.sheath_loss_authority != AUTHORITY_FULL:
            reasons = ", ".join(solved.sheath_loss_reason_codes) or "SHEATH_LOSS_INCOMPLETE"
            raise ProductionElectroThermalInputError(
                "IEC ampacity üretim sonucu bloke: sheath-loss completeness tamamlanmadı (" + reasons + "). "
                "Çoklu/CUSTOM durumda doğrulanmış dış λ1'' sağlayın veya desteklenen IEC kapsamı kullanın."
            )
        cache[key] = solved
        evaluations.append(ProductionAmpacityEvaluation(key, solved.maximum_conductor_temperature_c, solved.converged))
        return solved

    limit = float(project.cable.max_temperature_c)
    low = 0.0
    low_result = evaluate(low)
    high = 1.0
    high_result = evaluate(high)
    while high_result.maximum_conductor_temperature_c < limit and high < maximum_factor:
        low, low_result = high, high_result
        high = min(maximum_factor, high * 1.5)
        high_result = evaluate(high)
    if high_result.maximum_conductor_temperature_c < limit:
        raise ProductionElectroThermalInputError("Ampacity aralığı sıcaklık sınırını çevrelemedi.")
    best_factor = low
    best = low_result
    converged = False
    max_base = max((item.phase_current_a for item in base.values()), default=0.0)
    for _ in range(maximum_iterations):
        mid = 0.5 * (low + high)
        solved = evaluate(mid)
        temp = float(solved.maximum_conductor_temperature_c)
        if temp <= limit:
            low, low_result = mid, solved
            best_factor, best = mid, solved
        else:
            high, high_result = mid, solved
        if (high - low) * max(max_base, 1.0) <= current_tolerance_a and abs(temp - limit) <= temperature_tolerance_c:
            converged = True
            break
    rating = tuple(
        (item.circuit_id, item.phase_current_a * (best_factor if scale_mode == "COMMON_SCALE" or item.circuit_id in targets else 1.0))
        for item in scenario.circuit_states
    )
    return ProductionAmpacityResult(
        scenario.scenario_id,
        scale_mode,
        targets,
        converged,
        float(best_factor),
        rating,
        best.critical_region_id,
        best.critical_physical_cable_id,
        tuple(evaluations),
        best,
    )
