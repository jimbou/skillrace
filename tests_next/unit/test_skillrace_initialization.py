import json
from pathlib import Path
from dataclasses import replace

from skillrace_next.methods import skillrace
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import file_hash, tree_hash
from tests_next.unit.test_test_cases import config_for


PROPERTIES = [
    {"property_id": "P1", "description": "The requested artifact is correct."},
    {"property_id": "P2", "description": "The agent verifies the result."},
]


def fixture_skill(tmp_path: Path) -> SkillVersion:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Exact artifacts\n", encoding="utf-8")
    receipt = tmp_path / "skill-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    return SkillVersion(
        skill_id="exact-artifacts",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=receipt,
    )


def plan_response() -> list[dict[str, str]]:
    return [
        {
            "task": f"Create exact artifact variant {index}.",
            "environment_conditions": f"Environment condition {index}.",
        }
        for index in range(1, 11)
    ]


def test_create_diversity_plan_freezes_exactly_ten_ordered_descriptions(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": json.dumps(plan_response())}
                        ],
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    result = skillrace.create_diversity_plan(
        fixture_skill(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "plan",
        pi_runner=fake_pi,
    )

    assert len(requests) == 1
    assert requests[0].temperature == 1.0
    prompt = requests[0].prompt_path.read_text(encoding="utf-8")
    assert json.dumps(PROPERTIES, sort_keys=True) in prompt
    assert "# Exact artifacts" in prompt
    assert "exactly ten" in prompt.lower()
    assert "do not emit property or check ids" in prompt.lower()
    assert "every description must be feasible" in prompt.lower()
    assert "recovery path" in prompt.lower()
    assert "no docker access" in prompt.lower()
    assert "no runtime network" in prompt.lower()
    assert "must not contain the requested solution" in prompt.lower()
    assert "small representative fixture" in prompt.lower()
    assert "4 pi turns and 180 seconds" in prompt.lower()
    assert "one focused behavior" in prompt.lower()
    assert "python 3, node.js, bash/posix coreutils, and perl" in prompt.lower()
    assert "go, rust/cargo, ruby, jq" in prompt.lower()
    assert result["schema"] == "skillrace-diversity-plan/1"
    assert [item["seed_id"] for item in result["descriptions"]] == [
        f"seed-{index:02d}" for index in range(1, 11)
    ]
    assert [item["task"] for item in result["descriptions"]] == [
        item["task"] for item in plan_response()
    ]
    plan_path = Path(result["plan_path"])
    receipt_path = Path(result["receipt_path"])
    assert result["plan_hash"] == file_hash(plan_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["plan_hash"] == result["plan_hash"]
    assert receipt["catalog_hash"] == result["catalog_hash"]
    assert receipt["description_count"] == 10
    assert receipt["model"] == "deepseek-v3.2"
    assert receipt["temperature"] == 1.0


def test_create_diversity_plan_states_workspace_artifact_rule_without_rejecting_routes(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    response = plan_response()
    response[0] = {
        "task": "Expose a /health HTTP route.",
        "environment_conditions": "A binary is available at /usr/bin/tool.",
    }

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": json.dumps(response)}],
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    result = skillrace.create_diversity_plan(
        fixture_skill(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "plan",
        pi_runner=fake_pi,
    )

    assert len(requests) == 1
    prompt = requests[0].prompt_path.read_text(encoding="utf-8")
    assert "requested artifact destinations under /workspace" in prompt
    assert "do not use /mnt/data or /tmp" in prompt.lower()
    assert result["descriptions"][0]["task"] == response[0]["task"]


def test_diversity_plan_corrects_a_description_that_cannot_be_materialized(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    invalid = plan_response()
    invalid[2] = {
        "task": "Write a cleanup command containing /tmp/cache into a script.",
        "environment_conditions": "The artifact destination is under /workspace.",
    }

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        response = invalid if len(requests) < 3 else plan_response()
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": json.dumps(response)}],
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    result = skillrace.create_diversity_plan(
        fixture_skill(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "plan",
        pi_runner=fake_pi,
    )

    assert len(requests) == 3
    assert "outside /workspace" in requests[1].prompt_path.read_text(encoding="utf-8")
    assert "outside /workspace" in requests[2].prompt_path.read_text(encoding="utf-8")
    assert result["descriptions"][2]["task"] == plan_response()[2]["task"]


def test_materialize_initial_test_binds_plan_description_and_full_catalog(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    skill = fixture_skill(tmp_path)
    config = config_for(tmp_path)

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        response = (
            plan_response()
            if ".plan." in request.operation_id
            else {
                "prompt": "Create /workspace/seed-one.txt with exact content one.",
                "dockerfile": (
                    "FROM skillrace-next/task-fixture:test\n"
                    "RUN printf 'fixture\\n' > /fixture.txt\n"
                    "WORKDIR /workspace\n"
                ),
            }
        )
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": json.dumps(response)}],
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    def validator(case: CaseRecord, received_config: object) -> CaseRecord:
        return replace(
            case,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:fixture",
        )

    plan = skillrace.create_diversity_plan(
        skill,
        PROPERTIES,
        config,
        tmp_path / "plan",
        pi_runner=fake_pi,
    )
    proposed = skillrace.materialize_initial_test(
        plan,
        0,
        skill,
        PROPERTIES,
        config,
        tmp_path / "seed-01",
        pi_runner=fake_pi,
        validator=validator,
    )

    assert len(requests) == 2
    assert requests[1].temperature == 1.0
    materialization_prompt = requests[1].prompt_path.read_text(encoding="utf-8")
    assert json.dumps(plan["descriptions"][0], sort_keys=True) in materialization_prompt
    assert json.dumps(PROPERTIES, sort_keys=True) in materialization_prompt
    assert "must not contradict" in materialization_prompt.lower()
    assert "runtime network" in materialization_prompt.lower()
    assert (
        "must not create or test the requested solution"
        in materialization_prompt.lower()
    )
    assert "generate it compactly" in materialization_prompt.lower()
    assert "4 pi turns and 180 seconds" in materialization_prompt.lower()
    assert "go, rust/cargo, ruby, jq" in materialization_prompt.lower()
    assert proposed.validation_status == "valid"
    assert proposed.origin_method == "skillrace"
    assert json.loads(proposed.nl_check_path.read_text(encoding="utf-8")) == PROPERTIES
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert receipt["phase"] == "initial_seed"
    assert receipt["seed_id"] == "seed-01"
    assert receipt["seed_index"] == 1
    assert receipt["plan_hash"] == plan["plan_hash"]
    assert receipt["description"] == plan["descriptions"][0]
    assert receipt["catalog_hash"] == proposed.nl_check_hash
    assert receipt["temperature"] == 1.0


def test_materialize_initial_test_allows_two_validation_corrections(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    validation_calls = 0
    skill = fixture_skill(tmp_path)
    config = config_for(tmp_path)

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        response = (
            plan_response()
            if ".plan." in request.operation_id
            else {
                "prompt": "Create /workspace/seed-one.txt with exact content one.",
                "dockerfile": (
                    "FROM skillrace-next/task-fixture:test\n"
                    "WORKDIR /workspace\n"
                ),
            }
        )
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": json.dumps(response)}],
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    def validator(case: CaseRecord, received_config: object) -> CaseRecord:
        nonlocal validation_calls
        validation_calls += 1
        return replace(
            case,
            validation_status="valid" if validation_calls == 3 else "invalid_test",
            validation_diagnostic=(
                "validated" if validation_calls == 3 else "Docker build failed"
            ),
            container_image_id=("sha256:fixture" if validation_calls == 3 else ""),
        )

    plan = skillrace.create_diversity_plan(
        skill,
        PROPERTIES,
        config,
        tmp_path / "plan",
        pi_runner=fake_pi,
    )
    proposed = skillrace.materialize_initial_test(
        plan,
        0,
        skill,
        PROPERTIES,
        config,
        tmp_path / "seed-01",
        pi_runner=fake_pi,
        validator=validator,
    )

    assert proposed.validation_status == "valid"
    assert validation_calls == 3
    assert len(requests) == 4
    assert "Docker build failed" in requests[3].prompt_path.read_text(encoding="utf-8")
    assert "recheck every dockerfile constraint" in requests[3].prompt_path.read_text(
        encoding="utf-8"
    ).lower()


def test_thrice_malformed_initial_test_is_returned_as_invalid_slot(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        response = (
            json.dumps(plan_response())
            if ".plan." in request.operation_id
            else "not json"
        )
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": response}],
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    skill = fixture_skill(tmp_path)
    config = config_for(tmp_path)
    plan = skillrace.create_diversity_plan(
        skill,
        PROPERTIES,
        config,
        tmp_path / "plan",
        pi_runner=fake_pi,
    )

    proposed = skillrace.materialize_initial_test(
        plan,
        0,
        skill,
        PROPERTIES,
        config,
        tmp_path / "seed-01",
        pi_runner=fake_pi,
    )

    assert len(requests) == 4
    assert "previous materialization was invalid" in requests[3].prompt_path.read_text(
        encoding="utf-8"
    )
    assert proposed.validation_status == "invalid_test"
    assert proposed.validation_diagnostic
    assert proposed.prompt_path.is_file()
    assert (proposed.environment_directory / "Dockerfile").is_file()
    assert json.loads(proposed.nl_check_path.read_text(encoding="utf-8")) == PROPERTIES
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert receipt["phase"] == "initial_seed"
    assert receipt["seed_id"] == "seed-01"
    assert receipt["status"] == "invalid_test"
    assert receipt["diagnostic"] == proposed.validation_diagnostic
