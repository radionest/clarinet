## ADDED Requirements

### Requirement: Capability route guards
The patient, study and series routes SHALL be guarded by capability rather than admin role: `Patients` and `PatientDetail` by `patients:read`; `PatientNew` by `patients:write`; `Studies` and `StudyDetail` by `studies:read`; `SeriesDetail` by `series:read`. A user lacking the capability SHALL be redirected away.

#### Scenario: Capability holder enters
- **WHEN** a non-admin holding `patients:read` navigates to the patients list
- **THEN** the page renders

#### Scenario: Missing capability redirects
- **WHEN** a non-admin without `studies:read` navigates to a study detail URL
- **THEN** they are redirected to their landing route

#### Scenario: Read without write
- **WHEN** a non-admin holding only `patients:read` navigates to the new-patient URL
- **THEN** they are redirected to their landing route

### Requirement: Per-link navigation visibility
Each nav link SHALL be shown independently: Patients with `patients:read`; Studies with `studies:read`; Reports and Quarto Reports with `reports`; Records, Record Types, Workflow, Activity and Admin for admins only.

#### Scenario: Mixed capabilities
- **WHEN** a non-admin holds `patients:read` and `reports`
- **THEN** the nav shows Patients, Reports and Quarto Reports, and no other section link

#### Scenario: Admin
- **WHEN** an admin is logged in
- **THEN** the nav shows every link, as before

### Requirement: Reports-only landing
A user SHALL be treated as reports-only solely when `reports` is their only capability.

#### Scenario: Reports plus patients
- **WHEN** a non-admin holds `reports` and `patients:read`
- **THEN** their landing route is the dashboard, not the reports page

#### Scenario: Reports only
- **WHEN** a non-admin holds only `reports`
- **THEN** their landing route is the reports page

### Requirement: Write actions require write capability
"New Patient", patient Delete and patient Anonymize SHALL be shown only with `patients:write`. Study Delete SHALL be shown only with `studies:write`. "Add Record" SHALL remain admin-only.

#### Scenario: Reader sees no write actions
- **WHEN** a non-admin holding only `patients:read` opens a patient detail page
- **THEN** no Delete, Anonymize or Add Record control is rendered

### Requirement: Admin-only sections stay hidden and idle
For a non-admin, the patient detail page SHALL neither render nor initialise the PACS section and the activity feed. The studies section SHALL be hidden without `studies:read`. Series rows on study detail SHALL be plain text without `series:read`.

#### Scenario: No forbidden requests on load
- **WHEN** a non-admin holding `patients:read` opens a patient detail page
- **THEN** no request is sent to the PACS search or activity endpoints, and no error toast appears

### Requirement: Entity links follow capabilities
Links to a patient, study or series SHALL render as links when the user holds the matching `:read` capability and as plain text otherwise.

#### Scenario: Record page link
- **WHEN** a non-admin holding `patients:read` views a record
- **THEN** the patient identifier is a link to the patient detail route

### Requirement: Series detail reads the scoped records bucket
The series detail page SHALL load its records through the role-scoped records bucket keyed by series, not from the series payload.

#### Scenario: Bucket key includes series
- **WHEN** the bucket topic is built for a query with a series uid
- **THEN** the topic differs from the topic of the same query without it
