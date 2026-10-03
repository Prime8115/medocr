/**
 * What a failed document's error means for the pharmacist.
 *
 * The app used to say "AI busy" after ANY first failure and retry it, so a
 * request the AI refused outright looked like an overload and was retried for
 * nothing. The backend now keeps the real cause in `doc.error`; these read it.
 */

/** The AI was overloaded or rate-limited ("AI service is busy ..."). */
export function isBusyFailure(error: string | null | undefined): boolean {
  return !!error && /\bbusy\b/i.test(error);
}

/** The server restarted mid-scan ("Processing was interrupted ..."). */
export function isInterruptedFailure(error: string | null | undefined): boolean {
  return !!error && /\binterrupted\b/i.test(error);
}

/** Worth retrying automatically: the same request may well succeed later. */
export function isRetryableFailure(error: string | null | undefined): boolean {
  return isBusyFailure(error) || isInterruptedFailure(error);
}
