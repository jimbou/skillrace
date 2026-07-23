#!/usr/bin/env python3
"""Make one minimal Yunwu call and report its content, usage, and cost."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skillrace.closeai import chat
from skillrace.model_policy import DEFAULT_DEVELOPMENT_MODEL, SUPPORTED_MODELS


def hello(
    chat_fn=chat,
    *,
    model: str = DEFAULT_DEVELOPMENT_MODEL,
    operation_id: str | None = None,
) -> dict[str, object]:
    """Send the provider's minimal prompt and return compact billing details."""

    operation_id = operation_id or f"manual.yunwu-hello-cost.{uuid.uuid4().hex}"
    result = chat_fn(
        [{"role": "user", "content": "Say this is a test!"}],
        model=model,
        temperature=0.0,
        max_tokens=32,
        retries=1,
        reasoning=False,
        tag="manual.yunwu-hello-cost",
        operation_id=operation_id,
        timeout_seconds=180,
        journal_mode="development",
    )
    return {
        "model": model,
        "content": result["content"],
        "usage": result["usage"],
        "cost_provider_credits": result["cost_provider_credits"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", choices=SUPPORTED_MODELS, default=DEFAULT_DEVELOPMENT_MODEL
    )
    parser.add_argument(
        "--operation-id",
        help="stable journal identity; omitted means a fresh ID for this invocation",
    )
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                hello(model=args.model, operation_id=args.operation_id),
                indent=2,
            )
        )
    except Exception as error:  # concise provider failure; never print credentials
        print(f"Yunwu hello failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
