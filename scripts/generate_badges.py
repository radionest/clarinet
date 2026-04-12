#!/usr/bin/env python3
"""Generate SVG badges for the project README.

Usage:
    python scripts/generate_badges.py                    # defaults
    python scripts/generate_badges.py --output-dir /tmp  # custom output
"""

from __future__ import annotations

import argparse
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

# Approximate character widths for Verdana 11px (shields.io compatible)
_NARROW = 4.4
_CHAR_W: dict[str, float] = {
    " ": 3.3,
    "f": 5.6,
    "i": _NARROW,
    "j": _NARROW,
    "l": _NARROW,
    "r": 5.0,
    "t": 5.0,
    "1": 5.6,
    "!": _NARROW,
    ".": _NARROW,
    ",": _NARROW,
    ":": _NARROW,
    ";": _NARROW,
    "m": 9.8,
    "w": 9.2,
    "M": 8.8,
    "W": 10.6,
    "%": 9.2,
    "+": 7.6,
}
_DEFAULT_W = 7.0
_PAD = 10  # horizontal padding per side


def _text_width(s: str) -> float:
    return sum(_CHAR_W.get(c, _DEFAULT_W) for c in s) + _PAD * 2


def _make_badge(label: str, value: str, color: str) -> str:
    lw = _text_width(label)
    vw = _text_width(value)
    tw = lw + vw
    lx = lw / 2
    vx = lw + vw / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{tw:.0f}" height="20">'
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/>'
        f"</linearGradient>"
        f'<clipPath id="r"><rect width="{tw:.0f}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw:.0f}" height="20" fill="#555"/>'
        f'<rect x="{lw:.0f}" width="{vw:.0f}" height="20" fill="{color}"/>'
        f'<rect width="{tw:.0f}" height="20" fill="url(#s)"/>'
        f"</g>"
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'text-rendering="geometricPrecision" font-size="11">'
        f'<text x="{lx:.0f}" y="15" fill="#010101" fill-opacity=".3">{label}</text>'
        f'<text x="{lx:.0f}" y="14">{label}</text>'
        f'<text x="{vx:.0f}" y="15" fill="#010101" fill-opacity=".3">{value}</text>'
        f'<text x="{vx:.0f}" y="14">{value}</text>'
        f"</g></svg>"
    )


def _coverage_color(pct: float) -> str:
    if pct >= 80:
        return "#4c1"
    if pct >= 60:
        return "#dfb317"
    return "#e05d44"


def _count_meaningful_lines(root: Path, extensions: tuple[str, ...] = (".py",)) -> int:
    """Count non-blank, non-comment lines across source files."""
    total = 0
    for ext in extensions:
        for f in root.rglob(f"*{ext}"):
            # Skip build artifacts and test fixtures
            parts = f.parts
            if "build" in parts or "__pycache__" in parts or "node_modules" in parts:
                continue
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and not s.startswith("//"):
                        total += 1
            except (OSError, UnicodeDecodeError):
                continue
    return total


def _format_number(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SVG badges")
    parser.add_argument("--output-dir", type=Path, default=Path("badges"))
    parser.add_argument("--coverage-xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--src-dir", type=Path, default=Path("clarinet"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--slicer-version", default="5.6+")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Coverage
    if args.coverage_xml.exists():
        tree = ET.parse(args.coverage_xml)
        cov = round(float(tree.getroot().attrib["line-rate"]) * 100, 1)
        svg = _make_badge("coverage", f"{cov}%", _coverage_color(cov))
        (args.output_dir / "coverage.svg").write_text(svg)
        print(f"coverage: {cov}%")
    else:
        print(f"warning: {args.coverage_xml} not found, skipping coverage badge")

    # Lines of code (Python + Gleam)
    loc = _count_meaningful_lines(args.src_dir, (".py", ".gleam"))
    svg = _make_badge("code", f"{_format_number(loc)} lines", "#007ec6")
    (args.output_dir / "loc.svg").write_text(svg)
    print(f"loc: {loc}")

    # Python version
    data = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    py_ver = data["project"]["requires-python"]
    svg = _make_badge("python", py_ver, "#3776ab")
    (args.output_dir / "python.svg").write_text(svg)
    print(f"python: {py_ver}")

    # 3D Slicer
    svg = _make_badge("3D Slicer", args.slicer_version, "#3776ab")
    (args.output_dir / "slicer.svg").write_text(svg)
    print(f"slicer: {args.slicer_version}")


if __name__ == "__main__":
    main()
