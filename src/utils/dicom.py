"""DICOM parsing helpers."""

from datetime import UTC, date, datetime

from src.exceptions.domain import ValidationError
from src.utils.logger import logger


def parse_dicom_date(date_str: str | None) -> date:
    """Parse a DICOM date string (YYYYMMDD) to a Python date.

    Args:
        date_str: DICOM-format date string, or None

    Returns:
        Parsed date, or today's date (UTC) if input is None or unparseable
    """
    if date_str:
        try:
            return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=UTC).date()
        except ValueError:
            logger.warning(f"Invalid DICOM date '{date_str}', using today's date")
    return datetime.now(tz=UTC).date()


def parse_frame_numbers(frames: str) -> list[int]:
    """Parse comma-separated 1-based frame numbers.

    Args:
        frames: Comma-separated frame numbers (e.g. "1" or "1,2,3")

    Returns:
        List of integer frame numbers

    Raises:
        ValidationError: If values are not integers or result is empty
    """
    try:
        frame_numbers = [int(f.strip()) for f in frames.split(",") if f.strip()]
    except ValueError:
        raise ValidationError(f"Invalid frame numbers: {frames}") from None
    if not frame_numbers:
        raise ValidationError("No frame numbers specified")
    return frame_numbers
