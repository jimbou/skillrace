import json
import sys

import pytest

import skillrace.check_properties as checker


@pytest.mark.parametrize(
    ("applicability", "expected"),
    [
        (
            {
                "property_ids": ["p1"],
                "fixed_invariants": ["fixed-no-force-push"],
                "categories": ["outcome-integrity"],
                "contingency": "high",
            },
            ["fixed-no-force-push"],
        ),
        (None, None),
    ],
)
def test_checker_uses_compiled_fixed_allowlist_and_rq3_fallback(
    tmp_path, monkeypatch, applicability, expected
):
    run_dir = tmp_path / "run"
    checks_dir = tmp_path / "checks"
    run_dir.mkdir()
    checks_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"container": None}))
    compiled = {"checks": []}
    if applicability is not None:
        compiled["applicability"] = applicability
    (checks_dir / "manifest.json").write_text(json.dumps(compiled))

    seen = []
    writes = []

    def fake_fixed(run, applicable_ids=None):
        seen.append(applicable_ids)
        return []

    monkeypatch.setattr(checker, "run_fixed_checks", fake_fixed)
    monkeypatch.setattr(
        checker,
        "atomic_write_json",
        lambda path, value: writes.append((path, value)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_properties",
            "--run",
            str(run_dir),
            "--checks",
            str(checks_dir),
        ],
    )

    checker.main()

    assert seen == [expected]
    assert writes == [(run_dir / "verdicts.json", [])]


def test_hidden_evaluator_marks_precommitted_checks_hidden_independent(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    checks_dir = tmp_path / "checks"
    run_dir.mkdir()
    checks_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"container": None}))
    (checks_dir / "manifest.json").write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "property_id": "functional",
                        "script": "functional.sh",
                        "syntax_ok": False,
                        "error": "fixture",
                    }
                ]
            }
        )
    )
    (checks_dir / "functional.sh").write_text("exit 0\n")
    writes = []
    monkeypatch.setattr(checker, "run_fixed_checks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        checker, "atomic_write_json", lambda path, value: writes.append((path, value))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_properties",
            "--run",
            str(run_dir),
            "--checks",
            str(checks_dir),
            "--verdict-provenance",
            "hidden-independent",
        ],
    )

    checker.main()

    assert writes[0][1][0]["provenance"] == "hidden-independent"
