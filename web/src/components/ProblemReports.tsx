import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import {
  getProblemReport, getProblemReportSource, listProblemReports, updateProblemReport,
  type ProblemReport, type ReportStatus,
} from '../api/problemReports';
import type { SourcePage } from '../api/documents';

const statuses: (ReportStatus | undefined)[] = [undefined, 'open', 'in_progress', 'closed'];
const label = (status?: ReportStatus) => status ? status.replace('_', ' ') : 'All';
const date = (value?: string | null) => value ? new Date(value).toLocaleString() : '—';

export default function ProblemReports() {
  const [status, setStatus] = useState<ReportStatus | undefined>();
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<ProblemReport[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<ProblemReport | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [view, setView] = useState<'processed' | 'original'>('processed');
  const [source, setSource] = useState<SourcePage | null>(null);
  const [sourceError, setSourceError] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const result = await listProblemReports(status, offset, search);
      setItems(result.items);
      setTotal(result.total);
    } catch {
      setError('Could not load customer reports.');
    }
  }, [status, offset, search]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!selected?.document?.source_available) return;
    let active = true;
    setSource(null);
    setSourceError('');
    getProblemReportSource(selected.id, page, view)
      .then((result) => { if (active) setSource(result); })
      .catch(() => { if (active) setSourceError('Source page unavailable.'); });
    return () => { active = false; };
  }, [selected?.id, selected?.document?.source_available, page, view]);

  async function select(id: string) {
    setError('');
    setSelected(null);
    setPage(1);
    setView('processed');
    try {
      const report = await getProblemReport(id);
      setSelected(report);
      setNote(report.resolution_note ?? '');
    } catch {
      setError('Could not open this report.');
    }
  }

  async function save(next: ReportStatus) {
    if (!selected) return;
    if (next === 'closed' && !note.trim()) {
      setError('Enter what was resolved before closing this report.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const updated = await updateProblemReport(selected.id, next, note.trim() || null);
      setSelected(updated);
      await load();
    } catch {
      setError('Could not save the report status.');
    } finally {
      setBusy(false);
    }
  }

  return <div>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 24 }}>
      <div><h1>Customer reports</h1><p className="text-muted">Review reported scans, record the fix, and close the case.</p></div>
      <button className="btn-secondary" onClick={() => void load()}><RefreshCw size={16} /> Refresh</button>
    </div>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
      {statuses.map((item) => <button key={item ?? 'all'} className={status === item ? 'btn-primary' : 'btn-secondary'}
        onClick={() => { setStatus(item); setOffset(0); setSelected(null); }}>{label(item)}</button>)}
    </div>
    <form onSubmit={(event) => { event.preventDefault(); setOffset(0); setSelected(null); setSearch(query); }}
      style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
      <input className="input-field" aria-label="Search reports" placeholder="Search customer email, shop, or document ID"
        value={query} onChange={(event) => setQuery(event.target.value)} />
      <button className="btn-secondary" type="submit">Search</button>
    </form>
    {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
    <div className="glass-card" style={{ overflowX: 'auto', marginBottom: 20 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
        <thead><tr><th>Reported</th><th>Customer</th><th>Document</th><th>Problem</th><th>Status</th></tr></thead>
        <tbody>{items.map((item) => <tr key={item.id} className="data-row" onClick={() => void select(item.id)}
          style={{ cursor: 'pointer', borderTop: '1px solid var(--border-glass)', background: selected?.id === item.id ? 'rgba(99,102,241,.15)' : undefined }}>
          <td style={{ padding: 12, whiteSpace: 'nowrap' }}>{date(item.reported_at)}</td>
          <td style={{ padding: 12 }}>{item.reporter_email ?? 'Unknown'}<br /><small className="text-muted">{item.shop_name}</small></td>
          <td style={{ padding: 12 }}>{item.document_id ?? 'Unavailable'}</td>
          <td style={{ padding: 12, maxWidth: 360, overflowWrap: 'anywhere' }}>{item.note || 'No note supplied'}</td>
          <td style={{ padding: 12, textTransform: 'capitalize' }}>{label(item.status)}</td>
        </tr>)}</tbody>
      </table>
      {!items.length && <p className="text-muted">No reports in this view.</p>}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16 }}>
        <span className="text-muted">{total} report{total === 1 ? '' : 's'} · Showing {offset + (items.length ? 1 : 0)}–{offset + items.length}</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-secondary" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 50)); setSelected(null); }}>Previous</button>
          <button className="btn-secondary" disabled={offset + items.length >= total} onClick={() => { setOffset(offset + 50); setSelected(null); }}>Next</button>
        </div>
      </div>
    </div>
    {selected && <div className="glass-card">
      <h2>Report {selected.id}</h2>
      <p><strong>Customer:</strong> {selected.reporter_email ?? 'Unknown'} · <strong>Shop:</strong> {selected.shop_name ?? 'Unknown'} · <strong>Reported:</strong> {date(selected.reported_at)}</p>
      <p><strong>Document:</strong> {selected.document_id ?? 'Unavailable'} · <strong>Current status:</strong> {selected.document?.status ?? 'Source record unavailable'}</p>
      <p><strong>Customer’s note:</strong> {selected.note || 'No note supplied'}</p>
      <details><summary>Scan snapshot when reported</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(selected.snapshot, null, 2)}</pre></details>
      {selected.document?.source_available && <div style={{ marginTop: 20 }}>
        <h3>Source pages</h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
          <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous page</button>
          <span>Page {page}{source ? ` of ${source.pages}` : ''}</span>
          <button className="btn-secondary" disabled={!source || page >= source.pages} onClick={() => setPage(page + 1)}>Next page</button>
          {source?.original_available && <button className="btn-secondary" onClick={() => setView(view === 'original' ? 'processed' : 'original')}>
            View {view === 'original' ? 'OCR copy' : 'original upload'}
          </button>}
        </div>
        {sourceError ? <p role="alert">{sourceError}</p> : source ? <div style={{ overflow: 'auto', maxHeight: '65vh' }}>
          <img src={source.image} alt={`Reported invoice page ${page}`} style={{ display: 'block', width: source.width, maxWidth: 'none' }} />
        </div> : <p>Loading page…</p>}
      </div>}
      {selected.document?.payload && <details style={{ marginTop: 20 }}><summary>Current extracted fields</summary>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '55vh', overflowY: 'auto' }}>{JSON.stringify(selected.document.payload.fields, null, 2)}</pre>
      </details>}
      <details style={{ marginTop: 20 }}><summary>Saved corrections ({selected.saved_corrections?.length ?? 0})</summary>
        <ul>{selected.saved_corrections?.map((edit, index) => <li key={index}>
          {date(edit.at)} · {edit.field}{edit.line != null ? ` row ${edit.line + 1}` : ''}: “{edit.read ?? 'blank'}” → “{edit.corrected ?? 'blank'}”
        </li>)}</ul>
      </details>
      <details style={{ marginTop: 12 }}><summary>Recheck and edit events ({selected.document_events?.length ?? 0})</summary>
        <ul>{selected.document_events?.map((event, index) => <li key={index}>{date(event.at)} · {event.action}
          {Object.keys(event.detail).length ? <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(event.detail, null, 2)}</pre> : null}
        </li>)}</ul>
      </details>
      <details style={{ marginTop: 12 }}><summary>Other scans of the same uploaded file ({selected.same_file_scans?.length ?? 0})</summary>
        <ul>{selected.same_file_scans?.map((scan) => <li key={scan.id}>{date(scan.created_at)} · {scan.id} · {scan.status}
          {scan.replayed_fields.length ? ` · Replayed: ${scan.replayed_fields.join(', ')}` : ''}
        </li>)}</ul>
      </details>
      <label style={{ display: 'block', marginTop: 24, marginBottom: 8 }} htmlFor="resolution-note">Resolution note</label>
      <textarea id="resolution-note" className="input-field" rows={4} value={note} onChange={(event) => setNote(event.target.value)}
        placeholder="What was checked or fixed? Include the test or release reference." />
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        {selected.status !== 'in_progress' && <button className="btn-secondary" disabled={busy} onClick={() => void save('in_progress')}>Start review</button>}
        {selected.status !== 'closed' && <button className="btn-primary" disabled={busy} onClick={() => void save('closed')}>Close with resolution</button>}
        {selected.status !== 'open' && <button className="btn-secondary" disabled={busy} onClick={() => void save('open')}>Reopen</button>}
        <button className="btn-secondary" disabled={busy} onClick={() => void save(selected.status)}>Save note</button>
      </div>
      {!!selected.history?.length && <details style={{ marginTop: 20 }}><summary>Case history</summary><ul>
        {selected.history.map((entry, index) => <li key={index}>{date(entry.at)}: {label(entry.from as ReportStatus)} → {label(entry.to as ReportStatus)}{entry.resolution_note ? ` · ${entry.resolution_note}` : ''}</li>)}
      </ul></details>}
    </div>}
  </div>;
}
