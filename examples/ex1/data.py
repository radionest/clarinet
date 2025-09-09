from typing import Literal, List, Protocol, Callable

from clarinet.design import DataModel, CDepends, UserTask
from clarinet.models import Patient
from clarinet.service.image import Image, Segmentation, load_segmentation
from clarinet.types import Path
import clarinet

TypeACR = Literal["A", "B", "C", "D"]
TypeBIRADS = Literal[1, 2, 3, 4, 5, 6, 0]
TypeCalcification = Literal['benign','suspicious','malignant']

class MessageBase:
    patient: Patient
    study: Study
    series: Series
    
class TaskBaseDefinition(Protocol):
    min_users: int = 1
    max_users: int = 1
    level: Literal['STUDY','PATIENT','SERIES']
    role: Literal['admin','auto','doctor','expert']  
    
    def slicer_open(self):...  
    def slicer_save(self):...


    
    
    


class Step:
    ...
    def run(self,msg: Message):
        result = await self.handle_task(msg: Message)
        


class Pipe:
    ...
    
#---------------------------------------------------

class Message:
    patient_id: str
    series_uid: Optional[str]
    
    
pipe1 = Pipe(
    step1,
    step2,
    step3
)

pipe2 = Pipe(
    pipe1,
    step3,    
)


class UserBIRADS(TaskDefinition):
    
    __max_users__ = 1
    __role__ = 'doctor'
    __level__ = 'STUDY'
    
    birads_left: CategoriesBIRADS
    birads_right: CategoriesBIRADS
    acr_left: CategoriesACR
    acr_right: CategoriesACR

class AiBIRADS(TaskDefinition):
    __min_users__ = 1
    __max_users__ = 1
    __role__ = 'auto'
    __level__ = 'STUDY'

    birads_left: CategoriesBIRADS
    birads_right: CategoriesBIRADS
    acr_left: CategoriesACR
    acr_right: CategoriesACR
    
class CalcificationsUser(TaskDefinition):
    __level__ = 'STUDY'
    right_MLO: List[TypeCalcification]
    right_CC: List[TypeCalcification]
    left_MLO: List[TypeCalcification]
    left_CC: List[TypeCalcification]
    
    
class CalcificationAI_vs_User(TaskDefinition):
    __task_name__ = 'calcification'
    __task_level__ = 'STUDY'
    equal: bool

class DataModel:
    msg = Message

class LinkFile:
    def __get__(self, cls: DataModel, instance):
        cls.msg.series

class DataManager(DataModel):
    
    segmentation_calcium_user: Segmentation = LinkFile('calcifications_by_user_{user_id}.seg.nii')
    segmentation_calcium_ai: Segmentation = LinkFile('calcifications_by_ai.seg.nii')
    
    task_result_compare_segmentations: CalcificationAI_vs_User


@step 
def compare(data: DataManager):
    data.msg.patient
    data.segmentation_calcium_ai.filter_roi(by_name=('malignant','suspicious'))
    
    data.segmentation_calcium_ai.filter_roi(by_name='benign')
    data.task_result_compare_segmentations.equal = 
    
def step(handler: Callable[[Message, DataManager],
                           None]):
    ...


p = Pipeline()


    
@step
def test1(msg: Message,
          repository: DataManager):
    ...