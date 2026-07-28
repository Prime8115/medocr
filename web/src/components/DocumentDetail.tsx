import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Check, Save, Send } from 'lucide-react';

import {
  approveDocument,
  getDocument,
  patchDocument,
  pushDocument,
  type DocumentDto,
  type ExtractionPayload,
} from '../api/documents';
import { buildSections, confidencePercent, getLeaf, isLowConfidence, setLeafValue } from '../lib/payload';

type Fields = Record<string, unknown>;

function accentFor(c: number | null | undefined): string {
  if (c == null) return 'var(--border-glass)';
  if (c < 0.6) return 'var(--danger)';
  if (c < 0.85) return 'var(--warning)';
  return 'var(--success)';
}

export default function DocumentDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<DocumentDto | null>(null);
  const [fields, setFields] = useState<Fields>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null);

  const apply = useCallback((d: DocumentDto) => {
    setDoc(d);
    if (d.payload?.fields) setFields(d.payload.fields);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        apply(await getDocument(id));
      } finally {
        setLoading(false);
      }
    })();
  }, [id, apply]);

  async function save(): Promise<boolean> {
    setBusy('save');
    try {
      apply(await patchDocument(id, fields));
      setToast({ text: 'Saved', ok: true });
      return true;
    } catch {
      setToast({ text: 'Save failed', ok: false });
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    setBusy('approve');
    try {
      if (!(await save())) return;
      apply(await approveDocument(id));
      setToast({ text: 'Approved', ok: true });
    } finally {
      setBusy(null);
    }
  }

  async function push() {
    setBusy('push');
    try {
      const result = await pushDocument(id);
      apply(result);
      setToast({ text: `Sent — ${result.deliveries.length} delivery(ies)`, ok: true });
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Push failed';
      setToast({ text: detail, ok: false });
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <div className="text-muted">Loading…</div>;
  if (!doc) return <div style={{ color: 'var(--danger)' }}>Document not found.</div>;

  const payload = doc.payload as ExtractionPayload | null;
  const editable = ['needs_review', 'approved', 'pushed'].includes(doc.status);
  const sections = payload ? buildSections(payload, fields) : [];

  return (
    <div style={{ maxWidth: 820 }}>
      <button className="btn-secondary" onClick={() => navigate('/queue')} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 20 }}>
        <ArrowLeft size={16} /> Back
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ textTransform: 'capitalize' }}>{doc.doc_type}</h1>
          <div className="text-muted" style={{ fontFamily: 'monospace' }}>{doc.id} · {confidencePercent(doc.overall_confidence)}</div>
        </div>
        <span className={`badge badge-${doc.status}`}>{doc.status.replace('_', ' ')}</span>
      </div>

      {doc.status === 'failed' && (
        <div className="glass-card" style={{ borderColor: 'rgba(239,68,68,0.4)' }}>
          <strong style={{ color: 'var(--danger)' }}>Extraction failed.</strong>
          <div className="text-muted" style={{ marginTop: 8 }}>{doc.error}</div>
        </div>
      )}

      {(payload?.meta?.warnings?.length ?? 0) > 0 && (
        <div className="glass-card" style={{ borderColor: 'rgba(245,158,11,0.4)', marginBottom: 16 }}>
          <span style={{ color: 'var(--warning)' }}>Some fields have low confidence — please verify the highlighted ones.</span>
        </div>
      )}

      {sections.map((section) => (
        <div key={section.title} className="glass-card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginBottom: 16 }}>{section.title}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
            {section.fields.map((spec) => {
              const leaf = getLeaf(fields, spec.path);
              const conf = leaf?.confidence ?? null;
              return (
                <div key={spec.path}>
                  <label className="text-muted" style={{ display: 'block', marginBottom: 6, fontSize: 13 }}>
                    {spec.label} {conf != null && <span style={{ opacity: 0.6 }}>· {confidencePercent(conf)}</span>}
                  </label>
                  <input
                    className="field-input"
                    style={{ borderLeftColor: accentFor(conf) }}
                    value={leaf?.value ?? ''}
                    disabled={!editable}
                    onChange={(e) => setFields((prev) => setLeafValue(prev, spec.path, e.target.value))}
                  />
                  {isLowConfidence(conf) && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 4 }}>Please check</div>}
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {toast && (
        <div style={{ margin: '12px 0', color: toast.ok ? 'var(--success)' : 'var(--danger)' }}>{toast.text}</div>
      )}

      <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
        {doc.status === 'needs_review' && (
          <>
            <button className="btn-secondary" onClick={save} disabled={busy != null} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Save size={16} /> Save
            </button>
            <button className="btn-secondary" onClick={approve} disabled={busy != null} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Check size={16} /> Approve
            </button>
          </>
        )}
        {(doc.status === 'needs_review' || doc.status === 'approved') && (
          <button className="btn-primary" onClick={push} disabled={busy != null} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Send size={16} /> {busy === 'push' ? 'Sending…' : 'Send to software'}
          </button>
        )}
        {doc.status === 'pushed' && <span style={{ color: 'var(--success)' }}>✓ Sent to your software</span>}
      </div>
    </div>
  );
}
