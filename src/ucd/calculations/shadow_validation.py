"""v0.16.8 validation and shadow-comparison coordinator.

The module is additive and read-only.  It compares the locked production
IEC/bonding/nodal path with the v0.16.7 closed-loop physical shadow path,
checks numerical invariants and records the evidence still required before a
future PHYSICAL_PRIMARY promotion.  It never writes project inputs, lambda1,
engine-run records or schema fields.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import isfinite
from typing import Iterable

from ucd.calculations.bonding import BondingResult
from ucd.calculations.cable_physical_parameters import (
    PhysicalCableParameterResult,
    PhysicalParameterInputError,
    solve_cable_physical_parameters,
)
from ucd.calculations.calculation_policy import CalculationPolicyAudit, audit_calculation_policy
from ucd.calculations.electrothermal_coupled import (
    ElectroThermalAmpacityResult,
    ElectroThermalCoupledResult,
    solve_electrothermal_ampacity,
    solve_electrothermal_coupled,
)
from ucd.calculations.iec60287 import Iec60287SectionResult, solve_project
from ucd.calculations.installation import InstallationValidationIssue, validate_installation_design
from ucd.calculations.project_geometry_runtime import materialize_project_route_sections, solve_project_bonding
from ucd.calculations.nodal_thermal import NodalRouteStudyResult, solve_nodal_route
from ucd.calculations.source_audit import SourceAuditReport, audit_project_sources
from ucd.models.project import ProjectData


MODE = "SHADOW_VALIDATION"
PROMOTION_TARGET = "PHYSICAL_PRIMARY"
PROMOTION_HOLD = "HOLD_SHADOW"
PROMOTION_PILOT = "CONTROLLED_PILOT_CANDIDATE"
PROMOTION_READY = "PHYSICAL_PRIMARY_ACCEPTANCE_READY"

STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_REVIEW = "REVIEW"
STATUS_INFO = "INFO"
STATUS_NOT_COMPARABLE = "NOT_COMPARABLE"

REFERENCE = (
    "IEC 60287-1-1:2023 steady-state rating/loss framework; "
    "IEC 60287-1-3:2023 parallel single-core phase-current and circulating-loss scope; "
    "IEC 60287-2-1:2023 steady-state thermal-resistance scope; "
    "CIGRE TB 797 sheath-bonding architecture; CIGRE TB 880 rating-tool verification targets"
)

MANDATORY_EXTERNAL_BENCHMARKS: tuple[tuple[str, str], ...] = (
    ("IEC60287_1_1_STEADY_STATE", "IEC 60287-1-1 kararlı durum kayıp ve ampacity vakası"),
    ("IEC60287_1_3_PARALLEL_CURRENT", "IEC 60287-1-3 paralel kablo akım paylaşımı/kılıf kaybı vakası"),
    ("CIGRE_TB797_BONDING", "CIGRE TB 797 bonding gerilim-akım ağı vakası"),
    ("CIGRE_TB880_RATING_TOOL", "CIGRE TB 880 rating aracı doğrulama vakaları"),
)


class ShadowValidationInputError(ValueError):
    pass


@dataclass(frozen=True)
class ShadowValidationToleranceProfile:
    method_current_difference_a: float = 1.0e-6
    method_voltage_difference_v: float = 1.0e-6
    equation_residual: float = 1.0e-8
    phase_constraint_residual_a: float = 1.0e-6
    sheath_kcl_residual_a: float = 1.0e-6
    branch_voltage_residual_v: float = 1.0e-6
    core_voltage_residual_v: float = 1.0e-6
    condition_warning: float = 1.0e10
    condition_fail: float = 1.0e14
    thermal_energy_balance_warning_percent: float = 0.50
    thermal_energy_balance_fail_percent: float = 2.00
    thermal_linear_residual: float = 1.0e-7
    legacy_physical_ampacity_review_percent: float = 10.0
    legacy_physical_temperature_review_c: float = 5.0
    legacy_physical_lambda1_review_percent: float = 10.0


@dataclass(frozen=True)
class ExternalBenchmarkEvidence:
    benchmark_id: str
    passed: bool
    evidence_reference: str
    case_count: int = 1
    source_hash: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ShadowComparisonMetric:
    metric_id: str
    category: str
    label: str
    unit: str
    legacy_value: float | None
    physical_value: float | None
    absolute_difference: float | None
    difference_percent: float | None
    status: str
    reason_code: str
    explanation: str


@dataclass(frozen=True)
class ValidationGateResult:
    gate_id: str
    category: str
    label: str
    status: str
    blocking: bool
    measured_value: str
    acceptance_limit: str
    message: str


@dataclass(frozen=True)
class BenchmarkCaseResult:
    benchmark_id: str
    title: str
    source_type: str
    status: str
    blocking: bool
    evidence_reference: str
    case_count: int
    message: str


@dataclass
class ShadowValidationResult:
    mode: str
    promotion_target: str
    promotion_recommendation: str
    reference: str
    evaluated_at: str
    legacy_iec: tuple[Iec60287SectionResult, ...]
    legacy_bonding: BondingResult
    legacy_nodal: NodalRouteStudyResult
    physical_coupled: ElectroThermalCoupledResult
    physical_ampacity: ElectroThermalAmpacityResult | None
    physical_parameters: PhysicalCableParameterResult | None
    metrics: tuple[ShadowComparisonMetric, ...]
    gates: tuple[ValidationGateResult, ...]
    benchmarks: tuple[BenchmarkCaseResult, ...]
    source_audit: SourceAuditReport
    policy_audit: CalculationPolicyAudit
    installation_issues: tuple[InstallationValidationIssue, ...]
    trace: tuple[str, ...]

    @property
    def blocking_gate_count(self) -> int:
        return sum(1 for item in self.gates if item.blocking and item.status != STATUS_PASS)

    @property
    def failed_gate_count(self) -> int:
        return sum(1 for item in self.gates if item.status in {STATUS_FAIL, STATUS_BLOCKED})

    @property
    def warning_gate_count(self) -> int:
        return sum(1 for item in self.gates if item.status == STATUS_WARNING)

    @property
    def final_design_ready(self) -> bool:
        return self.promotion_recommendation == PROMOTION_READY and self.blocking_gate_count == 0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "promotion_target": self.promotion_target,
            "promotion_recommendation": self.promotion_recommendation,
            "reference": self.reference,
            "evaluated_at": self.evaluated_at,
            "metrics": [asdict(item) for item in self.metrics],
            "gates": [asdict(item) for item in self.gates],
            "benchmarks": [asdict(item) for item in self.benchmarks],
            "summary": {
                "blocking_gate_count": self.blocking_gate_count,
                "failed_gate_count": self.failed_gate_count,
                "warning_gate_count": self.warning_gate_count,
                "final_design_ready": self.final_design_ready,
            },
            "trace": list(self.trace),
        }


def _percent_difference(candidate: float | None, reference: float | None) -> float | None:
    if candidate is None or reference is None:
        return None
    if abs(reference) <= 1.0e-15:
        return 0.0 if abs(candidate) <= 1.0e-15 else None
    return 100.0 * (candidate - reference) / reference


def _metric(
    metric_id: str,
    category: str,
    label: str,
    unit: str,
    legacy: float | None,
    physical: float | None,
    *,
    review_percent: float | None = None,
    review_absolute: float | None = None,
    reason_code: str,
    explanation: str,
) -> ShadowComparisonMetric:
    if legacy is None or physical is None or not (isfinite(legacy) and isfinite(physical)):
        return ShadowComparisonMetric(
            metric_id, category, label, unit, legacy, physical, None, None,
            STATUS_NOT_COMPARABLE, "NO_COMPARABLE_RESULT",
            "İki hesap yolundan en az biri geçerli sayısal sonuç üretmedi.",
        )
    absolute = physical - legacy
    percent = _percent_difference(physical, legacy)
    review = False
    if review_percent is not None and percent is not None and abs(percent) > review_percent:
        review = True
    if review_absolute is not None and abs(absolute) > review_absolute:
        review = True
    return ShadowComparisonMetric(
        metric_id, category, label, unit, legacy, physical, absolute, percent,
        STATUS_REVIEW if review else STATUS_PASS,
        reason_code,
        explanation,
    )


def _gate(
    gate_id: str,
    category: str,
    label: str,
    passed: bool,
    *,
    blocking: bool,
    measured: str,
    limit: str,
    message_pass: str,
    message_fail: str,
    fail_status: str = STATUS_FAIL,
) -> ValidationGateResult:
    return ValidationGateResult(
        gate_id, category, label,
        STATUS_PASS if passed else fail_status,
        blocking,
        measured,
        limit,
        message_pass if passed else message_fail,
    )


def _condition_gate(value: float, profile: ShadowValidationToleranceProfile) -> ValidationGateResult:
    if value >= profile.condition_fail or not isfinite(value):
        return ValidationGateResult(
            "EM_MATRIX_CONDITION", "NUMERICAL", "Elektromanyetik matris koşul sayısı",
            STATUS_FAIL, True, f"{value:.6g}", f"<{profile.condition_fail:.3g}",
            "Matris koşul sayısı fiziksel motorun güvenli çözüm sınırını aşıyor.",
        )
    if value >= profile.condition_warning:
        return ValidationGateResult(
            "EM_MATRIX_CONDITION", "NUMERICAL", "Elektromanyetik matris koşul sayısı",
            STATUS_WARNING, False, f"{value:.6g}", f"uyarı<{profile.condition_warning:.3g}",
            "Matris çözülebilir durumda ancak hassasiyet incelemesi gerektiriyor.",
        )
    return ValidationGateResult(
        "EM_MATRIX_CONDITION", "NUMERICAL", "Elektromanyetik matris koşul sayısı",
        STATUS_PASS, False, f"{value:.6g}", f"<{profile.condition_warning:.3g}",
        "Matris koşul sayısı tanımlı normal doğrulama aralığında.",
    )


def _energy_gate(value: float, profile: ShadowValidationToleranceProfile) -> ValidationGateResult:
    if value > profile.thermal_energy_balance_fail_percent or not isfinite(value):
        status, blocking, message = STATUS_FAIL, True, "2D termal enerji kapanışı kabul sınırını aşıyor."
    elif value > profile.thermal_energy_balance_warning_percent:
        status, blocking, message = STATUS_WARNING, False, "2D termal enerji kapanışı inceleme aralığında."
    else:
        status, blocking, message = STATUS_PASS, False, "2D termal enerji kapanışı kabul aralığında."
    return ValidationGateResult(
        "THERMAL_ENERGY_BALANCE", "NUMERICAL", "2D termal enerji dengesi",
        status, blocking, f"%{value:.6g}",
        f"PASS≤%{profile.thermal_energy_balance_warning_percent:g}; FAIL>%{profile.thermal_energy_balance_fail_percent:g}",
        message,
    )


def _benchmark_results(evidence: Iterable[ExternalBenchmarkEvidence]) -> tuple[BenchmarkCaseResult, ...]:
    by_id = {item.benchmark_id: item for item in evidence}
    results: list[BenchmarkCaseResult] = []
    for benchmark_id, title in MANDATORY_EXTERNAL_BENCHMARKS:
        item = by_id.get(benchmark_id)
        if item is None:
            results.append(BenchmarkCaseResult(
                benchmark_id, title, "EXTERNAL_PUBLISHED_REFERENCE", STATUS_NOT_RUN, True, "", 0,
                "Lisanslı/yayımlanmış referans sayısal vaka kanıtı pakete gömülmemiştir; kullanıcı veya doğrulama laboratuvarı kanıtı gereklidir.",
            ))
        else:
            valid_evidence = bool(item.passed and item.case_count > 0 and item.evidence_reference.strip())
            results.append(BenchmarkCaseResult(
                benchmark_id, title, "EXTERNAL_PUBLISHED_REFERENCE",
                STATUS_PASS if valid_evidence else STATUS_FAIL,
                True,
                item.evidence_reference,
                int(item.case_count),
                item.notes or (
                    "Harici doğrulama kanıtı kabul edildi." if valid_evidence
                    else "Harici doğrulama kaydı başarısız veya izlenebilir kanıt içermiyor."
                ),
            ))
    return tuple(results)


def _build_metrics(
    project: ProjectData,
    legacy_iec: tuple[Iec60287SectionResult, ...],
    legacy_bonding: BondingResult,
    legacy_nodal: NodalRouteStudyResult,
    coupled: ElectroThermalCoupledResult,
    ampacity: ElectroThermalAmpacityResult | None,
    profile: ShadowValidationToleranceProfile,
) -> tuple[ShadowComparisonMetric, ...]:
    iec_ampacity = min((item.ampacity_a for item in legacy_iec), default=None)
    iec_temp = max((item.conductor_temperature_at_design_c for item in legacy_iec), default=None)
    nodal_ampacity = float(legacy_nodal.active.route_ampacity_per_cable_a)
    nodal_temp = float(legacy_nodal.active.maximum_conductor_temperature_c)
    physical_ampacity = None
    if ampacity is not None and ampacity.circuit_rating_currents_a:
        physical_ampacity = min(float(value) for value in ampacity.circuit_rating_currents_a.values())
    physical_temp = float(coupled.final_thermal.maximum_nodal_conductor_temperature_c)
    physical_em = coupled.final_global_em
    topology_is_simple = (
        len({item.circuit_id for item in physical_em.core_results}) == 1
        and all(item.parallel_index == 1 for item in physical_em.core_results)
    )
    topology_note = (
        "Tek devre/tek paralel olduğundan legacy ve fiziksel ağ doğrudan karşılaştırılabilir."
        if topology_is_simple else
        "Fiziksel motor çoklu devre/paralel akım paylaşımı çözer; legacy tek eşdeğer kablo sonucu topoloji bakımından yalnız referanstır."
    )
    return (
        _metric(
            "AMPACITY_IEC_VS_PHYSICAL", "AMPACITY", "IEC 60287 legacy ↔ kapalı çevrim fiziksel ampacity",
            "A", iec_ampacity, physical_ampacity,
            review_percent=profile.legacy_physical_ampacity_review_percent,
            reason_code="THERMAL_AND_LOSS_MODEL_CHANGED",
            explanation="Legacy analitik T1–T4 ve katsayı kayıpları; fiziksel sonuç global akım paylaşımı, kılıf ağı ve gerçek x-y 2D sıcaklık alanı kullanır. " + topology_note,
        ),
        _metric(
            "AMPACITY_NODAL_VS_PHYSICAL", "AMPACITY", "Legacy 2D nodal ↔ kapalı çevrim fiziksel ampacity",
            "A", nodal_ampacity, physical_ampacity,
            review_percent=profile.legacy_physical_ampacity_review_percent,
            reason_code="CURRENT_SHARING_AND_LOSS_FEEDBACK_CHANGED",
            explanation="Her iki yol 2D termal çözüm içerir; fiziksel yol ayrıca eşitsiz core paylaşımı ve sıcaklığa bağlı kılıf/core ağını kapalı çevrimde yeniden çözer. " + topology_note,
        ),
        _metric(
            "TEMPERATURE_IEC_VS_PHYSICAL", "TEMPERATURE", "Tasarım akımında IEC legacy ↔ fiziksel Tmax",
            "°C", iec_temp, physical_temp,
            review_absolute=profile.legacy_physical_temperature_review_c,
            reason_code="GEOMETRY_AND_HEAT_SOURCE_DISTRIBUTION_CHANGED",
            explanation="Legacy sonuç en kötü eşdeğer fazı; fiziksel sonuç gerçek x-y konumundaki en sıcak fiziksel kabloyu verir.",
        ),
        _metric(
            "TEMPERATURE_NODAL_VS_PHYSICAL", "TEMPERATURE", "Legacy 2D nodal ↔ fiziksel Tmax",
            "°C", nodal_temp, physical_temp,
            review_absolute=profile.legacy_physical_temperature_review_c,
            reason_code="ELECTROMAGNETIC_FEEDBACK_CHANGED",
            explanation="Fark; kılıf akımı, eşitsiz paralel akım paylaşımı ve sıcaklık-direnç geri beslemesinden kaynaklanabilir.",
        ),
        _metric(
            "LAMBDA1_LEGACY_VS_PHYSICAL", "LOSSES", "Bonding legacy ↔ global fiziksel λ1",
            "-", float(legacy_bonding.lambda1), float(physical_em.lambda1),
            review_percent=profile.legacy_physical_lambda1_review_percent,
            reason_code="NETWORK_SCOPE_CHANGED",
            explanation="Legacy tek-devre primitive/bonding yolu ile genel N-core/N-kılıf global ağının kılıf kayıp oranı karşılaştırılır. " + topology_note,
        ),
        _metric(
            "SHEATH_LOSS_LEGACY_VS_PHYSICAL", "LOSSES", "Toplam kılıf metal kaybı",
            "W", float(legacy_bonding.total_sheath_loss_w), float(physical_em.total_sheath_metal_loss_w),
            review_percent=profile.legacy_physical_lambda1_review_percent,
            reason_code="NETWORK_SCOPE_CHANGED",
            explanation="Fiziksel sonuç bütün minor section, link-box, sıcaklığa bağlı kılıf direnci ve gerçek geometriyi kullanır. " + topology_note,
        ),
        _metric(
            "CORE_LOSS_LEGACY_VS_PHYSICAL", "LOSSES", "Toplam iletken metal kaybı",
            "W", float(legacy_bonding.total_conductor_loss_w), float(physical_em.total_core_metal_loss_w),
            review_percent=profile.legacy_physical_lambda1_review_percent,
            reason_code="AC_RESISTANCE_AND_CURRENT_SHARING_CHANGED",
            explanation="Fiziksel çözüm her core akımını ayrı çözer ve sıcaklığa bağlı direnci kapalı çevrimde günceller.",
        ),
        _metric(
            "SHEATH_EARTH_VOLTAGE", "BONDING", "Legacy standing voltage ↔ fiziksel kılıf-toprak maksimumu",
            "V", float(legacy_bonding.max_standing_voltage_v), float(physical_em.maximum_sheath_to_earth_voltage_v),
            review_percent=10.0,
            reason_code="OPEN_CIRCUIT_PROFILE_VS_SOLVED_NODE_VOLTAGE",
            explanation="Legacy profil açık-devre indüklenen gerilim özetidir; fiziksel değer kılıf akımı ve bonding ağı çözülmüş gerçek düğüm gerilimidir.",
        ),
    )


def _build_gates(
    project: ProjectData,
    coupled: ElectroThermalCoupledResult,
    ampacity: ElectroThermalAmpacityResult | None,
    physical_parameters: PhysicalCableParameterResult | None,
    source_audit: SourceAuditReport,
    policy_audit: CalculationPolicyAudit,
    installation_issues: tuple[InstallationValidationIssue, ...],
    benchmarks: tuple[BenchmarkCaseResult, ...],
    profile: ShadowValidationToleranceProfile,
) -> tuple[ValidationGateResult, ...]:
    em = coupled.final_global_em
    direct = em.direct
    reduced = em.reduced
    regions = coupled.final_thermal.regions
    max_energy = max((abs(item.nodal_energy_balance_error_percent) for item in regions), default=float("inf"))
    max_linear = max((abs(item.nodal_maximum_linear_residual) for item in regions), default=float("inf"))
    max_condition = max(float(direct.matrix_condition_number), float(reduced.matrix_condition_number))
    installation_errors = [item for item in installation_issues if item.severity == "ERROR"]
    benchmark_pending = [item for item in benchmarks if item.status != STATUS_PASS]

    gates: list[ValidationGateResult] = [
        _gate(
            "CLOSED_LOOP_CONVERGENCE", "COUPLING", "Elektro-termal kapalı çevrim yakınsaması",
            bool(coupled.converged), blocking=True,
            measured=f"{coupled.iteration_count}/{coupled.maximum_iterations}",
            limit=(f"ΔT≤{coupled.temperature_tolerance_c:g} °C; ΔI≤%{coupled.current_tolerance_percent:g}; "
                   f"ΔP≤%{coupled.loss_tolerance_percent:g}"),
            message_pass="Sıcaklık, core/kılıf akımı ve aktif kayıp kapıları birlikte kapandı.",
            message_fail="Kapalı çevrim tanımlı toleranslarda yakınsamadı.",
        ),
        _gate(
            "EM_DUAL_METHOD_AGREEMENT", "NUMERICAL", "GLOBAL_DIRECT_KKT ↔ GLOBAL_SHEATH_SCHUR anlaşması",
            bool(em.methods_agree)
            and em.maximum_method_core_current_difference_a <= profile.method_current_difference_a
            and em.maximum_method_sheath_current_difference_a <= profile.method_current_difference_a
            and em.maximum_method_voltage_difference_v <= profile.method_voltage_difference_v,
            blocking=True,
            measured=(f"ΔIc={em.maximum_method_core_current_difference_a:.3e} A; "
                      f"ΔIsh={em.maximum_method_sheath_current_difference_a:.3e} A; "
                      f"ΔV={em.maximum_method_voltage_difference_v:.3e} V"),
            limit=(f"ΔI≤{profile.method_current_difference_a:.1e} A; "
                   f"ΔV≤{profile.method_voltage_difference_v:.1e} V"),
            message_pass="İki bağımsız kompleks çözüm aynı fiziksel sonucu veriyor.",
            message_fail="Bağımsız elektromanyetik çözüm yöntemleri tolerans dışında ayrışıyor.",
        ),
        _gate(
            "EM_EQUATION_RESIDUAL", "NUMERICAL", "Global denklem residual'ı",
            max(direct.equation_residual, reduced.equation_residual) <= profile.equation_residual,
            blocking=True,
            measured=f"{max(direct.equation_residual, reduced.equation_residual):.3e}",
            limit=f"≤{profile.equation_residual:.1e}",
            message_pass="Global kompleks sistem denklem residual'ı kabul sınırında.",
            message_fail="Global kompleks sistem denklem residual'ı kabul sınırını aşıyor.",
        ),
        _gate(
            "PHASE_CURRENT_CONSTRAINT", "PHYSICS", "Devre/faz akım toplamı korunumu",
            max(direct.phase_constraint_residual_a, reduced.phase_constraint_residual_a) <= profile.phase_constraint_residual_a,
            blocking=True,
            measured=f"{max(direct.phase_constraint_residual_a, reduced.phase_constraint_residual_a):.3e} A",
            limit=f"≤{profile.phase_constraint_residual_a:.1e} A",
            message_pass="Paralel core akımlarının faz toplamı sınır şartını sağlıyor.",
            message_fail="Paralel core faz toplamı akım kısıtını sağlamıyor.",
        ),
        _gate(
            "SHEATH_KCL", "PHYSICS", "Kılıf/link-box/GCC düğüm KCL korunumu",
            max(direct.sheath_kcl_residual_a, reduced.sheath_kcl_residual_a) <= profile.sheath_kcl_residual_a,
            blocking=True,
            measured=f"{max(direct.sheath_kcl_residual_a, reduced.sheath_kcl_residual_a):.3e} A",
            limit=f"≤{profile.sheath_kcl_residual_a:.1e} A",
            message_pass="Metalik ağ düğüm akımları KCL kapısını sağlıyor.",
            message_fail="Metalik ağ KCL residual'ı kabul sınırını aşıyor.",
        ),
        _gate(
            "SHEATH_BRANCH_VOLTAGE", "PHYSICS", "Kılıf dal gerilim denklemi",
            max(direct.sheath_branch_residual_v, reduced.sheath_branch_residual_v) <= profile.branch_voltage_residual_v,
            blocking=True,
            measured=f"{max(direct.sheath_branch_residual_v, reduced.sheath_branch_residual_v):.3e} V",
            limit=f"≤{profile.branch_voltage_residual_v:.1e} V",
            message_pass="Kılıf dallarında indüklenen EMF ve empedans düşümü denklemi kapanıyor.",
            message_fail="Kılıf dal gerilim denklemi residual'ı kabul sınırını aşıyor.",
        ),
        _gate(
            "CORE_VOLTAGE_EQUALITY", "PHYSICS", "Paralel core uçtan uca gerilim eşitliği",
            max(direct.core_voltage_residual_v, reduced.core_voltage_residual_v) <= profile.core_voltage_residual_v,
            blocking=True,
            measured=f"{max(direct.core_voltage_residual_v, reduced.core_voltage_residual_v):.3e} V",
            limit=f"≤{profile.core_voltage_residual_v:.1e} V",
            message_pass="Aynı fazın paralel core gerilim düşümleri eşitlik kısıtını sağlıyor.",
            message_fail="Paralel core gerilim eşitliği residual'ı kabul sınırını aşıyor.",
        ),
        _condition_gate(max_condition, profile),
        _gate(
            "THERMAL_REGION_CONVERGENCE", "THERMAL", "Bütün 2D termal bölgelerin yakınsaması",
            bool(regions) and all(item.nodal_converged for item in regions),
            blocking=True,
            measured=f"{sum(1 for item in regions if item.nodal_converged)}/{len(regions)}",
            limit="tüm bölgeler yakınsamalı",
            message_pass="Bütün etkin termal bölgeler yakınsadı.",
            message_fail="En az bir termal bölge yakınsamadı.",
        ),
        _energy_gate(max_energy, profile),
        _gate(
            "THERMAL_LINEAR_RESIDUAL", "NUMERICAL", "2D termal lineer denklem residual'ı",
            max_linear <= profile.thermal_linear_residual,
            blocking=True,
            measured=f"{max_linear:.3e}",
            limit=f"≤{profile.thermal_linear_residual:.1e}",
            message_pass="2D termal lineer sistem residual'ı kabul sınırında.",
            message_fail="2D termal lineer sistem residual'ı kabul sınırını aşıyor.",
        ),
        _gate(
            "INSTALLATION_MODEL_VALID", "INPUT", "Kablo-Kanal Düzeni veri bütünlüğü",
            not installation_errors, blocking=True,
            measured=f"{len(installation_errors)} hata",
            limit="0 hata",
            message_pass="Fiziksel kablo/devre/faz/slot ve koordinat bütünlüğü geçerli.",
            message_fail="Fiziksel kurulum modelinde çözümü bloke eden veri hataları var.",
        ),
        _gate(
            "PHYSICAL_PARAMETER_SCOPE", "INPUT", "Kablo fiziksel parametre motoru kapsamı",
            bool(physical_parameters and physical_parameters.final_design_ready),
            blocking=True,
            measured=(
                "READY" if physical_parameters and physical_parameters.final_design_ready
                else (f"ERROR={physical_parameters.error_count}" if physical_parameters else "NOT_RUN")
            ),
            limit="final_design_ready=True",
            message_pass="Rdc/skin/proximity/kapasitans/kılıf direnci fiziksel parametre kapısı açık.",
            message_fail="Kablo yapısı veya parametre kanıtı fiziksel hesap için tamamlanmamış.",
            fail_status=STATUS_BLOCKED,
        ),
        _gate(
            "CALCULATION_PROVENANCE", "GOVERNANCE", "Hesap parametresi kaynak/yöntem kapısı",
            not policy_audit.final_design_blocked,
            blocking=True,
            measured=f"hata={policy_audit.error_count}; uyarı={policy_audit.warning_count}",
            limit="nihai kapı BLOKE olmamalı",
            message_pass="Kritik hesap parametreleri için izlenebilir kaynak/yöntem kaydı mevcut.",
            message_fail="Legacy katsayı veya eksik köken kaydı nihai fiziksel motor geçişini bloke ediyor.",
            fail_status=STATUS_BLOCKED,
        ),
        _gate(
            "SOURCE_DATA_AUDIT", "GOVERNANCE", "Proje kaynak veri tutarlılığı",
            source_audit.critical_count == 0 and source_audit.high_count == 0,
            blocking=True,
            measured=f"{source_audit.status}; kritik={source_audit.critical_count}; yüksek={source_audit.high_count}",
            limit="kritik=0 ve yüksek=0",
            message_pass="Proje kaynak denetiminde kritik/yüksek çelişki yok.",
            message_fail="Kaynak veri çelişkileri fiziksel motorun nihai tasarım kullanımını bloke ediyor.",
            fail_status=STATUS_BLOCKED,
        ),
        _gate(
            "CLOSED_LOOP_AMPACITY", "AMPACITY", "Kapalı çevrim ampacity dış döngüsü",
            bool(ampacity and ampacity.converged and ampacity.final_coupled_result.converged),
            blocking=True,
            measured=(
                f"factor={ampacity.rating_factor:.7f}" if ampacity is not None else "NOT_RUN"
            ),
            limit="iç ve dış döngü yakınsamış olmalı",
            message_pass="En sıcak fiziksel kablo sıcaklık sınırına göre rating dış döngüsü kapandı.",
            message_fail="Kapalı çevrim ampacity çalıştırılmadı veya yakınsamadı.",
            fail_status=STATUS_NOT_RUN if ampacity is None else STATUS_FAIL,
        ),
        ValidationGateResult(
            "EXTERNAL_PUBLISHED_BENCHMARKS", "BENCHMARK", "Yayımlanmış IEC/CIGRE doğrulama vakaları",
            STATUS_PASS if not benchmark_pending else STATUS_NOT_RUN,
            True,
            f"{len(benchmarks)-len(benchmark_pending)}/{len(benchmarks)} PASS",
            "zorunlu dört benchmark ailesi PASS",
            "Bütün yayımlanmış harici benchmark kanıtları kabul edildi."
            if not benchmark_pending else
            "TB 880 ve diğer yayımlanmış sayısal referans vakaları izlenebilir kanıtla tamamlanmadan PHYSICAL_PRIMARY geçişi yapılamaz.",
        ),
    ]

    armour_present = bool(project.cable.armour_loss_factor > 0.0) or any(
        "ARMOUR" in str(layer.layer_type).upper() or "ZIRH" in str(layer.layer_type).upper()
        for layer in project.cable.layers
    )
    gates.append(ValidationGateResult(
        "ARMOUR_PHYSICS_SCOPE", "MODEL_SCOPE", "Fiziksel zırh kayıp ağı",
        STATUS_BLOCKED if armour_present else STATUS_PASS,
        bool(armour_present),
        "zırh mevcut" if armour_present else "zırhsız kapsam",
        "zırhlı kabloda doğrulanmış fiziksel zırh modeli",
        "Zırhsız kablo kapsamında blokaj yok."
        if not armour_present else
        "Zırh kaybı hâlâ λ2/fallback düzeyinde; zırhlı kabloda fiziksel motor ana motor yapılamaz.",
    ))
    gates.append(ValidationGateResult(
        "EARTH_RETURN_MODEL_SCOPE", "MODEL_SCOPE", "Toprak dönüş empedansı kapsamı",
        STATUS_WARNING, False,
        "SIMPLIFIED_CARSON_EQUIVALENT_DEPTH",
        "proje frekans/kapsamına doğrulanmış earth-return modeli",
        "Mevcut 50/60 Hz mühendislik çekirdeği simplified-Carson kullanır; tam Pollaczek/Wedepohl-Wilcox ve wideband EMT bu geçişin dışında kalır.",
    ))
    return tuple(gates)


def run_shadow_validation(
    project: ProjectData,
    *,
    tolerance_profile: ShadowValidationToleranceProfile | None = None,
    coupled_result: ElectroThermalCoupledResult | None = None,
    ampacity_result: ElectroThermalAmpacityResult | None = None,
    run_ampacity: bool = True,
    mesh_scale: float = 3.0,
    maximum_closed_loop_iterations: int = 15,
    maximum_rating_iterations: int = 10,
    external_benchmark_evidence: Iterable[ExternalBenchmarkEvidence] = (),
) -> ShadowValidationResult:
    """Run a deterministic, read-only validation bundle for the physical shadow motor."""

    profile = tolerance_profile or ShadowValidationToleranceProfile()
    before = deepcopy(project.to_dict())
    try:
        working_project = deepcopy(project)
        synchronized_routes, _materialization = materialize_project_route_sections(
            working_project, strict=True, mutate_project=False
        )
        legacy_iec = tuple(solve_project(working_project.cable, synchronized_routes))
        legacy_bonding = solve_project_bonding(working_project)
        legacy_nodal = solve_nodal_route(working_project, legacy_bonding)
        coupled = coupled_result or solve_electrothermal_coupled(
            project,
            mesh_scale=mesh_scale,
            maximum_iterations=maximum_closed_loop_iterations,
            temperature_tolerance_c=0.10,
            current_tolerance_percent=0.10,
            loss_tolerance_percent=0.10,
            relaxation_factor=0.60,
        )
        ampacity = ampacity_result
        if ampacity is None and run_ampacity:
            ampacity = solve_electrothermal_ampacity(
                project,
                mesh_scale=mesh_scale,
                maximum_closed_loop_iterations=maximum_closed_loop_iterations,
                maximum_rating_iterations=maximum_rating_iterations,
                temperature_tolerance_c=0.30,
                current_tolerance_a=6.0,
                current_tolerance_percent=0.10,
                loss_tolerance_percent=0.10,
                relaxation_factor=0.60,
            )
    except Exception as exc:
        raise ShadowValidationInputError(str(exc)) from exc

    physical_parameters: PhysicalCableParameterResult | None = None
    try:
        first_section = project.route_sections[0]
        physical_parameters = solve_cable_physical_parameters(
            project.cable,
            first_section,
            target_temperature_c=float(project.cable.max_temperature_c),
            mode="SHADOW_COMPARE",
        )
    except (PhysicalParameterInputError, IndexError):
        physical_parameters = None

    source_audit = audit_project_sources(deepcopy(project))
    policy_audit = audit_calculation_policy(deepcopy(project))
    installation_issues = tuple(validate_installation_design(project))
    benchmarks = _benchmark_results(external_benchmark_evidence)
    metrics = _build_metrics(project, legacy_iec, legacy_bonding, legacy_nodal, coupled, ampacity, profile)
    gates = _build_gates(
        project, coupled, ampacity, physical_parameters,
        source_audit, policy_audit, installation_issues, benchmarks, profile,
    )

    blocking = [item for item in gates if item.blocking and item.status != STATUS_PASS]
    failed = [item for item in gates if item.status in {STATUS_FAIL, STATUS_BLOCKED}]
    if not blocking:
        recommendation = PROMOTION_READY
    elif not failed and all(item.gate_id == "EXTERNAL_PUBLISHED_BENCHMARKS" for item in blocking):
        recommendation = PROMOTION_PILOT
    else:
        recommendation = PROMOTION_HOLD

    if project.to_dict() != before:
        raise ShadowValidationInputError("Shadow doğrulama proje verisini değiştirdi; işlem iptal edildi.")

    trace = (
        "Legacy üretim yolu ve fiziksel gölge yolu aynı proje snapshot'ından bağımsız çalıştırıldı.",
        "Karşılaştırma farkları tek başına PASS/FAIL değildir; neden kodları model kapsamı değişimini açıklar.",
        "Numerik/korunum kapıları başarısızsa fiziksel motor gölgede tutulur.",
        "Yayımlanmış IEC/CIGRE sayısal vaka kanıtları lisans nedeniyle pakete gömülmez; harici izlenebilir kanıt gerektirir.",
        "Bu sonuç proje lambda1, IEC rating, termal girdiler, run registry veya JSON şemasına yazılmaz.",
    )
    return ShadowValidationResult(
        MODE,
        PROMOTION_TARGET,
        recommendation,
        REFERENCE,
        datetime.now().isoformat(timespec="seconds"),
        legacy_iec,
        legacy_bonding,
        legacy_nodal,
        coupled,
        ampacity,
        physical_parameters,
        metrics,
        gates,
        benchmarks,
        source_audit,
        policy_audit,
        installation_issues,
        trace,
    )


def render_shadow_validation(result: ShadowValidationResult) -> str:
    lines = [
        "DiTuS v0.16.8 — Fiziksel Motor Doğrulama ve Shadow Karşılaştırma",
        f"Mod={result.mode}; hedef={result.promotion_target}",
        f"Öneri={result.promotion_recommendation}",
        f"Referans={result.reference}",
        f"Bloke kapı={result.blocking_gate_count}; başarısız/bloke={result.failed_gate_count}; uyarı={result.warning_gate_count}",
        "",
        "KARŞILAŞTIRMA METRİKLERİ",
    ]
    for item in result.metrics:
        legacy = "-" if item.legacy_value is None else f"{item.legacy_value:.9g}"
        physical = "-" if item.physical_value is None else f"{item.physical_value:.9g}"
        delta = "-" if item.absolute_difference is None else f"{item.absolute_difference:+.9g}"
        percent = "-" if item.difference_percent is None else f"%{item.difference_percent:+.4f}"
        lines.append(
            f"[{item.status}] {item.label}: legacy={legacy}; physical={physical}; Δ={delta} {item.unit}; {percent}; {item.reason_code}"
        )
        lines.append(f"  {item.explanation}")
    lines.extend(["", "KABUL KAPILARI"])
    for item in result.gates:
        block = "BLOCKING" if item.blocking else "ADVISORY"
        lines.append(
            f"[{item.status}] {item.gate_id} ({block}): {item.measured_value}; limit={item.acceptance_limit} — {item.message}"
        )
    lines.extend(["", "HARİCİ BENCHMARK KAYITLARI"])
    for item in result.benchmarks:
        lines.append(
            f"[{item.status}] {item.benchmark_id}: case={item.case_count}; evidence={item.evidence_reference or '-'} — {item.message}"
        )
    lines.extend(["", "İZ VE SINIRLAR"])
    lines.extend(f"- {item}" for item in result.trace)
    return "\n".join(lines)
