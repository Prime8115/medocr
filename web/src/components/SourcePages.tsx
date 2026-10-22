import { useEffect, useState } from 'react';
import { getSourcePage, type DocumentDto, type SourcePage } from '../api/documents';

export default function SourcePages({ documentId, selected, onSelect, manifest }: {
  documentId: string; selected?: number; onSelect: (page?: number) => void;
  manifest?: DocumentDto['source_manifest'];
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [view, setView] = useState<'processed' | 'original'>('processed');
  const [source, setSource] = useState<SourcePage | null>(null);
  const [error, setError] = useState('');

  useEffect(() => { setOpen(false); setPage(1); setView('processed'); }, [documentId]);
  useEffect(() => {
    let active = true;
    setSource(null);
    setError('');
    if (open) getSourcePage(documentId, page, view)
      .then((result) => { if (active) setSource(result); })
      .catch(() => { if (active) setError('Could not display this page.'); });
    return () => { active = false; };
  }, [documentId, page, open, view]);

  return <div className="glass-card" style={{ marginBottom: 16 }}>
    <button className="btn-secondary" type="button" onClick={() => setOpen(!open)}>
      {open ? 'Hide source pages' : 'View source pages'}
    </button>
    {selected && <p>Field rechecks use page {selected}. <button type="button" onClick={() => onSelect(undefined)}>Use automatic page selection</button></p>}
    {(manifest?.quality_warnings ?? []).map((warning, i) => <p key={i} role="note">{warning}</p>)}
    {!!manifest?.removed_pages?.length && <p role="note">
      Blank source pages {manifest.removed_pages.join(', ')} were left out of OCR. Review pages still map to their original positions.
    </p>}
    {open && <div style={{ marginTop: 12 }}>
      <p>Compare every address and item with the printed page before approval.</p>
      {source?.original_available && <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <button type="button" disabled={view === 'processed'} onClick={() => setView('processed')}>OCR copy</button>
        <button type="button" disabled={view === 'original'} onClick={() => setView('original')}>Original upload</button>
      </div>}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page}{source ? ` of ${source.pages}` : ''}</span>
        {source?.source_page && source.source_page !== page &&
          <span>Original page {source.source_page} of {source.source_pages}</span>}
        <button type="button" disabled={!source || page >= source.pages} onClick={() => setPage(page + 1)}>Next</button>
        <button type="button" disabled={!source} onClick={() => onSelect(page)}>Use page for recheck</button>
      </div>
      {error ? <p role="alert">{error}</p> : source ?
        <div style={{ overflow: 'auto', maxHeight: '70vh' }}>
          <img src={source.image} alt={`Document source page ${page}`}
            style={{ display: 'block', width: source.width, height: source.height, maxWidth: 'none' }} />
        </div> : <p>Loading page…</p>}
    </div>}
  </div>;
}
