from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from skillrace.experiment_driver import run_experiment_manifest


def test_manifest_driver_shares_one_global_pool_and_persists_progress(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 4,
                "resources": {"api": 2, "docker": 2, "agent": 2},
                "cells": [
                    {
                        "id": f"cell-{index}",
                        "output": f"cells/cell-{index}",
                        "campaign": {"method": "random", "skill": f"s{index}"},
                    }
                    for index in range(4)
                ],
            }
        )
    )
    pool_ids = set()
    lock = threading.Lock()
    active = 0
    peak = 0

    def runner(*, resource_pool, out_dir, method, skill, epoch_size):
        nonlocal active, peak
        pool_ids.add(id(resource_pool))
        with resource_pool.agent_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
        return {
            "complete": True,
            "status": "completed",
            "method": method,
            "skill": skill,
            "out": str(out_dir),
        }

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=runner,
        epoch_size=3,
    )

    assert len(pool_ids) == 1
    assert peak == 2
    assert schedule["status"] == "completed"
    assert {cell["status"] for cell in schedule["cells"]} == {"completed"}
    saved = json.loads((tmp_path / "experiment" / "schedule.json").read_text())
    assert saved == schedule
    assert saved["resource_peaks"]["agent"]["peak"] == 2


def test_manifest_driver_passes_absolute_cell_roots_to_campaigns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "manifest-absolute.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "cells": [
                    {"id": "cell-a", "output": "cells/a", "campaign": {}}
                ],
            }
        )
    )
    observed = []

    def runner(*, out_dir, **_kwargs):
        observed.append(out_dir)
        return {"complete": True, "status": "completed"}

    run_experiment_manifest(
        manifest,
        pathlib.Path("relative-output"),
        campaign_runner=runner,
    )

    assert observed == [(tmp_path / "relative-output" / "cells" / "a").resolve()]


def test_manifest_driver_rejects_duplicate_or_escaping_outputs(tmp_path):
    base = {
        "schema": "skillrace-experiment-manifest/1",
        "campaign_workers": 2,
        "resources": {"api": 1, "docker": 1, "agent": 1},
    }
    for cells in (
        [
            {"id": "a", "output": "same", "campaign": {}},
            {"id": "b", "output": "same", "campaign": {}},
        ],
        [{"id": "a", "output": "../escape", "campaign": {}}],
    ):
        manifest = tmp_path / f"manifest-{len(cells)}-{cells[0]['output'].replace('/', '_')}.json"
        manifest.write_text(json.dumps({**base, "cells": cells}))
        with pytest.raises(ValueError, match="output"):
            run_experiment_manifest(
                manifest,
                tmp_path / "experiment",
                campaign_runner=lambda **kwargs: {},
            )


@pytest.mark.parametrize(
    "result",
    [
        {"complete": False, "status": "failed"},
        {"complete": True, "status": "running"},
        None,
    ],
)
def test_manifest_driver_never_marks_an_incomplete_campaign_successful(tmp_path, result):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {"method": "random", "skill": "demo"},
                    }
                ],
            }
        )
    )

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=lambda **_kwargs: result,
    )

    assert schedule["status"] == "failed"
    assert schedule["cells"][0]["status"] == "failed"
    assert schedule["cells"][0]["result"]["complete"] is False


def test_manifest_driver_runs_repairs_only_after_terminal_campaign(tmp_path):
    manifest = tmp_path / "manifest-repair.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "repair": {"enabled": True, "evidence_max_bytes": 3600},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {
                            "method": "skillrace",
                            "skill": "demo",
                            "skill_dir": "skills/demo",
                        },
                    }
                ],
            }
        )
    )
    events = []

    def campaign_runner(*, out_dir, **_kwargs):
        events.append("campaign")
        out_dir.mkdir(parents=True, exist_ok=True)
        campaign = {
            "schema": "campaign/2",
            "complete": True,
            "status": "completed",
            "method": "skillrace",
            "attempts": [],
        }
        (out_dir / "campaign.json").write_text(json.dumps(campaign))
        return campaign

    def repair_runner(**kwargs):
        events.append("repair")
        assert kwargs["campaign"]["complete"] is True
        assert kwargs["campaign_path"].is_file()
        assert kwargs["out_dir"].name == "repairs"
        assert kwargs["evidence_max_bytes"] == 3600
        return {
            "schema": "skillrace-failure-repairs/1",
            "failed_public_executions": 0,
            "repair_executions": 0,
            "repairs": [],
        }

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=campaign_runner,
        repair_runner=repair_runner,
    )

    assert events == ["campaign", "repair"]
    assert schedule["status"] == "completed"
    assert schedule["cells"][0]["result"]["repair_executions"] == 0
    assert schedule["cells"][0]["result"]["repair_path"].endswith(
        "/cells/a/repairs/repairs.json"
    )


def test_when_both_post_search_steps_are_enabled_repair_precedes_confirmation(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "repair": {"enabled": True, "evidence_max_bytes": 3600},
                "confirmation": {"enabled": True},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {"method": "random", "skill": "demo"},
                    }
                ],
            }
        )
    )
    events = []

    def campaign_runner(*, out_dir, **_kwargs):
        events.append("campaign")
        out_dir.mkdir(parents=True)
        value = {"complete": True, "status": "completed", "attempts": []}
        (out_dir / "campaign.json").write_text(json.dumps(value))
        return value

    def repair_runner(**_kwargs):
        events.append("repair")
        return {"schema": "skillrace-failure-repairs/1", "repair_executions": 0}

    def confirmation_runner(**_kwargs):
        events.append("confirmation")
        return {"schema": "skillrace-confirmations/1", "confirmation_executions": 0}

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=campaign_runner,
        repair_runner=repair_runner,
        confirmation_runner=confirmation_runner,
    )

    assert schedule["status"] == "completed"
    assert events == ["campaign", "repair", "confirmation"]


def test_manifest_driver_fails_cell_when_required_repair_fails(tmp_path):
    manifest = tmp_path / "manifest-repair-failure.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "repair": {"enabled": True, "evidence_max_bytes": 3600},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {"method": "random", "skill": "demo"},
                    }
                ],
            }
        )
    )

    def campaign_runner(*, out_dir, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        campaign = {"complete": True, "status": "completed", "attempts": []}
        (out_dir / "campaign.json").write_text(json.dumps(campaign))
        return campaign

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=campaign_runner,
        repair_runner=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("repair stopped")
        ),
    )

    assert schedule["status"] == "failed"
    assert schedule["cells"][0]["status"] == "failed"
    assert "repair stopped" in schedule["cells"][0]["error"]


def test_manifest_driver_has_a_production_default_repair_runner(tmp_path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    manifest = tmp_path / "manifest-default-repair.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "repair": {"enabled": True, "evidence_max_bytes": 3600},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {
                            "method": "random",
                            "skill": "demo",
                            "skill_dir": str(skill),
                            "wall_clock": 120,
                        },
                    }
                ],
            }
        )
    )

    def campaign_runner(*, out_dir, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        campaign = {
            "schema": "campaign/2",
            "complete": True,
            "status": "completed",
            "method": "random",
            "skill": "demo",
            "model": "same-model",
            "counted_executions": 30,
            "attempts": [],
        }
        (out_dir / "campaign.json").write_text(json.dumps(campaign))
        return campaign

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=campaign_runner,
    )

    assert schedule["status"] == "completed"
    ledger = json.loads(
        (tmp_path / "experiment" / "cells" / "a" / "repairs" / "patches.json").read_text()
    )
    assert ledger["schema"] == "skillrace-patch-only-ledger/1"
    assert ledger["patch_executions"] == 0
    assert ledger["failed_public_executions"] == 0


def test_manifest_driver_has_a_production_default_confirmation_runner(tmp_path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    manifest = tmp_path / "manifest-default-confirmation.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "confirmation": {"enabled": True},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {
                            "method": "random",
                            "skill": "demo",
                            "skill_dir": str(skill),
                            "wall_clock": 120,
                        },
                    }
                ],
            }
        )
    )

    def campaign_runner(*, out_dir, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        campaign = {
            "schema": "campaign/2",
            "complete": True,
            "status": "completed",
            "method": "random",
            "skill": "demo",
            "model": "same-model",
            "counted_executions": 30,
            "attempts": [],
        }
        (out_dir / "campaign.json").write_text(json.dumps(campaign))
        return campaign

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=campaign_runner,
    )

    assert schedule["status"] == "completed"
    ledger = json.loads(
        (
            tmp_path
            / "experiment"
            / "cells"
            / "a"
            / "confirmations"
            / "confirmation.json"
        ).read_text()
    )
    assert ledger["confirmation_executions"] == 0
    assert ledger["search_agent_executions"] == 30


def test_development_campaign_records_explicit_zero_confirmation_skip(tmp_path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    manifest = tmp_path / "development-confirmation.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "confirmation": {"enabled": True},
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {
                            "method": "random",
                            "skill": "demo",
                            "skill_dir": str(skill),
                            "development_only": True,
                            "wall_clock": 120,
                        },
                    }
                ],
            }
        )
    )

    def campaign_runner(*, out_dir, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        campaign = {
            "schema": "campaign/2",
            "complete": True,
            "status": "completed",
            "method": "random",
            "skill": "demo",
            "model": "same-model",
            "counted_executions": 2,
            "attempts": [],
        }
        (out_dir / "campaign.json").write_text(json.dumps(campaign))
        return campaign

    schedule = run_experiment_manifest(
        manifest, tmp_path / "experiment", campaign_runner=campaign_runner
    )

    assert schedule["status"] == "completed"
    ledger = json.loads(
        (
            tmp_path
            / "experiment"
            / "cells"
            / "a"
            / "confirmations"
            / "confirmation.json"
        ).read_text()
    )
    assert ledger["development_only"] is True
    assert ledger["search_agent_executions"] == 2
    assert ledger["confirmation_executions"] == 0


def test_bounded_development_manifest_runs_real_confirmation_mode(tmp_path):
    manifest = tmp_path / "development-gate.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "status": "development-only",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "confirmation": {
                    "enabled": True,
                    "mode": "bounded-development",
                },
                "cells": [
                    {
                        "id": "cell-a",
                        "output": "cells/a",
                        "campaign": {
                            "method": "random",
                            "skill": "demo",
                            "development_only": True,
                        },
                    }
                ],
            }
        )
    )

    def campaign_runner(*, out_dir, **_kwargs):
        out_dir.mkdir(parents=True)
        value = {"complete": True, "status": "completed", "attempts": []}
        (out_dir / "campaign.json").write_text(json.dumps(value))
        return value

    seen = []

    def confirmation_runner(**kwargs):
        seen.append(kwargs["allow_bounded_development"])
        return {"schema": "skillrace-confirmations/1", "confirmation_executions": 1}

    schedule = run_experiment_manifest(
        manifest,
        tmp_path / "experiment",
        campaign_runner=campaign_runner,
        confirmation_runner=confirmation_runner,
    )

    assert schedule["status"] == "completed"
    assert seen == [True]


def test_headline_manifest_cannot_enable_bounded_development_confirmation(tmp_path):
    manifest = tmp_path / "invalid-headline.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-experiment-manifest/1",
                "status": "draft",
                "campaign_workers": 1,
                "resources": {"api": 1, "docker": 1, "agent": 1},
                "confirmation": {
                    "enabled": True,
                    "mode": "bounded-development",
                },
                "cells": [
                    {"id": "cell-a", "output": "a", "campaign": {}}
                ],
            }
        )
    )

    with pytest.raises(ValueError, match="development-only"):
        run_experiment_manifest(
            manifest,
            tmp_path / "experiment",
            campaign_runner=lambda **_kwargs: {},
        )
