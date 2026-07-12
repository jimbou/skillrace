"""Host-side policy for model-generated Dockerfile tails.

Generated environments may construct arbitrary projects under ``/workspace`` and
install ordinary dependencies.  They may not control how the trusted agent starts
or alter the protected Pi/skill/runtime surface inherited from the declared base.
The post-build fingerprint is the authoritative backstop; this textual policy
rejects clear attempts before spending more Docker work.
"""

from __future__ import annotations

import re


class CandidatePolicyViolation(ValueError):
    """The generated environment exceeds the candidate trust boundary."""


_RUNTIME_INSTRUCTIONS = {
    "CMD",
    "ENV",
    "ENTRYPOINT",
    "HEALTHCHECK",
    "ONBUILD",
    "SHELL",
    "STOPSIGNAL",
    "USER",
    "VOLUME",
}
_UNSAFE_ENV_KEYS = {
    "BASH_ENV",
    "CLOSE_API_KEY",
    "DOCKER_HOST",
    "ENV",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "HOME",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "NODE_EXTRA_CA_CERTS",
    "NODE_PATH",
    "NPM_CONFIG_PREFIX",
    "PATH",
    "PI_PROMPT",
    "PROMPT_COMMAND",
    "PYTHONPATH",
    "SHELLOPTS",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SSLKEYLOGFILE",
    "CURL_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "XDG_CONFIG_HOME",
}
_PROTECTED_PATHS = re.compile(
    r"(?:"
    r"/skills(?:/|\b)|"
    r"/workspace/\.pi(?:/|\b)|"
    r"/root/\.pi(?:/|\b)|"
    r"/usr/local/bin/(?:pi|node)(?:\b|/)|"
    r"/usr/local/lib/node_modules(?:/|\b)|"
    r"/etc/ld\.so\.preload(?:\b|/)|"
    r"/etc/ssl/certs/ca-certificates\.crt(?:\b|/)|"
    r"/etc/(?:bash\.bashrc|environment|profile)(?:\b|/)|"
    r"/root/\.(?:bash_profile|bashrc|gitconfig|profile)(?:\b|/)|"
    r"/(?:bin|usr/bin)/(?:bash|sh|env|git|node|sleep)(?:\b|/)"
    r")"
)
_PROTECTED_COMMAND = re.compile(
    r"(?<![\w.-])(?:/usr/bin/)?git\s+config\s+--global\b"
)
_PROJECT_PI_PATH = re.compile(r"(?<![\w/])(?:\./)?\.pi(?:/|\b)")
_DANGEROUS_HOOK_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"LD_[A-Z0-9_]*|GLIBC_[A-Z0-9_]*|GCONV_[A-Z0-9_]*|"
    r"BASH_ENV|ENV|NODE_OPTIONS|NODE_PATH|"
    r"PYTHON[A-Z0-9_]*|PERL[A-Z0-9_]*|RUBY[A-Z0-9_]*|"
    r"SSL_CERT[A-Z0-9_]*|CURL_[A-Z0-9_]*|GIT_[A-Z0-9_]*"
    r")\s*="
)


def _instructions(tail: str):
    """Yield top-level Dockerfile instructions, excluding heredoc bodies."""
    inside = None
    continuing = ""
    for raw in tail.splitlines():
        if inside is not None:
            if raw.strip() == inside:
                inside = None
            continue
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if continuing:
            continuing += "\n" + stripped
            if raw.rstrip().endswith("\\"):
                continue
            stripped, continuing = continuing, ""
        elif raw.rstrip().endswith("\\"):
            continuing = stripped[:-1].rstrip()
            continue
        match = re.match(r"([A-Za-z]+)(?:\s+|$)(.*)", stripped, re.DOTALL)
        if match:
            instruction, argument = match.group(1).upper(), match.group(2)
            yield instruction, argument
        heredoc = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", raw)
        if heredoc:
            inside = heredoc.group(1)


def _env_keys(argument: str) -> set[str]:
    tokens = argument.replace("\n", " ").split()
    if not tokens:
        return set()
    if "=" not in tokens[0]:
        return {tokens[0]}
    return {token.split("=", 1)[0] for token in tokens if "=" in token}


def validate_generated_tail(tail: str) -> str:
    if not isinstance(tail, str) or not tail.strip():
        raise CandidatePolicyViolation("generated tail must be nonempty text")
    if "\x00" in tail:
        raise CandidatePolicyViolation("generated tail contains a NUL byte")
    protected = _PROTECTED_PATHS.search(tail)
    if protected:
        raise CandidatePolicyViolation(
            f"generated tail references protected runtime path: {protected.group(0)}"
        )
    if _PROTECTED_COMMAND.search(tail):
        raise CandidatePolicyViolation(
            "generated tail may not modify protected global git configuration"
        )
    if _PROJECT_PI_PATH.search(tail):
        raise CandidatePolicyViolation(
            "generated tail may not create project-local Pi runtime configuration"
        )
    hook = _DANGEROUS_HOOK_ASSIGNMENT.search(tail)
    if hook:
        raise CandidatePolicyViolation(
            f"generated tail may not set runtime interception hook {hook.group(0).split('=')[0].strip()}"
        )
    for instruction, argument in _instructions(tail):
        if instruction in _RUNTIME_INSTRUCTIONS:
            raise CandidatePolicyViolation(
                f"generated tail may not use runtime-control instruction {instruction}"
            )
        if instruction == "ENV":
            unsafe = sorted(_env_keys(argument) & _UNSAFE_ENV_KEYS)
            if unsafe:
                raise CandidatePolicyViolation(
                    f"generated tail may not set runtime hook {unsafe[0]}"
                )
    return tail


def validate_candidate_containerfile(containerfile: str, base_image: str) -> str:
    """Validate a saved case again at the runner boundary."""
    validate_generated_tail(containerfile)
    from_instructions = [
        argument.strip()
        for instruction, argument in _instructions(containerfile)
        if instruction == "FROM"
    ]
    if from_instructions != [base_image]:
        raise CandidatePolicyViolation(
            "candidate Dockerfile must contain exactly one FROM matching its declared base"
        )
    return containerfile
