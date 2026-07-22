# Context Base Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, probe, and freeze one minimal Docker base image for each selected Part I skill and Part II scenario, then supply its exact capabilities to development-test generation.

**Architecture:** Static per-context Dockerfiles and capability records live under `skillrace_next/study/base-images/`. One small module validates exact selection coverage, performs a direct sequential Docker build/probe loop, writes immutable receipts, and looks up prompt context by image tag. Random, VeriGrey, and SkillRACE insert that context directly into their existing prompts.

**Tech Stack:** Python functions and dataclasses, JSON, `subprocess.run`, Dockerfiles, pytest, Docker CLI, Pi/Yunwu live contracts.

---

### Task 1: Capability lookup and proposal prompts

**Files:**
- Create: `skillrace_next/study_images.py`
- Modify: `skillrace_next/methods/random.py`
- Modify: `skillrace_next/methods/verigrey.py`
- Modify: `skillrace_next/methods/skillrace.py`
- Create: `tests_next/unit/test_study_images.py`
- Modify: `tests_next/unit/test_random.py`
- Modify: `tests_next/unit/test_verigrey.py`
- Modify: `tests_next/unit/test_skillrace_initialization.py`
- Modify: `tests_next/unit/test_edge_selector.py`

- [ ] **Step 1: Write the failing lookup test**

Create a temporary manifest containing one entry and assert:

```python
context = capability_for_image(
    "skillrace-next/study-part1-data-transform:2026-07-22", manifest
)
assert "numpy" in context.text
assert context.manifest_hash == file_hash(manifest)
```

Also assert that an unknown image fails closed when a manifest exists, while the temporary
`skillrace-next/task-fixture:test` image returns its explicit fixture description.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_study_images.py
```

Expected: collection fails because `skillrace_next.study_images` does not exist.

- [ ] **Step 3: Implement the direct lookup**

```python
@dataclass(frozen=True)
class ImageCapability:
    image_tag: str
    text: str
    manifest_hash: str

def capability_for_image(
    image_tag: str,
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> ImageCapability:
    if image_tag == "skillrace-next/task-fixture:test":
        return ImageCapability(image_tag, FIXTURE_CAPABILITY_TEXT, "fixture")
    path = Path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("images")
    if data.get("schema") != "skillrace-study-base-images/1" or not isinstance(entries, list):
        raise ValueError("study image manifest is invalid")
    matches = [entry for entry in entries if entry.get("image_tag") == image_tag]
    if len(matches) != 1:
        raise ValueError(f"expected one capability record for {image_tag}")
    text = matches[0].get("capability_text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("study image capability text is invalid")
    return ImageCapability(image_tag, text.strip(), file_hash(path))
```

Read one JSON manifest, select exactly one matching tag, reject duplicate or unknown study
tags, and keep one explicit fallback for the temporary test fixture.

- [ ] **Step 4: Write failing prompt tests**

Assert Random, VeriGrey seed/mutation, and SkillRACE planning/materialization/mutation all
include the recorded base-image capabilities and say that the root task agent may install
packages online within the unchanged task budget. Assert obsolete no-network and fixed
missing-tool claims are absent.

- [ ] **Step 5: Verify prompt tests RED**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_random.py tests_next/unit/test_verigrey.py tests_next/unit/test_skillrace_initialization.py tests_next/unit/test_edge_selector.py
```

Expected: failures on missing capability context and obsolete network wording.

- [ ] **Step 6: Insert the capability context directly**

Call `capability_for_image(config.docker_image)` at each existing test-generation boundary.
Do not change weak-agent, episode, tree, patcher, replay, or verifier prompts. Add the
manifest hash to existing generated proposal receipts.

- [ ] **Step 7: Verify GREEN and commit**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_study_images.py tests_next/unit/test_random.py tests_next/unit/test_verigrey.py tests_next/unit/test_skillrace_initialization.py tests_next/unit/test_edge_selector.py
git add skillrace_next/study_images.py skillrace_next/methods tests_next/unit
git commit -m "feat(skillrace-next): bind image capabilities to proposals"
```

### Task 2: Exact source validation and sequential builder

**Files:**
- Modify: `skillrace_next/study_images.py`
- Modify: `skillrace_next/cli.py`
- Modify: `tests_next/unit/test_study_images.py`
- Modify: `tests_next/unit/test_cli.py`

- [ ] **Step 1: Write failing source-validation tests**

Use temporary selections and source directories. Assert rejection of missing or extra
contexts, duplicate tags, malformed capability records, wrong base images, embedded task
fixtures, and absent probes. Assert exact coverage succeeds.

- [ ] **Step 2: Verify validation tests RED**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_study_images.py -k source
```

Expected: failures because source validation is absent.

- [ ] **Step 3: Implement validation and the direct build loop**

```python
def validate_image_sources(
    source_root: Path,
    part1_selection: Path,
    part2_selection: Path,
) -> list[dict[str, Any]]:
    part1 = json.loads(part1_selection.read_text(encoding="utf-8"))["selected"]
    part2 = json.loads(part2_selection.read_text(encoding="utf-8"))["scenarios"]
    expected = [("part1", item["skill_id"]) for item in part1]
    expected += [("part2", item["scenario_id"]) for item in part2]
    records = []
    for part, context_id in expected:
        directory = source_root / part / context_id
        dockerfile = directory / "Dockerfile"
        capability_path = directory / "capabilities.json"
        capability = json.loads(capability_path.read_text(encoding="utf-8"))
        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        if capability["part"] != part or capability["context_id"] != context_id:
            raise ValueError("study image identity differs")
        if "FROM skillrace/pi-runtime:0.73.1" not in dockerfile_text:
            raise ValueError("study image base differs")
        records.append({**capability, "dockerfile": str(dockerfile)})
    if len({record["image_tag"] for record in records}) != len(records):
        raise ValueError("study image tags must be unique")
    return records

def build_study_images(
    source_root: Path,
    evidence_root: Path,
    run_id: str,
) -> Path:
    records = validate_image_sources(source_root, PART1_SELECTION, PART2_SELECTION)
    built = []
    for record in records:
        context = Path(record["dockerfile"]).parent
        subprocess.run(
            ["docker", "build", "--tag", record["image_tag"], str(context)],
            check=True,
            timeout=3600,
        )
        image_id = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", record["image_tag"]],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["docker", "run", "--rm", record["image_tag"], "sh", "-lc", " && ".join(record["probes"])],
            check=True,
            timeout=120,
        )
        built.append({**record, "image_id": image_id})
    manifest = source_root / "manifest.json"
    atomic_write_json(manifest, {"schema": "skillrace-study-base-images/1", "images": built})
    return manifest
```

Loop in Part I rank order followed by Part II selection order. For each final Dockerfile,
run one authoritative `docker build --tag`, one `docker image inspect`, and one
`docker run --rm` probe. Use a 3600-second per-build timeout, stop on failure, preserve logs,
and write the aggregate manifest only after all images pass.

- [ ] **Step 4: Write the failing subprocess-order test**

Inject a recording command runner. Assert the exact sequence is build, inspect, probe for
each context. Assert a failed build stops immediately and no successful aggregate manifest
is written.

- [ ] **Step 5: Add the explicit CLI command**

Add:

```text
python -m skillrace_next build-study-images --live --run-id <id>
```

Require `--live`, use only the frozen source root, and write evidence under
`out/live-contracts/study-base-images/<run-id>/`.

- [ ] **Step 6: Verify GREEN and commit**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_study_images.py tests_next/unit/test_cli.py
git add skillrace_next/study_images.py skillrace_next/cli.py tests_next/unit/test_study_images.py tests_next/unit/test_cli.py
git commit -m "feat(skillrace-next): build study images sequentially"
```

### Task 3: Add all 40 frozen image sources

**Files:**
- Create: `skillrace_next/study/base-images/part1/<context>/Dockerfile` for all 30 selected skills
- Create: `skillrace_next/study/base-images/part1/<context>/capabilities.json` for all 30 selected skills
- Create: `skillrace_next/study/base-images/part2/<context>/Dockerfile` for all ten selected scenarios
- Create: `skillrace_next/study/base-images/part2/<context>/capabilities.json` for all ten selected scenarios
- Modify: `tests_next/unit/test_study_images.py`

- [ ] **Step 1: Write the failing real-selection coverage test**

Validate the repository selections and assert exactly 40 ordered records and 40 unique
tags. Verify required probes: compiler tools for compiler-hardening, TypeScript commands
for its three contexts, Python imports for data/API/YAML contexts, sqlite3 for SQL contexts,
and g++ for validator-agent.

- [ ] **Step 2: Verify repository test RED**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_study_images.py -k repository
```

Expected: failure because the source directories are absent.

- [ ] **Step 3: Add every Dockerfile and capability record**

Each Dockerfile uses this shape:

```dockerfile
FROM python:3.12.13-bookworm AS checker-python
RUN python -m pip install --no-cache-dir pytest==9.1.1 <context pins>

FROM skillrace/pi-runtime:0.73.1
COPY --from=checker-python /usr/local /usr/local
<optional apt or npm installation>
WORKDIR /workspace
```

Use unique tags `skillrace-next/study-<part>-<context>:2026-07-22`. Copy small local
duplication instead of adding a profile generator. Capability text lists exact installed
commands/packages and permits online root installation within the task budget.

- [ ] **Step 4: Verify and commit sources**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_study_images.py -k repository
git add skillrace_next/study/base-images tests_next/unit/test_study_images.py
git commit -m "build(skillrace-next): define study base images"
```

### Task 4: Build every Dockerfile once and freeze receipts

**Files:**
- Create: `skillrace_next/study/base-images/manifest.json`
- Modify: `skillrace_next/docs/CURRENT_STATUS.md`
- Modify: `skillrace_next/docs/FULL_STUDY_REMAINING_TODO.md`

- [ ] **Step 1: Run the authoritative sequential build**

```bash
python -m skillrace_next build-study-images --live --run-id 20260722T-context-images
```

Expected: 40 build receipts, immutable image IDs, passing probes, and one aggregate
manifest. Every context Dockerfile has its own successful build command; cached layers are
allowed.

- [ ] **Step 2: Inspect evidence and Docker state**

Verify every receipt binds its Dockerfile hash and declared capabilities. Confirm one base
Pi image ID, 30 Part I and ten Part II entries, no active provider credential in build
logs, and no owned probe container.

- [ ] **Step 3: Run offline verification and commit**

```bash
PYTHONPATH=. pytest -q tests_next/unit tests_next/integration
python -m compileall -q skillrace_next tests_next
git diff --check -- skillrace_next tests_next
git add skillrace_next/study/base-images/manifest.json skillrace_next/docs/CURRENT_STATUS.md skillrace_next/docs/FULL_STUDY_REMAINING_TODO.md
git commit -m "build(skillrace-next): freeze study base images"
```

### Task 5: Real two-model image contract

**Files:**
- Create: `tests_next/live/test_study_base_images_live.py`
- Modify: `skillrace_next/cli.py`
- Modify: `skillrace_next/docs/CURRENT_STATUS.md`
- Modify: `skillrace_next/docs/FULL_STUDY_REMAINING_TODO.md`

- [ ] **Step 1: Write the live contract**

Require `--live`. Use DeepSeek v4 Flash on compiler-hardening and Qwen 3.6 Flash on
data-transform or a TypeScript context. For each track, make a real proposal, build the
child Dockerfile, execute one real root weak agent, preserve artifact/trace/cleanup evidence,
and use Terra plus authoritative Docker checks when behavioral verification is included.

- [ ] **Step 2: Run and inspect both tracks**

```bash
source /home/jim/.bashrc
PYTHONPATH=. pytest -q tests_next/live/test_study_base_images_live.py --live -v -s
```

Inspect the proposal, child Dockerfile, root installation behavior, artifact, trace, and
receipts. Provider failure is terminal and is not mocked or relabeled.

- [ ] **Step 3: Run final verification and commit**

```bash
PYTHONPATH=. pytest -q tests_next/unit tests_next/integration
python -m compileall -q skillrace_next tests_next
git diff --check -- skillrace_next tests_next
git add tests_next/live/test_study_base_images_live.py skillrace_next/cli.py skillrace_next/docs/CURRENT_STATUS.md skillrace_next/docs/FULL_STUDY_REMAINING_TODO.md
git commit -m "test(skillrace-next): verify study base images live"
```

Do not freeze the 80 full-study configs or launch the full experiment in this task.
