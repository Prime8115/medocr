/**
 * Pure helpers for reading/writing the backend extraction payload.
 *
 * Payload shape: { schema_version, doc_type, fields, meta }
 * Each leaf field is { value, confidence, ... }.
 *
 * Paths use dot + bracket notation, e.g.:
 *   "patient.name"                -> fields.patient.name
 *   "medications[0].strength"     -> fields.medications[0].strength
 */

export type Leaf = { value: string | null; confidence?: number | null; [k: string]: unknown };
export type Fields = Record<string, unknown>;

export interface ExtractionPayload {
  schema_version?: string;
  doc_type: 'prescription' | 'invoice';
  fields: Fields;
  meta?: { overall_confidence?: number | null; warnings?: string[] };
}

function tokenize(path: string): (string | number)[] {
  const parts: (string | number)[] = [];
  for (const seg of path.split('.')) {
    const m = seg.match(/^([^[\]]+)((\[\d+\])*)$/);
    if (!m) {
      parts.push(seg);
      continue;
    }
    parts.push(m[1]);
    const idx = m[2].match(/\d+/g);
    if (idx) idx.forEach((i) => parts.push(Number(i)));
  }
  return parts;
}

export function getLeaf(fields: Fields, path: string): Leaf | undefined {
  let node: unknown = fields;
  for (const key of tokenize(path)) {
    if (node == null) return undefined;
    node = (node as Record<string | number, unknown>)[key];
  }
  return node as Leaf | undefined;
}

/** Immutably set a leaf's `value` at the given path; returns a new fields object. */
export function setLeafValue(fields: Fields, path: string, value: string): Fields {
  const keys = tokenize(path);
  const clone = structuredCloneSafe(fields);
  let node: Record<string | number, unknown> = clone as Record<string | number, unknown>;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (node[k] == null) node[k] = typeof keys[i + 1] === 'number' ? [] : {};
    node = node[k] as Record<string | number, unknown>;
  }
  const last = keys[keys.length - 1];
  const existing = (node[last] as Leaf) ?? { value: null };
  node[last] = { ...existing, value };
  return clone;
}

function structuredCloneSafe<T>(obj: T): T {
  // structuredClone exists in Hermes/modern RN; fall back to JSON clone.
  const g = globalThis as { structuredClone?: <U>(o: U) => U };
  if (typeof g.structuredClone === 'function') return g.structuredClone(obj);
  return JSON.parse(JSON.stringify(obj));
}

// --- Section descriptors that drive the review UI ---
export interface FieldSpec {
  path: string;
  label: string;
}
export interface Section {
  title: string;
  fields: FieldSpec[];
}

const PRESCRIPTION_SINGLE: Section[] = [
  {
    title: 'patient',
    fields: [
      { path: 'patient.name', label: 'Name' },
      { path: 'patient.age', label: 'Age' },
      { path: 'patient.gender', label: 'Gender' },
    ],
  },
  {
    title: 'prescriber',
    fields: [
      { path: 'prescriber.name', label: 'Doctor' },
      { path: 'prescriber.registration_no', label: 'Reg. no' },
    ],
  },
];

const MED_FIELDS = (i: number): FieldSpec[] => [
  { path: `medications[${i}].name`, label: 'Medicine' },
  { path: `medications[${i}].strength`, label: 'Strength' },
  { path: `medications[${i}].form`, label: 'Form' },
  { path: `medications[${i}].frequency`, label: 'Frequency' },
  { path: `medications[${i}].duration`, label: 'Duration' },
  { path: `medications[${i}].instructions`, label: 'Instructions' },
];

const INVOICE_SINGLE: Section[] = [
  {
    title: 'supplier',
    fields: [
      { path: 'supplier.name', label: 'Supplier' },
      { path: 'supplier.gstin', label: 'GSTIN' },
    ],
  },
  {
    title: 'invoiceDetails',
    fields: [
      { path: 'invoice.invoice_no', label: 'Invoice no' },
      { path: 'invoice.invoice_date', label: 'Date' },
      { path: 'invoice.total_amount', label: 'Total' },
    ],
  },
];

const LINE_FIELDS = (i: number): FieldSpec[] => [
  { path: `line_items[${i}].description`, label: 'Item' },
  { path: `line_items[${i}].batch_no`, label: 'Batch' },
  { path: `line_items[${i}].expiry`, label: 'Expiry' },
  { path: `line_items[${i}].quantity`, label: 'Qty' },
  { path: `line_items[${i}].mrp`, label: 'MRP' },
  { path: `line_items[${i}].rate`, label: 'Rate' },
];

/** Build the ordered sections to render for a payload's doc type. */
export function buildSections(payload: ExtractionPayload): Section[] {
  const fields = payload.fields || {};
  if (payload.doc_type === 'invoice') {
    const items = (fields.line_items as unknown[]) || [];
    return [
      ...INVOICE_SINGLE,
      ...items.map((_, i) => ({ title: `${'lineItems'} #${i + 1}`, fields: LINE_FIELDS(i) })),
    ];
  }
  const meds = (fields.medications as unknown[]) || [];
  return [
    ...PRESCRIPTION_SINGLE,
    ...meds.map((_, i) => ({ title: `${'medications'} #${i + 1}`, fields: MED_FIELDS(i) })),
  ];
}
