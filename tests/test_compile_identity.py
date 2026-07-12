import hashlib
import json
from types import SimpleNamespace

import pytest

import skillrace.compile_checks as compiler
from skillrace.compile_checks import compile_fingerprint
from skillrace.io_utils import file_hash


BASE = {
    "properties": [{"id": "p1", "nl": "must pass", "reads": "state"}],
    "candidate": {
        "candidate_id": "c1",
        "prompt": "fix it",
        "containerfile": "FROM base@sha256:one\nRUN true\n",
        "base_image": "base@sha256:one",
        "skill": "demo",
    },
    "image_digest": "sha256:image-one",
    "model": "model-a",
}


@pytest.mark.parametrize(
    ("candidate_field", "replacement"),
    [
        ("candidate_id", "c2"),
        ("prompt", "repair it"),
        ("containerfile", "FROM base@sha256:one\nRUN false\n"),
        ("base_image", "base@sha256:two"),
        ("skill", "other-skill"),
    ],
)
def test_compile_fingerprint_changes_for_every_candidate_input(
    candidate_field, replacement
):
    changed = {
        **BASE,
        "candidate": {**BASE["candidate"], candidate_field: replacement},
    }
    assert compile_fingerprint(**BASE) != compile_fingerprint(**changed)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("properties", [{"id": "p1", "nl": "must build", "reads": "state"}]),
        ("image_digest", "sha256:image-two"),
        ("model", "model-b"),
    ],
)
def test_compile_fingerprint_changes_for_every_non_candidate_input(
    field, replacement
):
    changed = {**BASE, field: replacement}
    assert compile_fingerprint(**BASE) != compile_fingerprint(**changed)


def test_compile_fingerprint_changes_with_prompt_version(monkeypatch):
    original = compile_fingerprint(**BASE)
    monkeypatch.setattr(compiler, "CHECK_PROMPT_VERSION", "compile-check-next")
    assert compile_fingerprint(**BASE) != original


def test_compile_fingerprint_changes_with_applicability_metadata():
    first = compile_fingerprint(
        **BASE,
        applicability={
            "property_ids": ["p1"],
            "fixed_invariants": ["fixed-no-force-push"],
            "categories": ["outcome-integrity"],
            "contingency": "high",
        },
    )
    second = compile_fingerprint(
        **BASE,
        applicability={
            "property_ids": ["p1"],
            "fixed_invariants": [],
            "categories": ["outcome-integrity"],
            "contingency": "high",
        },
    )
    assert first != second


def test_compile_fingerprint_changes_with_check_execution_policy():
    first = compile_fingerprint(**BASE)
    second = compile_fingerprint(
        **BASE,
        execution_policy={
            **compiler.CHECK_EXECUTION_POLICY,
            "timeout_seconds": 61,
        },
    )
    assert first != second


def _write_case(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    candidate = {**BASE["candidate"]}
    (case / "candidate.json").write_text(json.dumps(candidate))
    (case / "Dockerfile").write_text(candidate["containerfile"])
    return case, candidate


def test_matching_compile_fingerprint_reuses_manifest(tmp_path, monkeypatch):
    case, candidate = _write_case(tmp_path)
    checks = case / "checks"
    checks.mkdir()
    script = checks / "p1.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    expected_fingerprint = compile_fingerprint(**BASE)
    existing = {
        "fingerprint": expected_fingerprint,
        "property_ids": ["p1"],
        "checks": [{
            "property_id": "p1",
            "script": "p1.sh",
            "sha256": file_hash(script),
        }],
    }
    (checks / "manifest.json").write_text(json.dumps(existing))

    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda image: BASE["image_digest"]
    )

    def unexpected_probe(image):
        raise AssertionError("matching cache should not probe or re-author")

    monkeypatch.setattr(compiler, "probe_initial_env", unexpected_probe)
    manifest, cost = compiler.compile_case(
        case, BASE["properties"], BASE["model"], image="candidate:built"
    )
    assert manifest == existing
    assert cost == 0.0


@pytest.mark.parametrize("script_state", ["missing", "tampered"])
def test_matching_fingerprint_reauthors_missing_or_tampered_script(
    tmp_path, monkeypatch, script_state
):
    case, candidate = _write_case(tmp_path)
    checks = case / "checks"
    checks.mkdir()
    expected_script = "#!/usr/bin/env bash\ncd /workspace\nexit 0\n"
    expected_hash = hashlib.sha256(expected_script.encode()).hexdigest()
    script = checks / "p1.sh"
    if script_state == "tampered":
        script.write_text("#!/usr/bin/env bash\nexit 99\n")
    existing = {
        "fingerprint": compile_fingerprint(**BASE),
        "property_ids": ["p1"],
        "checks": [{
            "property_id": "p1",
            "script": "p1.sh",
            "sha256": expected_hash,
        }],
    }
    (checks / "manifest.json").write_text(json.dumps(existing))
    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda image: BASE["image_digest"]
    )
    monkeypatch.setattr(compiler, "probe_initial_env", lambda image: (["bash"], []))
    authored = []

    def fake_author(prop, skill, prompt, tools, tree, model, fix=None):
        authored.append(prop["id"])
        return expected_script, 0.1

    monkeypatch.setattr(compiler, "author_check", fake_author)

    manifest, cost = compiler.compile_case(
        case, BASE["properties"], BASE["model"], image="candidate:built"
    )
    assert authored == ["p1"]
    assert cost == 0.1
    assert manifest["checks"][0]["sha256"] == file_hash(script)


def test_stale_compile_fingerprint_reauthors_and_writes_atomically(
    tmp_path, monkeypatch
):
    case, candidate = _write_case(tmp_path)
    checks = case / "checks"
    checks.mkdir()
    (checks / "manifest.json").write_text(json.dumps({
        "fingerprint": "stale",
        "property_ids": ["p1"],
        "checks": [],
    }))
    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda image: BASE["image_digest"]
    )
    monkeypatch.setattr(compiler, "probe_initial_env", lambda image: (["bash"], ["x.py"]))
    authored = []

    def fake_author(prop, skill, prompt, tools, tree, model, fix=None):
        authored.append(prop["id"])
        return "#!/usr/bin/env bash\ncd /workspace\nexit 0\n", 0.25

    monkeypatch.setattr(compiler, "author_check", fake_author)
    writes = []

    def record_atomic_write(path, value):
        writes.append((path, value))
        path.write_text(json.dumps(value))

    monkeypatch.setattr(compiler, "atomic_write_json", record_atomic_write)

    manifest, cost = compiler.compile_case(
        case, BASE["properties"], BASE["model"], image="candidate:built"
    )
    assert authored == ["p1"]
    assert cost == 0.25
    assert manifest["fingerprint"] == compile_fingerprint(**BASE)
    assert manifest["properties"] == BASE["properties"]
    assert manifest["checks"][0]["sha256"] == file_hash(checks / "p1.sh")
    assert writes == [(checks / "manifest.json", manifest)]


def test_compiler_owned_image_is_removed_when_probe_fails(tmp_path, monkeypatch):
    case, candidate = _write_case(tmp_path)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(compiler.subprocess, "run", fake_run)
    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda image: BASE["image_digest"]
    )

    def failed_probe(image):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(compiler, "probe_initial_env", failed_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        compiler.compile_case(case, BASE["properties"], BASE["model"])

    build = next(command for command in commands if command[:2] == ["docker", "build"])
    remove = next(command for command in commands if command[:3] == ["docker", "rmi", "-f"])
    assert remove[3] == build[4]
    assert remove[3].startswith("skillrace/compile-")


def test_external_candidate_image_is_never_removed(tmp_path, monkeypatch):
    case, candidate = _write_case(tmp_path)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(compiler.subprocess, "run", fake_run)
    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda image: BASE["image_digest"]
    )
    monkeypatch.setattr(compiler, "probe_initial_env", lambda image: (["bash"], []))
    monkeypatch.setattr(
        compiler,
        "author_check",
        lambda *args, **kwargs: (
            "#!/usr/bin/env bash\ncd /workspace\nexit 0\n",
            0.0,
        ),
    )

    compiler.compile_case(
        case, BASE["properties"], BASE["model"], image="candidate:built"
    )
    assert not any(command[:3] == ["docker", "rmi", "-f"] for command in commands)


def test_compile_manifest_records_applicability(tmp_path, monkeypatch):
    case, candidate = _write_case(tmp_path)
    applicability = {
        "property_ids": ["p1"],
        "fixed_invariants": ["fixed-no-force-push"],
        "categories": ["outcome-integrity"],
        "contingency": "high",
    }
    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda image: BASE["image_digest"]
    )
    monkeypatch.setattr(compiler, "probe_initial_env", lambda image: (["bash"], []))
    monkeypatch.setattr(
        compiler,
        "author_check",
        lambda *args, **kwargs: (
            "#!/usr/bin/env bash\ncd /workspace\nexit 0\n",
            0.0,
        ),
    )

    manifest, _ = compiler.compile_case(
        case,
        BASE["properties"],
        BASE["model"],
        image="candidate:built",
        applicability=applicability,
    )

    assert manifest["applicability"] == applicability
