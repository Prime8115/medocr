import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';

import { useAuth } from '@/src/auth/AuthContext';
import {
  Connector,
  ConnectorType,
  createConnector,
  deleteConnector,
  listConnectors,
  testConnector,
} from '@/src/api/connectors';
import { Badge, Button, Card, Screen, SectionTitle } from '@/src/theme/components';
import { colors, font, radius, spacing } from '@/src/theme/tokens';

const TYPE_INFO: Record<ConnectorType, { label: string; help: string }> = {
  webhook: {
    label: 'Webhook (HTTP)',
    help: 'If your billing software can receive data at a web address (API).',
  },
  file_export: {
    label: 'File export (CSV/JSON)',
    help: 'If your software imports files from a folder.',
  },
  desktop_agent: {
    label: 'Desktop agent (Windows)',
    help: 'For older software with no API — a small helper on your shop PC writes the data in.',
  },
};

export default function SettingsScreen() {
  const { user, signOut } = useAuth();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [type, setType] = useState<ConnectorType>('webhook');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [saving, setSaving] = useState(false);
  const [testMsg, setTestMsg] = useState<Record<string, string>>({});

  const isOwner = user?.role === 'owner';

  const load = useCallback(async () => {
    try {
      setConnectors(await listConnectors());
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  async function add() {
    if (!name.trim()) {
      Alert.alert('Please enter a name');
      return;
    }
    setSaving(true);
    try {
      const config: Record<string, unknown> = {};
      if (type === 'webhook') config.url = url.trim();
      await createConnector({ type, name: name.trim(), config, secret: type === 'webhook' ? secret.trim() || undefined : undefined });
      setShowForm(false);
      setName('');
      setUrl('');
      setSecret('');
      await load();
    } catch {
      Alert.alert('Could not create connection', 'Only the shop owner can add connections.');
    } finally {
      setSaving(false);
    }
  }

  async function runTest(id: string) {
    setTestMsg((p) => ({ ...p, [id]: 'Testing…' }));
    try {
      const r = await testConnector(id);
      setTestMsg((p) => ({ ...p, [id]: `${r.ok ? '✓' : '✗'} ${r.status}${r.response_body ? ` — ${r.response_body}` : ''}` }));
    } catch {
      setTestMsg((p) => ({ ...p, [id]: '✗ test failed' }));
    }
  }

  function confirmDelete(c: Connector) {
    Alert.alert('Remove connection?', c.name, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: async () => {
          try {
            await deleteConnector(c.id);
            await load();
          } catch {
            Alert.alert('Could not remove');
          }
        },
      },
    ]);
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Profile */}
        <Card>
          <SectionTitle>Account</SectionTitle>
          <Text style={styles.email}>{user?.email}</Text>
          <View style={{ flexDirection: 'row', marginTop: spacing.sm }}>
            <Badge label={user?.role === 'owner' ? 'Owner' : 'Staff'} tone="info" />
          </View>
          <Button title="Log out" variant="danger" onPress={signOut} style={{ marginTop: spacing.lg }} />
        </Card>

        {/* Connections */}
        <Card>
          <View style={styles.rowBetween}>
            <SectionTitle>Send data to your software</SectionTitle>
            {isOwner && (
              <TouchableOpacity onPress={() => setShowForm((s) => !s)}>
                <Text style={styles.addLink}>{showForm ? 'Close' : '+ Add'}</Text>
              </TouchableOpacity>
            )}
          </View>
          <Text style={styles.help}>
            Connections decide where approved prescriptions/invoices are sent — into your existing
            billing or inventory software.
          </Text>

          {!isOwner && (
            <Text style={styles.mutedNote}>Only the shop owner can manage connections.</Text>
          )}

          {loading ? (
            <Text style={styles.mutedNote}>Loading…</Text>
          ) : connectors.length === 0 ? (
            <Text style={styles.mutedNote}>No connections yet.</Text>
          ) : (
            connectors.map((c) => (
              <View key={c.id} style={styles.connItem}>
                <View style={styles.rowBetween}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.connName}>{c.name}</Text>
                    <Text style={styles.connType}>{TYPE_INFO[c.type].label}</Text>
                    {c.type === 'desktop_agent' && c.config.pairing_code != null && (
                      <Text style={styles.pairing}>Pairing code: {String(c.config.pairing_code)}</Text>
                    )}
                  </View>
                  <View style={{ flexDirection: 'row', gap: spacing.sm }}>
                    <TouchableOpacity style={styles.smallBtn} onPress={() => runTest(c.id)}>
                      <Text style={styles.smallBtnText}>Test</Text>
                    </TouchableOpacity>
                    {isOwner && (
                      <TouchableOpacity style={styles.smallBtnDanger} onPress={() => confirmDelete(c)}>
                        <Text style={styles.smallBtnDangerText}>Remove</Text>
                      </TouchableOpacity>
                    )}
                  </View>
                </View>
                {testMsg[c.id] && <Text style={styles.testMsg}>{testMsg[c.id]}</Text>}
              </View>
            ))
          )}

          {showForm && isOwner && (
            <View style={styles.form}>
              <Text style={styles.fieldLabel}>Type</Text>
              <View style={styles.typeChips}>
                {(Object.keys(TYPE_INFO) as ConnectorType[]).map((t) => (
                  <TouchableOpacity
                    key={t}
                    onPress={() => setType(t)}
                    style={[styles.typeChip, type === t && styles.typeChipActive]}
                  >
                    <Text style={[styles.typeChipText, type === t && styles.typeChipTextActive]}>
                      {TYPE_INFO[t].label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
              <Text style={styles.help}>{TYPE_INFO[type].help}</Text>

              <Text style={styles.fieldLabel}>Name</Text>
              <TextInput style={styles.input} value={name} onChangeText={setName} placeholder="e.g. My billing software" placeholderTextColor={colors.textMuted} />

              {type === 'webhook' && (
                <>
                  <Text style={styles.fieldLabel}>Endpoint URL</Text>
                  <TextInput style={styles.input} value={url} onChangeText={setUrl} autoCapitalize="none" placeholder="https://…" placeholderTextColor={colors.textMuted} />
                  <Text style={styles.fieldLabel}>Signing secret (optional)</Text>
                  <TextInput style={styles.input} value={secret} onChangeText={setSecret} placeholder="Shared secret" placeholderTextColor={colors.textMuted} />
                </>
              )}
              {type === 'desktop_agent' && (
                <Text style={styles.mutedNote}>After creating, a pairing code appears here to link your shop PC's helper app.</Text>
              )}

              <Button title={saving ? 'Saving…' : 'Create connection'} onPress={add} loading={saving} style={{ marginTop: spacing.md }} />
            </View>
          )}
        </Card>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg },
  email: { ...font.h3, color: colors.text },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  addLink: { ...font.h3, color: colors.primary },
  help: { ...font.caption, color: colors.textSecondary, marginTop: spacing.xs, marginBottom: spacing.sm },
  mutedNote: { ...font.body, color: colors.textMuted, marginTop: spacing.sm },
  connItem: { borderTopWidth: 1, borderTopColor: colors.border, paddingVertical: spacing.md },
  connName: { ...font.h3, color: colors.text },
  connType: { ...font.caption, color: colors.textSecondary, marginTop: 2 },
  pairing: { ...font.caption, color: colors.primaryDark, marginTop: spacing.xs, fontWeight: '600' },
  smallBtn: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.md, backgroundColor: colors.surfaceAlt },
  smallBtnText: { ...font.label, color: colors.primary },
  smallBtnDanger: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.md, backgroundColor: colors.dangerTint },
  smallBtnDangerText: { ...font.label, color: colors.danger },
  testMsg: { ...font.caption, color: colors.textSecondary, marginTop: spacing.sm, fontFamily: 'monospace' },
  form: { marginTop: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.md },
  fieldLabel: { ...font.label, color: colors.textSecondary, marginTop: spacing.md, marginBottom: spacing.xs },
  input: { minHeight: 44, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: spacing.md, ...font.body, color: colors.text, backgroundColor: colors.surface },
  typeChips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  typeChip: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border },
  typeChipActive: { backgroundColor: colors.primaryTint, borderColor: colors.primary },
  typeChipText: { ...font.caption, color: colors.textSecondary },
  typeChipTextActive: { color: colors.primaryDark, fontWeight: '600' },
});
