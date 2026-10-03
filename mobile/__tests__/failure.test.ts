import { isBusyFailure, isInterruptedFailure, isRetryableFailure } from '../src/lib/failure';

describe('failure helpers', () => {
  const busy = 'AI service is busy (both models overloaded). Please retry. [503 UNAVAILABLE]';
  const interrupted = 'Processing was interrupted (server restart). Please retry.';
  const rejected =
    'Could not read the document: AI request was rejected: 400 INVALID_ARGUMENT. The specified schema produces a constraint that has too many states for serving.';

  test('an overloaded AI is busy and worth retrying', () => {
    expect(isBusyFailure(busy)).toBe(true);
    expect(isRetryableFailure(busy)).toBe(true);
  });

  test('a server restart is worth retrying but is not "AI busy"', () => {
    expect(isBusyFailure(interrupted)).toBe(false);
    expect(isInterruptedFailure(interrupted)).toBe(true);
    expect(isRetryableFailure(interrupted)).toBe(true);
  });

  test('a refused request is neither busy nor retried', () => {
    expect(isBusyFailure(rejected)).toBe(false);
    expect(isRetryableFailure(rejected)).toBe(false);
  });

  test('no error is not retryable', () => {
    expect(isRetryableFailure(null)).toBe(false);
    expect(isRetryableFailure(undefined)).toBe(false);
    expect(isRetryableFailure('')).toBe(false);
  });
});
