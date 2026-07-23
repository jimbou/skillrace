from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

import skillrace.rq3 as rq3
from skillrace.feedback import BYTE_BUDGET_ID, DEFAULT_LIMITS
from skillrace.io_utils import canonical_json_hash, file_hash
from skillrace.rq3 import (
    EVALUATION_CONDITIONS,
    HiddenCaseIdentity,
    HiddenScenarioIdentity,
    ManifestMismatchError,
    UncertainExternalOutcomeError,
    evaluate_hidden_scenario,
    load_rq3_manifest,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def test_hidden_case_identity_freezes_contract_criteria_and_validation_image():
    assert {
        "criterion_ids",
        "validation_evidence_hash",
        "validation_image_digest",
    }.issubset(HiddenCaseIdentity.__dataclass_fields__)


def _skill(root, name: str, body: str):
    path = root / name
    path.mkdir()
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    return path


def _inputs(tmp_path, protocol_hash: str):
    skills = {
        "zero-shot": _skill(tmp_path, "base", "# Base\n"),
        "random-feedback": _skill(tmp_path, "random", "# Random revision\n"),
        "greybox-feedback": _skill(tmp_path, "greybox", "# Greybox revision\n"),
        "skillrace-feedback": _skill(tmp_path, "skillrace", "# SkillRACE revision\n"),
    }
    base_hash = file_hash(skills["zero-shot"] / "SKILL.md")
    base = {
        "schema": "skillrace-base-generation/2",
        "skill_hash": base_hash,
        "artifact_hash": _digest("base-artifact"),
        "package_hash": _digest("base-package"),
        "generation_id": _digest("base-generation")[:24],
        "model_config": {
            "model": "glm-4.5-flash",
            "temperature": 0.0,
            "reasoning": True,
            "max_tokens": 4000,
            "prompt_version": "skillrace-rq3-base-generation/1",
        },
        "operation_id": f"rq3.base.{_digest('base-operation')}",
        "provider_model": "glm-4.5-flash",
        "provider_response_id_sha256": _digest("base-response"),
        "provider_request_id_sha256": None,
        "billing_status": "known",
        "journal_terminal_event_id": _digest("base-terminal-event"),
        "journal_terminal_receipt": ".skillrace/model-call-terminal.json",
        "journal_terminal_receipt_hash": _digest("base-terminal-receipt"),
        "journal_call_terminal_event_id": _digest("base-call-event"),
        "journal_call_terminal_receipt": ".skillrace/model-call-operation-terminal.json",
        "journal_call_terminal_receipt_hash": _digest("base-call-receipt"),
        "input_tokens": 10,
        "output_tokens": 2,
        "cost_provider_credits": 0.1,
    }
    campaigns = {}
    envelopes = {}
    revisions = {}
    for producer, condition in (
        ("random", "random-feedback"),
        ("greybox", "greybox-feedback"),
        ("skillrace", "skillrace-feedback"),
    ):
        campaign_hash = _digest(f"{producer}-campaign")
        envelope_hash = _digest(f"{producer}-envelope")
        revision_config = {
            "model": "glm-4.5-flash",
            "temperature": 0.0,
            "reasoning": True,
            "max_tokens": 4000,
            "prompt_version": "skillrace-rq3-revision/1",
        }
        revision_start = {
            "schema": "skillrace-revision-start/1",
            "producer": producer,
            "base_skill_hash": base_hash,
            "base_package_hash": _digest("base-package"),
            "envelope_hash": envelope_hash,
            "request_hash": _digest(f"{producer}-request"),
            "model_config": revision_config,
        }
        campaigns[producer] = {
            "artifact_hash": campaign_hash,
            "protocol_hash": protocol_hash,
            "base_skill_hash": base_hash,
            "budget": 30,
            "counted_executions": 30,
            "complete": True,
            "model": "glm-4.5-flash",
            "agent_model": "glm-4.5-flash",
            "allocation": {
                "bootstrap": 0 if producer == "random" else 10,
                "exploration": 30 if producer == "random" else 20,
                "budget": 30,
            },
            "cost_provider_credits": 1.0,
        }
        envelopes[producer] = {
            "artifact_hash": envelope_hash,
            "source_campaign_hash": campaign_hash,
            "budget_unit": BYTE_BUDGET_ID,
            "max_bytes": 3600,
            "used_bytes": 3000,
            "limits": dict(DEFAULT_LIMITS),
            "confirmation_executions": 1,
            "confirmation_cost_provider_credits": 0.05,
            "cost_provider_credits": 0.0,
        }
        revisions[producer] = {
            "schema": "skillrace-revision/2",
            "artifact_hash": _digest(f"{producer}-revision-record"),
            "base_skill_hash": base_hash,
            "envelope_hash": envelope_hash,
            "revised_skill_hash": file_hash(skills[condition] / "SKILL.md"),
            "model_config": revision_config,
            "operation_start_identity": revision_start,
            "operation_id": f"rq3.revision.{canonical_json_hash(revision_start)}",
            "request_hash": _digest(f"{producer}-request"),
            "provider_model": "glm-4.5-flash",
            "provider_response_id_sha256": _digest(f"{producer}-response"),
            "provider_request_id_sha256": None,
            "billing_status": "known",
            "journal_tag": "rq3.revise",
            "journal_skill": producer,
            "journal_terminal_event_id": _digest(f"{producer}-terminal-event"),
            "journal_terminal_receipt": "provenance/model-call-terminal.json",
            "journal_terminal_receipt_hash": _digest(f"{producer}-terminal-receipt"),
            "journal_call_terminal_event_id": _digest(f"{producer}-call-event"),
            "journal_call_terminal_receipt": "provenance/model-call-operation-terminal.json",
            "journal_call_terminal_receipt_hash": _digest(f"{producer}-call-receipt"),
            "cost_provider_credits": 0.25,
        }
    return skills, base, campaigns, envelopes, revisions


def _hidden_identity(tmp_path, base_skill_hash: str) -> HiddenScenarioIdentity:
    tests = []
    for number in range(1, 11):
        root = tmp_path / "hidden" / f"t{number}"
        (root / "checks").mkdir(parents=True)
        (root / "candidate.json").write_text("{}\n", encoding="utf-8")
        (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (root / "checks" / "pass.sh").write_text("exit 0\n", encoding="utf-8")
        tests.append(
            HiddenCaseIdentity(
                test_id=f"scenario-one/t{number}",
                root=root,
                contract_identity=_digest(f"contract-{number}"),
                candidate_hash=file_hash(root / "candidate.json"),
                dockerfile_hash=file_hash(root / "Dockerfile"),
                checks_hash=_digest(f"checks-{number}"),
                criterion_ids=("functional",),
                validation_evidence_hash=_digest(f"evidence-{number}"),
                validation_image_digest="sha256:" + _digest(f"image-{number}"),
            )
        )
    return HiddenScenarioIdentity(
        scenario_id="scenario-one",
        contract_identity=_digest("scenario-contract"),
        base_skill_hash=base_skill_hash,
        tests=tuple(tests),
    )


def _write_raw_execution(
    request,
    verdicts,
    *,
    run_id="hidden-run",
    input_tokens=10,
    output_tokens=2,
    cost_provider_credits=0.01,
    wall_seconds=1.5,
):
    execution = request.run_dir / "execution"
    execution.mkdir(parents=True, exist_ok=True)
    (execution / "launch.json").write_text(
        json.dumps({"schema": "skillrace-hidden-launch/1"}), encoding="utf-8"
    )
    (execution / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "base_image": f"skillrace/skillgen-base:0.73.1-{request.agent_model}",
                "base_image_id": "sha256:" + "a" * 64,
                "env_image_id": "sha256:" + "b" * 64,
                "termination": {"reason": "completed", "seconds": wall_seconds},
            }
        ),
        encoding="utf-8",
    )
    (execution / "verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")
    (execution / "cost.json").write_text(
        json.dumps(
            {
                "in": input_tokens,
                "out": output_tokens,
                "price_provider_credits": cost_provider_credits,
            }
        ),
        encoding="utf-8",
    )


def _complete_evaluation(tmp_path, monkeypatch):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()

    def executor(request):
        verdicts = [
            {
                "property_id": "functional",
                "provenance": "hidden-independent",
                "holds": True,
                "violated": False,
            }
        ]
        run_id = _digest(str(request.run_dir))[:16]
        _write_raw_execution(request, verdicts, run_id=run_id)
        return {
            "status": "completed",
            "verdicts": verdicts,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
            "wall_seconds": 1.5,
            "run_id": run_id,
        }

    output = tmp_path / "out"
    evaluate_hidden_scenario(
        scenario_dir=tmp_path / "scenario-one",
        out_dir=output,
        protocol_hash=protocol_hash,
        replication=1,
        base_skill=base,
        campaigns=campaigns,
        envelopes=envelopes,
        revisions=revisions,
        skills_by_condition=skills,
        model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
        public_artifact_roots=[public],
        executor=executor,
    )
    return output, hidden


def _rewrite_manifest(path, value):
    value["manifest_hash"] = rq3._manifest_hash(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_exact_four_conditions_once_each_and_receipt_recovery(tmp_path, monkeypatch):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    # Caller dictionary order is not part of the experiment identity; the writer
    # canonicalizes it to the frozen producer/condition order.
    skills = dict(reversed(list(skills.items())))
    campaigns = dict(reversed(list(campaigns.items())))
    envelopes = dict(reversed(list(envelopes.items())))
    revisions = dict(reversed(list(revisions.items())))
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public-artifacts"
    public.mkdir()
    (public / "campaign.log").write_text("public only\n", encoding="utf-8")
    calls = []

    def executor(request):
        calls.append(request)
        verdicts = [
            {
                "property_id": "functional",
                "provenance": "hidden-independent",
                "holds": True,
                "violated": False,
            },
            {
                "property_id": "fixed-safe",
                "provenance": "fixed",
                "holds": True,
                "violated": False,
            },
        ]
        run_id = _digest(str(request.run_dir))[:16]
        _write_raw_execution(request, verdicts, run_id=run_id)
        return {
            "status": "completed",
            "verdicts": verdicts,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
            "wall_seconds": 1.5,
            "run_id": run_id,
        }

    output = tmp_path / "out"
    manifest = evaluate_hidden_scenario(
        scenario_dir=tmp_path / "scenario-one",
        out_dir=output,
        protocol_hash=protocol_hash,
        replication=1,
        base_skill=base,
        campaigns=campaigns,
        envelopes=envelopes,
        revisions=revisions,
        skills_by_condition=skills,
        model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
        public_artifact_roots=[public],
        executor=executor,
    )

    assert tuple(manifest["evaluations"]) == EVALUATION_CONDITIONS
    assert len(calls) == 4 * 10
    assert all("condition" not in call.__dataclass_fields__ for call in calls)
    assert all(
        len(condition["tests"]) == 10
        for condition in manifest["evaluations"].values()
    )
    assert all(
        row["execution_count"] == 1
        for condition in manifest["evaluations"].values()
        for row in condition["tests"].values()
    )
    assert {
        row["test_contract_identity"]
        for condition in manifest["evaluations"].values()
        for row in condition["tests"].values()
    } == {test.contract_identity for test in hidden.tests}
    assert {call.criterion_ids for call in calls} == {("functional",)}
    assert {call.validation_image_digest for call in calls} == {
        test.validation_image_digest for test in hidden.tests
    }
    first_link = manifest["evaluations"]["zero-shot"]["tests"]["scenario-one/t1"]
    first_root = output / "evaluations" / "zero-shot" / "runs" / "t1"
    start = json.loads((first_root / "start.json").read_text())
    result = json.loads((first_root / "result.json").read_text())
    receipt_payload = json.loads((first_root / "receipt.json").read_text())
    expected_image = hidden.tests[0].validation_image_digest
    for payload in (first_link, start, result, receipt_payload):
        assert payload["validation_image_digest"] == expected_image
    assert first_link["criterion_ids"] == ["functional"]
    assert start["criterion_ids"] == ["functional"]
    assert result["criterion_ids"] == ["functional"]
    assert set(result["raw_artifacts"]) == {"launch", "run", "verdicts", "cost"}
    assert all(record["sha256"] for record in result["raw_artifacts"].values())
    assert first_link["raw_artifacts_hash"] == result["raw_artifacts_hash"]
    assert receipt_payload["raw_artifacts_hash"] == result["raw_artifacts_hash"]
    assert manifest["costs"]["campaign_provider_credits"] == pytest.approx(3.0)
    assert manifest["costs"]["confirmation_provider_credits"] == pytest.approx(0.15)
    assert manifest["costs"]["revision_provider_credits"] == pytest.approx(0.75)
    assert manifest["costs"]["evaluation_provider_credits"] == pytest.approx(0.4)
    assert manifest["costs"]["total_provider_credits"] == pytest.approx(4.3)

    receipt = output / "evaluations" / "random-feedback" / "runs" / "t1" / "receipt.json"
    receipt.unlink()
    calls.clear()
    resumed = evaluate_hidden_scenario(
        scenario_dir=tmp_path / "scenario-one",
        out_dir=output,
        protocol_hash=protocol_hash,
        replication=1,
        base_skill=base,
        campaigns=campaigns,
        envelopes=envelopes,
        revisions=revisions,
        skills_by_condition=skills,
        model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
        public_artifact_roots=[public],
        executor=executor,
    )
    assert calls == []
    assert receipt.is_file()
    assert resumed == load_rq3_manifest(output / "rq3-manifest.json")


def test_resume_refuses_protocol_or_result_hash_mismatch(tmp_path, monkeypatch):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()

    def executor(request):
        verdicts = [
            {
                "property_id": "functional",
                "provenance": "hidden-independent",
                "holds": True,
                "violated": False,
            }
        ]
        _write_raw_execution(request, verdicts)
        return {
            "status": "completed",
            "verdicts": verdicts,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
            "wall_seconds": 1.5,
            "run_id": "hidden-run",
        }

    output = tmp_path / "out"
    evaluate_hidden_scenario(
        scenario_dir=tmp_path / "scenario-one",
        out_dir=output,
        protocol_hash=protocol_hash,
        replication=1,
        base_skill=base,
        campaigns=campaigns,
        envelopes=envelopes,
        revisions=revisions,
        skills_by_condition=skills,
        model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
        public_artifact_roots=[public],
        executor=executor,
    )

    with pytest.raises(ManifestMismatchError, match="protocol"):
        load_rq3_manifest(
            output / "rq3-manifest.json", expected_protocol_hash=_digest("other")
        )

    result = output / "evaluations" / "zero-shot" / "runs" / "t1" / "result.json"
    payload = json.loads(result.read_text())
    payload["grade"]["functional_pass"] = False
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestMismatchError, match="result"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=output,
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("must not re-execute"),
        )


def test_recursive_verifier_recomputes_grade_even_if_outer_hashes_are_rewritten(
    tmp_path, monkeypatch
):
    output, _hidden = _complete_evaluation(tmp_path, monkeypatch)
    manifest_path = output / "rq3-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    link = manifest["evaluations"]["zero-shot"]["tests"]["scenario-one/t1"]
    run = output / "evaluations" / "zero-shot" / "runs" / "t1"
    result_path = run / "result.json"
    receipt_path = run / "receipt.json"
    result = json.loads(result_path.read_text())
    result["grade"]["functional_pass"] = False
    result["grade"]["strict_pass"] = False
    result_path.write_text(json.dumps(result), encoding="utf-8")
    receipt = json.loads(receipt_path.read_text())
    receipt["result_hash"] = file_hash(result_path)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    link["result_hash"] = file_hash(result_path)
    link["receipt_hash"] = file_hash(receipt_path)
    link["grade"] = result["grade"]
    _rewrite_manifest(manifest_path, manifest)

    with pytest.raises(ManifestMismatchError, match="grade"):
        rq3.verify_rq3_evaluation_artifacts(
            manifest_path,
            scenario_dir=tmp_path / "scenario-one",
        )


def test_recursive_verifier_rejects_raw_tamper_and_changed_hidden_identity(
    tmp_path, monkeypatch
):
    output, hidden = _complete_evaluation(tmp_path, monkeypatch)
    manifest_path = output / "rq3-manifest.json"
    raw_verdicts = (
        output
        / "evaluations"
        / "zero-shot"
        / "runs"
        / "t1"
        / "agent"
        / "execution"
        / "verdicts.json"
    )
    raw_verdicts.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ManifestMismatchError, match="raw"):
        rq3.verify_rq3_evaluation_artifacts(
            manifest_path,
            scenario_dir=tmp_path / "scenario-one",
        )

    # Restore the raw artifact, then prove that current hidden evidence is reloaded.
    result = json.loads(
        (
            output / "evaluations" / "zero-shot" / "runs" / "t1" / "result.json"
        ).read_text()
    )
    raw_verdicts.write_text(json.dumps(result["verdicts"]), encoding="utf-8")
    changed = dataclasses.replace(
        hidden,
        tests=(
            dataclasses.replace(
                hidden.tests[0],
                validation_image_digest="sha256:" + _digest("changed-image"),
            ),
            *hidden.tests[1:],
        ),
    )
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: changed)
    with pytest.raises(ManifestMismatchError, match="validation_image_digest"):
        rq3.verify_rq3_evaluation_artifacts(
            manifest_path,
            scenario_dir=tmp_path / "scenario-one",
        )


def test_recursive_verifier_requires_exact_four_by_ten_schedule(tmp_path, monkeypatch):
    output, _hidden = _complete_evaluation(tmp_path, monkeypatch)
    manifest_path = output / "rq3-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["evaluations"]["zero-shot"]["tests"]["scenario-one/t10"]
    _rewrite_manifest(manifest_path, manifest)

    with pytest.raises(ManifestMismatchError, match="t1.*t10|test set"):
        rq3.verify_rq3_evaluation_artifacts(
            manifest_path,
            scenario_dir=tmp_path / "scenario-one",
        )


def test_recursive_verifier_rejects_self_hashed_noncanonical_feedback_budget(
    tmp_path, monkeypatch
):
    output, _hidden = _complete_evaluation(tmp_path, monkeypatch)
    manifest_path = output / "rq3-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for envelope in manifest["feedback_envelopes"].values():
        envelope["max_bytes"] = 24000
    _rewrite_manifest(manifest_path, manifest)

    with pytest.raises(ManifestMismatchError, match="feedback max_bytes"):
        rq3.verify_rq3_evaluation_artifacts(
            manifest_path,
            scenario_dir=tmp_path / "scenario-one",
        )


def test_final_verifier_rejects_executor_results_without_all_raw_artifacts(
    tmp_path, monkeypatch
):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()

    with pytest.raises(ManifestMismatchError, match="raw execution artifacts"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: {
                "status": "completed",
                "verdicts": [
                    {
                        "property_id": "functional",
                        "provenance": "hidden-independent",
                        "holds": True,
                        "violated": False,
                    }
                ],
            },
        )


def test_recursive_regrading_preserves_completed_execution_with_unknown_oracle(
    tmp_path, monkeypatch
):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()

    def executor(request):
        verdicts = [
            {
                "property_id": "functional",
                "provenance": "hidden-independent",
                "holds": None,
                "violated": False,
            }
        ]
        _write_raw_execution(request, verdicts)
        return {
            "status": "completed",
            "verdicts": verdicts,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
            "wall_seconds": 1.5,
            "run_id": "hidden-run",
        }

    manifest = evaluate_hidden_scenario(
        scenario_dir=tmp_path / "scenario-one",
        out_dir=tmp_path / "out",
        protocol_hash=protocol_hash,
        replication=1,
        base_skill=base,
        campaigns=campaigns,
        envelopes=envelopes,
        revisions=revisions,
        skills_by_condition=skills,
        model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
        public_artifact_roots=[public],
        executor=executor,
    )

    assert manifest["evaluations"]["zero-shot"]["summary"]["status_counts"][
        "inconclusive"
    ] == 10


def test_hidden_evaluation_start_record_blocks_reexecution_after_unknown_crash(
    tmp_path, monkeypatch
):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()

    class ProcessLost(BaseException):
        pass

    output = tmp_path / "out"
    with pytest.raises(ProcessLost):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=output,
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: (_ for _ in ()).throw(ProcessLost()),
        )

    start = output / "evaluations" / "zero-shot" / "runs" / "t1" / "start.json"
    assert start.is_file()
    with pytest.raises(UncertainExternalOutcomeError, match="outcome is unknown"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=output,
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("uncertain external call must not repeat"),
        )


def test_hidden_resume_refuses_missing_artifacts_after_manifest_committed_completion(
    tmp_path, monkeypatch
):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()
    output = tmp_path / "out"

    def executor(request):
        verdicts = [
            {
                "property_id": "functional",
                "provenance": "hidden-independent",
                "holds": True,
                "violated": False,
            }
        ]
        _write_raw_execution(request, verdicts)
        return {
            "status": "completed",
            "verdicts": verdicts,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
            "wall_seconds": 1.5,
            "run_id": "hidden-run",
        }

    evaluate_hidden_scenario(
        scenario_dir=tmp_path / "scenario-one",
        out_dir=output,
        protocol_hash=protocol_hash,
        replication=1,
        base_skill=base,
        campaigns=campaigns,
        envelopes=envelopes,
        revisions=revisions,
        skills_by_condition=skills,
        model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
        public_artifact_roots=[public],
        executor=executor,
    )
    run = output / "evaluations" / "zero-shot" / "runs" / "t1"
    (run / "receipt.json").unlink()
    (run / "result.json").unlink()

    with pytest.raises(ManifestMismatchError, match="committed.*missing"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=output,
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("committed execution must not repeat"),
        )

def test_manifest_rejects_extra_conditions(tmp_path, monkeypatch):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    skills["expert"] = skills["zero-shot"]
    with pytest.raises(ManifestMismatchError, match="conditions"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[],
            executor=lambda _request: {},
        )


def test_manifest_rejects_legacy_base_generation_provenance(tmp_path, monkeypatch):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    base["schema"] = "skillrace-base-generation/1"

    with pytest.raises(ManifestMismatchError, match="base generation schema"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[],
            executor=lambda _request: pytest.fail("legacy base must fail pre-run"),
        )


def test_manifest_requires_complete_30_run_same_model_campaigns_and_no_secrets(
    tmp_path, monkeypatch
):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()

    campaigns["random"]["complete"] = False
    with pytest.raises(ManifestMismatchError, match="complete"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("must not execute"),
        )

    campaigns["random"]["complete"] = True
    campaigns["greybox"]["agent_model"] = "other-model"
    with pytest.raises(ManifestMismatchError, match="model"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out-model",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("must not execute"),
        )

    campaigns["greybox"]["agent_model"] = "glm-4.5-flash"
    campaigns["skillrace"]["allocation"]["bootstrap"] = 0
    with pytest.raises(ManifestMismatchError, match="allocation"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out-allocation",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("must not execute"),
        )

    campaigns["random"]["api_key"] = "must-never-be-recorded"
    with pytest.raises(ManifestMismatchError, match="secret"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out-secret",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[],
            executor=lambda _request: {},
        )

    campaigns["random"].pop("api_key")
    campaigns["skillrace"]["allocation"]["bootstrap"] = 10
    revisions["random"]["schema"] = "skillrace-revision/1"
    with pytest.raises(ManifestMismatchError, match="revision schema"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out-revision-schema",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("invalid revision must fail pre-run"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("budget_unit", "characters"),
        ("max_bytes", 24000),
        ("limits", {"max_string_chars": 999}),
    ],
)
def test_manifest_freezes_equal_3600_byte_feedback_contract(
    tmp_path, monkeypatch, field, value
):
    protocol_hash = _digest("protocol")
    skills, base, campaigns, envelopes, revisions = _inputs(tmp_path, protocol_hash)
    hidden = _hidden_identity(tmp_path, base["skill_hash"])
    monkeypatch.setattr(rq3, "_load_hidden_scenario_identity", lambda _path: hidden)
    public = tmp_path / "public"
    public.mkdir()
    envelopes["greybox"][field] = value

    with pytest.raises(ManifestMismatchError, match="feedback|byte|limit"):
        evaluate_hidden_scenario(
            scenario_dir=tmp_path / "scenario-one",
            out_dir=tmp_path / "out",
            protocol_hash=protocol_hash,
            replication=1,
            base_skill=base,
            campaigns=campaigns,
            envelopes=envelopes,
            revisions=revisions,
            skills_by_condition=skills,
            model_config={"model": "glm-4.5-flash", "wall_clock": 1200},
            public_artifact_roots=[public],
            executor=lambda _request: pytest.fail("invalid feedback must fail pre-run"),
        )
