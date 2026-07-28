import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, View } from 'react-native';

import {
  DocumentDto,
  approveDocument,
  getDocument,
  patchDocument,
  pushDocument,
} from '@/src/api/documents';
import { Badge, Button, Card, CenterState, Field, Screen, SectionTitle } from '@/src/theme/components';
import { colors, font, spacing } from '@/src/theme/tokens';
import { buildSections, getLeaf, setLeafValue, ExtractionPayload, Fields } from '@/src/lib/payload';
import { confidenceColor, confidencePercent, isLowConfidence } from '@/src/lib/confidence';
import { t } from '@/src/i18n/strings';

const POLL_MS = 2000;

export default function ReviewScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [doc, setDoc] = useState<DocumentDto | null>(null);
  const [fields, setFields] = useState<Fields>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const applyDoc = useCallback((d: DocumentDto) => {
    setDoc(d);
    if (d.payload?.fields) setFields(d.payload.fields);
  }, []);

  // Poll until processing completes.
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const d = await getDocument(id);
        if (!active) return;
        if (d.status === 'queued' || d.status === 'processing') {
          timer = setTimeout(poll, POLL_MS);
        } else {
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

  const onChangeField = (path: string, value: string) => {
    setFields((prev) => setLeafValue(prev, path, value));
  };

  async function save(): Promise<boolean> {
    setSaving(true);
    try {
      const updated = await patchDocument(id, fields);
      applyDoc(updated);
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
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? t('errorGeneric');
      Alert.alert(t('failed'), detail);
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <Screen>
        <CenterState title={t('processing')} subtitle="Reading the document…" />
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
        <CenterState title={t('failed')} subtitle={doc.error ?? 'Extraction failed. Please rescan.'} />
      </Screen>
    );
  }

  const payload = doc.payload as ExtractionPayload;
  const sections = buildSections({ ...payload, fields });
  const warnings = payload?.meta?.warnings ?? [];
  const editable = doc.status === 'needs_review' || doc.status === 'approved' || doc.status === 'pushed';

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.headerRow}>
          <View>
            <Text style={styles.docType}>{doc.doc_type === 'invoice' ? t('invoice') : t('prescription')}</Text>
            <Text style={styles.confidence}>
              {t('review')} · {confidencePercent(doc.overall_confidence)}
            </Text>
          </View>
          <Badge
            label={doc.status.replace('_', ' ')}
            tone={doc.status === 'pushed' || doc.status === 'approved' ? 'success' : 'info'}
          />
        </View>

        {warnings.length > 0 && (
          <View style={styles.warnBanner}>
            <Text style={styles.warnText}>{t('lowConfidence')}</Text>
          </View>
        )}

        {sections.map((section) => (
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

        <View style={styles.actions}>
          {doc.status === 'needs_review' && (
            <>
              <Button title={t('save')} variant="secondary" onPress={save} loading={saving} />
              <Button title={t('approve')} variant="success" onPress={approveOnly} loading={busy === 'approve'} />
            </>
          )}
          {(doc.status === 'needs_review' || doc.status === 'approved') && (
            <Button title={t('approveAndPush')} onPress={approveAndSend} loading={busy === 'push'} />
          )}
          {doc.status === 'pushed' && (
            <CenterState title={t('pushed')} subtitle="Sent to your software." />
          )}
        </View>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg, paddingBottom: spacing.xxl },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: spacing.lg },
  docType: { ...font.h1, color: colors.text },
  confidence: { ...font.body, color: colors.textSecondary, marginTop: spacing.xs },
  warnBanner: { backgroundColor: colors.warningTint, padding: spacing.md, borderRadius: spacing.sm, marginBottom: spacing.lg },
  warnText: { ...font.body, color: colors.warning },
  actions: { gap: spacing.md, marginTop: spacing.sm },
});
