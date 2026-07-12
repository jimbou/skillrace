from __future__ import annotations

import json
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
