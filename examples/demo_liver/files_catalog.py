# Регистр файлов проекта. Используется в pipeline_flow.py и FileAccessor.
# Уровень (level) определяет папку хранения и гарантии координатной сетки.

from dataclasses import dataclass
from typing import Literal


@dataclass
class File:
    pattern: str
    multiple: bool
    level: Literal["PATIENT", "STUDY", "SERIES"]


# --- Мастер-модель (одна на пациента) ---

master_model = File(
    pattern="master_model.seg.nii",
    multiple=False,
    level="PATIENT",
)

# --- Сегментации врачей (per-user, привязаны к серии best_series) ---

# Сегментация при просмотре только текущего исследования
segmentation_single = File(
    pattern="segmentation_single_{user_id}.seg.nrrd",
    multiple=True,
    level="SERIES",
)

# Сегментация при просмотре с архивными КТ (расширенный контекст)
segmentation_with_archive = File(
    pattern="segmentation_with_archive_{user_id}.seg.nrrd",
    multiple=True,
    level="SERIES",
)

# --- Проекция мастер-модели (одна на серию) ---

master_projection = File(
    pattern="master_projection.seg.nrrd",
    multiple=False,
    level="SERIES",
)

# --- Second review (per-user, привязан к серии) ---

second_review_output = File(
    pattern="second_review_{user_id}.seg.nrrd",
    multiple=True,
    level="SERIES",
)
