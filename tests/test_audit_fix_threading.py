"""Denetim bulgusu O-5 — ağır hesaplar GUI iş parçacığında çalışmamalı.

``ucd.ui.background_task.run_blocking_task`` motoru bir QThread üzerinde
çalıştırır, bu sırada GUI olay döngüsünü döndürür ve çağıran için eşzamanlı
kalır (sonuç döner, istisna özgün tipiyle GUI iş parçacığında yeniden fırlar).
Ana pencere ve ağır diyaloglardaki motor çağrıları bu yardımcıdan geçer.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QPushButton  # noqa: E402

from ucd.ui import background_task  # noqa: E402
from ucd.ui.background_task import (  # noqa: E402
    MINIMUM_DURATION_MS,
    SYNC_TASKS_ENV,
    run_blocking_task,
    task_running,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = ROOT / "examples" / "synthetic_20km_line.ucd.json"
ERROR_TEXT = "O-5 test girdisi eksik"


@pytest.fixture
def qapp(monkeypatch):
    monkeypatch.delenv(SYNC_TASKS_ENV, raising=False)
    app = QApplication.instance() or QApplication([])

    def _unexpected_modal(dialog, *_args, **_kwargs):
        raise AssertionError(f"Beklenmeyen modal diyalog: {dialog.windowTitle()}")

    # Offscreen testte beklenmeyen bir modal exec() testi kilitlemesin, düşürsün.
    monkeypatch.setattr(QtWidgets.QDialog, "exec", _unexpected_modal)
    yield app
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


@pytest.fixture
def messages(qapp, monkeypatch):
    """QMessageBox çağrılarını kaydet; offscreen testte modal kutu açılmasın."""

    records: list[tuple[str, str, str, int]] = []

    def _recorder(kind):
        def _box(_parent, title, text, *args, **kwargs):
            records.append((kind, str(title), str(text), threading.get_ident()))
            return QMessageBox.Ok
        return staticmethod(_box)

    for kind in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, kind, _recorder(kind))
    return records


def _project():
    from ucd.models.project import ProjectData

    return ProjectData.from_dict(json.loads(SYNTHETIC_CASE.read_text(encoding="utf-8")))


def _failing_engine(error_type, calls):
    def _engine(*_args, **_kwargs):
        calls.append(threading.get_ident())
        raise error_type(ERROR_TEXT)
    return _engine


def _recording_engine(real, calls):
    def _engine(*args, **kwargs):
        calls.append(threading.get_ident())
        return real(*args, **kwargs)
    return _engine


def _assert_handled_on_gui_thread(calls, records, kind, title):
    gui = threading.get_ident()
    assert calls, "motor çağrılmadı"
    assert calls[0] != gui, "motor GUI iş parçacığında çalıştı"
    assert (kind, title, ERROR_TEXT, gui) in records, records


# ---------------------------------------------------------------------------
# Yardımcı sözleşmesi
# ---------------------------------------------------------------------------

def test_result_is_returned_and_engine_runs_off_gui_thread(qapp) -> None:
    def engine(a, b=0):
        return a + b, threading.get_ident()

    value, ident = run_blocking_task(None, "Test", "Hesap çalışıyor…", engine, 2, b=3)

    assert value == 5
    assert ident != threading.get_ident()
    assert not task_running()
    assert QApplication.overrideCursor() is None


class _EngineInputError(ValueError):
    pass


def _raising_engine(record):
    record.append(threading.get_ident())
    raise _EngineInputError("T4 girdisi eksik")


def test_engine_exception_is_reraised_with_type_message_and_traceback(qapp) -> None:
    record: list[int] = []
    with pytest.raises(_EngineInputError, match="T4 girdisi eksik") as info:
        run_blocking_task(None, "Test", "Hesap çalışıyor…", _raising_engine, record)

    assert type(info.value) is _EngineInputError
    assert record and record[0] != threading.get_ident()
    frames = [frame.name for frame in traceback.extract_tb(info.value.__traceback__)]
    assert "_raising_engine" in frames
    assert QApplication.overrideCursor() is None
    assert not task_running()


def test_gui_event_loop_keeps_running_while_engine_works(qapp) -> None:
    # Eşzamanlı (eski) yolda zamanlayıcı motor bitene kadar tetiklenemez ve
    # bekleme 5 s sonunda False döner.
    released = threading.Event()
    QTimer.singleShot(20, released.set)
    started = time.monotonic()

    assert run_blocking_task(None, "Test", "Hesap çalışıyor…", released.wait, 5.0) is True
    assert time.monotonic() - started < 4.0


def test_wait_cursor_is_shown_and_restored_after_engine_error(qapp) -> None:
    QApplication.setOverrideCursor(QCursor(Qt.ArrowCursor))
    try:
        seen: dict[str, object] = {}
        probed = threading.Event()

        def probe() -> None:
            cursor = QApplication.overrideCursor()
            seen["shape"] = cursor.shape() if cursor is not None else None
            probed.set()

        def engine() -> None:
            probed.wait(5.0)
            raise RuntimeError("motor çöktü")

        QTimer.singleShot(0, probe)
        with pytest.raises(RuntimeError, match="motor çöktü"):
            run_blocking_task(None, "Test", "Hesap çalışıyor…", engine)

        assert seen["shape"] == Qt.WaitCursor
        assert QApplication.overrideCursor().shape() == Qt.ArrowCursor
    finally:
        QApplication.restoreOverrideCursor()
    assert QApplication.overrideCursor() is None


def test_busy_dialog_is_modal_cannot_be_dismissed_and_blocks_input(qapp) -> None:
    parent = QtWidgets.QWidget()
    button = QPushButton("Hesapla", parent)
    clicks: list[int] = []
    button.clicked.connect(lambda: clicks.append(1))
    parent.show()

    def click_button() -> None:
        # Pencere sistemi yolu: gerçek tıklama gibi modal kilitten geçer.
        QTest.mouseClick(parent.windowHandle(), Qt.LeftButton, Qt.NoModifier, button.geometry().center())

    click_button()
    assert clicks == [1], "kontrol tıklaması düğmeye ulaşmadı"
    seen: dict[str, object] = {}
    released = threading.Event()

    def probe() -> None:
        dialog = QApplication.activeModalWidget()
        seen["is_progress"] = isinstance(dialog, QProgressDialog)
        if isinstance(dialog, QProgressDialog):
            seen["title"] = dialog.windowTitle()
            seen["label"] = dialog.labelText()
            seen["range"] = (dialog.minimum(), dialog.maximum())
            seen["modality"] = dialog.windowModality()
            seen["buttons"] = dialog.findChildren(QPushButton)
            seen["parent"] = dialog.parentWidget() is parent
            dialog.reject()
            dialog.close()
            seen["still_visible"] = dialog.isVisible()
            click_button()
        released.set()

    QTimer.singleShot(MINIMUM_DURATION_MS + 300, probe)
    try:
        assert run_blocking_task(parent, "Bonding", "Bonding çözülüyor…", released.wait, 10.0) is True
    finally:
        parent.close()

    assert seen["is_progress"] is True
    assert seen["title"] == "Bonding"
    assert seen["label"] == "Bonding çözülüyor…"
    assert seen["range"] == (0, 0)
    assert seen["modality"] == Qt.ApplicationModal
    assert seen["buttons"] == []
    assert seen["parent"] is True
    assert seen["still_visible"] is True
    assert clicks == [1]
    assert QApplication.activeModalWidget() is None


def test_env_var_forces_synchronous_call(qapp, monkeypatch) -> None:
    monkeypatch.setenv(SYNC_TASKS_ENV, "1")
    assert run_blocking_task(None, "Test", "…", threading.get_ident) == threading.get_ident()


def test_missing_qapplication_falls_back_to_synchronous_call(monkeypatch) -> None:
    class _NoApplication:
        @staticmethod
        def instance():
            return None

    monkeypatch.delenv(SYNC_TASKS_ENV, raising=False)
    monkeypatch.setattr(background_task, "QApplication", _NoApplication)
    assert run_blocking_task(None, "Test", "…", threading.get_ident) == threading.get_ident()


def test_call_from_worker_thread_runs_synchronously(qapp) -> None:
    def outer():
        inner = run_blocking_task(None, "İç", "…", threading.get_ident)
        return threading.get_ident(), inner, task_running()

    outer_ident, inner_ident, running = run_blocking_task(None, "Dış", "…", outer)

    assert outer_ident != threading.get_ident()
    assert inner_ident == outer_ident
    assert running is True


def test_reentrant_call_on_gui_thread_runs_synchronously(qapp) -> None:
    gui = threading.get_ident()
    nested: list[tuple[bool, int]] = []
    done = threading.Event()

    def reenter() -> None:
        nested.append((task_running(), run_blocking_task(None, "İç", "…", threading.get_ident)))
        done.set()

    QTimer.singleShot(10, reenter)
    assert run_blocking_task(None, "Dış", "…", done.wait, 5.0) is True
    assert nested == [(True, gui)]
    assert not task_running()


# ---------------------------------------------------------------------------
# Ana pencere giriş noktaları
# ---------------------------------------------------------------------------

@pytest.fixture
def main_window(qapp, messages, tmp_path, monkeypatch):
    from ucd.ui import main_window as mw

    window = mw.MainWindow(tmp_path)
    monkeypatch.setattr(window, "_confirm_engine_precheck", lambda _engine_id: True)
    # Aşama etkinleştirme bazı aşamalarda modal düzenleyici açar; testte gerekmez.
    monkeypatch.setattr(window, "show_installation_designer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "show_project_cable_selection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_show_iteration_easter_egg", lambda *_args, **_kwargs: None)
    yield mw, window
    window.dirty = False
    window.close()
    qapp.processEvents()


_MAIN_WINDOW_ENGINE_CASES = [
    ("run_nodal_thermal_analysis", "solve_nodal_route", "NodalThermalInputError",
     "critical", "2D nodal termal girdi hatası"),
    ("run_transient_thermal_analysis", "solve_nodal_route", "NodalThermalInputError",
     "critical", "IEC 60853 girdi hatası"),
    ("run_thermal_route_analysis", "solve_thermal_route", "ThermalRouteInputError",
     "critical", "Termal güzergâh girdi hatası"),
    ("run_iec60287", "solve_thermal_route", "ThermalRouteInputError",
     "critical", "Termal güzergâh girdi hatası"),
    ("run_bonding_solver", "run_bonding_production", "BondingInputError",
     "critical", "Bonding girdi hatası"),
    ("run_fault_study", "solve_fault_study", "FaultStudyError",
     "critical", "Arıza / EPR girdi hatası"),
    ("run_svl_selection", "solve_project_bonding", "BondingInputError",
     "critical", "SVL için bonding girdi hatası"),
    ("run_thermal_preprocessor", "run_application_thermal_preprocessor", "ThermalRouteInputError",
     "critical", "Termal ön işlem girdi hatası"),
    ("run_first_design_iteration", "apply_load_calculation", "FirstDesignInputError",
     "warning", "İlk tasarım iterasyonu"),
]


@pytest.mark.parametrize("method, engine, error_name, kind, title", _MAIN_WINDOW_ENGINE_CASES)
def test_main_window_engines_run_off_gui_thread_and_keep_error_handling(
    main_window, messages, monkeypatch, method, engine, error_name, kind, title
) -> None:
    mw, window = main_window
    calls: list[int] = []
    monkeypatch.setattr(mw, engine, _failing_engine(getattr(mw, error_name), calls))

    getattr(window, method)()

    _assert_handled_on_gui_thread(calls, messages, kind, title)
    assert not task_running()


def test_transient_engine_runs_off_gui_thread(main_window, messages, monkeypatch) -> None:
    mw, window = main_window
    window.nodal_thermal_result = SimpleNamespace(regions=())
    calls: list[int] = []
    monkeypatch.setattr(mw, "solve_transient_route", _failing_engine(mw.TransientThermalInputError, calls))
    try:
        window.run_transient_thermal_analysis()
    finally:
        window.nodal_thermal_result = None
    _assert_handled_on_gui_thread(calls, messages, "critical", "IEC 60853 girdi hatası")


def test_production_electrothermal_point_runs_off_gui_thread(main_window, messages, monkeypatch) -> None:
    mw, window = main_window
    window._load_project_path(SYNTHETIC_CASE)
    calls: list[int] = []
    monkeypatch.setattr(
        mw, "solve_production_electrothermal_study",
        _failing_engine(mw.ThermalRouteInputError, calls),
    )

    window.run_thermal_route_analysis()

    assert calls and calls[0] != threading.get_ident()
    assert window.thermal_route_result is not None
    assert window.production_electrothermal_result is None
    assert "Üretim elektro-termal çalışma noktası bulunamadı." in window.warning_list.toPlainText()


def test_mesh_convergence_runs_off_gui_thread(main_window, messages, monkeypatch) -> None:
    mw, window = main_window
    region = SimpleNamespace(
        design_current_per_cable_a=500.0, active_circuit_count=1,
        regional_lambda1=0.1, energized_circuit_ids=("C1",),
    )
    scope = SimpleNamespace(solution_scope_id="SCENARIO_COMBINED", solution_scope_name="Birleşik")
    study = SimpleNamespace(
        scope_result=lambda _scenario_id, _scope_id: scope,
        iec_route_result=SimpleNamespace(scenarios=[
            SimpleNamespace(scenario_id="DESIGN", regions=[SimpleNamespace(region_id="R1", iec=object())])
        ]),
    )
    window.nodal_thermal_result = study
    window.current_nodal_review_key = ("DESIGN", "SCENARIO_COMBINED", "R1")
    monkeypatch.setattr(mw, "find_nodal_region_result", lambda *_args, **_kwargs: region)
    calls: list[int] = []
    monkeypatch.setattr(mw, "check_mesh_convergence", _failing_engine(RuntimeError, calls))
    try:
        window._run_selected_mesh_convergence()
    finally:
        window.nodal_thermal_result = None
        window.current_nodal_review_key = None
    _assert_handled_on_gui_thread(calls, messages, "critical", "Mesh yakınsama hatası")


def test_cross_bonding_optimizer_runs_off_gui_thread(main_window, messages, monkeypatch) -> None:
    mw, window = main_window
    monkeypatch.setattr(mw.QInputDialog, "getDouble", staticmethod(lambda *_args, **_kwargs: (250.0, True)))
    calls: list[int] = []
    monkeypatch.setattr(
        mw, "resolve_project_bonding_route_sections", _failing_engine(mw.BondingInputError, calls)
    )

    window._auto_design_cross_bonding()

    _assert_handled_on_gui_thread(calls, messages, "critical", "Otomatik bonding tasarım hatası")


def test_fault_study_success_populates_gui_after_worker_returns(main_window, messages, monkeypatch) -> None:
    mw, window = main_window
    window._load_project_path(SYNTHETIC_CASE)
    calls: list[int] = []
    monkeypatch.setattr(mw, "solve_fault_study", _recording_engine(mw.solve_fault_study, calls))

    window.run_fault_study()

    assert calls and calls[0] != threading.get_ident()
    assert window.fault_result is not None
    assert window.fault_result_table.rowCount() == len(window.fault_result.scenario_results) > 0
    assert not [item for item in messages if item[0] == "critical"]


def test_main_window_refuses_to_close_while_engine_runs(main_window) -> None:
    _mw, window = main_window
    window.dirty = False
    window.show()
    seen: dict[str, bool] = {}
    done = threading.Event()

    def try_close() -> None:
        seen["closed"] = window.close()
        seen["visible"] = window.isVisible()
        done.set()

    QTimer.singleShot(10, try_close)
    assert run_blocking_task(window, "Test", "…", done.wait, 5.0) is True
    assert seen == {"closed": False, "visible": True}


# ---------------------------------------------------------------------------
# Ağır diyaloglar
# ---------------------------------------------------------------------------

_DIALOG_ENGINE_CASES = [
    ("electrothermal_coupled_dialog", "ElectroThermalCoupledDialog", "run_solver",
     "solve_electrothermal_coupled", "ElectroThermalInputError", "Elektro-termal girdi hatası"),
    ("electrothermal_coupled_dialog", "ElectroThermalCoupledDialog", "run_ampacity",
     "solve_electrothermal_ampacity", "ElectroThermalInputError", "Elektro-termal ampacity girdi hatası"),
    ("multiconductor_em_dialog", "MulticonductorEMDialog", "run_solver",
     "solve_multiconductor_em", "MulticonductorEMInputError", "N-iletken EM girdi hatası"),
    ("multiconductor_em_dialog", "MulticonductorEMDialog", "run_network_solver",
     "solve_multiconductor_bonding_network", "MulticonductorBondingInputError",
     "N-iletken bonding ağı girdi hatası"),
    ("multiconductor_em_dialog", "MulticonductorEMDialog", "run_global_solver",
     "solve_global_multiconductor_network", "MulticonductorGlobalInputError",
     "Global N-iletken ağ girdi hatası"),
    ("multiconductor_thermal_dialog", "MulticonductorThermalDialog", "run_solver",
     "solve_multiconductor_thermal", "MulticonductorThermalInputError", "Çoklu kablo termal girdi hatası"),
    ("shadow_validation_dialog", "ShadowValidationDialog", "run_validation",
     "run_shadow_validation", "ShadowValidationInputError", "Shadow doğrulama girdi/çözüm hatası"),
]


@pytest.mark.parametrize("module_name, class_name, method, engine, error_name, title", _DIALOG_ENGINE_CASES)
def test_dialog_engines_run_off_gui_thread_and_keep_error_handling(
    qapp, messages, monkeypatch, module_name, class_name, method, engine, error_name, title
) -> None:
    import importlib

    module = importlib.import_module(f"ucd.ui.{module_name}")
    dialog = getattr(module, class_name)(_project())
    calls: list[int] = []
    monkeypatch.setattr(module, engine, _failing_engine(getattr(module, error_name), calls))
    try:
        getattr(dialog, method)()
    finally:
        dialog.close()
    _assert_handled_on_gui_thread(calls, messages, "critical", title)


def test_multiconductor_thermal_dialog_success_populates_tables(qapp, messages, monkeypatch) -> None:
    from ucd.ui import multiconductor_thermal_dialog as module

    dialog = module.MulticonductorThermalDialog(_project())
    calls: list[int] = []
    monkeypatch.setattr(
        module, "solve_multiconductor_thermal", _recording_engine(module.solve_multiconductor_thermal, calls)
    )
    try:
        dialog.run_solver()
        assert calls and calls[0] != threading.get_ident()
        assert dialog.result is not None
        assert dialog.trace_edit.toPlainText().strip()
        assert not [item for item in messages if item[0] == "critical"]
    finally:
        dialog.close()


def test_thermal_alternatives_run_off_gui_thread(qapp, messages, monkeypatch) -> None:
    from ucd.ui import thermal_detail_dialog as module

    fake_dialog = SimpleNamespace(
        alternative_detail=QtWidgets.QPlainTextEdit(), project=object(), nodal_study=object(),
        scenario_id="DESIGN", region_id="R1", scope_id="SCENARIO_COMBINED", alternatives=(),
    )
    calls: list[int] = []
    monkeypatch.setattr(module, "evaluate_thermal_design_alternatives", _failing_engine(RuntimeError, calls))

    module.ThermalAnalysisDialog._calculate_alternatives(fake_dialog)

    _assert_handled_on_gui_thread(calls, messages, "critical", "Termal alternatif hesabı")
    assert fake_dialog.alternative_detail.toPlainText() == ""


def test_installation_contour_runs_off_gui_thread(qapp, messages, monkeypatch) -> None:
    from ucd.ui import installation_designer_dialog as module

    dialog = module.InstallationDesignerDialog(_project(), lambda: None)
    try:
        if not dialog.contour_region_combo.currentData():
            pytest.skip("Sentetik örnekte kesite bağlı termal bölge yok.")
        calls: list[int] = []
        monkeypatch.setattr(
            module, "solve_multiconductor_thermal",
            _failing_engine(module.MulticonductorThermalInputError, calls),
        )
        dialog._run_temperature_contour()
        _assert_handled_on_gui_thread(calls, messages, "warning", "2D sıcaklık konturu")
    finally:
        dialog.close()


class _RunPrecheckDialog:
    RUN = 1
    OPEN_MISSING = 2

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def exec(self) -> int:
        return self.RUN


def test_report_export_runs_off_gui_thread(qapp, messages, monkeypatch, tmp_path) -> None:
    from ucd.calculations.reporting import CalculationResultsBundle
    from ucd.ui import report_builder_dialog as module

    dialog = module.ReportBuilderDialog(_project(), CalculationResultsBundle())
    build_calls: list[int] = []
    write_calls: list[int] = []
    monkeypatch.setattr(module, "EnginePrecheckDialog", _RunPrecheckDialog)
    monkeypatch.setattr(module.QFileDialog, "getExistingDirectory", staticmethod(lambda *_a, **_k: str(tmp_path)))
    monkeypatch.setattr(module, "build_project_report", _recording_engine(module.build_project_report, build_calls))
    monkeypatch.setattr(module, "write_project_report", _failing_engine(RuntimeError, write_calls))
    try:
        dialog.export_reports()
    finally:
        dialog.close()
    assert build_calls and build_calls[0] != threading.get_ident()
    _assert_handled_on_gui_thread(write_calls, messages, "critical", "Rapor üretilemedi")


def test_procurement_export_runs_off_gui_thread(qapp, messages, monkeypatch, tmp_path) -> None:
    from ucd.ui import procurement_dialog as module

    dialog = module.ProcurementDialog(_project())
    calls: list[int] = []
    monkeypatch.setattr(module, "EnginePrecheckDialog", _RunPrecheckDialog)
    monkeypatch.setattr(module.QFileDialog, "getExistingDirectory", staticmethod(lambda *_a, **_k: str(tmp_path)))
    monkeypatch.setattr(module, "write_procurement_package", _failing_engine(RuntimeError, calls))
    try:
        dialog.export_package()
    finally:
        dialog.close()
    _assert_handled_on_gui_thread(calls, messages, "critical", "Tedarik çıktısı üretilemedi")


def test_catalog_comparison_runs_off_gui_thread(qapp, messages, monkeypatch) -> None:
    from ucd.ui import catalog_comparison_dialog as module

    calls: list[int] = []
    monkeypatch.setattr(
        module, "compare_catalog_candidates", _recording_engine(module.compare_catalog_candidates, calls)
    )
    dialog = module.CatalogComparisonDialog(_project())
    try:
        assert calls and calls[0] != threading.get_ident()
        assert dialog.result is not None
    finally:
        dialog.close()
