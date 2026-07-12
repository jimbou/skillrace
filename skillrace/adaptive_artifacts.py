"""Version and restore SkillRACE's global adaptive JSON artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
from typing import Any

from .io_utils import atomic_write_json, atomic_write_text, canonical_json_hash


_ATTEMPT_ID = re.compile(r"e[0-9]{4}-a[0-9]{2}\Z")


def artifact_paths(tree_path: str | pathlib.Path) -> dict[str, pathlib.Path]:
    tree = pathlib.Path(tree_path)
    return {
        "tree.json": tree,
        "tree.cache.json": tree.with_suffix(".cache.json"),
        "tree.guards.json": tree.with_suffix(".guards.json"),
    }


def _entry_from_text(text: str, mode: int = 0o644) -> dict[str, Any]:
    encoded = text.encode("utf-8")
    return {
        "present": True,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "mode": mode,
        "content": text,
    }


def capture_adaptive_artifacts(
    tree_path: str | pathlib.Path,
    *,
    overrides: dict[str, str | dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Capture the adaptive files, optionally describing pending replacements.

    A string override models :func:`atomic_write_json`/``atomic_write_text`` and
    therefore uses the replacement file's ``0600`` mode.  Callers publishing by
    another mechanism can supply ``{"content": text, "mode": mode}`` explicitly.
    """
    overrides = overrides or {}
    captured = {}
    for name, path in artifact_paths(tree_path).items():
        if name in overrides:
            override = overrides[name]
            if isinstance(override, str):
                captured[name] = _entry_from_text(override, 0o600)
            elif (
                isinstance(override, dict)
                and isinstance(override.get("content"), str)
                and isinstance(override.get("mode"), int)
            ):
                captured[name] = _entry_from_text(
                    override["content"], override["mode"]
                )
            else:
                raise ValueError(f"malformed adaptive artifact override: {name}")
        elif path.exists():
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"adaptive artifact must be a regular file: {path}")
            captured[name] = _entry_from_text(
                path.read_text(encoding="utf-8"), stat.S_IMODE(path.stat().st_mode)
            )
        else:
            captured[name] = {"present": False}
    return captured


def _validate_artifact_snapshot(artifacts: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(artifacts, dict) or set(artifacts) != set(artifact_paths("tree.json")):
        raise ValueError("malformed adaptive artifact snapshot")
    for name, entry in artifacts.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("present"), bool):
            raise ValueError(f"malformed adaptive artifact entry: {name}")
        if entry["present"]:
            content = entry.get("content")
            mode = entry.get("mode")
            if not isinstance(content, str) or not isinstance(mode, int):
                raise ValueError(f"malformed adaptive artifact content: {name}")
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != entry.get("sha256"):
                raise ValueError(f"adaptive artifact content hash mismatch: {name}")
    return artifacts


def verify_adaptive_artifacts(tree_path, artifacts) -> None:
    expected = _validate_artifact_snapshot(artifacts)
    if capture_adaptive_artifacts(tree_path) != expected:
        raise ValueError("adaptive artifact content/presence mismatch")


def restore_adaptive_artifacts(tree_path, artifacts) -> None:
    expected = _validate_artifact_snapshot(artifacts)
    for name, path in artifact_paths(tree_path).items():
        entry = expected[name]
        if not entry["present"]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        atomic_write_text(path, entry["content"])
        os.chmod(path, entry["mode"])
    verify_adaptive_artifacts(tree_path, expected)


def _version_paths(tree_path, attempt_id):
    if not _ATTEMPT_ID.fullmatch(str(attempt_id)):
        raise ValueError("invalid fold artifact attempt ID")
    root = pathlib.Path(tree_path).parent / "fold-artifacts"
    return root / f"{attempt_id}.json", root / f"{attempt_id}.complete.json"


def _publish_immutable(path: pathlib.Path, value: dict, label: str) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"malformed {label}: {error}") from error
        if canonical_json_hash(existing) != canonical_json_hash(value):
            raise ValueError(f"conflicting immutable {label}")
        return
    atomic_write_json(path, value)


def stage_fold_artifact_version(tree_path, attempt_id, artifacts):
    artifacts = _validate_artifact_snapshot(artifacts)
    version_path, _ = _version_paths(tree_path, attempt_id)
    value = {
        "schema": "skillrace-fold-artifacts/1",
        "attempt_id": attempt_id,
        "artifacts": artifacts,
    }
    _publish_immutable(version_path, value, "forward-fold artifact version")
    return value


def complete_fold_artifact_version(tree_path, attempt_id):
    version_path, completion_path = _version_paths(tree_path, attempt_id)
    try:
        version = json.loads(version_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"missing/malformed forward-fold artifact version: {error}") from error
    verify_adaptive_artifacts(tree_path, version.get("artifacts"))
    completion = {
        "schema": "skillrace-fold-artifacts-complete/1",
        "attempt_id": attempt_id,
        "version_hash": canonical_json_hash(version),
    }
    _publish_immutable(completion_path, completion, "forward-fold completion")
    return version


def publish_completed_fold_artifact_version(tree_path, attempt_id):
    artifacts = capture_adaptive_artifacts(tree_path)
    stage_fold_artifact_version(tree_path, attempt_id, artifacts)
    return complete_fold_artifact_version(tree_path, attempt_id)


def recover_fold_artifact_version(tree_path, attempt_id):
    version_path, completion_path = _version_paths(tree_path, attempt_id)
    if not version_path.exists():
        return None
    try:
        version = json.loads(version_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"malformed forward-fold artifact version: {error}") from error
    if version.get("schema") != "skillrace-fold-artifacts/1" or version.get("attempt_id") != attempt_id:
        raise ValueError("conflicting forward-fold artifact version")
    artifacts = _validate_artifact_snapshot(version.get("artifacts"))
    tree_entry = artifacts["tree.json"]
    try:
        tree = json.loads(tree_entry["content"]) if tree_entry["present"] else {}
    except json.JSONDecodeError as error:
        raise ValueError("forward-fold tree artifact is malformed") from error
    if attempt_id not in tree.get("folded_attempts", {}):
        raise ValueError("forward-fold artifact lacks matching attempt marker")

    if completion_path.exists():
        try:
            completion = json.loads(completion_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"malformed forward-fold completion: {error}") from error
        if (
            completion.get("schema") != "skillrace-fold-artifacts-complete/1"
            or completion.get("attempt_id") != attempt_id
            or completion.get("version_hash") != canonical_json_hash(version)
        ):
            raise ValueError("conflicting forward-fold completion")
        try:
            verify_adaptive_artifacts(tree_path, artifacts)
        except ValueError as error:
            raise ValueError("forward-fold artifact drift after completion") from error
    else:
        restore_adaptive_artifacts(tree_path, artifacts)
        complete_fold_artifact_version(tree_path, attempt_id)
    return version
