"""Uniform permissions for immutable runtime package targets."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

READ_ONLY_DIRECTORY_MODE = 0o555
PROMOTABLE_DIRECTORY_MODE = 0o755
READ_ONLY_PAYLOAD_MODE = 0o555
READ_ONLY_METADATA_MODE = 0o444


def harden_runtime_target(root: Path, *, writable_root: bool = False) -> None:
    """Make one materialized runtime target immutable and uniformly executable."""
    marker = root / ".complete"
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.is_dir():
                path.chmod(READ_ONLY_DIRECTORY_MODE)
            elif path.is_file():
                path.chmod(READ_ONLY_METADATA_MODE if os.name == "nt" or path == marker else READ_ONLY_PAYLOAD_MODE)
        except OSError:
            pass
    try:
        root.chmod(PROMOTABLE_DIRECTORY_MODE if writable_root else READ_ONLY_DIRECTORY_MODE)
    except OSError:
        pass


def runtime_target_permissions_valid(root: Path) -> bool:
    """Return whether a target has the immutable runtime permission invariant."""
    marker = root / ".complete"
    try:
        if os.name == "nt":
            return all(not (path.lstat().st_mode & stat.S_IWRITE) for path in root.rglob("*") if path.is_file())
        if stat.S_IMODE(root.lstat().st_mode) != READ_ONLY_DIRECTORY_MODE:
            return False
        for path in root.rglob("*"):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                expected = READ_ONLY_DIRECTORY_MODE
            elif stat.S_ISREG(mode):
                expected = READ_ONLY_METADATA_MODE if path == marker else READ_ONLY_PAYLOAD_MODE
            else:
                continue
            if stat.S_IMODE(mode) != expected:
                return False
    except OSError:
        return False
    return True


def runtime_target_modes_safely_repairable(root: Path) -> bool:
    """Return whether chmod can restore modes without trusting writable artifact bytes."""
    try:
        marker = root / ".complete"
        json.loads(marker.read_text(encoding="utf-8"))
        paths = (root, *root.rglob("*"))
        if os.name == "nt":
            writable = (path for path in paths if path.is_file() and path.lstat().st_mode & stat.S_IWRITE)
        else:
            writable = (path for path in paths if (path.is_file() or path.is_dir()) and path.lstat().st_mode & 0o222)
        if any(writable):
            return False
    except (OSError, TypeError, json.JSONDecodeError):
        return False
    return True
