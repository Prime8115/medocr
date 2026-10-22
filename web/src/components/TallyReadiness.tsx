import { useEffect, useState } from 'react';
import { listConnectors, updateConnector, type Connector } from '../api/connectors';
import { downloadTallyPurchase, getTallyPreflight, type DocumentDto, type TallyPreflight } from '../api/documents';

type Config = Record<string, unknown>;
function value(field: unknown): string {
  if (field && typeof field === 'object' && 'value' in field) return String((field as { value?: unknown }).value ?? '');
  return field == null ? '' : String(field);
}
function facts(payload: DocumentDto['payload']) {
  const fields = (payload?.fields ?? {}) as Record<string, unknown>;
  const supplier = value(((fields.supplier ?? {}) as Record<string, unknown>).name);
  const items = (Array.isArray(fields.line_items) ? fields.line_items : []).map((raw: unknown) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return { name: value(item.description), batch: value(item.batch_no), expiry: value(item.expiry) };
  });
  return { supplier, items };
}

export default function TallyReadiness({ documentId, status, payload }: {
  documentId: string; status: string; payload: DocumentDto['payload'];
}) {
  const [connector, setConnector] = useState<Connector | null>(null);
  const [check, setCheck] = useState<TallyPreflight | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Config>({});
  const [stock, setStock] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const { supplier, items } = facts(payload);

  useEffect(() => {
    let active = true;
    listConnectors().then((rows) => {
      if (active) setConnector(rows.find((row) => row.enabled && row.type === 'file_export' &&
        row.config.format === 'tally_prime_xml') ?? null);
    }).catch(() => { if (active) setMessage('Could not check Tally settings.'); });
    return () => { active = false; };
  }, [documentId]);
  useEffect(() => {
    let active = true;
    setCheck(null);
    if (connector) getTallyPreflight(documentId, connector.id)
      .then((result) => { if (active) setCheck(result); })
      .catch(() => { if (active) setMessage('Could not check Tally purchase readiness.'); });
    return () => { active = false; };
  }, [documentId, connector, payload]);

  function startEditing() {
    if (!connector) return;
    const tally = (connector.config.tally ?? {}) as Config;
    const names = (tally.stock_items ?? {}) as Record<string, string>;
    const expiries = (tally.batch_expiries ?? {}) as Record<string, string>;
    setDraft({ ...tally, party_ledger: ((tally.party_ledgers ?? {}) as Record<string, string>)[supplier] ?? '' });
    setStock({ ...names });
    setConfirmed(Object.fromEntries(items.map((item) =>
      [`${item.name}|${item.batch}`, !!names[item.name] && expiries[`${names[item.name]}|${item.batch}`] === item.expiry])));
    setEditing(true);
  }
  async function save() {
    if (!connector) return;
    setBusy(true); setMessage('');
    try {
      const old = (connector.config.tally ?? {}) as Config;
      const party = { ...((old.party_ledgers ?? {}) as Record<string, string>) };
      if (supplier && String(draft.party_ledger ?? '').trim()) party[supplier] = String(draft.party_ledger).trim();
      const names = { ...((old.stock_items ?? {}) as Record<string, string>) };
      const expiries = { ...((old.batch_expiries ?? {}) as Record<string, string>) };
      for (const item of items) {
        const mapped = (stock[item.name] ?? '').trim();
        if (!mapped) continue;
        names[item.name] = mapped;
        if (item.batch) {
          if (confirmed[`${item.name}|${item.batch}`] && item.expiry)
            expiries[`${mapped}|${item.batch}`] = item.expiry;
          else delete expiries[`${mapped}|${item.batch}`];
        }
      }
      const next = { ...old, ...draft };
      delete next.party_ledger;
      const updated = await updateConnector(connector.id, { config: { ...connector.config,
        tally: { ...next, party_ledgers: party, stock_items: names, batch_expiries: expiries } } });
      setConnector(updated); setEditing(false);
    } catch { setMessage('Could not save Tally mappings. Ask the shop owner to update the connector.'); }
    finally { setBusy(false); }
  }
  function input(key: string, label: string) {
    return <label style={{ display: 'block', marginBottom: 10 }}>{label}
      <input className="input-field" value={String(draft[key] ?? '')}
        onChange={(event) => setDraft((old) => ({ ...old, [key]: event.target.value }))} />
    </label>;
  }

  if (!connector) return <div className="glass-card" style={{ marginBottom: 16 }}>
    <strong>Tally purchase import</strong>
    <p className="text-muted">Add a Tally purchase XML connector in Settings to check this invoice for import.</p>
    {message && <p role="alert">{message}</p>}
  </div>;
  return <div className="glass-card" style={{ marginBottom: 16 }}>
    <strong>Tally purchase import</strong>
    <p className="text-muted">Check stock, batch, expiry and ledger names against Tally before importing this pilot voucher.</p>
    {check ? check.ready ? <p>✓ Purchase data and configured mappings are ready.</p> :
      <ul style={{ paddingLeft: 18 }}>{check.issues.map((issue, i) =>
        <li key={`${issue.path}-${i}`}>{issue.message}</li>)}</ul> : <p className="text-muted">Checking…</p>}
    <button type="button" className="btn-secondary" onClick={() => editing ? setEditing(false) : startEditing()}>
      {editing ? 'Close mapping' : 'Set up Tally mapping'}
    </button>
    {editing && <div style={{ marginTop: 16 }}>
      {input('company', 'Tally company name')}
      {input('party_ledger', `Supplier ledger for ${supplier || 'this supplier'}`)}
      {input('purchase_ledger', 'Purchase ledger name')}
      {input('unit', 'Stock unit in Tally (for example, Nos)')}
      {input('cgst_ledger', 'Input CGST ledger, if used')}
      {input('sgst_ledger', 'Input SGST ledger, if used')}
      {input('igst_ledger', 'Input IGST ledger, if used')}
      {input('utgst_ledger', 'Input UTGST ledger, if used')}
      {input('godown', 'Godown name (optional)')}
      {items.map((item, i) => <div key={`${item.name}-${i}`} style={{ marginBottom: 14 }}>
        <label style={{ display: 'block' }}>Tally stock item for {item.name || `line ${i + 1}`}
          <input className="input-field" value={stock[item.name] ?? ''}
            onChange={(event) => setStock((old) => ({ ...old, [item.name]: event.target.value }))} />
        </label>
        <label style={{ display: 'block', marginTop: 6 }}>
          <input type="checkbox" checked={!!confirmed[`${item.name}|${item.batch}`]}
            onChange={(event) => setConfirmed((old) => ({ ...old,
              [`${item.name}|${item.batch}`]: event.target.checked }))} />{' '}
          I verified batch {item.batch || '(missing)'} and expiry {item.expiry || '(missing)'} in Tally
        </label>
      </div>)}
      <button className="btn-primary" type="button" disabled={busy} onClick={save}>Save Tally mapping</button>
    </div>}
    {check?.ready && <button type="button" className="btn-secondary" style={{ marginLeft: 8 }}
      disabled={busy || (status !== 'approved' && status !== 'pushed')}
      onClick={async () => {
        setBusy(true); setMessage('');
        try { await downloadTallyPurchase(documentId, connector.id); }
        catch { setMessage('Tally export could not be downloaded. Refresh and check the invoice.'); }
        finally { setBusy(false); }
      }}>
      {status === 'approved' || status === 'pushed' ? 'Download Tally purchase XML' : 'Approve invoice to download'}
    </button>}
    {message && <p role="alert">{message}</p>}
  </div>;
}
