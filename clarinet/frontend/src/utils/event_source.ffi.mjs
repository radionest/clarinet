export function connect(path, onOpen, onMessage, onError) {
  const url = new URL(path, window.location.href);
  const es = new EventSource(url, { withCredentials: true });
  es.onopen = () => onOpen(es);
  es.onmessage = (e) => onMessage(e.data); // only unnamed data: frames; retry/comments handled internally
  es.onerror = () => onError(es.readyState); // 0 CONNECTING, 2 CLOSED
}

export function closeSource(es) {
  es.onerror = null; // deliberate close: don't surface Errored (would trigger reconnect logic)
  try {
    es.close();
  } catch (_) {}
}
