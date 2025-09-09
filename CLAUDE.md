# CLAUDE.md - Руководство по стилю кода и best practices

## Общие принципы

### Архитектура
- Следуй принципам SOLID и DRY
- Используй модульную архитектуру с четким разделением ответственности
- Предпочитай композицию наследованию
- Каждый модуль должен иметь единственную цель

### Асинхронное программирование
- Используй `async/await` для всех I/O операций
- Избегай блокирующих вызовов в асинхронном коде
- Используй `asyncio.gather()` для параллельного выполнения независимых задач
- Всегда обрабатывай исключения в асинхронных функциях

## Стиль кода

### Именование
- Классы: `PascalCase` (например, `GeminiClient`)
- Функции и переменные: `snake_case` (например, `analyze_message`)
- Константы: `UPPER_SNAKE_CASE` (например, `MIN_MESSAGE_LENGTH`)
- Приватные методы: с префиксом `_` (например, `_process_data`)

### Импорты
```python
# Стандартная библиотека
import asyncio
import sys
from typing import Optional, List, Dict

# Сторонние библиотеки
from aiogram import Router, F
from loguru import logger

# Локальные модули
from config import settings
from utils.logger import logger
```

### Типизация
- Используй type hints для всех функций
- Применяй `Optional[]` для значений, которые могут быть None
- Используй `Union[]` осторожно, предпочитай более специфичные типы
- Для сложных структур создавай TypedDict или dataclass

```python
from typing import Optional, List, Dict
from dataclasses import dataclass

@dataclass
class AnalysisResult:
    text: str
    confidence: float
    response_type: str

async def analyze_message(text: str, context: Optional[Dict[str, str]] = None) -> AnalysisResult:
    ...
```

## Best Practices

### Обработка ошибок
```python
# Плохо
try:
    result = await some_operation()
except:
    pass

# Хорошо
try:
    result = await some_operation()
except SpecificException as e:
    logger.error(f"Ошибка при выполнении операции: {e}")
    # Обработка или повторный выброс
    raise
```

- Не оборачивай большие блоки кода в try-except. В блоке try должно быть не более двух вызовов функций.

### Логирование
- Используй loguru для всего логирования
- Логируй на соответствующих уровнях:
  - `DEBUG`: детальная информация для отладки
  - `INFO`: важные события в нормальном потоке
  - `WARNING`: неожиданные события, которые не прерывают работу
  - `ERROR`: ошибки, требующие внимания
  - `CRITICAL`: критические ошибки, угрожающие работе системы

```python
logger.debug(f"Обработка сообщения длиной {len(message)} символов")
logger.info(f"Пользователь {user_id} отправил команду {command}")
logger.warning(f"API лимит близок к исчерпанию: {remaining} запросов")
logger.error(f"Не удалось подключиться к API: {error}")
```

### Конфигурация
- Все настройки выноси в отдельный модуль config
- Используй переменные окружения для секретов
- Валидируй конфигурацию при запуске
- Предоставляй разумные значения по умолчанию

```python
# config/settings.py
import os
from typing import List

# Обязательные переменные
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY must be set")

# Опциональные с дефолтами
TIMEOUT: int = int(os.getenv("TIMEOUT", "30"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
```

### Работа с внешними API
```python
class APIClient:
    def __init__(self, timeout: int = 30, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        
    async def make_request(self, endpoint: str, data: Dict) -> Dict:
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        endpoint, 
                        json=data, 
                        timeout=aiohttp.ClientTimeout(total=self.timeout)
                    ) as response:
                        response.raise_for_status()
                        return await response.json()
            except aiohttp.ClientError as e:
                logger.warning(f"Попытка {attempt + 1}/{self.max_retries} не удалась: {e}")
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

### Структура проекта
```
src/
├── __init__.py
├── main.py              # Точка входа
├── config/
│   ├── __init__.py
│   └── settings.py      # Конфигурация
├── models/              # Модели данных
│   ├── __init__.py
│   └── ...
├── services/            # Бизнес-логика
│   ├── __init__.py
│   └── ...
├── handlers/            # Обработчики событий
│   ├── __init__.py
│   └── ...
└── utils/               # Вспомогательные утилиты
    ├── __init__.py
    └── ...
```

### Тестирование
- Пиши тесты для критической бизнес-логики
- Используй pytest и pytest-asyncio для асинхронных тестов
- Мокай внешние зависимости
- Стремись к покрытию >80% для основной логики

```python
# tests/test_analyzer.py
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_analyze_message():
    client = AsyncMock()
    client.analyze.return_value = "Test response"
    
    result = await analyze_message("test", client)
    assert result == "Test response"
    client.analyze.assert_called_once_with("test")
```

### Документация
- Используй docstrings для всех публичных функций и классов
- Пиши комментарии на английском языке
- Следуй Google Style для docstrings
- Документируй сложную логику inline комментариями
- Поддерживай README.md актуальным

```python
async def process_message(message: str, user_id: int) -> Optional[str]:
    """Обрабатывает входящее сообщение от пользователя.
    
    Args:
        message: Текст сообщения для обработки
        user_id: ID пользователя отправителя
        
    Returns:
        Обработанный ответ или None, если обработка не требуется
        
    Raises:
        ValueError: Если сообщение пустое
        APIError: При ошибке внешнего API
    """
    if not message:
        raise ValueError("Message cannot be empty")
    
    # Фильтрация по бизнес-правилам
    if not should_process(message):
        return None
        
    return await api_client.process(message, user_id)
```

### Производительность
- Используй кеширование для частых операций
- Применяй connection pooling для БД и HTTP
- Избегай N+1 запросов
- Профилируй критические участки кода

### Безопасность
- Никогда не логируй секретные данные
- Валидируй все входные данные
- Используй параметризованные запросы для БД
- Применяй rate limiting для API endpoints
- Обновляй зависимости регулярно

### Git workflow
- Делай атомарные коммиты с понятными сообщениями
- Используй conventional commits (feat:, fix:, docs:, etc.)
- Не коммить сгенерированные файлы и секреты
- Пиши содержательные PR описания

## Анти-паттерны которых следует избегать

- God objects - классы с слишком большой ответственностью
- Callback hell - используй async/await вместо callbacks
- Глобальные переменные - используй dependency injection
- Магические числа - выноси в константы
- Дублирование кода - выноси в функции/классы
- Игнорирование ошибок - всегда обрабатывай исключения
- Hardcoded конфигурация - используй переменные окружения