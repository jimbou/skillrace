import json
from pathlib import Path

import pytest

from skillrace_next.storage import file_hash
from skillrace_next.study_images import capability_for_image


def test_capability_lookup_binds_the_exact_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-study-base-images/1",
                "images": [
                    {
                        "image_tag": (
                            "skillrace-next/study-part1-data-transform:2026-07-22"
                        ),
                        "capability_text": (
                            "Python 3.12, pytest, numpy, and pandas are installed."
                        ),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    context = capability_for_image(
        "skillrace-next/study-part1-data-transform:2026-07-22", manifest
    )

    assert context.image_tag.endswith("data-transform:2026-07-22")
    assert "numpy" in context.text
    assert context.manifest_hash == file_hash(manifest)


def test_capability_lookup_rejects_an_unknown_study_image(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"schema":"skillrace-study-base-images/1","images":[]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one capability"):
        capability_for_image(
            "skillrace-next/study-part1-unknown:2026-07-22", manifest
        )


def test_capability_lookup_has_an_explicit_temporary_fixture_record() -> None:
    context = capability_for_image("skillrace-next/task-fixture:test")

    assert "Python 3" in context.text
    assert "Node.js" in context.text
    assert context.manifest_hash == "fixture"
