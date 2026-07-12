import json
import pathlib

import pytest

from skillrace.fixed_checks import run_fixed_checks
from skillrace.property_specs import (
    FIXED_INVARIANT_IDS,
    load_applicable_properties,
)


def _write_skill(tmp_path, *, properties=None, **matrix_overrides):
    properties = properties or [
        {"id": "p1", "nl": "one", "reads": "state"},
        {"id": "p2", "nl": "two", "reads": "trace"},
    ]
    matrix = {
        "skill": "demo",
        "property_ids": ["p2"],
        "fixed_invariants": ["fixed-no-force-push"],
        "sbe_categories": ["outcome-integrity"],
        "contingency": "medium",
        **matrix_overrides,
    }
    (tmp_path / "properties.json").write_text(json.dumps(properties))
    (tmp_path / "applicability.json").write_text(json.dumps(matrix))


def test_loader_selects_only_recorded_property_ids(tmp_path):
    _write_skill(tmp_path)

    selected = load_applicable_properties(tmp_path)

    assert [item["id"] for item in selected.properties] == ["p2"]
    assert selected.fixed_invariants == ["fixed-no-force-push"]
    assert selected.categories == ["outcome-integrity"]
    assert selected.contingency == "medium"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"property_ids": ["missing"]}, "unknown property id"),
        ({"property_ids": ["p1", "p1"]}, "duplicate property id"),
        ({"property_ids": None}, "property_ids"),
        ({"fixed_invariants": ["fixed-not-real"]}, "unknown fixed invariant"),
        (
            {"fixed_invariants": ["fixed-no-force-push", "fixed-no-force-push"]},
            "duplicate fixed invariant",
        ),
    ],
)
def test_loader_rejects_invalid_matrix_entries(tmp_path, overrides, message):
    _write_skill(tmp_path, **overrides)

    with pytest.raises(ValueError, match=message):
        load_applicable_properties(tmp_path)


def test_loader_rejects_duplicate_property_definitions(tmp_path):
    _write_skill(
        tmp_path,
        properties=[
            {"id": "same", "nl": "one", "reads": "state"},
            {"id": "same", "nl": "two", "reads": "trace"},
        ],
        property_ids=["same"],
    )

    with pytest.raises(ValueError, match="duplicate property id"):
        load_applicable_properties(tmp_path)


def test_all_repository_matrices_select_every_property_in_file_order():
    matrices = sorted(pathlib.Path("skills").glob("*/applicability.json"))
    suite = json.loads(
        pathlib.Path("experiments/manifests/rq1-skills.draft.json").read_text()
    )
    expected = {item["id"] for item in suite["headline_skills"]} | {
        item["id"] for item in suite["development_only"]
    }
    assert {path.parent.name for path in matrices} == expected
    for matrix_path in matrices:
        skill_dir = matrix_path.parent
        all_properties = json.loads((skill_dir / "properties.json").read_text())
        selected = load_applicable_properties(skill_dir)
        assert [item["id"] for item in selected.properties] == [
            item["id"] for item in all_properties
        ], skill_dir.name
        assert set(selected.fixed_invariants) <= FIXED_INVARIANT_IDS


def test_fixed_checks_honor_explicit_allowlist(tmp_path):
    (tmp_path / "run.json").write_text(
        json.dumps({"termination": {"reason": "completed"}})
    )

    verdicts = run_fixed_checks(
        tmp_path,
        applicable_ids=["fixed-no-force-push", "fixed-terminated-within-budget"],
    )

    assert [item["property_id"] for item in verdicts] == [
        "fixed-no-force-push",
        "fixed-terminated-within-budget",
    ]


def test_fixed_checks_empty_allowlist_runs_no_fixed_invariants(tmp_path):
    assert run_fixed_checks(tmp_path, applicable_ids=[]) == []
