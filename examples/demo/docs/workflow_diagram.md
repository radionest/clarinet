# Workflow диаграмма исследования метастазов печени

## Диагностический этап

```mermaid
flowchart TD
    %% Начало процесса
    Start([Пациент: 5 модальностей + КТ-архив]) --> QA[Оценка качества и определение эталонной серии]
    QA -->|2 независимые оценки| QA_Dec{Пригодность?}
    QA_Dec -->|Нет| Reject[Исследование отклонено]
    QA_Dec -->|Да| Anon[Анонимизация]

    %% Сегментация
    Anon --> Seg[Сегментация очагов — 2 врача независимо]

    %% Мастер-модель
    Seg -->|КТ + архив завершено первым| MM_Create[Эксперт: создание мастер-модели]
    MM_Create --> MM_Ready[Мастер-модель готова]

    %% Цикл для каждой модальности
    subgraph Loop ["Для каждой модальности (КТ, МРТ, КТ-АГ, МРТ-АГ, ПДКТ-АГ)"]
        direction TB
        Proj[Эксперт: проекция мастер-модели] --> Comp[Автоматическое сравнение проекции и сегментации]
        Comp --> Result{Результат сравнения}

        Result -->|Доп. очаги| Update_MM[Эксперт: обновление мастер-модели]
        Result -->|Пропущенные очаги| Review[Пересмотр: классификация пропущенных очагов]
        Result -->|Расхождений нет| Done[Модальность завершена]

        Review --> Done
    end

    MM_Ready --> Proj
    Seg --> Comp

    Update_MM -->|Инвалидация всех проекций| MM_Ready

    Done --> Final{Все модальности завершены?}
    Final -->|Да| Semiotics[Ретроспективная оценка семиотических признаков — washout 4–7 недель]
    Semiotics --> DiagComplete([Диагностический этап завершён])

    %% Стили
    classDef automatic fill:#e1f5ff,stroke:#0066cc,stroke-width:2px
    classDef manual fill:#fff4e1,stroke:#ff9900,stroke-width:2px
    classDef expert fill:#ffe1f5,stroke:#cc0066,stroke-width:2px
    classDef decision fill:#f0f0f0,stroke:#666,stroke-width:2px

    class Anon,Comp automatic
    class QA,Seg,Review,Semiotics manual
    class MM_Create,Proj,Update_MM expert
    class QA_Dec,Result,Final decision
```

## Хирургический этап

```mermaid
flowchart TD
    DiagComplete([Диагностический этап завершён]) --> MDK[МДК: классификация всех очагов]
    MDK --> ResModel[Эксперт: 3D-модель резекции]
    ResModel --> ResPlan[Эксперт: план резекции — кластеры удаления]
    ResPlan --> ResReport[Хирург: протокол резекции]
    ResReport --> Surgery[Операция — интраоперационное УЗИ]

    Surgery --> SurgResult{Доп. очаги на операции?}
    SurgResult -->|Да| UpdateMM[Обновление мастер-модели]
    SurgResult -->|Нет| PostOp

    UpdateMM --> PostOp[Послеоперационная КТ]
    PostOp --> Histo[Гистология — макро + микроскопия]
    Histo --> Complete([Исследование завершено])

    %% Стили
    classDef automatic fill:#e1f5ff,stroke:#0066cc,stroke-width:2px
    classDef manual fill:#fff4e1,stroke:#ff9900,stroke-width:2px
    classDef expert fill:#ffe1f5,stroke:#cc0066,stroke-width:2px
    classDef decision fill:#f0f0f0,stroke:#666,stroke-width:2px

    class MDK,Surgery,PostOp,Histo manual
    class ResModel,ResPlan,UpdateMM expert
    class ResReport manual
    class SurgResult decision
```

## Легенда

- **Синий** (голубой фон) — автоматические процессы
- **Оранжевый** (жёлтый фон) — ручные процессы (врачи)
- **Розовый** — задачи эксперта
- **Серый** — точки принятия решений

## Ключевые особенности workflow

1. **Параллельная обработка модальностей**: все 5 модальностей обрабатываются независимо
2. **Циклы обновления**: при обнаружении дополнительных очагов мастер-модель обновляется и все проекции инвалидируются
3. **Проверка hash**: при завершении проекции проверяется актуальность мастер-модели
4. **Двойная независимая оценка**: каждая сегментация выполняется двумя врачами независимо
5. **Пересмотр**: разделяет ограничение метода (невидимый очаг) и ошибку наблюдателя (пропущенный видимый очаг)
6. **Washout-период**: ретроспективная семиотика отделена от сегментации интервалом 4–7 недель
7. **Сквозная мастер-модель**: обновляется на всех этапах — от диагностики до интраоперационных находок
