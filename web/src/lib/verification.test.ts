import { describe, expect, test } from 'vitest';
import { choicesOf, openChecks, optionText, pendingChoices, verdictOf, verificationOf, type Verification } from './verification';

const v = (over: Partial<Verification> = {}): Verification => ({
  checks: [
    { id: 'total_reconciles', label: 'Lines add up to the bill total', status: 'pass' },
    { id: 'line_tax', label: "Each line's tax is its taxable value at the rate", status: 'fail', message: 'line 2 CGST' },
    { id: 'dates', label: 'Dates are possible', status: 'fail' },
    { id: 'line_net', label: 'Net', status: 'skipped' },
  ],
  passed: 1,
  failed: 2,
  verdict: 'needs_check',
  ...over,
});

describe('verification', () => {
  test('reads the verification off a payload, or nothing for an older one', () => {
    expect(verificationOf({ meta: { verification: v() } })?.failed).toBe(2);
    expect(verificationOf({ meta: {} })).toBeNull();
    expect(verificationOf(null)).toBeNull();
  });

  test('open checks are the failures not yet confirmed', () => {
    expect(openChecks(v()).map((c) => c.id)).toEqual(['line_tax', 'dates']);
    expect(openChecks(v({ acknowledged: [{ id: 'dates' }] })).map((c) => c.id)).toEqual(['line_tax']);
  });

  test('the verdict names what failed', () => {
    const verdict = verdictOf(v())!;
    expect(verdict.ok).toBe(false);
    expect(verdict.title).toBe('2 checks need a look');
    expect(verdict.detail).toContain('Dates are possible');
  });

  test('passing checks do not hide skipped checks', () => {
    const clean = v({
      checks: [
        { id: 'a', label: 'A', status: 'pass' },
        { id: 'b', label: 'B', status: 'pass' },
        { id: 'c', label: 'C', status: 'skipped' },
      ],
    });
    expect(verdictOf(clean)).toMatchObject({ ok: false, title: '2 checks passed · 1 not checked' });
  });

  test('required fields remain approval checks even if arithmetic passed', () => {
    const result = verificationOf({ meta: { verification: v({
      checks: [{ id: 'total', label: 'Total', status: 'pass' }],
      review_requirements: [{ id: 'extraction_coverage', label: 'Missing batch', status: 'fail' }],
    }) } });
    expect(openChecks(result).map((c) => c.id)).toEqual(['extraction_coverage']);
    expect(verdictOf(result)?.ok).toBe(false);
  });

  test('zero checks cannot show success', () => {
    expect(verdictOf(v({ checks: [] }))).toMatchObject({ ok: false, title: 'Not automatically checked' });
  });

  test('a scan confirmed by its second reading says so', () => {
    const scan = v({ checks: [{ id: 'cross_read', label: 'Second reading', status: 'pass' }] });
    expect(verdictOf(scan)!.detail).toContain('second reading');
  });
});

describe('choices', () => {
  const payload = {
    meta: {
      choices: [
        {
          id: 'po', label: 'Purchase order', question: 'Which one?', fields: ['invoice.po_no'],
          options: [
            { label: 'PO Number', values: { 'invoice.po_no': 'GN-15912', 'invoice.po_date': null } },
            { label: 'Order No', values: { 'invoice.po_no': '100178296', 'invoice.po_date': '30.06.2025' } },
          ],
          default: 1, chosen: null, remember: true,
        },
      ],
      verification: {
        checks: [{ id: 'choice_po', label: 'Purchase order: choose one', status: 'fail' }],
        passed: 0, failed: 1, verdict: 'needs_check',
      },
    },
  };

  test('a pending choice is listed, and never offered as a box to tick', () => {
    expect(pendingChoices(payload).map((c) => c.id)).toEqual(['po']);
    expect(openChecks(verificationOf(payload))).toEqual([]);
  });

  test("an option reads as its values, leaving out what the bill didn't print", () => {
    const [choice] = choicesOf(payload);
    expect(optionText(choice.options[0])).toBe('GN-15912');
    expect(optionText(choice.options[1])).toBe('100178296 · 30.06.2025');
  });

  test('a decided choice is no longer pending', () => {
    const decided = { meta: { choices: [{ ...payload.meta.choices[0], chosen: 0 }] } };
    expect(pendingChoices(decided)).toEqual([]);
  });
});

describe('filledNotes - learned columns', () => {
  test('names the columns read as the shop taught for this supplier', async () => {
    const { filledNotes } = await import('./verification');
    const notes = filledNotes({
      meta: { lessons_applied: [{ field: 'ptr', heading: 'RETAILER RATE', kind: 'is' }, { field: 'pts', heading: 'X', kind: 'not' }] },
    });
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain('Learned from your earlier corrections');
    expect(notes[0]).toContain('RETAILER RATE');
  });
});
