"""Crash-safe file helpers shared by project, catalog and settings writers.

``atomic_write_text`` never truncates the target in place: the new content is
written and fsync'ed in a sibling temporary file and then moved over the
target with ``os.replace``. A crash, full disk or dropped network share leaves
either the previous file or the complete new file, never a half-written one.
"""

from __future__ import annotations

import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path

__all__ = ["atomic_write_text", "backup_path", "quarantine_file"]


def backup_path(path: str | Path) -> Path:
    target = Path(path)
    return target.with_name(target.name + ".bak")


def _reserve_temporary_sibling(target: Path) -> Path:
    for _attempt in range(100):
        candidate = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        try:
            os.close(os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666))
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"Geçici dosya oluşturulamadı: {target}")


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    backup: bool = False,
) -> Path:
    """Replace ``path`` with ``text`` atomically.

    With ``backup=True`` the previous version of an existing target is kept as
    ``<name>.bak``. The temporary file is always removed when anything fails and
    the original exception is re-raised; the target is left untouched.
    """
    target = Path(path)
    if target.is_symlink():
        target = target.resolve()
    temporary = _reserve_temporary_sibling(target)
    try:
        with temporary.open("w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            try:
                shutil.copymode(target, temporary)
            except OSError:
                pass
            if backup:
                shutil.copy2(target, backup_path(target))
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return target


def quarantine_file(path: str | Path, reason_tag: str = "corrupt") -> Path:
    """Move an unusable file aside as ``<name>.<tag>-YYYYmmdd-HHMMSS`` and return the new path."""
    source = Path(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = source.with_name(f"{source.name}.{reason_tag}-{stamp}")
    index = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.{reason_tag}-{stamp}-{index}")
        index += 1
    source.rename(candidate)
    return candidate
