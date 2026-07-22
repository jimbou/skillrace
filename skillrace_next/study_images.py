from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

from .storage import atomic_write_json, file_hash


DEFAULT_MANIFEST = Path("skillrace_next/study/base-images/manifest.json")
DEFAULT_SOURCE_ROOT = DEFAULT_MANIFEST.parent
PART1_SELECTION = Path("skillrace_next/study/part1/selection.json")
PART2_SELECTION = Path("skillrace_next/study/part2/selection.json")
FIXTURE_CAPABILITY_TEXT = (
    "Python 3, pytest, Node.js, npm, Bash/POSIX coreutils, Perl, and Git are "
    "installed. Go, Rust/Cargo, Ruby, jq, the TypeScript compiler, and ts-node "
    "are not installed. The root task agent may install additional packages online, "
    "but installation consumes the unchanged task budget."
)


@dataclass(frozen=True)
class ImageCapability:
    image_tag: str
    text: str
    manifest_hash: str


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def capability_for_image(
    image_tag: str,
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> ImageCapability:
    if image_tag == "skillrace-next/task-fixture:test":
        return ImageCapability(
            image_tag=image_tag,
            text=FIXTURE_CAPABILITY_TEXT,
            manifest_hash="fixture",
        )
    path = Path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("images") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("schema") != "skillrace-study-base-images/1"
        or not isinstance(entries, list)
    ):
        raise ValueError("study image manifest is invalid")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("image_tag") == image_tag
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one capability for {image_tag}")
    text = matches[0].get("capability_text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("study image capability text is invalid")
    return ImageCapability(
        image_tag=image_tag,
        text=text.strip(),
        manifest_hash=file_hash(path),
    )


def _selected_contexts(
    part1_selection: Path, part2_selection: Path
) -> list[tuple[str, str]]:
    part1 = json.loads(part1_selection.read_text(encoding="utf-8"))
    part2 = json.loads(part2_selection.read_text(encoding="utf-8"))
    selected = part1.get("selected") if isinstance(part1, dict) else None
    scenarios = part2.get("scenarios") if isinstance(part2, dict) else None
    if not isinstance(selected, list) or not isinstance(scenarios, list):
        raise ValueError("study selection is invalid")
    contexts = [("part1", item.get("skill_id")) for item in selected]
    contexts += [("part2", item.get("scenario_id")) for item in scenarios]
    if any(not isinstance(context_id, str) or not context_id for _, context_id in contexts):
        raise ValueError("study selection context ID is invalid")
    if len(set(contexts)) != len(contexts):
        raise ValueError("study selection contains duplicate contexts")
    return [(part, context_id) for part, context_id in contexts]


def validate_image_sources(
    source_root: str | Path,
    part1_selection: str | Path = PART1_SELECTION,
    part2_selection: str | Path = PART2_SELECTION,
) -> list[dict[str, Any]]:
    root = Path(source_root)
    expected = _selected_contexts(Path(part1_selection), Path(part2_selection))
    actual = {
        (part, directory.name)
        for part in ("part1", "part2")
        for directory in (root / part).iterdir()
        if directory.is_dir()
    }
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise ValueError(
            f"study image source coverage differs: missing={missing}, extra={extra}"
        )

    required_fields = {
        "schema",
        "part",
        "context_id",
        "image_tag",
        "base_image",
        "capability_text",
        "probes",
    }
    records: list[dict[str, Any]] = []
    for part, context_id in expected:
        directory = root / part / context_id
        dockerfile = directory / "Dockerfile"
        capability_path = directory / "capabilities.json"
        if not dockerfile.is_file() or not capability_path.is_file():
            raise ValueError(f"study image source is incomplete: {part}/{context_id}")
        capability = json.loads(capability_path.read_text(encoding="utf-8"))
        if not isinstance(capability, dict) or set(capability) != required_fields:
            raise ValueError("study image capability record is invalid")
        expected_tag = f"skillrace-next/study-{part}-{context_id}:2026-07-22"
        if (
            capability["schema"] != "skillrace-study-base-image-source/1"
            or capability["part"] != part
            or capability["context_id"] != context_id
            or capability["image_tag"] != expected_tag
            or capability["base_image"] != "skillrace/pi-runtime:0.73.1"
        ):
            raise ValueError("study image capability identity differs")
        text = capability["capability_text"]
        probes = capability["probes"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or "install additional packages online" not in text
            or not isinstance(probes, list)
            or not probes
            or any(
                not isinstance(probe, str)
                or not probe.strip()
                or "\n" in probe
                or "\r" in probe
                for probe in probes
            )
        ):
            raise ValueError("study image capabilities or probes are invalid")

        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        lines = [line.strip() for line in dockerfile_text.splitlines() if line.strip()]
        allowed_copy = "COPY --from=checker-python /usr/local /usr/local"
        if (
            "FROM skillrace/pi-runtime:0.73.1" not in lines
            or "WORKDIR /workspace" not in lines
            or any(line.startswith("ADD ") for line in lines)
            or any(line.startswith("COPY ") and line != allowed_copy for line in lines)
            or any(
                marker in dockerfile_text
                for marker in (
                    "RUN printf",
                    "RUN echo",
                    "RUN cat",
                    "> /workspace/",
                    "skillrace-next/task-fixture:test",
                )
            )
        ):
            raise ValueError("study image Dockerfile contains a task fixture")
        records.append(
            {
                **capability,
                "dockerfile_path": str(dockerfile),
                "dockerfile_hash": file_hash(dockerfile),
                "capability_path": str(capability_path),
                "capability_hash": file_hash(capability_path),
            }
        )
    tags = [record["image_tag"] for record in records]
    if len(set(tags)) != len(tags):
        raise ValueError("study image tags must be unique")
    return records


def _write_command_log(
    output: Path,
    name: str,
    result: subprocess.CompletedProcess[str],
) -> None:
    (output / f"{name}.stdout.txt").write_text(
        result.stdout or "", encoding="utf-8"
    )
    (output / f"{name}.stderr.txt").write_text(
        result.stderr or "", encoding="utf-8"
    )


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def build_study_images(
    source_root: str | Path = DEFAULT_SOURCE_ROOT,
    evidence_root: str | Path = Path("out/live-contracts/study-base-images"),
    run_id: str = "",
    *,
    part1_selection: str | Path = PART1_SELECTION,
    part2_selection: str | Path = PART2_SELECTION,
    command_runner: CommandRunner = subprocess.run,
) -> Path:
    if not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("study image run ID is invalid")
    root = Path(source_root)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        raise ValueError("study image manifest already exists")
    records = validate_image_sources(root, part1_selection, part2_selection)
    evidence = Path(evidence_root) / run_id
    evidence.mkdir(parents=True, exist_ok=False)
    built: list[dict[str, Any]] = []
    for ordinal, record in enumerate(records, 1):
        context_output = evidence / f"{ordinal:02d}-{record['part']}-{record['context_id']}"
        context_output.mkdir()
        dockerfile = Path(record["dockerfile_path"])
        build_command = [
            "docker",
            "build",
            "--tag",
            record["image_tag"],
            str(dockerfile.parent),
        ]
        try:
            build = command_runner(
                build_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = subprocess.CompletedProcess(
                build_command,
                124,
                _timeout_text(exc.stdout),
                _timeout_text(exc.stderr),
            )
            _write_command_log(context_output, "build", timed_out)
            atomic_write_json(
                context_output / "receipt.json",
                {
                    "schema": "skillrace-study-base-image-build/1",
                    **record,
                    "status": "failed",
                    "failure": "build_timeout",
                },
            )
            raise RuntimeError(
                f"Docker build timed out for {record['part']}/{record['context_id']}"
            ) from exc
        _write_command_log(context_output, "build", build)
        if build.returncode != 0:
            raise RuntimeError(
                f"Docker build failed for {record['part']}/{record['context_id']}"
            )
        inspect = command_runner(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                record["image_tag"],
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        _write_command_log(context_output, "inspect", inspect)
        image_id = (inspect.stdout or "").strip()
        if inspect.returncode != 0 or not image_id.startswith("sha256:"):
            raise RuntimeError(
                f"Docker inspect failed for {record['part']}/{record['context_id']}"
            )
        probe = command_runner(
            [
                "docker",
                "run",
                "--rm",
                record["image_tag"],
                "sh",
                "-lc",
                " && ".join(record["probes"]),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        _write_command_log(context_output, "probe", probe)
        if probe.returncode != 0:
            raise RuntimeError(
                f"Docker capability probe failed for {record['part']}/{record['context_id']}"
            )
        receipt_path = context_output / "receipt.json"
        receipt = {
            "schema": "skillrace-study-base-image-build/1",
            **record,
            "image_id": image_id,
            "status": "passed",
        }
        atomic_write_json(receipt_path, receipt)
        built.append(
            {
                **receipt,
                "receipt_path": str(receipt_path),
                "receipt_hash": file_hash(receipt_path),
            }
        )
    atomic_write_json(
        manifest_path,
        {
            "schema": "skillrace-study-base-images/1",
            "run_id": run_id,
            "image_count": len(built),
            "images": built,
        },
    )
    return manifest_path
