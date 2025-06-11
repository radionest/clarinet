from clarinet.design import stage, SUCCES, FAIL, StageSignal, Stage, UserStage, stage_handler, StageContext
from clarinet.dicom import get_dicom, PACS
from clarinet.types import DicomUID
from clarinet import CDepends

from .commons import get_pacs_petrova

@stage
class GatherData:
    add_dicom_series: Stage
    anonimize_dicom: Stage
    dicom_quality_check: Stage
    check_is_multiple_series: Stage
    select_series: UserStage



class add_dicom_series(Stage):
    ...

class dicom_quality_check(Stage):
    check_scan_zone: Stage
    user_quality_check: UserStage

class Calcifications(Stage):
    GatherData: GatherData
    MakeCalcificationsDataset: MakeCalcificationsDataset
    segment_calcifications: UserStage
    ai_segment_calcifications: Stage
    compare_ai_vs_user: Stage

class ai_segment_calcifications(Stage):
    ...

class segment_calcifications(Stage):
    ...

@depends(ai_segment_calcifications, segment_calcifications)
class compare_ai_vs_user(Stage):
    ...

compare_ai_vs_user = Stage()


@stage
def add_dicom_series():
    ...

@stage
def gather_data():
    add_dicom_series


class GatherData:
    add_dicom_series: Stage
    anonimize_dicom: Stage
    dicom_quality_check: Stage
    check_is_multiple_series: Stage
    select_series: UserStage



