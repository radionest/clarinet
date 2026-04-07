---
paths:
  - "plan/utils/**"
---

# Раздел `plan/utils/`

Проектно-специфичные helper-модули, используемые pipeline-тасками, валидаторами, скриптами. Этот раздел не управляется фреймворком напрямую — его наполнение и структура полностью на ваше усмотрение, но конвенции ниже помогают сохранять чистоту.

## Что класть сюда

- **Shared-константы**: label-карты сегментов, имена категорий, пороги классификации.
  ```python
  SEG_LABELS: dict[str, int] = {"mts": 1, "unclear": 2, "benign": 3}
  ```
- **Файловые I/O-обёртки**: чтение/запись `.seg.nrrd` с segment metadata, чтение DICOM-метаданных, парсинг отчётов.
- **Image-обработка**, не относящаяся к одной конкретной задаче: label converters, connected components, метрики сегментаций (Dice, Hausdorff), морфологические операции.
- **Чистые helper-функции**, повторяющиеся между несколькими pipeline-тасками или валидаторами.

## Что НЕ класть

- **API-вызовы к clarinet**. Это работа pipeline-таски через `ctx.client` — не helper-а. Helper не должен знать про БД и HTTP.
- **Slicer-специфичную логику** (`slicer.util.getNode`, manipulation MRML-узлов). Её место — в `plan/scripts/` или `plan/validators/`. Helper в utils может быть импортирован любым модулем, включая Slicer-скрипты, но если он начинает дёргать `slicer` — это уже не helper, а часть Slicer-задачи.
- **Бизнес-логику workflow** (создание записей, переходы статусов). Её место — в `plan/workflows/pipeline_flow.py`.

## Именование и структура

Тематические snake_case-файлы. Жёстких требований нет:

```
plan/utils/
├── __init__.py        # обычно пустой
├── seg_utils.py       # read/write .seg.nrrd
├── constants.py       # label-карты, пороги
├── image_io.py        # NIfTI/DICOM helpers
└── metrics.py         # Dice, IoU, Hausdorff
```

## Импорты из других разделов

`plan/` добавляется фреймворком в `sys.path`, поэтому импорты работают так:

```python
# В pipeline_flow.py
from utils.seg_utils import save_seg_nrrd, master_label_converter
from record_types import master_model

# В Slicer-скриптах и валидаторах
from utils.seg_utils import read_seg_nrrd_labels
```

`plan/utils/__init__.py` может быть пустым — наличие файла делает структуру явной и переживает рефакторинги.

---

## Формат `.seg.nrrd` (важный частный случай)

3D Slicer хранит сегментации в NRRD-формате с дополнительными полями в header — имена и label values сегментов. Если ваш проект работает с сегментациями, в `utils/` обычно есть обёртки read/write.

### Обязательные поля header

```python
header = {
    "type": "unsigned char",
    "dimension": 3,
    "space": "left-posterior-superior",                    # LPS — Slicer convention
    "space directions": (direction * np.array(spacing)).T, # 3x3 cosine matrix
    "space origin": np.array(origin),                       # XYZ origin
}
```

### Метаданные сегментов

Для каждого сегмента (i = 0, 1, ...):

```python
header[f"Segment{i}_ID"] = f"Segment_{i}"
header[f"Segment{i}_Name"] = name           # имя для UI
header[f"Segment{i}_LabelValue"] = str(lbl) # int label в массиве
header[f"Segment{i}_Layer"] = "0"           # обычно "0"
```

### Label converter

Функция `(segment_name: str) -> int`, маппит имя в integer label value. Простейший случай — численные имена (`"1"` → `1`):

```python
def master_label_converter(name: str) -> int:
    return int(name)
```

Для категорий (`"mts"` → `1`):

```python
SEG_LABELS = {"mts": 1, "unclear": 2, "benign": 3}
def category_converter(name: str) -> int:
    return SEG_LABELS[name]
```

### Минимальный read/write

```python
import nrrd
import numpy as np

def save_seg_nrrd(
    data: np.ndarray,
    path: str,
    segment_names: list[str],
    label_converter,
    *,
    spacing: tuple[float, ...],
    origin: tuple[float, ...],
    direction: np.ndarray,
) -> None:
    header = {
        "type": "unsigned char",
        "dimension": 3,
        "space": "left-posterior-superior",
        "space directions": (direction * np.array(spacing)).T,
        "space origin": np.array(origin),
    }
    for i, name in enumerate(segment_names):
        header[f"Segment{i}_ID"] = f"Segment_{i}"
        header[f"Segment{i}_Name"] = name
        header[f"Segment{i}_LabelValue"] = str(label_converter(name))
        header[f"Segment{i}_Layer"] = "0"
    nrrd.write(path, data.astype(np.uint8), header)


def read_seg_nrrd_labels(path: str) -> dict[int, str]:
    """Returns {label_value: segment_name}."""
    _, header = nrrd.read(path)
    labels: dict[int, str] = {}
    i = 0
    while f"Segment{i}_Name" in header:
        labels[int(header[f"Segment{i}_LabelValue"])] = header[f"Segment{i}_Name"]
        i += 1
    return labels
```

### Альтернатива: `clarinet.services.image.Segmentation`

Фреймворк предоставляет numpy/nrrd-обёртку — её часто проще использовать, чем писать своё:

```python
from clarinet.services.image import Segmentation

seg = Segmentation(autolabel=False)
seg.read(path)
seg.img         # numpy 3D-массив
seg.spacing     # voxel spacing
seg._origin
seg._direction
seg.count       # voxel count
seg.difference(other_seg, max_overlap_ratio=0.05)
```

Используйте `Segmentation` для чтения и базовых операций; `seg_utils.save_seg_nrrd` — для записи с кастомными именами и label converter-ами, когда `Segmentation.write()` не подходит.
