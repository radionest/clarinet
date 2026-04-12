"""Built-in viewer adapters."""
# ruff: noqa: ARG002 — adapter interface requires all args even when unused

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from clarinet.services.viewer.base import ViewerAdapter

if TYPE_CHECKING:
    from clarinet.services.viewer.registry import ViewerConfig


class RadiantAdapter(ViewerAdapter):
    """RadiAnt DICOM Viewer (radiant:// URI scheme).

    RadiAnt queries PACS by Study UID via the configured AE title.
    """

    name = "radiant"
    uri_scheme = "radiant://"

    def __init__(self, *, pacs_name: str = "ORTHANC") -> None:
        self.pacs_name = pacs_name

    @classmethod
    def from_config(cls, config: ViewerConfig) -> RadiantAdapter:
        return cls(pacs_name=config.pacs_name or "ORTHANC")

    def build_uri(
        self,
        *,
        patient_id: str,
        study_uid: str,
        series_uid: str | None = None,
    ) -> str:
        # 0020000D = StudyInstanceUID DICOM tag
        query = urlencode(
            [
                ("n", "paet"),
                ("v", self.pacs_name),
                ("n", "pstv"),
                ("v", "0020000D"),
                ("v", study_uid),
            ]
        )
        return f"radiant://?{query}"


class WeasisAdapter(ViewerAdapter):
    """Weasis DICOM Viewer (weasis:// URI scheme).

    Uses weasis-pacs-connector to generate a launch manifest.
    ``base_url`` points to the connector (e.g. ``http://host:8080/weasis-pacs-connector``).
    """

    name = "weasis"
    uri_scheme = "weasis://"

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_config(cls, config: ViewerConfig) -> WeasisAdapter:
        if not config.base_url:
            msg = "Weasis viewer requires 'base_url' in configuration"
            raise ValueError(msg)
        return cls(base_url=config.base_url)

    def build_uri(
        self,
        *,
        patient_id: str,
        study_uid: str,
        series_uid: str | None = None,
    ) -> str:
        params: dict[str, str] = {"studyUID": study_uid, "patientID": patient_id}
        if series_uid:
            params["seriesUID"] = series_uid
        return f"{self.base_url}/weasis?{urlencode(params)}"


class TemplateAdapter(ViewerAdapter):
    """Generic adapter using a URI template string.

    Supports placeholders: ``{patient_id}``, ``{study_uid}``, ``{series_uid}``.
    """

    uri_scheme = "custom"

    def __init__(self, *, name: str, template: str) -> None:
        self.name = name  # type: ignore[assignment]
        self.template = template

    def build_uri(
        self,
        *,
        patient_id: str,
        study_uid: str,
        series_uid: str | None = None,
    ) -> str:
        return self.template.format(
            patient_id=patient_id,
            study_uid=study_uid,
            series_uid=series_uid or "",
        )
