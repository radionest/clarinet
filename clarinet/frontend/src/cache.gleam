// Cache module — self-contained MVU for global entity caches
import api/admin
import api/models.{
  type Patient, type Record, type RecordType, type RecordTypeStats, type Series,
  type Study, type User,
}
import api/patients
import api/record_page.{type RecordPage}
import api/records
import api/studies
import api/types.{type ApiError}
import api/users
import cache/bucket.{type Bucket, type BucketKey, type BucketStatus}
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/effect.{type Effect}

// --- Model ---

pub type Model {
  Model(
    studies: Dict(String, Study),
    series: Dict(String, Series),
    records: Dict(String, Record),
    record_types: Dict(String, RecordType),
    patients: Dict(String, Patient),
    users: Dict(String, User),
    record_type_stats: Option(List(RecordTypeStats)),
    record_buckets: Dict(String, Bucket),
  )
}

// --- Msg ---

pub type Msg {
  // Bulk loads
  LoadStudies
  StudiesLoaded(Result(List(Study), ApiError))
  LoadRecords(is_admin: Bool)
  RecordsLoaded(Result(List(Record), ApiError))
  LoadUsers
  UsersLoaded(Result(List(User), ApiError))
  LoadPatients
  PatientsLoaded(Result(List(Patient), ApiError))
  LoadRecordTypes
  RecordTypesLoaded(Result(List(RecordType), ApiError))
  LoadRecordTypeStats
  RecordTypeStatsLoaded(Result(List(RecordTypeStats), ApiError))

  // Single-entity loads
  LoadRecordDetail(id: String, current_user: Option(User))
  RecordDetailLoaded(
    current_user: Option(User),
    result: Result(Record, ApiError),
  )
  LoadPatientDetail(id: String)
  PatientDetailLoaded(Result(Patient, ApiError))

  // Auto-assign follow-up (result of OutMsg.AutoAssign)
  AutoAssignResult(Result(Record, ApiError))

  // Bucket-based pagination
  FetchBucketMsg(key: BucketKey)
  FetchMoreMsg(key: BucketKey)
  BucketLoaded(key: BucketKey, result: Result(RecordPage, ApiError))
  BucketMoreLoaded(key: BucketKey, result: Result(RecordPage, ApiError))
  InvalidateBucketMsg(key: BucketKey)
  InvalidateAllRecordBucketsMsg
}

// --- OutMsg ---

pub type OutMsg {
  /// Route through main.handle_api_error (session expiry, logging, etc.)
  ApiFailure(err: ApiError, fallback_msg: String)
  /// Toggle store.Model.loading (global flag still lives in store)
  Loading(Bool)
  /// Auto-assign a pending/inwork record to the current user
  AutoAssign(record_id: Int, user_id: String)
}

// --- Init ---

pub fn init() -> Model {
  Model(
    studies: dict.new(),
    series: dict.new(),
    records: dict.new(),
    record_types: dict.new(),
    patients: dict.new(),
    users: dict.new(),
    record_type_stats: None,
    record_buckets: dict.new(),
  )
}

// --- Update ---

pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    // --- Studies ---
    LoadStudies -> #(model, load_effect(studies.get_studies, StudiesLoaded), [
      Loading(True),
    ])

    StudiesLoaded(Ok(list_)) -> {
      // Rebuild from server snapshot so deleted entities disappear.
      let studies =
        list.fold(list_, dict.new(), fn(acc, s) {
          dict.insert(acc, s.study_uid, s)
        })
      #(Model(..model, studies: studies), effect.none(), [Loading(False)])
    }

    StudiesLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load studies"),
    ])

    // --- Records (legacy, kept for compatibility) ---
    LoadRecords(_is_admin) -> #(model, effect.none(), [])

    RecordsLoaded(_) -> #(model, effect.none(), [])

    // --- Users ---
    LoadUsers -> #(model, load_effect(users.get_users, UsersLoaded), [
      Loading(True),
    ])

    UsersLoaded(Ok(list_)) -> {
      let users =
        list.fold(list_, dict.new(), fn(acc, u) { dict.insert(acc, u.id, u) })
      #(Model(..model, users: users), effect.none(), [Loading(False)])
    }

    UsersLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load users"),
    ])

    // --- Patients ---
    LoadPatients -> #(model, load_effect(patients.get_patients, PatientsLoaded), [
      Loading(True),
    ])

    PatientsLoaded(Ok(list_)) -> {
      let patients =
        list.fold(list_, dict.new(), fn(acc, p) {
          dict.insert(acc, p.id, p)
        })
      #(Model(..model, patients: patients), effect.none(), [Loading(False)])
    }

    PatientsLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load patients"),
    ])

    // --- Record Types ---
    LoadRecordTypes -> #(
      model,
      load_effect(records.get_record_types, RecordTypesLoaded),
      [Loading(True)],
    )

    RecordTypesLoaded(Ok(list_)) -> {
      let record_types =
        list.fold(list_, dict.new(), fn(acc, rt) {
          dict.insert(acc, rt.name, rt)
        })
      #(Model(..model, record_types: record_types), effect.none(), [
        Loading(False),
      ])
    }

    RecordTypesLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load record types"),
    ])

    // --- Record Type Stats ---
    LoadRecordTypeStats -> #(
      model,
      load_effect(admin.get_record_type_stats, RecordTypeStatsLoaded),
      [Loading(True)],
    )

    RecordTypeStatsLoaded(Ok(stats)) -> #(
      Model(..model, record_type_stats: Some(stats)),
      effect.none(),
      [Loading(False)],
    )

    RecordTypeStatsLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load record type statistics"),
    ])

    // --- Record Detail (with auto-assign) ---
    LoadRecordDetail(id, current_user) -> {
      let eff = {
        use dispatch <- effect.from
        records.get_record(id)
        |> promise.tap(fn(result) {
          dispatch(RecordDetailLoaded(current_user, result))
        })
        Nil
      }
      #(model, eff, [Loading(True)])
    }

    RecordDetailLoaded(current_user, Ok(record)) -> {
      let new_model = put_record(model, record)
      // Auto-assign unassigned Pending/InWork records to the current user
      let out_msgs = case record.user_id, record.status, current_user {
        None, types.Pending, Some(models.User(id: uid, ..))
        | None, types.InWork, Some(models.User(id: uid, ..))
        ->
          case record.id {
            Some(rid) -> [Loading(False), AutoAssign(rid, uid)]
            None -> [Loading(False)]
          }
        _, _, _ -> [Loading(False)]
      }
      #(new_model, effect.none(), out_msgs)
    }

    RecordDetailLoaded(_, Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load record"),
    ])

    // --- Patient Detail ---
    LoadPatientDetail(id) -> {
      let eff = {
        use dispatch <- effect.from
        patients.get_patient(id)
        |> promise.tap(fn(result) { dispatch(PatientDetailLoaded(result)) })
        Nil
      }
      #(model, eff, [Loading(True)])
    }

    PatientDetailLoaded(Ok(patient)) -> #(
      put_patient(model, patient),
      effect.none(),
      [Loading(False)],
    )

    PatientDetailLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load patient"),
    ])

    // --- Auto-assign follow-up ---
    AutoAssignResult(Ok(record)) -> #(
      put_record(model, record),
      effect.none(),
      [],
    )

    // Silently ignore failed auto-assign — admin can still work without it
    AutoAssignResult(Error(_)) -> #(model, effect.none(), [])

    // --- Record Buckets ---
    FetchBucketMsg(key) -> {
      let topic = bucket.key_to_topic(key)
      let now = now_ms()
      let is_fresh = case dict.get(model.record_buckets, topic) {
        Ok(b) -> bucket.is_fresh(b.status, now)
        Error(_) -> False
      }
      case is_fresh {
        True -> #(model, effect.none(), [])
        False -> {
          // Preserve existing items for stale-while-revalidate
          let b = case dict.get(model.record_buckets, topic) {
            Ok(existing) -> bucket.Bucket(..existing, status: bucket.Loading)
            Error(_) ->
              bucket.Bucket(
                key: key,
                status: bucket.Loading,
                items: [],
                next_cursor: None,
              )
          }
          let new_buckets = dict.insert(model.record_buckets, topic, b)
          #(
            Model(..model, record_buckets: new_buckets),
            fetch_bucket_effect(key, None),
            [],
          )
        }
      }
    }

    FetchMoreMsg(key) -> {
      let topic = bucket.key_to_topic(key)
      case dict.get(model.record_buckets, topic) {
        Ok(b) ->
          case b.next_cursor {
            Some(_cursor) -> {
              let updated =
                bucket.Bucket(..b, status: bucket.LoadingMore(now_ms()))
              let new_buckets =
                dict.insert(model.record_buckets, topic, updated)
              #(
                Model(..model, record_buckets: new_buckets),
                fetch_bucket_more_effect(key, b.next_cursor),
                [],
              )
            }
            None -> #(model, effect.none(), [])
          }
        Error(_) -> #(model, effect.none(), [])
      }
    }

    BucketLoaded(key, Ok(page)) -> {
      let topic = bucket.key_to_topic(key)
      let b =
        bucket.Bucket(
          key: key,
          status: bucket.Live(now_ms()),
          items: page.items,
          next_cursor: page.next_cursor,
        )
      let new_buckets = dict.insert(model.record_buckets, topic, b)
      // Also upsert items into secondary records dict
      let new_records = upsert_records_dict(model.records, page.items)
      #(
        Model(..model, record_buckets: new_buckets, records: new_records),
        effect.none(),
        [],
      )
    }

    BucketLoaded(key, Error(err)) -> {
      let topic = bucket.key_to_topic(key)
      // Preserve existing items on failure (stale-while-revalidate)
      let b = case dict.get(model.record_buckets, topic) {
        Ok(existing) ->
          bucket.Bucket(..existing, status: bucket.Failed(api_error_msg(err)))
        Error(_) ->
          bucket.Bucket(
            key: key,
            status: bucket.Failed(api_error_msg(err)),
            items: [],
            next_cursor: None,
          )
      }
      let new_buckets = dict.insert(model.record_buckets, topic, b)
      #(
        Model(..model, record_buckets: new_buckets),
        effect.none(),
        [ApiFailure(err, "Failed to load records")],
      )
    }

    BucketMoreLoaded(key, Ok(page)) -> {
      let topic = bucket.key_to_topic(key)
      case dict.get(model.record_buckets, topic) {
        Ok(b) -> {
          let updated =
            bucket.Bucket(
              ..b,
              status: bucket.Live(now_ms()),
              items: list.flatten([b.items, page.items]),
              next_cursor: page.next_cursor,
            )
          let new_buckets =
            dict.insert(model.record_buckets, topic, updated)
          let new_records = upsert_records_dict(model.records, page.items)
          #(
            Model(..model, record_buckets: new_buckets, records: new_records),
            effect.none(),
            [],
          )
        }
        Error(_) -> #(model, effect.none(), [])
      }
    }

    BucketMoreLoaded(key, Error(err)) -> {
      let topic = bucket.key_to_topic(key)
      case dict.get(model.record_buckets, topic) {
        Ok(b) -> {
          // Revert to Live status, keep existing items
          let updated =
            bucket.Bucket(..b, status: bucket.Live(now_ms()))
          let new_buckets =
            dict.insert(model.record_buckets, topic, updated)
          #(
            Model(..model, record_buckets: new_buckets),
            effect.none(),
            [ApiFailure(err, "Failed to load more records")],
          )
        }
        Error(_) -> #(model, effect.none(), [])
      }
    }

    InvalidateBucketMsg(key) -> {
      let topic = bucket.key_to_topic(key)
      case dict.get(model.record_buckets, topic) {
        Ok(b) -> {
          let new_buckets =
            dict.insert(model.record_buckets, topic, bucket.mark_stale(b))
          #(Model(..model, record_buckets: new_buckets), effect.none(), [])
        }
        Error(_) -> #(model, effect.none(), [])
      }
    }

    InvalidateAllRecordBucketsMsg -> {
      let new_buckets =
        dict.map_values(model.record_buckets, fn(_k, b) {
          bucket.mark_stale(b)
        })
      #(Model(..model, record_buckets: new_buckets), effect.none(), [])
    }
  }
}

// --- Cache mutators (called from main.apply_out_msgs on CacheX OutMsg) ---

pub fn put_study(model: Model, study: Study) -> Model {
  Model(..model, studies: dict.insert(model.studies, study.study_uid, study))
}

pub fn put_series(model: Model, s: Series) -> Model {
  Model(..model, series: dict.insert(model.series, s.series_uid, s))
}

pub fn put_record(model: Model, record: Record) -> Model {
  case record.id {
    Some(id) ->
      Model(..model, records: dict.insert(model.records, int.to_string(id), record))
    None -> model
  }
}

pub fn put_record_type(model: Model, rt: RecordType) -> Model {
  Model(..model, record_types: dict.insert(model.record_types, rt.name, rt))
}

pub fn put_patient(model: Model, patient: Patient) -> Model {
  Model(..model, patients: dict.insert(model.patients, patient.id, patient))
}

// --- Bucket helpers (public) ---

pub fn bucket_items(model: Model, key: BucketKey) -> List(Record) {
  let topic = bucket.key_to_topic(key)
  case dict.get(model.record_buckets, topic) {
    Ok(b) -> b.items
    Error(_) -> []
  }
}

pub fn bucket_has_more(model: Model, key: BucketKey) -> Bool {
  let topic = bucket.key_to_topic(key)
  case dict.get(model.record_buckets, topic) {
    Ok(b) -> option.is_some(b.next_cursor)
    Error(_) -> False
  }
}

pub fn bucket_status(model: Model, key: BucketKey) -> BucketStatus {
  let topic = bucket.key_to_topic(key)
  case dict.get(model.record_buckets, topic) {
    Ok(b) -> b.status
    Error(_) -> bucket.Cold
  }
}

pub fn upsert_record_in_buckets(model: Model, record: Record) -> Model {
  let new_buckets =
    dict.map_values(model.record_buckets, fn(_k, b) {
      let new_items =
        list.map(b.items, fn(r) {
          case r.id == record.id {
            True -> record
            False -> r
          }
        })
      bucket.Bucket(..b, items: new_items)
    })
  Model(..model, record_buckets: new_buckets)
}

// --- Private helpers ---

fn bucket_key_to_filter(key: BucketKey) -> List(#(String, json.Json)) {
  case key {
    bucket.RecordsAll -> []
    bucket.RecordsMine(uid) -> [#("user_id", json.string(uid))]
    bucket.RecordsByPatient(pid) -> [#("patient_id", json.string(pid))]
    bucket.RecordsByStudy(suid) -> [#("study_uid", json.string(suid))]
    bucket.RecordsByRecordType(name) -> [
      #("record_type_name", json.string(name)),
    ]
  }
}

fn fetch_bucket_effect(key: BucketKey, cursor: Option(String)) -> Effect(Msg) {
  let filter = bucket_key_to_filter(key)
  use dispatch <- effect.from
  records.find_records(filter, cursor, 100)
  |> promise.tap(fn(result) { dispatch(BucketLoaded(key, result)) })
  Nil
}

fn fetch_bucket_more_effect(
  key: BucketKey,
  cursor: Option(String),
) -> Effect(Msg) {
  let filter = bucket_key_to_filter(key)
  use dispatch <- effect.from
  records.find_records(filter, cursor, 100)
  |> promise.tap(fn(result) { dispatch(BucketMoreLoaded(key, result)) })
  Nil
}

fn upsert_records_dict(
  records_dict: Dict(String, Record),
  items: List(Record),
) -> Dict(String, Record) {
  list.fold(items, records_dict, fn(acc, r) {
    case r.id {
      Some(id) -> dict.insert(acc, int.to_string(id), r)
      None -> acc
    }
  })
}

fn api_error_msg(err: ApiError) -> String {
  case err {
    types.NetworkError(msg) -> msg
    types.ParseError(msg) -> msg
    types.AuthError(msg) -> msg
    types.ServerError(_, msg) -> msg
    types.ValidationError(_) -> "Validation error"
  }
}

@external(javascript, "../cache_ffi.mjs", "now_ms")
fn now_ms() -> Int

fn load_effect(
  api_call: fn() -> Promise(Result(a, ApiError)),
  on_result: fn(Result(a, ApiError)) -> Msg,
) -> Effect(Msg) {
  use dispatch <- effect.from
  api_call() |> promise.tap(fn(r) { dispatch(on_result(r)) })
  Nil
}
