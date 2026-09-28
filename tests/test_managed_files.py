"""The one rule every scaffolder uses to decide whether a file is clarinet's."""

from pathlib import Path

import pytest

from clarinet.utils.managed_files import (
    clarinet_version,
    is_managed,
    managed_header,
    strip_header,
    with_header,
)

MD = managed_header("<!--", "clarinet agent update")
HASH = managed_header("#", "clarinet quality update")
FRONT = "---\npaths:\n  - 'plan/**'\n---\n"


def test_header_styles_name_version_and_command() -> None:
    assert (
        f"# managed by clarinet v{clarinet_version()} — do not edit; run 'clarinet quality update' to refresh\n"
        == HASH
    )
    assert MD.startswith("<!-- managed by clarinet v") and MD.endswith(" -->\n")
    assert "clarinet agent update" in MD


def test_header_goes_after_frontmatter_else_first() -> None:
    assert with_header(FRONT + "body\n", MD) == FRONT + MD + "body\n"
    assert with_header("body\n", HASH) == HASH + "body\n"


@pytest.mark.parametrize(
    "text",
    [
        "body\n",
        FRONT + "body\n",
        "<!-- managed by clarinet v0.1 — looks like a header -->\nbody\n",
        FRONT + "# managed by clarinet v0.1 in the body\nmore\n",
        "",
    ],
)
@pytest.mark.parametrize("header", [MD, HASH])
def test_strip_header_is_the_exact_inverse(text: str, header: str) -> None:
    assert strip_header(with_header(text, header)) == text


def test_strip_header_without_header_is_a_noop() -> None:
    assert (
        strip_header("plain\n# managed by clarinet later\n")
        == "plain\n# managed by clarinet later\n"
    )


def test_is_managed_both_styles_and_after_frontmatter(tmp_path: Path) -> None:
    for name, text in {
        "a.md": with_header(FRONT + "x\n", MD),
        "b.md": with_header("x\n", MD),
        "Makefile": with_header("all:\n", HASH),
    }.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        assert is_managed(tmp_path / name), name


def test_marker_later_in_body_is_not_managed(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    p.write_text("# Title\n<!-- managed by clarinet v1 -->\n", encoding="utf-8")
    assert not is_managed(p)


def test_unreadable_or_undecodable_is_not_managed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe\x00managed")
    assert is_managed(bad) is False
    assert is_managed(tmp_path / "missing.md") is False
    assert is_managed(tmp_path) is False  # a directory


def test_both_scaffolders_write_files_the_one_rule_calls_managed(tmp_path: Path) -> None:
    from clarinet.utils.agent_scaffold import scaffold_agent_docs
    from clarinet.utils.quality_scaffold import scaffold_quality_config

    scaffold_quality_config(project_dir=tmp_path, mode="init")
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")

    assert is_managed(tmp_path / "Makefile")
    assert is_managed(dest / "definitions.md")  # header after frontmatter
