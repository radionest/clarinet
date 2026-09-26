// Cookie FFI for utils/cookie.gleam. The value is encodeURIComponent-escaped
// so paths containing "/", ":", spaces, etc. stay cookie-safe; the backend
// (get_client_storage_path) urllib.parse.unquote()s it back. SameSite=Lax
// matches the session cookie. Secure is appended only when the page is served
// over HTTPS (location.protocol), so the cookie works whether TLS is terminated
// upstream (nginx) or it is served plain HTTP — an unconditional Secure cookie
// would be silently dropped on plain HTTP.
export function set_cookie(name, value, path, max_age) {
  if (typeof document === "undefined") return undefined;
  let cookie =
    name +
    "=" +
    encodeURIComponent(value) +
    "; path=" +
    path +
    "; max-age=" +
    max_age +
    "; SameSite=Lax";
  if (typeof location !== "undefined" && location.protocol === "https:") {
    cookie += "; Secure";
  }
  document.cookie = cookie;
  return undefined;
}

// URL-decoded value of a cookie visible to this page, "" when absent. First
// match wins: browsers list the most specific path first, so a sub-path cookie
// shadows a same-named one set by a root deploy on the same host.
export function get_cookie(name) {
  if (typeof document === "undefined") return "";
  const hit = document.cookie
    .split("; ")
    .find((c) => c.startsWith(name + "="));
  if (!hit) return "";
  try {
    return decodeURIComponent(hit.slice(name.length + 1));
  } catch {
    return "";
  }
}

export function delete_cookie(name, path) {
  if (typeof document === "undefined") return undefined;
  document.cookie = name + "=; path=" + path + "; max-age=0; SameSite=Lax";
  return undefined;
}
