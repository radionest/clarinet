## ADDED Requirements

### Requirement: Masked payloads for callers without unmask
A caller without `patients:unmask` SHALL receive patient, study and series payloads with anonymized identifiers. A caller holding `patients:unmask` (including superusers and the `admin` role) SHALL receive real identifiers.

| Model | Masked fields |
|---|---|
| `PatientRead` | `id` = `anon_id`, `name` = `anon_name` |
| `StudyRead` | `study_uid` = `anon_uid`, `patient_id` = patient `anon_id`, `date` = `1976-01-01`, `study_description` = null, nested `patient` masked |
| `SeriesRead` and nested series | `series_uid` = `anon_uid`, `study_uid` = study `anon_uid`, nested `study` masked |

All other fields SHALL pass through unchanged.

When `settings.anon_per_study_patient_id` is true, the masked patient identifier on a study or series payload SHALL be the per-study hash of that study (the value `Files.per_study_patient_id` yields, as in record masking) for both `patient_id` and the nested patient's `id` and `name`, and the nested patient's `auto_id`, `anon_id` and `anon_name` SHALL be null.

#### Scenario: Per-study mode hides the cross-study identifier
- **WHEN** `anon_per_study_patient_id=True` and a masked caller with `studies:read` lists two anonymized studies of one patient
- **THEN** each study's `patient_id` is its own per-study hash, the two values differ, and neither payload contains the patient's `auto_id`, `anon_id` or `anon_name`

#### Scenario: Masked patient
- **WHEN** a caller with `patients:read` and no `patients:unmask` fetches an anonymized patient
- **THEN** `id` equals the patient's `anon_id`, `name` equals its `anon_name`, and the real id and name appear nowhere in the response

#### Scenario: Masked study
- **WHEN** a masked caller with `studies:read` fetches an anonymized study
- **THEN** `study_uid` is the `anon_uid`, `patient_id` is the patient's `anon_id`, `date` is `1976-01-01`, `study_description` is null, and the nested patient is masked

#### Scenario: Unmasked caller
- **WHEN** a caller holding `patients:unmask` and `patients:read` fetches the same patient
- **THEN** the payload carries the real `id` and `name`

#### Scenario: New field must be classified
- **WHEN** the masked payload is compared against the allowlist of pass-through fields
- **THEN** any field that is neither masked nor on the allowlist fails the test

### Requirement: Masked callers see anonymized rows only
For a masked caller, list and fetch endpoints SHALL return only anonymized rows: patients with `anon_name` set; studies with `anon_uid` set whose patient is anonymized; series with `anon_uid` set whose study is anonymized. The same rule SHALL apply to nested study and series lists.

#### Scenario: Un-anonymized patient absent from list
- **WHEN** a masked caller lists patients and one patient has no `anon_name`
- **THEN** that patient is absent from the response

#### Scenario: Un-anonymized series absent from nested list
- **WHEN** a masked caller fetches an anonymized study containing one series without `anon_uid`
- **THEN** the nested `series` list omits that series

#### Scenario: Random series
- **WHEN** a masked caller calls `GET /series/random`
- **THEN** the returned series has an `anon_uid`

### Requirement: Anon-only addressing for masked callers
For a masked caller, a path identifier SHALL be interpreted only as an anonymized identifier. A real identifier SHALL yield 404, identical to an unknown identifier.

#### Scenario: Fetch by anon id
- **WHEN** a masked caller requests `GET /patients/{anon_id}` for an anonymized patient
- **THEN** the response is 200 with the masked patient

#### Scenario: Real id is not resolvable
- **WHEN** a masked caller requests `GET /patients/{real_id}` for an existing patient
- **THEN** the response is 404

#### Scenario: Study by anon uid
- **WHEN** a masked caller requests `GET /studies/{anon_uid}`
- **THEN** the response is 200 with the masked study

### Requirement: Anon fallback for unmasked callers
For an unmasked caller, a path identifier SHALL be resolved as a literal identifier first and as an anonymized identifier second.

#### Scenario: Link from a masked record
- **WHEN** an unmasked non-superuser requests `GET /patients/{anon_id}`
- **THEN** the response is 200 with the unmasked patient

#### Scenario: Literal id wins
- **WHEN** a patient's real id matches the `{prefix}_N` pattern and an unmasked caller requests it
- **THEN** the patient with that literal id is returned

### Requirement: Nested data follows capabilities
`PatientRead.studies` SHALL be empty for a caller without `studies:read`. Nested `series` lists SHALL be empty for a caller without `series:read`.

#### Scenario: Patients without studies capability
- **WHEN** a caller with `patients:read` and no `studies:read` fetches a patient that has studies
- **THEN** `studies` is an empty list

#### Scenario: Studies without series capability
- **WHEN** a caller with `studies:read` and no `series:read` fetches a study that has series
- **THEN** `series` is an empty list

### Requirement: Records do not ride on series
`SeriesRead.records` SHALL be empty for every non-admin caller. `POST /series/find` with non-empty `records` criteria from a non-admin SHALL respond 403. Admin behaviour SHALL be unchanged.

#### Scenario: Non-admin series fetch
- **WHEN** a non-admin with `series:read` fetches a series that has records
- **THEN** `records` is an empty list

#### Scenario: Record criteria from non-admin
- **WHEN** a non-admin with `series:read` posts `/series/find` with a non-empty `records` list
- **THEN** the response is 403

#### Scenario: Admin unchanged
- **WHEN** an admin fetches the same series
- **THEN** `records` contains the series' records

### Requirement: Records search accepts anonymized study and series uids
For non-superuser callers, the `study_uid` and `series_uid` criteria of `POST /records/find` SHALL match either the real uid or the `anon_uid`. Role scoping of the result SHALL be unchanged.

#### Scenario: Records by anon study uid
- **WHEN** a non-superuser posts `/records/find` with `study_uid` set to a study's `anon_uid`
- **THEN** the result contains that study's records the caller's roles permit, and no others

#### Scenario: Superuser literal match
- **WHEN** a superuser posts `/records/find` with `study_uid` set to an `anon_uid`
- **THEN** the criterion is matched literally against the real uid column
