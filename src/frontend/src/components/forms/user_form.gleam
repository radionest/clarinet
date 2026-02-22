// Static typed form for User model
import api/models.{type UserCreate}
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Msg}

// User form data type for managing form state
pub type UserFormData {
  UserFormData(
    email: String,
    password: String,
    password_confirm: String,
    is_active: Bool,
    is_superuser: Bool,
    is_verified: Bool,
  )
}

// Message types for form updates
pub type UserFormMsg {
  UpdateEmail(String)
  UpdatePassword(String)
  UpdatePasswordConfirm(String)
  UpdateIsActive(Bool)
  UpdateIsSuperuser(Bool)
  UpdateIsVerified(Bool)
  SubmitUser
}

// Convert form data to UserCreate model
pub fn to_user_create(data: UserFormData) -> UserCreate {
  models.UserCreate(
    email: data.email,
    password: data.password,
    is_active: Some(data.is_active),
    is_superuser: Some(data.is_superuser),
    is_verified: Some(data.is_verified),
  )
}

// Initialize empty form data
pub fn init() -> UserFormData {
  UserFormData(
    email: "",
    password: "",
    password_confirm: "",
    is_active: True,
    is_superuser: False,
    is_verified: False,
  )
}

// Initialize form data for editing (without password)
pub fn from_user(user: models.User) -> UserFormData {
  UserFormData(
    email: user.email,
    password: "",
    // Don't populate password for editing
    password_confirm: "",
    is_active: user.is_active,
    is_superuser: user.is_superuser,
    is_verified: user.is_verified,
  )
}

// Main form view
pub fn view(
  data data: UserFormData,
  errors errors: Dict(String, String),
  loading loading: Bool,
  is_edit is_edit: Bool,
  on_update on_update: fn(UserFormMsg) -> Msg,
  on_submit on_submit: fn() -> Msg,
) -> Element(Msg) {
  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [
      html.text(case is_edit {
        True -> "Edit User"
        False -> "Create User"
      }),
    ]),

    // Email field (required)
    form.field(
      label: "Email",
      name: "email",
      input: form.email_input(
        name: "email",
        value: data.email,
        placeholder: Some("Enter email address"),
        on_input: fn(value) { on_update(UpdateEmail(value)) },
      ),
      errors: errors,
      required: True,
    ),

    // Password fields (required for new users, optional for edit)
    case is_edit {
      False -> {
        html.div([], [
          form.field(
            label: "Password",
            name: "password",
            input: form.password_input(
              name: "password",
              value: data.password,
              placeholder: Some("Enter password"),
              on_input: fn(value) { on_update(UpdatePassword(value)) },
            ),
            errors: errors,
            required: True,
          ),
          form.field(
            label: "Confirm Password",
            name: "password_confirm",
            input: form.password_input(
              name: "password_confirm",
              value: data.password_confirm,
              placeholder: Some("Confirm password"),
              on_input: fn(value) { on_update(UpdatePasswordConfirm(value)) },
            ),
            errors: errors,
            required: True,
          ),
        ])
      }
      True -> {
        html.div([attribute.class("form-group")], [
          html.p([attribute.class("form-help-text")], [
            html.text("Leave password fields empty to keep existing password"),
          ]),
          form.field(
            label: "New Password",
            name: "password",
            input: form.password_input(
              name: "password",
              value: data.password,
              placeholder: Some("Enter new password (optional)"),
              on_input: fn(value) { on_update(UpdatePassword(value)) },
            ),
            errors: errors,
            required: False,
          ),
          case data.password {
            "" -> html.text("")
            _ ->
              form.field(
                label: "Confirm New Password",
                name: "password_confirm",
                input: form.password_input(
                  name: "password_confirm",
                  value: data.password_confirm,
                  placeholder: Some("Confirm new password"),
                  on_input: fn(value) { on_update(UpdatePasswordConfirm(value)) },
                ),
                errors: errors,
                required: False,
              )
          },
        ])
      }
    },

    // User status checkboxes
    html.fieldset([attribute.class("form-group")], [
      html.legend([], [html.text("User Status")]),

      form.checkbox(
        name: "is_active",
        checked: data.is_active,
        label: "Active (can login)",
        on_change: fn(checked) { on_update(UpdateIsActive(checked)) },
      ),

      form.checkbox(
        name: "is_superuser",
        checked: data.is_superuser,
        label: "Superuser (admin privileges)",
        on_change: fn(checked) { on_update(UpdateIsSuperuser(checked)) },
      ),

      form.checkbox(
        name: "is_verified",
        checked: data.is_verified,
        label: "Email Verified",
        on_change: fn(checked) { on_update(UpdateIsVerified(checked)) },
      ),
    ]),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(
        text: case is_edit {
          True -> "Update User"
          False -> "Create User"
        },
        disabled: loading,
        on_click: Some(on_submit()),
      ),
      form.cancel_button(text: "Cancel", on_click: store.Navigate(router.Users)),
    ]),

    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data
pub fn validate(
  data: UserFormData,
  is_edit: Bool,
) -> Result(UserFormData, Dict(String, String)) {
  let errors = dict.new()

  // Validate Email (required and format)
  let errors = case form.validate_required(value: data.email, field_name: "Email") {
    Error(msg) -> dict.insert(errors, "email", msg)
    Ok(email) ->
      case form.validate_email(email) {
        Error(msg) -> dict.insert(errors, "email", msg)
        Ok(_) -> errors
      }
  }

  // Validate Password (required for new users)
  let errors = case is_edit {
    False -> {
      case form.validate_required(value: data.password, field_name: "Password") {
        Error(msg) -> dict.insert(errors, "password", msg)
        Ok(password) -> {
          // Check minimum length
          case string.length(password) < 8 {
            True ->
              dict.insert(
                errors,
                "password",
                "Password must be at least 8 characters",
              )
            False -> errors
          }
        }
      }
    }
    True -> {
      // For edit, only validate if password is provided
      case data.password {
        "" -> errors
        password -> {
          case string.length(password) < 8 {
            True ->
              dict.insert(
                errors,
                "password",
                "Password must be at least 8 characters",
              )
            False -> errors
          }
        }
      }
    }
  }

  // Validate Password Confirmation
  let errors = case data.password {
    "" if is_edit -> errors
    // Skip if no password change
    _ -> {
      case data.password == data.password_confirm {
        False ->
          dict.insert(errors, "password_confirm", "Passwords do not match")
        True -> errors
      }
    }
  }

  case dict.size(errors) {
    0 -> Ok(data)
    _ -> Error(errors)
  }
}

// Update form data based on message
pub fn update(data: UserFormData, msg: UserFormMsg) -> UserFormData {
  case msg {
    UpdateEmail(value) -> UserFormData(..data, email: value)
    UpdatePassword(value) -> UserFormData(..data, password: value)
    UpdatePasswordConfirm(value) ->
      UserFormData(..data, password_confirm: value)
    UpdateIsActive(value) -> UserFormData(..data, is_active: value)
    UpdateIsSuperuser(value) -> UserFormData(..data, is_superuser: value)
    UpdateIsVerified(value) -> UserFormData(..data, is_verified: value)
    SubmitUser -> data
    // Submit is handled by parent component
  }
}

// Import router for navigation
import gleam/string
import router
