import { openChecks, verdictOf, verificationOf, Verification } from '../src/lib/verification';

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

  test('a clean bill is verified, counting only checks that ran', () => {
    const clean = v({
      checks: [
        { id: 'a', label: 'A', status: 'pass' },
        { id: 'b', label: 'B', status: 'pass' },
        { id: 'c', label: 'C', status: 'skipped' },
      ],
    });
    expect(verdictOf(clean)).toMatchObject({ ok: true, title: 'Verified - 2 of 2 checks passed' });
  });

  test('a scan confirmed by its second reading says so', () => {
    const scan = v({ checks: [{ id: 'cross_read', label: 'Second reading', status: 'pass' }] });
    expect(verdictOf(scan)!.detail).toContain('second reading');
  });
});
