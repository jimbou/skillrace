from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from skillrace.artifact_freeze import (
    ArtifactFreezeError,
    freeze_dual_protocol_data,
    freeze_protocol_data,
    freeze_skillgen_lock_data,
    freeze_suite_data,
    freeze_track_lock_data,
    hash_inventory,
)
from skillrace.io_utils import canonical_json_hash
from skillrace.input_identity import skill_input_tree_hash


ROOT = Path(__file__).resolve().parents[1]
MODELS = ("glm-4.5-flash", "deepseek-v4-flash")


def _draft_suite():
    return json.loads(
        (ROOT / "experiments/manifests/rq1-skills.draft.json").read_text()
    )


def _track_locks():
    suite = _draft_suite()
    locks = {}
    for model_index, model in enumerate(MODELS, 1):
        config = ROOT / f"images/pi-base/models.yunwu.{model}.json"
        records = []
        for skill_index, skill in enumerate(suite["headline_skills"], 1):
            construction = "sha256:" + f"{skill_index:064x}"
            final_number = model_index * 1000 + skill_index
            records.append(
                {
                    "skill": skill["id"],
                    "tag": f"skillrace/{skill['id']}:base-{model}",
                    "image_id": "sha256:" + f"{final_number:064x}",
                    "construction_image_id": construction,
                    "input_tree_hash": skill_input_tree_hash(
                        ROOT / "skills" / skill["id"]
                    ),
                    "model_config_sha256": hashlib.sha256(
                        config.read_bytes()
                    ).hexdigest(),
                    "runtime_audit": "passed-networkless",
                }
            )
        locks[model] = {
            "schema": "d1-track-images/1",
            "status": "draft",
            "model": model,
            "pi_version": "0.73.1",
            "suite_manifest": "experiments/manifests/rq1-skills.draft.json",
            "suite_manifest_hash": "0" * 64,
            "construction_base": "skillrace/skillgen-base:0.73.1-construction",
            "records": records,
        }
    return locks


def test_protocol_freeze_changes_only_identity_and_status():
    for model in MODELS:
        draft = json.loads(
            (ROOT / f"experiments/protocols/issta-main.{model}.draft.json").read_text()
        )
        frozen = freeze_protocol_data(draft, model=model)
        assert frozen["status"] == "frozen"
        assert frozen["protocol_id"] == f"skillrace-issta-main-{model}-v1"
        comparable = copy.deepcopy(frozen)
        comparable["status"] = draft["status"]
        comparable["protocol_id"] = draft["protocol_id"]
        assert comparable == draft


def test_dual_protocol_freeze_rebinds_both_tracks_without_pooling():
    draft = json.loads(
        (ROOT / "experiments/protocols/issta-main.dual-model.draft.json").read_text()
    )
    frozen_protocols = {
        model: freeze_protocol_data(
            json.loads(
                (
                    ROOT
                    / f"experiments/protocols/issta-main.{model}.draft.json"
                ).read_text()
            ),
            model=model,
        )
        for model in MODELS
    }
    frozen = freeze_dual_protocol_data(draft, protocols=frozen_protocols)
    assert frozen["status"] == "frozen"
    assert frozen["experiment_id"] == "skillrace-issta-dual-model-v1"
    assert frozen["reporting"] == "separate-primary-tables-plus-unpooled-robustness"
    assert [row["model"] for row in frozen["tracks"]] == list(MODELS)
    assert [row["protocol"] for row in frozen["tracks"]] == [
        f"experiments/protocols/issta-main.{model}.frozen.json" for model in MODELS
    ]


def test_suite_freeze_binds_each_skill_to_shared_construction_and_input_hash():
    frozen = freeze_suite_data(
        _draft_suite(), track_locks=_track_locks(), repo_root=ROOT
    )
    assert frozen["status"] == "frozen"
    assert frozen["suite_id"] == "skillrace-d1-public-v1"
    assert len(frozen["headline_skills"]) == 30
    for index, skill in enumerate(frozen["headline_skills"], 1):
        assert skill["input_tree_hash"] == skill_input_tree_hash(
            ROOT / "skills" / skill["id"]
        )
        assert skill["base_image_id"] == "sha256:" + f"{index:064x}"


def test_suite_freeze_rejects_cross_track_construction_drift():
    locks = _track_locks()
    locks[MODELS[1]]["records"][0]["construction_image_id"] = "sha256:" + "f" * 64
    with pytest.raises(ArtifactFreezeError, match="construction image"):
        freeze_suite_data(_draft_suite(), track_locks=locks, repo_root=ROOT)


def test_image_lock_freeze_rebinds_only_to_the_frozen_suite():
    draft_suite = _draft_suite()
    locks = _track_locks()
    frozen_suite = freeze_suite_data(
        draft_suite, track_locks=locks, repo_root=ROOT
    )
    for model in MODELS:
        locks[model]["suite_manifest_hash"] = canonical_json_hash(draft_suite)
        frozen = freeze_track_lock_data(
            locks[model],
            draft_suite=draft_suite,
            frozen_suite=frozen_suite,
        )
        assert frozen["status"] == "frozen"
        assert frozen["suite_manifest"] == "experiments/manifests/rq1-skills.frozen.json"
        assert frozen["suite_manifest_hash"] == canonical_json_hash(frozen_suite)
        comparable = copy.deepcopy(frozen)
        comparable["status"] = "draft"
        comparable["suite_manifest"] = "experiments/manifests/rq1-skills.draft.json"
        comparable["suite_manifest_hash"] = canonical_json_hash(draft_suite)
        assert comparable == locks[model]

    generic = {
        "schema": "skillrace-skillgen-track-images/1",
        "status": "draft",
        "pi_version": "0.73.1",
        "construction_base": "skillrace/skillgen-base:0.73.1-construction",
        "construction_base_id": "sha256:" + "1" * 64,
        "records": [],
    }
    assert freeze_skillgen_lock_data(generic)["status"] == "frozen"


def test_hash_inventory_binds_paths_modes_and_bytes_without_results(tmp_path):
    (tmp_path / "source").mkdir()
    (tmp_path / "source/a.txt").write_text("one")
    (tmp_path / "source/empty").mkdir()
    first = hash_inventory(tmp_path, ["source"])
    assert [row["path"] for row in first["files"]] == ["source/a.txt"]
    assert first["directories"] == ["source", "source/empty"]

    (tmp_path / "source/a.txt").write_text("two")
    second = hash_inventory(tmp_path, ["source"])
    assert first["inventory_sha256"] != second["inventory_sha256"]

    (tmp_path / "results").mkdir()
    (tmp_path / "results/observation.json").write_text("secret headline output")
    with pytest.raises(ArtifactFreezeError, match="forbidden result root"):
        hash_inventory(tmp_path, ["results"])
