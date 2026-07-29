import { useEffect, useRef, useState } from 'react';
import { Boxes, Upload, RefreshCw, Trash2, Search } from 'lucide-react';

import {
  clearInventory,
  importCsv,
  inventoryCount,
  listInventory,
  syncInventory,
  type InventoryItem,
} from '../api/inventory';

export default function Inventory() {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [url, setUrl] = useState('');
  const [authHeader, setAuthHeader] = useState('');
  const [itemsPath, setItemsPath] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    setLoading(true);
    try {
      const [c, list] = await Promise.all([inventoryCount(), listInventory(query || undefined)]);
      setCount(c.count);
      setItems(list);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await importCsv(file);
      setMsg({ text: `Imported ${r.imported} items from CSV.`, ok: true });
      await load();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMsg({ text: typeof detail === 'string' ? detail : 'Import failed', ok: false });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  async function sync() {
    if (!url.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await syncInventory(url.trim(), authHeader.trim() || undefined, itemsPath.trim() || undefined);
      setMsg({ text: `Synced ${r.imported} items from API.`, ok: true });
      await load();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMsg({ text: typeof detail === 'string' ? detail : 'Sync failed', ok: false });
    } finally {
      setBusy(false);
    }
  }

  async function clearAll() {
    if (!confirm('Remove all inventory items?')) return;
    await clearInventory();
    await load();
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1>Inventory</h1>
          <div className="text-muted">
            Connect your stock so scanned medicines are matched and substitutes are suggested.
          </div>
        </div>
        <span className={`badge ${count > 0 ? 'badge-approved' : ''}`}>
          {count > 0 ? `${count} items connected` : 'Not connected'}
        </span>
      </div>

      {/* Populate: two ways */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16, marginBottom: 24 }}>
        <div className="glass-card">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <Upload size={18} color="var(--primary-color)" /> Upload CSV
          </h3>
          <div className="text-muted" style={{ marginBottom: 16 }}>
            Export stock from your software as CSV. Columns like Name/Item, Composition/Salt, MRP,
            Stock are auto-detected.
          </div>
          <input ref={fileRef} type="file" accept=".csv" onChange={onFile} style={{ display: 'none' }} />
          <button className="btn-primary" onClick={() => fileRef.current?.click()} disabled={busy}>
            Choose CSV file
          </button>
        </div>

        <div className="glass-card">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <RefreshCw size={18} color="var(--primary-color)" /> Sync from API
          </h3>
          <input className="input-field" style={{ marginBottom: 8 }} placeholder="https://your-software/api/items" value={url} onChange={(e) => setUrl(e.target.value)} />
          <input className="input-field" style={{ marginBottom: 8 }} placeholder="Auth header (optional, e.g. Bearer xyz)" value={authHeader} onChange={(e) => setAuthHeader(e.target.value)} />
          <input className="input-field" style={{ marginBottom: 12 }} placeholder="Items path (optional, e.g. data.items)" value={itemsPath} onChange={(e) => setItemsPath(e.target.value)} />
          <button className="btn-primary" onClick={sync} disabled={busy}>{busy ? 'Syncing…' : 'Sync now'}</button>
        </div>
      </div>

      {msg && <div style={{ marginBottom: 16, color: msg.ok ? 'var(--success)' : 'var(--danger)' }}>{msg.text}</div>}

      {/* Catalog */}
      <div className="glass-card" style={{ padding: 0 }}>
        <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-glass)', display: 'flex', alignItems: 'center', gap: 16 }}>
          <Search size={18} className="text-muted" />
          <input
            type="text"
            placeholder="Search catalog…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load()}
            style={{ background: 'transparent', border: 'none', color: 'white', outline: 'none', flex: 1, fontSize: 15 }}
          />
          {count > 0 && (
            <button className="btn-danger" onClick={clearAll} style={{ padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Trash2 size={14} /> Clear
            </button>
          )}
        </div>

        {loading ? (
          <div style={{ padding: 24 }} className="text-muted">Loading…</div>
        ) : items.length === 0 ? (
          <div style={{ padding: 24 }} className="text-muted">
            <Boxes size={18} style={{ verticalAlign: 'middle', marginRight: 8 }} />
            No inventory yet. Upload a CSV or sync from your software above.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-glass)', color: 'var(--text-secondary)' }}>
                <th style={{ padding: '12px 24px', fontWeight: 500 }}>Name</th>
                <th style={{ padding: '12px 24px', fontWeight: 500 }}>Composition</th>
                <th style={{ padding: '12px 24px', fontWeight: 500 }}>MRP</th>
                <th style={{ padding: '12px 24px', fontWeight: 500 }}>Stock</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} style={{ borderBottom: '1px solid var(--border-glass)' }}>
                  <td style={{ padding: '12px 24px' }}>{it.name}</td>
                  <td style={{ padding: '12px 24px' }} className="text-muted">{it.composition ?? '—'}</td>
                  <td style={{ padding: '12px 24px' }}>{it.mrp != null ? `₹${it.mrp}` : '—'}</td>
                  <td style={{ padding: '12px 24px' }}>{it.stock_qty ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
