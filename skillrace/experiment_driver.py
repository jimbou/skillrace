"""Manifest-driven concurrent experiment scheduler with one global resource pool."""

from __future__ import annotations

import argparse
import json
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .io_utils import atomic_write_json, canonical_json_hash, resolve_campaign_path
from .resource_pool import ResourcePool


def _default_campaign_confirmation_runner(
    *,
    campaign: dict[str, Any],
    campaign_path: pathlib.Path,
    campaign_arguments: dict[str, Any],
    out_dir: pathlib.Path,
    resource_pool: ResourcePool,
    allow_bounded_development: bool = False,
) -> dict[str, Any]:
    """Confirm one representative per mechanical failure group for an RQ1 cell."""

    # A bounded development pilot may finish fewer than 30 executions.  It is
    # useful for exercising generation, Pi, and repair, but cannot make a
    # headline confirmation claim.  Persist an explicit zero-work terminal
    # ledger rather than pretending it underwent the 30-run protocol.
    if (
        campaign_arguments.get("development_only") is True
        and campaign.get("complete") is True
        and campaign.get("counted_executions") != 30
        and not allow_bounded_development
    ):
        out_dir.mkdir(parents=True, exist_ok=True)
        ledger = {
            "schema": "skillrace-confirmations/1",
            "source_campaign_hash": canonical_json_hash(campaign),
            "method": campaign.get("method"),
            "protocol_hash": campaign.get("protocol_hash"),
            "base_skill_hash": campaign.get("base_skill_hash"),
            "search_agent_executions": campaign.get("counted_executions"),
            "confirmation_executions": 0,
            "confirmation_executions_counted_in_search_budget": False,
            "clusters": [],
            "costs": {
                "total_provider_credits": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "wall_seconds": 0.0,
            },
            "development_only": True,
            "skip_reason": "bounded-development-campaign-not-eligible-for-headline-confirmation",
        }
        atomic_write_json(out_dir / "confirmation.json", ledger)
        return ledger

    from .loop import check_run, run_agent
    from .rq3_confirmation import confirm_campaign_findings

    try:
        saved = json.loads(campaign_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("terminal campaign artifact cannot be read before confirmation") from error
    if saved != campaign:
        raise ValueError("in-memory campaign differs from terminal campaign artifact")
    skill_dir = pathlib.Path(str(campaign_arguments.get("skill_dir", ""))).resolve()
    model = campaign.get("model")
    wall_clock = campaign_arguments.get("wall_clock", 1800)
    root = campaign_path.parent.resolve()

    def executor(request):
        case = resolve_campaign_path(root, request.case, "confirmation case")
        with resource_pool.slots("api", "docker", "agent"):
            returncode, tail, manifest = run_agent(
                case,
                request.run_dir,
                model,
                wall_clock,
                skill_dir,
            )
            verdicts, checker_tail, checker_returncode = check_run(
                request.run_dir, model
            )
        cost_path = request.run_dir / "cost.json"
        cost = (
            json.loads(cost_path.read_text(encoding="utf-8"))
            if cost_path.is_file()
            else {}
        )
        termination = (manifest or {}).get("termination") or {}
        reason = termination.get("reason") if isinstance(termination, dict) else None
        if reason == "timeout" or returncode == 124:
            status = "timeout"
        else:
            status = (
                "completed"
                if returncode == 0 and checker_returncode == 0
                else "error"
            )
        return {
            "status": status,
            "verdicts": verdicts,
            "agent_id": (manifest or {}).get("run_id"),
            "input_tokens": int(cost.get("in", cost.get("input_tokens", 0)) or 0),
            "output_tokens": int(cost.get("out", cost.get("output_tokens", 0)) or 0),
            "cost_provider_credits": float(
                cost.get("cost_provider_credits", cost.get("provider_credits", cost.get("price_provider_credits", 0.0)))
                or 0.0
            ),
            "wall_seconds": float(
                termination.get("seconds", 0.0)
                if isinstance(termination, dict)
                else 0.0
            ),
            "error": "\n".join(
                [str(tail), *(str(item) for item in checker_tail)]
            )[-500:],
        }

    return confirm_campaign_findings(
        campaign,
        out_dir,
        executor=executor,
        campaign_root=root,
        allow_bounded_development=allow_bounded_development,
    )


def _default_campaign_repair_runner(
    *,
    campaign: dict[str, Any],
    campaign_path: pathlib.Path,
    campaign_arguments: dict[str, Any],
    out_dir: pathlib.Path,
    resource_pool: ResourcePool,
    evidence_max_bytes: int,
) -> dict[str, Any]:
    """Produce frozen patch-only artifacts for one terminal RQ1 cell."""

    from .campaign_protocol import CampaignProtocol
    from .direct_patcher import make_direct_patcher
    from .patch_only import patch_campaign_failures
    from .pi_patcher import make_pi_patcher

    if not campaign_path.is_file():
        raise ValueError("terminal campaign artifact is missing before repair")
    try:
        saved_campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("terminal campaign artifact cannot be read before repair") from error
    if saved_campaign != campaign:
        raise ValueError("in-memory campaign differs from terminal campaign artifact")
    skill_name = campaign_arguments.get("skill")
    skill_dir = campaign_arguments.get("skill_dir")
    model = campaign.get("model")
    if campaign.get("skill") != skill_name:
        raise ValueError("repair skill identity differs from terminal campaign")
    if not isinstance(skill_dir, (str, pathlib.Path)):
        raise ValueError("repair requires the original skill_dir campaign argument")

    protocol_path = campaign_arguments.get("protocol")
    if protocol_path:
        protocol = CampaignProtocol.load(protocol_path)
        if protocol.model != model:
            raise ValueError("repair protocol model differs from terminal campaign")
        policy = protocol.repair
        backend_name = policy.backend_for(campaign["method"])
        backend = (
            make_direct_patcher(
                model=model,
                timeout_seconds=policy.timeout_seconds,
                max_tokens=policy.max_output_tokens,
                temperature=policy.temperature,
                reasoning=policy.reasoning,
            )
            if backend_name == "direct"
            else make_pi_patcher(
                model=model,
                timeout_seconds=policy.timeout_seconds,
            )
        )
    else:
        # Compatibility for old development manifests. Active schedules always
        # provide a protocol, so their per-method backend cannot be overridden.
        backend_name = "direct"
        backend = make_direct_patcher(model=model, timeout_seconds=120)

    def bounded_backend(*args, **kwargs):
        slots = ("api",) if backend_name == "direct" else ("api", "docker", "agent")
        with resource_pool.slots(*slots):
            return backend(*args, **kwargs)

    for attribute in ("backend_name", "model", "timeout_seconds", "config"):
        setattr(bounded_backend, attribute, getattr(backend, attribute))

    return patch_campaign_failures(
        campaign,
        skill_name=skill_name,
        original_skill_dir=skill_dir,
        campaign_root=campaign_path.parent,
        output_root=out_dir,
        backend=bounded_backend,
        evidence_max_bytes=evidence_max_bytes,
    )


def _default_patch_confirmation_runner(
    *,
    campaign: dict[str, Any],
    patch_ledger: dict[str, Any],
    campaign_path: pathlib.Path,
    campaign_arguments: dict[str, Any],
    patch_root: pathlib.Path,
    out_dir: pathlib.Path,
    resource_pool: ResourcePool,
) -> dict[str, Any]:
    """Replay completed patches only after the patch-only ledger is terminal."""

    from .patch_confirmation import confirm_campaign_patches
    from .repair_validation import make_replay_executor

    model = campaign.get("model")
    replay = make_replay_executor(
        model=model,
        wall_clock=int(campaign_arguments.get("wall_clock", 1800)),
    )

    def bounded_executor(*args, **kwargs):
        with resource_pool.slots("api", "docker", "agent"):
            return replay(*args, **kwargs)

    return confirm_campaign_patches(
        campaign,
        patch_ledger,
        skill_name=str(campaign_arguments.get("skill")),
        original_skill_dir=campaign_arguments["skill_dir"],
        campaign_root=campaign_path.parent,
        patch_root=patch_root,
        output_root=out_dir,
        executor=bounded_executor,
    )


def _read_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read experiment manifest {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != "skillrace-experiment-manifest/1":
        raise ValueError("unsupported experiment manifest schema")
    return value


def _safe_output(root: pathlib.Path, relative: Any) -> pathlib.Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("campaign output must be a nonempty relative path")
    value = pathlib.PurePosixPath(relative)
    if value.is_absolute() or ".." in value.parts or value == pathlib.PurePosixPath("."):
        raise ValueError(f"campaign output escapes experiment root: {relative!r}")
    return root.joinpath(*value.parts)


def run_experiment_manifest(
    manifest_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    *,
    campaign_runner: Callable[..., dict[str, Any]] | None = None,
    confirmation_runner: Callable[..., dict[str, Any]] | None = None,
    repair_runner: Callable[..., dict[str, Any]] | None = None,
    patch_confirmation_runner: Callable[..., dict[str, Any]] | None = None,
    epoch_size: int = 4,
) -> dict[str, Any]:
    manifest_path = pathlib.Path(manifest_path)
    root = pathlib.Path(out_dir).resolve()
    manifest = _read_manifest(manifest_path)
    workers = manifest.get("campaign_workers")
    resources = manifest.get("resources")
    cells = manifest.get("cells")
    confirmation = manifest.get("confirmation", {"enabled": False})
    repair = manifest.get("repair", {"enabled": False, "evidence_max_bytes": 3600})
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError("campaign_workers must be a positive integer")
    if not isinstance(resources, dict) or set(resources) != {"api", "docker", "agent"}:
        raise ValueError("manifest resources must define api, docker, and agent")
    if not isinstance(cells, list) or not cells:
        raise ValueError("experiment manifest requires at least one cell")
    confirmation_mode = (
        confirmation.get("mode", "headline")
        if isinstance(confirmation, dict)
        else None
    )
    if (
        not isinstance(confirmation, dict)
        or not set(confirmation).issubset({"enabled", "mode"})
        or "enabled" not in confirmation
        or not isinstance(confirmation.get("enabled"), bool)
        or confirmation_mode not in {"headline", "bounded-development"}
        or (not confirmation.get("enabled") and confirmation_mode != "headline")
    ):
        raise ValueError("manifest confirmation policy is malformed")
    if confirmation_mode == "bounded-development" and (
        manifest.get("status") != "development-only"
        or any(
            not isinstance(cell, dict)
            or not isinstance(cell.get("campaign"), dict)
            or cell["campaign"].get("development_only") is not True
            for cell in cells
        )
    ):
        raise ValueError(
            "bounded-development confirmation requires a development-only manifest "
            "and development-only cells"
        )
    if (
        not isinstance(repair, dict)
        or set(repair) != {"enabled", "evidence_max_bytes"}
        or not isinstance(repair.get("enabled"), bool)
        or isinstance(repair.get("evidence_max_bytes"), bool)
        or not isinstance(repair.get("evidence_max_bytes"), int)
        or repair["evidence_max_bytes"] < 1024
    ):
        raise ValueError("manifest repair policy is malformed")
    if repair["enabled"] and repair_runner is None:
        repair_runner = _default_campaign_repair_runner
    if confirmation["enabled"] and confirmation_runner is None:
        confirmation_runner = _default_campaign_confirmation_runner
    if campaign_runner is None:
        from .loop import run_campaign

        campaign_runner = run_campaign

    identifiers = []
    outputs = []
    normalized = []
    for cell in cells:
        if not isinstance(cell, dict) or not isinstance(cell.get("campaign"), dict):
            raise ValueError("malformed experiment cell")
        identifier = cell.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("experiment cell id must be nonempty")
        output = _safe_output(root, cell.get("output"))
        forbidden = {"out_dir", "resource_pool", "epoch_size"}.intersection(
            cell["campaign"]
        )
        if forbidden:
            raise ValueError(f"campaign manifest may not override driver fields: {forbidden}")
        identifiers.append(identifier)
        outputs.append(str(output.resolve()))
        normalized.append((identifier, output, dict(cell["campaign"])))
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate experiment cell id")
    if len(outputs) != len(set(outputs)):
        raise ValueError("duplicate campaign output directory")

    pool = ResourcePool(**resources)
    root.mkdir(parents=True, exist_ok=True)
    schedule_path = root / "schedule.json"
    schedule = {
        "schema": "skillrace-experiment-schedule/1",
        "manifest_hash": canonical_json_hash(manifest),
        "status": "running",
        "epoch_size": epoch_size,
        "resources": dict(resources),
        "confirmation": dict(confirmation),
        "repair": dict(repair),
        "resource_peaks": pool.snapshot(),
        "cells": [
            {
                "id": identifier,
                "output": str(output),
                "status": "queued",
                "result": None,
                "error": None,
            }
            for identifier, output, _ in normalized
        ],
    }
    lock = threading.Lock()

    def save():
        schedule["resource_peaks"] = pool.snapshot()
        atomic_write_json(schedule_path, schedule)

    save()

    def update(identifier, **changes):
        with lock:
            target = next(cell for cell in schedule["cells"] if cell["id"] == identifier)
            target.update(changes)
            save()

    def run_cell(identifier, output, arguments):
        update(identifier, status="running")
        try:
            result = campaign_runner(
                **arguments,
                out_dir=output,
                resource_pool=pool,
                epoch_size=epoch_size,
            )
        except Exception as error:
            update(identifier, status="failed", error=str(error)[:500])
            return False
        terminal_complete = (
            isinstance(result, dict)
            and result.get("complete") is True
            and result.get("status") == "completed"
        )
        summary = {
            "complete": terminal_complete,
            "status": result.get("status") if isinstance(result, dict) else None,
            "campaign_path": str(output / "campaign.json"),
        }
        if not terminal_complete:
            update(
                identifier,
                status="failed",
                result=summary,
                error="campaign returned without a complete terminal result",
            )
            return False
        repair_summary = None
        confirmation_summary = None
        if repair["enabled"]:
            campaign_path = output / "campaign.json"
            try:
                repair_result = repair_runner(
                    campaign=result,
                    campaign_path=campaign_path,
                    campaign_arguments=dict(arguments),
                    out_dir=output / "repairs",
                    resource_pool=pool,
                    evidence_max_bytes=repair["evidence_max_bytes"],
                )
            except Exception as error:
                update(identifier, status="failed", error=str(error)[:500])
                return False
            is_patch_only = (
                isinstance(repair_result, dict)
                and repair_result.get("schema") == "skillrace-patch-only-ledger/1"
                and isinstance(repair_result.get("patch_executions"), int)
            )
            is_historical = (
                isinstance(repair_result, dict)
                and repair_result.get("schema") == "skillrace-failure-repairs/1"
                and isinstance(repair_result.get("repair_executions"), int)
            )
            if not (is_patch_only or is_historical):
                update(
                    identifier,
                    status="failed",
                    error="repair runner returned a malformed terminal ledger",
                )
                return False
            repair_summary = (
                {
                    "patch_executions": repair_result["patch_executions"],
                    "patch_path": str(output / "repairs" / "patches.json"),
                }
                if is_patch_only
                else {
                    "repair_executions": repair_result["repair_executions"],
                    "repair_path": str(output / "repairs" / "repairs.json"),
                }
            )
            summary.update(repair_summary)
            if is_patch_only:
                runner = patch_confirmation_runner or _default_patch_confirmation_runner
                try:
                    validation = runner(
                        campaign=result,
                        patch_ledger=repair_result,
                        campaign_path=campaign_path,
                        campaign_arguments=dict(arguments),
                        patch_root=output / "repairs",
                        out_dir=output / "repair-confirmations",
                        resource_pool=pool,
                    )
                except Exception as error:
                    update(identifier, status="failed", error=str(error)[:500])
                    return False
                if (
                    not isinstance(validation, dict)
                    or validation.get("schema") != "skillrace-patch-confirmations/1"
                    or not isinstance(validation.get("confirmed_defects"), int)
                ):
                    update(
                        identifier,
                        status="failed",
                        error="patch confirmation runner returned a malformed ledger",
                    )
                    return False
                summary.update(
                    {
                        "patch_confirmation_executions": validation["confirmation_executions"],
                        "confirmed_defects": validation["confirmed_defects"],
                        "patch_confirmation_path": str(
                            output / "repair-confirmations" / "confirmations.json"
                        ),
                    }
                )
        if confirmation["enabled"]:
            campaign_path = output / "campaign.json"
            try:
                confirmation_result = confirmation_runner(
                    campaign=result,
                    campaign_path=campaign_path,
                    campaign_arguments=dict(arguments),
                    out_dir=output / "confirmations",
                    resource_pool=pool,
                    allow_bounded_development=(
                        confirmation_mode == "bounded-development"
                    ),
                )
            except Exception as error:
                update(identifier, status="failed", error=str(error)[:500])
                return False
            if (
                not isinstance(confirmation_result, dict)
                or confirmation_result.get("schema") != "skillrace-confirmations/1"
                or not isinstance(
                    confirmation_result.get("confirmation_executions"), int
                )
            ):
                update(
                    identifier,
                    status="failed",
                    error="confirmation runner returned a malformed terminal ledger",
                )
                return False
            confirmation_summary = {
                "confirmation_executions": confirmation_result[
                    "confirmation_executions"
                ],
                "confirmation_path": str(
                    output / "confirmations" / "confirmation.json"
                ),
            }
            summary.update(confirmation_summary)
        update(identifier, status="completed", result=summary)
        return True

    with ThreadPoolExecutor(max_workers=min(workers, len(normalized))) as executor:
        futures = {
            executor.submit(run_cell, identifier, output, arguments): identifier
            for identifier, output, arguments in normalized
        }
        for future in as_completed(futures):
            future.result()

    schedule["status"] = (
        "completed"
        if all(cell["status"] == "completed" for cell in schedule["cells"])
        else "failed"
    )
    save()
    return json.loads(json.dumps(schedule))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a SkillRACE experiment manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--epoch-size", type=int, default=1)
    args = parser.parse_args(argv)
    schedule = run_experiment_manifest(
        args.manifest, args.out, epoch_size=args.epoch_size
    )
    return 0 if schedule["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
