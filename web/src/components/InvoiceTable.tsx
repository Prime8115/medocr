/**
 * Invoice line items as a table — the view the pharmacist already has on paper.
 *
 * A 143-line invoice cannot be checked as a list of cards: you cannot compare
 * rows, scan a column of numbers, or spot a repeat. Here the desktop gets what a
 * phone cannot: the item column frozen to the left while the numbers scroll, and
 * a sticky header above them.
 *
 * Numbers are right-aligned with tabular figures, fixed decimals and fixed
 * column widths, so a row that is wrong is visible as a shape.
 */
import type { Leaf } from '../api/documents';
import { isLowConfidence, type Fields } from '../lib/payload';
import { cellText, invoiceColumns, type Column } from '../lib/table';

export interface TableRow {
  item: Record<string, Leaf>;
  /** Index into the untouched line_items array — what the edit modal needs. */
  index: number;
  attention: boolean;
  /** Inventory match score, when inventory is connected. */
  matchScore?: number | null;
}

const FROZEN_BG = '#131722';

function headerStyle(column: Column, frozen: boolean): React.CSSProperties {
  return {
    width: column.width,
    minWidth: column.width,
    padding: '10px 12px',
    textAlign: column.numeric ? 'right' : 'left',
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    color: 'var(--text-secondary)',
    background: FROZEN_BG,
    position: frozen ? 'sticky' : undefined,
    left: frozen ? 0 : undefined,
    zIndex: frozen ? 3 : 2,
  };
}

function cellStyle(column: Column, frozen: boolean, low: boolean, rowBg: string): React.CSSProperties {
  return {
    width: column.width,
    minWidth: column.width,
    padding: '9px 12px',
    textAlign: column.numeric ? 'right' : 'left',
    fontVariantNumeric: column.numeric ? 'tabular-nums' : undefined,
    fontWeight: column.key === 'description' ? 600 : 400,
    color: low ? 'var(--danger)' : column.key === 'description' ? undefined : 'var(--text-secondary)',
    background: frozen ? rowBg : undefined,
    position: frozen ? 'sticky' : undefined,
    left: frozen ? 0 : undefined,
    zIndex: frozen ? 1 : undefined,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  };
}

export default function InvoiceTable({
  fields,
  rows,
  onSelect,
  showMatch,
  maxHeight = 560,
}: {
  fields: Fields;
  rows: TableRow[];
  onSelect: (index: number) => void;
  showMatch?: boolean;
  maxHeight?: number;
}) {
  const columns = invoiceColumns(fields);

  if (rows.length === 0) {
    return <div className="text-muted" style={{ padding: 24, textAlign: 'center' }}>No items match your search.</div>;
  }

  return (
    <div style={{ maxHeight, overflow: 'auto' }}>
      <table style={{ borderCollapse: 'separate', borderSpacing: 0, fontSize: 14, minWidth: '100%' }}>
        <thead>
          <tr style={{ position: 'sticky', top: 0, zIndex: 2 }}>
            {columns.map((column, ci) => (
              <th key={column.key} style={headerStyle(column, ci === 0)}>
                {column.label}
                {column.sub ? (
                  <span style={{ display: 'block', fontSize: 10, opacity: 0.7, fontWeight: 400 }}>({column.sub})</span>
                ) : null}
              </th>
            ))}
            {showMatch && <th style={{ ...headerStyle(columns[0], false), width: 80, minWidth: 80, textAlign: 'left' }}>Match</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const rowBg = row.attention ? 'rgba(245,158,11,0.10)' : FROZEN_BG;
            return (
              <tr
                key={row.index}
                className="data-row"
                onClick={() => onSelect(row.index)}
                style={{ cursor: 'pointer', background: row.attention ? 'rgba(245,158,11,0.10)' : undefined }}
              >
                {columns.map((column, ci) => {
                  const low = isLowConfidence(row.item?.[column.key]?.confidence ?? null);
                  return (
                    <td
                      key={column.key}
                      style={{ ...cellStyle(column, ci === 0, low, rowBg), borderTop: '1px solid var(--border-glass)' }}
                      title={cellText(row.item, column)}
                    >
                      {cellText(row.item, column) || '—'}
                    </td>
                  );
                })}
                {showMatch && (
                  <td style={{ padding: '9px 12px', borderTop: '1px solid var(--border-glass)' }}>
                    {row.matchScore != null ? (
                      <span className={`badge ${row.matchScore >= 85 ? 'badge-approved' : 'badge-processing'}`}>
                        {Math.round(row.matchScore)}%
                      </span>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
