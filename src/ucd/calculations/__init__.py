from ucd.calculations.fault_epr import (
    REFERENCE as FAULT_REFERENCE,
    FaultGroundPointResult,
    FaultScenarioResult,
    FaultStudyError,
    FaultStudyResult,
    solve_fault_study,
    transfer_fault_tov_to_svl,
)
from ucd.calculations.svl import (
    REFERENCE as SVL_REFERENCE,
    SvlCandidateCheck,
    SvlInputError,
    SvlSelectionResult,
    solve_svl_selection,
)
from ucd.calculations.bonding import (
    BondingInputError,
    BondingLoopResult,
    MajorMatrixResult,
    BondingResult,
    CrossBondingDesignResult,
    DesignIteration,
    MinorSectionInducedVoltage,
    REFERENCE as BONDING_REFERENCE,
    StandingVoltagePoint,
    build_cross_bonding_system,
    induced_sheath_voltage_per_m,
    integrate_minor_voltage,
    physical_sheath_impedance_matrix_ohm_km,
    coupled_major_loop_impedance_matrix,
    resolve_major_paths,
    optimize_cross_bonding,
    sheath_loop_reactance_ohm_km,
    sheath_resistance_ohm_km,
    solve_bonding,
)
from ucd.calculations.iec60287 import (
    CalculationInputError,
    Iec60287SectionResult,
    STANDARD_REFERENCE,
    THERMAL_REFERENCE,
    VALIDATION_REFERENCE,
    solve_project,
    solve_section,
)
from ucd.calculations.thermal_resistance import (
    ExternalThermalResult,
    InternalThermalResult,
    SectionThermalResult,
    ThermalInputError,
    cylindrical_layer_resistance_km_w,
    resolve_external_thermal_resistance,
    resolve_internal_thermal_resistance,
    solve_project_thermal,
    solve_section_thermal,
)

__all__ = [
    "FaultStudyError",
    "FaultGroundPointResult",
    "FaultScenarioResult",
    "FaultStudyResult",
    "FAULT_REFERENCE",
    "solve_fault_study",
    "transfer_fault_tov_to_svl",
    "SvlInputError",
    "SvlCandidateCheck",
    "SvlSelectionResult",
    "SVL_REFERENCE",
    "solve_svl_selection",
    "BondingInputError",
    "BondingLoopResult",
    "MajorMatrixResult",
    "BondingResult",
    "CrossBondingDesignResult",
    "DesignIteration",
    "MinorSectionInducedVoltage",
    "StandingVoltagePoint",
    "BONDING_REFERENCE",
    "build_cross_bonding_system",
    "induced_sheath_voltage_per_m",
    "integrate_minor_voltage",
    "physical_sheath_impedance_matrix_ohm_km",
    "coupled_major_loop_impedance_matrix",
    "resolve_major_paths",
    "optimize_cross_bonding",
    "sheath_loop_reactance_ohm_km",
    "sheath_resistance_ohm_km",
    "solve_bonding",
    "CalculationInputError",
    "Iec60287SectionResult",
    "STANDARD_REFERENCE",
    "THERMAL_REFERENCE",
    "VALIDATION_REFERENCE",
    "solve_project",
    "solve_section",
    "ThermalInputError",
    "InternalThermalResult",
    "ExternalThermalResult",
    "SectionThermalResult",
    "cylindrical_layer_resistance_km_w",
    "resolve_internal_thermal_resistance",
    "resolve_external_thermal_resistance",
    "solve_section_thermal",
    "solve_project_thermal",
]

from ucd.calculations.first_design import (
    FirstDesignInputError,
    LoadCalculationResult,
    apply_candidate_to_project,
    apply_load_calculation,
    calculate_load_basis,
    generate_generic_candidates,
    suggest_voltage_class,
)

__all__ += [
    "FirstDesignInputError",
    "LoadCalculationResult",
    "apply_candidate_to_project",
    "apply_load_calculation",
    "calculate_load_basis",
    "generate_generic_candidates",
    "suggest_voltage_class",
]

from ucd.calculations.thermal_route import (
    ROUTE_THERMAL_REFERENCE,
    EffectiveThermalProfile,
    ThermalMaterializationResult,
    ThermalRegionOutcome,
    ThermalRegionResult,
    ThermalRouteInputError,
    ThermalRouteScenarioResult,
    ThermalRouteStudyResult,
    ThermalValidationClassification,
    ThermalValidationIssue,
    classify_thermal_validation_issues,
    materialize_route_sections,
    materialize_route_sections_partial,
    resolve_thermal_region,
    solve_thermal_route,
    validate_thermal_design,
)

__all__ += [
    "ROUTE_THERMAL_REFERENCE",
    "EffectiveThermalProfile",
    "ThermalMaterializationResult",
    "ThermalRegionOutcome",
    "ThermalRegionResult",
    "ThermalRouteInputError",
    "ThermalRouteScenarioResult",
    "ThermalRouteStudyResult",
    "ThermalValidationClassification",
    "ThermalValidationIssue",
    "classify_thermal_validation_issues",
    "materialize_route_sections",
    "materialize_route_sections_partial",
    "resolve_thermal_region",
    "solve_thermal_route",
    "validate_thermal_design",
]

from ucd.calculations.nodal_thermal import (
    NODAL_THERMAL_REFERENCE,
    MeshConvergenceResult,
    NodalCableResult,
    NodalRegionResult,
    NodalRouteScenarioResult,
    NodalRouteStudyResult,
    NodalThermalInputError,
    check_mesh_convergence,
    solve_nodal_region,
    solve_nodal_route,
)

__all__ += [
    "NODAL_THERMAL_REFERENCE",
    "MeshConvergenceResult",
    "NodalCableResult",
    "NodalRegionResult",
    "NodalRouteScenarioResult",
    "NodalRouteStudyResult",
    "NodalThermalInputError",
    "check_mesh_convergence",
    "solve_nodal_region",
    "solve_nodal_route",
]

from ucd.calculations.thermal_review import (
    ThermalReviewSummary,
    build_thermal_review_summaries,
    extract_material_boundary_segments,
    extract_quantized_isotherm_segments,
    find_nodal_region_result,
    review_order,
)

__all__ += [
    "ThermalReviewSummary",
    "build_thermal_review_summaries",
    "extract_material_boundary_segments",
    "extract_quantized_isotherm_segments",
    "find_nodal_region_result",
    "review_order",
]

from ucd.calculations.transient_thermal import (
    TRANSIENT_THERMAL_REFERENCE,
    TransientPointResult,
    TransientRegionResult,
    TransientRouteStudyResult,
    TransientThermalInputError,
    solve_transient_region,
    solve_transient_route,
)

__all__ += [
    "TRANSIENT_THERMAL_REFERENCE",
    "TransientPointResult",
    "TransientRegionResult",
    "TransientRouteStudyResult",
    "TransientThermalInputError",
    "solve_transient_region",
    "solve_transient_route",
]

from ucd.calculations.thermal_optimization import (
    ThermalDesignAlternative,
    ThermalParameterChange,
    apply_thermal_design_alternative,
    evaluate_thermal_design_alternatives,
)

__all__ += [
    "ThermalDesignAlternative",
    "ThermalParameterChange",
    "apply_thermal_design_alternative",
    "evaluate_thermal_design_alternatives",
]

from ucd.calculations.cable_library import (
    REFERENCE as CABLE_LIBRARY_REFERENCE,
    CableDerivedValues,
    CableLibraryInputError,
    CableValidationIssue,
    CableValidationReport,
    apply_catalog_record,
    cable_from_dict,
    cable_snapshot_hash,
    catalog_package_from_dict,
    catalog_package_to_dict,
    catalog_record_from_cable,
    create_project_snapshot,
    derive_from_layers,
    filter_catalog_records,
    merge_catalog_library,
    synchronize_cable_from_layers,
    update_cable_validation_state,
    validate_cable,
)

__all__ += [
    "CABLE_LIBRARY_REFERENCE",
    "CableDerivedValues",
    "CableLibraryInputError",
    "CableValidationIssue",
    "CableValidationReport",
    "apply_catalog_record",
    "cable_from_dict",
    "cable_snapshot_hash",
    "catalog_package_from_dict",
    "catalog_package_to_dict",
    "catalog_record_from_cable",
    "create_project_snapshot",
    "derive_from_layers",
    "filter_catalog_records",
    "merge_catalog_library",
    "synchronize_cable_from_layers",
    "update_cable_validation_state",
    "validate_cable",
]

from ucd.calculations.source_audit import (
    SourceAuditIssue,
    SourceAuditReport,
    audit_project_sources,
    audit_source_data,
    render_source_audit,
)

__all__ += [
    "SourceAuditIssue",
    "SourceAuditReport",
    "audit_project_sources",
    "audit_source_data",
    "render_source_audit",
]

from ucd.calculations.project_application import (
    REFERENCE as PROJECT_APPLICATION_REFERENCE,
    ApplicationIterationGate,
    ApplicationIterationSummary,
    CableCompletionReport,
    ProjectApplicationResult,
    ProjectCableApplicationError,
    ProjectVoltageDropResult,
    apply_catalog_candidate_to_project,
    assess_cable_completion,
    calculate_project_voltage_drop,
    evaluate_application_iteration_gates,
    resolve_source_conflict,
)

__all__ += [
    "PROJECT_APPLICATION_REFERENCE",
    "ApplicationIterationGate",
    "ApplicationIterationSummary",
    "CableCompletionReport",
    "ProjectApplicationResult",
    "ProjectCableApplicationError",
    "ProjectVoltageDropResult",
    "apply_catalog_candidate_to_project",
    "assess_cable_completion",
    "calculate_project_voltage_drop",
    "evaluate_application_iteration_gates",
    "resolve_source_conflict",
]

from ucd.calculations.catalog_comparison import (
    REFERENCE as CATALOG_COMPARISON_REFERENCE,
    CatalogComparisonCandidate,
    CatalogComparisonResult,
    CatalogParameterRow,
    compare_catalog_candidates,
    render_catalog_comparison_html,
    render_catalog_comparison_markdown,
    write_catalog_comparison_report,
)

__all__ += [
    "CATALOG_COMPARISON_REFERENCE",
    "CatalogComparisonCandidate",
    "CatalogComparisonResult",
    "CatalogParameterRow",
    "compare_catalog_candidates",
    "render_catalog_comparison_html",
    "render_catalog_comparison_markdown",
    "write_catalog_comparison_report",
]

from ucd.calculations.reporting import (
    REFERENCE as REPORTING_REFERENCE,
    ALL_MODULES,
    MODULE_LABELS,
    REPORT_TYPE_LABELS,
    REPORT_TEMPLATES,
    REPORT_CALCULATION,
    REPORT_DESIGN,
    REPORT_SUMMARY,
    REPORT_FULL,
    MODULE_PROJECT,
    MODULE_DESIGN_BASIS,
    MODULE_SOURCE_AUDIT,
    MODULE_CABLE,
    MODULE_ROUTE,
    MODULE_VOLTAGE_DROP,
    MODULE_IEC60287,
    MODULE_NODAL,
    MODULE_TRANSIENT,
    MODULE_BONDING,
    MODULE_FAULT,
    MODULE_SVL,
    MODULE_PROCUREMENT,
    MODULE_TRACE,
    MODULE_WARNINGS,
    ReportMetadata,
    ReportConfiguration,
    ReportTable,
    ReportSection,
    CalculationResultsBundle,
    ProjectReport,
    ReportGenerationError,
    default_report_configuration,
    build_project_report,
    render_project_report_markdown,
    render_project_report_html,
    write_project_report,
)

__all__ += [
    "REPORTING_REFERENCE",
    "ALL_MODULES",
    "MODULE_LABELS",
    "REPORT_TYPE_LABELS",
    "REPORT_TEMPLATES",
    "REPORT_CALCULATION",
    "REPORT_DESIGN",
    "REPORT_SUMMARY",
    "REPORT_FULL",
    "MODULE_PROJECT",
    "MODULE_DESIGN_BASIS",
    "MODULE_SOURCE_AUDIT",
    "MODULE_CABLE",
    "MODULE_ROUTE",
    "MODULE_VOLTAGE_DROP",
    "MODULE_IEC60287",
    "MODULE_NODAL",
    "MODULE_TRANSIENT",
    "MODULE_BONDING",
    "MODULE_FAULT",
    "MODULE_SVL",
    "MODULE_PROCUREMENT",
    "MODULE_TRACE",
    "MODULE_WARNINGS",
    "ReportMetadata",
    "ReportConfiguration",
    "ReportTable",
    "ReportSection",
    "CalculationResultsBundle",
    "ProjectReport",
    "ReportGenerationError",
    "default_report_configuration",
    "build_project_report",
    "render_project_report_markdown",
    "render_project_report_html",
    "write_project_report",
]

from ucd.calculations.procurement import (
    REFERENCE as PROCUREMENT_REFERENCE,
    STATUS_CONFIRMED,
    STATUS_CONDITIONAL as PROCUREMENT_STATUS_CONDITIONAL,
    STATUS_ASSUMPTION,
    CATEGORY_CABLE,
    CATEGORY_ACCESSORY,
    CATEGORY_BONDING,
    CATEGORY_GROUNDING,
    CATEGORY_CIVIL,
    CATEGORY_MARKING,
    VIEW_BOQ,
    VIEW_BOM,
    VIEW_RFQ,
    QuantityBasis,
    ProcurementLine,
    DrumCut,
    DrumAssignment,
    UnassignedDrumCut,
    DrumPlanSummary,
    ProcurementSummary,
    ProcurementPackage,
    ProcurementInputError,
    build_procurement_package,
    render_procurement_markdown,
    render_procurement_html,
    write_procurement_package,
)

__all__ += [
    "PROCUREMENT_REFERENCE",
    "STATUS_CONFIRMED",
    "PROCUREMENT_STATUS_CONDITIONAL",
    "STATUS_ASSUMPTION",
    "CATEGORY_CABLE",
    "CATEGORY_ACCESSORY",
    "CATEGORY_BONDING",
    "CATEGORY_GROUNDING",
    "CATEGORY_CIVIL",
    "CATEGORY_MARKING",
    "VIEW_BOQ",
    "VIEW_BOM",
    "VIEW_RFQ",
    "QuantityBasis",
    "ProcurementLine",
    "DrumCut",
    "DrumAssignment",
    "UnassignedDrumCut",
    "DrumPlanSummary",
    "ProcurementSummary",
    "ProcurementPackage",
    "ProcurementInputError",
    "build_procurement_package",
    "render_procurement_markdown",
    "render_procurement_html",
    "write_procurement_package",
]

from ucd.calculations.project_workflow import (
    STATUS_BLOCKED,
    STATUS_COMPLETE,
    STATUS_CONDITIONAL,
    STATUS_MISSING_DATA,
    STATUS_NOT_STARTED,
    STATUS_PRELIMINARY,
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_STALE,
    WORKFLOW_STATUSES,
    ProjectWorkflowEvaluation,
    WorkflowStageEvaluation,
    WorkflowStageSpec,
    engine_input_components,
    engine_input_signature,
    mark_engine_runs_stale,
    record_engine_run,
    evaluate_project_workflow,
    workflow_stage_specs,
)

__all__ += [
    "STATUS_BLOCKED",
    "STATUS_COMPLETE",
    "STATUS_CONDITIONAL",
    "STATUS_MISSING_DATA",
    "STATUS_NOT_STARTED",
    "STATUS_PRELIMINARY",
    "STATUS_READY",
    "STATUS_RUNNING",
    "STATUS_STALE",
    "WORKFLOW_STATUSES",
    "ProjectWorkflowEvaluation",
    "WorkflowStageEvaluation",
    "WorkflowStageSpec",
    "engine_input_components",
    "engine_input_signature",
    "mark_engine_runs_stale",
    "record_engine_run",
    "evaluate_project_workflow",
    "workflow_stage_specs",
]

from ucd.calculations.engine_precheck import (
    GATE_HARD,
    GATE_SOFT,
    CHECK_OK,
    CHECK_MISSING,
    CHECK_ASSUMPTION,
    PRECHECK_BLOCKED,
    PRECHECK_CONDITIONAL,
    PRECHECK_READY,
    MATURITY_SCREENING,
    MATURITY_CONDITIONAL,
    MATURITY_VERIFIED,
    EngineMethodProfile,
    EnginePrecheckItem,
    EnginePrecheckResult,
    engine_method_profile,
    evaluate_engine_precheck,
)

__all__ += [
    "GATE_HARD", "GATE_SOFT", "CHECK_OK", "CHECK_MISSING", "CHECK_ASSUMPTION",
    "PRECHECK_BLOCKED", "PRECHECK_CONDITIONAL", "PRECHECK_READY",
    "MATURITY_SCREENING", "MATURITY_CONDITIONAL", "MATURITY_VERIFIED",
    "EngineMethodProfile", "EnginePrecheckItem", "EnginePrecheckResult",
    "engine_method_profile", "evaluate_engine_precheck",
]

from ucd.calculations.application_database import (
    load_application_cable_database,
    save_application_cable_database,
)

__all__ += [
    "load_application_cable_database",
    "save_application_cable_database",
]

from ucd.calculations.installation import (
    INSTALLATION_REFERENCE,
    InstallationInputError,
    InstallationValidationIssue,
    ResolvedPhysicalCable,
    active_cross_section,
    cross_section_for_region,
    generate_standard_cross_section,
    installation_summary,
    phase_angle_deg,
    resolved_physical_cables,
    validate_installation_design,
)

__all__ += [
    "INSTALLATION_REFERENCE",
    "InstallationInputError",
    "InstallationValidationIssue",
    "ResolvedPhysicalCable",
    "active_cross_section",
    "cross_section_for_region",
    "generate_standard_cross_section",
    "installation_summary",
    "phase_angle_deg",
    "resolved_physical_cables",
    "validate_installation_design",
]

from ucd.calculations.calculation_policy import (
    REFERENCE as CALCULATION_POLICY_REFERENCE,
    PARAMETER_SPECIFICATIONS,
    CalculationPolicyAudit,
    CalculationPolicyIssue,
    ParameterSpecification,
    audit_calculation_policy,
    bootstrap_calculation_policy,
    find_parameter_record,
    register_parameter_provenance,
    register_physical_calculation,
    render_calculation_policy_audit,
)

__all__ += [
    "CALCULATION_POLICY_REFERENCE",
    "PARAMETER_SPECIFICATIONS",
    "CalculationPolicyAudit",
    "CalculationPolicyIssue",
    "ParameterSpecification",
    "audit_calculation_policy",
    "bootstrap_calculation_policy",
    "find_parameter_record",
    "register_parameter_provenance",
    "register_physical_calculation",
    "render_calculation_policy_audit",
]

from ucd.calculations.cable_physical_parameters import (
    REFERENCE as PHYSICAL_PARAMETER_REFERENCE,
    ConstructionCoefficientResult,
    PhysicalCableParameterResult,
    PhysicalParameterInputError,
    PhysicalParameterIssue,
    dielectric_loss_w_m,
    geometric_capacitance_uf_km,
    geometry_dc_resistance_20_ohm_km,
    material_alpha_20_per_c,
    material_resistivity_20_ohm_m,
    proximity_effect_factor,
    render_physical_parameter_result,
    resolve_construction_coefficients,
    run_project_physical_parameter_study,
    skin_effect_factor,
    solve_cable_physical_parameters,
)

__all__ += [
    "PHYSICAL_PARAMETER_REFERENCE",
    "ConstructionCoefficientResult",
    "PhysicalCableParameterResult",
    "PhysicalParameterInputError",
    "PhysicalParameterIssue",
    "dielectric_loss_w_m",
    "geometric_capacitance_uf_km",
    "geometry_dc_resistance_20_ohm_km",
    "material_alpha_20_per_c",
    "material_resistivity_20_ohm_m",
    "proximity_effect_factor",
    "render_physical_parameter_result",
    "resolve_construction_coefficients",
    "run_project_physical_parameter_study",
    "skin_effect_factor",
    "solve_cable_physical_parameters",
]

from ucd.calculations.multiconductor_em import (
    REFERENCE as MULTICONDUCTOR_EM_REFERENCE,
    MODE_SHADOW_COMPARE,
    SHEATH_OPEN,
    SHEATH_SOLID_BOTH_END,
    MulticonductorEMInputError,
    MulticonductorEMIssue,
    MulticonductorMethodResult,
    MulticonductorCableResult,
    MulticonductorGroupResult,
    MulticonductorEMResult,
    solve_multiconductor_em,
    render_multiconductor_em,
)

__all__ += [
    "MULTICONDUCTOR_EM_REFERENCE",
    "MODE_SHADOW_COMPARE",
    "SHEATH_OPEN",
    "SHEATH_SOLID_BOTH_END",
    "MulticonductorEMInputError",
    "MulticonductorEMIssue",
    "MulticonductorMethodResult",
    "MulticonductorCableResult",
    "MulticonductorGroupResult",
    "MulticonductorEMResult",
    "solve_multiconductor_em",
    "render_multiconductor_em",
]

from ucd.calculations.multiconductor_bonding_network import (
    REFERENCE as MULTICONDUCTOR_BONDING_REFERENCE,
    CORE_SHARING_MODE,
    MulticonductorBondingInputError,
    MulticonductorBondingIssue,
    MulticonductorBondingMethodResult,
    MulticonductorSheathResult,
    MulticonductorBondingSectionResult,
    MulticonductorBondingBranchResult,
    MulticonductorBondingMatrixBlock,
    MulticonductorBondingNetworkResult,
    solve_multiconductor_bonding_network,
    render_multiconductor_bonding_network,
)

__all__ += [
    "MULTICONDUCTOR_BONDING_REFERENCE",
    "CORE_SHARING_MODE",
    "MulticonductorBondingInputError",
    "MulticonductorBondingIssue",
    "MulticonductorBondingMethodResult",
    "MulticonductorSheathResult",
    "MulticonductorBondingSectionResult",
    "MulticonductorBondingBranchResult",
    "MulticonductorBondingMatrixBlock",
    "MulticonductorBondingNetworkResult",
    "solve_multiconductor_bonding_network",
    "render_multiconductor_bonding_network",
]

from ucd.calculations.multiconductor_thermal import (
    COUPLING_MODE as MULTICONDUCTOR_THERMAL_COUPLING_MODE,
    MODE as MULTICONDUCTOR_THERMAL_MODE,
    MulticonductorThermalCableResult,
    MulticonductorThermalInputError,
    MulticonductorThermalIssue,
    MulticonductorThermalRegionResult,
    MulticonductorThermalResult,
    render_multiconductor_thermal,
    solve_multiconductor_thermal,
)

__all__ += [
    "MULTICONDUCTOR_THERMAL_COUPLING_MODE",
    "MULTICONDUCTOR_THERMAL_MODE",
    "MulticonductorThermalCableResult",
    "MulticonductorThermalInputError",
    "MulticonductorThermalIssue",
    "MulticonductorThermalRegionResult",
    "MulticonductorThermalResult",
    "render_multiconductor_thermal",
    "solve_multiconductor_thermal",
]

from ucd.calculations.shadow_validation import (
    MODE as SHADOW_VALIDATION_MODE,
    PROMOTION_HOLD,
    PROMOTION_PILOT,
    PROMOTION_READY,
    PROMOTION_TARGET,
    BenchmarkCaseResult,
    ExternalBenchmarkEvidence,
    ShadowComparisonMetric,
    ShadowValidationInputError,
    ShadowValidationResult,
    ShadowValidationToleranceProfile,
    ValidationGateResult,
    render_shadow_validation,
    run_shadow_validation,
)

__all__ += [
    "SHADOW_VALIDATION_MODE",
    "PROMOTION_HOLD",
    "PROMOTION_PILOT",
    "PROMOTION_READY",
    "PROMOTION_TARGET",
    "BenchmarkCaseResult",
    "ExternalBenchmarkEvidence",
    "ShadowComparisonMetric",
    "ShadowValidationInputError",
    "ShadowValidationResult",
    "ShadowValidationToleranceProfile",
    "ValidationGateResult",
    "render_shadow_validation",
    "run_shadow_validation",
]

from ucd.calculations.thermal_material_library import (
    LIBRARY_REVISION as THERMAL_MATERIAL_LIBRARY_REVISION,
    REFERENCE_SCOPE as THERMAL_MATERIAL_REFERENCE_SCOPE,
    ThermalMaterialLibraryIssue,
    built_in_reference_materials,
    merge_reference_materials,
    validate_material_for_final_design,
)

__all__ += [
    "THERMAL_MATERIAL_LIBRARY_REVISION",
    "THERMAL_MATERIAL_REFERENCE_SCOPE",
    "ThermalMaterialLibraryIssue",
    "built_in_reference_materials",
    "merge_reference_materials",
    "validate_material_for_final_design",
]

from ucd.calculations.project_geometry_runtime import (
    materialize_project_route_sections,
    resolve_project_bonding_route_sections,
    solve_project_bonding,
)
from ucd.calculations.installation_coupling import (
    PhaseGroupGeometry,
    ResolvedRegionGeometry,
    ResolvedInstallationGeometry,
    resolve_installation_geometry,
    attach_resolved_geometry_to_route_sections,
)

__all__ += [
    "materialize_project_route_sections",
    "resolve_project_bonding_route_sections",
    "solve_project_bonding",
    "PhaseGroupGeometry",
    "ResolvedRegionGeometry",
    "ResolvedInstallationGeometry",
    "resolve_installation_geometry",
    "attach_resolved_geometry_to_route_sections",
]


from ucd.calculations.thermal_method_validation import (
    ENGINE_ID as THERMAL_METHOD_VALIDATION_ENGINE_ID,
    ANALYTIC_ENGINE_VERSION,
    NODAL_ENGINE_VERSION,
    BASIS_ANALYTIC_PREVIEW,
    BASIS_ANALYTIC_VALIDATED,
    BASIS_ANALYTIC_CONSERVATIVE,
    BASIS_NODAL_REQUIRED,
    BASIS_NODAL_BINDING,
    BASIS_HYBRID_BINDING,
    BASIS_MANUAL_SOURCE,
    BASIS_METHOD_DISAGREEMENT,
    BASIS_NODAL_NOT_CONVERGED,
    BASIS_NODAL_QUALITY_PENDING,
    ThermalMethodToleranceProfile,
    NodalQualityEvidence,
    ThermalMethodRegionComparison,
    ThermalMethodScenarioAuthority,
    ThermalMethodAuthorityResult,
    evaluate_thermal_method_authority,
    cache_thermal_method_authority,
    cached_thermal_method_authority,
)

__all__ += [
    "THERMAL_METHOD_VALIDATION_ENGINE_ID",
    "ANALYTIC_ENGINE_VERSION",
    "NODAL_ENGINE_VERSION",
    "BASIS_ANALYTIC_PREVIEW",
    "BASIS_ANALYTIC_VALIDATED",
    "BASIS_ANALYTIC_CONSERVATIVE",
    "BASIS_NODAL_REQUIRED",
    "BASIS_NODAL_BINDING",
    "BASIS_HYBRID_BINDING",
    "BASIS_MANUAL_SOURCE",
    "BASIS_METHOD_DISAGREEMENT",
    "BASIS_NODAL_NOT_CONVERGED",
    "BASIS_NODAL_QUALITY_PENDING",
    "ThermalMethodToleranceProfile",
    "NodalQualityEvidence",
    "ThermalMethodRegionComparison",
    "ThermalMethodScenarioAuthority",
    "ThermalMethodAuthorityResult",
    "evaluate_thermal_method_authority",
    "cache_thermal_method_authority",
    "cached_thermal_method_authority",
]

from ucd.calculations.bonding_accessories import (
    BondingAccessoryItem,
    BondingAccessoryPlan,
    BondingAccessoryInputError,
    resolve_bonding_accessory_plan,
)

__all__ += [
    "BondingAccessoryItem",
    "BondingAccessoryPlan",
    "BondingAccessoryInputError",
    "resolve_bonding_accessory_plan",
]

from ucd.calculations.operating_scenarios import (
    CircuitOperatingState,
    PhysicalCableOperatingPoint,
    ResolvedOperatingScenario,
    OperatingScenarioInputError,
    resolve_operating_scenarios,
    apply_operating_scenario,
)
from ucd.calculations.production_electrothermal import (
    ProductionElectroThermalInputError,
    ProductionCableOperatingResult,
    ProductionRegionOperatingResult,
    ProductionScenarioResult,
    ProductionElectroThermalStudyResult,
    ProductionThermalMethodComparison,
    ProductionAmpacityEvaluation,
    ProductionAmpacityResult,
    solve_production_operating_scenario,
    solve_production_electrothermal_study,
    validate_production_thermal_methods,
    solve_production_coupled_ampacity,
)

__all__ += [
    "CircuitOperatingState",
    "PhysicalCableOperatingPoint",
    "ResolvedOperatingScenario",
    "OperatingScenarioInputError",
    "resolve_operating_scenarios",
    "apply_operating_scenario",
    "ProductionElectroThermalInputError",
    "ProductionCableOperatingResult",
    "ProductionRegionOperatingResult",
    "ProductionScenarioResult",
    "ProductionElectroThermalStudyResult",
    "ProductionThermalMethodComparison",
    "ProductionAmpacityEvaluation",
    "ProductionAmpacityResult",
    "solve_production_operating_scenario",
    "solve_production_electrothermal_study",
    "validate_production_thermal_methods",
    "solve_production_coupled_ampacity",
]

from ucd.calculations.production_bonding import (
    ProductionBondingScenarioResult,
    ProductionBondingStudyResult,
    project_production_bonding_study,
    solve_production_bonding_study,
)

from ucd.calculations.model_applicability import (
    SUPPORTED as MODEL_SCOPE_SUPPORTED,
    REFERENCE_ONLY as MODEL_SCOPE_REFERENCE_ONLY,
    BLOCKED as MODEL_SCOPE_BLOCKED,
    CableModelApplicability,
    evaluate_cable_model_applicability,
    require_production_physics,
)

__all__ += [
    "MODEL_SCOPE_SUPPORTED", "MODEL_SCOPE_REFERENCE_ONLY", "MODEL_SCOPE_BLOCKED",
    "CableModelApplicability", "evaluate_cable_model_applicability", "require_production_physics",
]

from .application_orchestration import (
    BondingProductionRun,
    ThermalPreprocessorRun,
    run_bonding_production,
    run_thermal_preprocessor as run_application_thermal_preprocessor,
)
