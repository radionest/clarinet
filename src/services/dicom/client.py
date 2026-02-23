"""Async DICOM client for query-retrieve operations."""

import asyncio
from pathlib import Path

from src.services.dicom.models import (
    AssociationConfig,
    DicomNode,
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
from src.services.dicom.operations import DicomOperations
from src.utils.logger import logger


class DicomClient:
    """Async DICOM client for Query/Retrieve operations.

    This client provides async interface to DICOM operations while using
    synchronous pynetdicom library under the hood via asyncio.to_thread().
    """

    def __init__(
        self,
        calling_aet: str,
        max_pdu: int = 16384,
    ):
        """Initialize DICOM client.

        Args:
            calling_aet: Calling AE title
            max_pdu: Maximum PDU size (0 for unlimited)
        """
        self.calling_aet = calling_aet
        self.max_pdu = max_pdu
        self._operations = DicomOperations(calling_aet=calling_aet, max_pdu=max_pdu)

    def _create_association_config(
        self,
        called_aet: str,
        peer_host: str,
        peer_port: int,
        timeout: float = 30.0,
    ) -> AssociationConfig:
        """Create association configuration.

        Args:
            called_aet: Called AE title
            peer_host: Peer host address
            peer_port: Peer port number
            timeout: Association timeout

        Returns:
            Association configuration
        """
        return AssociationConfig(
            calling_aet=self.calling_aet,
            called_aet=called_aet,
            peer_host=peer_host,
            peer_port=peer_port,
            max_pdu=self.max_pdu,
            timeout=timeout,
        )

    async def find_studies(
        self,
        query: StudyQuery,
        peer: DicomNode,
        timeout: float = 30.0,
    ) -> list[StudyResult]:
        """Find studies matching query criteria.

        Args:
            query: Study query parameters
            peer: DICOM peer node
            timeout: Operation timeout

        Returns:
            List of matching studies

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Searching studies on {peer.aet}@{peer.host}:{peer.port}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        results = await asyncio.to_thread(
            self._operations.find_studies,
            config,
            query,
        )

        logger.info(f"Found {len(results)} studies")
        return results

    async def find_series(
        self,
        query: SeriesQuery,
        peer: DicomNode,
        timeout: float = 30.0,
    ) -> list[SeriesResult]:
        """Find series matching query criteria.

        Args:
            query: Series query parameters
            peer: DICOM peer node
            timeout: Operation timeout

        Returns:
            List of matching series

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Searching series on {peer.aet}@{peer.host}:{peer.port}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        results = await asyncio.to_thread(
            self._operations.find_series,
            config,
            query,
        )

        logger.info(f"Found {len(results)} series")
        return results

    async def find_images(
        self,
        query: ImageQuery,
        peer: DicomNode,
        timeout: float = 30.0,
    ) -> list[ImageResult]:
        """Find images matching query criteria.

        Args:
            query: Image query parameters
            peer: DICOM peer node
            timeout: Operation timeout

        Returns:
            List of matching images

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Searching images on {peer.aet}@{peer.host}:{peer.port}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        results = await asyncio.to_thread(
            self._operations.find_images,
            config,
            query,
        )

        logger.info(f"Found {len(results)} images")
        return results

    async def get_study(
        self,
        study_uid: str,
        peer: DicomNode,
        output_dir: Path,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        """Retrieve study and save to disk.

        Args:
            study_uid: Study instance UID
            peer: DICOM peer node
            output_dir: Directory to save DICOM files
            patient_id: Optional patient ID for query
            timeout: Operation timeout

        Returns:
            Retrieve result with statistics

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Retrieving study {study_uid} to {output_dir}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        request = RetrieveRequest(
            level=QueryRetrieveLevel.STUDY,
            study_instance_uid=study_uid,
            patient_id=patient_id,
        )

        storage = StorageConfig(
            mode=StorageMode.DISK,
            output_dir=output_dir,
        )

        result = await asyncio.to_thread(
            self._operations.get_study,
            config,
            request,
            storage,
        )

        logger.info(
            f"Retrieved study: {result.num_completed} completed, {result.num_failed} failed"
        )
        return result

    async def get_series(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        output_dir: Path,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        """Retrieve series and save to disk.

        Args:
            study_uid: Study instance UID
            series_uid: Series instance UID
            peer: DICOM peer node
            output_dir: Directory to save DICOM files
            patient_id: Optional patient ID for query
            timeout: Operation timeout

        Returns:
            Retrieve result with statistics

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Retrieving series {series_uid} to {output_dir}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        request = RetrieveRequest(
            level=QueryRetrieveLevel.SERIES,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            patient_id=patient_id,
        )

        storage = StorageConfig(
            mode=StorageMode.DISK,
            output_dir=output_dir,
        )

        result = await asyncio.to_thread(
            self._operations.get_study,
            config,
            request,
            storage,
        )

        logger.info(
            f"Retrieved series: {result.num_completed} completed, {result.num_failed} failed"
        )
        return result

    async def get_study_to_memory(
        self,
        study_uid: str,
        peer: DicomNode,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        """Retrieve study to memory.

        Args:
            study_uid: Study instance UID
            peer: DICOM peer node
            patient_id: Optional patient ID for query
            timeout: Operation timeout

        Returns:
            Retrieve result with instances in memory

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Retrieving study {study_uid} to memory")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        request = RetrieveRequest(
            level=QueryRetrieveLevel.STUDY,
            study_instance_uid=study_uid,
            patient_id=patient_id,
        )

        storage = StorageConfig(mode=StorageMode.MEMORY)

        result = await asyncio.to_thread(
            self._operations.get_study,
            config,
            request,
            storage,
        )

        logger.info(
            f"Retrieved study to memory: {result.num_completed} instances, "
            f"{result.num_failed} failed"
        )
        return result

    async def move_study(
        self,
        study_uid: str,
        peer: DicomNode,
        destination_aet: str,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        """Move study to another DICOM node.

        Args:
            study_uid: Study instance UID
            peer: Source DICOM peer node
            destination_aet: Destination AE title
            patient_id: Optional patient ID for query
            timeout: Operation timeout

        Returns:
            Move result with statistics

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Moving study {study_uid} to {destination_aet}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        request = RetrieveRequest(
            level=QueryRetrieveLevel.STUDY,
            study_instance_uid=study_uid,
            patient_id=patient_id,
        )

        result = await asyncio.to_thread(
            self._operations.move_study,
            config,
            request,
            destination_aet,
        )

        logger.info(
            f"Moved study: {result.num_completed} completed, {result.num_failed} failed"
        )
        return result

    async def move_series(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        destination_aet: str,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        """Move series to another DICOM node.

        Args:
            study_uid: Study instance UID
            series_uid: Series instance UID
            peer: Source DICOM peer node
            destination_aet: Destination AE title
            patient_id: Optional patient ID for query
            timeout: Operation timeout

        Returns:
            Move result with statistics

        Raises:
            CONFLICT: If association fails
        """
        logger.info(f"Moving series {series_uid} to {destination_aet}")

        config = self._create_association_config(
            called_aet=peer.aet,
            peer_host=peer.host,
            peer_port=peer.port,
            timeout=timeout,
        )

        request = RetrieveRequest(
            level=QueryRetrieveLevel.SERIES,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            patient_id=patient_id,
        )

        result = await asyncio.to_thread(
            self._operations.move_study,
            config,
            request,
            destination_aet,
        )

        logger.info(
            f"Moved series: {result.num_completed} completed, {result.num_failed} failed"
        )
        return result
