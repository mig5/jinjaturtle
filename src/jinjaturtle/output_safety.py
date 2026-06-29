from __future__ import annotations

"""Safer file-output helpers for the JinjaTurtle CLI.

The CLI is often used by administrators. A plain Path.write_text() follows a
final-path symlink and can therefore be dangerous when a root-run invocation
writes into an attacker-writable tree. These helpers validate path components,
refuse symlink parents, reject root-run output through untrusted parent
components, write through a temporary file in the target directory, and replace
the final path atomically. Existing final-path symlinks are refused rather than
followed.
"""

import os
from pathlib import Path
import stat
import tempfile


class OutputPathError(OSError):
    """Raised when a requested output path is unsafe."""


# Keep a reference to the real euid getter. Tests can monkeypatch
# _effective_uid directly without changing process-wide os.geteuid behaviour.
_OS_GETEUID = getattr(os, "geteuid", None)


def _effective_uid() -> int | None:
    if _OS_GETEUID is None:
        return None
    try:
        return int(_OS_GETEUID())
    except OSError:
        return None


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Best-effort; mkdir(mode=0o700) is already used for normal filesystems.
        pass


def _assert_trusted_root_parent(path: Path, st: os.stat_result) -> None:
    """Reject root-run output through attacker-controlled parent directories.

    A root-run JinjaTurtle process may write files that an administrator later
    applies as configuration-management input. Parent directories controlled by
    an unprivileged user are therefore not acceptable output anchors. Root-owned
    sticky shared directories such as /tmp are allowed as a boundary, but any
    existing child below them must be root-owned and not writable by group/other.
    """

    if _effective_uid() != 0:
        return
    if not stat.S_ISDIR(st.st_mode):
        raise OutputPathError(f"output parent is not a directory: {path}")
    if st.st_uid != 0:
        raise OutputPathError(
            f"output parent is not owned by root; refusing root-run output: {path}"
        )
    writable_by_group_or_other = st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    sticky = st.st_mode & stat.S_ISVTX
    if writable_by_group_or_other and not sticky:
        raise OutputPathError(
            "output parent is writable by group/other; "
            f"refusing root-run output: {path}"
        )


def _assert_existing_directory_component(path: Path) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise OutputPathError(f"unable to inspect output parent: {path}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise OutputPathError(f"refusing to use symlink parent: {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise OutputPathError(f"output parent is not a directory: {path}")
    _assert_trusted_root_parent(path, st)


def _mkdir_safe_dir_tree(path: Path) -> Path:
    """Create/validate a directory tree one component at a time.

    pathlib.mkdir(parents=True) can traverse a symlink inserted after a parent
    pre-check. Walking one component at a time keeps every existing component
    checked before it is used, and newly-created components are immediately
    re-inspected.
    """

    out = _absolute(path)
    parts = out.parts
    if not parts:
        return out

    if out.is_absolute():
        cur = Path(parts[0])
        rest = parts[1:]
        _assert_existing_directory_component(cur)
    else:
        # _absolute() currently always returns an absolute path, but keep this
        # branch for clarity if that helper is ever relaxed.
        cur = Path.cwd()
        rest = parts
        _assert_existing_directory_component(cur)

    for part in rest:
        cur = cur / part
        if os.path.lexists(cur):
            _assert_existing_directory_component(cur)
            continue
        try:
            os.mkdir(cur, 0o700)
        except FileExistsError:
            _assert_existing_directory_component(cur)
            continue
        _chmod_private(cur)
        _assert_existing_directory_component(cur)

    return out


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
    """Require every existing parent component to be a trusted real directory."""

    _mkdir_safe_dir_tree(parent)


def ensure_safe_directory(path: Path) -> None:
    """Create or validate a directory tree without accepting unsafe parents."""

    _mkdir_safe_dir_tree(path)


def write_text_safely(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text without following a final-path symlink.

    The target's parent is created/validated component by component. Existing
    parent symlinks are refused for every user. When running as root, existing
    parent components must also be root-owned and not writable by group/other,
    except for sticky shared boundaries such as /tmp. Existing final-path
    symlinks are refused rather than followed.
    """

    path = _absolute(path)
    parent = _mkdir_safe_dir_tree(path.parent)
    _check_existing_output_file(path)

    fd = -1
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(parent), text=True
        )
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = -1
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        _check_parent_components(path.parent)
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
