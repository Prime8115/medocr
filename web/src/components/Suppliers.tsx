/**
 * Suppliers — how well each supplier's bills are read.
 *
 * Every supplier prints its bill its own way, and a new one is where reading
 * breaks. This lists them with the ones needing attention first: how many of
 * their bills arrived verified (every check passed before anyone touched them),
 * which fields reviewers keep correcting, which printed fields we miss, and
 * what we have learned for them so far.
 */
import { useEffect, useState } from 'react';
import { Truck } from 'lucide-react';

import { getSupplierReport, type SupplierCoverage } from '../api/documents';
import { fieldLabel as fieldName } from '../lib/verification';

const STATUS: Record<SupplierCoverage['status'], { label: string; color: string }> = {
  needs_attention: { label: 'Needs attention', color: 'var(--danger)' },
  fair: { label: 'Fair', color: 'var(--warning)' },
  new: { label: 'New', color: 'var(--text-muted, #9ca3af)' },
  good: { label: 'Good', color: 'var(--success)' },
};

function Counts({ items }: { items: { field?: string; check?: string; count: number }[] }) {
  if (!items.length) return <span className="text-muted">—</span>;
  return (
    <span>
      {items.map((i) => `${fieldName(i.field ?? i.check ?? '')} ×${i.count}`).join(', ')}
    </span>
  );
}

export default function Suppliers() {
  const [rows, setRows] = useState<SupplierCoverage[] | null>(null);
  const [days, setDays] = useState(90);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    getSupplierReport(days)
      .then((r) => setRows(r.suppliers))
      .catch(() => setError('Could not load the supplier report.'));
  }, [days]);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <div>
          <h1>Suppliers</h1>
          <div className="text-muted">
            How well each supplier's bills are read — the ones needing attention first.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {[30, 90, 365].map((d) => (
            <button
              key={d}
              className={days === d ? 'btn-primary' : 'btn-secondary'}
              style={{ padding: '6px 14px', fontSize: 13 }}
              onClick={() => setDays(d)}
            >
              {d} days
            </button>
          ))}
        </div>
      </div>

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        {error ? (
          <div style={{ padding: 24, color: 'var(--danger)' }}>{error}</div>
        ) : rows === null ? (
          <div style={{ padding: 24 }} className="text-muted">Loading…</div>
        ) : rows.length === 0 ? (
          <div style={{ padding: 24 }} className="text-muted">No supplier invoices in this period.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr className="text-muted" style={{ textAlign: 'left', fontSize: 12, textTransform: 'uppercase' }}>
                <th style={{ padding: '12px 16px' }}><Truck size={14} /> Supplier</th>
                <th style={{ padding: '12px 16px' }}>Bills</th>
                <th style={{ padding: '12px 16px' }}>Verified on arrival</th>
                <th style={{ padding: '12px 16px' }}>Corrected most</th>
                <th style={{ padding: '12px 16px' }}>Printed but missed</th>
                <th style={{ padding: '12px 16px' }}>Learned</th>
                <th style={{ padding: '12px 16px' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const status = STATUS[r.status];
                return (
                  <tr key={r.supplier_gstin ?? r.supplier_name} style={{ borderTop: '1px solid var(--border-glass)' }}>
                    <td style={{ padding: '12px 16px' }}>
                      <div style={{ fontWeight: 600 }}>{r.supplier_name || 'Unnamed supplier'}</div>
                      <div className="text-muted" style={{ fontSize: 12 }}>{r.supplier_gstin ?? 'no GSTIN read'}</div>
                    </td>
                    <td style={{ padding: '12px 16px', fontVariantNumeric: 'tabular-nums' }}>
                      {r.documents}
                      {r.edited_documents > 0 && (
                        <div className="text-muted" style={{ fontSize: 12 }}>{r.edited_documents} edited</div>
                      )}
                    </td>
                    <td style={{ padding: '12px 16px', fontVariantNumeric: 'tabular-nums' }}>
                      {r.verified_on_arrival_pct != null ? `${r.verified_on_arrival_pct}%` : '—'}
                      <div className="text-muted" style={{ fontSize: 12 }}>
                        {r.verified_on_arrival} of {r.documents}
                      </div>
                    </td>
                    <td style={{ padding: '12px 16px' }}><Counts items={r.corrected_fields} /></td>
                    <td style={{ padding: '12px 16px' }}><Counts items={r.missed_fields} /></td>
                    <td style={{ padding: '12px 16px', fontSize: 13 }}>
                      {r.learned_labels > 0 || r.remembered_choices > 0 ? (
                        <>
                          {r.learned_labels > 0 && <div>{r.learned_labels} field location(s)</div>}
                          {r.remembered_choices > 0 && <div>{r.remembered_choices} choice(s)</div>}
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td style={{ padding: '12px 16px', color: status.color, fontWeight: 600 }}>{status.label}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="text-muted" style={{ fontSize: 12, marginTop: 12 }}>
        When a reviewer fills in a field we missed, we learn where that supplier prints it, and read it there
        on its next bill.
      </div>
    </div>
  );
}
