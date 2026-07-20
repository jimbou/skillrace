import json
from pathlib import Path

from skillrace_next.pipeline.stages import validate_nl_checks
from skillrace_next.records import TestCase as CaseRecord
from skillrace_next.storage import file_hash, tree_hash
from skillrace_next.part2_study import (
    PART2_SCENARIOS,
    prepare_part2_study,
    verify_part2_study,
)


def _write_source_suite(root: Path) -> dict[str, str]:
    before: dict[str, str] = {}
    for scenario_id in PART2_SCENARIOS:
        scenario = root / "scenarios" / scenario_id
        (scenario / "campaign").mkdir(parents=True)
        rubric_label = "rubric" if scenario_id == "csv-stats" else "Rubric"
        (scenario / "scenario.md").write_text(
            f"# Scenario: {scenario_id}\n\n**Target purpose.** Build it.\n\n"
            f"**{rubric_label}:**\n- Produce the requested artifact.\n\n"
            "**Contingency:** medium.\n",
            encoding="utf-8",
        )
        (scenario / "campaign" / "properties.json").write_text(
            json.dumps(
                [
                    {"id": "behavior", "reads": "state", "nl": "Behavior works."},
                    {"id": "trace", "reads": "trace", "nl": "Agent verified it."},
                    {
                        "id": "integrity",
                        "reads": "state+trace",
                        "nl": "Supplied files remain intact.",
                    },
                ]
            ),
            encoding="utf-8",
        )
        for index in range(1, 11):
            test = scenario / "tests" / f"t{index}"
            checks = test / "checks"
            evidence = test / "oracle" / "evidence"
            checks.mkdir(parents=True)
            evidence.mkdir(parents=True)
            candidate = test / "candidate.json"
            dockerfile = test / "Dockerfile"
            check = checks / "behavior.sh"
            candidate.write_text(
                json.dumps(
                    {
                        "skill": scenario_id,
                        "base_image": "fixture:test",
                        "prompt": f"Create result-{index}.txt with the requested value.",
                    }
                ),
                encoding="utf-8",
            )
            dockerfile.write_text("FROM fixture:test\nWORKDIR /workspace\n", encoding="utf-8")
            check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            contract = f"contract-{scenario_id}-{index}"
            (test / "test.json").write_text(
                json.dumps(
                    {
                        "schema": "skillrace-hidden-test/1",
                        "test_id": f"{scenario_id}/t{index}",
                        "candidate_sha256": file_hash(candidate),
                        "dockerfile_sha256": file_hash(dockerfile),
                        "contract_identity_sha256": contract,
                        "criteria": [
                            {
                                "id": "behavior",
                                "script": "checks/behavior.sh",
                                "script_sha256": file_hash(check),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (evidence / "validation.json").write_text(
                json.dumps(
                    {
                        "schema": "skillrace-oracle-evidence/1",
                        "test_id": f"{scenario_id}/t{index}",
                        "state": "validated",
                        "contract_identity_sha256": contract,
                        "reference_passed": True,
                        "starting_rejected": True,
                        "negative_oracles_passed": True,
                        "survivors": [],
                    }
                ),
                encoding="utf-8",
            )
        before[scenario_id] = tree_hash(scenario)
    return before


def test_prepare_part2_study_freezes_all_ten_scenarios_and_100_tests(
    tmp_path: Path,
) -> None:
    before = _write_source_suite(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "part2"

    manifest_path = prepare_part2_study(tmp_path, output)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "skillrace-part2-selection/1"
    assert [item["scenario_id"] for item in manifest["scenarios"]] == list(
        PART2_SCENARIOS
    )
    assert manifest["heldout_test_count"] == 100
    for scenario_item in manifest["scenarios"]:
        scenario_id = scenario_item["scenario_id"]
        assert tree_hash(tmp_path / "scenarios" / scenario_id) == before[scenario_id]
        assert len(scenario_item["heldout_tests"]) == 10
        scenario_copy = output / scenario_item["scenario_path"]
        assert scenario_copy.read_text(encoding="utf-8").startswith("# Scenario:")
        for heldout_item in scenario_item["heldout_tests"]:
            record_path = output / heldout_item["record_path"]
            assert file_hash(record_path) == heldout_item["record_hash"]
            raw = json.loads(record_path.read_text(encoding="utf-8"))
            case = CaseRecord.from_dict(raw)
            test_root = record_path.parent
            properties = validate_nl_checks(test_root / case.nl_check_path)
            assert [item["description"] for item in properties] == [
                "Behavior works.",
                "Supplied files remain intact.",
            ]
            assert case.prompt_hash == file_hash(test_root / case.prompt_path)
            assert case.environment_hash == tree_hash(
                test_root / case.environment_directory
            )
            sanity = json.loads(
                (
                    test_root / case.environment_directory / "sanity.json"
                ).read_text(encoding="utf-8")
            )
            assert sanity["status"] == "pass"
            assert sanity["source"] == "validated-oracle-evidence"
            assert case.nl_check_hash == file_hash(test_root / case.nl_check_path)
            receipt = json.loads(
                (test_root / case.proposal_receipt).read_text(encoding="utf-8")
            )
            assert file_hash(test_root / case.proposal_receipt) == heldout_item[
                "receipt_hash"
            ]
            assert receipt["schema"] == "skillrace-part2-heldout-receipt/1"
            assert receipt["oracle_audit"]["decision"] == "accepted"
            assert file_hash(test_root / "source-checks" / "behavior.sh") == (
                receipt["source_checks"][0]["hash"]
            )
    assert verify_part2_study(manifest_path) == 100


def test_verify_part2_study_rejects_changed_frozen_prompt(tmp_path: Path) -> None:
    _write_source_suite(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "part2"
    manifest_path = prepare_part2_study(tmp_path, output)
    prompt = output / PART2_SCENARIOS[0] / "heldout" / "t1" / "prompt.txt"
    prompt.write_text("changed\n", encoding="utf-8")

    try:
        verify_part2_study(manifest_path)
    except ValueError as exc:
        assert "prompt hash mismatch" in str(exc)
    else:
        raise AssertionError("changed frozen prompt was accepted")


def test_verify_part2_study_rejects_changed_frozen_oracle_receipt(
    tmp_path: Path,
) -> None:
    _write_source_suite(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "part2"
    manifest_path = prepare_part2_study(tmp_path, output)
    oracle = output / PART2_SCENARIOS[0] / "heldout" / "t1" / "oracle-validation.json"
    oracle.write_text("{}\n", encoding="utf-8")

    try:
        verify_part2_study(manifest_path)
    except ValueError as exc:
        assert "frozen provenance hash mismatch" in str(exc)
    else:
        raise AssertionError("changed frozen oracle receipt was accepted")
