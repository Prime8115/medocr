/**
 * What a failed document's error means for the pharmacist.
 *
 * The app used to say "AI busy" after ANY first failure and retry it, so a
 * request the AI refused outright looked like an overload and was retried for
 * nothing. The backend's message in `doc.error` says which kind of failure it
 * was - in plain words; the technical cause stays on the server.
 *
 * An overloaded AI no longer fails a scan straight away: the server puts it
 * back in its queue (`progress === 'waiting'`) and reads it again later.
 */

/**
 * The scan is back in the server's queue because the AI was busy; the server
 * reads it again by itself. Marked by the document's progress, not its error.
 */
export function isWaitingForAi(progress: string | null | undefined): boolean {
  return progress === 'waiting';
}

/** The server restarted mid-scan ("Processing was interrupted ..."). */
export function isInterruptedFailure(error: string | null | undefined): boolean {
  return !!error && /\binterrupted\b/i.test(error);
}

/**
 * Worth retrying automatically from the app. A busy AI is not: the server has
 * already waited it out for about ten minutes before failing the scan.
 */
export function isRetryableFailure(error: string | null | undefined): boolean {
  return isInterruptedFailure(error);
}
