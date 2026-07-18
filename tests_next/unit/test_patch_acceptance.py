import pytest

from skillrace_next.pipeline.stages import accept_patch


def result(check_id: str, status: str, diagnostic: str = "scientific result"):
    return {
        "check_id": check_id,
        "property_id": check_id.split("-")[0],
        "status": status,
        "diagnostic": diagnostic,
    }


def test_accepts_repaired_failure_when_passes_stay_passed() -> None:
    before = [result("P1-C1", "fail"), result("P2-C1", "pass")]
    replay = [result("P1-C1", "pass"), result("P2-C1", "pass")]
    regressions = [[result("P3-C1", "pass")]]

    assert accept_patch(before, replay, regressions) == "accepted"


def test_accepts_when_one_failure_is_repaired_and_another_remains_failing() -> None:
    before = [
        result("P1-C1", "fail"),
        result("P2-C1", "fail"),
        result("P3-C1", "pass"),
    ]
    replay = [
        result("P1-C1", "pass"),
        result("P2-C1", "fail"),
        result("P3-C1", "pass"),
    ]

    assert accept_patch(before, replay, []) == "accepted"


@pytest.mark.parametrize(
    ("before", "replay", "regressions"),
    [
        ([result("P1-C1", "fail")], [result("P1-C1", "fail")], []),
        ([result("P1-C1", "fail")], [result("P1-C1", "inconclusive")], []),
        ([result("P1-C1", "pass")], [result("P1-C1", "fail")], []),
        ([result("P1-C1", "pass")], [result("P1-C1", "inconclusive")], []),
        (
            [result("P1-C1", "fail")],
            [result("P1-C1", "pass")],
            [[result("P2-C1", "fail")]],
        ),
    ],
    ids=(
        "fail-stays-fail",
        "fail-becomes-inconclusive",
        "pass-becomes-fail",
        "pass-becomes-inconclusive",
        "regression-fails",
    ),
)
def test_rejects_nonrepairs_and_regressions(before, replay, regressions) -> None:
    assert accept_patch(before, replay, regressions) == "rejected"


def test_infrastructure_failure_is_unresolved() -> None:
    before = [result("P1-C1", "fail")]
    replay = [
        result(
            "P1-C1",
            "inconclusive",
            "checker infrastructure execution failed: container disappeared",
        )
    ]

    assert accept_patch(before, replay, []) == "unresolved"
