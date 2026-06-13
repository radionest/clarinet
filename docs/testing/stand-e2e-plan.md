# План: стенды живых проектов как часть e2e-тестирования Clarinet

Статус: проект архитектуры (draft v1, 2026-06-13). Документ — основание для реализации,
не сама реализация. Прозой по-русски, идентификаторы/команды/код — английским.
При переносе в репозиторий: `docs/testing/stand-e2e.md` (перевести прозу на английский).

---

## 1. Цель

Превратить разовый прототип `nir_liver` (в `/home/nest/clarinet-stand/nir_liver/tests/workflow/`)
в **системный механизм**:

1. Берём **живой проект** (его БД + DICOM + `plan/`-бандл).
2. **Анонимизируем** БД и DICOM (DICOM — существующим сервисом, БД — новым).
3. Пакуем в **версионированный фикстур-артефакт** стенда.
4. `test-all-stages` (или отдельный target) **разворачивает фикстуру на multi-VM окружении**
   и прогоняет весь workflow проекта, включая состояния гонок.

Целевая топология (как просил пользователь):

```
            ┌─────────────────────────────────────────────────────────────┐
            │ ХОСТ (WSL/Linux) — управляющий узел                          │
            │  • pytest-клиент = Stand driver (httpx + ssh/scp)            │
            │  • headless 3D Slicer (порт 2016) — реальные Slicer-стадии   │
            │  • прогон сценария, ассершены, ожидания состояний            │
            └───┬───────────────────┬───────────────────┬─────────────────┘
        HTTPS   │ SSH               │ SSH               │ SSH
       /<pref>/ │                   │                   │
            ┌───▼───────────┐  ┌────▼──────────┐  ┌─────▼─────────┐
            │ VM-STAND      │  │ VM-PACS       │  │ VM-WORKER     │
            │ nginx :443    │  │ Orthanc       │  │ clarinet-     │
            │ clarinet-api  │  │  DICOM :4242  │  │  worker       │
            │ PostgreSQL    │◄─┤  REST  :8042  │◄─┤ (default/     │
            │ RabbitMQ      │  │ (анонимные    │  │  dicom/gpu*)  │
            │ (SUT-ядро)    │  │  DICOM из     │  │  C-GET/NIfTI/ │
            │               │  │  фикстуры)    │  │  anonymize    │
            └───────▲───────┘  └───────────────┘  └──────┬────────┘
                    │  HTTP API + RabbitMQ + общий storage (NFS)              │
                    └────────────────────────────────────┘
       Все VM — на libvirt default NAT (virbr0), общаются по 192.168.122.x
```

Связи (по приватным IP virbr0):
- **Хост → VM-STAND**: HTTPS `https://<stand-ip>/<prefix>/` (API+фронт), SSH (управление, резолв путей).
- **Хост → VM-PACS / VM-WORKER**: SSH (провижн, инъекция файлов, проверки).
- **VM-WORKER → VM-STAND**: HTTP API (`ClarinetClient`: `api_base_url` + `service_token`) + RabbitMQ (`rabbitmq_host`)
  + **общий storage-моунт (NFS)** для файловых артефактов. **Прямого доступа к PostgreSQL у воркера нет** —
  все чтения/записи записей идут через API.
- **VM-STAND (api) → VM-PACS**: C-FIND (`import-study`).
- **VM-WORKER → VM-PACS**: C-GET (`convert_series_to_nifti`, `anonymize_study_pipeline`) — DICOM `:4242`.

---

## 2. Что уже есть (фундамент) и чего нет (пробелы)

### Есть, переиспользуем

| Кусок | Где | Заметка |
|---|---|---|
| Провижн VM (KVM/QEMU/libvirt) | `deploy/vm/vm.sh`, `deploy/vm/vm.conf` | `virt-install`/`virsh`, cloud-init, Ubuntu 24.04 |
| Golden-image + COW-overlay | `vm.sh cmd_bake` → `clarinet-golden.qcow2` | reimage = destroy+create за ~30с |
| Установка стека на VM | `deploy/install/install-clarinet.sh`, `setup-services.sh`, `generate-settings.sh` | postgres+rabbitmq+orthanc+nginx+systemd, идемпотентно |
| **Параметризация PACS** | `vm.conf:PACS_HOST` → `CLARINET_PACS_HOST` → `settings.pacs_host` | уже отделимо от localhost (см. ветку `worktree-vm-downstream-project`) |
| Service discovery в settings | `database_host/port`, `rabbitmq_host`, `pacs_host/port/aet`, `dicom_aet/port`, `dicom_retrieve_mode` | всё переопределяемо через env `CLARINET_*` |
| Загрузка DICOM в Orthanc при bake | `vm.sh cmd_bake` + `bake-image.sh` (REST upload) | основа для предзагрузки фикстуры в VM-PACS |
| **Анонимизация DICOM** | `services/anonymization_service.py`, `services/dicom/anonymizer.py` | детерминированный UID-хэш `sha256(salt:uid)→2.25.<int>`, PatientID/Name/StudyUID/SeriesUID/SOPUID |
| **DICOMweb-прокси (OHIF)** | `clarinet` api (WADO-RS/QIDO-RS), `dicomweb_cache/`, task `prefetch_dicom_web` | HTTP-источник анонимного DICOM при capture — отдаёт `dcm_anon` без DICOM-networking (см. 3.2/3.3) |
| **Воркер ↔ API по HTTP** | `ClarinetClient` (`client.py`), `effective_api_base_url`, `effective_service_token`, `TaskContext.client/.records/.files` | воркер читает/пишет **через API + service-token**, не через БД; файлы — через `ctx.files` (общий storage). Уже готов к выносу на отдельную VM |
| Прототип-харнесс (3 слоя) | `clarinet-stand/nir_liver/tests/workflow/` | `Stand` driver, `stand_tool.py`, `setup_stand.sh`, сценарий-DAG |
| `test-all-stages` оркестрация | `Makefile:216–385` | 8 стадий, флаги `SKIP_VM/KEEP_VM/SKIP_SCHEMA/SKIP_SLICER` |
| e2e (Playwright) | `deploy/test/e2e/`, env `CLARINET_TEST_URL` | модель «прогон против VM по HTTPS+sub-path» |
| Headless Slicer на хосте | `deploy/test/slicer/run-headless.sh` (порт 2016) | основа для реальных Slicer-стадий |
| RecordFlow DSL (workflow-DAG) | `plan/workflows/*_flow.py`, `recordflow/`, `plan_package.py` | декларация DAG проекта; состояния `preparing/blocked` сериализуют гонку prefill↔check-files |

### Нет — нужно построить

| Пробел | Критичность | Где решаем |
|---|---|---|
| **Анонимизация БД** (PII в `patient.name`, `study_description`, `series_description`, `record.data` JSON, `record.context_info`) | 🔴 блокер | Фаза 2 — `clarinet anon scrub-db` |
| **Multi-VM провижн** (`vm.conf` описывает 1 VM; нет ролей, межвм-сети, service-discovery wiring) | 🔴 блокер | Фаза 1 |
| **Фикстур-пайплайн** (capture→anonymize→package→registry→load) | 🔴 блокер | Фаза 3 |
| **Обобщение харнесса** (всё захардкожено под nir_liver) | 🟠 | Фаза 4 |
| **Декларативный формат сценария** (spine-DAG + race-точки + per-stage data) | 🟠 | Фаза 4 |
| **Slicer-стадии: replay реальных сегментаций + реальный Slicer** (прототип лишь синтезирует, минуя валидатор) | 🟡 | Фаза 5 |
| **Интеграция в CI** (multi-VM тяжелее текущей single-VM) | 🟠 | Фаза 6 |

---

## 3. Компоненты системы

### 3.1. Multi-VM провижн (Фаза 1)

**Проблема**: `vm.conf` описывает один `VM_NAME`; `install-clarinet.sh` ставит весь стек разом;
service-discovery местами подразумевает localhost.

**Решение**: топология-конфиг + роли + wiring по IP.

- **Топология-конфиг** `deploy/vm/topology.toml` (вместо/поверх плоского `vm.conf`):
  ```toml
  [defaults]
  image = "noble-server-cloudimg-amd64.img"
  ssh_key = "${HOME}/.ssh/clarinet-vm"

  [vm.stand]
  role = "stand"        # nginx + api + postgres + rabbitmq
  ram = 4096
  vcpus = 2
  path_prefix = "/nir_liver/"

  [vm.pacs]
  role = "pacs"         # только orthanc
  ram = 2048
  vcpus = 1

  [vm.worker]
  role = "worker"       # clarinet-worker (default+dicom)
  ram = 4096
  vcpus = 2
  queues = ["default", "dicom"]
  ```

- **Роли при провижне**: декомпозировать `install-clarinet.sh` на роль-профили (не три golden-образа,
  а один golden со всем установленным + включение нужного на deploy — см. решение D5):
  - `stand`: enable postgres+rabbitmq+nginx+`clarinet-api`+`clarinet-worker@default`(опц.).
  - `pacs`: enable только orthanc; зарегистрировать AET'ы стенда/воркера как Orthanc-модальности
    (вынести шаг из прототипного `setup_stand.sh:39–42` в роль-провижн); `AllowFind/AllowGet=true`.
  - `worker`: enable `clarinet-worker@dicom`; настроить `--dicom <AET>:<port>` (для c-move; для c-get не нужен reverse).

- **Service-discovery wiring** (после поднятия VM, IP известны):
  - VM-STAND `settings.toml`: `pacs_host=<pacs-ip>`.
  - VM-WORKER `settings.toml`/`.env`: `api_base_url=https://<stand-ip>/<prefix>/api` (+ `api_verify_ssl=false` для
    self-signed), `internal_service_token=<тот же, что на VM-STAND>`, `rabbitmq_host=<stand-ip>`, `pacs_host=<pacs-ip>`,
    `dicom_retrieve_mode="c-get"`. **`database_host` воркеру НЕ задаём** — данные он берёт через API (`ClarinetClient`),
    не из PostgreSQL.
  - **Общий storage** (обязателен при разнесении): каталог `storage_path` (`/var/lib/clarinet/data`) — общий для
    VM-STAND и VM-WORKER через NFS (VM-STAND экспортирует, VM-WORKER монтирует тем же путём). Причина: таски пишут
    файлы (NIfTI, seg, `dicomweb_cache/`) напрямую через `ctx.files`, а API/DICOMweb-прокси читает их же; путь
    рендерится одним движком `storage_paths` по обе стороны, поэтому пути обязаны совпадать на общем ФС.
  - **Сетевая экспозиция наружу** (virbr0): только **RabbitMQ** (`5672`) и **API через nginx** (`443`).
    **PostgreSQL остаётся на localhost** VM-STAND (+ SSH-туннель для PG-тестов стадии 6) — воркеру он не нужен,
    наружу не открываем (плюс к безопасности).

- **Lifecycle**: `vm.sh` расширить до набора VM — `cmd_topology_up` (создать все из `topology.toml`,
  дождаться SSH, собрать IP в `topology.lock.json`), `cmd_topology_wire` (записать settings по ролям),
  `cmd_topology_down`. Каждая VM по-прежнему — overlay поверх общего golden.

- **DICOM retrieve mode**: **c-get** (рекоменд.) — не требует обратного подключения PACS→worker и
  Storage-SCP listener'а на воркере; прототип уже выбрал c-get (`setup_stand.sh:30–33`).

**Deliverable Фазы 1**: `make vm-topology-up TOPOLOGY=nir_liver` поднимает stand+pacs+worker, они видят друг друга
(smoke: воркер коннектится к БД/брокеру стенда, api делает C-FIND к PACS).

### 3.2. Анонимизатор БД (Фаза 2) — главный пробел

**Новая CLI**: `clarinet anon scrub-db --in <dump|live-url> --out <dump> --patients <ids|selector> [--per-study]`.

Должна быть **консистентна с DICOM-анонимизацией**, иначе пути `{anon_patient_id}/{anon_study_uid}/{anon_series_uid}/dcm_anon/`
не сойдутся с `study.anon_uid`/`series.anon_uid` и `FileRepository.resolve_file` сломается на стенде.

**Источники при capture** (доступны на живом инстансе Clarinet): **БД** + **DICOMweb-прокси Clarinet**
(тот, что отдаёт DICOM в OHIF — WADO-RS/QIDO-RS поверх HTTP). DICOM качаем **через DICOMweb-прокси** (он уже
отдаёт анонимные `dcm_anon`), без прямого DICOM-networking к PACS. Дополнительно — **скрап реальных
пользовательских сегментаций** через `FileRepository` для replay-режима (см. 3.3). То есть capture опирается
только на доступ к живому инстансу (DB + HTTP DICOMweb), а не на сетевой доступ к самому PACS.

Операции:
1. **Выборка по FK-графу** из заданных пациентов: `patient → study → series → record → record_filelink`,
   плюс справочники (`recordtype`, `filedefinition`, `*_link`), плюс `user` (логины сохранить, e-mail скрабить).
2. **Маппинг ID** — переиспользовать существующую логику (`Patient.anon_id` или `compute_per_study_patient_id()`
   из `anonymizer.py`), тот же `salt`/`anon_id_prefix`, что и для DICOM:
   - `patient.id` (DICOM PatientID) → anon;
   - `study.anon_uid`/`series.anon_uid` ← `sha256(salt:uid)→2.25.<int>` (тот же `_anon_uid`).
3. **Скраб PII**: `patient.name`→`"Patient_<n>"`, `patient.anon_name` по политике, `study.date` (сдвиг или сохранить),
   `study_description`/`series_description`→generic, `record.context_info`→очистить.
4. **`record.data` (JSON)** — **schema-aware**: по схеме RecordType (`plan/schemas/*.schema.json`) определить
   free-text поля (скрабить) vs структурные enum/number (сохранить — нужны для прохождения workflow).
5. **Сохранить** `autoidcounter` (max `auto_id`, иначе коллизии), **выбросить** `session`/`accesstoken`.
6. **Аудит**: финальный скан дампа на известные PHI-паттерны (исходные имена/MRN) → fail, если найдено.

**Deliverable Фазы 2**: живой дамп → анонимный дамп, который восстанавливается на чистый стенд так, что
`FileRepository.resolve_file` находит анонимные DICOM, а в дампе нет исходного PHI (аудит зелёный).

### 3.3. Фикстур-пайплайн (Фаза 3)

**Capture** (read-only против живого инстанса):
- БД: `pg_dump` выборки из 3.2.
- DICOM (предпочтительно): через **DICOMweb-прокси Clarinet** живого проекта (WADO-RS) — отдаёт уже анонимные
  `dcm_anon` по HTTP, без DICOM-networking. Fallback: `AnonymizationService` (C-GET из живого PACS + анонимизация)
  → `dcm_anon/`, либо копия `dcm_anon/` с диска.
- **Реальные сегментации пользователей** (replay-фикстуры): через `FileRepository` достаём настоящие `.seg.nrrd`/маски,
  которые врачи создали в Slicer на проде, + соответствующие `record.data`. Кладём в `segmentations/` фикстуры и
  на стенде **воспроизводим (mode=replay)** вместо синтеза — высокая достоверность, реальная геометрия и краевые случаи.
  PII: имена/описания сегментов в заголовке `.seg.nrrd` могут нести free-text → прогнать через тот же скраб, что и БД.
- `plan/`-бандл: копия `plan/` проекта + `settings.toml` без секретов (или git-tag-ссылка).
- **Артефакты**: derived-выходы (NIfTI, master-model) **регенерируем на стенде** пайплайном — это и есть тест workflow.
  Нерегенерируемое без GPU (GPU-печень `auto-liver`) и захваченные сегментации — **inject/replay-фикстуры**.
  Консистентность replay: захваченная сегментация снималась против NIfTI, сконвертированного из тех же анонимных DICOM;
  на стенде NIfTI регенерируется тем же пайплайном → при детерминированной конверсии геометрия совпадает. `stand_tool.py`
  при replay **сверяет/ресэмплит** seg на актуальную сетку тома (страховка — захватывать и сам NIfTI том).

**Package** — версионированный артефакт:
```
fixtures/<project>/<version>/
  manifest.toml      # см. 3.5: проект, prefix, demo-пациенты, spine-DAG, seg-классы,
                     #          режим стадий (replay|synthetic|inject|slicer|form), race-точки,
                     #          source-provenance hash, версия схемы (для миграций)
  db.sql.gz          # анонимный pg_dump
  dicom/<anon_patient>/<anon_study>/<anon_series>/dcm_anon/*.dcm
  segmentations/     # реальные пользовательские .seg.nrrd/маски для mode=replay (см. 3.3)
  artifacts/         # injected нерегенерируемые выходы (GPU-маски и т.п.)
  plan/              # бандл проекта (или ссылка на git-tag)
```

**Registry/storage** (D1 — зафиксировано): **checksum в репо, артефакты пока на локальном диске**, позже — выгрузка в S3.
В git только `manifest.toml` + checksum артефакта; сам артефакт (`db.sql.gz` + `dicom/` + `artifacts/`) лежит локально
(`fixtures/<project>/<version>/`, в `.gitignore`), резолвится по checksum. S3-бэкенд добавляется позже **без смены формата**
(тот же layout и `manifest.toml`, меняется только источник: local FS → S3). `manifest.toml` пинит версию схемы
для миграции (`clarinet db upgrade`) при дрейфе.

**Load** на стенд (расширение `bake`/`deploy`):
- восстановить `db.sql.gz` в PostgreSQL VM-STAND;
- залить `dicom/` в Orthanc VM-PACS (REST upload, как `bake-image.sh`);
- разложить `artifacts/` по storage VM-STAND (резолв путей через `FileRepository`);
- задеплоить `plan/` + прописать service-discovery (3.1);
- `clarinet db upgrade` (миграция фикстуры под текущую схему), `clarinet db init` (admin).

**Deliverable Фазы 3**: `clarinet stand capture --project nir_liver --patients ...` → артефакт;
`clarinet stand load <fixture> --topology nir_liver` → готовый разнесённый стенд.

### 3.4. Обобщённый харнесс (Фаза 4)

Перенести прототип в репозиторий как переиспользуемую библиотеку. Расположение:
`deploy/test/stand/` (рядом с `deploy/test/e2e/`) — общий движок; per-project сценарии — в `<project>/tests/workflow/`.

Декомпозиция (что обобщить из прототипа):

| Прототип (захардкожено) | Обобщение |
|---|---|
| namespace `nir_liver` (из `STAND_URL`) | из topology/manifest |
| `DEMO001`, study UID (`conftest.py:25–30`) | demo-пациенты из `manifest.toml` |
| 13-стадийный DAG в `test_workflow.py` | spine из `manifest.toml` + generic-драйвер |
| seg-классы `mts/unclear/benign` (`stand_tool.py:41`) | из `clarinet_plan.utils.seg_utils` / manifest |
| `STORAGE_ROOT=/var/lib/clarinet/data` (`workflow_stand.py:34`) | читать из `settings.toml` VM по SSH |
| `PACS_HTTP`, orthanc creds | из topology |
| `setup_stand.sh` (c-get, dicom-worker, Orthanc-модальность) | в роль-провижн VM-PACS/VM-WORKER (3.1) |

Модули:
- `stand/driver.py` — класс `Stand` (httpx + ssh/scp + `wait_record`/`wait_record_data`), всё из конфигурации.
- `stand/tool.py` — VM-side (`FileRepository.resolve_file`, NIfTI-геометрия, синтез `.seg.nrrd` под seg-классы проекта).
- `stand/conftest.py` — фикстуры `stand` (топология из env), `project_fixture` (загрузка `manifest.toml`).
- generic-драйвер `stand/runner.py` — проходит spine из manifest, на каждой стадии вызывает per-project хук.

**Deliverable Фазы 4**: сценарий nir_liver гоняется зелёным **через generic-драйвер** против multi-VM стенда.

### 3.5. Формат сценария / manifest (Фаза 4)

Гибрид (решение D4): декларативный TOML на «хребет» + тонкий Python на данные/ассершены.

`manifest.toml`:
```toml
[project]
name = "nir_liver"
path_prefix = "/nir_liver/"

[[demo_patient]]
id = "DEMO001"
study_uid = "1.2.826.0.1.3680043.10.9999.501087842010466818539916187267295891"

# Хребет (spine) DAG: упорядоченные record-типы и ожидаемый терминальный статус
[[spine]]
record_type = "first-check"
expect = "finished"
mode = "form"           # form | replay | segment | inject | slicer | auto

[[spine]]
record_type = "segment-prospective-ct"
expect = "finished"
mode = "replay"         # воспроизвести реальную захваченную сегментацию (3.3);
                        # fallback: "segment" (синтез) | "slicer" (реальный Slicer на хосте)
replay_seg = "segment-prospective-ct.seg.nrrd"
seg_classes = ["mts", "unclear", "benign"]

[[spine]]
record_type = "mdk-conclusion"
mode = "inject"         # GPU-печень: подложить artifacts/, потом форма
inject_file = "auto-liver"

# Точки гонок: что проверять на конкуррентность/сериализацию
[[race]]
kind = "prefill_vs_checkfiles"   # расширить окно через CLARINET_PREFILL_DELAY, проверить preparing/blocked
record_type = "mdk-conclusion"
data_key = "lesion_cluster_mapping"

[[race]]
kind = "parallel_branches"        # anon/archive ветки — обе достигают ожидаемых состояний
branches = ["segment-ct-single", "segment-ct-with-archive"]
```

`scenario.py` (тонкий, per-project) — только то, что нельзя декларативно:
```python
def submit_mdk_conclusion(stand, record): ...      # данные формы (lesion fields)
def submit_pathomorphology(stand, record): ...     # mandard_trg, margin_status, ...
def assert_resection_report(stand, record): ...    # кастомные проверки (lesion→cluster покрытие)
```

**Покрытие гонок** — харнесс даёт примитивы:
- `wait_record_data` (уже есть — закрывает prefill-гонку);
- `CLARINET_PREFILL_DELAY` (test-only settings hook) — расширить окно prefill, убедиться что
  `preparing→pending` ре-валидация (`models/record.py:151`) держит сериализацию (record уходит в `blocked`, не `pending`);
- параллельные ветки — submit обе, ассертить оба терминальных состояния (прототип оставлял их `pending` намеренно —
  теперь покрываем явно).

### 3.6. Slicer-стадии: три уровня достоверности (Фаза 5)

Сегментационные стадии проходятся одним из трёх способов (выбор per-stage в `manifest.toml`):

1. **`replay`** (рекоменд. default, где есть захват) — воспроизвести **реальную пользовательскую сегментацию** из
   фикстуры (3.3): положить захваченный `.seg.nrrd` на резолвнутый путь, отправить через `POST /data`. Реальная
   геометрия и краевые случаи, детерминизм, быстро. Не покрывает сам Slicer-валидатор, но гоняет downstream
   (combine-resection, lesion→cluster mapping) на **настоящих** данных.
2. **`slicer`** (opt-in/nightly) — headless Slicer на хосте (`run-headless.sh`, порт 2016) тянет контекст из VM-STAND,
   гоняет реальный Slicer-скрипт проекта (`plan/scripts/*.py`), отдаёт результат через **полный валидированный путь**
   (context-hydration + record-data-validator). Самая высокая достоверность, самый медленный.
3. **`segment`/`synthetic`** (fallback) — синтез геометрически-консистентного `.seg.nrrd` (как в прототипе), когда
   захвата нет (новый record-тип, нет прод-данных).

Решение D3 (уточнено): **default — `replay`** там, где есть захваченные сегментации; `synthetic` — fallback без захвата;
`slicer` — opt-in/nightly для покрытия Slicer-валидатора.

---

## 4. Интеграция в test-all-stages (Фаза 6)

Multi-VM + загрузка фикстуры + полный DAG = ~10–20 мин/проект — тяжелее текущего single-VM e2e.
Поэтому (решение D2): **отдельный target `make test-stands`, не внутри 40-мин `test-all-stages` по умолчанию**.

- Новый target `vm-test-stand` / `test-stands`:
  - `make vm-topology-up TOPOLOGY=<project>` → `clarinet stand load <fixture>` → `pytest deploy/test/stand/ + <project>/tests/workflow/`.
  - env: `STAND_PROJECT=<name>`, `KEEP_STAND=1`, `SKIP_STAND` (в обычном CI=1).
  - переиспользовать golden-образ (services уже установлены, включаются по роли).
- В `test-all-stages` оставить текущий single-VM e2e (Stage 7). Стенды — **nightly job** / on-demand
  с увеличенным бюджетом времени и проверкой RAM хоста (3 VM).
- Точка вставки, если всё же внутри: после Stage 7 (e2e), перед Stage 8 (cleanup), под флагом `SKIP_STAND`.

---

## 5. Дорожная карта (фазы)

| Фаза | Содержание | Deliverable | Зависит от |
|---|---|---|---|
| **0. Фундамент** | Доделать параметризацию service-discovery (DB/Rabbit/PACS/AET host'ы), декомпозировать `install-clarinet.sh` на роли | один golden + установка по роли | — |
| **1. Multi-VM** | `topology.toml`, `vm.sh` multi-VM lifecycle, межвм-сеть, wiring по IP, роль PACS/worker | `make vm-topology-up` поднимает stand+pacs+worker, видят друг друга | 0 |
| **2. DB-аноним** | `clarinet anon scrub-db`, консистентный с DICOM-анонимизацией, schema-aware скраб `record.data`, аудит PHI | живой дамп → анонимный, FileRepository резолвит, аудит зелёный | — (паралл. 1) |
| **3. Фикстур-пайплайн** | capture+anonymize+package+registry+load | `clarinet stand capture` / `clarinet stand load` | 1, 2 |
| **4. Обобщённый харнесс** | перенос прототипа в репо, `manifest.toml`+`scenario.py`, generic-драйвер | nir_liver зелёный через generic-драйвер на multi-VM | 3 |
| **5. Slicer real + гонки** | `mode=slicer` (реальный headless Slicer), race-ассершены/задержки | реальные Slicer-стадии + покрытие prefill/parallel гонок | 4 |
| **6. CI** | `make test-stands`, nightly job, флаги SKIP/KEEP, отчётность | стенд в CI (nightly), отчёт в `/tmp/clarinet-test-report.json` | 4 |
| **7. Второй проект** | онбординг ещё одного живого проекта | доказана обобщённость (2 проекта на одном движке) | 4 |

Параллелизм: Фаза 2 (DB-аноним) независима от 1 — делать параллельно. 3 ← (1,2). 4 ← 3. 5/6/7 ← 4.

---

## 6. Открытые решения (с рекомендацией)

Зафиксировано (2026-06-13): **D1, D2 решены** (см. ниже). Старт реализации — **отложен**: документ
остаётся design-doc'ом, кодирование не начинаем до отдельного указания. D3–D6 — рекомендации в силе,
не противоречат; пересмотр по запросу.

| # | Решение | Опции | Итог |
|---|---|---|---|
| D1 | Хранилище фикстур | git-lfs / локальный диск+checksum→S3 / baked | ✅ **checksum в репо, артефакты на локальном диске → позже S3** |
| D2 | Место в CI | внутри `test-all-stages` / отдельный nightly | ✅ **отдельный `make test-stands` nightly** |
| D3 | Достоверность Slicer | всегда реальный / replay+synthetic+slicer | _реком._ `replay` (реальные захваты) default, `synthetic` fallback, `slicer` opt-in |
| D4 | Формат сценария | чистый Python / чистый декларатив / гибрид | _реком._ гибрид: `manifest.toml` + тонкий `scenario.py` |
| D5 | Golden-образы | 3 роль-образа / 1 образ + роль на deploy | _реком._ 1 образ, роль включается на deploy (проще bake) |
| D6 | DICOM retrieve mode на стенде | c-get / c-move | _реком._ c-get (нет reverse-сети PACS→worker) |

По D3–D6 при другом предпочтении — скажи, перепланирую соответствующую фазу.

---

## 7. Риски и смягчение

| Риск | Смягчение |
|---|---|
| **RAM хоста**: 3 VM (stand 4G + pacs 2G + worker 4G ≈ 10–12G) | проверить бюджет; PACS/worker ужать; вариант «worker на VM-STAND» для слабых хостов |
| **Общий storage stand↔worker** (NFS): новая инфра-зависимость (права, латентность, консистентность) | экспорт `storage_path` по NFS в тест-сети; альтернатива на слабых хостах — co-locate воркера со стендом (теряем изоляцию воркера) |
| **Утечка PHI** при неполной DB-анонимизации | обязательный аудит-скан дампа (Фаза 2.6); review-чеклист на новые PII-поля |
| **Детерминизм** пайплайна (GPU-стадии не воспроизвести) | inject-фикстуры для нерегенерируемого; пинить версии в manifest |
| **Дрейф схемы** vs старые фикстуры | manifest пинит версию; `clarinet db upgrade` на load; CI-проверка миграции фикстур |
| **Флейки** из-за реального async + межвм-сети | устойчивые polling-ожидания (есть `wait_record`/`wait_record_data`); таймауты; ретраи только по сети, не по логике |
| **Секреты в фикстуре** | `settings.toml` без секретов; генерить creds на load (`generate-settings.sh`) |

---

## 8. Связь с текущей веткой

`worktree-vm-downstream-project` уже правит `deploy/vm/vm.conf` (PACS был на отдельной «klara box»
`192.168.122.151`, временно возвращён на localhost). Это **семя разнесения PACS** из 3.1 — параметр
`PACS_HOST`/`pacs_host` уже отделён от localhost. Фаза 1 продолжает начатое: из «PACS на отдельном хосте»
(downstream-конфиг) в полноценную роль VM-PACS topology-конфига.
