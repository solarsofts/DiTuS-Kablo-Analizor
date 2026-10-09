from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from ucd.calculations.calculation_policy import resolve_project_alpha_20_per_c
from ucd.calculations.iec60287 import (
    COMPLETION_COMPLETE,
    COMPLETION_FAILED,
    COMPLETION_PARTIAL,
    SUITABILITY_INDETERMINATE,
    SUITABILITY_SUITABLE,
    SUITABILITY_UNSUITABLE,
    CalculationInputError,
    Iec60287SectionResult,
    classify_calculation_error,
    solve_section,
    validate_common_cable_inputs,
)
from ucd.calculations.result_status import is_suitable
from ucd.calculations.soil_dryout import SoilDryoutInputError, material_dryout_profile
from ucd.calculations.thermal_resistance import ThermalInputError
from ucd.calculations.phase_geometry import PhaseGeometryError, normalize_arrangement, phase_slot_offsets_m
from ucd.calculations.installation_coupling import (
    physical_positions_for_region,
    project_with_synchronized_installation_geometry,
)
from ucd.models.project import (
    EXTERNAL_THERMAL_AUTO,
    EXTERNAL_THERMAL_MANUAL,
    EXTERNAL_THERMAL_MIXED,
    CableData,
    ProjectData,
    RouteSection,
    ThermalCrossSectionTemplate,
    ThermalDesignData,
    ThermalMaterialData,
    ThermalRegion,
    THERMAL_INSTALL_DIRECT_BURIED,
    THERMAL_STATE_AS_BUILT,
    THERMAL_STATE_DESIGN,
    THERMAL_STATE_TESTED,
)


ROUTE_THERMAL_REFERENCE = (
    "IEC 60287 route-section workflow; mixed-zone analytical model retained as a comparator to the v0.12 2D nodal solver"
)


class ThermalRouteInputError(ValueError):
    pass


@dataclass(frozen=True)
class ThermalValidationIssue:
    severity: str  # ERROR / WARNING / INFO
    code: str
    message: str
    region_id: str = ""
    chainage_start_m: float = 0.0
    chainage_end_m: float = 0.0


@dataclass(frozen=True)
class EffectiveThermalProfile:
    region_id: str
    region_name: str
    start_m: float
    end_m: float
    template_id: str
    template_name: str
    installation_type: str
    arrangement: str
    burial_depth_m: float
    phase_spacing_m: float
    circuit_spacing_m: float
    ambient_temperature_c: float
    trench_width_m: float
    trench_depth_m: float
    bedding_thickness_m: float
    side_backfill_width_m: float
    cable_cover_height_m: float
    selected_upper_fill_thickness_m: float
    general_upper_fill_thickness_m: float
    surface_layer_thickness_m: float
    groundwater_depth_m: float
    native_soil: ThermalMaterialData
    bedding: ThermalMaterialData
    side_backfill: ThermalMaterialData
    cable_cover: ThermalMaterialData
    selected_upper_fill: ThermalMaterialData
    general_fill: ThermalMaterialData
    surface: ThermalMaterialData | None
    external_thermal_mode: str
    manual_t4_km_w: float
    backfill_effective_radius_m: float
    surface_thermal_correction_km_w: float
    data_state: str
    source_reference: str
    trace: tuple[str, ...]

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


@dataclass(frozen=True)
class ThermalRegionResult:
    region_id: str
    region_name: str
    start_m: float
    end_m: float
    length_m: float
    template_id: str
    installation_type: str
    data_state: str
    native_soil_resistivity_km_w: float
    backfill_resistivity_km_w: float
    regional_lambda1: float
    iec: Iec60287SectionResult
    warnings: tuple[str, ...]
    improvement_suggestions: tuple[str, ...]


@dataclass(frozen=True)
class ThermalRegionOutcome:
    region_id: str
    region_name: str
    start_m: float
    end_m: float
    result: ThermalRegionResult | None = None
    error_code: str = ""
    error_message: str = ""
    error_class: str = ""
    physical_rejection: bool = False
    cable_data_status: str = ""
    region_data_status: str = ""

    @property
    def success(self) -> bool:
        return self.result is not None


@dataclass(frozen=True)
class ThermalValidationClassification:
    project_errors: tuple[ThermalValidationIssue, ...]
    route_errors: tuple[ThermalValidationIssue, ...]
    region_errors: tuple[tuple[str, tuple[ThermalValidationIssue, ...]], ...]
    non_errors: tuple[ThermalValidationIssue, ...]

    def errors_for_region(self, region_id: str) -> tuple[ThermalValidationIssue, ...]:
        return next((items for key, items in self.region_errors if key == region_id), ())

    @property
    def all_errors(self) -> tuple[ThermalValidationIssue, ...]:
        return self.project_errors + self.route_errors + tuple(
            issue for _key, items in self.region_errors for issue in items
        )


@dataclass(frozen=True)
class ThermalMaterializationResult:
    sections: tuple[RouteSection, ...]
    issues: tuple[ThermalValidationIssue, ...]
    classification: ThermalValidationClassification


@dataclass(frozen=True)
class ThermalScenarioCurrent:
    scenario_id: str
    scenario_name: str
    circuit_current_a: float
    equivalent_scenario_ids: tuple[str, ...]
    equivalent_scenario_names: tuple[str, ...]


@dataclass(frozen=True)
class ThermalRouteScenarioResult:
    scenario_id: str
    scenario_name: str
    total_route_length_m: float
    design_current_per_cable_a: float
    regions: tuple[ThermalRegionResult, ...]
    critical_region_id: str
    critical_region_name: str
    route_ampacity_a: float | None
    maximum_conductor_temperature_c: float | None
    status: str
    critical_reasons: tuple[str, ...]
    validation_issues: tuple[ThermalValidationIssue, ...]
    trace: tuple[str, ...]
    completion_status: str = COMPLETION_COMPLETE
    suitability_status: str = SUITABILITY_SUITABLE
    region_outcomes: tuple[ThermalRegionOutcome, ...] = ()
    equivalent_scenario_ids: tuple[str, ...] = ()
    equivalent_scenario_names: tuple[str, ...] = ()
    ampacity_upper_bound_a: float | None = None
    temperature_lower_bound_c: float | None = None
    provisional_critical_region_id: str = ""
    provisional_critical_region_name: str = ""
    judgement_basis_status: str = ""




@dataclass(frozen=True)
class ThermalRouteStudyResult:
    reference: str
    scenarios: tuple[ThermalRouteScenarioResult, ...]
    active_scenario_id: str
    validation_issues: tuple[ThermalValidationIssue, ...]

    @property
    def active(self) -> ThermalRouteScenarioResult:
        for scenario in self.scenarios:
            if scenario.scenario_id == self.active_scenario_id:
                return scenario
        return self.scenarios[-1]


def _material_map(design: ThermalDesignData) -> dict[str, ThermalMaterialData]:
    return {material.material_id: material for material in design.materials}


def _template_map(design: ThermalDesignData) -> dict[str, ThermalCrossSectionTemplate]:
    return {template.template_id: template for template in design.templates}


def _material_or_error(
    materials: dict[str, ThermalMaterialData], material_id: str, label: str, region_id: str
) -> ThermalMaterialData:
    material = materials.get(str(material_id))
    if material is None:
        raise ThermalRouteInputError(
            f"{region_id}: {label} malzemesi bulunamadı: {material_id or '(boş)'}"
        )
    if material.thermal_resistivity_km_w <= 0:
        raise ThermalRouteInputError(
            f"{region_id}: {material.name} için ısıl özdirenç sıfırdan büyük olmalıdır."
        )
    return material


def _merged_template_values(template: ThermalCrossSectionTemplate, region: ThermalRegion) -> dict[str, Any]:
    values = asdict(template)
    allowed = set(values) | {
        "ambient_temperature_c",
        "native_soil_thermal_resistivity_km_w",
        "backfill_thermal_resistivity_km_w",
        "surface_thermal_correction_km_w",
        "material_field_class",
        "analytical_preview_allowed",
        "authoritative_method",
        "analytic_result_authority",
        "authority_reason_code",
        "authority_reason_message",
        "surface_correction_raw_km_w",
        "surface_correction_clamped",
        "geometry_basis",
        "geometry_fingerprint",
        "section_type",
        "cross_section_id",
    }
    for key, value in dict(region.overrides or {}).items():
        if key in allowed and value not in (None, ""):
            values[key] = value
    return values


def _derive_backfill_radius_m(
    cable: CableData,
    values: dict[str, Any],
) -> float:
    explicit = float(values.get("backfill_effective_radius_m", 0.0) or 0.0)
    if explicit > 0:
        return explicit

    diameter_m = float(cable.overall_diameter_mm) / 1000.0
    radius_m = diameter_m / 2.0
    spacing = float(values.get("phase_spacing_m", 0.15))
    arrangement = str(values.get("arrangement", cable.arrangement)).lower()
    if arrangement in {"flat", "düz", "duz"}:
        cluster_width = 2.0 * spacing + diameter_m
    else:
        cluster_width = spacing + diameter_m
    trench_width = float(values.get("trench_width_m", cluster_width + 0.4))
    cover_height = float(values.get("cable_cover_height_m", 0.30))
    side_room = max(radius_m * 1.05, trench_width / 2.0)
    top_room = max(radius_m * 1.05, cover_height + radius_m)
    return max(radius_m * 1.05, min(side_room, top_room))


def resolve_thermal_region(
    design: ThermalDesignData,
    region: ThermalRegion,
    cable: CableData,
) -> EffectiveThermalProfile:
    templates = _template_map(design)
    materials = _material_map(design)
    template = templates.get(region.template_id)
    if template is None:
        raise ThermalRouteInputError(
            f"{region.region_id}: kesit şablonu bulunamadı: {region.template_id}"
        )
    values = _merged_template_values(template, region)

    native = _material_or_error(
        materials,
        str(values.get("native_soil_material_id", "")),
        "Doğal zemin",
        region.region_id,
    )
    bedding = _material_or_error(
        materials, str(values.get("bedding_material_id", "")), "Yataklama", region.region_id
    )
    side = _material_or_error(
        materials, str(values.get("side_backfill_material_id", "")), "Yan dolgu", region.region_id
    )
    cover = _material_or_error(
        materials, str(values.get("cable_cover_material_id", "")), "Kablo üstü dolgu", region.region_id
    )
    selected = _material_or_error(
        materials,
        str(values.get("selected_upper_fill_material_id", "")),
        "Seçilmiş üst dolgu",
        region.region_id,
    )
    general = _material_or_error(
        materials,
        str(values.get("general_fill_material_id", "")),
        "Genel dolgu",
        region.region_id,
    )
    surface_id = str(values.get("surface_material_id", ""))
    surface = materials.get(surface_id) if surface_id else None

    native_rho = float(values.get("native_soil_thermal_resistivity_km_w", 0.0) or 0.0)
    if native_rho > 0:
        native = replace(native, thermal_resistivity_km_w=native_rho)
    backfill_rho = float(values.get("backfill_thermal_resistivity_km_w", 0.0) or 0.0)
    if backfill_rho > 0:
        cover = replace(cover, thermal_resistivity_km_w=backfill_rho)

    state = str(region.data_state or template.data_state or design.active_data_state).upper()
    source = str(region.source_reference or template.source_reference or "")
    radius = _derive_backfill_radius_m(cable, values)
    mode = str(values.get("external_thermal_mode", template.external_thermal_mode)).upper()

    trace = (
        f"Bölge {region.region_id}: {region.start_m:.3f}-{region.end_m:.3f} m",
        f"Şablon = {template.template_id} / {template.name}",
        f"Veri katmanı = {state}",
        f"Doğal zemin = {native.material_id}, rho={native.thermal_resistivity_km_w:.4f} K·m/W",
        f"Kablo çevresi = {cover.material_id}, rho={cover.thermal_resistivity_km_w:.4f} K·m/W",
        f"Kurulum / dış model = {values.get('installation_type')} / {mode}",
        f"Eşdeğer termal dolgu yarıçapı = {radius:.4f} m",
        "Şablon değerleri bölge override değerleriyle birleştirildi.",
    )
    return EffectiveThermalProfile(
        region.region_id,
        region.name,
        float(region.start_m),
        float(region.end_m),
        template.template_id,
        template.name,
        str(values.get("installation_type", template.installation_type)).upper(),
        str(values.get("arrangement", template.arrangement)),
        float(values.get("burial_depth_m", template.burial_depth_m)),
        float(values.get("phase_spacing_m", template.phase_spacing_m)),
        float(values.get("circuit_spacing_m", template.circuit_spacing_m)),
        float(values.get("ambient_temperature_c", 25.0)),
        float(values.get("trench_width_m", template.trench_width_m)),
        float(values.get("trench_depth_m", template.trench_depth_m)),
        float(values.get("bedding_thickness_m", template.bedding_thickness_m)),
        float(values.get("side_backfill_width_m", template.side_backfill_width_m)),
        float(values.get("cable_cover_height_m", template.cable_cover_height_m)),
        float(values.get("selected_upper_fill_thickness_m", template.selected_upper_fill_thickness_m)),
        float(values.get("general_upper_fill_thickness_m", template.general_upper_fill_thickness_m)),
        float(values.get("surface_layer_thickness_m", template.surface_layer_thickness_m)),
        float(values.get("groundwater_depth_m", template.groundwater_depth_m)),
        native,
        bedding,
        side,
        cover,
        selected,
        general,
        surface,
        mode,
        float(values.get("manual_t4_km_w", template.manual_t4_km_w)),
        radius,
        float(values.get("surface_thermal_correction_km_w", 0.0) or 0.0),
        state,
        source,
        trace,
    )


def validate_thermal_design(
    design: ThermalDesignData,
    cable: CableData,
) -> tuple[ThermalValidationIssue, ...]:
    issues: list[ThermalValidationIssue] = []
    route_length = float(design.route_length_m)
    tolerance = max(1e-6, float(design.coverage_tolerance_m))
    if route_length <= 0:
        return (ThermalValidationIssue("ERROR", "ROUTE_LENGTH", "Termal güzergâh uzunluğu pozitif olmalıdır."),)

    enabled = sorted((r for r in design.regions if r.enabled), key=lambda r: (r.start_m, r.end_m))
    if not enabled:
        return (ThermalValidationIssue("ERROR", "NO_REGIONS", "Etkin termal bölge bulunmuyor."),)

    cursor = 0.0
    for region in enabled:
        start = float(region.start_m)
        end = float(region.end_m)
        if end <= start:
            issues.append(ThermalValidationIssue(
                "ERROR", "INVALID_RANGE", "Bölge bitişi başlangıçtan büyük olmalıdır.",
                region.region_id, start, end,
            ))
            continue
        if start < -tolerance or end > route_length + tolerance:
            issues.append(ThermalValidationIssue(
                "ERROR", "OUTSIDE_ROUTE", "Bölge güzergâh sınırlarının dışında.",
                region.region_id, start, end,
            ))
        if start > cursor + tolerance:
            issues.append(ThermalValidationIssue(
                "ERROR", "COVERAGE_GAP", f"{cursor:.3f}-{start:.3f} m arasında termal bölge boşluğu var.",
                region.region_id, cursor, start,
            ))
        elif start < cursor - tolerance:
            issues.append(ThermalValidationIssue(
                "ERROR", "REGION_OVERLAP", f"Bölge önceki bölgeyle {cursor-start:.3f} m çakışıyor.",
                region.region_id, start, min(cursor, end),
            ))
        cursor = max(cursor, end)

        try:
            profile = resolve_thermal_region(design, region, cable)
        except ThermalRouteInputError as exc:
            issues.append(ThermalValidationIssue(
                "ERROR", "RESOLUTION", str(exc), region.region_id, start, end
            ))
            continue

        diameter_m = cable.overall_diameter_mm / 1000.0
        installation_type = profile.installation_type.upper()
        authority_code = str((region.overrides or {}).get("authority_reason_code", "") or "")
        if authority_code and profile.external_thermal_mode in {EXTERNAL_THERMAL_AUTO, EXTERNAL_THERMAL_MIXED}:
            issues.append(ThermalValidationIssue(
                "WARNING", authority_code,
                str((region.overrides or {}).get("authority_reason_message", "") or authority_code)
                + " Analitik sonuç engineering-preview olarak hesaplanabilir; üretim otoritesi nodal/legacy politika tarafından belirlenir.",
                region.region_id, start, end,
            ))
        if (
            profile.external_thermal_mode in {EXTERNAL_THERMAL_AUTO, EXTERNAL_THERMAL_MIXED}
            and float(profile.groundwater_depth_m) < 90.0
        ):
            issues.append(ThermalValidationIssue(
                "WARNING", "ANALYTIC_GROUNDWATER_BOUNDARY_REQUIRES_NODAL",
                "ANALYTIC_GROUNDWATER_BOUNDARY_REQUIRES_NODAL: Tanımlı yeraltı suyu sınırı "+
                "otomatik image/mixed-zone indirgemesinin kapsamı dışındadır; nodal veya kaynaklandırılmış manuel T4 kullanın.",
                region.region_id, start, end,
            ))

        if installation_type != THERMAL_INSTALL_DIRECT_BURIED and profile.external_thermal_mode in {
            EXTERNAL_THERMAL_AUTO, EXTERNAL_THERMAL_MIXED,
        }:
            issues.append(ThermalValidationIssue(
                "ERROR", "ANALYTIC_MODEL_SCOPE_REQUIRES_NODAL",
                "ANALYTIC_MODEL_SCOPE_REQUIRES_NODAL: Otomatik analitik dış termal model yalnız DIRECT_BURIED kurulumunda geçerlidir; "
                f"kurulum={installation_type}, mod={profile.external_thermal_mode}. "
                "Nodal çözümü seçin veya kaynaklandırılmış pozitif manuel T4 girin.",
                region.region_id, start, end,
            ))
        if (
            profile.external_thermal_mode == EXTERNAL_THERMAL_MIXED
            and profile.cable_cover.thermal_resistivity_km_w
            > profile.native_soil.thermal_resistivity_km_w
        ):
            issues.append(ThermalValidationIssue(
                "ERROR", "MIXED_ZONE_POOR_BACKFILL_REQUIRES_NODAL",
                "MIXED_ZONE_POOR_BACKFILL_REQUIRES_NODAL: Kablo çevresi dolgusunun ısıl "
                "özdirenci doğal zeminden yüksek. Karşılıklı ısınmayı eksik tahmin etmemek için "
                "2D nodal çözümü veya kaynaklandırılmış manuel T4 kullanın.",
                region.region_id, start, end,
            ))

        try:
            normalized_arrangement = normalize_arrangement(profile.arrangement)
        except PhaseGeometryError as exc:
            issues.append(ThermalValidationIssue(
                "ERROR", "ARRANGEMENT_UNSUPPORTED", str(exc), region.region_id, start, end,
            ))
            normalized_arrangement = "CUSTOM"
        if normalized_arrangement == "DUCT_BANK":
            issues.append(ThermalValidationIssue(
                "ERROR", "ARRANGEMENT_INSTALLATION_CONFLATION",
                "DUCT_BANK faz formasyonu değildir; kurulum tipi olarak seçilmeli ve faz formasyonu "
                "TREFOIL, FLAT, VERTICAL, SINGLE veya CUSTOM olmalıdır.",
                region.region_id, start, end,
            ))

        relative_depth = 0.0
        if normalized_arrangement not in {"CUSTOM", "DUCT_BANK"}:
            try:
                offsets = phase_slot_offsets_m(normalized_arrangement, profile.phase_spacing_m, "ABC")
                relative_depth = max(y for _x, y in offsets.values())
            except PhaseGeometryError as exc:
                issues.append(ThermalValidationIssue(
                    "ERROR", "ARRANGEMENT_GEOMETRY", str(exc), region.region_id, start, end,
                ))
        if installation_type == THERMAL_INSTALL_DIRECT_BURIED and normalized_arrangement == "VERTICAL"                 and profile.phase_spacing_m < diameter_m - 1e-9:
            issues.append(ThermalValidationIssue(
                "ERROR", "VERTICAL_PHASE_OVERLAP",
                f"Düşey formasyonda faz eksen aralığı ({profile.phase_spacing_m:.4f} m) "
                f"kablo dış çapından ({diameter_m:.4f} m) küçük olamaz.",
                region.region_id, start, end,
            ))

        if normalized_arrangement == "FLAT":
            cluster_width = 2.0 * profile.phase_spacing_m + diameter_m
        elif normalized_arrangement == "VERTICAL":
            cluster_width = diameter_m
        elif normalized_arrangement == "SINGLE":
            cluster_width = diameter_m
        else:
            cluster_width = profile.phase_spacing_m + diameter_m
        required_width = cluster_width + 2.0 * profile.side_backfill_width_m
        if installation_type == THERMAL_INSTALL_DIRECT_BURIED and profile.trench_width_m + 1e-9 < required_width:
            issues.append(ThermalValidationIssue(
                "ERROR", "TRENCH_WIDTH",
                f"Hendek genişliği {profile.trench_width_m:.3f} m; kablo kümesi ve yan dolgu için en az {required_width:.3f} m gerekli.",
                region.region_id, start, end,
            ))
        if profile.burial_depth_m <= diameter_m / 2.0:
            issues.append(ThermalValidationIssue(
                "ERROR", "BURIAL_DEPTH", "En sığ kablo eksen derinliği kablo yarıçapından büyük olmalıdır.",
                region.region_id, start, end,
            ))
        deepest_bottom = profile.burial_depth_m + relative_depth + diameter_m / 2.0
        if installation_type == THERMAL_INSTALL_DIRECT_BURIED and profile.trench_depth_m + tolerance < deepest_bottom:
            issues.append(ThermalValidationIssue(
                "ERROR", "TRENCH_DEPTH", "Kablo geometrisi hendek derinliği dışına taşıyor.",
                region.region_id, start, end,
            ))
        if profile.external_thermal_mode == EXTERNAL_THERMAL_MANUAL and profile.manual_t4_km_w <= 0:
            issues.append(ThermalValidationIssue(
                "ERROR", "MANUAL_T4", "Özel kurulum için pozitif manuel T4 gereklidir.",
                region.region_id, start, end,
            ))
        if profile.data_state not in {THERMAL_STATE_DESIGN, THERMAL_STATE_TESTED, THERMAL_STATE_AS_BUILT}:
            issues.append(ThermalValidationIssue(
                "WARNING", "DATA_STATE", f"Bilinmeyen veri katmanı: {profile.data_state}",
                region.region_id, start, end,
            ))

        template = _template_map(design).get(region.template_id)
        if template is not None:
            nodal_values = _merged_template_values(template, region)
            if bool(nodal_values.get("nodal_enabled", True)):
                for key, label in (
                    ("nodal_domain_half_width_m", "Nodal alan yarı genişliği"),
                    ("nodal_domain_depth_m", "Nodal alan derinliği"),
                    ("nodal_base_step_m", "Temel mesh adımı"),
                    ("nodal_refined_step_m", "İnceltilmiş mesh adımı"),
                    ("nodal_refinement_radius_m", "Mesh inceltme yarıçapı"),
                    ("cable_effective_conductivity_w_mk", "Kablo eşdeğer iletkenliği"),
                ):
                    if float(nodal_values.get(key, 0.0) or 0.0) <= 0:
                        issues.append(ThermalValidationIssue(
                            "ERROR", "NODAL_PARAMETER", f"{label} sıfırdan büyük olmalıdır.",
                            region.region_id, start, end,
                        ))
                base_step = float(nodal_values.get("nodal_base_step_m", 0.0) or 0.0)
                refined_step = float(nodal_values.get("nodal_refined_step_m", 0.0) or 0.0)
                if base_step > 0 and refined_step > base_step:
                    issues.append(ThermalValidationIssue(
                        "WARNING", "NODAL_MESH_ORDER",
                        "İnceltilmiş mesh adımı temel mesh adımından büyük; solver temel adımla sınırlar.",
                        region.region_id, start, end,
                    ))
                if int(nodal_values.get("nodal_max_cells", 0) or 0) < 100:
                    issues.append(ThermalValidationIssue(
                        "ERROR", "NODAL_MAX_CELLS", "Maksimum nodal hücre sayısı en az 100 olmalıdır.",
                        region.region_id, start, end,
                    ))
                boundary_type = str(nodal_values.get("surface_boundary_type", "")).upper()
                if boundary_type not in {"FIXED_TEMPERATURE", "CONVECTIVE"}:
                    issues.append(ThermalValidationIssue(
                        "ERROR", "NODAL_BOUNDARY",
                        "Yüzey sınırı FIXED_TEMPERATURE veya CONVECTIVE olmalıdır.",
                        region.region_id, start, end,
                    ))
                if boundary_type == "CONVECTIVE" and float(nodal_values.get("surface_heat_transfer_w_m2k", 0.0) or 0.0) <= 0:
                    issues.append(ThermalValidationIssue(
                        "ERROR", "NODAL_SURFACE_H",
                        "Konvektif yüzey için pozitif ısı geçiş katsayısı gereklidir.",
                        region.region_id, start, end,
                    ))
                if float(nodal_values.get("groundwater_conductivity_multiplier", 0.0) or 0.0) < 1.0:
                    issues.append(ThermalValidationIssue(
                        "WARNING", "GROUNDWATER_MULTIPLIER",
                        "Yeraltı suyu iletkenlik çarpanı 1 altında; su etkisi iletkenliği azaltıyor.",
                        region.region_id, start, end,
                    ))
                if profile.installation_type.upper() in {"DUCT_BANK", "HDD"}:
                    duct_inner = float(nodal_values.get("duct_inner_diameter_m", 0.0) or 0.0)
                    duct_outer = float(nodal_values.get("duct_outer_diameter_m", 0.0) or 0.0)
                    if duct_inner <= 0 or duct_outer <= duct_inner:
                        issues.append(ThermalValidationIssue(
                            "ERROR", "DUCT_GEOMETRY",
                            "Duct dış çapı pozitif ve iç çapından büyük olmalıdır.",
                            region.region_id, start, end,
                        ))
        unique_materials = {
            material.material_id: material
            for material in (profile.native_soil, profile.bedding, profile.side_backfill, profile.cable_cover)
        }
        for material in unique_materials.values():
            if material.reliability.upper() == "LOW" or material.source_type.upper() == "PRELIMINARY_ASSUMPTION":
                issues.append(ThermalValidationIssue(
                    "WARNING", "ASSUMED_MATERIAL",
                    f"{material.name}: ölçüm/sertifika ile doğrulanmamış ön tasarım verisi.",
                    region.region_id, start, end,
                ))
        for material in unique_materials.values():
            try:
                dryout = material_dryout_profile(material)
            except SoilDryoutInputError as exc:
                issues.append(ThermalValidationIssue(
                    "ERROR", "DRYOUT_PARAMETERS_INCOMPLETE", str(exc),
                    region.region_id, start, end,
                ))
                continue
            if dryout is not None:
                if dryout.critical_temperature_c <= float(profile.ambient_temperature_c):
                    issues.append(ThermalValidationIssue(
                        "ERROR", "DRYOUT_CRITICAL_NOT_ABOVE_AMBIENT",
                        f"{material.name}: kritik kuruma sıcaklığı ortam sıcaklığından büyük olmalıdır.",
                        region.region_id, start, end,
                    ))
                if str(material.data_state).upper() not in {THERMAL_STATE_TESTED, THERMAL_STATE_AS_BUILT}:
                    issues.append(ThermalValidationIssue(
                        "WARNING", "DRYOUT_DATA_NOT_TESTED",
                        f"{material.name}: kritik kuruma sıcaklığı/kuru ρ proje testiyle doğrulanmamış; sonuç koşulludur.",
                        region.region_id, start, end,
                    ))
        if profile.groundwater_depth_m <= profile.burial_depth_m:
            issues.append(ThermalValidationIssue(
                "INFO", "GROUNDWATER_AT_CABLE",
                "Yeraltı su seviyesi kablo ekseni seviyesinde/üzerinde; seçilen malzeme ısıl değeri bu durumu temsil etmelidir.",
                region.region_id, start, end,
            ))
        if region.transition_type.upper() == "GRADUAL":
            issues.append(ThermalValidationIssue(
                "INFO", "GRADUAL_TRANSITION",
                "Kademeli geçiş, IEC ve 2D orta-kesit çözümünde sabit enine bölge olarak temsil edilir; eksenel 3D geçiş modeli beklenmektedir.",
                region.region_id, start, end,
            ))

    if cursor < route_length - tolerance:
        issues.append(ThermalValidationIssue(
            "ERROR", "COVERAGE_GAP", f"{cursor:.3f}-{route_length:.3f} m arasında termal bölge boşluğu var.",
            "", cursor, route_length,
        ))
    return tuple(issues)


def classify_thermal_validation_issues(
    issues: Iterable[ThermalValidationIssue],
) -> ThermalValidationClassification:
    """Split validation into project-fatal, route-incomplete and region-local issues.

    ROUTE_LENGTH and NO_REGIONS prevent any meaningful matrix from being built.
    An unassigned coverage/topology error makes the route incomplete but does
    not erase otherwise solvable regions. Region-tagged errors stay local.
    """
    project_codes = {"ROUTE_LENGTH", "NO_REGIONS"}
    project_errors: list[ThermalValidationIssue] = []
    route_errors: list[ThermalValidationIssue] = []
    region_map: dict[str, list[ThermalValidationIssue]] = {}
    non_errors: list[ThermalValidationIssue] = []
    for issue in issues:
        if issue.severity != "ERROR":
            non_errors.append(issue)
        elif issue.code in project_codes:
            project_errors.append(issue)
        elif issue.region_id:
            region_map.setdefault(issue.region_id, []).append(issue)
        else:
            route_errors.append(issue)
    return ThermalValidationClassification(
        tuple(project_errors),
        tuple(route_errors),
        tuple((key, tuple(values)) for key, values in sorted(region_map.items())),
        tuple(non_errors),
    )


def _section_from_profile(region: ThermalRegion, profile: EffectiveThermalProfile) -> RouteSection:
    return RouteSection(
        name=f"{region.region_id} {region.name}",
        length_m=profile.length_m,
        section_type=profile.installation_type,
        burial_depth_m=profile.burial_depth_m,
        soil_thermal_resistivity_km_w=profile.native_soil.thermal_resistivity_km_w,
        cross_section_id=profile.template_id,
        ambient_temperature_c=profile.ambient_temperature_c,
        external_thermal_mode=profile.external_thermal_mode,
        phase_spacing_m=profile.phase_spacing_m,
        external_thermal_resistance_t4_km_w=profile.manual_t4_km_w,
        notes=" | ".join(profile.trace),
        start_chainage_m=profile.start_m,
        end_chainage_m=profile.end_m,
        thermal_region_id=profile.region_id,
        thermal_template_id=profile.template_id,
        thermal_data_state=profile.data_state,
        backfill_thermal_resistivity_km_w=profile.cable_cover.thermal_resistivity_km_w,
        backfill_effective_radius_m=profile.backfill_effective_radius_m,
        surface_thermal_correction_km_w=profile.surface_thermal_correction_km_w,
        soil_critical_dryout_temperature_c=float(profile.native_soil.critical_dryout_temperature_c or 0.0),
        soil_dry_state_thermal_resistivity_km_w=float(profile.native_soil.dry_state_thermal_resistivity_km_w or 0.0),
        soil_dryout_data_state=str(profile.native_soil.data_state),
        soil_dryout_source_reference=str(profile.native_soil.source_reference),
    )


def materialize_route_sections_partial(
    design: ThermalDesignData,
    cable: CableData,
) -> ThermalMaterializationResult:
    issues = list(validate_thermal_design(design, cable))
    classification = classify_thermal_validation_issues(issues)
    if classification.project_errors:
        raise ThermalRouteInputError(
            "; ".join(issue.message for issue in classification.project_errors[:5])
        )

    sections: list[RouteSection] = []
    for region in sorted((r for r in design.regions if r.enabled), key=lambda r: r.start_m):
        if classification.errors_for_region(region.region_id):
            continue
        try:
            profile = resolve_thermal_region(design, region, cable)
        except ThermalRouteInputError as exc:
            issues.append(ThermalValidationIssue(
                "ERROR", "RESOLUTION", str(exc), region.region_id, region.start_m, region.end_m
            ))
            continue
        sections.append(_section_from_profile(region, profile))

    # Reclassify to include any materialization-time resolution issue.
    final_issues = tuple(issues)
    return ThermalMaterializationResult(
        tuple(sections), final_issues, classify_thermal_validation_issues(final_issues)
    )


def materialize_route_sections(
    design: ThermalDesignData,
    cable: CableData,
) -> list[RouteSection]:
    """Strict compatibility entry point; partial callers use the detailed API."""
    result = materialize_route_sections_partial(design, cable)
    errors = result.classification.all_errors
    if errors:
        raise ThermalRouteInputError("; ".join(issue.message for issue in errors[:5]))
    return list(result.sections)


def _regional_lambda1(
    bonding_result: Any | None,
    start_m: float,
    end_m: float,
    fallback: float,
) -> float:
    if bonding_result is None:
        return max(0.0, float(fallback))
    primitive = getattr(bonding_result, "primitive_network_result", None)
    total_conductor_loss = float(getattr(bonding_result, "total_conductor_loss_w", 0.0) or 0.0)
    total_length = float(getattr(bonding_result, "total_length_m", 0.0) or 0.0)
    if primitive is None or total_conductor_loss <= 0 or total_length <= 0:
        return max(0.0, float(getattr(bonding_result, "lambda1", fallback)))

    sheath_loss = 0.0
    for section in getattr(primitive, "section_results", ()): 
        overlap = max(0.0, min(end_m, float(section.end_m)) - max(start_m, float(section.start_m)))
        section_length = max(1e-12, float(section.end_m) - float(section.start_m))
        sheath_loss += float(section.sheath_metal_loss_w) * overlap / section_length
    conductor_loss = total_conductor_loss * max(0.0, end_m - start_m) / total_length
    if conductor_loss <= 0:
        return max(0.0, float(getattr(bonding_result, "lambda1", fallback)))
    return max(0.0, sheath_loss / conductor_loss)


def _region_suggestions(
    profile: EffectiveThermalProfile,
    result: Iec60287SectionResult,
) -> tuple[str, ...]:
    suggestions: list[str] = []
    if not is_suitable(result.status):
        if profile.cable_cover.thermal_resistivity_km_w > 0.85:
            suggestions.append("Kablo çevresi dolgunun ısıl özdirencini düşürün veya daha geniş termal dolgu bölgesi değerlendirin.")
        if profile.burial_depth_m > 1.50:
            suggestions.append("Mekanik/çevresel koşullar izin veriyorsa bu bölgede gömülme derinliğini azaltın.")
        suggestions.append("Faz/devre aralığı, iletken kesiti veya paralel kablo/faz alternatifini iterasyona alın.")
        if profile.external_thermal_mode == EXTERNAL_THERMAL_MANUAL:
            suggestions.append("Özel geçiş geometrisini 2D nodal modelle doğrulayın; manuel T4 kaynak belgesini bağlayın.")
    if profile.native_soil.source_type.upper() == "PRELIMINARY_ASSUMPTION":
        suggestions.append("Doğal zemin ısıl özdirencini güzergâh bölgesi için ölçün ve TESTED kaydıyla değiştirin.")
    if profile.cable_cover.source_type.upper() == "PRELIMINARY_ASSUMPTION":
        suggestions.append("Termal dolgu lot/kompaksiyon değerlerini TESTED ve AS_BUILT katmanlarında izleyin.")
    return tuple(dict.fromkeys(suggestions))


def _critical_reasons(regions: Iterable[ThermalRegionResult], critical: ThermalRegionResult) -> tuple[str, ...]:
    all_regions = tuple(regions)
    reasons: list[str] = []
    if critical.iec.t4_km_w >= max(r.iec.t4_km_w for r in all_regions) - 1e-9:
        reasons.append("Güzergâhtaki en yüksek veya eşit en yüksek dış termal direnç T4.")
    if critical.iec.ambient_temperature_c >= max(r.iec.ambient_temperature_c for r in all_regions) - 1e-9:
        reasons.append("Güzergâhtaki en yüksek veya eşit en yüksek ortam sıcaklığı.")
    if critical.regional_lambda1 >= max(r.regional_lambda1 for r in all_regions) - 1e-12:
        reasons.append("Yüksek bölgesel metalik kılıf kayıp faktörü λ1.")
    if critical.installation_type != THERMAL_INSTALL_DIRECT_BURIED:
        reasons.append(f"Özel kurulum tipi: {critical.installation_type}; manuel/nodal termal doğrulama gerektirir.")
    if not is_suitable(critical.iec.status):
        reasons.append("Tasarım akımında iletken sıcaklığı veya ampacity sınırı sağlanmıyor.")
    return tuple(reasons or ("En düşük bölüm ampacity değeri.",))


def _scenario_currents(project: ProjectData) -> tuple[ThermalScenarioCurrent, ...]:
    basis = project.design_basis
    fallback = float(project.cable.design_current_a)
    normal = float(basis.normal_current_per_active_circuit_a or basis.normal_total_current_a or fallback)
    n1 = float(basis.n1_current_per_circuit_a or normal)
    design = float(basis.design_current_per_circuit_a or fallback or n1)
    values = [
        ("NORMAL", "Normal işletme", normal),
        ("N_MINUS_ONE", "N-1 / acil işletme", n1),
        ("DESIGN", "Tasarım-marjlı", design),
    ]
    if any(value < 0.0 for _sid, _name, value in values):
        raise ThermalRouteInputError("Yük senaryosu akımları negatif olamaz.")

    groups: list[list[tuple[str, str, float]]] = []
    for item in values:
        group = next((g for g in groups if abs(g[0][2] - item[2]) < 1e-9), None)
        if group is None:
            groups.append([item])
        else:
            group.append(item)
    priority = {"NORMAL": 1, "N_MINUS_ONE": 2, "DESIGN": 3}
    resolved: list[ThermalScenarioCurrent] = []
    for group in groups:
        canonical = max(group, key=lambda item: priority[item[0]])
        aliases = tuple(item[0] for item in sorted(group, key=lambda item: -priority[item[0]]))
        alias_names = tuple(item[1] for item in sorted(group, key=lambda item: -priority[item[0]]))
        resolved.append(ThermalScenarioCurrent(
            canonical[0], canonical[1], canonical[2], aliases, alias_names
        ))
    return tuple(resolved)


def _display_route_status(completion: str, suitability: str) -> str:
    if completion == COMPLETION_COMPLETE:
        return "UYGUN" if suitability == SUITABILITY_SUITABLE else "UYGUN DEĞİL"
    if completion == COMPLETION_PARTIAL:
        return "HESAP EKSİK — UYGUN DEĞİL" if suitability == SUITABILITY_UNSUITABLE else "HESAP EKSİK"
    return "BAŞARISIZ — UYGUN DEĞİL" if suitability == SUITABILITY_UNSUITABLE else "BAŞARISIZ"


def _data_status_note(project: ProjectData, suitability: str) -> str:
    status = str(project.cable.data_status or "DRAFT").upper()
    if suitability == SUITABILITY_UNSUITABLE and status != "VERIFIED":
        return (
            f"Mühendislik hükmü mevcut {status} kablo verisine göredir; "
            "veri doğrulama statüsü kesin tasarım hükmünü koşullandırır."
        )
    return f"Kablo veri statüsü = {status}."


def _error_outcome(
    project: ProjectData,
    region: ThermalRegion,
    *,
    code: str,
    message: str,
    error_class: str,
    physical_rejection: bool = False,
    region_data_status: str = "",
) -> ThermalRegionOutcome:
    return ThermalRegionOutcome(
        region_id=region.region_id,
        region_name=region.name,
        start_m=float(region.start_m),
        end_m=float(region.end_m),
        error_code=code,
        error_message=message,
        error_class=error_class,
        physical_rejection=physical_rejection,
        cable_data_status=str(project.cable.data_status or "DRAFT").upper(),
        region_data_status=region_data_status or str(region.data_state or "").upper(),
    )


def solve_thermal_route(
    project: ProjectData,
    bonding_result: Any | None = None,
    active_scenario_id: str = "DESIGN",
) -> ThermalRouteStudyResult:
    project = project_with_synchronized_installation_geometry(project)
    try:
        alpha_resolution = resolve_project_alpha_20_per_c(
            project, "cable.temperature_coefficient_20_per_c"
        )
        validate_common_cable_inputs(replace(
            project.cable, temperature_coefficient_20_per_c=alpha_resolution.value_per_c
        ))
    except (CalculationInputError, ValueError) as exc:
        raise ThermalRouteInputError(f"Proje-geneli kablo girdisi geçersiz: {exc}") from exc

    materialized = materialize_route_sections_partial(project.thermal_design, project.cable)
    issues = materialized.issues
    classification = materialized.classification
    section_by_region = {section.thermal_region_id: section for section in materialized.sections}
    enabled_regions = sorted(
        (region for region in project.thermal_design.regions if region.enabled),
        key=lambda region: (region.start_m, region.end_m),
    )
    scenario_results: list[ThermalRouteScenarioResult] = []
    cables_per_phase = max(1, int(project.cable.parallel_cables_per_phase))

    for scenario in _scenario_currents(project):
        current_per_cable = scenario.circuit_current_a / cables_per_phase
        outcomes: list[ThermalRegionOutcome] = []
        successful_regions: list[ThermalRegionResult] = []

        for region in enabled_regions:
            validation_errors = classification.errors_for_region(region.region_id)
            if validation_errors:
                outcomes.append(_error_outcome(
                    project, region,
                    code=validation_errors[0].code,
                    message="; ".join(item.message for item in validation_errors),
                    error_class="VALIDATION",
                ))
                continue
            section = section_by_region.get(region.region_id)
            if section is None:
                outcomes.append(_error_outcome(
                    project, region, code="MATERIALIZATION",
                    message="Bölüm termal çözüme dönüştürülemedi.", error_class="VALIDATION"
                ))
                continue
            try:
                profile = resolve_thermal_region(project.thermal_design, region, project.cable)
                lambda1 = _regional_lambda1(
                    bonding_result, profile.start_m, profile.end_m, project.cable.sheath_loss_factor
                )
                local_cable = replace(
                    project.cable,
                    arrangement=profile.arrangement,
                    design_current_a=current_per_cable,
                    sheath_loss_factor=lambda1,
                    temperature_coefficient_20_per_c=alpha_resolution.value_per_c,
                )
                explicit_positions = physical_positions_for_region(
                    project, region.region_id, active_circuit_count=None
                )
                iec = solve_section(
                    local_cable, section, explicit_positions,
                    temperature_coefficient_resolution=alpha_resolution,
                )
                region_warnings = tuple(
                    issue.message for issue in issues
                    if issue.region_id == region.region_id and issue.severity != "ERROR"
                )
                solved = ThermalRegionResult(
                    region.region_id, region.name, profile.start_m, profile.end_m, profile.length_m,
                    profile.template_id, profile.installation_type, profile.data_state,
                    profile.native_soil.thermal_resistivity_km_w,
                    profile.cable_cover.thermal_resistivity_km_w, lambda1, iec,
                    region_warnings, _region_suggestions(profile, iec),
                )
            except (CalculationInputError, ThermalRouteInputError, ThermalInputError) as exc:
                code, physical = classify_calculation_error(str(exc))
                outcomes.append(_error_outcome(
                    project, region, code=code, message=str(exc),
                    error_class=type(exc).__name__, physical_rejection=physical,
                ))
                continue
            successful_regions.append(solved)
            outcomes.append(ThermalRegionOutcome(
                region.region_id, region.name, profile.start_m, profile.end_m,
                result=solved,
                cable_data_status=str(project.cable.data_status or "DRAFT").upper(),
                region_data_status=profile.data_state,
            ))

        route_incomplete = bool(classification.route_errors)
        if len(successful_regions) == len(enabled_regions) and not route_incomplete:
            completion = COMPLETION_COMPLETE
        elif successful_regions:
            completion = COMPLETION_PARTIAL
        else:
            completion = COMPLETION_FAILED

        ampacity_upper = min(
            (item.iec.ampacity_a for item in successful_regions), default=None
        )
        temperature_lower = max(
            (item.iec.conductor_temperature_at_design_c for item in successful_regions), default=None
        )
        provisional_critical = min(
            successful_regions, key=lambda item: item.iec.ampacity_a, default=None
        )
        definite_unsuitable = any(
            not is_suitable(item.iec.status) for item in successful_regions
        ) or any(item.physical_rejection for item in outcomes)
        if ampacity_upper is not None and ampacity_upper < current_per_cable - 1e-9:
            definite_unsuitable = True
        if temperature_lower is not None and temperature_lower > project.cable.max_temperature_c + 1e-9:
            definite_unsuitable = True

        if definite_unsuitable:
            suitability = SUITABILITY_UNSUITABLE
        elif completion == COMPLETION_COMPLETE:
            suitability = SUITABILITY_SUITABLE
        else:
            suitability = SUITABILITY_INDETERMINATE
        status = _display_route_status(completion, suitability)

        official_ampacity = ampacity_upper if completion == COMPLETION_COMPLETE else None
        official_max_temp = temperature_lower if completion == COMPLETION_COMPLETE else None
        critical = provisional_critical if completion == COMPLETION_COMPLETE else None
        aliases = tuple(
            alias for alias in scenario.equivalent_scenario_ids if alias != scenario.scenario_id
        )
        alias_text = (
            "Eşdeğer çalışma noktaları = " + ", ".join(aliases)
            if aliases else "Eşdeğer çalışma noktası yok."
        )
        trace_lines = [
            f"Senaryo = {scenario.scenario_name}",
            alias_text,
            f"Devre akımı = {scenario.circuit_current_a:.3f} A; paralel kablo/faz = {cables_per_phase}; kablo başı = {current_per_cable:.3f} A",
            f"Sonuç matrisi = {len(successful_regions)}/{len(enabled_regions)} başarılı bölüm",
            f"Tamamlanma = {completion}; uygunluk = {suitability}; gösterim = {status}",
            _data_status_note(project, suitability),
        ]
        if official_ampacity is not None and critical is not None:
            trace_lines.extend([
                f"Hat ampacity = min(bölge ampacity) = {official_ampacity:.3f} A/kablo",
                f"Kritik bölge = {critical.region_id} / {critical.region_name}",
            ])
        elif ampacity_upper is not None:
            trace_lines.append(
                f"Ampacity üst sınırı = {ampacity_upper:.3f} A/kablo; gerçek hat ampacity daha düşük olabilir."
            )
        if temperature_lower is not None and completion != COMPLETION_COMPLETE:
            trace_lines.append(
                f"Maksimum sıcaklık alt sınırı = {temperature_lower:.3f} °C; gerçek maksimum daha yüksek olabilir."
            )
        for outcome in outcomes:
            if not outcome.success:
                trace_lines.append(
                    f"Hücre hatası {scenario.scenario_id}/{outcome.region_id}: "
                    f"{outcome.error_code} — {outcome.error_message}"
                )
        for issue in classification.route_errors:
            trace_lines.append(f"Güzergâh kapsam hatası: {issue.code} — {issue.message}")

        reasons: list[str] = []
        if critical is not None:
            reasons.extend(_critical_reasons(successful_regions, critical))
        elif suitability == SUITABILITY_UNSUITABLE:
            reasons.append("Çözülen bir bölüm veya fiziksel ret kapısı tasarımın yetersizliğini kesinleştirdi.")
        else:
            reasons.append("Bir veya daha fazla bölüm çözülemedi; kesin uygunluk hükmü üretilemez.")
        if str(project.cable.data_status or "DRAFT").upper() != "VERIFIED":
            reasons.append(_data_status_note(project, suitability))

        scenario_results.append(ThermalRouteScenarioResult(
            scenario_id=scenario.scenario_id,
            scenario_name=scenario.scenario_name,
            total_route_length_m=float(project.thermal_design.route_length_m),
            design_current_per_cable_a=current_per_cable,
            regions=tuple(successful_regions),
            critical_region_id=critical.region_id if critical else "",
            critical_region_name=critical.region_name if critical else "",
            route_ampacity_a=official_ampacity,
            maximum_conductor_temperature_c=official_max_temp,
            status=status,
            critical_reasons=tuple(dict.fromkeys(reasons)),
            validation_issues=issues,
            trace=tuple(trace_lines),
            completion_status=completion,
            suitability_status=suitability,
            region_outcomes=tuple(outcomes),
            equivalent_scenario_ids=scenario.equivalent_scenario_ids,
            equivalent_scenario_names=scenario.equivalent_scenario_names,
            ampacity_upper_bound_a=ampacity_upper,
            temperature_lower_bound_c=temperature_lower,
            provisional_critical_region_id=provisional_critical.region_id if provisional_critical else "",
            provisional_critical_region_name=provisional_critical.region_name if provisional_critical else "",
            judgement_basis_status=str(project.cable.data_status or "DRAFT").upper(),
        ))

    if not scenario_results:
        raise ThermalRouteInputError("Termal güzergâh çözümü için yük senaryosu bulunamadı.")
    scenario_ids = {item.scenario_id for item in scenario_results}
    if active_scenario_id not in scenario_ids:
        alias_owner = next((
            item.scenario_id for item in scenario_results
            if active_scenario_id in item.equivalent_scenario_ids
        ), None)
        active_scenario_id = alias_owner or scenario_results[-1].scenario_id
    return ThermalRouteStudyResult(
        ROUTE_THERMAL_REFERENCE, tuple(scenario_results), active_scenario_id, issues
    )

