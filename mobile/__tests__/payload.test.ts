import { buildSections, getLeaf, setLeafValue, ExtractionPayload } from '../src/lib/payload';

const prescription: ExtractionPayload = {
  doc_type: 'prescription',
  fields: {
    patient: { name: { value: 'Ramesh', confidence: 0.9 }, age: { value: '45', confidence: 0.8 } },
    prescriber: { name: { value: 'Dr. X', confidence: 0.7 } },
    medications: [
      { name: { value: 'Paracetamol', confidence: 0.95 }, strength: { value: '500 mg', confidence: 0.4 } },
    ],
  },
};

describe('payload path helpers', () => {
  test('getLeaf reads nested and array paths', () => {
    expect(getLeaf(prescription.fields, 'patient.name')?.value).toBe('Ramesh');
    expect(getLeaf(prescription.fields, 'medications[0].strength')?.value).toBe('500 mg');
    expect(getLeaf(prescription.fields, 'medications[0].strength')?.confidence).toBe(0.4);
    expect(getLeaf(prescription.fields, 'nope.here')).toBeUndefined();
  });

  test('setLeafValue is immutable and preserves confidence', () => {
    const next = setLeafValue(prescription.fields, 'patient.name', 'Corrected');
    expect(getLeaf(next, 'patient.name')?.value).toBe('Corrected');
    expect(getLeaf(next, 'patient.name')?.confidence).toBe(0.9); // preserved
    // original untouched
    expect(getLeaf(prescription.fields, 'patient.name')?.value).toBe('Ramesh');
  });

  test('setLeafValue updates array items', () => {
    const next = setLeafValue(prescription.fields, 'medications[0].name', 'Crocin');
    expect(getLeaf(next, 'medications[0].name')?.value).toBe('Crocin');
  });

  test('buildSections for prescription includes one card per medication', () => {
    const sections = buildSections(prescription);
    const titles = sections.map((s) => s.title);
    // Titles are translated for display, not raw i18n keys.
    expect(titles).toContain('Patient');
    expect(titles).toContain('Prescriber');
    expect(titles.some((tt) => tt.startsWith('Medications #1'))).toBe(true);
  });

  const invoice: ExtractionPayload = {
    doc_type: 'invoice',
    fields: {
      supplier: { name: { value: 'S' } },
      invoice: { invoice_no: { value: '1' } },
      line_items: [{ description: { value: 'X' } }, { description: { value: 'Y' } }],
    },
  };

  test('buildSections for invoice includes line items', () => {
    const sections = buildSections(invoice);
    expect(sections.filter((s) => s.title.startsWith('Line items'))).toHaveLength(2);
    expect(sections.map((s) => s.title)).toContain('Invoice');
  });

  test('invoice header card exposes the total', () => {
    const invoiceSection = buildSections(invoice).find((s) => s.title === 'Invoice');
    expect(invoiceSection?.fields.map((f) => f.path)).toContain('invoice.total_amount');
  });

  test('line items expose amount and free quantity', () => {
    const first = buildSections(invoice).find((s) => s.title.startsWith('Line items'));
    const paths = first!.fields.map((f) => f.path);
    expect(paths).toContain('line_items[0].amount');
    expect(paths).toContain('line_items[0].free_quantity');
    expect(paths).toContain('line_items[0].mrp');
  });

  test('rate is labelled with the column the supplier printed', () => {
    const withPtr: ExtractionPayload = {
      doc_type: 'invoice',
      fields: {
        line_items: [
          { description: { value: 'X' }, rate: { value: '77.79' }, rate_source: { value: 'PTR' } },
          { description: { value: 'Y' }, rate: { value: '19.80' }, rate_source: { value: 'RATE' } },
          { description: { value: 'Z' }, rate: { value: '5.00' } },
        ],
      },
    };
    const labelFor = (i: number) =>
      buildSections(withPtr)
        .find((s) => s.title === `Line items #${i + 1}`)!
        .fields.find((f) => f.path === `line_items[${i}].rate`)!.label;

    expect(labelFor(0)).toBe('Rate (PTR)');
    expect(labelFor(1)).toBe('Rate (RATE)');
    expect(labelFor(2)).toBe('Rate'); // no source recorded -> plain label
  });
});

// The client's import specification (InvoiceScanRequirementData.xlsx): every one
// of these is on screen for every invoice - blank when the bill does not print it.
describe('every required invoice field is always shown', () => {
  const empty = { doc_type: 'invoice', fields: { line_items: [{}] } } as unknown as ExtractionPayload;
  const sections = buildSections(empty);
  const paths = sections.flatMap((s) => s.fields.map((f) => f.path));

  test('the 18 header fields', () => {
    for (const path of [
      'bill_to.name', 'ship_to.name', 'bill_to.gstin', 'ship_to.gstin',
      'invoice.total_gst_amount', 'invoice.total_utgst_amount', 'invoice.lr_date',
      'supplier.dl_no_1', 'supplier.dl_date_1', 'supplier.dl_no_2', 'supplier.dl_date_2',
      'supplier.dl_no_3', 'supplier.dl_date_3', 'invoice.total_discount_amount',
      'bill_to.pan', 'ship_to.pan', 'supplier.email', 'invoice.po_date',
    ]) {
      expect(paths).toContain(path);
    }
  });

  test('the 13 line fields, on every line', () => {
    for (const key of [
      'hsn', 'cd_percent', 'cd_amount', 'wp_percent', 'wp_amount', 'igst_amount', 'utgst_amount',
      'uom', 'mfg_date', 'net_amount', 'manufacturer', 'pack', 'scheme_value',
    ]) {
      expect(paths).toContain(`line_items[0].${key}`);
    }
  });

  test('a field the invoice lacks reads blank, not missing', () => {
    expect(getLeaf(empty.fields, 'line_items[0].hsn')?.value ?? '').toBe('');
    expect(getLeaf(empty.fields, 'bill_to.pan')?.value ?? '').toBe('');
  });

  test('the four checked first still lead each line', () => {
    const line = sections.find((s) => s.title.endsWith('#1'))!;
    expect(line.fields.slice(0, 1).map((f) => f.path)).toEqual(['line_items[0].description']);
    const keys = line.fields.map((f) => f.path.split('.').pop());
    expect(keys.indexOf('amount')).toBeLessThan(keys.indexOf('hsn'));
  });
});
