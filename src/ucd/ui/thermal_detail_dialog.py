from __future__ import annotations

from ucd.calculations.result_status import display_background

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ucd.calculations import (
    MeshConvergenceResult,
    NodalRouteStudyResult,
    TransientRouteStudyResult,
    apply_thermal_design_alternative,
    evaluate_thermal_design_alternatives,
    find_nodal_region_result,
    resolve_thermal_region,
)
from ucd.models.project import ProjectData
from ucd.ui.graphics_views import SimpleDiagramView, TransientThermalView
from .background_task import run_blocking_task
from .window_layout import fit_window, DENSITY_WIDE


class ThermalAnalysisDialog(QDialog):
    """Full-size, single-record engineering review for one region/scenario."""

    def __init__(
        self,
        project: ProjectData,
        nodal_study: NodalRouteStudyResult,
        scenario_id: str,
        region_id: str,
        *,
        scope_id: str = "SCENARIO_COMBINED",
        transient_study: TransientRouteStudyResult | None = None,
        display_context: dict[str, object] | None = None,
        display_options: dict[str, bool] | None = None,
        mesh_convergence: MeshConvergenceResult | None = None,
        on_design_applied: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.nodal_study = nodal_study
        self.scenario_id = scenario_id
        self.scope_id = scope_id
        self.region_id = region_id
        self.transient_study = transient_study
        self.display_context = dict(display_context or {})
        self.display_options = dict(display_options or {})
        self.mesh_convergence = mesh_convergence
        self.on_design_applied = on_design_applied
        self.alternatives = ()

        self.region_result = find_nodal_region_result(
            nodal_study, scenario_id, region_id, scope_id
        )
        self.scenario_result = nodal_study.scope_result(scenario_id, scope_id)
        if self.region_result is None or self.scenario_result is None:
            raise ValueError("Seçili termal sonuç bulunamadı.")

        self.setWindowTitle(
            f"Termal Analiz Detayı — {self.region_result.region_id} · "
            f"{self.region_result.region_name} · {self.scenario_result.scenario_name} · "
            f"{self.scenario_result.solution_scope_name}"
        )
        fit_window(self, DENSITY_WIDE)
        self._build_ui()
        self._refresh_all()

    @staticmethod
    def _readonly_table(headers: list[str], columns: int | None = None) -> QTableWidget:
        table = QTableWidget(0, columns or len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _set_rows(table: QTableWidget, rows: list[tuple[object, ...]]) -> None:
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        self.header_label = QLabel()
        self.header_label.setStyleSheet("font-size: 14px; font-weight: 650; padding: 4px;")
        header.addWidget(self.header_label, 1)
        close_btn = QPushButton("Kapat")
        close_btn.clicked.connect(self.accept)
        header.addWidget(close_btn)
        outer.addLayout(header)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        # Summary
        summary_widget = QWidget()
        summary_layout = QVBoxLayout(summary_widget)
        self.summary_table = self._readonly_table(["Gösterge", "Değer / hüküm"], 2)
        summary_layout.addWidget(self.summary_table)
        self.tabs.addTab(summary_widget, "Özet")

        # Full field view
        field_widget = QWidget()
        field_layout = QVBoxLayout(field_widget)
        controls = QHBoxLayout()
        self.field_checks: dict[str, QCheckBox] = {}
        options = (
            ("show_material_boundaries", "Malzeme sınırları", True),
            ("show_geometry", "Hendek / su", True),
            ("show_cables", "Kablolar", True),
            ("show_mesh", "Mesh", False),
            ("show_isotherms", "İzotermler", False),
            ("show_hotspot", "Sıcak nokta", True),
            ("show_material_legend", "Malzeme listesi", True),
        )
        for key, label, default in options:
            check = QCheckBox(label)
            check.setChecked(bool(self.display_options.get(key, default)))
            check.toggled.connect(self._draw_field)
            self.field_checks[key] = check
            controls.addWidget(check)
        controls.addStretch(1)
        field_layout.addLayout(controls)
        self.field_view = SimpleDiagramView("thermal")
        field_layout.addWidget(self.field_view, 1)
        self.tabs.addTab(field_widget, "Kesit / Sıcaklık Alanı")

        # Inputs and sources
        inputs_widget = QWidget()
        inputs_layout = QVBoxLayout(inputs_widget)
        self.inputs_table = self._readonly_table(
            ["Grup", "Parametre", "Değer", "Kaynak / veri durumu"], 4
        )
        inputs_layout.addWidget(self.inputs_table)
        self.tabs.addTab(inputs_widget, "Girdiler ve Kaynaklar")

        # Verification
        verification_widget = QWidget()
        verification_layout = QVBoxLayout(verification_widget)
        self.verification_table = self._readonly_table(["Kontrol", "Sonuç", "Hüküm"], 3)
        verification_layout.addWidget(self.verification_table, 1)
        self.trace_view = QPlainTextEdit()
        self.trace_view.setReadOnly(True)
        self.trace_view.setMaximumHeight(220)
        verification_layout.addWidget(self.trace_view)
        self.tabs.addTab(verification_widget, "Enerji / Mesh Doğrulama")

        # IEC comparison and cable results
        iec_widget = QWidget()
        iec_layout = QVBoxLayout(iec_widget)
        self.iec_table = self._readonly_table(["Gösterge", "2D Nodal", "IEC 60287", "Fark"], 4)
        iec_layout.addWidget(self.iec_table, 0)
        self.cable_table = self._readonly_table(
            ["Kablo", "Devre", "Faz", "Akım [A]", "Tcond [°C]", "Tjacket [°C]", "Toplam kayıp [W/m]"], 7
        )
        iec_layout.addWidget(self.cable_table, 1)
        self.tabs.addTab(iec_widget, "IEC 60287 / 2D Karşılaştırma")

        # Transient/cyclic
        transient_widget = QWidget()
        transient_layout = QVBoxLayout(transient_widget)
        self.transient_status = QLabel()
        self.transient_status.setWordWrap(True)
        transient_layout.addWidget(self.transient_status)
        self.transient_view = TransientThermalView()
        transient_layout.addWidget(self.transient_view, 1)
        self.transient_table = self._readonly_table(["Gösterge", "Değer"], 2)
        self.transient_table.setMaximumHeight(210)
        transient_layout.addWidget(self.transient_table)
        self.tabs.addTab(transient_widget, "Transient / Cyclic")

        # Design alternatives
        alternatives_widget = QWidget()
        alternatives_layout = QVBoxLayout(alternatives_widget)
        note = QLabel(
            "Alternatifler seçili bölge üzerinde gerçek 2D nodal ampacity çözümü yeniden çalıştırılarak "
            "hesaplanır. Mekanik, inşaat, elektromanyetik ve maliyet uygunluğu ayrıca doğrulanmalıdır."
        )
        note.setWordWrap(True)
        alternatives_layout.addWidget(note)
        actions = QHBoxLayout()
        calculate_btn = QPushButton("Tasarım Alternatiflerini Hesapla")
        apply_btn = QPushButton("Seçili Alternatifi Tasarıma Uygula")
        calculate_btn.clicked.connect(self._calculate_alternatives)
        apply_btn.clicked.connect(self._apply_selected_alternative)
        actions.addWidget(calculate_btn)
        actions.addStretch(1)
        actions.addWidget(apply_btn)
        alternatives_layout.addLayout(actions)
        self.alternatives_table = self._readonly_table(
            ["Alternatif", "Değişiklik", "Iamp yeni [A]", "ΔI [A]", "ΔI [%]", "Tmax [°C]", "ΔT [°C]", "Marj [A]", "Durum"],
            9,
        )
        alternatives_layout.addWidget(self.alternatives_table, 1)
        self.alternative_detail = QPlainTextEdit()
        self.alternative_detail.setReadOnly(True)
        self.alternative_detail.setMaximumHeight(150)
        alternatives_layout.addWidget(self.alternative_detail)
        self.alternatives_table.itemSelectionChanged.connect(self._alternative_selected)
        self.tabs.addTab(alternatives_widget, "Tasarım Değişikliği Önerileri")

    def _refresh_all(self) -> None:
        region = self.region_result
        scenario = self.scenario_result
        self.header_label.setText(
            f"{region.region_id} · {region.region_name} | {region.start_m:.1f}–{region.end_m:.1f} m | "
            f"{scenario.scenario_name} | {region.installation_type}"
        )
        self._refresh_summary()
        self._draw_field()
        self._refresh_inputs()
        self._refresh_verification()
        self._refresh_iec()
        self._refresh_transient()

    def _refresh_summary(self) -> None:
        r = self.region_result
        hottest = max(r.cables, key=lambda item: item.conductor_temperature_c, default=None)
        rows = [
            ("Bölge / chainage", f"{r.region_id} · {r.region_name} · {r.start_m:.3f}–{r.end_m:.3f} m"),
            ("Kurulum / senaryo", f"{r.installation_type} · {self.scenario_result.scenario_name} · {self.scenario_result.solution_scope_name}"),
            ("Yük", f"{r.design_current_per_cable_a:.2f} A/kablo · {r.active_circuit_count} aktif devre"),
            ("2D ampacity", f"{r.ampacity_per_cable_a:.2f} A/kablo"),
            ("Akım marjı", f"{r.ampacity_per_cable_a-r.design_current_per_cable_a:+.2f} A"),
            ("Maksimum iletken sıcaklığı", f"{r.maximum_conductor_temperature_c:.2f} / {r.temperature_limit_c:.2f} °C"),
            ("En sıcak kablo", f"{hottest.cable_id} · {hottest.conductor_temperature_c:.2f} °C" if hottest else "—"),
            ("Maksimum jacket sıcaklığı", f"{r.maximum_jacket_temperature_c:.2f} °C"),
            ("IEC 60287 ampacity", f"{r.iec_ampacity_per_cable_a:.2f} A/kablo"),
            ("2D–IEC farkı", f"{r.difference_from_iec_percent:+.2f} %"),
            ("Genel hüküm", r.status),
            ("Uyarılar", " | ".join(r.warnings) if r.warnings else "Yok"),
        ]
        self._set_rows(self.summary_table, rows)
        for row in range(self.summary_table.rowCount()):
            if self.summary_table.item(row, 0).text() == "Genel hüküm":
                self.summary_table.item(row, 1).setBackground(
                    QBrush(QColor(display_background(r.status)))
                )

    def _draw_field(self, *_args) -> None:
        options = {key: check.isChecked() for key, check in self.field_checks.items()}
        self.field_view.draw_nodal_thermal(
            self.region_result,
            scenario_name=f"{self.scenario_result.scenario_name} · {self.scenario_result.solution_scope_name}",
            display_options=options,
            context=self.display_context,
        )

    def _refresh_inputs(self) -> None:
        region = next(item for item in self.project.thermal_design.regions if item.region_id == self.region_id)
        profile = resolve_thermal_region(self.project.thermal_design, region, self.project.cable)
        values = [
            ("Güzergâh", "Şablon", profile.template_id, f"{profile.data_state} · {profile.source_reference or 'Kaynak belirtilmedi'}"),
            ("Geometri", "Gömülme derinliği", f"{profile.burial_depth_m:.3f} m", profile.template_name),
            ("Geometri", "Faz aralığı", f"{profile.phase_spacing_m:.3f} m", profile.arrangement),
            ("Geometri", "Devre aralığı", f"{profile.circuit_spacing_m:.3f} m", profile.arrangement),
            ("Geometri", "Hendek genişliği / derinliği", f"{profile.trench_width_m:.3f} / {profile.trench_depth_m:.3f} m", profile.template_name),
            ("Malzeme", "Doğal zemin", f"{profile.native_soil.name} · ρ={profile.native_soil.thermal_resistivity_km_w:.3f} K·m/W", f"{profile.native_soil.data_state} · {profile.native_soil.source_reference or profile.native_soil.source_type}"),
            ("Malzeme", "Bedding", f"{profile.bedding.name} · ρ={profile.bedding.thermal_resistivity_km_w:.3f} K·m/W", f"{profile.bedding.data_state} · {profile.bedding.source_reference or profile.bedding.source_type}"),
            ("Malzeme", "Yan dolgu", f"{profile.side_backfill.name} · ρ={profile.side_backfill.thermal_resistivity_km_w:.3f} K·m/W", f"{profile.side_backfill.data_state} · {profile.side_backfill.source_reference or profile.side_backfill.source_type}"),
            ("Malzeme", "Kablo üstü dolgu", f"{profile.cable_cover.name} · ρ={profile.cable_cover.thermal_resistivity_km_w:.3f} K·m/W", f"{profile.cable_cover.data_state} · {profile.cable_cover.source_reference or profile.cable_cover.source_type}"),
            ("Sınır", "Yüzey / derin toprak", f"{self.display_context.get('surface_temperature_c', 0.0)} / {self.display_context.get('deep_soil_temperature_c', 0.0)} °C", str(self.display_context.get("surface_boundary_type", "—"))),
            ("Elektriksel", "Bölgesel λ1", f"{self.region_result.regional_lambda1:.8f}", "Primitive CIM/NV veya proje girdisi"),
        ]
        self._set_rows(self.inputs_table, values)

    def _refresh_verification(self) -> None:
        r = self.region_result
        convergence = self.mesh_convergence
        mesh_text = "Çalıştırılmadı"
        mesh_judgment = "BEKLİYOR"
        if convergence is not None:
            mesh_text = (
                f"{convergence.coarse_cells}→{convergence.refined_cells} hücre · "
                f"ΔT={convergence.difference_c:.4f} °C (%{convergence.difference_percent:.4f})"
            )
            mesh_judgment = "PASS" if convergence.passed else "FAIL"
        rows = [
            ("Sıcaklık iterasyonu", f"{r.solver_iterations} iterasyon", "PASS" if r.converged else "FAIL"),
            ("Lineer residual", f"{r.maximum_linear_residual:.3e}", "PASS" if r.maximum_linear_residual < 1e-7 else "İNCELE"),
            ("Enerji dengesi", f"Qsrc={r.total_heat_source_w_m:.6f}, Qout={r.total_boundary_heat_w_m:.6f} W/m · hata %{r.energy_balance_error_percent:.6f}", "PASS" if r.energy_balance_error_percent <= 0.5 else "İNCELE"),
            ("Mesh yakınsaması", mesh_text, mesh_judgment),
            ("Mesh kapsamı", f"{r.mesh_nx}×{r.mesh_ny} · {r.mesh_cell_count} hücre · {r.minimum_cell_size_m:.5f}–{r.maximum_cell_size_m:.5f} m", "BİLGİ"),
            ("2D model kapsamı", "Güzergâha dik orta kesit", "HDD/joint geçişlerinde 3D doğrulama gerekebilir"),
        ]
        self._set_rows(self.verification_table, rows)
        self.trace_view.setPlainText("\n".join(r.trace + r.warnings))

    def _refresh_iec(self) -> None:
        r = self.region_result
        rows = [
            ("Ampacity", f"{r.ampacity_per_cable_a:.2f} A", f"{r.iec_ampacity_per_cable_a:.2f} A", f"{r.difference_from_iec_percent:+.2f} %"),
            ("Tasarım akımında Tcond", f"{r.maximum_conductor_temperature_c:.2f} °C", "—", "—"),
            ("Bölgesel λ1", f"{r.regional_lambda1:.8f}", f"{r.regional_lambda1:.8f}", "Aynı kayıp girdisi"),
            ("Hüküm", r.status, "Referans karşılaştırması", "Yöntem otoritesi FAZ 4.2 doğrulama kaydına bağlıdır"),
        ]
        self._set_rows(self.iec_table, rows)
        cable_rows = [
            (
                cable.cable_id,
                cable.circuit_index,
                cable.phase,
                f"{cable.current_a:.2f}",
                f"{cable.conductor_temperature_c:.2f}",
                f"{cable.jacket_temperature_c:.2f}",
                f"{cable.total_loss_w_m:.5f}",
            )
            for cable in r.cables
        ]
        self._set_rows(self.cable_table, cable_rows)

    def _refresh_transient(self) -> None:
        transient = None
        if self.transient_study is not None:
            transient = next(
                (item for item in self.transient_study.regions if item.region_id == self.region_id),
                None,
            )
        if transient is None:
            self.transient_status.setText(
                "Bu bölge için IEC 60853 geçici/çevrimsel sonuç bulunmuyor. Ana pencereden geçici termal çalışmayı çalıştırın."
            )
            self.transient_view.scene_obj.clear()
            self.transient_view.scene_obj.addSimpleText("Geçici termal sonuç bulunmuyor.")
            self.transient_table.setRowCount(0)
            return
        self.transient_status.setText(
            f"Profil: {transient.profile_name} · Durum: {transient.status} · "
            f"Kritik tepe zamanı: {transient.time_of_maximum_h:.2f} h"
        )
        self.transient_view.draw_result(transient)
        rows = [
            ("Baz akım", f"{transient.base_current_per_cable_a:.2f} A/kablo"),
            ("Sürekli 2D ampacity", f"{transient.continuous_ampacity_per_cable_a:.2f} A/kablo"),
            ("Çevrimsel rating", f"{transient.cyclic_rating_per_cable_a:.2f} A/kablo"),
            ("Çevrimsel faktör", f"{transient.cyclic_rating_factor:.4f}"),
            ("Acil rating", f"{transient.emergency_rating_per_cable_a:.2f} A/kablo · {transient.emergency_duration_h:.2f} h"),
            ("Maksimum Tcond", f"{transient.maximum_conductor_temperature_c:.2f} °C"),
            ("Maksimum Tjacket", f"{transient.maximum_jacket_temperature_c:.2f} °C"),
            ("Ön koşullandırma", f"{transient.preconditioning_cycles} çevrim · uç ΔT={transient.cyclic_end_delta_c:.4f} °C"),
            ("Uyarılar", " | ".join(transient.warnings) if transient.warnings else "Yok"),
        ]
        self._set_rows(self.transient_table, rows)

    def _calculate_alternatives(self) -> None:
        self.alternative_detail.setPlainText("Alternatifler hesaplanıyor…")
        try:
            self.alternatives = run_blocking_task(
                self, "Termal tasarım alternatifleri", "Alternatif bölge tasarımları 2D olarak çözülüyor…",
                evaluate_thermal_design_alternatives,
                self.project,
                self.nodal_study,
                self.scenario_id,
                self.region_id,
                scope_id=self.scope_id,
                maximum_candidates=6,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Termal alternatif hesabı", str(exc))
            self.alternative_detail.clear()
            return
        self.alternatives_table.setRowCount(len(self.alternatives))
        for row, alternative in enumerate(self.alternatives):
            change_text = "; ".join(
                f"{item.label}: {item.old_value:.3f}→{item.new_value:.3f} {item.unit}"
                for item in alternative.changes
            )
            values = (
                alternative.title,
                change_text,
                f"{alternative.ampacity_a:.2f}",
                f"{alternative.ampacity_delta_a:+.2f}",
                f"{alternative.ampacity_delta_percent:+.2f}",
                f"{alternative.maximum_temperature_c:.2f}",
                f"{alternative.temperature_delta_c:+.2f}",
                f"{alternative.current_margin_a:+.2f}",
                alternative.status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, row)
                if alternative.status == "İYİLEŞME":
                    item.setBackground(QBrush(QColor("#e5f4ea")))
                elif alternative.status in {"HESAPLANAMADI", "YETERSİZ"}:
                    item.setBackground(QBrush(QColor("#fdecec")))
                self.alternatives_table.setItem(row, column, item)
        self.alternatives_table.resizeColumnsToContents()
        self.alternatives_table.horizontalHeader().setStretchLastSection(True)
        if self.alternatives:
            self.alternatives_table.selectRow(0)

    def _alternative_selected(self) -> None:
        row = self.alternatives_table.currentRow()
        if row < 0 or row >= len(self.alternatives):
            return
        alt = self.alternatives[row]
        changes = "\n".join(
            f"• {item.label}: {item.old_value:.4f} → {item.new_value:.4f} {item.unit}"
            for item in alt.changes
        )
        warnings = "\n".join(f"• {item}" for item in alt.warnings) or "• Yok"
        self.alternative_detail.setPlainText(
            f"{alt.title}\n{alt.rationale}\n\nDeğişiklikler:\n{changes}\n\n"
            f"Hesap sonucu: Iamp={alt.ampacity_a:.2f} A, ΔI={alt.ampacity_delta_a:+.2f} A, "
            f"Tmax={alt.maximum_temperature_c:.2f} °C\n\nUyarılar:\n{warnings}"
        )

    def _apply_selected_alternative(self) -> None:
        row = self.alternatives_table.currentRow()
        if row < 0 or row >= len(self.alternatives):
            QMessageBox.information(self, "Tasarım alternatifi", "Önce hesaplanmış bir alternatif seçin.")
            return
        alternative = self.alternatives[row]
        changes = "\n".join(
            f"• {item.label}: {item.old_value:.4f} → {item.new_value:.4f} {item.unit}"
            for item in alternative.changes
        )
        answer = QMessageBox.question(
            self,
            "Alternatifi tasarıma uygula",
            f"{alternative.title}\n\n{changes}\n\n"
            "Bu değişiklik seçili termal bölgenin override değerlerine yazılacak ve mevcut hesap sonuçları geçersiz olacaktır. Devam edilsin mi?",
        )
        if answer != QMessageBox.Yes:
            return
        apply_thermal_design_alternative(self.project, alternative)
        if self.on_design_applied is not None:
            self.on_design_applied()
        QMessageBox.information(
            self,
            "Tasarım alternatifi",
            "Değişiklik bölge tasarımına uygulandı. Ana pencerede 2D ve IEC 60853 hesaplarını yeniden çalıştırın.",
        )
        self.accept()
