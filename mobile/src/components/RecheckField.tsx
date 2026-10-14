import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { recheckField, RecheckResult } from '@/src/api/documents';
import { colors, font, radius, spacing } from '@/src/theme/tokens';

/**
 * "Recheck" under a field the reviewer doubts: the AI is asked again about
 * this one value and says what the bill prints for it. Nothing changes until
 * the reviewer uses the value and saves - then it is recorded as a correction,
 * so this supplier's next bill is read right.
 */
export default function RecheckField({ docId, path, onUse, revision }: {
  docId: string;
  path: string;
  onUse: (value: string) => void;
  revision?: unknown;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RecheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  useEffect(() => {
    request.current += 1;
    setResult(null);
    setError(null);
    setBusy(false);
    return () => { request.current += 1; };
  }, [docId, path, revision]);

  async function ask() {
    const token = ++request.current;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const answer = await recheckField(docId, path);
      if (token === request.current) setResult(answer);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      if (token === request.current) setError(typeof detail === 'string' ? detail : 'Could not recheck this field. Please try again.');
    } finally {
      if (token === request.current) setBusy(false);
    }
  }

  const offer = !!(result && result.found && !result.agrees && result.on_page !== false);
  return (
    <View style={styles.wrap}>
      {!result && (
        <TouchableOpacity onPress={ask} disabled={busy} accessibilityRole="button" style={styles.link}>
          {busy ? <ActivityIndicator size="small" color={colors.primary} /> : <Text style={styles.linkText}>Recheck</Text>}
        </TouchableOpacity>
      )}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {result && (
        <View style={styles.box} accessibilityLiveRegion="polite">
          <Text style={[styles.message, result.on_page === false && styles.error]}>{result.message}</Text>
          {result.printed_text ? <Text style={styles.muted}>AI-reported context: “{result.printed_text}”</Text> : null}
          <View style={styles.actions}>
            {offer && (
              <TouchableOpacity accessibilityRole="button" style={styles.use}
                onPress={() => { onUse(result.found as string); setResult(null); }}>
                <Text style={styles.useText}>Use this</Text>
              </TouchableOpacity>
            )}
            <TouchableOpacity accessibilityRole="button" style={styles.link} onPress={() => setResult(null)}>
              <Text style={styles.mutedLink}>{offer ? 'Keep mine' : 'Close'}</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginTop: -spacing.sm, marginBottom: spacing.md },
  link: { minHeight: 32, justifyContent: 'center', alignSelf: 'flex-start' },
  linkText: { ...font.caption, color: colors.primary, fontWeight: '600' },
  box: { backgroundColor: colors.surfaceAlt, borderRadius: radius.md, padding: spacing.sm },
  message: { ...font.caption, color: colors.text },
  muted: { ...font.caption, color: colors.textMuted, marginTop: spacing.xs },
  mutedLink: { ...font.caption, color: colors.textMuted },
  error: { ...font.caption, color: colors.danger },
  actions: { flexDirection: 'row', gap: spacing.md, alignItems: 'center', marginTop: spacing.xs },
  use: { backgroundColor: colors.primary, borderRadius: radius.pill, paddingHorizontal: spacing.md, minHeight: 32, justifyContent: 'center' },
  useText: { ...font.caption, color: '#FFFFFF', fontWeight: '600' },
});
