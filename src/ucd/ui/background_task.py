"""Ağır hesap motorlarını GUI iş parçacığının dışında çalıştırır (denetim bulgusu O-5).

:func:`run_blocking_task` çağıran açısından eşzamanlıdır: motorun dönüş değerini
verir ya da motorun fırlattığı istisnayı özgün tipi, mesajı ve iziyle GUI iş
parçacığında yeniden fırlatır. Böylece çağıranlardaki mevcut ``try/except``
blokları değişmeden çalışır. Yalnız motor çağrısı bir ``QThread`` üzerinde koşar;
GUI iş parçacığı bu sürede yerel bir ``QEventLoop`` döndürür, pencere boyanmaya
devam eder ve Windows uygulamayı "Yanıt vermiyor" olarak işaretlemez.

Varsayım ve sözleşme:

* ``fn`` proje verisini (ör. ``MainWindow.project``) okuyabilir ve
  değiştirebilir. Bunun güvenli olması, görev süresince kullanıcının projeye
  dokunamamasına dayanır: kısa ``MINIMUM_DURATION_MS`` gecikmesinde kullanıcı
  girdisi hiç işlenmez (``ExcludeUserInputEvents``), ardından açılan
  uygulama-modal meşgul penceresi diğer bütün pencerelere girdiyi ve kapatma
  isteğini engeller. Meşgul penceresinin iptal düğmesi yoktur; Esc ve kapatma
  isteği yok sayılır, çünkü motorlar iptal edilemez.
* ``fn`` hiçbir QWidget/QObject'e dokunmamalıdır. Tablo, grafik ve mesaj
  kutusu güncellemeleri çağrı döndükten sonra GUI iş parçacığında yapılır.
* QApplication yoksa, çağrı GUI dışı bir iş parçacığından geliyorsa, başka bir
  görev zaten sürüyorsa (iç içe çağrı) veya ``DITUS_SYNC_TASKS=1`` ise ``fn``
  doğrudan, eşzamanlı çağrılır.
"""

from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

from PySide6.QtCore import QEventLoop, QThread, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

__all__ = [
    "MINIMUM_DURATION_MS",
    "SYNC_TASKS_ENV",
    "run_blocking_task",
    "task_running",
]

SYNC_TASKS_ENV = "DITUS_SYNC_TASKS"
MINIMUM_DURATION_MS = 400
# Platformlar arası tutarlılık: macOS ikincil iş parçacığı yığını 512 KiB'tır.
_WORKER_STACK_BYTES = 8 * 1024 * 1024

_T = TypeVar("_T")
_active_tasks = 0


class _TaskThread(QThread):
    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.result: Any = None
        self.error: BaseException | None = None
        self.done = False

    def run(self) -> None:
        try:
            self.result = self._fn(*self._args, **self._kwargs)
        except BaseException as exc:  # GUI iş parçacığında yeniden fırlatılır.
            self.error = exc
        finally:
            self.done = True


class _BusyDialog(QProgressDialog):
    """İptal edilemeyen meşgul göstergesi."""

    shown = Signal()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Simge durumundan geri yükleme gibi kendiliğinden gösterimler sayılmaz.
        if not event.spontaneous():
            self.shown.emit()

    def reject(self) -> None:
        # Esc meşgul penceresini gizleyip modal kilidi kaldırmamalı.
        return

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()


def task_running() -> bool:
    """GUI iş parçacığında bir arka plan hesap görevi sürüyorsa ``True``."""

    return _active_tasks > 0


def _run_synchronously() -> bool:
    if os.environ.get(SYNC_TASKS_ENV, "").strip() == "1":
        return True
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return True
    if QThread.currentThread() != app.thread():
        return True
    return _active_tasks > 0


def _busy_dialog(parent: object, title: str, label: str) -> _BusyDialog:
    dialog = _BusyDialog(parent if isinstance(parent, QWidget) else None)
    # Ekran-sığdırma yöneticisi bu küçük göstergeyi çalışma penceresi gibi büyütmesin.
    dialog.setProperty("ditus_disable_auto_fit", True)
    dialog.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setCancelButton(None)
    dialog.setRange(0, 0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setMinimumDuration(MINIMUM_DURATION_MS)
    dialog.setValue(0)
    return dialog


def run_blocking_task(
    parent: QWidget | None,
    title: str,
    label: str,
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> _T:
    """``fn(*args, **kwargs)`` sonucunu döndür; motor bir ``QThread`` üzerinde koşar.

    Çağrı tamamlanana kadar bekleme imleci ve (``MINIMUM_DURATION_MS`` sonra)
    modal meşgul penceresi gösterilir. İmleç hata durumunda da geri alınır,
    iş parçacığı her durumda ``wait()`` ile kapatılır.
    """

    if _run_synchronously():
        return fn(*args, **kwargs)

    global _active_tasks
    worker = _TaskThread(fn, args, kwargs)
    worker.setStackSize(_WORKER_STACK_BYTES)
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    dialog = _busy_dialog(parent, title, label)
    dialog.shown.connect(loop.quit)
    _active_tasks += 1
    try:
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        worker.start()
        if not worker.done:
            # Meşgul penceresi açılana kadar kullanıcı girdisi bekletilir.
            loop.exec(QEventLoop.ExcludeUserInputEvents)
        if not worker.done:
            # Pencere görünür: modal kilit girdiyi engeller, olay döngüsü serbest.
            loop.exec()
    finally:
        worker.wait()
        QApplication.restoreOverrideCursor()
        _active_tasks -= 1
        dialog.hide()
        dialog.deleteLater()

    error = worker.error
    if error is not None:
        worker.error = None
        try:
            raise error
        finally:
            error = None
    return worker.result
