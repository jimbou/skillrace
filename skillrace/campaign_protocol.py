"""Validated, reviewable contract for a SkillRACE testing campaign.

The protocol is intentionally small: it exposes only choices that are part of the
paper's frozen comparison.  In particular, there is one model field and one global
VeriGrey granularity; callers cannot quietly tune a role or a skill independently.
"""

from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any

from .io_utils import canonical_json_hash


HEADLINE_METHODS = ("random", "greybox", "skillrace")
ADAPTIVE_METHODS = ("greybox", "skillrace")

_ROLE_MODEL_FIELDS = {
    "agent_model",
    "generation_model",
    "realization_model",
    "repair_model",
    "segmentation_model",
    "tree_model",
    "merge_model",
    "guard_model",
    "selection_model",
    "synthesis_model",
    "check_model",
    "revision_model",
}
_FIELDS = {
    "schema",
    "protocol_id",
    "status",
    "model",
    "budget",
    "bootstrap_count",
    "max_generation_attempts_per_execution",
    "seed_generator",
    "greybox_level",
    "random_seed",
} | _ROLE_MODEL_FIELDS
_SEED_FIELDS = {"batch_size", "temperature", "build_retries"}


def _plain_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


@dataclass(frozen=True)
class CampaignProtocol:
    raw: dict[str, Any]
    protocol_id: str
    status: str
    model: str
    budget: int
    bootstrap_count: int
    max_generation_attempts_per_execution: int
    seed_generator: dict[str, Any]
    greybox_level: str
    random_seed: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CampaignProtocol":
        if not isinstance(data, dict):
            raise ValueError("campaign protocol must be a JSON object")
        unknown = sorted(set(data) - _FIELDS)
        if unknown:
            raise ValueError(f"unknown protocol field: {unknown[0]}")
        if data.get("schema") != "campaign-protocol/1":
            raise ValueError("unsupported campaign protocol schema")

        protocol_id = data.get("protocol_id")
        status = data.get("status")
        model = data.get("model")
        if not isinstance(protocol_id, str) or not protocol_id.strip():
            raise ValueError("protocol_id must be a nonempty string")
        if status not in {"draft", "frozen", "runtime"}:
            raise ValueError("status must be draft, frozen, or runtime")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a nonempty string")
        if any(field in data for field in _ROLE_MODEL_FIELDS):
            raise ValueError(
                "role-specific model overrides are forbidden; every role must use the same model"
            )

        budget = _plain_int(data.get("budget"), "budget")
        bootstrap = _plain_int(data.get("bootstrap_count"), "bootstrap_count")
        attempts = _plain_int(
            data.get("max_generation_attempts_per_execution"),
            "max_generation_attempts_per_execution",
        )
        random_seed = _plain_int(data.get("random_seed"), "random_seed")
        if budget <= 0:
            raise ValueError("budget must be positive")
        if bootstrap < 0 or bootstrap > budget:
            raise ValueError("bootstrap_count must be between zero and budget")
        if attempts <= 0:
            raise ValueError("generation attempt cap must be positive")
        if data.get("greybox_level") != "L1":
            raise ValueError("the headline VeriGrey granularity is globally fixed at L1")

        seed_generator = data.get("seed_generator")
        if not isinstance(seed_generator, dict):
            raise ValueError("seed_generator must be an object")
        extra_seed = sorted(set(seed_generator) - _SEED_FIELDS)
        missing_seed = sorted(_SEED_FIELDS - set(seed_generator))
        if extra_seed or missing_seed:
            raise ValueError("seed_generator must contain exactly batch_size, temperature, and build_retries")
        batch_size = _plain_int(seed_generator["batch_size"], "seed_generator.batch_size")
        build_retries = _plain_int(
            seed_generator["build_retries"], "seed_generator.build_retries"
        )
        temperature = seed_generator["temperature"]
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise ValueError("seed_generator.temperature must be numeric")
        if batch_size <= 0 or build_retries < 0 or not 0 <= float(temperature) <= 2:
            raise ValueError("invalid seed generator configuration")

        return cls(
            raw=copy.deepcopy(data),
            protocol_id=protocol_id,
            status=status,
            model=model,
            budget=budget,
            bootstrap_count=bootstrap,
            max_generation_attempts_per_execution=attempts,
            seed_generator=copy.deepcopy(seed_generator),
            greybox_level="L1",
            random_seed=random_seed,
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "CampaignProtocol":
        return cls.from_dict(json.loads(pathlib.Path(path).read_text()))

    @property
    def hash(self) -> str:
        return canonical_json_hash(self.raw)

    @property
    def headline_methods(self) -> tuple[str, ...]:
        return HEADLINE_METHODS

    def bootstrap_for(self, method: str) -> int:
        if method not in HEADLINE_METHODS:
            raise ValueError(f"unknown method: {method}")
        return self.bootstrap_count if method in ADAPTIVE_METHODS else 0

    def exploration_for(self, method: str) -> int:
        return self.budget - self.bootstrap_for(method)

    def allocation_for(self, method: str) -> dict[str, int]:
        return {
            "budget": self.budget,
            "bootstrap": self.bootstrap_for(method),
            "exploration": self.exploration_for(method),
        }
