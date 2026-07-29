import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';

import { useAuth } from '@/src/auth/AuthContext';
import {
  Connector,
  ConnectorOptions,
  ConnectorType,
  createConnector,
  deleteConnector,
  getConnectorOptions,
  listConnectors,
  testConnector,
} from '@/src/api/connectors';
import { changePassword } from '@/src/api/auth';
import { inventoryCount, syncInventory } from '@/src/api/inventory';

const PROFILE_LABEL: Record<string, string> = {
  generic: 'Generic', marg: 'Marg ERP', vyapar: 'Vyapar', tally: 'Tally',
};
const FORMAT_LABEL: Record<string, string> = {
  csv: 'CSV', json: 'JSON', tally_xml: 'Tally XML', both: 'CSV + JSON',
};
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
  const [profile, setProfile] = useState('generic');
  const [format, setFormat] = useState('csv');
  const [options, setOptions] = useState<ConnectorOptions | null>(null);
  const [saving, setSaving] = useState(false);
  const [testMsg, setTestMsg] = useState<Record<string, string>>({});

  // change password
  const [curPw, setCurPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [pwMsg, setPwMsg] = useState<string | null>(null);

  // inventory
  const [invCount, setInvCount] = useState<number | null>(null);
  const [invUrl, setInvUrl] = useState('');
  const [invMsg, setInvMsg] = useState<string | null>(null);
  const [invSyncing, setInvSyncing] = useState(false);

  const isOwner = user?.role === 'owner';

  async function syncNow() {
    if (!invUrl.trim()) {
      Alert.alert('Enter your inventory API URL');
      return;
    }
    setInvSyncing(true);
    setInvMsg(null);
    try {
      const r = await syncInventory(invUrl.trim());
      setInvMsg(`✓ Synced ${r.imported} items`);
      setInvCount(r.imported);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setInvMsg('✗ ' + (typeof detail === 'string' ? detail : 'Sync failed'));
    } finally {
      setInvSyncing(false);
    }
  }

  const load = useCallback(async () => {
    try {
      const [conns, opts, inv] = await Promise.all([
        listConnectors(),
        getConnectorOptions().catch(() => null),
        inventoryCount().catch(() => null),
      ]);
      setConnectors(conns);
      if (opts) setOptions(opts);
      if (inv) setInvCount(inv.count);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  async function submitPassword() {
    setPwMsg(null);
    try {
      await changePassword(curPw, newPw);
      setPwMsg('✓ Password changed');
      setCurPw('');
      setNewPw('');
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setPwMsg('✗ ' + (typeof detail === 'string' ? detail : 'Could not change password'));
    }
  }

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
      if (type === 'file_export') {
        config.profile = profile;
        config.format = format;
      }
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
              {type === 'file_export' && (
                <>
                  <Text style={styles.fieldLabel}>Your software</Text>
                  <View style={styles.typeChips}>
                    {(options?.profiles ?? ['generic']).map((p) => (
                      <TouchableOpacity key={p} onPress={() => setProfile(p)} style={[styles.typeChip, profile === p && styles.typeChipActive]}>
                        <Text style={[styles.typeChipText, profile === p && styles.typeChipTextActive]}>{PROFILE_LABEL[p] ?? p}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                  <Text style={styles.fieldLabel}>Format</Text>
                  <View style={styles.typeChips}>
                    {(options?.formats ?? ['csv']).map((f) => (
                      <TouchableOpacity key={f} onPress={() => setFormat(f)} style={[styles.typeChip, format === f && styles.typeChipActive]}>
                        <Text style={[styles.typeChipText, format === f && styles.typeChipTextActive]}>{FORMAT_LABEL[f] ?? f}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </>
              )}

              {type === 'desktop_agent' && (
                <Text style={styles.mutedNote}>After creating, a pairing code appears here to link your shop PC's helper app.</Text>
              )}

              <Button title={saving ? 'Saving…' : 'Create connection'} onPress={add} loading={saving} style={{ marginTop: spacing.md }} />
            </View>
          )}
        </Card>

        {/* Inventory */}
        <Card>
          <SectionTitle>Inventory</SectionTitle>
          <Text style={styles.help}>
            Connect your stock so scanned medicines are matched to your inventory and substitutes
            in stock are suggested. Leave it unconnected to use the app normally.
          </Text>
          <View style={{ flexDirection: 'row', marginTop: spacing.xs, marginBottom: spacing.sm }}>
            <Badge
              label={invCount && invCount > 0 ? `${invCount} items connected` : 'Not connected'}
              tone={invCount && invCount > 0 ? 'success' : 'neutral'}
            />
          </View>
          {isOwner ? (
            <>
              <Text style={styles.fieldLabel}>Sync from your software's API</Text>
              <TextInput
                style={styles.input}
                value={invUrl}
                onChangeText={setInvUrl}
                autoCapitalize="none"
                placeholder="https://your-software/api/items"
                placeholderTextColor={colors.textMuted}
              />
              {invMsg && <Text style={styles.testMsg}>{invMsg}</Text>}
              <Button title={invSyncing ? 'Syncing…' : 'Sync now'} onPress={syncNow} loading={invSyncing} style={{ marginTop: spacing.md }} />
              <Text style={[styles.mutedNote, { marginTop: spacing.sm }]}>
                Tip: CSV upload of stock is available in the web Admin console.
              </Text>
            </>
          ) : (
            <Text style={styles.mutedNote}>Only the shop owner can manage inventory.</Text>
          )}
        </Card>

        {/* Change password */}
        <Card>
          <SectionTitle>Change password</SectionTitle>
          <TextInput
            style={[styles.input, { marginTop: spacing.sm }]}
            value={curPw}
            onChangeText={setCurPw}
            placeholder="Current password"
            placeholderTextColor={colors.textMuted}
            secureTextEntry
          />
          <TextInput
            style={[styles.input, { marginTop: spacing.sm }]}
            value={newPw}
            onChangeText={setNewPw}
            placeholder="New password (min 8)"
            placeholderTextColor={colors.textMuted}
            secureTextEntry
          />
          {pwMsg && <Text style={styles.testMsg}>{pwMsg}</Text>}
          <Button title="Update password" onPress={submitPassword} style={{ marginTop: spacing.md }} />
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
