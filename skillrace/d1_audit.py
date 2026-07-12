"""Validate and fingerprint the public-skill RQ1 suite.

The headline boundary is intentionally mechanical: every prepared skill with an
external provenance record is included, while every prepared in-repository skill
without one is development-only.  This prevents later results from influencing the
selection.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from .input_identity import skill_input_tree_hash
from .io_utils import canonical_json_hash
from .third_party_audit import ThirdPartyValidationError, validate_third_party_manifest


_ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTINGENCY = {"low", "medium", "high"}
_TOP_FIELDS = {
    "schema",
    "suite_id",
    "status",
    "selection_rule",
    "headline_skills",
    "excluded_public",
    "development_only",
}
_SKILL_FIELDS = {
    "id",
    "family",
    "contingency",
    "base_image",
    "input_tree_hash",
    "base_image_id",
}


class SuiteValidationError(ValueError):
    """The suite is incomplete, ambiguous, or inconsistent with the repository."""


def _read_json(path: pathlib.Path, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SuiteValidationError(f"cannot read {label} at {path}: {error}") from error


def _docker_image_id(image: str) -> str:
    process = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode != 0:
        detail = (process.stdout + process.stderr).strip()
        raise SuiteValidationError(
            f"base image is unavailable for {image}: {detail[-300:]}"
        )
    image_id = process.stdout.strip()
    if not _IMAGE_ID.fullmatch(image_id):
        raise SuiteValidationError(f"Docker returned malformed image identity for {image}")
    return image_id


def _prepared_skill_ids(skills_root: pathlib.Path) -> set[str]:
    return {
        directory.name
        for directory in skills_root.iterdir()
        if directory.is_dir()
        and (directory / "properties.json").is_file()
        and (directory / "applicability.json").is_file()
    }


def _validate_properties(skill_id: str, directory: pathlib.Path) -> list[str]:
    properties = _read_json(directory / "properties.json", f"{skill_id} properties")
    if not isinstance(properties, list) or not properties:
        raise SuiteValidationError(f"{skill_id} properties must be a nonempty list")
    identifiers: list[str] = []
    for item in properties:
        if not isinstance(item, dict) or set(item) != {"id", "reads", "nl"}:
            raise SuiteValidationError(f"{skill_id} has a malformed property")
        identifier = item["id"]
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise SuiteValidationError(f"{skill_id} has an invalid property id")
        if item["reads"] not in {"state", "trace"}:
            raise SuiteValidationError(f"{skill_id}/{identifier} has invalid reads")
        if not isinstance(item["nl"], str) or len(item["nl"].strip()) < 20:
            raise SuiteValidationError(f"{skill_id}/{identifier} has an empty property")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise SuiteValidationError(f"{skill_id} has duplicate property ids")
    return identifiers


def _validate_skill_files(
    record: Mapping[str, Any], directory: pathlib.Path, *, require_provenance: bool
) -> str:
    skill_id = record["id"]
    required = ["SKILL.md", "Containerfile.base", "properties.json", "applicability.json"]
    if require_provenance:
        required.append("PROVENANCE.md")
    for name in required:
        if not (directory / name).is_file():
            raise SuiteValidationError(f"{skill_id} is missing required {name}")
    if require_provenance:
        provenance = (directory / "PROVENANCE.md").read_text()
        if "https://" not in provenance and "http://" not in provenance:
            raise SuiteValidationError(f"{skill_id} PROVENANCE lacks a source URL")

    property_ids = _validate_properties(skill_id, directory)
    applicability = _read_json(
        directory / "applicability.json", f"{skill_id} applicability"
    )
    if not isinstance(applicability, dict):
        raise SuiteValidationError(f"{skill_id} applicability must be an object")
    expected = {
        "skill": skill_id,
        "contingency": record["contingency"],
        "property_ids": property_ids,
    }
    for field, value in expected.items():
        if applicability.get(field) != value:
            raise SuiteValidationError(
                f"{skill_id} applicability {field} does not match the suite/property order"
            )
    invariants = applicability.get("fixed_invariants")
    if not isinstance(invariants, list) or not invariants:
        raise SuiteValidationError(f"{skill_id} has no fixed invariant allowlist")
    return skill_input_tree_hash(directory)


def validate_suite(
    manifest: str | pathlib.Path,
    *,
    repo_root: str | pathlib.Path,
    require_images: bool = False,
    image_inspector: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Validate selection, local inputs, and optionally Docker image availability."""
    manifest_path = pathlib.Path(manifest)
    root = pathlib.Path(repo_root)
    data = _read_json(manifest_path, "D1 suite manifest")
    if not isinstance(data, dict) or set(data) != _TOP_FIELDS:
        raise SuiteValidationError("D1 suite manifest has unsupported fields")
    if data.get("schema") != "d1-suite/1":
        raise SuiteValidationError("unsupported D1 suite schema")
    status = data.get("status")
    if status not in {"draft", "frozen"}:
        raise SuiteValidationError("D1 suite status must be draft or frozen")
    suite_id = data.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id.strip():
        raise SuiteValidationError("D1 suite_id must be nonempty")
    if status == "frozen" and suite_id.endswith("-draft"):
        raise SuiteValidationError("frozen D1 suite_id cannot end in -draft")
    if not isinstance(data.get("selection_rule"), str) or len(data["selection_rule"]) < 40:
        raise SuiteValidationError("D1 selection rule is missing")

    headline = data.get("headline_skills")
    excluded = data.get("excluded_public")
    development = data.get("development_only")
    if not isinstance(headline, list) or not headline:
        raise SuiteValidationError("headline_skills must be a nonempty list")
    if not isinstance(development, list):
        raise SuiteValidationError("development_only must be a list")
    if not isinstance(excluded, list):
        raise SuiteValidationError("excluded_public must be a list")

    excluded_ids: list[str] = []
    for record in excluded:
        if (
            not isinstance(record, dict)
            or set(record) != {"id", "reason", "license"}
            or not isinstance(record.get("id"), str)
            or not _ID.fullmatch(record["id"])
            or not isinstance(record.get("reason"), str)
            or len(record["reason"].strip()) < 30
            or not isinstance(record.get("license"), str)
            or not record["license"].strip()
        ):
            raise SuiteValidationError("malformed excluded-public record")
        excluded_ids.append(record["id"])
    if len(set(excluded_ids)) != len(excluded_ids):
        raise SuiteValidationError("duplicate excluded-public skill id")

    development_ids: list[str] = []
    for record in development:
        if (
            not isinstance(record, dict)
            or set(record) != {"id", "reason"}
            or not isinstance(record.get("id"), str)
            or not _ID.fullmatch(record["id"])
            or not isinstance(record.get("reason"), str)
            or len(record["reason"].strip()) < 20
        ):
            raise SuiteValidationError("malformed development-only record")
        development_ids.append(record["id"])
    if len(set(development_ids)) != len(development_ids):
        raise SuiteValidationError("duplicate development-only skill id")

    raw_headline_ids = [
        record.get("id") for record in headline if isinstance(record, dict)
    ]
    overlap = (
        (set(raw_headline_ids) & set(development_ids))
        | (set(raw_headline_ids) & set(excluded_ids))
        | (set(development_ids) & set(excluded_ids))
    )
    if overlap:
        raise SuiteValidationError(
            f"skill appears in multiple suite partitions: {sorted(overlap)[0]}"
        )

    headline_ids: list[str] = []
    families: set[str] = set()
    content_hashes: dict[str, str] = {}
    resolved_images: dict[str, str] = {}
    inspector = image_inspector or _docker_image_id
    skills_root = root / "skills"
    for record in headline:
        if not isinstance(record, dict) or not set(record).issubset(_SKILL_FIELDS):
            raise SuiteValidationError("malformed headline skill record")
        required = {"id", "family", "contingency", "base_image"}
        if not required.issubset(record):
            raise SuiteValidationError("headline skill record is missing required fields")
        skill_id = record["id"]
        family = record["family"]
        if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
            raise SuiteValidationError("headline skill id is invalid")
        if not isinstance(family, str) or not _ID.fullmatch(family):
            raise SuiteValidationError(f"{skill_id} family is invalid")
        if record["contingency"] not in _CONTINGENCY:
            raise SuiteValidationError(f"{skill_id} contingency is invalid")
        expected_image = f"skillrace/{skill_id}:base"
        if record["base_image"] != expected_image:
            raise SuiteValidationError(f"{skill_id} base image is not canonical")
        tree_hash = _validate_skill_files(
            record, skills_root / skill_id, require_provenance=True
        )
        content_hashes[skill_id] = tree_hash
        if status == "frozen":
            if not _SHA256.fullmatch(str(record.get("input_tree_hash", ""))):
                raise SuiteValidationError(f"{skill_id} is missing frozen input_tree_hash")
            if record["input_tree_hash"] != tree_hash:
                raise SuiteValidationError(f"{skill_id} frozen input_tree_hash drifted")
            if not _IMAGE_ID.fullmatch(str(record.get("base_image_id", ""))):
                raise SuiteValidationError(f"{skill_id} is missing frozen base_image_id")
        if require_images:
            image_id = inspector(expected_image)
            resolved_images[skill_id] = image_id
            if status == "frozen" and image_id != record["base_image_id"]:
                raise SuiteValidationError(f"{skill_id} frozen base image drifted")
        headline_ids.append(skill_id)
        families.add(family)

    if len(set(headline_ids)) != len(headline_ids):
        raise SuiteValidationError("duplicate headline skill id")

    prepared = _prepared_skill_ids(skills_root)
    externally_sourced = {
        skill_id
        for skill_id in prepared
        if (skills_root / skill_id / "PROVENANCE.md").is_file()
    }
    if set(headline_ids) != externally_sourced:
        missing = sorted(externally_sourced - set(headline_ids))
        extra = sorted(set(headline_ids) - externally_sourced)
        raise SuiteValidationError(
            "headline must equal all locally distributed provenance-backed "
            f"prepared skills; missing={missing}, extra={extra}"
        )
    redistributed_exclusions = sorted(
        skill_id for skill_id in excluded_ids if (skills_root / skill_id).exists()
    )
    if redistributed_exclusions:
        raise SuiteValidationError(
            "license-excluded public skill content must not ship in the artifact: "
            f"{redistributed_exclusions}"
        )
    expected_development = prepared - externally_sourced
    if set(development_ids) != expected_development:
        raise SuiteValidationError(
            "development-only list must enumerate every prepared non-public skill"
        )

    try:
        third_party = validate_third_party_manifest(
            root / "experiments/manifests/third-party-skills.json",
            suite_manifest=manifest_path,
            repo_root=root,
        )
    except ThirdPartyValidationError as error:
        raise SuiteValidationError(f"third-party provenance invalid: {error}") from error

    return {
        "schema": "d1-suite-validation/1",
        "suite_id": suite_id,
        "status": status,
        "suite_hash": canonical_json_hash(data),
        "headline_skills": len(headline_ids),
        "excluded_public": len(excluded_ids),
        "development_only": len(development_ids),
        "families": len(families),
        "contingency": {
            value: sum(item["contingency"] == value for item in headline)
            for value in sorted(_CONTINGENCY)
        },
        "content_hashes": content_hashes,
        "image_check": "passed" if require_images else "not-requested",
        "resolved_images": resolved_images,
        "missing_images": [],
        "third_party_records": third_party["records"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path("."))
    parser.add_argument("--require-images", action="store_true")
    args = parser.parse_args(argv)
    report = validate_suite(
        args.manifest,
        repo_root=args.repo_root,
        require_images=args.require_images,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
