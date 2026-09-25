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
