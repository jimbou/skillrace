import hashlib
import json
from pathlib import Path

import pytest

from skillrace_next import storage
from skillrace_next.storage import (
    atomic_write_json,
    canonical_json_bytes,
    canonical_json_hash,
    file_hash,
    tree_hash,
)


def test_canonical_json_is_compact_and_order_independent() -> None:
    assert canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    assert canonical_json_hash({"a": 2, "b": 1}) == canonical_json_hash(
        {"b": 1, "a": 2}
    )


def test_file_hash_streams_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"skillrace-next")

    assert file_hash(path) == hashlib.sha256(b"skillrace-next").hexdigest()


def test_tree_hash_matches_a_byte_identical_copy(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "SKILL.md").write_text("instructions\n", encoding="utf-8")
        (root / "nested" / "data.json").write_text('{"value":1}\n', encoding="utf-8")

    assert tree_hash(first) == tree_hash(second)


def test_tree_hash_changes_with_relative_path_or_content(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one.txt").write_text("same", encoding="utf-8")
    (second / "two.txt").write_text("same", encoding="utf-8")
    assert tree_hash(first) != tree_hash(second)

    (second / "two.txt").write_text("different", encoding="utf-8")
    assert tree_hash(first) != tree_hash(second)


def test_atomic_write_json_preserves_prior_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "record.json"
    destination.write_text('{"prior":true}\n', encoding="utf-8")

    def fail_replace(source: Path | str, target: Path | str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_json(destination, {"new": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"prior": True}
