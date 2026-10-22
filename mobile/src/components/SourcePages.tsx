import { useEffect, useState } from 'react';
import { Image, Modal, ScrollView, Text, View } from 'react-native';
import { getSourcePage, type SourcePage } from '@/src/api/documents';
import { Button } from '@/src/theme/components';
import { colors, spacing } from '@/src/theme/tokens';

export default function SourcePages({ documentId, selected, onSelect }: {
  documentId: string; selected?: number; onSelect: (page?: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [source, setSource] = useState<SourcePage | null>(null);
  const [error, setError] = useState('');

  useEffect(() => { setOpen(false); setPage(1); }, [documentId]);
  useEffect(() => {
    let active = true;
    setSource(null);
    setError('');
    if (open) getSourcePage(documentId, page)
      .then((result) => { if (active) setSource(result); })
      .catch(() => { if (active) setError('Could not display this page.'); });
    return () => { active = false; };
  }, [documentId, page, open]);

  const imageWidth = source ? Math.min(source.width, 1400) : 0;
  const imageHeight = source ? Math.round(imageWidth * source.height / source.width) : 0;
  return <>
    <Button title="View source pages" variant="secondary" onPress={() => setOpen(true)} />
    {selected && <Button title={`Rechecks use page ${selected} · clear selection`}
      variant="secondary" onPress={() => onSelect(undefined)} />}
    <Modal visible={open} onRequestClose={() => setOpen(false)} animationType="slide">
      <View style={{ flex: 1, padding: spacing.lg, paddingTop: 48, backgroundColor: colors.bg }}>
        <Button title="Close source" onPress={() => setOpen(false)} />
        <Text style={{ color: colors.text, marginVertical: 8 }}>
          Page {page}{source ? ` of ${source.pages}` : ''} · Check addresses and rows against the page.
        </Text>
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
