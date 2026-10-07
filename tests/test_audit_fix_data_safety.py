"""Audit fixes O-2 (corrupt application data), O-3 (atomic saves), D-1 (schema version)."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import pytest

from ucd import fileio
from ucd.calculations.application_database import (
    load_application_cable_database,
    load_application_cable_database_with_status,
    save_application_cable_database,
)
from ucd.calculations.cable_library import (
    CableLibraryInputError,
    catalog_package_from_dict,
    catalog_package_to_dict,
)
from ucd.fileio import atomic_write_text, quarantine_file
from ucd.models.project import (
    EXTERNAL_THERMAL_AUTO,
    EXTERNAL_THERMAL_MANUAL,
    INTERNAL_THERMAL_AUTO,
    INTERNAL_THERMAL_MANUAL,
    PROJECT_SCHEMA_VERSION,
    CableCatalogRecord,
    ProjectData,
    is_schema_newer_than_supported,
    schema_version_tuple,
)

DB_NAME = "cable_database.ditus-cable-catalog.json"


def _builtin_record_ids(tmp_path: Path) -> list[str]:
    library = load_application_cable_database(tmp_path / "missing" / DB_NAME)
    return [record.record_id for record in library.records]


def _valid_database_bytes(tmp_path: Path) -> bytes:
    scratch = tmp_path / "scratch" / DB_NAME
    library = load_application_cable_database(scratch)
    library.records.append(
        CableCatalogRecord(
            record_id="USER-KEEP-001",
            manufacturer="User",
            series="Kept",
            model="1x630 Cu",
            voltage_class="20.3/35 kV",
            conductor_material="Cu",
            conductor_area_mm2=630.0,
        )
    )
    save_application_cable_database(library, scratch)
    return scratch.read_bytes()


def _corrupt_variants(tmp_path: Path) -> dict[str, bytes]:
    valid = _valid_database_bytes(tmp_path)
    return {
        "list_root": b"[]",
        "string_root": b'"x"',
        "truncated": valid[: len(valid) // 2],
        "records_not_list": json.dumps(
            {"format": "DITUS_CABLE_CATALOG", "package": {"records": "USER-KEEP-001"}}
        ).encode("utf-8"),
        "sources_not_list": json.dumps(
            {"format": "DITUS_CABLE_CATALOG", "package": {"sources": {"a": 1}, "records": []}}
        ).encode("utf-8"),
        "not_utf8": b"\xff\xfe\x00garbage",
    }


# ---------------------------------------------------------------------------
# O-2: corrupt / unreadable application database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    ["list_root", "string_root", "truncated", "records_not_list", "sources_not_list", "not_utf8"],
)
def test_corrupt_database_is_quarantined_and_builtins_are_loaded(tmp_path: Path, variant: str) -> None:
    original = _corrupt_variants(tmp_path)[variant]
    root = tmp_path / "appdata"
    root.mkdir()
    path = root / DB_NAME
    path.write_bytes(original)

    status = load_application_cable_database_with_status(path)

    assert status.save_allowed is True
    assert status.quarantined_path is not None
    assert status.quarantined_path.parent == root
    assert re.fullmatch(re.escape(DB_NAME) + r"\.corrupt-\d{8}-\d{6}", status.quarantined_path.name)
    assert status.quarantined_path.read_bytes() == original
    assert not path.exists()
    assert status.quarantined_path.name in status.error_message
    assert [record.record_id for record in status.library.records] == _builtin_record_ids(tmp_path)
    assert status.library.package_source == "APPLICATION_DATABASE"


@pytest.mark.parametrize("payload", [b"[]", b'"x"', b"[1, 2]"])
def test_legacy_loader_never_raises_for_non_dict_root(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / DB_NAME
    path.write_bytes(payload)
    library = load_application_cable_database(path)
    assert [record.record_id for record in library.records] == _builtin_record_ids(tmp_path)
    assert list(tmp_path.glob(DB_NAME + ".corrupt-*"))


def test_save_after_quarantine_keeps_quarantined_copy(tmp_path: Path) -> None:
    path = tmp_path / DB_NAME
    path.write_bytes(b"{ truncated")
    status = load_application_cable_database_with_status(path)
    assert status.quarantined_path is not None

    save_application_cable_database(status.library, path)

    assert status.quarantined_path.read_bytes() == b"{ truncated"
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == "DITUS_CABLE_CATALOG"


def test_valid_database_with_bom_is_loaded_not_quarantined(tmp_path: Path) -> None:
    path = tmp_path / DB_NAME
    path.write_bytes(b"\xef\xbb\xbf" + _valid_database_bytes(tmp_path))
    status = load_application_cable_database_with_status(path)
    assert status.error_message == ""
    assert status.quarantined_path is None
    assert any(record.record_id == "USER-KEEP-001" for record in status.library.records)
    assert path.exists()


def test_unreadable_database_path_blocks_saving_and_is_not_touched(tmp_path: Path) -> None:
    path = tmp_path / DB_NAME
    path.mkdir()

    status = load_application_cable_database_with_status(path)

    assert status.save_allowed is False
    assert status.quarantined_path is None
    assert status.error_message
    assert [record.record_id for record in status.library.records] == _builtin_record_ids(tmp_path)
    assert path.is_dir()
    assert list(path.iterdir()) == []
    assert not list(tmp_path.glob(DB_NAME + ".corrupt-*"))


def test_failed_quarantine_blocks_saving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ucd.calculations import application_database

    path = tmp_path / DB_NAME
    path.write_bytes(b"[]")

    def _refuse(_path, reason_tag="corrupt"):
        raise PermissionError("locked")

    monkeypatch.setattr(application_database, "quarantine_file", _refuse)
    status = load_application_cable_database_with_status(path)
    assert status.save_allowed is False
    assert status.quarantined_path is None
    assert path.read_bytes() == b"[]"


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "x",
        {"format": "DITUS_CABLE_CATALOG", "package": {"records": "abc"}},
        {"format": "DITUS_CABLE_CATALOG", "package": {"sources": {"a": 1}}},
        {"format": "DITUS_CABLE_CATALOG", "package": {"records": None}},
    ],
)
def test_catalog_package_from_dict_rejects_malformed_structure(raw) -> None:
    with pytest.raises(CableLibraryInputError):
        catalog_package_from_dict(raw)


def test_catalog_package_round_trip_still_accepted(tmp_path: Path) -> None:
    library = load_application_cable_database(tmp_path / DB_NAME)
    loaded = catalog_package_from_dict(catalog_package_to_dict(library))
    assert [record.record_id for record in loaded.records] == [record.record_id for record in library.records]


# ---------------------------------------------------------------------------
# fileio helpers
# ---------------------------------------------------------------------------


def test_atomic_write_keeps_original_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project.ucd.json"
    target.write_text("original", encoding="utf-8")

    def _fail_replace(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(fileio.os, "replace", _fail_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "original"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["project.ucd.json"]


def test_atomic_write_keeps_original_when_encoding_fails(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "kablo ğüşıöç", encoding="ascii")
    assert target.read_text(encoding="utf-8") == "original"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["data.json"]


def test_atomic_write_replaces_content_and_optionally_keeps_backup(tmp_path: Path) -> None:
    target = tmp_path / "project.ucd.json"
    atomic_write_text(target, "v1", backup=True)
    assert target.read_text(encoding="utf-8") == "v1"
    assert not (tmp_path / "project.ucd.json.bak").exists()

    atomic_write_text(target, "v2", backup=True)
    assert target.read_text(encoding="utf-8") == "v2"
    assert (tmp_path / "project.ucd.json.bak").read_text(encoding="utf-8") == "v1"

    atomic_write_text(target, "v3")
    assert target.read_text(encoding="utf-8") == "v3"
    assert (tmp_path / "project.ucd.json.bak").read_text(encoding="utf-8") == "v1"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["project.ucd.json", "project.ucd.json.bak"]


def test_atomic_write_through_symlink_updates_link_target(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("old", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not available")
    atomic_write_text(link, "new")
    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "new"


def test_quarantine_names_are_unique(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FixedClock:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(fileio, "datetime", _FixedClock)
    target = tmp_path / DB_NAME
    target.write_text("first", encoding="utf-8")
    first = quarantine_file(target)
    target.write_text("second", encoding="utf-8")
    second = quarantine_file(target)

    assert first.name == f"{DB_NAME}.corrupt-20260102-030405"
    assert second.name == f"{DB_NAME}.corrupt-20260102-030405-1"
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"
    assert not target.exists()


def test_application_database_save_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / DB_NAME
    original = _valid_database_bytes(tmp_path)
    path.write_bytes(original)
    library = load_application_cable_database(path)

    def _fail_replace(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(fileio.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        save_application_cable_database(library, path)
    assert path.read_bytes() == original
    assert sorted(item.name for item in tmp_path.iterdir()) == [DB_NAME, "scratch"]


# ---------------------------------------------------------------------------
# Standard defaults file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ['"x"', "[]", "[1, 2]", "42", '{"ks_round_stranded": {"value": "abc"}}', '{"ks_round_stranded": {"value": [1]}}'],
)
def test_standard_defaults_invalid_payload_returns_defaults(tmp_path: Path, content: str) -> None:
    from ucd.ui.standard_defaults_dialog import StandardDefaults, load_standard_defaults

    path = tmp_path / "ditus-standard-defaults.json"
    path.write_text(content, encoding="utf-8")
    assert load_standard_defaults(path) == StandardDefaults()


def test_standard_defaults_save_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ucd.ui.standard_defaults_dialog import (
        CoefficientEntry,
        StandardDefaults,
        load_standard_defaults,
        save_standard_defaults,
    )

    path = tmp_path / "ditus-standard-defaults.json"
    defaults = StandardDefaults()
    defaults.soil_resistivity_km_w = CoefficientEntry(1.2, "MANUFACTURER", "ref")
    save_standard_defaults(path, defaults)
    original = path.read_bytes()

    def _fail_replace(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(fileio.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        save_standard_defaults(path, StandardDefaults())
    assert path.read_bytes() == original
    assert load_standard_defaults(path).soil_resistivity_km_w.value == 1.2
    assert sorted(item.name for item in tmp_path.iterdir()) == ["ditus-standard-defaults.json"]


# ---------------------------------------------------------------------------
# D-1: schema version comparison
# ---------------------------------------------------------------------------


def _legacy_probe(schema_version: str) -> ProjectData:
    return ProjectData.from_dict(
        {
            "schema_version": schema_version,
            "route_sections": [{"name": "A", "length_m": 100.0}],
        }
    )


@pytest.mark.parametrize("schema_version", ["0.1", "0.2", "0.2.9"])
def test_pre_v03_schema_is_legacy_thermal(schema_version: str) -> None:
    project = _legacy_probe(schema_version)
    assert project.cable.internal_thermal_mode == INTERNAL_THERMAL_MANUAL
    assert project.route_sections[0].external_thermal_mode == EXTERNAL_THERMAL_MANUAL


@pytest.mark.parametrize("schema_version", ["0.3", "0.10", "0.11", "0.16", "0.16.3.1", "0.16.4", "0.17", "1.0"])
def test_v03_and_later_schemas_are_not_legacy_thermal(schema_version: str) -> None:
    project = _legacy_probe(schema_version)
    assert project.cable.internal_thermal_mode == INTERNAL_THERMAL_AUTO
    assert project.route_sections[0].external_thermal_mode == EXTERNAL_THERMAL_AUTO
    assert project.schema_version == PROJECT_SCHEMA_VERSION


def test_unparseable_schema_keeps_fallback_allow_list() -> None:
    project = _legacy_probe("0.16-beta")
    assert project.cable.internal_thermal_mode == INTERNAL_THERMAL_MANUAL


def test_schema_version_helpers() -> None:
    assert PROJECT_SCHEMA_VERSION == "0.16.4"
    assert ProjectData().schema_version == PROJECT_SCHEMA_VERSION
    assert ProjectData().to_dict()["schema_version"] == PROJECT_SCHEMA_VERSION
    assert schema_version_tuple("0.16.4") == (0, 16, 4)
    assert schema_version_tuple("0.10") == (0, 10)
    assert schema_version_tuple("0.3.0") == (0, 3)
    assert schema_version_tuple(" 0.17 ") == (0, 17)
    for invalid in ("", "abc", "0.16-beta", "1..2", "+1.0", "1_0", "0.²"):
        assert schema_version_tuple(invalid) is None
    for newer in ("0.17", "0.16.5", "0.16.4.1", "1.0", "1"):
        assert is_schema_newer_than_supported(newer), newer
    for supported in ("0.16.4", "0.16.4.0", "0.16.3.1", "0.16", "0.10", "0.2", "abc", "", None):
        assert not is_schema_newer_than_supported(supported), supported


# ---------------------------------------------------------------------------
# UI integration (offscreen)
# ---------------------------------------------------------------------------


@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _recording_message_box():
    from PySide6.QtWidgets import QMessageBox

    class _RecordingMessageBox(QMessageBox):
        calls: ClassVar[list[tuple[str, tuple]]] = []

        @staticmethod
        def critical(*args, **_kwargs):
            _RecordingMessageBox.calls.append(("critical", args))
            return QMessageBox.Ok

        @staticmethod
        def warning(*args, **_kwargs):
            _RecordingMessageBox.calls.append(("warning", args))
            return QMessageBox.Ok

        @staticmethod
        def information(*args, **_kwargs):
            _RecordingMessageBox.calls.append(("information", args))
            return QMessageBox.Ok

    _RecordingMessageBox.calls = []
    return _RecordingMessageBox


def _isolated_app_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from PySide6.QtCore import QStandardPaths

    from ucd.ui import main_window

    app_data = tmp_path / "appdata"
    app_data.mkdir()

    class _Paths:
        AppDataLocation = QStandardPaths.AppDataLocation

        @staticmethod
        def writableLocation(_location) -> str:
            return str(app_data)

    monkeypatch.setattr(main_window, "QStandardPaths", _Paths)
    return app_data


def _close(window, app) -> None:
    from PySide6.QtWidgets import QMessageBox

    for box in window.findChildren(QMessageBox):
        box.close()
    window.close()
    app.processEvents()


def test_main_window_survives_corrupt_database_and_defaults(qt_app, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from ucd.ui import main_window

    app_data = _isolated_app_data(monkeypatch, tmp_path)
    recorder = _recording_message_box()
    monkeypatch.setattr(main_window, "QMessageBox", recorder)
    db_path = app_data / DB_NAME
    db_path.write_bytes(b"[]")
    (app_data / "ditus-standard-defaults.json").write_text('"x"', encoding="utf-8")

    window = main_window.MainWindow(tmp_path)
    try:
        quarantined = list(app_data.glob(DB_NAME + ".corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == b"[]"
        assert not db_path.exists()
        assert window.application_database_path == db_path
        assert window.application_database_save_allowed is True
        assert quarantined[0].name in window.warning_list.toPlainText()
        assert [r.record_id for r in window.database_project.cable_library.records] == _builtin_record_ids(tmp_path)
        assert recorder.calls == []

        qt_app.processEvents()
        popups = [box for box in window.findChildren(QMessageBox) if box.isVisible()]
        assert popups and all(not box.isModal() for box in popups)

        window._on_database_changed()
        assert json.loads(db_path.read_text(encoding="utf-8"))["format"] == "DITUS_CABLE_CATALOG"
        assert quarantined[0].read_bytes() == b"[]"
    finally:
        _close(window, qt_app)


def test_main_window_does_not_overwrite_unreadable_database(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import main_window

    app_data = _isolated_app_data(monkeypatch, tmp_path)
    recorder = _recording_message_box()
    monkeypatch.setattr(main_window, "QMessageBox", recorder)
    db_path = app_data / DB_NAME
    db_path.mkdir()

    window = main_window.MainWindow(tmp_path)
    try:
        assert window.application_database_save_allowed is False
        window._on_database_changed()
        window._on_database_changed()
        assert db_path.is_dir() and list(db_path.iterdir()) == []
        assert not list(app_data.glob(DB_NAME + ".corrupt-*"))
        assert "üzerine yazılmadı" in window.warning_list.toPlainText()
        assert recorder.calls == []
    finally:
        _close(window, qt_app)


def test_main_window_save_project_is_atomic_with_backup(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import main_window

    _isolated_app_data(monkeypatch, tmp_path)
    recorder = _recording_message_box()
    monkeypatch.setattr(main_window, "QMessageBox", recorder)
    project_path = tmp_path / "proje.ucd.json"
    project_path.write_text('{"schema_version": "0.16.4", "project_name": "eski"}', encoding="utf-8")

    window = main_window.MainWindow(tmp_path)
    try:
        window.current_file = project_path
        assert window.save_project() is True
        backup = tmp_path / "proje.ucd.json.bak"
        assert json.loads(backup.read_text(encoding="utf-8"))["project_name"] == "eski"
        saved = json.loads(project_path.read_text(encoding="utf-8"))
        assert saved["schema_version"] == PROJECT_SCHEMA_VERSION
        good_copy = project_path.read_bytes()

        def _fail_replace(_src, _dst):
            raise OSError("disk full")

        monkeypatch.setattr(fileio.os, "replace", _fail_replace)
        window.project.project_name = "yarım kalan kayıt"
        assert window.save_project() is False
        assert project_path.read_bytes() == good_copy
        assert [name for name, _args in recorder.calls] == ["critical"]
        assert not list(tmp_path.glob(".proje.ucd.json.*.tmp"))
    finally:
        _close(window, qt_app)


def test_main_window_warns_when_project_schema_is_newer(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import main_window

    _isolated_app_data(monkeypatch, tmp_path)
    recorder = _recording_message_box()
    monkeypatch.setattr(main_window, "QMessageBox", recorder)
    newer = tmp_path / "gelecek.ucd.json"
    raw = ProjectData().to_dict()
    raw["schema_version"] = "0.17"
    newer.write_text(json.dumps(raw), encoding="utf-8")
    current = tmp_path / "guncel.ucd.json"
    current.write_text(json.dumps(ProjectData().to_dict()), encoding="utf-8")

    window = main_window.MainWindow(tmp_path)
    try:
        window._load_project_path(current)
        assert "Proje şema sürümü" not in window.warning_list.toPlainText()

        window._load_project_path(newer)
        assert window.current_file == newer
        assert window.project.schema_version == PROJECT_SCHEMA_VERSION
        warnings = window.warning_list.toPlainText()
        assert "Proje şema sürümü" in warnings and "0.17" in warnings
        assert recorder.calls == []
    finally:
        _close(window, qt_app)


def test_standard_defaults_dialog_accept_keeps_dialog_open_on_write_error(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import standard_defaults_dialog as dialog_module
    from ucd.ui.standard_defaults_dialog import StandardDefaults, StandardDefaultsDialog

    recorder = _recording_message_box()
    monkeypatch.setattr(dialog_module, "QMessageBox", recorder)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("file", encoding="utf-8")
    original = StandardDefaults()

    dialog = StandardDefaultsDialog(original, blocker / "ditus-standard-defaults.json")
    try:
        dialog._accept()
        assert [name for name, _args in recorder.calls] == ["critical"]
        assert dialog.result() == 0
        assert dialog.result_defaults is original
    finally:
        dialog.close()
        qt_app.processEvents()

    ok_path = tmp_path / "ok" / "ditus-standard-defaults.json"
    dialog = StandardDefaultsDialog(original, ok_path)
    try:
        dialog._accept()
        assert dialog.result() == 1
        assert ok_path.exists()
    finally:
        dialog.close()
        qt_app.processEvents()


def test_catalog_export_reports_write_errors(qt_app, tmp_path, monkeypatch) -> None:
    from ucd.ui import cable_library_widget as widget_module
    from ucd.ui.cable_library_widget import CableLibraryWidget

    recorder = _recording_message_box()
    monkeypatch.setattr(widget_module, "QMessageBox", recorder)
    target = {"path": str(tmp_path / "yok" / "katalog.ditus-cable-catalog.json")}

    class _Dialog:
        @staticmethod
        def getSaveFileName(*_args, **_kwargs):
            return target["path"], ""

    monkeypatch.setattr(widget_module, "QFileDialog", _Dialog)
    project = ProjectData()
    project.cable_library = load_application_cable_database(tmp_path / DB_NAME)
    widget = CableLibraryWidget(project, database_mode=True)
    try:
        widget._export_catalog()
        assert [name for name, _args in recorder.calls] == ["critical"]

        recorder.calls.clear()
        target["path"] = str(tmp_path / "katalog.ditus-cable-catalog.json")
        widget._export_catalog()
        assert [name for name, _args in recorder.calls] == ["information"]
        exported = json.loads(Path(target["path"]).read_text(encoding="utf-8"))
        assert exported["format"] == "DITUS_CABLE_CATALOG"
    finally:
        widget.close()
        qt_app.processEvents()
