import hashlib
import json

import pytest

import skillrace.io_utils as io_utils
from skillrace.io_utils import (
    _fsync_directory,
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    canonical_json_hash,
    file_hash,
)


def test_canonical_json_bytes_are_stable_and_compact():
    assert canonical_json_bytes({"b": 2, "a": "café"}) == (
        b'{"a":"caf\xc3\xa9","b":2}'
    )


def test_canonical_json_hash_ignores_mapping_order():
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash(
        {"a": 1, "b": 2}
    )
    assert canonical_json_hash({"a": 1}) != canonical_json_hash({"a": 2})


def test_file_hash_reads_file_bytes(tmp_path):
    path = tmp_path / "payload.bin"
    payload = b"skillrace\x00artifact\n"
    path.write_bytes(payload)
    assert file_hash(path) == hashlib.sha256(payload).hexdigest()


def test_atomic_write_text_creates_parent_and_replaces_complete_text(tmp_path):
    path = tmp_path / "nested" / "artifact.txt"
    atomic_write_text(path, "old")
    atomic_write_text(path, "new\n")
    assert path.read_text() == "new\n"
    assert list(path.parent.glob(".artifact.txt.*.tmp")) == []


def test_atomic_write_json_replaces_complete_document(tmp_path):
    path = tmp_path / "campaign.json"
    atomic_write_json(path, {"iterations": [1]})
    atomic_write_json(path, {"iterations": [1, 2]})
    assert json.loads(path.read_text()) == {"iterations": [1, 2]}
    assert path.read_text().endswith("\n")
    assert list(tmp_path.glob(".campaign.json.*.tmp")) == []


def test_atomic_write_json_preserves_old_file_when_replace_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "campaign.json"
    atomic_write_json(path, {"state": "old"})

    def fail_replace(source, destination):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr("skillrace.io_utils.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_json(path, {"state": "new"})
    assert json.loads(path.read_text()) == {"state": "old"}
    assert list(tmp_path.glob(".campaign.json.*.tmp")) == []


def test_atomic_write_fsyncs_parent_after_replace(tmp_path, monkeypatch):
    path = tmp_path / "artifact.txt"
    events = []
    real_replace = io_utils.os.replace

    def record_replace(source, destination):
        real_replace(source, destination)
        events.append("replace")

    monkeypatch.setattr(io_utils.os, "replace", record_replace)
    monkeypatch.setattr(
        io_utils, "_fsync_directory", lambda directory: events.append(("fsync", directory))
    )
    atomic_write_text(path, "complete")
    assert events == ["replace", ("fsync", tmp_path)]


def test_fsync_directory_syncs_and_closes_open_directory(tmp_path, monkeypatch):
    opened = []
    synced = []
    closed = []

    def fake_open(path, flags):
        opened.append((path, flags))
        return 987

    monkeypatch.setattr(io_utils.os, "open", fake_open)
    monkeypatch.setattr(io_utils.os, "fsync", synced.append)
    monkeypatch.setattr(io_utils.os, "close", closed.append)
    _fsync_directory(tmp_path)

    assert opened and opened[0][0] == tmp_path
    assert synced == [987]
    assert closed == [987]
