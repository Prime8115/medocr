import { isBusyFailure, isInterruptedFailure, isRetryableFailure } from '../src/lib/failure';

describe('failure helpers', () => {
  // The messages the backend stores on a failed document.
  const busy = 'The AI service is busy right now. Please try again in a moment.';
  const interrupted = 'Processing was interrupted. Please try again.';
  const failed = "We couldn't read this document. Please try again.";

  test('an overloaded AI is busy and worth retrying', () => {
    expect(isBusyFailure(busy)).toBe(true);
    expect(isRetryableFailure(busy)).toBe(true);
  });

  test('a server restart is worth retrying but is not "AI busy"', () => {
    expect(isBusyFailure(interrupted)).toBe(false);
    expect(isInterruptedFailure(interrupted)).toBe(true);
    expect(isRetryableFailure(interrupted)).toBe(true);
  });

  test('any other failure is neither busy nor retried', () => {
    expect(isBusyFailure(failed)).toBe(false);
    expect(isRetryableFailure(failed)).toBe(false);
  });

  test('no error is not retryable', () => {
    expect(isRetryableFailure(null)).toBe(false);
    expect(isRetryableFailure(undefined)).toBe(false);
    expect(isRetryableFailure('')).toBe(false);
  });
});
