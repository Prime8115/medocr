/**
 * The verification layer's result, as the server stores it in
 * `payload.meta.verification` (backend/app/services/ocr/verify.py).
 *
 * Every check is pass, fail or skipped. A document is "verified" only with no
 * failures; otherwise each failed check must be confirmed against the paper
 * before it can be approved - the server refuses approval until it is.
 */
export type CheckStatus = 'pass' | 'fail' | 'skipped';

export interface Check {
  id: string;
  label: string;
  status: CheckStatus;
  message?: string;
  fields?: string[];
}

export interface Verification {
  checks: Check[];
  passed: number;
  failed: number;
  skipped?: number;
  verdict: 'verified' | 'needs_check';
  acknowledged?: { id: string; by?: string; at?: string }[];
}

export function verificationOf(payload: unknown): Verification | null {
  const meta = (payload as { meta?: { verification?: Verification } } | null)?.meta;
  const v = meta?.verification;
  return v && Array.isArray(v.checks) ? v : null;
}

/** Failed checks not yet confirmed - what stands between review and approval. */
export function openChecks(v: Verification | null): Check[] {
  if (!v) return [];
  const done = new Set((v.acknowledged ?? []).map((a) => a.id));
  // A pending choice is answered by choosing, never by ticking it off.
  return v.checks.filter((c) => c.status === 'fail' && !done.has(c.id) && !c.id.startsWith('choice_'));
}

/** Whether the scan was confirmed by a second, independent reading. */
export function confirmedBySecondReading(v: Verification | null): boolean {
  return !!v?.checks.some((c) => c.id === 'cross_read' && c.status === 'pass');
}

export interface Verdict {
  ok: boolean;
  title: string;
  detail: string;
}

/** The one-line verdict shown at the top of review. */
export function verdictOf(v: Verification | null): Verdict | null {
  if (!v) return null;
  const tested = v.checks.filter((c) => c.status !== 'skipped').length;
  const failed = v.checks.filter((c) => c.status === 'fail');
  if (failed.length === 0) {
    return {
      ok: true,
      title: `Verified - ${tested} of ${tested} checks passed`,
      detail: confirmedBySecondReading(v)
        ? 'Every figure agrees with the rest of the bill, and a second reading of the scan confirms it.'
        : 'Every figure agrees with the rest of the bill.',
    };
  }
  return {
    ok: false,
    title: `${failed.length} ${failed.length === 1 ? 'check needs' : 'checks need'} a look`,
    detail: failed.map((c) => c.label).join(' · '),
  };
}

/**
 * A field the bill gives two answers to, for the reviewer to decide
 * (backend/app/services/ocr/choices.py). Approval waits until each is chosen;
 * a choice marked `remember` is kept for the supplier, so its next bill
 * arrives decided - and shows here as remembered, still changeable.
 */
export interface ChoiceOption {
  label: string;
  values: Record<string, string | null>;
}

export interface Choice {
  id: string;
  label: string;
  question: string;
  fields: string[];
  options: ChoiceOption[];
  default: number;
  chosen: number | null;
  remember: boolean;
  remembered?: boolean;
}

export function choicesOf(payload: unknown): Choice[] {
  const meta = (payload as { meta?: { choices?: Choice[] } } | null)?.meta;
  return Array.isArray(meta?.choices) ? meta!.choices! : [];
}

export function pendingChoices(payload: unknown): Choice[] {
  return choicesOf(payload).filter((c) => c.chosen === null || c.chosen === undefined);
}

/** "GN-15912-1068-SHREE" / "100178296 · 30.06.2025" - an option's values, plainly. */
export function optionText(option: ChoiceOption): string {
  return Object.values(option.values)
    .filter((v) => v !== null && v !== undefined && v !== '')
    .join(' · ');
}
