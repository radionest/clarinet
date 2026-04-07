// Cache module — self-contained MVU for global entity caches
import api/admin
import api/models.{
  type Patient, type Record, type RecordType, type RecordTypeStats, type Series,
  type Study, type User,
}
import api/patients
import api/records
import api/studies
import api/types.{type ApiError}
import api/users
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise.{type Promise}
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
      let studies =
        list.fold(list_, model.studies, fn(acc, s) {
          dict.insert(acc, s.study_uid, s)
        })
      #(Model(..model, studies: studies), effect.none(), [Loading(False)])
    }

    StudiesLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load studies"),
    ])

    // --- Records (role-aware) ---
    LoadRecords(is_admin) -> {
      let fetch_fn = case is_admin {
        True -> records.get_records
        False -> records.get_my_records
      }
      #(model, load_effect(fetch_fn, RecordsLoaded), [Loading(True)])
    }

    RecordsLoaded(Ok(list_)) -> {
      let records =
        list.fold(list_, model.records, fn(acc, r) {
          case r.id {
            Some(id) -> dict.insert(acc, int.to_string(id), r)
            None -> acc
          }
        })
      #(Model(..model, records: records), effect.none(), [Loading(False)])
    }

    RecordsLoaded(Error(err)) -> #(model, effect.none(), [
      ApiFailure(err, "Failed to load records"),
    ])

    // --- Users ---
    LoadUsers -> #(model, load_effect(users.get_users, UsersLoaded), [
      Loading(True),
    ])

    UsersLoaded(Ok(list_)) -> {
      let users =
        list.fold(list_, model.users, fn(acc, u) { dict.insert(acc, u.id, u) })
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
        list.fold(list_, model.patients, fn(acc, p) {
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
        list.fold(list_, model.record_types, fn(acc, rt) {
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

// --- Private helpers ---

fn load_effect(
  api_call: fn() -> Promise(Result(a, ApiError)),
  on_result: fn(Result(a, ApiError)) -> Msg,
) -> Effect(Msg) {
  use dispatch <- effect.from
  api_call() |> promise.tap(fn(r) { dispatch(on_result(r)) })
  Nil
}
