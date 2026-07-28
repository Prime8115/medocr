import { useCallback, useEffect, useState } from 'react';
import { Search, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { listDocuments, type DocumentDto } from '../api/documents';
import { confidencePercent } from '../lib/payload';

const STATUS_FILTERS = [undefined, 'needs_review', 'approved', 'pushed', 'failed'] as const;

export default function ReviewQueue() {
  const [docs, setDocs] = useState<DocumentDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<string | undefined>(undefined);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDocs(await listDocuments(status ? { status } : undefined));
    } catch {
      setError('Could not load documents. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  const q = query.trim().toLowerCase();
  const filtered = docs.filter((d) => !q || d.id.toLowerCase().includes(q) || d.doc_type.includes(q));

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <div>
          <h1>Review Queue</h1>
          <div className="text-muted">Review and approve extracted documents before they sync.</div>
        </div>
        <button className="btn-secondary" onClick={load} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        {STATUS_FILTERS.map((f) => (
          <button
            key={f ?? 'all'}
            className={status === f ? 'btn-primary' : 'btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 13 }}
            onClick={() => setStatus(f)}
          >
            {f ? f.replace('_', ' ') : 'All'}
          </button>
        ))}
      </div>

      <div className="glass-card" style={{ padding: 0 }}>
        <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-glass)', display: 'flex', alignItems: 'center', gap: 16 }}>
          <Search size={18} className="text-muted" />
          <input
            type="text"
            placeholder="Search by document ID or type…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ background: 'transparent', border: 'none', color: 'white', outline: 'none', flex: 1, fontSize: 15 }}
          />
        </div>

        {error ? (
          <div style={{ padding: 24, color: 'var(--danger)' }}>{error}</div>
        ) : loading ? (
          <div style={{ padding: 24 }} className="text-muted">Loading…</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 24 }} className="text-muted">No documents yet.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-glass)', color: 'var(--text-secondary)' }}>
                <th style={{ padding: '16px 24px', fontWeight: 500 }}>Document ID</th>
                <th style={{ padding: '16px 24px', fontWeight: 500 }}>Type</th>
                <th style={{ padding: '16px 24px', fontWeight: 500 }}>Confidence</th>
                <th style={{ padding: '16px 24px', fontWeight: 500 }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((doc) => (
                <tr
                  key={doc.id}
                  className="data-row"
                  onClick={() => navigate(`/documents/${doc.id}`)}
                  style={{ borderBottom: '1px solid var(--border-glass)' }}
                >
                  <td style={{ padding: '16px 24px', fontFamily: 'monospace' }}>{doc.id}</td>
                  <td style={{ padding: '16px 24px', textTransform: 'capitalize' }}>{doc.doc_type}</td>
                  <td style={{ padding: '16px 24px' }}>
                    <ConfidenceBar value={doc.overall_confidence ?? null} />
                  </td>
                  <td style={{ padding: '16px 24px' }}>
                    <span className={`badge badge-${doc.status}`}>{doc.status.replace('_', ' ')}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function ConfidenceBar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted">—</span>;
  const color = value > 0.85 ? 'var(--success)' : value > 0.6 ? 'var(--warning)' : 'var(--danger)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ width: 60, height: 6, background: 'rgba(255,255,255,0.1)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${value * 100}%`, height: '100%', background: color }} />
      </div>
      <span className="text-muted" style={{ fontSize: 12 }}>{confidencePercent(value)}</span>
    </div>
  );
}
