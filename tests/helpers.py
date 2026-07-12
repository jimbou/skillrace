from __future__ import annotations

import json
import pathlib


def assistant_tool(name: str, arguments: dict, *, extra_content=None):
    content = list(extra_content or [])
    content.append({"type": "toolCall", "name": name, "arguments": arguments})
    return {"message": {"role": "assistant", "content": content}}


def write_session(rows, root: pathlib.Path):
    run_dir = root
    (run_dir / "raw").mkdir(parents=True)
    with (run_dir / "raw" / "session.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    return run_dir
