import { colors } from '../theme/tokens';
import { LOW_CONFIDENCE_THRESHOLD } from '../config';

export function isLowConfidence(
  confidence: number | null | undefined,
  threshold = LOW_CONFIDENCE_THRESHOLD,
): boolean {
  return confidence != null && confidence < threshold;
}

/** Border/accent color for a field based on its OCR confidence. */
export function confidenceColor(confidence: number | null | undefined): string {
  if (confidence == null) return colors.border;
  if (confidence < LOW_CONFIDENCE_THRESHOLD) return colors.danger;
  if (confidence < 0.85) return colors.warning;
  return colors.success;
}

export function confidencePercent(confidence: number | null | undefined): string {
  if (confidence == null) return '';
  return `${Math.round(confidence * 100)}%`;
}
