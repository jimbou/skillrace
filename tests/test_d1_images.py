from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillrace.d1_images import (
    CONSTRUCTION_BASE,
    PI_VERSION,
    build_images,
    build_plan,
    construction_image_tag,
    track_image_tag,
    validate_image_locks,
)


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "experiments/manifests/rq1-skills.draft.json"


def test_dual_track_plan_builds_heavy_layers_once_and_two_tiny_overlays():
    plan = build_plan(SUITE)
    assert plan["pi_version"] == "0.73.1"
    assert plan["construction_base"] == CONSTRUCTION_BASE
    assert len(plan["construction"]) == 30
    assert list(plan["tracks"]) == ["glm-4.5-flash", "deepseek-v4-flash"]
    assert all(len(records) == 30 for records in plan["tracks"].values())
    assert construction_image_tag("demo") == f"skillrace/demo:base-construction-{PI_VERSION}"
    assert track_image_tag("demo", "glm-4.5-flash") == "skillrace/demo:base-glm-4.5-flash"


def test_image_plan_rejects_an_unsafe_skill_identifier(tmp_path):
    data = json.loads(SUITE.read_text())
    data["headline_skills"][0]["id"] = "demo; touch /tmp/injected"
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(data))
    with pytest.raises(Exception, match="skill identifier"):
        build_plan(path)


def test_every_headline_containerfile_has_a_pinned_overridable_construction_base():
    plan = build_plan(SUITE)
    for record in plan["construction"]:
        path = ROOT / "skills" / record["skill"] / "Containerfile.base"
        text = path.read_text(encoding="utf-8")
        assert (
            "ARG SKILLGEN_BASE_IMAGE=skillrace/skillgen-base:0.73.1-construction"
            in text
        )
        assert "FROM ${SKILLGEN_BASE_IMAGE}" in text
        assert "skillgen-base:latest" not in text


def test_track_overlay_bakes_exact_catalog_instead_of_runtime_secret_or_bind_mount():
    text = (ROOT / "images/skill-track/Dockerfile.skill-track").read_text()
    assert "COPY ${MODEL_CONFIG} /root/.pi/agent/models.json" in text
    assert "models.length!==1" in text
    assert 'apiKey!=="yunwu_key"' in text
    assert "Bearer" not in text


def test_per_skill_runtime_audit_executes_networkless_container(monkeypatch):
    import skillrace.d1_images as d1_images

    calls: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], *, cwd: Path) -> str:
        calls.append((command, cwd))
        return "audited\n"

    monkeypatch.setattr(d1_images, "_run", fake_run)

    result = d1_images._runtime_audit(
        "skillrace/demo:base-glm-4.5-flash",
        "demo",
        "glm-4.5-flash",
        cwd=ROOT,
    )

    assert result == "audited\n"
    assert len(calls) == 1
    command, cwd = calls[0]
    assert command[:4] == ["docker", "run", "--rm", "--network=none"]
    assert command[4] == "skillrace/demo:base-glm-4.5-flash"
    assert "/skills/demo/SKILL.md" in command[-1]
    assert "find /skills -mindepth 1 -maxdepth 1" in command[-1]
    assert "glm-4.5-flash" in command[-1]
    assert cwd == ROOT


def test_build_images_resumes_existing_construction_tags(tmp_path, monkeypatch):
    import skillrace.d1_images as d1_images

    plan = build_plan(SUITE)
    already_built = {
        record["image"] for record in plan["construction"][1:]
    }
    commands: list[list[str]] = []

    monkeypatch.setattr(
        d1_images,
        "_construction_is_current",
        lambda image, input_tree_hash, construction_base_id, *, cwd: (
            image in already_built
        ),
    )
    monkeypatch.setattr(
        d1_images,
        "_runtime_audit",
        lambda image, skill, model, *, cwd: "",
    )
    monkeypatch.setattr(
        d1_images,
        "_skillgen_runtime_audit",
        lambda image, model, *, cwd: "",
    )
    monkeypatch.setattr(
        d1_images,
        "_inspect_image",
        lambda image, *, cwd: "sha256:"
        + ("b" if image.endswith("deepseek-v4-flash") else "a") * 64,
    )

    def fake_run(command: list[str], cwd: Path) -> str:
        commands.append(command)
        return "ok\n"

    report = build_images(
        SUITE,
        repo_root=ROOT,
        output_dir=tmp_path,
        workers=2,
        command_runner=fake_run,
    )

    construction_builds = [
        command
        for command in commands
        if command[:2] == ["docker", "build"]
        and "Dockerfile.skill-track" not in " ".join(command)
    ]
    assert len(construction_builds) == 1
    assert plan["construction"][0]["skill"] in " ".join(construction_builds[0])
    assert report["construction_images"] == 30
    assert report["construction_images_reused"] == 29
    assert report["skillgen_track_images"] == 2
    validated = validate_image_locks(tmp_path, repo_root=ROOT)
    assert validated["models"] == ["glm-4.5-flash", "deepseek-v4-flash"]
    assert validated["images"] == 62


def test_frozen_lock_validation_uses_frozen_filenames(tmp_path):
    from skillrace.d1_images import D1ImageError

    with pytest.raises(D1ImageError, match=r"skillgen-track-images\.frozen\.json"):
        validate_image_locks(
            tmp_path, repo_root=ROOT, lock_status="frozen"
        )
