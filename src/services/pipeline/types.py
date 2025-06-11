from typing import Protocol, ParamSpec, Callable, Self, Iterable, Any

StepPar = ParamSpec('StepPar')

class Step(Protocol): 
    def __init__(self, pipeline):
        self.pipeline = pipeline
    
    def __call__(self,handler):
        self.pipeline.add_handler(handler)
        return self

    def __gt__(self, value):
        self.pipeline
    
    def step(self, handler):
        self._pipeline

class DataRepository(Protocol):
    def __init__(self, msg: Message):
        self.msg = msg

    ...




class Message(Protocol):
    patient_id: str


class FileLink:
    _data = None

    def __init__(self, path: str, level: str):
        self.path = path
        self.level = level

    def __get__(self, cls, instance: DataRepository):
        if not self._data:
            self._data = instance.msg.patient_id
        return self._data

    def __set__(self, instance, value):
        self._data = value


class DataBaseLink[ModelType]:
    _data: ModelType


# --------------------

step = Step()

class S(Step):
    msg: Message
    repository: DataRepository


class Rep(DataRepository):
    raw_mammogram = FileLink("{user_id}.nii.gz", level="STUDY")
    patient = DataBaseLink[Patient]


@Step
def dcm2nii(msg: Message, repository: Rep = Depends(Rep)):
    repository.raw_mammogram = make_nii(dcm=repository.dcm_mammogram)


segment_calcifications = UserStep(task_description=SegmentCalcifications)


dcm2nii > (
    segment_calcifications_user,
    segment_calcifications_ai,
) > compare_segmentations


class T:
    def __gt__(self, value: Self | Iterable[Self]) -> Self:
        return self
    def __lt__(self, value: Self | Iterable[Self]) -> Self:
        return self

a = T()
b = T()
c = T()

a > (b, c) > c