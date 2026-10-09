from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ucd" / "ui" / "main_window.py"


def _source() -> str:
    return MAIN_WINDOW.read_text(encoding="utf-8")


def _method_block(source: str, name: str, next_name: str) -> str:
    start = source.index(f"    def {name}")
    end = source.index(f"    def {next_name}", start)
    return source[start:end]


def test_application_version_is_guided_first_design_release() -> None:
    source = _source()
    assert "from ucd import __version__" in source
    assert "APP_VERSION = __version__" in source
    assert (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() == "0.16.9.4.39"


def test_application_signature_is_visible_in_status_and_about() -> None:
    source = _source()
    assert 'QLabel("Designed by S. Esim — AI-assisted development")' in source
    assert source.count('"Designed by S. Esim — AI-assisted development"') >= 2
    assert '"designed by S.Esim & gpt"' not in source
    assert '"v0.9: grafik ve hesap' not in source


def test_main_window_is_route_canvas_without_fixed_workflow_or_result_blocks() -> None:
    source = _source()
    block = _method_block(source, "_build_ui(self) -> None:", "_workflow_runtime_context")
    assert "self.upper_splitter.addWidget(self.project_tree)" in block
    assert "self.upper_splitter.addWidget(self.plan_view)" in block
    assert "central_layout.addWidget(self.workflow_stage_bar)" not in block
    assert "central_layout.addWidget(self.progress_label)" not in block
    assert "central_layout.addWidget(self.bottom_tabs" not in block
    assert "upper_splitter.addWidget(prop_container)" not in block


def test_editors_context_and_results_are_separate_modeless_windows() -> None:
    source = _source()
    block = _method_block(source, "_build_ui(self) -> None:", "_workflow_runtime_context")
    for dialog_name in ("module_dialog", "context_dialog", "results_dialog"):
        assert f"self.{dialog_name} = QDialog(self)" in block
        assert f"self.{dialog_name}.setModal(False)" in block
    assert "results_layout.addWidget(self.bottom_tabs)" in block
    # Çalışma alanı artık doğrudan pencereye değil, aşama konağının gövdesine
    # yerleşir.  Konak akış çerçevesini (neredeyim / ne eksik / sıradaki adım)
    # sabit tutar; aşamalar ard arda bağımsız pencere açmaz.
    assert "self.stage_host = StageHostFrame()" in block
    assert "self.stage_host.set_body(self.workspace_tabs)" in block
    assert "module_layout.addWidget(self.stage_host, 1)" in block


def test_workflow_status_is_shown_in_project_tree_with_guidance_tooltip() -> None:
    source = _source()
    block = _method_block(source, "_build_tree(self) -> None:", "_set_property_rows")
    assert '"Proje Tasarım Akışı"' in block
    assert "display = user_stage_state(stage)" in block
    assert "STATUS_COLORS.get(display.color_status" in block
    assert '"Tamamlanması gerekenler:"' in block
    assert '"Bloke nedenleri:"' in block
    assert "display.action" in block
    assert "evaluation.recommended_stage_id" in block


def test_main_canvas_closes_auxiliary_windows() -> None:
    source = _source()
    block = _method_block(source, "_show_main_canvas(self) -> None:", "_build_thermal_review_widget")
    assert "self.module_dialog.hide()" in block
    assert "self.results_dialog.hide()" in block
    assert "self.context_dialog.hide()" in block
    assert "self.plan_view.setFocus()" in block


def test_project_tree_opens_only_on_double_click() -> None:
    source = _source()
    build = _method_block(source, "_build_ui(self) -> None:", "_workflow_runtime_context")
    assert "self.project_tree.itemDoubleClicked.connect(self._tree_item_double_clicked)" in build
    assert "self.project_tree.setExpandsOnDoubleClick(False)" in build

    selection = _method_block(source, "_tree_selection_changed(self) -> None:", "_tree_item_double_clicked")
    for forbidden in (
        "_activate_workflow_stage(",
        "_show_workspace_widget(",
        "show_results_dialog(",
        "show_report_builder(",
        "show_procurement_builder(",
        "_show_main_canvas(",
    ):
        assert forbidden not in selection
    assert "açmak için çift tıklayın" in selection

    double_click = _method_block(source, "_tree_item_double_clicked", "_property_changed")
    assert "_activate_workflow_stage" in double_click
    assert "show_results_dialog" in double_click
    assert "item.setExpanded(not item.isExpanded())" in double_click


def test_modeless_windows_are_fitted_to_available_screen() -> None:
    source = _source()
    helper = _method_block(source, "_fit_dialog_to_available_screen", "_show_workspace_widget")
    # Sığdırma matematiği ucd.ui.window_layout'a taşındı; ana pencere yalnız delege eder.
    assert "fit_window(dialog" in helper
    assert "self._density_for(" in helper
    # Geometri matematiği ana pencerede tekrarlanmamalı.
    assert "setGeometry(" not in helper

    workspace = _method_block(source, "_show_workspace_widget", "show_results_dialog")
    assert "self.module_dialog.showNormal()" in workspace
    assert "_fit_dialog_to_available_screen(self.module_dialog" in workspace
    assert "widget is not self.bonding_table_widget" in workspace

    results = _method_block(source, "show_results_dialog", "_show_results_widget")
    assert "self.results_dialog.showNormal()" in results
    assert "_fit_dialog_to_available_screen(self.results_dialog" in results


def test_bonding_wheel_zoom_stops_at_fit_to_content() -> None:
    source = (ROOT / "src/ucd/ui/graphics_views.py").read_text(encoding="utf-8")
    # v0.16.9.4.38: bonding no longer carries a one-off wheel override.
    # The shared engineering-canvas contract clamps every managed view at its
    # current fit scale and preserves manual zoom across viewport resize.
    assert 'if delta < 0 and target <= fit_scale * 1.001:' in source
    assert 'self._zoom_view_mode = "MANUAL"' in source
    assert 'self._last_fit_bounds = bounds' in source
    simple = source.split("class SimpleDiagramView", 1)[1].split("class TransientThermalView", 1)[0]
    assert 'def wheelEvent' not in simple
