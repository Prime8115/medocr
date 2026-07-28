import { describe, expect, test } from 'vitest';

import { buildSections, getLeaf, setLeafValue } from './payload';
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
});
