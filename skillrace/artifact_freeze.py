"""Pure builders for the immutable pre-headline SkillRACE artifact freeze.

The builders do not contact a model and do not launch an experiment.  The eventual
freeze command composes these values only after the image, D2, pilot, and regression
gates have passed; keeping the transformations pure makes the draft-to-frozen delta
reviewable and testable before that point.
"""

from __future__ import annotations

import copy
import hashlib
import os
import pathlib
import re
import stat
from collections.abc import Mapping, Sequence
from typing import Any

from .campaign_protocol import CampaignProtocol
from .input_identity import skill_input_tree_hash
from .io_utils import canonical_json_hash
from .model_policy import EXPERIMENT_MODELS


_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_RESULT_ROOTS = {"results", "headline-results"}
_FROZEN_PROTOCOL_FIELDS = {
    "budget": 30,
    "bootstrap_count": 10,
    "max_generation_attempts_per_execution": 5,
    "greybox_level": "L1",
    "random_seed": 20260711,
    "seed_generator": {
        "batch_size": 5,
        "temperature": 0.9,
        "build_retries": 4,
    },
}


class ArtifactFreezeError(ValueError):
    """A draft input cannot be promoted without changing or weakening the protocol."""


def freeze_protocol_data(value: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    """Return the exact reviewed protocol with only its draft identity promoted."""

    if model not in EXPERIMENT_MODELS:
        raise ArtifactFreezeError(f"unknown model track: {model}")
    data = copy.deepcopy(dict(value))
    try:
        protocol = CampaignProtocol.from_dict(data)
    except (TypeError, ValueError) as error:
        raise ArtifactFreezeError(f"invalid draft protocol for {model}: {error}") from error
    if (
        protocol.model != model
        or protocol.status != "draft"
        or protocol.protocol_id != f"skillrace-issta-main-{model}-v1-draft"
    ):
        raise ArtifactFreezeError(f"unexpected draft protocol identity for {model}")
    for field, expected in _FROZEN_PROTOCOL_FIELDS.items():
        if data.get(field) != expected:
            raise ArtifactFreezeError(f"draft protocol {field} is not the approved value")
    data["status"] = "frozen"
    data["protocol_id"] = f"skillrace-issta-main-{model}-v1"
    # Parse the promoted value too, so the output cannot bypass the public schema.
    CampaignProtocol.from_dict(data)
    return data


def freeze_dual_protocol_data(
    value: Mapping[str, Any],
    *,
    protocols: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Promote the unpooled two-track index and bind its exact protocol paths."""

    data = copy.deepcopy(dict(value))
    if (
        data.get("schema") != "dual-model-experiment/1"
        or data.get("status") != "draft"
        or data.get("experiment_id") != "skillrace-issta-dual-model-v1-draft"
        or data.get("reporting")
        != "separate-primary-tables-plus-unpooled-robustness"
        or set(protocols) != set(EXPERIMENT_MODELS)
    ):
        raise ArtifactFreezeError("unexpected dual-model draft identity")
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or len(tracks) != len(EXPERIMENT_MODELS):
        raise ArtifactFreezeError("dual-model manifest must contain exactly two tracks")
    frozen_tracks = []
    for raw, model in zip(tracks, EXPERIMENT_MODELS, strict=True):
        protocol = protocols[model]
        if (
            not isinstance(raw, Mapping)
            or raw.get("track_id") != model
            or raw.get("model") != model
            or raw.get("protocol")
            != f"experiments/protocols/issta-main.{model}.draft.json"
            or raw.get("output_root") != f"results/{model}"
            or protocol.get("status") != "frozen"
            or protocol.get("protocol_id") != f"skillrace-issta-main-{model}-v1"
            or protocol.get("model") != model
        ):
            raise ArtifactFreezeError(f"dual-model track drifted for {model}")
        track = copy.deepcopy(dict(raw))
        track["protocol"] = f"experiments/protocols/issta-main.{model}.frozen.json"
        frozen_tracks.append(track)
    data["tracks"] = frozen_tracks
    data["status"] = "frozen"
    data["experiment_id"] = "skillrace-issta-dual-model-v1"
    return data


def _records_by_skill(lock: Mapping[str, Any], *, model: str) -> dict[str, Mapping[str, Any]]:
    if (
        lock.get("schema") != "d1-track-images/1"
        or lock.get("status") != "draft"
        or lock.get("model") != model
        or lock.get("pi_version") != "0.73.1"
    ):
        raise ArtifactFreezeError(f"malformed draft D1 image lock for {model}")
    records = lock.get("records")
    if not isinstance(records, list):
        raise ArtifactFreezeError(f"D1 image records are missing for {model}")
    by_skill: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("skill"), str):
            raise ArtifactFreezeError(f"malformed D1 image record for {model}")
        skill = record["skill"]
        if skill in by_skill:
            raise ArtifactFreezeError(f"duplicate D1 image record for {model}/{skill}")
        by_skill[skill] = record
    return by_skill


def freeze_suite_data(
    value: Mapping[str, Any],
    *,
    track_locks: Mapping[str, Mapping[str, Any]],
    repo_root: str | pathlib.Path,
) -> dict[str, Any]:
    """Bind the D1 suite to shared construction IDs and exact skill input trees."""

    suite = copy.deepcopy(dict(value))
    if (
        suite.get("schema") != "d1-suite/1"
        or suite.get("status") != "draft"
        or suite.get("suite_id") != "skillrace-d1-public-v1-draft"
    ):
        raise ArtifactFreezeError("unexpected D1 draft suite identity")
    if set(track_locks) != set(EXPERIMENT_MODELS):
        raise ArtifactFreezeError("D1 image locks must contain exactly both model tracks")
    indexed = {
        model: _records_by_skill(track_locks[model], model=model)
        for model in EXPERIMENT_MODELS
    }
    headline = suite.get("headline_skills")
    if not isinstance(headline, list) or len(headline) != 30:
        raise ArtifactFreezeError("D1 freeze requires exactly 30 headline skills")
    root = pathlib.Path(repo_root).resolve()
    frozen_records: list[dict[str, Any]] = []
    expected_order: list[str] = []
    for raw in headline:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str):
            raise ArtifactFreezeError("malformed D1 headline skill record")
        skill = raw["id"]
        expected_order.append(skill)
        left = indexed[EXPERIMENT_MODELS[0]].get(skill)
        right = indexed[EXPERIMENT_MODELS[1]].get(skill)
        if left is None or right is None:
            raise ArtifactFreezeError(f"D1 image lock is missing {skill}")
        construction_id = left.get("construction_image_id")
        if construction_id != right.get("construction_image_id"):
            raise ArtifactFreezeError(
                f"cross-track construction image differs for {skill}"
            )
        if not _IMAGE_ID.fullmatch(str(construction_id)):
            raise ArtifactFreezeError(f"invalid construction image identity for {skill}")
        input_hash = skill_input_tree_hash(root / "skills" / skill)
        if left.get("input_tree_hash") != input_hash or right.get("input_tree_hash") != input_hash:
            raise ArtifactFreezeError(f"D1 image input tree drifted for {skill}")
        for model, record in ((EXPERIMENT_MODELS[0], left), (EXPERIMENT_MODELS[1], right)):
            if (
                record.get("tag") != f"skillrace/{skill}:base-{model}"
                or not _IMAGE_ID.fullmatch(str(record.get("image_id", "")))
                or record.get("runtime_audit") != "passed-networkless"
            ):
                raise ArtifactFreezeError(f"D1 model overlay is not audited: {model}/{skill}")
        frozen = copy.deepcopy(dict(raw))
        frozen["input_tree_hash"] = input_hash
        frozen["base_image_id"] = construction_id
        frozen_records.append(frozen)
    for model in EXPERIMENT_MODELS:
        if list(indexed[model]) != expected_order:
            raise ArtifactFreezeError(f"D1 image order differs from suite for {model}")
    suite["headline_skills"] = frozen_records
    suite["status"] = "frozen"
    suite["suite_id"] = "skillrace-d1-public-v1"
    return suite


def freeze_track_lock_data(
    value: Mapping[str, Any],
    *,
    draft_suite: Mapping[str, Any],
    frozen_suite: Mapping[str, Any],
) -> dict[str, Any]:
    """Promote a validated per-model image lock and rebind it to the frozen D1 suite."""

    lock = copy.deepcopy(dict(value))
    model = lock.get("model")
    if (
        model not in EXPERIMENT_MODELS
        or lock.get("schema") != "d1-track-images/1"
        or lock.get("status") != "draft"
        or lock.get("suite_manifest")
        != "experiments/manifests/rq1-skills.draft.json"
        or lock.get("suite_manifest_hash") != canonical_json_hash(draft_suite)
    ):
        raise ArtifactFreezeError("D1 track lock is not bound to the reviewed draft suite")
    if (
        frozen_suite.get("status") != "frozen"
        or frozen_suite.get("suite_id") != "skillrace-d1-public-v1"
    ):
        raise ArtifactFreezeError("cannot bind an image lock to a non-frozen D1 suite")
    lock["status"] = "frozen"
    lock["suite_manifest"] = "experiments/manifests/rq1-skills.frozen.json"
    lock["suite_manifest_hash"] = canonical_json_hash(frozen_suite)
    return lock


def freeze_skillgen_lock_data(value: Mapping[str, Any]) -> dict[str, Any]:
    """Promote the shared two-model Skillgen overlay lock without changing its inputs."""

    lock = copy.deepcopy(dict(value))
    if (
        lock.get("schema") != "skillrace-skillgen-track-images/1"
        or lock.get("status") != "draft"
        or lock.get("pi_version") != "0.73.1"
        or not _IMAGE_ID.fullmatch(str(lock.get("construction_base_id", "")))
        or not isinstance(lock.get("records"), list)
    ):
        raise ArtifactFreezeError("malformed draft Skillgen track-image lock")
    lock["status"] = "frozen"
    return lock


def _safe_relative(value: str | pathlib.PurePath) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(os.fspath(value))
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise ArtifactFreezeError(f"inventory path must be a safe relative path: {value}")
    if path.parts[0] in _FORBIDDEN_RESULT_ROOTS:
        raise ArtifactFreezeError(f"forbidden result root in freeze inventory: {path.parts[0]}")
    return path


def hash_inventory(
    repo_root: str | pathlib.Path,
    paths: Sequence[str | pathlib.PurePath],
) -> dict[str, Any]:
    """Hash selected source trees, including path and permission metadata.

    Headline result roots are rejected rather than accidentally blessing observations as
    protocol inputs. Symlinks are also rejected so the inventory is self-contained.
    """

    root = pathlib.Path(repo_root).resolve()
    files: list[dict[str, Any]] = []
    directories: list[str] = []
    seen: set[str] = set()
    for requested in paths:
        relative = _safe_relative(requested)
        target = root.joinpath(*relative.parts)
        if target.is_symlink():
            raise ArtifactFreezeError(f"symlink is forbidden in freeze inventory: {relative}")
        if not target.exists():
            raise ArtifactFreezeError(f"freeze inventory input is missing: {relative}")
        candidates = [target] if target.is_file() else [target, *sorted(target.rglob("*"))]
        for candidate in candidates:
            rel = candidate.relative_to(root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            if candidate.is_symlink():
                raise ArtifactFreezeError(f"symlink is forbidden in freeze inventory: {rel}")
            mode = stat.S_IMODE(candidate.stat().st_mode)
            if candidate.is_dir():
                directories.append(rel)
            elif candidate.is_file():
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                files.append({"path": rel, "mode": f"{mode:04o}", "sha256": digest})
            else:
                raise ArtifactFreezeError(f"unsupported filesystem entry in inventory: {rel}")
    files.sort(key=lambda row: row["path"])
    directories.sort()
    payload = {
        "schema": "skillrace-source-inventory/1",
        "files": files,
        "directories": directories,
    }
    payload["inventory_sha256"] = canonical_json_hash(payload)
    if not _SHA256.fullmatch(payload["inventory_sha256"]):  # defensive schema assertion
        raise ArtifactFreezeError("inventory hash generation failed")
    return payload
