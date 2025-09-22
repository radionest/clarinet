# Подробное руководство по рефакторингу с `use` в Gleam

## Как работает `use` синтаксис

`use` - это синтаксический сахар в Gleam, который превращает вложенные callback'и в линейный код.

**Трансформация:**
```gleam
// Код с use:
use result <- some_function(arg)
process(result)

// Превращается в:
some_function(arg, fn(result) {
  process(result)
})
```

## Пошаговая инструкция рефакторинга

### Шаг 1: Понимание текущей структуры
```gleam
// Сейчас у вас:
fetch.send(req)
|> promise.await(fn(resp_result) {     // Уровень 1
  case resp_result {                    // Уровень 2
    Error(_) -> ...
    Ok(response) -> {
      case response.status {            // Уровень 3
        200 -> {
          fetch.read_text_body(response)
          |> promise.map(fn(body_result) { // Уровень 4
            case body_result {           // Уровень 5
              // ...
            }
          })
        }
      }
    }
  }
})
```

### Шаг 2: Замена promise.await на use
```gleam
// Вместо:
fetch.send(req)
|> promise.await(fn(resp_result) {
  // обработка resp_result
})

// Пишем:
use resp_result <- promise.await(fetch.send(req))
// обработка resp_result продолжается линейно
```

### Шаг 3: Обработка ранних выходов
```gleam
// Для ранних возвратов используем return:
use fetch_result <- promise.await(fetch.send(req))

// Ранний выход при ошибке
let response = case fetch_result {
  Error(fetch.NetworkError(msg)) ->
    return promise.resolve(Error(types.NetworkError(msg)))
  Error(_) ->
    return promise.resolve(Error(types.NetworkError("Request failed")))
  Ok(resp) -> resp  // Продолжаем с успешным результатом
}
```

### Шаг 4: Вложенные use для цепочки операций
```gleam
// Для последовательных асинхронных операций:
use response <- promise.await(fetch.send(req))
use body_result <- promise.await(fetch.read_text_body(response))
// Теперь работаем с body_result
```

### Шаг 5: Извлечение вспомогательных функций
```gleam
// Выносим повторяющуюся логику:
fn parse_json_body(body: String) -> Result(Dynamic, ApiError) {
  case json.parse(body, decode.dynamic) {
    Ok(data) -> Ok(data)
    Error(_) -> Error(types.ParseError("Invalid JSON"))
  }
}
```

## Полный пример рефакторинга маленькой функции

**До:**
```gleam
pub fn example(id: Int) -> Promise(Result(String, Error)) {
  fetch_data(id)
  |> promise.await(fn(result) {
    case result {
      Error(e) -> promise.resolve(Error(e))
      Ok(data) -> {
        process_data(data)
        |> promise.map(fn(processed) {
          case processed {
            Error(e) -> Error(e)
            Ok(value) -> Ok(format_output(value))
          }
        })
      }
    }
  })
}
```

**После:**
```gleam
pub fn example(id: Int) -> Promise(Result(String, Error)) {
  use data <- promise.try_await(fetch_data(id))
  use processed <- promise.try_await(process_data(data))
  promise.resolve(Ok(format_output(processed)))
}
```

## Рефакторинг request_with_body

### Вариант 1: Базовый рефакторинг с use

```gleam
// Complete request with body reading - refactored with use syntax
pub fn request_with_body(
  method: http.Method,
  path: String,
  body: Option(String),
) -> Promise(Result(Dynamic, ApiError)) {
  // Build the request
  let req = request.new()
    |> request.set_method(method)
    |> request.set_path("/api" <> path)
    |> request.set_header("content-type", "application/json")
    |> request.set_header("accept", "application/json")
    |> set_body_if_present(body)

  // Send request and handle response
  use fetch_result <- promise.await(fetch.send(req))

  // Handle network errors early
  let response = case fetch_result {
    Error(fetch.NetworkError(msg)) ->
      return promise.resolve(Error(types.NetworkError(msg)))
    Error(_) ->
      return promise.resolve(Error(types.NetworkError("Request failed")))
    Ok(resp) -> resp
  }

  // Handle different status codes
  case response.status {
    200 | 201 -> handle_success_response(response)
    204 -> handle_no_content_response(response)
    401 -> promise.resolve(Error(types.AuthError("Unauthorized")))
    403 -> promise.resolve(Error(types.AuthError("Forbidden")))
    404 -> promise.resolve(Error(types.ServerError(404, "Not Found")))
    400 -> promise.resolve(Error(types.ValidationError([])))
    code -> promise.resolve(Error(types.ServerError(code, "Server error")))
  }
}

// Helper to set body if present
fn set_body_if_present(req: request.Request(String), body: Option(String)) -> request.Request(String) {
  case body {
    Some(json_body) -> request.set_body(req, json_body)
    None -> request.set_body(req, "")
  }
}

// Handle successful response with body
fn handle_success_response(response: fetch.Response) -> Promise(Result(Dynamic, ApiError)) {
  use body_result <- promise.map(fetch.read_text_body(response))

  case body_result {
    Error(_) -> Error(types.ParseError("Failed to read body"))
    Ok(text_response) -> parse_json_body(text_response.body)
  }
}

// Handle 204 No Content response
fn handle_no_content_response(response: fetch.Response) -> Promise(Result(Dynamic, ApiError)) {
  use _ <- promise.map(fetch.read_text_body(response))
  parse_json_body("{}")
}

// Parse JSON body to dynamic
fn parse_json_body(body: String) -> Result(Dynamic, ApiError) {
  case json.parse(body, decode.dynamic) {
    Ok(data) -> Ok(data)
    Error(_) -> Error(types.ParseError("Invalid JSON"))
  }
}
```

### Вариант 2: Продвинутый рефакторинг с более линейным flow

```gleam
import gleam/result

// Alternative approach with custom combinators
pub fn request_with_body_v2(
  method: http.Method,
  path: String,
  body: Option(String),
) -> Promise(Result(Dynamic, ApiError)) {
  let req = build_request(method, path, body)

  use response <- promise.try_await(
    fetch.send(req)
    |> promise.map_error(handle_fetch_error)
  )

  use body_text <- promise.try_await(
    read_response_body(response)
  )

  promise.resolve(parse_response(response.status, body_text))
}

fn build_request(
  method: http.Method,
  path: String,
  body: Option(String)
) -> request.Request(String) {
  request.new()
  |> request.set_method(method)
  |> request.set_path("/api" <> path)
  |> request.set_header("content-type", "application/json")
  |> request.set_header("accept", "application/json")
  |> set_body_if_present(body)
}

fn handle_fetch_error(error: fetch.FetchError) -> ApiError {
  case error {
    fetch.NetworkError(msg) -> types.NetworkError(msg)
    _ -> types.NetworkError("Request failed")
  }
}

fn read_response_body(response: fetch.Response) -> Promise(Result(String, ApiError)) {
  case response.status {
    204 -> promise.resolve(Ok("{}"))
    200 | 201 ->
      fetch.read_text_body(response)
      |> promise.map(fn(result) {
        result
        |> result.map(fn(resp) { resp.body })
        |> result.map_error(fn(_) { types.ParseError("Failed to read body") })
      })
    401 -> promise.resolve(Error(types.AuthError("Unauthorized")))
    403 -> promise.resolve(Error(types.AuthError("Forbidden")))
    404 -> promise.resolve(Error(types.ServerError(404, "Not Found")))
    400 -> promise.resolve(Error(types.ValidationError([])))
    code -> promise.resolve(Error(types.ServerError(code, "Server error")))
  }
}

fn parse_response(status: Int, body_text: String) -> Result(Dynamic, ApiError) {
  case status {
    200 | 201 | 204 -> parse_json_body(body_text)
    _ -> Error(types.ServerError(status, "Unexpected status"))
  }
}
```

## Тесты для request_with_body

```gleam
// test/api/http_client_test.gleam

import gleam/option.{None, Some}
import gleam/result
import gleam/dynamic
import gleam/json
import gleam/http
import gleam/promise
import gleeunit
import gleeunit/should
import api/http_client
import api/types

// Мок для fetch модуля
pub type MockResponse {
  MockResponse(
    status: Int,
    body: String,
    should_fail: Bool,
  )
}

// Хранилище для мок ответов
pub type TestContext {
  TestContext(
    expected_path: String,
    expected_method: http.Method,
    expected_body: Option(String),
    mock_response: MockResponse,
  )
}

pub fn main() {
  gleeunit.main()
}

// Тест успешного GET запроса с JSON ответом
pub fn request_with_body_success_200_test() {
  let mock_response = MockResponse(
    status: 200,
    body: "{\"id\": 1, \"name\": \"Test\"}",
    should_fail: False,
  )

  let context = TestContext(
    expected_path: "/users/1",
    expected_method: http.Get,
    expected_body: None,
    mock_response: mock_response,
  )

  // Запускаем тест с моком
  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Get,
        "/users/1",
        None
      )
    })
  )

  // Проверяем результат
  result
  |> should.be_ok()

  case result {
    Ok(data) -> {
      // Проверяем что вернулся правильный JSON
      data
      |> dynamic.field("id", dynamic.int)
      |> should.equal(Ok(1))

      data
      |> dynamic.field("name", dynamic.string)
      |> should.equal(Ok("Test"))
    }
    Error(_) -> panic("Should not fail")
  }
}

// Тест POST запроса с телом
pub fn request_with_body_post_with_body_test() {
  let request_body = "{\"username\": \"testuser\"}"
  let mock_response = MockResponse(
    status: 201,
    body: "{\"id\": 123, \"username\": \"testuser\"}",
    should_fail: False,
  )

  let context = TestContext(
    expected_path: "/users",
    expected_method: http.Post,
    expected_body: Some(request_body),
    mock_response: mock_response,
  )

  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Post,
        "/users",
        Some(request_body)
      )
    })
  )

  result
  |> should.be_ok()

  case result {
    Ok(data) -> {
      data
      |> dynamic.field("id", dynamic.int)
      |> should.equal(Ok(123))
    }
    Error(_) -> panic("Should not fail")
  }
}

// Тест обработки 204 No Content
pub fn request_with_body_no_content_test() {
  let mock_response = MockResponse(
    status: 204,
    body: "",
    should_fail: False,
  )

  let context = TestContext(
    expected_path: "/users/1",
    expected_method: http.Delete,
    expected_body: None,
    mock_response: mock_response,
  )

  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Delete,
        "/users/1",
        None
      )
    })
  )

  // Для 204 должен вернуться пустой JSON объект
  result
  |> should.be_ok()
}

// Тест обработки ошибки сети
pub fn request_with_body_network_error_test() {
  let mock_response = MockResponse(
    status: 0,
    body: "",
    should_fail: True,
  )

  let context = TestContext(
    expected_path: "/users",
    expected_method: http.Get,
    expected_body: None,
    mock_response: mock_response,
  )

  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Get,
        "/users",
        None
      )
    })
  )

  // Должна вернуться ошибка сети
  case result {
    Error(types.NetworkError(msg)) -> {
      msg
      |> should.not_equal("")
    }
    _ -> panic("Should return NetworkError")
  }
}

// Тест обработки 401 Unauthorized
pub fn request_with_body_unauthorized_test() {
  let mock_response = MockResponse(
    status: 401,
    body: "{\"error\": \"Unauthorized\"}",
    should_fail: False,
  )

  let context = TestContext(
    expected_path: "/protected",
    expected_method: http.Get,
    expected_body: None,
    mock_response: mock_response,
  )

  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Get,
        "/protected",
        None
      )
    })
  )

  case result {
    Error(types.AuthError(msg)) -> {
      msg
      |> should.equal("Unauthorized")
    }
    _ -> panic("Should return AuthError")
  }
}

// Тест обработки невалидного JSON
pub fn request_with_body_invalid_json_test() {
  let mock_response = MockResponse(
    status: 200,
    body: "This is not JSON",
    should_fail: False,
  )

  let context = TestContext(
    expected_path: "/bad",
    expected_method: http.Get,
    expected_body: None,
    mock_response: mock_response,
  )

  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Get,
        "/bad",
        None
      )
    })
  )

  case result {
    Error(types.ParseError(msg)) -> {
      msg
      |> should.equal("Invalid JSON")
    }
    _ -> panic("Should return ParseError")
  }
}

// Тест обработки 404
pub fn request_with_body_not_found_test() {
  let mock_response = MockResponse(
    status: 404,
    body: "",
    should_fail: False,
  )

  let context = TestContext(
    expected_path: "/missing",
    expected_method: http.Get,
    expected_body: None,
    mock_response: mock_response,
  )

  use result <- promise.await(
    run_with_mock_fetch(context, fn() {
      http_client.request_with_body(
        http.Get,
        "/missing",
        None
      )
    })
  )

  case result {
    Error(types.ServerError(code, msg)) -> {
      code
      |> should.equal(404)
      msg
      |> should.equal("Not Found")
    }
    _ -> panic("Should return ServerError(404)")
  }
}

// Вспомогательная функция для запуска тестов с моком
fn run_with_mock_fetch(
  context: TestContext,
  test_fn: fn() -> Promise(Result(Dynamic, types.ApiError))
) -> Promise(Result(Dynamic, types.ApiError)) {
  // В реальном тесте здесь нужно заменить fetch модуль на мок
  // Это упрощенный пример структуры

  // Проверяем что запрос соответствует ожиданиям
  // и возвращаем мок ответ

  test_fn()
}
```

## Тесты для проверки идентичности после рефакторинга

```gleam
// test/api/http_client_regression_test.gleam

import gleam/list
import gleam/promise
import api/http_client as original
import api/http_client_refactored as refactored

// Тест идентичности поведения
pub fn compare_implementations_test() {
  let test_cases = [
    #(http.Get, "/users", None),
    #(http.Post, "/users", Some("{\"name\":\"test\"}")),
    #(http.Put, "/users/1", Some("{\"name\":\"updated\"}")),
    #(http.Delete, "/users/1", None),
  ]

  // Прогоняем каждый тест через обе реализации
  list.each(test_cases, fn(test_case) {
    let #(method, path, body) = test_case

    use original_result <- promise.await(
      original.request_with_body(method, path, body)
    )

    use refactored_result <- promise.await(
      refactored.request_with_body(method, path, body)
    )

    // Результаты должны быть идентичны
    refactored_result
    |> should.equal(original_result)
  })
}
```

## Пошаговая проверка рефакторинга

1. **Сохраните оригинальную версию:**
   ```bash
   cp src/frontend/src/api/http_client.gleam src/frontend/src/api/http_client_original.gleam
   ```

2. **Создайте тесты перед рефакторингом:**
   ```bash
   mkdir -p src/frontend/test/api
   # Создайте http_client_test.gleam с тестами выше
   ```

3. **Запустите тесты на оригинальной версии:**
   ```bash
   cd src/frontend
   gleam test
   ```

4. **Выполните рефакторинг пошагово:**
   - Начните с одной функции
   - Запускайте тесты после каждого изменения
   - Сравнивайте результаты

5. **Финальная проверка:**
   ```bash
   # Запустите полный набор тестов
   gleam test

   # Проверьте типы
   gleam check

   # Запустите приложение и проверьте вручную
   gleam build --target javascript
   ```

## Ключевые моменты при рефакторинге

### Важные особенности `use`

1. **`use` работает только с функциями, принимающими callback последним аргументом**
   ```gleam
   // Работает:
   fn fetch_data(id: Int, callback: fn(Result(Data, Error)) -> a) -> a

   // Можно использовать:
   use data <- fetch_data(123)
   ```

2. **`return` в use блоке выходит из всей функции**
   ```gleam
   pub fn example() {
     use x <- some_function()
     if x > 10 {
       return "early exit"  // Выход из example(), не из callback
     }
     "normal exit"
   }
   ```

3. **Каждый `use` добавляет уровень вложенности в скомпилированном коде**
   ```gleam
   // Избегайте слишком многих use подряд
   use a <- func1()
   use b <- func2(a)
   use c <- func3(b)
   // Лучше извлечь в отдельную функцию если больше 3-4 use
   ```

4. **`promise.try_await` автоматически обрабатывает Result в Promise**
   ```gleam
   // Вместо:
   use result <- promise.await(fetch())
   case result {
     Error(e) -> return promise.resolve(Error(e))
     Ok(data) -> data
   }

   // Используйте:
   use data <- promise.try_await(fetch())
   // data уже распакован из Ok
   ```

### Преимущества рефакторинга

1. **Линейность кода** - читается сверху вниз без прыжков
2. **Меньше вложенности** - максимум 2-3 уровня вместо 5+
3. **Легче добавлять логику** - просто вставляете новый use
4. **Понятнее обработка ошибок** - early returns вместо вложенных case
5. **Модульность** - легко извлекать части в отдельные функции

### Когда НЕ использовать `use`

1. **Простые синхронные операции** - обычный pipeline лучше
2. **Когда нужно параллельное выполнение** - use последовательный
3. **Слишком много операций подряд** (>5) - разбейте на функции

## Чек-лист после рефакторинга

- [ ] Все тесты проходят
- [ ] Типы корректны (gleam check)
- [ ] Нет регрессий в функциональности
- [ ] Код читается линейно
- [ ] Уменьшилась вложенность
- [ ] Ошибки обрабатываются корректно
- [ ] Early returns работают как ожидается
- [ ] Нет лишних promise.resolve обёрток

## Примечание о совместимости

Функция `use` появилась в Gleam v0.25+. Убедитесь что у вас актуальная версия:
```bash
gleam --version
```

Если версия старее, обновите Gleam перед рефакторингом.