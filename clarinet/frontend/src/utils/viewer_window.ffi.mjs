// Viewer window handle FFI — window.open without plinth, because plinth's
// open() wraps the popup-blocked null in Ok and the blocked case becomes
// indistinguishable from success.
import { Ok, Error } from "../gleam.mjs";

export function openWindow(url) {
  const w = window.open(url, "_blank");
  return w ? new Ok(w) : new Error(undefined); // null = blocked by the browser
}

// replace (not assign): the loading stub must not stay in the popup's history
export function navigate(w, url) {
  w.location.replace(url);
}

export function closeWindow(w) {
  try {
    w.close();
  } catch (_) {}
}

export function isClosed(w) {
  return w.closed;
}
