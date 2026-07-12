from __future__ import annotations

import pathlib

import pytest

import skillrace.runtime_trust as runtime_trust
from skillrace.generator import REALIZER_SYS, REPAIR_SYS
from skillrace.candidate_policy import (
    CandidatePolicyViolation,
    validate_candidate_containerfile,
    validate_generated_tail,
)
from skillrace.runtime_trust import (
    RuntimeFingerprintError,
    RuntimeIntegrityError,
    compare_runtime_fingerprints,
    fingerprint_image,
    verify_runtime_integrity,
)


SAFE_TAIL = """RUN mkdir -p /workspace/src
RUN cat > /workspace/src/app.py <<'EOF'
print('ENTRYPOINT is documentation, not a Docker instruction')
EOF
WORKDIR /workspace
"""


def test_generated_tail_policy_accepts_workspace_setup_without_runtime_control():
    assert validate_generated_tail(SAFE_TAIL) == SAFE_TAIL


def test_realizer_and_repair_prompts_explain_the_shared_runtime_boundary():
    for prompt in (REALIZER_SYS, REPAIR_SYS):
        assert "ENTRYPOINT" in prompt
        assert "/skills" in prompt
        assert "/root/.pi" in prompt
        assert "PATH" in prompt


def test_saved_candidate_containerfile_must_have_one_declared_base_and_same_policy():
    valid = "FROM demo:base\nRUN true\n"
    assert validate_candidate_containerfile(valid, "demo:base") == valid
    for invalid in [
        "FROM attacker:base\nRUN true\n",
        "FROM demo:base\nFROM attacker:base\n",
        "FROM demo:base\nRUN true\nENV HTTPS_PROXY=https://attacker.invalid\n",
    ]:
        with pytest.raises(CandidatePolicyViolation):
            validate_candidate_containerfile(invalid, "demo:base")


@pytest.mark.parametrize(
    "tail",
    [
        "ENTRYPOINT [\"/workspace/evil\"]",
        "CMD [\"evil\"]",
        "USER nobody",
        "SHELL [\"/workspace/shell\"]",
        "HEALTHCHECK CMD curl attacker",
        "ONBUILD RUN touch /workspace/pwned",
        "STOPSIGNAL SIGKILL",
        "VOLUME /workspace",
        "ENV APP_MODE=test",
        "ENV PATH=/workspace/bin:$PATH",
        "ENV NODE_OPTIONS=--require=/workspace/hook.js",
        "ENV BASH_ENV=/workspace/hook.sh",
        "ENV LD_PRELOAD=/workspace/evil.so",
        "ENV HTTPS_PROXY=https://attacker.invalid",
        "ENV NODE_EXTRA_CA_CERTS=/workspace/evil-ca.pem",
        "ENV XDG_CONFIG_HOME=/workspace/fake-config",
        "ENV GIT_CONFIG_GLOBAL=/workspace/evil.gitconfig",
        "RUN rm -rf /skills",
        "RUN printf evil > /root/.pi/agent/models.json",
        "RUN ln -sf /workspace/evil /usr/local/bin/pi",
        "RUN ln -sf /workspace/evil /usr/local/bin/node",
        "RUN printf evil > /etc/ssl/certs/ca-certificates.crt",
        "RUN printf evil > /etc/ld.so.preload",
        "RUN printf evil > /root/.bashrc",
        "RUN git config --global core.hooksPath /workspace/hooks",
        "RUN mkdir -p /workspace/.pi/extensions",
        "RUN mkdir -p .pi/extensions",
        "ENV SSLKEYLOGFILE=/workspace/tls.keys",
        "RUN export LD_AUDIT=/workspace/hook.so",
        "RUN export GLIBC_TUNABLES=glibc.malloc.check=3",
        "RUN export GCONV_PATH=/workspace/gconv",
        "RUN export PYTHONINSPECT=1",
        "RUN export PERL5OPT=-M/workspace/hook.pm",
        "RUN export RUBYOPT=-r/workspace/hook.rb",
        "RUN export GIT_CONFIG_SYSTEM=/workspace/gitconfig",
    ],
)
def test_generated_tail_policy_rejects_runtime_controls_hooks_and_protected_paths(tail):
    with pytest.raises(CandidatePolicyViolation):
        validate_generated_tail(tail)


def test_runtime_fingerprint_comparison_rejects_changed_or_missing_protected_files():
    base = {"/usr/local/bin/pi": "abc", "/bin/bash": "def"}
    with pytest.raises(RuntimeIntegrityError, match="/usr/local/bin/pi"):
        compare_runtime_fingerprints(
            base,
            {"/usr/local/bin/pi": "tampered", "/bin/bash": "def"},
        )
    with pytest.raises(RuntimeIntegrityError, match="/bin/bash"):
        compare_runtime_fingerprints(base, {"/usr/local/bin/pi": "abc"})


def test_protected_surface_covers_complete_pi_config_global_git_and_shell_hooks():
    assert "/root/.pi" in runtime_trust.PROTECTED_RUNTIME_PATHS
    assert "/root/.gitconfig" in runtime_trust.PROTECTED_RUNTIME_PATHS
    assert "/etc/profile.d" in runtime_trust.PROTECTED_RUNTIME_PATHS
    for path in [
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ld.so.conf.d",
        "/lib64/ld-linux-x86-64.so.2",
        "/lib/x86_64-linux-gnu/libc.so.6",
        "/lib/x86_64-linux-gnu/libtinfo.so.6",
    ]:
        assert path in runtime_trust.PROTECTED_RUNTIME_PATHS


def test_runtime_verifier_caches_base_but_fingerprints_each_candidate():
    calls = []

    def fake_fingerprint(image):
        calls.append(image)
        return {
            path: "same" for path in runtime_trust.PROTECTED_RUNTIME_PATHS
        }

    cache = {}
    verify_runtime_integrity("base:1", "candidate:1", fingerprint=fake_fingerprint,
                             dependency_discover=lambda image: (),
                             dependency_fingerprint=lambda image, paths: {},
                             base_cache=cache)
    verify_runtime_integrity("base:1", "candidate:2", fingerprint=fake_fingerprint,
                             dependency_discover=lambda image: (),
                             dependency_fingerprint=lambda image, paths: {},
                             base_cache=cache)
    assert calls == ["base:1", "candidate:1", "candidate:2"]


def test_runtime_verifier_preserves_fingerprint_infrastructure_failure():
    def unavailable(image):
        raise RuntimeFingerprintError("docker daemon unavailable")

    with pytest.raises(RuntimeFingerprintError, match="daemon"):
        verify_runtime_integrity("base:1", "candidate:1", fingerprint=unavailable,
                                 base_cache={})


def test_runtime_verifier_refuses_a_declared_base_missing_required_agent_files():
    def missing_pi(image):
        return {path: "<missing>" for path in runtime_trust.PROTECTED_RUNTIME_PATHS}

    with pytest.raises(RuntimeFingerprintError, match="declared base"):
        verify_runtime_integrity(
            "base:broken", "candidate:broken", fingerprint=missing_pi, base_cache={}
        )


def test_image_fingerprint_uses_create_and_cp_without_candidate_commands(tmp_path):
    calls = []

    class Result:
        def __init__(self, rc=0, out="", err=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "create"]:
            return Result(out="container-id\n")
        if argv[:2] == ["docker", "cp"]:
            destination = pathlib.Path(argv[-1])
            destination.write_bytes(b"trusted bytes")
            return Result()
        if argv[:3] == ["docker", "rm", "-f"]:
            return Result()
        raise AssertionError(argv)

    result = fingerprint_image(
        "candidate:built",
        protected_paths=("/bin/bash",),
        run=fake_run,
        temp_root=tmp_path,
    )

    assert result["/bin/bash"]
    create = calls[0]
    assert create[:2] == ["docker", "create"]
    assert create[create.index("--entrypoint") + 1] == "/bin/true"
    assert any(call[:2] == ["docker", "cp"] for call in calls)
    assert any(call[:3] == ["docker", "cp", "-L"] for call in calls)
    assert all("exec" not in call for call in calls)
