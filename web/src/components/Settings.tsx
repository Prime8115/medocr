import { useEffect, useState } from 'react';
import { Link2, Plus, Trash2, Zap } from 'lucide-react';

import {
  createConnector,
  deleteConnector,
  listConnectors,
  testConnector,
  type Connector,
  type ConnectorType,
} from '../api/connectors';

const TYPE_LABEL: Record<ConnectorType, string> = {
  webhook: 'Webhook (HTTP push)',
  file_export: 'File export (CSV/JSON)',
  desktop_agent: 'Desktop agent (Windows)',
};

export default function Settings() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  // New connector form state
  const [type, setType] = useState<ConnectorType>('webhook');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [outputDir, setOutputDir] = useState('');
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setConnectors(await listConnectors());
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const config: Record<string, unknown> = {};
      if (type === 'webhook') config.url = url;
      if (type === 'file_export') config.output_dir = outputDir || undefined;
      await createConnector({ type, name, config, secret: type === 'webhook' ? secret || undefined : undefined });
      setShowForm(false);
      setName('');
      setUrl('');
      setSecret('');
      setOutputDir('');
      await load();
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    await deleteConnector(id);
    await load();
  }

  async function runTest(id: string) {
    setTestResult((p) => ({ ...p, [id]: 'Testing…' }));
    try {
      const r = await testConnector(id);
      setTestResult((p) => ({ ...p, [id]: `${r.ok ? '✓' : '✗'} ${r.status}${r.response_body ? ` — ${r.response_body}` : ''}` }));
    } catch {
      setTestResult((p) => ({ ...p, [id]: '✗ test failed' }));
    }
  }

  return (
    <div style={{ maxWidth: 820 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <div>
          <h1>Integration Settings</h1>
          <div className="text-muted">Where approved documents are sent — configure your connectors.</div>
        </div>
        <button className="btn-primary" onClick={() => setShowForm((s) => !s)} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Plus size={16} /> Add connector
        </button>
      </div>

      {showForm && (
        <form onSubmit={add} className="glass-card" style={{ marginBottom: 24 }}>
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 20 }}>
            <Link2 size={18} color="var(--primary-color)" /> New connector
          </h3>

          <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Type</label>
          <select className="input-field" style={{ marginBottom: 16 }} value={type} onChange={(e) => setType(e.target.value as ConnectorType)}>
            {(Object.keys(TYPE_LABEL) as ConnectorType[]).map((t) => (
              <option key={t} value={t}>{TYPE_LABEL[t]}</option>
            ))}
          </select>

          <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Name</label>
          <input className="input-field" style={{ marginBottom: 16 }} value={name} onChange={(e) => setName(e.target.value)} required />

          {type === 'webhook' && (
            <>
              <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Endpoint URL</label>
              <input className="input-field" style={{ marginBottom: 16 }} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" required />
              <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Signing secret (optional)</label>
              <input className="input-field" style={{ marginBottom: 16 }} value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="HMAC secret" />
            </>
          )}
          {type === 'file_export' && (
            <>
              <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Output folder (optional)</label>
              <input className="input-field" style={{ marginBottom: 16 }} value={outputDir} onChange={(e) => setOutputDir(e.target.value)} placeholder="C:\PharmacySoftware\import" />
            </>
          )}
          {type === 'desktop_agent' && (
            <div className="text-muted" style={{ marginBottom: 16 }}>
              After creating, a one-time pairing code will be shown to pair the Windows agent.
            </div>
          )}

          <button className="btn-primary" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Create'}</button>
        </form>
      )}

      {loading ? (
        <div className="text-muted">Loading…</div>
      ) : connectors.length === 0 ? (
        <div className="glass-card text-muted">No connectors yet. Add one to start sending data.</div>
      ) : (
        connectors.map((c) => (
          <div key={c.id} className="glass-card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <h3 style={{ marginBottom: 4 }}>{c.name}</h3>
                <div className="text-muted">{TYPE_LABEL[c.type]}{!c.enabled && ' · disabled'}</div>
                {c.type === 'webhook' && <div className="text-muted" style={{ marginTop: 4 }}>{String(c.config.url ?? '')}</div>}
                {c.type === 'desktop_agent' && c.config.pairing_code != null && (
                  <div style={{ marginTop: 8 }}>
                    Pairing code: <strong style={{ fontFamily: 'monospace', color: 'var(--primary-color)' }}>{String(c.config.pairing_code)}</strong>
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn-secondary" onClick={() => runTest(c.id)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px' }}>
                  <Zap size={14} /> Test
                </button>
                <button className="btn-danger" onClick={() => remove(c.id)} style={{ padding: '8px 12px' }}>
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
            {testResult[c.id] && (
              <div className="text-muted" style={{ marginTop: 12, fontFamily: 'monospace', fontSize: 13 }}>{testResult[c.id]}</div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
