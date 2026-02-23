# DICOM Client

Асинхронный DICOM клиент для Query/Retrieve операций на основе pynetdicom.

## Возможности

- ✅ **C-FIND**: Поиск исследований, серий и изображений
- ✅ **C-GET**: Получение DICOM данных с сохранением на диск или в память
- ✅ **C-MOVE**: Перемещение данных на другой DICOM сервер
- ✅ **C-STORE**: Обработка входящих DICOM файлов (save to disk/memory/forward)
- ✅ **Async API**: Не блокирует event loop через `asyncio.to_thread()`
- ✅ **Type Safe**: Полная типизация с Pydantic моделями

## Архитектура

### Компоненты

```
src/services/dicom/
├── client.py       # DicomClient - async API
├── operations.py   # DicomOperations - sync wrappers для pynetdicom
├── handlers.py     # StorageHandler - обработка C-STORE
├── models.py       # Pydantic модели
└── __init__.py     # Public API exports
```

### Принципы дизайна

**KISS (Keep It Simple)**:
- Один основной класс `DicomClient` с простым API
- Минимум абстракций
- Прямое использование pynetdicom

**DRY (Don't Repeat Yourself)**:
- Переиспользуемые компоненты (handlers, parsers)
- Единая обработка ошибок
- Общая логика для всех Query/Retrieve операций

**YAGNI (You Aren't Gonna Need It)**:
- Только запрошенные операции
- Без излишних features
- Конфигурация через settings

### Интеграция с asyncio

pynetdicom - синхронная библиотека на основе потоков. Для интеграции с async кодом используется `asyncio.to_thread()`:

```python
# Async метод
async def find_studies(self, query, peer):
    # Запускаем синхронную операцию в отдельном потоке
    return await asyncio.to_thread(
        self._operations.find_studies,
        config,
        query
    )
```

Это предотвращает блокировку event loop.

## Установка

Установите зависимости:

```bash
pip install "clarinet[dicom]"
```

Или добавьте в `pyproject.toml`:

```toml
dependencies = [
    "pynetdicom>=2.0.2",
]
```

## Конфигурация

В `settings.py` или через environment variables:

```python
# DICOM settings
dicom_aet: str = "CLARINET"           # Calling AE Title
dicom_port: int = 11112               # SCP port (for server mode)
dicom_ip: str | None = None           # SCP IP address
dicom_max_pdu: int = 16384           # Maximum PDU size
```

Environment variables:

```bash
export CLARINET_DICOM_AET="MY_CLIENT"
export CLARINET_DICOM_MAX_PDU=32768
```

## Использование

### Базовый пример

```python
from pathlib import Path
from src.services.dicom import DicomClient, DicomNode, StudyQuery
from src.settings import settings

# Создать клиент
client = DicomClient(
    calling_aet=settings.dicom_aet,
    max_pdu=settings.dicom_max_pdu
)

# Определить PACS сервер
pacs = DicomNode(
    aet="PACS_SERVER",
    host="192.168.1.100",
    port=11112
)

# Найти исследования
studies = await client.find_studies(
    query=StudyQuery(patient_id="12345"),
    peer=pacs
)

# Скачать исследование на диск
result = await client.get_study(
    study_uid=studies[0].study_instance_uid,
    peer=pacs,
    output_dir=Path("/data/dicom")
)

print(f"Retrieved {result.num_completed} instances")
```

### C-FIND: Поиск исследований

```python
from src.services.dicom import StudyQuery

# Поиск по Patient ID
studies = await client.find_studies(
    query=StudyQuery(patient_id="12345"),
    peer=pacs
)

# Поиск по имени и дате
studies = await client.find_studies(
    query=StudyQuery(
        patient_name="Doe^John",
        study_date="20240101-20240131"
    ),
    peer=pacs
)

# Результаты
for study in studies:
    print(f"{study.study_instance_uid}")
    print(f"  Patient: {study.patient_name}")
    print(f"  Date: {study.study_date}")
    print(f"  Description: {study.study_description}")
    print(f"  Series: {study.number_of_study_related_series}")
```

### C-FIND: Поиск серий

```python
from src.services.dicom import SeriesQuery

series = await client.find_series(
    query=SeriesQuery(
        study_instance_uid="1.2.840.113619.2.1.1.1",
        modality="CT"
    ),
    peer=pacs
)

for s in series:
    print(f"Series {s.series_number}: {s.series_description}")
```

### C-GET: Получение на диск

```python
from pathlib import Path

# Получить исследование
result = await client.get_study(
    study_uid="1.2.840.113619.2.1.1.1",
    peer=pacs,
    output_dir=Path("/data/dicom/study1")
)

# Получить серию
result = await client.get_series(
    study_uid="1.2.840.113619.2.1.1.1",
    series_uid="1.2.840.113619.2.1.2.1",
    peer=pacs,
    output_dir=Path("/data/dicom/series1")
)

print(f"Completed: {result.num_completed}")
print(f"Failed: {result.num_failed}")
```

### C-GET: Получение в память

```python
# Получить в память для обработки
result = await client.get_study_to_memory(
    study_uid="1.2.840.113619.2.1.1.1",
    peer=pacs
)

# Обработать datasets
for ds in result.instances:
    print(f"Instance: {ds.SOPInstanceUID}")
    print(f"Modality: {ds.Modality}")
    # Обработка изображения...
```

### C-MOVE: Перемещение на другой сервер

```python
# Переместить исследование
result = await client.move_study(
    study_uid="1.2.840.113619.2.1.1.1",
    peer=pacs,
    destination_aet="DEST_PACS"
)

# Переместить серию
result = await client.move_series(
    study_uid="1.2.840.113619.2.1.1.1",
    series_uid="1.2.840.113619.2.1.2.1",
    peer=pacs,
    destination_aet="DEST_PACS"
)

print(f"Moved {result.num_completed} instances")
```

### Использование в FastAPI

```python
from fastapi import APIRouter, Depends
from src.services.dicom import DicomClient, DicomNode, StudyQuery
from src.settings import settings

router = APIRouter(prefix="/dicom", tags=["DICOM"])

def get_dicom_client() -> DicomClient:
    """Dependency for DICOM client."""
    return DicomClient(
        calling_aet=settings.dicom_aet,
        max_pdu=settings.dicom_max_pdu
    )

@router.get("/studies/{patient_id}")
async def find_patient_studies(
    patient_id: str,
    client: DicomClient = Depends(get_dicom_client)
):
    """Find all studies for patient."""
    pacs = DicomNode(
        aet="PACS_SERVER",
        host="192.168.1.100",
        port=11112
    )

    studies = await client.find_studies(
        query=StudyQuery(patient_id=patient_id),
        peer=pacs
    )

    return {"studies": studies}

@router.post("/studies/{study_uid}/retrieve")
async def retrieve_study(
    study_uid: str,
    client: DicomClient = Depends(get_dicom_client)
):
    """Retrieve study to local storage."""
    pacs = DicomNode(
        aet="PACS_SERVER",
        host="192.168.1.100",
        port=11112
    )

    output_dir = Path(settings.storage_path) / "dicom" / study_uid

    result = await client.get_study(
        study_uid=study_uid,
        peer=pacs,
        output_dir=output_dir
    )

    return {
        "status": result.status,
        "completed": result.num_completed,
        "failed": result.num_failed
    }
```

## Модели данных

### StudyQuery

Параметры поиска исследований:

```python
StudyQuery(
    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str | None = None
    study_date: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    modality: str | None = None
)
```

### StudyResult

Результат поиска исследования:

```python
StudyResult(
    patient_id: str | None
    patient_name: str | None
    study_instance_uid: str
    study_date: str | None
    study_time: str | None
    study_description: str | None
    accession_number: str | None
    modalities_in_study: str | None
    number_of_study_related_series: int | None
    number_of_study_related_instances: int | None
)
```

### RetrieveResult

Результат C-GET/C-MOVE операции:

```python
RetrieveResult(
    status: str                          # "success", "pending", "warning_0x..."
    num_remaining: int = 0               # Оставшиеся
    num_completed: int = 0               # Завершённые
    num_failed: int = 0                  # Неудачные
    num_warning: int = 0                 # С предупреждениями
    failed_sop_instances: list[str] = [] # Список неудачных
    instances: list[Dataset] = []        # Datasets (для memory mode)
)
```

### DicomNode

Конфигурация DICOM узла:

```python
DicomNode(
    aet: str        # AE Title
    host: str       # IP address
    port: int       # Port number
)
```

## Обработка ошибок

Клиент использует стандартные исключения из `src.exceptions.http`:

```python
from src.exceptions.http import CONFLICT, NOT_FOUND

try:
    studies = await client.find_studies(query, peer)
except CONFLICT as e:
    # Ошибка установки ассоциации
    logger.error(f"Failed to connect to PACS: {e}")
```

## Логирование

Клиент использует логирование через `src.utils.logger`:

```python
from src.utils.logger import logger

# Логи автоматически:
# INFO: "Searching studies on PACS@192.168.1.100:11112"
# INFO: "Found 5 studies"
# INFO: "Retrieved study: 100 completed, 0 failed"
```

## Производительность

### Оптимизация PDU

Для больших объёмов данных увеличьте PDU:

```python
client = DicomClient(
    calling_aet="CLARINET",
    max_pdu=0  # Unlimited PDU size
)
```

### Параллельные операции

Используйте `asyncio.gather()` для параллельных запросов:

```python
# Найти серии в нескольких исследованиях параллельно
results = await asyncio.gather(
    client.find_series(SeriesQuery(study_instance_uid=uid1), pacs),
    client.find_series(SeriesQuery(study_instance_uid=uid2), pacs),
    client.find_series(SeriesQuery(study_instance_uid=uid3), pacs),
)
```

### Таймауты

Настройте таймауты для длительных операций:

```python
# Большой таймаут для скачивания большого исследования
result = await client.get_study(
    study_uid=uid,
    peer=pacs,
    output_dir=output_dir,
    timeout=600.0  # 10 минут
)
```

## Ограничения

1. **Синхронная библиотека**: pynetdicom работает на потоках, не на asyncio
2. **C-STORE Server**: Для приёма входящих данных нужен отдельный SCP
3. **Storage Commitment**: Не реализовано
4. **Worklist (C-FIND MWL)**: Не реализовано

## Roadmap

- [ ] C-STORE SCP (Storage Server)
- [ ] Worklist Query (C-FIND MWL)
- [ ] Storage Commitment
- [ ] Query/Retrieve на других уровнях (Patient, Image)
- [ ] Batch операции
- [ ] Retry mechanism
- [ ] Progress callbacks

## См. также

- [pynetdicom документация](https://pydicom.github.io/pynetdicom/)
- [DICOM Standard](https://www.dicomstandard.org/)
- [Examples](../examples/dicom_client_example.py)
