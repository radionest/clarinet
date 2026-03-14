# Workflow диаграмма исследования метастазов печени

```mermaid
flowchart TD
    %% Начало процесса
    Start([Пациент: 5 модальностей + КТ-архив]) --> QA1[Оценка качества]

    %% Оценка качества
    QA1 -->|2 независимые оценки| QA2{Пригодность?}
    QA2 -->|Нет| Reject[Исследование отклонено]
    QA2 -->|Да| QA3[Определение эталонной серии]

    %% Анонимизация
    QA3 --> Anon[Анонимизация автоматически]

    %% Разделение по модальностям
    Anon --> Split{Распределение по модальностям}

    %% КТ (первичное)
    Split -->|КТ изолированное| CT1[Сегментация КТ]
    CT1 -->|2 врача независимо| CT1_Done

    %% КТ с архивом - строит мастер-модель
    Split -->|КТ + архив| CT2[Сегментация КТ + архив]
    CT2 -->|2 врача независимо| CT2_Done[КТ+архив завершено]
    CT2_Done -->|Первая завершённая| MM_Create[Эксперт: создание мастер-модели]

    %% Другие модальности
    Split -->|МРТ| MRI[Сегментация МРТ]
    Split -->|КТ-АГ| CTAG[Сегментация КТ-АГ]
    Split -->|МРТ-АГ| MRIAG[Сегментация МРТ-АГ]
    Split -->|ПДКТ-АГ| PETCT[Сегментация ПДКТ-АГ]

    MRI -->|2 врача независимо| MRI_Done
    CTAG -->|2 врача независимо| CTAG_Done
    MRIAG -->|2 врача независимо| MRIAG_Done
    PETCT -->|2 врача независимо| PETCT_Done

    %% Создание проекций для всех модальностей
    MM_Create --> MM_Ready[Мастер-модель готова]

    MM_Ready --> Proj_CT1[Проекция для КТ изолированного]
    MM_Ready --> Proj_MRI[Проекция для МРТ]
    MM_Ready --> Proj_CTAG[Проекция для КТ-АГ]
    MM_Ready --> Proj_MRIAG[Проекция для МРТ-АГ]
    MM_Ready --> Proj_PETCT[Проекция для ПДКТ-АГ]

    %% Проверка hash при создании проекций
    Proj_CT1 -->|Проверка hash мастер-модели| Proj_CT1_Check{Hash совпадает?}
    Proj_MRI -->|Проверка hash| Proj_MRI_Check{Hash совпадает?}
    Proj_CTAG -->|Проверка hash| Proj_CTAG_Check{Hash совпадает?}
    Proj_MRIAG -->|Проверка hash| Proj_MRIAG_Check{Hash совпадает?}
    Proj_PETCT -->|Проверка hash| Proj_PETCT_Check{Hash совпадает?}

    Proj_CT1_Check -->|Нет| Proj_CT1
    Proj_MRI_Check -->|Нет| Proj_MRI
    Proj_CTAG_Check -->|Нет| Proj_CTAG
    Proj_MRIAG_Check -->|Нет| Proj_MRIAG
    Proj_PETCT_Check -->|Нет| Proj_PETCT

    Proj_CT1_Check -->|Да| Proj_CT1_Ready
    Proj_MRI_Check -->|Да| Proj_MRI_Ready
    Proj_CTAG_Check -->|Да| Proj_CTAG_Ready
    Proj_MRIAG_Check -->|Да| Proj_MRIAG_Ready
    Proj_PETCT_Check -->|Да| Proj_PETCT_Ready

    %% Сравнение проекций с сегментациями
    CT1_Done --> Compare_CT1{Ожидание проекции КТ}
    Proj_CT1_Ready --> Compare_CT1
    Compare_CT1 --> Comp_CT1[Автоматическое сравнение]

    MRI_Done --> Compare_MRI{Ожидание проекции МРТ}
    Proj_MRI_Ready --> Compare_MRI
    Compare_MRI --> Comp_MRI[Автоматическое сравнение]

    CTAG_Done --> Compare_CTAG{Ожидание проекции КТ-АГ}
    Proj_CTAG_Ready --> Compare_CTAG
    Compare_CTAG --> Comp_CTAG[Автоматическое сравнение]

    MRIAG_Done --> Compare_MRIAG{Ожидание проекции МРТ-АГ}
    Proj_MRIAG_Ready --> Compare_MRIAG
    Compare_MRIAG --> Comp_MRIAG[Автоматическое сравнение]

    PETCT_Done --> Compare_PETCT{Ожидание проекции ПДКТ-АГ}
    Proj_PETCT_Ready --> Compare_PETCT
    Compare_PETCT --> Comp_PETCT[Автоматическое сравнение]

    %% Анализ результатов сравнения
    Comp_CT1 --> Analysis_CT1{Результат КТ}
    Comp_MRI --> Analysis_MRI{Результат МРТ}
    Comp_CTAG --> Analysis_CTAG{Результат КТ-АГ}
    Comp_MRIAG --> Analysis_MRIAG{Результат МРТ-АГ}
    Comp_PETCT --> Analysis_PETCT{Результат ПДКТ-АГ}

    %% Обработка дополнительных очагов
    Analysis_CT1 -->|Дополнительные очаги| Update_MM
    Analysis_MRI -->|Дополнительные очаги| Update_MM
    Analysis_CTAG -->|Дополнительные очаги| Update_MM
    Analysis_MRIAG -->|Дополнительные очаги| Update_MM
    Analysis_PETCT -->|Дополнительные очаги| Update_MM

    Update_MM[Эксперт: обновление мастер-модели]
    Update_MM -->|Инвалидация всех проекций| MM_Ready

    %% Обработка пропущенных очагов
    Analysis_CT1 -->|Пропущенные очаги| Review_CT1[Пересмотр КТ]
    Analysis_MRI -->|Пропущенные очаги| Review_MRI[Пересмотр МРТ]
    Analysis_CTAG -->|Пропущенные очаги| Review_CTAG[Пересмотр КТ-АГ]
    Analysis_MRIAG -->|Пропущенные очаги| Review_MRIAG[Пересмотр МРТ-АГ]
    Analysis_PETCT -->|Пропущенные очаги| Review_PETCT[Пересмотр ПДКТ-АГ]

    Review_CT1 --> Review_CT1_Done[Классификация очагов]
    Review_MRI --> Review_MRI_Done[Классификация очагов]
    Review_CTAG --> Review_CTAG_Done[Классификация очагов]
    Review_MRIAG --> Review_MRIAG_Done[Классификация очагов]
    Review_PETCT --> Review_PETCT_Done[Классификация очагов]

    %% Завершение
    Analysis_CT1 -->|Расхождений нет| Done_CT1[КТ завершено]
    Analysis_MRI -->|Расхождений нет| Done_MRI[МРТ завершено]
    Analysis_CTAG -->|Расхождений нет| Done_CTAG[КТ-АГ завершено]
    Analysis_MRIAG -->|Расхождений нет| Done_MRIAG[МРТ-АГ завершено]
    Analysis_PETCT -->|Расхождений нет| Done_PETCT[ПДКТ-АГ завершено]

    Review_CT1_Done --> Done_CT1
    Review_MRI_Done --> Done_MRI
    Review_CTAG_Done --> Done_CTAG
    Review_MRIAG_Done --> Done_MRIAG
    Review_PETCT_Done --> Done_PETCT

    Done_CT1 --> Final{Все модальности завершены?}
    Done_MRI --> Final
    Done_CTAG --> Final
    Done_MRIAG --> Final
    Done_PETCT --> Final

    Final -->|Да| Complete([Исследование завершено])
    Final -->|Нет| Wait[Ожидание других модальностей]

    %% Стили
    classDef automatic fill:#e1f5ff,stroke:#0066cc,stroke-width:2px
    classDef manual fill:#fff4e1,stroke:#ff9900,stroke-width:2px
    classDef expert fill:#ffe1f5,stroke:#cc0066,stroke-width:2px
    classDef decision fill:#f0f0f0,stroke:#666,stroke-width:2px

    class Anon,Comp_CT1,Comp_MRI,Comp_CTAG,Comp_MRIAG,Comp_PETCT automatic
    class QA1,QA3,CT1,CT2,MRI,CTAG,MRIAG,PETCT,Review_CT1,Review_MRI,Review_CTAG,Review_MRIAG,Review_PETCT manual
    class MM_Create,Proj_CT1,Proj_MRI,Proj_CTAG,Proj_MRIAG,Proj_PETCT,Update_MM expert
    class QA2,Split,Analysis_CT1,Analysis_MRI,Analysis_CTAG,Analysis_MRIAG,Analysis_PETCT,Final decision
```

## Легенда

- **Синий** (голубой фон) — автоматические процессы
- **Оранжевый** (желтый фон) — ручные процессы (врачи)
- **Розовый** — задачи эксперта
- **Серый** — точки принятия решений

## Ключевые особенности workflow

1. **Параллельная обработка модальностей**: все 5 модальностей (КТ, МРТ, КТ-АГ, МРТ-АГ, ПДКТ-АГ) обрабатываются независимо

2. **Циклы обновления**: при обнаружении дополнительных очагов мастер-модель обновляется, и все проекции инвалидируются

3. **Проверка hash**: при завершении создания проекции проверяется, не изменилась ли мастер-модель

4. **Двойная независимая оценка**: каждая сегментация выполняется двумя врачами независимо

5. **Пересмотр**: врачи пересматривают пропущенные очаги после сравнения с проекцией
