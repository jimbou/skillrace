"""Validated, reviewable contract for a SkillRACE testing campaign.

The protocol is intentionally small: it exposes only choices that are part of the
paper's frozen comparison.  In particular, there is one model field and one global
VeriGrey granularity; callers cannot quietly tune a role or a skill independently.
"""

from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass, replace
from typing import Any

from .io_utils import canonical_json_hash
from .model_policy import AGENT_MODELS, EXPERIMENT_MODELS, REASONING_TRACE_MODELS


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
    "repair",
} | _ROLE_MODEL_FIELDS
_SEED_FIELDS = {"batch_size", "temperature", "build_retries"}
_REPAIR_FIELDS = {
    "enabled", "timeout_seconds", "max_output_tokens", "temperature",
    "reasoning", "backend_by_method",
}
_PATCH_BACKENDS = {"direct", "pi"}


def _plain_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


@dataclass(frozen=True)
class RepairPolicy:
    enabled: bool
    timeout_seconds: int
    max_output_tokens: int
    temperature: float
    reasoning: bool
    backend_by_method: dict[str, str]

    @classmethod
    def from_dict(cls, value: Any) -> "RepairPolicy":
        if not isinstance(value, dict) or set(value) != _REPAIR_FIELDS:
            raise ValueError("repair must contain exactly the frozen repair fields")
        if not isinstance(value["enabled"], bool):
            raise ValueError("repair.enabled must be boolean")
        timeout = _plain_int(value["timeout_seconds"], "repair.timeout_seconds")
        output = _plain_int(value["max_output_tokens"], "repair.max_output_tokens")
        temperature = value["temperature"]
        if (
            not 1 <= timeout <= 600
            or not 1 <= output <= 65536
            or isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0 <= float(temperature) <= 2
            or not isinstance(value["reasoning"], bool)
        ):
            raise ValueError("repair limits or reasoning configuration are invalid")
        backends = value["backend_by_method"]
        if not isinstance(backends, dict) or set(backends) != set(HEADLINE_METHODS):
            raise ValueError("repair backend_by_method must cover every headline method")
        if any(backend not in _PATCH_BACKENDS for backend in backends.values()):
            raise ValueError("repair backend must be direct or pi")
        return cls(
            enabled=value["enabled"],
            timeout_seconds=timeout,
            max_output_tokens=output,
            temperature=float(temperature),
            reasoning=value["reasoning"],
            backend_by_method=copy.deepcopy(backends),
        )

    def backend_for(self, method: str) -> str:
        if method not in HEADLINE_METHODS:
            raise ValueError(f"unknown repair method: {method}")
        return self.backend_by_method[method]


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
    repair: RepairPolicy

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
        if status == "runtime" and model not in AGENT_MODELS:
            raise ValueError(
                f"model is not agent-capable through the configured Yunwu API: {model}"
            )
        if status == "runtime" and model not in REASONING_TRACE_MODELS:
            raise ValueError(
                f"model does not expose the reasoning trace required by SkillRACE: {model}"
            )
        if status != "runtime" and model not in EXPERIMENT_MODELS:
            raise ValueError(
                f"model is not a selected experiment model for this protocol status: {model}"
            )
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
        repair = RepairPolicy.from_dict(data.get("repair"))

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
            repair=repair,
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "CampaignProtocol":
        return cls.from_dict(json.loads(pathlib.Path(path).read_text()))

    @classmethod
    def load_legacy_development_resume(
        cls, path: str | pathlib.Path
    ) -> "CampaignProtocol":
        """Load one pre-repair-policy development protocol without changing its hash.

        This compatibility path exists only to finish already-started, non-headline
        campaigns. Normal parsing remains strict, and the synthetic disabled repair
        policy is runtime metadata rather than part of the immutable raw protocol.
        """

        data = json.loads(pathlib.Path(path).read_text())
        if (
            not isinstance(data, dict)
            or data.get("schema") != "campaign-protocol/1"
            or data.get("status") != "runtime"
            or not isinstance(data.get("protocol_id"), str)
            or not data["protocol_id"].startswith("development-only-")
            or "repair" in data
        ):
            raise ValueError(
                "legacy development resume requires a repair-less, runtime, "
                "development-only protocol"
            )
        augmented = copy.deepcopy(data)
        augmented["repair"] = {
            "enabled": False,
            "timeout_seconds": 300,
            "max_output_tokens": 4000,
            "temperature": 0.0,
            "reasoning": True,
            "backend_by_method": {
                "random": "direct",
                "greybox": "direct",
                "skillrace": "pi",
            },
        }
        parsed = cls.from_dict(augmented)
        return replace(parsed, raw=copy.deepcopy(data))

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
