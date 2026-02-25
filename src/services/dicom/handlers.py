"""Event handlers for DICOM C-STORE operations."""

from pathlib import Path
from typing import Any

from pydicom import Dataset
from pynetdicom import evt
from pynetdicom.ae import ApplicationEntity

from src.services.dicom.models import StorageMode
from src.utils.logger import logger


class StorageHandler:
    """Handler for C-STORE events with different storage modes."""

    def __init__(
        self,
        mode: StorageMode,
        output_dir: Path | None = None,
        destination_ae: ApplicationEntity | None = None,
        destination_aet: str | None = None,
        destination_host: str | None = None,
        destination_port: int | None = None,
    ):
        """Initialize storage handler.

        Args:
            mode: Storage mode (disk, memory, forward)
            output_dir: Directory for saving files (DISK mode)
            destination_ae: AE for forwarding (FORWARD mode)
            destination_aet: Destination AE title (FORWARD mode)
            destination_host: Destination host (FORWARD mode)
            destination_port: Destination port (FORWARD mode)
        """
        self.mode = mode
        self.output_dir = output_dir
        self.destination_ae = destination_ae
        self.destination_aet = destination_aet
        self.destination_host = destination_host
        self.destination_port = destination_port
        self.stored_instances: list[Dataset] = []

        # Validate configuration
        if mode == StorageMode.DISK and not output_dir:
            raise ValueError("output_dir required for DISK mode")
        if mode == StorageMode.FORWARD and not all(
            [destination_aet, destination_host, destination_port]
        ):
            raise ValueError(
                "destination_aet, destination_host, destination_port required for FORWARD mode"
            )

        # Create output directory if needed
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

    def handle_store(self, event: evt.Event) -> int:
        """Handle C-STORE request.

        Args:
            event: pynetdicom event object

        Returns:
            Status code (0x0000 for success)
        """
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta

            match self.mode:
                case StorageMode.DISK:
                    return self._store_to_disk(ds)
                case StorageMode.MEMORY:
                    return self._store_to_memory(ds)
                case StorageMode.FORWARD:
                    return self._forward_instance(ds)
                case _:
                    logger.error(f"Unknown storage mode: {self.mode}")
                    return 0xC000  # Failure

        except Exception as e:
            logger.error(f"Error handling C-STORE: {e}")
            return 0xC000  # Failure

    def _store_to_disk(self, ds: Dataset) -> int:
        """Store instance to disk.

        Args:
            ds: DICOM dataset

        Returns:
            Status code
        """
        try:
            if not self.output_dir:
                return 0xC000

            # Use SOP Instance UID as filename
            filename = f"{ds.SOPInstanceUID}.dcm"
            filepath = self.output_dir / filename

            ds.save_as(filepath, write_like_original=False)
            logger.info(f"Stored instance to {filepath}")
            return 0x0000  # Success

        except Exception as e:
            logger.error(f"Error storing to disk: {e}")
            return 0xC000  # Failure

    def _store_to_memory(self, ds: Dataset) -> int:
        """Store instance in memory.

        Args:
            ds: DICOM dataset

        Returns:
            Status code
        """
        try:
            self.stored_instances.append(ds)
            logger.debug(
                f"Stored instance in memory: {ds.SOPInstanceUID} "
                f"(total: {len(self.stored_instances)})"
            )
            return 0x0000  # Success

        except Exception as e:
            logger.error(f"Error storing to memory: {e}")
            return 0xC000  # Failure

    def _forward_instance(self, ds: Dataset) -> int:
        """Forward instance to another DICOM node.

        Args:
            ds: DICOM dataset

        Returns:
            Status code
        """
        if (
            self.destination_ae is None
            or self.destination_aet is None
            or self.destination_host is None
            or self.destination_port is None
        ):
            logger.error("Destination AE not configured")
            return 0xC000

        try:
            assoc = self.destination_ae.associate(
                self.destination_host, self.destination_port, ae_title=self.destination_aet
            )
        except Exception as e:
            logger.error(f"Error forwarding instance: {e}")
            return 0xC000

        if not assoc.is_established:
            logger.error(
                f"Failed to establish association with "
                f"{self.destination_aet}@{self.destination_host}:{self.destination_port}"
            )
            return 0xC000

        try:
            status = assoc.send_c_store(ds)
            if status and status.Status == 0x0000:
                logger.info(f"Forwarded instance {ds.SOPInstanceUID} to {self.destination_aet}")
                return 0x0000
            logger.error(
                f"Failed to forward instance {ds.SOPInstanceUID}: "
                f"status={status.Status if status else 'None'}"
            )
            return 0xC000
        finally:
            assoc.release()

    def get_stored_instances(self) -> list[Dataset]:
        """Get all instances stored in memory.

        Returns:
            List of stored datasets
        """
        return self.stored_instances

    def clear_stored_instances(self) -> None:
        """Clear all stored instances from memory."""
        self.stored_instances.clear()


def create_store_handler(
    mode: StorageMode,
    output_dir: Path | None = None,
    destination_ae: ApplicationEntity | None = None,
    destination_aet: str | None = None,
    destination_host: str | None = None,
    destination_port: int | None = None,
) -> tuple[list[tuple[Any, Any]], StorageHandler]:
    """Create C-STORE event handler.

    Args:
        mode: Storage mode
        output_dir: Directory for saving files (DISK mode)
        destination_ae: AE for forwarding (FORWARD mode)
        destination_aet: Destination AE title (FORWARD mode)
        destination_host: Destination host (FORWARD mode)
        destination_port: Destination port (FORWARD mode)

    Returns:
        Tuple of (event handlers list, storage handler instance)
    """
    handler = StorageHandler(
        mode=mode,
        output_dir=output_dir,
        destination_ae=destination_ae,
        destination_aet=destination_aet,
        destination_host=destination_host,
        destination_port=destination_port,
    )

    handlers = [(evt.EVT_C_STORE, handler.handle_store)]
    return handlers, handler
