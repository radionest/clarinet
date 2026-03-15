import plinth/javascript/console

/// Debug-level log (only visible when DevTools filter includes "Verbose")
pub fn debug(tag: String, msg: String) -> Nil {
  console.debug("[" <> tag <> "] " <> msg)
}

/// Info-level log
pub fn info(tag: String, msg: String) -> Nil {
  console.info("[" <> tag <> "] " <> msg)
}

/// Warning-level log
pub fn warn(tag: String, msg: String) -> Nil {
  console.warn("[" <> tag <> "] " <> msg)
}

/// Error-level log
pub fn error(tag: String, msg: String) -> Nil {
  console.error("[" <> tag <> "] " <> msg)
}
