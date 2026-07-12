"""Validated, executable per-skill property applicability records."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass


FIXED_INVARIANT_IDS = frozenset(
    {
        "fixed-no-force-push",
        "fixed-no-destructive-rm",
        "fixed-no-pathological-repetition",
        "fixed-terminated-within-budget",
    }
)


@dataclass(frozen=True)
class ApplicableProperties:
    """The exact properties and fixed checks selected for one skill."""

    properties: list[dict]
    fixed_invariants: list[str]
    categories: list[str]
    contingency: str

    def metadata(self) -> dict:
        """Return the JSON record embedded in a compiled-check manifest."""
        return {
            "property_ids": [item["id"] for item in self.properties],
            "fixed_invariants": list(self.fixed_invariants),
            "categories": list(self.categories),
            "contingency": self.contingency,
        }


def _string_list(value, field, source):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings in {source}")
    return value


def _duplicates(values):
    seen = set()
    return [value for value in values if value in seen or seen.add(value)]


def load_applicable_properties(
    skill_dir: str | pathlib.Path,
) -> ApplicableProperties:
    """Load and validate one skill's properties and applicability matrix."""
    root = pathlib.Path(skill_dir)
    properties_path = root / "properties.json"
    matrix_path = root / "applicability.json"
    properties = json.loads(properties_path.read_text())
    matrix = json.loads(matrix_path.read_text())
    if not isinstance(properties, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        or not item["id"]
        for item in properties
    ):
        raise ValueError(f"properties must be a list with non-empty ids in {properties_path}")
    if not isinstance(matrix, dict):
        raise ValueError(f"applicability matrix must be an object in {matrix_path}")

    all_ids = [item["id"] for item in properties]
    duplicates = _duplicates(all_ids)
    if duplicates:
        raise ValueError(f"duplicate property id(s) in {properties_path}: {duplicates}")
    by_id = {item["id"]: item for item in properties}

    selected_ids = _string_list(matrix.get("property_ids"), "property_ids", matrix_path)
    duplicates = _duplicates(selected_ids)
    if duplicates:
        raise ValueError(f"duplicate property id(s) in {matrix_path}: {duplicates}")
    unknown = [property_id for property_id in selected_ids if property_id not in by_id]
    if unknown:
        raise ValueError(f"unknown property id(s) in {matrix_path}: {unknown}")

    fixed = _string_list(
        matrix.get("fixed_invariants"), "fixed_invariants", matrix_path
    )
    duplicates = _duplicates(fixed)
    if duplicates:
        raise ValueError(f"duplicate fixed invariant(s) in {matrix_path}: {duplicates}")
    unknown_fixed = [item for item in fixed if item not in FIXED_INVARIANT_IDS]
    if unknown_fixed:
        raise ValueError(f"unknown fixed invariant(s) in {matrix_path}: {unknown_fixed}")

    categories = _string_list(
        matrix.get("sbe_categories"), "sbe_categories", matrix_path
    )
    contingency = matrix.get("contingency")
    if contingency not in {"low", "medium", "high"}:
        raise ValueError(
            f"contingency must be low, medium, or high in {matrix_path}"
        )

    return ApplicableProperties(
        properties=[by_id[property_id] for property_id in selected_ids],
        fixed_invariants=list(fixed),
        categories=list(categories),
        contingency=contingency,
    )
