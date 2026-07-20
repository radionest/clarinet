# Workflow diagram — NDT comparative defect-detection study

## Detection phase

```mermaid
flowchart TD
    %% Start of process
    Start([Part: 5 modalities + baseline archive]) --> QA[Quality assessment and reference-series selection]
    QA -->|2 independent assessments| QA_Dec{Suitable?}
    QA_Dec -->|No| Reject[Study rejected]
    QA_Dec -->|Yes| Anon[Anonymization]

    %% Segmentation
    Anon --> Seg[Defect segmentation — 2 inspectors independently]

    %% Master model
    Seg -->|CT + archive completed first| MM_Create[Expert: master model creation]
    MM_Create --> MM_Ready[Master model ready]

    %% Loop for each modality
    subgraph Loop ["For each modality (CT, UT, CT-HD, UT-HD, MCT)"]
        direction TB
        Proj[Expert: master model projection] --> Comp[Automatic comparison of projection and segmentation]
        Comp --> Result{Comparison result}

        Result -->|Additional defects| Update_MM[Expert: master model update]
        Result -->|Missed defects| Review[Second review: classify missed defects]
        Result -->|No discrepancies| Done[Modality complete]

        Review --> Done
    end

    MM_Ready --> Proj
    Seg --> Comp

    Update_MM -->|Invalidate all projections| MM_Ready

    Done --> Final{All modalities complete?}
    Final -->|Yes| Characterization[Retrospective characterization — blind-reassessment interval 4–7 weeks]
    Characterization --> DiagComplete([Detection phase complete])

    %% Styles
    classDef automatic fill:#e1f5ff,stroke:#0066cc,stroke-width:2px
    classDef manual fill:#fff4e1,stroke:#ff9900,stroke-width:2px
    classDef expert fill:#ffe1f5,stroke:#cc0066,stroke-width:2px
    classDef decision fill:#f0f0f0,stroke:#666,stroke-width:2px

    class Anon,Comp automatic
    class QA,Seg,Review,Characterization manual
    class MM_Create,Proj,Update_MM expert
    class QA_Dec,Result,Final decision
```

## Repair phase

```mermaid
flowchart TD
    DiagComplete([Detection phase complete]) --> MRB[MRB: classify all defects]
    MRB --> RepairModel[Expert: 3D repair model]
    RepairModel --> RepairPlan[Expert: repair plan — repair clusters]
    RepairPlan --> RepairReport[Technician: repair report]
    RepairReport --> Repair[Repair operation — in-process UT]

    Repair --> RepairResult{Additional defects during repair?}
    RepairResult -->|Yes| UpdateMM[Master model update]
    RepairResult -->|No| PostRepair

    UpdateMM --> PostRepair[Post-repair CT]
    PostRepair --> Metallo[Metallography — macro + microscopy]
    Metallo --> Complete([Study complete])

    %% Styles
    classDef automatic fill:#e1f5ff,stroke:#0066cc,stroke-width:2px
    classDef manual fill:#fff4e1,stroke:#ff9900,stroke-width:2px
    classDef expert fill:#ffe1f5,stroke:#cc0066,stroke-width:2px
    classDef decision fill:#f0f0f0,stroke:#666,stroke-width:2px

    class MRB,Repair,PostRepair,Metallo manual
    class RepairModel,RepairPlan,UpdateMM expert
    class RepairReport manual
    class RepairResult decision
```

## Legend

- **Blue** (light blue fill) — automatic processes
- **Orange** (yellow fill) — manual processes (inspectors)
- **Pink** — expert tasks
- **Gray** — decision points

## Key workflow features

1. **Parallel modality processing**: all 5 modalities are processed independently
2. **Update loops**: when additional defects are found, the master model is updated and all projections are invalidated
3. **Hash check**: on projection completion, the master model's currency is verified
4. **Double independent assessment**: each segmentation is performed by two inspectors independently
5. **Second review**: separates method limitation (invisible defect) from observer error (missed visible defect)
6. **Blind-reassessment interval**: retrospective characterization is separated from segmentation by a 4–7 week interval
7. **End-to-end master model**: updated at every stage — from detection through in-process repair findings
