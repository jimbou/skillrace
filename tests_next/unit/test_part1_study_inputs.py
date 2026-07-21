import json
from pathlib import Path

from skillrace_next.pipeline.stages import validate_nl_checks
from skillrace_next.storage import file_hash, tree_hash
from skillrace_next.study_inputs import (
    EXCLUDED_PART1_SKILLS,
    SELECTED_PART1_SKILLS,
    prepare_part1_study,
    verify_part1_study,
)


def _write_source_skills(root: Path) -> dict[str, str]:
    before: dict[str, str] = {}
    for name in SELECTED_PART1_SKILLS:
        skill = root / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test skill.\n---\n# {name}\n",
            encoding="utf-8",
        )
        if name not in {"file-check", "js-feature"}:
            (skill / "properties.json").write_text(
                json.dumps(
                    [
                        {"id": "first-check", "reads": "state", "nl": "First behavior."},
                        {"id": "second-check", "reads": "state", "nl": "Second behavior."},
                    ]
                ),
                encoding="utf-8",
            )
        before[name] = tree_hash(skill)
    return before


def test_part1_selection_is_the_approved_fixed_30_of_37() -> None:
    assert len(SELECTED_PART1_SKILLS) == 30
    assert len(set(SELECTED_PART1_SKILLS)) == 30
    assert len(EXCLUDED_PART1_SKILLS) == 7
    assert set(SELECTED_PART1_SKILLS).isdisjoint(EXCLUDED_PART1_SKILLS)
    assert set(EXCLUDED_PART1_SKILLS) == {
        "hello",
        "cli-argparse-fix",
        "finishing-a-development-branch",
        "using-git-worktrees",
        "mcp-server-patterns",
        "rest-api-caller",
        "sql-query-json",
    }


def test_prepare_part1_study_preserves_sources_and_binds_receipts(tmp_path: Path) -> None:
    before = _write_source_skills(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "part1"

    manifest_path = prepare_part1_study(tmp_path, output)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "skillrace-part1-selection/1"
    assert manifest["selection_rule"].startswith("Include skills evaluable as")
    assert [item["skill_id"] for item in manifest["selected"]] == list(
        SELECTED_PART1_SKILLS
    )
    assert [item["rank"] for item in manifest["selected"]] == list(range(1, 31))

    for item in manifest["selected"]:
        name = item["skill_id"]
        source = tmp_path / item["source_directory"]
        prepared = output / name
        receipt_path = output / item["receipt_path"]
        properties_path = output / item["properties_path"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        properties = validate_nl_checks(properties_path)

        assert tree_hash(source) == before[name]
        assert receipt["schema"] == "skillrace-part1-s0-receipt/1"
        assert receipt["skill_id"] == name
        assert receipt["skill_tree_hash"] == before[name]
        assert receipt["skill_md_hash"] == file_hash(source / "SKILL.md")
        assert receipt["properties_hash"] == file_hash(properties_path)
        assert receipt_path == prepared / "s0-receipt.json"
        if name not in {"file-check", "js-feature"}:
            assert receipt["property_source"]["path"] == (
                f"skills/{name}/properties.json"
            )
            assert receipt["property_source"]["mappings"] == [
                {
                    "source_id": "first-check",
                    "reads": "state",
                    "property_id": "P1",
                },
                {
                    "source_id": "second-check",
                    "reads": "state",
                    "property_id": "P2",
                },
            ]
        else:
            assert len(receipt["property_source"]["mappings"]) == len(properties)
            assert all(
                mapping["property_id"] == f"P{index}"
                and mapping["source_id"]
                and mapping["reads"] in {"state", "trace", "state+trace"}
                for index, mapping in enumerate(
                    receipt["property_source"]["mappings"], start=1
                )
            )
        assert [value["property_id"] for value in properties] == [
            f"P{index}" for index in range(1, len(properties) + 1)
        ]

    assert verify_part1_study(tmp_path, manifest_path) == 30


def test_verify_part1_study_rejects_changed_s0(tmp_path: Path) -> None:
    _write_source_skills(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "part1"
    manifest_path = prepare_part1_study(tmp_path, output)
    changed = tmp_path / "skills" / SELECTED_PART1_SKILLS[0] / "SKILL.md"
    changed.write_text(
        changed.read_text(encoding="utf-8") + "changed\n", encoding="utf-8"
    )

    try:
        verify_part1_study(tmp_path, manifest_path)
    except ValueError as exc:
        assert "S0 tree hash mismatch" in str(exc)
    else:
        raise AssertionError("changed S0 was accepted")


def test_verify_part1_study_rejects_reordered_selection(tmp_path: Path) -> None:
    _write_source_skills(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "part1"
    manifest_path = prepare_part1_study(tmp_path, output)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["selected"][0], manifest["selected"][1] = (
        manifest["selected"][1],
        manifest["selected"][0],
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        verify_part1_study(tmp_path, manifest_path)
    except ValueError as exc:
        assert "approved ordered selection" in str(exc)
    else:
        raise AssertionError("reordered selection was accepted")
