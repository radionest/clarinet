"""Pipeline flow: Liver metastasis segmentation study.

Workflow for multi-modality segmentation, master model management,
projection comparison, and second review.

See README.md for full business logic description.
"""

from files_catalog import master_model, master_projection, segmentation_single

from clarinet.flow import Field as F
from clarinet.flow import file, record, study, task

# ---------------------------------------------------------------------------
# Pipeline tasks (выполняются в воркерах)
# ---------------------------------------------------------------------------


@task
def init_master_model(msg, ctx):
    """Создание мастер-модели по первой завершённой КТ-сегментации.

    Берёт сегментацию врача, разделяет на отдельные ROI с уникальными номерами,
    сохраняет как master_model на уровне PATIENT.
    """
    import image_processor as img

    if ctx.files.exists(master_model):
        return  # мастер-модель уже существует

    volume = img.load(ctx.files.get(segmentation_single, uid=msg["series_uid"]))
    rois = img.split_islands(volume)
    new_img = img.new(size_from=volume)
    for num, roi_val in enumerate(rois):
        new_img[roi_val] = num
    img.save(new_img, ctx.files.get_path(master_model))


@task
def compare_w_projection(msg, ctx):
    """Автоматическое сравнение сегментации врача с проекцией мастер-модели.

    Для каждого ROI проверяет пересечение:
    - пересечение есть -> один и тот же очаг
    - пересечения нет -> false negative или false positive

    Результат записывается в data записи compare_with_projection.
    """
    import image_processor as img

    segmentation = img.load(ctx.files.get(segmentation_single, uid=msg["series_uid"]))
    projection = img.load(ctx.files.get(master_projection, uid=msg["series_uid"]))

    false_negative = []  # очаги на проекции, не найденные врачом
    false_positive_num = 0  # очаги врача, отсутствующие на проекции

    for roi_num in img.unique(projection):
        if not img.has_overlap(projection, roi_num, segmentation):
            false_negative.append({"lesion_num": roi_num})

    for roi_num in img.unique(segmentation):
        if not img.has_overlap(segmentation, roi_num, projection):
            false_positive_num += 1

    ctx.client.update_record_data(
        msg["record_id"],
        {
            "false_negative": false_negative,
            "false_negative_num": len(false_negative),
            "false_positive_num": false_positive_num,
        },
    )


# ---------------------------------------------------------------------------
# Flow: создание записей по результатам first_check
# ---------------------------------------------------------------------------

# При поступлении нового исследования создаётся first_check
(study().on_creation().create_record("first_check"))

# КТ -> две сегментации (single + with_archive)
(
    record("first_check")
    .on_finished()
    .if_record(F.is_good == True, F.study_type == "CT")
    .create_record("segment_CT_single", "segment_CT_with_archive")
)

# МРТ
(
    record("first_check")
    .on_finished()
    .if_record(F.is_good == True, F.study_type == "MRI")
    .create_record("segment_MRI_single")
)

# КТ-АГ
(
    record("first_check")
    .on_finished()
    .if_record(F.is_good == True, F.study_type == "CT-AG")
    .create_record("segment_CTAG_single")
)

# МРТ-АГ
(
    record("first_check")
    .on_finished()
    .if_record(F.is_good == True, F.study_type == "MRI-AG")
    .create_record("segment_MRIAG_single")
)

# ПДКТ-АГ
(
    record("first_check")
    .on_finished()
    .if_record(F.is_good == True, F.study_type == "PDCT-AG")
    .create_record("segment_PDCTAG_single")
)

# ---------------------------------------------------------------------------
# Flow: после завершения сегментации
# ---------------------------------------------------------------------------

# Список всех типов сегментации (single-варианты)
SEGMENT_TYPES = [
    "segment_CT_single",
    "segment_MRI_single",
    "segment_CTAG_single",
    "segment_MRIAG_single",
    "segment_PDCTAG_single",
]

for seg_type in SEGMENT_TYPES:
    # Создание мастер-модели по первой завершённой КТ-сегментации
    if seg_type == "segment_CT_single":
        (record(seg_type).on_finished().do_task(init_master_model))

    # Создание проекции мастер-модели на серию сегментации
    (record(seg_type).on_finished().create_record("create_master_projection"))

# segment_CT_with_archive тоже запускает проекцию
(record("segment_CT_with_archive").on_finished().create_record("create_master_projection"))

# ---------------------------------------------------------------------------
# Flow: после завершения проекции -> автоматическое сравнение
# ---------------------------------------------------------------------------

(record("create_master_projection").on_finished().create_record("compare_with_projection"))

# Автозаполнение compare_with_projection при создании (role=auto)
(record("compare_with_projection").on_status("pending").do_task(compare_w_projection))

# ---------------------------------------------------------------------------
# Flow: по результатам сравнения
# ---------------------------------------------------------------------------

# false_positive > 0 -> создать update_master_model
(
    record("compare_with_projection")
    .on_finished()
    .if_record(F.false_positive_num > 0)
    .create_record("update_master_model")
)

# Любые расхождения -> second_review для врача
(
    record("compare_with_projection")
    .on_finished()
    .if_record(F.false_negative_num > 0)
    .create_record("second_review")
)

# ---------------------------------------------------------------------------
# Flow: инвалидация проекций при обновлении мастер-модели
# ---------------------------------------------------------------------------

(file(master_model).on_update().invalidate_all_records("create_master_projection"))
