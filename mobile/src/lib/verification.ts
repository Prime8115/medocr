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

/** "invoice.lr_no" -> "LR no.", for people. */
export function fieldLabel(path: string): string {
  const names: Record<string, string> = {
    line_items: 'line items',
    'invoice.lr_no': 'LR no.', 'invoice.lr_date': 'LR date', 'invoice.po_no': 'PO no.',
    'invoice.po_date': 'PO date', 'invoice.irn': 'IRN', 'invoice.eway_bill_no': 'e-way bill',
    'supplier.dl_no_1': 'DL 1', 'supplier.dl_no_2': 'DL 2', 'supplier.gstin': 'supplier GSTIN',
    'bill_to.gstin': 'bill-to GSTIN', 'ship_to.gstin': 'ship-to GSTIN',
  };
  if (names[path]) return names[path];
  const [section, key = ''] = path.split('.');
  const words = key.replace(/_/g, ' ');
  return section === 'invoice' ? words : `${section.replace('_', '-')} ${words}`;
}

export interface FilledField {
  path: string;
  value: string;
  label?: string;
  replaced?: string | null;
}

/**
 * Fields not read directly: filled by the AI from the page text (each one
 * printed there word for word), or read at a label a reviewer taught us for
 * this supplier. Both are held at lower confidence; this says which and why.
 */
export function filledNotes(payload: unknown): string[] {
  const meta = (payload as { meta?: { gap_filled?: FilledField[]; learned_filled?: FilledField[] } } | null)?.meta;
  const notes: string[] = [];
  const ai = Array.isArray(meta?.gap_filled) ? meta!.gap_filled! : [];
  const learned = Array.isArray(meta?.learned_filled) ? meta!.learned_filled! : [];
  if (ai.length) {
    notes.push(`Filled by AI from the bill's text — please check: ${ai.map((f) => fieldLabel(f.path)).join(', ')}.`);
  }
  if (learned.length) {
    notes.push(
      `Read where a reviewer showed us for this supplier: ${learned.map((f) => fieldLabel(f.path)).join(', ')}.`,
    );
  }
  return notes;
}
