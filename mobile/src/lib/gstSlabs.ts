/** Individual GST summary rows printed below the product table. */
export type GstSlabRow = Partial<Record<
  | 'amount' | 'scheme_amount' | 'discount_amount' | 'taxable_amount'
  | 'cgst_percent' | 'cgst_amount' | 'sgst_percent' | 'sgst_amount'
  | 'igst_percent' | 'igst_amount' | 'utgst_percent' | 'utgst_amount'
  | 'total_amount', string>>;

const valueKeys: (keyof GstSlabRow)[] = [
  'amount', 'scheme_amount', 'discount_amount', 'taxable_amount',
  'cgst_percent', 'cgst_amount', 'sgst_percent', 'sgst_amount',
  'igst_percent', 'igst_amount', 'utgst_percent', 'utgst_amount', 'total_amount',
];

/** Old saved invoices may not have rows; a blank printed slab is not a division. */
export function gstSlabRows(summary: unknown): GstSlabRow[] {
  if (!summary || typeof summary !== 'object') return [];
  const rows = (summary as { rows?: unknown }).rows;
  if (!Array.isArray(rows)) return [];
  return rows.flatMap((row): GstSlabRow[] => {
    if (!row || typeof row !== 'object') return [];
    const values = row as Record<string, unknown>;
    const parsed: GstSlabRow = {};
    for (const key of valueKeys) {
      const value = values[key];
      if (typeof value === 'string' && Number.isFinite(Number(value))) parsed[key] = value;
    }
    return ['amount', 'taxable_amount', 'cgst_amount', 'sgst_amount',
      'igst_amount', 'utgst_amount'].some((key) => Number(parsed[key as keyof GstSlabRow]) > 0)
      ? [parsed] : [];
  });
}

export function gstSlabRate(row: GstSlabRow): string | null {
  const rates = ['cgst_percent', 'sgst_percent', 'igst_percent', 'utgst_percent']
    .map((key) => Number(row[key as keyof GstSlabRow] || 0));
  const total = rates.reduce((sum, rate) => sum + rate, 0);
  return total > 0 ? `${Number(total.toFixed(2))}%` : null;
}
