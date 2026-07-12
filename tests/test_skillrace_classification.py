from skillrace.loop import (
    classify_property_discoveries,
    classify_target_execution,
    summarize_skillrace_discoveries,
)


def test_intended_branch_reached_and_new_child_created():
    actions = [("merge", "n0", ""), ("merge", "n1", ""), ("new", "n9", "")]
    assert classify_target_execution(actions, "n1") == "intended_branch"


def test_different_new_branch_is_kept_as_opportunistic_discovery():
    actions = [("merge", "n0", ""), ("new", "n8", "")]
    assert classify_target_execution(actions, "n4") == "different_new_branch"


def test_reached_target_without_new_behavior_is_diagnostic_not_a_rejection():
    actions = [("merge", "n0", ""), ("merge", "n1", ""), ("merge", "n2", "")]
    assert classify_target_execution(actions, "n1") == "no_divergence"
    assert classify_target_execution(actions[:2], "n1") == "no_divergence"


def test_path_miss_and_unfolded_are_distinct():
    assert classify_target_execution([("merge", "n0", "")], "n4") == "path_miss"
    assert classify_target_execution(None, "n4") == "unfolded"


def test_virtual_root_target_uses_first_action():
    assert classify_target_execution([("new", "n1", "")], None) == "intended_branch"
    assert classify_target_execution([("merge", "n1", "")], None) == "no_divergence"


def test_property_relationship_does_not_mislabel_unconfirmed_observations():
    discoveries = classify_property_discoveries(
        ["p-other", "p-target", "p-third"], targeted_property="p-target"
    )
    assert discoveries == [
        {"property_id": "p-other", "relationship": "serendipitous"},
        {"property_id": "p-target", "relationship": "targeted"},
        {"property_id": "p-third", "relationship": "serendipitous"},
    ]

    summary = summarize_skillrace_discoveries(
        [("merge", "n0", ""), ("new", "n8", "")],
        target_parent="n4",
        violated_property_ids=["p-other", "p-target", "p-third"],
        targeted_property="p-target",
    )
    assert summary["branch_outcome"] == "different_new_branch"
    assert "confirmed_yield" not in summary
    assert summary["observed_violation_count"] == 3
    assert summary["observed_property_ids"] == ["p-other", "p-target", "p-third"]
    assert summary["confirmation_status"] == "unconfirmed"
