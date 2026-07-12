"""Validation for the public campaign slice of an RQ3 scenario package."""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from .scenario_contract import load_scenario


class PublicCampaignConfigError(ValueError):
    pass


def _object(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise PublicCampaignConfigError(f"{label} symlink is forbidden")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicCampaignConfigError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise PublicCampaignConfigError(f"{label} must be a JSON object")
    return value


def load_public_campaign_config(scenario_dir: str | pathlib.Path) -> dict[str, Any]:
    scenario = load_scenario(scenario_dir)
    root = scenario.root
    campaign = root / "campaign"
    if campaign.is_symlink() or not campaign.is_dir():
        raise PublicCampaignConfigError("scenario lacks a regular public campaign directory")
    expected_files = {"config.json", "properties.json", "applicability.json", "Containerfile.base"}
    actual_files = {
        path.name for path in campaign.iterdir() if path.is_file() and not path.is_symlink()
    }
    if actual_files != expected_files or any(path.is_symlink() for path in campaign.rglob("*")):
        raise PublicCampaignConfigError(
            f"campaign must contain exactly {sorted(expected_files)}"
        )
    config = _object(campaign / "config.json", "campaign config")
    if set(config) != {
        "schema",
        "scenario_id",
        "skill_name",
        "base_image",
        "properties",
        "applicability",
        "containerfile",
    }:
        raise PublicCampaignConfigError("campaign config fields are not frozen")
    scenario_id = scenario.scenario_id
    if (
        config.get("schema") != "skillrace-rq3-campaign-config/1"
        or config.get("scenario_id") != scenario_id
        or config.get("skill_name") != scenario_id
        or config.get("base_image") != f"skillrace/rq3-{scenario_id}:base"
        or config.get("properties") != "properties.json"
        or config.get("applicability") != "applicability.json"
        or config.get("containerfile") != "Containerfile.base"
    ):
        raise PublicCampaignConfigError("campaign config identity mismatch")
    try:
        properties = json.loads((campaign / "properties.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicCampaignConfigError("campaign properties are malformed") from error
    if (
        not isinstance(properties, list)
        or len(properties) < 2
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"id", "reads", "nl"}
            or not isinstance(row["id"], str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{2,80}", row["id"])
            or row["reads"] not in {"state", "trace", "state+trace"}
            or not isinstance(row["nl"], str)
            or not row["nl"].strip()
            for row in properties
        )
    ):
        raise PublicCampaignConfigError("campaign properties must be a nonempty reviewed list")
    property_ids = [row["id"] for row in properties]
    if len(property_ids) != len(set(property_ids)):
        raise PublicCampaignConfigError("campaign property IDs must be unique")
    applicability = _object(campaign / "applicability.json", "campaign applicability")
    if (
        applicability.get("skill") != scenario_id
        or applicability.get("property_ids") != property_ids
        or not isinstance(applicability.get("fixed_invariants"), list)
        or any(not isinstance(value, str) for value in applicability["fixed_invariants"])
        or applicability.get("contingency") not in {"low", "medium", "high"}
    ):
        raise PublicCampaignConfigError("campaign applicability mismatch")
    containerfile = (campaign / "Containerfile.base").read_text(encoding="utf-8")
    if (
        not containerfile.startswith("FROM skillrace/skillgen-base:latest\n")
        or f"/skills/{scenario_id}/SKILL.md" not in containerfile
        or re.search(r"(?:^|[/ ])tests(?:/|$)", containerfile, flags=re.MULTILINE)
    ):
        raise PublicCampaignConfigError("campaign Containerfile violates the public boundary")
    return {
        **config,
        "properties": properties,
        "applicability": applicability,
        "containerfile_text": containerfile,
    }

