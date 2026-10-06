import AsyncStorage from '@react-native-async-storage/async-storage';
import { useLocalSearchParams, router } from 'expo-router';
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
  chooseOption,
  getDocument,
  patchDocument,
  pushDocument,
  reportDocument,
  retryDocument,
} from '@/src/api/documents';
import { Badge, Button, Card, CenterState, Field, Screen, SectionTitle } from '@/src/theme/components';
import { colors, font, radius, spacing } from '@/src/theme/tokens';
import { addLineItem, buildSections, extraColumns, getLeaf, removeLineItem, setLeafValue, ExtractionPayload, FieldSpec, Fields, Leaf, Section } from '@/src/lib/payload';
import InvoiceTable, { TableRow } from '@/src/components/InvoiceTable';
import { formatMoney } from '@/src/lib/table';
import { confidenceColor, confidencePercent, isLowConfidence } from '@/src/lib/confidence';
import { isRetryableFailure, isWaitingForAi } from '@/src/lib/failure';
import { matchDocument, DocMatch, MatchItem } from '@/src/api/inventory';
import { t } from '@/src/i18n/strings';
import {
  Check,
  Choice,
  choicesOf,
  openChecks,
  optionText,
  pendingChoices,
  verdictOf,
  verificationOf,
} from '@/src/lib/verification';

const POLL_MS = 2000;
const MAX_AUTO_RETRIES = 3;

/** Table vs cards is a working preference, so it outlives the screen. */
const VIEW_MODE_KEY = 'review.viewMode';
type ViewMode = 'table' | 'cards';

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
  const [elapsed, setElapsed] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>('table');
  const [headerOpen, setHeaderOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [rawTextOpen, setRawTextOpen] = useState(false);
  const [reportNote, setReportNote] = useState('');
  const [reporting, setReporting] = useState(false);
  // The confirm-before-approve checklist: which failed checks, which are ticked.
  const [ackAction, setAckAction] = useState<'approve' | 'push' | null>(null);
  const [ackChecks, setAckChecks] = useState<Check[]>([]);
  const [acked, setAcked] = useState<Set<string>>(new Set());

  useEffect(() => {
    AsyncStorage.getItem(VIEW_MODE_KEY)
      .then((saved) => {
        if (saved === 'table' || saved === 'cards') setViewMode(saved);
      })
      .catch(() => {});
  }, []);

  const chooseViewMode = (mode: ViewMode) => {
    setViewMode(mode);
    AsyncStorage.setItem(VIEW_MODE_KEY, mode).catch(() => {});
  };

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
    let interval: ReturnType<typeof setInterval>;
    let retries = 0;
    let errorCount = 0;

    interval = setInterval(() => {
      if (active) setElapsed((prev) => prev + 1);
    }, 1000);

    async function poll() {
      try {
        const d = await getDocument(id);
        if (!active) return;
        errorCount = 0;
        setDoc(d); // Always update doc so progress (e.g. 2/5 pages) updates in real-time
        if (d.status === 'queued' || d.status === 'processing') {
          timer = setTimeout(poll, POLL_MS);
        } else if (d.status === 'failed' && retries < 1 && isRetryableFailure(d.error)) {
          // Only a scan cut short by a server restart is retried here. A busy
          // AI is already waited out on the server, and anything else would
          // fail the same way again.
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
        if (!active) return;
        errorCount += 1;
        // If it's a momentary network blip, keep trying instead of failing immediately
        if (errorCount < 5) {
          timer = setTimeout(poll, POLL_MS);
        } else {
          setLoading(false);
        }
      }
    }
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [id, applyDoc]);

  const onChangeField = (path: string, value: string) => setFields((prev) => setLeafValue(prev, path, value));

  // A line the reading missed - or every line, on a document sent for manual
  // entry - is typed in here, then saved with the rest.
  function addItem() {
    const next = addLineItem(fields);
    setFields(next.fields);
    setEditIndex(next.index);
  }

  function removeItem(index: number) {
    Alert.alert('Remove this item?', 'It will be taken off this invoice when you save.', [
      { text: t('cancel'), style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: () => {
          setEditIndex(null);
          setFields((prev) => removeLineItem(prev, index));
        },
      },
    ]);
  }

  async function save(): Promise<DocumentDto | null> {
    setSaving(true);
    try {
      const saved = await patchDocument(id, fields);
      applyDoc(saved);
      return saved;
    } catch {
      Alert.alert(t('errorGeneric'));
      return null;
    } finally {
      setSaving(false);
    }
  }

  // Approval confirms every failed check against the paper first. Saving
  // re-runs the checks on the server, so the list is taken from what it
  // returns, never from the screen's older copy.
  async function startApproval(action: 'approve' | 'push') {
    setBusy(action);
    try {
      const saved = await save();
      if (!saved) return;
      const undecided = pendingChoices(saved.payload);
      if (undecided.length > 0) {
        Alert.alert('Choose first', undecided.map((c) => c.question).join('\n\n'));
        return;
      }
      const open = openChecks(verificationOf(saved.payload));
      if (open.length > 0) {
        setAckChecks(open);
        setAcked(new Set());
        setAckAction(action);
        return;
      }
      await finishApproval(action, []);
    } finally {
      setBusy(null);
    }
  }

  async function finishApproval(action: 'approve' | 'push', acknowledged: string[]) {
    setBusy(action);
    try {
      const approved = await approveDocument(id, acknowledged);
      setAckAction(null);
      if (action === 'approve') {
        applyDoc(approved);
        return;
      }
      const result = await pushDocument(id);
      applyDoc(result);
      Alert.alert(t('pushed'), `${result.deliveries.length} delivery(ies)`);
    } catch (e: unknown) {
      const raw = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      const detail = typeof raw === 'string' ? raw : (raw as { message?: string })?.message ?? t('errorGeneric');
      Alert.alert(t('failed'), detail);
    } finally {
      setBusy(null);
    }
  }

  // A choice is made on the server, so any edits on screen are saved first and
  // nothing typed is lost when the answer comes back.
  async function pick(choice: Choice, option: number) {
    setBusy('choose');
    try {
      const saved = await save();
      if (!saved) return;
      applyDoc(await chooseOption(id, choice.id, option, choice.remember));
    } catch {
      Alert.alert(t('errorGeneric'));
    } finally {
      setBusy(null);
    }
  }

  const approveOnly = () => startApproval('approve');
  const approveAndSend = () => startApproval('push');

  /**
   * Every defect so far reached us as a WhatsApp message that had to be
   * reproduced from a description. This sends what the pipeline actually
   * produced alongside the stored file, so it can become a test case.
   */
  async function sendReport() {
    setReporting(true);
    try {
      const ack = await reportDocument(id, reportNote);
      setReportOpen(false);
      setReportNote('');
      Alert.alert('Reported', ack.message);
    } catch {
      Alert.alert(t('errorGeneric'));
    } finally {
      setReporting(false);
    }
  }

  async function tryAgain() {
    setManualRetrying(true);
    setLoading(true);
    setElapsed(0);
    try {
      await retryDocument(id);
    } catch {
      /* ignore */
    } finally {
      setManualRetrying(false);
    }
    const pollOnce = async () => {
      try {
        const d = await getDocument(id);
        setDoc(d);
        if (d.status === 'queued' || d.status === 'processing') setTimeout(pollOnce, POLL_MS);
        else {
          applyDoc(d);
          setLoading(false);
        }
      } catch {
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
          title={
            autoRetry > 0 ? 'Retrying…' : isWaitingForAi(doc?.progress) ? 'Waiting in the queue…' : t('processing')
          }
          subtitle={
            autoRetry > 0
              ? 'The scan was interrupted - reading it again…'
              : isWaitingForAi(doc?.progress)
                ? 'Many scans are being read right now. Yours will finish on its own - you can leave this screen.'
                : doc?.progress
                ? `Reading page ${doc.progress}…`
                : elapsed > 15
                  ? `Analyzing document (${elapsed}s)…`
                  : 'Reading the document…'
          }
        >
          {elapsed > 12 && (
            <Button
              title="Check History"
              variant="secondary"
              onPress={() => router.replace('/(tabs)/history')}
              style={{ marginTop: spacing.lg, minWidth: 180 }}
            />
          )}
        </CenterState>
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
        <CenterState title={t('failed')} subtitle={doc.error ?? 'Extraction failed. Please try again.'}>
          <Button title={manualRetrying ? 'Retrying…' : 'Try again'} onPress={tryAgain} loading={manualRetrying} style={{ marginTop: spacing.lg, minWidth: 200 }} />
        </CenterState>
      </Screen>
    );
  }

  const meta = payload?.meta as
    | {
        warnings?: string[];
        pages?: number;
        item_count?: number;
        line_items_total?: string | null;
        total_reconciles?: boolean | null;
        total_reconciled_by?: string | null;
        total_in_words?: string | null;
        total_in_words_disagrees?: boolean;
        needs_manual_entry?: boolean;
        raw_text?: string | null;
      }
    | undefined;
  const warnings = meta?.warnings ?? [];
  const verdict = verdictOf(verificationOf(payload));
  const choices = choicesOf(payload);

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

  // Table mode is for invoices only; a prescription's fields are prose, not columns.
  const isInvoice = doc.doc_type === 'invoice';
  // What the bill itself says is payable - the figure on the paper in the
  // pharmacist's hand.
  const billTotal = getLeaf(fields, 'invoice.total_amount')?.value ?? null;
  const showTable = isInvoice && viewMode === 'table' && itemSections.length > 0;
  const rawItems = (fields.line_items as Record<string, Leaf>[]) || [];
  const tableRows: TableRow[] = filtered.map(({ section, i }) => ({
    item: rawItems[i] ?? {},
    index: i,
    attention: needsAttention(section, i),
  }));

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
          <View style={{ alignItems: 'flex-end', gap: spacing.xs }}>
            <Badge label={doc.status.replace('_', ' ')} tone={doc.status === 'pushed' || doc.status === 'approved' ? 'success' : 'info'} />
            <TouchableOpacity onPress={() => setReportOpen(true)} accessibilityRole="button">
              <Text style={styles.reportLink}>⚑ Report a problem</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Integrity warnings are full sentences ("the invoice states 143 items
            but 429 were read"); low-confidence warnings are dotted field paths.
            Show the sentences — they are the ones that change a decision. */}
        {/* The verdict: every check the bill's own figures allow, passed or
            not. Tapping a failing verdict shows just the lines to check. */}
        {verdict && (
          <TouchableOpacity
            activeOpacity={verdict.ok ? 1 : 0.7}
            onPress={() => {
              if (!verdict.ok) setAttentionOnly(true);
            }}
            style={[styles.verdict, verdict.ok ? styles.verdictOk : styles.verdictWarn]}
          >
            <Text style={[styles.verdictTitle, { color: verdict.ok ? colors.success : colors.warning }]}>
              {verdict.ok ? '✓ ' : '⚠ '}
              {verdict.title}
            </Text>
            <Text style={styles.verdictDetail}>{verdict.detail}</Text>
          </TouchableOpacity>
        )}

        {/* Fields the bill gives two answers to: the reviewer decides. A
            remembered decision (from this supplier's earlier bills) shows as
            such and can still be changed. */}
        {choices.map((choice) => (
          <View key={choice.id} style={[styles.choiceCard, choice.chosen == null && styles.choicePending]}>
            <Text style={styles.choiceTitle}>
              {choice.label}
              {choice.chosen == null ? ' - choose one' : choice.remembered ? ' · remembered for this supplier' : ''}
            </Text>
            <Text style={styles.choiceQuestion}>{choice.question}</Text>
            {choice.options.map((opt, i) => {
              const on = choice.chosen === i;
              return (
                <TouchableOpacity
                  key={opt.label}
                  style={[styles.choiceOption, on && styles.choiceOptionOn]}
                  onPress={() => pick(choice, i)}
                  disabled={busy !== null}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on }}
                >
                  <Text style={styles.choiceOptionLabel}>
                    {on ? '● ' : '○ '}
                    {opt.label}
                  </Text>
                  <Text style={styles.choiceOptionValue}>{optionText(opt) || 'blank on the bill'}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ))}

        {!verdict && warnings.length > 0 &&
          (() => {
            const sentences = warnings.filter((w) => w.includes(' ')).slice(0, 2);
            const shown = sentences.length > 0 ? sentences : [t('lowConfidence')];
            return (
              <View style={styles.warnBanner}>
                {shown.map((w) => (
                  <Text key={w} style={styles.warnText}>
                    {w}
                  </Text>
                ))}
              </View>
            );
          })()}

        {/* In table mode the supplier/invoice fields live behind this line, so the
            table gets the full height of the screen. */}
        {showTable && (
          <TouchableOpacity style={styles.headerSummary} onPress={() => setHeaderOpen(true)} activeOpacity={0.7}>
            <Text style={styles.headerSummaryText} numberOfLines={1}>
              {[
                getLeaf(fields, 'supplier.name')?.value,
                getLeaf(fields, 'invoice.invoice_no')?.value,
                getLeaf(fields, 'invoice.invoice_date')?.value,
              ]
                .filter(Boolean)
                .join('  ·  ') || 'Invoice details'}
            </Text>
            <Text style={styles.headerSummaryChevron}>›</Text>
          </TouchableOpacity>
        )}

        {isInvoice && itemSections.length > 0 && (
          <View style={styles.viewToggle}>
            <TouchableOpacity
              onPress={() => chooseViewMode('table')}
              style={[styles.toggleBtn, viewMode === 'table' && styles.toggleBtnActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: viewMode === 'table' }}
            >
              <Text style={[styles.toggleText, viewMode === 'table' && styles.toggleTextActive]}>▤  Table</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => chooseViewMode('cards')}
              style={[styles.toggleBtn, viewMode === 'cards' && styles.toggleBtnActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: viewMode === 'cards' }}
            >
              <Text style={[styles.toggleText, viewMode === 'cards' && styles.toggleTextActive]}>☰  Cards</Text>
            </TouchableOpacity>
          </View>
        )}

        {isInvoice && (editable || meta?.raw_text) && (
          <View style={styles.manualRow}>
            {editable && (
              <TouchableOpacity onPress={addItem} style={styles.manualBtn} accessibilityRole="button">
                <Text style={styles.manualBtnText}>＋ Add line item</Text>
              </TouchableOpacity>
            )}
            {meta?.raw_text ? (
              <TouchableOpacity onPress={() => setRawTextOpen(true)} style={styles.manualBtn} accessibilityRole="button">
                <Text style={styles.manualBtnText}>📄 Text from the document</Text>
              </TouchableOpacity>
            ) : null}
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

      {showTable ? (
        <InvoiceTable
          fields={fields}
          rows={tableRows}
          onSelect={setEditIndex}
          emptyText={itemSections.length > 0 ? `No items match “${search}”.` : undefined}
        />
      ) : (
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
      )}

      {/* The trust signal: do the lines we read add up to the total on the paper?
          If they do, the pharmacist can approve without checking every line. */}
      {isInvoice && itemSections.length > 0 && (
        <View
          style={[
            styles.totalsBar,
            meta?.total_reconciles === true && styles.totalsOk,
            meta?.total_reconciles === false && styles.totalsMismatch,
          ]}
        >
          <View style={{ flex: 1 }}>
            <Text style={styles.totalsItems}>
              {itemSections.length} items
              {filtered.length !== itemSections.length ? ` · ${filtered.length} shown` : ''}
            </Text>
            {/* The lines' own sum, kept visible but clearly secondary. It is the
                TAXABLE total, which on most bills is thousands less than the
                payable one - shown as the headline it read as a mismatch. */}
            {meta?.line_items_total ? (
              <Text style={styles.totalsSub}>
                items ₹{formatMoney(meta.line_items_total)} before tax
                {/* What the bill says in its OWN words, when that differs
                    from what its figures add up to. On an Indian tax invoice
                    the words are the controlling figure, so a reviewer about
                    to approve a payment should see both. */}
                {meta?.total_in_words_disagrees && meta.total_in_words
                  ? ` · in words ₹${formatMoney(meta.total_in_words)}` : ''}
                {/* A tick earned only against the printed TAXABLE total is a
                    weaker one - the bill's own grand total was never reached,
                    usually because a narrow tax column did not read. Say so,
                    rather than letting it look like a full match. */}
                {meta?.total_reconciles === true && /taxable/.test(meta.total_reconciled_by || '')
                  ? ' · matched on the taxable total' : ''}
              </Text>
            ) : null}
          </View>
          {/* The headline figure is the total PRINTED ON THE BILL, because that
              is the number the pharmacist is holding and comparing against.
              The tick says our lines agree with it; it must never sit beside a
              different number. */}
          <Text style={styles.totalsAmount}>
            {meta?.total_reconciles === true ? '✓ ' : meta?.total_reconciles === false ? '⚠ ' : ''}
            ₹{billTotal ? formatMoney(billTotal) : meta?.line_items_total ? formatMoney(meta.line_items_total) : '—'}
          </Text>
        </View>
      )}

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

      {/* Confirm-before-approve: each failed check ticked against the paper. */}
      <Modal visible={ackAction !== null} animationType="slide" transparent onRequestClose={() => setAckAction(null)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setAckAction(null)} />
        <View style={styles.modalSheet}>
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>Check against the paper</Text>
            <TouchableOpacity onPress={() => setAckAction(null)}>
              <Text style={styles.modalDone}>Cancel</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.reportHelp}>
            These didn't add up on this bill. Look at each on the paper, then tick it to confirm what the app shows is right.
          </Text>
          <ScrollView style={{ maxHeight: 360 }}>
            {ackChecks.map((check) => {
              const on = acked.has(check.id);
              return (
                <TouchableOpacity
                  key={check.id}
                  style={styles.ackRow}
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: on }}
                  onPress={() =>
                    setAcked((prev) => {
                      const next = new Set(prev);
                      if (on) next.delete(check.id);
                      else next.add(check.id);
                      return next;
                    })
                  }
                >
                  <Text style={[styles.ackBox, on && styles.ackBoxOn]}>{on ? '✓' : ''}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.ackLabel}>{check.label}</Text>
                    {check.message ? <Text style={styles.ackMessage}>{check.message}</Text> : null}
                  </View>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
          <Button
            title={ackAction === 'push' ? 'Confirm, approve & send' : 'Confirm & approve'}
            variant="success"
            disabled={acked.size < ackChecks.length}
            loading={busy === ackAction}
            onPress={() => ackAction && finishApproval(ackAction, ackChecks.map((c) => c.id))}
          />
        </View>
      </Modal>

      {/* Report-a-problem sheet */}
      <Modal visible={reportOpen} animationType="slide" transparent onRequestClose={() => setReportOpen(false)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setReportOpen(false)} />
        <View style={styles.modalSheet}>
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>Report a problem</Text>
            <TouchableOpacity onPress={() => setReportOpen(false)}>
              <Text style={styles.modalDone}>Cancel</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.reportHelp}>
            What looks wrong on this {doc.doc_type === 'invoice' ? 'bill' : 'prescription'}? We'll get
            the document and the details we read, so we can fix it.
          </Text>
          <TextInput
            style={styles.reportInput}
            value={reportNote}
            onChangeText={setReportNote}
            placeholder="e.g. 143 items on the bill but the app shows 429"
            placeholderTextColor={colors.textMuted}
            multiline
            numberOfLines={4}
            textAlignVertical="top"
          />
          <Button title={reporting ? 'Sending…' : 'Send report'} onPress={sendReport} loading={reporting} />
        </View>
      </Modal>

      {/* Supplier / invoice fields — reachable from the summary line in table mode */}
      <Modal visible={headerOpen} animationType="slide" transparent onRequestClose={() => setHeaderOpen(false)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setHeaderOpen(false)} />
        <View style={styles.modalSheet}>
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>{t('invoiceDetails')}</Text>
            <TouchableOpacity onPress={() => setHeaderOpen(false)}>
              <Text style={styles.modalDone}>Done</Text>
            </TouchableOpacity>
          </View>
          <ScrollView>
            {singleSections.map((section) => (
              <View key={section.title}>
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
              </View>
            ))}
          </ScrollView>
        </View>
      </Modal>

      {/* What the server read off the document, for a reviewer filling it in by
          hand: selectable, so a batch number or a name can be copied, not retyped. */}
      <Modal visible={rawTextOpen} animationType="slide" transparent onRequestClose={() => setRawTextOpen(false)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setRawTextOpen(false)} />
        <View style={styles.modalSheet}>
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>Text from the document</Text>
            <TouchableOpacity onPress={() => setRawTextOpen(false)}>
              <Text style={styles.modalDone}>Done</Text>
            </TouchableOpacity>
          </View>
          <ScrollView>
            <Text selectable style={styles.rawText}>
              {meta?.raw_text ?? ''}
            </Text>
          </ScrollView>
        </View>
      </Modal>

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
                {editable && isInvoice && (
                  <TouchableOpacity onPress={() => removeItem(editIndex)} style={styles.removeBtn} accessibilityRole="button">
                    <Text style={styles.removeText}>Remove this item</Text>
                  </TouchableOpacity>
                )}
                {/* Columns this supplier prints that we have no name for. Read-only:
                    they are shown so nothing on the bill is invisible. */}
                {extraColumns(fields, editIndex).length > 0 && (
                  <View style={styles.invBox}>
                    <Text style={styles.invLabel}>Also on this invoice</Text>
                    {extraColumns(fields, editIndex).map((e) => (
                      <View key={e.label} style={styles.invRow}>
                        <Text style={styles.invName}>{e.label}</Text>
                        <Text style={styles.invMeta}>{e.value}</Text>
                      </View>
                    ))}
                  </View>
                )}
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

// The fields worth showing on a collapsed row, in the order a pharmacist checks
// them. Falls back to "whatever is populated" for any section without them.
const SUMMARY_PREFERENCE = ['.quantity', '.rate', '.amount', '.strength', '.frequency', '.duration'];

function summarise(section: Section, fields: Fields): string {
  const labelled = (specs: FieldSpec[]) =>
    specs
      .map((f) => {
        const v = getLeaf(fields, f.path)?.value;
        return v ? `${f.label}: ${v}` : null;
      })
      .filter(Boolean) as string[];

  const preferred = SUMMARY_PREFERENCE
    .map((suffix) => section.fields.find((f) => f.path.endsWith(suffix)))
    .filter(Boolean) as FieldSpec[];

  const picked = labelled(preferred);
  return (picked.length ? picked : labelled(section.fields.slice(1))).slice(0, 3).join('  ·  ');
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
  const secondary = summarise(section, fields);
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
  reportLink: { ...font.caption, color: colors.textMuted, textDecorationLine: 'underline' },
  reportHelp: { ...font.body, color: colors.textSecondary, marginBottom: spacing.md },
  reportInput: {
    minHeight: 110, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md,
    padding: spacing.md, ...font.body, color: colors.text,
    backgroundColor: colors.surface, marginBottom: spacing.lg,
  },
  headerSummary: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surfaceAlt, borderRadius: radius.md,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginTop: spacing.sm,
  },
  headerSummaryText: { ...font.caption, color: colors.textSecondary, flex: 1 },
  headerSummaryChevron: { ...font.h3, color: colors.textMuted },
  viewToggle: {
    flexDirection: 'row', gap: spacing.xs, marginTop: spacing.sm,
    backgroundColor: colors.surfaceAlt, borderRadius: radius.pill, padding: 3, alignSelf: 'flex-start',
  },
  toggleBtn: { paddingHorizontal: spacing.lg, paddingVertical: spacing.xs, borderRadius: radius.pill },
  toggleBtnActive: { backgroundColor: colors.surface },
  toggleText: { ...font.caption, color: colors.textSecondary },
  toggleTextActive: { color: colors.primaryDark, fontWeight: '700' },
  totalsBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceAlt, borderTopWidth: 1, borderTopColor: colors.border,
  },
  totalsOk: { backgroundColor: colors.successTint },
  totalsMismatch: { backgroundColor: colors.warningTint },
  totalsItems: { ...font.caption, color: colors.textSecondary, fontWeight: '600' },
  totalsSub: { ...font.caption, fontSize: 11, color: colors.textMuted, marginTop: 1 },
  totalsAmount: { ...font.h3, color: colors.text },
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
  verdict: { padding: spacing.md, borderRadius: spacing.sm, marginBottom: spacing.lg },
  choiceCard: { padding: spacing.md, borderRadius: spacing.sm, marginBottom: spacing.lg, borderWidth: 1, borderColor: colors.border },
  choicePending: { borderColor: colors.warning, backgroundColor: colors.warningTint },
  choiceTitle: { ...font.body, fontWeight: '700' },
  choiceQuestion: { ...font.body, color: colors.textMuted, marginTop: 2, marginBottom: spacing.sm },
  choiceOption: { padding: spacing.md, borderRadius: spacing.sm, borderWidth: 1, borderColor: colors.border, marginTop: spacing.sm, backgroundColor: '#fff' },
  choiceOptionOn: { borderColor: colors.success, backgroundColor: colors.successTint },
  choiceOptionLabel: { ...font.body, fontWeight: '600' },
  choiceOptionValue: { ...font.body, color: colors.textMuted, marginTop: 2 },
  verdictOk: { backgroundColor: colors.successTint },
  verdictWarn: { backgroundColor: colors.warningTint },
  verdictTitle: { ...font.body, fontWeight: '700' },
  verdictDetail: { ...font.body, color: colors.textMuted, marginTop: 2 },
  ackRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md, paddingVertical: spacing.md, borderBottomWidth: StyleSheet.hairlineWidth, borderColor: colors.border },
  ackBox: { width: 24, height: 24, borderRadius: 6, borderWidth: 2, borderColor: colors.border, textAlign: 'center', lineHeight: 20, fontWeight: '700', color: '#fff' },
  ackBoxOn: { backgroundColor: colors.success, borderColor: colors.success },
  ackLabel: { ...font.body, fontWeight: '600' },
  ackMessage: { ...font.body, color: colors.textMuted, marginTop: 2 },
  modalSheet: { position: 'absolute', bottom: 0, left: 0, right: 0, maxHeight: '85%', backgroundColor: colors.bg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg },
  modalHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.md },
  modalTitle: { ...font.h2, color: colors.text },
  modalDone: { ...font.h3, color: colors.primary },
  manualRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm },
  manualBtn: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: spacing.sm,
    borderWidth: 1,
    borderColor: colors.primary,
  },
  manualBtnText: { ...font.body, color: colors.primary },
  removeBtn: { alignItems: 'center', paddingVertical: spacing.md, marginTop: spacing.md },
  removeText: { ...font.body, color: colors.danger },
  rawText: { ...font.body, color: colors.text, fontFamily: 'monospace', paddingBottom: spacing.xl },
  invBox: { marginTop: spacing.md, backgroundColor: colors.surfaceAlt, borderRadius: radius.md, padding: spacing.md },
  invLabel: { ...font.label, color: colors.textSecondary, textTransform: 'uppercase', marginBottom: spacing.sm },
  invRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: spacing.xs },
  invName: { ...font.body, color: colors.text, flex: 1 },
  invMeta: { ...font.caption, color: colors.textSecondary },
});
