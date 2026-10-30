## ADDED Requirements

### Requirement: Entity capability vocabulary
The capability vocabulary SHALL include `patients:read`, `patients:write`, `patients:unmask`, `studies:read`, `studies:write`, `series:read` and `series:write` in addition to `reports`. Superusers and members of the `admin` role SHALL hold every capability.

#### Scenario: Admin holds all entity capabilities
- **WHEN** capabilities are resolved for a superuser or a member of the `admin` role
- **THEN** the result contains all seven entity capabilities and `reports`

#### Scenario: Unknown capability refuses startup
- **WHEN** `settings.role_capabilities` names a capability outside the vocabulary (e.g. `patients:raed`)
- **THEN** startup fails with `ConfigurationError` listing the unknown name

### Requirement: Write implies read
Capability resolution SHALL add `X:read` to a user's effective capabilities whenever `X:write` is granted, for `X` in patients, studies, series.

#### Scenario: Write-only grant
- **WHEN** a role maps only to `studies:write` and `patients:unmask`, and a user holds that role
- **THEN** the user's effective capabilities contain `studies:read`, `studies:write` and `patients:unmask`

### Requirement: Per-endpoint capability guards
Each patient, study and series endpoint SHALL require exactly the capability below, and SHALL respond 403 to an authenticated caller lacking it. The router SHALL NOT carry a router-wide admin dependency.

| Capability | Endpoints |
|---|---|
| `patients:read` | `GET /patients`, `GET /patients/{id}` |
| `patients:write` | `POST /patients`, `DELETE /patients/{id}`, `POST /patients/{id}/anonymize`, `POST /patients/{id}/file-events` |
| `studies:read` | `GET /studies`, `GET /studies/{uid}` |
| `studies:write` | `POST /studies`, `DELETE /studies/{uid}`, `POST /studies/{uid}/add_anonymized` |
| `series:read` | `GET /series`, `GET /series/random`, `GET /series/{uid}`, `POST /series/find`, `GET /studies/{uid}/series` |
| `series:write` | `POST /series`, `POST /series/{uid}/add_anonymized` |

#### Scenario: Read capability admits read, denies write
- **WHEN** a non-admin holding only `patients:read` calls `GET /patients` and then `DELETE /patients/{id}`
- **THEN** the first responds 200 and the second responds 403

#### Scenario: No capability
- **WHEN** an authenticated non-admin with no entity capability calls any endpoint in the table
- **THEN** the response is 403

#### Scenario: Areas are independent
- **WHEN** a non-admin holding only `patients:read` calls `GET /studies`
- **THEN** the response is 403

#### Scenario: Every route is guarded
- **WHEN** the routes of the patient/study/series router are enumerated
- **THEN** each route has a capability dependency

#### Scenario: Service token unaffected
- **WHEN** a request authenticated by the internal service token calls any endpoint in the table
- **THEN** it is admitted as before

### Requirement: Writers must be unmasked
Startup SHALL fail with `ConfigurationError` when a role is granted any `*:write` capability without `patients:unmask`.

#### Scenario: Masked writer role
- **WHEN** `role_capabilities` maps a role to `["patients:read", "patients:write"]`
- **THEN** startup fails with `ConfigurationError` naming the role

### Requirement: Unmask requires a read capability
Startup SHALL fail with `ConfigurationError` when a role is granted `patients:unmask` and, after write-implies-read, holds no `*:read` capability.

#### Scenario: Unmask alone
- **WHEN** `role_capabilities` maps a role to `["patients:unmask"]`
- **THEN** startup fails with `ConfigurationError` naming the role

#### Scenario: Unmask with write only
- **WHEN** `role_capabilities` maps a role to `["patients:unmask", "series:write"]`
- **THEN** startup succeeds

### Requirement: Per-study anonymization forbids masked patient access
When `settings.anon_per_study_patient_id` is true, startup SHALL fail with `ConfigurationError` for any role granted a `patients:*` capability without `patients:unmask`.

#### Scenario: Masked patient reader in per-study mode
- **WHEN** `anon_per_study_patient_id=True` and a role maps to `["patients:read"]`
- **THEN** startup fails with `ConfigurationError` naming the role

#### Scenario: Masked study reader in per-study mode
- **WHEN** `anon_per_study_patient_id=True` and a role maps to `["studies:read", "series:read"]`
- **THEN** startup succeeds

#### Scenario: Same config in per-patient mode
- **WHEN** `anon_per_study_patient_id=False` and a role maps to `["patients:read"]`
- **THEN** startup succeeds
