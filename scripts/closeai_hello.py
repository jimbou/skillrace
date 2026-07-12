#!/usr/bin/env python3
"""Make one minimal, journaled CloseAI hello call."""

from __future__ import annotations

import argparse
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skillrace.closeai import chat


def hello(chat_fn=chat, *, operation_id: str | None = None) -> str:
    operation_id = operation_id or f"manual.hello.{uuid.uuid4().hex}"
    result = chat_fn(
        [{"role": "user", "content": "Say hello in one short sentence."}],
        model="qwen3.6-flash",
        temperature=0.0,
        max_tokens=32,
        retries=1,
        reasoning=False,
        tag="manual.hello",
        operation_id=operation_id,
        timeout_seconds=30,
    )
    return result["content"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation-id",
        help="stable journal identity; omitted means a fresh ID for this invocation",
    )
    args = parser.parse_args()
    try:
        print(hello(operation_id=args.operation_id))
    except Exception as error:  # concise provider failure; never print credentials
        print(f"CloseAI hello failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
