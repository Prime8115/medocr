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
    expect(titles).toContain('patient');
    expect(titles).toContain('prescriber');
    expect(titles.some((tt) => tt.startsWith('medications #1'))).toBe(true);
  });

  test('buildSections for invoice includes line items', () => {
    const invoice: ExtractionPayload = {
      doc_type: 'invoice',
      fields: {
        supplier: { name: { value: 'S' } },
        invoice: { invoice_no: { value: '1' } },
        line_items: [{ description: { value: 'X' } }, { description: { value: 'Y' } }],
      },
    };
    const sections = buildSections(invoice);
    expect(sections.filter((s) => s.title.startsWith('lineItems')).length).toBe(2);
  });
});
