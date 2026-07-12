from __future__ import annotations

import pytest

import skillrace.runtime_trust as runtime_trust
from skillrace.runtime_trust import (
    DependencyDiscoveryError,
    RuntimeIntegrityError,
    discover_base_dependencies,
    parse_ldd_dependencies,
    verify_runtime_integrity,
)


GIT_LDD = """
linux-vdso.so.1 (0x00007fff)
libpcre2-8.so.0 => /lib/x86_64-linux-gnu/libpcre2-8.so.0 (0x001)
libz.so.1 => /lib/x86_64-linux-gnu/libz.so.1 (0x002)
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x003)
/lib64/ld-linux-x86-64.so.2 (0x004)
"""

NODE_LDD = """
libstdc++.so.6 => /usr/lib/x86_64-linux-gnu/libstdc++.so.6 (0x010)
libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x011)
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x012)
/lib64/ld-linux-x86-64.so.2 (0x013)
"""


def test_ldd_parser_captures_git_node_and_direct_loader_paths_sorted_and_deduped():
    parsed = parse_ldd_dependencies(GIT_LDD + NODE_LDD)
    assert parsed == tuple(
        sorted(
            {
                "/lib/x86_64-linux-gnu/libpcre2-8.so.0",
                "/lib/x86_64-linux-gnu/libz.so.1",
                "/lib/x86_64-linux-gnu/libc.so.6",
                "/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
                "/lib/x86_64-linux-gnu/libm.so.6",
                "/lib64/ld-linux-x86-64.so.2",
            }
        )
    )


@pytest.mark.parametrize(
    "output",
    [
        "libpcre2-8.so.0 => not found",
        "libz.so.1 => relative/path (0x1)",
        "this is malformed ldd output",
    ],
)
def test_ldd_parser_rejects_missing_relative_or_malformed_dependencies(output):
    with pytest.raises(DependencyDiscoveryError):
        parse_ldd_dependencies(output)


def test_dependency_discovery_runs_only_against_declared_base_and_follows_symlink_names():
    calls = []
    outputs = {
        "/bin/bash": GIT_LDD,
        "/bin/sh": GIT_LDD,
        "/usr/local/bin/node": NODE_LDD,
        "/usr/local/bin/pi": "not a dynamic executable",
        "/usr/bin/git": GIT_LDD,
        "/usr/bin/env": GIT_LDD,
        "/usr/bin/sleep": GIT_LDD,
    }

    def execute(image, executable):
        calls.append((image, executable))
        if executable in outputs:
            rc = 1 if executable == "/usr/local/bin/pi" else 0
            return rc, outputs[executable]
        return 1, "No such file or directory"

    dependencies = discover_base_dependencies("trusted-base:1", execute=execute)
    assert "/lib/x86_64-linux-gnu/libpcre2-8.so.0" in dependencies
    assert "/usr/lib/x86_64-linux-gnu/libstdc++.so.6" in dependencies
    assert {image for image, _ in calls} == {"trusted-base:1"}
    assert ("trusted-base:1", "/bin/sh") in calls
    assert not any("candidate" in image for image, _ in calls)


def test_mandatory_dependency_discovery_failure_is_infrastructure():
    with pytest.raises(DependencyDiscoveryError, match="/bin/bash"):
        discover_base_dependencies(
            "trusted-base:1",
            execute=lambda image, executable: (2, "ldd crashed"),
        )


def test_dynamic_dependency_change_rejects_candidate_and_base_closure_is_cached():
    static = {
        path: "same" for path in runtime_trust.PROTECTED_RUNTIME_PATHS
    }
    discovery_calls = []
    dynamic_calls = []

    def fingerprint(image):
        return dict(static)

    def discover(image):
        discovery_calls.append(image)
        return ("/lib/libgit.so", "/lib/libnode.so")

    def fingerprint_paths(image, paths):
        dynamic_calls.append((image, tuple(paths)))
        if image == "candidate:tampered":
            return {paths[0]: "changed", paths[1]: "same"}
        return {path: "same" for path in paths}

    cache = {}
    with pytest.raises(RuntimeIntegrityError, match="libgit"):
        verify_runtime_integrity(
            "trusted-base:1",
            "candidate:tampered",
            fingerprint=fingerprint,
            dependency_discover=discover,
            dependency_fingerprint=fingerprint_paths,
            base_cache=cache,
        )
    verify_runtime_integrity(
        "trusted-base:1",
        "candidate:clean",
        fingerprint=fingerprint,
        dependency_discover=discover,
        dependency_fingerprint=fingerprint_paths,
        base_cache=cache,
    )
    assert discovery_calls == ["trusted-base:1"]
    assert [image for image, _ in dynamic_calls].count("trusted-base:1") == 1
    assert all(image != "candidate:tampered" for image in discovery_calls)
