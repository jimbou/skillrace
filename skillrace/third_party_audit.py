"""Validate the pinned provenance and redistribution boundary for public skills."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_DISPOSITIONS = {"headline", "excluded"}
_FIDELITY = {"exact", "abridged"}
_UNSAFE_LICENSES = {"noassertion", "proprietary", "unknown", "unlicensed"}
_LICENSE_MARKERS = {
    "MIT": "MIT License",
    "Apache-2.0": "Apache License",
    "AGPL-3.0-only": "GNU AFFERO GENERAL PUBLIC LICENSE",
    "CC0-1.0": "CC0",
    "FSL-1.1-ALv2": "Functional Source License",
}
_TOP_FIELDS = {"schema", "recorded_at", "records"}
_RECORD_FIELDS = {
    "id",
    "disposition",
    "source_repo",
    "source_commit",
    "source_path",
    "source_url",
    "skill_sha256",
    "fidelity",
    "license",
    "license_evidence",
}


class ThirdPartyValidationError(ValueError):
    """A third-party source is unpinned, misclassified, or locally drifted."""


def _load(path: pathlib.Path, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ThirdPartyValidationError(f"cannot read {label} at {path}: {error}") from error


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_license(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in _UNSAFE_LICENSES or normalized.startswith("proprietary")


def validate_third_party_manifest(
    manifest: str | pathlib.Path,
    *,
    suite_manifest: str | pathlib.Path,
    repo_root: str | pathlib.Path,
) -> dict[str, int | str]:
    """Bind every public skill to a source commit, license, and local byte hash."""

    path = pathlib.Path(manifest)
    root = pathlib.Path(repo_root)
    data = _load(path, "third-party manifest")
    if not isinstance(data, dict) or set(data) != _TOP_FIELDS:
        raise ThirdPartyValidationError("third-party manifest has unsupported fields")
    if data.get("schema") != "third-party-skills/1":
        raise ThirdPartyValidationError("unsupported third-party manifest schema")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("recorded_at", ""))):
        raise ThirdPartyValidationError("third-party recorded_at must be an ISO date")
    records = data.get("records")
    if not isinstance(records, list) or not records:
        raise ThirdPartyValidationError("third-party records must be a nonempty list")

    suite = _load(pathlib.Path(suite_manifest), "D1 suite manifest")
    try:
        headline_ids = {item["id"] for item in suite["headline_skills"]}
        excluded_ids = {item["id"] for item in suite["excluded_public"]}
    except (KeyError, TypeError) as error:
        raise ThirdPartyValidationError("D1 suite partitions are malformed") from error

    observed: set[str] = set()
    licensed_repositories: set[str] = set()
    counts = {"headline": 0, "excluded": 0, "exact": 0, "abridged": 0}
    last_id = ""
    for record in records:
        if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
            raise ThirdPartyValidationError("malformed third-party record")
        skill_id = record["id"]
        if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
            raise ThirdPartyValidationError("invalid third-party skill id")
        if skill_id in observed:
            raise ThirdPartyValidationError(f"duplicate third-party skill id: {skill_id}")
        if last_id and skill_id <= last_id:
            raise ThirdPartyValidationError("third-party records must be sorted by id")
        observed.add(skill_id)
        last_id = skill_id

        disposition = record["disposition"]
        if disposition not in _DISPOSITIONS:
            raise ThirdPartyValidationError(f"{skill_id} has invalid disposition")
        expected_ids = headline_ids if disposition == "headline" else excluded_ids
        if skill_id not in expected_ids:
            raise ThirdPartyValidationError(
                f"{skill_id} disposition disagrees with the D1 suite partition"
            )

        repository = record["source_repo"]
        commit = record["source_commit"]
        source_path = record["source_path"]
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise ThirdPartyValidationError(f"{skill_id} has invalid source_repo")
        if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
            raise ThirdPartyValidationError(f"{skill_id} has invalid source_commit")
        if (
            not isinstance(source_path, str)
            or not source_path.endswith("SKILL.md")
            or source_path.startswith("/")
            or ".." in pathlib.PurePosixPath(source_path).parts
        ):
            raise ThirdPartyValidationError(f"{skill_id} has unsafe source_path")
        expected_url = f"https://github.com/{repository}/blob/{commit}/{source_path}"
        if record["source_url"] != expected_url:
            raise ThirdPartyValidationError(
                f"{skill_id} source_url is not the commit-pinned source_url"
            )

        evidence = record["license_evidence"]
        evidence_prefixes = (
            f"https://github.com/{repository}/blob/{commit}/",
            f"https://github.com/{repository}/tree/{commit}",
        )
        if not isinstance(evidence, str) or not evidence.startswith(evidence_prefixes):
            raise ThirdPartyValidationError(
                f"{skill_id} license_evidence is not commit-pinned"
            )
        license_name = record["license"]
        if not isinstance(license_name, str) or not license_name.strip():
            raise ThirdPartyValidationError(f"{skill_id} has empty license")
        if disposition == "headline" and _unsafe_license(license_name):
            raise ThirdPartyValidationError(
                f"{skill_id} has an unsafe license for the headline partition"
            )
        if disposition == "headline":
            licensed_repositories.add(repository)
            license_copy = (
                root
                / "licenses"
                / "third-party"
                / f"{repository.replace('/', '--')}.txt"
            )
            if not license_copy.is_file() or license_copy.is_symlink():
                raise ThirdPartyValidationError(
                    f"{skill_id} has no regular embedded upstream license copy"
                )
            license_text = license_copy.read_text(encoding="utf-8")
            marker = _LICENSE_MARKERS.get(license_name)
            if marker is None or marker not in license_text:
                raise ThirdPartyValidationError(
                    f"{skill_id} embedded license does not match {license_name}"
                )

        fidelity = record["fidelity"]
        if fidelity not in _FIDELITY:
            raise ThirdPartyValidationError(f"{skill_id} has invalid fidelity")
        expected_hash = record["skill_sha256"]
        if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
            raise ThirdPartyValidationError(f"{skill_id} has invalid skill_sha256")
        local_directory = root / "skills" / skill_id
        local_skill = local_directory / "SKILL.md"
        if disposition == "excluded":
            if local_directory.exists():
                raise ThirdPartyValidationError(
                    f"{skill_id} excluded unsafe content is still redistributed locally"
                )
        else:
            if not local_skill.is_file() or not (
                local_skill.parent / "PROVENANCE.md"
            ).is_file():
                raise ThirdPartyValidationError(
                    f"{skill_id} local public skill is incomplete"
                )
            if _sha256(local_skill) != expected_hash:
                raise ThirdPartyValidationError(f"{skill_id} local SKILL.md drifted")

        counts[disposition] += 1
        counts[fidelity] += 1

    expected_public = headline_ids | excluded_ids
    if observed != expected_public:
        raise ThirdPartyValidationError(
            "third-party records must exactly equal headline plus excluded public skills"
        )
    embedded = {
        path.stem.replace("--", "/", 1)
        for path in (root / "licenses" / "third-party").glob("*.txt")
    }
    if embedded != licensed_repositories:
        raise ThirdPartyValidationError(
            "embedded third-party license files do not exactly match headline sources"
        )
    return {
        "schema": "third-party-skills-validation/1",
        "records": len(records),
        **counts,
        "embedded_licenses": len(embedded),
    }
