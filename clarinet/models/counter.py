"""Monotonic auto-increment counters for ID generation.

PostgreSQL uses a native ``Sequence`` (``patient_auto_id_seq``).
SQLite has no sequence support, so ``AutoIdCounter`` table serves as a fallback.
"""

from sqlalchemy import Sequence as SaSequence
from sqlmodel import Field, SQLModel

# PostgreSQL: native sequence for patient auto_id.
# Registered in SQLModel.metadata so Alembic autogenerate emits CREATE SEQUENCE.
patient_auto_id_seq = SaSequence("patient_auto_id_seq", metadata=SQLModel.metadata)


class AutoIdCounter(SQLModel, table=True):
    """Single-row monotonic counter — SQLite fallback (no native sequences).

    Not used on PostgreSQL where ``patient_auto_id_seq`` handles this.
    """

    __tablename__ = "auto_id_counter"

    name: str = Field(primary_key=True, max_length=64)
    last_value: int = Field(default=0)
