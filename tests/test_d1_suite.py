import copy
import json
from pathlib import Path

import pytest

from skillrace.d1_audit import SuiteValidationError, validate_suite
from skillrace.third_party_audit import (
    ThirdPartyValidationError,
    validate_third_party_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "experiments/manifests/rq1-skills.draft.json"
THIRD_PARTY = ROOT / "experiments/manifests/third-party-skills.json"


def _manifest():
    return json.loads(MANIFEST.read_text())


def test_draft_manifest_contains_22_redistributable_public_headline_skills():
    report = validate_suite(MANIFEST, repo_root=ROOT)
    assert report["headline_skills"] == 22
    assert report["development_only"] == 4
    assert report["excluded_public"] == 3
    assert report["missing_images"] == []
    assert report["families"] >= 10


def test_original_development_skill_cannot_enter_headline(tmp_path):
    data = _manifest()
    data["headline_skills"].append(
        {
            "id": "fix-failing-test",
            "family": "debugging",
            "contingency": "high",
            "base_image": "skillrace/fix-failing-test:base",
        }
    )
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(data))
    with pytest.raises(SuiteValidationError, match="multiple suite partitions"):
        validate_suite(path, repo_root=ROOT)


def test_headline_skill_requires_provenance(tmp_path):
    data = _manifest()
    data["headline_skills"][0] = {
        "id": "build-python-cli",
        "family": "cli",
        "contingency": "high",
        "base_image": "skillrace/build-python-cli:base",
    }
    data["development_only"] = [
        item for item in data["development_only"] if item["id"] != "build-python-cli"
    ]
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(data))
    with pytest.raises(SuiteValidationError, match="PROVENANCE"):
        validate_suite(path, repo_root=ROOT)


def test_frozen_suite_requires_content_and_image_identities(tmp_path):
    data = copy.deepcopy(_manifest())
    data["status"] = "frozen"
    data["suite_id"] = "skillrace-d1-public-v1"
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(data))
    with pytest.raises(SuiteValidationError, match="input_tree_hash"):
        validate_suite(path, repo_root=ROOT)


def test_provenance_backed_skill_with_unsafe_license_is_explicitly_excluded():
    data = _manifest()
    assert data["excluded_public"] == [
        {
            "id": "cli-typer-scripts",
            "reason": "upstream repository publishes no license grant, so artifact redistribution is not authorized",
            "license": "NOASSERTION",
        },
        {
            "id": "json-serialization",
            "reason": "upstream repository uses a proprietary commercial license that does not clearly permit artifact redistribution",
            "license": "proprietary",
        },
        {
            "id": "json-tools",
            "reason": "the skill declares proprietary terms but its referenced LICENSE.txt is absent, leaving redistribution rights unclear",
            "license": "proprietary",
        },
    ]
    headline = {item["id"] for item in data["headline_skills"]}
    assert {"cli-typer-scripts", "json-serialization", "json-tools"}.isdisjoint(
        headline
    )
    for skill_id in {"cli-typer-scripts", "json-serialization", "json-tools"}:
        assert not (ROOT / "skills" / skill_id).exists(), (
            f"unsafe excluded content must not ship in the artifact: {skill_id}"
        )


def test_third_party_manifest_pins_every_public_source_and_local_skill_hash():
    report = validate_third_party_manifest(
        THIRD_PARTY, suite_manifest=MANIFEST, repo_root=ROOT
    )
    assert report == {
        "schema": "third-party-skills-validation/1",
        "records": 25,
        "headline": 22,
        "excluded": 3,
        "exact": 24,
        "abridged": 1,
        "embedded_licenses": 18,
    }


def test_third_party_source_url_must_be_commit_pinned(tmp_path):
    data = json.loads(THIRD_PARTY.read_text())
    data["records"][0]["source_url"] = "https://github.com/example/repo"
    path = tmp_path / "third-party.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ThirdPartyValidationError, match="commit-pinned source_url"):
        validate_third_party_manifest(path, suite_manifest=MANIFEST, repo_root=ROOT)


def test_unsafe_license_cannot_enter_headline_partition(tmp_path):
    data = json.loads(THIRD_PARTY.read_text())
    record = next(item for item in data["records"] if item["disposition"] == "headline")
    record["license"] = "NOASSERTION"
    path = tmp_path / "third-party.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ThirdPartyValidationError, match="unsafe license"):
        validate_third_party_manifest(path, suite_manifest=MANIFEST, repo_root=ROOT)
