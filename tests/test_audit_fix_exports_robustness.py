from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from ucd.calculations import production_electrothermal as production
from ucd.calculations.export_safety import (
    SpreadsheetSafeDictWriter,
    spreadsheet_safe_row,
    spreadsheet_safe_text,
)
from ucd.calculations.operating_scenarios import resolve_operating_scenarios
from ucd.calculations.procurement import (
    VIEW_RFQ,
    build_procurement_package,
    write_procurement_package,
)
from ucd.models.project import (
    ExternalHeatSourceData,
    ProcurementQuantityOverride,
    ProjectData,
    ThermalMaterialRegionData,
)

ROOT = Path(__file__).resolve().parents[1]
INJECTED_PROJECT_NAME = '=HYPERLINK("rfq-dosyasi","Tikla")'


def _synthetic_project() -> ProjectData:
    raw = json.loads((ROOT / "examples" / "synthetic_20km_applied.ucd.json").read_text(encoding="utf-8"))
    return ProjectData.from_dict(raw)


def _read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle, delimiter=";"))


# ---------------------------------------------------------------------------
# D-3: spreadsheet formula injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["=1+2", "+cmd", "-cmd|' /C calc'!A0", "@SUM(A1:A3)", "\t=1", "\r=1", "-", "+", "=", "-5 m"],
)
def test_spreadsheet_safe_text_prefixes_formula_triggers(value: str) -> None:
    assert spreadsheet_safe_text(value) == "'" + value


@pytest.mark.parametrize(
    "value",
    ["-5", "+1.25", "-0.012000", "-1e-3", "TR-01", "kablo = 3 faz", "", " =1", "'=1"],
)
def test_spreadsheet_safe_text_keeps_numeric_and_plain_text(value: str) -> None:
    assert spreadsheet_safe_text(value) == value


@pytest.mark.parametrize("value", [-5, -0.25, 0, 3.5, None, True, ("=1",)])
def test_spreadsheet_safe_text_ignores_non_string_values(value: object) -> None:
    assert spreadsheet_safe_text(value) is value


def test_spreadsheet_safe_row_and_dict_writer(tmp_path: Path) -> None:
    assert spreadsheet_safe_row(["=A1", -2.5, "-2.5", "ok"]) == ["'=A1", -2.5, "-2.5", "ok"]
    path = tmp_path / "rows.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = SpreadsheetSafeDictWriter(handle, fieldnames=["a", "b"])
        writer.writeheader()
        writer.writerow({"a": "@cmd", "b": "-7"})
        writer.writerows([{"a": -1.5, "b": "+x"}])
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [["a", "b"], ["'@cmd", "-7"], ["-1.5", "'+x"]]


def test_procurement_xlsx_keeps_project_text_literal_and_rfq_total_formula(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    project = _synthetic_project()
    project.project_name = INJECTED_PROJECT_NAME
    project.procurement.quantity_overrides = [
        ProcurementQuantityOverride("BND-LB-CROSS-001", 30.0, "=1+2 yedek"),
    ]
    package = build_procurement_package(project)
    paths = write_procurement_package(package, tmp_path, "d3_xlsx", ("xlsx",))
    workbook = openpyxl.load_workbook(paths["xlsx"])
    try:
        summary = workbook["Özet"]
        assert summary["B4"].data_type == "s"
        assert summary["B4"].value == INJECTED_PROJECT_NAME

        boq = workbook["BOQ"]
        headers = [cell.value for cell in boq[1]]
        rationale_col = headers.index("Override Gerekçesi") + 1
        rationale_cells = [
            boq.cell(row=row, column=rationale_col)
            for row in range(2, boq.max_row + 1)
            if boq.cell(row=row, column=1).value == "BND-LB-CROSS-001"
        ]
        assert rationale_cells
        assert all(cell.data_type == "s" and cell.value == "=1+2 yedek" for cell in rationale_cells)

        rfq = workbook["RFQ"]
        rfq_rows = len(package.lines_for_view(VIEW_RFQ))
        assert rfq_rows > 0
        assert rfq.cell(row=1, column=18).value == "Toplam Fiyat"
        for row in range(2, rfq_rows + 2):
            cell = rfq.cell(row=row, column=18)
            assert cell.data_type == "f"
            assert cell.value == f"=F{row}*Q{row}"
    finally:
        workbook.close()


def test_procurement_csv_bundle_neutralises_formula_like_user_text(tmp_path: Path) -> None:
    project = _synthetic_project()
    project.procurement.quantity_overrides = [
        ProcurementQuantityOverride("BND-LB-CROSS-001", 30.0, "=HYPERLINK(\"x\",\"y\")"),
        ProcurementQuantityOverride("BND-LB-GROUND-001", 12.0, "-5"),
    ]
    package = build_procurement_package(project)
    by_id = {item.item_id: item for item in package.lines}
    assert by_id["BND-LB-CROSS-001"].override_rationale.startswith("=")
    assert by_id["BND-LB-GROUND-001"].override_rationale == "-5"
    paths = write_procurement_package(package, tmp_path, "d3_csv", ("csv",))
    rows = _read_csv(paths["csv_boq"])
    header = rows[0]
    rationale_col = header.index("Override Gerekçesi")
    by_row_id = {row[0]: row for row in rows[1:]}
    assert by_row_id["BND-LB-CROSS-001"][rationale_col] == "'=HYPERLINK(\"x\",\"y\")"
    assert by_row_id["BND-LB-GROUND-001"][rationale_col] == "-5"
    for name in ("csv_boq", "csv_bom", "csv_rfq", "csv_drum_plan"):
        for row in _read_csv(paths[name])[1:]:
            for cell in row:
                if cell[:1] in {"=", "+", "-", "@", "\t", "\r"}:
                    float(cell)  # only plain numbers may keep a trigger prefix


@pytest.fixture()
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    yield app
    app.processEvents()


def test_installation_section_csv_export_neutralises_formula_like_names(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import installation_designer_dialog as module

    project = ProjectData()
    section = project.installation_design.cross_sections[0]
    section.material_regions.append(ThermalMaterialRegionData(
        "MR-D3", "=1+2", "MAT-NATIVE-01",
        [[-0.5, 0.7], [0.5, 0.7], [0.5, 1.3], [-0.5, 1.3]],
    ))
    # Inactive: the canvas fit path does not support active heat sources (separate issue).
    section.external_heat_sources.append(ExternalHeatSourceData("HS-D3", "@SUM(A1)", 1.5, 1.0, 5.0, active=False))
    image_path = tmp_path / "kesit.png"
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(image_path), ""))
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args: messages.append(("info", args[2])))
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: messages.append(("warning", args[2])))
    monkeypatch.setattr(module.QMessageBox, "critical", lambda *args: messages.append(("critical", args[2])))

    dialog = module.InstallationDesignerDialog(project)
    try:
        dialog._export_engineering_section()
    finally:
        dialog.close()
    assert [kind for kind, _text in messages] == ["info"], messages

    objects_path = tmp_path / "kesit_objects.csv"
    with objects_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    region_names = {row["name"] for row in rows if row["object_id"] == "MR-D3"}
    heat_names = {row["name"] for row in rows if row["object_id"] == "HS-D3"}
    assert region_names == {"'=1+2"}
    assert heat_names == {"'@SUM(A1)"}
    for path in (objects_path, tmp_path / "kesit_validation.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                for cell in row:
                    if cell[:1] in {"=", "+", "-", "@", "\t", "\r"}:
                        float(cell)


# ---------------------------------------------------------------------------
# D-6a: production dryout gate must not skip unresolved regions silently
# ---------------------------------------------------------------------------


def test_dryout_gate_baseline_project_does_not_require_nodal() -> None:
    assert production._project_requires_nodal_dryout(ProjectData()) == (False, (), ())


def test_dryout_gate_forces_nodal_when_region_template_cannot_be_resolved() -> None:
    project = ProjectData()
    project.thermal_design.regions[0].template_id = "TPL-YOK"
    requires, material_ids, reasons = production._project_requires_nodal_dryout(project)
    region_id = project.thermal_design.regions[0].region_id
    assert requires is True
    assert material_ids == ()
    assert len(reasons) == 1
    assert reasons[0].startswith(f"DRYOUT_GATE_REGION_UNRESOLVED[{region_id}]")
    assert "TPL-YOK" in reasons[0]


def test_dryout_gate_forces_nodal_when_region_override_is_not_numeric() -> None:
    project = ProjectData()
    region = project.thermal_design.regions[1]
    region.overrides = dict(region.overrides or {}, burial_depth_m="derin")
    requires, _material_ids, reasons = production._project_requires_nodal_dryout(project)
    assert requires is True
    assert any(item.startswith(f"DRYOUT_GATE_REGION_UNRESOLVED[{region.region_id}]") for item in reasons)


def test_dryout_gate_does_not_hide_programming_errors(monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise AttributeError("beklenmeyen hata")

    monkeypatch.setattr(production, "resolve_thermal_region", broken)
    with pytest.raises(AttributeError):
        production._project_requires_nodal_dryout(ProjectData())


def test_analytic_production_run_reports_unresolved_dryout_gate_explicitly() -> None:
    project = ProjectData()
    project.thermal_design.regions[0].template_id = "TPL-YOK"
    scenario = resolve_operating_scenarios(project)[0]
    result = production.solve_production_operating_scenario(project, scenario, thermal_method="ANALYTIC")
    assert result.completion_status == "FAILED"
    assert result.error_code == "ANALYTIC_DRYOUT_REQUIRES_NODAL"
    assert "Termal bölge çözülemediği" in result.error_message
    assert "TPL-YOK" in result.error_message
    assert any(item.startswith("DRYOUT_GATE_REGION_UNRESOLVED[") for item in result.trace)


# ---------------------------------------------------------------------------
# D-6b: unreadable DXF entities are counted and reported
# ---------------------------------------------------------------------------


def _write_dxf(path: Path) -> Path:
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (100.0, 0.0), dxfattribs={"layer": "ROUTE"})
    msp.add_lwpolyline([(100.0, 0.0), (100.0, 50.0), (150.0, 50.0)], dxfattribs={"layer": "ROUTE"})
    msp.add_text("TR-01", dxfattribs={"layer": "NOTE", "insert": (10.0, 10.0)})
    msp.add_mtext("Bozuk not", dxfattribs={"layer": "NOTE", "insert": (20.0, 20.0)})
    msp.add_polyline2d([(0.0, 0.0), (0.0, 10.0), (5.0, 10.0)], dxfattribs={"layer": "ROUTE"})
    doc.saveas(str(path))
    return path


def _break_mtext_and_polyline(monkeypatch) -> None:
    import ezdxf
    from ezdxf.entities import MText, Polyline

    def broken_plain_text(self, *args, **kwargs):
        raise ValueError("MTEXT ayrıştırılamadı")

    def broken_vertices(self):
        raise ValueError("POLYLINE köşeleri okunamadı")

    monkeypatch.setattr(MText, "plain_text", broken_plain_text)
    real_readfile = ezdxf.readfile

    def readfile_with_broken_polyline(filename, *args, **kwargs):
        doc = real_readfile(filename, *args, **kwargs)
        # Break vertex access only after the document has been loaded.
        monkeypatch.setattr(Polyline, "vertices", property(broken_vertices))
        return doc

    monkeypatch.setattr(ezdxf, "readfile", readfile_with_broken_polyline)


def test_dxf_reader_without_failures_reports_no_skipped_entities(tmp_path: Path) -> None:
    from ucd.cad import read_dxf_geometry

    geometry = read_dxf_geometry(_write_dxf(tmp_path / "route.dxf"))
    assert len(geometry.lines) == 1
    assert len(geometry.polylines) == 2
    assert sorted(text for _pos, text, _layer in geometry.texts) == ["Bozuk not", "TR-01"]
    assert geometry.skipped_entities == {}
    assert geometry.skipped_entity_count == 0
    assert geometry.skipped_entities_warning == ""


def test_dxf_reader_counts_unreadable_entities_and_keeps_good_ones(tmp_path: Path, monkeypatch) -> None:
    from ucd.cad import read_dxf_geometry

    path = _write_dxf(tmp_path / "route.dxf")
    _break_mtext_and_polyline(monkeypatch)
    geometry = read_dxf_geometry(path)
    assert geometry.lines == [((0.0, 0.0), (100.0, 0.0), "ROUTE")]
    assert geometry.polylines == [([(100.0, 0.0), (100.0, 50.0), (150.0, 50.0)], False, "ROUTE")]
    assert [text for _pos, text, _layer in geometry.texts] == ["TR-01"]
    assert geometry.skipped_entities == {"MTEXT": 1, "POLYLINE": 1}
    assert geometry.skipped_entity_count == 2
    warning = geometry.skipped_entities_warning
    assert "2 varlık atlandı" in warning
    assert "MTEXT: 1" in warning and "POLYLINE: 1" in warning


def test_main_window_dxf_import_reports_skipped_entities_without_blocking(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import main_window as module

    path = _write_dxf(tmp_path / "route.dxf")
    _break_mtext_and_polyline(monkeypatch)
    blocking: list[str] = []
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(path), "DXF (*.dxf)"))
    monkeypatch.setattr(module.QMessageBox, "critical", lambda *args, **kwargs: blocking.append("critical"))
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args, **kwargs: blocking.append("warning"))

    window = module.MainWindow(tmp_path)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    try:
        window.import_dxf()
        assert blocking == []
        assert window.project.cad_source == str(path.resolve())
        log_text = window.log_view.toPlainText()
        assert "DXF uyarısı: okunamayan 2 varlık atlandı" in log_text
        assert "DXF uyarısı" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_main_window_wizard_dxf_reload_reports_skipped_entities(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import main_window as module

    path = _write_dxf(tmp_path / "route.dxf")
    _break_mtext_and_polyline(monkeypatch)
    wizard_project = ProjectData()
    wizard_project.cad_source = str(path)

    class _AcceptedWizard:
        def __init__(self, _parent=None) -> None:
            self.result_project = wizard_project
            self.run_first_iteration = False

        def exec(self) -> int:
            return module.QDialog.Accepted

    monkeypatch.setattr(module, "NewDesignWizard", _AcceptedWizard)
    window = module.MainWindow(tmp_path)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    try:
        window.run_project_wizard()
        assert window.project is wizard_project
        assert "DXF uyarısı: okunamayan 2 varlık atlandı" in window.log_view.toPlainText()
        assert "DXF uyarısı" in window.statusBar().currentMessage()
        assert not any("DXF yeniden açılamadı" in item for item in wizard_project.design_progress.missing_data)
    finally:
        window.close()


def test_project_wizard_dxf_selection_reports_skipped_entities(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import project_wizard as module

    path = _write_dxf(tmp_path / "route.dxf")
    _break_mtext_and_polyline(monkeypatch)
    blocking: list[str] = []
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(path), "DXF (*.dxf)"))
    monkeypatch.setattr(module.QMessageBox, "critical", lambda *args, **kwargs: blocking.append("critical"))

    wizard = module.NewDesignWizard()
    try:
        wizard._select_dxf()
        assert blocking == []
        assert wizard.route_dxf_radio.isChecked()
        assert abs(wizard.route_length_spin.value() - 200.0) < 1e-6
        summary = wizard.route_summary.text()
        assert "DXF toplam çizgi/polyline uzunluğu" in summary
        assert "DXF uyarısı: okunamayan 2 varlık atlandı" in summary
    finally:
        wizard.close()
