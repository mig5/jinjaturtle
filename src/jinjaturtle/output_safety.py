from __future__ import annotations

"""Safer file-output helpers for the JinjaTurtle CLI.

The CLI is often used by administrators.  A plain Path.write_text() follows a
final-path symlink and can therefore be dangerous when a root-run invocation
writes into an attacker-writable tree.  These helpers validate path components,
write through a private temporary file in the target directory, and replace the
final path atomically.  Existing final-path symlinks are refused rather than
followed.
"""

import os
from pathlib import Path
import stat
import tempfile


class OutputPathError(OSError):
    """Raised when a requested output path is unsafe."""


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _check_existing_path_not_symlink(path: Path) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise OutputPathError(f"refusing to use symlink path: {path}")


def _check_existing_output_file(path: Path) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise OutputPathError(f"refusing to write through symlink: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise OutputPathError(f"refusing to replace non-regular file: {path}")


def _check_parent_components(parent: Path) -> None:
    """Require every existing parent component to be a real directory."""

    parent = _absolute(parent)
    parts = parent.parts
    if not parts:
        return

    cur = Path(parts[0])
    for part in parts[1:]:
        cur = cur / part
        try:
            st = cur.lstat()
        except FileNotFoundError as exc:
            raise OutputPathError(f"output parent does not exist: {cur}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise OutputPathError(f"refusing to use symlink parent: {cur}")
        if not stat.S_ISDIR(st.st_mode):
            raise OutputPathError(f"output parent is not a directory: {cur}")


def ensure_safe_directory(path: Path) -> None:
    """Create or validate a directory tree without accepting symlinks."""

    path = _absolute(path)
    parts = path.parts
    if not parts:
        return

    cur = Path(parts[0])
    for part in parts[1:]:
        cur = cur / part
        try:
            st = cur.lstat()
        except FileNotFoundError:
            cur.mkdir(mode=0o700)
            st = cur.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise OutputPathError(f"refusing to use symlink directory: {cur}")
        if not stat.S_ISDIR(st.st_mode):
            raise OutputPathError(f"output path is not a directory: {cur}")


def write_text_safely(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text without following a final-path symlink.

    The target's parent must already exist and every parent component must be a
    real directory.  The write is completed with os.replace(), which atomically
    swaps the final directory entry and does not dereference a final symlink.
    """

    path = _absolute(path)
    _check_parent_components(path.parent)
    _check_existing_output_file(path)

    fd = -1
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
        )
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = -1
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        _check_existing_output_file(path)
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
