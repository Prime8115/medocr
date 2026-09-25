/**
 * Column model for the invoice table view.
 *
 * Pure logic, no React: what columns to show, in what order, how wide, and how
 * each value is formatted. The review screen renders it; the tests pin it.
 *
 * Two rules drive the design:
 *
 *  - **Order by what a pharmacist checks first.** Item, then quantity, rate and
 *    amount, so the four columns that decide whether a line is right fit on a
 *    phone without scrolling sideways. Batch, expiry and the rest follow.
 *  - **Only show columns that carry data.** An invoice with no scheme goods
 *    should not spend 48px on an empty "Free" column.
 */
import { Fields, Leaf, getLeaf } from './payload';

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
  { key: 'description', label: 'Item', width: 148, numeric: false },
  { key: 'quantity', label: 'Qty', width: 52, numeric: true },
  { key: 'rate', label: 'Rate', width: 84, numeric: true, money: true },
  { key: 'amount', label: 'Amount', width: 92, numeric: true, money: true },
  { key: 'batch_no', label: 'Batch', width: 96, numeric: false },
  { key: 'expiry', label: 'Expiry', width: 68, numeric: false },
  { key: 'free_quantity', label: 'Free', width: 48, numeric: true },
  { key: 'mrp', label: 'MRP', width: 76, numeric: true, money: true },
  { key: 'discount_percent', label: 'Disc%', width: 56, numeric: true },
  { key: 'gst_percent', label: 'GST%', width: 56, numeric: true },
  { key: 'pack', label: 'Pack', width: 72, numeric: false },
  { key: 'hsn', label: 'HSN', width: 76, numeric: false },
];

/** Columns the Item column is never dropped in favour of — always shown. */
const ALWAYS = new Set(['description', 'quantity', 'rate', 'amount']);

export function lineItems(fields: Fields): Record<string, Leaf>[] {
  return ((fields.line_items as Record<string, Leaf>[]) || []).filter(Boolean);
}

function hasAnyValue(items: Record<string, Leaf>[], key: string): boolean {
  return items.some((item) => {
    const v = item?.[key]?.value;
    return v !== null && v !== undefined && String(v).trim() !== '';
  });
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

/** The columns to render for this invoice, in priority order. */
export function invoiceColumns(fields: Fields): Column[] {
  const items = lineItems(fields);
  const source = rateSource(items);
  return CATALOG.filter((c) => ALWAYS.has(c.key) || hasAnyValue(items, c.key)).map((c) =>
    c.key === 'rate' && source ? { ...c, sub: source } : c,
  );
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
  return Number.isInteger(n) ? String(n) : String(n);
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
