import json
from pathlib import Path

from .records import ExperimentConfig
from .storage import atomic_write_json, canonical_json_hash


def load_config(path: str | Path) -> ExperimentConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    return ExperimentConfig.from_dict(raw)


def freeze_config(config: ExperimentConfig, output: str | Path) -> str:
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    normalized = config.to_dict()
    digest = canonical_json_hash(normalized)
    atomic_write_json(output_path / "config.json", normalized)
    (output_path / "config.sha256").write_text(f"{digest}\n", encoding="utf-8")
    return digest
