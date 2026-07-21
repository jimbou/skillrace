import json
from pathlib import Path
from typing import Any

from .pipeline.stages import validate_nl_checks
from .storage import atomic_write_json, file_hash, tree_hash


SELECTION_RULE = (
    "Include skills evaluable as self-contained local coding tasks under Docker with "
    "no external network/service or unavailable repository dependency."
)

SELECTED_PART1_SKILLS = (
    "argparse-scaffolder",
    "build-python-cli",
    "cli-subcommand-validator",
    "code-refactor-fowler",
    "compiler-hardening",
    "condition-based-waiting",
    "csv-workbench",
    "data-transform",
    "debugging-difficult-bugs",
    "fastapi-endpoint",
    "file-check",
    "fix-failing-test",
    "frontent-design",
    "js-feature",
    "json-parser",
    "log-parser",
    "network-config-validation",
    "parser-generator",
    "refactor-complexity-reduce",
    "refactor",
    "regex-expert",
    "sql-queries",
    "sql-query-generator",
    "sqlmodel-orm",
    "systematic-debugging",
    "test-driven-development",
    "unit-test-generation",
    "unit-test-generator",
    "validator-agent",
    "yaml-config",
)

EXCLUDED_PART1_SKILLS = {
    "hello": "smoke-test skill rather than a substantive coding task",
    "cli-argparse-fix": "depends on unavailable Plan Marshall context",
    "finishing-a-development-branch": "repository lifecycle operation",
    "using-git-worktrees": "repository lifecycle operation",
    "mcp-server-patterns": "depends on external evolving SDKs and documentation",
    "rest-api-caller": "requires an external network service",
    "sql-query-json": "requires external database or package setup",
}

_AUTHORED_PROPERTIES = {
    "file-check": (
        "The requested file exists at the exact requested path and has exactly the "
        "requested text content, including whitespace and line endings when specified.",
        "The agent reads the created file back from disk before reporting success and "
        "corrects any mismatch it observes.",
        "The agent does not create unrelated files or alter supplied files that the task "
        "does not ask it to change.",
    ),
    "js-feature": (
        "The requested JavaScript behavior is implemented completely, including stated "
        "edge cases and error behavior.",
        "A focused test using Node built-ins exercises every stated requirement without "
        "weakening or deleting supplied checks.",
        "The implementation uses no newly installed packages and the relevant Node test "
        "command passes.",
    ),
}

_AUTHORED_PROPERTY_READS = {
    "file-check": ("state", "trace", "state"),
    "js-feature": ("state", "state+trace", "state+trace"),
}


def _normalize_properties(
    skill_id: str, source_path: Path
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if source_path.is_file():
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{source_path} must contain a nonempty list")
        descriptions: list[str] = []
        source_ids: list[str] = []
        reads_values: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"{source_path} contains a non-object property")
            source_id = item.get("id")
            reads = item.get("reads")
            description = item.get("nl")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError(f"{source_path} contains a malformed property id")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"{source_path} contains an empty property description")
            if not isinstance(reads, str) or not reads.strip():
                raise ValueError(f"{source_path} contains a malformed reads field")
            source_ids.append(source_id)
            reads_values.append(reads)
            descriptions.append(description)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError(f"{source_path} contains duplicate property ids")
        source = {
            "kind": "existing-skill-properties",
            "path": source_path.as_posix(),
            "hash": file_hash(source_path),
            "property_ids": source_ids,
        }
    else:
        descriptions = list(_AUTHORED_PROPERTIES.get(skill_id, ()))
        if not descriptions:
            raise ValueError(f"properties are missing for {skill_id}")
        reads_values = list(_AUTHORED_PROPERTY_READS[skill_id])
        source_ids = [f"{skill_id}-P{index}" for index in range(1, len(descriptions) + 1)]
        source = {"kind": "study-authored", "property_ids": source_ids}
    normalized = [
        {"property_id": f"P{index}", "description": description}
        for index, description in enumerate(descriptions, start=1)
    ]
    source["mappings"] = [
        {
            "source_id": source_id,
            "reads": reads,
            "property_id": f"P{index}",
        }
        for index, (source_id, reads) in enumerate(
            zip(source_ids, reads_values, strict=True), start=1
        )
    ]
    return normalized, source


def prepare_part1_study(repo_root: str | Path, output_root: str | Path) -> Path:
    repo = Path(repo_root)
    output = Path(output_root)
    if output.exists():
        raise FileExistsError(f"Part I study output already exists: {output}")

    prepared: list[tuple[str, Path, list[dict[str, str]], dict[str, Any]]] = []
    for skill_id in SELECTED_PART1_SKILLS:
        source = repo / "skills" / skill_id
        if not (source / "SKILL.md").is_file():
            raise ValueError(f"selected S0 is missing SKILL.md: {source}")
        properties, property_source = _normalize_properties(
            skill_id, source / "properties.json"
        )
        if "path" in property_source:
            property_source["path"] = (
                source / "properties.json"
            ).relative_to(repo).as_posix()
        prepared.append((skill_id, source, properties, property_source))

    selected: list[dict[str, Any]] = []
    output.mkdir(parents=True)
    for rank, (skill_id, source, properties, property_source) in enumerate(
        prepared, start=1
    ):
        relative_source = source.relative_to(repo).as_posix()
        skill_output = output / skill_id
        properties_path = skill_output / "properties.json"
        receipt_path = skill_output / "s0-receipt.json"
        atomic_write_json(properties_path, properties)
        validate_nl_checks(properties_path)
        receipt = {
            "schema": "skillrace-part1-s0-receipt/1",
            "skill_id": skill_id,
            "selection_rank": rank,
            "source_directory": relative_source,
            "skill_tree_hash": tree_hash(source),
            "skill_md_path": f"{relative_source}/SKILL.md",
            "skill_md_hash": file_hash(source / "SKILL.md"),
            "property_source": property_source,
            "properties_path": f"{skill_id}/properties.json",
            "properties_hash": file_hash(properties_path),
        }
        atomic_write_json(receipt_path, receipt)
        selected.append(
            {
                "rank": rank,
                "skill_id": skill_id,
                "source_directory": relative_source,
                "receipt_path": f"{skill_id}/s0-receipt.json",
                "properties_path": f"{skill_id}/properties.json",
            }
        )

    manifest_path = output / "selection.json"
    atomic_write_json(
        manifest_path,
        {
            "schema": "skillrace-part1-selection/1",
            "selection_rule": SELECTION_RULE,
            "source_root": "skills",
            "selected_count": len(selected),
            "selected": selected,
            "excluded": [
                {"skill_id": skill_id, "reason": reason}
                for skill_id, reason in EXCLUDED_PART1_SKILLS.items()
            ],
        },
    )
    return manifest_path


def verify_part1_study(repo_root: str | Path, manifest_path: str | Path) -> int:
    repo = Path(repo_root)
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema") != "skillrace-part1-selection/1":
        raise ValueError("Part I selection schema is invalid")
    selected = manifest.get("selected")
    if not isinstance(selected, list) or len(selected) != 30:
        raise ValueError("Part I selection must contain exactly 30 skills")
    if [item.get("skill_id") for item in selected] != list(SELECTED_PART1_SKILLS):
        raise ValueError("Part I manifest does not match the approved ordered selection")

    for rank, item in enumerate(selected, start=1):
        if item.get("rank") != rank:
            raise ValueError("Part I selection ranks are invalid")
        source = repo / item["source_directory"]
        receipt_path = manifest_file.parent / item["receipt_path"]
        properties_path = manifest_file.parent / item["properties_path"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("schema") != "skillrace-part1-s0-receipt/1":
            raise ValueError("S0 receipt schema is invalid")
        if receipt.get("skill_id") != item.get("skill_id"):
            raise ValueError("S0 receipt skill ID mismatch")
        if tree_hash(source) != receipt.get("skill_tree_hash"):
            raise ValueError(f"S0 tree hash mismatch for {item['skill_id']}")
        if file_hash(source / "SKILL.md") != receipt.get("skill_md_hash"):
            raise ValueError(f"S0 SKILL.md hash mismatch for {item['skill_id']}")
        validate_nl_checks(properties_path)
        if file_hash(properties_path) != receipt.get("properties_hash"):
            raise ValueError(f"property hash mismatch for {item['skill_id']}")
    return len(selected)
