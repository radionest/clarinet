#!/usr/bin/env python3
"""Parse multi-VM topology TOML files and read/write the runtime lock file.

Host-side helper for vm.sh's topology-* commands. Stdlib only (tomllib + json)
so it runs on the operator's machine with no extra dependencies, and is
importable from pytest.

Argv contract (each subcommand prints one value per line, machine-parseable):

    topology.py file <name|path>               resolve to a topology .toml; exit 2 if missing
    topology.py vms <file>                      vm keys, one per line, ordered pacs, stand, worker
    topology.py get <file> <vm> <key> [default] vm value (ram/vcpus/disk_size fall back to [defaults];
                                                queues space-joined)
    topology.py project <file> <key> [default]  [project] value
    topology.py lock-write <lockfile> <json>    validate <json> and write it to <lockfile>
    topology.py lock-get <lockfile> <vm> <field> vms.<vm>.<field>; '' if absent
    topology.py key-of-role <file> <role>       vm key for a role (first match); '' if none
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

# Topologies live next to vm.sh, not next to this script: deploy/vm/topologies/.
TOPOLOGIES_DIR = Path(__file__).resolve().parent.parent / "vm" / "topologies"

# Create/wire order matters: PACS first (so the stand can be told its IP),
# worker last (so it can be told stand + pacs IPs).
ROLE_RANK = {"pacs": 0, "stand": 1, "worker": 2}

# Per-VM keys that fall back to the [defaults] table when omitted on a [vm.*].
DEFAULTABLE = ("ram", "vcpus", "disk_size")


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def resolve_file(name_or_path: str) -> Path:
    """Resolve a topology argument to an existing .toml path.

    Accepts an absolute/relative path to an existing file, or a bare name that
    maps to deploy/vm/topologies/<name>.toml. Exits 2 if nothing matches.
    """
    direct = Path(name_or_path)
    if direct.is_file():
        return direct.resolve()
    candidate = TOPOLOGIES_DIR / f"{name_or_path}.toml"
    if candidate.is_file():
        return candidate.resolve()
    _die(f"topology not found: {name_or_path} (looked for {candidate})", 2)
    raise AssertionError  # unreachable, keeps type checkers happy


def _load(path: str) -> dict[str, Any]:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def cmd_vms(file: str) -> None:
    data = _load(file)
    vms = data.get("vm", {})
    ordered = sorted(
        vms.items(),
        key=lambda kv: (ROLE_RANK.get(kv[1].get("role", ""), 99), kv[0]),
    )
    for name, _ in ordered:
        print(name)


def cmd_get(file: str, vm: str, key: str, default: str | None) -> None:
    data = _load(file)
    vm_data = data.get("vm", {}).get(vm, {})
    if key in vm_data:
        print(_format_value(vm_data[key]))
        return
    if key in DEFAULTABLE and key in data.get("defaults", {}):
        print(_format_value(data["defaults"][key]))
        return
    print(default if default is not None else "")


def cmd_project(file: str, key: str, default: str | None) -> None:
    data = _load(file)
    project = data.get("project", {})
    if key in project:
        print(_format_value(project[key]))
        return
    print(default if default is not None else "")


def cmd_lock_write(lockfile: str, payload: str) -> None:
    parsed = json.loads(payload)  # validate; raises on malformed input
    Path(lockfile).write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")


def cmd_lock_get(lockfile: str, vm: str, field: str) -> None:
    try:
        data = json.loads(Path(lockfile).read_text(encoding="utf-8"))
        print(data["vms"][vm][field])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        # TypeError covers a well-formed-JSON-but-wrong-shape lock (e.g. a
        # top-level array) — degrade to '' like a missing key rather than crash.
        print("")


def cmd_key_of_role(file: str, role: str) -> None:
    """Print the first vm key with the given role (deterministic order).

    Wire/smoke address VMs by role; this maps a role back to its [vm.<key>]
    table name so the lock (keyed by vm name) and the TOML can be queried for an
    arbitrary key. Assumes one VM per role — the first match wins.
    """
    data = _load(file)
    vms = data.get("vm", {})
    ordered = sorted(
        vms.items(),
        key=lambda kv: (ROLE_RANK.get(kv[1].get("role", ""), 99), kv[0]),
    )
    for name, vm_data in ordered:
        if vm_data.get("role") == role:
            print(name)
            return
    print("")


def main(argv: list[str]) -> None:
    if not argv:
        _die(__doc__ or "usage: topology.py <subcommand> ...", 2)
    cmd, rest = argv[0], argv[1:]
    if cmd == "file":
        print(resolve_file(rest[0]))
    elif cmd == "vms":
        cmd_vms(rest[0])
    elif cmd == "get":
        cmd_get(rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else None)
    elif cmd == "project":
        cmd_project(rest[0], rest[1], rest[2] if len(rest) > 2 else None)
    elif cmd == "lock-write":
        cmd_lock_write(rest[0], rest[1])
    elif cmd == "lock-get":
        cmd_lock_get(rest[0], rest[1], rest[2])
    elif cmd == "key-of-role":
        cmd_key_of_role(rest[0], rest[1])
    else:
        _die(f"unknown subcommand: {cmd}", 2)


if __name__ == "__main__":
    main(sys.argv[1:])
