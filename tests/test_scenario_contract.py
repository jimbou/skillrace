from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from skillrace.scenario_contract import (
    ContractError,
    EvidenceState,
    audit_static,
    contract_identity_for_manifest,
    duplicate_identity_errors,
    exact_id_error,
    load_scenario,
    load_test,
    main,
    public_leakage_matches,
    refresh_hashes,
    tree_hash,
    validate_root,
)


ROOT = Path(__file__).parents[1] / "scenarios"
SCENARIOS = (
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


def test_repository_has_exactly_ten_scenarios_and_ten_tests_each():
    report = validate_root(ROOT)
    assert report.scenario_count == 10
    assert report.test_count == 100
    assert report.check_count == 192
    assert tuple(sorted(report.scenario_ids)) == tuple(sorted(SCENARIOS))
    assert report.errors == ()


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_scenario_contract_freezes_public_hidden_boundary(scenario_id: str):
    contract = load_scenario(ROOT / scenario_id)
    assert contract.scenario_id == scenario_id
    assert contract.hidden_tests_dir == ROOT / scenario_id / "tests"
    assert contract.public_paths
    assert all(contract.hidden_tests_dir not in path.parents for path in contract.public_paths)
    assert contract.expected_test_ids == tuple(f"{scenario_id}/t{i}" for i in range(1, 11))


def test_every_hidden_test_has_stable_hashes_semantics_and_oracle_records():
    identities: dict[str, str] = {}
    for test_dir in sorted(ROOT.glob("*/tests/t*")):
        contract = load_test(test_dir)
        assert contract.test_id == f"{test_dir.parents[1].name}/{test_dir.name}"
        assert contract.candidate_sha256 == hashlib.sha256(
            (test_dir / "candidate.json").read_bytes()
        ).hexdigest()
        assert contract.dockerfile_sha256 == hashlib.sha256(
            (test_dir / "Dockerfile").read_bytes()
        ).hexdigest()
        assert any(criterion.kind == "functional" for criterion in contract.criteria)
        assert all(criterion.expected_status in {"zero", "nonzero"} for criterion in contract.criteria)
        assert all(criterion.expected_output for criterion in contract.criteria)
        assert contract.reference_overlay.is_dir()
        assert contract.reference_sha256 == tree_hash(contract.reference_overlay)
        assert contract.negative_implementations
        assert all(negative.overlay.is_dir() for negative in contract.negative_implementations)
        assert all(negative.overlay_sha256 == tree_hash(negative.overlay)
                   for negative in contract.negative_implementations)
        assert len(contract.contract_identity_sha256) == 64
        assert contract.evidence.path.is_file()
        assert contract.evidence.state in {EvidenceState.PENDING_DOCKER, EvidenceState.VALIDATED}
        previous = identities.setdefault(contract.content_identity_sha256, contract.test_id)
        assert previous == contract.test_id or contract.duplicate_justification


def test_text_template_prompts_define_double_brace_placeholders():
    prefix = (
        "Replace every {{key}} placeholder in the template with data[key]. "
        "A key absent from the data leaves the double-brace placeholder text EXACTLY as written. "
        "Single braces like {x} are left untouched."
    )
    for path in sorted((ROOT / "text-template/tests").glob("t*/candidate.json")):
        prompt = json.loads(path.read_text(encoding="utf-8"))["prompt"]
        assert prefix in prompt
        assert "Replace every {key} in the template" not in prompt


def test_static_audit_reports_no_weak_scripts_after_hardening():
    report = audit_static(ROOT)
    assert report.total_scripts == 192
    assert report.weak_scripts == ()


def test_lint_entrypoint_is_location_independent(tmp_path: Path):
    result = subprocess.run(
        ["bash", str(ROOT / "lint_checks.sh")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"tests": 100' in result.stdout
    assert '"pending_docker": 0' in result.stdout
    assert '"runtime_ready": true' in result.stdout


def test_readme_records_validated_runtime_evidence():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())
    assert "100 validated Docker evidence records" in normalized
    assert "all 215 assigned negative-criterion pairs are killed" in normalized
    assert "runtime_ready=true" in normalized


def test_implementation_status_records_runtime_oracle_gate():
    status = (ROOT.parent / "docs/implementation-status.md").read_text(encoding="utf-8")
    normalized = " ".join(status.split())
    assert "100 validated Docker evidence records" in normalized
    assert "all 192 checks run inside the built containers" in normalized


def test_json_csv_empty_array_criterion_has_behavioral_id_and_semantics():
    manifest = json.loads((ROOT / "json-csv/tests/t5/test.json").read_text(encoding="utf-8"))
    criterion = manifest["criteria"][0]
    assert criterion["id"] == "empty-array-valid-csv"
    assert criterion["script"] == "checks/no-crash.sh"
    assert "newly created" in criterion["expected"]["output"]
    assert "valid empty CSV" in criterion["expected"]["output"]


def _minimal_test_tree(tmp_path: Path) -> Path:
    test_dir = tmp_path / "demo" / "tests" / "t1"
    (test_dir / "checks").mkdir(parents=True)
    (test_dir / "oracle/reference").mkdir(parents=True)
    (test_dir / "oracle/negative/wrong").mkdir(parents=True)
    (test_dir / "oracle/evidence").mkdir(parents=True)
    (test_dir / "candidate.json").write_text(
        '{"skill":"demo","base_image":"example@sha256:abc","prompt":"public"}\n',
        encoding="utf-8",
    )
    (test_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (test_dir / "checks/behavior.sh").write_text(
        "#!/usr/bin/env bash\nset -u\n[ -f tool.py ] || exit 1\nrm -f out\n"
        "bash tool.py >out 2>err\nrc=$?\n[ \"$rc\" -eq 0 ] || exit 1\n"
        "[ -f out ] || exit 1\n[ \"$(cat out)\" = ok ]\n",
        encoding="utf-8",
    )
    (test_dir / "oracle/reference/tool.py").write_text("printf 'ok\\n'\n", encoding="utf-8")
    (test_dir / "oracle/negative/wrong/tool.py").write_text("printf 'bad\\n'\n", encoding="utf-8")
    (test_dir / "oracle/evidence/validation.json").write_text(
        json.dumps(
            {
                "schema": "skillrace-oracle-evidence/1",
                "test_id": "demo/t1",
                "state": "pending-docker",
                "contract_identity_sha256": None,
                "reason": "Docker validation has not run",
                "reference": None,
                "negative_implementations": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    candidate_hash = hashlib.sha256((test_dir / "candidate.json").read_bytes()).hexdigest()
    docker_hash = hashlib.sha256((test_dir / "Dockerfile").read_bytes()).hexdigest()
    script_hash = hashlib.sha256((test_dir / "checks/behavior.sh").read_bytes()).hexdigest()
    identity = hashlib.sha256(
        bytes.fromhex(candidate_hash)
        + bytes.fromhex(docker_hash)
        + bytes.fromhex(script_hash)
        + bytes.fromhex(tree_hash(test_dir / "oracle/reference"))
        + b"wrong\0"
        + bytes.fromhex(tree_hash(test_dir / "oracle/negative/wrong"))
    ).hexdigest()
    manifest = {
        "schema": "skillrace-hidden-test/1",
        "test_id": "demo/t1",
        "candidate_sha256": candidate_hash,
        "dockerfile_sha256": docker_hash,
        "content_identity_sha256": identity,
        "contract_identity_sha256": "0" * 64,
        "duplicate_justification": None,
        "entrypoint": "bash tool.py",
        "criteria": [
            {
                "id": "behavior",
                "script": "checks/behavior.sh",
                "script_sha256": script_hash,
                "kind": "functional",
                "expected": {"status": "zero", "output": "stdout is exactly ok"},
                "negative_ids": ["wrong"],
            }
        ],
        "reference_overlay": "oracle/reference",
        "reference_sha256": tree_hash(test_dir / "oracle/reference"),
        "negative_implementations": [
            {
                "id": "wrong",
                "overlay": "oracle/negative/wrong",
                "overlay_sha256": tree_hash(test_dir / "oracle/negative/wrong"),
                "fault": "prints wrong value",
            }
        ],
        "validation_evidence": "oracle/evidence/validation.json",
    }
    manifest["contract_identity_sha256"] = contract_identity_for_manifest(manifest)
    (test_dir / "test.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return test_dir


def test_loader_rejects_malformed_json(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    (test_dir / "test.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(ContractError, match="JSON"):
        load_test(test_dir)


def test_loader_rejects_malformed_bash(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    (test_dir / "checks/behavior.sh").write_text("if then\n", encoding="utf-8")
    with pytest.raises(ContractError, match="bash syntax"):
        load_test(test_dir)


def test_loader_rejects_missing_reference_or_negative_evidence(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    (test_dir / "oracle/evidence/validation.json").unlink()
    with pytest.raises(ContractError, match="evidence"):
        load_test(test_dir)


def test_loader_rejects_paths_that_escape_hidden_package(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    manifest = json.loads((test_dir / "test.json").read_text(encoding="utf-8"))
    manifest["reference_overlay"] = "../../public"
    (test_dir / "test.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="relative path"):
        load_test(test_dir)


def test_validated_evidence_cannot_claim_success_without_run_details(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    evidence_path = test_dir / "oracle/evidence/validation.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["state"] = "validated"
    (evidence_path).write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ContractError, match="validated evidence"):
        load_test(test_dir)


def test_exact_id_gate_rejects_missing_and_extra_hidden_tests():
    expected = ("demo/t1", "demo/t2")
    assert "missing" in exact_id_error(("demo/t1",), expected, "demo")
    assert "extra" in exact_id_error(("demo/t1", "demo/t2", "demo/t3"), expected, "demo")
    assert exact_id_error(expected, expected, "demo") is None


def test_duplicate_content_identity_requires_justification_on_both_tests():
    rows = (
        ("a" * 64, "demo/t1", None),
        ("a" * 64, "demo/t2", "same case, distinct replication"),
    )
    assert duplicate_identity_errors(rows) == (
        "duplicate content identity without justification: demo/t1, demo/t2",
    )
    justified = tuple((identity, test_id, "paired replication") for identity, test_id, _ in rows)
    assert duplicate_identity_errors(justified) == ()


def test_public_leakage_detector_reports_hidden_content(tmp_path: Path):
    public = tmp_path / "campaign.json"
    public.write_text('{"prompt":"SECRET HIDDEN PROMPT"}', encoding="utf-8")
    matches = public_leakage_matches((public,), (("demo/t1", "SECRET HIDDEN PROMPT"),))
    assert matches == (f"public leakage in {public}: contains demo/t1",)


def test_validate_test_cli_checks_one_hidden_package(tmp_path: Path, capsys):
    test_dir = _minimal_test_tree(tmp_path)
    assert main(["validate", "--test", str(test_dir)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["test_id"] == "demo/t1"
    assert output["checks"] == 1
    assert output["pending_docker"] is True


def test_refresh_hashes_updates_only_contract_digests_atomically(
    tmp_path: Path, capsys
):
    test_dir = _minimal_test_tree(tmp_path)
    manifest_path = test_dir / "test.json"
    evidence_path = test_dir / "oracle/evidence/validation.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_before = evidence_path.read_bytes()
    (test_dir / "candidate.json").write_text(
        '{"skill":"demo","base_image":"example@sha256:abc","prompt":"changed"}\n',
        encoding="utf-8",
    )

    assert main(["refresh-hashes", str(test_dir)]) == 0

    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_top = {
        key for key in before if before[key] != after[key]
    }
    assert changed_top == {
        "candidate_sha256",
        "content_identity_sha256",
        "contract_identity_sha256",
    }
    assert evidence_path.read_bytes() == evidence_before
    assert str(manifest_path) in capsys.readouterr().out
    assert load_test(test_dir).test_id == "demo/t1"


def test_reference_and_negative_tree_hashes_bind_contract_bytes(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    (test_dir / "oracle/reference/tool.py").write_text("printf 'changed\\n'\n", encoding="utf-8")
    with pytest.raises(ContractError, match="reference_sha256"):
        load_test(test_dir)


def test_refresh_refuses_to_preserve_stale_validated_evidence(tmp_path: Path):
    test_dir = _minimal_test_tree(tmp_path)
    manifest = json.loads((test_dir / "test.json").read_text(encoding="utf-8"))
    evidence_path = test_dir / "oracle/evidence/validation.json"
    evidence = {
        "schema": "skillrace-oracle-evidence/1",
        "test_id": "demo/t1",
        "state": "validated",
        "reason": "prior Docker audit passed",
        "contract_identity_sha256": manifest["contract_identity_sha256"],
        "validated_at": "2026-07-11T00:00:00+00:00",
        "image_digest": "sha256:old",
        "docker_version": "test",
        "reference": {"passed": True},
        "starting": {"rejected": True},
        "negative_implementations": {"wrong": {"killed_assigned": True}},
    }
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    (test_dir / "candidate.json").write_text(
        '{"skill":"demo","base_image":"example@sha256:abc","prompt":"changed"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="stale validated evidence"):
        refresh_hashes(test_dir)
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == "validated"


def test_audit_failed_is_nonzero_and_not_reported_as_pending(tmp_path: Path, capsys):
    test_dir = _minimal_test_tree(tmp_path)
    manifest = json.loads((test_dir / "test.json").read_text(encoding="utf-8"))
    evidence_path = test_dir / "oracle/evidence/validation.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema": "skillrace-oracle-evidence/1",
                "test_id": "demo/t1",
                "state": "audit-failed",
                "reason": "reference failed",
                "contract_identity_sha256": manifest["contract_identity_sha256"],
                "validated_at": "2026-07-11T00:00:00+00:00",
                "image_digest": "sha256:test",
                "docker_version": "test",
                    "reference": {
                        "passed": False,
                        "criteria": {
                            "behavior": {
                                "exit_code": 1,
                                "isolation": "fresh-container-per-criterion",
                            }
                        },
                    },
                    "starting": {
                        "rejected": True,
                        "criteria": {
                            "behavior": {
                                "exit_code": 1,
                                "isolation": "fresh-container-per-criterion",
                            }
                        },
                    },
                    "negative_implementations": {
                        "wrong": {
                            "killed_assigned": True,
                            "criteria": {
                                "behavior": {
                                    "exit_code": 1,
                                    "isolation": "fresh-container-per-criterion",
                                }
                            },
                        }
                    },
            }
        ),
        encoding="utf-8",
    )
    assert main(["validate", "--test", str(test_dir)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["pending_docker"] is False
    assert payload["audit_failed"] is True
    assert payload["errors"]
