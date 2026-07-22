from dataclasses import replace
import hashlib
import json
from pathlib import Path

from skillrace_next.methods import skillrace
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import tree_hash
from tests_next.unit.test_test_cases import config_for


PROPERTIES = [
    {"property_id": "P1", "description": "The requested artifact is correct."},
    {"property_id": "P2", "description": "The agent verifies the result."},
]


def edge_id(source: str, target: str) -> str:
    digest = hashlib.sha256(f"{source}\0{target}".encode()).hexdigest()
    return "edge-" + digest[:16]


def long_observed_tree(length: int = 140) -> dict[str, object]:
    nodes: list[dict[str, object]] = [
        {
            "node_id": "root",
            "purpose": "root",
            "outcome": "root",
            "member_run_ids": [],
            "member_episode_ids": [],
            "reach_status": "reached",
            "failure_ids": [],
        }
    ]
    edges: list[dict[str, str]] = []
    previous = "root"
    for index in range(length):
        node_id = f"node-{index:03d}"
        nodes.append(
            {
                "node_id": node_id,
                "purpose": f"Perform observed development episode {index}",
                "outcome": (
                    "The tool existed only at /opt/tool/bin/tool and required discovery"
                    if index == 91
                    else f"Observed episode {index} completed"
                ),
                "member_run_ids": [f"run-{index // 5:02d}"],
                "member_episode_ids": [f"episode-{index:03d}"],
                "reach_status": "reached",
                "failure_ids": ["failure-path-assumption"] if index == 91 else [],
            }
        )
        edges.append(
            {
                "source_node_id": previous,
                "target_node_id": node_id,
                "reason": (
                    "Assume the required executable is at /usr/bin/tool"
                    if index == 91
                    else f"Continue through observed transition {index}"
                ),
            }
        )
        previous = node_id
    return {
        "schema": "skillrace-reasoning-tree/1",
        "nodes": nodes,
        "edges": edges,
    }


def fixture_skill(tmp_path: Path) -> SkillVersion:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Tool workflow\nInspect the environment, create the artifact, and verify it.\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "skill-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    return SkillVersion(
        skill_id="tool-workflow",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=receipt,
    )


def pi_result(request: PiRequest, response: str) -> PiResult:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    trace = request.output_dir / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": response}],
                },
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


def valid_mutation() -> dict[str, str]:
    return {
        "bug_hypothesis": "The skill assumes a fixed executable path.",
        "mutation": "Place the executable at a recoverable nonstandard path.",
        "why_patchable": "Path discovery guidance makes the task finishable.",
        "prompt": "Create /workspace/result.txt with the local tool and verify it.",
        "dockerfile": (
            "FROM skillrace-next/task-fixture:test\n"
            "RUN mkdir -p /opt/tool/bin && ln -s /bin/printf /opt/tool/bin/tool\n"
            "WORKDIR /workspace\n"
        ),
    }


def valid_case(case: CaseRecord, config: object) -> CaseRecord:
    return replace(
        case,
        validation_status="valid",
        validation_diagnostic="validated",
        container_image_id="sha256:fixture",
    )


def test_compact_index_and_branch_isolation_cover_a_long_observed_tree() -> None:
    tree = long_observed_tree()

    index = skillrace.build_edge_index(tree)
    selected = edge_id("node-090", "node-091")
    branch = skillrace.isolate_branch(tree, selected)

    assert len(index) == 139
    assert set(index[0]) == {
        "edge_id",
        "source",
        "reasoning",
        "target",
        "outcome",
        "observations",
        "failures",
    }
    assert next(item for item in index if item["edge_id"] == selected)["failures"] == 1
    assert branch["target_edge"]["edge_id"] == selected
    assert branch["path"][0]["node_id"] == "root"
    assert branch["path"][-1]["node_id"] == "node-091"
    assert len(branch["path"]) == 93
    assert len(json.dumps(index)) < len(json.dumps(tree))


def test_pi_agent_selects_one_observed_edge_then_returns_patchable_mutation(
    tmp_path: Path,
) -> None:
    tree = long_observed_tree()
    selected = edge_id("node-090", "node-091")
    requests: list[PiRequest] = []

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        response = (
            {
                "target_edge_id": selected,
                "selection_reason": (
                    "The fixed executable path is brittle and has a local recovery route."
                ),
            }
            if ".select." in request.operation_id
            else {
                "bug_hypothesis": (
                    "The skill lacks guidance for executable path discovery."
                ),
                "mutation": (
                    "Place the executable at a discoverable nonstandard path."
                ),
                "why_patchable": (
                    "The executable remains local and the agent can find it "
                    "within the fixed budget."
                ),
                "prompt": (
                    "Create /workspace/result.txt by running the available "
                    "tool and verify the exact output."
                ),
                "dockerfile": (
                    "FROM skillrace-next/task-fixture:test\n"
                    "RUN mkdir -p /opt/tool/bin && printf '#!/bin/sh\\nprintf ok\\n' "
                    "> /opt/tool/bin/tool && chmod +x /opt/tool/bin/tool\n"
                    "WORKDIR /workspace\n"
                ),
            }
        )
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                    "text": "Selection complete.\n\n```json\n"
                                + json.dumps(response)
                                + "\n```",
                            }
                        ],
                    },
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

    def validator(case: CaseRecord, config: object) -> CaseRecord:
        return replace(
            case,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:fixture",
        )

    proposed = skillrace.propose_test(
        tree,
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(
            config_for(tmp_path),
            provider="lab",
            model_id="deepseek-v4-flash",
            role_budgets={"proposer": 6},
        ),
        pi_runner=proposal_pi,
        validator=validator,
    )

    assert proposed.validation_status == "valid"
    assert len(requests) == 2
    selector_request, mutator_request = requests
    assert selector_request.allowed_tools == ()
    assert selector_request.mounts == ()
    selector_prompt = selector_request.prompt_path.read_text(encoding="utf-8")
    assert "COMPACT EDGE INDEX" in selector_prompt
    assert selected in selector_prompt
    assert "genuine, patchable skill failure" in selector_prompt
    assert mutator_request.allowed_tools == ()
    assert mutator_request.mounts == ()
    mutator_prompt = mutator_request.prompt_path.read_text(encoding="utf-8")
    assert "ISOLATED OBSERVED BRANCH" in mutator_prompt
    assert selected in mutator_prompt
    assert "local recovery route" in mutator_prompt
    assert "exact target edge" in mutator_prompt.lower()
    assert "COMPACT EDGE INDEX" not in mutator_prompt
    assert "must not download or install" in mutator_prompt
    assert "local files or symlinks" in mutator_prompt
    assert "must exist when the task container starts" in mutator_prompt
    assert "must not remove, move, or disable" in mutator_prompt
    assert "must make the selected edge assumption fail" in mutator_prompt
    assert "must not reveal the recovery path" in mutator_prompt
    assert "quoted here-document" in mutator_prompt
    assert "4 pi turns and 180 seconds" in mutator_prompt.lower()
    assert "go, rust/cargo, ruby, jq" in mutator_prompt.lower()
    assert "at most 600 characters" in mutator_prompt.lower()
    assert "at most 2 kib" in mutator_prompt.lower()
    assert "at most 8 kib" in mutator_prompt.lower()
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    selector_root = Path(receipt["selector_input_path"])
    assert len(json.loads((selector_root / "edge-index.json").read_text())) == 139
    selected_branch = selector_root / "selected-branch.json"
    assert json.loads(selected_branch.read_text())["target_edge"]["edge_id"] == selected
    assert receipt["target_edge_id"] == selected
    assert receipt["selection_reason"].startswith("The fixed executable path")
    assert receipt["bug_hypothesis"].startswith("The skill lacks")
    assert receipt["mutation"].startswith("Place the executable")
    assert receipt["why_patchable"].startswith("The executable remains")
    assert receipt["selector_input_hash"] == tree_hash(selector_root)
    assert receipt["selector_pi_receipt_path"] == str(selector_request.output_dir / "receipt.json")
    assert receipt["pi_receipt_path"] == str(mutator_request.output_dir / "receipt.json")


def test_edge_selector_allows_two_corrections_without_rerunning_mutator(
    tmp_path: Path,
) -> None:
    tree = long_observed_tree()
    selected = edge_id("node-090", "node-091")
    requests: list[PiRequest] = []
    selector_responses = [
        "not json",
        json.dumps(
            {
                "target_edge_id": "edge-unknown",
                "selection_reason": "A brittle assumption.",
            }
        ),
        json.dumps(
            {
                "target_edge_id": selected,
                "selection_reason": "A recoverable path assumption.",
            }
        ),
    ]

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        response = (
            selector_responses.pop(0)
            if ".select." in request.operation_id
            else json.dumps(valid_mutation())
        )
        return pi_result(request, response)

    proposed = skillrace.propose_test(
        tree,
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 6}),
        pi_runner=proposal_pi,
        validator=valid_case,
    )

    assert proposed.validation_status == "valid"
    selector_requests = [item for item in requests if ".select." in item.operation_id]
    mutator_requests = [item for item in requests if ".mutate." in item.operation_id]
    assert len(selector_requests) == 3
    assert len(mutator_requests) == 1
    assert "previous response was invalid" in selector_requests[1].prompt_path.read_text(
        encoding="utf-8"
    ).lower()
    assert "unknown edge" in selector_requests[2].prompt_path.read_text(
        encoding="utf-8"
    )


def test_mutator_allows_two_structural_corrections_without_rerunning_selector(
    tmp_path: Path,
) -> None:
    tree = long_observed_tree()
    selected = edge_id("node-090", "node-091")
    requests: list[PiRequest] = []
    invalid_with_inner_fences = {
        **valid_mutation(),
        "prompt": "Write this example:\n```text\nok\n```\nto /workspace/result.txt.",
        "unexpected": "extra field",
    }
    invalid_dockerfile = {
        **valid_mutation(),
        "dockerfile": "FROM wrong:image\nWORKDIR /workspace\n",
    }
    mutator_responses = [
        "```json\n" + json.dumps(invalid_with_inner_fences) + "\n```",
        json.dumps(invalid_dockerfile),
        json.dumps(valid_mutation()),
    ]

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        response = (
            json.dumps(
                {
                    "target_edge_id": selected,
                    "selection_reason": "A recoverable path assumption.",
                }
            )
            if ".select." in request.operation_id
            else mutator_responses.pop(0)
        )
        return pi_result(request, response)

    proposed = skillrace.propose_test(
        tree,
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 6}),
        pi_runner=proposal_pi,
        validator=valid_case,
    )

    assert proposed.validation_status == "valid"
    selector_requests = [item for item in requests if ".select." in item.operation_id]
    mutator_requests = [item for item in requests if ".mutate." in item.operation_id]
    assert len(selector_requests) == 1
    assert len(mutator_requests) == 3
    assert "response is invalid" in mutator_requests[1].prompt_path.read_text(
        encoding="utf-8"
    )
    assert "must start with" in mutator_requests[2].prompt_path.read_text(
        encoding="utf-8"
    ).lower()


def test_mutator_corrects_failed_generated_test_validation(tmp_path: Path) -> None:
    tree = long_observed_tree()
    selected = edge_id("node-090", "node-091")
    requests: list[PiRequest] = []
    validation_calls = 0

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        response = (
            {
                "target_edge_id": selected,
                "selection_reason": "A recoverable path assumption.",
            }
            if ".select." in request.operation_id
            else valid_mutation()
        )
        return pi_result(request, json.dumps(response))

    def validator(case: CaseRecord, config: object) -> CaseRecord:
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

    proposed = skillrace.propose_test(
        tree,
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 6}),
        pi_runner=proposal_pi,
        validator=validator,
    )

    assert proposed.validation_status == "valid"
    assert validation_calls == 3
    selector_requests = [item for item in requests if ".select." in item.operation_id]
    mutator_requests = [item for item in requests if ".mutate." in item.operation_id]
    assert len(selector_requests) == 1
    assert len(mutator_requests) == 3
    assert "Docker build failed" in mutator_requests[1].prompt_path.read_text(
        encoding="utf-8"
    )
    assert "Docker build failed" in mutator_requests[2].prompt_path.read_text(
        encoding="utf-8"
    )


def test_mutator_corrects_a_visible_prompt_that_reveals_the_recovery_path(
    tmp_path: Path,
) -> None:
    tree = long_observed_tree()
    selected = edge_id("node-090", "node-091")
    requests: list[PiRequest] = []
    revealed = {
        **valid_mutation(),
        "mutation": "Relocate the helper to /usr/local/lib/helpdesk/bin/reportgen.",
        "prompt": (
            "Create /workspace/result.txt and verify it with "
            "/usr/local/lib/helpdesk/bin/reportgen."
        ),
    }
    responses = [revealed, valid_mutation()]

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        response = (
            {
                "target_edge_id": selected,
                "selection_reason": "A recoverable path assumption.",
            }
            if ".select." in request.operation_id
            else responses.pop(0)
        )
        return pi_result(request, json.dumps(response))

    proposed = skillrace.propose_test(
        tree,
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 6}),
        pi_runner=proposal_pi,
        validator=valid_case,
    )

    assert proposed.validation_status == "valid"
    selector_requests = [item for item in requests if ".select." in item.operation_id]
    mutator_requests = [item for item in requests if ".mutate." in item.operation_id]
    assert len(selector_requests) == 1
    assert len(mutator_requests) == 2
    assert "visible prompt reveals the mutation's recovery path" in (
        mutator_requests[1].prompt_path.read_text(encoding="utf-8")
    )


def test_mutator_corrects_an_oversized_response(tmp_path: Path) -> None:
    tree = long_observed_tree()
    selected = edge_id("node-090", "node-091")
    requests: list[PiRequest] = []
    responses = [
        {**valid_mutation(), "bug_hypothesis": "x" * 601},
        valid_mutation(),
    ]

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        response = (
            {
                "target_edge_id": selected,
                "selection_reason": "A recoverable path assumption.",
            }
            if ".select." in request.operation_id
            else responses.pop(0)
        )
        return pi_result(request, json.dumps(response))

    proposed = skillrace.propose_test(
        tree,
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 6}),
        pi_runner=proposal_pi,
        validator=valid_case,
    )

    assert proposed.validation_status == "valid"
    selector_requests = [item for item in requests if ".select." in item.operation_id]
    mutator_requests = [item for item in requests if ".mutate." in item.operation_id]
    assert len(selector_requests) == 1
    assert len(mutator_requests) == 2
    assert "bug_hypothesis exceeds 600 characters" in (
        mutator_requests[1].prompt_path.read_text(encoding="utf-8")
    )
