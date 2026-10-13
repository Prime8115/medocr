import { useState } from 'react';
import { recheckField, type RecheckResult } from '../api/documents';

/**
 * "Recheck" under a field the reviewer doubts: the AI is asked again about
 * this one value and says what the bill prints for it. Nothing changes until
 * the reviewer uses the value and saves - then it is recorded as a correction,
 * so this supplier's next bill is read right.
 */
export default function RecheckField({ docId, path, onUse }: {
  docId: string;
  path: string;
  onUse: (value: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RecheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await recheckField(docId, path));
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Could not recheck this field. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  const offer = result && result.found && !result.agrees;
  return (
    <div style={{ fontSize: 12, marginTop: 4 }}>
      {!result && (
        <button type="button" className="link-button" onClick={ask} disabled={busy}
          style={{ background: 'none', border: 'none', padding: 0, color: 'var(--primary-color)', cursor: busy ? 'wait' : 'pointer', fontSize: 12 }}>
          {busy ? 'Rechecking…' : 'Recheck'}
        </button>
      )}
      {error && <div style={{ color: 'var(--danger)' }}>{error}</div>}
      {result && (
        <div role="status" style={{ padding: '6px 8px', borderRadius: 6, background: 'var(--surface-2, rgba(127,127,127,0.12))' }}>
          <div style={{ color: result.on_page === false ? 'var(--danger)' : undefined }}>{result.message}</div>
          {result.printed_text && <div className="text-muted" style={{ marginTop: 2 }}>On the bill: “{result.printed_text}”</div>}
          <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
            {offer && (
              <button type="button" className="btn-secondary" style={{ padding: '2px 10px', fontSize: 12 }}
                onClick={() => { onUse(result.found as string); setResult(null); }}>
                {result.on_page === false ? 'Use anyway' : 'Use this'}
              </button>
            )}
            <button type="button" onClick={() => setResult(null)}
              style={{ background: 'none', border: 'none', padding: 0, color: 'var(--text-muted, inherit)', cursor: 'pointer', fontSize: 12 }}>
              {offer ? 'Keep mine' : 'Close'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
