from __future__ import annotations

import cmath
from math import pi

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ucd.calculations.multiconductor_bonding_network import (
    MulticonductorBondingInputError,
    MulticonductorBondingNetworkResult,
    render_multiconductor_bonding_network,
    solve_multiconductor_bonding_network,
)
from ucd.calculations.multiconductor_global_network import (
    GlobalMulticonductorNetworkResult,
    MulticonductorGlobalInputError,
    render_global_multiconductor_network,
    solve_global_multiconductor_network,
)
from ucd.calculations.multiconductor_em import (
    SHEATH_OPEN,
    SHEATH_SOLID_BOTH_END,
    MulticonductorEMInputError,
    MulticonductorEMResult,
    render_multiconductor_em,
    solve_multiconductor_em,
)
from ucd.models.project import ProjectData
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


def _angle(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


def _cell(value: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(value))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class MulticonductorEMDialog(QDialog):
    """Read-only v0.16.6 local, route and global shadow comparison window."""

    def __init__(self, project: ProjectData, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.result: MulticonductorEMResult | None = None
        self.network_result: MulticonductorBondingNetworkResult | None = None
        self.global_result: GlobalMulticonductorNetworkResult | None = None
        self.setWindowTitle("DiTuS — Genel N-İletken EM / Bonding / Global Core Gölge Çözümleri")
        fit_window(self, DENSITY_WIDE)

        layout = QVBoxLayout(self)
        notice = QLabel(
            "v0.16.6 SHADOW_COMPARE — yerel kesit, minor-section N-kılıf ağı ve güzergâh boyunca "
            "global core sürekliliği + bonding ağı. Bu ekran mevcut bonding/IEC/nodal sonuçlarını ve proje λ1 değerini değiştirmez."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "font-weight:700; color:#174a6e; background:#eef6fb; border:1px solid #a8c7dc; "
            "padding:8px; border-radius:5px;"
        )
        layout.addWidget(notice)

        controls = QGridLayout()
        controls.addWidget(QLabel("Fiziksel kesit"), 0, 0)
        self.section_combo = QComboBox()
        for section in project.installation_design.cross_sections:
            self.section_combo.addItem(f"{section.cross_section_id} — {section.name}", section.cross_section_id)
        active = project.installation_design.active_cross_section_id
        if active:
            for index in range(self.section_combo.count()):
                if self.section_combo.itemData(index) == active:
                    self.section_combo.setCurrentIndex(index)
                    break
        controls.addWidget(self.section_combo, 0, 1)

        controls.addWidget(QLabel("Yerel kılıf sınır koşulu"), 0, 2)
        self.sheath_combo = QComboBox()
        self.sheath_combo.addItem("SOLID_BOTH_END_SECTION — yerel iki uçtan bağlı", SHEATH_SOLID_BOTH_END)
        self.sheath_combo.addItem("OPEN_SHEATH — açık kılıf / yalnız indüklenen EMF", SHEATH_OPEN)
        controls.addWidget(self.sheath_combo, 0, 3)

        local_button = QPushButton("Yerel N-İletken Çözümünü Çalıştır")
        local_button.clicked.connect(self.run_solver)
        controls.addWidget(local_button, 0, 4)

        network_button = QPushButton("N-İletken Bonding Ağını Çalıştır")
        network_button.clicked.connect(self.run_network_solver)
        controls.addWidget(network_button, 1, 4)

        global_button = QPushButton("Global Core + Bonding Çözümünü Çalıştır")
        global_button.clicked.connect(self.run_global_solver)
        controls.addWidget(global_button, 2, 4)
        network_note = QLabel(
            "Tüm güzergâh: fiziksel kılıflar + LB çapraz bağlantıları + toprak/GCC dalları"
        )
        network_note.setWordWrap(True)
        network_note.setStyleSheet("font-style:italic; color:#5a6670;")
        controls.addWidget(network_note, 1, 0, 1, 4)
        global_note = QLabel(
            "Tüm güzergâh: paralel core akımları tek global süreklilik kısıtıyla, gerçek kılıf/link-box ağıyla birlikte çözülür"
        )
        global_note.setWordWrap(True)
        global_note.setStyleSheet("font-style:italic; color:#5a6670;")
        controls.addWidget(global_note, 2, 0, 1, 4)
        controls.setColumnStretch(1, 2)
        controls.setColumnStretch(3, 1)
        layout.addLayout(controls)

        self.summary = QLabel("Henüz çözüm çalıştırılmadı.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("padding:6px; font-weight:700;")
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.cable_table = QTableWidget(0, 15)
        self.cable_table.setHorizontalHeaderLabels([
            "Fiziksel kablo", "Devre", "Faz", "Paralel", "x [m]", "Derinlik [m]",
            "|Ic| [A]", "∠Ic [°]", "Pay [%]", "Eşit önizleme [A]", "ΔI [A]",
            "|Ish| [A]", "∠Ish [°]", "Pc [W/km]", "Psh [W/km]",
        ])
        self.cable_table.setAlternatingRowColors(True)
        self.cable_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.cable_table, "Yerel · Fiziksel Kablo Akımları")

        self.group_table = QTableWidget(0, 10)
        self.group_table.setHorizontalHeaderLabels([
            "Devre:Faz", "Paralel", "Hedef |I|", "Çözülen |ΣI|", "Residual [A]",
            "|ΔV| [V/km]", "∠ΔV [°]", "I maks.", "I min.", "Dengesizlik [%]",
        ])
        self.group_table.setAlternatingRowColors(True)
        self.group_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.group_table, "Yerel · Devre/Faz Kısıtları")

        self.emf_table = QTableWidget(0, 5)
        self.emf_table.setHorizontalHeaderLabels([
            "Fiziksel kablo", "|E açık| [V/km]", "∠E [°]", "|Ish| [A]", "Kılıf kaybı [W/km]"
        ])
        self.emf_table.setAlternatingRowColors(True)
        self.emf_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.emf_table, "Yerel · Kılıf EMF/Akım")

        self.network_section_table = QTableWidget(0, 12)
        self.network_section_table.setHorizontalHeaderLabels([
            "Minor", "Major", "Başlangıç [m]", "Bitiş [m]", "Fiziksel kesitler",
            "Kılıf adedi", "|Ish|max [A]", "|Vsh-e|max [V]", "|Vsh-sh|max [V]",
            "Pcore [W]", "Psh [W]", "Pgcc [W]",
        ])
        self.network_section_table.setAlternatingRowColors(True)
        self.network_section_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.network_section_table, "Ağ · Minor Section Özeti")

        self.network_sheath_table = QTableWidget(0, 13)
        self.network_sheath_table.setHorizontalHeaderLabels([
            "Minor", "Fiziksel kablo", "Devre", "Faz", "Paralel",
            "|Ish| [A]", "∠Ish [°]", "|Vbaş-e| [V]", "∠Vbaş [°]",
            "|Vson-e| [V]", "∠Vson [°]", "|E açık| [V]", "Psh [W]",
        ])
        self.network_sheath_table.setAlternatingRowColors(True)
        self.network_sheath_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.network_sheath_table, "Ağ · Fiziksel Kılıf V/I")

        self.network_branch_table = QTableWidget(0, 8)
        self.network_branch_table.setHorizontalHeaderLabels([
            "Dal", "Tip", "Başlangıç", "Bitiş", "|I| [A]", "∠I [°]", "R+jX [Ω]", "Kayıp [W]"
        ])
        self.network_branch_table.setAlternatingRowColors(True)
        self.network_branch_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.network_branch_table, "Ağ · Link Box/Toprak Dalları")

        self.trace_edit = QPlainTextEdit()
        self.trace_edit.setReadOnly(True)
        self.tabs.addTab(self.trace_edit, "Yerel · İz ve Sınırlar")

        self.network_trace_edit = QPlainTextEdit()
        self.network_trace_edit.setReadOnly(True)
        self.tabs.addTab(self.network_trace_edit, "Ağ · İz ve Sınırlar")

        self.global_core_table = QTableWidget(0, 12)
        self.global_core_table.setHorizontalHeaderLabels([
            "Fiziksel kablo", "Devre", "Faz", "Paralel", "|Ic| [A]", "∠Ic [°]",
            "Pay [%]", "Eşit pay [A]", "ΔI [A]", "|ΔV route| [V]", "∠ΔV [°]", "Pcore [W]",
        ])
        self.global_core_table.setAlternatingRowColors(True)
        self.global_core_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.global_core_table, "Global · Core Sürekliliği")

        self.global_group_table = QTableWidget(0, 10)
        self.global_group_table.setHorizontalHeaderLabels([
            "Devre:Faz", "Paralel", "Hedef |I|", "Çözülen |ΣI|", "Residual [A]",
            "|ΔV route| [V]", "∠ΔV [°]", "I maks.", "I min.", "Dengesizlik [%]",
        ])
        self.global_group_table.setAlternatingRowColors(True)
        self.global_group_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.global_group_table, "Global · Devre/Faz Kısıtları")

        self.global_section_table = QTableWidget(0, 12)
        self.global_section_table.setHorizontalHeaderLabels([
            "Minor", "Major", "Başlangıç [m]", "Bitiş [m]", "Fiziksel kesitler",
            "Kılıf adedi", "|Ish|max [A]", "|Vsh-e|max [V]", "|Vsh-sh|max [V]",
            "Pcore [W]", "Psh [W]", "Pgcc [W]",
        ])
        self.global_section_table.setAlternatingRowColors(True)
        self.global_section_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.global_section_table, "Global · Minor Section Özeti")

        self.global_trace_edit = QPlainTextEdit()
        self.global_trace_edit.setReadOnly(True)
        self.tabs.addTab(self.global_trace_edit, "Global · İz ve Sınırlar")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def run_solver(self) -> None:
        try:
            self.result = run_blocking_task(
                self, "N-iletken EM", "N-iletken EM gölge çözümü çalışıyor…",
                solve_multiconductor_em,
                self.project,
                cross_section_id=str(self.section_combo.currentData() or ""),
                sheath_mode=str(self.sheath_combo.currentData() or SHEATH_SOLID_BOTH_END),
            )
        except MulticonductorEMInputError as exc:
            QMessageBox.critical(self, "N-iletken EM girdi hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "N-iletken EM hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate_local()

    def run_network_solver(self) -> None:
        try:
            self.network_result = run_blocking_task(
                self, "N-iletken bonding ağı", "N-iletken bonding ağı gölge çözümü çalışıyor…",
                solve_multiconductor_bonding_network, self.project,
            )
        except MulticonductorBondingInputError as exc:
            QMessageBox.critical(self, "N-iletken bonding ağı girdi hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "N-iletken bonding ağı hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate_network()

    def run_global_solver(self) -> None:
        try:
            self.global_result = run_blocking_task(
                self, "Global N-iletken ağ", "Global core + bonding ağı gölge çözümü çalışıyor…",
                solve_global_multiconductor_network, self.project,
            )
        except MulticonductorGlobalInputError as exc:
            QMessageBox.critical(self, "Global N-iletken ağ girdi hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Global N-iletken ağ hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate_global()

    def _populate_local(self) -> None:
        result = self.result
        if result is None:
            return
        agree = "PASS" if result.methods_agree else "FAIL"
        self.summary.setText(
            f"YEREL {agree} — core={result.core_count}, sheath={result.sheath_count}, "
            f"KKT↔Schur ΔI={result.maximum_method_current_difference_a:.3e} A, "
            f"ΔV={result.maximum_method_voltage_difference_v_km:.3e} V/km; "
            f"λ1(shadow)={result.lambda1:.8f}; |Igcc|={abs(result.gcc_current_a):.4f} A; "
            f"maks. akım dengesizliği=%{result.maximum_current_imbalance_percent:.4f}"
        )
        self.summary.setStyleSheet(
            "padding:6px; font-weight:700; color:#146c2e;" if result.methods_agree
            else "padding:6px; font-weight:700; color:#a32626;"
        )

        self.cable_table.setRowCount(len(result.cable_results))
        self.emf_table.setRowCount(len(result.cable_results))
        for row, item in enumerate(result.cable_results):
            values = [
                item.physical_cable_id, item.circuit_id, item.phase, item.parallel_index,
                f"{item.x_m:.5f}", f"{item.depth_m:.5f}",
                f"{abs(item.core_current_a):.6f}", f"{_angle(item.core_current_a):.4f}",
                f"{item.current_share_percent:.5f}", f"{abs(item.equal_share_preview_a):.6f}",
                f"{item.current_difference_from_equal_share_a:.6f}",
                f"{abs(item.sheath_current_a):.6f}", f"{_angle(item.sheath_current_a):.4f}",
                f"{item.core_loss_w_km:.6f}", f"{item.sheath_loss_w_km:.6f}",
            ]
            for col, value in enumerate(values):
                self.cable_table.setItem(row, col, _cell(value))
            emf_values = [
                item.physical_cable_id, f"{abs(item.open_sheath_emf_v_km):.6f}",
                f"{_angle(item.open_sheath_emf_v_km):.4f}", f"{abs(item.sheath_current_a):.6f}",
                f"{item.sheath_loss_w_km:.6f}",
            ]
            for col, value in enumerate(emf_values):
                self.emf_table.setItem(row, col, _cell(value))

        self.group_table.setRowCount(len(result.group_results))
        for row, item in enumerate(result.group_results):
            values = [
                item.group_id, item.parallel_count, f"{abs(item.target_current_a):.6f}",
                f"{abs(item.solved_current_a):.6f}", f"{item.current_sum_residual_a:.3e}",
                f"{abs(item.voltage_drop_v_km):.6f}", f"{_angle(item.voltage_drop_v_km):.4f}",
                f"{item.maximum_current_a:.6f}", f"{item.minimum_current_a:.6f}",
                f"{item.imbalance_percent:.6f}",
            ]
            for col, value in enumerate(values):
                self.group_table.setItem(row, col, _cell(value))

        for table in (self.cable_table, self.group_table, self.emf_table):
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
        self.trace_edit.setPlainText(render_multiconductor_em(result))
        self.tabs.setCurrentWidget(self.cable_table)

    def _populate_network(self) -> None:
        result = self.network_result
        if result is None:
            return
        agree = "PASS" if result.methods_agree else "FAIL"
        self.summary.setText(
            f"BONDING AĞI {agree} — fiziksel kılıf={len(result.sheath_order)}, minor={len(result.section_results)}, "
            f"CIM↔NV ΔI={result.maximum_method_current_difference_a:.3e} A, "
            f"ΔV={result.maximum_method_voltage_difference_v:.3e} V; "
            f"λ1(shadow)={result.lambda1:.8f}; |Ish|max={result.maximum_sheath_current_a:.4f} A; "
            f"|Vsh-e|max={result.maximum_sheath_to_earth_voltage_v:.3f} V"
        )
        self.summary.setStyleSheet(
            "padding:6px; font-weight:700; color:#146c2e;" if result.methods_agree
            else "padding:6px; font-weight:700; color:#a32626;"
        )

        self.network_section_table.setRowCount(len(result.section_results))
        sheath_rows = sum(len(item.sheath_results) for item in result.section_results)
        self.network_sheath_table.setRowCount(sheath_rows)
        sheath_row = 0
        for row, section in enumerate(result.section_results):
            values = [
                section.section_id, section.major_index, f"{section.start_m:.3f}", f"{section.end_m:.3f}",
                ", ".join(section.route_cross_sections), len(section.sheath_results),
                f"{section.maximum_sheath_current_a:.6f}",
                f"{section.maximum_sheath_to_earth_voltage_v:.6f}",
                f"{section.maximum_sheath_to_sheath_voltage_v:.6f}",
                f"{section.core_metal_loss_w:.6f}", f"{section.sheath_metal_loss_w:.6f}",
                f"{section.gcc_metal_loss_w:.6f}",
            ]
            for col, value in enumerate(values):
                self.network_section_table.setItem(row, col, _cell(value))
            for item in section.sheath_results:
                sheath_values = [
                    item.section_id, item.physical_cable_id, item.circuit_id, item.phase, item.parallel_index,
                    f"{abs(item.sheath_current_a):.6f}", f"{_angle(item.sheath_current_a):.4f}",
                    f"{abs(item.start_voltage_to_earth_v):.6f}", f"{_angle(item.start_voltage_to_earth_v):.4f}",
                    f"{abs(item.end_voltage_to_earth_v):.6f}", f"{_angle(item.end_voltage_to_earth_v):.4f}",
                    f"{abs(item.integrated_open_emf_v):.6f}", f"{item.sheath_metal_loss_w:.6f}",
                ]
                for col, value in enumerate(sheath_values):
                    self.network_sheath_table.setItem(sheath_row, col, _cell(value))
                sheath_row += 1

        self.network_branch_table.setRowCount(len(result.accessory_branches))
        for row, item in enumerate(result.accessory_branches):
            values = [
                item.branch_id, item.branch_type, item.from_label, item.to_label,
                f"{abs(item.current_a):.6f}", f"{_angle(item.current_a):.4f}",
                f"{item.impedance_ohm.real:.8f}+j{item.impedance_ohm.imag:.8f}",
                f"{item.active_loss_w:.6f}",
            ]
            for col, value in enumerate(values):
                self.network_branch_table.setItem(row, col, _cell(value))

        for table in (self.network_section_table, self.network_sheath_table, self.network_branch_table):
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
        self.network_trace_edit.setPlainText(render_multiconductor_bonding_network(result))
        self.tabs.setCurrentWidget(self.network_section_table)

    def _populate_global(self) -> None:
        result = self.global_result
        if result is None:
            return
        agree = "PASS" if result.methods_agree else "FAIL"
        self.summary.setText(
            f"GLOBAL CORE+BONDING {agree} — core={len(result.core_order)}, kılıf={len(result.sheath_order)}, "
            f"Direct↔Reduced ΔIc={result.maximum_method_core_current_difference_a:.3e} A, "
            f"ΔIsh={result.maximum_method_sheath_current_difference_a:.3e} A, "
            f"ΔV={result.maximum_method_voltage_difference_v:.3e} V; "
            f"λ1(shadow)={result.lambda1:.8f}; core dengesizliği maks.=%{result.maximum_core_current_imbalance_percent:.4f}"
        )
        self.summary.setStyleSheet(
            "padding:6px; font-weight:700; color:#146c2e;" if result.methods_agree
            else "padding:6px; font-weight:700; color:#a32626;"
        )

        self.global_core_table.setRowCount(len(result.core_results))
        for row, item in enumerate(result.core_results):
            values = [
                item.physical_cable_id, item.circuit_id, item.phase, item.parallel_index,
                f"{abs(item.core_current_a):.6f}", f"{_angle(item.core_current_a):.4f}",
                f"{item.current_share_percent:.6f}", f"{abs(item.equal_share_current_a):.6f}",
                f"{item.current_difference_from_equal_share_a:.6f}",
                f"{abs(item.route_voltage_drop_v):.6f}", f"{_angle(item.route_voltage_drop_v):.4f}",
                f"{item.core_metal_loss_w:.6f}",
            ]
            for col, value in enumerate(values):
                self.global_core_table.setItem(row, col, _cell(value))

        self.global_group_table.setRowCount(len(result.group_results))
        for row, item in enumerate(result.group_results):
            values = [
                item.group_id, item.parallel_count, f"{abs(item.target_current_a):.6f}",
                f"{abs(item.solved_current_a):.6f}", f"{item.current_sum_residual_a:.3e}",
                f"{abs(item.route_voltage_drop_v):.6f}", f"{_angle(item.route_voltage_drop_v):.4f}",
                f"{item.maximum_current_a:.6f}", f"{item.minimum_current_a:.6f}",
                f"{item.imbalance_percent:.6f}",
            ]
            for col, value in enumerate(values):
                self.global_group_table.setItem(row, col, _cell(value))

        self.global_section_table.setRowCount(len(result.section_results))
        for row, section in enumerate(result.section_results):
            values = [
                section.section_id, section.major_index, f"{section.start_m:.3f}", f"{section.end_m:.3f}",
                ", ".join(section.route_cross_sections), len(section.sheath_results),
                f"{section.maximum_sheath_current_a:.6f}",
                f"{section.maximum_sheath_to_earth_voltage_v:.6f}",
                f"{section.maximum_sheath_to_sheath_voltage_v:.6f}",
                f"{section.core_metal_loss_w:.6f}", f"{section.sheath_metal_loss_w:.6f}",
                f"{section.gcc_metal_loss_w:.6f}",
            ]
            for col, value in enumerate(values):
                self.global_section_table.setItem(row, col, _cell(value))

        for table in (self.global_core_table, self.global_group_table, self.global_section_table):
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
        self.global_trace_edit.setPlainText(render_global_multiconductor_network(result))
        self.tabs.setCurrentWidget(self.global_core_table)
