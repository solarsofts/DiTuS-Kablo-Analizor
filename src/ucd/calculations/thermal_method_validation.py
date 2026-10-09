"""FAZ 4.2 analytic/nodal method-authority policy.

The module never chooses the numerically more convenient result.  It classifies
whether the IEC analytical reduction is validated, whether nodal is mandatory
and quality-qualified, or whether the two methods require engineering review.
The result is runtime evidence and is cached in the existing engine-run
registry; the persisted project schema is unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Mapping

from ucd.calculations.installation_coupling import (
    GEOMETRY_BASIS_LEGACY,
    MATERIAL_HOMOGENEOUS,
    MATERIAL_LAYERED,
    MATERIAL_COMPLEX_REGIONS,
    RESULT_DERIVED_FROM_SCALAR,
    RESULT_ENGINEERING_APPROXIMATION,
    RESULT_IEC_ANALYTIC,
    RESULT_NODAL,
    AUTH_METHOD_ANALYTIC,
    AUTH_METHOD_LEGACY,
    AUTH_METHOD_NODAL,
    resolve_installation_geometry,
)
from ucd.calculations.project_workflow import (
    STATUS_COMPLETE,
    STATUS_CONDITIONAL,
    engine_input_signature,
    record_engine_run,
)
from ucd.calculations.result_status import is_suitable
from ucd.models.project import EXTERNAL_THERMAL_MANUAL, ProjectData

ENGINE_ID = "thermal_method_validation"
ANALYTIC_ENGINE_VERSION = "IEC60287_ROUTE_FAZ4.2"
NODAL_ENGINE_VERSION = "NODAL_FVM_FAZ4.2"

BASIS_ANALYTIC_PREVIEW = "ANALYTIC_PREVIEW"
BASIS_ANALYTIC_VALIDATED = "ANALYTIC_VALIDATED"
BASIS_ANALYTIC_CONSERVATIVE = "VALIDATED_WITH_CONSERVATIVE_BIAS"
BASIS_NODAL_REQUIRED = "NODAL_REQUIRED"
BASIS_NODAL_BINDING = "NODAL_BINDING"
BASIS_HYBRID_BINDING = "HYBRID_BINDING"
BASIS_MANUAL_SOURCE = "MANUAL_SOURCE"
REDUCTION_MANUAL_SOURCE = "MANUAL_SOURCE"
BASIS_DERIVED_FROM_SCALAR = "DERIVED_FROM_SCALAR"
BASIS_METHOD_DISAGREEMENT = "METHOD_DISAGREEMENT"
BASIS_NODAL_NOT_CONVERGED = "NODAL_NOT_CONVERGED"
BASIS_NODAL_QUALITY_PENDING = "NODAL_QUALITY_PENDING"

VALIDATION_PASS = "PASS"
VALIDATION_REVIEW = "REVIEW"
VALIDATION_FAIL = "FAIL"
VALIDATION_NOT_RUN = "NOT_RUN"
VALIDATION_NOT_APPLICABLE = "NOT_APPLICABLE"

QUALITY_PASS = "PASS"
QUALITY_FAIL = "FAIL"
QUALITY_PENDING = "PENDING"


@dataclass(frozen=True)
class ThermalMethodToleranceProfile:
    ampacity_pass_percent: float = 3.0
    ampacity_absolute_fail_percent: float = 7.0
    temperature_pass_c: float = 2.0
    analytic_optimistic_temperature_fail_c: float = 3.0
    temperature_absolute_fail_c: float = 5.0
    mesh_ampacity_percent: float = 1.0
    mesh_temperature_c: float = 1.0
    energy_balance_percent: float = 0.50
    linear_residual: float = 1.0e-7
    critical_region_difference_percent: float = 1.0


@dataclass(frozen=True)
class NodalQualityEvidence:
    scenario_id: str
    region_id: str
    converged: bool
    energy_balance_error_percent: float
    maximum_linear_residual: float
    mesh_check_status: str
    mesh_ampacity_difference_percent: float | None
    mesh_temperature_difference_c: float | None
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ThermalMethodRegionComparison:
    scenario_id: str
    region_id: str
    region_name: str
    geometry_fingerprint: str
    reduction_class: str
    analytic_ampacity_a: float | None
    nodal_ampacity_a: float | None
    ampacity_difference_percent: float | None
    analytic_temperature_c: float | None
    nodal_temperature_c: float | None
    temperature_difference_c: float | None
    analytic_status: str
    nodal_status: str
    nodal_quality: NodalQualityEvidence
    validation_status: str
    calculation_basis: str
    reasons: tuple[str, ...]
    geometry_basis: str = ""
    result_authority: str = ""
    authoritative_method: str = ""
    analytical_preview_allowed: bool = True


@dataclass(frozen=True)
class ThermalMethodScenarioAuthority:
    scenario_id: str
    scenario_name: str
    geometry_fingerprint: str
    calculation_basis: str
    validation_status: str
    judgement_basis_status: str
    official_ampacity_a: float | None
    analytic_ampacity_a: float | None
    nodal_ampacity_a: float | None
    ampacity_difference_percent: float | None
    analytic_temperature_c: float | None
    nodal_temperature_c: float | None
    temperature_difference_c: float | None
    analytic_critical_region_id: str
    nodal_critical_region_id: str
    region_comparisons: tuple[ThermalMethodRegionComparison, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ThermalMethodAuthorityResult:
    evaluated_at: str
    geometry_fingerprint: str
    input_signature: str
    analytic_engine_version: str
    nodal_engine_version: str
    calculation_basis: str
    validation_status: str
    judgement_basis_status: str
    active_scenario_id: str
    scenarios: tuple[ThermalMethodScenarioAuthority, ...]
    trace: tuple[str, ...]
    cache_hit: bool = False

    @property
    def active(self) -> ThermalMethodScenarioAuthority:
        return next((x for x in self.scenarios if x.scenario_id == self.active_scenario_id), self.scenarios[-1])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)



def _percent(candidate: float | None, reference: float | None) -> float | None:
    if candidate is None or reference is None or not (isfinite(candidate) and isfinite(reference)):
        return None
    if abs(reference) <= 1.0e-15:
        return 0.0 if abs(candidate) <= 1.0e-15 else None
    return 100.0 * (candidate - reference) / reference


def _geometry_maps(project: ProjectData) -> tuple[str, dict[str, tuple[str, str, str, str, bool]]]:
    """Return region provenance: fingerprint, geometry basis, material class, analytic authority, authoritative method, preview flag."""
    resolved = resolve_installation_geometry(project)
    values: dict[str, tuple[str, str, str, str, bool]] = {}
    manual_regions: set[str] = set()
    try:
        from ucd.calculations.thermal_route import resolve_thermal_region
        for region in project.thermal_design.regions:
            profile = resolve_thermal_region(project.thermal_design, region, project.cable)
            if str(profile.external_thermal_mode).upper() == EXTERNAL_THERMAL_MANUAL:
                manual_regions.add(str(region.region_id))
    except Exception:
        manual_regions = set()
    for item in resolved.regions:
        projection = item.projection
        if item.region_id in manual_regions:
            material = REDUCTION_MANUAL_SOURCE
            authority = "MANUAL_SOURCE"
            method = "MANUAL"
            preview = False
        else:
            material = str(projection.get("material_field_class", MATERIAL_HOMOGENEOUS))
            authority = str(projection.get("analytic_result_authority", RESULT_IEC_ANALYTIC))
            method = str(projection.get("authoritative_method", AUTH_METHOD_ANALYTIC))
            preview = bool(projection.get("analytical_preview_allowed", True))
        values[item.region_id] = (item.geometry_fingerprint, item.geometry_basis, material, authority, method, preview)
    if resolved.fingerprint:
        return resolved.fingerprint, values
    signature = engine_input_signature(project, ENGINE_ID)
    for route in project.route_sections:
        region_id = str(route.thermal_region_id or route.name)
        if route.external_thermal_mode == EXTERNAL_THERMAL_MANUAL:
            values[region_id] = (str(route.geometry_fingerprint or signature), GEOMETRY_BASIS_LEGACY, REDUCTION_MANUAL_SOURCE, "MANUAL_SOURCE", "MANUAL", False)
        else:
            values[region_id] = (str(route.geometry_fingerprint or signature), GEOMETRY_BASIS_LEGACY, MATERIAL_HOMOGENEOUS, RESULT_DERIVED_FROM_SCALAR, AUTH_METHOD_LEGACY, True)
    return signature, values


def _quality(region: Any, mesh: Any | None, profile: ThermalMethodToleranceProfile) -> NodalQualityEvidence:
    reasons: list[str] = []
    if not bool(region.converged):
        reasons.append("Nodal sıcaklık iterasyonu yakınsamadı.")
    if bool(getattr(region, "dryout_enabled", False)) and not bool(getattr(region, "dryout_converged", False)):
        reasons.append("Nodal kritik-izoterm kuruma iterasyonu yakınsamadı.")
    if float(region.energy_balance_error_percent) > profile.energy_balance_percent:
        reasons.append(
            f"Enerji dengesi hatası %{region.energy_balance_error_percent:.4f} > %{profile.energy_balance_percent:.4f}."
        )
    if float(region.maximum_linear_residual) > profile.linear_residual:
        reasons.append(
            f"Lineer residual {region.maximum_linear_residual:.3e} > {profile.linear_residual:.3e}."
        )
    mesh_status = VALIDATION_NOT_RUN
    mesh_amp = None
    mesh_temp = None
    if mesh is not None:
        mesh_amp = getattr(mesh, "ampacity_difference_percent", None)
        mesh_temp = getattr(mesh, "difference_c", None)
        mesh_status = VALIDATION_PASS if bool(getattr(mesh, "passed", False)) else VALIDATION_FAIL
        if not bool(getattr(mesh, "solutions_converged", True)):
            reasons.append("Mesh duyarlılığı nodal çözümlerinden en az biri yakınsamadı.")
        if mesh_amp is None:
            reasons.append("Mesh ampacity duyarlılığı kaydı bulunmuyor.")
        elif abs(float(mesh_amp)) > profile.mesh_ampacity_percent:
            reasons.append(
                f"Mesh ampacity farkı %{abs(float(mesh_amp)):.4f} > %{profile.mesh_ampacity_percent:.4f}."
            )
        if mesh_temp is None:
            reasons.append("Mesh sıcaklık duyarlılığı kaydı bulunmuyor.")
        elif abs(float(mesh_temp)) > profile.mesh_temperature_c:
            reasons.append(
                f"Mesh sıcaklık farkı {abs(float(mesh_temp)):.4f} °C > {profile.mesh_temperature_c:.4f} °C."
            )
    hard_fail = (
        not bool(region.converged)
        or float(region.energy_balance_error_percent) > profile.energy_balance_percent
        or float(region.maximum_linear_residual) > profile.linear_residual
        or mesh_status == VALIDATION_FAIL
    )
    if hard_fail:
        status = QUALITY_FAIL
    elif mesh is None:
        status = QUALITY_PENDING
        reasons.append("Bağlayıcı nodal hüküm için mesh inceltme kanıtı bekleniyor.")
    else:
        status = QUALITY_PASS
    return NodalQualityEvidence(
        str(region.scenario_id), str(region.region_id), bool(region.converged),
        float(region.energy_balance_error_percent), float(region.maximum_linear_residual),
        mesh_status, None if mesh_amp is None else float(mesh_amp),
        None if mesh_temp is None else float(mesh_temp), status, tuple(reasons),
    )


def _region_comparison(
    analytic: Any,
    nodal: Any,
    geometry_fingerprint: str,
    geometry_basis: str,
    material_field_class: str,
    result_authority: str,
    authoritative_method: str,
    analytical_preview_allowed: bool,
    mesh: Any | None,
    profile: ThermalMethodToleranceProfile,
) -> ThermalMethodRegionComparison:
    quality = _quality(nodal, mesh, profile)
    ia = float(analytic.iec.ampacity_a)
    inn = float(nodal.ampacity_per_cable_a)
    ta = float(analytic.iec.conductor_temperature_at_design_c)
    tn = float(nodal.maximum_conductor_temperature_c)
    di = _percent(ia, inn)
    dt = ta - tn
    same_status = is_suitable(analytic.iec.status) == is_suitable(nodal.status)
    reasons: list[str] = list(quality.reasons)

    dryout_required = bool(getattr(nodal, "dryout_enabled", False))
    analytic_eligible = (
        material_field_class == MATERIAL_HOMOGENEOUS
        and result_authority == RESULT_IEC_ANALYTIC
        and authoritative_method == AUTH_METHOD_ANALYTIC
        and not dryout_required
    )
    nodal_required = (
        authoritative_method == AUTH_METHOD_NODAL
        or material_field_class in {MATERIAL_LAYERED, MATERIAL_COMPLEX_REGIONS}
        or dryout_required
    )
    if nodal_required:
        if quality.status == QUALITY_PASS:
            preview_outside_band = (
                (di is not None and abs(di) > profile.ampacity_absolute_fail_percent)
                or abs(dt) > profile.temperature_absolute_fail_c
                or not same_status
            )
            basis = BASIS_NODAL_BINDING
            validation = VALIDATION_REVIEW if preview_outside_band else VALIDATION_PASS
            if preview_outside_band:
                reasons.append("Nodal üretim otoritesi korunur; analitik engineering-preview ile fark shadow-review bandı dışında.")
        elif quality.status == QUALITY_FAIL:
            basis, validation = BASIS_NODAL_NOT_CONVERGED, VALIDATION_FAIL
        else:
            basis, validation = BASIS_NODAL_QUALITY_PENDING, VALIDATION_NOT_RUN
        reasons.append(
            f"Malzeme alanı={material_field_class}; analitik yetki={result_authority}; "
            + ("kritik-izoterm kuruma etkin olduğu için " if dryout_required else "")
            + "nodal kalite kapısı zorunlu."
        )
    elif analytic_eligible:
        optimistic_fail = (
            (di is not None and di > profile.ampacity_pass_percent)
            or dt < -profile.analytic_optimistic_temperature_fail_c
        )
        absolute_fail = (
            (di is not None and abs(di) > profile.ampacity_absolute_fail_percent)
            or abs(dt) > profile.temperature_absolute_fail_c
            or not same_status
        )
        conservative_review = (
            di is not None and di < -profile.ampacity_pass_percent
            and abs(di) <= profile.ampacity_absolute_fail_percent
            and dt >= -profile.analytic_optimistic_temperature_fail_c
            and abs(dt) <= profile.temperature_absolute_fail_c
            and same_status
        )
        within_pass = (
            di is not None and abs(di) <= profile.ampacity_pass_percent
            and abs(dt) <= profile.temperature_pass_c
            and same_status
        )
        if quality.status == QUALITY_FAIL:
            basis, validation = BASIS_NODAL_NOT_CONVERGED, VALIDATION_FAIL
        elif quality.status == QUALITY_PENDING:
            # An unqualified nodal result may be displayed but cannot invalidate
            # or validate the IEC analytical result.
            basis, validation = BASIS_ANALYTIC_PREVIEW, VALIDATION_NOT_RUN
        elif optimistic_fail or absolute_fail:
            basis, validation = BASIS_METHOD_DISAGREEMENT, VALIDATION_FAIL
            reasons.append("Analitik–nodal farkı kabul bandı dışında veya uygunluk hükümleri çelişiyor.")
        elif within_pass:
            basis, validation = BASIS_ANALYTIC_VALIDATED, VALIDATION_PASS
        elif conservative_review:
            basis, validation = BASIS_ANALYTIC_CONSERVATIVE, VALIDATION_REVIEW
            reasons.append("Analitik sonuç nodala göre konservatif; fark raporda görünür tutulur.")
        else:
            basis, validation = BASIS_METHOD_DISAGREEMENT, VALIDATION_FAIL
            reasons.append("Fark geçiş bandında ancak kabul veya konservatif bias koşullarını birlikte sağlamıyor.")
    elif result_authority == RESULT_DERIVED_FROM_SCALAR or authoritative_method == AUTH_METHOD_LEGACY:
        basis, validation = BASIS_DERIVED_FROM_SCALAR, VALIDATION_NOT_APPLICABLE
        reasons.append("Fiziksel koordinat kaynağı yok; analitik sayı DERIVED_FROM_SCALAR yetkisini geçemez.")
    else:
        basis, validation = BASIS_MANUAL_SOURCE, VALIDATION_NOT_APPLICABLE
        reasons.append("Kaynaklandırılmış manuel T4 bağımsız bir yöntem otoritesidir.")

    return ThermalMethodRegionComparison(
        str(analytic.iec.scenario_id if hasattr(analytic.iec, "scenario_id") else nodal.scenario_id),
        str(analytic.region_id), str(analytic.region_name), geometry_fingerprint,
        material_field_class, ia, inn, di, ta, tn, dt, str(analytic.iec.status), str(nodal.status),
        quality, validation, basis, tuple(reasons), geometry_basis, result_authority, authoritative_method, analytical_preview_allowed,
    )


def _nodal_only_region_comparison(
    nodal: Any,
    geometry_fingerprint: str,
    geometry_basis: str,
    material_field_class: str,
    result_authority: str,
    authoritative_method: str,
    analytical_preview_allowed: bool,
    mesh: Any | None,
    profile: ThermalMethodToleranceProfile,
) -> ThermalMethodRegionComparison:
    quality = _quality(nodal, mesh, profile)
    reasons = list(quality.reasons)
    reasons.append(
        f"Analitik preview bulunmuyor; malzeme alanı={material_field_class}, otorite={authoritative_method}."
    )
    if quality.status == QUALITY_PASS:
        basis, validation = BASIS_NODAL_BINDING, VALIDATION_PASS
    elif quality.status == QUALITY_FAIL:
        basis, validation = BASIS_NODAL_NOT_CONVERGED, VALIDATION_FAIL
    else:
        basis, validation = BASIS_NODAL_QUALITY_PENDING, VALIDATION_NOT_RUN
    return ThermalMethodRegionComparison(
        str(nodal.scenario_id), str(nodal.region_id), str(nodal.region_name),
        geometry_fingerprint, material_field_class, None, float(nodal.ampacity_per_cable_a),
        None, None, float(nodal.maximum_conductor_temperature_c), None,
        "NOT_APPLICABLE", str(nodal.status), quality, validation, basis, tuple(reasons),
        geometry_basis, result_authority, authoritative_method, analytical_preview_allowed,
    )


def _scenario_authority(
    analytic: Any,
    nodal: Any,
    geometry_fingerprint: str,
    geometry_by_region: Mapping[str, tuple[str, str, str, str, str, bool]],
    mesh_checks: Mapping[tuple[str, str], Any],
    profile: ThermalMethodToleranceProfile,
    judgement_basis_status: str,
) -> ThermalMethodScenarioAuthority:
    comparisons: list[ThermalMethodRegionComparison] = []
    analytic_by_id = {item.region_id: item for item in analytic.regions}
    for nregion in nodal.regions:
        aregion = analytic_by_id.get(nregion.region_id)
        local_fp, geometry_basis, material_class, result_authority, authoritative_method, preview_allowed = geometry_by_region.get(
            nregion.region_id, (geometry_fingerprint, GEOMETRY_BASIS_LEGACY, MATERIAL_HOMOGENEOUS, RESULT_DERIVED_FROM_SCALAR, AUTH_METHOD_LEGACY, True)
        )
        mesh = mesh_checks.get((str(nodal.scenario_id), str(nregion.region_id)))
        if aregion is None:
            comparisons.append(_nodal_only_region_comparison(
                nregion, local_fp, geometry_basis, material_class, result_authority, authoritative_method, preview_allowed, mesh, profile
            ))
        else:
            comparisons.append(_region_comparison(
                aregion, nregion, local_fp, geometry_basis, material_class, result_authority, authoritative_method, preview_allowed, mesh, profile,
            ))

    bases = {item.calculation_basis for item in comparisons}
    validations = {item.validation_status for item in comparisons}
    reasons: list[str] = []
    critical_mismatch = (
        comparisons
        and all(item.nodal_quality.status == QUALITY_PASS for item in comparisons)
        and analytic.route_ampacity_a is not None
        and str(analytic.critical_region_id) != str(nodal.critical_region_id)
        and abs(_percent(float(analytic.route_ampacity_a), float(nodal.route_ampacity_per_cable_a)) or 0.0)
        > profile.critical_region_difference_percent
    )
    if critical_mismatch:
        bases.add(BASIS_METHOD_DISAGREEMENT)
        validations.add(VALIDATION_FAIL)
        reasons.append("Analitik ve nodal farklı kritik bölge buldu; güzergâh rating farkı yakınlık bandı dışında.")
    if BASIS_METHOD_DISAGREEMENT in bases:
        basis, validation = BASIS_METHOD_DISAGREEMENT, VALIDATION_FAIL
    elif BASIS_NODAL_NOT_CONVERGED in bases:
        basis, validation = BASIS_NODAL_NOT_CONVERGED, VALIDATION_FAIL
    elif BASIS_NODAL_QUALITY_PENDING in bases:
        basis, validation = BASIS_NODAL_QUALITY_PENDING, VALIDATION_NOT_RUN
    elif BASIS_ANALYTIC_PREVIEW in bases:
        basis, validation = BASIS_ANALYTIC_PREVIEW, VALIDATION_NOT_RUN
    elif bases == {BASIS_NODAL_BINDING}:
        basis = BASIS_NODAL_BINDING
        validation = VALIDATION_REVIEW if VALIDATION_REVIEW in validations else VALIDATION_PASS
    elif BASIS_NODAL_BINDING in bases and bases <= {BASIS_NODAL_BINDING, BASIS_ANALYTIC_VALIDATED, BASIS_ANALYTIC_CONSERVATIVE, BASIS_MANUAL_SOURCE}:
        basis = BASIS_HYBRID_BINDING
        validation = VALIDATION_REVIEW if BASIS_ANALYTIC_CONSERVATIVE in bases else VALIDATION_PASS
    elif BASIS_DERIVED_FROM_SCALAR in bases:
        basis, validation = BASIS_DERIVED_FROM_SCALAR, VALIDATION_NOT_APPLICABLE
    elif BASIS_MANUAL_SOURCE in bases and bases <= {BASIS_MANUAL_SOURCE, BASIS_ANALYTIC_VALIDATED, BASIS_ANALYTIC_CONSERVATIVE}:
        if bases == {BASIS_MANUAL_SOURCE}:
            basis, validation = BASIS_MANUAL_SOURCE, VALIDATION_NOT_APPLICABLE
        else:
            basis = BASIS_HYBRID_BINDING
            validation = VALIDATION_REVIEW if BASIS_ANALYTIC_CONSERVATIVE in bases else VALIDATION_PASS
    elif BASIS_ANALYTIC_CONSERVATIVE in bases:
        basis, validation = BASIS_ANALYTIC_CONSERVATIVE, VALIDATION_REVIEW
    elif bases and bases <= {BASIS_ANALYTIC_VALIDATED}:
        basis, validation = BASIS_ANALYTIC_VALIDATED, VALIDATION_PASS
    elif BASIS_MANUAL_SOURCE in bases:
        basis, validation = BASIS_MANUAL_SOURCE, VALIDATION_NOT_APPLICABLE
    else:
        basis, validation = BASIS_ANALYTIC_PREVIEW, VALIDATION_NOT_RUN

    ia = None if analytic.route_ampacity_a is None else float(analytic.route_ampacity_a)
    inn = float(nodal.route_ampacity_per_cable_a)
    ta = None if analytic.maximum_conductor_temperature_c is None else float(analytic.maximum_conductor_temperature_c)
    tn = float(nodal.maximum_conductor_temperature_c)
    official = None
    if basis in {BASIS_ANALYTIC_VALIDATED, BASIS_ANALYTIC_CONSERVATIVE}:
        official = ia
    elif basis == BASIS_NODAL_BINDING:
        official = inn
    elif basis == BASIS_HYBRID_BINDING:
        # If any section is analytically out of scope, nodal is the route-level
        # aggregator.  A manual-T4 + validated-analytic route keeps the IEC
        # route result because the manual source is already part of that chain.
        official = inn if BASIS_NODAL_BINDING in bases else ia
    reasons.extend(reason for item in comparisons for reason in item.reasons)
    return ThermalMethodScenarioAuthority(
        str(analytic.scenario_id), str(analytic.scenario_name), geometry_fingerprint,
        basis, validation, judgement_basis_status, official, ia, inn, _percent(ia, inn),
        ta, tn, None if ta is None else ta - tn, str(analytic.critical_region_id),
        str(nodal.critical_region_id), tuple(comparisons), tuple(dict.fromkeys(reasons)),
    )


def evaluate_thermal_method_authority(
    project: ProjectData,
    nodal_result: Any,
    *,
    mesh_checks: Mapping[tuple[str, str], Any] | None = None,
    tolerance_profile: ThermalMethodToleranceProfile | None = None,
    cache_hit: bool = False,
) -> ThermalMethodAuthorityResult:
    profile = tolerance_profile or ThermalMethodToleranceProfile()
    mesh_map = mesh_checks or {}
    geometry_fingerprint, geometry_by_region = _geometry_maps(project)
    analytic_by_id = {item.scenario_id: item for item in nodal_result.iec_route_result.scenarios}
    scenarios: list[ThermalMethodScenarioAuthority] = []
    judgement = str(project.cable.data_status or "DRAFT").upper()
    for nodal in nodal_result.scenarios:
        analytic = analytic_by_id.get(nodal.scenario_id)
        if analytic is None:
            continue
        scenarios.append(_scenario_authority(
            analytic, nodal, geometry_fingerprint, geometry_by_region, mesh_map,
            profile, judgement,
        ))
    if not scenarios:
        raise ValueError("Analitik–nodal karşılaştırma için ortak senaryo bulunamadı.")
    bases = {item.calculation_basis for item in scenarios}
    statuses = {item.validation_status for item in scenarios}
    if BASIS_METHOD_DISAGREEMENT in bases:
        overall_basis, overall_status = BASIS_METHOD_DISAGREEMENT, VALIDATION_FAIL
    elif BASIS_NODAL_NOT_CONVERGED in bases:
        overall_basis, overall_status = BASIS_NODAL_NOT_CONVERGED, VALIDATION_FAIL
    elif BASIS_NODAL_QUALITY_PENDING in bases:
        overall_basis, overall_status = BASIS_NODAL_QUALITY_PENDING, VALIDATION_NOT_RUN
    elif BASIS_ANALYTIC_PREVIEW in bases:
        overall_basis, overall_status = BASIS_ANALYTIC_PREVIEW, VALIDATION_NOT_RUN
    elif BASIS_HYBRID_BINDING in bases or (BASIS_NODAL_BINDING in bases and len(bases) > 1) or (BASIS_MANUAL_SOURCE in bases and len(bases) > 1):
        overall_basis = BASIS_HYBRID_BINDING
        overall_status = VALIDATION_REVIEW if VALIDATION_REVIEW in statuses else VALIDATION_PASS
    elif BASIS_NODAL_BINDING in bases:
        overall_basis, overall_status = BASIS_NODAL_BINDING, VALIDATION_PASS
    elif BASIS_ANALYTIC_CONSERVATIVE in bases:
        overall_basis, overall_status = BASIS_ANALYTIC_CONSERVATIVE, VALIDATION_REVIEW
    elif bases == {BASIS_ANALYTIC_VALIDATED}:
        overall_basis, overall_status = BASIS_ANALYTIC_VALIDATED, VALIDATION_PASS
    else:
        overall_basis, overall_status = BASIS_MANUAL_SOURCE, VALIDATION_NOT_APPLICABLE
    return ThermalMethodAuthorityResult(
        datetime.now().isoformat(timespec="seconds"), geometry_fingerprint,
        engine_input_signature(project, ENGINE_ID), ANALYTIC_ENGINE_VERSION, NODAL_ENGINE_VERSION,
        overall_basis, overall_status, judgement, str(nodal_result.active_scenario_id),
        tuple(scenarios),
        (
            "İndirgenebilir geometride IEC analitik motor hızlı tasarım otoritesidir; nodal doğrulayıcıdır.",
            "İndirgenemeyen geometride nodal ancak yakınsama, enerji, residual ve mesh kapıları geçince bağlayıcıdır.",
            "Yöntem uyuşmazlığında resmi ampacity üretilmez ve kullanıcıya sessiz yöntem seçimi yaptırılmaz.",
            f"Geometri fingerprint={geometry_fingerprint}",
        ),
        cache_hit,
    )


def cache_thermal_method_authority(project: ProjectData, result: ThermalMethodAuthorityResult) -> dict[str, Any]:
    successful = result.validation_status in {VALIDATION_PASS, VALIDATION_REVIEW}
    record = record_engine_run(
        project, ENGINE_ID, STATUS_COMPLETE if successful else STATUS_CONDITIONAL,
        result_count=sum(len(item.region_comparisons) for item in result.scenarios),
        warning_count=sum(1 for item in result.scenarios if item.validation_status != VALIDATION_PASS),
        message=f"Termal yöntem otoritesi: {result.calculation_basis} / {result.validation_status}",
        conditional_reasons=[reason for item in result.scenarios for reason in item.reasons][:8],
    )
    record.update({
        "geometry_fingerprint": result.geometry_fingerprint,
        "analytic_engine_version": result.analytic_engine_version,
        "nodal_engine_version": result.nodal_engine_version,
        "authority_result": result.to_dict(),
    })
    return record


def cached_thermal_method_authority(project: ProjectData) -> dict[str, Any] | None:
    record = project.workflow.engine_runs.get(ENGINE_ID)
    if not isinstance(record, dict):
        return None
    fingerprint, _ = _geometry_maps(project)
    if record.get("geometry_fingerprint") != fingerprint:
        return None
    if record.get("input_signature") != engine_input_signature(project, ENGINE_ID):
        return None
    if record.get("analytic_engine_version") != ANALYTIC_ENGINE_VERSION:
        return None
    if record.get("nodal_engine_version") != NODAL_ENGINE_VERSION:
        return None
    payload = record.get("authority_result")
    return payload if isinstance(payload, dict) else None
