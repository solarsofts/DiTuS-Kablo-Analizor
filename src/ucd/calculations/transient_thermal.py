from __future__ import annotations

from ucd.calculations.model_applicability import require_production_physics

from ucd.calculations.result_status import aggregate_binary_status

from dataclasses import dataclass
from math import pi, sqrt
from typing import Any

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import factorized

from ucd.calculations.iec60287 import ac_resistance_at_temperature_ohm_km, dielectric_loss_w_m
from ucd.calculations.load_cycle import load_cycle_metrics
from ucd.calculations.nodal_thermal import (
    NodalRouteStudyResult,
    NodalThermalInputError,
    _NodalModel,
    _internal_thermal_chains,
    _solve_at_current_with_retry,
    solve_nodal_route,
)
from ucd.calculations.thermal_resistance import resolve_internal_thermal_resistance
from ucd.calculations.thermal_route import resolve_thermal_region
from ucd.models.project import (
    LoadProfilePoint,
    ProjectData,
    ThermalMaterialData,
    ThermalRegion,
    TransientLoadProfile,
    TRANSIENT_INITIAL_CYCLIC,
    TRANSIENT_INITIAL_STEADY,
    TRANSIENT_INITIAL_USER,
)


TRANSIENT_THERMAL_REFERENCE = (
    "IEC 60853 workflow with a 2D finite-volume transient conduction model; "
    "not a verbatim implementation of the proprietary closed-form standard equations"
)


class TransientThermalInputError(ValueError):
    pass


@dataclass(frozen=True)
class TransientPointResult:
    time_h: float
    current_a: float
    current_multiplier: float
    maximum_conductor_temperature_c: float
    maximum_jacket_temperature_c: float
    hottest_cable_id: str


@dataclass(frozen=True)
class TransientRegionResult:
    region_id: str
    region_name: str
    installation_type: str
    profile_id: str
    profile_name: str
    base_current_per_cable_a: float
    continuous_ampacity_per_cable_a: float
    cyclic_rating_per_cable_a: float
    cyclic_rating_factor: float
    emergency_duration_h: float
    emergency_rating_per_cable_a: float
    maximum_conductor_temperature_c: float
    maximum_jacket_temperature_c: float
    time_of_maximum_h: float
    normal_temperature_limit_c: float
    emergency_temperature_limit_c: float
    preconditioning_cycles: int
    cyclic_end_delta_c: float
    transient_mesh_nx: int
    transient_mesh_ny: int
    minimum_heat_capacity_mj_m3k: float
    status: str
    points: tuple[TransientPointResult, ...]
    warnings: tuple[str, ...]
    trace: tuple[str, ...]
    current_load_factor: float = 0.0
    loss_load_factor_mu: float = 0.0
    peak_profile_multiplier: float = 0.0


@dataclass(frozen=True)
class TransientRouteStudyResult:
    reference: str
    profile_id: str
    profile_name: str
    regions: tuple[TransientRegionResult, ...]
    critical_cyclic_region_id: str
    route_cyclic_rating_per_cable_a: float
    critical_emergency_region_id: str
    route_emergency_rating_per_cable_a: float
    maximum_conductor_temperature_c: float
    status: str
    nodal_reference: str
    trace: tuple[str, ...]
    current_load_factor: float = 0.0
    loss_load_factor_mu: float = 0.0
    peak_profile_multiplier: float = 0.0


@dataclass
class _TransientState:
    field_c: np.ndarray
    conductor_c: np.ndarray


@dataclass(frozen=True)
class _CycleOutput:
    state: _TransientState
    points: tuple[TransientPointResult, ...]
    max_conductor_c: float
    max_jacket_c: float
    time_of_maximum_h: float


def _validate_profile(profile: TransientLoadProfile) -> None:
    if profile.duration_h <= 0:
        raise TransientThermalInputError(f"{profile.profile_id}: profil süresi sıfırdan büyük olmalıdır.")
    if len(profile.points) < 2:
        raise TransientThermalInputError(f"{profile.profile_id}: en az iki yük noktası gereklidir.")
    points = sorted(profile.points, key=lambda item: item.time_h)
    if abs(points[0].time_h) > 1e-9:
        raise TransientThermalInputError(f"{profile.profile_id}: ilk yük noktası 0 h olmalıdır.")
    if abs(points[-1].time_h - profile.duration_h) > 1e-6:
        raise TransientThermalInputError(
            f"{profile.profile_id}: son nokta profil süresine eşit olmalıdır ({profile.duration_h:g} h)."
        )
    previous = -1.0
    for point in points:
        if point.time_h <= previous:
            raise TransientThermalInputError(f"{profile.profile_id}: zaman noktaları artan ve benzersiz olmalıdır.")
        if point.current_multiplier < 0:
            raise TransientThermalInputError(f"{profile.profile_id}: akım çarpanı negatif olamaz.")
        previous = point.time_h
    if profile.interpolation.upper() not in {"STEP", "LINEAR"}:
        raise TransientThermalInputError(f"{profile.profile_id}: interpolation STEP veya LINEAR olmalıdır.")


def _profile_multiplier(profile: TransientLoadProfile, time_h: float) -> float:
    points = sorted(profile.points, key=lambda item: item.time_h)
    t = min(max(float(time_h), 0.0), profile.duration_h)
    if t >= points[-1].time_h - 1e-12:
        return float(points[-1].current_multiplier)
    for left, right in zip(points, points[1:]):
        if left.time_h <= t < right.time_h:
            if profile.interpolation.upper() == "LINEAR":
                fraction = (t - left.time_h) / max(right.time_h - left.time_h, 1e-12)
                return float(left.current_multiplier + fraction * (right.current_multiplier - left.current_multiplier))
            return float(left.current_multiplier)
    return float(points[0].current_multiplier)


def _category_fallback_capacity_mj_m3k(project: ProjectData, material: ThermalMaterialData) -> float:
    settings = project.transient_study
    category = material.category.upper()
    if category in {"NATIVE_SOIL"}:
        return settings.default_soil_heat_capacity_mj_m3k
    if category in {"THERMAL_BACKFILL", "GENERAL_FILL"}:
        return settings.default_backfill_heat_capacity_mj_m3k
    if category in {"CONCRETE_GROUT", "SURFACE"}:
        return settings.default_concrete_heat_capacity_mj_m3k
    if category in {"DUCT", "POLYMER"}:
        return settings.default_duct_heat_capacity_mj_m3k
    if category in {"AIR", "DUCT_FILL"}:
        return settings.default_air_heat_capacity_mj_m3k
    return settings.default_soil_heat_capacity_mj_m3k


def _capacity_vector(project: ProjectData, model: _NodalModel) -> tuple[np.ndarray, tuple[str, ...], float]:
    warnings: list[str] = []
    area = model.dy[:, None] * model.dx[None, :]
    capacity_mj = np.empty((model.ny, model.nx), dtype=float)
    missing_ids: set[str] = set()
    for iy in range(model.ny):
        for ix in range(model.nx):
            material_id = str(model.material_ids[iy, ix])
            if material_id == "CABLE":
                value = float(project.transient_study.cable_outer_heat_capacity_mj_m3k)
            else:
                material = model.materials.get(material_id)
                if material is None:
                    raise TransientThermalInputError(
                        f"{model.region.region_id}: ısı kapasitesi için malzeme bulunamadı: {material_id}"
                    )
                value = float(material.volumetric_heat_capacity_mj_m3k or 0.0)
                if value <= 0:
                    value = _category_fallback_capacity_mj_m3k(project, material)
                    missing_ids.add(material_id)
            if value <= 0:
                raise TransientThermalInputError(f"{material_id}: hacimsel ısı kapasitesi sıfırdan büyük olmalıdır.")
            capacity_mj[iy, ix] = value
    if missing_ids:
        warnings.append(
            "Hacimsel ısı kapasitesi eksik malzemeler için kategori varsayımı kullanıldı: "
            + ", ".join(sorted(missing_ids))
        )
    capacity_j_mk = capacity_mj * 1e6 * area
    return capacity_j_mk.ravel(), tuple(warnings), float(np.min(capacity_mj))


def _conductor_heat_capacity_j_mk(project: ProjectData) -> float:
    cable = project.cable
    area_m2 = max(float(cable.conductor_area_mm2), 1.0) * 1e-6 * max(1, int(cable.conductors_per_cable))
    if cable.conductor_material.strip().upper().startswith("AL"):
        density, cp = 2700.0, 900.0
    else:
        density, cp = 8960.0, 385.0
    conductor = area_m2 * density * cp
    conductor_radius = max(float(cable.conductor_diameter_mm), 1.0) / 2000.0
    t1_radius = max(float(cable.t1_outer_diameter_mm), cable.conductor_diameter_mm + 1.0) / 2000.0
    insulation_area = max(0.0, pi * (t1_radius**2 - conductor_radius**2))
    insulation_part = 0.35 * insulation_area * 1.5e6
    return max(500.0, conductor + insulation_part)


def _source_vector(model: _NodalModel, cable_heat_w_m: np.ndarray) -> np.ndarray:
    if len(cable_heat_w_m) != len(model.locations):
        raise TransientThermalInputError("Kablo ısı kaynağı sayısı geometriyle uyuşmuyor.")
    vector = np.zeros(model.cell_count, dtype=float)
    area_grid = model.dy[:, None] * model.dx[None, :]
    for heat, mask in zip(cable_heat_w_m, model.cable_masks):
        cells = np.flatnonzero(mask.ravel())
        if not cells.size:
            raise TransientThermalInputError("Kablo ısı kaynağı için mesh hücresi bulunamadı.")
        weights = area_grid[mask] / max(float(np.sum(area_grid[mask])), 1e-12)
        vector[cells] += float(heat) * weights
    return vector


def _initial_state(
    project: ProjectData,
    model: _NodalModel,
    profile: TransientLoadProfile,
    base_current_a: float,
    lambda1: float,
) -> _TransientState:
    mode = project.transient_study.initial_condition_mode.upper()
    ambient = float(model.profile.ambient_temperature_c)
    first_current = base_current_a * _profile_multiplier(profile, 0.0)
    if mode in {TRANSIENT_INITIAL_STEADY, TRANSIENT_INITIAL_CYCLIC}:
        solved, _ = _solve_at_current_with_retry(
            model, first_current, lambda1, max_iterations=35, tolerance_c=0.03
        )
        field, cables, iterations, converged, *_ = solved
        if not converged:
            raise TransientThermalInputError(
                f"{model.region.region_id}: {first_current:.1f} A başlangıç kararlı durum sıcaklık iterasyonu "
                f"{iterations} iterasyonda yakınsamadı; geçici çözüm yakınsamamış başlangıç durumuyla başlatılamaz."
            )
        conductor = np.asarray([item.conductor_temperature_c for item in cables], dtype=float)
        return _TransientState(np.asarray(field, dtype=float), conductor)
    if mode == TRANSIENT_INITIAL_USER:
        initial = float(project.transient_study.user_initial_conductor_temperature_c)
        if initial < ambient:
            raise TransientThermalInputError("Kullanıcı başlangıç iletken sıcaklığı ortam sıcaklığından düşük olamaz.")
        return _TransientState(
            np.full((model.ny, model.nx), ambient, dtype=float),
            np.full(len(model.locations), initial, dtype=float),
        )
    raise TransientThermalInputError(f"Bilinmeyen başlangıç koşulu: {mode}")


def _cable_internal_step(
    old_core: np.ndarray,
    jacket: np.ndarray,
    conductor_loss: np.ndarray,
    *,
    wd: float,
    lambda1: float,
    lambda2: float,
    internal_chain: float,
    dielectric_internal_chain: float,
    r_internal: float,
    c_core: float,
    dt_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Implicit conductor-node step of the cable interior.

    Conductor node (c_core) -> T1 -> quasi-static screen node -> T2+T3 -> jacket;
    sheath/armour loss and half of Wd enter at the screen node.  Written on the
    lumped R = T1+T2+T3 with the offset K, the steady limit is the IEC chain
    theta_c - theta_j = Wc*internal_chain + Wd*dielectric_internal_chain and the
    heat delivered to the jacket equals the total cable loss.
    Returns (conductor temperature, heat delivered to the jacket) per cable.
    """

    offset = (
        conductor_loss * (internal_chain - r_internal)
        + wd * (dielectric_internal_chain - 0.5 * r_internal)
    )
    storage = c_core / dt_s
    q_core = conductor_loss + 0.5 * wd
    core_new = (
        storage * old_core + q_core + (jacket + offset) / r_internal
    ) / (storage + 1.0 / r_internal)
    q_transfer = (core_new - jacket - offset) / r_internal
    sheath_loss = conductor_loss * max(0.0, lambda1)
    armour_loss = conductor_loss * lambda2
    return core_new, sheath_loss + armour_loss + 0.5 * wd + q_transfer


def _simulate(
    project: ProjectData,
    model: _NodalModel,
    profile: TransientLoadProfile,
    base_current_a: float,
    lambda1: float,
    initial_state: _TransientState,
    *,
    record_points: bool,
) -> _CycleOutput:
    settings = project.transient_study
    dt_s = float(settings.time_step_minutes) * 60.0
    if dt_s <= 0:
        raise TransientThermalInputError("Zaman adımı sıfırdan büyük olmalıdır.")
    steps = max(1, int(np.ceil(profile.duration_h * 3600.0 / dt_s)))
    dt_s = profile.duration_h * 3600.0 / steps
    capacity, _, _ = _capacity_vector(project, model)
    dynamic_matrix = model.matrix + diags(capacity / dt_s, 0, format="csc")
    solve_dynamic = factorized(dynamic_matrix)

    cable = project.cable
    wd = dielectric_loss_w_m(cable)
    lambda2 = max(0.0, float(cable.armour_loss_factor))
    internal = resolve_internal_thermal_resistance(cable)
    r_internal = max(1e-5, float(internal.t1_km_w + internal.t2_km_w + internal.t3_km_w))
    internal_chain, dielectric_internal_chain = _internal_thermal_chains(cable, lambda1)
    c_core = _conductor_heat_capacity_j_mk(project)

    old_field = np.asarray(initial_state.field_c, dtype=float).copy()
    old_core = np.asarray(initial_state.conductor_c, dtype=float).copy()
    points: list[TransientPointResult] = []
    max_cond = float(np.max(old_core))
    max_jacket = float(np.max(model.cable_jacket_temperatures(old_field)))
    max_time = 0.0

    if record_points:
        jackets = model.cable_jacket_temperatures(old_field)
        hottest = int(np.argmax(old_core))
        points.append(TransientPointResult(
            0.0,
            base_current_a * _profile_multiplier(profile, 0.0),
            _profile_multiplier(profile, 0.0),
            float(np.max(old_core)),
            float(np.max(jackets)),
            model.locations[hottest].cable_id,
        ))

    for step in range(1, steps + 1):
        time_h = step * profile.duration_h / steps
        multiplier = _profile_multiplier(profile, time_h)
        current = max(0.0, base_current_a * multiplier)
        old_flat = old_field.ravel()
        jacket_guess = model.cable_jacket_temperatures(old_field)
        core_new = old_core.copy()
        field_new = old_field.copy()

        for _ in range(3):
            conductor_loss = np.zeros(len(model.locations), dtype=float)
            for index, temperature in enumerate(core_new):
                eval_temp = max(-273.149999, min(float(temperature), settings.emergency_temperature_limit_c + 100.0))
                _, rac_km = ac_resistance_at_temperature_ohm_km(
                    cable, eval_temp, model.profile.phase_spacing_m
                )
                conductor_loss[index] = current**2 * rac_km / 1000.0
            core_new, q_outer = _cable_internal_step(
                old_core, jacket_guess, conductor_loss,
                wd=wd, lambda1=lambda1, lambda2=lambda2,
                internal_chain=internal_chain, dielectric_internal_chain=dielectric_internal_chain,
                r_internal=r_internal, c_core=c_core, dt_s=dt_s,
            )
            rhs = model.boundary_rhs + _source_vector(model, q_outer) + (capacity / dt_s) * old_flat
            field_new = np.asarray(solve_dynamic(rhs), dtype=float).reshape((model.ny, model.nx))
            jacket_new = model.cable_jacket_temperatures(field_new)
            if float(np.max(np.abs(jacket_new - jacket_guess))) < 0.01:
                jacket_guess = jacket_new
                break
            jacket_guess = 0.6 * jacket_new + 0.4 * jacket_guess

        old_field = field_new
        old_core = core_new
        step_max = float(np.max(core_new))
        step_jacket = float(np.max(jacket_guess))
        if step_max > max_cond:
            max_cond = step_max
            max_time = time_h
        max_jacket = max(max_jacket, step_jacket)
        if record_points:
            hottest = int(np.argmax(core_new))
            points.append(TransientPointResult(
                time_h,
                current,
                multiplier,
                step_max,
                step_jacket,
                model.locations[hottest].cable_id,
            ))

    return _CycleOutput(
        _TransientState(old_field, old_core),
        tuple(points),
        max_cond,
        max_jacket,
        max_time,
    )


def _run_preconditioned_cycle(
    project: ProjectData,
    model: _NodalModel,
    profile: TransientLoadProfile,
    base_current_a: float,
    lambda1: float,
    *,
    record_final: bool,
) -> tuple[_CycleOutput, int, float]:
    settings = project.transient_study
    state = _initial_state(project, model, profile, base_current_a, lambda1)
    mode = settings.initial_condition_mode.upper()
    cycles = 1
    end_delta = 0.0
    if mode == TRANSIENT_INITIAL_CYCLIC:
        maximum = max(1, int(settings.maximum_preconditioning_cycles))
        tolerance = max(0.001, float(settings.cyclic_convergence_tolerance_c))
        for index in range(1, maximum + 1):
            start_core = state.conductor_c.copy()
            output = _simulate(
                project, model, profile, base_current_a, lambda1, state,
                record_points=record_final and index == maximum,
            )
            state = output.state
            cycles = index
            end_delta = float(np.max(np.abs(state.conductor_c - start_core)))
            if end_delta <= tolerance:
                if record_final:
                    output = _simulate(
                        project, model, profile, base_current_a, lambda1, state, record_points=True
                    )
                    cycles += 1
                    end_delta = float(np.max(np.abs(output.state.conductor_c - state.conductor_c)))
                return output, cycles, end_delta
        if record_final and not output.points:
            output = _simulate(project, model, profile, base_current_a, lambda1, state, record_points=True)
            cycles += 1
        return output, cycles, end_delta
    output = _simulate(project, model, profile, base_current_a, lambda1, state, record_points=record_final)
    return output, cycles, end_delta


def _corrected_cyclic_rating(
    project: ProjectData,
    model: _NodalModel,
    profile: TransientLoadProfile,
    base_current_a: float,
    lambda1: float,
    base_output: _CycleOutput,
) -> float:
    limit = float(project.transient_study.normal_temperature_limit_c)
    ambient = float(model.profile.ambient_temperature_c)
    rise = max(base_output.max_conductor_c - ambient, 0.05)
    estimate = max(1.0, base_current_a * sqrt(max(limit - ambient, 0.1) / rise))
    low, high = 0.0, max(estimate * 1.15, base_current_a * 1.10, 100.0)
    for _ in range(5):
        output, _, _ = _run_preconditioned_cycle(
            project, model, profile, high, lambda1, record_final=False
        )
        if output.max_conductor_c > limit:
            break
        high *= 1.25
    for _ in range(8):
        mid = 0.5 * (low + high)
        output, _, _ = _run_preconditioned_cycle(
            project, model, profile, mid, lambda1, record_final=False
        )
        if output.max_conductor_c > limit:
            high = mid
        else:
            low = mid
        if high - low <= max(0.5, high * 0.002):
            break
    return 0.5 * (low + high)


def _constant_profile(duration_h: float) -> TransientLoadProfile:
    return TransientLoadProfile(
        "EMERGENCY_HOLD",
        "Sabit acil yük tutma süresi",
        duration_h,
        "STEP",
        [LoadProfilePoint(0.0, 1.0, "Başlangıç"), LoadProfilePoint(duration_h, 1.0, "Son")],
    )


def _emergency_rating(
    project: ProjectData,
    model: _NodalModel,
    lambda1: float,
    starting_state: _TransientState,
    continuous_ampacity_a: float,
) -> float:
    duration = float(project.transient_study.emergency_duration_h)
    if duration <= 0:
        raise TransientThermalInputError("Acil yük süresi sıfırdan büyük olmalıdır.")
    profile = _constant_profile(duration)
    limit = float(project.transient_study.emergency_temperature_limit_c)
    low = 0.0
    high = max(100.0, continuous_ampacity_a * 1.5)
    for _ in range(6):
        output = _simulate(project, model, profile, high, lambda1, starting_state, record_points=False)
        if output.max_conductor_c > limit:
            break
        high *= 1.35
    for _ in range(10):
        mid = 0.5 * (low + high)
        output = _simulate(project, model, profile, mid, lambda1, starting_state, record_points=False)
        if output.max_conductor_c > limit:
            high = mid
        else:
            low = mid
        if high - low <= max(0.5, high * 0.0015):
            break
    return 0.5 * (low + high)


def solve_transient_region(
    project: ProjectData,
    region_id: str,
    nodal_result: NodalRouteStudyResult,
    profile: TransientLoadProfile,
) -> TransientRegionResult:
    _validate_profile(profile)
    cycle_metrics = load_cycle_metrics(profile)
    active = nodal_result.active
    steady_region = next((item for item in active.regions if item.region_id == region_id), None)
    if steady_region is None:
        raise TransientThermalInputError(f"2D kararlı durum sonucu bulunamadı: {region_id}")
    region = next((item for item in project.thermal_design.regions if item.region_id == region_id), None)
    if region is None:
        raise TransientThermalInputError(f"Termal bölge bulunamadı: {region_id}")
    profile_geometry = resolve_thermal_region(project.thermal_design, region, project.cable)
    mesh_scale = max(0.5, float(project.transient_study.transient_mesh_scale))
    try:
        model = _NodalModel(project, region, profile_geometry, active.active_circuit_count, mesh_scale)
    except NodalThermalInputError as exc:
        raise TransientThermalInputError(str(exc)) from exc

    capacity, capacity_warnings, minimum_capacity = _capacity_vector(project, model)
    del capacity
    base_current = max(0.0, float(active.current_per_cable_a))
    lambda1 = max(0.0, float(steady_region.regional_lambda1))
    output, cycles, end_delta = _run_preconditioned_cycle(
        project, model, profile, base_current, lambda1, record_final=True
    )

    settings = project.transient_study
    if settings.calculate_cyclic_rating:
        cyclic_rating = _corrected_cyclic_rating(
            project, model, profile, base_current, lambda1, output
        )
    else:
        cyclic_rating = 0.0
    if settings.calculate_emergency_rating:
        emergency_rating = _emergency_rating(
            project, model, lambda1, output.state, steady_region.ampacity_per_cable_a
        )
    else:
        emergency_rating = 0.0

    cyclic_factor = cyclic_rating / max(steady_region.ampacity_per_cable_a, 1e-12) if cyclic_rating > 0 else 0.0
    status = "UYGUN" if output.max_conductor_c <= settings.normal_temperature_limit_c + 1e-6 else "UYGUN DEĞİL"
    warnings = list(capacity_warnings)
    if end_delta > settings.cyclic_convergence_tolerance_c:
        warnings.append(
            f"Çevrimsel başlangıç {cycles} çevrimde yakınsamadı; uç sıcaklık farkı {end_delta:.3f} °C."
        )
    if profile.profile_id == "EMERGENCY":
        warnings.append("EMERGENCY profili örnek veridir; gerçek koruma ve işletme süresiyle değiştirilmelidir.")
    trace = (
        f"Bölge = {region.region_id} / {region.name}",
        f"Profil = {profile.profile_id} / {profile.name}; süre = {profile.duration_h:.3f} h",
        f"Profil tepe çarpanı = {cycle_metrics.peak_multiplier:.6f}; akım yük faktörü = {cycle_metrics.current_load_factor:.6f}; IEC 60853 kayıp-yük faktörü μ = {cycle_metrics.loss_load_factor_mu:.6f}",
        f"Zaman adımı = {settings.time_step_minutes:.3f} min; transient mesh = {model.nx}×{model.ny}",
        f"Başlangıç koşulu = {settings.initial_condition_mode}; ön koşullandırma çevrimi = {cycles}",
        f"Baz akım = {base_current:.3f} A/kablo; λ1 = {lambda1:.8f}",
        f"Maksimum iletken sıcaklığı = {output.max_conductor_c:.3f} °C @ {output.time_of_maximum_h:.3f} h",
        f"Sürekli 2D ampacity = {steady_region.ampacity_per_cable_a:.3f} A/kablo",
        f"Çevrimsel rating = {cyclic_rating:.3f} A/kablo; faktör = {cyclic_factor:.5f}",
        f"{settings.emergency_duration_h:.3f} h acil rating = {emergency_rating:.3f} A/kablo",
        f"Durum = {status}",
    )
    return TransientRegionResult(
        region.region_id,
        region.name,
        steady_region.installation_type,
        profile.profile_id,
        profile.name,
        base_current,
        steady_region.ampacity_per_cable_a,
        cyclic_rating,
        cyclic_factor,
        settings.emergency_duration_h,
        emergency_rating,
        output.max_conductor_c,
        output.max_jacket_c,
        output.time_of_maximum_h,
        settings.normal_temperature_limit_c,
        settings.emergency_temperature_limit_c,
        cycles,
        end_delta,
        model.nx,
        model.ny,
        minimum_capacity,
        status,
        output.points,
        tuple(warnings),
        trace,
        current_load_factor=cycle_metrics.current_load_factor,
        loss_load_factor_mu=cycle_metrics.loss_load_factor_mu,
        peak_profile_multiplier=cycle_metrics.peak_multiplier,
    )


def solve_transient_route(
    project: ProjectData,
    bonding_result: Any | None = None,
    nodal_result: NodalRouteStudyResult | None = None,
) -> TransientRouteStudyResult:
    try:
        require_production_physics(project.cable, engine_label="IEC 60853 transient")
    except ValueError as exc:
        raise TransientThermalInputError(str(exc)) from exc
    settings = project.transient_study
    profile = next((item for item in settings.profiles if item.profile_id == settings.active_profile_id), None)
    if profile is None:
        raise TransientThermalInputError(f"Aktif yük profili bulunamadı: {settings.active_profile_id}")
    _validate_profile(profile)
    cycle_metrics = load_cycle_metrics(profile)
    if settings.normal_temperature_limit_c <= 0 or settings.emergency_temperature_limit_c <= 0:
        raise TransientThermalInputError("Normal ve acil sıcaklık limitleri sıfırdan büyük olmalıdır.")
    if settings.emergency_temperature_limit_c < settings.normal_temperature_limit_c:
        raise TransientThermalInputError("Acil sıcaklık limiti normal limitten düşük olamaz.")
    if nodal_result is None:
        nodal_result = solve_nodal_route(project, bonding_result, active_scenario_id="DESIGN")

    enabled = [item for item in project.thermal_design.regions if item.enabled]
    selected = set(settings.selected_region_ids)
    regions_to_solve = [item for item in enabled if not selected or item.region_id in selected]
    if not regions_to_solve:
        raise TransientThermalInputError("Geçici termal çözüm için seçili etkin bölge bulunamadı.")

    results = tuple(
        solve_transient_region(project, region.region_id, nodal_result, profile)
        for region in regions_to_solve
    )
    cyclic_candidates = [item for item in results if item.cyclic_rating_per_cable_a > 0]
    emergency_candidates = [item for item in results if item.emergency_rating_per_cable_a > 0]
    critical_cyclic = min(cyclic_candidates, key=lambda item: item.cyclic_rating_per_cable_a) if cyclic_candidates else results[0]
    critical_emergency = min(emergency_candidates, key=lambda item: item.emergency_rating_per_cable_a) if emergency_candidates else results[0]
    maximum_temperature = max(item.maximum_conductor_temperature_c for item in results)
    status = aggregate_binary_status(tuple(item.status for item in results))
    trace = (
        f"Referans yaklaşım = {TRANSIENT_THERMAL_REFERENCE}",
        f"Aktif profil = {profile.profile_id} / {profile.name}",
        f"Akım yük faktörü = {cycle_metrics.current_load_factor:.6f}; IEC 60853 kayıp-yük faktörü μ = {cycle_metrics.loss_load_factor_mu:.6f}; tepe çarpanı = {cycle_metrics.peak_multiplier:.6f}",
        f"Çözülen termal bölge = {len(results)}",
        f"Güzergâh çevrimsel rating = {critical_cyclic.cyclic_rating_per_cable_a:.3f} A/kablo; kritik = {critical_cyclic.region_id}",
        f"Güzergâh {settings.emergency_duration_h:.3f} h acil rating = {critical_emergency.emergency_rating_per_cable_a:.3f} A/kablo; kritik = {critical_emergency.region_id}",
        f"Maksimum profil sıcaklığı = {maximum_temperature:.3f} °C",
        f"Durum = {status}",
    )
    return TransientRouteStudyResult(
        TRANSIENT_THERMAL_REFERENCE,
        profile.profile_id,
        profile.name,
        results,
        critical_cyclic.region_id,
        critical_cyclic.cyclic_rating_per_cable_a,
        critical_emergency.region_id,
        critical_emergency.emergency_rating_per_cable_a,
        maximum_temperature,
        status,
        nodal_result.reference,
        trace,
        current_load_factor=cycle_metrics.current_load_factor,
        loss_load_factor_mu=cycle_metrics.loss_load_factor_mu,
        peak_profile_multiplier=cycle_metrics.peak_multiplier,
    )
