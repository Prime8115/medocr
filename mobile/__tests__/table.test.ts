import {
  cellText,
  formatMoney,
  formatQuantity,
  groupIndian,
  invoiceColumns,
  lineItems,
  printedTotal,
  rateSource,
  totalWidth,
} from '../src/lib/table';
import type { Fields } from '../src/lib/payload';

const f = (value: string | null, confidence: number | null = 1) => ({ value, confidence });

const invoice: Fields = {
  invoice: { total_amount: f('168924.60') },
  line_items: [
    {
      description: f('ESPRA 40 TAB'), batch_no: f('TB-022501'), expiry: f('01/2027'),
      quantity: f('25'), free_quantity: f('5'), mrp: f('108.90'), ptr: f('77.79'),
      rate: f('77.79'), rate_source: f('PTR', null), amount: f('1750.25'),
      gst_percent: f('12'), hsn: f('30049039'), pack: f('10 X 10'), discount_percent: f(null),
    },
    {
      description: f('IMOL PLUS TAB'), batch_no: f('BEB1106'), expiry: f('04/2027'),
      quantity: f('240'), free_quantity: f(null), mrp: f('23.70'), ptr: f('16.93'),
      rate: f('16.93'), rate_source: f('PTR', null), amount: f('3657.60'),
      gst_percent: f('12'), hsn: f('30049063'), pack: f('20 X 2'), discount_percent: f(null),
    },
  ],
};

describe('invoice table columns', () => {
  test('orders columns by what a pharmacist checks first', () => {
    const keys = invoiceColumns(invoice).map((c) => c.key);
    expect(keys.slice(0, 4)).toEqual(['description', 'quantity', 'rate', 'amount']);
  });

  test('the first four columns fit a phone without scrolling sideways', () => {
    const first4 = invoiceColumns(invoice).slice(0, 4);
    expect(totalWidth(first4)).toBeLessThanOrEqual(390);
  });

  test('drops columns that carry no data on this invoice', () => {
    const keys = invoiceColumns(invoice).map((c) => c.key);
    expect(keys).toContain('free_quantity');       // row 1 has scheme goods
    expect(keys).not.toContain('discount_percent'); // no row has a discount
  });

  test('always keeps the four core columns even when empty', () => {
    const sparse: Fields = { line_items: [{ description: f('X') }] };
    const keys = invoiceColumns(sparse).map((c) => c.key);
    expect(keys).toEqual(['description', 'quantity', 'rate', 'amount']);
  });

  test('labels the rate column with the supplier own wording', () => {
    const rate = invoiceColumns(invoice).find((c) => c.key === 'rate');
    expect(rate?.label).toBe('Rate');
    expect(rate?.sub).toBe('PTR');
  });

  test('rate column has no sub-label when no source was recorded', () => {
    const noSource: Fields = { line_items: [{ description: f('X'), rate: f('5.00') }] };
    expect(invoiceColumns(noSource).find((c) => c.key === 'rate')?.sub).toBeUndefined();
  });

  test('one odd row cannot mislabel the whole rate column', () => {
    const mixed = [
      { rate_source: f('PTR', null) },
      { rate_source: f('PTR', null) },
      { rate_source: f('RATE', null) },
    ];
    expect(rateSource(mixed)).toBe('PTR');
  });

  test('lineItems tolerates a missing list', () => {
    expect(lineItems({})).toEqual([]);
  });
});

describe('number formatting', () => {
  test('groups digits the Indian way', () => {
    expect(groupIndian('168924')).toBe('1,68,924');
    expect(groupIndian('21453055')).toBe('2,14,53,055');
    expect(groupIndian('999')).toBe('999');
    expect(groupIndian('1750')).toBe('1,750');
  });

  test('money keeps two decimals', () => {
    expect(formatMoney('168924.6')).toBe('1,68,924.60');
    expect(formatMoney('77.79')).toBe('77.79');
    expect(formatMoney('1750.25')).toBe('1,750.25');
  });

  test('money passes through anything that is not a number', () => {
    expect(formatMoney('')).toBe('');
    expect(formatMoney(null)).toBe('');
    expect(formatMoney('n/a')).toBe('n/a');
  });

  test('quantities stay whole', () => {
    expect(formatQuantity('240')).toBe('240');
    expect(formatQuantity('')).toBe('');
  });
});

describe('cell rendering', () => {
  const columns = invoiceColumns(invoice);
  const col = (key: string) => columns.find((c) => c.key === key)!;
  const row = (invoice.line_items as Record<string, { value: string | null }>[])[0];

  test('formats money columns and leaves text alone', () => {
    expect(cellText(row, col('amount'))).toBe('1,750.25');
    expect(cellText(row, col('rate'))).toBe('77.79');
    expect(cellText(row, col('description'))).toBe('ESPRA 40 TAB');
    expect(cellText(row, col('batch_no'))).toBe('TB-022501');
  });

  test('renders an empty string for a missing value', () => {
    const blank = (invoice.line_items as Record<string, { value: string | null }>[])[1];
    expect(cellText(blank, col('free_quantity'))).toBe('');
  });

  test('printedTotal formats the invoice total', () => {
    expect(printedTotal(invoice)).toBe('1,68,924.60');
  });
});
