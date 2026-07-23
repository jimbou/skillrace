"""Deterministic hashing and crash-safe file replacement helpers."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import pathlib
import tempfile
from typing import Any


_UNSUPPORTED_DIRECTORY_FSYNC = {
    errno.EACCES,
    errno.EBADF,
    errno.EINVAL,
    errno.EPERM,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for content identities."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    """Return the SHA-256 hex digest of canonical JSON content."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_hash(path: str | pathlib.Path) -> str:
    """Return the SHA-256 hex digest of a file without loading it all at once."""
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_campaign_path(
    root: str | pathlib.Path, raw: Any, label: str
) -> pathlib.Path:
    """Resolve a saved campaign path while containing it beneath ``root``.

    Current drivers pass absolute cell roots. Older development drivers could save a
    workspace-relative path that already included the cell root; prefer the normal
    root-relative interpretation when it exists, then accept that legacy spelling only
    when it resolves to an existing artifact inside the same cell root.
    """

    if not isinstance(raw, str) or not raw:
        raise ValueError(f"campaign lacks {label}")
    root_path = pathlib.Path(root).resolve()
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        rooted = (root_path / candidate).resolve()
        workspace_relative = candidate.resolve()
        resolved = (
            workspace_relative
            if not rooted.exists()
            and workspace_relative.exists()
            and (workspace_relative == root_path or root_path in workspace_relative.parents)
            else rooted
        )
    if resolved == root_path or root_path not in resolved.parents:
        raise ValueError(f"campaign {label} escapes its cell root")
    return resolved


def _fsync_directory(directory: pathlib.Path) -> None:
    """Persist a directory entry where the host filesystem supports it."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError as error:
        if error.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
            return
        raise
    try:
        try:
            os.fsync(fd)
        except OSError as error:
            if error.errno not in _UNSUPPORTED_DIRECTORY_FSYNC:
                raise
    finally:
        os.close(fd)


def atomic_write_text(path: str | pathlib.Path, text: str) -> None:
    """Replace a UTF-8 text file atomically, preserving its old value on failure."""
    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str | pathlib.Path, value: Any) -> None:
    """Write a complete, human-readable JSON document atomically."""
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")
