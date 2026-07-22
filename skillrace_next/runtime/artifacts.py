from dataclasses import dataclass
from pathlib import Path

from ..storage import tree_hash


@dataclass(frozen=True)
class FrozenArtifact:
    path: Path
    tree_hash: str
    checker_uid: int


def freeze_artifact(path: str | Path, checker_uid: int) -> FrozenArtifact:
    artifact = Path(path)
    if not artifact.is_dir():
        raise ValueError("artifact must be an existing directory")
    digest = tree_hash(artifact)
    children = sorted(
        artifact.rglob("*"),
        key=lambda child: len(child.relative_to(artifact).parts),
        reverse=True,
    )
    for child in children:
        if child.is_symlink():
            continue
        child.chmod(child.stat().st_mode & ~0o222)
    artifact.chmod(artifact.stat().st_mode & ~0o222)
    return FrozenArtifact(path=artifact, tree_hash=digest, checker_uid=checker_uid)


def verify_artifact_unchanged(frozen: FrozenArtifact) -> bool:
    return tree_hash(frozen.path) == frozen.tree_hash
