import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skillgen_base_has_rebuild_source_and_locked_parent():
    directory = ROOT / "images/skillgen-base"
    dockerfile = (directory / "Dockerfile.skillgen-base").read_text()
    lock = json.loads((directory / "base-image.lock.json").read_text())
    assert "skillrace/pi-base:0.73.1-glm-4.5-flash" in dockerfile
    assert "python3.11-pytest7.2.1-bootstrap" in dockerfile
    assert lock["schema"] == "skillrace-base-image-lock/1"
    assert lock["parent_tag"] == "skillrace/pi-base:0.73.1-glm-4.5-flash"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", lock["parent_image_id"])
    assert lock["python"] == "3.11.2"
    assert lock["pytest"] == "7.2.1"
    assert lock["construction_tag"] == "skillrace/skillgen-base:0.73.1-construction"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", lock["construction_image_id"])


def test_networked_skill_dependencies_are_exactly_pinned():
    expected = {
        "fastapi-endpoint": [
            "fastapi==0.139.0",
            "uvicorn==0.51.0",
            "httpx==0.28.1",
        ],
        "sqlmodel-orm": ["sqlmodel==0.0.39"],
        "yaml-config": ["pyyaml==6.0.3"],
        "json-parser": ["numpy==2.2.2", "pandas==2.2.3"],
    }
    for skill, requirements in expected.items():
        dockerfile = (ROOT / "skills" / skill / "Containerfile.base").read_text()
        assert "python3-pip" in dockerfile
        assert "--break-system-packages" in dockerfile
        for requirement in requirements:
            assert requirement in dockerfile
