"""Build shared D1 environments and freeze tiny model-specific final overlays."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import re
import subprocess
from typing import Any, Callable

from .input_identity import skill_input_tree_hash
from .io_utils import atomic_write_json, canonical_json_hash
from .model_policy import EXPERIMENT_MODELS, skillgen_track_image


PI_VERSION = "0.73.1"
CONSTRUCTION_BASE = f"skillrace/skillgen-base:{PI_VERSION}-construction"
_IMAGE_ID_PREFIX = "sha256:"
_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SKILL_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class D1ImageError(RuntimeError):
    """A D1 image failed to build, audit, or match its lock."""


def construction_image_tag(skill: str) -> str:
    return f"skillrace/{skill}:base-construction-{PI_VERSION}"


def track_image_tag(skill: str, model: str) -> str:
    if model not in EXPERIMENT_MODELS:
        raise ValueError(f"unknown experiment model: {model}")
    return f"skillrace/{skill}:base-{model}"


def build_plan(suite_manifest: str | pathlib.Path) -> dict[str, Any]:
    data = json.loads(pathlib.Path(suite_manifest).read_text(encoding="utf-8"))
    records = data.get("headline_skills") if isinstance(data, dict) else None
    if not isinstance(records, list) or len(records) != 30:
        raise D1ImageError("D1 image plan requires exactly 30 headline skills")
    skills: list[str] = []
    for record in records:
        skill = record.get("id") if isinstance(record, dict) else None
        if not isinstance(skill, str) or not _SKILL_ID_RE.fullmatch(skill):
            raise D1ImageError("D1 image plan contains an unsafe skill identifier")
        skills.append(skill)
    if len(skills) != len(set(skills)):
        raise D1ImageError("D1 image plan contains duplicate skills")
    return {
        "schema": "d1-image-build-plan/1",
        "pi_version": PI_VERSION,
        "construction_base": CONSTRUCTION_BASE,
        "suite_manifest_hash": canonical_json_hash(data),
        "construction": [
            {"skill": skill, "image": construction_image_tag(skill)}
            for skill in skills
        ],
        "tracks": {
            model: [
                {"skill": skill, "image": track_image_tag(skill, model)}
                for skill in skills
            ]
            for model in EXPERIMENT_MODELS
        },
    }


def _run(command: list[str], *, cwd: pathlib.Path) -> str:
    process = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    output = (process.stdout or "") + (process.stderr or "")
    if process.returncode != 0:
        raise D1ImageError(
            f"command failed ({process.returncode}): {' '.join(command)}\n{output[-2000:]}"
        )
    return output


def _inspect_image(image: str, *, cwd: pathlib.Path) -> str:
    identity = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], cwd=cwd
    ).strip()
    if not identity.startswith(_IMAGE_ID_PREFIX) or len(identity) != 71:
        raise D1ImageError(f"Docker returned an invalid identity for {image}")
    return identity


def _inspect_labels(image: str, *, cwd: pathlib.Path) -> dict[str, str] | None:
    process = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0:
        return None
    parsed = json.loads(process.stdout.strip() or "null")
    return parsed if isinstance(parsed, dict) else {}


def _construction_is_current(
    image: str,
    input_tree_hash: str,
    construction_base_id: str,
    *,
    cwd: pathlib.Path,
) -> bool:
    labels = _inspect_labels(image, cwd=cwd)
    return bool(
        labels
        and labels.get("org.skillrace.input-tree-sha256") == input_tree_hash
        and labels.get("org.skillrace.construction-base-id") == construction_base_id
    )


def _runtime_audit(image: str, skill: str, model: str, *, cwd: pathlib.Path) -> str:
    script = (
        "set -euo pipefail; "
        f'test "$(pi --version 2>&1)" = "{PI_VERSION}"; '
        f'test -f "/skills/{skill}/SKILL.md"; '
        f'test "$(find /skills -mindepth 1 -maxdepth 1 -type d -printf \'%f\\n\')" = "{skill}"; '
        f'MODEL="{model}" node -e \'const fs=require("fs"); '
        'const c=JSON.parse(fs.readFileSync("/root/.pi/agent/models.json","utf8")); '
        'const m=c.providers?.yunwu?.models??[]; '
        'if(m.length!==1||m[0].id!==process.env.MODEL)process.exit(2)\'; '
        'test -z "$(git -C /workspace status --porcelain)"'
    )
    return _run(
        ["docker", "run", "--rm", "--network=none", image, "bash", "-lc", script],
        cwd=cwd,
    )


def _skillgen_runtime_audit(image: str, model: str, *, cwd: pathlib.Path) -> str:
    script = (
        "set -euo pipefail; "
        f'test "$(pi --version 2>&1)" = "{PI_VERSION}"; '
        'test "$(python3 --version 2>&1)" = "Python 3.11.2"; '
        "python3 -m pytest --version | grep -F 'pytest 7.2.1'; "
        'test -z "$(git -C /workspace status --porcelain)"; '
        f'MODEL="{model}" node -e \'const fs=require("fs"); '
        'const c=JSON.parse(fs.readFileSync("/root/.pi/agent/models.json","utf8")); '
        'const m=c.providers?.yunwu?.models??[]; '
        'if(m.length!==1||m[0].id!==process.env.MODEL)process.exit(2)\''
    )
    return _run(
        ["docker", "run", "--rm", "--network=none", image, "bash", "-lc", script],
        cwd=cwd,
    )


def build_images(
    suite_manifest: str | pathlib.Path,
    *,
    repo_root: str | pathlib.Path,
    output_dir: str | pathlib.Path,
    workers: int = 3,
    command_runner: Callable[[list[str], pathlib.Path], str] | None = None,
) -> dict[str, Any]:
    """Build 30 heavy environments once, then 60 cheap track overlays."""

    if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    root = pathlib.Path(repo_root).resolve()
    manifest_path = pathlib.Path(suite_manifest).resolve()
    out = pathlib.Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    plan = build_plan(manifest_path)
    run = command_runner or (lambda command, cwd: _run(command, cwd=cwd))
    construction_base_id = _inspect_image(CONSTRUCTION_BASE, cwd=root)

    skillgen_records: list[dict[str, Any]] = []
    for model in EXPERIMENT_MODELS:
        image = skillgen_track_image(model)
        config = root / "images/pi-base" / f"models.yunwu.{model}.json"
        output = run(
            [
                "docker", "build", "--build-arg", f"SKILL_IMAGE={CONSTRUCTION_BASE}",
                "--build-arg", f"TRACK_MODEL={model}", "--build-arg",
                f"MODEL_CONFIG=models.yunwu.{model}.json", "--tag", image,
                "--file", str(root / "images/skill-track/Dockerfile.skill-track"),
                str(root / "images/pi-base"),
            ],
            root,
        )
        (out / f"skillgen-overlay.{model}.log").write_text(output, encoding="utf-8")
        _skillgen_runtime_audit(image, model, cwd=root)
        skillgen_records.append(
            {
                "model": model,
                "tag": image,
                "image_id": _inspect_image(image, cwd=root),
                "model_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "runtime_audit": "passed-networkless",
            }
        )
    atomic_write_json(
        out / "skillgen-track-images.draft.json",
        {
            "schema": "skillrace-skillgen-track-images/1",
            "status": "draft",
            "pi_version": PI_VERSION,
            "construction_base": CONSTRUCTION_BASE,
            "construction_base_id": construction_base_id,
            "records": skillgen_records,
        },
    )

    def construct(record: dict[str, str]) -> dict[str, Any]:
        skill = record["skill"]
        directory = root / "skills" / skill
        image = record["image"]
        input_tree_hash = skill_input_tree_hash(directory)
        if _construction_is_current(
            image, input_tree_hash, construction_base_id, cwd=root
        ):
            return {"skill": skill, "image": image, "reused": True}
        try:
            output = run(
                [
                    "docker", "build", "--build-arg",
                    f"SKILLGEN_BASE_IMAGE={CONSTRUCTION_BASE}",
                    "--label", f"org.skillrace.input-tree-sha256={input_tree_hash}",
                    "--label", f"org.skillrace.construction-base-id={construction_base_id}",
                    "--tag", image, "--tag", f"skillrace/{skill}:base",
                    "--file", str(directory / "Containerfile.base"), str(directory),
                ],
                root,
            )
        except Exception as exc:
            (out / f"construction.{skill}.failed.log").write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )
            raise
        (out / f"construction.{skill}.log").write_text(output, encoding="utf-8")
        (out / f"construction.{skill}.failed.log").unlink(missing_ok=True)
        return {"skill": skill, "image": image, "reused": False}

    constructed: list[dict[str, Any]] = []
    failures: list[tuple[str, Exception]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(construct, record): record["skill"]
            for record in plan["construction"]
        }
        for future in concurrent.futures.as_completed(futures):
            skill = futures[future]
            try:
                constructed.append(future.result())
            except Exception as exc:
                failures.append((skill, exc))
    if failures:
        summary = "\n".join(
            f"- {skill}: {type(exc).__name__}: {exc}" for skill, exc in failures
        )
        raise D1ImageError(f"{len(failures)} construction image(s) failed:\n{summary}")
    constructed.sort(key=lambda record: record["skill"])

    config_hashes = {}
    records_by_model: dict[str, list[dict[str, Any]]] = {}
    for model in EXPERIMENT_MODELS:
        config = root / "images" / "pi-base" / f"models.yunwu.{model}.json"
        config_hashes[model] = hashlib.sha256(config.read_bytes()).hexdigest()

        def overlay(record: dict[str, str]) -> dict[str, Any]:
            skill = record["skill"]
            source = construction_image_tag(skill)
            image = record["image"]
            output = run(
                [
                    "docker", "build", "--build-arg", f"SKILL_IMAGE={source}",
                    "--build-arg", f"TRACK_MODEL={model}", "--build-arg",
                    f"MODEL_CONFIG=models.yunwu.{model}.json", "--tag", image,
                    "--file", str(root / "images/skill-track/Dockerfile.skill-track"),
                    str(root / "images/pi-base"),
                ],
                root,
            )
            (out / f"overlay.{model}.{skill}.log").write_text(output, encoding="utf-8")
            _runtime_audit(image, skill, model, cwd=root)
            return {
                "skill": skill,
                "tag": image,
                "image_id": _inspect_image(image, cwd=root),
                "construction_image_id": _inspect_image(source, cwd=root),
                "input_tree_hash": skill_input_tree_hash(root / "skills" / skill),
                "model_config_sha256": config_hashes[model],
                "runtime_audit": "passed-networkless",
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            records_by_model[model] = list(
                executor.map(overlay, plan["tracks"][model])
            )
        lock = {
            "schema": "d1-track-images/1",
            "status": "draft",
            "model": model,
            "pi_version": PI_VERSION,
            "suite_manifest": str(manifest_path.relative_to(root)),
            "suite_manifest_hash": plan["suite_manifest_hash"],
            "construction_base": CONSTRUCTION_BASE,
            "records": records_by_model[model],
        }
        atomic_write_json(out / f"d1-images.{model}.draft.json", lock)

    report = {
        "schema": "d1-dual-track-images/1",
        "status": "draft",
        "pi_version": PI_VERSION,
        "suite_manifest_hash": plan["suite_manifest_hash"],
        "construction_images": len(constructed),
        "construction_images_reused": sum(
            bool(record["reused"]) for record in constructed
        ),
        "skillgen_track_images": len(skillgen_records),
        "track_images": {model: len(records_by_model[model]) for model in EXPERIMENT_MODELS},
        "locks": {
            model: (
                (out / f"d1-images.{model}.draft.json").relative_to(root).as_posix()
                if (out / f"d1-images.{model}.draft.json").is_relative_to(root)
                else str(out / f"d1-images.{model}.draft.json")
            )
            for model in EXPERIMENT_MODELS
        },
    }
    atomic_write_json(out / "d1-images.dual-model.draft.json", report)
    return report


def validate_image_locks(
    lock_dir: str | pathlib.Path,
    *,
    repo_root: str | pathlib.Path,
    require_images: bool = False,
    lock_status: str = "draft",
) -> dict[str, Any]:
    """Validate both track locks offline, optionally against the local Docker daemon."""

    if lock_status not in {"draft", "frozen"}:
        raise ValueError("lock_status must be draft or frozen")
    root = pathlib.Path(repo_root).resolve()
    directory = pathlib.Path(lock_dir).resolve()
    locks: dict[str, dict[str, Any]] = {}
    skills_by_model: dict[str, list[str]] = {}
    skillgen_path = directory / f"skillgen-track-images.{lock_status}.json"
    try:
        skillgen = json.loads(skillgen_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise D1ImageError(f"cannot read Skillgen track-image lock: {skillgen_path}") from exc
    if (
        skillgen.get("schema") != "skillrace-skillgen-track-images/1"
        or skillgen.get("status") != lock_status
        or skillgen.get("pi_version") != PI_VERSION
        or skillgen.get("construction_base") != CONSTRUCTION_BASE
        or not _IMAGE_ID_RE.fullmatch(str(skillgen.get("construction_base_id", "")))
    ):
        raise D1ImageError("Skillgen track-image lock header mismatch")
    generic_records = skillgen.get("records")
    if not isinstance(generic_records, list) or [
        record.get("model") for record in generic_records if isinstance(record, dict)
    ] != list(EXPERIMENT_MODELS):
        raise D1ImageError("Skillgen track-image model inventory drifted")
    for record in generic_records:
        model = record["model"]
        config = root / "images/pi-base" / f"models.yunwu.{model}.json"
        if (
            record.get("tag") != skillgen_track_image(model)
            or not _IMAGE_ID_RE.fullmatch(str(record.get("image_id", "")))
            or record.get("model_config_sha256")
            != hashlib.sha256(config.read_bytes()).hexdigest()
            or record.get("runtime_audit") != "passed-networkless"
        ):
            raise D1ImageError(f"Skillgen track-image record drifted: {model}")
        if require_images:
            if _inspect_image(record["tag"], cwd=root) != record["image_id"]:
                raise D1ImageError(f"local Skillgen track image drifted: {model}")
            _skillgen_runtime_audit(record["tag"], model, cwd=root)
    if len({record["image_id"] for record in generic_records}) != len(EXPERIMENT_MODELS):
        raise D1ImageError("Skillgen model-track images are not distinct")

    for model in EXPERIMENT_MODELS:
        path = directory / f"d1-images.{model}.{lock_status}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D1ImageError(f"cannot read D1 image lock: {path}") from exc
        if (
            value.get("schema") != "d1-track-images/1"
            or value.get("status") != lock_status
            or value.get("model") != model
            or value.get("pi_version") != PI_VERSION
            or value.get("construction_base") != CONSTRUCTION_BASE
        ):
            raise D1ImageError(f"D1 image lock header mismatch: {path}")
        manifest = root / value.get("suite_manifest", "")
        suite = json.loads(manifest.read_text(encoding="utf-8"))
        if canonical_json_hash(suite) != value.get("suite_manifest_hash"):
            raise D1ImageError(f"D1 suite manifest drifted for {model}")
        expected_skills = [record["id"] for record in suite["headline_skills"]]
        records = value.get("records")
        if not isinstance(records, list) or len(records) != len(expected_skills):
            raise D1ImageError(f"D1 track lock does not contain 30 records for {model}")
        actual_skills: list[str] = []
        config = root / "images/pi-base" / f"models.yunwu.{model}.json"
        config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        for record in records:
            skill = record.get("skill") if isinstance(record, dict) else None
            if not isinstance(skill, str):
                raise D1ImageError(f"malformed D1 record in {path}")
            actual_skills.append(skill)
            expected_input = skill_input_tree_hash(root / "skills" / skill)
            if (
                record.get("tag") != track_image_tag(skill, model)
                or not _IMAGE_ID_RE.fullmatch(str(record.get("image_id", "")))
                or not _IMAGE_ID_RE.fullmatch(
                    str(record.get("construction_image_id", ""))
                )
                or record.get("input_tree_hash") != expected_input
                or record.get("model_config_sha256") != config_hash
                or record.get("runtime_audit") != "passed-networkless"
            ):
                raise D1ImageError(f"D1 image record drifted: {model}/{skill}")
            if require_images:
                if _inspect_image(record["tag"], cwd=root) != record["image_id"]:
                    raise D1ImageError(f"local final image drifted: {model}/{skill}")
                construction = construction_image_tag(skill)
                if (
                    _inspect_image(construction, cwd=root)
                    != record["construction_image_id"]
                    or not _construction_is_current(
                        construction,
                        expected_input,
                        _inspect_image(CONSTRUCTION_BASE, cwd=root),
                        cwd=root,
                    )
                ):
                    raise D1ImageError(f"local construction image drifted: {skill}")
                _runtime_audit(record["tag"], skill, model, cwd=root)
        if actual_skills != expected_skills or len(set(actual_skills)) != len(actual_skills):
            raise D1ImageError(f"D1 image lock skill inventory drifted for {model}")
        locks[model] = value
        skills_by_model[model] = actual_skills

    if len({tuple(skills) for skills in skills_by_model.values()}) != 1:
        raise D1ImageError("D1 model tracks do not contain the same ordered skill set")
    first, second = EXPERIMENT_MODELS
    for left, right in zip(locks[first]["records"], locks[second]["records"]):
        if left["construction_image_id"] != right["construction_image_id"]:
            raise D1ImageError(f"track construction layers differ for {left['skill']}")
        if left["image_id"] == right["image_id"]:
            raise D1ImageError(f"track final images are not distinct for {left['skill']}")
    return {
        "schema": "d1-dual-track-image-validation/1",
        "status": "passed",
        "models": list(EXPERIMENT_MODELS),
        "skills": len(skills_by_model[first]),
        "images": len(generic_records) + sum(
            len(lock["records"]) for lock in locks.values()
        ),
        "docker_audit": "passed" if require_images else "not-requested",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="experiments/manifests/rq1-skills.draft.json")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out", default="experiments/image-locks")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--require-images", action="store_true")
    parser.add_argument("--lock-status", choices=("draft", "frozen"), default="draft")
    args = parser.parse_args(argv)
    if args.plan_only and args.validate:
        parser.error("--plan-only and --validate are mutually exclusive")
    if args.plan_only:
        print(json.dumps(build_plan(args.suite), indent=2, sort_keys=True))
        return 0
    if args.validate:
        print(
            json.dumps(
                validate_image_locks(
                    args.out,
                    repo_root=args.repo_root,
                    require_images=args.require_images,
                    lock_status=args.lock_status,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    report = build_images(
        args.suite,
        repo_root=args.repo_root,
        output_dir=args.out,
        workers=args.workers,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
