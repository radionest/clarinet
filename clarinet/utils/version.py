"""The installed clarinet version, shared by the scaffolders and the pipeline fingerprint."""

from importlib.metadata import PackageNotFoundError, version


def clarinet_version() -> str:
    """Installed clarinet version, or ``"unknown"`` in a source tree."""
    try:
        return version("clarinet")
    except PackageNotFoundError:  # pragma: no cover - source-tree fallback
        return "unknown"
