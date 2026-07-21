import json
from pathlib import Path
import shutil
from typing import Any

from .pipeline.stages import validate_nl_checks
from .records import TestCase
from .storage import atomic_write_json, file_hash, tree_hash


PART2_SCENARIOS = (
    "argparse-cli",
    "config-parser",
    "csv-stats",
    "fix-failing-test",
    "interval-merge",
    "json-csv",
    "log-parser",
    "regex-validate",
    "sqlite-query",
    "text-template",
)

SELECTION_RULE = (
    "Use the repository's complete D2 suite: self-contained local coding scenarios "
    "with ten independently authored held-out Docker tests and validated reference, "
    "starting-state, and assigned-negative oracle evidence."
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _heldout_properties(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} must contain a nonempty property list")
    checks: list[dict[str, str]] = []
    included_ids: list[str] = []
    excluded_ids: list[str] = []
    all_ids: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{path} contains a non-object property")
        source_id = item.get("id")
        reads = item.get("reads")
        description = item.get("nl")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"{path} contains a malformed property id")
        if not isinstance(reads, str) or not reads.strip():
            raise ValueError(f"{path} contains a malformed reads field")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{path} contains an empty property description")
        all_ids.append(source_id)
        if "state" in reads.split("+"):
            included_ids.append(source_id)
            checks.append(
                {
                    "property_id": f"P{len(checks) + 1}",
                    "description": description,
                }
            )
        else:
            excluded_ids.append(source_id)
    if len(set(all_ids)) != len(all_ids):
        raise ValueError(f"{path} contains duplicate property ids")
    if not checks:
        raise ValueError(f"{path} has no artifact-readable held-out properties")
    return checks, {
        "path": path.as_posix(),
        "hash": file_hash(path),
        "included_property_ids": included_ids,
        "excluded_trace_only_property_ids": excluded_ids,
    }


def _development_properties(
    path: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} must contain a nonempty property list")
    checks: list[dict[str, str]] = []
    mappings: list[dict[str, str]] = []
    source_ids: list[str] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path} contains a non-object property")
        source_id = item.get("id")
        reads = item.get("reads")
        description = item.get("nl")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"{path} contains a malformed property id")
        if not isinstance(reads, str) or not reads.strip():
            raise ValueError(f"{path} contains a malformed reads field")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{path} contains an empty property description")
        source_ids.append(source_id)
        property_id = f"P{index}"
        checks.append({"property_id": property_id, "description": description})
        mappings.append(
            {"source_id": source_id, "reads": reads, "property_id": property_id}
        )
    if len(set(source_ids)) != len(source_ids):
        raise ValueError(f"{path} contains duplicate property ids")
    return checks, mappings


def _audit_test(repo: Path, scenario_id: str, test_id: str) -> dict[str, Any]:
    source = repo / "scenarios" / scenario_id / "tests" / test_id
    candidate_path = source / "candidate.json"
    dockerfile_path = source / "Dockerfile"
    contract_path = source / "test.json"
    oracle_path = source / "oracle" / "evidence" / "validation.json"
    for path in (candidate_path, dockerfile_path, contract_path, oracle_path):
        if not path.is_file():
            raise ValueError(f"held-out source file is missing: {path}")

    candidate = _json(candidate_path)
    contract = _json(contract_path)
    oracle = _json(oracle_path)
    qualified_id = f"{scenario_id}/{test_id}"
    if candidate.get("skill") != scenario_id:
        raise ValueError(f"candidate skill mismatch for {qualified_id}")
    if not isinstance(candidate.get("prompt"), str) or not candidate["prompt"].strip():
        raise ValueError(f"candidate prompt is empty for {qualified_id}")
    if contract.get("schema") != "skillrace-hidden-test/1":
        raise ValueError(f"hidden-test schema mismatch for {qualified_id}")
    if contract.get("test_id") != qualified_id:
        raise ValueError(f"hidden-test ID mismatch for {qualified_id}")
    if contract.get("candidate_sha256") != file_hash(candidate_path):
        raise ValueError(f"candidate hash mismatch for {qualified_id}")
    if contract.get("dockerfile_sha256") != file_hash(dockerfile_path):
        raise ValueError(f"Dockerfile hash mismatch for {qualified_id}")

    criteria = contract.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError(f"held-out criteria are missing for {qualified_id}")
    source_checks: list[dict[str, str]] = []
    declared_paths: set[str] = set()
    for criterion in criteria:
        script_name = criterion.get("script")
        if not isinstance(script_name, str) or not script_name.startswith("checks/"):
            raise ValueError(f"unsafe checker path for {qualified_id}")
        script_path = source / script_name
        if not script_path.is_file() or script_path.parent != source / "checks":
            raise ValueError(f"checker is missing for {qualified_id}: {script_name}")
        digest = file_hash(script_path)
        if criterion.get("script_sha256") != digest:
            raise ValueError(f"checker hash mismatch for {qualified_id}: {script_name}")
        if script_name in declared_paths:
            raise ValueError(f"duplicate checker for {qualified_id}: {script_name}")
        declared_paths.add(script_name)
        source_checks.append(
            {
                "criterion_id": criterion["id"],
                "source_path": script_name,
                "hash": digest,
            }
        )
    actual_checks = {
        path.relative_to(source).as_posix()
        for path in (source / "checks").iterdir()
        if path.is_file()
    }
    if actual_checks != declared_paths:
        raise ValueError(f"declared checker set mismatch for {qualified_id}")

    if oracle.get("schema") != "skillrace-oracle-evidence/1":
        raise ValueError(f"oracle schema mismatch for {qualified_id}")
    if oracle.get("test_id") != qualified_id:
        raise ValueError(f"oracle test ID mismatch for {qualified_id}")
    if oracle.get("contract_identity_sha256") != contract.get(
        "contract_identity_sha256"
    ):
        raise ValueError(f"oracle contract mismatch for {qualified_id}")
    required = (
        oracle.get("state") == "validated"
        and oracle.get("reference_passed") is True
        and oracle.get("starting_rejected") is True
        and oracle.get("negative_oracles_passed") is True
        and oracle.get("survivors") == []
    )
    if not required:
        raise ValueError(f"oracle evidence is not strong enough for {qualified_id}")
    return {
        "source": source,
        "candidate_path": candidate_path,
        "candidate": candidate,
        "dockerfile_path": dockerfile_path,
        "contract_path": contract_path,
        "contract": contract,
        "oracle_path": oracle_path,
        "oracle": oracle,
        "source_checks": source_checks,
    }


def prepare_part2_study(repo_root: str | Path, output_root: str | Path) -> Path:
    repo = Path(repo_root)
    output = Path(output_root)
    if output.exists():
        raise FileExistsError(f"Part II study output already exists: {output}")

    prepared: list[dict[str, Any]] = []
    for scenario_id in PART2_SCENARIOS:
        source = repo / "scenarios" / scenario_id
        scenario_path = source / "scenario.md"
        properties_path = source / "campaign" / "properties.json"
        scenario_text = scenario_path.read_text(encoding="utf-8")
        if (
            "target purpose" not in scenario_text.lower()
            or "rubric" not in scenario_text.lower()
        ):
            raise ValueError(f"public scenario is underspecified: {scenario_id}")
        properties, property_source = _heldout_properties(properties_path)
        development_properties, development_mappings = _development_properties(
            properties_path
        )
        property_source["path"] = properties_path.relative_to(repo).as_posix()
        tests = [
            _audit_test(repo, scenario_id, f"t{index}")
            for index in range(1, 11)
        ]
        prepared.append(
            {
                "scenario_id": scenario_id,
                "source": source,
                "scenario_path": scenario_path,
                "properties": properties,
                "property_source": property_source,
                "development_properties": development_properties,
                "development_mappings": development_mappings,
                "development_source_path": properties_path.relative_to(repo).as_posix(),
                "development_source_hash": file_hash(properties_path),
                "tests": tests,
            }
        )

    output.mkdir(parents=True)
    scenario_records: list[dict[str, Any]] = []
    for scenario_data in prepared:
        scenario_id = scenario_data["scenario_id"]
        scenario_output = output / scenario_id
        copied_scenario = scenario_output / "scenario.md"
        copied_scenario.parent.mkdir(parents=True)
        shutil.copyfile(scenario_data["scenario_path"], copied_scenario)
        development_path = scenario_output / "development-properties.json"
        development_receipt_path = (
            scenario_output / "development-properties-receipt.json"
        )
        atomic_write_json(development_path, scenario_data["development_properties"])
        validate_nl_checks(development_path)
        atomic_write_json(
            development_receipt_path,
            {
                "schema": "skillrace-part2-development-properties-receipt/1",
                "scenario_id": scenario_id,
                "source_path": scenario_data["development_source_path"],
                "source_hash": scenario_data["development_source_hash"],
                "prepared_path": f"{scenario_id}/development-properties.json",
                "prepared_hash": file_hash(development_path),
                "mappings": scenario_data["development_mappings"],
            },
        )
        heldout_records: list[dict[str, str]] = []
        for index, audit in enumerate(scenario_data["tests"], start=1):
            test_id = f"t{index}"
            test_output = scenario_output / "heldout" / test_id
            environment = test_output / "environment"
            source_checks = test_output / "source-checks"
            environment.mkdir(parents=True)
            source_checks.mkdir()
            prompt_path = test_output / "prompt.txt"
            checks_path = test_output / "nl-checks.json"
            receipt_path = test_output / "source-receipt.json"
            contract_copy = test_output / "source-test.json"
            candidate_copy = test_output / "source-candidate.json"
            oracle_copy = test_output / "oracle-validation.json"
            prompt_path.write_text(audit["candidate"]["prompt"] + "\n", encoding="utf-8")
            shutil.copyfile(audit["dockerfile_path"], environment / "Dockerfile")
            atomic_write_json(
                environment / "sanity.json",
                {
                    "schema": "skillrace-environment-sanity/1",
                    "status": "pass",
                    "source": "validated-oracle-evidence",
                    "source_oracle_validation_hash": file_hash(audit["oracle_path"]),
                    "contract_identity_sha256": audit["contract"].get(
                        "contract_identity_sha256"
                    ),
                },
            )
            shutil.copyfile(audit["contract_path"], contract_copy)
            shutil.copyfile(audit["candidate_path"], candidate_copy)
            shutil.copyfile(audit["oracle_path"], oracle_copy)
            copied_checks: list[dict[str, str]] = []
            for source_check in audit["source_checks"]:
                name = Path(source_check["source_path"]).name
                copied = source_checks / name
                shutil.copyfile(audit["source"] / source_check["source_path"], copied)
                copied_checks.append(
                    {
                        **source_check,
                        "prepared_path": f"source-checks/{name}",
                        "prepared_hash": file_hash(copied),
                    }
                )
            atomic_write_json(checks_path, scenario_data["properties"])
            validate_nl_checks(checks_path)
            receipt = {
                "schema": "skillrace-part2-heldout-receipt/1",
                "test_id": f"{scenario_id}/{test_id}",
                "source_directory": audit["source"].relative_to(repo).as_posix(),
                "source_test_hash": file_hash(audit["contract_path"]),
                "source_candidate_hash": file_hash(audit["candidate_path"]),
                "source_dockerfile_hash": file_hash(audit["dockerfile_path"]),
                "source_checks": copied_checks,
                "property_source": scenario_data["property_source"],
                "oracle_audit": {
                    "decision": "accepted",
                    "validation_hash": file_hash(audit["oracle_path"]),
                    "contract_identity_sha256": audit["contract"].get(
                        "contract_identity_sha256"
                    ),
                    "reference_passed": True,
                    "starting_rejected": True,
                    "negative_oracles_passed": True,
                    "survivors": [],
                },
                "prepared_hashes": {
                    "prompt": file_hash(prompt_path),
                    "environment": tree_hash(environment),
                    "nl_checks": file_hash(checks_path),
                    "source_test": file_hash(contract_copy),
                    "source_candidate": file_hash(candidate_copy),
                    "oracle_validation": file_hash(oracle_copy),
                },
            }
            atomic_write_json(receipt_path, receipt)
            case = TestCase(
                test_id=f"{scenario_id}/{test_id}",
                prompt_path=Path("prompt.txt"),
                prompt_hash=file_hash(prompt_path),
                environment_directory=Path("environment"),
                environment_hash=tree_hash(environment),
                nl_check_path=Path("nl-checks.json"),
                nl_check_hash=file_hash(checks_path),
                origin_method="heldout",
                proposal_receipt=Path("source-receipt.json"),
                validation_status="pending",
                validation_diagnostic="",
                container_image_id="",
            )
            record_path = test_output / "test-case.json"
            atomic_write_json(record_path, case.to_dict())
            heldout_records.append(
                {
                    "test_id": case.test_id,
                    "record_path": record_path.relative_to(output).as_posix(),
                    "record_hash": file_hash(record_path),
                    "receipt_hash": file_hash(receipt_path),
                }
            )
        scenario_records.append(
            {
                "scenario_id": scenario_id,
                "source_directory": scenario_data["source"].relative_to(repo).as_posix(),
                "source_scenario_hash": file_hash(scenario_data["scenario_path"]),
                "scenario_path": copied_scenario.relative_to(output).as_posix(),
                "scenario_hash": file_hash(copied_scenario),
                "development_properties_path": development_path.relative_to(
                    output
                ).as_posix(),
                "development_properties_hash": file_hash(development_path),
                "development_properties_receipt_path": (
                    development_receipt_path.relative_to(output).as_posix()
                ),
                "development_properties_receipt_hash": file_hash(
                    development_receipt_path
                ),
                "heldout_tests": heldout_records,
            }
        )

    manifest_path = output / "selection.json"
    atomic_write_json(
        manifest_path,
        {
            "schema": "skillrace-part2-selection/1",
            "selection_rule": SELECTION_RULE,
            "scenario_count": len(scenario_records),
            "heldout_test_count": sum(
                len(item["heldout_tests"]) for item in scenario_records
            ),
            "heldout_property_policy": (
                "Include existing scenario properties whose reads field contains state; "
                "exclude trace-only properties from final artifact scoring."
            ),
            "scenarios": scenario_records,
        },
    )
    return manifest_path


def verify_part2_study(manifest_path: str | Path) -> int:
    manifest_file = Path(manifest_path)
    output = manifest_file.parent
    manifest = _json(manifest_file)
    if manifest.get("schema") != "skillrace-part2-selection/1":
        raise ValueError("Part II selection schema is invalid")
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("Part II scenarios must be a list")
    if [item.get("scenario_id") for item in scenarios] != list(PART2_SCENARIOS):
        raise ValueError("Part II manifest does not match the fixed scenario selection")

    count = 0
    for scenario in scenarios:
        scenario_path = output / scenario["scenario_path"]
        if file_hash(scenario_path) != scenario.get("scenario_hash"):
            raise ValueError(f"scenario hash mismatch for {scenario['scenario_id']}")
        development_path = output / scenario["development_properties_path"]
        if file_hash(development_path) != scenario.get("development_properties_hash"):
            raise ValueError(
                f"development property hash mismatch for {scenario['scenario_id']}"
            )
        validate_nl_checks(development_path)
        development_receipt_path = output / scenario[
            "development_properties_receipt_path"
        ]
        if file_hash(development_receipt_path) != scenario.get(
            "development_properties_receipt_hash"
        ):
            raise ValueError(
                f"development property receipt mismatch for {scenario['scenario_id']}"
            )
        development_receipt = _json(development_receipt_path)
        if (
            development_receipt.get("schema")
            != "skillrace-part2-development-properties-receipt/1"
            or development_receipt.get("scenario_id") != scenario["scenario_id"]
            or development_receipt.get("prepared_hash")
            != scenario["development_properties_hash"]
        ):
            raise ValueError(
                f"development property receipt invalid for {scenario['scenario_id']}"
            )
        heldout = scenario.get("heldout_tests")
        if not isinstance(heldout, list) or len(heldout) != 10:
            raise ValueError(f"held-out count mismatch for {scenario['scenario_id']}")
        expected_ids = [f"{scenario['scenario_id']}/t{index}" for index in range(1, 11)]
        if [item.get("test_id") for item in heldout] != expected_ids:
            raise ValueError(f"held-out order mismatch for {scenario['scenario_id']}")
        for item in heldout:
            record_path = output / item["record_path"]
            if file_hash(record_path) != item.get("record_hash"):
                raise ValueError(f"test-case hash mismatch for {item['test_id']}")
            case = TestCase.from_dict(_json(record_path))
            test_root = record_path.parent
            prompt = test_root / case.prompt_path
            environment = test_root / case.environment_directory
            checks = test_root / case.nl_check_path
            receipt_path = test_root / case.proposal_receipt
            if file_hash(prompt) != case.prompt_hash:
                raise ValueError(f"prompt hash mismatch for {case.test_id}")
            if tree_hash(environment) != case.environment_hash:
                raise ValueError(f"environment hash mismatch for {case.test_id}")
            validate_nl_checks(checks)
            if file_hash(checks) != case.nl_check_hash:
                raise ValueError(f"NL-check hash mismatch for {case.test_id}")
            if file_hash(receipt_path) != item.get("receipt_hash"):
                raise ValueError(f"receipt hash mismatch for {case.test_id}")
            receipt = _json(receipt_path)
            if receipt.get("schema") != "skillrace-part2-heldout-receipt/1":
                raise ValueError(f"receipt schema mismatch for {case.test_id}")
            if receipt.get("test_id") != case.test_id:
                raise ValueError(f"receipt test ID mismatch for {case.test_id}")
            if receipt.get("oracle_audit", {}).get("decision") != "accepted":
                raise ValueError(f"oracle audit rejected {case.test_id}")
            prepared_hashes = receipt.get("prepared_hashes", {})
            provenance_files = {
                "source_test": test_root / "source-test.json",
                "source_candidate": test_root / "source-candidate.json",
                "oracle_validation": test_root / "oracle-validation.json",
            }
            for name, provenance_path in provenance_files.items():
                if file_hash(provenance_path) != prepared_hashes.get(name):
                    raise ValueError(
                        f"frozen provenance hash mismatch for {case.test_id}: {name}"
                    )
            for source_check in receipt.get("source_checks", []):
                copied = test_root / source_check["prepared_path"]
                if file_hash(copied) != source_check.get("prepared_hash"):
                    raise ValueError(f"source-check hash mismatch for {case.test_id}")
            count += 1
    if count != 100 or manifest.get("heldout_test_count") != 100:
        raise ValueError("Part II bundle must contain exactly 100 held-out tests")
    return count
