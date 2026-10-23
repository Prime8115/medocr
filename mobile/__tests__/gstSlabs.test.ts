import { gstSlabRate, gstSlabRows } from '../src/lib/gstSlabs';

test('keeps both printed GST divisions and omits an empty slab', () => {
  const rows = gstSlabRows({ rows: [
    { amount: '0.00', taxable_amount: '0.00', cgst_percent: '6.00' },
    { amount: '5162.10', taxable_amount: '5007.24', cgst_percent: '6.00',
      cgst_amount: '300.43', sgst_percent: '6.00', sgst_amount: '300.43' },
    { amount: '6785.10', taxable_amount: '6581.55', cgst_percent: '9.00',
      cgst_amount: '592.34', sgst_percent: '9.00', sgst_amount: '592.34' },
  ] });
  expect(rows.map((row) => row.amount)).toEqual(['5162.10', '6785.10']);
  expect(rows.map(gstSlabRate)).toEqual(['12%', '18%']);
  expect(gstSlabRows(undefined)).toEqual([]);
});
