from clarinet.pipeline import node, event, gather_pipes, finished
from clarinet.pipeline.commons import give_task

@node
def anonimize_dicom(msg)->None:
    pass

@node
def get_birads_from_protocol(msg)->None:
    pass

@node 
def get_acr_from_procol(msg)->None:
    pass

@node
def compare_ai_with_doctor(msg)->Bool:
    return get_doc_result(msg.series_id) == get_ai_result(msg.series_id)

@node(need_gpu=True)
def segment_mammo(msg)->None:
    pass


event('patient_created')
    > anonimize_dicom 
    > (give_task('quality_check'),
       get_birads_from_protocol,
       get_acr_from_procol,
       segment_mammo)

event('quality_check', status='finished') 
    > (give_task('segment_calcifications'),
       make_seg_calcifications_from_ai
       )

(finished('segment_calcifications')
make_seg_calcifications_from_ai) 
    > compare_ai_with_doctor.if(result=False) 
    > give_task('check_calcification_differences')

pipeline = gather_pipes() # Должен собирать пайпланы из locals

