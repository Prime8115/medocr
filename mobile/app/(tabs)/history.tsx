import { useFocusEffect, router } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';

import { DocumentDto, listDocuments } from '@/src/api/documents';
import { useQueue } from '@/src/queue/QueueContext';
import { Badge, CenterState, Screen } from '@/src/theme/components';
import { colors, font, radius, spacing } from '@/src/theme/tokens';
import { confidencePercent } from '@/src/lib/confidence';
import { t } from '@/src/i18n/strings';

const STATUS_TONE: Record<string, 'neutral' | 'success' | 'warning' | 'danger' | 'info'> = {
  needs_review: 'info',
  approved: 'success',
  pushed: 'success',
  failed: 'danger',
  processing: 'warning',
  queued: 'warning',
};

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    needs_review: t('needsReview'),
    approved: t('approved'),
    pushed: t('pushed'),
    failed: t('failed'),
    processing: t('processing'),
    queued: t('queued'),
  };
  return map[status] ?? status;
}

export default function HistoryScreen() {
  const [docs, setDocs] = useState<DocumentDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<string | undefined>(undefined);
  const { items: queueItems, retryAll, pending } = useQueue();

  const load = useCallback(async () => {
    try {
      setDocs(await listDocuments(filter ? { status: filter } : undefined));
    } catch {
      /* offline: still show queue below */
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  const q = query.trim().toLowerCase();
  const filtered = docs.filter((d) => !q || d.id.toLowerCase().includes(q) || d.doc_type.includes(q));

  const uploading = queueItems.filter((i) => i.status !== 'done');

  return (
    <Screen>
      <View style={styles.searchBar}>
        <TextInput
          style={styles.searchInput}
          placeholder={t('search')}
          placeholderTextColor={colors.textMuted}
          value={query}
          onChangeText={setQuery}
        />
      </View>

      <View style={styles.filters}>
        {[undefined, 'needs_review', 'approved', 'pushed', 'failed'].map((f) => (
          <TouchableOpacity
            key={f ?? 'all'}
            onPress={() => setFilter(f)}
            style={[styles.filterChip, filter === f && styles.filterChipActive]}
          >
            <Text style={[styles.filterText, filter === f && styles.filterTextActive]}>
              {f ? statusLabel(f) : t('all')}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {pending > 0 && (
        <TouchableOpacity onPress={retryAll} style={styles.queueBanner}>
          <Text style={styles.queueText}>
            {pending} {t('queued')}
          </Text>
          <Text style={styles.queueRetry}>{t('retry')}</Text>
        </TouchableOpacity>
      )}

      <FlatList
        data={filtered}
        keyExtractor={(d) => d.id}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ListHeaderComponent={
          uploading.length ? (
            <View>
              {uploading.map((i) => (
                <View key={i.id} style={styles.card}>
                  <Text style={styles.cardId}>{i.fileName}</Text>
                  <Badge label={i.status === 'error' ? t('failed') : t('syncing')} tone={i.status === 'error' ? 'danger' : 'warning'} />
                </View>
              ))}
            </View>
          ) : null
        }
        ListEmptyComponent={
          loading ? null : <CenterState title={t('empty')} subtitle="Scanned documents will appear here." />
        }
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.card} onPress={() => router.push(`/review/${item.id}`)}>
            <View style={styles.cardMain}>
              <Text style={styles.cardId}>{item.id}</Text>
              <Text style={styles.cardMeta}>
                {item.doc_type} · {confidencePercent(item.overall_confidence)}
              </Text>
            </View>
            <Badge label={statusLabel(item.status)} tone={STATUS_TONE[item.status] ?? 'neutral'} />
          </TouchableOpacity>
        )}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  searchBar: { padding: spacing.md, backgroundColor: colors.surface },
  searchInput: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    minHeight: 44,
    ...font.body,
    color: colors.text,
  },
  filters: { flexDirection: 'row', gap: spacing.sm, paddingHorizontal: spacing.md, paddingBottom: spacing.sm, backgroundColor: colors.surface, flexWrap: 'wrap' },
  filterChip: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderRadius: radius.pill, backgroundColor: colors.surfaceAlt },
  filterChipActive: { backgroundColor: colors.primaryTint },
  filterText: { ...font.caption, color: colors.textSecondary },
  filterTextActive: { color: colors.primaryDark, fontWeight: '600' },
  queueBanner: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: colors.warningTint, padding: spacing.md },
  queueText: { ...font.body, color: colors.warning },
  queueRetry: { ...font.body, color: colors.warning, fontWeight: '700' },
  list: { padding: spacing.md, gap: spacing.sm },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.lg,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardMain: { flex: 1 },
  cardId: { ...font.h3, color: colors.text },
  cardMeta: { ...font.caption, color: colors.textSecondary, marginTop: 2, textTransform: 'capitalize' },
});
