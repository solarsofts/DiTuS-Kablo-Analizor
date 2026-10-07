from __future__ import annotations

import cmath
from math import pi

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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

from ucd.calculations.multiconductor_thermal import (
    MulticonductorThermalInputError,
    MulticonductorThermalResult,
    render_multiconductor_thermal,
    solve_multiconductor_thermal,
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


class MulticonductorThermalDialog(QDialog):
    """Read-only v0.16.6 real-x/y thermal shadow comparison window."""

    def __init__(self, project: ProjectData, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.result: MulticonductorThermalResult | None = None
        self.setWindowTitle("DiTuS — Gerçek x-y Çoklu Kablo Termal Gölge Çözümü")
        fit_window(self, DENSITY_WIDE)

        layout = QVBoxLayout(self)
        notice = QLabel(
            "v0.16.6 SHADOW_COMPARE — global N-core/N-kılıf kayıpları her fiziksel kablonun gerçek x-y "
            "konumundan analitik karşılıklı ısıl direnç matrisi ve ortak 2D alana aktarılır. "
            "Mevcut IEC/nodal üretim sonuçları ve proje λ1 değeri değiştirilmez."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "font-weight:700; color:#174a6e; background:#eef6fb; border:1px solid #a8c7dc; "
            "padding:8px; border-radius:5px;"
        )
        layout.addWidget(notice)

        controls = QGridLayout()
        controls.addWidget(QLabel("2D ağ ölçeği"), 0, 0)
        self.mesh_scale = QDoubleSpinBox()
        self.mesh_scale.setRange(0.75, 4.0)
        self.mesh_scale.setDecimals(2)
        self.mesh_scale.setSingleStep(0.25)
        self.mesh_scale.setValue(1.5)
        self.mesh_scale.setToolTip("1.0 daha ince; büyük değer daha hızlı gölge çözüm üretir.")
        controls.addWidget(self.mesh_scale, 0, 1)
        run_button = QPushButton("Gerçek x-y Termal Gölge Çözümünü Çalıştır")
        run_button.clicked.connect(self.run_solver)
        controls.addWidget(run_button, 0, 2)
        note = QLabel(
            "Analitik yöntem homojen/eşdeğer dolgu matrisi; 2D yöntem katmanlı hendek, duct/grout ve harici ısı kaynaklarını çözer."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-style:italic; color:#5a6670;")
        controls.addWidget(note, 1, 0, 1, 3)
        controls.setColumnStretch(2, 1)
        layout.addLayout(controls)

        self.summary = QLabel("Henüz çözüm çalıştırılmadı.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("padding:6px; font-weight:700;")
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.region_table = QTableWidget(0, 15)
        self.region_table.setHorizontalHeaderLabels([
            "Bölge", "Fiziksel kesit", "Kurulum", "Kablo", "Ağ", "İterasyon", "Yakınsama",
            "Tcond analitik maks. [°C]", "Tcond 2D maks. [°C]", "Maks. fark [°C]",
            "Kritik kablo A", "Kritik kablo 2D", "Q kaynak [W/m]", "Enerji hata [%]", "Residual",
        ])
        self.region_table.setAlternatingRowColors(True)
        self.region_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.region_table, "Bölge Karşılaştırması")

        self.cable_table = QTableWidget(0, 20)
        self.cable_table.setHorizontalHeaderLabels([
            "Bölge", "Fiziksel kablo", "Devre", "Faz", "Paralel", "x [m]", "Derinlik [m]",
            "|Ic| [A]", "∠Ic [°]", "Wc [W/m]", "Wsh [W/m]", "Wd [W/m]", "Warm [W/m]", "Wtop [W/m]",
            "Tj A [°C]", "Tc A [°C]", "Tj 2D [°C]", "Tc 2D [°C]", "T4 A", "T4 2D",
        ])
        self.cable_table.setAlternatingRowColors(True)
        self.cable_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.cable_table, "Fiziksel Kablo Kayıp/Sıcaklık")

        self.trace_edit = QPlainTextEdit()
        self.trace_edit.setReadOnly(True)
        self.tabs.addTab(self.trace_edit, "İz, Sınırlar ve Uyarılar")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def run_solver(self) -> None:
        try:
            self.result = run_blocking_task(
                self, "Çoklu kablo termal", "Gerçek x-y termal gölge çözümü çalışıyor…",
                solve_multiconductor_thermal,
                self.project,
                mesh_scale=float(self.mesh_scale.value()),
            )
        except MulticonductorThermalInputError as exc:
            QMessageBox.critical(self, "Çoklu kablo termal girdi hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Çoklu kablo termal hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate()

    def _populate(self) -> None:
        result = self.result
        if result is None:
            return
        self.summary.setText(
            f"SHADOW — bölge={len(result.regions)}; "
            f"Tcond,max analitik/2D={result.maximum_analytical_conductor_temperature_c:.3f}/"
            f"{result.maximum_nodal_conductor_temperature_c:.3f} °C; "
            f"maks. yöntem farkı={result.maximum_method_temperature_difference_c:.3f} °C; "
            f"kritik bölge A/2D={result.critical_analytical_region_id}/{result.critical_nodal_region_id}"
        )
        self.summary.setStyleSheet("padding:6px; font-weight:700; color:#174a6e;")

        self.region_table.setRowCount(len(result.regions))
        cable_count = sum(len(region.cables) for region in result.regions)
        self.cable_table.setRowCount(cable_count)
        cable_row = 0
        for row, region in enumerate(result.regions):
            values = [
                region.region_id, region.cross_section_id, region.installation_type, len(region.cables),
                f"{region.nodal_mesh_nx}×{region.nodal_mesh_ny}", region.nodal_iterations,
                "EVET" if region.nodal_converged else "HAYIR",
                f"{region.maximum_analytical_conductor_temperature_c:.4f}",
                f"{region.maximum_nodal_conductor_temperature_c:.4f}",
                f"{region.maximum_method_temperature_difference_c:.4f}",
                region.critical_analytical_cable_id, region.critical_nodal_cable_id,
                f"{region.nodal_total_heat_source_w_m:.6f}",
                f"{region.nodal_energy_balance_error_percent:.6f}",
                f"{region.nodal_maximum_linear_residual:.3e}",
            ]
            for col, value in enumerate(values):
                self.region_table.setItem(row, col, _cell(value))
            for item in region.cables:
                cable_values = [
                    region.region_id, item.physical_cable_id, item.circuit_id, item.phase, item.parallel_index,
                    f"{item.x_m:.5f}", f"{item.depth_m:.5f}", f"{abs(item.current_a):.6f}",
                    f"{_angle(item.current_a):.4f}", f"{item.conductor_loss_w_m:.6f}",
                    f"{item.sheath_loss_w_m:.6f}", f"{item.dielectric_loss_w_m:.6f}",
                    f"{item.armour_loss_w_m:.6f}", f"{item.total_loss_w_m:.6f}",
                    f"{item.analytical_jacket_temperature_c:.4f}",
                    f"{item.analytical_conductor_temperature_c:.4f}",
                    f"{item.nodal_jacket_temperature_c:.4f}",
                    f"{item.nodal_conductor_temperature_c:.4f}",
                    f"{item.analytical_equivalent_t4_km_w:.6f}",
                    f"{item.nodal_equivalent_t4_km_w:.6f}",
                ]
                for col, value in enumerate(cable_values):
                    self.cable_table.setItem(cable_row, col, _cell(value))
                cable_row += 1

        for table in (self.region_table, self.cable_table):
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
        self.trace_edit.setPlainText(render_multiconductor_thermal(result))
        self.tabs.setCurrentWidget(self.region_table)
