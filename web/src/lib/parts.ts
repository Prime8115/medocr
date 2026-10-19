/**
 * One PDF can hold several bills - Ajanta sends two credit notes in one
 * 7-page file. The server makes one document of each; these helpers say
 * which one is open and where the others are, so none is left to be found
 * in History.
 */
import { formatMoney } from './table';

export interface DocumentPart {
  id: string;
  part_no?: number | null;
  pages?: string | null;
  status: string;
  number?: string | null;
  total?: string | null;
}

export interface PartsOf {
  id: string;
  part_no?: number | null;
  part_count?: number | null;
  part_pages?: string | null;
  parts?: DocumentPart[];
}

const KINDS: Record<string, string> = {
  credit_note: 'Credit note',
  debit_note: 'Debit note',
  return: 'Goods return',
  delivery_challan: 'Delivery challan',
  proforma: 'Proforma invoice',
  quotation: 'Quotation',
  purchase_order: 'Purchase order',
};

/** What the bill is, by the kind its page shows; null for a plain invoice. */
export function kindLabel(kind: unknown): string | null {
  return typeof kind === 'string' ? KINDS[kind] ?? null : null;
}

export function pagesLabel(n: number): string {
  return `${n} ${n === 1 ? 'page' : 'pages'}`;
}

function pagesOf(span: string | null | undefined): string {
  if (!span) return '';
  return /[-,]/.test(span) ? `pages ${span}` : `page ${span}`;
}

/** "Bill 1 of 2 in this PDF · page 1", or null for a file of one bill. */
export function partTitle(doc: PartsOf): string | null {
  if (!doc.part_count || doc.part_count < 2 || !doc.part_no) return null;
  const where = pagesOf(doc.part_pages);
  return `Bill ${doc.part_no} of ${doc.part_count} in this PDF${where ? ` · ${where}` : ''}`;
}

/** The other bills of the same file, each as one line to tap. */
export function otherParts(doc: PartsOf): { id: string; label: string }[] {
  return (doc.parts ?? [])
    .filter((p) => p.id !== doc.id)
    .map((p) => {
      const bits = [`Bill ${p.part_no ?? '?'}`];
      if (p.number) bits.push(`#${p.number}`);
      if (p.pages) bits.push(pagesOf(p.pages));
      if (p.total) bits.push(`₹${formatMoney(p.total)}`);
      else if (p.status === 'queued' || p.status === 'processing') bits.push('reading…');
      return { id: p.id, label: bits.join(' · ') };
    });
}
