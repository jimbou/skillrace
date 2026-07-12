from __future__ import annotations

import hashlib
import json

import pytest

from skillrace.campaign_engine import CampaignEngine
from skillrace.campaign_protocol import CampaignProtocol
from skillrace.io_utils import canonical_json_hash, file_hash
from skillrace.rq3_campaign import (
    CampaignArtifactError,
    derive_campaign_cost_record,
    prepare_campaign_input_record,
    validate_campaign_artifact,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write_real_campaign2(
    root,
    method="skillrace",
    *,
    base_hash=None,
    base_package_hash=None,
    public_stage_hash=None,
):
    protocol = {
        "schema": "campaign-protocol/1",
        "protocol_id": "skillrace-issta-main-v1",
        "status": "frozen",
        "model": "qwen3.6-flash",
        "budget": 30,
        "bootstrap_count": 10,
        "max_generation_attempts_per_execution": 5,
        "seed_generator": {"batch_size": 5, "temperature": 0.9, "build_retries": 4},
        "greybox_level": "L1",
        "random_seed": 20260711,
    }
    protocol_hash = canonical_json_hash(protocol)
    base_hash = base_hash or _digest("base skill")
    base_package_hash = base_package_hash or _digest("base package")
    public_stage_hash = public_stage_hash or _digest("public stage")
    output_identity = _digest("output identity")
    root.mkdir(parents=True, exist_ok=True)
    prepare_campaign_input_record(
        root,
        method=method,
        protocol_hash=protocol_hash,
        base_skill_hash=base_hash,
        base_package_hash=base_package_hash,
        public_stage_hash=public_stage_hash,
        output_identity=output_identity,
    )
    attempts = []
    iterations = []
    for ordinal in range(30):
        execution_id = f"e{ordinal:04d}"
        attempt_id = f"{execution_id}-a00"
        phase = "explore" if method == "random" or ordinal >= 10 else "bootstrap"
        source = "bootstrap" if phase == "bootstrap" else method
        provenance = {"source": source}
        if method == "random":
            provenance["independent_test"] = True
        directory = root / "attempts" / attempt_id
        directory.mkdir(parents=True)
        envelope = {
            "protocol_hash": protocol_hash,
            "method": method,
            "skill": "scenario-one",
            "output_identity": output_identity,
            "execution_id": execution_id,
            "attempt_id": attempt_id,
        }
        proposal = {
            "schema": "campaign-proposal/1",
            **envelope,
            "phase": phase,
            "source": source,
            "candidate": {
                "candidate_id": f"candidate-{ordinal:03d}",
                "provenance": provenance,
            },
            "generator_state": {},
            "bootstrap_generator_state": {},
        }
        result = {
            "agent_started": True,
            "status": "completed",
            "runner_status": "completed",
            "oracle_status": "completed",
            "violated": [],
            "inconclusive": [],
            "cost_usd": 0.01,
            "input_tokens": 10,
            "output_tokens": 2,
            "run_id": f"agent-{ordinal:04d}",
        }
        run = root / "runs" / f"agent-{ordinal:04d}"
        run.mkdir(parents=True)
        (run / "cost.json").write_text(
            json.dumps({"usd": 0.01, "in": 10, "out": 2}) + "\n"
        )
        (run / "run.json").write_text(
            json.dumps(
                {
                    "run_id": f"agent-{ordinal:04d}",
                    "model": "qwen3.6-flash",
                    "agent_started": True,
                    "case": f"cases/candidate-{ordinal:03d}",
                }
            )
            + "\n"
        )
        receipt = {
            "schema": "campaign-attempt-receipt/1",
            **envelope,
            "candidate_id": f"candidate-{ordinal:03d}",
            "result": result,
        }
        cleanup_intent = {
            "schema": "candidate-image-cleanup-intent/1",
            "candidate_id": f"candidate-{ordinal:03d}",
            "image": None,
            "base_image": None,
            "owned": False,
            "action": "missing",
        }
        cleanup = {
            "schema": "candidate-image-cleanup/1",
            "candidate_id": f"candidate-{ordinal:03d}",
            "image": None,
            "owned": False,
            "intent_hash": canonical_json_hash(cleanup_intent),
            "removal_invoked": False,
            "recovered_after_intent": False,
            "status": "missing",
        }
        fold = {
            "schema": "campaign-fold/1",
            **envelope,
            "phase": phase,
            "status": "folded",
            "classification": None,
            "fold_result": None,
            "error": None,
            "generator_state": {},
            "bootstrap_generator_state": {},
        }
        for name, value in (
            ("proposal.json", proposal),
            ("receipt.json", receipt),
            ("cleanup.intent.json", cleanup_intent),
            ("cleanup.json", cleanup),
            ("fold.json", fold),
        ):
            (directory / name).write_text(json.dumps(value) + "\n")
        record = {
            "i": ordinal,
            "execution_id": execution_id,
            "attempt_id": attempt_id,
            "phase": phase,
            "source": source,
            "candidate_id": f"candidate-{ordinal:03d}",
            "candidate": proposal["candidate"],
            "provenance": provenance,
            "generation_status": "generated",
            "infrastructure_status": "ready",
            "runner_status": "completed",
            "oracle_status": "completed",
            "agent_started": True,
            "consume_budget": True,
            "violated": [],
            "inconclusive": [],
            "n_verdicts": 0,
            "run": f"runs/agent-{ordinal:04d}",
            "case": f"cases/candidate-{ordinal:03d}",
            "status": "completed",
            "classification": None,
            "cleanup": cleanup,
            "cleanup_intent_hash": canonical_json_hash(cleanup_intent),
            "proposal_hash": canonical_json_hash(proposal),
            "receipt_hash": canonical_json_hash(receipt),
            "cleanup_hash": canonical_json_hash(cleanup),
            "fold_status": "folded",
            "result": result,
            "fold_hash": canonical_json_hash(fold),
            "fold_error": None,
            "fold_result": None,
        }
        attempts.append(record)
        iterations.append(json.loads(json.dumps(record)))
    state = {
        "schema": "campaign/2",
        "protocol_id": protocol["protocol_id"],
        "protocol_hash": protocol_hash,
        "protocol": protocol,
        "method": method,
        "skill": "scenario-one",
        "output_identity": output_identity,
        "budget": 30,
        "bootstrap_count": 0 if method == "random" else 10,
        "seed_count": 0 if method == "random" else 10,
        "configured_bootstrap_count": 10,
        "allocation": {
            "budget": 30,
            "bootstrap": 0 if method == "random" else 10,
            "exploration": 30 if method == "random" else 20,
        },
        "model": "qwen3.6-flash",
        "agent_model": "qwen3.6-flash",
        "greybox_level": "L1" if method == "greybox" else None,
        "max_pre_agent_attempts": 5,
        "counted_executions": 30,
        "attempts": attempts,
        "iterations": iterations,
        "generator_state": {"gen_cost_usd": 0.2},
        "bootstrap_generator_state": {"gen_cost_usd": 0.1},
        "pending_fold": None,
        "folded_attempt_ids": [row["attempt_id"] for row in iterations],
        "status": "completed",
        "complete": True,
        "stop_reason": None,
        "totals": {"runs": 30, "attempts": 30},
    }
    campaign_path = root / "campaign.json"
    campaign_path.write_text(json.dumps(state) + "\n")
    derive_campaign_cost_record(campaign_path)
    return campaign_path, protocol_hash, base_hash


def test_adapter_accepts_real_campaign2_and_recursively_verifies_receipts(tmp_path):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "campaign")

    record = validate_campaign_artifact(
        campaign_path,
        expected_method="skillrace",
        expected_protocol_hash=protocol_hash,
        expected_base_skill_hash=base_hash,
    )

    assert record["schema"] == "campaign/2"
    assert record["counted_executions"] == 30
    assert record["allocation"] == {"budget": 30, "bootstrap": 10, "exploration": 20}
    assert record["cost_usd"] == pytest.approx(0.6)
    assert record["agent_ids"] == [f"agent-{i:04d}" for i in range(30)]
    assert record["artifact_hash"] == canonical_json_hash(
        json.loads(campaign_path.read_text())
    )

    receipt = campaign_path.parent / "attempts" / "e0004-a00" / "receipt.json"
    value = json.loads(receipt.read_text())
    value["result"]["status"] = "timeout"
    receipt.write_text(json.dumps(value))
    with pytest.raises(CampaignArtifactError, match="receipt hash"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )


@pytest.mark.parametrize("method", ["random", "greybox"])
def test_adapter_accepts_frozen_baseline_semantics(tmp_path, method):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(
        tmp_path / method, method=method
    )

    record = validate_campaign_artifact(
        campaign_path,
        expected_method=method,
        expected_protocol_hash=protocol_hash,
        expected_base_skill_hash=base_hash,
    )

    assert record["allocation"] == {
        "budget": 30,
        "bootstrap": 0 if method == "random" else 10,
        "exploration": 30 if method == "random" else 20,
    }


def test_adapter_rejects_noncontiguous_phase_or_embedded_protocol_tampering(tmp_path):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "campaign")
    state = json.loads(campaign_path.read_text())
    state["iterations"][10]["phase"] = "bootstrap"
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="phase sequence"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )

    state["iterations"][10]["phase"] = "explore"
    state["protocol"]["budget"] = 99
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="embedded protocol"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )


def test_adapter_rejects_duplicate_candidates_unstarted_runs_and_wrong_phase_sources(tmp_path):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "campaign")
    state = json.loads(campaign_path.read_text())
    duplicate = state["attempts"][0]["candidate_id"]
    directory = campaign_path.parent / "attempts" / "e0001-a00"
    proposal = json.loads((directory / "proposal.json").read_text())
    proposal["candidate"]["candidate_id"] = duplicate
    (directory / "proposal.json").write_text(json.dumps(proposal))
    receipt = json.loads((directory / "receipt.json").read_text())
    receipt["candidate_id"] = duplicate
    (directory / "receipt.json").write_text(json.dumps(receipt))
    for row in (state["attempts"][1], state["iterations"][1]):
        row["candidate_id"] = duplicate
        row["candidate"]["candidate_id"] = duplicate
        row["proposal_hash"] = canonical_json_hash(proposal)
        row["receipt_hash"] = canonical_json_hash(receipt)
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="candidate IDs"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )

    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "unstarted")
    state = json.loads(campaign_path.read_text())
    state["attempts"][0]["agent_started"] = False
    state["iterations"][0]["agent_started"] = False
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="agent_started"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )

    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "source")
    state = json.loads(campaign_path.read_text())
    proposal_path = campaign_path.parent / "attempts" / "e0000-a00" / "proposal.json"
    proposal = json.loads(proposal_path.read_text())
    proposal["candidate"]["provenance"]["source"] = "skillrace"
    proposal_path.write_text(json.dumps(proposal))
    for row in (state["attempts"][0], state["iterations"][0]):
        row["provenance"]["source"] = "skillrace"
        row["candidate"]["provenance"]["source"] = "skillrace"
        row["proposal_hash"] = canonical_json_hash(proposal)
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="bootstrap provenance"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )


def test_random_adapter_requires_fresh_independent_provenance(tmp_path):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(
        tmp_path / "campaign", method="random"
    )
    state = json.loads(campaign_path.read_text())
    proposal_path = campaign_path.parent / "attempts" / "e0000-a00" / "proposal.json"
    proposal = json.loads(proposal_path.read_text())
    proposal["candidate"]["provenance"].pop("independent_test")
    proposal_path.write_text(json.dumps(proposal))
    for row in (state["attempts"][0], state["iterations"][0]):
        row["provenance"].pop("independent_test")
        row["candidate"]["provenance"].pop("independent_test")
        row["proposal_hash"] = canonical_json_hash(proposal)
    campaign_path.write_text(json.dumps(state))

    with pytest.raises(CampaignArtifactError, match="independent fresh"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="random",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )


def test_adapter_rejects_duplicate_agent_ids_and_wrong_raw_run_model(tmp_path):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "campaign")
    costs_path = campaign_path.parent / "rq3-costs.json"
    costs = json.loads(costs_path.read_text())
    costs["agent_ids"][1] = costs["agent_ids"][0]
    costs_path.write_text(json.dumps(costs))
    with pytest.raises(CampaignArtifactError, match="unique agent IDs"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )


def test_adapter_binds_campaign_records_to_proposal_and_receipt_payloads(tmp_path):
    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "proposal")
    state = json.loads(campaign_path.read_text())
    proposal_path = campaign_path.parent / "attempts" / "e0000-a00" / "proposal.json"
    proposal = json.loads(proposal_path.read_text())
    proposal["candidate"]["provenance"]["source"] = "skillrace"
    proposal_path.write_text(json.dumps(proposal))
    state["attempts"][0]["proposal_hash"] = canonical_json_hash(proposal)
    state["iterations"][0]["proposal_hash"] = canonical_json_hash(proposal)
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="proposal/campaign provenance"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )

    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "receipt")
    state = json.loads(campaign_path.read_text())
    receipt_path = campaign_path.parent / "attempts" / "e0000-a00" / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["result"]["agent_started"] = False
    receipt_path.write_text(json.dumps(receipt))
    state["attempts"][0]["receipt_hash"] = canonical_json_hash(receipt)
    state["iterations"][0]["receipt_hash"] = canonical_json_hash(receipt)
    campaign_path.write_text(json.dumps(state))
    with pytest.raises(CampaignArtifactError, match="receipt/campaign result"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )

    campaign_path, protocol_hash, base_hash = _write_real_campaign2(tmp_path / "raw-model")
    run_path = campaign_path.parent / "runs" / "agent-0000" / "run.json"
    run = json.loads(run_path.read_text())
    run["model"] = "other-model"
    run_path.write_text(json.dumps(run))
    with pytest.raises(CampaignArtifactError, match="raw run model"):
        validate_campaign_artifact(
            campaign_path,
            expected_method="skillrace",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
        )

def test_adapter_accepts_artifacts_emitted_by_the_actual_campaign_engine(tmp_path):
    root = tmp_path / "actual"
    protocol = CampaignProtocol.from_dict(
        {
            "schema": "campaign-protocol/1",
            "protocol_id": "skillrace-issta-main-v1",
            "status": "frozen",
            "model": "qwen3.6-flash",
            "budget": 30,
            "bootstrap_count": 10,
            "max_generation_attempts_per_execution": 5,
            "seed_generator": {"batch_size": 5, "temperature": 0.9, "build_retries": 4},
            "greybox_level": "L1",
            "random_seed": 20260711,
        }
    )
    base_hash = _digest("actual base")
    base_package_hash = _digest("actual package")
    stage_hash = _digest("actual stage")
    output_identity = _digest("actual output")
    prepare_campaign_input_record(
        root,
        method="skillrace",
        protocol_hash=protocol.hash,
        base_skill_hash=base_hash,
        base_package_hash=base_package_hash,
        public_stage_hash=stage_hash,
        output_identity=output_identity,
    )

    class Generator:
        def __init__(self, source):
            self.source = source
            self.index = 0
            self.folded = []

        def propose(self):
            candidate = {
                "candidate_id": f"{self.source}-{self.index:03d}",
                "provenance": {"source": self.source},
            }
            self.index += 1
            return candidate

        def fold(self, _candidate, _run, phase="explore", attempt_id=None):
            self.folded.append([attempt_id, phase])
            return None

        def snapshot(self):
            return {
                "source": self.source,
                "index": self.index,
                "folded": self.folded,
                "gen_cost_usd": 0.1,
            }

        def restore(self, value):
            self.index = value["index"]
            self.folded = list(value["folded"])

    class Executor:
        def execute(self, _candidate, execution_id, _attempt_id, lifecycle=None):
            run = root / "runs" / execution_id
            run.mkdir(parents=True, exist_ok=True)
            agent_id = f"agent-{execution_id}"
            (run / "cost.json").write_text(
                json.dumps({"usd": 0.01, "in": 10, "out": 2}) + "\n"
            )
            (run / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": agent_id,
                        "model": "qwen3.6-flash",
                        "agent_started": True,
                    }
                )
                + "\n"
            )
            if lifecycle:
                lifecycle("started", {"run_dir": f"runs/{execution_id}"})
            result = {
                "agent_started": True,
                "status": "completed",
                "runner_status": "completed",
                "oracle_status": "completed",
                "violated": [],
                "inconclusive": [],
                "cost_usd": 0.01,
                "input_tokens": 10,
                "output_tokens": 2,
                "run_id": agent_id,
                "run_dir": f"runs/{execution_id}",
            }
            if lifecycle:
                lifecycle("external-terminal", {"result": result})
            return result

    state = CampaignEngine(
        protocol=protocol,
        method="skillrace",
        skill="scenario-one",
        out_dir=root,
        output_identity=output_identity,
        generator=Generator("skillrace"),
        bootstrap_generator=Generator("bootstrap"),
        executor=Executor(),
        image_remover=lambda _image: None,
        image_inspector=lambda _image: False,
    ).run()
    assert state["complete"] is True
    derive_campaign_cost_record(root / "campaign.json")

    record = validate_campaign_artifact(
        root / "campaign.json",
        expected_method="skillrace",
        expected_protocol_hash=protocol.hash,
        expected_base_skill_hash=base_hash,
    )

    assert record["counted_executions"] == 30
    assert len(record["agent_ids"]) == 30
