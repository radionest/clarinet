// Silent replaceState — does NOT dispatch modem-replace, so the application
// receives no on_url_change. Used by sort-toggle handlers that already
// updated the page Model locally and only need the URL to reflect state.
export function replace_state(path) {
  if (typeof window !== "undefined" && window.history) {
    window.history.replaceState({}, "", path);
  }
}
