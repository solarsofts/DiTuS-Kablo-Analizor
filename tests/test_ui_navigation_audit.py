from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ucd" / "ui" / "main_window.py"


def _source() -> str:
    return MAIN_WINDOW.read_text(encoding="utf-8")


def _method_block(source: str, name: str, next_name: str) -> str:
    start = source.index(f"    def {name}")
    end = source.index(f"    def {next_name}", start)
    return source[start:end]


def test_steady_thermal_stage_routes_to_inputs_before_results_exist() -> None:
    source = _source()
    block = _method_block(source, "_workspace_for_stage", "_apply_result_group_visibility")
    assert 'if key == "thermal_review"' in block
    assert "self.nodal_thermal_result is not None" in block
    assert "self.thermal_route_widget" in block
    assert "self.thermal_review_widget" in block


def test_thermal_result_screen_has_internal_scenario_and_region_selectors() -> None:
    source = _source()
    block = _method_block(source, "_build_thermal_review_widget", "_build_transient_widget")
    assert "self.thermal_scenario_selector = QComboBox()" in block
    assert "self.thermal_region_selector = QComboBox()" in block
    assert 'QPushButton("Termal Güzergâh Girdileri")' in block
    assert 'QPushButton("2D Sonuçlarını Aç")' in block
    assert "yalnız alt 2D Nodal Sonuçları" not in block


def test_result_table_selection_does_not_spawn_editor_windows() -> None:
    source = _source()
    nodal = _method_block(source, "_nodal_result_selection_changed", "_open_thermal_analysis_detail")
    transient = _method_block(source, "_transient_result_selection_changed", "_refresh_thermal_result_selectors")
    assert "_show_workspace_widget" not in nodal
    assert "_show_workspace_widget" not in transient


def test_calculation_commands_do_not_auto_open_results_dialog() -> None:
    source = _source()
    method_pairs = [
        ("run_transient_thermal_analysis", "_populate_transient_results"),
        ("run_thermal_route_analysis", "_populate_thermal_route_results"),
        ("run_nodal_thermal_analysis", "_populate_nodal_results"),
        ("run_thermal_preprocessor", "_populate_thermal_results"),
        ("run_bonding_solver", "_bonding_result_selection_changed"),
        ("run_fault_study", "_populate_fault_results"),
        ("run_svl_selection", "_populate_svl_results"),
        ("_auto_design_cross_bonding", "show_cable_library"),
    ]
    for name, next_name in method_pairs:
        block = _method_block(source, name, next_name)
        assert "_show_results_widget" not in block, name
        assert "show_results_dialog" not in block, name


def test_editors_have_explicit_result_buttons() -> None:
    source = _source()
    required_labels = (
        "IEC 60287 Sonuçlarını Aç",
        "Termal Sonuçları Aç",
        "2D Sonuçlarını Aç",
        "IEC 60853 Sonuçlarını Aç",
        "Bonding Sonuçlarını Aç",
        "Arıza / EPR Sonuçlarını Aç",
        "SVL Sonuçlarını Aç",
    )
    for label in required_labels:
        assert label in source


def test_project_tree_contains_no_dead_generic_phase_nodes() -> None:
    source = _source()
    block = _method_block(source, "_build_tree", "_set_property_rows")
    for dead_label in ('("Faz A", ("generic"', '("Faz B", ("generic"', '("Faz C", ("generic"', '("ECC", ("generic"'):
        assert dead_label not in block
    assert '"thermal_region"' in block
    assert '"thermal_template"' in block
    assert '"thermal_material"' in block
    assert '"svl_candidate"' in block


def test_actionable_tree_children_select_matching_editor_rows() -> None:
    source = _source()
    block = _method_block(source, "_tree_item_double_clicked", "_property_changed")
    assert "self.route_table.selectRow(index)" in block
    assert "self.thermal_region_table.selectRow(index)" in block
    assert "self.thermal_template_table.selectRow(index)" in block
    assert "self.thermal_material_table.selectRow(index)" in block
    assert "self.bonding_minor_table.selectRow(index)" in block
    assert "self.bonding_node_table.selectRow(index)" in block
    assert "self.bonding_linkbox_table.selectRow(index)" in block
    assert "self.fault_scenario_table.selectRow(index)" in block
    assert "self.svl_candidate_table.selectRow(index)" in block


def test_wizard_and_deliverable_dialogs_do_not_open_background_module_window() -> None:
    source = _source()
    wizard = _method_block(source, "run_project_wizard", "run_first_design_iteration")
    report = _method_block(source, "show_report_builder", "show_procurement_builder")
    procurement = _method_block(source, "show_procurement_builder", "save_project")
    assert '_activate_workflow_stage("system_load", switch_workspace=False)' in wizard
    assert '_activate_workflow_stage("deliverables", switch_workspace=False)' in report
    assert '_activate_workflow_stage("deliverables", switch_workspace=False)' in procurement


def test_large_modal_dialogs_are_fitted_to_available_screen() -> None:
    source = _source()
    thermal_detail = _method_block(source, "_open_thermal_analysis_detail", "_thermal_design_applied")
    catalog = _method_block(source, "show_catalog_comparison", "_on_project_cable_changed")
    report = _method_block(source, "show_report_builder", "show_procurement_builder")
    procurement = _method_block(source, "show_procurement_builder", "save_project")
    assert "_fit_dialog_to_available_screen(dialog, 1380, 860)" in thermal_detail
    assert "_fit_dialog_to_available_screen(dialog, 1320, 820)" in catalog
    assert "_fit_dialog_to_available_screen(dialog, 1280, 820)" in report
    assert "_fit_dialog_to_available_screen(dialog, 1420, 860)" in procurement


def test_thermal_review_uses_responsive_wrapped_layout() -> None:
    source = _source()
    block = _method_block(source, "_build_thermal_review_widget", "_build_transient_widget")
    assert "command_grid = QGridLayout()" in block
    assert "index // 3, index % 3" in block
    assert "selector_grid = QGridLayout()" in block
    assert "controls_grid = QGridLayout()" in block
    assert "self.thermal_view.setMinimumHeight(300)" in block
    assert "top = QHBoxLayout()" not in block


def test_dialog_fit_delegates_to_single_window_layout_authority() -> None:
    """Boyut kararı tek yerde olmalı: ucd.ui.window_layout.

    Daha önce her diyalog kurucusunda mutlak ``resize()`` vardı ve ana pencere
    ayrı bir yardımcıyla bazılarını sığdırıyordu.  İki otorite olduğu için iç
    içe açılan diyaloglar sığdırma yolundan hiç geçmiyordu.  Bu test, ana
    pencerenin artık kendi geometri matematiğini taşımadığını kilitler.
    """

    source = _source()
    block = _method_block(source, "_fit_dialog_to_available_screen", "_show_workspace_widget")
    assert "fit_window(dialog" in block
    assert "self._density_for(" in block
    # Geometri matematiği ana pencerede tekrar edilmemeli.
    assert "availableGeometry()" not in block
    assert "setGeometry(" not in block
    assert "from ucd.ui.window_layout import" in source


def test_no_ui_module_hardcodes_absolute_window_size() -> None:
    """Hiçbir diyalog kendi piksel boyutunu seçmemeli; yalnız yoğunluk bildirir."""

    import re
    from pathlib import Path

    ui_dir = Path(__file__).resolve().parents[1] / "src" / "ucd" / "ui"
    offenders: list[str] = []
    for path in sorted(ui_dir.glob("*.py")):
        if path.name == "window_layout.py":
            continue
        for match in re.finditer(r"\.resize\(\s*\d+\s*,\s*\d+\s*\)", path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {match.group(0)}")
    assert not offenders, "Mutlak pencere boyutu kalmış: " + "; ".join(offenders)


def test_first_design_precheck_status_is_synchronized_to_tree() -> None:
    source = _source()
    apply_block = _method_block(source, "_apply_selected_first_design_candidate", "_build_route_table_widget")
    iteration_block = _method_block(source, "run_first_design_iteration", "new_project")
    runtime_block = _method_block(source, "_workflow_runtime_context", "_refresh_workflow")
    assert 'last_iteration_status = "READY"' in apply_block
    assert 'last_iteration_status = "RUNNING"' in iteration_block
    assert 'last_iteration_status = "BLOCKED"' in iteration_block
    assert 'last_iteration_status = "CONDITIONAL_READY"' in iteration_block
    assert "apply_candidate_to_project" not in iteration_block
    assert "run_bonding_solver" not in iteration_block
    assert "run_transient_thermal_analysis" not in iteration_block
    assert "run_fault_study" not in iteration_block
    assert "run_svl_selection" not in iteration_block
    assert "self._build_tree()" in iteration_block
    assert 'precheck_runtime = None if precheck_status in {"", "NOT_RUN"}' in runtime_block


def test_physical_parameter_shadow_tool_is_reachable_from_cable_menu() -> None:
    source = _source()
    assert '"Kablo Fiziksel Parametreleri…", self, triggered=self.show_physical_parameters' in source
    assert "def show_physical_parameters" in source
    assert "CablePhysicalParametersDialog" in source


def test_iteration_and_deliverables_do_not_reopen_first_design_workspace() -> None:
    source = _source()
    activate = _method_block(source, "_activate_workflow_stage", "_workspace_for_stage")
    workspace = _method_block(source, "_workspace_for_stage", "_apply_result_group_visibility")
    assert 'elif stage.stage_id == "iteration":' in activate
    assert 'elif stage.stage_id == "deliverables":' in activate
    assert activate.count("self.show_results_dialog(self.summary_table)") >= 2
    assert activate.count("self.module_dialog.hide()") >= 2
    assert '"deliverables": self.first_design_widget' not in workspace


def test_installation_screen_uses_kablo_kanal_name_and_turkish_type_caption() -> None:
    main_source = _source()
    installation = (ROOT / "src/ucd/ui/installation_designer_dialog.py").read_text(encoding="utf-8")
    route_dialog = (ROOT / "src/ucd/ui/route_section_dialog.py").read_text(encoding="utf-8")
    assert '"Kablo-Kanal Düzeni…"' in main_source
    assert "from ucd import __version__" in installation
    assert 'f"DiTuS — Kablo-Kanal Düzeni v{__version__}"' in installation
    assert '"doğrudan gömülü"' in installation
    assert 'font-style:italic' in installation
    assert '"doğrudan gömülü"' in route_dialog
    assert 'font-style:italic' in route_dialog


def test_bonding_link_boxes_show_local_and_cumulative_cross_mapping() -> None:
    graphics = (ROOT / "src/ucd/ui/graphics_views.py").read_text(encoding="utf-8")
    assert 'yerel L→R' in graphics
    assert 'Başlangıca göre:' in graphics
    assert 'Major başlangıcına göre kümülatif yol' in graphics
    assert 'mapping.get(phase' in graphics
    assert '-L→' in graphics and '-R' in graphics
