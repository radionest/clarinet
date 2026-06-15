// Same-origin cookie writer. Mirrors a per-device setting (the Slicer storage
// path) into a cookie alongside localStorage so it rides on requests the
// frontend's own HTTP client does not build — notably formosh's `rsvp.post`,
// which drops custom headers. The browser auto-attaches same-origin cookies to
// every request, including those, so the value survives form-submit with no
// formosh change. Values are sensitive only at the network-topology level
// (internal network, low sensitivity); the cookie is JS-set (not HttpOnly).
import lustre/effect.{type Effect}

@external(javascript, "./cookie.ffi.mjs", "set_cookie")
fn do_set_cookie(
  _name: String,
  _value: String,
  _path: String,
  _max_age: Int,
) -> Nil {
  Nil
}

@external(javascript, "./cookie.ffi.mjs", "delete_cookie")
fn do_delete_cookie(_name: String, _path: String) -> Nil {
  Nil
}

/// Write a cookie scoped to `path`. The value is `encodeURIComponent`-escaped
/// in the FFI (the backend URL-decodes it). `SameSite=Lax`; `Secure` is added
/// only when the page is served over HTTPS (`location.protocol`), so the cookie
/// works whether TLS is terminated upstream (nginx) or it is served plain HTTP —
/// an unconditional `Secure` cookie would be silently dropped on plain HTTP.
/// Fire-and-forget effect.
pub fn set_cookie(
  name: String,
  value: String,
  path: String,
  max_age_seconds: Int,
) -> Effect(msg) {
  use _ <- effect.from
  do_set_cookie(name, value, path, max_age_seconds)
  Nil
}

/// Expire a cookie (max-age=0). `path` must match the one used to set it,
/// otherwise the browser keeps the original cookie. Fire-and-forget effect.
pub fn delete_cookie(name: String, path: String) -> Effect(msg) {
  use _ <- effect.from
  do_delete_cookie(name, path)
  Nil
}
