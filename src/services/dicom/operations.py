"""Synchronous DICOM operations using pynetdicom."""

from typing import Any

from pydicom import Dataset
from pynetdicom import AE, StoragePresentationContexts, build_role  # type: ignore[import-not-found]
from pynetdicom.pdu_primitives import (  # type: ignore[import-not-found]
    SCP_SCU_RoleSelectionNegotiation,
)
from pynetdicom.sop_class import (  # type: ignore[import-not-found,attr-defined]
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
)

from src.exceptions.http import CONFLICT
from src.services.dicom.handlers import create_store_handler
from src.services.dicom.models import (
    AssociationConfig,
    ImageQuery,
    ImageResult,
    QueryRetrieveLevel,
    RetrieveRequest,
    RetrieveResult,
    SeriesQuery,
    SeriesResult,
    StorageConfig,
    StorageMode,
    StudyQuery,
    StudyResult,
)
from src.utils.logger import logger


def _ds_str(ds: Dataset, attr: str) -> str | None:
    """Get a DICOM attribute as a plain string."""
    val: Any = getattr(ds, attr, None)
    if val is None or val == "":
        return None
    return str(val)


def _ds_int(ds: Dataset, attr: str) -> int | None:
    """Get a DICOM attribute as an int."""
    val: Any = getattr(ds, attr, None)
    if val is None or val == "":
        return None
    return int(val)


def _set_ds_fields(ds: Dataset, fields: dict[str, Any]) -> None:
    """Set DICOM dataset fields, using empty string for None values."""
    for attr, value in fields.items():
        setattr(ds, attr, value if value is not None else "")


class DicomOperations:
    """Synchronous DICOM operations wrapper for pynetdicom."""

    def __init__(self, calling_aet: str, max_pdu: int = 16384):
        """Initialize DICOM operations.

        Args:
            calling_aet: Calling AE title
            max_pdu: Maximum PDU size (0 for unlimited)
        """
        self.calling_aet = calling_aet
        self.max_pdu = max_pdu

    def _create_ae(self) -> AE:
        """Create Application Entity for Query/Retrieve operations.

        Returns:
            Configured AE instance
        """
        ae = AE(ae_title=self.calling_aet)
        ae.maximum_pdu_size = self.max_pdu

        ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
        ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
        ae.add_requested_context(PatientRootQueryRetrieveInformationModelMove)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

        return ae

    def _create_get_ae(self) -> tuple[AE, list[SCP_SCU_RoleSelectionNegotiation]]:
        """Create Application Entity for C-GET with SCP/SCU role negotiation.

        C-GET requires the peer to send C-STORE sub-operations back to us,
        so we must negotiate SCP role for each storage presentation context.

        Returns:
            Tuple of (configured AE, role selection items for ext_neg)
        """
        ae = AE(ae_title=self.calling_aet)
        ae.maximum_pdu_size = self.max_pdu

        ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)

        # C-GET requires client to accept incoming C-STORE sub-operations
        # Limit to 126 (128 - 2 for GET contexts) to stay within DICOM max
        storage_contexts = StoragePresentationContexts[:126]
        roles: list[SCP_SCU_RoleSelectionNegotiation] = []
        for cx in storage_contexts:
            if cx.abstract_syntax is not None:
                ae.add_requested_context(cx.abstract_syntax)
                roles.append(build_role(cx.abstract_syntax, scp_role=True))

        return ae, roles

    def _build_study_query_dataset(self, query: StudyQuery) -> Dataset:
        """Build DICOM dataset for study-level C-FIND.

        Args:
            query: Study query parameters

        Returns:
            DICOM dataset for query
        """
        ds = Dataset()
        ds.QueryRetrieveLevel = QueryRetrieveLevel.STUDY.value

        _set_ds_fields(
            ds,
            {
                "PatientID": query.patient_id,
                "PatientName": query.patient_name,
                "StudyInstanceUID": query.study_instance_uid,
                "StudyDate": query.study_date,
                "StudyDescription": query.study_description,
                "AccessionNumber": query.accession_number,
                "ModalitiesInStudy": query.modality,
                "StudyTime": None,
                "NumberOfStudyRelatedSeries": None,
                "NumberOfStudyRelatedInstances": None,
            },
        )

        return ds

    def _build_series_query_dataset(self, query: SeriesQuery) -> Dataset:
        """Build DICOM dataset for series-level C-FIND.

        Args:
            query: Series query parameters

        Returns:
            DICOM dataset for query
        """
        ds = Dataset()
        ds.QueryRetrieveLevel = QueryRetrieveLevel.SERIES.value
        ds.StudyInstanceUID = query.study_instance_uid

        _set_ds_fields(
            ds,
            {
                "SeriesInstanceUID": query.series_instance_uid,
                "SeriesNumber": query.series_number,
                "Modality": query.modality,
                "SeriesDescription": query.series_description,
                "NumberOfSeriesRelatedInstances": None,
            },
        )

        return ds

    def _build_image_query_dataset(self, query: ImageQuery) -> Dataset:
        """Build DICOM dataset for image-level C-FIND.

        Args:
            query: Image query parameters

        Returns:
            DICOM dataset for query
        """
        ds = Dataset()
        ds.QueryRetrieveLevel = QueryRetrieveLevel.IMAGE.value
        ds.StudyInstanceUID = query.study_instance_uid
        ds.SeriesInstanceUID = query.series_instance_uid

        _set_ds_fields(
            ds,
            {
                "SOPInstanceUID": query.sop_instance_uid,
                "InstanceNumber": query.instance_number,
                "SOPClassUID": None,
            },
        )
        ds.Rows = None
        ds.Columns = None

        return ds

    def _build_retrieve_dataset(self, request: RetrieveRequest) -> Dataset:
        """Build DICOM dataset for C-GET or C-MOVE.

        Args:
            request: Retrieve request parameters

        Returns:
            DICOM dataset for retrieve
        """
        ds = Dataset()
        data = request.to_dict()
        for key, value in data.items():
            setattr(ds, key, value)
        return ds

    def find_studies(self, config: AssociationConfig, query: StudyQuery) -> list[StudyResult]:
        """Execute C-FIND for studies.

        Args:
            config: Association configuration
            query: Study query parameters

        Returns:
            List of study results

        Raises:
            CONFLICT: If association fails
        """
        ae = self._create_ae()
        ds = self._build_study_query_dataset(query)

        assoc = ae.associate(
            config.peer_host,
            config.peer_port,
            ae_title=config.called_aet,
        )

        if not assoc.is_established:
            logger.error(f"Failed to establish association with {config.called_aet}")
            raise CONFLICT.with_context("Failed to establish DICOM association")

        try:
            results: list[StudyResult] = []
            responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)

            for status, identifier in responses:
                if not status:
                    continue

                # Pending status means we have data
                match status.Status:
                    case 0xFF00 | 0xFF01:
                        if identifier:
                            result = self._parse_study_result(identifier)
                            results.append(result)
                    case 0x0000:
                        logger.info(f"C-FIND completed successfully, found {len(results)} studies")
                    case _:
                        logger.warning(f"C-FIND warning status: 0x{status.Status:04x}")

            return results

        finally:
            assoc.release()

    def find_series(self, config: AssociationConfig, query: SeriesQuery) -> list[SeriesResult]:
        """Execute C-FIND for series.

        Args:
            config: Association configuration
            query: Series query parameters

        Returns:
            List of series results

        Raises:
            CONFLICT: If association fails
        """
        ae = self._create_ae()
        ds = self._build_series_query_dataset(query)

        assoc = ae.associate(
            config.peer_host,
            config.peer_port,
            ae_title=config.called_aet,
        )

        if not assoc.is_established:
            logger.error(f"Failed to establish association with {config.called_aet}")
            raise CONFLICT.with_context("Failed to establish DICOM association")

        try:
            results: list[SeriesResult] = []
            responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)

            for status, identifier in responses:
                if not status:
                    continue

                match status.Status:
                    case 0xFF00 | 0xFF01:
                        if identifier:
                            result = self._parse_series_result(identifier)
                            results.append(result)
                    case 0x0000:
                        logger.info(f"C-FIND completed successfully, found {len(results)} series")

            return results

        finally:
            assoc.release()

    def find_images(self, config: AssociationConfig, query: ImageQuery) -> list[ImageResult]:
        """Execute C-FIND for images.

        Args:
            config: Association configuration
            query: Image query parameters

        Returns:
            List of image results

        Raises:
            CONFLICT: If association fails
        """
        ae = self._create_ae()
        ds = self._build_image_query_dataset(query)

        assoc = ae.associate(
            config.peer_host,
            config.peer_port,
            ae_title=config.called_aet,
        )

        if not assoc.is_established:
            logger.error(f"Failed to establish association with {config.called_aet}")
            raise CONFLICT.with_context("Failed to establish DICOM association")

        try:
            results: list[ImageResult] = []
            responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)

            for status, identifier in responses:
                if not status:
                    continue

                match status.Status:
                    case 0xFF00 | 0xFF01:
                        if identifier:
                            result = self._parse_image_result(identifier)
                            results.append(result)
                    case 0x0000:
                        logger.info(f"C-FIND completed successfully, found {len(results)} images")

            return results

        finally:
            assoc.release()

    def get_study(
        self, config: AssociationConfig, request: RetrieveRequest, storage: StorageConfig
    ) -> RetrieveResult:
        """Execute C-GET to retrieve study.

        Args:
            config: Association configuration
            request: Retrieve request
            storage: Storage configuration

        Returns:
            Retrieve result

        Raises:
            CONFLICT: If association fails
        """
        ae, roles = self._create_get_ae()
        ds = self._build_retrieve_dataset(request)

        # Create storage handler
        handlers, storage_handler = create_store_handler(
            mode=storage.mode,
            output_dir=storage.output_dir,
            destination_aet=storage.destination_aet,
            destination_host=storage.destination_host,
            destination_port=storage.destination_port,
        )

        assoc = ae.associate(
            config.peer_host,
            config.peer_port,
            ae_title=config.called_aet,
            evt_handlers=handlers,  # type: ignore[arg-type]
            ext_neg=roles,  # type: ignore[arg-type]
        )

        if not assoc.is_established:
            logger.error(f"Failed to establish association with {config.called_aet}")
            raise CONFLICT.with_context("Failed to establish DICOM association")

        try:
            result = RetrieveResult(status="pending")
            responses = assoc.send_c_get(ds, PatientRootQueryRetrieveInformationModelGet)

            for status, _identifier in responses:
                if not status:
                    continue

                # Update counters from status
                if hasattr(status, "NumberOfRemainingSuboperations"):
                    result.num_remaining = status.NumberOfRemainingSuboperations or 0
                if hasattr(status, "NumberOfCompletedSuboperations"):
                    result.num_completed = status.NumberOfCompletedSuboperations or 0
                if hasattr(status, "NumberOfFailedSuboperations"):
                    result.num_failed = status.NumberOfFailedSuboperations or 0
                if hasattr(status, "NumberOfWarningSuboperations"):
                    result.num_warning = status.NumberOfWarningSuboperations or 0

                match status.Status:
                    case 0x0000:
                        result.status = "success"
                        logger.info(
                            f"C-GET completed: {result.num_completed} completed, "
                            f"{result.num_failed} failed"
                        )
                    case 0xFF00:
                        result.status = "pending"
                    case _:
                        result.status = f"warning_0x{status.Status:04x}"
                        logger.warning(f"C-GET status: 0x{status.Status:04x}")

            # Get stored instances if in memory mode
            if storage.mode == StorageMode.MEMORY:
                result.instances = storage_handler.get_stored_instances()

            return result

        finally:
            assoc.release()

    def move_study(
        self, config: AssociationConfig, request: RetrieveRequest, destination_aet: str
    ) -> RetrieveResult:
        """Execute C-MOVE to move study to another node.

        Args:
            config: Association configuration
            request: Retrieve request
            destination_aet: Destination AE title

        Returns:
            Retrieve result

        Raises:
            CONFLICT: If association fails
        """
        ae = self._create_ae()
        ds = self._build_retrieve_dataset(request)

        assoc = ae.associate(
            config.peer_host,
            config.peer_port,
            ae_title=config.called_aet,
        )

        if not assoc.is_established:
            logger.error(f"Failed to establish association with {config.called_aet}")
            raise CONFLICT.with_context("Failed to establish DICOM association")

        try:
            result = RetrieveResult(status="pending")
            responses = assoc.send_c_move(
                ds, destination_aet, PatientRootQueryRetrieveInformationModelMove
            )

            for status, _identifier in responses:
                if not status:
                    continue

                # Update counters
                if hasattr(status, "NumberOfRemainingSuboperations"):
                    result.num_remaining = status.NumberOfRemainingSuboperations or 0
                if hasattr(status, "NumberOfCompletedSuboperations"):
                    result.num_completed = status.NumberOfCompletedSuboperations or 0
                if hasattr(status, "NumberOfFailedSuboperations"):
                    result.num_failed = status.NumberOfFailedSuboperations or 0
                if hasattr(status, "NumberOfWarningSuboperations"):
                    result.num_warning = status.NumberOfWarningSuboperations or 0

                match status.Status:
                    case 0x0000:
                        result.status = "success"
                        logger.info(
                            f"C-MOVE completed: {result.num_completed} completed, "
                            f"{result.num_failed} failed, destination: {destination_aet}"
                        )
                    case 0xFF00:
                        result.status = "pending"
                    case _:
                        result.status = f"warning_0x{status.Status:04x}"
                        logger.warning(f"C-MOVE status: 0x{status.Status:04x}")

            return result

        finally:
            assoc.release()

    def _parse_study_result(self, ds: Dataset) -> StudyResult:
        """Parse DICOM dataset to StudyResult.

        Args:
            ds: DICOM dataset

        Returns:
            Parsed study result
        """
        return StudyResult(
            patient_id=_ds_str(ds, "PatientID"),
            patient_name=_ds_str(ds, "PatientName"),
            study_instance_uid=str(ds.StudyInstanceUID),
            study_date=_ds_str(ds, "StudyDate"),
            study_time=_ds_str(ds, "StudyTime"),
            study_description=_ds_str(ds, "StudyDescription"),
            accession_number=_ds_str(ds, "AccessionNumber"),
            modalities_in_study=_ds_str(ds, "ModalitiesInStudy"),
            number_of_study_related_series=_ds_int(ds, "NumberOfStudyRelatedSeries"),
            number_of_study_related_instances=_ds_int(ds, "NumberOfStudyRelatedInstances"),
        )

    def _parse_series_result(self, ds: Dataset) -> SeriesResult:
        """Parse DICOM dataset to SeriesResult.

        Args:
            ds: DICOM dataset

        Returns:
            Parsed series result
        """
        return SeriesResult(
            study_instance_uid=str(ds.StudyInstanceUID),
            series_instance_uid=str(ds.SeriesInstanceUID),
            series_number=_ds_int(ds, "SeriesNumber"),
            modality=_ds_str(ds, "Modality"),
            series_description=_ds_str(ds, "SeriesDescription"),
            number_of_series_related_instances=_ds_int(ds, "NumberOfSeriesRelatedInstances"),
        )

    def _parse_image_result(self, ds: Dataset) -> ImageResult:
        """Parse DICOM dataset to ImageResult.

        Args:
            ds: DICOM dataset

        Returns:
            Parsed image result
        """
        return ImageResult(
            study_instance_uid=str(ds.StudyInstanceUID),
            series_instance_uid=str(ds.SeriesInstanceUID),
            sop_instance_uid=str(ds.SOPInstanceUID),
            sop_class_uid=_ds_str(ds, "SOPClassUID"),
            instance_number=_ds_int(ds, "InstanceNumber"),
            rows=_ds_int(ds, "Rows"),
            columns=_ds_int(ds, "Columns"),
        )
