from __future__ import annotations

from skillrace.greybox import GreyboxGenerator

from tests.helpers import assistant_tool, write_session


def make_generator():
    return GreyboxGenerator(
        "fix-failing-test",
        "skills/fix-failing-test",
        "skillrace/fix-failing-test:base",
    )


def test_all_bootstrap_seeds_are_retained_even_with_duplicate_sequences(tmp_path):
    generator = make_generator()
    first = write_session(
        [assistant_tool("bash", {"command": "pytest -q"})], tmp_path / "one"
    )
    second = write_session(
        [assistant_tool("bash", {"command": "pytest -q"})], tmp_path / "two"
    )

    generator.fold_initial({"candidate_id": "a", "provenance": {}}, first)
    generator.fold_initial({"candidate_id": "b", "provenance": {}}, second)

    assert [seed["cand"]["candidate_id"] for seed in generator.corpus] == ["a", "b"]
    assert generator.d_seq == {("bash:pytest",)}
    assert generator.stats["initial_retained"] == 2
    assert [seed["energy"] for seed in generator.corpus] == [2, 0]
    assert [seed["base_energy"] for seed in generator.corpus] == [2, 0]
    assert [seed["cand"]["candidate_id"] for seed in generator.queue] == ["a"]


def test_empty_bootstrap_sequences_are_still_all_retained(tmp_path):
    generator = make_generator()
    for index in range(3):
        run = write_session([], tmp_path / f"empty-{index}")
        generator.fold_initial(
            {"candidate_id": f"s{index}", "provenance": {}}, run
        )

    assert [item["cand"]["candidate_id"] for item in generator.corpus] == [
        "s0",
        "s1",
        "s2",
    ]
    assert generator.d_seq == {()}
    assert [seed["energy"] for seed in generator.corpus] == [1, 0, 0]
    assert [seed["cand"]["candidate_id"] for seed in generator.queue] == ["s0"]


def test_duplicate_mutant_is_observed_but_not_retained(tmp_path):
    generator = make_generator()
    initial = write_session(
        [assistant_tool("bash", {"command": "pytest -q"})], tmp_path / "initial"
    )
    duplicate = write_session(
        [assistant_tool("bash", {"command": "pytest -q"})], tmp_path / "duplicate"
    )
    generator.fold_initial({"candidate_id": "a", "provenance": {}}, initial)

    retained = generator.fold_mutant(
        {"candidate_id": "m", "provenance": {}}, duplicate
    )

    assert retained is None
    assert [seed["cand"]["candidate_id"] for seed in generator.corpus] == ["a"]
    assert generator.stats["folded"] == 2
    assert generator.stats["novel_mutants"] == 0


def test_every_bootstrap_execution_populates_all_three_coverage_databases(tmp_path):
    generator = make_generator()
    run = write_session(
        [
            assistant_tool("bash", {"command": "ls"}),
            assistant_tool("read", {"path": "src/a.py"}),
        ],
        tmp_path / "run",
    )

    seed = generator.fold_initial({"candidate_id": "a", "provenance": {}}, run)

    assert generator.d_tool == {"bash:ls", "read:.py"}
    assert generator.d_trans == {("bash:ls", "read:.py")}
    assert generator.d_seq == {("bash:ls", "read:.py")}
    assert seed["energy"] == 3


def test_energy_adds_one_per_new_tool_transition_and_sequence(tmp_path):
    generator = make_generator()
    first = write_session(
        [
            assistant_tool("bash", {"command": "ls"}),
            assistant_tool("read", {"path": "a.py"}),
        ],
        tmp_path / "first",
    )
    second = write_session(
        [
            assistant_tool("bash", {"command": "ls"}),
            assistant_tool("edit", {"path": "a.py"}),
        ],
        tmp_path / "second",
    )
    generator.fold_initial({"candidate_id": "a", "provenance": {}}, first)

    mutant = generator.fold_mutant({"candidate_id": "b", "provenance": {}}, second)

    assert mutant["energy"] == 3


def test_recycle_restores_each_positive_seeds_earned_energy_not_arbitrary_one(tmp_path):
    generator = make_generator()
    run = write_session(
        [
            assistant_tool("bash", {"command": "ls"}),
            assistant_tool("read", {"path": "a.py"}),
        ],
        tmp_path / "run",
    )
    seed = generator.fold_initial({"candidate_id": "a", "provenance": {}}, run)
    assert seed["base_energy"] == 3
    for _ in range(3):
        chosen = generator._choose_seed()
        assert chosen is seed
        chosen["energy"] -= 1

    recycled = generator._choose_seed()
    assert recycled is seed
    assert recycled["energy"] == 3


def test_zero_energy_bootstrap_corpus_is_retained_but_not_schedulable(tmp_path):
    generator = make_generator()
    first = write_session([], tmp_path / "first")
    duplicate = write_session([], tmp_path / "duplicate")
    generator.fold_initial({"candidate_id": "first", "provenance": {}}, first)
    zero = generator.fold_initial(
        {"candidate_id": "zero", "provenance": {}}, duplicate
    )
    generator.corpus[0]["energy"] = 0
    generator.queue.clear()
    generator._pending = None
    assert zero["base_energy"] == 0
    assert generator._choose_seed()["cand"]["candidate_id"] == "first"
    generator.corpus[0]["base_energy"] = 0
    generator.queue.clear()
    generator._pending = None
    assert generator._choose_seed() is None
