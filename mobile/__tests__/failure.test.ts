import { isInterruptedFailure, isRetryableFailure, isWaitingForAi } from '../src/lib/failure';

describe('failure helpers', () => {
  // The messages the backend stores on a failed document.
  const busy = 'The AI service is busy right now. Please try again in a few minutes.';
  const interrupted = 'Processing was interrupted. Please try again.';
  const failed = "We couldn't read this document. Please try again.";

  test('a busy failure is not retried - the server already waited', () => {
    expect(isRetryableFailure(busy)).toBe(false);
  });

  test('a scan back in the queue is waiting for the AI', () => {
    expect(isWaitingForAi('waiting')).toBe(true);
    expect(isWaitingForAi('3/12')).toBe(false);
    expect(isWaitingForAi(null)).toBe(false);
    expect(isWaitingForAi(undefined)).toBe(false);
  });

  test('a server restart is worth retrying', () => {
    expect(isInterruptedFailure(interrupted)).toBe(true);
    expect(isRetryableFailure(interrupted)).toBe(true);
  });

  test('any other failure is not retried', () => {
    expect(isRetryableFailure(failed)).toBe(false);
  });

  test('no error is not retryable', () => {
    expect(isRetryableFailure(null)).toBe(false);
    expect(isRetryableFailure(undefined)).toBe(false);
    expect(isRetryableFailure('')).toBe(false);
  });
});
