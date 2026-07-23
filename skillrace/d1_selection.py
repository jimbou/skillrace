"""Validate the pre-result D1 continuation audit against the frozen S5 pool."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any

from .io_utils import canonical_json_hash


_TOP_FIELDS = {
    "schema",
    "recorded_at",
    "before_any_headline_execution",
    "pool",
    "historical_boundary",
    "protocol",
    "selected",
    "rejection_groups",
    "notable_rejections",
}
_REJECTION_CODES = {
    "X1_NON_DOING_OR_PRESENTATIONAL",
    "X2_PROJECT_COUPLED_OR_EXTERNAL",
    "X3_DUPLICATE_OR_SATURATED",
    "X4_UNSAFE_FOR_BENCHMARK",
    "X5_LICENSE_OR_SNAPSHOT",
    "I3_I4_THIN_OR_LONG_HORIZON",
}
_ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GITHUB = re.compile(r"https://github\.com/([^/]+/[^/]+)/")


class SelectionAuditError(ValueError):
    """The continuation order or its pre-result decision ledger is invalid."""


def _load(path: pathlib.Path, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SelectionAuditError(f"cannot read {label} at {path}: {error}") from error


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_continuation_audit(
    audit_path: str | pathlib.Path,
    *,
    suite_manifest: str | pathlib.Path,
    repo_root: str | pathlib.Path,
) -> dict[str, Any]:
    """Rehash the pool and prove that rows through the eighth admit are partitioned."""

    path = pathlib.Path(audit_path)
    root = pathlib.Path(repo_root)
    audit = _load(path, "D1 continuation audit")
    if not isinstance(audit, dict) or set(audit) != _TOP_FIELDS:
        raise SelectionAuditError("continuation audit has unsupported fields")
    if audit.get("schema") != "d1-continuation-audit/1":
        raise SelectionAuditError("unsupported continuation audit schema")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(audit.get("recorded_at", ""))):
        raise SelectionAuditError("continuation audit recorded_at must be an ISO date")
    if audit.get("before_any_headline_execution") is not True:
        raise SelectionAuditError("continuation audit must precede headline execution")

    pool_meta = audit.get("pool")
    if not isinstance(pool_meta, dict) or set(pool_meta) != {
        "path",
        "sha256",
        "records",
        "ordering",
    }:
        raise SelectionAuditError("continuation pool metadata is malformed")
    pool_rel = pathlib.PurePosixPath(str(pool_meta.get("path", "")))
    if pool_rel.is_absolute() or ".." in pool_rel.parts:
        raise SelectionAuditError("continuation pool path is unsafe")
    pool_path = root.joinpath(*pool_rel.parts)
    expected_pool_hash = pool_meta.get("sha256")
    if not isinstance(expected_pool_hash, str) or not _SHA256.fullmatch(
        expected_pool_hash
    ):
        raise SelectionAuditError("continuation pool hash is malformed")
    if _file_sha256(pool_path) != expected_pool_hash:
        raise SelectionAuditError("frozen continuation pool hash drifted")
    pool = _load(pool_path, "frozen S5 candidate pool")
    if not isinstance(pool, list) or len(pool) != pool_meta.get("records"):
        raise SelectionAuditError("frozen continuation pool record count drifted")
    if not isinstance(pool_meta.get("ordering"), str) or len(pool_meta["ordering"]) < 40:
        raise SelectionAuditError("continuation ordering rule is missing")

    boundary = audit.get("historical_boundary")
    if not isinstance(boundary, dict) or set(boundary) != {
        "manifest_path",
        "headline_count",
        "statement",
        "skill_ids",
    }:
        raise SelectionAuditError("historical D1 boundary is malformed")
    historical_ids = boundary.get("skill_ids")
    if (
        boundary.get("headline_count") != 22
        or not isinstance(historical_ids, list)
        or len(historical_ids) != 22
        or len(set(historical_ids)) != 22
        or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in historical_ids)
        or not isinstance(boundary.get("statement"), str)
        or len(boundary["statement"]) < 80
    ):
        raise SelectionAuditError("historical D1 boundary is incomplete")

    protocol = audit.get("protocol")
    if not isinstance(protocol, dict) or set(protocol) != {
        "target_additions",
        "criteria_path",
        "screened_through_pool_index",
        "repository_cap",
        "stop_rule",
        "reason_policy",
    }:
        raise SelectionAuditError("continuation protocol is malformed")
    stop_index = protocol.get("screened_through_pool_index")
    if (
        protocol.get("target_additions") != 8
        or not isinstance(stop_index, int)
        or isinstance(stop_index, bool)
        or not 0 <= stop_index < len(pool)
        or protocol.get("repository_cap") != 2
        or not isinstance(protocol.get("stop_rule"), str)
        or len(protocol["stop_rule"]) < 80
        or not isinstance(protocol.get("reason_policy"), str)
        or len(protocol["reason_policy"]) < 80
    ):
        raise SelectionAuditError("continuation protocol values are invalid")

    selected = audit.get("selected")
    if not isinstance(selected, list) or len(selected) != 8:
        raise SelectionAuditError("continuation must contain exactly eight selected rows")
    selected_indices: list[int] = []
    selected_ids: list[str] = []
    repositories: dict[str, int] = {}
    for item in selected:
        if not isinstance(item, dict) or set(item) != {
            "pool_index",
            "skill_id",
            "name",
            "author",
            "stars",
            "candidate_url",
        }:
            raise SelectionAuditError("selected continuation row is malformed")
        index = item["pool_index"]
        skill_id = item["skill_id"]
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index <= stop_index
            or not isinstance(skill_id, str)
            or not _ID.fullmatch(skill_id)
        ):
            raise SelectionAuditError("selected continuation identity is invalid")
        candidate = pool[index]
        expected = {
            "name": candidate.get("name"),
            "author": candidate.get("author"),
            "stars": int(candidate.get("stars", 0) or 0),
            "candidate_url": candidate.get("githubUrl"),
        }
        for field, value in expected.items():
            if item.get(field) != value:
                raise SelectionAuditError(
                    f"selected continuation row {index} drifted at {field}"
                )
        match = _GITHUB.match(str(item["candidate_url"]))
        if match is None:
            raise SelectionAuditError("selected continuation URL is not a GitHub source")
        repository = match.group(1).lower()
        repositories[repository] = repositories.get(repository, 0) + 1
        selected_indices.append(index)
        selected_ids.append(skill_id)
    if selected_indices != sorted(selected_indices) or len(set(selected_indices)) != 8:
        raise SelectionAuditError("selected continuation rows are not strictly ordered")
    if selected_indices[-1] != stop_index:
        raise SelectionAuditError("continuation did not stop at the eighth selected row")
    if max(repositories.values(), default=0) > protocol["repository_cap"]:
        raise SelectionAuditError("continuation repository cap was exceeded")

    groups = audit.get("rejection_groups")
    if not isinstance(groups, list) or len(groups) != len(_REJECTION_CODES):
        raise SelectionAuditError("continuation rejection groups are incomplete")
    rejected_indices: list[int] = []
    observed_codes: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {
            "reason_code",
            "pool_indices",
        }:
            raise SelectionAuditError("continuation rejection group is malformed")
        code = group["reason_code"]
        indices = group["pool_indices"]
        if code not in _REJECTION_CODES or code in observed_codes:
            raise SelectionAuditError("continuation rejection code is invalid or duplicated")
        if (
            not isinstance(indices, list)
            or any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index <= stop_index
                for index in indices
            )
            or indices != sorted(indices)
            or len(indices) != len(set(indices))
        ):
            raise SelectionAuditError("continuation rejection indices are invalid")
        observed_codes.add(code)
        rejected_indices.extend(indices)
    accounted = selected_indices + rejected_indices
    if len(accounted) != len(set(accounted)) or set(accounted) != set(
        range(stop_index + 1)
    ):
        raise SelectionAuditError(
            "selected and rejected rows must form an exact partition through the stop"
        )

    notable = audit.get("notable_rejections")
    if not isinstance(notable, list) or not notable:
        raise SelectionAuditError("notable continuation rejections are missing")
    rejected_set = set(rejected_indices)
    for item in notable:
        if not isinstance(item, dict) or set(item) != {
            "pool_indices",
            "candidate",
            "reason_code",
            "reason",
        }:
            raise SelectionAuditError("notable continuation rejection is malformed")
        indices = item["pool_indices"]
        if (
            not isinstance(indices, list)
            or not indices
            or any(index not in rejected_set for index in indices)
            or item["reason_code"] not in _REJECTION_CODES
            or not isinstance(item.get("reason"), str)
            or len(item["reason"]) < 40
        ):
            raise SelectionAuditError("notable continuation rejection is invalid")

    suite = _load(pathlib.Path(suite_manifest), "D1 suite manifest")
    try:
        headline_ids = [item["id"] for item in suite["headline_skills"]]
    except (KeyError, TypeError) as error:
        raise SelectionAuditError("D1 suite headline partition is malformed") from error
    if headline_ids[:22] != historical_ids or headline_ids[22:] != selected_ids:
        raise SelectionAuditError(
            "D1 suite order does not equal historical boundary plus continuation"
        )
    for skill_id in selected_ids:
        directory = root / "skills" / skill_id
        if not (directory / "SKILL.md").is_file() or not (
            directory / "PROVENANCE.md"
        ).is_file():
            raise SelectionAuditError(
                f"selected continuation skill is not prepared: {skill_id}"
            )

    return {
        "schema": "d1-continuation-validation/1",
        "audit_hash": canonical_json_hash(audit),
        "pool_hash": expected_pool_hash,
        "historical_headline": 22,
        "selected": 8,
        "selected_ids": selected_ids,
        "screened_rows": stop_index + 1,
        "stop_pool_index": stop_index,
        "rejection_counts": {
            group["reason_code"]: len(group["pool_indices"]) for group in groups
        },
    }

