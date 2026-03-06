# E2E Plan: User Registration → Role Assignment → Restricted Access

## File

`tests/e2e/test_user_management.py`

## Goal

Test the complete user management lifecycle: admin creates roles, registers users,
assigns roles, and verifies role-based access control across the system. Covers
user CRUD, role CRUD, activation/deactivation, and authorization enforcement.

## Markers & Conditions

No special markers — all tests run against in-memory SQLite (no external deps).

## Fixtures

### Shared (from conftest)

| Fixture | Purpose |
|---|---|
| `client` | E2E conftest provides `unauthenticated_client` — real auth flow |
| `test_session` | Async SQLAlchemy session with auto-rollback |

### New (local to test file)

| Fixture | Purpose |
|---|---|
| `admin_user` | Superuser created in DB + logged in via `client` |
| `doctor_role` | `UserRole(name="doctor")` persisted in DB |
| `viewer_role` | `UserRole(name="viewer")` persisted in DB |
| `registered_user` | Regular user registered via API, returns `(user_data, password)` |
| `second_client` | Second `AsyncClient` for simultaneous user sessions |

### Client Strategy

- **`client`**: uses the e2e conftest override (unauthenticated) — real cookie-based auth.
- Admin actions: login as superuser first, then make admin API calls.
- Regular user actions: login as the registered user.
- Tests needing two simultaneous sessions use `second_client`.

## Mocking Strategy

- No external services mocked — purely DB + API.
- Settings patches: `patch.object(settings, "session_max_concurrent", ...)` for session limit tests.

## Test Classes & Scenarios

### `TestRoleManagement`

Precondition: logged in as admin (superuser).

1. **`test_create_role`**
   - Login as admin
   - `POST /api/user/roles` with `{"name": "doctor"}`
   - Assert: 201, response `{"name": "doctor"}`
   - DB check: `UserRole(name="doctor")` exists

2. **`test_create_duplicate_role_fails`**
   - Create "doctor" role twice
   - Assert: second call returns 409

3. **`test_get_role_details`**
   - `GET /api/user/roles/doctor`
   - Assert: 200, `{"name": "doctor"}`

4. **`test_get_nonexistent_role_returns_404`**
   - `GET /api/user/roles/nonexistent`
   - Assert: 404

### `TestUserLifecycle`

5. **`test_admin_creates_user`**
   - Login as admin
   - `POST /api/user` with user data
   - Assert: 201, user created with `is_active=True`
   - DB check: `User` row exists

6. **`test_admin_lists_users`**
   - `GET /api/user`
   - Assert: 200, list contains the created user and admin

7. **`test_admin_gets_user_by_id`**
   - `GET /api/user/{user_id}`
   - Assert: 200, correct user data

8. **`test_admin_updates_user`**
   - `PUT /api/user/{user_id}` — change email
   - Assert: 200, email updated
   - DB check: email changed

9. **`test_admin_deactivates_user`**
   - `POST /api/user/{user_id}/deactivate`
   - Assert: 200, `is_active=False`
   - Verify: deactivated user cannot login (POST `/api/auth/login` fails)

10. **`test_admin_reactivates_user`**
    - `POST /api/user/{user_id}/activate`
    - Assert: 200, `is_active=True`
    - Verify: reactivated user can login again

11. **`test_admin_deletes_user`**
    - `DELETE /api/user/{user_id}`
    - Assert: 204
    - DB check: user no longer exists
    - Verify: deleted user cannot login

### `TestRoleAssignment`

12. **`test_assign_role_to_user`**
    - `POST /api/user/{user_id}/roles/doctor`
    - Assert: 200, user response includes role info
    - DB check: `UserRolesLink` row exists

13. **`test_get_user_roles`**
    - `GET /api/user/{user_id}/roles`
    - Assert: 200, list contains "doctor"

14. **`test_assign_multiple_roles`**
    - Assign "doctor" and "viewer" to same user
    - `GET /api/user/{user_id}/roles`
    - Assert: both roles present

15. **`test_remove_role_from_user`**
    - `DELETE /api/user/{user_id}/roles/doctor`
    - Assert: 200
    - `GET /api/user/{user_id}/roles` — "doctor" no longer present

16. **`test_assign_nonexistent_role_fails`**
    - `POST /api/user/{user_id}/roles/nonexistent`
    - Assert: 404

### `TestCurrentUserEndpoints`

17. **`test_get_current_user_me`**
    - Login as regular user
    - `GET /api/user/me`
    - Assert: 200, returns the logged-in user's data

18. **`test_get_my_roles`**
    - Login as user with "doctor" role assigned
    - `GET /api/user/me/roles`
    - Assert: 200, list contains "doctor"

### `TestRoleBasedAccessControl`

19. **`test_regular_user_cannot_list_users`**
    - Login as regular (non-superuser) user
    - `GET /api/user`
    - Assert: 403

20. **`test_regular_user_cannot_create_role`**
    - `POST /api/user/roles` as regular user
    - Assert: 403

21. **`test_regular_user_cannot_delete_other_user`**
    - `DELETE /api/user/{admin_id}` as regular user
    - Assert: 403

22. **`test_regular_user_cannot_assign_roles`**
    - `POST /api/user/{other_id}/roles/doctor` as regular user
    - Assert: 403

23. **`test_regular_user_can_access_own_me_endpoint`**
    - Login as regular user
    - `GET /api/user/me` → 200
    - `GET /api/user/me/roles` → 200

24. **`test_unauthenticated_user_gets_401`**
    - No login
    - `GET /api/user/me` → 401

### `TestAdminDashboardAccess`

25. **`test_admin_can_access_stats`**
    - Login as admin
    - `GET /api/admin/stats`
    - Assert: 200, valid `AdminStats` shape

26. **`test_admin_can_access_record_type_stats`**
    - `GET /api/admin/record-types/stats`
    - Assert: 200, list of `RecordTypeStats`

27. **`test_admin_can_assign_record`**
    - Create a record (need patient, study, series, record_type in DB)
    - `PATCH /api/admin/records/{id}/assign?user_id={uuid}`
    - Assert: 200, record user_id updated

28. **`test_regular_user_cannot_access_admin_stats`**
    - Login as regular user
    - `GET /api/admin/stats` → 403

29. **`test_regular_user_cannot_assign_records`**
    - `PATCH /api/admin/records/{id}/assign` → 403

### `TestFullRegistrationToRestrictedAccessFlow`

30. **`test_complete_user_onboarding_flow`**
    - Combined scenario:
      1. Admin logs in → creates "doctor" role
      2. New user registers via `/api/auth/register`
      3. Admin assigns "doctor" role to new user
      4. New user logs in → `GET /me/roles` shows "doctor"
      5. New user accesses role-gated endpoints (e.g., record types with `role_name="doctor"`)
      6. New user cannot access admin endpoints
      7. Admin deactivates user → user's session becomes invalid

## Assertions Checklist

- [ ] HTTP status codes (200, 201, 204, 401, 403, 404, 409)
- [ ] User CRUD correctness (create, read, update, delete)
- [ ] Role CRUD correctness (create, read, assign, remove)
- [ ] Activation/deactivation affects login ability
- [ ] RBAC enforcement: superuser-only endpoints reject regular users
- [ ] Unauthenticated requests get 401
- [ ] DB state matches API responses after each operation
- [ ] `GET /me` and `GET /me/roles` reflect current user context

## Dependencies

None — pure API + DB tests.
