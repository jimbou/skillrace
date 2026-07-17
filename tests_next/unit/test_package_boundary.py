import ast
from pathlib import Path


def test_clean_room_package_exists_and_never_imports_legacy() -> None:
    root = Path("skillrace_next")
    assert root.is_dir()
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "skillrace" or name.startswith("skillrace.") for name in names):
                offenders.append(str(path))
    assert offenders == []
