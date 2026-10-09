"""Generate the release engine SHA-256 lock from the current source tree."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Sequence


ENGINE_FOLDERS = ("src/ucd/calculations", "src/ucd/models")


def _engine_sha256(path: Path) -> str:
    """Hash Python source independently of checkout line-ending policy."""

    normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _engine_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_folder in ENGINE_FOLDERS:
        folder = root / relative_folder
        files.extend(
            path
            for path in folder.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def render_engine_baseline(root: Path) -> str:
    root = root.resolve()
    return "".join(
        f"{_engine_sha256(path)}  {path.relative_to(root).as_posix()}\n"
        for path in _engine_files(root)
    )


def write_engine_baseline(root: Path, output: Path) -> int:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = render_engine_baseline(root)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(payload.splitlines())


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    count = write_engine_baseline(args.root, args.output)
    print(f"Wrote {count} engine hashes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
