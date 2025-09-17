// Static typed form for User model
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import gleam/option.{type Option, None, Some}
import gleam/dict.{type Dict}
import gleam/list
import api/models.{type UserCreate}
import components/forms/base as form
import store.{type Msg}

// User form data type for managing form state
pub type UserFormData {
  UserFormData(
    username: String,
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
  UpdateUsername(String)
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
    username: data.username,
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
    username: "",
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
    username: user.username,
    email: user.email,
    password: "",  // Don't populate password for editing
    password_confirm: "",
    is_active: user.is_active,
    is_superuser: user.is_superuser,
    is_verified: user.is_verified,
  )
}

// Main form view
pub fn view(
  data: UserFormData,
  errors: Dict(String, String),
  loading: Bool,
  is_edit: Bool,
  on_update: fn(UserFormMsg) -> Msg,
  on_submit: fn() -> Msg,
) -> Element(Msg) {
  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [
      html.text(case is_edit {
        True -> "Edit User"
        False -> "Create User"
      })
    ]),

    // Username field (required)
    form.required_field(
      "Username",
      "username",
      form.text_input(
        "username",
        data.username,
        Some("Enter username"),
        fn(value) { on_update(UpdateUsername(value)) },
      ),
      errors,
    ),

    // Email field (required)
    form.required_field(
      "Email",
      "email",
      form.email_input(
        "email",
        data.email,
        Some("Enter email address"),
        fn(value) { on_update(UpdateEmail(value)) },
      ),
      errors,
    ),

    // Password fields (required for new users, optional for edit)
    case is_edit {
      False -> {
        html.div([], [
          form.required_field(
            "Password",
            "password",
            form.password_input(
              "password",
              data.password,
              Some("Enter password"),
              fn(value) { on_update(UpdatePassword(value)) },
            ),
            errors,
          ),
          form.required_field(
            "Confirm Password",
            "password_confirm",
            form.password_input(
              "password_confirm",
              data.password_confirm,
              Some("Confirm password"),
              fn(value) { on_update(UpdatePasswordConfirm(value)) },
            ),
            errors,
          ),
        ])
      }
      True -> {
        html.div([attribute.class("form-group")], [
          html.p([attribute.class("form-help-text")], [
            html.text("Leave password fields empty to keep existing password"),
          ]),
          form.field(
            "New Password",
            "password",
            form.password_input(
              "password",
              data.password,
              Some("Enter new password (optional)"),
              fn(value) { on_update(UpdatePassword(value)) },
            ),
            errors,
          ),
          case data.password {
            "" -> html.text("")
            _ -> form.field(
              "Confirm New Password",
              "password_confirm",
              form.password_input(
                "password_confirm",
                data.password_confirm,
                Some("Confirm new password"),
                fn(value) { on_update(UpdatePasswordConfirm(value)) },
              ),
              errors,
            )
          },
        ])
      }
    },

    // User status checkboxes
    html.fieldset([attribute.class("form-group")], [
      html.legend([], [html.text("User Status")]),

      form.checkbox(
        "is_active",
        data.is_active,
        "Active (can login)",
        fn(checked) { on_update(UpdateIsActive(checked)) },
      ),

      form.checkbox(
        "is_superuser",
        data.is_superuser,
        "Superuser (admin privileges)",
        fn(checked) { on_update(UpdateIsSuperuser(checked)) },
      ),

      form.checkbox(
        "is_verified",
        data.is_verified,
        "Email Verified",
        fn(checked) { on_update(UpdateIsVerified(checked)) },
      ),
    ]),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(
        case is_edit {
          True -> "Update User"
          False -> "Create User"
        },
        loading,
        Some(on_submit()),
      ),
      form.cancel_button("Cancel", store.Navigate(router.Users)),
    ]),

    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data
pub fn validate(data: UserFormData, is_edit: Bool) -> Result(UserFormData, Dict(String, String)) {
  let errors = dict.new()

  // Validate Username (required)
  let errors = case form.validate_required(data.username, "Username") {
    Error(msg) -> dict.insert(errors, "username", msg)
    Ok(_) -> errors
  }

  // Validate Email (required and format)
  let errors = case form.validate_required(data.email, "Email") {
    Error(msg) -> dict.insert(errors, "email", msg)
    Ok(email) -> case form.validate_email(email) {
      Error(msg) -> dict.insert(errors, "email", msg)
      Ok(_) -> errors
    }
  }

  // Validate Password (required for new users)
  let errors = case is_edit {
    False -> {
      case form.validate_required(data.password, "Password") {
        Error(msg) -> dict.insert(errors, "password", msg)
        Ok(password) -> {
          // Check minimum length
          case string.length(password) < 8 {
            True -> dict.insert(errors, "password", "Password must be at least 8 characters")
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
            True -> dict.insert(errors, "password", "Password must be at least 8 characters")
            False -> errors
          }
        }
      }
    }
  }

  // Validate Password Confirmation
  let errors = case data.password {
    "" if is_edit -> errors  // Skip if no password change
    _ -> {
      case data.password == data.password_confirm {
        False -> dict.insert(errors, "password_confirm", "Passwords do not match")
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
    UpdateUsername(value) -> UserFormData(..data, username: value)
    UpdateEmail(value) -> UserFormData(..data, email: value)
    UpdatePassword(value) -> UserFormData(..data, password: value)
    UpdatePasswordConfirm(value) -> UserFormData(..data, password_confirm: value)
    UpdateIsActive(value) -> UserFormData(..data, is_active: value)
    UpdateIsSuperuser(value) -> UserFormData(..data, is_superuser: value)
    UpdateIsVerified(value) -> UserFormData(..data, is_verified: value)
    SubmitUser -> data  // Submit is handled by parent component
  }
}

// Import router for navigation
import router
import gleam/string