"""Transport-agnostic event engine for SSE push.

Submodules:
- ``models`` — thin event payloads (``EntityEvent``, ``TaskProgressEvent``).
- ``bus`` — in-process fan-out with per-connection RBAC filtering.
- ``capture`` — SQLAlchemy session listeners that turn ORM mutations into events.
"""
