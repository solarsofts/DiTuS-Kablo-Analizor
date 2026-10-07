"""FAZ 8.0 — Arayüz kabuğu kapanışı.

Dört sözleşmeyi kilitler:

1. Boyut kararı tek yerdedir (``ucd.ui.window_layout``); hiçbir diyalog kendi
   piksel boyutunu seçmez ve iç içe açılan diyaloglar da aynı yoldan geçer.
2. Aşama içeriği sabit bir akış çerçevesi (``StageHostFrame``) içinde açılır;
   kullanıcı akışta nerede olduğunu, ne eksik olduğunu ve sıradaki adımı görür.
3. Sağ ağaçtaki renk ve eksik açıklaması yalnız tasarım akışı dalında değil,
   tüm dallarda vardır; grup düğümü en kötü çocuk durumunu rozetler.
4. Standart katsayı ön tanımları Ayarlar altında toplanır, her alan madde
   adresini taşır ve eksik değer hesap kapısını fail-closed kapatır.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ucd" / "ui"


def _text(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Tek boyut otoritesi
# ---------------------------------------------------------------------------

def test_window_layout_is_the_only_sizing_authority() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "window_layout.py":
            continue
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\.resize\(\s*\d+\s*,\s*\d+\s*\)", source):
            offenders.append(f"{path.name}: {match.group(0)}")
    assert not offenders, "Mutlak pencere boyutu kalmış: " + "; ".join(offenders)


def test_nested_dialogs_also_go_through_the_authority() -> None:
    """Diyalog içinden açılan diyaloglar da sığdırma yolundan geçmeli."""

    for name in (
        "installation_designer_dialog.py",
        "parameter_provenance_dialog.py",
        "cable_library_widget.py",
    ):
        source = _text(name)
        assert "from .window_layout import" in source, name
        assert "fit_window(dialog" in source, name


def test_density_classes_resolve_inside_the_work_area() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    from ucd.ui.window_layout import (
        DENSITY_COMPACT,
        DENSITY_FULL,
        DENSITY_NORMAL,
        DENSITY_WIDE,
        available_work_area,
        fit_window,
    )

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    area = available_work_area(None)
    previous = 0
    for density in (DENSITY_COMPACT, DENSITY_NORMAL, DENSITY_WIDE, DENSITY_FULL):
        widget = QtWidgets.QDialog()
        fit_window(widget, density)
        geometry = widget.geometry()
        assert geometry.width() <= area.width()
        assert geometry.height() <= area.height()
        # Ekrana sığdırma maksimum boyut kilidi değildir; kullanıcı pencereyi
        # daha sonra maksimize edebilmelidir.
        assert widget.maximumWidth() > area.width()
        assert widget.maximumHeight() > area.height()
        assert geometry.width() >= previous, "yoğunluk sınıfları artan olmalı"
        previous = geometry.width()
    assert app is not None


# ---------------------------------------------------------------------------
# 2. Aşama konağı
# ---------------------------------------------------------------------------

def test_stage_host_frame_carries_position_missing_items_and_navigation() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    from ucd.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(Path(os.environ.get("TMPDIR", "/tmp")) / "ditus-phase8-host")
    host = window.stage_host

    window._activate_workflow_stage("system_load")
    assert host.step_badge.text() == "1"
    assert host.position_label.text().startswith("Adım 1 /")
    assert not host.prev_button.isEnabled()
    assert host.next_button.isEnabled()

    window._go_next_stage()
    assert host.step_badge.text() == "2"
    assert host.prev_button.isEnabled()

    window._go_previous_stage()
    assert host.step_badge.text() == "1"
    assert app is not None


def test_workspace_widgets_live_inside_the_stage_host_body() -> None:
    source = _text("main_window.py")
    assert "self.stage_host = StageHostFrame()" in source
    assert "self.stage_host.set_body(self.workspace_tabs)" in source
    assert "module_layout.addWidget(self.stage_host, 1)" in source
    # Aşama değişimi konağı da tazelemeli; yoksa çerçeve içerikten kopar.
    assert "self._sync_stage_host(" in source


# ---------------------------------------------------------------------------
# 3. Ağaç durum kaplaması
# ---------------------------------------------------------------------------

def test_tree_status_and_tooltips_cover_every_branch() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt

    from ucd.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(Path(os.environ.get("TMPDIR", "/tmp")) / "ditus-phase8-tree")
    root = window.project_tree.topLevelItem(0)

    with_status = 0
    with_tooltip = 0
    total = 0
    for group_index in range(root.childCount()):
        group = root.child(group_index)
        for child_index in range(group.childCount()):
            child = group.child(child_index)
            total += 1
            if child.data(0, Qt.UserRole + 1):
                with_status += 1
            if child.toolTip(0):
                with_tooltip += 1

    assert total > 0
    # Kaplama yalnız tasarım akışı dalında kalmamalı.
    assert with_status >= total * 0.6, f"durum taşıyan düğüm oranı düşük: {with_status}/{total}"
    assert with_tooltip == total, "her düğüm açıklama taşımalı"
    assert app is not None


def test_group_nodes_roll_up_the_worst_child_status() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    from ucd.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(Path(os.environ.get("TMPDIR", "/tmp")) / "ditus-phase8-rollup")
    root = window.project_tree.topLevelItem(0)
    labels = [root.child(index).text(0) for index in range(root.childCount())]
    badged = [label for label in labels if label[:1] in {"■", "▲", "○"}]
    assert badged, "hiçbir grup rozet taşımıyor; ağacı açmadan sorun görünmez"
    assert app is not None


def test_tooltips_are_width_limited_rich_text() -> None:
    source = _text("main_window.py")
    assert "def _rich_tooltip(" in source
    assert "max-width:430px" in source
    # Düz metin birleştirme uzun eksik listelerinde taşıyordu.
    assert 'child.setToolTip(0, "\\n".join(tooltip))' not in source


# ---------------------------------------------------------------------------
# 4. Standart katsayı ön tanımları
# ---------------------------------------------------------------------------

def test_every_required_default_names_its_standard_clause() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.ui.standard_defaults_dialog import FIELD_GROUPS

    for _key, _title, rows in FIELD_GROUPS:
        for name, label, _unit, clause, _required in rows:
            assert clause.strip(), f"{name} madde adresi taşımıyor"
            assert "IEC" in clause, f"{name} için standart kimliği yazılmamış: {label}"


def test_missing_defaults_are_reported_with_clause_addresses() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.ui.standard_defaults_dialog import StandardDefaults, missing_default_fields

    missing = missing_default_fields(StandardDefaults())
    assert missing, "boş profilde zorunlu alanlar eksik sayılmalı"
    for name, label, clause in missing:
        assert name and label and clause
        assert "IEC" in clause


def test_defaults_round_trip_through_an_exportable_pack(tmp_path) -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.ui.standard_defaults_dialog import (
        CoefficientEntry,
        StandardDefaults,
        load_standard_defaults,
        missing_default_fields,
        save_standard_defaults,
    )

    defaults = StandardDefaults()
    defaults.soil_resistivity_km_w = CoefficientEntry(1.0, "USER_STANDARD_COPY", "TS IEC 60287-3-1")
    path = tmp_path / "pack.json"
    save_standard_defaults(path, defaults)
    restored = load_standard_defaults(path)
    assert restored.soil_resistivity_km_w.value == pytest.approx(1.0)
    assert restored.soil_resistivity_km_w.provenance == "USER_STANDARD_COPY"
    assert restored.soil_resistivity_km_w.reference == "TS IEC 60287-3-1"
    # Bir alanın dolması diğerlerini tamamlamaz; kapı hâlâ kapalı olmalı.
    assert missing_default_fields(restored)


def test_value_without_provenance_is_not_accepted_as_complete() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ucd.ui.standard_defaults_dialog import CoefficientEntry

    assert not CoefficientEntry(1.2, "", "").is_complete
    assert not CoefficientEntry(0.0, "MANUFACTURER", "").is_complete
    assert CoefficientEntry(1.2, "MANUFACTURER", "").is_complete


def test_standard_defaults_profile_is_not_a_global_engine_gate() -> None:
    source = _text("main_window.py")
    assert "def show_standard_defaults(" in source
    assert "def _confirm_standard_defaults(" not in source
    assert "Standart katsayı ön tanımları eksik olduğu için motor çalıştırılmadı" not in source
    assert "result = evaluate_engine_precheck(self.project, engine_id)" in source


def test_defaults_action_is_reachable_from_settings_menu() -> None:
    source = _text("main_window.py")
    assert 'settings_menu.addAction(self.act_standard_defaults)' in source
    assert '"Standart Katsayıları ve Varsayılanlar…"' in source


def test_defaults_screen_is_optional_and_does_not_reproduce_table_layout() -> None:
    source = _text("standard_defaults_dialog.py")
    assert "evrensel zorunlu veri kapısı değildir" in source
    assert "tablo düzeni" in source
    assert "0.435" not in source
    assert "0.62" not in source
