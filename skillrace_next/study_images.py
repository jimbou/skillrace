from dataclasses import dataclass
import json
from pathlib import Path

from .storage import file_hash


DEFAULT_MANIFEST = Path("skillrace_next/study/base-images/manifest.json")
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
