"""Base class for viewer adapters."""

from abc import ABC, abstractmethod


class ViewerAdapter(ABC):
    """Adapter that generates a URI for an external DICOM viewer."""

    name: str
    uri_scheme: str

    @abstractmethod
    def build_uri(
        self,
        *,
        patient_id: str,
        study_uid: str,
        series_uid: str | None = None,
    ) -> str:
        """Build a viewer URI for the given DICOM identifiers."""
        ...
