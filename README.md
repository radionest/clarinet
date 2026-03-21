# Clarinet

A framework for organizing clinical-radiological studies. You describe record types, data schemas, and workflow logic — run `clarinet run` — and get a web application with an admin panel, auto-generated forms, task management, and PACS integration.

## Why

Running a medical imaging study means coordinating dozens of participants, modalities, processing steps, and files. Typically this looks like: images on PACS, annotations in shared folders, protocols in spreadsheets, and tracking "who did what" in the coordinator's head.

Clarinet replaces all of this with a single system where:

- Data is organized into a hierarchy: **Patient → Study → Series → Record**
- Each record is typed, with a data schema, file registry, and access control
- Transitions between steps happen automatically via workflow rules
- Heavy computation (segmentation, anonymization, comparison) runs on remote workers

## How It Works

### 1. Define Your Study Structure

Record types, files, and data schemas are described in Python or TOML:

```python
from clarinet.flow import FileDef, FileRef, RecordDef

segmentation = FileDef(
    pattern="segmentation_{user_id}.seg.nrrd",
    level="STUDY",
)

segment_ct = RecordDef(
    name="segment-ct",
    label="CT Segmentation",
    level="STUDY",
    role="doctor_CT",
    min_records=2, max_records=4,
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    files=[FileRef(segmentation, "output")],
    data_schema="schemas/segment.schema.json",
)
```

Data schemas are JSON Schema. Clarinet auto-generates forms in the web UI:

```json
{
    "properties": {
        "is_good": {"type": "boolean"},
        "study_type": {"type": "string", "enum": ["CT", "MRI", "CT-AG"]},
        "best_series": {"type": "string", "x-options": {"source": "study_series"}}
    }
}
```

### 2. Define Your Workflow

A Python DSL describes what happens when a record's status changes, data is submitted, or files are modified:

```python
from clarinet.services.recordflow import Field, record, study, file

F = Field()

# New study arrives → create initial assessment
study().on_creation().create_record("first-check")

# Assessment done → create segmentation tasks by modality
(
    record("first-check")
    .on_finished()
    .if_record(F.is_good == True)
    .match(F.study_type)
    .case("CT").create_record("segment-ct", "segment-ct-archive")
    .case("MRI").create_record("segment-mri")
)

# Segmentation finished → run automatic comparison
record("segment-ct").on_finished().do_task(compare_with_model)

# Master model file changed → invalidate all projections
file("master_model").on_update().invalidate_all_records("create-projection")
```

### 3. Write Processing Tasks

Tasks requiring computation (GPU segmentation, DICOM anonymization, annotation comparison) are defined as pipeline tasks and executed on remote workers via RabbitMQ:

```python
from clarinet.services.pipeline import pipeline_task, PipelineMessage, SyncTaskContext

@pipeline_task(queue="clarinet.gpu")
def run_segmentation(msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    image = ctx.files.resolve("ct_image")
    output = ctx.files.resolve("segmentation")
    model.predict(image, output)

@pipeline_task(auto_submit=True)
def compare_with_model(msg: PipelineMessage, ctx: SyncTaskContext) -> dict:
    seg = Segmentation(ctx.files.resolve("segmentation"))
    proj = Segmentation(ctx.files.resolve("projection"))
    return {"false_negative": seg.difference(proj).count}
```

### 4. Run

```bash
clarinet run                    # API + web UI
clarinet worker                 # start a worker for pipeline tasks
clarinet worker --queues gpu    # worker for GPU tasks only
```

## What You Get

- **Web UI** with auto-generated forms from data schemas, user/role management, task assignment, and progress tracking
- **REST API** with Swagger docs, httpOnly cookie authentication, and role-based access control
- **DICOM integration**: connect to PACS (C-FIND/C-GET/C-STORE), anonymize patients and studies
- **OHIF Viewer** for viewing DICOM images in the browser — a DICOMweb proxy with caching translates requests to a traditional PACS
- **3D Slicer integration**: automatic workspace setup per task, file loading, annotation validation, context hydrators for passing additional data to the Slicer environment
- **Distributed processing**: pipeline tasks on remote machines with queue routing (GPU, DICOM, default), automatic retries, and dead letter queues
- **RecordFlow**: event-driven workflow engine — automatic task creation, invalidation on data/file changes, pattern matching on fields, cascading reactions

## Example: Liver Metastasis Study

A real-world example in `examples/demo_liver_v2/` — a multi-modality study with 15+ record types:

1. Patient undergoes CT, MRI, CT angiography
2. Each study gets an initial assessment (`first-check`)
3. Segmentation tasks are automatically created for multiple doctors based on modality
4. The first completed CT segmentation becomes the master model
5. The master model is projected onto other modalities, results are compared automatically
6. Discrepancies trigger a second review for the specific doctor
7. MDK council classifies lesions → 3D modeling → resection planning → histology

This entire pipeline is described in ~100 lines of workflow and ~300 lines of record type definitions.

## Requirements

- Python 3.12+
- PostgreSQL or SQLite
- RabbitMQ (for pipeline workers, optional)
- 3D Slicer (for image annotation, optional)

## Getting Started

```bash
git clone https://github.com/radionest/clarinet.git && cd clarinet
make dev-setup
uv run clarinet db init
make run-dev
```

## Status

Clarinet is in **alpha**. The API, DSL, and configuration format are still evolving and **will** change. Use it for exploration and pilot studies, but expect breaking changes.

## License

MIT
