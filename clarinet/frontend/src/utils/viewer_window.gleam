// Handle to a pre-opened viewer window (popup). Opened synchronously inside
// the click handler so the transient user activation is still valid — opening
// later (e.g. when preload finishes) gets silently blocked by the browser.

pub type ViewerWindow

@external(javascript, "./viewer_window.ffi.mjs", "openWindow")
pub fn open(url: String) -> Result(ViewerWindow, Nil)

@external(javascript, "./viewer_window.ffi.mjs", "navigate")
pub fn navigate(win: ViewerWindow, url: String) -> Nil

@external(javascript, "./viewer_window.ffi.mjs", "closeWindow")
pub fn close(win: ViewerWindow) -> Nil

@external(javascript, "./viewer_window.ffi.mjs", "isClosed")
pub fn is_closed(win: ViewerWindow) -> Bool
