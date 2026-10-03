/**
 * Column model for the invoice table view.
 *
 * Pure logic, no React: what columns to show, in what order, how wide, and how
 * each value is formatted. Mirrors mobile/src/lib/table.ts so a line reads the
 * same on the counter phone and in the admin console.
 *
 * Two rules drive the design:
 *
 *  - **Order by what a pharmacist checks first.** Item, then quantity, rate and
 *    amount; batch, expiry and the rest follow.
 *  - **Every column, always.** The client's import needs every field on every
 *    invoice, so a column the bill does not print is shown blank rather than
 *    left out - an absent column reads as "the app did not look", a blank one
 *    as "the invoice does not have it".
 */
import type { Leaf } from '../api/documents';
import { getLeaf, type Fields } from './payload';

export interface Column {
  /** Field name inside a line item. */
  key: string;
  /** Header text. */
  label: string;
  /** Second header line, e.g. the supplier's own price-column name. */
  sub?: string;
  width: number;
  numeric: boolean;
  /** Numeric columns render with fixed decimals; quantities stay whole. */
  money?: boolean;
}

const CATALOG: Column[] = [
  { key: 'description', label: 'Item', width: 240, numeric: false },
  { key: 'quantity', label: 'Qty', width: 68, numeric: true },
  { key: 'rate', label: 'Rate', width: 110, numeric: true, money: true },
  { key: 'amount', label: 'Amount', width: 120, numeric: true, money: true },
  { key: 'batch_no', label: 'Batch', width: 124, numeric: false },
  { key: 'expiry', label: 'Expiry', width: 88, numeric: false },
  { key: 'hsn', label: 'HSN', width: 98, numeric: false },
  { key: 'product_code', label: 'Code', width: 102, numeric: false },
  { key: 'manufacturer', label: 'Mfg name', width: 124, numeric: false },
  { key: 'mfg_date', label: 'Mfg date', width: 94, numeric: false },
  { key: 'pack', label: 'Pack', width: 94, numeric: false },
  { key: 'uom', label: 'UOM', width: 68, numeric: false },
  { key: 'free_quantity', label: 'Free', width: 62, numeric: true },
  { key: 'total_quantity', label: 'Tot qty', width: 76, numeric: true },
  { key: 'mrp', label: 'MRP', width: 98, numeric: true, money: true },
  { key: 'ptr', label: 'PTR', width: 98, numeric: true, money: true },
  { key: 'pts', label: 'PTS', width: 98, numeric: true, money: true },
  { key: 'discount_percent', label: 'Disc%', width: 72, numeric: true },
  { key: 'discount_amount', label: 'Disc amt', width: 98, numeric: true, money: true },
  { key: 'scheme', label: 'Scheme', width: 98, numeric: false },
  { key: 'scheme_value', label: 'Sch val', width: 98, numeric: true, money: true },
  { key: 'cd_percent', label: 'CD%', width: 72, numeric: true },
  { key: 'cd_amount', label: 'CD amt', width: 98, numeric: true, money: true },
  { key: 'wp_percent', label: 'WP%', width: 72, numeric: true },
  { key: 'wp_amount', label: 'WP amt', width: 98, numeric: true, money: true },
  { key: 'gross_amount', label: 'Gross', width: 110, numeric: true, money: true },
  { key: 'gst_percent', label: 'GST%', width: 72, numeric: true },
  { key: 'cgst_percent', label: 'CGST%', width: 78, numeric: true },
  { key: 'cgst_amount', label: 'CGST', width: 98, numeric: true, money: true },
  { key: 'sgst_percent', label: 'SGST%', width: 78, numeric: true },
  { key: 'sgst_amount', label: 'SGST', width: 98, numeric: true, money: true },
  { key: 'igst_percent', label: 'IGST%', width: 78, numeric: true },
  { key: 'igst_amount', label: 'IGST', width: 98, numeric: true, money: true },
  { key: 'utgst_percent', label: 'UTGST%', width: 84, numeric: true },
  { key: 'utgst_amount', label: 'UTGST', width: 98, numeric: true, money: true },
  { key: 'net_amount', label: 'Net amt', width: 110, numeric: true, money: true },
];

export function lineItems(fields: Fields): Record<string, Leaf>[] {
  return ((fields.line_items as Record<string, Leaf>[]) || []).filter(Boolean);
}

/**
 * The supplier's own name for the price in `rate` ("RATE", "PTR", "PTS").
 * Uses the most common value across the invoice, so one unreadable row cannot
 * mislabel the whole column.
 */
export function rateSource(items: Record<string, Leaf>[]): string | undefined {
  const counts = new Map<string, number>();
  for (const item of items) {
    const source = String(item?.rate_source?.value ?? '').trim();
    if (source) counts.set(source, (counts.get(source) ?? 0) + 1);
  }
  let best: string | undefined;
  let bestCount = 0;
  for (const [source, count] of counts) {
    if (count > bestCount) {
      best = source;
      bestCount = count;
    }
  }
  return best;
}

/** Every column, in priority order; a field the invoice lacks renders blank. */
export function invoiceColumns(fields: Fields): Column[] {
  const source = rateSource(lineItems(fields));
  return CATALOG.map((c) => (c.key === 'rate' && source ? { ...c, sub: source } : c));
}

export function totalWidth(columns: Column[]): number {
  return columns.reduce((sum, c) => sum + c.width, 0);
}

// ------------------------------- formatting -------------------------------
/** Indian digit grouping: 1,68,924.60 — not 168,924.60. */
export function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  return `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')},${last3}`;
}

export function formatMoney(raw: unknown, decimals = 2): string {
  const n = Number(String(raw ?? '').replace(/,/g, ''));
  if (!Number.isFinite(n) || String(raw ?? '').trim() === '') return String(raw ?? '');
  const fixed = Math.abs(n).toFixed(decimals);
  const [int, dec] = fixed.split('.');
  return `${n < 0 ? '-' : ''}${groupIndian(int)}${dec ? `.${dec}` : ''}`;
}

export function formatQuantity(raw: unknown): string {
  const text = String(raw ?? '').trim();
  const n = Number(text.replace(/,/g, ''));
  if (!Number.isFinite(n) || text === '') return text;
  return String(n);
}

/** The display string for one cell. */
export function cellText(item: Record<string, Leaf>, column: Column): string {
  const raw = item?.[column.key]?.value;
  if (raw === null || raw === undefined) return '';
  if (column.money) return formatMoney(raw);
  if (column.numeric) return formatQuantity(raw);
  return String(raw);
}

/** The invoice total as printed, formatted for the footer. */
export function printedTotal(fields: Fields): string {
  return formatMoney(getLeaf(fields, 'invoice.total_amount')?.value ?? '');
}
