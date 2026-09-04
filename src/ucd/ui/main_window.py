from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QCursor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ucd.cad import read_dxf_geometry
from ucd.calculations.installation_coupling import (
    PRODUCTION_GEOMETRY_ENGINE_IDS,
    cross_section_for_region,
    physical_positions_for_region,
    synchronize_installation_geometry,
)
from ucd.calculations.project_geometry_runtime import (
    materialize_project_route_sections,
    resolve_project_bonding_route_sections,
    solve_project_bonding,
)
from ucd.calculations import (
    BondingInputError,
    validate_cable,
    create_project_snapshot,
    BondingResult,
    FirstDesignInputError,
    apply_candidate_to_project,
    apply_load_calculation,
    generate_generic_candidates,
    CalculationInputError,
    FaultStudyError,
    FaultStudyResult,
    Iec60287SectionResult,
    SectionThermalResult,
    SvlInputError,
    SvlSelectionResult,
    ThermalInputError,
    ThermalRouteInputError,
    ThermalRouteStudyResult,
    NodalThermalInputError,
    NodalRouteStudyResult,
    TransientRouteStudyResult,
    TransientThermalInputError,
    check_mesh_convergence,
    find_nodal_region_result,
    review_order,
    materialize_route_sections,
    materialize_route_sections_partial,
    solve_thermal_route,
    solve_production_electrothermal_study,
    ProductionElectroThermalStudyResult,
    solve_production_bonding_study,
    ProductionBondingStudyResult,
    solve_nodal_route,
    solve_transient_route,
    validate_thermal_design,
    optimize_cross_bonding,
    solve_bonding,
    solve_fault_study,
    transfer_fault_tov_to_svl,
    solve_project,
    solve_project_thermal,
    solve_section_thermal,
    solve_svl_selection,
    audit_project_sources,
    render_source_audit,
    evaluate_application_iteration_gates,
    evaluate_project_workflow,
    workflow_stage_specs,
    mark_engine_runs_stale,
    record_engine_run,
    ProjectWorkflowEvaluation,
    STATUS_BLOCKED,
    STATUS_COMPLETE,
    STATUS_CONDITIONAL,
    STATUS_MISSING_DATA,
    STATUS_NOT_STARTED,
    STATUS_PRELIMINARY,
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_STALE,
    CalculationResultsBundle,
    EnginePrecheckResult,
    PRECHECK_CONDITIONAL,
    evaluate_engine_precheck,
    load_application_cable_database,
    save_application_cable_database,
    installation_summary,
    validate_installation_design,
    bootstrap_calculation_policy,
    register_physical_calculation,
    run_bonding_production,
    run_application_thermal_preprocessor,
    OperatingScenarioInputError,
)
from ucd.calculations.iec60287 import SUITABILITY_SUITABLE
from ucd.calculations.result_status import is_suitable
from ucd.calculations.thermal_method_validation import (
    BASIS_METHOD_DISAGREEMENT,
    VALIDATION_FAIL as METHOD_VALIDATION_FAIL,
    cache_thermal_method_authority,
    evaluate_thermal_method_authority,
)

from ucd.models.project import (
    BONDING_CROSS,
    BONDING_SINGLE_POINT,
    BONDING_SOLID_BOTH_END,
    EXTERNAL_THERMAL_AUTO,
    EXTERNAL_THERMAL_MIXED,
    EXTERNAL_THERMAL_MANUAL,
    INTERNAL_THERMAL_AUTO,
    INTERNAL_THERMAL_MANUAL,
    BondingConnection,
    BondingLinkBox,
    BondingMinorSection,
    BondingNode,
    FAULT_PHASE_PHASE,
    FAULT_SINGLE_PHASE_GROUND,
    FAULT_THREE_PHASE,
    FaultScenario,
    MATURITY_LEVEL_1,
    MATURITY_LEVEL_2,
    MATURITY_LEVEL_3,
    MATURITY_LEVEL_4,
    MATURITY_LEVEL_5,
    ProjectData,
    RouteSection,
    RouteCableAssignment,
    ThermalCrossSectionTemplate,
    ThermalMaterialData,
    ThermalRegion,
    LoadProfilePoint,
    TransientLoadProfile,
    SvlCandidate,
    default_bonding_system,
)
from ucd.ui.cable_library_widget import CableLibraryWidget
from ucd.ui.project_cable_selection_dialog import ProjectCableSelectionDialog
from ucd.ui.route_section_dialog import RouteSectionDialog
from ucd.ui.window_layout import (
    fit_window,
    clamp_to_screen,
    DENSITY_COMPACT,
    DENSITY_NORMAL,
    DENSITY_WIDE,
    DENSITY_FULL,
)
from ucd.ui.stage_host import StageHostFrame
from ucd.ui.standard_defaults_dialog import (
    StandardDefaultsDialog,
    defaults_path,
    load_standard_defaults,
)
from ucd.ui.workflow_user_state import user_stage_state
from ucd.ui.catalog_comparison_dialog import CatalogComparisonDialog
from ucd.calculations.load_cycle import load_cycle_metrics
from ucd.ui.graphics_views import CrossSectionView, PlanView, SimpleDiagramView, TransientThermalView
from ucd.ui.project_wizard import NewDesignWizard, StartDialog, geometry_total_length
from ucd.ui.report_builder_dialog import ReportBuilderDialog
from ucd.ui.procurement_dialog import ProcurementDialog
from ucd.ui.engine_precheck_dialog import EnginePrecheckDialog
from ucd.ui.thermal_detail_dialog import ThermalAnalysisDialog
from ucd.ui.installation_designer_dialog import InstallationDesignerDialog
from ucd.ui.parameter_provenance_dialog import ParameterProvenanceDialog
from ucd.ui.cable_physical_parameters_dialog import CablePhysicalParametersDialog
from ucd.ui.multiconductor_em_dialog import MulticonductorEMDialog
from ucd.ui.multiconductor_thermal_dialog import MulticonductorThermalDialog
from ucd.ui.electrothermal_coupled_dialog import ElectroThermalCoupledDialog
from ucd.ui.shadow_validation_dialog import ShadowValidationDialog
from ucd.ui.thermal_material_library_dialog import ThermalMaterialLibraryDialog
from ucd.ui.workflow_widgets import (
    ProjectIdentityHeader, WorkflowGuideWidget, WorkflowStageBar, STATUS_COLORS,
    STATUS_COMPLETE, STATUS_NOT_STARTED,
    INPUT_READINESS_TR, RUN_STATUS_TR, FRESHNESS_TR, MATURITY_TR, status_text,
)

APP_VERSION = "0.16.9.4.38"


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        fit_window(self, DENSITY_FULL)
        self.settings = QSettings("DiTuS Engineering", "DiTuS Kablo Analizör")
        # İsteğe bağlı kurum/kullanıcı ön tanım profili. Hesap motorlarının
        # standart denklem sabitleri ve desteklenen konstrüksiyon katsayıları
        # kendi resolver'larında yaşar; eksik profil alanı motoru bloklamaz.
        self.standard_defaults_path = defaults_path(
            Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or self.project_root)
        )
        self.standard_defaults = load_standard_defaults(self.standard_defaults_path)

        self.project = ProjectData()
        bootstrap_calculation_policy(self.project)
        app_data_location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        app_data_root = Path(app_data_location) if app_data_location else project_root / "user_data"
        self.application_database_path = app_data_root / "cable_database.ditus-cable-catalog.json"
        self.database_project = ProjectData(project_name="DiTuS Uygulama Veri Tabanı")
        self.database_project.cable_library = load_application_cable_database(self.application_database_path)
        self.current_file: Path | None = None
        self.dirty = False
        self.iec_results: list[Iec60287SectionResult] = []
        self.thermal_results: list[SectionThermalResult] = []
        self.thermal_route_result: ThermalRouteStudyResult | None = None
        self.production_electrothermal_result: ProductionElectroThermalStudyResult | None = None
        self.production_bonding_result: ProductionBondingStudyResult | None = None
        self.nodal_thermal_result: NodalRouteStudyResult | None = None
        self.transient_thermal_result: TransientRouteStudyResult | None = None
        self.bonding_result: BondingResult | None = None
        self.svl_result: SvlSelectionResult | None = None
        self.fault_result: FaultStudyResult | None = None
        self.last_bonding_design = None
        self.last_mesh_convergence: dict[tuple[str, str, str], object] = {}
        self.current_nodal_review_key: tuple[str, str, str] | None = None
        self.bonding_focus_mode = False
        self.workflow_evaluation: ProjectWorkflowEvaluation | None = None
        self._suppress_engine_precheck = False
        self._active_engine_prechecks: dict[str, EnginePrecheckResult] = {}
        self.setWindowTitle(f"DiTuS Kablo Analizör™ v{APP_VERSION}")
        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_ui()
        self._refresh_all()

    def _build_actions(self) -> None:
        self.act_new = QAction("Yeni Kablo Sistemi Tasarla", self, triggered=self.run_project_wizard)
        self.act_quick_new = QAction("Mevcut Tasarımı Kontrol Et", self, triggered=self.new_project)
        self.act_open = QAction("Projeyi Aç…", self, triggered=self.open_project)
        self.act_open_sample_case = QAction(
            "Sentetik 20 km Örnek Hattı…", self, triggered=self.open_bundled_synthetic_case
        )
        self.act_save = QAction("Kaydet", self, triggered=self.save_project)
        self.act_save_as = QAction("Farklı Kaydet…", self, triggered=self.save_project_as)
        self.act_import_dxf = QAction("DXF İçe Aktar…", self, triggered=self.import_dxf)
        self.act_validate = QAction("Ön Doğrulama", self, triggered=self.validate_project)
        self.act_source_audit = QAction(
            "Kaynak Veri Tutarlılık Denetimi", self, triggered=self.show_source_audit
        )
        self.act_calculation_policy = QAction(
            "Hesap Parametreleri ve Kaynakları…", self, triggered=self.show_calculation_policy
        )
        self.act_physical_parameters = QAction(
            "Kablo Fiziksel Parametreleri…", self, triggered=self.show_physical_parameters
        )
        self.act_application_gates = QAction(
            "Kablo Uygulama / İterasyon Kapıları", self, triggered=self.show_cable_application_gates
        )
        self.act_installation_designer = QAction(
            "Kablo-Kanal Düzeni…", self, triggered=self.show_installation_designer
        )
        self.act_thermal = QAction("Termal Direnç Ön İşlemi", self, triggered=self.run_thermal_preprocessor)
        self.act_thermal_route = QAction("Termal Güzergâh Analizi", self, triggered=self.run_thermal_route_analysis)
        self.act_nodal_thermal = QAction("2D Nodal Termal Çözüm", self, triggered=self.run_nodal_thermal_analysis)
        self.act_transient_thermal = QAction("IEC 60853 Geçici / Çevrimsel", self, triggered=self.run_transient_thermal_analysis)
        self.act_iec60287 = QAction("IEC 60287 Sürekli Durum", self, triggered=self.run_iec60287)
        self.act_bonding = QAction("IEEE/CIGRE Bonding Ön Çözümü", self, triggered=self.run_bonding_solver)
        self.act_multiconductor_em = QAction(
            "Genel N-İletken EM Gölge Çözümü / Bonding Ağı…", self, triggered=self.show_multiconductor_em
        )
        self.act_multiconductor_thermal = QAction(
            "Gerçek x-y Çoklu Kablo Termal Gölge Çözümü…", self, triggered=self.show_multiconductor_thermal
        )
        self.act_electrothermal_coupled = QAction(
            "Elektro-Termal Kapalı Çevrim Gölge Çözümü…", self, triggered=self.show_electrothermal_coupled
        )
        self.act_shadow_validation = QAction(
            "Fiziksel Motor Doğrulama ve Shadow Karşılaştırma…", self, triggered=self.show_shadow_validation
        )
        self.act_fault = QAction("Arıza / EPR / TOV", self, triggered=self.run_fault_study)
        self.act_svl = QAction("SVL Boyutlandırma ve Seçim", self, triggered=self.run_svl_selection)
        self.act_first_iteration = QAction("İlk Tasarım İterasyonu", self, triggered=self.run_first_design_iteration)
        self.act_project_cable_select = QAction("Proje Kablosu Seç…", self, triggered=self.show_project_cable_selection)
        self.act_assigned_cable = QAction("Atanmış Kabloyu Görüntüle", self, triggered=self.show_assigned_cable)
        self.act_change_cable = QAction("Atanmış Kabloyu Değiştir…", self, triggered=self.show_project_cable_selection)
        self.act_complete_cable = QAction("Kablo Veri Eksiklerini Tamamla", self, triggered=self.show_assigned_cable)
        self.act_cable_library = QAction("Kablolar", self, triggered=self.show_cable_library)
        self.act_db_thermal_materials = QAction(
            "Termal Malzemeler", self, triggered=self.show_thermal_material_library
        )
        self.act_db_joint = QAction("Joint / Termination", self, triggered=lambda: self._show_database_placeholder("Joint / Termination"))
        self.act_db_linkbox = QAction("Link Box", self, triggered=lambda: self._show_database_placeholder("Link Box"))
        self.act_db_svl = QAction("SVL", self, triggered=lambda: self._show_database_placeholder("SVL"))
        self.act_db_bonding = QAction("Bonding / Topraklama Bileşenleri", self, triggered=lambda: self._show_database_placeholder("Bonding / Topraklama Bileşenleri"))
        self.act_catalog_comparison = QAction("Kablo Karşılaştırması", self, triggered=self.show_catalog_comparison)
        self.act_report_builder = QAction("Rapor Oluşturucu…", self, triggered=self.show_report_builder)
        self.act_procurement = QAction("BOQ / BOM / RFQ Oluşturucu…", self, triggered=self.show_procurement_builder)
        self.act_toggle_properties = QAction("Aşama Rehberi / Nesne Bilgileri…", self, triggered=self._toggle_property_panel)
        self.act_toggle_properties.setCheckable(True)
        self.act_toggle_properties.setChecked(False)
        self.act_results = QAction("Sonuçlar ve Kayıtlar…", self, triggered=self.show_results_dialog)
        self.act_workflow_guide = QAction("Aşama Rehberi…", self, triggered=self.show_workflow_guide)
        self.act_next_stage = QAction("Önerilen Sonraki Adım", self, triggered=self._open_recommended_workflow_stage)
        self.act_yesilcam = QAction("Yeşilçam esprileri", self)
        self.act_yesilcam.setCheckable(True)
        self.act_standard_defaults = QAction(
            "Standart Katsayıları ve Varsayılanlar…", self, triggered=self.show_standard_defaults
        )
        self.act_standard_defaults.setToolTip(
            "İsteğe bağlı kurum/kullanıcı başlangıç profili. Standart denklem sabitleri ve desteklenen "
            "konstrüksiyon katsayıları kod resolver'larında otomatik uygulanır; aktif proje verisi önceliklidir."
        )
        self.act_yesilcam.setChecked(self.settings.value("yesilcam_esprileri", True, bool))
        self.act_yesilcam.toggled.connect(lambda checked: self.settings.setValue("yesilcam_esprileri", checked))
        self.act_calc = QAction("Birleşik Hesap", self, triggered=self.run_combined_calculation)
        self.act_exit = QAction("Çıkış", self, triggered=self.close)
        self.act_about = QAction("Hakkında", self, triggered=self.show_about)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Dosya")
        for action in (self.act_new, self.act_quick_new, self.act_open, self.act_save, self.act_save_as):
            file_menu.addAction(action)
        file_menu.addAction(self.act_open_sample_case)
        file_menu.addSeparator()
        file_menu.addAction(self.act_import_dxf)
        file_menu.addSeparator()
        file_menu.addAction(self.act_exit)

        project_menu = self.menuBar().addMenu("Proje")
        project_menu.addAction(self.act_validate)
        project_menu.addAction(self.act_source_audit)
        project_menu.addAction(self.act_calculation_policy)
        project_menu.addAction(self.act_application_gates)
        project_menu.addAction(self.act_workflow_guide)
        project_menu.addAction(self.act_next_stage)
        project_menu.addAction(self.act_first_iteration)
        project_menu.addSeparator()
        project_menu.addAction(self.act_installation_designer)
        cable_menu = self.menuBar().addMenu("Kablo")
        cable_menu.addAction(self.act_project_cable_select)
        cable_menu.addAction(self.act_assigned_cable)
        cable_menu.addAction(self.act_change_cable)
        cable_menu.addAction(self.act_complete_cable)
        cable_menu.addSeparator()
        cable_menu.addAction(self.act_physical_parameters)
        cable_menu.addAction(self.act_catalog_comparison)

        database_menu = self.menuBar().addMenu("Veri Tabanları")
        database_menu.addAction(self.act_cable_library)
        database_menu.addAction(self.act_db_thermal_materials)
        database_menu.addAction(self.act_db_joint)
        database_menu.addAction(self.act_db_linkbox)
        database_menu.addAction(self.act_db_svl)
        database_menu.addAction(self.act_db_bonding)

        report_menu = self.menuBar().addMenu("Rapor")
        report_menu.addAction(self.act_report_builder)
        report_menu.addAction(self.act_procurement)
        settings_menu = self.menuBar().addMenu("Ayarlar")
        settings_menu.addAction(self.act_standard_defaults)
        settings_menu.addSeparator()
        settings_menu.addAction(self.act_toggle_properties)
        settings_menu.addAction(self.act_yesilcam)
        calc_menu = self.menuBar().addMenu("Hesap")
        calc_menu.addAction(self.act_thermal)
        calc_menu.addAction(self.act_thermal_route)
        calc_menu.addAction(self.act_nodal_thermal)
        calc_menu.addAction(self.act_transient_thermal)
        calc_menu.addAction(self.act_iec60287)
        calc_menu.addAction(self.act_bonding)
        calc_menu.addAction(self.act_multiconductor_em)
        calc_menu.addAction(self.act_multiconductor_thermal)
        calc_menu.addAction(self.act_electrothermal_coupled)
        calc_menu.addAction(self.act_shadow_validation)
        calc_menu.addAction(self.act_fault)
        calc_menu.addAction(self.act_svl)
        calc_menu.addSeparator()
        calc_menu.addAction(self.act_results)
        calc_menu.addSeparator()
        calc_menu.addAction(self.act_calc)
        self.menuBar().addMenu("Yardım").addAction(self.act_about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Hızlı İşlemler")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)
        for action in (
            self.act_new,
            self.act_open,
            self.act_save,
            self.act_project_cable_select,
            self.act_first_iteration,
            self.act_report_builder,
        ):
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.act_next_stage)

    def _build_ui(self) -> None:
        # Ana pencere yalnız proje ağacı + tam güzergâh/Plan-CAD tuvalidir.
        # Ayrıntılı editörler, aşama rehberi/nesne bilgileri ve sonuçlar ayrı
        # modeless pencerelerde tutulur; böylece ana çalışma alanı boğulmaz.
        self.upper_splitter = QSplitter(Qt.Horizontal)
        self.root_splitter = self.upper_splitter  # geriye dönük iç API uyumluluğu

        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderLabel("PROJE AĞACI")
        self.project_tree.setMinimumWidth(245)
        self.project_tree.setMaximumWidth(390)
        self.project_tree.setMouseTracking(True)
        # Tek tıklama yalnız seçim yapar. Editör/sonuç pencereleri kullanıcı
        # niyetini açık eden çift tıklama ile açılır.
        self.project_tree.setExpandsOnDoubleClick(False)
        self.project_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.project_tree.itemDoubleClicked.connect(self._tree_item_double_clicked)

        # Ana tuval: sadece güzergâh / CAD.
        self.plan_view = PlanView()
        self.upper_splitter.addWidget(self.project_tree)
        self.upper_splitter.addWidget(self.plan_view)
        self.upper_splitter.setStretchFactor(0, 0)
        self.upper_splitter.setStretchFactor(1, 1)
        self.upper_splitter.setSizes([270, 1280])

        # Ayrıntılı modül/editör penceresi. Mevcut modül widget'ları korunur,
        # fakat ana pencerenin merkezine sıkıştırılmaz.
        self.workspace_tabs = QTabWidget()
        self.first_design_widget = self._build_first_design_widget()
        self.cable_library_widget = CableLibraryWidget(self.project, self._on_project_cable_changed, database_mode=False)
        self.profile_view = SimpleDiagramView("profile")
        self.cross_section_view = CrossSectionView(self._cross_section_changed)
        self.route_table_widget = self._build_route_table_widget()
        self.thermal_route_widget = self._build_thermal_route_widget()
        self.bonding_view = SimpleDiagramView("bonding")
        self.bonding_table_widget = self._build_bonding_table_widget()
        self.fault_table_widget = self._build_fault_table_widget()
        self.svl_table_widget = self._build_svl_table_widget()
        self.thermal_review_widget = self._build_thermal_review_widget()
        self.transient_widget = self._build_transient_widget()
        for widget, title in (
            (self.first_design_widget, "İlk Tasarım"),
            (self.cable_library_widget, "Atanmış Kablo"),
            (self.profile_view, "Boyuna Profil"),
            (self.cross_section_view, "Enine Kesit"),
            (self.route_table_widget, "Güzergâh Bölümleri"),
            (self.thermal_route_widget, "Termal Güzergâh"),
            (self.bonding_table_widget, "Bonding Ağı"),
            (self.fault_table_widget, "Arıza / EPR"),
            (self.svl_table_widget, "SVL Koordinasyonu"),
            (self.thermal_review_widget, "Termal Alan"),
            (self.transient_widget, "Geçici Termal"),
        ):
            self.workspace_tabs.addTab(widget, title)
        self.workspace_tabs.tabBar().hide()
        self.module_dialog = QDialog(self)
        self.module_dialog.setModal(False)
        self.module_dialog.setWindowFlags(self.module_dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.module_dialog.setWindowTitle("DiTuS — Proje Modülü")
        module_layout = QVBoxLayout(self.module_dialog)
        module_layout.setContentsMargins(5, 5, 5, 5)
        # Aşama konağı: içerik değişse de akış çerçevesi (neredeyim / ne eksik /
        # sıradaki adım) sabit kalır.  Aşamalar artık ard arda bağımsız pencere
        # açmaz; aynı konağın gövdesine yerleşir.
        self.stage_host = StageHostFrame()
        self.module_title = self.stage_host.stage_title
        self.stage_host.set_body(self.workspace_tabs)
        self.stage_host.previousRequested.connect(self._go_previous_stage)
        self.stage_host.nextRequested.connect(self._go_next_stage)
        self.stage_host.recommendedRequested.connect(self._open_recommended_workflow_stage)
        self.stage_host.exitFlowRequested.connect(self.module_dialog.hide)
        module_layout.addWidget(self.stage_host, 1)

        # Uygulama genelindeki veri tabanı proje modülünden ayrıdır.
        self.database_cable_widget = CableLibraryWidget(
            self.database_project, self._on_database_changed, database_mode=True
        )
        self.database_dialog = QDialog(self)
        self.database_dialog.setModal(False)
        self.database_dialog.setWindowFlags(self.database_dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.database_dialog.setWindowTitle("DiTuS — Veri Tabanları / Kablolar")
        database_layout = QVBoxLayout(self.database_dialog)
        database_layout.setContentsMargins(5, 5, 5, 5)
        database_layout.addWidget(self.database_cable_widget)

        # Aşama rehberi ve nesne özellikleri ana pencerenin sağında değil,
        # istenildiğinde açılan bağımsız bilgi penceresindedir.
        self.prop_container = QWidget()
        prop_layout = QVBoxLayout(self.prop_container)
        prop_layout.setContentsMargins(2, 2, 2, 2)
        self.context_tabs = QTabWidget()
        self.workflow_guide = WorkflowGuideWidget()
        self.workflow_guide.openStageRequested.connect(self._activate_workflow_stage)
        self.workflow_guide.openRecommendedRequested.connect(self._activate_workflow_stage)
        self.context_tabs.addTab(self.workflow_guide, "Aşama Rehberi")
        property_page = QWidget()
        property_layout = QVBoxLayout(property_page)
        property_layout.setContentsMargins(4, 4, 4, 4)
        title = QLabel("SEÇİLİ NESNE ÖZELLİKLERİ")
        title.setStyleSheet("font-weight:700; padding:5px;")
        self.property_table = QTableWidget(0, 3)
        self.property_table.setHorizontalHeaderLabels(["Parametre", "Değer", "Kaynak"])
        self.property_table.horizontalHeader().setStretchLastSection(True)
        self.property_table.setAlternatingRowColors(True)
        self.property_table.itemChanged.connect(self._property_changed)
        property_layout.addWidget(title)
        property_layout.addWidget(self.property_table)
        self.context_tabs.addTab(property_page, "Nesne")
        prop_layout.addWidget(self.context_tabs)
        self.context_dialog = QDialog(self)
        self.context_dialog.setModal(False)
        self.context_dialog.setWindowFlags(self.context_dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.context_dialog.setWindowTitle("DiTuS — Aşama Rehberi ve Nesne Bilgileri")
        context_layout = QVBoxLayout(self.context_dialog)
        context_layout.setContentsMargins(5, 5, 5, 5)
        context_layout.addWidget(self.prop_container)
        self.context_dialog.finished.connect(lambda _=0: self.act_toggle_properties.setChecked(False))

        # Sonuç merkezi ayrı penceredir; ana ekranın altında sabit sonuç bloğu yoktur.
        self.bottom_tabs = QTabWidget()
        self.summary_table = QTableWidget(0, 3)
        self.summary_table.setHorizontalHeaderLabels(["Gösterge", "Durum / Değer", "Açıklama"])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_result_table = QTableWidget(0, 8)
        self.thermal_result_table.setHorizontalHeaderLabels(
            ["Bölüm", "T1", "T2", "T3", "T4 etkin", "Faz T4", "İç kaynak", "Dış kaynak"]
        )
        self.thermal_result_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_result_table.setAlternatingRowColors(True)
        self.thermal_route_result_table = QTableWidget(0, 15)
        self.thermal_route_result_table.setHorizontalHeaderLabels([
            "Senaryo", "Bölge", "Başlangıç", "Bitiş", "Kurulum", "Veri", "ρ zemin", "ρ dolgu",
            "λ1", "T4", "Ampacity [A]", "Marj [A]", "T @ yük [°C]", "Durum", "Öneri / uyarı"
        ])
        self.thermal_route_result_table.setAlternatingRowColors(True)
        self.thermal_route_result_table.horizontalHeader().setStretchLastSection(True)
        self.nodal_result_table = QTableWidget(0, 10)
        self.nodal_result_table.setHorizontalHeaderLabels([
            "Senaryo", "Termal kapsam", "Bölge", "Chainage", "Kurulum", "I yük [A]",
            "Iamp 2D [A]", "Marj [A]", "Tcond max [°C]", "Durum"
        ])
        self.nodal_result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.nodal_result_table.setAlternatingRowColors(True)
        self.nodal_result_table.horizontalHeader().setStretchLastSection(True)
        self.nodal_result_table.itemSelectionChanged.connect(self._nodal_result_selection_changed)
        self.nodal_result_table.itemDoubleClicked.connect(self._open_thermal_analysis_detail)
        self.transient_result_table = QTableWidget(0, 15)
        self.transient_result_table.setHorizontalHeaderLabels([
            "Bölge", "Profil", "I baz [A]", "I sürekli 2D [A]", "I çevrimsel [A]",
            "Çevrim faktörü", "Acil süre [h]", "I acil [A]", "Tmax [°C]", "Tjacket [°C]",
            "Tmax zamanı [h]", "Ön çevrim", "Uç ΔT [°C]", "Durum", "Uyarı"
        ])
        self.transient_result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.transient_result_table.setAlternatingRowColors(True)
        self.transient_result_table.horizontalHeader().setStretchLastSection(True)
        self.transient_result_table.itemSelectionChanged.connect(self._transient_result_selection_changed)
        self.iec_result_table = QTableWidget(0, 9)
        self.iec_result_table.setHorizontalHeaderLabels(
            ["Bölüm", "Ampacity [A]", "Marj [A]", "T @ tasarım [°C]", "Rac [Ω/km]", "T4 [K·m/W]", "T kaynak", "Toplam kayıp [W/m]", "Durum"]
        )
        self.bonding_result_table = QTableWidget(0, 10)
        self.bonding_result_table.setHorizontalHeaderLabels(
            ["Loop", "Metalik kılıf yolu", "Kalan EMF [V]", "Metalik kılıf akımı [A]", "Zii [Ω]", "Metalik kılıf kaybı [W]", "Maks. minor V [V]", "λ1", "Çözüm", "Matris cond"]
        )
        self.bonding_result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bonding_result_table.itemSelectionChanged.connect(self._bonding_result_selection_changed)
        self.bonding_matrix_table = QTableWidget(0, 8)
        self.bonding_matrix_table.setHorizontalHeaderLabels(
            ["Major", "Satır", "Z1 [Ω]", "Z2 [Ω]", "Z3 [Ω]", "E [V]", "I [A]", "cond"]
        )
        self.bonding_matrix_table.setAlternatingRowColors(True)
        self.bonding_matrix_table.horizontalHeader().setStretchLastSection(True)
        self.primitive_result_table = QTableWidget(0, 10)
        self.primitive_result_table.setHorizontalHeaderLabels(
            ["Section", "M", "Ikılıf A [A]", "Ikılıf B [A]", "Ikılıf C [A]", "IGCC [A]", "Vmax [V]", "Pkılıf [W]", "Pearth [W]", "CIM↔NV"]
        )
        self.primitive_result_table.setAlternatingRowColors(True)
        self.primitive_result_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_result_table.horizontalHeader().setStretchLastSection(True)
        self.fault_result_table = QTableWidget(0, 13)
        self.fault_result_table.setHorizontalHeaderLabels(
            ["Senaryo", "Tip", "If [A]", "Süre [s]", "Ish max [A]", "IGCC max [A]",
             "Vkılıf-toprak max [V]", "Vinterrupt max [V]", "EPR max [V]", "Iearth max [A]",
             "Pkılıf [W]", "Pearth [W]", "CIM↔NV"]
        )
        self.fault_result_table.setAlternatingRowColors(True)
        self.fault_result_table.horizontalHeader().setStretchLastSection(True)
        self.svl_result_table = QTableWidget(0, 11)
        self.svl_result_table.setHorizontalHeaderLabels(
            ["Aday", "Durum", "MCOV", "Sürekli gerek", "TOV", "Residual", "Lead düşümü", "Koruma seviyesi", "Enerji", "Deşarj", "Açıklama"]
        )
        self.svl_result_table.horizontalHeader().setStretchLastSection(True)
        self.svl_result_table.setAlternatingRowColors(True)
        self.bonding_result_table.setAlternatingRowColors(True)
        self.iec_result_table.horizontalHeader().setStretchLastSection(True)
        self.iec_result_table.setAlternatingRowColors(True)
        self.warning_list = QPlainTextEdit()
        self.warning_list.setReadOnly(True)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.validation_view = QPlainTextEdit()
        self.validation_view.setReadOnly(True)
        for widget, title in (
            (self.summary_table, "Özet"),
            (self.thermal_result_table, "Termal Dirençler"),
            (self.thermal_route_result_table, "Termal Güzergâh Sonuçları"),
            (self.nodal_result_table, "2D Nodal Sonuçları"),
            (self.transient_result_table, "IEC 60853 Sonuçları"),
            (self.bonding_result_table, "Bonding Sonuçları"),
            (self.bonding_matrix_table, "Legacy Loop Matrisi"),
            (self.primitive_result_table, "Primitive CIM / NV"),
            (self.fault_result_table, "Arıza / EPR Sonuçları"),
            (self.svl_result_table, "SVL Sonuçları"),
            (self.iec_result_table, "IEC 60287 Sonuçları"),
            (self.warning_list, "Uyarılar"),
            (self.log_view, "Hesap Günlüğü"),
            (self.validation_view, "Doğrulama"),
        ):
            self.bottom_tabs.addTab(widget, title)
        self.results_dialog = QDialog(self)
        self.results_dialog.setModal(False)
        self.results_dialog.setWindowFlags(self.results_dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.results_dialog.setWindowTitle("DiTuS — Sonuçlar ve Kayıtlar")
        results_layout = QVBoxLayout(self.results_dialog)
        results_layout.setContentsMargins(5, 5, 5, 5)
        results_layout.addWidget(self.bottom_tabs)

        # workflow bileşenleri iç API/rehber penceresi için korunur; ana ekrana eklenmez.
        self.workflow_stage_bar = WorkflowStageBar()
        self.workflow_stage_bar.stageSelected.connect(self._activate_workflow_stage)
        self.progress_label = QLabel()
        self.progress_label.hide()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 4)
        mascot_path = self.project_root / "assets" / "ditus_mascot.png"
        self.project_identity = ProjectIdentityHeader(mascot_path)
        central_layout.addWidget(self.project_identity)
        central_layout.addWidget(self.upper_splitter, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.status_brand_label = QLabel("DiTuS™ · İşler karışmadan vaziyet alın")
        self.status_brand_label.setStyleSheet("color:#445b6e; padding-right:12px;")
        self.status_credit_label = QLabel("Designed by S. Esim — AI-assisted development")
        self.status_credit_label.setStyleSheet("font-weight:650; color:#274f6f; padding-right:8px;")
        self.statusBar().addPermanentWidget(self.status_brand_label)
        self.statusBar().addPermanentWidget(self.status_credit_label)
        if mascot_path.exists():
            icon = QIcon(str(mascot_path))
            self.setWindowIcon(icon)
            self.module_dialog.setWindowIcon(icon)
            self.context_dialog.setWindowIcon(icon)
            self.results_dialog.setWindowIcon(icon)
            self.database_dialog.setWindowIcon(icon)

    def _workflow_runtime_context(self) -> dict[str, object]:
        precheck_status = (self.project.cable_application.last_iteration_status or "").strip().upper()
        # NOT_RUN is an absence of a runtime result, not proof that the stage has
        # never started.  Let the workflow evaluator derive READY from an assigned
        # cable and a valid design current instead of overriding it with Başlamadı.
        precheck_runtime = None if precheck_status in {"", "NOT_RUN"} else precheck_status
        return {
            "system_load": self.project.design_progress.system_load,
            "route": self.project.design_progress.route,
            "cable": self.project.design_progress.cable,
            "precheck": precheck_runtime,
            "iec60287": bool(self.iec_results),
            "thermal_route": bool(self.thermal_route_result),
            "nodal": bool(self.nodal_thermal_result),
            "bonding": bool(self.bonding_result),
            "fault_epr": bool(self.fault_result),
            "svl": bool(self.svl_result),
            "transient": bool(self.transient_thermal_result),
            "iteration": self.project.design_progress.final_design,
        }

    def show_standard_defaults(self, focus_field: str = "") -> bool:
        """Standart katsayı ön tanım ekranını aç.

        ``focus_field`` verilirse doğrudan eksik alanın sekmesi açılır; kullanıcı
        hangi sekmede olduğunu aramaz.
        """

        dialog = StandardDefaultsDialog(
            self.standard_defaults, self.standard_defaults_path, self, focus_field
        )
        if dialog.exec() == QDialog.Accepted:
            self.standard_defaults = dialog.result_defaults
            self._build_tree()
            self.statusBar().showMessage("Standart katsayı ön tanımları güncellendi.", 6000)
            return True
        return False

    def _confirm_engine_precheck(self, engine_id: str) -> bool:
        """Evaluate one motor's data gates and obtain explicit user consent.

        Combined/first-design iterations suppress child dialogs but still evaluate
        and persist each child's precheck so workflow maturity remains traceable.
        """
        if engine_id in PRODUCTION_GEOMETRY_ENGINE_IDS:
            synchronize_installation_geometry(self.project)
        result = evaluate_engine_precheck(self.project, engine_id)
        self._active_engine_prechecks[engine_id] = result
        if self._suppress_engine_precheck:
            if not result.can_run:
                self._record_engine_status(
                    engine_id,
                    STATUS_BLOCKED,
                    warning_count=len(result.hard_missing),
                    message="Hesap ön kontrolü zorunlu girdi eksikleri nedeniyle bloke oldu.",
                    conditional_reasons=[item.label for item in result.hard_missing],
                )
            return result.can_run

        mascot_path = self.project_root / "assets" / "ditus_mascot.png"
        dialog = EnginePrecheckDialog(result, mascot_path, self)
        self._fit_dialog_to_available_screen(dialog, 920, 680)
        decision = dialog.exec()
        if decision == EnginePrecheckDialog.OPEN_MISSING:
            self._activate_workflow_stage(result.primary_owner_stage_id)
            self.statusBar().showMessage(
                f"{result.method.display_name}: eksik verinin sahibi olan aşama açıldı.", 8000
            )
            return False
        if decision != EnginePrecheckDialog.RUN:
            self._active_engine_prechecks.pop(engine_id, None)
            return False
        return result.can_run

    def _precheck_payload(self, engine_id: str) -> dict[str, object] | None:
        result = self._active_engine_prechecks.get(engine_id)
        return result.to_dict() if result is not None else None

    def _record_engine_status(
        self,
        engine_id: str,
        status: str,
        *,
        result_count: int = 0,
        warning_count: int = 0,
        message: str = "",
        conditional_reasons: list[str] | tuple[str, ...] = (),
        rebuild_tree: bool = True,
    ) -> None:
        precheck = self._active_engine_prechecks.get(engine_id)
        reasons = list(conditional_reasons)
        effective_status = status
        if precheck is not None and precheck.status == PRECHECK_CONDITIONAL:
            reasons.extend(
                [item.label for item in precheck.soft_missing]
                + [item.label for item in precheck.assumed_items]
                + list(precheck.assumptions)
            )
        record_engine_run(
            self.project,
            engine_id,
            effective_status,
            result_count=result_count,
            warning_count=warning_count,
            message=message,
            conditional_reasons=list(dict.fromkeys(reasons)),
            precheck=precheck.to_dict() if precheck is not None else None,
        )
        if effective_status != STATUS_RUNNING:
            self._active_engine_prechecks.pop(engine_id, None)
        self._mark_dirty()
        self._refresh_workflow()
        if rebuild_tree:
            self._build_tree()

    def _begin_engine_run(self, engine_id: str, message: str = "") -> None:
        self._record_engine_status(
            engine_id, STATUS_RUNNING, message=message or "Hesap motoru çalışıyor.", rebuild_tree=True
        )

    def _fail_engine_run(self, engine_id: str, message: str) -> None:
        self._record_engine_status(
            engine_id, STATUS_BLOCKED, message=message, warning_count=1, rebuild_tree=True
        )

    def _refresh_workflow(self) -> None:
        self.workflow_evaluation = evaluate_project_workflow(
            self.project, self._workflow_runtime_context()
        )
        self.project.workflow.last_recommended_stage_id = self.workflow_evaluation.recommended_stage_id
        self.project.workflow.last_evaluated_at = self.workflow_evaluation.evaluated_at
        self.project_identity.update_project(
            self.project.project_name,
            self.project.project_code,
            self.workflow_evaluation.overall_status,
        )
        self.workflow_stage_bar.set_evaluation(self.workflow_evaluation)
        stage = self.workflow_evaluation.stage(self.workflow_evaluation.current_stage_id)
        self.workflow_guide.set_stage(stage, self.workflow_evaluation)
        self.progress_label.setText(
            f"Aktif aşama: {stage.number}. {stage.title} · {status_text(stage.status)}  |  "
            f"Önerilen: {self.workflow_evaluation.recommended_action}"
        )
        self.statusBar().showMessage(
            f"Önerilen sonraki adım: {self.workflow_evaluation.recommended_action}"
        )

    def _activate_workflow_stage(self, stage_id: str, switch_workspace: bool = True) -> None:
        if not stage_id:
            return
        if self.workflow_evaluation is None:
            self.workflow_evaluation = evaluate_project_workflow(
                self.project, self._workflow_runtime_context()
            )
        try:
            stage = self.workflow_evaluation.stage(stage_id)
        except KeyError:
            return
        self.project.workflow.current_stage_id = stage_id
        self.workflow_evaluation.current_stage_id = stage_id
        self.workflow_stage_bar.select_stage(stage_id)
        self.workflow_guide.set_stage(stage, self.workflow_evaluation)
        self.progress_label.setText(
            f"Aktif aşama: {stage.number}. {stage.title} · {status_text(stage.status)}  |  "
            f"Sonraki işlem: {stage.next_action}"
        )
        if switch_workspace:
            self.stage_host.set_standalone_mode(False)
            if stage.stage_id == "cable":
                self.show_project_cable_selection()
            elif stage.stage_id == "installation":
                self.show_installation_designer()
            elif stage.stage_id == "iteration":
                # DEBUG/HOTFIX: Final birleşik iterasyon, erken aday-kablo
                # ön-eleme sayfası değildir. Mevcut sonuç penceresini aç;
                # hesap akışı ve veri modeli değişmeden kalır.
                self.module_dialog.hide()
                self.show_results_dialog(self.summary_table)
            elif stage.stage_id == "deliverables":
                # DEBUG/HOTFIX: Çıktılar aşaması yanlışlıkla İlk Tasarım
                # çalışma alanına bağlıydı. Mevcut birleşik sonuç/kayıt
                # penceresine yönlendir; rapor ve BOQ araçları ağaçta
                # ayrı komutlar olarak kalır.
                self.module_dialog.hide()
                self.show_results_dialog(self.summary_table)
            else:
                workspace = self._workspace_for_stage(stage.workspace_key)
                if workspace is not None:
                    title = f"{stage.number}. {stage.title}"
                    if stage.stage_id == "steady_thermal":
                        title += " — Sonuç İnceleme" if self.nodal_thermal_result is not None else " — Girdiler ve Bölge Tanımı"
                    self._show_workspace_widget(workspace, title)
        self._sync_stage_host(f"{stage.number}. {stage.title}")
        self.statusBar().showMessage(
            f"{stage.number}. {stage.title} — {status_text(stage.status)} · {stage.next_action}", 7000
        )

    def _workspace_for_stage(self, key: str) -> QWidget | None:
        # IEC 60287 / 2D termal aşaması sonuç oluşmadan boş sonuç ekranına
        # düşmemelidir. Önce termal güzergâh girdileri açılır; 2D çözüm
        # üretildikten sonra aynı aşama termal sonuç incelemesini açar.
        if key == "thermal_review":
            return self.thermal_review_widget if self.nodal_thermal_result is not None else self.thermal_route_widget
        mapping = {
            "first_design": self.first_design_widget,
            "route": self.route_table_widget,
            "thermal_route": self.thermal_route_widget,
            "cable": self.cable_library_widget,
            "bonding": self.bonding_table_widget,
            "fault": self.fault_table_widget,
            "svl": self.svl_table_widget,
            "transient": self.transient_widget,
        }
        return mapping.get(key)

    def _apply_result_group_visibility(self, group: str) -> None:
        preferred = {
            "summary": self.summary_table,
            "thermal_resistance": self.thermal_result_table,
            "steady_thermal": self.nodal_result_table,
            "bonding": self.bonding_result_table,
            "fault_epr": self.fault_result_table,
            "svl": self.svl_result_table,
            "transient": self.transient_result_table,
        }.get(group, self.summary_table)
        self.bottom_tabs.setCurrentWidget(preferred)

    def _sync_stage_host(self, fallback_title: str = "") -> None:
        """Konak çerçevesini aktif aşamaya göre tazele."""

        evaluation = self.workflow_evaluation
        stage = None
        if evaluation is not None:
            stage_id = str(self.project.workflow.current_stage_id or "")
            if stage_id:
                try:
                    stage = evaluation.stage(stage_id)
                except KeyError:
                    stage = None
        self.stage_host.set_stage(stage, evaluation, fallback_title)

    def _go_previous_stage(self) -> None:
        stage_id = self.stage_host.neighbour_stage_id(-1)
        if stage_id:
            self._activate_workflow_stage(stage_id)

    def _go_next_stage(self) -> None:
        stage_id = self.stage_host.neighbour_stage_id(1)
        if stage_id:
            self._activate_workflow_stage(stage_id)

    def _open_recommended_workflow_stage(self) -> None:
        self._refresh_workflow()
        if self.workflow_evaluation is not None:
            self._activate_workflow_stage(self.workflow_evaluation.recommended_stage_id)

    def _toggle_property_panel(self) -> None:
        if self.act_toggle_properties.isChecked():
            self.context_dialog.showNormal()
            self._fit_dialog_to_available_screen(self.context_dialog, 620, 760)
            self.context_dialog.show()
            self.context_dialog.raise_()
            self.context_dialog.activateWindow()
        else:
            self.context_dialog.hide()

    def show_workflow_guide(self) -> None:
        self.context_tabs.setCurrentIndex(0)
        self.act_toggle_properties.setChecked(True)
        self._toggle_property_panel()

    # Boyut otoritesi tek yerde: ucd.ui.window_layout.  Bu yöntem yalnız
    # geriye dönük çağrı noktalarını oraya yönlendiren ince bir sarmalayıcıdır;
    # piksel tercihi artık yoğunluk sınıfına çevrilir.
    def _fit_dialog_to_available_screen(
        self, dialog: QDialog, preferred_width: int = 0, preferred_height: int = 0
    ) -> None:
        fit_window(dialog, self._density_for(preferred_width, preferred_height), center_on=self)

    @staticmethod
    def _density_for(width: int, height: int) -> str:
        if width >= 1440 or height >= 880:
            return DENSITY_WIDE
        if width <= 700 and height <= 700:
            return DENSITY_COMPACT
        return DENSITY_NORMAL

    def _show_workspace_widget(
        self, widget: QWidget, title: str | None = None, *, workflow_frame: bool = True
    ) -> None:
        index = self.workspace_tabs.indexOf(widget)
        if index < 0:
            return
        self.workspace_tabs.setCurrentIndex(index)
        label = title or self.workspace_tabs.tabText(index)
        self.module_dialog.setWindowTitle(f"DiTuS — {label}")
        # Proje ağacından açılan nesne editörleri sihirbaz değildir. Aynı
        # çalışma widget'ını kullanırlar fakat StageHost üst/alt akış şeritleri
        # gizlenir. Tasarım Akışı üzerinden açılan aşamalar çerçeveyi korur.
        self.stage_host.set_standalone_mode(not workflow_frame)
        if workflow_frame:
            self._sync_stage_host(label)
        else:
            self.stage_host.set_stage(None, self.workflow_evaluation, label)
        # Bonding tam-ekran modu başka bir modüle taşınmamalıdır.
        if widget is not self.bonding_table_widget and self.bonding_focus_mode:
            self.bonding_focus_mode = False
            self.bonding_focus_button.setText("Bonding Tam Ekran")
        if not (widget is self.bonding_table_widget and self.bonding_focus_mode):
            self.module_dialog.showNormal()
            self._fit_dialog_to_available_screen(self.module_dialog, 1420, 850)
        self.module_dialog.show()
        self.module_dialog.raise_()
        self.module_dialog.activateWindow()

    def show_results_dialog(self, widget: QWidget | None = None) -> None:
        if widget is not None and self.bottom_tabs.indexOf(widget) >= 0:
            self.bottom_tabs.setCurrentWidget(widget)
        self.results_dialog.showNormal()
        self._fit_dialog_to_available_screen(self.results_dialog, 1450, 820)
        self.results_dialog.show()
        self.results_dialog.raise_()
        self.results_dialog.activateWindow()

    def _show_results_widget(self, widget: QWidget) -> None:
        self.show_results_dialog(widget)

    def _show_main_canvas(self) -> None:
        self.module_dialog.hide()
        self.results_dialog.hide()
        self.context_dialog.hide()
        self.act_toggle_properties.setChecked(False)
        self.plan_view.setFocus()

    def _build_thermal_review_widget(self) -> QWidget:
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(5, 5, 5, 5)

        # Keep the selected-region summary on its own line.  The former single
        # horizontal row (summary + six commands) created a large minimum width
        # and could push the dialog frame beyond a smaller monitor.
        self.thermal_selected_summary = QLabel(
            "2D nodal çözüm henüz çalıştırılmadı. Önce termal güzergâh bölgelerini ve kesit girdilerini doğrulayın."
        )
        self.thermal_selected_summary.setWordWrap(True)
        self.thermal_selected_summary.setStyleSheet(
            "font-weight: 650; padding: 6px; border: 1px solid #c7d2dc; border-radius: 4px;"
        )
        outer.addWidget(self.thermal_selected_summary)

        input_btn = QPushButton("Termal Güzergâh Girdileri")
        detail_btn = QPushButton("Termal Analiz Detayı")
        critical_btn = QPushButton("Kritik Bölgeyi Aç")
        mesh_btn = QPushButton("Mesh Yakınsaması")
        solve_btn = QPushButton("Tüm Bölgeleri 2D Çöz")
        results_btn = QPushButton("2D Sonuçlarını Aç")
        input_btn.clicked.connect(lambda: self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh Girdileri"))
        detail_btn.clicked.connect(self._open_thermal_analysis_detail)
        critical_btn.clicked.connect(self._select_critical_nodal_region)
        mesh_btn.clicked.connect(self._run_selected_mesh_convergence)
        solve_btn.clicked.connect(self.run_nodal_thermal_analysis)
        results_btn.clicked.connect(lambda: self.show_results_dialog(self.nodal_result_table))
        command_grid = QGridLayout()
        command_grid.setHorizontalSpacing(8)
        command_grid.setVerticalSpacing(6)
        for index, button in enumerate((input_btn, detail_btn, critical_btn, mesh_btn, solve_btn, results_btn)):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            command_grid.addWidget(button, index // 3, index % 3)
        outer.addLayout(command_grid)

        selector_grid = QGridLayout()
        selector_grid.addWidget(QLabel("Senaryo"), 0, 0)
        self.thermal_scenario_selector = QComboBox()
        self.thermal_scenario_selector.setMinimumWidth(160)
        self.thermal_scenario_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.thermal_scenario_selector.currentIndexChanged.connect(self._thermal_scenario_selector_changed)
        selector_grid.addWidget(self.thermal_scenario_selector, 0, 1)
        selector_grid.addWidget(QLabel("Çözüm kapsamı"), 1, 0)
        self.thermal_scope_selector = QComboBox()
        self.thermal_scope_selector.setMinimumWidth(180)
        self.thermal_scope_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.thermal_scope_selector.currentIndexChanged.connect(self._thermal_scope_selector_changed)
        selector_grid.addWidget(self.thermal_scope_selector, 1, 1)
        selector_grid.addWidget(QLabel("Bölge"), 2, 0)
        self.thermal_region_selector = QComboBox()
        self.thermal_region_selector.setMinimumWidth(180)
        self.thermal_region_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.thermal_region_selector.currentIndexChanged.connect(self._thermal_region_selector_changed)
        selector_grid.addWidget(self.thermal_region_selector, 2, 1)
        selector_grid.setColumnStretch(1, 1)
        outer.addLayout(selector_grid)

        controls_grid = QGridLayout()
        self.thermal_show_material_boundaries = QCheckBox("Malzeme sınırları")
        self.thermal_show_geometry = QCheckBox("Kanal / katman / duct geometrisi")
        self.thermal_show_cables = QCheckBox("Kablolar")
        self.thermal_show_mesh = QCheckBox("Mesh")
        self.thermal_show_isotherms = QCheckBox("İzotermler")
        self.thermal_show_hotspot = QCheckBox("Sıcak nokta")
        self.thermal_show_material_legend = QCheckBox("Malzeme listesi")
        for index, (checkbox, checked) in enumerate((
            (self.thermal_show_material_boundaries, True),
            (self.thermal_show_geometry, True),
            (self.thermal_show_cables, True),
            (self.thermal_show_mesh, False),
            (self.thermal_show_isotherms, False),
            (self.thermal_show_hotspot, True),
            (self.thermal_show_material_legend, True),
        )):
            checkbox.setChecked(checked)
            checkbox.toggled.connect(self._redraw_selected_nodal_result)
            controls_grid.addWidget(checkbox, index // 4, index % 4)
        fit_thermal_btn = QPushButton("Görünüme Sığdır")
        fit_thermal_btn.setToolTip("Zoom/pan sonrasında tüm termal kesiti yeniden görünür alana sığdırır.")
        fit_thermal_btn.clicked.connect(lambda: self.thermal_view.fit_current_view())
        controls_grid.addWidget(fit_thermal_btn, 1, 3)
        outer.addLayout(controls_grid)

        full_note = (
            "Bu görünüm seçili güzergâh bölgesinin güzergâha dik 2D orta kesitidir. "
            "Üretim bağlı kesitte Kablo-Kanal ekranındaki tüm fiziksel kablolar, hendek katmanları, "
            "malzemeler, duct/grout, özel malzeme bölgeleri ve dış ısı kaynakları modele girer. "
            "'Senaryo birlikte' elektrik senaryosundaki devreleri; 'Kanalın tüm devreleri birlikte' "
            "aynı kesitteki bütün devrelerin karşılıklı ısınmasını; 'Yalnız Cx enerjili' ise diğer "
            "kabloları pasif ısıl cisim olarak koruyup seçili devrenin izole katkısını gösterir. "
            "Ayrıntılı doğrulamalar ve tasarım alternatifleri Termal Analiz Detayı penceresindedir."
        )
        note = QLabel(
            "ⓘ 2D orta kesit · Fiziksel kablolar/duct/katmanlar çözüm geometrisinden çizilir. "
            "Ayrıntı için imleci bu satırda bekletin."
        )
        note.setWordWrap(True)
        note.setToolTip(full_note)
        note.setStyleSheet("color:#526878; padding:2px 4px;")
        outer.addWidget(note)

        self.thermal_view = SimpleDiagramView("thermal")
        self.thermal_view.setMinimumHeight(300)
        self.thermal_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(self.thermal_view, 1)
        return container

    def _build_transient_widget(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(5, 5, 5, 5)
        hint = QLabel(
            "IEC 60853 çalışma akışı: kullanıcı yük-zaman profilini tanımlar; her termal bölge "
            "2D geçici sonlu hacim modeliyle çözülür. Çevrimsel ve acil rating sonuçları "
            "kararlı durum 2D ampacity ile birlikte gösterilir."
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)

        commands = QHBoxLayout()
        commands.addWidget(QLabel("Aktif profil"))
        self.transient_profile_selector = QComboBox()
        self.transient_profile_selector.currentIndexChanged.connect(self._transient_profile_selected)
        commands.addWidget(self.transient_profile_selector)
        add_btn = QPushButton("Yük Noktası Ekle")
        delete_btn = QPushButton("Seçili Noktayı Sil")
        run_btn = QPushButton("IEC 60853 Çalışmasını Çalıştır")
        results_btn = QPushButton("IEC 60853 Sonuçlarını Aç")
        add_btn.clicked.connect(self._add_transient_point)
        delete_btn.clicked.connect(self._delete_transient_point)
        run_btn.clicked.connect(self.run_transient_thermal_analysis)
        results_btn.clicked.connect(lambda: self.show_results_dialog(self.transient_result_table))
        commands.addWidget(add_btn)
        commands.addWidget(delete_btn)
        commands.addStretch(1)
        commands.addWidget(results_btn)
        commands.addWidget(run_btn)
        outer.addLayout(commands)

        body = QSplitter(Qt.Horizontal)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(QLabel("YÜK-ZAMAN PROFİLİ"))
        self.transient_profile_table = QTableWidget(0, 3)
        self.transient_profile_table.setHorizontalHeaderLabels(["Zaman [h]", "Akım çarpanı", "Açıklama"])
        self.transient_profile_table.horizontalHeader().setStretchLastSection(True)
        self.transient_profile_table.setAlternatingRowColors(True)
        self.transient_profile_table.itemChanged.connect(self._transient_profile_table_changed)
        editor_layout.addWidget(self.transient_profile_table, 2)

        editor_layout.addWidget(QLabel("ÇÖZÜM AYARLARI"))
        self.transient_settings_table = QTableWidget(0, 2)
        self.transient_settings_table.setHorizontalHeaderLabels(["Parametre", "Değer"])
        self.transient_settings_table.horizontalHeader().setStretchLastSection(True)
        self.transient_settings_table.setAlternatingRowColors(True)
        self.transient_settings_table.itemChanged.connect(self._transient_settings_table_changed)
        editor_layout.addWidget(self.transient_settings_table, 2)

        editor_layout.addWidget(QLabel("ÇÖZÜLECEK TERMAL BÖLGELER"))
        self.transient_region_table = QTableWidget(0, 3)
        self.transient_region_table.setHorizontalHeaderLabels(["Bölge", "Ad", "Çöz"])
        self.transient_region_table.horizontalHeader().setStretchLastSection(True)
        self.transient_region_table.itemChanged.connect(self._transient_region_table_changed)
        editor_layout.addWidget(self.transient_region_table, 1)

        plot_box = QWidget()
        plot_layout = QVBoxLayout(plot_box)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(QLabel("SON ÇEVRİM — AKIM VE SICAKLIK"))
        self.transient_plot = TransientThermalView()
        plot_layout.addWidget(self.transient_plot, 1)
        self.transient_inspector = QPlainTextEdit()
        self.transient_inspector.setReadOnly(True)
        self.transient_inspector.setMaximumHeight(150)
        plot_layout.addWidget(self.transient_inspector)

        body.addWidget(editor)
        body.addWidget(plot_box)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([470, 950])
        outer.addWidget(body, 1)
        return container

    def _active_transient_profile(self) -> TransientLoadProfile | None:
        profile_id = self.project.transient_study.active_profile_id
        return next((item for item in self.project.transient_study.profiles if item.profile_id == profile_id), None)

    def _refresh_transient_tables(self) -> None:
        selector = self.transient_profile_selector
        selector.blockSignals(True)
        selector.clear()
        active_index = 0
        for index, profile in enumerate(self.project.transient_study.profiles):
            selector.addItem(f"{profile.profile_id} — {profile.name}", profile.profile_id)
            if profile.profile_id == self.project.transient_study.active_profile_id:
                active_index = index
        if selector.count():
            selector.setCurrentIndex(active_index)
        selector.blockSignals(False)

        profile = self._active_transient_profile()
        self.transient_profile_table.blockSignals(True)
        points = sorted(profile.points, key=lambda item: item.time_h) if profile else []
        self.transient_profile_table.setRowCount(len(points))
        for row, point in enumerate(points):
            for col, value in enumerate((f"{point.time_h:g}", f"{point.current_multiplier:g}", point.label)):
                self.transient_profile_table.setItem(row, col, QTableWidgetItem(value))
        self.transient_profile_table.blockSignals(False)

        settings = self.project.transient_study
        cycle_metrics = load_cycle_metrics(profile) if profile is not None else None
        rows = [
            ("Profil tepe akım çarpanı", None, f"{cycle_metrics.peak_multiplier:.6f}" if cycle_metrics else "—"),
            ("Akım yük faktörü LF", None, f"{cycle_metrics.current_load_factor:.6f}" if cycle_metrics else "—"),
            ("IEC 60853 kayıp-yük faktörü μ", None, f"{cycle_metrics.loss_load_factor_mu:.6f}" if cycle_metrics else "—"),
            ("Zaman adımı [min]", "time_step_minutes", settings.time_step_minutes),
            ("Transient mesh ölçeği", "transient_mesh_scale", settings.transient_mesh_scale),
            ("Başlangıç koşulu", "initial_condition_mode", settings.initial_condition_mode),
            ("Kullanıcı başlangıç T [°C]", "user_initial_conductor_temperature_c", settings.user_initial_conductor_temperature_c),
            ("Maks. ön çevrim", "maximum_preconditioning_cycles", settings.maximum_preconditioning_cycles),
            ("Çevrim yakınsama [°C]", "cyclic_convergence_tolerance_c", settings.cyclic_convergence_tolerance_c),
            ("Normal sıcaklık limiti [°C]", "normal_temperature_limit_c", settings.normal_temperature_limit_c),
            ("Acil sıcaklık limiti [°C]", "emergency_temperature_limit_c", settings.emergency_temperature_limit_c),
            ("Acil yük süresi [h]", "emergency_duration_h", settings.emergency_duration_h),
            ("Çevrimsel rating hesapla", "calculate_cyclic_rating", "Evet" if settings.calculate_cyclic_rating else "Hayır"),
            ("Acil rating hesapla", "calculate_emergency_rating", "Evet" if settings.calculate_emergency_rating else "Hayır"),
            ("Kablo dışı hacimsel C [MJ/m³K]", "cable_outer_heat_capacity_mj_m3k", settings.cable_outer_heat_capacity_mj_m3k),
        ]
        self.transient_settings_table.blockSignals(True)
        self.transient_settings_table.setRowCount(len(rows))
        for row, (label, key, value) in enumerate(rows):
            label_item = QTableWidgetItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            value_item = QTableWidgetItem(str(value))
            value_item.setData(Qt.UserRole, key)
            if key is None:
                value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)
                value_item.setToolTip("Aktif yük profilinden otomatik türetilir; kararlı durum akımına uygulanmaz.")
            self.transient_settings_table.setItem(row, 0, label_item)
            self.transient_settings_table.setItem(row, 1, value_item)
        self.transient_settings_table.blockSignals(False)

        selected = set(settings.selected_region_ids)
        self.transient_region_table.blockSignals(True)
        regions = [item for item in self.project.thermal_design.regions if item.enabled]
        self.transient_region_table.setRowCount(len(regions))
        for row, region in enumerate(regions):
            values = (region.region_id, region.name, "Evet" if not selected or region.region_id in selected else "Hayır")
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col < 2:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                cell.setData(Qt.UserRole, region.region_id)
                self.transient_region_table.setItem(row, col, cell)
        self.transient_region_table.blockSignals(False)

    def _transient_profile_selected(self) -> None:
        profile_id = self.transient_profile_selector.currentData()
        if not profile_id:
            return
        self.project.transient_study.active_profile_id = str(profile_id)
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_transient_tables()

    def _transient_profile_table_changed(self, item: QTableWidgetItem) -> None:
        profile = self._active_transient_profile()
        if profile is None or item.row() >= len(profile.points):
            return
        points = sorted(profile.points, key=lambda point: point.time_h)
        point = points[item.row()]
        try:
            if item.column() == 0:
                point.time_h = self._parse_number(item.text())
            elif item.column() == 1:
                point.current_multiplier = self._parse_number(item.text())
            else:
                point.label = item.text().strip()
            profile.points = sorted(points, key=lambda value: value.time_h)
            self._invalidate_results()
            self._mark_dirty()
            self._refresh_transient_tables()
        except ValueError:
            QMessageBox.warning(self, "Yük profili", "Zaman veya akım çarpanı geçersiz.")
            self._refresh_transient_tables()

    def _transient_settings_table_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        key = item.data(Qt.UserRole)
        if not key:
            return
        settings = self.project.transient_study
        try:
            if key in {"initial_condition_mode"}:
                value = item.text().strip().upper()
            elif key in {"calculate_cyclic_rating", "calculate_emergency_rating"}:
                value = self._parse_bool(item.text())
            elif key in {"maximum_preconditioning_cycles"}:
                value = int(self._parse_number(item.text()))
            else:
                value = self._parse_number(item.text())
            setattr(settings, str(key), value)
            self._invalidate_results()
            self._mark_dirty()
        except ValueError:
            QMessageBox.warning(self, "Geçici termal ayar", "Girilen değer geçersiz.")
            self._refresh_transient_tables()

    def _transient_region_table_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 2:
            return
        try:
            selected = []
            for row in range(self.transient_region_table.rowCount()):
                cell = self.transient_region_table.item(row, 2)
                region_id = self.transient_region_table.item(row, 0).text()
                if cell and self._parse_bool(cell.text()):
                    selected.append(region_id)
            all_enabled = [region.region_id for region in self.project.thermal_design.regions if region.enabled]
            self.project.transient_study.selected_region_ids = [] if selected == all_enabled else selected
            self._invalidate_results()
            self._mark_dirty()
        except ValueError:
            QMessageBox.warning(self, "Geçici termal bölge", "Çöz alanına Evet veya Hayır girin.")
            self._refresh_transient_tables()

    def _add_transient_point(self) -> None:
        profile = self._active_transient_profile()
        if profile is None:
            return
        existing = sorted(point.time_h for point in profile.points)
        candidate = profile.duration_h / 2.0
        while any(abs(candidate - value) < 1e-6 for value in existing):
            candidate += max(0.1, profile.duration_h / 20.0)
            if candidate >= profile.duration_h:
                candidate = max(0.1, profile.duration_h - 0.1)
                break
        profile.points.append(LoadProfilePoint(candidate, 1.0, "Yeni yük noktası"))
        profile.points.sort(key=lambda point: point.time_h)
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_transient_tables()

    def _delete_transient_point(self) -> None:
        profile = self._active_transient_profile()
        row = self.transient_profile_table.currentRow()
        if profile is None or row < 0:
            return
        points = sorted(profile.points, key=lambda point: point.time_h)
        if len(points) <= 2:
            QMessageBox.warning(self, "Yük profili", "Profilde en az iki nokta kalmalıdır.")
            return
        points.pop(row)
        profile.points = points
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_transient_tables()

    def run_transient_thermal_analysis(self) -> None:
        if not self._confirm_engine_precheck("transient"):
            return
        self._activate_workflow_stage("transient")
        self._begin_engine_run("transient", "IEC 60853 geçici/çevrimsel çözümü çalışıyor.")
        try:
            if self.nodal_thermal_result is None:
                self.nodal_thermal_result = solve_nodal_route(self.project, self.bonding_result)
            result = solve_transient_route(
                self.project, self.bonding_result, self.nodal_thermal_result
            )
        except (TransientThermalInputError, NodalThermalInputError, ThermalRouteInputError) as exc:
            self._fail_engine_run("transient", str(exc))
            QMessageBox.critical(self, "IEC 60853 girdi hatası", str(exc))
            self.warning_list.setPlainText(str(exc))
            return
        except Exception as exc:
            self._fail_engine_run("transient", f"Beklenmeyen hata: {exc}")
            QMessageBox.critical(self, "IEC 60853 hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self.transient_thermal_result = result
        self._populate_transient_results()
        first = result.regions[0]
        self.transient_plot.draw_result(first)
        self.transient_inspector.setPlainText("\n".join(first.trace + first.warnings))
        self._show_workspace_widget(self.transient_widget, "IEC 60853 Geçici / Çevrimsel")
        self.project.design_progress.thermal = "TRANSIENT_COMPLETE" if is_suitable(result.status) else "CONDITIONAL"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_5
        transient_warnings = [warning for region in result.regions for warning in region.warnings]
        self._record_engine_status(
            "transient",
            STATUS_COMPLETE if is_suitable(result.status) else STATUS_CONDITIONAL,
            result_count=len(result.regions),
            warning_count=len(transient_warnings),
            message=f"IEC 60853 sonucu: {result.status}",
            conditional_reasons=transient_warnings[:8],
        )
        log = ["IEC 60853 GEÇİCİ / ÇEVRİMSEL TERMAL ÇALIŞMA", "=" * 72, *result.trace]
        warnings = []
        for region in result.regions:
            log.extend(["", *region.trace])
            warnings.extend(region.warnings)
        self.log_view.setPlainText("\n".join(log))
        self.warning_list.setPlainText("\n".join(f"• {value}" for value in dict.fromkeys(warnings)))
        self._update_summary()
        self._refresh_first_design()
        self.statusBar().showMessage(
            f"IEC 60853 tamamlandı — çevrimsel {result.route_cyclic_rating_per_cable_a:.1f} A/kablo; "
            f"acil {result.route_emergency_rating_per_cable_a:.1f} A/kablo", 12000
        )

    def _populate_transient_results(self) -> None:
        result = self.transient_thermal_result
        if result is None:
            self.transient_result_table.setRowCount(0)
            return
        self.transient_result_table.setRowCount(len(result.regions))
        for row, region in enumerate(result.regions):
            values = [
                f"{region.region_id} {region.region_name}", region.profile_name,
                f"{region.base_current_per_cable_a:.1f}", f"{region.continuous_ampacity_per_cable_a:.1f}",
                f"{region.cyclic_rating_per_cable_a:.1f}", f"{region.cyclic_rating_factor:.4f}",
                f"{region.emergency_duration_h:.2f}", f"{region.emergency_rating_per_cable_a:.1f}",
                f"{region.maximum_conductor_temperature_c:.2f}", f"{region.maximum_jacket_temperature_c:.2f}",
                f"{region.time_of_maximum_h:.2f}", str(region.preconditioning_cycles),
                f"{region.cyclic_end_delta_c:.3f}", region.status, " | ".join(region.warnings),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                cell.setData(Qt.UserRole, region.region_id)
                self.transient_result_table.setItem(row, col, cell)
        self.transient_result_table.resizeColumnsToContents()
        self.transient_result_table.horizontalHeader().setStretchLastSection(True)

    def _transient_result_selection_changed(self) -> None:
        result = self.transient_thermal_result
        row = self.transient_result_table.currentRow()
        if result is None or row < 0:
            return
        item = self.transient_result_table.item(row, 0)
        region_id = item.data(Qt.UserRole) if item else None
        region = next((value for value in result.regions if value.region_id == region_id), None)
        if region is None:
            return
        self.transient_plot.draw_result(region)
        self.transient_inspector.setPlainText("\n".join(region.trace + region.warnings))

    def _refresh_thermal_result_selectors(self) -> None:
        scenario_selector = self.thermal_scenario_selector
        scope_selector = self.thermal_scope_selector
        region_selector = self.thermal_region_selector
        for selector in (scenario_selector, scope_selector, region_selector):
            selector.blockSignals(True)
            selector.clear()
        result = self.nodal_thermal_result
        if result is None or not result.scenarios:
            scenario_selector.setEnabled(False)
            scope_selector.setEnabled(False)
            region_selector.setEnabled(False)
            scenario_selector.addItem("2D çözüm çalıştırılmadı", None)
            scope_selector.addItem("Çözüm kapsamı yok", None)
            region_selector.addItem("Bölge sonucu yok", None)
            for selector in (scenario_selector, scope_selector, region_selector):
                selector.blockSignals(False)
            return
        scenario_selector.setEnabled(True)
        for scenario in result.scenarios:
            scenario_selector.addItem(scenario.scenario_name, scenario.scenario_id)
        active = result.active
        active_index = max(0, scenario_selector.findData(active.scenario_id))
        scenario_selector.setCurrentIndex(active_index)
        scenario_selector.blockSignals(False)
        scope_selector.blockSignals(False)
        region_selector.blockSignals(False)
        self._populate_thermal_scope_selector(
            active.scenario_id, active.solution_scope_id, active.critical_region_id
        )

    def _populate_thermal_scope_selector(
        self,
        scenario_id: str,
        preferred_scope_id: str | None = None,
        preferred_region_id: str | None = None,
    ) -> None:
        selector = self.thermal_scope_selector
        selector.blockSignals(True)
        selector.clear()
        result = self.nodal_thermal_result
        scopes = result.scopes_for_scenario(scenario_id) if result else ()
        if not scopes:
            selector.addItem("Çözüm kapsamı yok", None)
            selector.setEnabled(False)
            selector.blockSignals(False)
            self._populate_thermal_region_selector(scenario_id, "", preferred_region_id)
            return
        selector.setEnabled(True)
        for scope in scopes:
            selector.addItem(scope.solution_scope_name, scope.solution_scope_id)
        target = preferred_scope_id or scopes[0].solution_scope_id
        index = selector.findData(target)
        selector.setCurrentIndex(index if index >= 0 else 0)
        selected_scope_id = str(selector.currentData())
        selector.blockSignals(False)
        self._populate_thermal_region_selector(
            scenario_id, selected_scope_id, preferred_region_id
        )

    def _populate_thermal_region_selector(
        self,
        scenario_id: str,
        scope_id: str,
        preferred_region_id: str | None = None,
    ) -> None:
        selector = self.thermal_region_selector
        selector.blockSignals(True)
        selector.clear()
        result = self.nodal_thermal_result
        scenario = result.scope_result(scenario_id, scope_id) if result else None
        if scenario is None:
            selector.addItem("Bölge sonucu yok", None)
            selector.setEnabled(False)
            selector.blockSignals(False)
            return
        selector.setEnabled(True)
        for region in scenario.regions:
            prefix = "★ " if region.region_id == scenario.critical_region_id else ""
            selector.addItem(
                f"{prefix}{region.region_id} · {region.region_name} · {region.start_m:.1f}–{region.end_m:.1f} m",
                region.region_id,
            )
        target = preferred_region_id or scenario.critical_region_id
        index = selector.findData(target)
        selector.setCurrentIndex(index if index >= 0 else 0)
        selector.blockSignals(False)

    def _thermal_scenario_selector_changed(self, _index: int) -> None:
        scenario_id = self.thermal_scenario_selector.currentData()
        if not scenario_id:
            return
        result = self.nodal_thermal_result
        scenario = next((item for item in result.scenarios if item.scenario_id == scenario_id), None) if result else None
        preferred_region = scenario.critical_region_id if scenario is not None else None
        preferred_scope = scenario.solution_scope_id if scenario is not None else None
        self._populate_thermal_scope_selector(
            str(scenario_id), preferred_scope, preferred_region
        )
        self._thermal_region_selector_changed(self.thermal_region_selector.currentIndex())

    def _thermal_scope_selector_changed(self, _index: int) -> None:
        scenario_id = self.thermal_scenario_selector.currentData()
        scope_id = self.thermal_scope_selector.currentData()
        if not scenario_id or not scope_id:
            return
        result = self.nodal_thermal_result
        scope = result.scope_result(str(scenario_id), str(scope_id)) if result else None
        preferred = scope.critical_region_id if scope is not None else None
        self._populate_thermal_region_selector(str(scenario_id), str(scope_id), preferred)
        self._thermal_region_selector_changed(self.thermal_region_selector.currentIndex())

    def _thermal_region_selector_changed(self, _index: int) -> None:
        scenario_id = self.thermal_scenario_selector.currentData()
        scope_id = self.thermal_scope_selector.currentData()
        region_id = self.thermal_region_selector.currentData()
        if scenario_id and scope_id and region_id:
            self._show_nodal_result(
                str(scenario_id), str(scope_id), str(region_id),
                sync_selection=True, sync_selectors=False,
            )

    def _thermal_display_options(self) -> dict[str, bool]:
        return {
            "show_material_boundaries": self.thermal_show_material_boundaries.isChecked(),
            "show_geometry": self.thermal_show_geometry.isChecked(),
            "show_cables": self.thermal_show_cables.isChecked(),
            "show_mesh": self.thermal_show_mesh.isChecked(),
            "show_isotherms": self.thermal_show_isotherms.isChecked(),
            "show_hotspot": self.thermal_show_hotspot.isChecked(),
            "show_material_legend": self.thermal_show_material_legend.isChecked(),
        }

    def _nodal_display_context(self, region_id: str) -> dict[str, object]:
        material_names = {
            item.material_id: item.name for item in self.project.thermal_design.materials
        }
        region = next(
            (item for item in self.project.thermal_design.regions if item.region_id == region_id),
            None,
        )
        template = next(
            (
                item for item in self.project.thermal_design.templates
                if region is not None and item.template_id == region.template_id
            ),
            None,
        )
        values = dict(vars(template)) if template is not None else {}
        if region is not None:
            values.update(dict(region.overrides or {}))
        linked_section = cross_section_for_region(self.project, region_id)
        if linked_section is not None:
            geometry = linked_section.channel_geometry
            active_circuits = {
                item.circuit_id for item in linked_section.physical_cables if item.active
            }
            return {
                "cross_section_id": linked_section.cross_section_id,
                "installation_type": linked_section.installation_type,
                "geometry_source": "Kablo-Kanal üretim bağlı fiziksel kesit",
                "cable_outer_diameter_m": self.project.cable.overall_diameter_mm / 1000.0,
                "physical_cable_count": sum(1 for item in linked_section.physical_cables if item.active),
                "physical_circuit_count": len(active_circuits),
                "trench_center_x_m": float(geometry.center_x_m),
                "trench_width_m": float(geometry.trench_width_m),
                "trench_depth_m": float(geometry.trench_depth_m),
                "trench_side_slope_h_to_v": float(geometry.side_slope_h_to_v),
                "bedding_thickness_m": float(geometry.bedding_thickness_m),
                "thermal_backfill_height_m": float(geometry.thermal_backfill_height_m),
                "selected_fill_thickness_m": float(geometry.selected_fill_thickness_m),
                "surface_layer_thickness_m": float(geometry.surface_layer_thickness_m),
                "cover_slab_enabled": bool(geometry.cover_slab_enabled),
                "cover_slab_width_m": float(geometry.cover_slab_width_m),
                "cover_slab_thickness_m": float(geometry.cover_slab_thickness_m),
                "cover_slab_depth_m": float(geometry.cover_slab_depth_m),
                "duct_bank_width_m": float(geometry.duct_bank_width_m),
                "duct_bank_height_m": float(geometry.duct_bank_height_m),
                "duct_slots": tuple(
                    {
                        "slot_id": item.slot_id,
                        "x_m": float(item.x_m),
                        "depth_m": float(item.depth_m),
                        "inner_diameter_m": float(item.inner_diameter_m),
                        "outer_diameter_m": float(item.outer_diameter_m),
                    }
                    for item in linked_section.duct_slots if item.active
                ),
                "groundwater_depth_m": values.get("groundwater_depth_m", 99.0),
                "surface_boundary_type": values.get("surface_boundary_type", ""),
                "surface_temperature_c": values.get("surface_temperature_c", 0.0),
                "deep_soil_temperature_c": values.get("deep_soil_temperature_c", 0.0),
                "material_names": material_names,
            }
        if region is None:
            return {
                "cable_outer_diameter_m": self.project.cable.overall_diameter_mm / 1000.0,
                "material_names": material_names,
            }
        return {
            "template_id": region.template_id,
            "data_state": region.data_state,
            "source_reference": region.source_reference or (template.source_reference if template else ""),
            "cable_outer_diameter_m": self.project.cable.overall_diameter_mm / 1000.0,
            "trench_width_m": values.get("trench_width_m", 0.0),
            "trench_depth_m": values.get("trench_depth_m", 0.0),
            "groundwater_depth_m": values.get("groundwater_depth_m", 99.0),
            "surface_boundary_type": values.get("surface_boundary_type", ""),
            "surface_temperature_c": values.get("surface_temperature_c", 0.0),
            "deep_soil_temperature_c": values.get("deep_soil_temperature_c", 0.0),
            "material_names": material_names,
        }

    def _populate_thermal_review_workspace(self) -> None:
        result = self.nodal_thermal_result
        self._refresh_thermal_result_selectors()
        if result is None:
            self.current_nodal_review_key = None
            self.thermal_selected_summary.setText(
                "2D nodal çözüm henüz çalıştırılmadı. Termal Güzergâh Girdileri ekranında "
                "bölgeleri/kesitleri tanımlayın ve Tüm Bölgeleri 2D Çöz komutunu kullanın."
            )
            self.thermal_view.scene_obj.clear()
            self.thermal_view._draw_empty_thermal()
            return
        active = result.active
        self._show_nodal_result(
            active.scenario_id, active.solution_scope_id, active.critical_region_id,
            sync_selection=True,
        )

    def _show_nodal_result(
        self,
        scenario_id: str,
        scope_id: str,
        region_id: str,
        *,
        sync_selection: bool = False,
        sync_selectors: bool = True,
    ) -> None:
        result = self.nodal_thermal_result
        if result is None:
            return
        region = find_nodal_region_result(result, scenario_id, region_id, scope_id)
        scenario = result.scope_result(scenario_id, scope_id)
        if region is None or scenario is None:
            return
        self.current_nodal_review_key = (scenario_id, scope_id, region_id)
        energized = ", ".join(region.energized_circuit_ids) or "—"
        interaction_text = self._nodal_interaction_comparison_text(
            scenario_id, scope_id, region_id, region
        )
        self.thermal_selected_summary.setText(
            f"{scenario.scenario_name} | {scenario.solution_scope_name} | "
            f"{region.region_id} · {region.region_name} | "
            f"{region.start_m:.1f}–{region.end_m:.1f} m | {region.installation_type} | "
            f"devre {region.active_circuit_count}/{region.present_circuit_count} enerjili ({energized}) | "
            f"Iamp={region.ampacity_per_cable_a:.1f} A | "
            f"Tmax={region.maximum_conductor_temperature_c:.2f} °C | {region.status}"
            f"{interaction_text}"
        )
        self.thermal_view.draw_nodal_thermal(
            region,
            scenario_name=f"{scenario.scenario_name} · {scenario.solution_scope_name}",
            display_options=self._thermal_display_options(),
            context=self._nodal_display_context(region_id),
        )
        if sync_selection:
            self._sync_nodal_review_selection(scenario_id, scope_id, region_id)
        if sync_selectors:
            self._sync_thermal_result_selectors(scenario_id, scope_id, region_id)

    def _nodal_interaction_comparison_text(
        self,
        scenario_id: str,
        scope_id: str,
        region_id: str,
        selected_region,
    ) -> str:
        """Describe mutual circuit heating without changing production status.

        The comparison is made at the same scenario reference current and the
        same physical channel.  Isolated solutions retain the neighbouring
        cable bodies as passive materials, so the delta represents electrical
        co-loading rather than removal of geometry.
        """

        study = self.nodal_thermal_result
        if study is None or selected_region.present_circuit_count < 2:
            return ""

        def region_for(scope) -> object | None:
            if scope is None:
                return None
            return next((item for item in scope.regions if item.region_id == region_id), None)

        all_scope = study.scope_result(scenario_id, "ALL_CIRCUITS_COMBINED")
        all_region = region_for(all_scope)
        isolated_regions = [
            item
            for scope in study.scopes_for_scenario(scenario_id)
            if scope.solution_scope_id.startswith("ISOLATED::")
            for item in scope.regions
            if item.region_id == region_id
        ]
        if not isolated_regions:
            return ""

        combined_region = all_region
        if combined_region is None and selected_region.active_circuit_count == selected_region.present_circuit_count:
            combined_region = selected_region
        if combined_region is None:
            return ""

        if scope_id.startswith("ISOLATED::") or selected_region.active_circuit_count == 1:
            baseline = selected_region
        else:
            baseline = max(
                isolated_regions,
                key=lambda item: item.maximum_conductor_temperature_c,
            )
        delta_t = (
            combined_region.maximum_conductor_temperature_c
            - baseline.maximum_conductor_temperature_c
        )
        delta_i = combined_region.ampacity_per_cable_a - baseline.ampacity_per_cable_a
        return (
            f" | Karşılıklı devre etkisi: ΔT={delta_t:+.2f} °C, "
            f"ΔIamp={delta_i:+.1f} A/kablo"
        )

    def _sync_nodal_review_selection(self, scenario_id: str, scope_id: str, region_id: str) -> None:
        key = (scenario_id, scope_id, region_id)
        self.nodal_result_table.blockSignals(True)
        for row in range(self.nodal_result_table.rowCount()):
            item = self.nodal_result_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == key:
                self.nodal_result_table.selectRow(row)
                break
        self.nodal_result_table.blockSignals(False)

    def _sync_thermal_result_selectors(self, scenario_id: str, scope_id: str, region_id: str) -> None:
        if not hasattr(self, "thermal_scenario_selector"):
            return
        scenario_index = self.thermal_scenario_selector.findData(scenario_id)
        if scenario_index >= 0 and self.thermal_scenario_selector.currentIndex() != scenario_index:
            self.thermal_scenario_selector.blockSignals(True)
            self.thermal_scenario_selector.setCurrentIndex(scenario_index)
            self.thermal_scenario_selector.blockSignals(False)
            self._populate_thermal_scope_selector(scenario_id, scope_id, region_id)
        scope_index = self.thermal_scope_selector.findData(scope_id)
        if scope_index >= 0 and self.thermal_scope_selector.currentIndex() != scope_index:
            self.thermal_scope_selector.blockSignals(True)
            self.thermal_scope_selector.setCurrentIndex(scope_index)
            self.thermal_scope_selector.blockSignals(False)
            self._populate_thermal_region_selector(scenario_id, scope_id, region_id)
        region_index = self.thermal_region_selector.findData(region_id)
        if region_index >= 0 and self.thermal_region_selector.currentIndex() != region_index:
            self.thermal_region_selector.blockSignals(True)
            self.thermal_region_selector.setCurrentIndex(region_index)
            self.thermal_region_selector.blockSignals(False)

    def _redraw_selected_nodal_result(self, *_args) -> None:
        if self.current_nodal_review_key:
            self._show_nodal_result(*self.current_nodal_review_key)

    def _select_critical_nodal_region(self) -> None:
        result = self.nodal_thermal_result
        if result is None:
            QMessageBox.information(self, "2D termal sonuç", "Önce 2D nodal termal çözümü çalıştırın.")
            return
        scenario_id = self.thermal_scenario_selector.currentData() or result.active.scenario_id
        scope_id = self.thermal_scope_selector.currentData() or result.active.solution_scope_id
        scope = result.scope_result(str(scenario_id), str(scope_id)) or result.active
        self._show_nodal_result(
            scope.scenario_id, scope.solution_scope_id, scope.critical_region_id,
            sync_selection=True,
        )

    def _run_selected_mesh_convergence(self) -> None:
        study = self.nodal_thermal_result
        if study is None or self.current_nodal_review_key is None:
            QMessageBox.information(self, "Mesh yakınsaması", "Önce bir 2D bölge sonucu seçin.")
            return
        scenario_id, scope_id, region_id = self.current_nodal_review_key
        region = find_nodal_region_result(study, scenario_id, region_id, scope_id)
        scope = study.scope_result(scenario_id, scope_id)
        iec_scenario = next(
            (item for item in study.iec_route_result.scenarios if item.scenario_id == scenario_id),
            None,
        )
        iec_region = next(
            (item for item in iec_scenario.regions if item.region_id == region_id),
            None,
        ) if iec_scenario else None
        if region is None or scope is None or iec_region is None:
            QMessageBox.warning(self, "Mesh yakınsaması", "Seçili bölgenin IEC/termal kapsam sonucu bulunamadı.")
            return
        try:
            check = check_mesh_convergence(
                self.project,
                region_id,
                region.design_current_per_cable_a,
                region.active_circuit_count,
                region.regional_lambda1,
                iec_region.iec,
                tolerance_percent=1.0,
                energized_circuit_ids=region.energized_circuit_ids,
                solution_scope_id=scope.solution_scope_id,
                solution_scope_name=scope.solution_scope_name,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Mesh yakınsama hatası", str(exc))
            return
        self.last_mesh_convergence[(scenario_id, scope_id, region_id)] = check
        if scope_id == "SCENARIO_COMBINED" and self.nodal_thermal_result is not None:
            mesh_map = {
                (sid, rid): value
                for (sid, local_scope, rid), value in self.last_mesh_convergence.items()
                if local_scope == "SCENARIO_COMBINED"
            }
            validation = evaluate_thermal_method_authority(
                self.project, self.nodal_thermal_result, mesh_checks=mesh_map
            )
            self.nodal_thermal_result = replace(
                self.nodal_thermal_result, method_validation=validation
            )
            cache_thermal_method_authority(self.project, validation)
        QMessageBox.information(
            self,
            "Mesh yakınsama sonucu",
            f"{region_id} · {scope.solution_scope_name} · {'PASS' if check.passed else 'FAIL'}\n"
            f"Kaba mesh: {check.coarse_cells} hücre, Tmax={check.coarse_max_temperature_c:.3f} °C\n"
            f"İnce mesh: {check.refined_cells} hücre, Tmax={check.refined_max_temperature_c:.3f} °C\n"
            f"Sıcaklık farkı: {check.difference_c:.4f} °C (%{check.difference_percent:.4f})\n"
            f"Ampacity farkı: {check.ampacity_difference_a:+.3f} A (%{check.ampacity_difference_percent:+.4f})",
        )

    def _build_first_design_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        hint = QLabel(
            "İlk tasarım iterasyonu yalnız sistem/yük, güzergâh, kurulum varsayımları ve kaba kablo adaylarını değerlendirir. "
            "Program öneri üretir; kullanıcı açıkça Projeye Ata demeden proje kablosu değişmez. IEC 60853, SVL ve ayrıntılı "
            "arıza/bonding hesapları kendi ileri aşamalarında çalıştırılır."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("background:#eaf0f5; padding:8px; border-radius:4px;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        wizard_btn = QPushButton("Yeni Tasarım Sihirbazı")
        refresh_btn = QPushButton("Adayları Yenile")
        apply_btn = QPushButton("Seçili Adayı Projeye Ata")
        iteration_btn = QPushButton("İlk Tasarım İterasyonu")
        wizard_btn.clicked.connect(self.run_project_wizard)
        refresh_btn.clicked.connect(self._regenerate_first_design_candidates)
        apply_btn.clicked.connect(self._apply_selected_first_design_candidate)
        iteration_btn.clicked.connect(self.run_first_design_iteration)
        for button in (wizard_btn, refresh_btn, apply_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(iteration_btn)
        layout.addLayout(buttons)

        self.first_design_state = QLabel("Aday seçimi projeyi değiştirmez.")
        self.first_design_state.setWordWrap(True)
        self.first_design_state.setStyleSheet(
            "font-weight:700; padding:8px; background:#fff8df; border:1px solid #d8bd58; border-radius:4px;"
        )
        layout.addWidget(self.first_design_state)

        splitter = QSplitter(Qt.Vertical)
        self.design_basis_table = QTableWidget(0, 3)
        self.design_basis_table.setHorizontalHeaderLabels(["Tasarım girdisi / sonuç", "Değer", "Kaynak / durum"])
        self.design_basis_table.horizontalHeader().setStretchLastSection(True)
        self.design_basis_table.setAlternatingRowColors(True)
        self.design_basis_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.first_candidate_table = QTableWidget(0, 10)
        self.first_candidate_table.setHorizontalHeaderLabels([
            "Seçim", "ID", "Aday", "Malzeme", "Kesit", "Kablo/faz", "Ön ampacity", "Marj", "Ön kayıp", "Olgunluk"
        ])
        self.first_candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.first_candidate_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.first_candidate_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.first_candidate_table.setAlternatingRowColors(True)
        self.first_candidate_table.horizontalHeader().setStretchLastSection(True)
        self.first_candidate_table.itemSelectionChanged.connect(self._first_design_candidate_selection_changed)
        splitter.addWidget(self.design_basis_table)
        splitter.addWidget(self.first_candidate_table)
        splitter.setSizes([300, 350])
        layout.addWidget(splitter, 1)
        return container

    def _refresh_first_design(self) -> None:
        basis = self.project.design_basis
        try:
            load = apply_load_calculation(basis)
            if self.project.cable_application.application_status.startswith("APPLIED"):
                self.project.cable.design_current_a = load.design_current_per_circuit_a / max(1, self.project.cable.parallel_cables_per_phase)
            load_status = "Hesaplandı"
        except FirstDesignInputError as exc:
            load = None
            load_status = f"Eksik/hatalı: {exc}"

        rows = [
            ("Sistem gerilimi", f"{basis.system_voltage_kv:g} kV", "Kullanıcı / proje"),
            ("Frekans", f"{basis.frequency_hz:g} Hz", "Kullanıcı / proje"),
            ("Devreler", f"{basis.circuit_count} toplam / {basis.active_circuit_count} aktif", "Kullanıcı / proje"),
            ("Yük giriş biçimi", basis.load_input_mode, "Kullanıcı"),
            ("Normal toplam akım", f"{basis.normal_total_current_a:.2f} A" if load else "—", load_status),
            ("Normal devre başı", f"{basis.normal_current_per_active_circuit_a:.2f} A" if load else "—", load_status),
            ("N-1 devre başı", f"{basis.n1_current_per_circuit_a:.2f} A" if load else "—", "N-1" if basis.n_minus_one_enabled else "N-1 kapalı"),
            ("Marjlı tasarım akımı", f"{basis.design_current_per_circuit_a:.2f} A" if load else "—", "Büyüme + tasarım marjı"),
            ("İlk gerilim sınıfı", basis.suggested_voltage_class, "Şartname/topraklama teyidi gerekli"),
            ("Güzergâh", f"{basis.total_route_length_m:.1f} m · {basis.route_input_mode}", "DXF/çizim/ön uzunluk"),
            ("Kurulum profili", basis.installation_profile, "İlk yerleşim"),
            ("Toprak ρth", f"{basis.soil_thermal_resistivity_km_w:g} K·m/W", basis.soil_thermal_value_source),
            ("Seçili başlangıç kablosu", basis.selected_candidate_id or "Atanmadı", basis.initial_selection_mode),
            ("Tasarım olgunluğu", self.project.design_progress.maturity_level, "Sistem"),
        ]
        self.design_basis_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.design_basis_table.setItem(row, col, cell)
        self.design_basis_table.resizeColumnsToContents()
        self.design_basis_table.horizontalHeader().setStretchLastSection(True)

        self.first_candidate_table.setRowCount(len(basis.candidates))
        assigned_id = self.project.cable_application.selected_candidate_id if self.project.cable_application.application_status.startswith("APPLIED") else ""
        recommended_id = self.project.cable_application.selected_candidate_id if self.project.cable_application.last_iteration_status in {"READY", "CONDITIONAL_READY"} else ""
        for row, candidate in enumerate(basis.candidates):
            marker = "✓ ATANMIŞ" if candidate.candidate_id == assigned_id else ("★ ÖNERİLEN" if candidate.candidate_id == recommended_id else "")
            values = [
                marker, candidate.candidate_id, candidate.label, candidate.conductor_material,
                f"{candidate.conductor_area_mm2:g} mm²", str(candidate.cables_per_phase),
                f"{candidate.estimated_ampacity_a:.1f} A", f"{candidate.estimated_margin_a:.1f} A",
                f"{candidate.estimated_loss_kw_km:.2f} kW/km", candidate.maturity_level,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                if marker == "✓ ATANMIŞ":
                    cell.setBackground(QColor("#e8f7ed"))
                elif marker == "★ ÖNERİLEN":
                    cell.setBackground(QColor("#fff8df"))
                self.first_candidate_table.setItem(row, col, cell)
        self.first_candidate_table.resizeColumnsToContents()
        self.first_candidate_table.horizontalHeader().setStretchLastSection(True)

        if self.project.cable_application.application_status.startswith("APPLIED"):
            self.first_design_state.setText(
                f"✓ PROJEYE ATANMIŞ KABLO: {self.project.cable.name}\n"
                "Bağlı hesaplar kablo atamasına göre çalışır. Projede kaydedilmemiş değişiklik varsa ana başlıkta * görünür."
            )
            self.first_design_state.setStyleSheet(
                "font-weight:700; padding:8px; background:#e8f7ed; border:1px solid #70b184; border-radius:4px;"
            )
        elif recommended_id:
            self.first_design_state.setText(
                f"★ ÖNERİLEN ADAY: {recommended_id}\nProjeye henüz atanmadı. Satırı seçip Seçili Adayı Projeye Ata komutunu kullanın."
            )
            self.first_design_state.setStyleSheet(
                "font-weight:700; padding:8px; background:#fff8df; border:1px solid #d8bd58; border-radius:4px;"
            )
        else:
            self.first_design_state.setText("Aday seçimi projeyi değiştirmez. Önce İlk Tasarım İterasyonu ile öneri üretin.")

        p = self.project.design_progress
        status_map = {
            "COMPLETE": "✓", "PRELIMINARY": "◐", "NOT_RUN": "○", "NOT_READY": "○",
            "STALE": "↻", "MISSING": "△", "CONDITIONAL": "△", "PASS": "✓",
        }
        items = [
            ("Sistem/Yük", p.system_load), ("Güzergâh", p.route), ("Kablo", p.cable),
            ("Termal", p.thermal), ("Bonding", p.bonding), ("Arıza/EPR", p.fault_epr),
            ("SVL", p.svl), ("Nihai Tasarım", p.final_design),
        ]
        self.progress_label.setText("   ".join(f"{status_map.get(value, '•')} {name}: {value}" for name, value in items))

    def _first_design_candidate_selection_changed(self) -> None:
        row = self.first_candidate_table.currentRow()
        candidates = self.project.design_basis.candidates
        if row < 0 or row >= len(candidates):
            return
        candidate = candidates[row]
        self.first_design_state.setText(
            f"SEÇİLİ ADAY — projeye henüz atanmadı\n{candidate.label} · "
            f"{candidate.cables_per_phase} kablo/faz · ön marj {candidate.estimated_margin_a:+.1f} A"
        )
        self.first_design_state.setStyleSheet(
            "font-weight:700; padding:8px; background:#fff8df; border:1px solid #d8bd58; border-radius:4px;"
        )

    def _regenerate_first_design_candidates(self) -> None:
        try:
            generate_generic_candidates(self.project.design_basis)
            self.project.cable_application.last_iteration_status = "NOT_RUN"
            self.project.cable_application.last_iteration_trace = [
                "Jenerik adaylar yeniden üretildi; ön eleme henüz çalıştırılmadı."
            ]
            self.project.design_progress.cable = "PRELIMINARY"
            self.project.design_progress.maturity_level = MATURITY_LEVEL_1
            self._mark_dirty()
            self._refresh_first_design()
            self._show_workspace_widget(self.first_design_widget, "İlk Tasarım")
        except FirstDesignInputError as exc:
            QMessageBox.warning(self, "Kablo adayları", str(exc))

    def _apply_selected_first_design_candidate(self) -> None:
        row = self.first_candidate_table.currentRow()
        candidates = self.project.design_basis.candidates
        if row < 0 or row >= len(candidates):
            QMessageBox.information(self, "Kablo adayı", "Önce aday tablosundan bir satır seçin.")
            return
        candidate = candidates[row]
        if not self.project.route_sections:
            QMessageBox.warning(self, "Güzergâh gerekli", "Kablo atanmadan önce en az bir güzergâh bölümü tanımlayın.")
            return
        affected = (
            "Bu işlem proje kablosunu değiştirir ve aşağıdaki sonuçları yeniden hesaplanacak olarak işaretler:\n"
            "• Elektriksel ön eleme ve gerilim düşümü\n"
            "• IEC 60287 ve 2D termal\n"
            "• Bonding, Arıza/EPR, SVL ve IEC 60853\n"
            "• BOQ/BOM/RFQ"
        )
        answer = QMessageBox.question(
            self,
            "Seçili adayı projeye ata",
            f"{candidate.label}\n{candidate.cables_per_phase} kablo/faz\n\n{affected}\n\nProjeye atansın mı?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        apply_candidate_to_project(candidate, self.project.design_basis, self.project.cable)
        self.project.cable = create_project_snapshot(self.project.cable, f"GENERIC-{candidate.candidate_id}")
        self.project.cable_application.selected_candidate_id = candidate.candidate_id
        self.project.cable_application.selected_catalog_record_id = ""
        self.project.cable_application.applied_snapshot_id = self.project.cable.snapshot_id
        self.project.cable_application.applied_snapshot_hash = self.project.cable.snapshot_hash
        self.project.cable_application.assignments = [
            RouteCableAssignment(
                assignment_id=f"ASSIGN-{index:03d}",
                route_section_name=section.name,
                cable_snapshot_id=self.project.cable.snapshot_id,
                parallel_cables_per_phase=candidate.cables_per_phase,
                active=True,
                notes="İlk tasarım adayından açık kullanıcı onayıyla atandı.",
            )
            for index, section in enumerate(self.project.route_sections, 1)
        ]
        self.project.cable_application.application_status = "APPLIED_CONDITIONAL"
        self.project.cable_application.last_iteration_status = "READY"
        self.project.cable_application.last_iteration_trace = [
            f"{candidate.label} kullanıcı onayıyla projeye atandı.",
            "İlk elektriksel ön eleme çalıştırılabilir.",
        ]
        self.project.design_progress.cable = "CONDITIONAL"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_1
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_all()
        QMessageBox.information(
            self,
            "Kablo projeye atandı",
            f"{candidate.label} tüm aktif güzergâh bölümlerine atandı.\n"
            "Proje değiştirildi; diske yazmak için Kaydet komutunu kullanın.\n"
            "Bağlı hesaplar yeniden hesaplanacak olarak işaretlendi.",
        )

    def _build_route_table_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        hint = QLabel(
            "Güzergâh bölümleri özet olarak gösterilir. Hücreleri tek tek doldurmak yerine "
            "Bölüm Ekle / Düzenle formunu kullanın. Hazır projelerde kaynak güzergâhı önce gözden geçirip kabul edin."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("background:#eaf0f5; padding:8px; border-radius:4px;")
        layout.addWidget(hint)

        self.route_approval_label = QLabel()
        self.route_approval_label.setWordWrap(True)
        self.route_approval_label.setStyleSheet("font-weight:700; padding:7px; background:#fff8df; border:1px solid #d8bd58;")
        layout.addWidget(self.route_approval_label)

        self.route_table = QTableWidget(0, 11)
        self.route_table.setHorizontalHeaderLabels(
            ["Bölüm", "Uzunluk [m]", "Tip", "Derinlik [m]", "Toprak ρth [K·m/W]", "Kesit", "Ortam [°C]", "T4 modu", "Faz aralığı [m]", "Manuel T4", "Not"]
        )
        self.route_table.horizontalHeader().setStretchLastSection(True)
        self.route_table.setAlternatingRowColors(True)
        self.route_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.route_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.route_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.route_table.itemDoubleClicked.connect(lambda *_: self._edit_route_section())

        # Komutlar tablonun ALTINDA kaybolmamalıdır. Özellikle StageHost veya
        # küçük monitörlerde kullanıcı önce Ekle/Düzenle/Sil/Kabul Et satırını
        # görür; tablo kalan yüksekliği kullanır.
        buttons = QHBoxLayout()
        add_btn = QPushButton("Bölüm Ekle")
        edit_btn = QPushButton("Seçili Bölümü Düzenle")
        remove_btn = QPushButton("Seçili Bölümü Sil")
        accept_btn = QPushButton("Mevcut Güzergâhı Kabul Et")
        add_btn.clicked.connect(self._add_route_section)
        edit_btn.clicked.connect(self._edit_route_section)
        remove_btn.clicked.connect(self._remove_route_section)
        accept_btn.clicked.connect(self._accept_route_source)
        buttons.addWidget(add_btn)
        buttons.addWidget(edit_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(accept_btn)
        layout.addLayout(buttons)
        layout.addWidget(self.route_table, 1)
        return container

    def _build_thermal_route_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        hint = QLabel(
            "Termal güzergâh; zemin, termal dolgu, yüzey, yeraltı suyu ve kurulum değişimlerini chainage bazında tutar. "
            "Şablonlar tekrarlanan kesitleri tanımlar; bölge override değerleri yalnız yerel farkları değiştirir. "
            "DESIGN / TESTED / AS_BUILT veri katmanları birbirine karıştırılmaz."
        )
        hint.setWordWrap(True)

        commands = QHBoxLayout()
        add_btn = QPushButton("Bölge Ekle")
        split_btn = QPushButton("Seçili Bölgeyi Böl")
        remove_btn = QPushButton("Seçili Bölgeyi Sil")
        validate_btn = QPushButton("Bölgeleri Doğrula")
        run_btn = QPushButton("Bölgesel IEC 60287 Çalıştır")
        nodal_btn = QPushButton("2D Nodal Çalıştır")
        iec_results_btn = QPushButton("IEC 60287 Sonuçlarını Aç")
        results_btn = QPushButton("Termal Sonuçları Aç")
        add_btn.clicked.connect(self._add_thermal_region)
        split_btn.clicked.connect(self._split_thermal_region)
        remove_btn.clicked.connect(self._remove_thermal_region)
        validate_btn.clicked.connect(self.validate_thermal_route)
        run_btn.clicked.connect(self.run_thermal_route_analysis)
        nodal_btn.clicked.connect(self.run_nodal_thermal_analysis)
        iec_results_btn.clicked.connect(lambda: self.show_results_dialog(self.iec_result_table))
        results_btn.clicked.connect(
            lambda: self.show_results_dialog(
                self.nodal_result_table if self.nodal_thermal_result is not None else self.thermal_route_result_table
            )
        )
        for button in (add_btn, split_btn, remove_btn):
            commands.addWidget(button)
        commands.addStretch(1)
        commands.addWidget(iec_results_btn)
        commands.addWidget(results_btn)
        commands.addWidget(validate_btn)
        commands.addWidget(run_btn)
        commands.addWidget(nodal_btn)

        self.thermal_editor_tabs = QTabWidget()
        self.thermal_region_table = QTableWidget(0, 11)
        self.thermal_region_table.setHorizontalHeaderLabels([
            "ID", "Bölge", "Başlangıç [m]", "Bitiş [m]", "Uzunluk [m]", "Kesit şablonu",
            "Geçiş", "Etkin", "Veri katmanı", "Kaynak", "Not"
        ])
        self.thermal_region_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.thermal_region_table.setAlternatingRowColors(True)
        self.thermal_region_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_region_table.itemChanged.connect(self._thermal_region_table_changed)

        self.thermal_template_table = QTableWidget(0, 20)
        self.thermal_template_table.setHorizontalHeaderLabels([
            "ID", "Şablon", "Kurulum", "Yerleşim", "Derinlik", "Faz aralığı", "Devre aralığı",
            "Hendek genişliği", "Hendek derinliği", "Bedding", "Yan dolgu", "Kablo üstü",
            "Doğal zemin", "Bedding malz.", "Yan dolgu malz.", "Üst dolgu malz.",
            "Dış model", "Manuel T4", "Veri katmanı", "Not"
        ])
        self.thermal_template_table.setAlternatingRowColors(True)
        self.thermal_template_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_template_table.itemChanged.connect(self._thermal_template_table_changed)

        self.thermal_material_table = QTableWidget(0, 16)
        self.thermal_material_table.setHorizontalHeaderLabels([
            "ID", "Malzeme", "Kategori", "ρth [K·m/W]", "Kuru yoğunluk", "Yaş yoğunluk",
            "Nem [%]", "Cv [MJ/m³K]", "Kompaksiyon [%]", "Kritik kuruma T [°C]", "Kuru durum ρth",
            "Veri katmanı", "Kaynak tipi", "Güven", "Kaynak/rapor", "Not"
        ])
        self.thermal_material_table.setAlternatingRowColors(True)
        self.thermal_material_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_material_table.itemChanged.connect(self._thermal_material_table_changed)

        self.nodal_settings_table = QTableWidget(0, 22)
        self.nodal_settings_table.setHorizontalHeaderLabels([
            "Şablon ID", "Etkin", "Alan yarı genişlik", "Alan derinlik", "Temel adım",
            "İnce adım", "İnce yarıçap", "Maks. hücre", "Yüzey sınırı", "Yüzey T",
            "Derin zemin T", "h yüzey", "Kablo k", "Yeraltı suyu k çarpanı",
            "Duct iç çap", "Duct dış çap", "Bank genişlik", "Bank yükseklik", "Paralel kablo aralığı",
            "Duct ρth override", "Duct içi ρth override", "Grout ρth override"
        ])
        self.nodal_settings_table.setAlternatingRowColors(True)
        self.nodal_settings_table.horizontalHeader().setStretchLastSection(True)
        self.nodal_settings_table.itemChanged.connect(self._nodal_settings_table_changed)

        template_page = QWidget()
        template_layout = QVBoxLayout(template_page)
        template_buttons = QHBoxLayout()
        add_template_btn = QPushButton("Şablon Ekle")
        remove_template_btn = QPushButton("Seçili Şablonu Sil")
        add_template_btn.clicked.connect(self._add_thermal_template)
        remove_template_btn.clicked.connect(self._remove_thermal_template)
        template_buttons.addWidget(add_template_btn)
        template_buttons.addWidget(remove_template_btn)
        template_buttons.addStretch(1)
        template_layout.addLayout(template_buttons)
        template_layout.addWidget(self.thermal_template_table)

        material_page = QWidget()
        material_layout = QVBoxLayout(material_page)
        material_buttons = QHBoxLayout()
        add_material_btn = QPushButton("Malzeme Ekle")
        remove_material_btn = QPushButton("Seçili Malzemeyi Sil")
        add_material_btn.clicked.connect(self._add_thermal_material)
        remove_material_btn.clicked.connect(self._remove_thermal_material)
        material_buttons.addWidget(add_material_btn)
        material_buttons.addWidget(remove_material_btn)
        material_buttons.addStretch(1)
        material_layout.addLayout(material_buttons)
        material_layout.addWidget(self.thermal_material_table)

        nodal_page = QWidget()
        nodal_layout = QVBoxLayout(nodal_page)
        nodal_hint = QLabel(
            "2D ağ ve sınır koşulları şablon bazındadır. Alan sınırları kablolardan yeterince uzak tutulmalı; "
            "mesh yakınsaması ve enerji dengesi sonuçlarda kontrol edilir."
        )
        nodal_hint.setWordWrap(True)
        nodal_layout.addWidget(nodal_hint)
        nodal_layout.addWidget(self.nodal_settings_table)

        self.thermal_editor_tabs.addTab(self.thermal_region_table, "Termal Bölgeler")
        self.thermal_editor_tabs.addTab(template_page, "Kesit Şablonları")
        self.thermal_editor_tabs.addTab(material_page, "Malzeme Kütüphanesi")
        self.thermal_editor_tabs.addTab(nodal_page, "2D Nodal Ayarlar")

        layout.addWidget(hint)
        layout.addLayout(commands)
        layout.addWidget(self.thermal_editor_tabs, 1)
        return container

    def _refresh_thermal_route_tables(self) -> None:
        design = self.project.thermal_design
        self.thermal_region_table.blockSignals(True)
        self.thermal_region_table.setRowCount(len(design.regions))
        for row, region in enumerate(design.regions):
            values = [
                region.region_id, region.name, f"{region.start_m:g}", f"{region.end_m:g}",
                f"{region.length_m:g}", region.template_id, region.transition_type,
                self._bool_text(region.enabled), region.data_state, region.source_reference, region.notes,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 4:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.thermal_region_table.setItem(row, col, item)
        self.thermal_region_table.resizeColumnsToContents()
        self.thermal_region_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_region_table.blockSignals(False)

        self.thermal_template_table.blockSignals(True)
        self.thermal_template_table.setRowCount(len(design.templates))
        for row, template in enumerate(design.templates):
            values = [
                template.template_id, template.name, template.installation_type, template.arrangement,
                f"{template.burial_depth_m:g}", f"{template.phase_spacing_m:g}", f"{template.circuit_spacing_m:g}",
                f"{template.trench_width_m:g}", f"{template.trench_depth_m:g}", f"{template.bedding_thickness_m:g}",
                f"{template.side_backfill_width_m:g}", f"{template.cable_cover_height_m:g}",
                template.native_soil_material_id, template.bedding_material_id, template.side_backfill_material_id,
                template.cable_cover_material_id, template.external_thermal_mode, f"{template.manual_t4_km_w:g}",
                template.data_state, template.notes,
            ]
            for col, value in enumerate(values):
                self.thermal_template_table.setItem(row, col, QTableWidgetItem(value))
        self.thermal_template_table.resizeColumnsToContents()
        self.thermal_template_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_template_table.blockSignals(False)

        self.nodal_settings_table.blockSignals(True)
        self.nodal_settings_table.setRowCount(len(design.templates))
        for row, template in enumerate(design.templates):
            values = [
                template.template_id, self._bool_text(template.nodal_enabled),
                f"{template.nodal_domain_half_width_m:g}", f"{template.nodal_domain_depth_m:g}",
                f"{template.nodal_base_step_m:g}", f"{template.nodal_refined_step_m:g}",
                f"{template.nodal_refinement_radius_m:g}", str(template.nodal_max_cells),
                template.surface_boundary_type, f"{template.surface_temperature_c:g}",
                f"{template.deep_soil_temperature_c:g}", f"{template.surface_heat_transfer_w_m2k:g}",
                f"{template.cable_effective_conductivity_w_mk:g}",
                f"{template.groundwater_conductivity_multiplier:g}",
                f"{template.duct_inner_diameter_m:g}", f"{template.duct_outer_diameter_m:g}",
                f"{template.duct_bank_width_m:g}", f"{template.duct_bank_height_m:g}",
                f"{template.parallel_cable_spacing_m:g}",
                f"{template.duct_thermal_resistivity_km_w:g}",
                f"{template.duct_fill_thermal_resistivity_km_w:g}",
                f"{template.grout_thermal_resistivity_km_w:g}",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.nodal_settings_table.setItem(row, col, item)
        self.nodal_settings_table.resizeColumnsToContents()
        self.nodal_settings_table.horizontalHeader().setStretchLastSection(True)
        self.nodal_settings_table.blockSignals(False)

        self.thermal_material_table.blockSignals(True)
        self.thermal_material_table.setRowCount(len(design.materials))
        for row, material in enumerate(design.materials):
            values = [
                material.material_id, material.name, material.category, f"{material.thermal_resistivity_km_w:g}",
                f"{material.dry_density_kg_m3:g}", f"{material.wet_density_kg_m3:g}",
                f"{material.moisture_percent:g}", f"{material.volumetric_heat_capacity_mj_m3k:g}",
                f"{material.compaction_percent:g}", f"{material.critical_dryout_temperature_c:g}",
                f"{material.dry_state_thermal_resistivity_km_w:g}", material.data_state, material.source_type,
                material.reliability, material.source_reference, material.notes,
            ]
            for col, value in enumerate(values):
                self.thermal_material_table.setItem(row, col, QTableWidgetItem(value))
        self.thermal_material_table.resizeColumnsToContents()
        self.thermal_material_table.horizontalHeader().setStretchLastSection(True)
        self.thermal_material_table.blockSignals(False)

    def _thermal_region_table_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self.project.thermal_design.regions) or col == 4:
            return
        region = self.project.thermal_design.regions[row]
        try:
            if col in {0, 1, 5, 6, 8, 9, 10}:
                attr = {0: "region_id", 1: "name", 5: "template_id", 6: "transition_type", 8: "data_state", 9: "source_reference", 10: "notes"}[col]
                setattr(region, attr, item.text().strip())
            elif col in {2, 3}:
                setattr(region, "start_m" if col == 2 else "end_m", float(item.text()))
            elif col == 7:
                region.enabled = self._parse_bool(item.text())
            self._invalidate_results()
            self._mark_dirty()
            self._refresh_thermal_route_tables()
            self._build_tree()
        except ValueError:
            QMessageBox.warning(self, "Termal bölge", "Sayısal değer veya Evet/Hayır girişi geçersiz.")
            self._refresh_thermal_route_tables()

    def _thermal_template_table_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self.project.thermal_design.templates):
            return
        template = self.project.thermal_design.templates[row]
        text_fields = {
            0: "template_id", 1: "name", 2: "installation_type", 3: "arrangement",
            12: "native_soil_material_id", 13: "bedding_material_id", 14: "side_backfill_material_id",
            15: "cable_cover_material_id", 16: "external_thermal_mode", 18: "data_state", 19: "notes",
        }
        numeric_fields = {
            4: "burial_depth_m", 5: "phase_spacing_m", 6: "circuit_spacing_m", 7: "trench_width_m",
            8: "trench_depth_m", 9: "bedding_thickness_m", 10: "side_backfill_width_m",
            11: "cable_cover_height_m", 17: "manual_t4_km_w",
        }
        try:
            if col in text_fields:
                setattr(template, text_fields[col], item.text().strip())
            elif col in numeric_fields:
                setattr(template, numeric_fields[col], float(item.text()))
            self._invalidate_results()
            self._mark_dirty()
            self._refresh_thermal_route_tables()
            self._build_tree()
        except ValueError:
            QMessageBox.warning(self, "Termal kesit şablonu", "Sayısal değer geçersiz.")
            self._refresh_thermal_route_tables()

    def _nodal_settings_table_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self.project.thermal_design.templates) or col == 0:
            return
        template = self.project.thermal_design.templates[row]
        bool_map = {1: "nodal_enabled"}
        text_map = {8: "surface_boundary_type"}
        number_map = {
            2: "nodal_domain_half_width_m", 3: "nodal_domain_depth_m",
            4: "nodal_base_step_m", 5: "nodal_refined_step_m",
            6: "nodal_refinement_radius_m", 7: "nodal_max_cells",
            9: "surface_temperature_c", 10: "deep_soil_temperature_c",
            11: "surface_heat_transfer_w_m2k", 12: "cable_effective_conductivity_w_mk",
            13: "groundwater_conductivity_multiplier", 14: "duct_inner_diameter_m",
            15: "duct_outer_diameter_m", 16: "duct_bank_width_m",
            17: "duct_bank_height_m", 18: "parallel_cable_spacing_m",
            19: "duct_thermal_resistivity_km_w",
            20: "duct_fill_thermal_resistivity_km_w",
            21: "grout_thermal_resistivity_km_w",
        }
        try:
            if col in bool_map:
                setattr(template, bool_map[col], self._parse_bool(item.text()))
            elif col in text_map:
                setattr(template, text_map[col], item.text().strip().upper())
            elif col in number_map:
                value = self._parse_number(item.text())
                setattr(template, number_map[col], int(value) if col == 7 else value)
            self._invalidate_results()
            self._mark_dirty()
        except ValueError:
            QMessageBox.warning(self, "2D nodal ayar", "Sayısal veya Evet/Hayır değeri geçersiz.")
            self._refresh_thermal_route_tables()

    def _thermal_material_table_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self.project.thermal_design.materials):
            return
        material = self.project.thermal_design.materials[row]
        text_fields = {
            0: "material_id", 1: "name", 2: "category", 11: "data_state", 12: "source_type",
            13: "reliability", 14: "source_reference", 15: "notes",
        }
        numeric_fields = {
            3: "thermal_resistivity_km_w", 4: "dry_density_kg_m3", 5: "wet_density_kg_m3",
            6: "moisture_percent", 7: "volumetric_heat_capacity_mj_m3k", 8: "compaction_percent",
            9: "critical_dryout_temperature_c", 10: "dry_state_thermal_resistivity_km_w",
        }
        try:
            if col in text_fields:
                setattr(material, text_fields[col], item.text().strip())
            elif col in numeric_fields:
                setattr(material, numeric_fields[col], float(item.text()))
            self._invalidate_results()
            self._mark_dirty()
            self._refresh_thermal_route_tables()
        except ValueError:
            QMessageBox.warning(self, "Termal malzeme", "Sayısal değer geçersiz.")
            self._refresh_thermal_route_tables()

    def _add_thermal_region(self) -> None:
        design = self.project.thermal_design
        start = max((region.end_m for region in design.regions), default=0.0)
        end = start + 100.0
        design.route_length_m = max(design.route_length_m, end)
        index = len(design.regions) + 1
        template_id = design.templates[0].template_id if design.templates else ""
        design.regions.append(ThermalRegion(f"TR-{index:02}", f"Yeni Termal Bölge {index}", start, end, template_id))
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_all()
        self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh")

    def _split_thermal_region(self) -> None:
        row = self.thermal_region_table.currentRow()
        if row < 0 or row >= len(self.project.thermal_design.regions):
            QMessageBox.information(self, "Termal bölge", "Önce bölünecek bölgeyi seçin.")
            return
        region = self.project.thermal_design.regions[row]
        midpoint = (region.start_m + region.end_m) / 2.0
        split_m, ok = QInputDialog.getDouble(
            self, "Termal Bölgeyi Böl", "Bölme chainage [m]", midpoint,
            region.start_m + 0.01, region.end_m - 0.01, 3,
        )
        if not ok:
            return
        new_index = len(self.project.thermal_design.regions) + 1
        second = ThermalRegion(
            f"TR-{new_index:02}", region.name + " — 2", split_m, region.end_m, region.template_id,
            region.transition_type, region.enabled, region.data_state, region.source_reference,
            dict(region.overrides), region.notes,
        )
        region.end_m = split_m
        region.name = region.name + " — 1"
        self.project.thermal_design.regions.insert(row + 1, second)
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_all()
        self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh")

    def _remove_thermal_region(self) -> None:
        row = self.thermal_region_table.currentRow()
        if row < 0 or row >= len(self.project.thermal_design.regions):
            return
        del self.project.thermal_design.regions[row]
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_all()

    def _add_thermal_template(self) -> None:
        index = len(self.project.thermal_design.templates) + 1
        self.project.thermal_design.templates.append(ThermalCrossSectionTemplate(
            f"TPL-{index:02}", f"Yeni Kesit Şablonu {index}"
        ))
        self._mark_dirty()
        self._refresh_all()
        self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh")
        self.thermal_editor_tabs.setCurrentIndex(1)

    def _remove_thermal_template(self) -> None:
        row = self.thermal_template_table.currentRow()
        if row < 0 or row >= len(self.project.thermal_design.templates):
            return
        template_id = self.project.thermal_design.templates[row].template_id
        if any(region.template_id == template_id for region in self.project.thermal_design.regions):
            QMessageBox.warning(self, "Kesit şablonu", "Bu şablon termal bölgelerde kullanılıyor; önce bölgeleri başka şablona atayın.")
            return
        del self.project.thermal_design.templates[row]
        self._mark_dirty()
        self._refresh_all()

    def _add_thermal_material(self) -> None:
        index = len(self.project.thermal_design.materials) + 1
        self.project.thermal_design.materials.append(ThermalMaterialData(
            f"MAT-{index:02}", f"Yeni Termal Malzeme {index}"
        ))
        self._mark_dirty()
        self._refresh_all()
        self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh")
        self.thermal_editor_tabs.setCurrentIndex(2)

    def _remove_thermal_material(self) -> None:
        row = self.thermal_material_table.currentRow()
        if row < 0 or row >= len(self.project.thermal_design.materials):
            return
        material_id = self.project.thermal_design.materials[row].material_id
        referenced = False
        for template in self.project.thermal_design.templates:
            referenced |= material_id in {
                template.native_soil_material_id, template.bedding_material_id, template.side_backfill_material_id,
                template.cable_cover_material_id, template.selected_upper_fill_material_id,
                template.general_fill_material_id, template.surface_material_id,
                template.duct_material_id, template.duct_fill_material_id, template.grout_material_id,
            }
        if referenced:
            QMessageBox.warning(self, "Termal malzeme", "Bu malzeme bir veya daha fazla kesit şablonunda kullanılıyor.")
            return
        del self.project.thermal_design.materials[row]
        self._mark_dirty()
        self._refresh_all()

    def validate_thermal_route(self) -> None:
        issues = validate_thermal_design(self.project.thermal_design, self.project.cable)
        errors = [issue for issue in issues if issue.severity == "ERROR"]
        warnings = [issue for issue in issues if issue.severity == "WARNING"]
        lines = ["TERMAL GÜZERGÂH DOĞRULAMASI", "=" * 72]
        if not issues:
            lines.append("PASS — Bölge kapsaması, şablonlar, malzemeler ve geometri kontrolleri geçti.")
        else:
            for issue in issues:
                chainage = f" [{issue.chainage_start_m:.3f}-{issue.chainage_end_m:.3f} m]" if issue.chainage_end_m else ""
                lines.append(f"{issue.severity} · {issue.code} · {issue.region_id}{chainage}: {issue.message}")
        self.validation_view.setPlainText("\n".join(lines))
        self.warning_list.setPlainText("\n".join(f"• {issue.message}" for issue in issues if issue.severity != "INFO"))
        summary = f"{len(errors)} hata, {len(warnings)} uyarı. Ayrıntı: Sonuçlar ve Kayıtlar → Doğrulama."
        if errors:
            QMessageBox.warning(self, "Termal güzergâh doğrulaması", summary)
        else:
            QMessageBox.information(self, "Termal güzergâh doğrulaması", summary)
        self.statusBar().showMessage(f"Termal güzergâh doğrulandı — {len(errors)} hata, {len(warnings)} uyarı", 7000)

    def run_thermal_route_analysis(self) -> None:
        if not self._confirm_engine_precheck("thermal_route"):
            return
        self._active_engine_prechecks["iec60287"] = evaluate_engine_precheck(self.project, "iec60287")
        self._activate_workflow_stage("steady_thermal")
        self._begin_engine_run("thermal_route", "Bölgesel IEC 60287/termal güzergâh çözümü çalışıyor.")
        self._begin_engine_run("iec60287", "IEC 60287 bölgesel çözümü çalışıyor.")
        try:
            result = solve_thermal_route(self.project, self.bonding_result)
        except ThermalRouteInputError as exc:
            self._fail_engine_run("thermal_route", str(exc))
            self._fail_engine_run("iec60287", str(exc))
            QMessageBox.critical(self, "Termal güzergâh girdi hatası", str(exc))
            self.warning_list.setPlainText(str(exc))
            return
        except Exception as exc:
            self._fail_engine_run("thermal_route", f"Beklenmeyen hata: {exc}")
            self._fail_engine_run("iec60287", f"Beklenmeyen hata: {exc}")
            QMessageBox.critical(self, "Termal güzergâh hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self.thermal_route_result = result
        try:
            self.production_electrothermal_result = solve_production_electrothermal_study(
                self.project, active_scenario_id="DESIGN", thermal_method="AUTO"
            )
        except Exception as exc:
            self.production_electrothermal_result = None
            self.production_bonding_result = None
            self.warning_list.setPlainText(
                "Üretim elektro-termal çalışma noktası çözülemedi: " + str(exc)
            )
        active = result.active
        self.iec_results = [region.iec for region in active.regions]
        try:
            # The project-level wrapper delegates to materialize_route_sections_partial
            # and then attaches the same resolved x-y snapshot used by bonding.
            synchronized_sections, materialized = materialize_project_route_sections(
                self.project, strict=False, mutate_project=True
            )
            thermal_results = []
            for section in synchronized_sections:
                try:
                    thermal_results.append(solve_section_thermal(
                        self.project.cable, section,
                        physical_positions_for_region(self.project, section.thermal_region_id),
                    ))
                except ThermalInputError:
                    continue
            self.thermal_results = thermal_results
        except Exception:
            self.thermal_results = []

        route_warnings = [
            issue.message for issue in result.validation_issues if issue.severity != "INFO"
        ]
        route_warnings.extend(
            f"{outcome.region_id}: {outcome.error_code} — {outcome.error_message}"
            for outcome in active.region_outcomes if not outcome.success
        )
        production_active = (
            self.production_electrothermal_result.active
            if self.production_electrothermal_result is not None else None
        )
        nodal_dryout_binding = bool(
            production_active is not None
            and production_active.converged
            and getattr(production_active, "thermal_method", "") == "NODAL"
            and bool(getattr(production_active, "dryout_material_ids", ()))
        )
        if active.completion_status == "FAILED" and not nodal_dryout_binding:
            self.project.design_progress.thermal = "FAILED"
        elif active.completion_status == "COMPLETE" and active.suitability_status == SUITABILITY_SUITABLE:
            self.project.design_progress.thermal = "COMPLETE"
        else:
            self.project.design_progress.thermal = "CONDITIONAL"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_2

        if production_active is None:
            route_warnings.append("Üretim elektro-termal çalışma noktası bulunamadı.")
        elif not production_active.converged:
            route_warnings.append(
                f"{production_active.scenario.scenario_id}: "
                f"{production_active.error_code or 'COUPLED_OPERATING_POINT_NOT_CONVERGED'} — "
                f"{production_active.error_message or 'kapalı çevrim yakınsamadı'}"
            )
        elif nodal_dryout_binding:
            route_warnings.append(
                "Kritik-izoterm toprak kuruması etkin: analitik IEC bölüm yolu model kapsamı dışında; "
                "üretim çalışma noktası nodal termal yöntemle çözüldü."
            )
        if active.completion_status == "FAILED" and not nodal_dryout_binding:
            message = f"Bölgesel termal sonuç: {active.status}"
            self._fail_engine_run("thermal_route", message)
            self._fail_engine_run("iec60287", message)
        else:
            engine_status = (
                STATUS_COMPLETE
                if active.completion_status == "COMPLETE" and active.suitability_status == SUITABILITY_SUITABLE
                else STATUS_CONDITIONAL
            )
            engine_message = (
                "Kritik-izoterm kuruma: nodal üretim çalışma noktası bağlayıcı; analitik IEC yol kapsam dışı."
                if nodal_dryout_binding else f"Bölgesel termal sonuç: {active.status}"
            )
            self._record_engine_status(
                "thermal_route", engine_status, result_count=len(active.regions),
                warning_count=len(route_warnings), message=engine_message,
                conditional_reasons=route_warnings[:8], rebuild_tree=False,
            )
            self._record_engine_status(
                "iec60287", engine_status,
                result_count=len(self.iec_results) if not nodal_dryout_binding else len(production_active.regions),
                warning_count=len(route_warnings),
                message=(
                    "IEC 60287 kritik-izoterm kuruma kapsamı nodal üretim çalışma noktasıyla çözüldü."
                    if nodal_dryout_binding else f"IEC 60287 sonucu: {active.status}"
                ),
                conditional_reasons=route_warnings[:8],
            )
        self._populate_thermal_route_results()
        self._populate_iec_results()
        self._populate_thermal_results()
        log_lines = ["TERMAL GÜZERGÂH VE BÖLGESEL IEC 60287", "=" * 72, *active.trace]
        for outcome in active.region_outcomes:
            if outcome.result is not None:
                region = outcome.result
                log_lines.extend(["", f"{region.region_id} · {region.region_name}", *region.iec.trace_lines()])
            else:
                log_lines.extend([
                    "", f"{outcome.region_id} · {outcome.region_name}",
                    f"HATA {outcome.error_code}: {outcome.error_message}",
                ])
        if production_active is not None:
            log_lines.extend([
                "",
                "ÜRETİM ELEKTRO-TERMAL ÇALIŞMA NOKTASI",
                "-" * 72,
                *production_active.trace,
            ])
            if production_active.maximum_conductor_temperature_c is not None:
                log_lines.append(
                    f"Tmax={production_active.maximum_conductor_temperature_c:.4f} °C; "
                    f"uygunluk={production_active.suitability_status}; "
                    f"λ1 rating={'N/A' if production_active.lambda1_rating is None else f'{production_active.lambda1_rating:.8f}'}; "
                    f"λ1′ network={'N/A' if production_active.network_sheath_loss_ratio is None else f'{production_active.network_sheath_loss_ratio:.8f}'}; "
                    f"λ1″ eddy={'N/A' if production_active.lambda1_eddy is None else f'{production_active.lambda1_eddy:.8f}'}; "
                    f"authority={production_active.sheath_loss_authority}"
                )
            log_lines.append(
                f"Termal yöntem={getattr(production_active, 'thermal_method', 'ANALYTIC')}; "
                f"kuruma malzemeleri={','.join(getattr(production_active, 'dryout_material_ids', ()) or ()) or 'yok'}"
            )
        self.log_view.setPlainText("\n".join(log_lines))
        warning_lines = list(route_warnings)
        warning_lines.extend(active.critical_reasons)
        self.warning_list.setPlainText("\n".join(f"• {line}" for line in dict.fromkeys(warning_lines)))
        self._update_summary()
        self._refresh_first_design()
        if active.route_ampacity_a is not None:
            message = (
                f"Termal güzergâh çözüldü — kritik {active.critical_region_id}: "
                f"{active.route_ampacity_a:.1f} A/kablo ({active.status})"
            )
        elif active.ampacity_upper_bound_a is not None:
            message = (
                f"Termal güzergâh kısmi — ampacity üst sınırı "
                f"{active.ampacity_upper_bound_a:.1f} A/kablo ({active.status})"
            )
        elif nodal_dryout_binding and production_active is not None:
            message = (
                "Kritik-izoterm kuruma nodal üretim yöntemiyle çözüldü — "
                f"Tmax {production_active.maximum_conductor_temperature_c:.1f} °C "
                f"({production_active.suitability_status})"
            )
        else:
            message = f"Termal güzergâh sonucu: {active.status}"
        self.statusBar().showMessage(message, 9000)

    def _populate_thermal_route_results(self) -> None:
        result = self.thermal_route_result
        if result is None:
            self.thermal_route_result_table.setRowCount(0)
            return
        rows = [
            (scenario, outcome)
            for scenario in result.scenarios
            for outcome in scenario.region_outcomes
        ]
        self.thermal_route_result_table.setRowCount(len(rows))
        for row, (scenario, outcome) in enumerate(rows):
            region = outcome.result
            if region is not None:
                note = " | ".join((*region.improvement_suggestions, *region.warnings))
                values = [
                    scenario.scenario_name, f"{region.region_id} {region.region_name}", f"{region.start_m:.1f}",
                    f"{region.end_m:.1f}", region.installation_type, region.data_state,
                    f"{region.native_soil_resistivity_km_w:.3f}", f"{region.backfill_resistivity_km_w:.3f}",
                    f"{region.regional_lambda1:.6f}", f"{region.iec.t4_km_w:.4f}", f"{region.iec.ampacity_a:.1f}",
                    f"{region.iec.margin_a:.1f}", f"{region.iec.conductor_temperature_at_design_c:.1f}",
                    region.iec.status, note,
                ]
            else:
                values = [
                    scenario.scenario_name, f"{outcome.region_id} {outcome.region_name}",
                    f"{outcome.start_m:.1f}", f"{outcome.end_m:.1f}", "—", outcome.region_data_status or "—",
                    "—", "—", "—", "—", "—", "—", "—",
                    "HESAPLANAMADI", f"{outcome.error_code}: {outcome.error_message}",
                ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                if region is None:
                    cell.setBackground(QBrush(QColor("#fff4d6")))
                self.thermal_route_result_table.setItem(row, col, cell)
        self.thermal_route_result_table.resizeColumnsToContents()
        self.thermal_route_result_table.horizontalHeader().setStretchLastSection(True)

    def run_nodal_thermal_analysis(self) -> None:
        if not self._confirm_engine_precheck("nodal"):
            return
        self._activate_workflow_stage("steady_thermal")
        self._begin_engine_run("nodal", "2D nodal kararlı durum çözümü çalışıyor.")
        try:
            result = solve_nodal_route(self.project, self.bonding_result)
        except (NodalThermalInputError, ThermalRouteInputError) as exc:
            self._fail_engine_run("nodal", str(exc))
            QMessageBox.critical(self, "2D nodal termal girdi hatası", str(exc))
            self.warning_list.setPlainText(str(exc))
            return
        except Exception as exc:
            self._fail_engine_run("nodal", f"Beklenmeyen hata: {exc}")
            QMessageBox.critical(self, "2D nodal termal hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self.nodal_thermal_result = result
        self.thermal_route_result = result.iec_route_result
        if result.method_validation is not None:
            cache_thermal_method_authority(self.project, result.method_validation)
        active = result.active
        self._populate_nodal_results()
        self._populate_thermal_review_workspace()
        self._show_workspace_widget(self.thermal_review_widget, "Termal Alan")
        self.project.design_progress.thermal = "COMPLETE" if is_suitable(active.status) else "CONDITIONAL"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_2
        log_lines = ["2D NODAL TERMAL GÜZERGÂH ÇÖZÜMÜ", "=" * 72, *active.trace]
        warnings: list[str] = []
        for region in active.regions:
            log_lines.extend(["", f"{region.region_id} · {region.region_name}", *region.trace])
            warnings.extend(region.warnings)
        method_validation = result.method_validation
        if method_validation is not None:
            mv = method_validation.active
            log_lines.extend([
                "", "ANALİTİK–NODAL YÖNTEM OTORİTESİ",
                f"Temel={mv.calculation_basis}; doğrulama={mv.validation_status}; veri hüküm temeli={mv.judgement_basis_status}",
                f"Analitik={mv.analytic_ampacity_a}; nodal={mv.nodal_ampacity_a}; fark=%{mv.ampacity_difference_percent if mv.ampacity_difference_percent is not None else 0.0:+.4f}",
            ])
            if mv.validation_status != "PASS":
                warnings.extend(mv.reasons[:8])
        method_failed = bool(
            method_validation is not None
            and method_validation.validation_status == METHOD_VALIDATION_FAIL
        )
        self._record_engine_status(
            "nodal", STATUS_COMPLETE if is_suitable(active.status) and not method_failed else STATUS_CONDITIONAL,
            result_count=sum(len(scenario.regions) for scenario in result.scenarios),
            warning_count=len(warnings),
            message=(
                f"2D nodal sonuç: {active.status}; yöntem={method_validation.calculation_basis}"
                if method_validation is not None else f"2D nodal sonuç: {active.status}"
            ),
            conditional_reasons=list(dict.fromkeys(warnings))[:8],
        )
        self.log_view.setPlainText("\n".join(log_lines))
        self.warning_list.setPlainText("\n".join(f"• {item}" for item in dict.fromkeys(warnings)))
        self._update_summary()
        self._refresh_first_design()
        method_label = (
            f" · {method_validation.calculation_basis}/{method_validation.validation_status}"
            if method_validation is not None else ""
        )
        self.statusBar().showMessage(
            f"2D nodal güzergâh çözüldü — kritik {active.critical_region_id}: "
            f"{active.route_ampacity_per_cable_a:.1f} A/kablo ({active.status}){method_label}", 10000
        )

    def _populate_nodal_results(self) -> None:
        result = self.nodal_thermal_result
        if result is None:
            self.nodal_result_table.setRowCount(0)
            return
        scopes = tuple(result.scenarios) + tuple(result.circuit_scope_scenarios)
        rows = [(scope, region) for scope in scopes for region in scope.regions]
        self.nodal_result_table.setRowCount(len(rows))
        for row, (scope, region) in enumerate(rows):
            values = [
                scope.scenario_name,
                scope.solution_scope_name,
                f"{'★ ' if region.region_id == scope.critical_region_id else ''}{region.region_id} {region.region_name}",
                f"{region.start_m:.1f}–{region.end_m:.1f} m",
                region.installation_type,
                f"{region.design_current_per_cable_a:.1f}",
                f"{region.ampacity_per_cable_a:.1f}",
                f"{region.ampacity_per_cable_a-region.design_current_per_cable_a:+.1f}",
                f"{region.maximum_conductor_temperature_c:.2f}",
                region.status,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                cell.setData(
                    Qt.UserRole,
                    (scope.scenario_id, scope.solution_scope_id, region.region_id),
                )
                if not is_suitable(region.status):
                    cell.setBackground(QBrush(QColor("#fdecec")))
                elif region.region_id == scope.critical_region_id:
                    cell.setBackground(QBrush(QColor("#fff4d6")))
                self.nodal_result_table.setItem(row, col, cell)
        self.nodal_result_table.resizeColumnsToContents()
        self.nodal_result_table.horizontalHeader().setStretchLastSection(True)

    def _nodal_result_selection_changed(self) -> None:
        result = self.nodal_thermal_result
        row = self.nodal_result_table.currentRow()
        if result is None or row < 0:
            return
        item = self.nodal_result_table.item(row, 0)
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if not key:
            return
        scenario_id, scope_id, region_id = key
        self._show_nodal_result(
            scenario_id, scope_id, region_id, sync_selection=True
        )

    def _open_thermal_analysis_detail(self, *_args) -> None:
        if self.nodal_thermal_result is None or self.current_nodal_review_key is None:
            QMessageBox.information(
                self, "Termal Analiz Detayı",
                "Önce 2D nodal çözümü çalıştırın ve Termal Alan ekranından bir senaryo/kapsam/bölge seçin.",
            )
            return
        scenario_id, scope_id, region_id = self.current_nodal_review_key
        dialog = ThermalAnalysisDialog(
            self.project,
            self.nodal_thermal_result,
            scenario_id,
            region_id,
            scope_id=scope_id,
            transient_study=self.transient_thermal_result,
            display_context=self._nodal_display_context(region_id),
            display_options=self._thermal_display_options(),
            mesh_convergence=self.last_mesh_convergence.get((scenario_id, scope_id, region_id)),
            on_design_applied=self._thermal_design_applied,
            parent=self,
        )
        self._fit_dialog_to_available_screen(dialog, 1380, 860)
        dialog.exec()

    def _thermal_design_applied(self) -> None:
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_all()
        self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh")
        self.statusBar().showMessage(
            "Termal bölge tasarımı güncellendi; IEC 60287, 2D nodal ve IEC 60853 hesaplarını yeniden çalıştırın.",
            12000,
        )

    def _build_bonding_table_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        hint = QLabel(
            "v0.14 primitive CIM/NV cross-bonding çalışma alanı: renkler sürekli metalik kılıf çevrimi kimliğini, satırlar fiziksel A/B/C metalik kılıf konumunu gösterir. "
            "Şema ve hesap aynı explicit bağlantı grafiğinden üretilir; A→B→C, B→C→A ve C→A→B yolları ayrı izlenir. "
            "Her link box'ta yerel sol→sağ permütasyon yeniden uygulanır; kutu etiketi yerel bağlantıyı ve major başlangıcına göre kümülatif yolu ayrı gösterir. "
            "Cross-bonded major section'lar 3×3 bağlı kompleks metalik-kılıf çevrim empedans matrisiyle çözülür."
        )
        hint.setWordWrap(True)

        command_row = QHBoxLayout()
        auto_btn = QPushButton("Otomatik Cross-Bond Tasarla")
        joint_btn = QPushButton("Joint Ekle")
        link_btn = QPushButton("Link Box Ekle")
        pattern_btn = QPushButton("Cross-Bond Şablonu Uygula")
        remove_btn = QPushButton("Seçileni Sil")
        focus_btn = QPushButton("Bonding Tam Ekran")
        results_btn = QPushButton("Bonding Sonuçlarını Aç")
        calc_btn = QPushButton("Bonding Hesapla")
        self.bonding_focus_button = focus_btn
        auto_btn.clicked.connect(self._auto_design_cross_bonding)
        joint_btn.clicked.connect(self._add_bonding_joint)
        link_btn.clicked.connect(self._add_link_box)
        pattern_btn.clicked.connect(self._apply_cross_bond_pattern)
        remove_btn.clicked.connect(self._remove_bonding_selected)
        focus_btn.clicked.connect(self._toggle_bonding_focus)
        results_btn.clicked.connect(
            lambda: self.show_results_dialog(
                self.primitive_result_table if self.bonding_result and self.bonding_result.primitive_network_result is not None
                else self.bonding_result_table
            )
        )
        calc_btn.clicked.connect(self.run_bonding_solver)
        for button in (auto_btn, joint_btn, link_btn, pattern_btn, remove_btn):
            command_row.addWidget(button)
        command_row.addStretch(1)
        command_row.addWidget(focus_btn)
        command_row.addWidget(results_btn)
        command_row.addWidget(calc_btn)

        self.bonding_splitter = QSplitter(Qt.Vertical)
        self.bonding_view.setMinimumHeight(390)
        self.bonding_splitter.addWidget(self.bonding_view)

        self.bonding_editor_tabs = QTabWidget()
        self.bonding_minor_table = QTableWidget(0, 9)
        self.bonding_minor_table.setHorizontalHeaderLabels(
            ["ID", "Major", "Başlangıç", "Bitiş", "Başlangıç km", "Bitiş km", "Uzunluk [m]", "Faz sırası", "Maks. V [V]"]
        )
        self.bonding_minor_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_minor_table.setAlternatingRowColors(True)
        self.bonding_minor_table.itemChanged.connect(self._bonding_minor_table_changed)

        self.bonding_node_table = QTableWidget(0, 6)
        self.bonding_node_table.setHorizontalHeaderLabels(
            ["Joint ID", "Ad", "Tip", "Konum [m]", "Topraklama", "Topraklama R [Ω]"]
        )
        self.bonding_node_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_node_table.setAlternatingRowColors(True)
        self.bonding_node_table.itemChanged.connect(self._bonding_node_table_changed)

        self.bonding_linkbox_table = QTableWidget(0, 9)
        self.bonding_linkbox_table.setHorizontalHeaderLabels(
            ["Link Box ID", "Ad", "Bağlı joint", "Konum [m]", "Lead [m]", "Lead tipi", "SVL", "Erişilebilir", "SVL adayı"]
        )
        self.bonding_linkbox_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_linkbox_table.setAlternatingRowColors(True)
        self.bonding_linkbox_table.itemChanged.connect(self._bonding_linkbox_table_changed)

        self.bonding_connection_table = QTableWidget(0, 6)
        self.bonding_connection_table.setHorizontalHeaderLabels(
            ["Link Box", "Joint", "Sol metalik kılıf", "Sağ metalik kılıf / Toprak", "Bağlantı", "Yol kontrolü"]
        )
        self.bonding_connection_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_connection_table.setAlternatingRowColors(True)
        self.bonding_connection_table.itemChanged.connect(self._bonding_connection_table_changed)

        self.bonding_editor_tabs.addTab(self.bonding_minor_table, "Minor Section'lar")
        self.bonding_editor_tabs.addTab(self.bonding_node_table, "Sectionalizing Joint'ler")
        self.bonding_editor_tabs.addTab(self.bonding_linkbox_table, "Link Box'lar")
        self.bonding_editor_tabs.addTab(self.bonding_connection_table, "Faz Metalik Kılıf Cross Bağları")
        self.bonding_splitter.addWidget(self.bonding_editor_tabs)
        self.bonding_splitter.setSizes([520, 190])

        layout.addWidget(hint)
        layout.addLayout(command_row)
        layout.addWidget(self.bonding_splitter)
        return container


    def _build_fault_table_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        hint = QLabel(
            "Güç frekanslı arıza çalışması, normal yükte kullanılan aynı primitive iletken/metalik-kılıf/GCC/topraklama ağını "
            "üç faz, faz-faz ve tek faz-toprak akım setleriyle çözer. EPR lumped toprak elektrotlarıyla; "
            "SVL-TOV ise metalik-kılıf–toprak ve sectionalizing-interrupt gerilimleriyle hesaplanır. EMT değildir."
        )
        hint.setWordWrap(True)
        buttons = QHBoxLayout()
        add_btn = QPushButton("Senaryo Ekle")
        remove_btn = QPushButton("Seçili Senaryoyu Sil")
        run_btn = QPushButton("Arıza / EPR Hesapla")
        results_btn = QPushButton("Arıza / EPR Sonuçlarını Aç")
        transfer_btn = QPushButton("En Kötü TOV'u SVL'ye Aktar")
        add_btn.clicked.connect(self._add_fault_scenario)
        remove_btn.clicked.connect(self._remove_fault_scenario)
        run_btn.clicked.connect(self.run_fault_study)
        results_btn.clicked.connect(lambda: self.show_results_dialog(self.fault_result_table))
        transfer_btn.clicked.connect(self._transfer_fault_tov_to_svl)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(transfer_btn)
        buttons.addWidget(results_btn)
        buttons.addWidget(run_btn)
        self.fault_scenario_table = QTableWidget(0, 9)
        self.fault_scenario_table.setHorizontalHeaderLabels(
            ["ID", "Ad", "Etkin", "Tip", "Arıza akımı [A]", "Faz 1", "Faz 2", "Süre [s]", "Not"]
        )
        self.fault_scenario_table.setAlternatingRowColors(True)
        self.fault_scenario_table.horizontalHeader().setStretchLastSection(True)
        self.fault_scenario_table.itemChanged.connect(self._fault_scenario_table_changed)
        criteria = QLabel(
            "Solver: PRIMITIVE_CIM / NODE_VOLTAGE · TOV süre çarpanı ve dielektrik charging ayarları "
            "Ayarlar → Aşama Rehberi / Nesne Bilgileri penceresinden değiştirilebilir."
        )
        criteria.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        layout.addWidget(criteria)
        layout.addWidget(self.fault_scenario_table)
        return container

    def _build_svl_table_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        hint = QLabel(
            "SVL ön boyutlandırma; bonding hesabındaki normal standing voltage ile kullanıcı/EMT kaynaklı "
            "fault-TOV, residual-voltage, enerji ve darbe akımı verilerini birlikte kontrol eder. "
            "Eksik transient verilerinde sonuç CONDITIONAL kalır; yazılım değer uydurmaz."
        )
        hint.setWordWrap(True)

        buttons = QHBoxLayout()
        add_btn = QPushButton("Aday Ekle")
        remove_btn = QPushButton("Seçili Adayı Sil")
        run_btn = QPushButton("SVL Seçimini Çalıştır")
        results_btn = QPushButton("SVL Sonuçlarını Aç")
        assign_btn = QPushButton("Önerileni Link Box'lara Ata")
        add_btn.clicked.connect(self._add_svl_candidate)
        remove_btn.clicked.connect(self._remove_svl_candidate)
        run_btn.clicked.connect(self.run_svl_selection)
        results_btn.clicked.connect(lambda: self.show_results_dialog(self.svl_result_table))
        assign_btn.clicked.connect(self._assign_recommended_svl)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(assign_btn)
        buttons.addWidget(results_btn)
        buttons.addWidget(run_btn)

        splitter = QSplitter(Qt.Vertical)
        self.svl_criteria_table = QTableWidget(0, 3)
        self.svl_criteria_table.setHorizontalHeaderLabels(["Kriter / görev", "Değer", "Kaynak / durum"])
        self.svl_criteria_table.horizontalHeader().setStretchLastSection(True)
        self.svl_criteria_table.setAlternatingRowColors(True)
        self.svl_criteria_table.itemChanged.connect(self._svl_criteria_table_changed)
        self.svl_candidate_table = QTableWidget(0, 14)
        self.svl_candidate_table.setHorizontalHeaderLabels([
            "ID", "Üretici", "Model", "Teknoloji", "MCOV [V rms]", "TOV 1s", "TOV 10s",
            "TOV 100s", "Residual [V peak]", "Enerji [kJ]", "In [kA]", "Bağlantılar", "Kaynak", "Not"
        ])
        self.svl_candidate_table.horizontalHeader().setStretchLastSection(True)
        self.svl_candidate_table.setAlternatingRowColors(True)
        self.svl_candidate_table.itemChanged.connect(self._svl_candidate_table_changed)
        splitter.addWidget(self.svl_criteria_table)
        splitter.addWidget(self.svl_candidate_table)
        splitter.setSizes([270, 390])

        layout.addWidget(hint)
        layout.addLayout(buttons)
        layout.addWidget(splitter)
        return container

    def _refresh_all(self) -> None:
        if getattr(self, "cable_library_widget", None) is not None:
            if self.cable_library_widget.project is not self.project:
                self.cable_library_widget.set_project(self.project)
            else:
                self.cable_library_widget.refresh()
        if getattr(self, "database_cable_widget", None) is not None:
            if self.database_cable_widget.project is not self.database_project:
                self.database_cable_widget.set_project(self.database_project)
            else:
                self.database_cable_widget.refresh()
        self._refresh_first_design()
        self._refresh_workflow()
        self._build_tree()
        self._show_project_properties()
        self._refresh_route_table()
        self._refresh_thermal_route_tables()
        self._refresh_bonding_tables()
        self._refresh_fault_tables()
        self._refresh_svl_tables()
        self._refresh_transient_tables()
        self._populate_thermal_review_workspace()
        self.bonding_view.draw_bonding_system(self.project.bonding, self.bonding_result)
        self._update_summary()
        self.statusBar().showMessage(
            "Hazır — proje tasarım akışı kullanıcı girdilerini, hesap motorlarını ve veri kapılarını yönlendiriyor"
        )

    # ------------------------------------------------------------------
    # Ağaç durum kaplaması.  Renk ve eksik listesi daha önce yalnız
    # "Proje Tasarım Akışı" dalında vardı; nesne, sonuç ve çıktı dallarında
    # ne renk ne açıklama bulunuyordu.  Aynı veri modeli (missing_inputs /
    # blocking_reasons) tüm dallara bağlanır.
    def _stage_for_payload(self, payload) -> object | None:
        if self.workflow_evaluation is None or not payload:
            return None
        kind = payload[0] if isinstance(payload, tuple) else ""
        stage_id = ""
        if kind == "workflow_stage":
            stage_id = str(payload[1] or "")
        else:
            stage_id = self.TREE_PAYLOAD_STAGE.get(str(kind), "")
        if not stage_id:
            return None
        try:
            return self.workflow_evaluation.stage(stage_id)
        except KeyError:
            return None

    @staticmethod
    def _rich_tooltip(title: str, rows: list[tuple[str, str]], bullets: list[tuple[str, list[str]]]) -> str:
        """Genişliği sınırlı zengin metin tooltip.

        Düz metin ``\n`` birleştirmesi uzun eksik listelerinde tek blok halinde
        taşıyordu; zengin metin sarmalama ile okunur kalır.
        """

        from html import escape

        parts = [
            "<div style='max-width:430px'>",
            f"<b>{escape(title)}</b>",
        ]
        for label, value in rows:
            if value:
                parts.append(f"<br><span style='color:#41576b'>{escape(label)}:</span> {escape(value)}")
        for heading, items in bullets:
            visible = [item for item in items if item]
            if not visible:
                continue
            parts.append(f"<br><br><b>{escape(heading)}</b><ul style='margin:2px 0 0 14px'>")
            for item in visible[:8]:
                parts.append(f"<li>{escape(str(item))}</li>")
            if len(visible) > 8:
                parts.append(f"<li>… ve {len(visible) - 8} madde daha</li>")
            parts.append("</ul>")
        parts.append("</div>")
        return "".join(parts)

    def _decorate_tree_item(self, item, payload, base_label: str) -> str:
        """Düğüme durum rengi ve eksik açıklaması uygula; renk durumunu döndür."""

        stage = self._stage_for_payload(payload)
        if stage is None:
            item.setToolTip(0, self._rich_tooltip(base_label, [], []))
            return ""
        display = user_stage_state(stage)
        background, foreground, _border = STATUS_COLORS.get(
            display.color_status, STATUS_COLORS[STATUS_NOT_STARTED]
        )
        item.setBackground(0, QBrush(QColor(background)))
        item.setForeground(0, QBrush(QColor(foreground)))
        item.setToolTip(
            0,
            self._rich_tooltip(
                base_label,
                [
                    ("Aşama", f"{stage.number}. {stage.title}"),
                    ("Durum", display.label),
                    ("Açıklama", display.reason),
                    ("Sonraki işlem", display.action),
                ],
                [
                    ("Tamamlanması gerekenler", list(getattr(stage, "missing_inputs", ()) or ())),
                    ("Bloke nedenleri", list(getattr(stage, "blocking_reasons", ()) or ())),
                    ("Teknik ayrıntı", list(getattr(stage, "notes", ()) or ())),
                ],
            ),
        )
        return str(display.color_status)

    @staticmethod
    def _roll_up_group_status(group_item) -> None:
        """Grup düğümü, altındaki en kötü çocuk durumunu rozet olarak taşısın.

        Ağacı açmadan da nerede sorun olduğu görünür.
        """

        severity = {
            "BLOCKED": 5,
            "MISSING_DATA": 4,
            "STALE": 3,
            "CONDITIONAL": 2,
            "PRELIMINARY": 2,
            "NOT_STARTED": 1,
        }
        worst = ""
        worst_rank = 0
        for index in range(group_item.childCount()):
            status = str(group_item.child(index).data(0, Qt.UserRole + 1) or "")
            rank = severity.get(status.upper(), 0)
            if rank > worst_rank:
                worst, worst_rank = status, rank
        if not worst:
            return
        background, foreground, _border = STATUS_COLORS.get(worst, STATUS_COLORS[STATUS_NOT_STARTED])
        marker = {5: "■", 4: "■", 3: "▲", 2: "▲", 1: "○"}.get(worst_rank, "")
        text = group_item.text(0)
        if marker and not text.startswith(marker):
            group_item.setText(0, f"{marker}  {text}")
        group_item.setForeground(0, QBrush(QColor(foreground)))
        group_item.setToolTip(
            0,
            f"<div style='max-width:380px'><b>{text}</b><br>"
            f"Bu grupta en yüksek öncelikli durum: <b>{worst}</b>."
            "<br>Ayrıntı için alt maddelerin üzerine gelin.</div>",
        )

    # Ağaç düğümü türünden tasarım akışı aşamasına eşleme.
    TREE_PAYLOAD_STAGE: dict[str, str] = {
        "cable": "cable",
        "route": "route",
        "installation": "installation",
        "installation_section": "installation",
        "fault_study": "fault_epr",
        "fault_scenario": "fault_epr",
        "thermal": "thermal_route",
        "thermal_region": "thermal_route",
        "thermal_template": "thermal_route",
        "thermal_material": "thermal_route",
        "bonding": "bonding",
        "bonding_minor": "bonding",
        "bonding_node": "bonding",
        "bonding_linkbox": "bonding",
        "svl": "svl",
        "svl_candidate": "svl",
        "report_builder": "deliverables",
        "procurement": "deliverables",
    }

    def _result_node_state(self, key: str) -> tuple[str, str]:
        """Sonuç düğümünün üretilip üretilmediğini ve kısa durumunu ver."""

        produced = {
            "summary": bool(self.iec_results),
            "thermal_resistance": bool(self.thermal_results),
            "thermal_route": self.thermal_route_result is not None,
            "nodal": self.nodal_thermal_result is not None,
            "transient": self.transient_thermal_result is not None,
            "bonding": self.bonding_result is not None,
            "primitive": self.production_bonding_result is not None,
            "fault": self.fault_result is not None,
            "svl": self.svl_result is not None,
            "iec60287": bool(self.iec_results),
        }
        if key not in produced:
            return "", ""
        if produced[key]:
            return STATUS_COMPLETE, "Sonuç üretildi"
        return STATUS_NOT_STARTED, "Henüz çalıştırılmadı"

    def _build_tree(self) -> None:
        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        root = QTreeWidgetItem([self.project.project_name])
        root.setData(0, Qt.UserRole, ("project", None))
        root.setToolTip(0, "Ana güzergâh / CAD tuvaline dön")
        self.project_tree.addTopLevelItem(root)

        evaluation = self.workflow_evaluation or evaluate_project_workflow(
            self.project, self._workflow_runtime_context()
        )
        workflow_group = QTreeWidgetItem(["Proje Tasarım Akışı"])
        workflow_group.setData(0, Qt.UserRole, ("workflow_group", None))
        root.addChild(workflow_group)
        workflow_items: dict[str, QTreeWidgetItem] = {}
        for stage in evaluation.stages:
            display = user_stage_state(stage)
            caption = f"●  {stage.number}. {stage.short_title} — {display.label}"
            if display.reason:
                short_reason = display.reason.rstrip(".")
                if len(short_reason) <= 54:
                    caption += f" · {short_reason}"
            child = QTreeWidgetItem([caption])
            child.setData(0, Qt.UserRole, ("workflow_stage", stage.stage_id))
            bg, fg, border = STATUS_COLORS.get(display.color_status, STATUS_COLORS[STATUS_NOT_STARTED])
            child.setBackground(0, QBrush(QColor(bg)))
            child.setForeground(0, QBrush(QColor(fg)))
            font = child.font(0)
            font.setBold(stage.stage_id == evaluation.recommended_stage_id)
            child.setFont(0, font)
            tooltip = [
                stage.title,
                f"Durum: {display.label}",
                f"Açıklama: {display.reason}",
                f"Sonraki işlem: {display.action}",
            ]
            if stage.missing_inputs:
                tooltip.extend(["", "Tamamlanması gerekenler:", *[f"• {value}" for value in stage.missing_inputs]])
            if stage.blocking_reasons:
                tooltip.extend(["", "Bloke nedenleri:", *[f"• {value}" for value in stage.blocking_reasons]])
            if stage.notes:
                tooltip.extend(["", "Teknik ayrıntı:", *[f"• {value}" for value in stage.notes]])
            if stage.stage_id == evaluation.recommended_stage_id:
                tooltip.append("★ Programın önerdiği sonraki aşama")
            child.setToolTip(
                0,
                self._rich_tooltip(
                    f"{stage.number}. {stage.title}",
                    [
                        ("Durum", display.label),
                        ("Açıklama", display.reason),
                        ("Sonraki işlem", display.action),
                    ],
                    [
                        ("Tamamlanması gerekenler", list(stage.missing_inputs or ())),
                        ("Bloke nedenleri", list(stage.blocking_reasons or ())),
                        ("Teknik ayrıntı", list(stage.notes or ())),
                    ],
                )
                + (
                    "<div style='color:#2f6690'><b>★ Programın önerdiği sonraki aşama</b></div>"
                    if stage.stage_id == evaluation.recommended_stage_id
                    else ""
                ),
            )
            child.setData(0, Qt.UserRole + 1, str(display.color_status))
            workflow_group.addChild(child)
            workflow_items[stage.stage_id] = child

        object_groups = [
            ("Elektrik Sistemi", [
                ("Şebeke ve Sistem/Yük", ("workflow_stage", "system_load")),
                ("Yük Profilleri", ("workflow_stage", "transient")),
                (self.project.fault_study.name, ("fault_study", None)),
            ]),
            ("Projeye Atanmış Kablolar", [
                (self.project.cable.name, ("cable", None)),
            ]),
            ("Güzergâh", [
                (section.name, ("route", index))
                for index, section in enumerate(self.project.route_sections)
            ]),
            ("Kurulum ve Fiziksel Kesit", [
                (self.project.installation_design.name, ("installation", None)),
                *[(f"Kesit · {section.name}", ("installation_section", section.cross_section_id))
                  for section in self.project.installation_design.cross_sections],
            ]),
            ("Termal Güzergâh", [
                (self.project.thermal_design.name, ("thermal", None)),
                *[(f"Bölge · {region.name}", ("thermal_region", index))
                  for index, region in enumerate(self.project.thermal_design.regions)],
                *[(f"Kesit · {template.name}", ("thermal_template", index))
                  for index, template in enumerate(self.project.thermal_design.templates)],
                *[(f"Malzeme · {material.name}", ("thermal_material", index))
                  for index, material in enumerate(self.project.thermal_design.materials)],
            ]),
            ("Bonding Sistemi", [
                (self.project.bonding.name, ("bonding", None)),
                *[(section.name, ("bonding_minor", index)) for index, section in enumerate(self.project.bonding.minor_sections)],
                *[(node.name, ("bonding_node", index)) for index, node in enumerate(self.project.bonding.nodes)],
                *[(box.name, ("bonding_linkbox", index)) for index, box in enumerate(self.project.bonding.link_boxes)],
            ]),
            ("Arıza / EPR", [
                (self.project.fault_study.name, ("fault_study", None)),
                *[(scenario.name, ("fault_scenario", index)) for index, scenario in enumerate(self.project.fault_study.scenarios)]
            ]),
            ("SVL Sistemi", [
                (self.project.svl.name, ("svl", None)),
                *[(f"Aday · {(candidate.manufacturer + ' ' + candidate.model).strip() or candidate.candidate_id}", ("svl_candidate", index))
                  for index, candidate in enumerate(self.project.svl.candidates)],
            ]),
        ]
        for group_name, children in object_groups:
            group = QTreeWidgetItem([group_name])
            group.setData(0, Qt.UserRole, ("object_group", group_name))
            root.addChild(group)
            for label, payload in children:
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.UserRole, payload)
                child.setData(0, Qt.UserRole + 1, self._decorate_tree_item(child, payload, label))
                group.addChild(child)
            self._roll_up_group_status(group)

        result_group = QTreeWidgetItem(["Sonuçlar ve Kayıtlar"])
        result_group.setData(0, Qt.UserRole, ("result_group", None))
        root.addChild(result_group)
        result_nodes = [
            ("Proje Özeti", "summary"),
            ("Termal Dirençler", "thermal_resistance"),
            ("Termal Güzergâh Sonuçları", "thermal_route"),
            ("2D Nodal Sonuçları", "nodal"),
            ("IEC 60853 Sonuçları", "transient"),
            ("Bonding Sonuçları", "bonding"),
            ("Primitive CIM / NV", "primitive"),
            ("Arıza / EPR Sonuçları", "fault"),
            ("SVL Sonuçları", "svl"),
            ("IEC 60287 Sonuçları", "iec60287"),
            ("Uyarılar", "warnings"),
            ("Hesap Günlüğü", "log"),
            ("Doğrulama", "validation"),
        ]
        for label, key in result_nodes:
            child = QTreeWidgetItem([label])
            child.setData(0, Qt.UserRole, ("result", key))
            status, detail = self._result_node_state(key)
            if status:
                background, foreground, _border = STATUS_COLORS.get(
                    status, STATUS_COLORS[STATUS_NOT_STARTED]
                )
                child.setBackground(0, QBrush(QColor(background)))
                child.setForeground(0, QBrush(QColor(foreground)))
                child.setData(0, Qt.UserRole + 1, status)
            child.setToolTip(
                0,
                self._rich_tooltip(
                    label,
                    [("Durum", detail or "Henüz üretilmedi"),
                     ("Açılış", "Sonuçlar ve Kayıtlar penceresinde")],
                    [],
                ),
            )
            result_group.addChild(child)
        self._roll_up_group_status(result_group)

        deliverables = QTreeWidgetItem(["Raporlar ve Tedarik Çıktıları"])
        deliverables.setData(0, Qt.UserRole, ("deliverable_group", None))
        root.addChild(deliverables)
        for label, payload in (
            ("Hesap / Proje Raporu", ("report_builder", None)),
            ("BOQ / BOM / RFQ", ("procurement", None)),
        ):
            child = QTreeWidgetItem([label])
            child.setData(0, Qt.UserRole, payload)
            child.setData(0, Qt.UserRole + 1, self._decorate_tree_item(child, payload, label))
            deliverables.addChild(child)
        self._roll_up_group_status(deliverables)

        root.setExpanded(True)
        workflow_group.setExpanded(True)
        for index in range(1, root.childCount()):
            root.child(index).setExpanded(False)
        self.project_tree.setCurrentItem(root)
        self.project_tree.blockSignals(False)

    def _set_property_rows(self, rows: list[tuple[str, str, str, bool]]) -> None:
        self.property_table.blockSignals(True)
        self.property_table.setRowCount(len(rows))
        for row, (name, value, source, editable) in enumerate(rows):
            items = [QTableWidgetItem(name), QTableWidgetItem(value), QTableWidgetItem(source)]
            items[0].setFlags(items[0].flags() & ~Qt.ItemIsEditable)
            items[2].setFlags(items[2].flags() & ~Qt.ItemIsEditable)
            if not editable:
                items[1].setFlags(items[1].flags() & ~Qt.ItemIsEditable)
            for col, cell in enumerate(items):
                cell.setData(Qt.UserRole, name)
                self.property_table.setItem(row, col, cell)
        self.property_table.resizeColumnsToContents()
        self.property_table.horizontalHeader().setStretchLastSection(True)
        self.property_table.blockSignals(False)

    def _show_project_properties(self) -> None:
        self._set_property_rows([
            ("Proje adı", self.project.project_name, "Kullanıcı", True),
            ("Proje kodu", self.project.project_code, "Kullanıcı", True),
            ("Standart profili", self.project.standards_profile, "Proje", True),
            ("Şema sürümü", self.project.schema_version, "Sistem", False),
            ("CAD kaynağı", self.project.cad_source or "Atanmadı", "Dosya", False),
            ("Oluşturma", self.project.created_at, "Sistem", False),
            ("Son değişiklik", self.project.modified_at, "Sistem", False),
        ])

    def _show_cable_properties(self) -> None:
        c = self.project.cable
        rows = [
            ("Kablo sistemi", c.name, "Kullanıcı", True),
            ("Sistem gerilimi [kV]", f"{c.voltage_kv:g}", "Proje", True),
            ("Frekans [Hz]", f"{c.frequency_hz:g}", "Proje", True),
            ("Tasarım akımı / kablo [A]", f"{c.design_current_a:g}", "Proje", True),
            ("Paralel kablo/faz", f"{c.parallel_cables_per_phase}", "İlk tasarım", True),
            ("İletken", c.conductor_material, "Üretici", True),
            ("Kesit [mm²]", f"{c.conductor_area_mm2:g}", "Üretici", True),
            ("Rdc20 [Ω/km] (0=otomatik)", f"{c.dc_resistance_20_ohm_km:g}", "Üretici / hesap", True),
            ("α20 [1/°C]", f"{c.temperature_coefficient_20_per_c:g}", "Üretici / malzeme", True),
            ("Skin faktörü ys", f"{c.skin_effect_factor:g}", "Mühendislik girdisi", True),
            ("Proximity faktörü yp", f"{c.proximity_effect_factor:g}", "Mühendislik girdisi", True),
            ("Kapasitans [µF/km]", f"{c.capacitance_uf_km:g}", "Üretici", True),
            ("tanδ", f"{c.dielectric_loss_tan_delta:g}", "Üretici", True),
            ("Legacy λ1 girdisi", f"{c.sheath_loss_factor:g}", "Geriye uyumluluk; üretim sheath-loss completeness yerine geçmez", True),
            ("Zırh kayıp faktörü λ2", f"{c.armour_loss_factor:g}", "Kablo girdisi", True),
            ("Metalik kılıf/ekran malzemesi", c.sheath_material, "Üretici / kablo modeli", True),
            ("Metalik kılıf/ekran kesiti [mm²]", f"{c.sheath_cross_section_mm2:g}", "Üretici / kablo modeli", True),
            ("Metalik kılıf Rdc20 [Ω/km] (0=otomatik)", f"{c.sheath_dc_resistance_20_ohm_km:g}", "Üretici / hesap", True),
            ("Metalik kılıf α20 [1/°C]", f"{c.sheath_temperature_coefficient_20_per_c:g}", "Malzeme", True),
            ("Metalik kılıf işletme sıcaklığı [°C]", f"{c.sheath_operating_temperature_c:g}", "Tasarım", True),
            ("Metalik kılıf ortalama çapı [mm]", f"{c.sheath_mean_diameter_mm:g}", "Kablo geometrisi", True),
            ("Metalik kılıf GMR [mm] (0=otomatik)", f"{c.sheath_gmr_mm:g}", "Primitive CIM/NV", True),
            ("Harici λ1″", f"{c.sheath_eddy_external_factor:g}", "CUSTOM/çok-devre için doğrulanmış dış girdi", True),
            ("Harici λ1″ kaynak türü", c.sheath_eddy_external_source_type, "Provenance", True),
            ("Harici λ1″ referansı", c.sheath_eddy_external_reference, "Provenance", True),
            ("Harici λ1″ referans frekansı [Hz]", f"{c.sheath_eddy_external_frequency_hz:g}", "Referans koşulu", True),
            ("Harici λ1″ referans kılıf sıcaklığı [°C]", f"{c.sheath_eddy_external_sheath_temperature_c:g}", "Referans koşulu", True),
            ("Harici λ1″ referans d [mm]", f"{c.sheath_eddy_external_d_mm:g}", "Referans koşulu", True),
            ("Harici λ1″ referans s [mm]", f"{c.sheath_eddy_external_s_mm:g}", "Referans koşulu", True),
            ("Harici λ1″ formasyon varsayımı", c.sheath_eddy_external_formation_assumption, "Referans koşulu", True),
            ("İç termal mod", c.internal_thermal_mode, "Tasarım", True),
            ("T1 [K·m/W] manuel", f"{c.thermal_resistance_t1_km_w:g}", "Manuel mod", True),
            ("T2 [K·m/W] manuel", f"{c.thermal_resistance_t2_km_w:g}", "Manuel mod", True),
            ("T3 [K·m/W] manuel", f"{c.thermal_resistance_t3_km_w:g}", "Manuel mod", True),
            ("İletken çapı [mm]", f"{c.conductor_diameter_mm:g}", "Eşdeğer geometri", True),
            ("İletken GMR [mm] (0=otomatik)", f"{c.conductor_gmr_mm:g}", "Primitive CIM/NV", True),
            ("T1 dış sınır çapı [mm]", f"{c.t1_outer_diameter_mm:g}", "Eşdeğer geometri", True),
            ("T2 dış sınır çapı [mm]", f"{c.t2_outer_diameter_mm:g}", "Eşdeğer geometri", True),
            ("Kablo dış çapı [mm]", f"{c.overall_diameter_mm:g}", "Üretici / geometri", True),
            ("T1 ρth [K·m/W]", f"{c.t1_thermal_resistivity_km_w:g}", "Malzeme", True),
            ("T2 ρth [K·m/W]", f"{c.t2_thermal_resistivity_km_w:g}", "Malzeme", True),
            ("T3 ρth [K·m/W]", f"{c.t3_thermal_resistivity_km_w:g}", "Malzeme", True),
            ("Yalıtım", c.insulation, "Üretici", True),
            ("Maks. sıcaklık [°C]", f"{c.max_temperature_c:g}", "IEC / üretici", True),
            ("Referans ortam [°C]", f"{c.reference_ambient_c:g}", "Proje", True),
            ("Yerleşim", c.arrangement, "Tasarım", True),
        ]
        self._set_property_rows(rows)

    def _show_route_properties(self, index: int) -> None:
        rs = self.project.route_sections[index]
        self._set_property_rows([
            ("Bölüm adı", rs.name, "CAD / kullanıcı", True),
            ("Uzunluk [m]", f"{rs.length_m:g}", "CAD", True),
            ("Bölüm tipi", rs.section_type, "Kullanıcı", True),
            ("Gömülme derinliği [m]", f"{rs.burial_depth_m:g}", "CAD / saha", True),
            ("Toprak ısıl özdirenci [K·m/W]", f"{rs.soil_thermal_resistivity_km_w:g}", "Saha / varsayım", True),
            ("Kesit şablonu", rs.cross_section_id, "Proje", True),
            ("Ortam sıcaklığı [°C]", f"{rs.ambient_temperature_c:g}", "Saha / proje", True),
            ("T4 modu", rs.external_thermal_mode, "Tasarım", True),
            ("Faz eksen aralığı [m]", f"{rs.phase_spacing_m:g}", "Kesit", True),
            ("T4 [K·m/W] manuel", f"{rs.external_thermal_resistance_t4_km_w:g}", "Manuel mod", True),
            ("Not", rs.notes, "Kullanıcı", True),
        ])

    def _show_bonding_properties(self) -> None:
        b = self.project.bonding
        result = self.bonding_result
        self._set_property_rows([
            ("Bonding sistemi", b.name, "Proje", True),
            ("Bonding şeması", b.scheme, "Tasarım", True),
            ("Bonding hedef devresi", b.target_circuit_id, "Fiziksel kesit", True),
            ("Bonding hedef paralel grubu", f"{b.target_parallel_index:d}", "Fiziksel kesit", True),
            ("Legacy faz eksen aralığı [m]", f"{b.phase_spacing_m:g}", "Geometri yoksa fallback", True),
            ("Normal metalik kılıf gerilim limiti [V]", f"{b.normal_sheath_voltage_limit_v:g}", "Proje kriteri", True),
            ("Maks. bonding lead [m]", f"{b.maximum_bonding_lead_length_m:g}", "Proje kriteri", True),
            ("Maks. λ1", f"{b.maximum_lambda1:g}", "Proje kriteri", True),
            ("Optimizasyon iterasyonu", f"{b.optimization_max_iterations:g}", "Çözücü", True),
            ("Konum snap [m]", f"{b.optimization_snap_m:g}", "Çözücü", True),
            ("Bonding çözüm modu", b.solver_mode, "Çözücü", True),
            ("Toprak özdirenci [Ω·m]", f"{b.earth_resistivity_ohm_m:g}", "Primitive CIM/NV", True),
            ("Dielektrik charging", "Evet" if b.include_dielectric_charging else "Hayır", "Primitive CIM/NV", True),
            ("GCC/ECC", "Etkin" if b.gcc_enabled else "Devre dışı", "Primitive CIM/NV", True),
            ("GCC/ECC kesiti [mm²]", f"{b.gcc_area_mm2:g}", "Primitive CIM/NV", True),
            ("GCC/ECC X ofset [m]", f"{b.gcc_x_offset_m:g}", "Primitive CIM/NV", True),
            ("GCC/ECC derinlik ofset [m]", f"{b.gcc_depth_offset_m:g}", "Primitive CIM/NV", True),
            ("Bonding lead L [µH/m]", f"{b.bonding_lead_inductance_uh_per_m:g}", "Primitive CIM/NV", True),
            ("Metalik kılıf karşılıklı bağlaşımı", "Evet" if b.sheath_mutual_coupling_enabled else "Hayır", "Çözücü", True),
            ("Link box temas R [mΩ]", f"{b.link_box_contact_resistance_mohm:g}", "Aksesuar / varsayım", True),
            ("Bonding lead sabit R [mΩ]", f"{b.bonding_lead_resistance_mohm:g}", "Aksesuar / varsayım", True),
            ("Bonding lead R [mΩ/m]", f"{b.bonding_lead_resistance_mohm_per_m:g}", "Lead kesiti / varsayım", True),
            ("λ1 otomatik aktar", "Evet" if b.auto_apply_lambda1 else "Hayır", "Tasarım", True),
            ("Major section sayısı", f"{result.major_section_count}" if result else "Hesaplanmadı", "Bonding hesabı", False),
            ("Hesaplanan λ1", f"{result.lambda1:.8f}" if result else "Hesaplanmadı", "Bonding hesabı", False),
            ("Maks. standing V [V]", f"{result.max_standing_voltage_v:.3f}" if result else "—", "Bonding hesabı", False),
            ("Matris koşul sayısı", f"{result.maximum_matrix_condition_number:.6g}" if result else "—", "Bonding hesabı", False),
            ("İdeal iptal", "Evet" if result and result.ideal_cancellation else "Hayır / hesaplanmadı", "Bonding hesabı", False),
        ])

    def _show_bonding_minor_properties(self, index: int) -> None:
        section = self.project.bonding.minor_sections[index]
        nodes = {node.node_id: node for node in self.project.bonding.nodes}
        start = nodes.get(section.start_node_id)
        end = nodes.get(section.end_node_id)
        self._set_property_rows([
            ("Minor section ID", section.section_id, "Sistem", True),
            ("Minor section adı", section.name, "Proje", True),
            ("Major section", f"{section.major_index}", "Bonding ağı", True),
            ("Başlangıç düğümü", section.start_node_id, "Bonding ağı", True),
            ("Bitiş düğümü", section.end_node_id, "Bonding ağı", True),
            ("Başlangıç konumu [m]", f"{start.position_m:g}" if start else "—", "Joint", False),
            ("Bitiş konumu [m]", f"{end.position_m:g}" if end else "—", "Joint", False),
            ("Uzunluk [m]", f"{section.length_m:g}", "Joint konumlarından", False),
            ("Faz sırası", section.phase_order, "Tasarım", True),
            ("Güzergâh referansı", section.route_reference, "CAD / proje", True),
        ])

    def _show_bonding_node_properties(self, index: int) -> None:
        node = self.project.bonding.nodes[index]
        self._set_property_rows([
            ("Düğüm ID", node.node_id, "Sistem", True),
            ("Düğüm adı", node.name, "Proje", True),
            ("Düğüm tipi", node.node_type, "Bonding ağı", True),
            ("Konum [m]", f"{node.position_m:g}", "CAD / proje", True),
            ("Topraklanmış", "Evet" if node.grounded else "Hayır", "Bonding topolojisi", True),
            ("Topraklama R [Ω]", f"{node.earth_resistance_ohm:g}", "Saha / tasarım", True),
        ])

    def _show_bonding_linkbox_properties(self, index: int) -> None:
        box = self.project.bonding.link_boxes[index]
        self._set_property_rows([
            ("Link Box ID", box.link_box_id, "Sistem", True),
            ("Link Box adı", box.name, "Proje", True),
            ("Bağlı joint", box.joint_node_id, "Bonding ağı", True),
            ("Link Box konumu [m]", f"{box.position_m:g}", "CAD / proje", True),
            ("Bonding lead uzunluğu [m]", f"{box.lead_length_m:g}", "Yerleşim", True),
            ("Bonding lead tipi", box.lead_type, "Tasarım", True),
            ("SVL içerir", "Evet" if box.contains_svl else "Hayır", "Tasarım", True),
            ("Erişilebilir", "Evet" if box.accessible else "Hayır", "Yerleşim", True),
            ("Atanmış SVL", box.svl_candidate_id or "Atanmadı", "SVL seçimi", True),
        ])

    def _show_fault_study_properties(self) -> None:
        study = self.project.fault_study
        result = self.fault_result
        self._set_property_rows([
            ("Arıza çalışma adı", study.name, "Proje", True),
            ("Arıza çözüm modu", study.solver_mode, "CIM/NV", True),
            ("SVL'ye otomatik TOV aktar", "Evet" if study.auto_transfer_worst_tov_to_svl else "Hayır", "İş akışı", True),
            ("TOV süre çarpanı", f"{study.tov_duration_multiplier:g}", "Koruma/reclose kriteri", True),
            ("Arızada dielektrik charging", "Evet" if study.include_dielectric_charging_during_fault else "Hayır", "Model", True),
            ("Yönetici TOV [V rms]", f"{result.governing_tov_rms_v:.3f}" if result else "Hesaplanmadı", "Hesap", False),
            ("Maksimum EPR [V]", f"{result.maximum_epr_v:.3f}" if result else "Hesaplanmadı", "Hesap", False),
        ])

    def _show_fault_scenario_properties(self, index: int) -> None:
        scenario = self.project.fault_study.scenarios[index]
        self._set_property_rows([
            ("Senaryo ID", scenario.scenario_id, "Proje", True),
            ("Senaryo adı", scenario.name, "Proje", True),
            ("Arıza tipi", scenario.fault_type, "Proje", True),
            ("Arıza akımı [A]", f"{scenario.fault_current_a:g}", "Kısa devre hesabı", True),
            ("Faz 1", scenario.faulted_phase, "Proje", True),
            ("Faz 2", scenario.second_phase, "Proje", True),
            ("Süre [s]", f"{scenario.duration_s:g}", "Koruma", True),
            ("Etkin", "Evet" if scenario.enabled else "Hayır", "Proje", True),
            ("Not", scenario.notes, "Proje", True),
        ])

    def _show_svl_properties(self) -> None:
        svl = self.project.svl
        result = self.svl_result
        self._set_property_rows([
            ("SVL sistemi", svl.name, "Proje", True),
            ("Bağlantı modu", svl.connection_mode, "Tasarım", True),
            ("Acil yük gerilim çarpanı", f"{svl.emergency_voltage_multiplier:g}", "Proje kriteri", True),
            ("Sürekli gerilim marjı [%]", f"{svl.continuous_voltage_margin_percent:g}", "Proje kriteri", True),
            ("Fault TOV [V rms]", f"{svl.fault_tov_rms_v:g}", "Kısa devre / EMT", True),
            ("Fault TOV süresi [s]", f"{svl.fault_tov_duration_s:g}", "Koruma açma", True),
            ("Gerekli enerji [kJ]", f"{svl.required_energy_kj:g}", "EMT", True),
            ("Gerekli deşarj akımı [kA]", f"{svl.required_discharge_current_ka:g}", "EMT / şartname", True),
            ("Akım yükselme hızı [kA/µs]", f"{svl.current_rise_ka_per_us:g}", "EMT", True),
            ("Lead endüktansı [µH/m]", f"{svl.lead_inductance_uh_per_m:g}", "Geometri / varsayım", True),
            ("Joint interrupt BIL [V peak]", f"{svl.joint_interrupt_impulse_withstand_peak_v:g}", "Aksesuar", True),
            ("Dış kılıf BIL [V peak]", f"{svl.jacket_impulse_withstand_peak_v:g}", "Kablo / aksesuar", True),
            ("Koruma seviyesi oranı", f"{svl.maximum_protective_level_fraction:g}", "Koordinasyon kriteri", True),
            ("Enerji marjı [%]", f"{svl.energy_margin_percent:g}", "Koordinasyon kriteri", True),
            ("Seçilen aday", svl.selected_candidate_id or "Atanmadı", "SVL seçimi", False),
            ("Öneri", result.recommended_display_name if result and result.has_recommendation else "Hesaplanmadı / uygun aday yok", "Hesap", False),
        ])

    def _show_iec_properties(self) -> None:
        limiting = min(self.iec_results, key=lambda r: r.ampacity_a) if self.iec_results else None
        self._set_property_rows([
            ("Motor", "IEC 60287 sürekli durum çekirdeği", "v0.8", False),
            ("Kapsam", "Otomatik/manüel T1-T4 + R, Wd ve λ girdileri", "Sistem", False),
            ("Sınırlayıcı bölüm", limiting.section_name if limiting else "Hesaplanmadı", "Hesap", False),
            ("Minimum ampacity [A]", f"{limiting.ampacity_a:.2f}" if limiting else "—", "Hesap", False),
            ("Doğrulama", "CIGRE TB 880 regresyonu bekliyor", "Kalite kapısı", False),
        ])

    def _show_installation_properties(self, cross_section_id: str = "") -> None:
        design = self.project.installation_design
        section = next(
            (item for item in design.cross_sections if item.cross_section_id == cross_section_id),
            None,
        )
        if section is None and design.cross_sections:
            section = next(
                (item for item in design.cross_sections if item.cross_section_id == design.active_cross_section_id),
                design.cross_sections[0],
            )
        issues = validate_installation_design(self.project)
        errors = sum(1 for item in issues if item.severity == "ERROR")
        warnings = sum(1 for item in issues if item.severity == "WARNING")
        rows = [
            ("Model", design.name, f"v{design.model_revision}", False),
            ("Kesit sayısı", str(len(design.cross_sections)), "Proje", False),
            ("Solver bağı", design.solver_coupling_mode, "Kapsam", False),
            ("Doğrulama", f"{errors} hata / {warnings} uyarı", "Kurulum modeli", False),
        ]
        if section is not None:
            rows.extend([
                ("Aktif kesit", f"{section.cross_section_id} — {section.name}", "Proje", False),
                ("Kurulum", section.installation_type, "Kesit", False),
                ("Yerleşim", section.arrangement_label, "Kesit", False),
                ("Devre", str(len(section.circuits)), "Kesit", False),
                ("Fiziksel kablo", str(len(section.physical_cables)), "Kesit", False),
                ("Duct slot", str(len(section.duct_slots)), "Kesit", False),
                ("Harici ısı kaynağı", str(len(section.external_heat_sources)), "Kesit", False),
                ("Termal bölge bağları", ", ".join(section.region_ids) or "Atanmadı", "Kesit", False),
            ])
        rows.append((
            "Kapsam sınırı",
            "Fiziksel model kaydedilir; IEC/bonding/nodal solver bağları v0.16.4-v0.16.5 kapsamındadır.",
            "Sürüm",
            False,
        ))
        self._set_property_rows(rows)

    def _show_thermal_properties(self) -> None:
        first = self.thermal_results[0] if self.thermal_results else None
        self._set_property_rows([
            ("Motor", "Termal direnç ön işlemcisi", "v0.8", False),
            ("İç model", self.project.cable.internal_thermal_mode, "Proje", False),
            ("Dış model", "Bölüm bazlı AUTO_IMAGE / MANUAL", "Proje", False),
            ("Sonuç", f"{len(self.thermal_results)} bölüm" if first else "Hesaplanmadı", "Hesap", False),
            ("Doğrulama", "CIGRE TB 880 regresyonu bekliyor", "Kalite kapısı", False),
        ])

    def _show_nodal_thermal_properties(self) -> None:
        result = self.nodal_thermal_result.active if self.nodal_thermal_result else None
        critical = None
        if result is not None:
            critical = next((r for r in result.regions if r.region_id == result.critical_region_id), None)
        self._set_property_rows([
            ("Motor", "2D hücre-merkezli sonlu hacim / kararlı durum", "v0.12", False),
            ("Çözülen senaryo", result.scenario_name if result else "Hesaplanmadı", "Hesap", False),
            ("Kritik bölge", critical.region_name if critical else "—", "Hesap", False),
            ("Nodal ampacity [A/kablo]", f"{critical.ampacity_per_cable_a:.2f}" if critical else "—", "Hesap", False),
            ("Maks. iletken sıcaklığı [°C]", f"{critical.maximum_conductor_temperature_c:.2f}" if critical else "—", "Hesap", False),
            ("Enerji dengesi hatası [%]", f"{critical.energy_balance_error_percent:.5f}" if critical else "—", "Doğrulama", False),
            ("Sınır", "Kararlı 2D orta kesit; HDD uçları ve transient 3D kapsam dışı", "Kapsam", False),
        ])

    def _tree_selection_changed(self) -> None:
        """Select and prepare context only; never open a window on one click."""
        items = self.project_tree.selectedItems()
        if not items:
            return
        item = items[0]
        kind, data = item.data(0, Qt.UserRole) or ("generic", None)
        if kind == "project":
            self._show_project_properties()
        elif kind == "cable":
            self._show_cable_properties()
        elif kind == "route" and data is not None:
            self._show_route_properties(int(data))
        elif kind in {"installation", "installation_section"}:
            self._show_installation_properties(str(data) if data else "")
        elif kind == "thermal":
            self._show_thermal_properties()
        elif kind in {"thermal_region", "thermal_template", "thermal_material"}:
            self._show_thermal_properties()
        elif kind == "nodal_thermal":
            self._show_nodal_thermal_properties()
        elif kind == "iec60287":
            self._show_iec_properties()
        elif kind == "bonding":
            self._show_bonding_properties()
        elif kind == "bonding_minor" and data is not None:
            self._show_bonding_minor_properties(int(data))
        elif kind == "bonding_node" and data is not None:
            self._show_bonding_node_properties(int(data))
        elif kind == "bonding_linkbox" and data is not None:
            self._show_bonding_linkbox_properties(int(data))
        elif kind == "fault_study":
            self._show_fault_study_properties()
        elif kind == "fault_scenario" and data is not None:
            self._show_fault_scenario_properties(int(data))
        elif kind in {"svl", "svl_candidate"}:
            self._show_svl_properties()
        elif kind not in {
            "workflow_stage", "workflow_group", "object_group", "result_group",
            "deliverable_group", "result", "report_builder", "procurement",
            "cable_library", "transient_thermal",
        }:
            self._set_property_rows([
                ("Nesne", item.text(0), "Model", False),
                ("Durum", "Ayrıntı proje nesnesi", "Sistem", False),
            ])
        self.statusBar().showMessage(
            f"{item.text(0)} seçildi — açmak için çift tıklayın.", 4000
        )

    def _tree_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Open/toggle the selected project-tree item only on double click."""
        kind, data = item.data(0, Qt.UserRole) or ("generic", None)
        if kind == "project":
            self._show_project_properties()
            self._show_main_canvas()
            self.statusBar().showMessage("Ana güzergâh / CAD çalışma alanı", 4000)
        elif kind == "workflow_stage":
            self._activate_workflow_stage(str(data))
        elif kind == "cable_library":
            self.show_cable_library()
        elif kind == "cable":
            self._show_cable_properties()
            self.show_assigned_cable()
        elif kind == "route" and data is not None:
            index = int(data)
            self._show_route_properties(index)
            self._show_workspace_widget(
                self.route_table_widget, "Güzergâh Bölümleri", workflow_frame=False
            )
            self.route_table.selectRow(index)
        elif kind in {"installation", "installation_section"}:
            section_id = str(data) if data else ""
            self._show_installation_properties(section_id)
            self.show_installation_designer(section_id)
        elif kind == "thermal":
            self._show_thermal_properties()
            self._show_workspace_widget(self.thermal_route_widget, "Termal Güzergâh ve Malzeme Girdileri")
        elif kind == "thermal_region" and data is not None:
            index = int(data)
            self._activate_workflow_stage("installation")
            self.thermal_editor_tabs.setCurrentIndex(0)
            self.thermal_region_table.selectRow(index)
        elif kind == "thermal_template" and data is not None:
            index = int(data)
            self._activate_workflow_stage("installation")
            self.thermal_editor_tabs.setCurrentIndex(1)
            self.thermal_template_table.selectRow(index)
        elif kind == "thermal_material" and data is not None:
            index = int(data)
            self._activate_workflow_stage("installation")
            self.thermal_editor_tabs.setCurrentIndex(2)
            self.thermal_material_table.selectRow(index)
        elif kind == "nodal_thermal":
            self._show_nodal_thermal_properties()
            self._activate_workflow_stage("steady_thermal")
        elif kind == "transient_thermal":
            self._activate_workflow_stage("transient")
        elif kind == "iec60287":
            self._show_iec_properties()
            self._activate_workflow_stage("steady_thermal")
        elif kind == "bonding":
            self._show_bonding_properties()
            self._activate_workflow_stage("bonding")
        elif kind == "bonding_minor" and data is not None:
            index = int(data)
            self._show_bonding_minor_properties(index)
            self._show_workspace_widget(self.bonding_table_widget, "Bonding Ağı")
            self.bonding_editor_tabs.setCurrentIndex(0)
            self.bonding_minor_table.selectRow(index)
        elif kind == "bonding_node" and data is not None:
            index = int(data)
            self._show_bonding_node_properties(index)
            self._show_workspace_widget(self.bonding_table_widget, "Bonding Ağı")
            self.bonding_editor_tabs.setCurrentIndex(1)
            self.bonding_node_table.selectRow(index)
        elif kind == "bonding_linkbox" and data is not None:
            index = int(data)
            self._show_bonding_linkbox_properties(index)
            self._show_workspace_widget(self.bonding_table_widget, "Bonding Ağı")
            self.bonding_editor_tabs.setCurrentIndex(2)
            self.bonding_linkbox_table.selectRow(index)
        elif kind == "fault_study":
            self._show_fault_study_properties()
            self._activate_workflow_stage("fault_epr")
        elif kind == "fault_scenario" and data is not None:
            index = int(data)
            self._show_fault_scenario_properties(index)
            self._show_workspace_widget(self.fault_table_widget, "Arıza / EPR")
            self.fault_scenario_table.selectRow(index)
        elif kind == "svl":
            self._show_svl_properties()
            self._activate_workflow_stage("svl")
        elif kind == "svl_candidate" and data is not None:
            index = int(data)
            self._show_svl_properties()
            self._activate_workflow_stage("svl")
            self.svl_candidate_table.selectRow(index)
        elif kind == "result":
            mapping = {
                "summary": self.summary_table,
                "thermal_resistance": self.thermal_result_table,
                "thermal_route": self.thermal_route_result_table,
                "nodal": self.nodal_result_table,
                "transient": self.transient_result_table,
                "bonding": self.bonding_result_table,
                "primitive": self.primitive_result_table,
                "fault": self.fault_result_table,
                "svl": self.svl_result_table,
                "iec60287": self.iec_result_table,
                "warnings": self.warning_list,
                "log": self.log_view,
                "validation": self.validation_view,
            }
            self.show_results_dialog(mapping.get(str(data), self.summary_table))
        elif kind == "report_builder":
            self.show_report_builder()
        elif kind == "procurement":
            self.show_procurement_builder()
        elif kind in {"workflow_group", "object_group", "result_group", "deliverable_group"}:
            item.setExpanded(not item.isExpanded())

    def _property_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        selected = self.project_tree.selectedItems()
        if not selected:
            return
        kind, data = selected[0].data(0, Qt.UserRole) or ("generic", None)
        key = self.property_table.item(item.row(), 0).text()
        value = item.text().strip()
        try:
            if kind == "project":
                mapping = {"Proje adı": "project_name", "Proje kodu": "project_code", "Standart profili": "standards_profile"}
                if key in mapping:
                    setattr(self.project, mapping[key], value)
            elif kind == "cable":
                c = self.project.cable
                text_map = {
                    "Kablo sistemi": "name", "İletken": "conductor_material", "Yalıtım": "insulation",
                    "Yerleşim": "arrangement", "İç termal mod": "internal_thermal_mode", "Metalik kılıf/ekran malzemesi": "sheath_material",
                    "Harici λ1″ kaynak türü": "sheath_eddy_external_source_type", "Harici λ1″ referansı": "sheath_eddy_external_reference",
                    "Harici λ1″ formasyon varsayımı": "sheath_eddy_external_formation_assumption",
                }
                number_map = {
                    "Sistem gerilimi [kV]": "voltage_kv", "Frekans [Hz]": "frequency_hz", "Tasarım akımı / kablo [A]": "design_current_a",
                    "Paralel kablo/faz": "parallel_cables_per_phase",
                    "Kesit [mm²]": "conductor_area_mm2", "Rdc20 [Ω/km] (0=otomatik)": "dc_resistance_20_ohm_km",
                    "α20 [1/°C]": "temperature_coefficient_20_per_c", "Skin faktörü ys": "skin_effect_factor",
                    "Proximity faktörü yp": "proximity_effect_factor", "Kapasitans [µF/km]": "capacitance_uf_km",
                    "tanδ": "dielectric_loss_tan_delta", "Legacy λ1 girdisi": "sheath_loss_factor",
                    "Zırh kayıp faktörü λ2": "armour_loss_factor", "T1 [K·m/W] manuel": "thermal_resistance_t1_km_w",
                    "T2 [K·m/W] manuel": "thermal_resistance_t2_km_w", "T3 [K·m/W] manuel": "thermal_resistance_t3_km_w",
                    "İletken çapı [mm]": "conductor_diameter_mm", "İletken GMR [mm] (0=otomatik)": "conductor_gmr_mm",
                    "T1 dış sınır çapı [mm]": "t1_outer_diameter_mm",
                    "T2 dış sınır çapı [mm]": "t2_outer_diameter_mm", "Kablo dış çapı [mm]": "overall_diameter_mm",
                    "T1 ρth [K·m/W]": "t1_thermal_resistivity_km_w", "T2 ρth [K·m/W]": "t2_thermal_resistivity_km_w",
                    "T3 ρth [K·m/W]": "t3_thermal_resistivity_km_w",
                    "Maks. sıcaklık [°C]": "max_temperature_c", "Referans ortam [°C]": "reference_ambient_c",
                    "Metalik kılıf/ekran kesiti [mm²]": "sheath_cross_section_mm2",
                    "Metalik kılıf Rdc20 [Ω/km] (0=otomatik)": "sheath_dc_resistance_20_ohm_km",
                    "Metalik kılıf α20 [1/°C]": "sheath_temperature_coefficient_20_per_c",
                    "Metalik kılıf işletme sıcaklığı [°C]": "sheath_operating_temperature_c",
                    "Metalik kılıf ortalama çapı [mm]": "sheath_mean_diameter_mm",
                    "Metalik kılıf GMR [mm] (0=otomatik)": "sheath_gmr_mm",
                    "Harici λ1″": "sheath_eddy_external_factor",
                    "Harici λ1″ referans frekansı [Hz]": "sheath_eddy_external_frequency_hz",
                    "Harici λ1″ referans kılıf sıcaklığı [°C]": "sheath_eddy_external_sheath_temperature_c",
                    "Harici λ1″ referans d [mm]": "sheath_eddy_external_d_mm",
                    "Harici λ1″ referans s [mm]": "sheath_eddy_external_s_mm",
                }
                if key in text_map:
                    setattr(c, text_map[key], value.upper() if key == "İç termal mod" else value)
                elif key in number_map:
                    parsed = self._parse_number(value)
                    setattr(c, number_map[key], int(parsed) if key == "Paralel kablo/faz" else parsed)
            elif kind == "route" and data is not None:
                rs = self.project.route_sections[int(data)]
                text_map = {
                    "Bölüm adı": "name", "Bölüm tipi": "section_type", "Kesit şablonu": "cross_section_id",
                    "T4 modu": "external_thermal_mode", "Not": "notes",
                }
                number_map = {
                    "Uzunluk [m]": "length_m", "Gömülme derinliği [m]": "burial_depth_m",
                    "Toprak ısıl özdirenci [K·m/W]": "soil_thermal_resistivity_km_w",
                    "Ortam sıcaklığı [°C]": "ambient_temperature_c", "Faz eksen aralığı [m]": "phase_spacing_m",
                    "T4 [K·m/W] manuel": "external_thermal_resistance_t4_km_w",
                }
                if key in text_map:
                    setattr(rs, text_map[key], value.upper() if key == "T4 modu" else value)
                elif key in number_map:
                    setattr(rs, number_map[key], self._parse_number(value))
            elif kind == "bonding":
                b = self.project.bonding
                text_map = {
                    "Bonding sistemi": "name", "Bonding şeması": "scheme",
                    "Bonding çözüm modu": "solver_mode", "Bonding hedef devresi": "target_circuit_id",
                }
                number_map = {
                    "Bonding hedef paralel grubu": "target_parallel_index",
                    "Legacy faz eksen aralığı [m]": "phase_spacing_m",
                    "Normal metalik kılıf gerilim limiti [V]": "normal_sheath_voltage_limit_v",
                    "Maks. bonding lead [m]": "maximum_bonding_lead_length_m",
                    "Maks. λ1": "maximum_lambda1",
                    "Optimizasyon iterasyonu": "optimization_max_iterations",
                    "Konum snap [m]": "optimization_snap_m",
                    "Link box temas R [mΩ]": "link_box_contact_resistance_mohm",
                    "Bonding lead sabit R [mΩ]": "bonding_lead_resistance_mohm",
                    "Bonding lead R [mΩ/m]": "bonding_lead_resistance_mohm_per_m",
                    "Toprak özdirenci [Ω·m]": "earth_resistivity_ohm_m",
                    "GCC/ECC kesiti [mm²]": "gcc_area_mm2",
                    "GCC/ECC X ofset [m]": "gcc_x_offset_m",
                    "GCC/ECC derinlik ofset [m]": "gcc_depth_offset_m",
                    "Bonding lead L [µH/m]": "bonding_lead_inductance_uh_per_m",
                }
                if key in text_map:
                    setattr(b, text_map[key], value.upper() if key in {"Bonding şeması", "Bonding çözüm modu"} else value)
                elif key in number_map:
                    parsed = self._parse_number(value)
                    setattr(
                        b, number_map[key],
                        int(parsed) if key in {"Optimizasyon iterasyonu", "Bonding hedef paralel grubu"} else parsed,
                    )
                elif key == "λ1 otomatik aktar":
                    b.auto_apply_lambda1 = value.lower() in {"evet", "yes", "1", "true", "aktif"}
                elif key == "Metalik kılıf karşılıklı bağlaşımı":
                    b.sheath_mutual_coupling_enabled = value.lower() in {"evet", "yes", "1", "true", "aktif"}
                elif key == "Dielektrik charging":
                    b.include_dielectric_charging = value.lower() in {"evet", "yes", "1", "true", "aktif"}
                elif key == "GCC/ECC":
                    b.gcc_enabled = value.lower() in {"etkin", "evet", "yes", "1", "true", "aktif"}
            elif kind == "bonding_minor" and data is not None:
                section = self.project.bonding.minor_sections[int(data)]
                text_map = {
                    "Minor section ID": "section_id", "Minor section adı": "name",
                    "Başlangıç düğümü": "start_node_id", "Bitiş düğümü": "end_node_id",
                    "Faz sırası": "phase_order", "Güzergâh referansı": "route_reference",
                }
                if key in text_map:
                    setattr(section, text_map[key], value.upper() if key == "Faz sırası" else value)
                elif key == "Major section":
                    section.major_index = int(self._parse_number(value))
            elif kind == "bonding_node" and data is not None:
                node = self.project.bonding.nodes[int(data)]
                text_map = {"Düğüm ID": "node_id", "Düğüm adı": "name", "Düğüm tipi": "node_type"}
                if key in text_map:
                    setattr(node, text_map[key], value.upper() if key == "Düğüm tipi" else value)
                elif key == "Konum [m]":
                    node.position_m = self._parse_number(value)
                    self._sync_minor_lengths_from_nodes()
                elif key == "Topraklama R [Ω]":
                    node.earth_resistance_ohm = self._parse_number(value)
                elif key == "Topraklanmış":
                    node.grounded = value.lower() in {"evet", "yes", "1", "true", "aktif"}
            elif kind == "bonding_linkbox" and data is not None:
                box = self.project.bonding.link_boxes[int(data)]
                text_map = {
                    "Link Box ID": "link_box_id", "Link Box adı": "name",
                    "Bağlı joint": "joint_node_id", "Bonding lead tipi": "lead_type",
                }
                if key in text_map:
                    setattr(box, text_map[key], value.upper() if key == "Bonding lead tipi" else value)
                elif key == "Link Box konumu [m]":
                    box.position_m = self._parse_number(value)
                elif key == "Bonding lead uzunluğu [m]":
                    box.lead_length_m = self._parse_number(value)
                elif key == "SVL içerir":
                    box.contains_svl = value.lower() in {"evet", "yes", "1", "true", "aktif"}
                elif key == "Erişilebilir":
                    box.accessible = value.lower() in {"evet", "yes", "1", "true", "aktif"}
                elif key == "Atanmış SVL":
                    box.svl_candidate_id = "" if value.lower() in {"atanmadı", "none", "yok"} else value
            elif kind == "fault_study":
                study = self.project.fault_study
                if key == "Arıza çalışma adı":
                    study.name = value
                elif key == "Arıza çözüm modu":
                    study.solver_mode = value.upper()
                elif key == "SVL'ye otomatik TOV aktar":
                    study.auto_transfer_worst_tov_to_svl = value.lower() in {"evet", "yes", "1", "true", "aktif"}
                elif key == "TOV süre çarpanı":
                    study.tov_duration_multiplier = self._parse_number(value)
                elif key == "Arızada dielektrik charging":
                    study.include_dielectric_charging_during_fault = value.lower() in {"evet", "yes", "1", "true", "aktif"}
            elif kind == "fault_scenario" and data is not None:
                scenario = self.project.fault_study.scenarios[int(data)]
                text_map = {
                    "Senaryo ID": "scenario_id", "Senaryo adı": "name", "Arıza tipi": "fault_type",
                    "Faz 1": "faulted_phase", "Faz 2": "second_phase", "Not": "notes",
                }
                if key in text_map:
                    setattr(scenario, text_map[key], value.upper() if key in {"Arıza tipi", "Faz 1", "Faz 2"} else value)
                elif key == "Arıza akımı [A]":
                    scenario.fault_current_a = self._parse_number(value)
                elif key == "Süre [s]":
                    scenario.duration_s = self._parse_number(value)
                elif key == "Etkin":
                    scenario.enabled = value.lower() in {"evet", "yes", "1", "true", "aktif"}
            elif kind == "svl":
                svl = self.project.svl
                text_map = {"SVL sistemi": "name", "Bağlantı modu": "connection_mode"}
                number_map = {
                    "Acil yük gerilim çarpanı": "emergency_voltage_multiplier",
                    "Sürekli gerilim marjı [%]": "continuous_voltage_margin_percent",
                    "Fault TOV [V rms]": "fault_tov_rms_v",
                    "Fault TOV süresi [s]": "fault_tov_duration_s",
                    "Gerekli enerji [kJ]": "required_energy_kj",
                    "Gerekli deşarj akımı [kA]": "required_discharge_current_ka",
                    "Akım yükselme hızı [kA/µs]": "current_rise_ka_per_us",
                    "Lead endüktansı [µH/m]": "lead_inductance_uh_per_m",
                    "Joint interrupt BIL [V peak]": "joint_interrupt_impulse_withstand_peak_v",
                    "Dış kılıf BIL [V peak]": "jacket_impulse_withstand_peak_v",
                    "Koruma seviyesi oranı": "maximum_protective_level_fraction",
                    "Enerji marjı [%]": "energy_margin_percent",
                }
                if key in text_map:
                    setattr(svl, text_map[key], value.upper() if key == "Bağlantı modu" else value)
                elif key in number_map:
                    setattr(svl, number_map[key], self._parse_number(value))
            self._invalidate_results()
            self._mark_dirty()
            self._build_tree()
            self._refresh_route_table()
            self._refresh_bonding_tables()
            self.bonding_view.draw_bonding_system(self.project.bonding, self.bonding_result)
            self._update_summary()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", f"{key} için geçerli bir değer girin.")
            self._tree_selection_changed()

    @staticmethod
    def _parse_number(value: str) -> float:
        return float(value.replace(" ", "").replace(",", "."))

    def _refresh_route_table(self) -> None:
        self.route_table.blockSignals(True)
        self.route_table.setRowCount(len(self.project.route_sections))
        for row, rs in enumerate(self.project.route_sections):
            values = [
                rs.name, f"{rs.length_m:g}", rs.section_type, f"{rs.burial_depth_m:g}",
                f"{rs.soil_thermal_resistivity_km_w:g}", rs.cross_section_id,
                f"{rs.ambient_temperature_c:g}", rs.external_thermal_mode, f"{rs.phase_spacing_m:g}",
                f"{rs.external_thermal_resistance_t4_km_w:g}", rs.notes,
            ]
            for col, value in enumerate(values):
                self.route_table.setItem(row, col, QTableWidgetItem(value))
        self.route_table.resizeColumnsToContents()
        self.route_table.horizontalHeader().setStretchLastSection(True)
        self.route_table.blockSignals(False)
        approved = self.project.workflow.stage_notes.get("route_approval") == "APPROVED"
        total = sum(item.length_m for item in self.project.route_sections)
        if approved:
            self.route_approval_label.setText(
                f"✓ Güzergâh proje verisi olarak kabul edildi · {len(self.project.route_sections)} bölüm · {total:.2f} m"
            )
            self.route_approval_label.setStyleSheet(
                "font-weight:700; padding:7px; background:#e8f7ed; border:1px solid #70b184;"
            )
        else:
            self.route_approval_label.setText(
                f"Güzergâh ön tasarım / kaynak verisi · {len(self.project.route_sections)} bölüm · {total:.2f} m · "
                "Kontrol edin ve Mevcut Güzergâhı Kabul Et komutunu kullanın."
            )
            self.route_approval_label.setStyleSheet(
                "font-weight:700; padding:7px; background:#fff8df; border:1px solid #d8bd58;"
            )

    def _route_table_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self.project.route_sections):
            return
        rs = self.project.route_sections[row]
        try:
            if col in (0, 2, 5, 7, 10):
                attr = {0: "name", 2: "section_type", 5: "cross_section_id", 7: "external_thermal_mode", 10: "notes"}[col]
                value = item.text().strip()
                setattr(rs, attr, value.upper() if col == 7 else value)
            else:
                attr = {
                    1: "length_m", 3: "burial_depth_m", 4: "soil_thermal_resistivity_km_w",
                    6: "ambient_temperature_c", 8: "phase_spacing_m", 9: "external_thermal_resistance_t4_km_w",
                }[col]
                setattr(rs, attr, self._parse_number(item.text()))
            self._invalidate_results()
            self._mark_dirty()
            self._build_tree()
            self._update_summary()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "Güzergâh tablosunda geçerli bir değer bekleniyor.")
            self._refresh_route_table()

    def _route_changed(self, message: str) -> None:
        self.project.workflow.stage_notes.pop("route_approval", None)
        self.project.design_progress.route = "PRELIMINARY"
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_all()
        self.statusBar().showMessage(message + " · Güzergâh yeniden kullanıcı onayı bekliyor.", 7000)

    def _add_route_section(self) -> None:
        idx = len(self.project.route_sections) + 1
        dialog = RouteSectionDialog(RouteSection(f"RS-{idx:02} Yeni bölüm", 100.0), self)
        self._fit_dialog_to_available_screen(dialog, 560, 620)
        if dialog.exec() != QDialog.Accepted or dialog.result_section is None:
            return
        self.project.route_sections.append(dialog.result_section)
        self._route_changed(f"{dialog.result_section.name} eklendi")
        self._show_workspace_widget(self.route_table_widget, "Güzergâh Bölümleri")
        self.route_table.selectRow(len(self.project.route_sections) - 1)

    def _edit_route_section(self) -> None:
        row = self.route_table.currentRow()
        if row < 0 or row >= len(self.project.route_sections):
            QMessageBox.information(self, "Bölüm seçin", "Düzenlemek için güzergâh listesinden bir satır seçin.")
            return
        dialog = RouteSectionDialog(self.project.route_sections[row], self)
        self._fit_dialog_to_available_screen(dialog, 560, 620)
        if dialog.exec() != QDialog.Accepted or dialog.result_section is None:
            return
        self.project.route_sections[row] = dialog.result_section
        self._route_changed(f"{dialog.result_section.name} güncellendi")
        self._show_workspace_widget(self.route_table_widget, "Güzergâh Bölümleri")
        self.route_table.selectRow(row)

    def _accept_route_source(self) -> None:
        if not self.project.route_sections or any(item.length_m <= 0 for item in self.project.route_sections):
            QMessageBox.warning(self, "Güzergâh", "Kabulden önce pozitif uzunluklu güzergâh bölümlerini tanımlayın.")
            return
        self.project.workflow.stage_notes["route_approval"] = "APPROVED"
        self.project.design_progress.route = "COMPLETE"
        self._mark_dirty()
        self._refresh_workflow()
        self._refresh_route_table()
        self._build_tree()
        QMessageBox.information(
            self,
            "Güzergâh kabul edildi",
            f"{len(self.project.route_sections)} bölüm proje tasarım verisi olarak kabul edildi.\n"
            "Kurulum ve termal kesit bilgileri ayrı aşamada doğrulanacaktır.",
        )

    def _remove_route_section(self) -> None:
        row = self.route_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Bölüm seçin", "Silmek için güzergâh listesinden bir satır seçin.")
            return
        name = self.project.route_sections[row].name
        answer = QMessageBox.question(
            self, "Güzergâh bölümünü sil", f"{name} silinsin mi?", QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return
        del self.project.route_sections[row]
        self._route_changed(f"{name} silindi")

    def _sync_minor_lengths_from_nodes(self) -> None:
        nodes = {node.node_id: node for node in self.project.bonding.nodes}
        ordered = sorted(self.project.bonding.minor_sections, key=lambda section: nodes.get(section.start_node_id, BondingNode("", "", 0.0)).position_m)
        self.project.bonding.minor_sections = ordered
        for index, section in enumerate(ordered):
            start = nodes.get(section.start_node_id)
            end = nodes.get(section.end_node_id)
            if start is not None and end is not None:
                section.length_m = max(0.0, end.position_m - start.position_m)
                section.major_index = index // 3 + 1

    @staticmethod
    def _bool_text(value: bool) -> str:
        return "Evet" if value else "Hayır"

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in {"evet", "yes", "1", "true", "aktif", "var"}

    def _minor_result_by_id(self) -> dict[str, object]:
        if self.bonding_result is None:
            return {}
        return {item.section_id: item for item in self.bonding_result.minor_results}

    def _refresh_fault_tables(self) -> None:
        scenarios = self.project.fault_study.scenarios
        self.fault_scenario_table.blockSignals(True)
        self.fault_scenario_table.setRowCount(len(scenarios))
        for row, scenario in enumerate(scenarios):
            values = [
                scenario.scenario_id, scenario.name, self._bool_text(scenario.enabled), scenario.fault_type,
                f"{scenario.fault_current_a:g}", scenario.faulted_phase, scenario.second_phase,
                f"{scenario.duration_s:g}", scenario.notes,
            ]
            for col, value in enumerate(values):
                self.fault_scenario_table.setItem(row, col, QTableWidgetItem(value))
        self.fault_scenario_table.resizeColumnsToContents()
        self.fault_scenario_table.horizontalHeader().setStretchLastSection(True)
        self.fault_scenario_table.blockSignals(False)

    def _fault_scenario_table_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row < 0 or row >= len(self.project.fault_study.scenarios):
            return
        scenario = self.project.fault_study.scenarios[row]
        try:
            text_fields = {0: "scenario_id", 1: "name", 3: "fault_type", 5: "faulted_phase", 6: "second_phase", 8: "notes"}
            if item.column() in text_fields:
                value = item.text().strip()
                if item.column() == 0 and not value:
                    raise ValueError
                setattr(scenario, text_fields[item.column()], value.upper() if item.column() in {3, 5, 6} else value)
            elif item.column() == 2:
                scenario.enabled = self._parse_bool(item.text())
            elif item.column() == 4:
                scenario.fault_current_a = self._parse_number(item.text())
            elif item.column() == 7:
                scenario.duration_s = self._parse_number(item.text())
            self._invalidate_results()
            self._mark_dirty()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "Arıza senaryo tablosunda geçerli bir değer bekleniyor.")
            self._refresh_fault_tables()

    def _add_fault_scenario(self) -> None:
        index = len(self.project.fault_study.scenarios) + 1
        self.project.fault_study.scenarios.append(
            FaultScenario(f"F-{index:02d}", f"Yeni Arıza Senaryosu {index}", FAULT_SINGLE_PHASE_GROUND, 31500.0, "A", "B", 0.50, True)
        )
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_fault_tables()
        self._build_tree()

    def _remove_fault_scenario(self) -> None:
        row = self.fault_scenario_table.currentRow()
        if row < 0 or row >= len(self.project.fault_study.scenarios):
            QMessageBox.information(self, "Arıza", "Silmek için senaryo tablosundan bir satır seçin.")
            return
        del self.project.fault_study.scenarios[row]
        self._invalidate_results()
        self._mark_dirty()
        self._refresh_fault_tables()
        self._build_tree()

    def _svl_model_changed(self) -> None:
        self.svl_result = None
        self.svl_result_table.setRowCount(0)
        self._mark_dirty()
        self._update_summary()

    def _refresh_svl_tables(self) -> None:
        svl = self.project.svl
        normal_value = (
            f"{self.bonding_result.max_standing_voltage_v:.3f}" if self.bonding_result else "Bonding hesabı gerekli"
        )
        rows = [
            ("Normal standing voltage [V rms]", normal_value, "Bonding hesabı", None),
            ("Bağlantı modu", svl.connection_mode, "Tasarım", "connection_mode"),
            ("Acil yük gerilim çarpanı", f"{svl.emergency_voltage_multiplier:g}", "Proje kriteri", "emergency_voltage_multiplier"),
            ("Sürekli gerilim marjı [%]", f"{svl.continuous_voltage_margin_percent:g}", "Proje kriteri", "continuous_voltage_margin_percent"),
            ("Fault TOV [V rms]", f"{svl.fault_tov_rms_v:g}", "Kısa devre / EMT", "fault_tov_rms_v"),
            ("Fault TOV süresi [s]", f"{svl.fault_tov_duration_s:g}", "Koruma açma + reclose", "fault_tov_duration_s"),
            ("Gerekli enerji [kJ]", f"{svl.required_energy_kj:g}", "EMT", "required_energy_kj"),
            ("Gerekli deşarj akımı [kA]", f"{svl.required_discharge_current_ka:g}", "EMT / şartname", "required_discharge_current_ka"),
            ("Akım yükselme hızı [kA/µs]", f"{svl.current_rise_ka_per_us:g}", "EMT", "current_rise_ka_per_us"),
            ("Lead endüktansı [µH/m]", f"{svl.lead_inductance_uh_per_m:g}", "Geometri / varsayım", "lead_inductance_uh_per_m"),
            ("Joint interrupt BIL [V peak]", f"{svl.joint_interrupt_impulse_withstand_peak_v:g}", "Aksesuar", "joint_interrupt_impulse_withstand_peak_v"),
            ("Dış kılıf BIL [V peak]", f"{svl.jacket_impulse_withstand_peak_v:g}", "Kablo / aksesuar", "jacket_impulse_withstand_peak_v"),
            ("Koruma seviyesi kullanım oranı", f"{svl.maximum_protective_level_fraction:g}", "Koordinasyon kriteri", "maximum_protective_level_fraction"),
            ("Enerji marjı [%]", f"{svl.energy_margin_percent:g}", "Koordinasyon kriteri", "energy_margin_percent"),
            ("Seçilen aday", svl.selected_candidate_id or "Atanmadı", "Seçim", None),
        ]
        self.svl_criteria_table.blockSignals(True)
        self.svl_criteria_table.setRowCount(len(rows))
        for row, (name, value, source, field_name) in enumerate(rows):
            cells = [QTableWidgetItem(name), QTableWidgetItem(value), QTableWidgetItem(source)]
            cells[0].setFlags(cells[0].flags() & ~Qt.ItemIsEditable)
            cells[2].setFlags(cells[2].flags() & ~Qt.ItemIsEditable)
            if field_name is None:
                cells[1].setFlags(cells[1].flags() & ~Qt.ItemIsEditable)
            cells[1].setData(Qt.UserRole, field_name)
            for col, cell in enumerate(cells):
                self.svl_criteria_table.setItem(row, col, cell)
        self.svl_criteria_table.resizeColumnsToContents()
        self.svl_criteria_table.horizontalHeader().setStretchLastSection(True)
        self.svl_criteria_table.blockSignals(False)

        self.svl_candidate_table.blockSignals(True)
        self.svl_candidate_table.setRowCount(len(svl.candidates))
        for row, candidate in enumerate(svl.candidates):
            values = [
                candidate.candidate_id, candidate.manufacturer, candidate.model, candidate.technology,
                f"{candidate.mcov_rms_v:g}", f"{candidate.tov_1s_rms_v:g}",
                f"{candidate.tov_10s_rms_v:g}", f"{candidate.tov_100s_rms_v:g}",
                f"{candidate.residual_voltage_peak_v:g}", f"{candidate.energy_capacity_kj:g}",
                f"{candidate.nominal_discharge_current_ka:g}", candidate.connection_options,
                candidate.source, candidate.notes,
            ]
            for col, value in enumerate(values):
                self.svl_candidate_table.setItem(row, col, QTableWidgetItem(value))
        self.svl_candidate_table.resizeColumnsToContents()
        self.svl_candidate_table.horizontalHeader().setStretchLastSection(True)
        self.svl_candidate_table.blockSignals(False)

    def _svl_criteria_table_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        field_name = item.data(Qt.UserRole)
        if not field_name:
            return
        try:
            if field_name == "connection_mode":
                setattr(self.project.svl, field_name, item.text().strip().upper())
            else:
                setattr(self.project.svl, field_name, self._parse_number(item.text()))
            self._svl_model_changed()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "SVL kriter tablosunda geçerli bir değer bekleniyor.")
            self._refresh_svl_tables()

    def _svl_candidate_table_changed(self, item: QTableWidgetItem) -> None:
        if item.row() >= len(self.project.svl.candidates):
            return
        candidate = self.project.svl.candidates[item.row()]
        text_fields = {
            0: "candidate_id", 1: "manufacturer", 2: "model", 3: "technology",
            11: "connection_options", 12: "source", 13: "notes",
        }
        number_fields = {
            4: "mcov_rms_v", 5: "tov_1s_rms_v", 6: "tov_10s_rms_v",
            7: "tov_100s_rms_v", 8: "residual_voltage_peak_v",
            9: "energy_capacity_kj", 10: "nominal_discharge_current_ka",
        }
        try:
            if item.column() in text_fields:
                value = item.text().strip()
                if item.column() == 0 and not value:
                    raise ValueError
                setattr(candidate, text_fields[item.column()], value)
            elif item.column() in number_fields:
                setattr(candidate, number_fields[item.column()], self._parse_number(item.text()))
            self._svl_model_changed()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "SVL aday tablosunda geçerli bir değer bekleniyor.")
            self._refresh_svl_tables()

    def _add_svl_candidate(self) -> None:
        index = len(self.project.svl.candidates) + 1
        self.project.svl.candidates.append(
            SvlCandidate(f"SVL-{index:02d}", "", f"Yeni Aday {index}", source="USER")
        )
        self._svl_model_changed()
        self._refresh_svl_tables()
        self._show_workspace_widget(self.svl_table_widget, "SVL Koordinasyonu")

    def _remove_svl_candidate(self) -> None:
        row = self.svl_candidate_table.currentRow()
        if row < 0 or row >= len(self.project.svl.candidates):
            QMessageBox.information(self, "SVL", "Silmek için aday tablosundan bir satır seçin.")
            return
        candidate_id = self.project.svl.candidates[row].candidate_id
        del self.project.svl.candidates[row]
        if self.project.svl.selected_candidate_id == candidate_id:
            self.project.svl.selected_candidate_id = ""
        for box in self.project.bonding.link_boxes:
            if box.svl_candidate_id == candidate_id:
                box.svl_candidate_id = ""
        self._svl_model_changed()
        self._refresh_svl_tables()

    def _refresh_bonding_tables(self) -> None:
        self._sync_minor_lengths_from_nodes()
        nodes = {node.node_id: node for node in self.project.bonding.nodes}
        minor_results = self._minor_result_by_id()

        self.bonding_node_table.blockSignals(True)
        self.bonding_node_table.setRowCount(len(self.project.bonding.nodes))
        for row, node in enumerate(sorted(self.project.bonding.nodes, key=lambda item: item.position_m)):
            values = [
                node.node_id, node.name, node.node_type, f"{node.position_m:g}",
                self._bool_text(node.grounded), f"{node.earth_resistance_ohm:g}",
            ]
            for col, value in enumerate(values):
                self.bonding_node_table.setItem(row, col, QTableWidgetItem(value))
        self.bonding_node_table.resizeColumnsToContents()
        self.bonding_node_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_node_table.blockSignals(False)

        self.bonding_minor_table.blockSignals(True)
        self.bonding_minor_table.setRowCount(len(self.project.bonding.minor_sections))
        for row, section in enumerate(self.project.bonding.minor_sections):
            start = nodes.get(section.start_node_id)
            end = nodes.get(section.end_node_id)
            result = minor_results.get(section.section_id)
            values = [
                section.section_id, str(section.major_index), section.start_node_id, section.end_node_id,
                f"{start.position_m:g}" if start else "—", f"{end.position_m:g}" if end else "—",
                f"{section.length_m:g}", section.phase_order,
                f"{result.max_open_circuit_voltage_v:.3f}" if result else "—",
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col in (4, 5, 6, 8):
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.bonding_minor_table.setItem(row, col, cell)
        self.bonding_minor_table.resizeColumnsToContents()
        self.bonding_minor_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_minor_table.blockSignals(False)

        self.bonding_linkbox_table.blockSignals(True)
        self.bonding_linkbox_table.setRowCount(len(self.project.bonding.link_boxes))
        for row, box in enumerate(sorted(self.project.bonding.link_boxes, key=lambda item: item.position_m)):
            values = [
                box.link_box_id, box.name, box.joint_node_id, f"{box.position_m:g}",
                f"{box.lead_length_m:g}", box.lead_type, self._bool_text(box.contains_svl),
                self._bool_text(box.accessible), box.svl_candidate_id or "—",
            ]
            for col, value in enumerate(values):
                self.bonding_linkbox_table.setItem(row, col, QTableWidgetItem(value))
        self.bonding_linkbox_table.resizeColumnsToContents()
        self.bonding_linkbox_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_linkbox_table.blockSignals(False)

        self.bonding_connection_table.blockSignals(True)
        self.bonding_connection_table.setRowCount(len(self.project.bonding.connections))
        for row, connection in enumerate(self.project.bonding.connections):
            status = (
                f"{connection.from_sheath}→{connection.to_sheath}"
                if connection.connection_type.upper() == "CROSS" else f"{connection.from_sheath}→Toprak"
            )
            values = [
                connection.link_box_id, connection.node_id, connection.from_sheath,
                connection.to_sheath, connection.connection_type, status,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 5:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.bonding_connection_table.setItem(row, col, cell)
        self.bonding_connection_table.resizeColumnsToContents()
        self.bonding_connection_table.horizontalHeader().setStretchLastSection(True)
        self.bonding_connection_table.blockSignals(False)

    def _bonding_node_table_changed(self, item: QTableWidgetItem) -> None:
        ordered = sorted(self.project.bonding.nodes, key=lambda node: node.position_m)
        if item.row() >= len(ordered):
            return
        node = ordered[item.row()]
        old_id = node.node_id
        try:
            col = item.column()
            if col == 0:
                new_id = item.text().strip()
                if not new_id:
                    raise ValueError
                node.node_id = new_id
                for section in self.project.bonding.minor_sections:
                    if section.start_node_id == old_id:
                        section.start_node_id = new_id
                    if section.end_node_id == old_id:
                        section.end_node_id = new_id
                for box in self.project.bonding.link_boxes:
                    if box.joint_node_id == old_id:
                        box.joint_node_id = new_id
                for connection in self.project.bonding.connections:
                    if connection.node_id == old_id:
                        connection.node_id = new_id
            elif col == 1:
                node.name = item.text().strip()
            elif col == 2:
                node.node_type = item.text().strip().upper()
            elif col == 3:
                node.position_m = self._parse_number(item.text())
                self._sync_minor_lengths_from_nodes()
            elif col == 4:
                node.grounded = self._parse_bool(item.text())
            elif col == 5:
                node.earth_resistance_ohm = self._parse_number(item.text())
            self._bonding_model_changed()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "Joint tablosunda geçerli bir değer bekleniyor.")
            self._refresh_bonding_tables()

    def _bonding_minor_table_changed(self, item: QTableWidgetItem) -> None:
        if item.row() >= len(self.project.bonding.minor_sections):
            return
        section = self.project.bonding.minor_sections[item.row()]
        try:
            col = item.column()
            if col == 0:
                section.section_id = item.text().strip()
            elif col == 1:
                section.major_index = int(self._parse_number(item.text()))
            elif col == 2:
                section.start_node_id = item.text().strip()
            elif col == 3:
                section.end_node_id = item.text().strip()
            elif col == 7:
                section.phase_order = item.text().strip().upper()
            self._sync_minor_lengths_from_nodes()
            self._bonding_model_changed()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "Minor section tablosunda geçerli bir değer bekleniyor.")
            self._refresh_bonding_tables()

    def _bonding_linkbox_table_changed(self, item: QTableWidgetItem) -> None:
        ordered = sorted(self.project.bonding.link_boxes, key=lambda box: box.position_m)
        if item.row() >= len(ordered):
            return
        box = ordered[item.row()]
        old_id = box.link_box_id
        try:
            col = item.column()
            if col == 0:
                box.link_box_id = item.text().strip()
                for connection in self.project.bonding.connections:
                    if connection.link_box_id == old_id:
                        connection.link_box_id = box.link_box_id
            elif col == 1:
                box.name = item.text().strip()
            elif col == 2:
                box.joint_node_id = item.text().strip()
            elif col == 3:
                box.position_m = self._parse_number(item.text())
            elif col == 4:
                box.lead_length_m = self._parse_number(item.text())
            elif col == 5:
                box.lead_type = item.text().strip().upper()
            elif col == 6:
                box.contains_svl = self._parse_bool(item.text())
            elif col == 7:
                box.accessible = self._parse_bool(item.text())
            elif col == 8:
                box.svl_candidate_id = "" if item.text().strip() in {"", "—", "-"} else item.text().strip()
            self._bonding_model_changed()
        except ValueError:
            QMessageBox.warning(self, "Geçersiz değer", "Link box tablosunda geçerli bir değer bekleniyor.")
            self._refresh_bonding_tables()

    def _bonding_connection_table_changed(self, item: QTableWidgetItem) -> None:
        if item.row() >= len(self.project.bonding.connections):
            return
        connection = self.project.bonding.connections[item.row()]
        col = item.column()
        if col == 0:
            connection.link_box_id = item.text().strip()
        elif col == 1:
            connection.node_id = item.text().strip()
        elif col == 2:
            connection.from_sheath = item.text().strip().upper()
        elif col == 3:
            connection.to_sheath = item.text().strip().upper()
        elif col == 4:
            connection.connection_type = item.text().strip().upper()
        self._bonding_model_changed()

    def _bonding_model_changed(self) -> None:
        self._invalidate_results()
        self._mark_dirty()
        self._build_tree()
        self._refresh_bonding_tables()
        self._refresh_fault_tables()
        self._refresh_svl_tables()
        self._refresh_transient_tables()
        self._populate_thermal_review_workspace()
        self.bonding_view.draw_bonding_system(self.project.bonding, None)
        self._update_summary()

    def _reset_equal_bonding_sections(self) -> None:
        total = sum(section.length_m for section in self.project.route_sections)
        old = self.project.bonding
        new = default_bonding_system(total)
        for attr in (
            "scheme", "phase_spacing_m", "target_circuit_id", "target_parallel_index",
            "link_box_contact_resistance_mohm",
            "bonding_lead_resistance_mohm", "bonding_lead_resistance_mohm_per_m",
            "auto_apply_lambda1", "normal_sheath_voltage_limit_v",
            "maximum_bonding_lead_length_m", "maximum_lambda1",
            "optimization_max_iterations", "optimization_snap_m",
            "solver_mode", "sheath_mutual_coupling_enabled",
            "earth_resistivity_ohm_m", "earth_return_model",
            "include_dielectric_charging", "compare_cim_nv",
            "bonding_lead_inductance_uh_per_m", "link_box_contact_inductance_uh",
            "ground_bus_contact_resistance_mohm", "minimum_branch_impedance_ohm",
            "gcc_enabled", "gcc_material", "gcc_area_mm2",
            "gcc_dc_resistance_20_ohm_km", "gcc_temperature_coefficient_20_per_c",
            "gcc_operating_temperature_c", "gcc_gmr_mm", "gcc_x_offset_m",
            "gcc_depth_offset_m", "gcc_ground_at_major_boundaries",
            "gcc_ground_at_link_boxes",
        ):
            setattr(new, attr, getattr(old, attr))
        self.project.bonding = new
        self._bonding_model_changed()
        self._show_workspace_widget(self.bonding_table_widget, "Bonding Ağı")

    def _toggle_bonding_focus(self) -> None:
        self.bonding_focus_mode = not self.bonding_focus_mode
        self.bonding_focus_button.setText("Normal Görünüm" if self.bonding_focus_mode else "Bonding Tam Ekran")
        self._show_workspace_widget(self.bonding_table_widget, "Bonding Ağı")
        if self.bonding_focus_mode:
            self.module_dialog.showMaximized()
            self.bonding_splitter.setSizes([680, 210])
        else:
            self.module_dialog.showNormal()

    def _add_bonding_joint(self) -> None:
        total = sum(section.length_m for section in self.project.route_sections)
        position, ok = QInputDialog.getDouble(
            self, "Sectionalizing Joint Ekle", "Güzergâh konumu [m]:", total / 2.0, 0.1, total - 0.1, 2
        )
        if not ok:
            return
        if any(abs(node.position_m - position) < 0.1 for node in self.project.bonding.nodes):
            QMessageBox.warning(self, "Konum kullanılıyor", "Bu konumda zaten bir bonding düğümü var.")
            return
        index = 1
        ids = {node.node_id for node in self.project.bonding.nodes}
        while f"J{index}" in ids:
            index += 1
        self.project.bonding.nodes.append(
            BondingNode(f"J{index}", f"Sectionalizing Joint {index}", position, "SECTIONALIZING_JOINT", 0.0, False)
        )
        self._rebuild_minors_from_nodes()
        self._bonding_model_changed()

    def _add_link_box(self) -> None:
        joints = [node for node in self.project.bonding.nodes if node.node_type.upper() == "SECTIONALIZING_JOINT"]
        if not joints:
            QMessageBox.information(self, "Joint gerekli", "Önce bir sectionalizing joint ekleyin.")
            return
        labels = [f"{node.node_id} — {node.position_m:.1f} m" for node in joints]
        selected, ok = QInputDialog.getItem(self, "Link Box Ekle", "Bağlanacak joint:", labels, 0, False)
        if not ok:
            return
        joint = joints[labels.index(selected)]
        if any(box.joint_node_id == joint.node_id for box in self.project.bonding.link_boxes):
            QMessageBox.warning(self, "Link box mevcut", "Bu joint'e bağlı bir link box zaten var.")
            return
        index = 1
        ids = {box.link_box_id for box in self.project.bonding.link_boxes}
        while f"LB{index}" in ids:
            index += 1
        self.project.bonding.link_boxes.append(
            BondingLinkBox(f"LB{index}", f"Link Box {index}", joint.node_id, joint.position_m, 3.0, "COAXIAL", True, True)
        )
        self._bonding_model_changed()

    def _rebuild_minors_from_nodes(self) -> None:
        nodes = sorted(self.project.bonding.nodes, key=lambda node: node.position_m)
        minors = []
        for index, (start, end) in enumerate(zip(nodes, nodes[1:]), start=1):
            minors.append(
                BondingMinorSection(
                    f"MS{index}", f"Minor Section {index}", start.node_id, end.node_id,
                    end.position_m - start.position_m, "ABC", "", (index - 1) // 3 + 1,
                )
            )
        self.project.bonding.minor_sections = minors

    def _apply_cross_bond_pattern(self) -> None:
        connections = []
        by_joint = {box.joint_node_id: box for box in self.project.bonding.link_boxes}
        ordered_nodes = sorted(self.project.bonding.nodes, key=lambda node: node.position_m)
        internal = [node for node in ordered_nodes if node.node_type.upper() == "SECTIONALIZING_JOINT"]
        for index, node in enumerate(internal, start=1):
            box = by_joint.get(node.node_id)
            if box is None:
                continue
            # Every third internal boundary closes a major section and is solidly grounded.
            if index % 3 == 0:
                node.grounded = True
                box.contains_svl = False
                for phase in "ABC":
                    connections.append(BondingConnection(box.link_box_id, node.node_id, phase, "G", "SOLID_GROUND"))
            else:
                node.grounded = False
                box.contains_svl = True
                connections.extend([
                    BondingConnection(box.link_box_id, node.node_id, "A", "B", "CROSS"),
                    BondingConnection(box.link_box_id, node.node_id, "B", "C", "CROSS"),
                    BondingConnection(box.link_box_id, node.node_id, "C", "A", "CROSS"),
                ])
        self.project.bonding.connections = connections
        self._rebuild_minors_from_nodes()
        self._bonding_model_changed()
        self.bonding_editor_tabs.setCurrentWidget(self.bonding_connection_table)

    def _remove_bonding_selected(self) -> None:
        tab = self.bonding_editor_tabs.currentWidget()
        if tab is self.bonding_node_table:
            row = self.bonding_node_table.currentRow()
            ordered = sorted(self.project.bonding.nodes, key=lambda node: node.position_m)
            if row < 0 or row >= len(ordered):
                return
            node = ordered[row]
            if node.node_type.upper() == "TERMINATION":
                QMessageBox.warning(self, "Silinemez", "Başlangıç/bitiş terminasyonu silinemez.")
                return
            self.project.bonding.nodes.remove(node)
            removed_boxes = {box.link_box_id for box in self.project.bonding.link_boxes if box.joint_node_id == node.node_id}
            self.project.bonding.link_boxes = [box for box in self.project.bonding.link_boxes if box.joint_node_id != node.node_id]
            self.project.bonding.connections = [c for c in self.project.bonding.connections if c.link_box_id not in removed_boxes]
            self._rebuild_minors_from_nodes()
        elif tab is self.bonding_linkbox_table:
            row = self.bonding_linkbox_table.currentRow()
            ordered = sorted(self.project.bonding.link_boxes, key=lambda box: box.position_m)
            if row < 0 or row >= len(ordered):
                return
            box = ordered[row]
            self.project.bonding.link_boxes.remove(box)
            self.project.bonding.connections = [c for c in self.project.bonding.connections if c.link_box_id != box.link_box_id]
        elif tab is self.bonding_connection_table:
            row = self.bonding_connection_table.currentRow()
            if row < 0 or row >= len(self.project.bonding.connections):
                return
            del self.project.bonding.connections[row]
        else:
            QMessageBox.information(self, "Joint üzerinden silin", "Minor section doğrudan silinmez; ilgili joint'i silin.")
            return
        self._bonding_model_changed()

    def _auto_design_cross_bonding(self) -> None:
        default = self.project.bonding.normal_sheath_voltage_limit_v
        limit, ok = QInputDialog.getDouble(
            self, "Otomatik Cross-Bond Tasarımı", "Normal işletmede maksimum metalik kılıf gerilimi [V]:",
            default, 1.0, 5000.0, 1,
        )
        if not ok:
            return
        try:
            synchronized_sections = resolve_project_bonding_route_sections(
                self.project, mutate_project=True
            )
            design = optimize_cross_bonding(
                self.project.cable, synchronized_sections, self.project.bonding, limit
            )
        except (BondingInputError, ThermalRouteInputError) as exc:
            QMessageBox.critical(self, "Otomatik bonding tasarım hatası", str(exc))
            return
        self.project.bonding = design.bonding
        self.bonding_result = design.calculation
        self.last_bonding_design = design
        if self.project.bonding.auto_apply_lambda1:
            # FAZ 6.1: λ1 is a derived scenario result, not a mutable thermal input.
            self.iec_results = []
            self.iec_result_table.setRowCount(0)
        self._mark_dirty()
        self._build_tree()
        self._refresh_bonding_tables()
        self._populate_bonding_results()
        self._populate_bonding_matrix_results()
        self._populate_primitive_results()
        self.bonding_view.draw_bonding_system(self.project.bonding, self.bonding_result)
        lines = [
            "OTOMATİK CROSS-BONDING İTERASYONU",
            "=" * 72,
            f"Minor section: {design.minor_section_count}",
            f"Major section: {design.major_section_count}",
            f"Başlangıç sınırları: {', '.join(f'{v:.1f}' for v in design.initial_boundaries_m)}",
            f"Optimize sınırlar: {', '.join(f'{v:.1f}' for v in design.optimized_boundaries_m)}",
            f"Maks. standing V: {design.calculation.max_standing_voltage_v:.3f} V / limit {limit:.3f} V",
            f"λ1: {design.calculation.lambda1:.8f}",
            "",
            *design.notes,
            "",
            "İterasyon izi:",
        ]
        lines.extend(
            f"M{it.major_index} i={it.iteration}: b1={it.boundary_1_m:.1f}, b2={it.boundary_2_m:.1f}, "
            f"J={it.objective:.6g}, Vmax={it.max_standing_voltage_v:.3f}, Eres={it.max_residual_emf_v:.3f}"
            for it in design.iterations
        )
        self.log_view.setPlainText("\n".join(lines))
        self.warning_list.setPlainText("\n".join(f"• {note}" for note in design.calculation.notes))
        self._update_summary()
        self._show_bonding_properties()
        self.statusBar().showMessage(
            f"Cross-bonding otomatik tasarlandı — {design.major_section_count} major / "
            f"{design.minor_section_count} minor, Vmax={design.calculation.max_standing_voltage_v:.1f} V", 10000
        )

    def show_project_cable_selection(self) -> None:
        self._activate_workflow_stage("cable", switch_workspace=False)
        dialog = ProjectCableSelectionDialog(
            self.project,
            catalog_library=self.database_project.cable_library,
            on_applied=self._on_project_cable_changed,
            open_database=self.show_cable_library,
            define_project_cable=self.show_new_project_cable_definition,
            parent=self,
        )
        self._fit_dialog_to_available_screen(dialog, 1160, 800)
        dialog.exec()

    def show_new_project_cable_definition(self) -> None:
        """Start a project-local cable definition without modifying the shared database."""
        self._activate_workflow_stage("cable", switch_workspace=False)
        self.cable_library_widget.begin_new_manual_cable()
        self._show_workspace_widget(self.cable_library_widget, "Bu Proje İçin Yeni Kablo Tanımla")

    def show_assigned_cable(self) -> None:
        if not (self.project.cable.snapshot_hash or self.project.cable_application.applied_snapshot_hash):
            QMessageBox.information(
                self, "Projeye atanmış kablo", "Projeye henüz kablo atanmadı. Önce Proje Kablosu Seç komutunu kullanın."
            )
            self.show_project_cable_selection()
            return
        self._activate_workflow_stage("cable", switch_workspace=False)
        self.cable_library_widget.refresh()
        self._show_workspace_widget(self.cable_library_widget, "Atanmış Kablo ve Veri Eksikleri")
        self._show_cable_properties()

    def show_cable_library(self) -> None:
        self.database_cable_widget.refresh()
        self.database_dialog.showNormal()
        self._fit_dialog_to_available_screen(self.database_dialog, 1400, 840)
        self.database_dialog.show()
        self.database_dialog.raise_()
        self.database_dialog.activateWindow()

    def show_thermal_material_library(self) -> None:
        dialog = ThermalMaterialLibraryDialog(self.project, self._on_installation_changed, self)
        self._fit_dialog_to_available_screen(dialog, 1380, 700)
        dialog.exec()

    def _show_database_placeholder(self, title: str) -> None:
        QMessageBox.information(
            self,
            f"Veri Tabanları — {title}",
            f"{title} veri tabanı bu menü altında yönetilecektir. Bu sürümde kablo veri tabanı aktiftir; "
            "diğer veri tabanı editörleri sonraki sürümlerde ayrı eleman editörleriyle açılacaktır.",
        )

    def _on_database_changed(self) -> None:
        try:
            save_application_cable_database(
                self.database_project.cable_library,
                self.application_database_path,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Kablo veri tabanı", f"Veri tabanı kaydedilemedi:\n{exc}")
            return
        self.statusBar().showMessage(
            f"Kablo veri tabanı güncellendi — {self.application_database_path}", 7000
        )

    def show_installation_designer(self, initial_section_id: str = "") -> None:
        if not isinstance(initial_section_id, str):
            initial_section_id = ""
        dialog = InstallationDesignerDialog(
            self.project, self._on_installation_changed, self, initial_section_id=initial_section_id
        )
        self._fit_dialog_to_available_screen(dialog, 1540, 900)
        dialog.exec()

    def _on_installation_changed(self) -> None:
        linked_regions = synchronize_installation_geometry(self.project)
        self._invalidate_results()
        mark_engine_runs_stale(
            self.project,
            list(PRODUCTION_GEOMETRY_ENGINE_IDS),
            "Kablo-Kanal fiziksel x-y veya kanal geometrisi değişti; geometriye bağlı bütün sonuçlar yeniden hesaplanmalıdır.",
        )
        self._mark_dirty()
        self._refresh_workflow()
        self._build_tree()
        self._update_summary()
        self.statusBar().showMessage(
            f"Kablo-Kanal geometrisi {len(linked_regions)} bölgeye bağlandı — yeniden hesap zorunlu", 12000
        )
        answer = QMessageBox.question(
            self,
            "Geometri değişti — yeniden hesap gerekli",
            "Kablo-Kanal düzeni üretim hesap girdilerine aktarıldı.\n\n"
            "IEC 60287, 2D nodal, bonding, Arıza/EPR, SVL, IEC 60853, rapor ve metraj sonuçları "
            "artık güncel değildir. Birleşik hesap akışı şimdi başlatılsın mı?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            QTimer.singleShot(0, self.run_combined_calculation)

    def show_catalog_comparison(self) -> None:
        physical_ampacity = None
        if self.nodal_thermal_result is not None and getattr(self.nodal_thermal_result, "method_validation", None) is not None:
            physical_ampacity = getattr(self.nodal_thermal_result.method_validation.active, "official_ampacity_a", None)
        if physical_ampacity is None and self.thermal_route_result is not None:
            physical_ampacity = getattr(self.thermal_route_result.active, "route_ampacity_a", None)
        dialog = CatalogComparisonDialog(
            self.project, physical_model_ampacity_a=physical_ampacity, parent=self
        )
        self._fit_dialog_to_available_screen(dialog, 1320, 820)
        dialog.exec()

    def _on_project_cable_changed(self) -> None:
        self._invalidate_results()
        self._mark_dirty()
        self.cross_section_view.draw_cross_section(
            self.project.cable.arrangement,
            self.project.cable.overall_diameter_mm,
            self.project.route_sections[0].phase_spacing_m if self.project.route_sections else 0.15,
        ) if hasattr(self.cross_section_view, "draw_cross_section") else None
        self._build_tree()
        self._update_summary()
        self._refresh_first_design()

    def _show_iteration_easter_egg(self, operation: str) -> None:
        if not self.act_yesilcam.isChecked():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("DiTuS — Vaziyet Al")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        layout = QHBoxLayout(dialog)
        mascot = QLabel()
        mascot_path = self.project_root / "assets" / "ditus_mascot.png"
        if mascot_path.exists():
            pixmap = QPixmap(str(mascot_path))
            if not pixmap.isNull():
                mascot.setPixmap(pixmap.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        mascot.setFixedSize(160, 160)
        mascot.setAlignment(Qt.AlignCenter)
        layout.addWidget(mascot)
        content = QVBoxLayout()
        heading = QLabel("Burası karışacak, vaziyet alın.")
        heading.setWordWrap(True)
        heading.setStyleSheet("font-size:16pt; font-weight:800; color:#173d5d;")
        info = QLabel(
            f"{operation} başlatılıyor. Program gerekli veri kapılarını kontrol edecek; "
            "teknik sonuçlar ve uyarılar profesyonel hesap metniyle gösterilecektir."
        )
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        content.addStretch(1)
        content.addWidget(heading)
        content.addWidget(info)
        content.addStretch(1)
        content.addWidget(buttons)
        layout.addLayout(content, 1)
        dialog.exec()

    def _cross_section_changed(self, positions: dict[str, tuple[float, float]]) -> None:
        values = ", ".join(f"{p}:({x:.0f},{y:.0f})" for p, (x, y) in positions.items())
        self.statusBar().showMessage(f"Kesit görünümü güncellendi — {values}; fiziksel metre ölçeği sonraki CAD/kesit bağında etkinleşecek", 5000)
        self._invalidate_results()
        self._mark_dirty()

    def _invalidate_results(self) -> None:
        had_results = bool(self.iec_results or self.thermal_results or self.thermal_route_result or self.nodal_thermal_result or self.transient_thermal_result or self.bonding_result or self.fault_result or self.svl_result)
        self.iec_results = []
        self.thermal_results = []
        self.thermal_route_result = None
        self.production_electrothermal_result = None
        self.production_bonding_result = None
        self.nodal_thermal_result = None
        self.transient_thermal_result = None
        self.bonding_result = None
        self.fault_result = None
        self.svl_result = None
        self.last_bonding_design = None
        self.last_mesh_convergence.clear()
        self.current_nodal_review_key = None
        self.iec_result_table.setRowCount(0)
        self.thermal_result_table.setRowCount(0)
        self.thermal_route_result_table.setRowCount(0)
        self.nodal_result_table.setRowCount(0)
        self.transient_result_table.setRowCount(0)
        self.transient_plot.draw_result(None)
        self._populate_thermal_review_workspace()
        self.bonding_result_table.setRowCount(0)
        self.bonding_matrix_table.setRowCount(0)
        self.primitive_result_table.setRowCount(0)
        self.fault_result_table.setRowCount(0)
        self.bonding_view.highlight_bonding_loop("")
        self.svl_result_table.setRowCount(0)
        if had_results:
            progress = self.project.design_progress
            for field_name in ("thermal", "bonding", "fault_epr", "svl"):
                if getattr(progress, field_name) in {"COMPLETE", "TRANSIENT_COMPLETE", "PASS", "CONDITIONAL"}:
                    setattr(progress, field_name, "STALE")
            progress.final_design = "NOT_READY"
            mark_engine_runs_stale(
                self.project,
                ["iec60287", "thermal_route", "nodal", "bonding", "fault_epr", "svl", "transient", "iteration", "report", "procurement"],
                "Bir hesap girdisi veya proje nesnesi değiştirildi.",
            )
            self.log_view.appendPlainText("Girdi değişti: termal, bonding, arıza/EPR, SVL ve IEC 60287 sonuçları güncelliğini kaybetti.")
            self._refresh_first_design()
            self._refresh_workflow()
            self._build_tree()

    def _update_summary(self) -> None:
        limiting = min(self.iec_results, key=lambda r: r.ampacity_a) if self.iec_results else None
        iec_status = f"{limiting.ampacity_a:.1f} A" if limiting else "Hesaplanmadı"
        iec_desc = f"Kritik: {limiting.section_name} / {limiting.status}" if limiting else "Otomatik/manüel T1-T4 ile v0.8 çekirdeği"
        bonding_status = f"λ1={self.bonding_result.lambda1:.6f}" if self.bonding_result else "Hesaplanmadı"
        bonding_desc = (
            f"{self.bonding_result.scheme}; maks. {self.bonding_result.max_standing_voltage_v:.1f} V"
            if self.bonding_result else "Cross/single-point/solid-bonding ön çözümü"
        )
        fault_status = (
            f"{self.fault_result.governing_tov_rms_v:.1f} V" if self.fault_result else "Hesaplanmadı"
        )
        fault_desc = (
            f"{self.fault_result.governing_scenario_name}; EPR {self.fault_result.maximum_epr_v:.1f} V"
            if self.fault_result else "3PH / PP / SLG power-frequency CIM/NV"
        )
        svl_status = (
            self.svl_result.recommended_display_name if self.svl_result and self.svl_result.has_recommendation
            else ("Uygun aday yok" if self.svl_result else "Hesaplanmadı")
        )
        svl_desc = (
            f"{self.svl_result.checks[0].status if self.svl_result and self.svl_result.checks else '—'}; "
            f"sürekli gerek {self.svl_result.continuous_required_rms_v:.1f} V"
            if self.svl_result else "MCOV/TOV/residual/enerji/deşarj kontrolleri"
        )
        if self.thermal_route_result:
            active_route = self.thermal_route_result.active
            if active_route.route_ampacity_a is not None:
                thermal_route_status = f"{active_route.route_ampacity_a:.1f} A/kablo"
            elif active_route.ampacity_upper_bound_a is not None:
                thermal_route_status = f"≤ {active_route.ampacity_upper_bound_a:.1f} A/kablo üst sınır"
            else:
                thermal_route_status = active_route.status
            critical_id = active_route.critical_region_id or active_route.provisional_critical_region_id or "—"
            thermal_route_desc = f"Kritik/geçici: {critical_id} / {active_route.status}"
        else:
            thermal_route_status = "Hesaplanmadı"
            thermal_route_desc = f"{len(self.project.thermal_design.regions)} chainage bazlı bölge"
        nodal_status = (
            f"{self.nodal_thermal_result.active.route_ampacity_per_cable_a:.1f} A/kablo"
            if self.nodal_thermal_result else "Hesaplanmadı"
        )
        nodal_desc = (
            f"Kritik: {self.nodal_thermal_result.active.critical_region_id}; "
            f"Tmax={self.nodal_thermal_result.active.maximum_conductor_temperature_c:.1f} °C"
            if self.nodal_thermal_result else "2D kararlı durum sonlu hacim çözümü"
        )
        transient_status = (
            f"{self.transient_thermal_result.route_cyclic_rating_per_cable_a:.1f} A/kablo"
            if self.transient_thermal_result else "Hesaplanmadı"
        )
        transient_desc = (
            f"Kritik çevrim: {self.transient_thermal_result.critical_cyclic_region_id}; "
            f"{self.project.transient_study.emergency_duration_h:g} h acil="
            f"{self.transient_thermal_result.route_emergency_rating_per_cable_a:.1f} A/kablo"
            if self.transient_thermal_result else "IEC 60853 iş akışlı 2D geçici sonlu hacim çözümü"
        )
        rows = [
            ("Proje", self.project.project_name, self.project.project_code),
            ("Sistem", f"{self.project.cable.voltage_kv:g} kV / {self.project.cable.frequency_hz:g} Hz", "Girdi"),
            ("Tasarım akımı", f"{self.project.cable.design_current_a:g} A", "Girdi"),
            ("Kablo", f"{self.project.cable.conductor_material} {self.project.cable.conductor_area_mm2:g} mm²", self.project.cable.arrangement),
            ("Güzergâh", f"{sum(s.length_m for s in self.project.route_sections):,.0f} m", f"{len(self.project.route_sections)} hesap bölümü"),
            ("CAD", "Yüklendi" if self.project.cad_source else "Örnek görünüm", self.project.cad_source or "DXF atanmadı"),
            ("Termal ön işlem", f"{len(self.thermal_results)} bölüm" if self.thermal_results else "Hesaplanmadı", "AUTO_GEOMETRY / AUTO_IMAGE / AUTO_MIXED_ZONE veya manuel"),
            ("Termal güzergâh", thermal_route_status, thermal_route_desc),
            ("IEC 60287", iec_status, iec_desc),
            ("Bonding", bonding_status, bonding_desc),
            ("Arıza / EPR", fault_status, fault_desc),
            ("SVL", svl_status, svl_desc),
            ("2D nodal termal", nodal_status, nodal_desc),
            ("IEC 60853 geçici", transient_status, transient_desc),
        ]
        self.summary_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.summary_table.setItem(r, c, cell)
        self.summary_table.resizeColumnsToContents()
        self.summary_table.horizontalHeader().setStretchLastSection(True)

    def validate_project(self) -> None:
        warnings: list[str] = []
        passes: list[str] = []
        c = self.project.cable
        cable_report = validate_cable(c)
        if cable_report.has_errors:
            warnings.extend(f"Kablo {issue.code}: {issue.message}" for issue in cable_report.issues if issue.severity == "ERROR")
        else:
            passes.append(f"Parametrik kablo konstrüksiyonu: {cable_report.status}.")
        warnings.extend(
            f"Kablo {issue.code}: {issue.message}"
            for issue in cable_report.issues if issue.severity == "WARNING"
        )
        checks = [
            (bool(self.project.project_name.strip()), "Proje adı tanımlı.", "Proje adı eksik."),
            (c.voltage_kv > 0, "Sistem gerilimi pozitif.", "Sistem gerilimi geçersiz."),
            (c.design_current_a > 0, "Tasarım akımı tanımlı.", "Tasarım akımı eksik veya geçersiz."),
            (c.conductor_area_mm2 > 0, "İletken kesiti tanımlı.", "İletken kesiti eksik."),
            (bool(self.project.route_sections), f"{len(self.project.route_sections)} güzergâh bölümü mevcut.", "Güzergâh bölümü yok."),
        ]
        for ok, pass_text, warning_text in checks:
            (passes if ok else warnings).append(pass_text if ok else warning_text)
        if c.internal_thermal_mode.upper() == INTERNAL_THERMAL_AUTO:
            if c.conductor_diameter_mm < c.t1_outer_diameter_mm < c.t2_outer_diameter_mm < c.overall_diameter_mm:
                passes.append("Eşdeğer kablo termal çapları artan sırada.")
            else:
                warnings.append("AUTO_GEOMETRY için çaplar artan sırada değil.")
        elif c.internal_thermal_mode.upper() != INTERNAL_THERMAL_MANUAL:
            warnings.append("İç termal mod AUTO_GEOMETRY veya MANUAL olmalı.")
        for rs in self.project.route_sections:
            if rs.external_thermal_mode.upper() in {EXTERNAL_THERMAL_AUTO, EXTERNAL_THERMAL_MIXED}:
                if rs.phase_spacing_m <= c.overall_diameter_mm / 1000.0:
                    warnings.append(f"{rs.name}: faz eksen aralığı kablo dış çapından büyük olmalı.")
                if rs.section_type.lower() not in {"standart hendek", "direct buried", "doğrudan gömülü", "dogrudan gomulu"}:
                    warnings.append(f"{rs.name}: AUTO_IMAGE özel bölüm tipini temsil etmez; MANUAL/nodal model kullanın.")
            elif rs.external_thermal_mode.upper() != EXTERNAL_THERMAL_MANUAL:
                warnings.append(f"{rs.name}: T4 modu AUTO_IMAGE, AUTO_MIXED_ZONE veya MANUAL olmalı.")
        thermal_issues = validate_thermal_design(self.project.thermal_design, c)
        thermal_errors = [issue for issue in thermal_issues if issue.severity == "ERROR"]
        if thermal_errors:
            warnings.extend(f"Termal bölge {issue.region_id}: {issue.message}" for issue in thermal_errors)
        else:
            passes.append(
                f"Termal güzergâh {len(self.project.thermal_design.regions)} bölgeyle boşluksuz ve çakışmasız tanımlı."
            )
        warnings.extend(
            f"Termal bölge {issue.region_id}: {issue.message}"
            for issue in thermal_issues if issue.severity == "WARNING"
        )
        b = self.project.bonding
        if b.scheme not in {BONDING_CROSS, BONDING_SINGLE_POINT, BONDING_SOLID_BOTH_END}:
            warnings.append("Bonding şeması CROSS_BONDED, SINGLE_POINT veya SOLID_BOTH_END olmalı.")
        if b.phase_spacing_m <= c.overall_diameter_mm / 1000.0:
            warnings.append("Bonding faz eksen aralığı kablo dış çapından büyük olmalı.")
        if b.scheme == BONDING_CROSS and len(b.minor_sections) % 3 != 0:
            warnings.append("Sectionalized cross-bonding için minor section sayısı üçün katı olmalı.")
        if b.normal_sheath_voltage_limit_v <= 0:
            warnings.append("Normal metalik kılıf gerilim limiti pozitif olmalı.")
        joint_ids = {node.node_id for node in b.nodes}
        for box in b.link_boxes:
            if box.joint_node_id not in joint_ids:
                warnings.append(f"{box.name}: bağlı joint bulunamadı ({box.joint_node_id}).")
            if box.lead_length_m > b.maximum_bonding_lead_length_m:
                warnings.append(f"{box.name}: bonding lead {box.lead_length_m:g} m, kriter {b.maximum_bonding_lead_length_m:g} m üzerinde.")
        valid_orders = {"".join(p) for p in [("A","B","C"),("A","C","B"),("B","A","C"),("B","C","A"),("C","A","B"),("C","B","A")]}
        for section in b.minor_sections:
            if section.length_m <= 0:
                warnings.append(f"{section.name}: uzunluk pozitif olmalı.")
            if section.phase_order.upper() not in valid_orders:
                warnings.append(f"{section.name}: faz sırası ABC permütasyonu olmalı.")
        if c.sheath_cross_section_mm2 <= 0 and c.sheath_dc_resistance_20_ohm_km <= 0:
            warnings.append("Bonding hesabı için metalik kılıf/ekran kesiti veya üretici metalik kılıf Rdc20 değeri gerekli.")
        if not self.project.cad_source:
            warnings.append("CAD kaynağı atanmadı; örnek güzergâh kullanılıyor.")
        if c.dc_resistance_20_ohm_km <= 0:
            warnings.append("Rdc20 üretici değeri girilmedi; Cu/Al nominal kesit yaklaşımı kullanılacak.")
        fault = self.project.fault_study
        if fault.solver_mode.upper() not in {"PRIMITIVE_CIM", "NODE_VOLTAGE"}:
            warnings.append("Arıza çözüm modu PRIMITIVE_CIM veya NODE_VOLTAGE olmalı.")
        if not any(scenario.enabled for scenario in fault.scenarios):
            warnings.append("Etkin arıza senaryosu yok.")
        for scenario in fault.scenarios:
            if scenario.enabled and (scenario.fault_current_a <= 0 or scenario.duration_s <= 0):
                warnings.append(f"{scenario.name}: arıza akımı ve süresi pozitif olmalı.")
        svl = self.project.svl
        if not svl.candidates:
            warnings.append("SVL aday listesi boş.")
        if svl.fault_tov_rms_v <= 0 or svl.fault_tov_duration_s <= 0:
            warnings.append("SVL fault-TOV gerilim/süre girdisi yok; TOV kontrolü CONDITIONAL kalır.")
        if svl.required_energy_kj <= 0:
            warnings.append("SVL enerji gereksinimi girilmedi; frequency-dependent EMT çalışması gereklidir.")
        if svl.current_rise_ka_per_us <= 0:
            warnings.append("SVL bonding-lead L·di/dt kontrolü için akım yükselme hızı girilmedi.")
        if any(candidate.source.upper().startswith("ILLUSTRATIVE") for candidate in svl.candidates):
            warnings.append("SVL listesinde ILLUSTRATIVE_TEST_DATA kayıtları var; üretici onaylı eğrilerle değiştirilmelidir.")
        warnings.extend([
            "AUTO_GEOMETRY eşdeğer konsantrik tek damarlı modeldir; ayrıntılı katman/katalog eşlemesi daha sonra yapılacaktır.",
            "AUTO_IMAGE homojen toprak, izotermal yüzey ve eşit faz kaybı varsayar; kuruma, kanal ve HDD dahil değildir.",
            "Skin/proximity faktörleri hâlen kullanıcı girdisidir.",
            "Bonding v0.9, primitive iletken/metalik-kılıf/opsiyonel GCC ağı için normal yük ve güç frekanslı arıza CIM/Node-Voltage çözer; toprak dönüşü simplified-Carson'dur.",
            "IEC/CIGRE TB 880 ve bonding referans vakaları regresyon doğrulaması tamamlanmadı; nihai tasarım için kullanmayın.",
        ])
        text = "ÖN DOĞRULAMA\n" + "=" * 65 + "\n\nGEÇEN KONTROLLER\n- " + "\n- ".join(passes)
        text += "\n\nUYARILAR / EKSİKLER\n- " + "\n- ".join(warnings)
        self.validation_view.setPlainText(text)
        self.warning_list.setPlainText("\n".join(f"• {w}" for w in warnings))
        self._show_results_widget(self.validation_view)
        self.log_view.appendPlainText("Ön doğrulama çalıştırıldı.")
        self.statusBar().showMessage(f"Doğrulama tamamlandı — {len(warnings)} uyarı", 5000)

    def run_thermal_preprocessor(self) -> None:
        self._activate_workflow_stage("installation")
        try:
            run = run_application_thermal_preprocessor(self.project)
        except ThermalRouteInputError as exc:
            QMessageBox.critical(self, "Termal ön işlem girdi hatası", str(exc))
            self.warning_list.setPlainText(str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Termal ön işlem hatası", f"Beklenmeyen hata:\n{exc}")
            return

        results = list(run.results)
        all_errors = list(run.errors)
        if not results and all_errors:
            self.thermal_results = []
            self.project.design_progress.thermal = "FAILED"
            self.warning_list.setPlainText("\n".join(all_errors))
            QMessageBox.critical(
                self, "Termal ön işlem başarısız",
                "Hiçbir bölüm çözülemedi.\n\n" + "\n".join(all_errors[:8]),
            )
            return

        self.thermal_results = results
        self.project.design_progress.thermal = "CONDITIONAL" if all_errors else "COMPLETE"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_2
        self._populate_thermal_results()
        self.log_view.clear()
        self.log_view.appendPlainText("TERMAL DİRENÇ ÖN İŞLEM İZİ\n" + "=" * 72)
        for result in results:
            self.log_view.appendPlainText("\nBölüm: " + result.section_name)
            for line in result.internal.trace + result.external.trace:
                self.log_view.appendPlainText(line)
            self.log_view.appendPlainText("-" * 72)
        if all_errors:
            self.log_view.appendPlainText("\nÇÖZÜLEMEYEN BÖLÜMLER\n" + "-" * 72)
            self.log_view.appendPlainText("\n".join(all_errors))
        self.warning_list.setPlainText("\n".join(f"• {item}" for item in all_errors))
        self._update_summary()
        self._refresh_first_design()
        self._show_thermal_properties()
        state = "kısmi" if all_errors else "tam"
        self.statusBar().showMessage(
            f"Termal ön işlem {state} tamamlandı — {len(results)} başarılı bölüm, {len(all_errors)} hata",
            7000,
        )

    def _populate_thermal_results(self) -> None:
        self.thermal_result_table.setRowCount(len(self.thermal_results))
        for row, result in enumerate(self.thermal_results):
            i, e = result.internal, result.external
            values = [
                result.section_name, f"{i.t1_km_w:.6f}", f"{i.t2_km_w:.6f}", f"{i.t3_km_w:.6f}",
                f"{e.effective_t4_km_w:.6f}", " / ".join(f"{v:.6f}" for v in e.phase_t4_km_w),
                i.source, e.source,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.thermal_result_table.setItem(row, col, cell)
        self.thermal_result_table.resizeColumnsToContents()
        self.thermal_result_table.horizontalHeader().setStretchLastSection(True)

    def _bonding_input_error_message(self, exc: Exception) -> str:
        """Translate scenario consistency failures into actionable project guidance."""
        raw = str(exc).strip()
        if "kesitler arasında devre akımı değişiyor" not in raw:
            return raw

        circuit_id = raw.split(":", 1)[0].strip()
        rows: list[tuple[str, str, float]] = []
        for section in self.project.installation_design.cross_sections:
            for circuit in section.circuits:
                if circuit.active and str(circuit.circuit_id) == circuit_id:
                    rows.append((section.cross_section_id, section.name, float(circuit.load_current_a)))
        if len(rows) < 2:
            return raw
        reference = rows[0][2]
        deltas = [abs(value - reference) for _sid, _name, value in rows[1:]]
        max_delta = max(deltas, default=0.0)
        scale = max(abs(reference), max((abs(value) for _sid, _name, value in rows), default=0.0), 1.0)
        relative = max_delta / scale
        detail = "\n".join(
            f"• {sid} — {name}: {value:.12f} A" for sid, name, value in rows
        )
        if relative <= 1e-5:
            reason = (
                "Fark çok küçük ve gösterim/yuvarlama kaynaklı bir veri round-trip tutarsızlığına benziyor. "
                "Kablo-Kanal geometrisi düzenlenirken devre akımı değiştirilmemelidir."
            )
        else:
            reason = (
                "Aynı sürekli elektriksel devre farklı fiziksel kesitlerde farklı RMS akımla tanımlanmış. "
                "Mevcut bonding senaryosu bunu tek sürekli devre olarak çözemiyor."
            )
        return (
            f"Devre akımı tutarsızlığı — {circuit_id}\n\n{detail}\n\n"
            f"Maksimum fark: {max_delta:.12f} A (%{relative * 100.0:.8f})\n\n"
            f"Neden: {reason}\n\n"
            "Çözüm: aynı sürekli devrenin kesitlerindeki çalışma akımını eşitleyin; gerçek bir tap/dallanma varsa "
            "onu ayrı elektriksel devre/topoloji olarak modelleyin."
        )

    def run_bonding_solver(self) -> None:
        if not self._confirm_engine_precheck("bonding"):
            return
        self._activate_workflow_stage("bonding")
        self._begin_engine_run("bonding", "Bonding/CIM çözümü çalışıyor.")
        try:
            run = run_bonding_production(self.project)
            production_electrothermal = run.electrothermal
            production_bonding = run.production
            result = run.legacy_diagnostic
        except (BondingInputError, ThermalRouteInputError, OperatingScenarioInputError) as exc:
            message = self._bonding_input_error_message(exc)
            self._fail_engine_run("bonding", message)
            QMessageBox.critical(self, "Bonding girdi hatası", message)
            self.warning_list.setPlainText(message)
            return
        except Exception as exc:
            self._fail_engine_run("bonding", f"Beklenmeyen hata: {exc}")
            QMessageBox.critical(self, "Bonding hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return

        self.bonding_result = result
        self.production_electrothermal_result = production_electrothermal
        self.production_bonding_result = production_bonding
        self.project.design_progress.bonding = "COMPLETE"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_3
        self._populate_bonding_results()
        self._populate_bonding_matrix_results()
        self._populate_primitive_results()
        self._refresh_bonding_tables()
        trace_text = (
            "IEEE/CIGRE ÜRETİM BONDING İZİ\n" + "=" * 72 + "\n\n"
            + "\n".join(production_bonding.trace_lines())
            + "\n\nLEGACY / TANISAL ÜÇ-LOOP KARŞILAŞTIRMASI\n" + "-" * 72 + "\n"
            + "\n".join(result.trace_lines())
        )
        if self.project.bonding.auto_apply_lambda1:
            trace_text += (
                "\n\nFAZ 6.1: λ1 proje kablosuna yazılmadı; senaryo bazlı "
                "elektro-termal kayıp vektöründen türetilecektir."
            )
        self.log_view.setPlainText(trace_text)
        warnings = list(result.notes)
        warnings.insert(0, "FAZ 6.6: üretim bonding otoritesi global N-core/N-kılıf ağdır; legacy üç-loop tablo yalnız tanısaldır.")
        if result.scheme == BONDING_CROSS and result.lambda1 < 1e-8:
            warnings.insert(0, "Faz metalik kılıfları arasındaki cross-bond yollarında major-section kalan EMF yaklaşık sıfırlandı.")
        if not result.voltage_limit_ok:
            warnings.insert(0, f"Standing voltage {result.max_standing_voltage_v:.1f} V, proje limiti {result.voltage_limit_v:.1f} V üzerinde.")
        if not result.lead_length_ok:
            warnings.insert(0, "Bir veya daha fazla link-box bonding lead uzunluğu proje kriterini aşıyor.")
        if result.lambda1 > self.project.bonding.maximum_lambda1:
            warnings.insert(0, f"λ1={result.lambda1:.6f}, proje kriteri {self.project.bonding.maximum_lambda1:.6f} üzerinde.")
        if result.primitive_network_result is not None:
            primitive = result.primitive_network_result
            if not primitive.methods_agree:
                warnings.insert(0, "CIM ve Node-Voltage sonuçları kabul toleransında uyuşmuyor.")
            warnings.append(
                "Primitive güç-frekansı çözümü aktiftir; toprak dönüşü simplified-Carson'dur. "
                "Tam Pollaczek/Wedepohl-Wilcox ve EMT doğrulaması sonraki katmandır."
            )
        else:
            warnings.append("Legacy loop-equivalent çözüm modu aktiftir; primitive ağ doğrulaması çalıştırılmadı.")
        self.warning_list.setPlainText("\n".join(f"• {w}" for w in warnings))
        self.bonding_view.draw_bonding_system(self.project.bonding, result)
        self._update_summary()
        self._show_bonding_properties()
        self.statusBar().showMessage(
            f"Bonding üretim çalışması tamamlandı — {len(production_bonding.scenarios)} senaryo; "
            f"legacy λ1={result.lambda1:.6f} yalnız tanısal",
            8000,
        )

    def _bonding_result_selection_changed(self) -> None:
        rows = self.bonding_result_table.selectionModel().selectedRows()
        if not rows:
            self.bonding_view.highlight_bonding_loop("")
            return
        row = rows[0].row()
        item = self.bonding_result_table.item(row, 0)
        self.bonding_view.highlight_bonding_loop(item.text() if item else "")

    def _populate_bonding_results(self) -> None:
        result = self.bonding_result
        production = getattr(self, "production_bonding_result", None)
        if production is not None and production.scenarios:
            self.bonding_result_table.setHorizontalHeaderLabels([
                "Senaryo", "Devre akımları", "Devre dışı", "Ish maks [A]", "Vsh-e maks [V]",
                "Vsh-sh maks [V]", "Kılıf kaybı [W]", "Network sheath-loss ratio", "Yöntem", "Durum"
            ])
            self.bonding_result_table.setRowCount(len(production.scenarios))
            for row, item in enumerate(production.scenarios):
                currents = ", ".join(f"{cid}:{amps:.1f}" for cid, amps in item.circuit_currents_a)
                values = [
                    item.scenario_id, currents, ",".join(item.deenergized_circuit_ids) or "—",
                    "—" if item.maximum_sheath_current_a is None else f"{item.maximum_sheath_current_a:.6f}",
                    "—" if item.maximum_sheath_to_earth_voltage_v is None else f"{item.maximum_sheath_to_earth_voltage_v:.6f}",
                    "—" if item.maximum_sheath_to_sheath_voltage_v is None else f"{item.maximum_sheath_to_sheath_voltage_v:.6f}",
                    "—" if item.total_sheath_metal_loss_w is None else f"{item.total_sheath_metal_loss_w:.6f}",
                    "N/A" if item.lambda1 is None else f"{item.lambda1:.8f}",
                    "GLOBAL N-CORE/N-SHEATH",
                    "PASS" if item.converged and item.methods_agree else (item.error_code or "INDETERMINATE"),
                ]
                for col, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    self.bonding_result_table.setItem(row, col, cell)
            self.bonding_result_table.resizeColumnsToContents()
            self.bonding_result_table.horizontalHeader().setStretchLastSection(True)
            return
        if result is None:
            self.bonding_result_table.setRowCount(0)
            self.bonding_matrix_table.setRowCount(0)
            self.primitive_result_table.setRowCount(0)
            return
        self.bonding_result_table.setHorizontalHeaderLabels([
            "Çevrim", "Kılıf yolu", "Residual EMF [V]", "Akım [A]", "Zloop [Ω]",
            "Kayıp [W]", "Minör Voc maks [V]", "λ1", "Çözücü", "Koşul"
        ])
        matrix_cond_by_major = {m.major_index: m.condition_number for m in result.major_matrix_results}
        self.bonding_result_table.setRowCount(len(result.loop_results))
        for row, loop in enumerate(result.loop_results):
            z_text = (
                "∞" if loop.loop_impedance_ohm.real == float("inf")
                else f"{loop.loop_impedance_ohm.real:.5f}+j{loop.loop_impedance_ohm.imag:.5f}"
            )
            values = [
                loop.loop_name,
                "→".join(loop.sheath_path),
                f"{loop.residual_emf_magnitude_v:.6f}",
                f"{loop.current_magnitude_a:.6f}",
                z_text,
                f"{loop.sheath_loss_w:.6f}",
                f"{loop.max_minor_open_circuit_voltage_v:.3f}",
                f"{result.lambda1:.8f}",
                result.solver_mode,
                f"{matrix_cond_by_major.get(loop.major_index, 1.0):.6g}",
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.bonding_result_table.setItem(row, col, cell)
        self.bonding_result_table.resizeColumnsToContents()
        self.bonding_result_table.horizontalHeader().setStretchLastSection(True)

    def _populate_bonding_matrix_results(self) -> None:
        result = self.bonding_result
        matrices = [] if result is None else list(result.major_matrix_results)
        self.bonding_matrix_table.setRowCount(len(matrices) * 3)
        row = 0
        for matrix in matrices:
            for index in range(3):
                z_values = matrix.impedance_matrix_ohm[index]
                source = matrix.source_vector_v[index]
                current = matrix.current_vector_a[index]
                values = [
                    f"M{matrix.major_index}",
                    matrix.loop_names[index],
                    f"{z_values[0].real:.6f}+j{z_values[0].imag:.6f}",
                    f"{z_values[1].real:.6f}+j{z_values[1].imag:.6f}",
                    f"{z_values[2].real:.6f}+j{z_values[2].imag:.6f}",
                    f"{abs(source):.6f}",
                    f"{abs(current):.6f}",
                    f"{matrix.condition_number:.6g}",
                ]
                for col, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    self.bonding_matrix_table.setItem(row, col, cell)
                row += 1
        self.bonding_matrix_table.resizeColumnsToContents()
        self.bonding_matrix_table.horizontalHeader().setStretchLastSection(True)

    def _populate_primitive_results(self) -> None:
        result = self.bonding_result
        primitive = None if result is None else result.primitive_network_result
        if primitive is None:
            self.primitive_result_table.setRowCount(0)
            return
        self.primitive_result_table.setRowCount(len(primitive.section_results))
        comparison = (
            f"PASS ΔV={primitive.maximum_method_voltage_difference_v:.2e}; "
            f"ΔI={primitive.maximum_method_current_difference_a:.2e}"
            if primitive.methods_agree
            else f"FAIL ΔV={primitive.maximum_method_voltage_difference_v:.2e}; "
                 f"ΔI={primitive.maximum_method_current_difference_a:.2e}"
        )
        for row, section in enumerate(primitive.section_results):
            values = [
                section.section_id, f"{section.major_index}",
                f"{abs(section.sheath_currents_a[0]):.6f}",
                f"{abs(section.sheath_currents_a[1]):.6f}",
                f"{abs(section.sheath_currents_a[2]):.6f}",
                f"{abs(section.gcc_current_a):.6f}",
                f"{section.max_sheath_voltage_v:.3f}",
                f"{section.sheath_metal_loss_w:.6f}",
                f"{section.earth_return_loss_w:.6f}",
                comparison if row == 0 else primitive.selected_method,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.primitive_result_table.setItem(row, col, cell)
        self.primitive_result_table.resizeColumnsToContents()
        self.primitive_result_table.horizontalHeader().setStretchLastSection(True)

    def run_fault_study(self) -> None:
        if not self._confirm_engine_precheck("fault_epr"):
            return
        self._activate_workflow_stage("fault_epr")
        self._begin_engine_run("fault_epr", "Arıza/EPR çözümü çalışıyor.")
        try:
            result = solve_fault_study(
                self.project.cable, self.project.bonding, self.project.route_sections, self.project.fault_study
            )
        except FaultStudyError as exc:
            self._fail_engine_run("fault_epr", str(exc))
            QMessageBox.critical(self, "Arıza / EPR girdi hatası", str(exc))
            self.warning_list.setPlainText(str(exc))
            return
        except Exception as exc:
            self._fail_engine_run("fault_epr", f"Beklenmeyen hata: {exc}")
            QMessageBox.critical(self, "Arıza / EPR hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self.fault_result = result
        self.project.design_progress.fault_epr = "COMPLETE"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_4
        fault_warnings = list(result.notes) + [note for scenario in result.scenario_results for note in scenario.notes]
        self._record_engine_status(
            "fault_epr", STATUS_COMPLETE if not fault_warnings else STATUS_CONDITIONAL,
            result_count=len(result.scenario_results), warning_count=len(fault_warnings),
            message=f"Arıza/EPR sonucu: {len(result.scenario_results)} senaryo",
            conditional_reasons=fault_warnings[:8],
        )
        if self.project.fault_study.auto_transfer_worst_tov_to_svl:
            transfer_fault_tov_to_svl(self.project.fault_study, result, self.project.svl)
            self.svl_result = None
            self.svl_result_table.setRowCount(0)
            mark_engine_runs_stale(
                self.project, ["svl", "iteration", "report", "procurement"],
                "Arıza/EPR hesabı SVL TOV görevini güncelledi.",
            )
            self._refresh_svl_tables()
        self._populate_fault_results()
        self.log_view.setPlainText(
            "ARIZA / EPR / POWER-FREQUENCY TOV HESAP İZİ\n"
            + "=" * 72 + "\n\n" + "\n".join(result.trace_lines())
        )
        warnings = list(result.notes)
        if not result.all_methods_agree:
            warnings.insert(0, "CIM ve Node-Voltage arıza sonuçları kabul toleransında uyuşmuyor.")
        if not self.project.bonding.gcc_enabled:
            warnings.insert(0, "GCC/ECC devre dışı; özellikle tek faz-toprak arıza dönüş yolu sonucu şartlıdır.")
        if self.project.fault_study.auto_transfer_worst_tov_to_svl:
            warnings.insert(0,
                f"SVL TOV görevi güncellendi: {self.project.svl.fault_tov_rms_v:.1f} V rms / "
                f"{self.project.svl.fault_tov_duration_s:.3f} s."
            )
        self.warning_list.setPlainText("\n".join(f"• {text}" for text in warnings))
        self._show_fault_study_properties()
        self._update_summary()
        self._refresh_first_design()
        self.statusBar().showMessage(
            f"Arıza/EPR tamamlandı — {result.governing_scenario_name}: "
            f"TOV {result.governing_tov_rms_v:.1f} V, EPR {result.maximum_epr_v:.1f} V",
            9000,
        )

    def _populate_fault_results(self) -> None:
        result = self.fault_result
        if result is None:
            self.fault_result_table.setRowCount(0)
            return
        self.fault_result_table.setRowCount(len(result.scenario_results))
        for row, scenario in enumerate(result.scenario_results):
            comparison = (
                f"PASS ΔV={scenario.cim_nv_voltage_difference_v:.1e}; ΔI={scenario.cim_nv_current_difference_a:.1e}"
                if scenario.methods_agree
                else f"FAIL ΔV={scenario.cim_nv_voltage_difference_v:.1e}; ΔI={scenario.cim_nv_current_difference_a:.1e}"
            )
            values = [
                f"{scenario.name} [{scenario.scenario_id}]", scenario.fault_type,
                f"{scenario.fault_current_a:.1f}", f"{scenario.duration_s:.3f}",
                f"{scenario.maximum_sheath_current_a:.3f}", f"{scenario.maximum_gcc_current_a:.3f}",
                f"{scenario.maximum_sheath_to_local_ground_v:.3f}",
                f"{scenario.maximum_sectionalizing_interrupt_v:.3f}",
                f"{scenario.maximum_epr_v:.3f}", f"{scenario.maximum_earth_electrode_current_a:.3f}",
                f"{scenario.total_sheath_metal_loss_w:.3f}", f"{scenario.total_earth_return_loss_w:.3f}",
                comparison,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.fault_result_table.setItem(row, col, cell)
        self.fault_result_table.resizeColumnsToContents()
        self.fault_result_table.horizontalHeader().setStretchLastSection(True)

    def _transfer_fault_tov_to_svl(self) -> None:
        if self.fault_result is None:
            self.run_fault_study()
        if self.fault_result is None:
            return
        try:
            voltage, duration = transfer_fault_tov_to_svl(
                self.project.fault_study, self.fault_result, self.project.svl
            )
        except FaultStudyError as exc:
            QMessageBox.critical(self, "TOV aktarım hatası", str(exc))
            return
        self.svl_result = None
        mark_engine_runs_stale(
            self.project, ["svl", "iteration", "report", "procurement"],
            "Arıza görevi SVL TOV girdilerine aktarıldı.",
        )
        self._mark_dirty()
        self._refresh_svl_tables()
        self._refresh_workflow()
        self._build_tree()
        self.statusBar().showMessage(
            f"Arıza görevi SVL'ye aktarıldı: {voltage:.1f} V rms / {duration:.3f} s", 7000
        )

    def run_svl_selection(self) -> None:
        if not self._confirm_engine_precheck("svl"):
            return
        self._activate_workflow_stage("svl")
        self._begin_engine_run("svl", "SVL boyutlandırma ve seçim motoru çalışıyor.")
        if self.bonding_result is None:
            try:
                self.bonding_result = solve_project_bonding(self.project)
                self._populate_bonding_results()
                self._populate_bonding_matrix_results()
                self._populate_primitive_results()
                self.bonding_view.draw_bonding_system(self.project.bonding, self.bonding_result)
            except BondingInputError as exc:
                self._fail_engine_run("svl", str(exc))
                QMessageBox.critical(self, "SVL için bonding girdi hatası", str(exc))
                return
            except Exception as exc:
                self._fail_engine_run("svl", f"Bonding ön çözüm hatası: {exc}")
                QMessageBox.critical(self, "SVL için bonding hesap hatası", f"Beklenmeyen hata:\n{exc}")
                return

        try:
            result = solve_svl_selection(
                self.project.svl,
                self.project.bonding,
                self.bonding_result.max_standing_voltage_v,
            )
        except SvlInputError as exc:
            self._fail_engine_run("svl", str(exc))
            QMessageBox.critical(self, "SVL girdi hatası", str(exc))
            self.warning_list.setPlainText(str(exc))
            return
        except Exception as exc:
            self._fail_engine_run("svl", f"Beklenmeyen hata: {exc}")
            QMessageBox.critical(self, "SVL seçim hatası", f"Beklenmeyen hata:\n{exc}")
            return

        self.svl_result = result
        self.project.design_progress.svl = "PASS" if result.has_recommendation else "CONDITIONAL"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_4
        svl_notes = list(result.notes)
        self._record_engine_status(
            "svl", STATUS_COMPLETE if result.has_recommendation and not svl_notes else STATUS_CONDITIONAL,
            result_count=len(result.checks), warning_count=len(svl_notes),
            message="SVL önerisi üretildi." if result.has_recommendation else "Uygun SVL önerisi üretilemedi.",
            conditional_reasons=svl_notes[:8],
        )
        self._populate_svl_results()
        self._refresh_svl_tables()
        self.log_view.setPlainText(
            "SVL BOYUTLANDIRMA VE YALITIM KOORDİNASYONU İZİ\n"
            + "=" * 72 + "\n\n" + "\n".join(result.trace_lines())
        )
        warnings = list(result.notes)
        failed = [check for check in result.checks if check.status == "FAIL"]
        conditional = [check for check in result.checks if check.status == "CONDITIONAL"]
        if failed:
            warnings.insert(0, f"{len(failed)} SVL adayı en az bir zorunlu kontrolden kaldı.")
        if conditional:
            warnings.insert(0, f"{len(conditional)} SVL adayı eksik fault/EMT veya üretici verisi nedeniyle CONDITIONAL.")
        if result.has_recommendation:
            warnings.insert(0, f"Ön öneri: {result.recommended_display_name} [{result.recommended_candidate_id}].")
        else:
            warnings.insert(0, "Mevcut adaylar arasında önerilebilir SVL bulunamadı.")
        self.warning_list.setPlainText("\n".join(f"• {w}" for w in warnings))
        self._show_svl_properties()
        self._update_summary()
        self._refresh_first_design()
        self.statusBar().showMessage(
            f"SVL ön seçimi tamamlandı — "
            f"{result.recommended_display_name if result.has_recommendation else 'öneri yok'}",
            8000,
        )

    def _populate_svl_results(self) -> None:
        result = self.svl_result
        if result is None:
            self.svl_result_table.setRowCount(0)
            return
        self.svl_result_table.setRowCount(len(result.checks))
        for row, check in enumerate(result.checks):
            tov_text = (
                f"{check.tov_withstand_rms_v:.0f}/{check.tov_required_rms_v:.0f} V"
                if check.tov_withstand_rms_v is not None and check.tov_required_rms_v > 0
                else "Bekliyor"
            )
            protection_text = (
                f"{check.protective_level_peak_v:.0f}/{check.protected_limit_peak_v:.0f} V"
                if check.protected_limit_peak_v is not None else "Bekliyor"
            )
            energy_text = (
                f"{check.energy_capacity_kj:.2f}/{check.required_energy_kj:.2f} kJ"
                if check.required_energy_kj > 0 else "Bekliyor"
            )
            discharge_text = (
                f"{check.nominal_discharge_current_ka:.2f}/{check.required_discharge_current_ka:.2f} kA"
                if check.required_discharge_current_ka > 0 else "Bekliyor"
            )
            details = "; ".join(check.failed_checks or check.pending_checks)
            values = [
                f"{check.display_name} [{check.candidate_id}]",
                check.status,
                f"{check.mcov_rms_v:.1f}",
                f"{check.continuous_required_rms_v:.1f}",
                tov_text,
                f"{check.residual_voltage_peak_v:.1f}",
                f"{check.lead_inductive_drop_peak_v:.1f}",
                protection_text,
                energy_text,
                discharge_text,
                details,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.svl_result_table.setItem(row, col, cell)
        self.svl_result_table.resizeColumnsToContents()
        self.svl_result_table.horizontalHeader().setStretchLastSection(True)

    def _assign_recommended_svl(self) -> None:
        if self.svl_result is None:
            self.run_svl_selection()
        if self.svl_result is None or not self.svl_result.has_recommendation:
            QMessageBox.information(self, "SVL", "Atanabilecek bir SVL önerisi bulunmuyor.")
            return
        candidate_id = self.svl_result.recommended_candidate_id
        self.project.svl.selected_candidate_id = candidate_id
        assigned = 0
        for box in self.project.bonding.link_boxes:
            if box.contains_svl:
                box.svl_candidate_id = candidate_id
                assigned += 1
        self._mark_dirty()
        self._refresh_svl_tables()
        self._refresh_bonding_tables()
        self._show_svl_properties()
        self.statusBar().showMessage(
            f"{candidate_id}, SVL bulunan {assigned} link box'a atandı.", 6000
        )

    def run_combined_calculation(self) -> None:
        if not self._confirm_engine_precheck("iteration"):
            return
        self._activate_workflow_stage("iteration")
        self._show_iteration_easter_egg("Birleşik hesap")
        if self.project.cable_application.application_status != "NOT_APPLIED":
            gates = evaluate_application_iteration_gates(self.project)
            if gates.status == "BLOCKED":
                QMessageBox.warning(
                    self,
                    "Birleşik iterasyon bloke",
                    "Kablo uygulama veya kaynak veri kapılarında bloke eden maddeler var. "
                    "Proje → Kablo Uygulama / İterasyon Kapıları ekranını inceleyin.\n\n"
                    + "\n".join(gates.trace[1:]),
                )
                return
            if gates.status == "CONDITIONAL_READY":
                answer = QMessageBox.question(
                    self,
                    "Koşullu birleşik iterasyon",
                    "Üretici teyidi veya mühendislik varsayımı gerektiren veriler var. "
                    "Sonuçlar nihai uygunluk sayılmadan iterasyon çalıştırılsın mı?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
        previous_suppression = self._suppress_engine_precheck
        self._suppress_engine_precheck = True
        self._begin_engine_run("iteration", "Birleşik tasarım iterasyonu çalışıyor.")
        try:
            self.run_bonding_solver()
            if self.bonding_result is None:
                self._fail_engine_run("iteration", "Bonding çözümü tamamlanamadı.")
                return
            self.run_fault_study()
            if self.fault_result is None:
                self._fail_engine_run("iteration", "Arıza/EPR çözümü tamamlanamadı.")
                return
            self.run_svl_selection()
            self.run_thermal_route_analysis()
            if self.thermal_route_result is not None:
                self.run_nodal_thermal_analysis()
            if self.nodal_thermal_result is not None:
                self.run_transient_thermal_analysis()
            child_failures = [
                name for name, value in (
                    ("Bonding", self.bonding_result),
                    ("Arıza/EPR", self.fault_result),
                    ("Termal güzergâh", self.thermal_route_result),
                ) if value is None
            ]
            if child_failures:
                self._fail_engine_run(
                    "iteration", "Tamamlanamayan alt motorlar: " + ", ".join(child_failures)
                )
            else:
                warnings = []
                if self.svl_result is None:
                    warnings.append("SVL sonucu üretilemedi veya veri kapısı bloke oldu.")
                if self.transient_thermal_result is None:
                    warnings.append("IEC 60853 sonucu üretilmedi.")
                self._record_engine_status(
                    "iteration",
                    STATUS_CONDITIONAL if warnings else STATUS_COMPLETE,
                    result_count=5 - len(warnings),
                    warning_count=len(warnings),
                    message="Birleşik tasarım iterasyonu tamamlandı.",
                    conditional_reasons=warnings,
                )
        finally:
            self._suppress_engine_precheck = previous_suppression

    def run_iec60287(self) -> None:
        """Run IEC 60287 through the chainage-based thermal route model."""
        self.run_thermal_route_analysis()

    def _populate_iec_results(self) -> None:
        self.iec_result_table.setRowCount(len(self.iec_results))
        for row, r in enumerate(self.iec_results):
            values = [
                r.section_name, f"{r.ampacity_a:.2f}", f"{r.margin_a:.2f}",
                f"{r.conductor_temperature_at_design_c:.2f}", f"{r.ac_resistance_ohm_km:.6f}",
                f"{r.t4_km_w:.6f}", f"{r.internal_thermal_source} / {r.external_thermal_source}",
                f"{r.total_loss_at_design_w_m:.3f}", r.status,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.iec_result_table.setItem(row, col, cell)
        self.iec_result_table.resizeColumnsToContents()
        self.iec_result_table.horizontalHeader().setStretchLastSection(True)

    def show_solver_status(self) -> None:
        message = (
            f"v{APP_VERSION} aktif hesap, workflow ve çıktı katmanları:\n\n"
            "• IEC 60287 bölgesel kararlı durum ve 2D nodal termal çözüm\n"
            "• IEC 60853 iş akışlı transient/cyclic/emergency rating\n"
            "• IEEE 575 bonding ön tasarımı ve CIGRE TB 797 yönelimli Primitive CIM\n"
            "• Node-Voltage bağımsız doğrulama ve Coupled Loop Matrix karşılaştırması\n"
            "• Arıza/EPR, metalik ekran dayanımı ve SVL ön koordinasyonu\n"
            "• Hesap/proje raporu, BOQ/BOM, makara planı ve RFQ çıktıları\n"
            "• Merkezi HARD/SOFT hesap ön kontrolü, girdi imzası ve dört boyutlu workflow durumu\n\n"
            "Yöntem sınırları:\n"
            "• Toprak dönüşü halen SIMPLIFIED_CARSON; tam Pollaczek/Wedepohl-Wilcox/Ametani yok\n"
            "• IEC 60853-3 kısmi toprak kuruması/yeniden nemlenme henüz yok\n"
            "• HDD giriş/çıkışı, joint bay ve kesit geçişleri için yerel 3D çözüm henüz yok\n"
            "• Frequency-dependent EMT ve doğrusal olmayan MOV zaman alanı enerjisi henüz yok\n"
            "• Parametrik chainage ölçekli cross-bonding diyagramı sonraki editör sürümündedir\n\n"
            "Koşullu/ön model sonuçları üretici ve saha verileriyle doğrulanmadan nihai tasarım olarak kullanılmamalıdır."
        )
        QMessageBox.information(self, "Hesap motoru durumu", message)

    def import_dxf(self) -> None:
        self._activate_workflow_stage("route")
        path, _ = QFileDialog.getOpenFileName(self, "DXF Dosyası Seç", str(Path.home()), "DXF (*.dxf)")
        if not path:
            return
        try:
            geometry = read_dxf_geometry(path)
            self.plan_view.load_dxf(geometry)
            self.project.cad_source = str(Path(path).resolve())
            imported_length = geometry_total_length(geometry)
            if imported_length > 0:
                self.project.design_basis.route_input_mode = "DXF"
                self.project.design_basis.total_route_length_m = imported_length
                self.project.thermal_design.route_length_m = imported_length
                if len(self.project.thermal_design.regions) == 1:
                    self.project.thermal_design.regions[0].start_m = 0.0
                    self.project.thermal_design.regions[0].end_m = imported_length
                self.project.design_progress.route = "PRELIMINARY"
            self._mark_dirty()
            self._refresh_all()
            self._show_main_canvas()
            self.log_view.appendPlainText(
                f"DXF yüklendi: {path}\nKatman: {len(geometry.layers)}, çizgi: {len(geometry.lines)}, "
                f"polyline: {len(geometry.polylines)}, daire: {len(geometry.circles)}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "DXF içe aktarma hatası", f"Dosya okunamadı:\n{exc}")

    def show_start_dialog(self) -> None:
        dialog = StartDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.choice == StartDialog.NEW_DESIGN:
            self.run_project_wizard()
        elif dialog.choice == StartDialog.OPEN_PROJECT:
            self.open_project()
        elif dialog.choice == StartDialog.REVIEW_DESIGN:
            self.new_project()
        elif dialog.choice == StartDialog.SYNTHETIC_20KM_CASE:
            self.open_bundled_synthetic_case()

    def run_project_wizard(self) -> None:
        self._activate_workflow_stage("system_load", switch_workspace=False)
        if not self._confirm_discard():
            return
        wizard = NewDesignWizard(self)
        if wizard.exec() != QDialog.Accepted or wizard.result_project is None:
            return
        self.project = wizard.result_project
        self.current_file = None
        self.dirty = True
        self.iec_results = []
        self.thermal_results = []
        self.thermal_route_result = None
        self.production_electrothermal_result = None
        self.production_bonding_result = None
        self.nodal_thermal_result = None
        self.transient_thermal_result = None
        self.bonding_result = None
        self.fault_result = None
        self.svl_result = None
        self.last_bonding_design = None
        self.plan_view.clear_geometry()
        if self.project.cad_source:
            try:
                self.plan_view.load_dxf(read_dxf_geometry(self.project.cad_source))
            except Exception as exc:
                self.plan_view.draw_sample_route()
                self.project.design_progress.missing_data.append(f"DXF yeniden açılamadı: {exc}")
        else:
            self.plan_view.draw_sample_route()
        self._refresh_all()
        self._update_title()
        self._show_workspace_widget(self.first_design_widget, "İlk Tasarım")
        if wizard.run_first_iteration:
            self.run_first_design_iteration()

    def run_first_design_iteration(self) -> None:
        """Produce a recommendation without silently changing the project cable."""
        if not self._confirm_engine_precheck("precheck"):
            return
        self._activate_workflow_stage("precheck")
        self._show_iteration_easter_egg("İlk tasarım iterasyonu")
        self.project.cable_application.last_iteration_status = "RUNNING"
        self.project.cable_application.last_iteration_trace = ["İlk tasarım adayları değerlendiriliyor."]
        self._record_engine_status("precheck", STATUS_RUNNING, message="İlk tasarım adayları değerlendiriliyor.")
        self._refresh_workflow()
        self._build_tree()

        basis = self.project.design_basis
        try:
            load = apply_load_calculation(basis)
            self.project.design_progress.system_load = "COMPLETE"
            if not basis.candidates:
                generate_generic_candidates(basis)
            if not basis.candidates:
                raise FirstDesignInputError("Uygun başlangıç kablo adayı üretilemedi.")
        except FirstDesignInputError as exc:
            self.project.design_progress.system_load = "MISSING"
            self.project.cable_application.last_iteration_status = "BLOCKED"
            self.project.cable_application.last_iteration_trace = [str(exc)]
            self._record_engine_status("precheck", STATUS_BLOCKED, warning_count=1, message=str(exc))
            self._refresh_first_design()
            self._refresh_workflow()
            self._build_tree()
            QMessageBox.warning(self, "İlk tasarım iterasyonu", str(exc))
            return

        recommended = max(
            basis.candidates,
            key=lambda item: (item.estimated_margin_a >= 0, item.estimated_margin_a, -item.conductor_area_mm2),
        )
        # Selection/recommendation is not an assignment. Keep the active project
        # cable untouched until the user explicitly confirms Projeye Ata.
        basis.selected_candidate_id = ""
        self.project.cable_application.selected_candidate_id = recommended.candidate_id
        self.project.cable_application.selected_catalog_record_id = ""
        self.project.cable_application.last_iteration_status = "CONDITIONAL_READY"
        self.project.cable_application.last_iteration_trace = [
            f"Tasarım akımı {load.design_current_per_circuit_a:.3f} A/devre olarak hesaplandı.",
            f"Önerilen kaba aday: {recommended.label}.",
            "Aday projeye atanmadı; kullanıcı kararı bekleniyor.",
            "IEC 60853, SVL, ayrıntılı arıza/EPR ve bonding bu ilk iterasyonda çalıştırılmadı.",
        ]
        self.project.design_progress.cable = "PRELIMINARY"
        self.project.design_progress.thermal = "NOT_RUN"
        self.project.design_progress.bonding = "NOT_RUN"
        self.project.design_progress.fault_epr = "NOT_RUN"
        self.project.design_progress.svl = "NOT_RUN"
        self.project.design_progress.final_design = "NOT_READY"
        self.project.design_progress.maturity_level = MATURITY_LEVEL_1
        self._record_engine_status(
            "precheck",
            STATUS_CONDITIONAL,
            result_count=len(basis.candidates),
            warning_count=1,
            message="İlk tasarım adayları üretildi; proje kablosu için kullanıcı kararı bekleniyor.",
            conditional_reasons=["Önerilen aday projeye henüz atanmadı."],
        )
        self._mark_dirty()
        self._refresh_first_design()
        self._refresh_workflow()
        self._build_tree()
        self._show_workspace_widget(self.first_design_widget, "İlk Tasarım — Aday Önerisi")
        # Select the recommendation visibly.
        for row, candidate in enumerate(basis.candidates):
            if candidate.candidate_id == recommended.candidate_id:
                self.first_candidate_table.selectRow(row)
                break
        QMessageBox.information(
            self,
            "İlk tasarım sonucu",
            "İlk tasarım değerlendirmesi tamamlandı.\n\n"
            f"Önerilen aday: {recommended.label}\n"
            f"Tasarım akımı: {load.design_current_per_circuit_a:.3f} A/devre\n"
            f"Ön ampacity: {recommended.estimated_ampacity_a:.1f} A\n"
            f"Ön marj: {recommended.estimated_margin_a:+.1f} A\n\n"
            "Bu adım proje kablosunu değiştirmedi. Adayı inceleyip Seçili Adayı Projeye Ata komutuyla açıkça onaylayın.\n"
            "Sonraki aşamalar: kurulum/termal kesit doğrulaması ve ardından ayrıntılı hesaplar.",
        )
        self.statusBar().showMessage(
            f"İlk tasarım tamamlandı — önerilen aday {recommended.label}; projeye atama için kullanıcı onayı bekleniyor.",
            12000,
        )

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "Yeni Proje", "Proje adı:", text="Yeni DiTuS Kablo Projesi")
        if not ok:
            return
        self.project = ProjectData(project_name=name.strip() or "Yeni DiTuS Kablo Projesi")
        self.current_file = None
        self.dirty = False
        self.iec_results = []
        self.thermal_results = []
        self.thermal_route_result = None
        self.production_electrothermal_result = None
        self.production_bonding_result = None
        self.nodal_thermal_result = None
        self.transient_thermal_result = None
        self.bonding_result = None
        self.fault_result = None
        self.svl_result = None
        self.last_bonding_design = None
        self.plan_view.clear_geometry()
        self.plan_view.draw_sample_route()
        self._refresh_all()
        self._update_title()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Projeyi Aç", str(Path.home()), "UCD Projesi (*.ucd.json);;JSON (*.json)")
        if not path:
            return
        self._load_project_path(Path(path))

    def _load_project_path(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.project = ProjectData.from_dict(raw)
            self.project.schema_version = "0.16.4"
            self.current_file = path
            self.dirty = False
            self.iec_results = []
            self.thermal_results = []
            self.thermal_route_result = None
            self.nodal_thermal_result = None
            self.transient_thermal_result = None
            self.bonding_result = None
            self.fault_result = None
            self.svl_result = None
            self.last_bonding_design = None
            self._refresh_all()
            self._update_title()
            self.log_view.appendPlainText(f"Proje açıldı: {path}")
            if self.project.source_audit.source_name:
                report = audit_project_sources(self.project)
                self.warning_list.appendPlainText(
                    f"\nKAYNAK VERİ DENETİMİ — {report.status}\n"
                    f"Çelişki: {report.issue_count}; nihai tasarım eksik verisi: {report.missing_data_count}"
                )
        except Exception as exc:
            QMessageBox.critical(self, "Proje açılamadı", str(exc))

    def open_bundled_synthetic_case(self) -> None:
        if not self._confirm_discard():
            return
        case_path = self.project_root / "examples" / "synthetic_20km_line.ucd.json"
        try:
            if not case_path.exists():
                raise FileNotFoundError("Paket içinde sentetik 20 km örnek proje bulunamadı.")
            self._load_project_path(case_path)
            # Paket örneği salt başlangıç şablonudur; kullanıcı kaydetmeyi
            # seçmedikçe paket içindeki dosyanın üzerine yazılmaz.
            self.current_file = None
            self.dirty = False
            self.statusBar().showMessage(
                "Sentetik 20 km örnek hat açıldı — gerçek proje veya saha verisi içermez.",
                10000,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Sentetik örnek açılamadı", str(exc))

    def show_calculation_policy(self) -> None:
        bootstrap_calculation_policy(self.project)
        dialog = ParameterProvenanceDialog(self.project, self._mark_dirty, self)
        dialog.exec()

    def show_multiconductor_em(self) -> None:
        dialog = MulticonductorEMDialog(self.project, self)
        self._fit_dialog_to_available_screen(dialog, 1450, 860)
        dialog.exec()

    def show_multiconductor_thermal(self) -> None:
        dialog = MulticonductorThermalDialog(self.project, self)
        self._fit_dialog_to_available_screen(dialog, 1450, 860)
        dialog.exec()

    def show_electrothermal_coupled(self) -> None:
        dialog = ElectroThermalCoupledDialog(self.project, self)
        self._fit_dialog_to_available_screen(dialog, 1500, 900)
        dialog.exec()

    def show_shadow_validation(self) -> None:
        dialog = ShadowValidationDialog(self.project, self)
        self._fit_dialog_to_available_screen(dialog, 1550, 920)
        dialog.exec()

    def show_physical_parameters(self) -> None:
        dialog = CablePhysicalParametersDialog(self.project, self._mark_dirty, self)
        self._fit_dialog_to_available_screen(dialog, 1450, 830)
        dialog.exec()

    def show_source_audit(self) -> None:
        report = audit_project_sources(self.project)
        dialog = QDialog(self)
        dialog.setWindowTitle("Kaynak Veri Tutarlılık Denetimi")
        fit_window(dialog, self._density_for(920, 680), center_on=self)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(render_source_audit(report))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._fit_dialog_to_available_screen(dialog, 920, 680)
        dialog.exec()

    def show_cable_application_gates(self) -> None:
        summary = evaluate_application_iteration_gates(self.project)
        dialog = QDialog(self)
        dialog.setWindowTitle("Kablo Uygulama / İterasyon Kapıları")
        fit_window(dialog, self._density_for(900, 620), center_on=self)
        layout = QVBoxLayout(dialog)
        heading = QLabel(f"Durum: {summary.status}")
        heading.setStyleSheet("font-size: 13pt; font-weight: 700; padding: 7px; background: #eaf0f5;")
        layout.addWidget(heading)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(summary.trace))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._fit_dialog_to_available_screen(dialog, 900, 620)
        dialog.exec()

    def show_report_builder(self) -> None:
        self._activate_workflow_stage("deliverables", switch_workspace=False)
        results = CalculationResultsBundle(
            iec_results=tuple(self.iec_results),
            production_electrothermal_result=self.production_electrothermal_result,
            production_bonding_result=self.production_bonding_result,
            nodal_thermal_result=self.nodal_thermal_result,
            transient_thermal_result=self.transient_thermal_result,
            bonding_result=self.bonding_result,
            fault_result=self.fault_result,
            svl_result=self.svl_result,
        )
        dialog = ReportBuilderDialog(self.project, results, self)
        self._fit_dialog_to_available_screen(dialog, 1280, 820)
        dialog.exec()
        self._mark_dirty()
        self._refresh_workflow()
        self._build_tree()

    def show_procurement_builder(self) -> None:
        self._activate_workflow_stage("deliverables", switch_workspace=False)
        dialog = ProcurementDialog(self.project, self)
        self._fit_dialog_to_available_screen(dialog, 1420, 860)
        dialog.exec()
        self._mark_dirty()
        self._refresh_all()
        self._refresh_workflow()
        self._build_tree()

    def save_project(self) -> bool:
        if self.current_file is None:
            return self.save_project_as()
        try:
            self.current_file.write_text(json.dumps(self.project.to_dict(touch_modified=True), ensure_ascii=False, indent=2), encoding="utf-8")
            self.dirty = False
            self._update_title()
            self.statusBar().showMessage(f"Kaydedildi: {self.current_file}", 4000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Kaydetme hatası", str(exc))
            return False

    def save_project_as(self) -> bool:
        default_name = f"{self.project.project_code}.ucd.json"
        path, _ = QFileDialog.getSaveFileName(self, "Projeyi Kaydet", str(Path.home() / default_name), "UCD Projesi (*.ucd.json)")
        if not path:
            return False
        if not path.lower().endswith(".ucd.json"):
            path += ".ucd.json"
        self.current_file = Path(path)
        return self.save_project()

    def _mark_dirty(self) -> None:
        self.dirty = True
        self._update_title()

    def _update_title(self) -> None:
        marker = " *" if self.dirty else ""
        self.setWindowTitle(f"DiTuS Kablo Analizör™ v{APP_VERSION} — {self.project.project_name}{marker}")

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        result = QMessageBox.question(
            self, "Kaydedilmemiş değişiklik", "Değişiklikler kaydedilsin mi?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if result == QMessageBox.Save:
            return self.save_project()
        return result == QMessageBox.Discard

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        event.accept() if self._confirm_discard() else event.ignore()

    def show_about(self) -> None:
        message = (
            f"DiTuS Kablo Analizör™ v{APP_VERSION}\nİşler karışmadan vaziyet alın\n\n"
            "Yeraltı OG/YG kablo sistemleri için güzergâh ve kablo-kanal geometrisi, IEC 60287 akım taşıma kapasitesi, "
            "IEEE 575 bonding/cross-bonding, 2D kararlı/geçici termal analiz, arıza/EPR ve SVL tasarım platformu.\n\n"
            "Üretim bonding: güzergâh çözünürlüklü explicit N-core/N-sheath/opsiyonel GCC ağı; primitive CIM/MNA ve "
            "Node-Voltage çapraz çözümü; simplified-Carson earth return.\n"
            "Termal/rating: IEC 60287 analitik zinciri, 2D nodal sonlu hacim, IEC 60853 iş akışlı transient/cyclic analiz, "
            "NORMAL/DESIGN/N-1 senaryoları ve yöntem-otoritesi/fail-closed kapsam kapıları.\n"
            "Kılıf kayıpları: longitudinal network I²R kaybı ile uygulanabilir koşullarda IEC λ₁″ tamamlayıcılığı; "
            "kapsam dışı koşullar sessizce tahmin edilmez.\n\n"
            "Bağımsız geliştirilmiştir; ticari yazılım üreticileriyle bağlantı veya onay ima edilmez.\n\n"
            "Designed by S. Esim — AI-assisted development"
        )
        QMessageBox.about(self, "DiTuS Kablo Analizör", message)

