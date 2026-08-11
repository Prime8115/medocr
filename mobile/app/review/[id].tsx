import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import {
  DocumentDto,
  approveDocument,
  getDocument,
  patchDocument,
  pushDocument,
  retryDocument,
} from '@/src/api/documents';
import { Badge, Button, Card, CenterState, Field, Screen, SectionTitle } from '@/src/theme/components';
import { colors, font, radius, spacing } from '@/src/theme/tokens';
import { buildSections, getLeaf, setLeafValue, ExtractionPayload, Fields, Section } from '@/src/lib/payload';
import { confidenceColor, confidencePercent, isLowConfidence } from '@/src/lib/confidence';
import { matchDocument, DocMatch, MatchItem } from '@/src/api/inventory';
import { t } from '@/src/i18n/strings';

const POLL_MS = 2000;
const MAX_AUTO_RETRIES = 3;

export default function ReviewScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [doc, setDoc] = useState<DocumentDto | null>(null);
  const [fields, setFields] = useState<Fields>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [inv, setInv] = useState<DocMatch | null>(null);
  const [autoRetry, setAutoRetry] = useState(0);
  const [manualRetrying, setManualRetrying] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null); // item section being edited
  const [search, setSearch] = useState('');
  const [attentionOnly, setAttentionOnly] = useState(false);

  const applyDoc = useCallback((d: DocumentDto) => {
    setDoc(d);
    if (d.payload?.fields) setFields(d.payload.fields);
  }, []);

  useEffect(() => {
    if (doc && ['needs_review', 'approved', 'pushed'].includes(doc.status)) {
      matchDocument(id).then(setInv).catch(() => setInv(null));
    }
  }, [doc, id]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let retries = 0;
    async function poll() {
      try {
        const d = await getDocument(id);
        if (!active) return;
        if (d.status === 'queued' || d.status === 'processing') {
          timer = setTimeout(poll, POLL_MS);
        } else if (d.status === 'failed' && retries < MAX_AUTO_RETRIES) {
          retries += 1;
          setAutoRetry(retries);
          try {
            await retryDocument(id);
          } catch {
            /* ignore */
          }
          timer = setTimeout(poll, POLL_MS);
        } else {
          setAutoRetry(0);
          applyDoc(d);
          setLoading(false);
        }
      } catch {
        if (active) setLoading(false);
      }
    }
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, applyDoc]);

  const onChangeField = (path: string, value: string) => setFields((prev) => setLeafValue(prev, path, value));

  async function save(): Promise<boolean> {
    setSaving(true);
    try {
      applyDoc(await patchDocument(id, fields));
      return true;
    } catch {
      Alert.alert(t('errorGeneric'));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function approveOnly() {
    setBusy('approve');
    try {
      if (!(await save())) return;
      applyDoc(await approveDocument(id));
    } catch {
      Alert.alert(t('errorGeneric'));
    } finally {
      setBusy(null);
    }
  }

  async function approveAndSend() {
    setBusy('push');
    try {
      if (!(await save())) return;
      await approveDocument(id);
      const result = await pushDocument(id);
      applyDoc(result);
      Alert.alert(t('pushed'), `${result.deliveries.length} delivery(ies)`);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? t('errorGeneric');
      Alert.alert(t('failed'), detail);
    } finally {
      setBusy(null);
    }
  }

  async function tryAgain() {
    setManualRetrying(true);
    setLoading(true);
    try {
      await retryDocument(id);
    } catch {
      /* ignore */
    } finally {
      setManualRetrying(false);
    }
    const pollOnce = async () => {
      const d = await getDocument(id);
      if (d.status === 'queued' || d.status === 'processing') setTimeout(pollOnce, POLL_MS);
      else {
        applyDoc(d);
        setLoading(false);
      }
    };
    pollOnce();
  }

  const payload = doc?.payload as ExtractionPayload | undefined;
  const sections = useMemo<Section[]>(
    () => (payload ? buildSections({ ...payload, fields }) : []),
    [payload, fields],
  );
  // Split into single (header) sections and repeating item sections (title has "#N").
  const singleSections = sections.filter((s) => !/#\d+$/.test(s.title));
  const itemSections = sections.filter((s) => /#\d+$/.test(s.title));

  const editable = !!doc && ['needs_review', 'approved', 'pushed'].includes(doc.status);

  // ---- states ----
  if (loading) {
    return (
      <Screen>
        <CenterState
          title={autoRetry > 0 ? 'AI busy — retrying…' : t('processing')}
          subtitle={
            autoRetry > 0
              ? `Attempt ${autoRetry} of ${MAX_AUTO_RETRIES}`
              : doc?.progress
                ? `Reading page ${doc.progress}`
                : 'Reading the document…'
          }
        />
      </Screen>
    );
  }
  if (!doc) {
    return (
      <Screen>
        <CenterState title={t('errorGeneric')} subtitle="Could not load this document." />
      </Screen>
    );
  }
  if (doc.status === 'failed') {
    return (
      <Screen>
        <CenterState title={t('failed')} subtitle={doc.error ?? 'Extraction failed. The AI may be busy — please try again.'}>
          <Button title={manualRetrying ? 'Retrying…' : 'Try again'} onPress={tryAgain} loading={manualRetrying} style={{ marginTop: spacing.lg, minWidth: 200 }} />
        </CenterState>
      </Screen>
    );
  }

  const meta = payload?.meta as { warnings?: string[]; pages?: number; item_count?: number } | undefined;
  const warnings = meta?.warnings ?? [];

  const matchForIndex = (i: number): MatchItem | undefined =>
    inv?.connected ? inv.items[i] : undefined;

  const needsAttention = (section: Section, i: number): boolean => {
    if (section.fields.some((f) => isLowConfidence(getLeaf(fields, f.path)?.confidence ?? null))) return true;
    if (inv?.connected) {
      const mi = inv.items[i];
      if (!mi || mi.candidates.length === 0 || mi.best_score < 70) return true;
    }
    return false;
  };

  const manyItems = itemSections.length > 6;
  const q = search.trim().toLowerCase();
  const filtered = itemSections
    .map((section, i) => ({ section, i }))
    .filter(({ section, i }) => {
      if (attentionOnly && !needsAttention(section, i)) return false;
      if (q) {
        const primary = String(getLeaf(fields, section.fields[0].path)?.value ?? '').toLowerCase();
        if (!primary.includes(q)) return false;
      }
      return true;
    });
  const attentionCount = itemSections.filter((s, i) => needsAttention(s, i)).length;

  // Header sections (patient/supplier) + line-item title — scroll with the list.
  const listHeader = (
    <View>
      {singleSections.map((section) => (
        <Card key={section.title}>
          <SectionTitle>{section.title}</SectionTitle>
          {section.fields.map((spec) => {
            const leaf = getLeaf(fields, spec.path);
            const conf = leaf?.confidence ?? null;
            return (
              <Field
                key={spec.path}
                label={spec.label}
                value={leaf?.value ?? ''}
                editable={editable}
                onChangeText={(v) => onChangeField(spec.path, v)}
                accentColor={confidenceColor(conf)}
                hint={isLowConfidence(conf) ? `${t('lowConfidence')} (${confidencePercent(conf)})` : undefined}
              />
            );
          })}
        </Card>
      ))}
      {itemSections.length > 0 && !manyItems && (
        <View style={styles.listHeaderRow}>
          <SectionTitle>{doc.doc_type === 'invoice' ? t('lineItems') : t('medications')}</SectionTitle>
          <Text style={styles.itemCount}>{itemSections.length}</Text>
        </View>
      )}
    </View>
  );

  return (
    <Screen>
      {/* Fixed top: title, summary, and (for long lists) search + filter — always visible */}
      <View style={styles.topBar}>
        <View style={styles.headerRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.docType}>{doc.doc_type === 'invoice' ? t('invoice') : t('prescription')}</Text>
            <Text style={styles.confidence}>
              {itemSections.length > 0
                ? `${itemSections.length} items${meta?.pages ? ` · ${meta.pages} pages` : ''}${inv?.connected ? ` · ${inv.matched}/${inv.total} matched` : ''}`
                : `${t('review')} · ${confidencePercent(doc.overall_confidence)}`}
            </Text>
          </View>
          <Badge label={doc.status.replace('_', ' ')} tone={doc.status === 'pushed' || doc.status === 'approved' ? 'success' : 'info'} />
        </View>

        {warnings.length > 0 && (
          <View style={styles.warnBanner}>
            <Text style={styles.warnText}>{warnings[0]}</Text>
          </View>
        )}

        {manyItems && (
          <>
            <TextInput
              style={styles.search}
              value={search}
              onChangeText={setSearch}
              placeholder={`Search ${itemSections.length} items…`}
              placeholderTextColor={colors.textMuted}
              autoCorrect={false}
            />
            <View style={styles.filterRow}>
              <TouchableOpacity onPress={() => setAttentionOnly(false)} style={[styles.chip, !attentionOnly && styles.chipActive]}>
                <Text style={[styles.chipText, !attentionOnly && styles.chipTextActive]}>All ({itemSections.length})</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setAttentionOnly(true)} style={[styles.chip, attentionOnly && styles.chipActive]}>
                <Text style={[styles.chipText, attentionOnly && styles.chipTextActive]}>Needs check ({attentionCount})</Text>
              </TouchableOpacity>
              {(q || attentionOnly) && (
                <Text style={styles.showing}>{filtered.length} shown</Text>
              )}
            </View>
          </>
        )}
      </View>

      <FlatList
        data={filtered}
        keyExtractor={({ section }) => section.title}
        ListHeaderComponent={listHeader}
        contentContainerStyle={styles.content}
        initialNumToRender={14}
        windowSize={11}
        removeClippedSubviews
        keyboardShouldPersistTaps="handled"
        ListEmptyComponent={
          itemSections.length > 0 ? (
            <Text style={styles.noMatch}>No items match “{search}”.</Text>
          ) : null
        }
        renderItem={({ item: { section, i } }) => (
          <ItemRow section={section} fields={fields} match={matchForIndex(i)} onPress={() => setEditIndex(i)} />
        )}
      />

      {/* Fixed bottom: actions — always reachable without scrolling */}
      {doc.status !== 'pushed' ? (
        <View style={styles.bottomBar}>
          {doc.status === 'needs_review' && (
            <Button title={t('save')} variant="secondary" onPress={save} loading={saving} style={styles.flexBtn} />
          )}
          {doc.status === 'needs_review' && (
            <Button title={t('approve')} variant="success" onPress={approveOnly} loading={busy === 'approve'} style={styles.flexBtn} />
          )}
          {(doc.status === 'needs_review' || doc.status === 'approved') && (
            <Button title={t('approveAndPush')} onPress={approveAndSend} loading={busy === 'push'} style={styles.flexBtn} />
          )}
        </View>
      ) : (
        <View style={styles.bottomBar}>
          <Text style={styles.sentText}>✓ {t('pushed')}</Text>
        </View>
      )}

      {/* Edit-a-single-item modal */}
      <Modal visible={editIndex !== null} animationType="slide" transparent onRequestClose={() => setEditIndex(null)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setEditIndex(null)} />
        <View style={styles.modalSheet}>
          {editIndex !== null && itemSections[editIndex] && (
            <>
              <View style={styles.modalHead}>
                <Text style={styles.modalTitle}>{itemSections[editIndex].title}</Text>
                <TouchableOpacity onPress={() => setEditIndex(null)}>
                  <Text style={styles.modalDone}>Done</Text>
                </TouchableOpacity>
              </View>
              <ScrollView>
                {itemSections[editIndex].fields.map((spec) => {
                  const leaf = getLeaf(fields, spec.path);
                  const conf = leaf?.confidence ?? null;
                  return (
                    <Field
                      key={spec.path}
                      label={spec.label}
                      value={leaf?.value ?? ''}
                      editable={editable}
                      onChangeText={(v) => onChangeField(spec.path, v)}
                      accentColor={confidenceColor(conf)}
                      hint={isLowConfidence(conf) ? `${t('lowConfidence')} (${confidencePercent(conf)})` : undefined}
                    />
                  );
                })}
                {matchForIndex(editIndex)?.candidates?.length ? (
                  <View style={styles.invBox}>
                    <Text style={styles.invLabel}>In your inventory</Text>
                    {matchForIndex(editIndex)!.candidates.slice(0, 3).map((c) => (
                      <View key={c.id} style={styles.invRow}>
                        <Text style={styles.invName}>{c.name}</Text>
                        <Text style={styles.invMeta}>
                          {c.stock_qty != null ? `stock ${c.stock_qty}` : ''} · {Math.round(c.score)}%
                        </Text>
                      </View>
                    ))}
                  </View>
                ) : null}
              </ScrollView>
            </>
          )}
        </View>
      </Modal>
    </Screen>
  );
}

/** Compact, read-only row for a line item / medication. Tap to edit. */
function ItemRow({
  section,
  fields,
  match,
  onPress,
}: {
  section: Section;
  fields: Fields;
  match?: MatchItem;
  onPress: () => void;
}) {
  const primary = getLeaf(fields, section.fields[0].path)?.value || '(unnamed)';
  // Secondary summary from a couple of key fields (skip the primary).
  const secondary = section.fields
    .slice(1)
    .map((f) => {
      const v = getLeaf(fields, f.path)?.value;
      return v ? `${f.label}: ${v}` : null;
    })
    .filter(Boolean)
    .slice(0, 3)
    .join('  ·  ');
  const best = match?.best_score ?? null;

  return (
    <TouchableOpacity style={styles.itemRow} onPress={onPress} activeOpacity={0.7}>
      <View style={{ flex: 1 }}>
        <Text style={styles.itemPrimary} numberOfLines={1}>{primary}</Text>
        {secondary ? <Text style={styles.itemSecondary} numberOfLines={1}>{secondary}</Text> : null}
      </View>
      {best != null && (
        <Badge label={`${Math.round(best)}%`} tone={best >= 85 ? 'success' : best >= 70 ? 'warning' : 'neutral'} />
      )}
      <Text style={styles.chevron}>›</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg, paddingBottom: spacing.xl },
  topBar: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  search: {
    minHeight: 44, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md,
    paddingHorizontal: spacing.md, ...font.body, color: colors.text, backgroundColor: colors.surfaceAlt, marginTop: spacing.sm,
  },
  filterRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginTop: spacing.sm },
  chip: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderRadius: radius.pill, backgroundColor: colors.surfaceAlt },
  chipActive: { backgroundColor: colors.primaryTint },
  chipText: { ...font.caption, color: colors.textSecondary },
  chipTextActive: { color: colors.primaryDark, fontWeight: '600' },
  showing: { ...font.caption, color: colors.textMuted, marginLeft: 'auto' },
  noMatch: { ...font.body, color: colors.textMuted, textAlign: 'center', padding: spacing.xl },
  bottomBar: { flexDirection: 'row', gap: spacing.sm, padding: spacing.md, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
  flexBtn: { flex: 1 },
  sentText: { ...font.h3, color: colors.success, textAlign: 'center', flex: 1, paddingVertical: spacing.sm },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: spacing.sm },
  docType: { ...font.h1, color: colors.text },
  confidence: { ...font.body, color: colors.textSecondary, marginTop: spacing.xs },
  warnBanner: { backgroundColor: colors.warningTint, padding: spacing.md, borderRadius: spacing.sm, marginBottom: spacing.lg },
  warnText: { ...font.body, color: colors.warning },
  listHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: spacing.sm, marginBottom: spacing.sm },
  itemCount: { ...font.label, color: colors.textSecondary },
  itemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
    gap: spacing.sm,
  },
  itemPrimary: { ...font.h3, color: colors.text },
  itemSecondary: { ...font.caption, color: colors.textSecondary, marginTop: 2 },
  chevron: { ...font.h2, color: colors.textMuted },
  actions: { gap: spacing.md, marginTop: spacing.lg },
  invSummary: { ...font.caption, color: colors.textSecondary, textAlign: 'center', marginBottom: spacing.sm },
  modalBackdrop: { flex: 1, backgroundColor: colors.overlay },
  modalSheet: { position: 'absolute', bottom: 0, left: 0, right: 0, maxHeight: '85%', backgroundColor: colors.bg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg },
  modalHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.md },
  modalTitle: { ...font.h2, color: colors.text },
  modalDone: { ...font.h3, color: colors.primary },
  invBox: { marginTop: spacing.md, backgroundColor: colors.surfaceAlt, borderRadius: radius.md, padding: spacing.md },
  invLabel: { ...font.label, color: colors.textSecondary, textTransform: 'uppercase', marginBottom: spacing.sm },
  invRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: spacing.xs },
  invName: { ...font.body, color: colors.text, flex: 1 },
  invMeta: { ...font.caption, color: colors.textSecondary },
});
