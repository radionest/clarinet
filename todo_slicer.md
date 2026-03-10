# Slicer: план реализации фич для demo_liver_v2

## Обзор

Для запуска demo_liver_v2 необходимо реализовать **7 новых методов SlicerHelper** и **4 slicer-скрипта**.
Скрипты используют DSL из `helper.py`, который выполняется внутри 3D Slicer через HTTP API.

---

## Часть 1. Новые методы SlicerHelper

### 1.1 `get_segment_names(segmentation)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** Несколько скриптов требуют получить список имён/номеров сегментов из
загруженной сегментации — для проверки уникальности, автонумерации, навигации, отображения.

**Сигнатура:**
```python
def get_segment_names(self, segmentation: Any) -> list[str]
```

**Поведение:**
- Получить `vtkSegmentation` из segmentation node.
- Итерировать по `GetNumberOfSegments()` → `GetNthSegment()`.
- Вернуть список `segment.GetName()` в порядке индексов.
- Если segmentation — `SegmentationBuilder`, извлечь `.node`.

**Контекст использования:**
- `create_projection.py`: получить список ROI для создания пустых копий.
- `update_master_model.py`: определить max номер ROI для auto-increment.
- `second_review.py`: получить номера пропущенных очагов.

---

### 1.2 `copy_segments(source_seg, target_seg, segment_names=None, empty=False)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** Копирование сегментов между segmentation-нодами. В режиме `empty=True` —
только структура (имя, цвет), без labelmap-данных. Это ключевая операция для
`create_projection.py`, где нужны пустые ROI с той же нумерацией что у master model.

**Сигнатура:**
```python
def copy_segments(
    self,
    source_seg: Any,
    target_seg: Any | SegmentationBuilder,
    segment_names: list[str] | None = None,
    empty: bool = False,
) -> None
```

**Поведение:**
- Если `segment_names=None` — копировать все сегменты из source в target.
- Если указан список имён — копировать только перечисленные сегменты.
- `empty=False` (по умолчанию): полная копия — имя, цвет, labelmap.
  Использовать `vtkSegmentation.CopySegmentFromSegmentation(source_vtkSeg, segment_id)`.
- `empty=True`: копировать только метаданные (имя, цвет), labelmap остаётся пустым.
  Эквивалент `target.add_segment(name, color)` для каждого сегмента из source.
- Если target — `SegmentationBuilder`, извлекать `.node`.

**Контекст использования:**
- `create_projection.py` (`empty=True`): создать пустые ROI с нумерацией master model.
- `second_review.py` (`empty=False`): перенос ROI между нодами.
- `update_master_model.py` (`empty=False`): слияние новых ROI в мастер-модель.

---

### 1.3 `subtract_segmentations(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** `second_review.py` должен показать врачу только пропущенные очаги:
проекция минус то, что врач уже отметил. Операция работает на уровне **целых ROI**,
а не попиксельно — аналогично `Segmentation.difference()` из image-сервиса.
Если ROI проекции частично пересекается с любым ROI врача, удаляется **весь ROI целиком**.

**Сигнатура:**
```python
def subtract_segmentations(
    self,
    seg_a: Any,
    seg_b: Any,
    output_name: str | None = None,
    max_overlap: int = 0,
    max_overlap_ratio: float | None = None,
) -> Any
```

**Поведение:**
- Для каждого сегмента (ROI) в `seg_a`:
  1. Получить binary labelmap сегмента через `GetBinaryLabelmapRepresentation()`.
  2. Получить объединённый binary labelmap всех сегментов `seg_b`.
  3. Посчитать количество пересекающихся вокселей (`overlap`).
  4. Если `overlap > max_overlap` → **удалить весь сегмент** из результата.
  5. Если задан `max_overlap_ratio` и `overlap / total_voxels > max_overlap_ratio` →
     тоже удалить (оба порога работают по AND-логике, как в image-сервисе).
- Результат — новый segmentation node (или модификация `seg_a` in-place, если `output_name=None`).
- Если `output_name` задан — создать новый node с этим именем, скопировать
  только «выжившие» сегменты.

**Семантика порогов** (идентична `Segmentation.difference()` из `services/image/`):
- `max_overlap=0` (по умолчанию, strict): любое пересечение (>= 1 воксель) →
  весь ROI считается «найденным» врачом и удаляется.
- `max_overlap=N`: допустить до N вокселей шума/артефактов, удалять только
  при значительном пересечении.
- `max_overlap_ratio=R`: удалять если доля пересечения > R от объёма ROI.
  Полезно для случаев, когда мелкий ROI случайно касается соседнего.
- Если заданы оба — ROI удаляется только если **оба** порога превышены (AND).

**Реализация в контексте Slicer:**
```python
# Для каждого сегмента в seg_a:
seg_id = vtk_seg_a.GetNthSegmentID(i)
labelmap_a = vtk.vtkOrientedImageData()
vtk_seg_a.GetBinaryLabelmapRepresentation(seg_id, labelmap_a)

# Объединённая маска seg_b (все сегменты):
merged_b = vtk.vtkOrientedImageData()
vtk_seg_b.GetBinaryLabelmapRepresentation(first_seg_id, merged_b)
# ... merge остальных через vtkImageMathematics (OR)

# Подсчёт overlap через numpy:
arr_a = slicer.util.arrayFromVTKMatrix(labelmap_a)
arr_b = slicer.util.arrayFromVTKMatrix(merged_b)
overlap = int(np.sum((arr_a > 0) & (arr_b > 0)))
total = int(np.sum(arr_a > 0))
```

**Контекст использования:**
- `second_review.py`: `subtract_segmentations(projection, doctor_seg)` →
  ROI, которые врач нашёл хотя бы частично, удаляются целиком.
  Остаются только полностью пропущенные ROI.
- `second_review.py` (повторный): `subtract_segmentations(remaining, prev_review)` →
  убрать ROI, уже классифицированные в прошлом review.

---

### 1.4 `auto_number_segment(segmentation, prefix="ROI", start_from=None)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** `update_master_model.py` требует автоматического присвоения уникальных
номеров новым ROI, добавленным экспертом. Номер должен быть следующим после
максимального существующего.

**Сигнатура:**
```python
def auto_number_segment(
    self,
    segmentation: Any,
    prefix: str = "ROI",
    start_from: int | None = None,
) -> int
```

**Поведение:**
- Если `start_from=None` — автоматически определить max номер из существующих сегментов
  (парсить имена вида `{prefix}_{N}`, взять max N).
- Добавить новый пустой сегмент с именем `{prefix}_{next_number}`.
- Вернуть присвоенный номер.
- Если нет существующих сегментов — начать с 1.

**Контекст использования:**
- `update_master_model.py`: эксперт нажимает `N` → auto_number_segment
  создаёт `ROI_N+1`, эксперт рисует на нём.

---

### 1.5 `set_dual_layout(volume_a, volume_b, seg_a=None, seg_b=None, linked=True)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** `create_projection.py` требует side-by-side Compare View: слева КТ с master
model, справа МРТ с пустой проекцией. Текущий `set_layout()` поддерживает только
single-volume layouts. Нужен метод для настройки dual-view с раздельными томами
и сегментациями на каждом viewport.

**Сигнатура:**
```python
def set_dual_layout(
    self,
    volume_a: Any,
    volume_b: Any,
    seg_a: Any | None = None,
    seg_b: Any | None = None,
    linked: bool = True,
) -> None
```

**Поведение:**
1. Установить layout `SlicerLayoutSideBySideView` (Red + Yellow axial views рядом).
2. Настроить Red slice view:
   - `compositeNode.SetBackgroundVolumeID(volume_a.GetID())` — фоновый том.
   - Если `seg_a` передан: `compositeNode.SetSegmentationNode(seg_a)` — overlay сегментации.
3. Настроить Yellow slice view:
   - `compositeNode.SetBackgroundVolumeID(volume_b.GetID())`.
   - Если `seg_b` передан: `compositeNode.SetSegmentationNode(seg_b)`.
4. Если `linked=True`:
   - Установить одинаковый `linkedControl` на оба slice node.
   - Включить `HotLinkedControl` для синхронизации скролла и зума.
   - Оба viewport будут синхронно скроллиться при навигации по срезам.
5. Вызвать `resetSliceViews()` для корректного fit.

**Примечание по реализации:**
```python
# Пример настройки linked views в Slicer Python API
red_node = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeRed")
yellow_node = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeYellow")
red_node.SetLinkedControl(True)
yellow_node.SetLinkedControl(True)
# Для синхронизации через InteractionNode:
red_node.SetInteractionFlags(red_node.GetInteractionFlagsModifier())
```

**Контекст использования:**
- `create_projection.py`: `set_dual_layout(ct_vol, mri_vol, master_seg, projection_seg, linked=True)`.

---

### 1.6 `get_segment_centroid(segmentation, segment_name)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** При навигации по ROI в `create_projection.py` — когда эксперт выбирает
пустой ROI в проекции, нужно перейти к позиции соответствующего ROI в master model.
Для этого нужно вычислить центр масс (centroid) сегмента в RAS-координатах.

**Сигнатура:**
```python
def get_segment_centroid(self, segmentation: Any, segment_name: str) -> tuple[float, float, float] | None
```

**Поведение:**
- Получить segment ID по имени из vtkSegmentation.
- Вычислить centroid сегмента в RAS-координатах.
- Способ вычисления — через `SegmentStatistics` модуль Slicer:
  ```python
  import SegmentStatistics
  stats = SegmentStatistics.SegmentStatisticsLogic()
  stats.getParameterNode().SetParameter("Segmentation", seg_node.GetID())
  stats.computeStatistics()
  centroid_ras = stats.getStatistics()[segment_id, "LabelmapSegmentStatisticsPlugin.centroid_ras"]
  ```
- Если сегмент пустой (нет вокселей) — вернуть `None`.
- Вернуть `(R, A, S)` координаты центроида.

**Контекст использования:**
- `create_projection.py`: навигация к ROI master model при выборе пустого ROI проекции.
- Потенциально полезен в `second_review.py` для навигации к пропущенным очагам.

---

### 1.7 `setup_segment_focus_observer(editable_seg, reference_seg)`

**Где:** `clarinet/services/slicer/helper.py`, метод класса `SlicerHelper`

**Зачем:** Ключевая UX-фича для `create_projection.py`. Когда эксперт выбирает
пустой ROI в проекции (editable_seg), оба viewport должны автоматически перейти
к позиции соответствующего ROI в master model (reference_seg). Это позволяет
эксперту сразу видеть, где очаг находится на КТ, и отметить его на МРТ.

**Сигнатура:**
```python
def setup_segment_focus_observer(
    self,
    editable_seg: Any,
    reference_seg: Any,
) -> None
```

**Поведение:**
1. Получить `vtkMRMLSegmentEditorNode` из текущего SegmentEditor.
2. Добавить observer на событие изменения выбранного сегмента
   (`SegmentEditorNode.SelectedSegmentIDChangedEvent` или через `AddObserver`
   на `ModifiedEvent` с фильтрацией по `GetSelectedSegmentID()`).
3. Callback при смене выбранного сегмента:
   a. Получить имя выбранного сегмента в `editable_seg`.
   b. Проверить, пустой ли этот сегмент (нет вокселей в labelmap).
   c. Если пустой — найти сегмент с тем же именем в `reference_seg`.
   d. Вызвать `get_segment_centroid(reference_seg, segment_name)`.
   e. Если centroid найден — навигировать все slice views к этой RAS-позиции:
      ```python
      for color in ["Red", "Yellow", "Green"]:
          slice_node = slicer.mrmlScene.GetNodeByID(f"vtkMRMLSliceNode{color}")
          if slice_node:
              slice_node.JumpSlice(r, a, s)
      ```
   f. Если ROI уже заполнен в editable_seg — не навигировать (эксперт работает
      с уже размеченным ROI, фокус не нужен).

**Примечание по реализации:**
- Observer живёт всё время сессии скрипта (не нужно убирать).
- Использовать `vtk.vtkCommand.ModifiedEvent` на SegmentEditorNode.
- `JumpSlice` на linked views перемещает оба viewport синхронно.

**Контекст использования:**
- `create_projection.py`: единственный потребитель. Устанавливается после `set_dual_layout`
  и `setup_editor`.

---

## Часть 2. Slicer-скрипты

### 2.1 `segment.py` — интерактивная сегментация очагов

**Где:** `examples/demo_liver_v2/tasks/segment.py`

**Используется:** 6 record types — `segment_CT_single`, `segment_CT_with_archive`,
`segment_MRI_single`, `segment_CTAG_single`, `segment_MRIAG_single`, `segment_PDCTAG_single`

**Контекстные переменные** (инжектируются SlicerService):
- `working_folder` — рабочая директория записи
- `output_path` — путь для сохранения результата (e.g. `segmentation_single_42.seg.nrrd`)
- `pacs_host`, `pacs_port`, `pacs_aet`, `pacs_calling_aet`, `pacs_prefer_cget`, `pacs_move_aet`

**Алгоритм:**
1. Создать `SlicerHelper(working_folder)`.
2. Загрузить основной том серии из PACS (`s.load_study_from_pacs(study_uid)`) или
   из локального файла (`s.load_volume(...)`).
3. Если существует предыдущая сегментация (`output_path` файл существует) —
   загрузить её (`s.load_segmentation(output_path)`) для доработки.
4. Иначе — создать пустую сегментацию с одним сегментом "Lesions"
   (`s.create_segmentation("Segmentation").add_segment("Lesions", (1.0, 0.0, 0.0))`).
5. Настроить редактор: `s.setup_editor(seg, effect="Paint", brush_size=5.0)`.
6. Установить layout: `s.set_layout("axial")`.
7. Добавить стандартные шорткаты: `s.add_view_shortcuts()`.
8. Добавить аннотацию с инструкцией: `s.annotate("Segment all lesions")`.

**Сохранение:** Не автоматическое — сохранение происходит через endpoint
`/records/{id}/validate`, который вызывает `slicer_result_validator` скрипт.
Нужен парный валидатор, вызывающий `export_segmentation("Segmentation", output_path)`.

**Особенности `segment_CT_with_archive`:**
- После загрузки основного тома, дополнительно загрузить архивные КТ-исследования
  через `s.load_study_from_pacs(archive_study_uid)` для каждого архивного UID.
- Архивные UID передаются через контекст (из lifecycle.open) или через
  дополнительный `slicer_script_args`.

**Зависимости от новых методов:** Нет — все нужные методы уже есть в SlicerHelper.

---

### 2.2 `create_projection.py` — ручная проекция мастер-модели на серию

**Где:** `examples/demo_liver_v2/tasks/create_projection.py`

**Используется:** `create_master_projection` (role=expert, level=SERIES)

**Суть процедуры:**
Эксперт видит два viewport рядом. Слева — КТ (или та серия, на которой основана
master model) с наложенной мастер-моделью: все ROI с номерами видны на КТ. Справа —
целевая серия (например Т2 МРТ) с пустой сегментацией, в которой созданы те же ROI
с той же нумерацией, но без данных. Задача эксперта — для каждого ROI найти очаг
на правом viewport и отметить его границы. При выборе пустого ROI в проекции
фокус автоматически переходит к позиции соответствующего ROI в master model,
чтобы эксперт видел, что именно надо искать.

**Контекстные переменные:**
- `working_folder` — рабочая директория записи
- `master_model_path` — путь к `master_model.seg.nii` (input, PATIENT level)
- `master_volume_study_uid` — Study UID серии, на которой основана master model
- `target_study_uid` — Study UID целевой серии (на которую проецируем)
- `output_path` — путь для `master_projection.seg.nrrd` (output)
- `pacs_host`, `pacs_port`, etc. — для загрузки серий из PACS

**Алгоритм:**
1. Создать `SlicerHelper(working_folder)`.
2. Загрузить том-источник (серия master model) из PACS:
   `master_vol = s.load_study_from_pacs(master_volume_study_uid)`.
3. Загрузить master model: `master_seg = s.load_segmentation(master_model_path, "MasterModel")`.
4. Загрузить целевой том из PACS:
   `target_vol = s.load_study_from_pacs(target_study_uid)`.
5. Создать пустую сегментацию-проекцию на целевом томе:
   `projection = s.create_segmentation("Projection")`.
6. Скопировать структуру ROI из master model (только имена и цвета, без данных):
   `s.copy_segments(master_seg, projection, empty=True)`.
7. Настроить dual layout с синхронизацией скролла:
   `s.set_dual_layout(master_vol, target_vol, seg_a=master_seg, seg_b=projection, linked=True)`.
   - Левый viewport (Red): КТ + MasterModel (reference, read-only визуально).
   - Правый viewport (Yellow): МРТ + Projection (editable).
8. Настроить редактор на projection segmentation:
   `s.setup_editor(projection, effect="Paint", brush_size=5.0)`.
9. Установить observer для автофокуса:
   `s.setup_segment_focus_observer(editable_seg=projection, reference_seg=master_seg)`.
   При выборе пустого ROI в Projection — автоматический jump к центроиду
   соответствующего ROI в MasterModel.
10. Добавить шорткаты: `s.add_view_shortcuts()`.
11. Аннотация: показать общее количество ROI для разметки.

**Зависимости от новых методов:**
- `copy_segments` (с `empty=True`) — создание пустых ROI с нумерацией master model.
- `set_dual_layout` — dual viewport с синхронизацией.
- `get_segment_centroid` — вычисление позиции ROI (используется внутри observer).
- `setup_segment_focus_observer` — автонавигация при выборе пустого ROI.
- `get_segment_names` — для отображения списка ROI / проверки.

---

### 2.3 `second_review.py` — классификация пропущенных очагов

**Где:** `examples/demo_liver_v2/tasks/second_review.py`

**Используется:** `second_review` (level=SERIES)

**Контекстные переменные:**
- `working_folder` — рабочая директория
- `master_projection_path` — путь к `master_projection.seg.nrrd` (input)
- `doctor_segmentation_path` — путь к `segmentation_single_{user_id}.seg.nrrd` (input)
- `previous_review_path` — путь к предыдущему `second_review_{user_id}.seg.nrrd` (input, если есть)
- `output_path` — путь для нового `second_review_{user_id}.seg.nrrd` (output)
- `false_negative` — список `[{"lesion_num": N}, ...]` из compare_with_projection (из record.data)

**Алгоритм:**
1. Создать `SlicerHelper(working_folder)`.
2. Загрузить reference volume серии из PACS.
3. Загрузить master projection: `s.load_segmentation(master_projection_path, "Projection")`.
4. Загрузить doctor's segmentation: `s.load_segmentation(doctor_segmentation_path, "DoctorSeg")`.
5. Вычесть сегментацию врача из проекции:
   `remaining = s.subtract_segmentations(projection_seg, doctor_seg, "MissedLesions")`.
   Результат — только те ROI, которые врач пропустил (false negatives).
6. Если есть `previous_review_path` (повторный review после инвалидации) —
   вычесть предыдущий review: `s.subtract_segmentations(remaining, prev_review_seg)`.
   Останутся только НОВЫЕ пропуски.
7. Создать output segmentation с 4 классификационными сегментами:
   ```python
   output = s.create_segmentation("Classification")
   output.add_segment("metastasis", (1.0, 0.0, 0.0))    # красный
   output.add_segment("unclear", (1.0, 1.0, 0.0))       # жёлтый
   output.add_segment("benign", (0.0, 1.0, 0.0))        # зелёный
   output.add_segment("invisible", (0.5, 0.5, 0.5))     # серый
   ```
8. Настроить layout "four_up" для обзора.
9. Настроить редактор на output segmentation с эффектом "Islands" —
   врач кликает на пропущенный очаг в `MissedLesions` и переносит в нужную категорию.
10. Аннотация: показать номера пропущенных очагов из `false_negative`.

**Зависимости от новых методов:**
- `subtract_segmentations` — ключевая операция (шаги 5, 6).
- `get_segment_names` — для отображения номеров пропущенных ROI.
- `copy_segments` — опционально, если нужен перенос ROI между нодами.

---

### 2.4 `update_master_model.py` — обновление мастер-модели экспертом

**Где:** `examples/demo_liver_v2/tasks/update_master_model.py`

**Используется:** `update_master_model` (role=expert, level=PATIENT)

**Контекстные переменные:**
- `working_folder` — рабочая директория
- `master_model_path` — путь к `master_model.seg.nii` (input + output, тот же файл)
- `doctor_segmentation_path` — путь к сегментации врача, содержащей false positive ROI
- `output_path` — путь для обновлённой `master_model.seg.nii`

**Алгоритм:**
1. Создать `SlicerHelper(working_folder)`.
2. Загрузить reference volume (серию, на которой основана master model) из PACS.
3. Загрузить master model: `s.load_segmentation(master_model_path, "MasterModel")`.
4. Загрузить сегментацию врача с false positives (для визуального контекста):
   `s.load_segmentation(doctor_segmentation_path, "DoctorNewROI")`.
5. Получить текущий максимальный номер ROI:
   `names = s.get_segment_names(master_seg)` → парсить максимальный номер.
6. Настроить редактор на master model segmentation.
7. Добавить shortcut для создания нового ROI:
   ```python
   s.add_shortcuts([("n", f"s.auto_number_segment(master_seg, start_from={max_num+1})")])
   ```
   По нажатию `N` — создаётся новый пустой сегмент с следующим номером.
8. Эксперт визуально сравнивает `DoctorNewROI` с `MasterModel`:
   - Если ROI врача действительно новый очаг → нажать N, нарисовать ROI в мастер-модели.
   - Если ROI врача ложноположительный → пропустить.
9. Аннотация: показать общее количество ROI до и после обновления.

**Зависимости от новых методов:**
- `get_segment_names` — для определения текущего max номера ROI.
- `auto_number_segment` — для автоматического создания ROI с уникальным номером.

---

## Часть 3. Валидаторы результатов (slicer_result_validator)

Каждый slicer-скрипт открывает workspace, но сохранение результата происходит
через отдельный endpoint `POST /records/{id}/validate`. Для этого в RecordType
используется поле `slicer_result_validator`.

### 3.1 `segment_validator.py`

**Для:** все `segment_*` record types

**Алгоритм:**
1. Вызвать `export_segmentation("Segmentation", output_path)`.
2. Проверить, что файл создан и не пустой.
3. Вернуть `{"status": "ok", "file": output_path}`.

### 3.2 `projection_validator.py`

**Для:** `create_master_projection`

**Алгоритм:**
1. `export_segmentation("Projection", output_path)`.
2. Проверить, что количество непустых сегментов > 0.
3. Вернуть `{"status": "ok", "segments": count}`.

### 3.3 `second_review_validator.py`

**Для:** `second_review`

**Алгоритм:**
1. `export_segmentation("Classification", output_path)`.
2. Собрать статистику: сколько очагов классифицировано в каждую категорию.
3. Проверить, что все пропущенные очаги классифицированы (ни один не остался
   в `MissedLesions`).
4. Вернуть `{"status": "ok", "classifications": {...}}`.

### 3.4 `master_model_validator.py`

**Для:** `update_master_model`

**Алгоритм:**
1. `export_segmentation("MasterModel", output_path)`.
2. Вернуть `{"status": "ok", "total_rois": count}`.

---

## Часть 4. Порядок реализации

### Фаза 1: Helper-методы (блокирующие для скриптов)

| # | Метод | Сложность | Блокирует |
|---|-------|-----------|-----------|
| 1 | `get_segment_names` | Низкая | все скрипты |
| 2 | `get_segment_centroid` | Низкая | create_projection.py |
| 3 | `copy_segments` | Средняя | create_projection.py, second_review.py |
| 4 | `auto_number_segment` | Низкая | update_master_model.py |
| 5 | `subtract_segmentations` | Средняя | second_review.py |
| 6 | `set_dual_layout` | Средняя | create_projection.py |
| 7 | `setup_segment_focus_observer` | Высокая | create_projection.py |

### Фаза 2: Скрипты (зависят от helper-методов)

| # | Скрипт | Зависит от методов | Сложность |
|---|--------|--------------------|-----------|
| 1 | `segment.py` + валидатор | — (всё есть) | Низкая |
| 2 | `update_master_model.py` + валидатор | get_segment_names, auto_number_segment | Средняя |
| 3 | `create_projection.py` + валидатор | copy_segments, set_dual_layout, get_segment_centroid, setup_segment_focus_observer, get_segment_names | Высокая |
| 4 | `second_review.py` + валидатор | subtract_segmentations, get_segment_names, copy_segments | Высокая |

### Фаза 3: Тесты

- Unit-тесты для новых helper-методов (mock Slicer API).
- Integration-тесты для скриптов (требуют запущенный 3D Slicer с `@pytest.mark.slicer`).
- Обновить `record_types.py` — добавить `slicer_result_validator` поля.
