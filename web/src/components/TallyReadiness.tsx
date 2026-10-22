import { useEffect, useState } from 'react';
import { listConnectors, updateConnector, type Connector } from '../api/connectors';
import { useAuth } from '../auth/AuthContext';
import { downloadTallyPurchase, getTallyExportStatus, getTallyPreflight, reportTallyImport,
  type DocumentDto, type TallyExportStatus, type TallyPreflight } from '../api/documents';

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
  const { user } = useAuth();
  const isOwner = user?.role === 'owner';
  const [connector, setConnector] = useState<Connector | null>(null);
  const [check, setCheck] = useState<TallyPreflight | null>(null);
  const [exportStatus, setExportStatus] = useState<TallyExportStatus | null>(null);
  const [exportLoaded, setExportLoaded] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Config>({});
  const [stock, setStock] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [voucherReference, setVoucherReference] = useState('');
  const [importedRows, setImportedRows] = useState('');
  const [importedTotal, setImportedTotal] = useState('');
  const [rowsChecked, setRowsChecked] = useState(false);
  const [notImportedReason, setNotImportedReason] = useState('');
  const { supplier, items } = facts(payload);

  useEffect(() => {
    let active = true;
    listConnectors().then((rows) => {
      if (active) setConnector(rows.find((row) => row.enabled && row.type === 'file_export' &&
        row.config.format === 'tally_prime_xml') ?? rows.find((row) => row.type === 'file_export' &&
        row.config.format === 'tally_prime_xml') ?? null);
    }).catch(() => { if (active) setMessage('Could not check Tally settings.'); });
    return () => { active = false; };
  }, [documentId]);
  useEffect(() => {
    let active = true;
    setCheck(null);
    if (connector?.enabled) getTallyPreflight(documentId, connector.id)
      .then((result) => { if (active) setCheck(result); })
      .catch(() => { if (active) setMessage('Could not check Tally purchase readiness.'); });
    return () => { active = false; };
  }, [documentId, connector, payload]);
  useEffect(() => {
    let active = true;
    setExportLoaded(false);
    setExportStatus(null);
    if (connector) getTallyExportStatus(documentId, connector.id)
      .then((result) => { if (active) { setExportStatus(result); setExportLoaded(true); } })
      .catch(() => { if (active) setMessage('Could not check whether this invoice was already exported to Tally.'); });
    return () => { active = false; };
  }, [documentId, connector]);

  async function report(outcome: 'imported' | 'not_imported') {
    if (!connector) return;
    setBusy(true); setMessage('');
    try {
      const result = await reportTallyImport(documentId, connector.id, outcome === 'imported' ? {
        outcome, external_reference: voucherReference.trim(),
        imported_line_count: Number(importedRows), imported_total: importedTotal.trim(),
        rows_and_batches_checked: rowsChecked,
      } : { outcome, reason: notImportedReason.trim() });
      setExportStatus(result);
      setMessage(outcome === 'imported' ? 'Tally import reported. Keep the voucher reference for reconciliation.'
        : 'Recorded as not imported. You can download a new XML after the invoice is approved.');
    } catch (error: unknown) {
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setMessage(typeof detail === 'string' ? detail : 'Could not record the Tally outcome. Check the details and try again.');
    } finally { setBusy(false); }
  }

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
    {!connector.enabled && <p role="status">This Tally connector is disabled. Its export history remains available; enable it in Settings to prepare another file.</p>}
    {connector.enabled && (check ? check.ready ? <p>✓ Purchase data and configured mappings are ready.</p> :
      <ul style={{ paddingLeft: 18 }}>{check.issues.map((issue, i) =>
        <li key={`${issue.path}-${i}`}>{issue.message}</li>)}</ul> : <p className="text-muted">Checking…</p>)}
    {isOwner && <button type="button" className="btn-secondary" onClick={() => editing ? setEditing(false) : startEditing()}>
      {editing ? 'Close mapping' : 'Set up Tally mapping'}
    </button>}
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
    {exportStatus?.status === 'downloaded' && <div style={{ marginTop: 16 }}>
      <p role="status">XML revision {exportStatus.revision} was prepared for download. This does not mean the browser saved it or Tally imported it. Check the Purchase Register and Import Exceptions before continuing; importing the same file twice can duplicate or alter a voucher. If the file was not saved, record “not imported” with that reason to try again.</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
        <label>Tally voucher reference
          <input className="input-field" value={voucherReference} onChange={(event) => setVoucherReference(event.target.value)} />
        </label>
        <label>Stock lines in Tally (expected {exportStatus.line_count})
          <input className="input-field" type="number" min="0" value={importedRows}
            onChange={(event) => setImportedRows(event.target.value)} />
        </label>
        <label>Voucher total in Tally (expected ₹{exportStatus.total_amount})
          <input className="input-field" inputMode="decimal" value={importedTotal}
            onChange={(event) => setImportedTotal(event.target.value)} />
        </label>
      </div>
      <label style={{ display: 'block', marginTop: 12 }}>
        <input type="checkbox" checked={rowsChecked} onChange={(event) => setRowsChecked(event.target.checked)} />{' '}
        I compared every stock line, billed/free quantity, batch and expiry with the approved invoice.
      </label>
      {isOwner && <button type="button" className="btn-primary" disabled={busy || !voucherReference.trim() || !importedRows || !importedTotal || !rowsChecked}
        onClick={() => report('imported')} style={{ marginTop: 10 }}>Record reported import</button>
      }
      {isOwner && <div style={{ marginTop: 16 }}>
        <label>Not imported? Record the Tally error or why the file was not used.
          <input className="input-field" value={notImportedReason}
            onChange={(event) => setNotImportedReason(event.target.value)} />
        </label>
        <button type="button" className="btn-secondary" disabled={busy || !notImportedReason.trim()}
          onClick={() => report('not_imported')} style={{ marginTop: 8 }}>Record not imported</button>
      </div>}
      {!isOwner && <p className="text-muted">Ask the shop owner to record the Tally import outcome.</p>}
    </div>}
    {exportStatus?.status === 'reported_imported' && <p role="status">
      Reported imported into Tally as {exportStatus.external_reference}; {exportStatus.line_count} lines, ₹{exportStatus.total_amount}.
      This is an owner report, not an automatic Tally acknowledgement.
    </p>}
    {exportStatus?.status === 'not_imported' && <p role="status">
      Previous XML was not imported: {exportStatus.report_reason}. Review any corrections before downloading a new revision.
    </p>}
    {check?.ready && exportLoaded && (!exportStatus || exportStatus.status === 'not_imported') && <button type="button" className="btn-secondary" style={{ marginLeft: 8 }}
      disabled={busy || (status !== 'approved' && status !== 'pushed')}
      onClick={async () => {
        setBusy(true); setMessage('');
        try {
          await downloadTallyPurchase(documentId, connector.id, (exportStatus?.revision ?? 0) + 1);
          setExportStatus(await getTallyExportStatus(documentId, connector.id));
        }
        catch { setMessage('The file may have been prepared. Refresh and check its export status before trying again.'); }
        finally { setBusy(false); }
      }}>
      {status === 'approved' || status === 'pushed' ? 'Download Tally purchase XML' : 'Approve invoice to download'}
    </button>}
    {message && <p role="alert">{message}</p>}
  </div>;
}
