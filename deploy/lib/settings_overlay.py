#!/usr/bin/env python3
"""Upsert flat scalar keys into a Clarinet settings.custom.toml overlay.

Runs on the target VM, usually shipped over stdin:

    ssh clarinet@<ip> "sudo python3 - /opt/clarinet/settings.custom.toml \\
        rabbitmq_host=1.2.3.4 api_verify_ssl=false" < settings_overlay.py

Usage: settings_overlay.py <target_file> KEY=VALUE [KEY=VALUE ...]

Reads <target_file> via tomllib if it exists, merges the given keys (last write
wins), and rewrites a flat TOML whose first line is the stand-overlay header
that generate-settings.sh keys its stale-overlay cleanup on. Output is
deterministic (keys sorted) so re-running with the same kv pairs is a no-op
diff. VALUE is coerced: true/false -> bool, integer-looking -> int, else str.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

# Must match generate-settings.sh's overlay header (its stale-overlay cleanup
# greps head -1 for "Clarinet stand overrides").
HEADER = "# Clarinet stand overrides (layered over the project's settings.toml)"

# Keys whose VALUE must never be type-coerced — they are secrets/credentials
# that may legitimately be all-digit or look like "true"/"false" (admin_password
# is `openssl rand -hex 8`, so all-digit ~1 in 1845). Coercing them corrupts the
# credential: a TOML int drops leading zeros and changes the type, and pydantic
# then rejects int/bool for these str fields, crashing the worker on startup.
NEVER_COERCE = frozenset(
    {
        "admin_password",
        "secret_key",
        "rabbitmq_password",
        "rabbitmq_login",
        "database_password",
        "database_username",
        "anon_uid_salt",
    }
)

# Integer literal with no leading zeros or underscores, so zero-padded codes like
# "007" stay strings while "4242" still coerces to int.
_INT_RE = re.compile(r"-?(0|[1-9][0-9]*)$")


def coerce(key: str, value: str) -> object:
    if key in NEVER_COERCE:
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    if _INT_RE.match(value):
        return int(value)
    return value


def format_value(value: object) -> str:
    if isinstance(value, bool):  # bool before int — bool is a subclass of int
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: settings_overlay.py <target_file> KEY=VALUE ...", file=sys.stderr)
        return 2

    target = Path(argv[0])
    pairs = argv[1:]

    settings: dict[str, object] = {}
    if target.is_file():
        with target.open("rb") as fh:
            settings = tomllib.load(fh)

    for pair in pairs:
        if "=" not in pair:
            print(f"settings_overlay.py: malformed pair (need KEY=VALUE): {pair}", file=sys.stderr)
            return 2
        key, value = pair.split("=", 1)
        settings[key] = coerce(key, value)

    lines = [HEADER]
    lines += [f"{key} = {format_value(settings[key])}" for key in sorted(settings)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
