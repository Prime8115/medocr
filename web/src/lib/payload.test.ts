import { describe, expect, test } from 'vitest';

import { buildSections, getLeaf, setLeafValue, type Fields } from './payload';
import type { ExtractionPayload } from '../api/documents';

const prescription: ExtractionPayload = {
  doc_type: 'prescription',
  fields: {
    patient: { name: { value: 'Ramesh', confidence: 0.9 } },
    prescriber: { name: { value: 'Dr X', confidence: 0.7 } },
    medications: [{ name: { value: 'Paracetamol', confidence: 0.95 }, strength: { value: '500 mg', confidence: 0.4 } }],
  },
};

describe('web payload helpers', () => {
  test('getLeaf reads nested + array paths', () => {
    expect(getLeaf(prescription.fields, 'patient.name')?.value).toBe('Ramesh');
    expect(getLeaf(prescription.fields, 'medications[0].strength')?.confidence).toBe(0.4);
  });

  test('setLeafValue is immutable and keeps confidence', () => {
    const next = setLeafValue(prescription.fields, 'patient.name', 'Corrected');
    expect(getLeaf(next, 'patient.name')?.value).toBe('Corrected');
    expect(getLeaf(next, 'patient.name')?.confidence).toBe(0.9);
    expect(getLeaf(prescription.fields, 'patient.name')?.value).toBe('Ramesh');
  });

  test('buildSections adds one card per medication', () => {
    const titles = buildSections(prescription, prescription.fields).map((s) => s.title);
    expect(titles).toContain('Patient');
    expect(titles).toContain('Medication #1');
  });

  const invoice: ExtractionPayload = {
    doc_type: 'invoice',
    fields: {
      invoice: { total_amount: { value: '168924.60', confidence: 1 } },
      line_items: [
        { description: { value: 'ESPRA 40' }, rate: { value: '77.79' }, rate_source: { value: 'PTR' } },
        { description: { value: 'CALPOL 650' }, rate: { value: '19.80' }, rate_source: { value: 'RATE' } },
        { description: { value: 'AZEE 500' }, rate: { value: '98.40' } },
      ],
    },
  };

  test('invoice line items expose amount and free quantity', () => {
    const first = buildSections(invoice, invoice.fields).find((s) => s.title === 'Line item #1');
    const paths = first!.fields.map((f) => f.path);
    expect(paths).toContain('line_items[0].amount');
    expect(paths).toContain('line_items[0].free_quantity');
  });

  test('rate is labelled with the column the supplier printed', () => {
    const sections = buildSections(invoice, invoice.fields);
    const labelFor = (i: number) =>
      sections
        .find((s) => s.title === `Line item #${i + 1}`)!
        .fields.find((f) => f.path === `line_items[${i}].rate`)!.label;

    expect(labelFor(0)).toBe('Rate (PTR)');
    expect(labelFor(1)).toBe('Rate (RATE)');
    expect(labelFor(2)).toBe('Rate');
  });
});

// The client's import specification (InvoiceScanRequirementData.xlsx): every one
// of these is on screen for every invoice - blank when the bill does not print it.
describe('every required invoice field is always shown', () => {
  const fields = { line_items: [{}] } as unknown as Fields;
  const payload = { doc_type: 'invoice', fields } as unknown as ExtractionPayload;
  const paths = buildSections(payload, fields).flatMap((s) => s.fields.map((f) => f.path));

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
});
