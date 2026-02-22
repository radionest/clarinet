# Frontend Refactoring Plan

## Обзор

Анализ фронтенда (`src/frontend/src/`) выявил серьезные нарушения принципов DRY, KISS, YAGNI.
Основная проблема — большое количество мертвого кода, написанного "на будущее", дублирование паттернов
и излишняя сложность в структурах данных.

---

## 1. YAGNI — мертвый код и преждевременные абстракции

### 1.1 store.gleam — Model перегружен неиспользуемыми полями

**Удалить из Model:**

| Поле | Причина |
|------|---------|
| `record_types: Dict(String, RecordType)` | Никогда не заполняется и не читается |
| `patients: Dict(String, Patient)` | Никогда не заполняется и не читается |
| `study_form: Option(dynamic.Dynamic)` | Формы используют собственные типы состояния |
| `record_type_form: Option(dynamic.Dynamic)` | Формы используют собственные типы состояния |
| `patient_form: Option(dynamic.Dynamic)` | Формы используют собственные типы состояния |
| `form_errors: Dict(String, String)` | Не используется страницами |
| `current_page: Int` | Пагинация не реализована |
| `items_per_page: Int` | Пагинация не реализована |
| `total_items: Int` | Пагинация не реализована |
| `search_query: String` | Фильтрация не реализована |
| `active_filters: Dict(String, String)` | Фильтрация не реализована |
| `modal_open: Bool` | Модалки нигде не рендерятся |
| `modal_content: ModalContent` | Модалки нигде не рендерятся |

**Удалить тип `ModalContent`** — не используется.

**Удалить из `init()`** соответствующие инициализации.

### 1.2 store.gleam — неиспользуемые Msg-варианты

**Удалить ~30 необработанных вариантов Msg:**

- `LogoutComplete`
- `LoadStudyDetail`, `StudyDetailLoaded`
- `LoadRecordDetail`, `RecordDetailLoaded`
- `UpdateStudyForm`, `SubmitStudyForm`, `StudyFormSubmitted`
- `UpdateRecordTypeForm`, `UpdateRecordTypeSchema`, `SubmitRecordTypeForm`, `RecordTypeFormSubmitted`
- `UpdatePatientForm`, `SubmitPatientForm`, `PatientFormSubmitted`
- `SubmitRecordData`, `RecordDataSaved`
- `OpenModal`, `CloseModal`, `ConfirmModalAction`
- `UpdateSearchQuery`, `AddFilter`, `RemoveFilter`, `ClearFilters`
- `SetPage`, `SetItemsPerPage`
- `NoOp`, `RefreshData`, `ShowSchemaError`

Все проглатываются `_ -> #(model, effect.none())` в `main.gleam:421`.

> **Внимание:** после удаления вариантов убрать wildcard `_` в `update()` и сделать
> исчерпывающий pattern match — компилятор сразу покажет, если что-то осталось.

### 1.3 store.gleam — неиспользуемые хелперы

**Удалить функции:**
- `cache_study` — не вызывается
- `cache_record` — не вызывается
- `cache_record_type` — не вызывается
- `set_form_error` — не вызывается
- `clear_form_errors` — не вызывается
- `apply_filter` — не вызывается
- `remove_filter` — не вызывается
- `clear_filters` — не вызывается

### 1.4 types.gleam — неиспользуемые типы

**Удалить:**
- `ApiResponse(a)` — не используется
- `Pagination` — не используется
- `ListResponse(a)` — не используется

### 1.5 models.gleam — неиспользуемые Create/Read типы

**Удалить (не используются в реальных API-вызовах):**
- `PatientCreate`, `PatientRead`
- `StudyCreate`, `StudyRead`
- `RecordTypeCreate`
- `RecordCreate`, `RecordRead`
- `SeriesCreate`, `SeriesRead`
- `UserCreate`, `UserRead`

> **Примечание:** `StudyCreate` используется в `study_form.gleam`, но сама форма не подключена.
> При удалении формы удалить и тип.

### 1.6 Неподключенные форм-компоненты

**Удалить файлы (формы полностью реализованы, но не подключены ни к одной странице):**
- `src/frontend/src/components/forms/study_form.gleam`
- `src/frontend/src/components/forms/patient_form.gleam`
- `src/frontend/src/components/forms/user_form.gleam`

> **Альтернатива:** если формы планируется использовать в ближайшем будущем — оставить,
> но убрать соответствующие Msg-варианты и типы из store до момента подключения.

### 1.7 dom.gleam — неиспользуемые функции

**Удалить:**
- `set_input_value` — не вызывается
- `focus_element` — не вызывается
- `is_development` — не вызывается

Оставить только `get_input_value` (используется login/register).

### 1.8 records.gleam — `get_my_records()`

**Удалить** — не вызывается нигде.

### 1.9 Страницы-заглушки

6 из 13 страниц — пустые заглушки ("will be implemented here"):
- `pages/studies/list.gleam`
- `pages/studies/detail.gleam`
- `pages/records/list.gleam`
- `pages/records/detail.gleam`
- `pages/records/new.gleam`
- `pages/users/list.gleam`
- `pages/users/profile.gleam`

При этом `view_content` в `main.gleam` не использует даже эти заглушки — рендерит inline-текст.

**Решение:** удалить файлы-заглушки. Inline-заглушки в `view_content` достаточно.
Когда страницы будут реализовываться — создавать файлы заново.

### 1.10 execute.gleam — отключенная функциональность

280 строк с broken-интеграцией formosh, ссылается на `schema/parser`.
Страница не подключена в `view_content`.

**Решение:** удалить или сократить до минимальной заглушки до момента починки formosh.

---

## 2. DRY — дублирование

### 2.1 Двойной decoder для User

**Проблема:**
- `auth.gleam:87-124` — приватный `decode_user`
- `users.gleam:18-35` — публичный `user_decoder`

**Исправление:** удалить `decode_user` из `auth.gleam`, использовать `users.user_decoder()`.

```gleam
// auth.gleam — заменить decode_user на:
import api/users

fn decode_user(data: dynamic.Dynamic) -> Result(User, ApiError) {
  case decode.run(data, users.user_decoder()) {
    Ok(user) -> Ok(user)
    Error(_) -> Error(types.ParseError("Invalid user data"))
  }
}
```

### 2.2 Повторяющийся паттерн decode-обертки

**Проблема:** каждый API-модуль содержит идентичную обертку:

```gleam
fn decode_X(data: Dynamic) -> Result(X, ApiError) {
  case decode.run(data, X_decoder()) {
    Ok(x) -> Ok(x)
    Error(_) -> Error(types.ParseError("Invalid X data"))
  }
}
```

**Исправление:** добавить generic-хелпер в `http_client.gleam`:

```gleam
pub fn decode_response(
  data: Dynamic,
  decoder: decode.Decoder(a),
  error_msg: String,
) -> Result(a, ApiError) {
  case decode.run(data, decoder) {
    Ok(value) -> Ok(value)
    Error(_) -> Error(types.ParseError(error_msg))
  }
}
```

Тогда в API-модулях:

```gleam
pub fn get_studies() -> Promise(Result(List(Study), ApiError)) {
  http_client.get("/studies")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _, decode.list(study_decoder()), "Invalid studies data",
    ))
  })
}
```

### 2.3 text_input / email_input / password_input

**Проблема:** три почти идентичные функции в `forms/base.gleam` (строки 45-114),
отличающиеся только `attribute.type_()`.

**Исправление:** одна функция с параметром типа:

```gleam
pub fn input(
  input_type: String,
  name: String,
  value: String,
  placeholder: Option(String),
  on_input: fn(String) -> Msg,
) -> Element(Msg) {
  let attrs = [
    attribute.type_(input_type),
    attribute.id(name),
    attribute.name(name),
    attribute.value(value),
    attribute.class("form-input"),
    event.on_input(on_input),
  ]
  let attrs = case placeholder {
    Some(p) -> list.append(attrs, [attribute.placeholder(p)])
    None -> attrs
  }
  html.input(attrs)
}

// Удобные алиасы (опционально):
pub fn text_input(name, value, placeholder, on_input) {
  input("text", name, value, placeholder, on_input)
}
pub fn email_input(name, value, placeholder, on_input) {
  input("email", name, value, placeholder, on_input)
}
pub fn password_input(name, value, placeholder, on_input) {
  input("password", name, value, placeholder, on_input)
}
```

### 2.4 field / required_field

**Проблема:** отличаются только CSS-классом и звездочкой в label.

**Исправление:** одна функция с параметром `required: Bool`:

```gleam
pub fn field(
  label: String,
  name: String,
  input: Element(Msg),
  errors: Dict(String, String),
  required: Bool,
) -> Element(Msg) {
  let class = case required {
    True -> "form-field required"
    False -> "form-field"
  }
  html.div([attribute.class(class)], [
    html.label([attribute.for(name), attribute.class("form-label")], [
      html.text(label),
      case required {
        True -> html.span([attribute.class("required-marker")], [html.text(" *")])
        False -> html.text("")
      },
    ]),
    input,
    error_message(errors, name),
  ])
}
```

### 2.5 stat_card дублирование

**Проблема:**
- `home.gleam:45-63` — `stat_card` (с ссылкой)
- `admin.gleam:187-194` — `admin_stat_card` (без ссылки)

**Исправление:** вынести общий компонент, например, в `components/stat_card.gleam`
с опциональной ссылкой:

```gleam
pub fn view(
  label: String,
  count: Int,
  color: String,
  route: Option(router.Route),
) -> Element(Msg) { ... }
```

### 2.6 build_request / build_multipart_request — дублирование origin-логики

**Проблема:** обе функции в `http_client.gleam` повторяют одну и ту же логику
резолвинга browser origin.

**Исправление:** вынести в хелпер:

```gleam
fn base_request(method: http.Method, path: String) -> request.Request(String) {
  {
    use origin <- result.try(window.origin() |> uri.parse)
    request.from_uri(origin)
  }
  |> result.unwrap(request.new())
  |> request.set_method(method)
  |> request.set_path("/api" <> path)
  |> request.set_header("accept", "application/json")
}
```

---

## 3. KISS — излишняя сложность

### 3.1 Двойные структуры данных Dict + List

**Проблема:** store поддерживает `studies: Dict` + `studies_list: List`,
то же для records и users. Риск рассинхронизации, лишняя работа при обновлении.

**Исправление:** оставить только `Dict`. Для отображения списков использовать
`dict.values()`. Если нужна сортировка — сортировать при рендере.

```gleam
// Вместо:
studies: Dict(String, Study),
studies_list: List(Study),

// Оставить:
studies: Dict(String, Study),
```

### 3.2 is_same_section в router.gleam — ручное перечисление пар

**Проблема:** 14 case-ветвей вручную перечисляют все комбинации маршрутов.

**Исправление:** ввести функцию `section`:

```gleam
fn section(route: Route) -> String {
  case route {
    Home -> "home"
    Login -> "login"
    Register -> "register"
    Studies | StudyDetail(_) -> "studies"
    Records | RecordDetail(_) | RecordNew | RecordTypeDesign(_) -> "records"
    Users | UserProfile(_) -> "users"
    AdminDashboard -> "admin"
    NotFound -> "notfound"
  }
}

pub fn is_same_section(route1: Route, route2: Route) -> Bool {
  section(route1) == section(route2)
}
```

### 3.3 Record тип слишком большой

**Проблема:** `Record` в `models.gleam` содержит 23 поля, decoder ставит 19 из них в `None`.

**Исправление:** разделить на `RecordSummary` (для списков, то что реально приходит из API)
и `RecordFull` (для детальной страницы, когда будет реализована):

```gleam
pub type RecordSummary {
  RecordSummary(
    id: Option(Int),
    context_info: Option(String),
    status: RecordStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    record_type_name: String,
    user_id: Option(String),
    patient_id: String,
  )
}
```

### 3.4 view_content — inline-заглушки вместо файлов

**Проблема:** `main.gleam` рендерит `html.text("Studies page")` для маршрутов,
хотя существуют файлы-заглушки.

**Исправление:** после удаления файлов-заглушек (п.1.9) inline-заглушки становятся
единственным местом — это нормально. Либо подключить существующие файлы, если они остаются.

### 3.5 login/register — DOM-запросы вместо controlled inputs

**Проблема:** страницы login и register получают значения через `dom.get_input_value("email")`
вместо хранения состояния в Model и `event.on_input`. Анти-паттерн для MVU.

**Исправление:** добавить поля `login_email`, `login_password` (и аналогичные для register)
в Model. Использовать `event.on_input` для обновления и читать из Model при submit.

> **Приоритет:** средний. Текущий подход работает, но противоречит архитектуре Lustre.

---

## 4. Баг

### 4.1 Двойной `/api` prefix в auth.gleam

**Файл:** `auth.gleam:62`

```gleam
// Текущий (ОШИБКА):
http_client.post("/api/auth/register", body)

// Правильно:
http_client.post("/auth/register", body)
```

`http_client.post` уже добавляет `/api`, итоговый URL будет `/api/api/auth/register`.

---

## 5. Стилистические замечания

### 5.1 Импорты в конце файла

Файлы `forms/base.gleam` (строки 369-370), `study_form.gleam` (строка 172),
`patient_form.gleam` (строка 144), `user_form.gleam` (строки 298-299) имеют
импорты в конце файла. Перенести в начало.

---

## План выполнения

### Фаза 1 — Критическое (баг + мертвый код)

1. [ ] Исправить двойной `/api` в `auth.gleam:62`
2. [ ] Удалить неиспользуемые поля из `Model` в `store.gleam`
3. [ ] Удалить необработанные Msg-варианты из `store.gleam`
4. [ ] Убрать wildcard `_` из `update()`, сделать exhaustive match
5. [ ] Удалить неиспользуемые хелперы из `store.gleam`
6. [ ] Удалить `ModalContent` тип
7. [ ] Удалить неиспользуемые типы из `types.gleam` и `models.gleam`
8. [ ] Удалить неиспользуемые функции из `dom.gleam`
9. [ ] Удалить `get_my_records()` из `records.gleam`

### Фаза 2 — Удаление неподключенного кода

10. [ ] Удалить страницы-заглушки (или подключить к `view_content`)
11. [ ] Удалить/сократить `execute.gleam`
12. [ ] Решить судьбу форм-компонентов (`study_form`, `patient_form`, `user_form`)

### Фаза 3 — DRY-рефакторинг

13. [ ] Объединить user decoder (использовать `users.user_decoder()` в `auth.gleam`)
14. [ ] Добавить `decode_response` хелпер в `http_client.gleam`
15. [ ] Объединить `text_input`/`email_input`/`password_input` в `forms/base.gleam`
16. [ ] Объединить `field`/`required_field` в `forms/base.gleam`
17. [ ] Вынести `stat_card` в общий компонент
18. [ ] Вынести `base_request` хелпер в `http_client.gleam`
19. [ ] Перенести импорты в начало файлов

### Фаза 4 — KISS-упрощение

20. [ ] Убрать дублирование Dict+List в store (оставить Dict)
21. [ ] Упростить `is_same_section` через `section()` хелпер
22. [ ] Разделить `Record` на `RecordSummary` / `RecordFull`
23. [ ] (Опционально) Перевести login/register на controlled inputs

### После каждой фазы

- `gleam build --target javascript` — проверить компиляцию
- Проверить работу в браузере (login, dashboard, admin)
