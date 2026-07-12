from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from skillrace.loop import RealCampaignExecutor, build_parser, run_campaign


def test_search_runner_has_no_inline_regrade_control_or_confirmation_execution():
    assert "regrade_k" not in inspect.signature(RealCampaignExecutor).parameters
    assert "regrade_k" not in inspect.signature(run_campaign).parameters
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--method", "random", "--skill", "demo", "--skill-dir", "demo",
                "--base", "demo:base", "--props", "properties.json",
                "--out", "out", "--regrade-k", "1",
            ]
        )
    script = (Path(__file__).parents[1] / "scripts" / "run_suite.sh").read_text()
    assert "regrade-k" not in script
    assert "REGRADE_K" not in script
