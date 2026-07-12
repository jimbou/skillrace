from __future__ import annotations

import json

import pytest

import skillrace.generator as generator_module
from skillrace.campaign_engine import (
    CampaignEngine,
    CleanupRecoveryError,
    cleanup_candidate_image,
)

from tests.test_campaign_engine import FakeExecutor, FakeGenerator, SimulatedCrash, protocol


def test_owned_candidate_image_is_removed_once_with_durable_receipt(tmp_path):
    removed = []
    candidate = {
        "candidate_id": "c1",
        "built_image": "skillrace/c1:built",
        "base_image": "demo:base",
        "image_ownership": "campaign",
    }
    receipt = tmp_path / "cleanup.json"

    first = cleanup_candidate_image(candidate, remover=removed.append, receipt_path=receipt)
    second = cleanup_candidate_image(candidate, remover=removed.append, receipt_path=receipt)

    assert removed == ["skillrace/c1:built"]
    assert first == second
    assert first["status"] == "removed"
    assert candidate["image_cleaned"] is True


def test_missing_external_and_base_images_are_never_removed(tmp_path):
    removed = []
    missing = cleanup_candidate_image({}, remover=removed.append)
    external = cleanup_candidate_image(
        {
            "candidate_id": "c2",
            "built_image": "user/image:latest",
            "base_image": "demo:base",
            "image_ownership": "external",
        },
        remover=removed.append,
    )
    base = cleanup_candidate_image(
        {
            "candidate_id": "c3",
            "built_image": "demo:base",
            "base_image": "demo:base",
            "image_ownership": "campaign",
        },
        remover=removed.append,
    )

    assert removed == []
    assert [missing["status"], external["status"], base["status"]] == [
        "missing", "external", "base-image"
    ]


def test_cleanup_error_is_recorded_not_raised(tmp_path):
    def fail(image):
        raise RuntimeError("daemon unavailable")

    report = cleanup_candidate_image(
        {
            "candidate_id": "c1",
            "built_image": "skillrace/c1:built",
            "base_image": "demo:base",
            "image_ownership": "campaign",
        },
        remover=fail,
        receipt_path=tmp_path / "cleanup.json",
    )
    assert report["status"] == "error"
    assert "daemon unavailable" in report["error"]


def test_cleanup_error_evidence_is_immutable_but_a_later_call_can_complete(tmp_path):
    state = {"exists": True, "removals": 0}
    receipt = tmp_path / "cleanup.json"

    def transient_failure(image):
        state["removals"] += 1
        raise RuntimeError("daemon unavailable")

    first = cleanup_candidate_image(
        _owned_candidate(),
        remover=transient_failure,
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )
    evidence = tmp_path / "cleanup.attempts" / "v0000.json"
    assert first["status"] == "error"
    assert evidence.is_file()
    first_bytes = evidence.read_bytes()
    assert not receipt.exists()

    def successful_remove(image):
        state["removals"] += 1
        state["exists"] = False

    second = cleanup_candidate_image(
        _owned_candidate(),
        remover=successful_remove,
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )
    third = cleanup_candidate_image(
        _owned_candidate(),
        remover=lambda image: (_ for _ in ()).throw(AssertionError("removed twice")),
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )

    assert second["status"] == "removed"
    assert third == second
    assert state == {"exists": False, "removals": 2}
    assert evidence.read_bytes() == first_bytes
    assert (tmp_path / "cleanup.attempts" / "v0001.json").is_file()


def test_cleanup_retry_inspects_after_ambiguous_error_and_never_removes_absent_image(
    tmp_path,
):
    state = {"exists": True, "removals": 0}
    receipt = tmp_path / "cleanup.json"

    def removed_then_error(image):
        state["removals"] += 1
        state["exists"] = False
        raise RuntimeError("connection dropped after remove")

    first = cleanup_candidate_image(
        _owned_candidate(),
        remover=removed_then_error,
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )
    assert first["status"] == "error"

    final = cleanup_candidate_image(
        _owned_candidate(),
        remover=lambda image: (_ for _ in ()).throw(AssertionError("removed twice")),
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )

    assert final["status"] == "removed"
    assert final["recovered_after_intent"] is True
    assert state == {"exists": False, "removals": 1}


def test_terminal_cleanup_receipt_binds_all_prior_error_evidence(tmp_path):
    receipt = tmp_path / "cleanup.json"
    state = {"exists": True}

    cleanup_candidate_image(
        _owned_candidate(),
        remover=lambda image: (_ for _ in ()).throw(RuntimeError("temporary")),
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )

    def remove(image):
        state["exists"] = False

    cleanup_candidate_image(
        _owned_candidate(),
        remover=remove,
        image_exists=lambda image: state["exists"],
        receipt_path=receipt,
    )
    error_evidence = tmp_path / "cleanup.attempts" / "v0000.json"
    changed = json.loads(error_evidence.read_text())
    changed["error"] = "tampered"
    error_evidence.write_text(json.dumps(changed))

    with pytest.raises(ValueError, match="attempt history"):
        cleanup_candidate_image(
            _owned_candidate(),
            remover=lambda image: None,
            image_exists=lambda image: state["exists"],
            receipt_path=receipt,
        )


def test_engine_cleans_only_after_executor_final_consumer_and_once_across_resume(tmp_path):
    events = []

    class Generator(FakeGenerator):
        def propose(self):
            candidate = super().propose()
            candidate.update(
                {
                    "built_image": f"skillrace/{candidate['candidate_id']}:built",
                    "base_image": "demo:base",
                    "image_ownership": "campaign",
                }
            )
            return candidate

    class Executor(FakeExecutor):
        def execute(self, candidate, execution_id, attempt_id):
            events.append(("execute-final-consumer", candidate["built_image"]))
            return super().execute(candidate, execution_id, attempt_id)

    crashed = False

    def hook(event, context):
        nonlocal crashed
        if event == "after_receipt" and not crashed:
            crashed = True
            raise SimulatedCrash

    def remove(image):
        events.append(("remove", image))

    with pytest.raises(SimulatedCrash):
        CampaignEngine(
            protocol=protocol(budget=1, bootstrap=0), method="random", skill="demo",
            out_dir=tmp_path, generator=Generator("random"), executor=Executor(),
            image_remover=remove, fault_hook=hook,
        ).run()

    state = CampaignEngine(
        protocol=protocol(budget=1, bootstrap=0), method="random", skill="demo",
        out_dir=tmp_path, generator=Generator("random"), executor=Executor(),
        image_remover=remove,
    ).run()

    assert state["complete"] is True
    assert events == [
        ("execute-final-consumer", "skillrace/random-0:built"),
        ("remove", "skillrace/random-0:built"),
    ]
    cleanup = json.loads(
        (tmp_path / "attempts" / "e0000-a00" / "cleanup.json").read_text()
    )
    assert cleanup["status"] == "removed"


def test_shared_pipeline_removes_a_built_image_when_final_validation_rejects(
    monkeypatch,
):
    sanity = {
        "required_paths": ["/workspace"],
        "required_tools": ["bash"],
        "task_probe": {"command": "true", "allowed_exit_codes": [0]},
        "unsolved_check": None,
    }
    monkeypatch.setattr(
        generator_module,
        "realize",
        lambda *args, **kwargs: ("do it", "RUN true", sanity, 0.1),
    )
    monkeypatch.setattr(
        generator_module, "build_image", lambda *args, **kwargs: (True, "built")
    )
    removed = []

    artifact, cost, error = generator_module.realize_and_build(
        "ctx", "task", "env", "model", "demo:base", "candidate-1",
        build_retries=0,
        validator=lambda image: (False, "target absent"),
        failed_image_remover=removed.append,
    )

    assert artifact is None
    assert cost == 0.1
    assert "validation failed" in error
    assert removed == ["skillrace/candidate-1:built"]


def _owned_candidate():
    return {
        "candidate_id": "c1",
        "built_image": "skillrace/c1:built",
        "base_image": "demo:base",
        "image_ownership": "campaign",
    }


def test_cleanup_crash_after_intent_before_removal_retries_without_agent_effect(tmp_path):
    state = {"exists": True, "removals": 0}

    def remove(image):
        state["removals"] += 1
        state["exists"] = False

    def crash(event):
        if event == "after_intent":
            raise SimulatedCrash(event)

    receipt = tmp_path / "cleanup.json"
    with pytest.raises(SimulatedCrash):
        cleanup_candidate_image(
            _owned_candidate(), remover=remove,
            image_exists=lambda image: state["exists"],
            receipt_path=receipt, fault_hook=crash,
        )
    assert state["removals"] == 0
    assert (tmp_path / "cleanup.intent.json").is_file()
    assert not receipt.exists()

    report = cleanup_candidate_image(
        _owned_candidate(), remover=remove,
        image_exists=lambda image: state["exists"], receipt_path=receipt,
    )
    assert report["status"] == "removed"
    assert state["removals"] == 1


def test_cleanup_crash_after_successful_removal_inspects_and_never_removes_twice(tmp_path):
    state = {"exists": True, "removals": 0}

    def remove(image):
        state["removals"] += 1
        state["exists"] = False

    def crash(event):
        if event == "after_remove":
            raise SimulatedCrash(event)

    receipt = tmp_path / "cleanup.json"
    with pytest.raises(SimulatedCrash):
        cleanup_candidate_image(
            _owned_candidate(), remover=remove,
            image_exists=lambda image: state["exists"],
            receipt_path=receipt, fault_hook=crash,
        )
    assert state == {"exists": False, "removals": 1}
    assert not receipt.exists()

    report = cleanup_candidate_image(
        _owned_candidate(), remover=remove,
        image_exists=lambda image: state["exists"], receipt_path=receipt,
    )
    assert report["status"] == "removed"
    assert report["recovered_after_intent"] is True
    assert state["removals"] == 1


def test_cleanup_recovery_inspect_failure_blocks_commit_without_silent_leak(tmp_path):
    receipt = tmp_path / "cleanup.json"
    state = {"exists": True, "removals": 0}

    def remove(image):
        state["removals"] += 1
        state["exists"] = False

    with pytest.raises(SimulatedCrash):
        cleanup_candidate_image(
            _owned_candidate(), remover=remove,
            image_exists=lambda image: True,
            receipt_path=receipt,
            fault_hook=lambda event: (_ for _ in ()).throw(SimulatedCrash())
            if event == "after_intent" else None,
        )
    with pytest.raises(CleanupRecoveryError, match="inspect"):
        cleanup_candidate_image(
            _owned_candidate(), remover=lambda image: None,
            image_exists=lambda image: (_ for _ in ()).throw(RuntimeError("daemon down")),
            receipt_path=receipt,
        )
    assert not receipt.exists()
    report = cleanup_candidate_image(
        _owned_candidate(), remover=remove,
        image_exists=lambda image: state["exists"], receipt_path=receipt,
    )
    assert report["status"] == "removed"
    assert state == {"exists": False, "removals": 1}


def test_engine_cleanup_recovery_never_reexecutes_agent_after_remove_crash(tmp_path):
    calls = []
    image = {"exists": True, "removals": 0}

    class Generator(FakeGenerator):
        def propose(self):
            candidate = super().propose()
            candidate.update(_owned_candidate())
            candidate["candidate_id"] = "c1"
            return candidate

    class Executor(FakeExecutor):
        def execute(self, candidate, execution_id, attempt_id):
            calls.append(attempt_id)
            return super().execute(candidate, execution_id, attempt_id)

    def remove(value):
        image["removals"] += 1
        image["exists"] = False

    crashed = False

    def cleanup_hook(event):
        nonlocal crashed
        if event == "after_remove" and not crashed:
            crashed = True
            raise SimulatedCrash(event)

    with pytest.raises(SimulatedCrash):
        CampaignEngine(
            protocol=protocol(budget=1, bootstrap=0), method="random", skill="demo",
            out_dir=tmp_path, generator=Generator("random"), executor=Executor(),
            image_remover=remove, image_inspector=lambda value: image["exists"],
            cleanup_fault_hook=cleanup_hook,
        ).run()

    final = CampaignEngine(
        protocol=protocol(budget=1, bootstrap=0), method="random", skill="demo",
        out_dir=tmp_path, generator=Generator("random"), executor=Executor(),
        image_remover=remove, image_inspector=lambda value: image["exists"],
    ).run()
    assert final["complete"] is True
    assert calls == ["e0000-a00"]
    assert image["removals"] == 1
