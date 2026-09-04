from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_route_tree_opens_object_editor_without_workflow_wizard_chrome():
    source = _source("src/ucd/ui/main_window.py")
    assert 'self._show_workspace_widget(\n                self.route_table_widget, "Güzergâh Bölümleri", workflow_frame=False' in source
    assert 'self._activate_workflow_stage("route")\n            self.route_table.selectRow(index)' not in source
    stage = _source("src/ucd/ui/stage_host.py")
    assert "def set_standalone_mode" in stage
    assert "self.header.setVisible(not self._standalone_mode)" in stage
    assert "self.footer.setVisible(not self._standalone_mode)" in stage


def test_route_commands_are_above_expandable_route_table():
    source = _source("src/ucd/ui/main_window.py")
    command_pos = source.index('add_btn = QPushButton("Bölüm Ekle")')
    add_layout_pos = source.index("layout.addLayout(buttons)", command_pos)
    add_table_pos = source.index("layout.addWidget(self.route_table, 1)", command_pos)
    assert add_layout_pos < add_table_pos
    assert 'accept_btn = QPushButton("Mevcut Güzergâhı Kabul Et")' in source


def test_automatic_geometry_regeneration_never_roundtrips_current_through_ui_text():
    source = _source("src/ucd/ui/installation_designer_dialog.py")
    assert 'format(float(item.load_current_a), ".17g")' in source
    assert "if automatic:" in source
    assert "loads = [float(item.load_current_a) for item in active_circuits]" in source
    assert "circuit.load_current_a = prior.load_current_a" in source
    assert "self._installation_type_regeneration_pending = True" in source


def test_installation_specific_visual_integrity_checks_are_explicit():
    source = _source("src/ucd/ui/installation_designer_dialog.py")
    for code in (
        "DUCT_LAYOUT_MISSING",
        "DUCT_ASSIGNMENT_MISSING",
        "DUCT_CABLE_COORDINATE_MISMATCH",
        "CABLE_TOO_LARGE_FOR_DUCT",
        "CABLE_INTERSECTS_TROUGH_WALL",
        "CABLE_OUTSIDE_HDD_BORE",
    ):
        assert code in source
    assert "visual_findings = self._installation_visual_integrity_findings(section)" in source


def test_duct_trough_hdd_preview_uses_cable_cluster_not_trench_bottom_as_anchor():
    source = _source("src/ucd/ui/installation_designer_dialog.py")
    assert "mean_depth = (" in source
    assert "trough_bottom = min(" in source
    assert "sum(float(item.depth_m) for item in active_cables) / len(active_cables)" in source


def test_bonding_operating_scenario_error_is_actionable_not_unexpected():
    source = _source("src/ucd/ui/main_window.py")
    assert "OperatingScenarioInputError" in source
    assert 'except (BondingInputError, ThermalRouteInputError, OperatingScenarioInputError) as exc:' in source
    assert "def _bonding_input_error_message" in source
    assert "Devre akımı tutarsızlığı" in source
    assert "Maksimum fark:" in source
    assert ":.12f" in source
