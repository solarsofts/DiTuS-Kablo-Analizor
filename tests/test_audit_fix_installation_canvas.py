from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from ucd.models.project import ExternalHeatSourceData, ProjectData
from ucd.ui.installation_designer_dialog import InstallationCanvas


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_canvas_draws_section_with_active_external_heat_source(qapp) -> None:
    project = ProjectData()
    section = project.installation_design.cross_sections[0]
    section.external_heat_sources.append(
        ExternalHeatSourceData("HS-01", "Komşu boru", 0.75, 1.8, 45.0, 0.40)
    )
    canvas = InstallationCanvas()
    try:
        canvas.draw_section(section, project.cable.overall_diameter_mm / 1000.0, auto_fit=True)
        rect = canvas._content_rect_for_section(section)
    finally:
        canvas.deleteLater()
    # The 0.40 m effective radius of the active source must enlarge the
    # content envelope to at least 1.8 m + 0.40 m depth and 0.75 m + 0.40 m x.
    _, source_bottom = canvas._scene_xy(0.75 + 0.40, 1.8 + 0.40)
    source_right, _ = canvas._scene_xy(0.75 + 0.40, 0.0)
    assert rect.bottom() >= source_bottom - 1e-6
    assert rect.right() >= source_right - 1e-6
