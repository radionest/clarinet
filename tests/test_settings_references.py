"""G1: every settings key and CLARINET_* name the repo ships must be a real setting.

Settings uses extra="ignore", so a stale name in a shipped file or message is
dropped without an error and the project silently runs on the default. This
test replaces the review that kept missing that class (#504).
"""

import re
import shutil
import subprocess
from functools import cache
from pathlib import Path

import pytest

from clarinet.settings import Settings

REPO = Path(__file__).resolve().parent.parent
# Settings(...) kwargs are ignored (init source dropped), so names come from the model.
FIELDS = frozenset(Settings.model_fields) | {
    f.alias for f in Settings.model_fields.values() if f.alias
}
# No env_nested_delimiter (settings.py model_config): CLARINET_<NAME> <-> field <name>.

# Git pathspecs (a `*` also crosses `/`): only tracked files are scanned, so local
# gitignored scratch (docs/superpowers/, .superpowers/) never fails a dev machine.
TOML_GROUPS: dict[str, tuple[str, ...]] = {
    "scaffold payload": ("clarinet/scaffold/settings*.toml",),
    "demo": ("examples/demo/settings.toml",),
    "settings.toml.example": ("settings.toml.example",),
    "settings.frontend.toml": ("settings.frontend.toml",),
}
ENV_GROUPS: dict[str, tuple[str, ...]] = {
    "clarinet/": (
        "clarinet/*.py",
        "clarinet/*.md",
        "clarinet/*.toml",
        "clarinet/scaffold/env.example",
    ),
    "docs/": ("docs/",),
    "examples/": ("examples/",),
    "deploy/": ("deploy/",),
    "README.md": ("README.md",),
}

_INSTALLER = "installer input read by deploy/install/*.sh, not by Settings"
_DEPLOY_TOOL = "deploy tooling input (deploy/install, deploy/vm), not a setting"
_SLICER_STAND = "env of the headless Slicer test webserver (deploy/test/slicer)"
NOT_SETTINGS: dict[str, str] = {
    "CLARINET_DOCS": "{{CLARINET_DOCS}} token substituted by utils/agent_scaffold.py",
    "CLARINET_SSE_AUDIT_STRICT": "read from os.environ by services/events/capture.py",
    "CLARINET_PREFILL_DELAY": "proposed test hook in docs/testing/stand-e2e-plan.md; not implemented",
    "CLARINET_E2E_REQUIRE_QUARTO": "e2e harness switch (deploy/test)",
    **dict.fromkeys(
        [
            "CLARINET_ANON_SALT",
            "CLARINET_DB_NAME",
            "CLARINET_DB_PASS",
            "CLARINET_DB_USER",
            "CLARINET_RABBIT_PASS",
            "CLARINET_RABBIT_USER",
            "CLARINET_SETTINGS_OVERLAY",
        ],
        _INSTALLER,
    ),
    **dict.fromkeys(
        [
            "CLARINET_PATH_PREFIX",
            "CLARINET_PROJECT_BUNDLE",
            "CLARINET_ROLE",
            "CLARINET_RELEASE_REPO",
            "CLARINET_VM_NAME",
            "CLARINET_PROJECT_SOURCE_DIR",
        ],
        _DEPLOY_TOOL,
    ),
    **dict.fromkeys(
        [
            "CLARINET_SLICER_CALLING_AET",
            "CLARINET_SLICER_DICOM_DB",
            "CLARINET_SLICER_PACS_AET",
            "CLARINET_SLICER_PACS_HOST",
            "CLARINET_SLICER_PACS_PORT",
            "CLARINET_SLICER_SCP_PORT",
        ],
        _SLICER_STAND,
    ),
}
NOT_SETTINGS_PREFIXES: dict[str, str] = {
    "CLARINET_TEST_": "test-harness variables (tests/config.py, deploy/test)",
}

_TOML_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=")
# Not preceded by a word char: skips the __CLARINET_DATASOURCES__ OHIF sentinel.
# A letter must follow the prefix: anonymized IDs like CLARINET_42 are not env names.
_ENV_NAME = re.compile(r"(?<![A-Za-z0-9_])CLARINET_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")


def toml_top_level_keys(text: str) -> list[str]:
    """Active and commented top-level keys, up to the first active `[table]`.

    A commented `# [table]` header hides the commented keys under it until the
    next non-comment line.
    """
    keys: list[str] = []
    in_commented_table = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            break  # every later key belongs to a table
        commented = line.startswith("#")
        body = line.lstrip("#").strip() if commented else line
        if not commented:
            in_commented_table = False
        if body.startswith("["):
            in_commented_table = True
            continue
        if not in_commented_table and (m := _TOML_KEY.match(body)):
            keys.append(m.group(1))
    return keys


def unknown_env_names(text: str) -> list[str]:
    return [
        n
        for n in _ENV_NAME.findall(text)
        if n.removeprefix("CLARINET_").lower() not in FIELDS
        and n not in NOT_SETTINGS
        and not n.startswith(tuple(NOT_SETTINGS_PREFIXES))
    ]


@cache
def _tracked(patterns: tuple[str, ...]) -> list[Path]:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("needs a git checkout: the guard scans tracked files only")
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", *patterns],
        capture_output=True,
        check=True,
    ).stdout.decode()
    return sorted(REPO / p for p in out.split("\0") if p and (REPO / p).is_file())


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def test_scanners_catch_planted_names() -> None:
    assert (
        toml_top_level_keys('database_login = "x"\n# database_login = "x"\n')
        == ["database_login"] * 2
    )
    assert "database_login" not in FIELDS
    assert toml_top_level_keys("[viewers.radiant]\nenabled = true\n") == []
    assert toml_top_level_keys("# [viewers.radiant]\n# enabled = true\n\nport = 1\n") == ["port"]
    assert unknown_env_names("export CLARINET_JWT_SECRET_KEY=x") == ["CLARINET_JWT_SECRET_KEY"]
    assert unknown_env_names("{{CLARINET_DOCS}} __CLARINET_DATASOURCES__ CLARINET_PORT") == []
    assert unknown_env_names("anon id CLARINET_42 / CLARINET_12345") == []


@pytest.mark.parametrize("group", [*TOML_GROUPS, *ENV_GROUPS])
def test_every_source_group_matches_files(group: str) -> None:
    patterns = TOML_GROUPS.get(group) or ENV_GROUPS[group]
    assert _tracked(patterns), f"source group {group!r} matched no tracked file: {patterns}"


def test_settings_toml_keys_are_settings_fields() -> None:
    files = _tracked(tuple(p for ps in TOML_GROUPS.values() for p in ps))
    found = [(f, k) for f in files for k in toml_top_level_keys(_read(f) or "")]
    assert found, "no keys scanned — the scanner is broken"
    bad = sorted({f"{f.relative_to(REPO).as_posix()}: {k}" for f, k in found if k not in FIELDS})
    assert not bad, "keys that are not Settings fields:\n" + "\n".join(bad)


def test_clarinet_env_names_are_settings_fields() -> None:
    scanned = 0
    bad: set[str] = set()
    for f in _tracked(tuple(p for ps in ENV_GROUPS.values() for p in ps)):
        text = _read(f)
        if text is None:
            continue
        scanned += len(_ENV_NAME.findall(text))
        bad |= {f"{f.relative_to(REPO).as_posix()}: {n}" for n in unknown_env_names(text)}
    assert scanned, "no CLARINET_* names scanned — the scanner is broken"
    assert not bad, (
        "CLARINET_* names that are not settings (fix the name, or allowlist it with a reason):\n"
        + "\n".join(sorted(bad))
    )
