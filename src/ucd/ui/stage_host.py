"""Aşama konağı çerçevesi.

Önceki davranışta "tasarım akışını başlat" onayından sonra aşamalar ard arda
bağımsız üst düzey pencereler olarak açılıyordu.  Pencere sayısı değil, asıl
sorun şuydu: hiçbir pencere akışta *nerede* olunduğunu taşımıyordu.  Kullanıcı
bir pencereyi kapattığında geri mi döndüğünü, ilerlediğini mi, sıradaki adımın
ne olduğunu göremiyordu.

Bu modül aşama içeriğinin etrafına sabit bir çerçeve koyar:

* üstte hangi adımda olunduğu, durumu ve programın önerdiği adım,
* ortada mevcut çalışma alanı widget'ı (yeniden yazılmaz, olduğu gibi kullanılır),
* altta eksik girdiler ve gezinme (önceki / önerilen / sonraki / akıştan çık).

Böylece iterasyon yön gösterici olur.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .workflow_user_state import user_stage_state

__all__ = ["StageHostFrame"]

_STATUS_TINT = {
    "READY": ("#e8f7ed", "#1c6b3a"),
    "COMPLETE": ("#e8f7ed", "#1c6b3a"),
    "IN_PROGRESS": ("#fff8df", "#7a5a12"),
    "CONDITIONAL": ("#fff8df", "#7a5a12"),
    "BLOCKED": ("#fdecec", "#8c2f2f"),
    "NOT_STARTED": ("#eef2f6", "#41576b"),
}


def _tint(color_status: str) -> tuple[str, str]:
    return _STATUS_TINT.get(str(color_status).upper(), _STATUS_TINT["NOT_STARTED"])


class StageHostFrame(QWidget):
    """Aşama içeriğini sabit bir akış çerçevesi içinde tutar."""

    previousRequested = Signal()
    nextRequested = Signal()
    recommendedRequested = Signal()
    exitFlowRequested = Signal()
    missingItemActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stage_ids: tuple[str, ...] = ()
        self._current_stage_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- Üst şerit: neredeyim -------------------------------------------
        self.header = QFrame()
        self.header.setFrameShape(QFrame.StyledPanel)
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(10, 7, 10, 7)
        header_layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.step_badge = QLabel("—")
        self.step_badge.setAlignment(Qt.AlignCenter)
        self.step_badge.setFixedWidth(58)
        self.step_badge.setStyleSheet(
            "font-size:11pt; font-weight:800; color:#ffffff; background:#2f6690;"
            " border-radius:4px; padding:3px 6px;"
        )
        self.stage_title = QLabel("Proje Modülü")
        self.stage_title.setStyleSheet("font-size:13pt; font-weight:800; color:#173d5d;")
        self.stage_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.status_chip = QLabel("")
        self.status_chip.setAlignment(Qt.AlignCenter)
        self.status_chip.setStyleSheet("font-weight:700; padding:3px 10px; border-radius:9px;")
        title_row.addWidget(self.step_badge)
        title_row.addWidget(self.stage_title, 1)
        title_row.addWidget(self.status_chip)
        header_layout.addLayout(title_row)

        self.next_action = QLabel("")
        self.next_action.setWordWrap(True)
        self.next_action.setStyleSheet("color:#41576b;")
        header_layout.addWidget(self.next_action)
        layout.addWidget(self.header)

        # --- Gövde: mevcut çalışma alanı ------------------------------------
        # Modül içerikleri kendi doğal minimumlarını büyütebilir. Ana pencere
        # yüksekliği bundan etkilenmez: gövde gerektiğinde kayar, böylece üst
        # ve alt akış şeritleri hiçbir monitörde erişilemez hale gelmez.
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.body_host = QWidget()
        self.body_host.setMinimumSize(0, 0)
        self.body_layout = QVBoxLayout(self.body_host)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_scroll.setWidget(self.body_host)
        layout.addWidget(self.body_scroll, 1)

        # --- Alt şerit: eksikler ve gezinme ---------------------------------
        self.footer = QFrame()
        self.footer.setFrameShape(QFrame.StyledPanel)
        footer_layout = QVBoxLayout(self.footer)
        footer_layout.setContentsMargins(10, 6, 10, 6)
        footer_layout.setSpacing(5)

        self.missing_label = QLabel("")
        self.missing_label.setWordWrap(True)
        self.missing_label.setTextFormat(Qt.RichText)
        self.missing_label.setStyleSheet("color:#8c2f2f;")
        self.missing_label.hide()
        footer_layout.addWidget(self.missing_label)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.prev_button = QPushButton("◀  Önceki aşama")
        self.recommended_button = QPushButton("★  Önerilen aşama")
        self.next_button = QPushButton("Sonraki aşama  ▶")
        self.exit_button = QPushButton("Akıştan çık")
        self.recommended_button.setStyleSheet(
            "font-weight:700; background:#2f6690; color:#ffffff; padding:5px 12px; border-radius:4px;"
        )
        self.prev_button.clicked.connect(self.previousRequested)
        self.next_button.clicked.connect(self.nextRequested)
        self.recommended_button.clicked.connect(self.recommendedRequested)
        self.exit_button.clicked.connect(self.exitFlowRequested)
        nav_row.addWidget(self.prev_button)
        nav_row.addWidget(self.recommended_button)
        nav_row.addWidget(self.next_button)
        nav_row.addStretch(1)
        self.position_label = QLabel("")
        self.position_label.setStyleSheet("color:#41576b;")
        nav_row.addWidget(self.position_label)
        nav_row.addWidget(self.exit_button)
        footer_layout.addLayout(nav_row)
        layout.addWidget(self.footer)
        self._standalone_mode = False

    # ------------------------------------------------------------------
    def set_standalone_mode(self, enabled: bool) -> None:
        """Hide workflow chrome when a project-tree object editor is opened directly.

        The workspace widget remains the same; only the wizard-like stage header/footer
        are removed so object editors keep their own commands and usable viewport.
        """
        self._standalone_mode = bool(enabled)
        self.header.setVisible(not self._standalone_mode)
        self.footer.setVisible(not self._standalone_mode)

    # ------------------------------------------------------------------
    def set_body(self, widget: QWidget) -> None:
        """Gövdeye yeni çalışma alanı widget'ını yerleştir."""

        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
        widget.setMinimumSize(0, 0)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.body_layout.addWidget(widget)
        widget.show()

    # ------------------------------------------------------------------
    def set_stage(self, stage: Any, evaluation: Any, fallback_title: str = "") -> None:
        """Çerçeveyi verilen aşamaya göre güncelle."""

        if stage is None:
            self.step_badge.setText("—")
            self.stage_title.setText(fallback_title or "Proje Modülü")
            self.status_chip.setText("")
            self.status_chip.setVisible(False)
            self.next_action.setText("Bu görünüm tasarım akışının bir aşaması değildir.")
            self.missing_label.hide()
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.position_label.setText("")
            self._current_stage_id = ""
            return

        display = user_stage_state(stage)
        background, foreground = _tint(display.color_status)
        stages = tuple(getattr(evaluation, "stages", ()) or ())
        self._stage_ids = tuple(str(item.stage_id) for item in stages)
        self._current_stage_id = str(stage.stage_id)

        self.status_chip.setVisible(True)
        self.step_badge.setText(f"{stage.number}")
        self.stage_title.setText(str(stage.title))
        self.status_chip.setText(display.label)
        self.status_chip.setStyleSheet(
            f"font-weight:700; padding:3px 10px; border-radius:9px;"
            f" background:{background}; color:{foreground};"
        )
        self.header.setStyleSheet(
            f"QFrame {{ background:{background}; border:1px solid #ccd7e1; border-radius:5px; }}"
        )
        self.next_action.setText(f"Sonraki işlem: {display.action}")

        blocking = tuple(getattr(stage, "blocking_reasons", ()) or ())
        missing = tuple(getattr(stage, "missing_inputs", ()) or ())
        parts: list[str] = []
        if blocking:
            parts.append(
                "<b>Bloke nedenleri:</b> "
                + "; ".join(str(value) for value in blocking[:4])
            )
        if missing:
            parts.append(
                "<b>Tamamlanması gerekenler:</b> "
                + "; ".join(str(value) for value in missing[:4])
            )
        if parts:
            self.missing_label.setText("<br>".join(parts))
            self.missing_label.show()
        else:
            self.missing_label.hide()

        index = self._stage_ids.index(self._current_stage_id) if self._current_stage_id in self._stage_ids else -1
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(0 <= index < len(self._stage_ids) - 1)
        if index >= 0:
            self.position_label.setText(f"Adım {index + 1} / {len(self._stage_ids)}")
        else:
            self.position_label.setText("")

        recommended = str(getattr(evaluation, "recommended_stage_id", "") or "")
        on_recommended = recommended == self._current_stage_id
        self.recommended_button.setEnabled(bool(recommended) and not on_recommended)
        self.recommended_button.setText(
            "★  Bu, önerilen aşama" if on_recommended else "★  Önerilen aşamaya git"
        )

    # ------------------------------------------------------------------
    def neighbour_stage_id(self, offset: int) -> str:
        if self._current_stage_id not in self._stage_ids:
            return ""
        index = self._stage_ids.index(self._current_stage_id) + offset
        if 0 <= index < len(self._stage_ids):
            return self._stage_ids[index]
        return ""
