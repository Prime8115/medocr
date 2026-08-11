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
import { matchDocument, type DocMatch, type MatchItem } from '../api/inventory';

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
  const [inv, setInv] = useState<DocMatch | null>(null);
  const [editItem, setEditItem] = useState<number | null>(null);
  const [itemSearch, setItemSearch] = useState('');

  const apply = useCallback((d: DocumentDto) => {
    setDoc(d);
    if (d.payload?.fields) setFields(d.payload.fields);
  }, []);

  useEffect(() => {
    if (doc && ['needs_review', 'approved', 'pushed'].includes(doc.status)) {
      matchDocument(id).then(setInv).catch(() => setInv(null));
    }
  }, [doc, id]);

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
  const singleSections = sections.filter((s) => !/#\d+$/.test(s.title));
  const itemSections = sections.filter((s) => /#\d+$/.test(s.title));
  const meta = payload?.meta as { pages?: number } | undefined;
  const matchFor = (i: number): MatchItem | undefined => (inv?.connected ? inv.items[i] : undefined);

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

      {/* Header sections (patient/prescriber or supplier/invoice) — editable inline */}
      {singleSections.map((section) => (
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

      {/* Line items / medications — compact table, click a row to edit */}
      {itemSections.length > 0 && (
        <div className="glass-card" style={{ padding: 0, marginBottom: 16 }}>
          <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border-glass)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
            <h3 style={{ margin: 0 }}>{doc.doc_type === 'invoice' ? 'Line items' : 'Medications'}</h3>
            {itemSections.length > 8 && (
              <input
                className="field-input"
                style={{ maxWidth: 260, borderLeftWidth: 1 }}
                placeholder={`Search ${itemSections.length} items…`}
                value={itemSearch}
                onChange={(e) => setItemSearch(e.target.value)}
              />
            )}
            <span className="text-muted" style={{ whiteSpace: 'nowrap' }}>
              {itemSections.length} items{meta?.pages ? ` · ${meta.pages}p` : ''}
              {inv?.connected ? ` · ${inv.matched}/${inv.total} matched` : ''}
            </span>
          </div>
          <div style={{ maxHeight: 520, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: 14 }}>
              <thead>
                <tr style={{ color: 'var(--text-secondary)', position: 'sticky', top: 0, background: '#131722' }}>
                  <th style={{ padding: '10px 20px' }}>{itemSections[0].fields[0].label}</th>
                  {itemSections[0].fields.slice(1, 4).map((f) => (
                    <th key={f.path} style={{ padding: '10px 12px' }}>{f.label}</th>
                  ))}
                  {inv?.connected && <th style={{ padding: '10px 12px' }}>Match</th>}
                  <th style={{ padding: '10px 12px' }} />
                </tr>
              </thead>
              <tbody>
                {itemSections.map((section, i) => {
                  const primary = getLeaf(fields, section.fields[0].path)?.value || '—';
                  if (itemSearch && !String(primary).toLowerCase().includes(itemSearch.toLowerCase())) return null;
                  const mi = matchFor(i);
                  return (
                    <tr key={section.title} className="data-row" onClick={() => setEditItem(i)} style={{ borderTop: '1px solid var(--border-glass)', cursor: 'pointer' }}>
                      <td style={{ padding: '10px 20px' }}>{primary}</td>
                      {section.fields.slice(1, 4).map((f) => (
                        <td key={f.path} style={{ padding: '10px 12px' }} className="text-muted">
                          {getLeaf(fields, f.path)?.value ?? '—'}
                        </td>
                      ))}
                      {inv?.connected && (
                        <td style={{ padding: '10px 12px' }}>
                          {mi && mi.candidates.length > 0 ? (
                            <span className={`badge ${mi.best_score >= 85 ? 'badge-approved' : 'badge-processing'}`}>{Math.round(mi.best_score)}%</span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                      )}
                      <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>edit ›</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Edit-a-single-item modal */}
      {editItem !== null && itemSections[editItem] && (
        <div onClick={() => setEditItem(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}>
          <div onClick={(e) => e.stopPropagation()} className="glass-panel" style={{ width: 560, maxWidth: '90vw', maxHeight: '85vh', overflowY: 'auto', padding: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ margin: 0 }}>{itemSections[editItem].title}</h3>
              <span style={{ cursor: 'pointer', color: 'var(--primary-color)' }} onClick={() => setEditItem(null)}>Done</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14 }}>
              {itemSections[editItem].fields.map((spec) => {
                const leaf = getLeaf(fields, spec.path);
                const conf = leaf?.confidence ?? null;
                return (
                  <div key={spec.path}>
                    <label className="text-muted" style={{ display: 'block', marginBottom: 6, fontSize: 13 }}>{spec.label}</label>
                    <input className="field-input" style={{ borderLeftColor: accentFor(conf) }} value={leaf?.value ?? ''} disabled={!editable}
                      onChange={(e) => setFields((prev) => setLeafValue(prev, spec.path, e.target.value))} />
                  </div>
                );
              })}
            </div>
            {matchFor(editItem)?.candidates?.length ? (
              <div style={{ marginTop: 16, padding: 12, background: 'rgba(255,255,255,0.03)', borderRadius: 8 }}>
                <div className="text-muted" style={{ fontSize: 12, textTransform: 'uppercase', marginBottom: 8 }}>In your inventory</div>
                {matchFor(editItem)!.candidates.slice(0, 3).map((c) => (
                  <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 14 }}>
                    <span>{c.name}{c.composition ? <span className="text-muted"> · {c.composition}</span> : null}</span>
                    <span className="text-muted">{c.stock_qty != null ? `stock ${c.stock_qty}` : ''} · {Math.round(c.score)}%</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      )}

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
