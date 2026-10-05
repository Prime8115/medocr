/**
 * What the server said about an upload it would not take.
 *
 * The server now checks a file before queueing it, and refuses one it cannot
 * read - password-protected or damaged PDF, blank or unreadable photo - with a
 * plain reason. Only a failure with NO reply (the network dropped) belongs in
 * the offline queue: queueing a refused file would just be refused again.
 */
export function refusalMessage(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  if (!response || !response.status || response.status < 400 || response.status >= 500) return null;
  const detail = response.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : 'This file could not be uploaded.';
}
