"""Deterministic identities for the immutable inputs of a campaign."""

from __future__ import annotations

import hashlib
import os
import pathlib
import stat


_EXCLUDED_COMPONENTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
}


def _field(digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _hash_entry(
    digest,
    root: pathlib.Path,
    path: pathlib.Path,
    info: os.stat_result,
) -> None:
    relative = "." if path == root else path.relative_to(root).as_posix()
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"skill input symlink is forbidden: {relative}")
    _field(digest, relative.encode("utf-8"))
    _field(digest, f"{stat.S_IMODE(info.st_mode):04o}".encode("ascii"))
    if stat.S_ISDIR(info.st_mode):
        _field(digest, b"directory")
    elif stat.S_ISREG(info.st_mode):
        _field(digest, b"file")
        _field(digest, str(info.st_size).encode("ascii"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    else:
        _field(digest, b"other")


def skill_input_tree_hash(skill_dir: str | pathlib.Path) -> str:
    """Hash relevant skill paths, bytes, file types, and permission modes.

    Only repository/test interpreter metadata is excluded. Ordinary directories
    named cache, out, or output remain effective inputs. Symlinks are rejected
    instead of followed or represented ambiguously.
    """
    root = pathlib.Path(skill_dir)
    digest = hashlib.sha256()
    _field(digest, b"skill-input-tree/3")
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        _field(digest, b"missing")
        return digest.hexdigest()

    _hash_entry(digest, root, root, root_info)
    if not stat.S_ISDIR(root_info.st_mode):
        return digest.hexdigest()

    directories = [root]
    while directories:
        directory = directories.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        descendants = []
        for entry in children:
            path = pathlib.Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                _hash_entry(digest, root, path, info)
            if entry.name in _EXCLUDED_COMPONENTS:
                continue
            _hash_entry(digest, root, path, info)
            if stat.S_ISDIR(info.st_mode):
                descendants.append(path)
        directories.extend(reversed(descendants))
    return digest.hexdigest()
