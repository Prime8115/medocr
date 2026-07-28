import { confidenceColor, confidencePercent, isLowConfidence } from '../src/lib/confidence';
import { colors } from '../src/theme/tokens';

describe('confidence helpers', () => {
  test('isLowConfidence', () => {
    expect(isLowConfidence(0.4)).toBe(true);
    expect(isLowConfidence(0.6)).toBe(false);
    expect(isLowConfidence(0.9)).toBe(false);
    expect(isLowConfidence(null)).toBe(false);
    expect(isLowConfidence(undefined)).toBe(false);
  });

  test('confidenceColor thresholds', () => {
    expect(confidenceColor(0.3)).toBe(colors.danger);
    expect(confidenceColor(0.7)).toBe(colors.warning);
    expect(confidenceColor(0.95)).toBe(colors.success);
    expect(confidenceColor(null)).toBe(colors.border);
  });

  test('confidencePercent', () => {
    expect(confidencePercent(0.912)).toBe('91%');
    expect(confidencePercent(null)).toBe('');
  });
});
