import { useEffect, useState } from 'react';
import { Image, Modal, ScrollView, Text, View } from 'react-native';
import { getSourcePage, type DocumentDto, type SourcePage } from '@/src/api/documents';
import { Button } from '@/src/theme/components';
import { colors, spacing } from '@/src/theme/tokens';

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

  const imageWidth = source ? Math.min(source.width, 1400) : 0;
  const imageHeight = source ? Math.round(imageWidth * source.height / source.width) : 0;
  const scanNoteCount = (manifest?.quality_warnings?.length ?? 0) + (manifest?.removed_pages?.length ? 1 : 0);
  return <>
    <Button title={`View source pages${scanNoteCount ? ` · ${scanNoteCount} scan note${scanNoteCount === 1 ? '' : 's'}` : ''}`}
      variant="secondary" onPress={() => setOpen(true)} />
    {selected && <Button title={`Rechecks use page ${selected} · clear selection`}
      variant="secondary" onPress={() => onSelect(undefined)} />}
    <Modal visible={open} onRequestClose={() => setOpen(false)} animationType="slide">
      <View style={{ flex: 1, padding: spacing.lg, paddingTop: 48, backgroundColor: colors.bg }}>
        <Button title="Close source" onPress={() => setOpen(false)} />
        <Text style={{ color: colors.text, marginVertical: 8 }}>
          Page {page}{source ? ` of ${source.pages}` : ''} · Check addresses and rows against the page.
        </Text>
        {(manifest?.quality_warnings ?? []).map((warning, i) =>
          <Text key={i} style={{ color: colors.text, marginVertical: 6 }}>{warning}</Text>)}
        {!!manifest?.removed_pages?.length && <Text style={{ color: colors.text, marginVertical: 6 }}>
          Blank source pages {manifest.removed_pages.join(', ')} were left out of OCR.
        </Text>}
        {source?.original_available && <View style={{ flexDirection: 'row', gap: 8 }}>
          <Button title="OCR copy" disabled={view === 'processed'} onPress={() => setView('processed')} />
          <Button title="Original upload" disabled={view === 'original'} onPress={() => setView('original')} />
        </View>}
        {source?.source_page && source.source_page !== page &&
          <Text style={{ color: colors.text }}>Original page {source.source_page} of {source.source_pages}</Text>}
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Button title="Previous" disabled={page <= 1} onPress={() => setPage(page - 1)} />
          <Button title="Next" disabled={!source || page >= source.pages} onPress={() => setPage(page + 1)} />
        </View>
        <Button title="Use this page for rechecks" disabled={!source}
          onPress={() => { onSelect(page); setOpen(false); }} />
        {error ? <Text style={{ color: colors.text }}>{error}</Text> : source ?
          <ScrollView style={{ flex: 1 }}><ScrollView horizontal>
            <Image source={{ uri: source.image }} style={{ width: imageWidth, height: imageHeight }}
              resizeMode="contain" accessibilityLabel={`Document source page ${page}`} />
          </ScrollView></ScrollView> : <Text style={{ color: colors.text }}>Loading page…</Text>}
      </View>
    </Modal>
  </>;
}
