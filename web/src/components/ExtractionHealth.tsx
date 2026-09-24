/**
 * Extraction health — the numbers we should have been watching.
 *
 * The invoice defects that reached users (a 143-item bill shown as 429, a blank
 * total, the wrong price column) were all visible in aggregate long before
 * anyone complained. This card puts them on the screen an operator already opens.
 *
 * The two that matter most:
 *   - **Adds up** — the share of invoices whose lines match their printed total.
 *     When this drops, line parsing is wrong somewhere.
 *   - **Exact parser** — the share handled by the free, deterministic PDF parser
 *     rather than the AI fallback. A silent Tier-1 regression shows up here and
 *     nowhere else, because the fallback still returns plausible data.
 */
import { useEffect, useState } from 'react';
import { Activity } from 'lucide-react';

import { getExtractionStats, type ExtractionStats } from '../api/documents';

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'good' | 'warn' | 'bad';
}) {
  const color =
    tone === 'good' ? 'var(--success)' : tone === 'warn' ? 'var(--warning)' : tone === 'bad' ? 'var(--danger)' : undefined;
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.4 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {hint && <div className="text-muted" style={{ fontSize: 12 }}>{hint}</div>}
    </div>
  );
}

/** Share thresholds are deliberately generous — this is a smoke alarm, not a SLO. */
function toneForPct(pct: number | null, good: number, bad: number): 'good' | 'warn' | 'bad' | undefined {
  if (pct == null) return undefined;
  if (pct >= good) return 'good';
  if (pct < bad) return 'bad';
  return 'warn';
}

export default function ExtractionHealth({ days = 30 }: { days?: number }) {
  const [stats, setStats] = useState<ExtractionStats | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getExtractionStats(days).then(setStats).catch(() => setFailed(true));
  }, [days]);

  // Never block the queue on a telemetry call.
  if (failed || !stats || stats.documents === 0) return null;

  const inv = stats.invoices;
  const hasInvoices = inv.total > 0;

  return (
    <div className="glass-card" style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <Activity size={16} />
        <h3 style={{ margin: 0 }}>Extraction health</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>last {stats.window_days} days</span>
      </div>

      {stats.warnings.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          {stats.warnings.map((w) => (
            <div key={w} style={{ color: 'var(--warning)', fontSize: 13, marginBottom: 4 }}>⚠ {w}</div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 20 }}>
        <Stat label="Documents" value={String(stats.documents)} hint={`${inv.total} invoices`} />
        {hasInvoices && (
          <Stat
            label="Adds up"
            value={inv.reconciled_pct != null ? `${inv.reconciled_pct}%` : '—'}
            hint={`${inv.total_mismatch} mismatched · ${inv.total_unreadable} no total`}
            tone={toneForPct(inv.reconciled_pct, 90, 70)}
          />
        )}
        {hasInvoices && (
          <Stat
            label="Exact parser"
            value={inv.tier1_parser_pct != null ? `${inv.tier1_parser_pct}%` : '—'}
            hint={`${inv.tier1_parser}/${inv.total} skipped the AI`}
            tone={toneForPct(inv.tier1_parser_pct, 70, 40)}
          />
        )}
        {hasInvoices && (inv.multi_copy_pdfs > 0 || inv.duplicate_rows_removed > 0) && (
          <Stat
            label="Repeats removed"
            value={String(inv.duplicate_rows_removed)}
            hint={`${inv.multi_copy_pdfs} multi-copy PDF(s)`}
          />
        )}
        {stats.reported_by_users > 0 && (
          <Stat label="Reported" value={String(stats.reported_by_users)} hint="flagged by users" tone="bad" />
        )}
        {stats.pages_failed_documents > 0 && (
          <Stat
            label="Unread pages"
            value={String(stats.pages_failed_documents)}
            hint="documents with a page we could not read"
            tone="warn"
          />
        )}
      </div>
    </div>
  );
}
