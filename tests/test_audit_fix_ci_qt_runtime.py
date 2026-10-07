"""CI'da Qt çalışma zamanı eksikse UI testleri sessizce atlanmamalı.

``DITUS_REQUIRE_QT=1`` ortamında (CI) ``PySide6.QtWidgets`` yüklenemiyorsa bu
test açık bir mesajla başarısız olur; yerel çalıştırmalarda atlanır.
"""

from __future__ import annotations

import importlib
import os

import pytest


def test_qt_widgets_runtime_is_available_when_required() -> None:
    if os.environ.get("DITUS_REQUIRE_QT") != "1":
        pytest.skip("DITUS_REQUIRE_QT=1 değil; Qt çalışma zamanı zorunluluğu yalnız CI'da denetlenir.")
    try:
        importlib.import_module("PySide6.QtWidgets")
    except ImportError as exc:
        pytest.fail(
            "DITUS_REQUIRE_QT=1 iken PySide6.QtWidgets yüklenemedi; UI testleri atlanırdı. "
            "Qt offscreen sistem kütüphanelerini (ör. libegl1, libgl1, libxkbcommon0, "
            f"libfontconfig1, libdbus-1-3) kurun. Hata: {type(exc).__name__}: {exc}"
        )
