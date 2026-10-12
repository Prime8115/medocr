/**
 * What the reader has learned about suppliers' columns from this shop's
 * corrections - "for this supplier, 'RETAILER RATE' is the PTR" - with how
 * often each lesson was used, and a way to forget one known to be wrong.
 * Lessons a reviewer had to undo twice are retired automatically.
 */
import { useEffect, useState } from 'react';

import { forgetSupplierLesson, getSupplierLessons, type SupplierLesson } from '../api/documents';
import { fieldLabel } from '../lib/verification';

export default function SupplierLessons({ names }: { names: Record<string, string> }) {
  const [lessons, setLessons] = useState<SupplierLesson[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSupplierLessons()
      .then(setLessons)
      .catch(() => setError('Could not load what the app has learned.'));
  }, []);

  async function forget(id: string) {
    try {
      await forgetSupplierLesson(id);
      setLessons((all) => (all ?? []).filter((l) => l.id !== id));
    } catch {
      setError('Could not forget that lesson.');
    }
  }

  if (error) return <div style={{ color: 'var(--danger)', marginTop: 24 }}>{error}</div>;
  if (!lessons || lessons.length === 0) return null;
  return (
    <div style={{ marginTop: 32 }}>
      <h2 style={{ fontSize: 18 }}>Learned from your corrections</h2>
      <div className="text-muted" style={{ marginBottom: 12 }}>
        Columns the app now reads differently for a supplier, because a reviewer corrected them.
      </div>
      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
          <thead>
            <tr className="text-muted" style={{ textAlign: 'left', fontSize: 12, textTransform: 'uppercase' }}>
              <th style={{ padding: '12px 16px' }}>Supplier</th>
              <th style={{ padding: '12px 16px' }}>Lesson</th>
              <th style={{ padding: '12px 16px' }}>Used</th>
              <th style={{ padding: '12px 16px' }}>Status</th>
              <th style={{ padding: '12px 16px' }} />
            </tr>
          </thead>
          <tbody>
            {lessons.map((l) => (
              <tr key={l.id} style={{ borderTop: '1px solid var(--border-glass)' }}>
                <td style={{ padding: '12px 16px' }}>
                  <div style={{ fontWeight: 600 }}>{names[l.supplier_gstin] || l.supplier_gstin}</div>
                  <div className="text-muted" style={{ fontSize: 12 }}>{l.supplier_gstin}</div>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  “{l.heading}” {l.kind === 'is' ? 'is' : 'is not'} the {fieldLabel(`line_items.${l.field}`)}
                </td>
                <td style={{ padding: '12px 16px', fontVariantNumeric: 'tabular-nums' }}>
                  {l.times_used}×
                  {l.times_undone + l.times_failed > 0 && (
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      undone {l.times_undone}, failed {l.times_failed}
                    </div>
                  )}
                </td>
                <td style={{ padding: '12px 16px', color: l.active ? 'var(--success)' : 'var(--text-muted, #9ca3af)' }}>
                  {l.active ? 'In use' : 'Retired'}
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <button className="btn-secondary" style={{ padding: '4px 10px', fontSize: 12 }}
                    onClick={() => forget(l.id)}>
                    Forget
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
