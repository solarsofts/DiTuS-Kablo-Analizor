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
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ucd.calculations.electrothermal_coupled import (
    ElectroThermalAmpacityResult,
    ElectroThermalCoupledResult,
    ElectroThermalInputError,
    render_electrothermal_ampacity,
    render_electrothermal_coupled,
    solve_electrothermal_ampacity,
    solve_electrothermal_coupled,
)
from ucd.models.project import ProjectData
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


def _cell(value: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(value))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _angle(value: complex) -> float:
    return 0.0 if abs(value) < 1e-15 else cmath.phase(value) * 180.0 / pi


class ElectroThermalCoupledDialog(QDialog):
    """Read-only v0.16.7 closed-loop electro-thermal shadow window."""

    def __init__(self, project: ProjectData, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.result: ElectroThermalCoupledResult | None = None
        self.ampacity_result: ElectroThermalAmpacityResult | None = None
        self.setWindowTitle("DiTuS — Elektro-Termal Kapalı Çevrim Gölge Çözümü")
        fit_window(self, DENSITY_WIDE)

        layout = QVBoxLayout(self)
        notice = QLabel(
            "v0.16.7 SHADOW_COMPARE — fiziksel core/kılıf sıcaklığı; global N-core/N-kılıf direnci, "
            "akım paylaşımı, link-box/GCC ağı, kablo bazlı kayıplar ve gerçek x-y 2D sıcaklık alanı "
            "yakınsayana kadar birlikte çözülür. Mevcut üretim sonuçları ve proje λ1 değeri değiştirilmez."
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
        self.mesh_scale.setValue(2.0)
        controls.addWidget(self.mesh_scale, 0, 1)

        controls.addWidget(QLabel("Maks. dış iterasyon"), 0, 2)
        self.max_iterations = QSpinBox()
        self.max_iterations.setRange(2, 60)
        self.max_iterations.setValue(20)
        controls.addWidget(self.max_iterations, 0, 3)

        controls.addWidget(QLabel("ΔT toleransı [°C]"), 0, 4)
        self.temperature_tolerance = QDoubleSpinBox()
        self.temperature_tolerance.setRange(0.005, 2.0)
        self.temperature_tolerance.setDecimals(3)
        self.temperature_tolerance.setValue(0.05)
        controls.addWidget(self.temperature_tolerance, 0, 5)

        controls.addWidget(QLabel("ΔI toleransı [%]"), 1, 0)
        self.current_tolerance = QDoubleSpinBox()
        self.current_tolerance.setRange(0.001, 5.0)
        self.current_tolerance.setDecimals(3)
        self.current_tolerance.setValue(0.10)
        controls.addWidget(self.current_tolerance, 1, 1)

        controls.addWidget(QLabel("ΔP toleransı [%]"), 1, 2)
        self.loss_tolerance = QDoubleSpinBox()
        self.loss_tolerance.setRange(0.001, 5.0)
        self.loss_tolerance.setDecimals(3)
        self.loss_tolerance.setValue(0.10)
        controls.addWidget(self.loss_tolerance, 1, 3)

        controls.addWidget(QLabel("Relaxation"), 1, 4)
        self.relaxation = QDoubleSpinBox()
        self.relaxation.setRange(0.05, 1.0)
        self.relaxation.setDecimals(2)
        self.relaxation.setSingleStep(0.05)
        self.relaxation.setValue(0.60)
        controls.addWidget(self.relaxation, 1, 5)

        run_button = QPushButton("Elektro-Termal Kapalı Çevrimi Çalıştır")
        run_button.clicked.connect(self.run_solver)
        controls.addWidget(run_button, 0, 6)
        rating_button = QPushButton("Kapalı Çevrim Ampacity Gölge Çözümü")
        rating_button.clicked.connect(self.run_ampacity)
        controls.addWidget(rating_button, 1, 6)
        controls.setColumnStretch(6, 1)
        layout.addLayout(controls)

        self.summary = QLabel("Henüz çözüm çalıştırılmadı.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("padding:6px; font-weight:700;")
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.iteration_table = QTableWidget(0, 14)
        self.iteration_table.setHorizontalHeaderLabels([
            "İterasyon", "Relax", "ΔT [°C]", "ΔIc [%]", "ΔIsh [%]", "ΔP [%]",
            "Tcond maks. [°C]", "Tsheath maks. [°C]", "Ic maks. [A]", "Ish maks. [A]",
            "Pcore [W]", "Psheath [W]", "λ1", "Kapılar",
        ])
        self.iteration_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.iteration_table, "Yakınsama İzi")

        self.cable_table = QTableWidget(0, 16)
        self.cable_table.setHorizontalHeaderLabels([
            "Bölge", "Fiziksel kablo", "Devre", "Faz", "Paralel", "|Ic| [A]", "∠Ic [°]",
            "Wc [W/m]", "Wsh [W/m]", "Wd [W/m]", "Warm [W/m]",
            "Tj [°C]", "Tc [°C]", "x [m]", "Derinlik [m]", "Kesit",
        ])
        self.cable_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.cable_table, "Final Kablo Sonuçları")

        self.ampacity_table = QTableWidget(0, 5)
        self.ampacity_table.setHorizontalHeaderLabels([
            "Faktör", "Tcond maks. [°C]", "İç yakınsama", "İç iterasyon", "Durum"
        ])
        self.ampacity_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.ampacity_table, "Ampacity Dış Döngüsü")

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
                self, "Elektro-termal kapalı çevrim",
                "Elektro-termal kapalı çevrim gölge çözümü çalışıyor…",
                solve_electrothermal_coupled,
                self.project,
                mesh_scale=float(self.mesh_scale.value()),
                maximum_iterations=int(self.max_iterations.value()),
                temperature_tolerance_c=float(self.temperature_tolerance.value()),
                current_tolerance_percent=float(self.current_tolerance.value()),
                loss_tolerance_percent=float(self.loss_tolerance.value()),
                relaxation_factor=float(self.relaxation.value()),
            )
        except ElectroThermalInputError as exc:
            QMessageBox.critical(self, "Elektro-termal girdi hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Elektro-termal hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate()

    def run_ampacity(self) -> None:
        try:
            self.ampacity_result = run_blocking_task(
                self, "Elektro-termal ampacity",
                "Kapalı çevrim ampacity gölge çözümü çalışıyor…",
                solve_electrothermal_ampacity,
                self.project,
                mesh_scale=float(self.mesh_scale.value()),
                maximum_closed_loop_iterations=int(self.max_iterations.value()),
                temperature_tolerance_c=max(0.05, float(self.temperature_tolerance.value())),
                current_tolerance_percent=float(self.current_tolerance.value()),
                loss_tolerance_percent=float(self.loss_tolerance.value()),
                relaxation_factor=float(self.relaxation.value()),
            )
        except ElectroThermalInputError as exc:
            QMessageBox.critical(self, "Elektro-termal ampacity girdi hatası", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Elektro-termal ampacity hesap hatası", f"Beklenmeyen hata:\n{exc}")
            return
        self._populate_ampacity()

    def _populate(self) -> None:
        result = self.result
        if result is None:
            return
        state = "YAKINSADI" if result.converged else "YAKINSAMADI"
        self.summary.setText(
            f"SHADOW — {state}; iterasyon={result.iteration_count}/{result.maximum_iterations}; "
            f"Tcond,max={result.final_thermal.maximum_nodal_conductor_temperature_c:.3f} °C; "
            f"network sheath-loss ratio={result.final_global_em.lambda1:.8f}; "
            f"kritik bölge={result.final_thermal.critical_nodal_region_id}"
        )
        self.summary.setStyleSheet(
            "padding:6px; font-weight:700; color:#1c6b38;" if result.converged
            else "padding:6px; font-weight:700; color:#9b4b00;"
        )

        self.iteration_table.setRowCount(len(result.iterations))
        for row, item in enumerate(result.iterations):
            gates = (
                ("EM✓" if item.em_methods_agree else "EM✗")
                + " / "
                + ("TERM✓" if item.thermal_regions_converged else "TERM✗")
            )
            values = [
                item.iteration, f"{item.relaxation_factor:.3f}",
                f"{item.maximum_temperature_residual_c:.6e}",
                f"{item.maximum_core_current_change_percent:.6e}",
                f"{item.maximum_sheath_current_change_percent:.6e}",
                f"{item.active_loss_change_percent:.6e}",
                f"{item.maximum_conductor_temperature_c:.5f}",
                f"{item.maximum_sheath_temperature_c:.5f}",
                f"{item.maximum_core_current_a:.6f}",
                f"{item.maximum_sheath_current_a:.6f}",
                f"{item.total_core_loss_w:.6f}", f"{item.total_sheath_loss_w:.6f}",
                f"{item.lambda1:.9f}", gates,
            ]
            for col, value in enumerate(values):
                self.iteration_table.setItem(row, col, _cell(value))

        cable_count = sum(len(region.cables) for region in result.final_thermal.regions)
        self.cable_table.setRowCount(cable_count)
        row = 0
        for region in result.final_thermal.regions:
            for cable in region.cables:
                values = [
                    region.region_id, cable.physical_cable_id, cable.circuit_id, cable.phase,
                    cable.parallel_index, f"{abs(cable.current_a):.6f}", f"{_angle(cable.current_a):.4f}",
                    f"{cable.conductor_loss_w_m:.6f}", f"{cable.sheath_loss_w_m:.6f}",
                    f"{cable.dielectric_loss_w_m:.6f}", f"{cable.armour_loss_w_m:.6f}",
                    f"{cable.nodal_jacket_temperature_c:.4f}",
                    f"{cable.nodal_conductor_temperature_c:.4f}",
                    f"{cable.x_m:.5f}", f"{cable.depth_m:.5f}", region.cross_section_id,
                ]
                for col, value in enumerate(values):
                    self.cable_table.setItem(row, col, _cell(value))
                row += 1

        for table in (self.iteration_table, self.cable_table):
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
        self.trace_edit.setPlainText(render_electrothermal_coupled(result))
        self.tabs.setCurrentWidget(self.iteration_table)
    def _populate_ampacity(self) -> None:
        result = self.ampacity_result
        if result is None:
            return
        self.ampacity_table.setRowCount(len(result.evaluations))
        for row, item in enumerate(result.evaluations):
            values = [
                f"{item.factor:.8f}",
                f"{item.maximum_conductor_temperature_c:.5f}",
                "EVET" if item.closed_loop_converged else "HAYIR",
                item.closed_loop_iterations,
                "ALT" if item.maximum_conductor_temperature_c <= result.temperature_limit_c else "ÜST",
            ]
            for col, value in enumerate(values):
                self.ampacity_table.setItem(row, col, _cell(value))
        self.ampacity_table.resizeColumnsToContents()
        self.ampacity_table.horizontalHeader().setStretchLastSection(True)
        circuit_text = "; ".join(
            f"{key}={value:.3f} A" for key, value in sorted(result.circuit_rating_currents_a.items())
        )
        state = "YAKINSADI" if result.converged else "YAKINSAMADI"
        self.summary.setText(
            f"AMPACITY SHADOW — {state}; faktör={result.rating_factor:.7f}; {circuit_text}; "
            f"Tlimit={result.temperature_limit_c:.3f} °C; "
            f"kritik={result.critical_region_id}/{result.critical_cable_id}"
        )
        self.trace_edit.setPlainText(render_electrothermal_ampacity(result))
        self.tabs.setCurrentWidget(self.ampacity_table)

