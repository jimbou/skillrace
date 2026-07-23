from __future__ import annotations

import hashlib
import json

import pytest

from skillrace.closeai import nonproduction_chat_fixture
from skillrace.io_utils import canonical_json_hash
from skillrace.repair_validation import (
    REPAIR_STATUSES,
    RQ1_REPAIR_EVIDENCE_MAX_BYTES,
    build_repair_evidence,
    make_model_patcher,
    make_replay_executor,
    select_failure_repairs,
)
from skillrace.revise_skill import package_hash
from skillrace.rq3_confirmation import failure_signature


def _signature(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _failed_attempt(
    ordinal: int,
    candidate_id: str,
    properties: tuple[str, ...],
    *,
    method: str = "skillrace",
) -> dict:
    signatures = {property_id: _signature(property_id) for property_id in properties}
    verdicts = [
        {
            "property_id": property_id,
            "holds": False,
            "violated": True,
            "detail": f"{property_id} failed mechanically",
        }
        for property_id in properties
    ]
    return {
        "execution_id": f"e{ordinal:04d}",
        "attempt_id": f"e{ordinal:04d}-a00",
        "consume_budget": True,
        "candidate_id": candidate_id,
        "case": f"cases/{candidate_id}",
        "run": f"runs/{candidate_id}",
        "runner_status": "completed",
        "oracle_status": "completed",
        "violated": list(properties),
        "provenance": {
            "task_nl": f"task for {candidate_id}",
            "env_nl": f"environment for {candidate_id}",
            "input_files": [
                {
                    "path": "inputs/request.json",
                    "sha256": _signature(f"input-{candidate_id}"),
                }
            ],
            "guard": "input shape differs",
            "mutation": "change the public input shape",
            "targeted_property": properties[0],
            "intended_branch": "validated-input branch",
            "observed_branch": "fallback branch",
            "reasoning_episodes": [
                {
                    "intent": "inspect input",
                    "reasoning": "The request shape may select the validation path.",
                    "tool_calls": [
                        {"name": "read", "arguments": {"path": "inputs/request.json"}}
                    ],
                    "tool_results": [
                        {"name": "read", "content": '{"mode":"strict"}'}
                    ],
                    "outcome": "wrong branch selected",
                }
            ],
            "tree_path": ["root", "inspect", "wrong-branch"],
        },
        "classification": {
            "branch_outcome": "different_new_branch",
            "targeting": "targeted",
        },
        "result": {
            "verdicts": verdicts,
            "failure_signatures": signatures,
            "workspace_diff_summary": "artifact contains the wrong value",
            "failed_artifact": {
                "path": "artifact/result.json",
                "sha256": _signature(f"artifact-{candidate_id}"),
                "representation": '{"actual":"wrong"}',
            },
            "executable_conditions": [
                {
                    "property_id": property_id,
                    "condition": f"validate.sh reports {property_id} as satisfied",
                }
                for property_id in properties
            ],
        },
        "method": method,
    }


def _campaign(method: str = "skillrace") -> dict:
    first = _failed_attempt(0, "candidate-a", ("p-shared",), method=method)
    second = _failed_attempt(1, "candidate-b", ("p-shared",), method=method)
    third = _failed_attempt(
        2, "candidate-c", ("p-other", "p-second"), method=method
    )
    successful = {
        "execution_id": "e0003",
        "attempt_id": "e0003-a00",
        "consume_budget": True,
        "candidate_id": "candidate-pass",
        "violated": [],
        "oracle_status": "completed",
    }
    rejected = {
        "execution_id": "e0004",
        "attempt_id": "e0004-a00",
        "consume_budget": False,
        "candidate_id": "candidate-rejected",
        "violated": ["p-rejected"],
    }
    inconclusive = {
        "execution_id": "e0005",
        "attempt_id": "e0005-a00",
        "consume_budget": True,
        "candidate_id": "candidate-unknown",
        "violated": [],
        "inconclusive": ["p-unknown"],
        "oracle_status": "inconclusive",
    }
    return {
        "schema": "campaign/2",
        "method": method,
        "complete": True,
        "counted_executions": 30,
        "attempts": [first, second, third, successful, rejected, inconclusive],
    }


def _skill(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Original skill\n", encoding="utf-8")
    return skill


def test_selects_every_raw_failed_execution_without_signature_deduplication(tmp_path):
    requests = select_failure_repairs(
        _campaign(),
        skill_name="demo",
        original_skill_dir=_skill(tmp_path),
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )

    assert [request.candidate_id for request in requests] == [
        "candidate-a",
        "candidate-b",
        "candidate-c",
    ]
    assert requests[0].failure_signatures == requests[1].failure_signatures
    assert len({request.repair_id for request in requests}) == 3


def test_multiple_property_failures_share_one_patch_and_replay(tmp_path):
    requests = select_failure_repairs(
        _campaign(),
        skill_name="demo",
        original_skill_dir=_skill(tmp_path),
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )

    request = requests[2]
    assert request.failed_property_ids == ("p-other", "p-second")
    assert request.failure_signatures == (
        _signature("p-other"),
        _signature("p-second"),
    )
    assert request.output_dir == tmp_path / "repairs" / request.repair_id


def test_selection_loads_definite_failure_from_linked_run_verdict_receipt(tmp_path):
    campaign = _campaign("random")
    attempt = campaign["attempts"][0]
    attempt["result"].pop("verdicts")
    attempt["result"].pop("failure_signatures")
    run = tmp_path / attempt["run"]
    run.mkdir(parents=True)
    linked = {
        "property_id": "p-shared",
        "holds": False,
        "violated": True,
        "detail": "linked mechanical failure",
    }
    (run / "verdicts.json").write_text(json.dumps([linked]), encoding="utf-8")

    requests = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=_skill(tmp_path),
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )

    assert requests[0].failed_property_ids == ("p-shared",)
    assert requests[0].failure_signatures == (failure_signature(linked),)


def test_selection_resolves_workspace_relative_paths_saved_by_manifest_driver(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "out" / "development" / "cell"
    root.mkdir(parents=True)
    campaign = _campaign("random")
    attempt = campaign["attempts"][0]
    attempt["result"].pop("verdicts")
    attempt["result"].pop("failure_signatures")
    attempt["case"] = "out/development/cell/cases/candidate-a"
    attempt["run"] = "out/development/cell/runs/candidate-a"
    (root / "cases" / "candidate-a").mkdir(parents=True)
    run = root / "runs" / "candidate-a"
    run.mkdir(parents=True)
    verdict = {
        "property_id": "p-shared",
        "holds": False,
        "violated": True,
        "detail": "workspace-relative linked failure",
    }
    (run / "verdicts.json").write_text(json.dumps([verdict]), encoding="utf-8")

    requests = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=_skill(tmp_path),
        campaign_root=root,
        output_root=root / "repairs",
        phase="public",
    )

    assert requests[0].run_dir == run
    assert requests[0].case_dir == root / "cases" / "candidate-a"


def test_hidden_failures_are_never_selected_for_repair(tmp_path):
    with pytest.raises(ValueError, match="hidden"):
        select_failure_repairs(
            _campaign(),
            skill_name="demo",
            original_skill_dir=_skill(tmp_path),
            campaign_root=tmp_path,
            output_root=tmp_path / "repairs",
            phase="hidden",
        )


def test_requests_bind_the_original_skill_and_not_a_prior_patch(tmp_path):
    original = _skill(tmp_path)
    requests = select_failure_repairs(
        _campaign(),
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )
    expected = package_hash(original)

    assert {request.original_skill_hash for request in requests} == {expected}
    assert all(request.original_skill_dir == original.resolve() for request in requests)


def test_skillrace_evidence_contains_native_reasoning_but_random_does_not(tmp_path):
    original = _skill(tmp_path)
    skillrace = select_failure_repairs(
        _campaign("skillrace"),
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "skillrace-repairs",
        phase="public",
    )[0]
    random = select_failure_repairs(
        _campaign("random"),
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "random-repairs",
        phase="public",
    )[0]

    rich = build_repair_evidence(_campaign("skillrace"), skillrace, max_bytes=3600)
    baseline = build_repair_evidence(_campaign("random"), random, max_bytes=3600)

    assert rich["failure_core"] == baseline["failure_core"]
    assert rich["method_evidence"]["reasoning_episodes"]
    episode = rich["method_evidence"]["reasoning_episodes"][0]
    assert episode["reasoning"] == "The request shape may select the validation path."
    assert episode["tool_calls"][0]["name"] == "read"
    assert episode["tool_results"][0]["content"] == '{"mode":"strict"}'
    assert rich["method_evidence"]["tree_path"]
    assert rich["method_evidence"]["guard_mutation"]
    assert rich["method_evidence"]["branch_evidence"] == {
        "intended_branch": "validated-input branch",
        "observed_branch": "fallback branch",
        "branch_outcome": "different_new_branch",
        "targeting": "targeted",
    }
    assert baseline["method_evidence"] == {
        "reasoning_episodes": [],
        "tree_path": [],
        "guard_mutation": {},
        "branch_evidence": {},
    }
    assert "method" not in rich["reviser_payload"]
    assert "producer" not in rich["reviser_payload"]
    assert '"skillrace"' not in json.dumps(rich["reviser_payload"])
    assert rich["accounting"]["used_bytes"] <= 3600
    assert baseline["accounting"]["used_bytes"] <= 3600
    assert rich["evidence_hash"] == canonical_json_hash(rich["reviser_payload"])


def test_skillrace_evidence_loads_saved_run_episodes_and_tool_trace(tmp_path):
    campaign = _campaign("skillrace")
    attempt = campaign["attempts"][0]
    attempt["provenance"].pop("reasoning_episodes")
    run = tmp_path / attempt["run"]
    (run / "raw").mkdir(parents=True)
    (run / "episodes.json").write_text(
        json.dumps(
            {
                "episodes": [
                    {
                        "index": 1,
                        "start_call": 1,
                        "end_call": 1,
                        "intent": "inspect generated artifact",
                        "what_it_did": "read the generated configuration",
                        "outcome": "the required field was absent",
                        "opening_reasoning": "I should inspect the produced file first.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    trace_rows = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "I should inspect the produced file first.",
                    },
                    {
                        "type": "toolCall",
                        "id": "call-1",
                        "name": "read",
                        "arguments": {"path": "/workspace/output.json"},
                    },
                ],
            }
        },
        {
            "message": {
                "role": "toolResult",
                "toolCallId": "call-1",
                "toolName": "read",
                "content": [{"type": "text", "text": '{"mode":"legacy"}'}],
                "isError": False,
            }
        },
    ]
    (run / "raw" / "session.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in trace_rows),
        encoding="utf-8",
    )
    original = _skill(tmp_path)
    request = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )[0]

    evidence = build_repair_evidence(campaign, request, max_bytes=12_000)

    episode = evidence["method_evidence"]["reasoning_episodes"][0]
    assert episode["intent"] == "inspect generated artifact"
    assert episode["reasoning"] == "I should inspect the produced file first."
    assert episode["tool_calls"] == [
        {
            "call_index": 1,
            "name": "read",
            "arguments": {"path": "/workspace/output.json"},
        }
    ]
    assert episode["tool_results"] == [
        {
            "call_index": 1,
            "name": "read",
            "content": '{"mode":"legacy"}',
            "is_error": False,
        }
    ]
    assert episode["tool_span"] == {"start_call": 1, "end_call": 1}
    assert episode["what_it_did"] == "read the generated configuration"
    assert evidence["failure_core"]["failures"][0]["checker_error"] == (
        "p-shared failed mechanically"
    )


def test_headline_budget_retains_all_compact_saved_episodes_with_large_trace_results(
    tmp_path,
):
    campaign = _campaign("skillrace")
    attempt = campaign["attempts"][0]
    attempt["provenance"].pop("reasoning_episodes")
    attempt["result"]["failed_artifact"]["representation"] = "artifact" * 300
    run = tmp_path / attempt["run"]
    (run / "raw").mkdir(parents=True)
    episodes = []
    trace_rows = []
    for index in range(1, 8):
        call_id = f"call-{index}"
        episodes.append(
            {
                "index": index,
                "start_call": index,
                "end_call": index,
                "intent": f"inspect phase {index}",
                "what_it_did": "read generated output " + ("detail " * 200),
                "outcome": "observed failure evidence " + ("result " * 200),
                "opening_reasoning": "reason carefully " * 300,
            }
        )
        trace_rows.extend(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "thought " * 500},
                            {
                                "type": "toolCall",
                                "id": call_id,
                                "name": "read",
                                "arguments": {"path": "/workspace/" + ("x" * 900)},
                            },
                        ],
                    }
                },
                {
                    "message": {
                        "role": "toolResult",
                        "toolCallId": call_id,
                        "toolName": "read",
                        "content": [{"type": "text", "text": "output " * 900}],
                        "isError": False,
                    }
                },
            ]
        )
    (run / "episodes.json").write_text(json.dumps({"episodes": episodes}))
    (run / "raw" / "session.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in trace_rows)
    )
    original = _skill(tmp_path)
    request = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )[0]

    evidence = build_repair_evidence(
        campaign, request, max_bytes=RQ1_REPAIR_EVIDENCE_MAX_BYTES
    )

    retained = evidence["method_evidence"]["reasoning_episodes"]
    assert len(retained) == 7
    assert evidence["accounting"]["used_bytes"] <= RQ1_REPAIR_EVIDENCE_MAX_BYTES
    assert retained[0]["tool_results"][0]["content"].count("truncated") == 1
    assert retained[0]["tool_calls"][0]["arguments"]["truncated"] is True


def test_common_repair_evidence_preserves_exact_failure_inputs_without_versions(tmp_path):
    campaign = _campaign("random")
    long_task = "Create an artifact with exact spacing: " + ("value  \n" * 45)
    long_environment = "Environment facts:\n" + ("- exact item\n" * 35)
    campaign["attempts"][0]["provenance"]["task_nl"] = long_task
    campaign["attempts"][0]["provenance"]["env_nl"] = long_environment
    request = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=_skill(tmp_path),
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )[0]

    evidence = build_repair_evidence(campaign, request, max_bytes=12_000)
    core = evidence["failure_core"]

    assert core["task"] == long_task
    assert core["environment"] == long_environment
    assert core["input_files"] == campaign["attempts"][0]["provenance"]["input_files"]
    assert core["failed_artifact"] == campaign["attempts"][0]["result"]["failed_artifact"]
    assert core["executable_conditions"] == campaign["attempts"][0]["result"]["executable_conditions"]
    assert core["failures"][0]["checker_error"] == "p-shared failed mechanically"
    serialized = json.dumps(evidence["reviser_payload"])
    assert "dependency_versions" not in serialized
    assert "tool_versions" not in serialized


def test_common_evidence_recovers_saved_artifact_and_executable_checks(tmp_path):
    campaign = _campaign("random")
    attempt = campaign["attempts"][0]
    attempt["provenance"].pop("input_files")
    attempt["result"].pop("failed_artifact")
    attempt["result"].pop("executable_conditions")
    attempt["result"].pop("workspace_diff_summary")
    case = tmp_path / attempt["case"]
    checks = case / "checks"
    checks.mkdir(parents=True)
    (case / "candidate.json").write_text(
        json.dumps(
            {
                "sanity": {
                    "required_paths": [
                        "/workspace/input.json",
                        "/workspace/schema.txt",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    check_text = "#!/bin/sh\npython3 /checks/validate_output.py\n"
    (checks / "p-shared.sh").write_text(check_text, encoding="utf-8")
    attempt["result"]["verdicts"][0]["script"] = str(checks / "p-shared.sh")
    run = tmp_path / attempt["run"]
    (run / "logs").mkdir(parents=True)
    diff_text = "diff --git a/result.json b/result.json\n+{\"actual\":\"wrong\"}\n"
    (run / "logs" / "workspace.diff").write_text(diff_text, encoding="utf-8")
    request = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=_skill(tmp_path),
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )[0]

    core = build_repair_evidence(campaign, request, max_bytes=12_000)[
        "failure_core"
    ]

    assert core["input_files"] == [
        {"path": "/workspace/input.json"},
        {"path": "/workspace/schema.txt"},
    ]
    assert core["failed_artifact"]["workspace_diff"]["content"] == diff_text
    assert core["failed_artifact"]["workspace_diff"]["truncated"] is False
    assert core["executable_conditions"] == [
        {
            "property_id": "p-shared",
            "checker_script": {
                "bytes": len(check_text.encode()),
                "content": check_text,
                "sha256": hashlib.sha256(check_text.encode()).hexdigest(),
                "truncated": False,
            },
        }
    ]


def test_repair_status_vocabulary_is_frozen():
    assert REPAIR_STATUSES == (
        "repaired",
        "same_failure",
        "different_failure",
        "timeout",
        "error",
        "inconclusive",
    )


def test_model_patcher_uses_frozen_settings_and_preserves_package(tmp_path):
    original = _skill(tmp_path)
    (original / "helper.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    campaign = _campaign("skillrace")
    request = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )[0]
    request.case_dir.mkdir(parents=True)
    evidence = build_repair_evidence(campaign, request, max_bytes=3600)
    calls = []

    def fake_chat(messages, **settings):
        calls.append((messages, settings))
        return {
            "content": "# Repaired skill\n\nUse the validated branch before writing.\n",
            "model": "same-model",
            "id": "provider-repair-1",
            "usage": {"prompt_tokens": 30, "completion_tokens": 10},
            "cost_provider_credits": 0.04,
        }

    patcher = make_model_patcher(
        model="same-model",
        chat_fn=nonproduction_chat_fixture(fake_chat),
        temperature=0.0,
        reasoning=True,
        max_tokens=2000,
    )
    patch_root = tmp_path / "patch"
    patch_root.mkdir()
    result = patcher(request, evidence, patch_root)

    assert result["status"] == "completed"
    assert result["operation_id"].startswith("repair.patch.")
    skill = patch_root / "skill"
    assert result["skill_dir"] == str(skill.resolve())
    assert (skill / "SKILL.md").read_text().startswith("# Repaired skill")
    assert (skill / "helper.sh").read_text() == "#!/bin/sh\nexit 0\n"
    assert (patch_root / "provenance" / "patch.json").is_file()
    assert len(calls) == 1
    messages, settings = calls[0]
    assert messages[0]["role"] == "system"
    assert "general" in messages[0]["content"].lower()
    assert '"skillrace"' not in messages[1]["content"]
    assert "reasoning_episodes" in messages[1]["content"]
    assert settings["model"] == "same-model"
    assert settings["temperature"] == 0.0
    assert settings["reasoning"] is True
    assert settings["max_tokens"] == 2000
    assert settings["tag"] == "repair.patch"
    assert settings["skill"] == "demo"


def test_replay_executor_reuses_exact_case_checks_model_and_budget(tmp_path):
    original = _skill(tmp_path)
    campaign = _campaign("random")
    request = select_failure_repairs(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path,
        output_root=tmp_path / "repairs",
        phase="public",
    )[0]
    request.case_dir.mkdir(parents=True)
    patched = tmp_path / "patched"
    patched.mkdir()
    (patched / "SKILL.md").write_text("# Patched\n", encoding="utf-8")
    observed = []

    def run_agent(case_dir, replay_dir, model, wall_clock, skill_dir):
        observed.append((case_dir, replay_dir, model, wall_clock, skill_dir))
        replay_dir.mkdir(parents=True)
        (replay_dir / "cost.json").write_text(
            json.dumps({"in": 12, "out": 3, "price_provider_credits": 0.05})
        )
        return 0, "ok", {"run_id": "repair-run-1"}

    def check_run(replay_dir, model):
        assert replay_dir == tmp_path / "replay"
        assert model == "same-model"
        return (
            [{"property_id": "p-shared", "holds": True, "violated": False}],
            [],
            0,
        )

    executor = make_replay_executor(
        model="same-model",
        wall_clock=321,
        run_agent_fn=run_agent,
        check_run_fn=check_run,
    )
    result = executor(request, patched, tmp_path / "replay")

    assert observed == [
        (request.case_dir, tmp_path / "replay", "same-model", 321, patched.resolve())
    ]
    assert result == {
        "status": "completed",
        "verdicts": [
            {"property_id": "p-shared", "holds": True, "violated": False}
        ],
        "agent_id": "repair-run-1",
        "input_tokens": 12,
        "output_tokens": 3,
        "cost_provider_credits": 0.05,
    }
