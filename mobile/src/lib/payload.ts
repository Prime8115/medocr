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

import { StringKey, t } from '../i18n/strings';

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

/**
 * A blank line item appended to the invoice; returns the new fields and its
 * index. For a document sent for manual entry - or a line the reading missed -
 * the pharmacist types it in. The backend fills every other field in, blank.
 */
export function addLineItem(fields: Fields): { fields: Fields; index: number } {
  const clone = structuredCloneSafe(fields);
  const items = ((clone.line_items as unknown[]) || []).slice();
  items.push({ description: { value: '', confidence: 1 } });
  clone.line_items = items as Fields[keyof Fields];
  return { fields: clone, index: items.length - 1 };
}

/** The invoice without line item `index`. */
export function removeLineItem(fields: Fields, index: number): Fields {
  const clone = structuredCloneSafe(fields);
  const items = ((clone.line_items as unknown[]) || []).filter((_, i) => i !== index);
  clone.line_items = items as Fields[keyof Fields];
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

// Every field the client's import asks for is listed, so it always appears -
// blank when the invoice does not print it - rather than only when filled.
const INVOICE_SINGLE: Section[] = [
  {
    title: 'supplier',
    fields: [
      { path: 'supplier.name', label: 'Supplier' },
      { path: 'supplier.gstin', label: 'GSTIN' },
      { path: 'supplier.pan', label: 'PAN' },
      { path: 'supplier.address', label: 'Address' },
      { path: 'supplier.email', label: 'Email' },
      { path: 'supplier.dl_no_1', label: 'Drug licence 1' },
      { path: 'supplier.dl_date_1', label: 'Drug licence 1 date' },
      { path: 'supplier.dl_no_2', label: 'Drug licence 2' },
      { path: 'supplier.dl_date_2', label: 'Drug licence 2 date' },
      { path: 'supplier.dl_no_3', label: 'Drug licence 3' },
      { path: 'supplier.dl_date_3', label: 'Drug licence 3 date' },
    ],
  },
  {
    title: 'billTo',
    fields: [
      { path: 'bill_to.name', label: 'Bill to' },
      { path: 'bill_to.gstin', label: 'GSTIN' },
      { path: 'bill_to.pan', label: 'PAN' },
      { path: 'bill_to.address', label: 'Address' },
    ],
  },
  {
    title: 'shipTo',
    fields: [
      { path: 'ship_to.name', label: 'Ship to' },
      { path: 'ship_to.gstin', label: 'GSTIN' },
      { path: 'ship_to.pan', label: 'PAN' },
      { path: 'ship_to.address', label: 'Address' },
    ],
  },
  {
    title: 'invoiceDetails',
    fields: [
      { path: 'invoice.document_title', label: 'Document title' },
      { path: 'invoice.invoice_no', label: 'Invoice no' },
      { path: 'invoice.invoice_date', label: 'Date' },
      { path: 'invoice.total_amount', label: 'Total' },
      { path: 'invoice.due_date', label: 'Due date' },
      { path: 'invoice.total_taxable_amount', label: 'Taxable total' },
      { path: 'invoice.total_gst_amount', label: 'GST total' },
      { path: 'invoice.total_discount_amount', label: 'Discount total' },
      { path: 'invoice.total_utgst_amount', label: 'UTGST total' },
      { path: 'invoice.lr_date', label: 'LR date' },
      { path: 'invoice.po_date', label: 'PO date' },
      { path: 'invoice.total_cgst_amount', label: 'CGST total' },
      { path: 'invoice.total_sgst_amount', label: 'SGST total' },
      { path: 'invoice.total_igst_amount', label: 'IGST total' },
      { path: 'invoice.eway_bill_no', label: 'E-way bill' },
      { path: 'invoice.irn', label: 'IRN' },
      { path: 'invoice.lr_no', label: 'LR no' },
      { path: 'invoice.transport', label: 'Transport' },
      { path: 'invoice.po_no', label: 'PO no' },
    ],
  },
];

/**
 * Label the rate with the column the supplier actually printed.
 *
 * MRP, PTR, PTS and the billed rate are different numbers; the backend records
 * which one fed `rate` in `rate_source`, and we surface it so the pharmacist can
 * see what the figure means instead of guessing per invoice.
 */
function rateLabel(fields: Fields, i: number): string {
  const source = String(getLeaf(fields, `line_items[${i}].rate_source`)?.value ?? '').trim();
  return source ? `Rate (${source})` : 'Rate';
}

// The four a pharmacist checks first lead; then every other line field, so each
// is always there to see and correct - blank when the invoice does not print it.
const LINE_FIELDS = (i: number, fields: Fields): FieldSpec[] => {
  const p = (key: string, label: string): FieldSpec => ({ path: `line_items[${i}].${key}`, label });
  return [
    p('description', 'Item'),
    p('batch_no', 'Batch'),
    p('expiry', 'Expiry'),
    p('quantity', 'Qty'),
    p('free_quantity', 'Free qty'),
    p('free_supply', 'Free supply'),
    p('mrp', 'MRP'),
    p('rate', rateLabel(fields, i)),
    p('amount', 'Amount'),
    p('hsn', 'HSN'),
    p('product_code', 'Product code'),
    p('manufacturer', 'Mfg name'),
    p('mfg_date', 'Mfg date'),
    p('pack', 'Pack'),
    p('uom', 'UOM'),
    p('total_quantity', 'Total qty'),
    p('ptr', 'PTR'),
    p('pts', 'PTS'),
    p('discount_percent', 'Discount %'),
    p('discount_amount', 'Discount amount'),
    p('scheme', 'Scheme'),
    p('scheme_percent', 'Scheme %'),
    p('scheme_value', 'Scheme value'),
    p('cd_percent', 'CD %'),
    p('cd_amount', 'CD amount'),
    p('wp_percent', 'WP %'),
    p('wp_amount', 'WP amount'),
    p('gross_amount', 'Gross amount'),
    p('gst_percent', 'GST %'),
    p('cgst_percent', 'CGST %'),
    p('cgst_amount', 'CGST amount'),
    p('sgst_percent', 'SGST %'),
    p('sgst_amount', 'SGST amount'),
    p('igst_percent', 'IGST %'),
    p('igst_amount', 'IGST amount'),
    p('utgst_percent', 'UTGST %'),
    p('utgst_amount', 'UTGST amount'),
    p('net_amount', 'Net amount'),
  ];
};

/** Section titles are stored as i18n keys; resolve them for display. */
function translateTitle(section: Section): Section {
  return { ...section, title: t(section.title as StringKey) };
}

/** Build the ordered sections to render for a payload's doc type. */
export function buildSections(payload: ExtractionPayload): Section[] {
  const fields = payload.fields || {};
  if (payload.doc_type === 'invoice') {
    const items = (fields.line_items as unknown[]) || [];
    return [
      ...INVOICE_SINGLE.map(translateTitle),
      ...items.map((_, i) => ({ title: `${t('lineItems')} #${i + 1}`, fields: LINE_FIELDS(i, fields) })),
    ];
  }
  const meds = (fields.medications as unknown[]) || [];
  return [
    ...PRESCRIPTION_SINGLE.map(translateTitle),
    ...meds.map((_, i) => ({ title: `${t('medications')} #${i + 1}`, fields: MED_FIELDS(i) })),
  ];
}

/** Columns the supplier printed that we have no name for. Shown in the row
 *  detail so a layout we have never seen is still visible, never dropped. */
export interface ExtraColumn { label: string; value: string }

export function extraColumns(fields: Fields, index: number): ExtraColumn[] {
  const raw = getLeaf(fields, `line_items[${index}].extras`) as unknown;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((e) => e as { label?: string; value?: string })
    .filter((e) => e && e.label && e.value)
    .map((e) => ({ label: String(e.label), value: String(e.value) }));
}

/** What a scanned document is called in lists: the bill itself, not its ID.
 *  An invoice is its supplier and number; a prescription its patient. */
export function documentTitle(doc: {
  doc_type: string;
  status: string;
  created_at: string;
  payload?: { fields?: Fields } | null;
}): { title: string; subtitle: string } {
  const fields = doc.payload?.fields ?? {};
  const v = (path: string) => {
    const value = getLeaf(fields, path)?.value;
    return value == null ? '' : String(value).trim();
  };
  const when = new Date(doc.created_at);
  const scanned = Number.isNaN(when.getTime())
    ? ''
    : when.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  if (doc.doc_type === 'prescription') {
    const who = v('patient.name') || v('prescriber.name');
    return {
      title: who ? `Prescription · ${who}` : 'Prescription',
      subtitle: scanned ? `scanned ${scanned}` : '',
    };
  }
  const supplier = v('supplier.name');
  const number = v('invoice.invoice_no');
  const total = Number(v('invoice.total_amount').replace(/[^\d.-]/g, ''));
  const money = v('invoice.total_amount') && Number.isFinite(total)
    ? `₹${total.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : '';
  let title = [supplier, number && `#${number}`].filter(Boolean).join(' · ');
  if (!title) {
    title = doc.status === 'queued' || doc.status === 'processing' ? 'Invoice (reading…)' : 'Invoice';
  }
  return {
    title,
    subtitle: [v('invoice.invoice_date'), money, !v('invoice.invoice_date') && scanned && `scanned ${scanned}`]
      .filter(Boolean)
      .join(' · '),
  };
}
