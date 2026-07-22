# Context Base Images

Date: 2026-07-22

## Scope

Create one frozen Docker image for each of the 30 selected Part I skills and ten
selected Part II scenarios. These images are development-test bases only. They do not
contain task fixtures, requested solutions, held-out inputs, checks, or method state.

Every final Dockerfile must complete its own recorded Docker build. Sharing cached layers
does not substitute for building each of the 40 Dockerfiles. Each successful build records
the resulting immutable image ID and deterministic capability probes.

## Image contract

Each image:

- derives from the pinned `skillrace/pi-runtime:0.73.1` runtime;
- preserves the Pi installation and model configuration;
- contains Python 3.12, pip, and pytest because generated Codex checker scripts may use
  Python even when the task itself does not;
- retains the runtime's Node.js, npm, Bash, Perl, and Git;
- adds only the context-specific tools listed below;
- uses `/workspace` as its working directory;
- contains no benchmark task data or solution; and
- has a unique local tag and an immutable image-ID receipt.

The weak Pi agent runs as root inside its owned task container. It may use the network to
install additional packages with apt, pip, or npm when necessary. A generated test
Dockerfile may also install more software while extending the context image. Installation
time counts against the same frozen weak-execution timeout. The agent receives no Docker
socket, privileged mode, or broader host access.

## Context-specific additions

The following mapping comes from the frozen property catalogs and the selected skill or
scenario descriptions. `core` means the image contract above with no further package.

### Part I

| Context | Addition |
| --- | --- |
| argparse-scaffolder | core |
| build-python-cli | core |
| cli-subcommand-validator | core |
| code-refactor-fowler | TypeScript compiler and ts-node |
| compiler-hardening | build-essential, clang, cmake, and binutils |
| condition-based-waiting | TypeScript compiler and ts-node |
| csv-workbench | core |
| data-transform | numpy and pandas |
| debugging-difficult-bugs | TypeScript compiler and ts-node |
| fastapi-endpoint | FastAPI, Uvicorn, Pydantic, SQLAlchemy, SQLModel, and HTTPX |
| file-check | core |
| fix-failing-test | core |
| frontent-design | core |
| js-feature | core |
| json-parser | core |
| log-parser | core |
| network-config-validation | core |
| parser-generator | core |
| refactor-complexity-reduce | core |
| refactor | core |
| regex-expert | core |
| sql-queries | sqlite3 command-line client |
| sql-query-generator | sqlite3 command-line client |
| sqlmodel-orm | sqlite3, FastAPI, Pydantic, SQLAlchemy, SQLModel, and HTTPX |
| systematic-debugging | core |
| test-driven-development | core |
| unit-test-generation | core |
| unit-test-generator | core |
| validator-agent | build-essential |
| yaml-config | PyYAML |

### Part II

All scenarios use `core` except `sqlite-query`, which also includes the sqlite3
command-line client. Python's standard `argparse`, `configparser`, `csv`, `json`, `re`,
`sqlite3`, and string/file facilities cover the other nine scenario domains.

## Files and provenance

Store the source Dockerfile and declared capability record under:

```text
skillrace_next/study/base-images/<part>/<context>/
```

A single manifest covers exactly the selected 30 Part I and ten Part II identifiers. Each
entry binds the selection source, Dockerfile hash, unique image tag, declared tools, probe
commands, and successful immutable image ID. A direct sequential build command processes
the manifest in order and writes sanitized build logs and receipts under
`out/live-contracts/study-base-images/<run-id>/`.

The full-study configs use the unique context tag. Test-generation prompts receive the
recorded capability description and explicitly state that additional online installation
is allowed inside the root-owned task container but consumes the task budget.

## Validation

Offline tests first fail unless:

- the manifest exactly covers the frozen selections with no duplicates;
- every entry has a Dockerfile and capability record;
- every Dockerfile uses the pinned Pi runtime and contains no task fixture or solution;
- the declared probes match the context package mapping; and
- proposal prompts use the selected context's capability record rather than the temporary
  fixture-wide hard-coded list.

After offline validation, build every final Dockerfile once in manifest order. Inspect the
image ID and run every declared command/version probe in the built image. A failed build or
probe stops the sequence and is not recorded as a frozen successful image.

Finally, run real DeepSeek v4 Flash and Qwen 3.6 Flash contracts on representative
specialized images. The calls must preserve same-track model provenance, generate a child
Docker environment from the context image, and execute a real root weak-agent task. Preserve
the proposal, build, execution, artifact, trace, and cleanup evidence. Terra remains the
checker where a verifier is required.

## Non-goals

Do not add an image registry service, dependency resolver, package manager abstraction,
dynamic image planner, or universal study image. Do not rebuild held-out images or change
the final package/cutover state.
