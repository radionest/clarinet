"""Viewer adapter registry — built from settings at startup."""

from __future__ import annotations

from pydantic import BaseModel

from clarinet.services.viewer.adapters import (
    OHIFAdapter,
    RadiantAdapter,
    TemplateAdapter,
    WeasisAdapter,
)
from clarinet.services.viewer.base import ViewerAdapter
from clarinet.utils.logger import logger


class ViewerConfig(BaseModel):
    """Configuration for a single viewer plugin."""

    enabled: bool = False
    pacs_name: str | None = None
    base_url: str | None = None
    uri_template: str | None = None


# Built-in adapters keyed by name → factory class
_BUILTIN_ADAPTERS: dict[str, type[OHIFAdapter] | type[RadiantAdapter] | type[WeasisAdapter]] = {
    "ohif": OHIFAdapter,
    "radiant": RadiantAdapter,
    "weasis": WeasisAdapter,
}


class ViewerRegistry:
    """Registry of viewer adapters available in the application."""

    def __init__(self) -> None:
        self._adapters: dict[str, ViewerAdapter] = {}

    def register(self, adapter: ViewerAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> ViewerAdapter | None:
        return self._adapters.get(name)

    def build_all_uris(
        self,
        *,
        patient_id: str,
        study_uid: str,
        series_uid: str | None = None,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, adapter in self._adapters.items():
            try:
                result[name] = adapter.build_uri(
                    patient_id=patient_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                )
            except Exception:
                logger.error(f"Failed to build URI for viewer '{name}'")
        return result

    @property
    def available(self) -> list[str]:
        return list(self._adapters)

    def viewer_info(self) -> list[dict[str, str]]:
        """Return metadata for each registered viewer (for frontend config)."""
        result: list[dict[str, str]] = []
        for name, adapter in self._adapters.items():
            info: dict[str, str] = {"name": name}
            if isinstance(adapter, RadiantAdapter):
                info["pacs_name"] = adapter.pacs_name
            result.append(info)
        return result


def build_viewer_registry(viewers: dict[str, ViewerConfig]) -> ViewerRegistry:
    """Create a ViewerRegistry from settings configuration."""
    registry = ViewerRegistry()
    for name, config in viewers.items():
        if not config.enabled:
            continue
        try:
            adapter: ViewerAdapter
            if name in _BUILTIN_ADAPTERS:
                adapter = _BUILTIN_ADAPTERS[name].from_config(config)
            elif config.uri_template:
                adapter = TemplateAdapter(name=name, template=config.uri_template)
            else:
                logger.warning(
                    f"Viewer '{name}' has no built-in adapter and no uri_template — skipped"
                )
                continue
        except ValueError as exc:
            logger.error(f"Invalid viewer config for '{name}': {exc}")
            continue
        registry.register(adapter)
        logger.info(f"Registered viewer adapter: {name}")
    return registry
