# План рефакторинга сервисного слоя

## Текущие проблемы

### 1. Утечка деталей ORM в сервисы
- `StudyService` напрямую использует `session.refresh()` и `session.execute()`
- Сервисы знают о внутренней структуре репозиториев
- SQLAlchemy-специфичные конструкции в бизнес-логике

### 2. Смешивание I/O операций
- Прямое чтение файлов через `aiofiles` в `StudyService._generate_anonymous_name()`
- I/O операции не абстрагированы

## План рефакторинга (KISS, YAGNI, DRY)

### Этап 1: Инкапсуляция ORM в репозиториях (Приоритет: ВЫСОКИЙ)

#### 1.1 StudyRepository - добавить недостающие методы
```python
# src/repositories/study_repository.py

async def get_with_series(self, study_uid: str) -> Study:
    """Получить study с загруженными series."""
    study = await self.get(study_uid)
    await self.session.refresh(study, ["series"])
    return study
```

#### 1.2 SeriesRepository - добавить метод get_random
```python
# src/repositories/series_repository.py

async def get_random(self) -> Series:
    """Получить случайную серию."""
    from sqlmodel import func, select
    
    statement = select(Series).order_by(func.random()).limit(1)
    result = await self.session.execute(statement)
    series = result.scalars().first()
    
    if not series:
        raise NOT_FOUND.with_context("No series found")
    
    return series
```

#### 1.3 Обновить StudyService
```python
# Заменить прямые обращения к session:

# БЫЛО (строка 198):
await self.study_repo.session.refresh(study, ["series"])

# СТАЛО:
study = await self.study_repo.get_with_series(study_uid)

# БЫЛО (строки 279-286):
statement = select(Series).order_by(func.random()).limit(1)
result = await self.series_repo.session.execute(statement)
# ...

# СТАЛО:
return await self.series_repo.get_random()
```

### Этап 2: Абстрагирование I/O операций (Приоритет: СРЕДНИЙ)

#### 2.1 Создать простой провайдер имен
```python
# src/services/providers/anonymous_name_provider.py

class AnonymousNameProvider:
    """Провайдер анонимных имен."""
    
    def __init__(self, names_file_path: str | None = None):
        self.names_file_path = names_file_path
        self._names_cache: list[str] | None = None
    
    async def get_available_names(self) -> list[str]:
        """Получить список доступных имен."""
        if not self.names_file_path:
            return []
        
        if self._names_cache is None:
            await self._load_names()
        
        return self._names_cache or []
    
    async def _load_names(self) -> None:
        """Загрузить имена из файла."""
        try:
            async with aiofiles.open(self.names_file_path) as f:
                content = await f.read()
                self._names_cache = content.strip().split("\n")
        except Exception:
            self._names_cache = []
```

#### 2.2 Обновить StudyService
```python
# src/services/study_service.py

def __init__(
    self,
    study_repo: StudyRepository,
    patient_repo: PatientRepository,
    series_repo: SeriesRepository,
    name_provider: AnonymousNameProvider | None = None,
):
    self.study_repo = study_repo
    self.patient_repo = patient_repo
    self.series_repo = series_repo
    self.name_provider = name_provider or AnonymousNameProvider(settings.anon_names_list)

async def _generate_anonymous_name(self, patient: Patient) -> str | None:
    """Генерация анонимного имени."""
    # Получаем имена через провайдер
    anon_names_list = await self.name_provider.get_available_names()
    
    if anon_names_list:
        available_names = []
        for name in anon_names_list:
            name = name.strip()
            if name and not await self.patient_repo.exists_anon_name(name):
                available_names.append(name)
        
        if available_names:
            return random.choice(available_names)
    
    # Fallback к auto-generated
    return f"{settings.anon_id_prefix}_{patient.auto_id}"
```

### Этап 4: Исправления в методе find_series (Приоритет: ВЫСОКИЙ)

#### 4.1 Перенести логику поиска в SeriesRepository
```python
# src/repositories/series_repository.py

async def find_by_criteria(self, find_query: SeriesFind) -> list[Series]:
    """Найти серии по критериям."""
    statement = select(Series)
    
    # Применяем фильтры
    for query_key, query_value in find_query.model_dump(
        exclude_none=True, exclude_defaults=True, exclude={"tasks"}
    ).items():
        if hasattr(Series, query_key):
            if query_value == "*":
                statement = statement.where(getattr(Series, query_key).isnot(None))
            else:
                statement = statement.where(getattr(Series, query_key) == query_value)
    
    # Task-related фильтры
    if find_query.tasks:
        statement = statement.join(Task, isouter=True)
        statement = statement.join(TaskDesign, isouter=True)
    
    result = await self.session.execute(statement.distinct())
    return list(result.scalars().all())
```

#### 4.2 Упростить StudyService.find_series
```python
async def find_series(self, find_query: SeriesFind) -> list[Series]:
    """Найти серии по критериям."""
    return await self.series_repo.find_by_criteria(find_query)
```

## Принципы применения

### KISS (Keep It Simple, Stupid)
- Не создаем сложные абстракции без необходимости
- AnonymousNameProvider - простой класс с одной ответственностью
- Не вводим интерфейсы/протоколы пока не нужны

### YAGNI (You Aren't Gonna Need It)
- RoleService создаем только при расширении функционала
- Не создаем фабрики и сложные паттерны
- Не делаем универсальные решения "на будущее"

### DRY (Don't Repeat Yourself)
- Логика работы с session только в репозиториях
- Общие операции в BaseRepository
- Переиспользование существующих методов

## Метрики успеха

1. **Отсутствие прямых обращений к session в сервисах**
2. **I/O операции инкапсулированы в провайдерах**
3. **Каждый класс имеет одну ответственность**
4. **Тесты проходят без изменений**
5. **Код легче тестировать (можно мокать зависимости)**

## Риски и митигация

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Регрессия функционала | Низкая | Покрытие тестами перед рефакторингом |
| Усложнение кода | Низкая | Следование KISS, ревью на каждом этапе |
| Производительность | Очень низкая | Профилирование критических путей |
