"""Lean, explicit SkillRACE strategy boundary used by the evaluation protocol."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .io_utils import canonical_json_hash


@dataclass(frozen=True)
class AblationConfig:
    name: str
    frontier_policy: str
    signal_mode: str
    generator: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def hash(self) -> str:
        return canonical_json_hash(self.as_dict())

    def validate(self, *, headline: bool = False) -> "AblationConfig":
        if self.name not in {"full", "outcomes-only"}:
            raise ValueError(f"unsupported SkillRACE strategy {self.name!r}")
        if self.frontier_policy != "property-guided" or self.generator != "tree":
            raise ValueError("unsupported SkillRACE mechanism substitution")
        expected_signal = {
            "full": "reasoning-and-outcomes",
            "outcomes-only": "outcomes-only",
        }[self.name]
        if self.signal_mode != expected_signal:
            raise ValueError("strategy name and signal mode conflict")
        if headline and self.name != "full":
            raise ValueError("only full SkillRACE is permitted in headline comparisons")
        return self


ABLATIONS = {
    "full": AblationConfig(
        "full", "property-guided", "reasoning-and-outcomes", "tree"
    ),
    "outcomes-only": AblationConfig(
        "outcomes-only", "property-guided", "outcomes-only", "tree"
    ),
}


def get_strategy(name: str) -> AblationConfig:
    try:
        strategy = ABLATIONS[name]
    except (KeyError, TypeError) as error:
        raise ValueError(f"unsupported SkillRACE strategy {name!r}") from error
    return strategy.validate()


_REASONING_KEYS = {"reasoning", "opening_reasoning"}


def _strip_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_reasoning(item)
            for key, item in value.items()
            if key not in _REASONING_KEYS
        }
    if isinstance(value, list):
        return [_strip_reasoning(item) for item in value]
    return value


def guard_view(branch: Any, *, signal_mode: str) -> Any:
    """Return the exact JSON information permitted to reach guard extraction."""
    try:
        copied = json.loads(json.dumps(branch, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"guard signal must be JSON serializable: {error}") from error
    if signal_mode == "reasoning-and-outcomes":
        return copied
    if signal_mode == "outcomes-only":
        return _strip_reasoning(copied)
    raise ValueError(f"unsupported guard signal mode {signal_mode!r}")

