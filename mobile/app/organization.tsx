import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';

import { useAuth } from '@/src/auth/AuthContext';
import { useQueue } from '@/src/queue/QueueContext';
import { addUser, addWorkspace, getOrg, listUsers, listWorkspaces, reason, updateUser,
  type OrgSummary, type OrgUser, type Workspace } from '@/src/api/org';
import { Button, Card, Screen, SectionTitle } from '@/src/theme/components';
import { colors, font, radius, spacing } from '@/src/theme/tokens';

export default function OrganizationScreen() {
  const router = useRouter();
  const { user, switchWorkspace } = useAuth();
  const { pending } = useQueue();
  const [org, setOrg] = useState<OrgSummary | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [members, setMembers] = useState<OrgUser[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [grants, setGrants] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const owner = user?.role === 'owner';

  const load = useCallback(async () => {
    try {
      const [summary, spaces, users] = await Promise.all([
        getOrg(), listWorkspaces(), owner ? listUsers() : Promise.resolve([]),
      ]);
      setOrg(summary);
      setWorkspaces(spaces);
      setMembers(users);
      setGrants((old) => old.length ? old : [user?.shop_id ?? spaces[0]?.id].filter(Boolean) as string[]);
      setError('');
    } catch (e) { setError(reason(e)); }
  }, [owner, user?.shop_id]);
  useFocusEffect(useCallback(() => { void load(); }, [load]));

  async function run(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); await load(); }
    catch (e) { setError(reason(e)); }
    finally { setBusy(false); }
  }

  async function chooseWorkspace(id: string) {
    if (id === user?.shop_id) return;
    // Old queue entries have no workspace ID. Never send them to a newly
    // selected workspace while offline uploads are pending.
    if (pending > 0) {
      Alert.alert('Finish pending uploads', 'Wait for queued uploads to finish before switching workspaces.');
      return;
    }
    await run(async () => { await switchWorkspace(id); router.replace('/(tabs)/history'); });
  }

  function toggleGrant(id: string) {
    setGrants((current) => current.includes(id) ? current.filter((x) => x !== id) : [...current, id]);
  }

  if (!org) return <Screen style={styles.content}><Text style={styles.note}>{error || 'Loading organization…'}</Text></Screen>;

  return <Screen><ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
    {error ? <Text style={styles.error}>{error}</Text> : null}
    <Card>
      <SectionTitle>Organization</SectionTitle>
      <Text style={styles.title}>{org.name}</Text>
      <Text style={styles.note}>{org.plan_label} · {org.seats_used} / {org.seats_allowed} licensed seats</Text>
      <Text style={styles.note}>One user occupies one seat across all assigned workspaces.</Text>
    </Card>
    <Card>
      <SectionTitle>Workspaces</SectionTitle>
      <Text style={styles.note}>Invoices, supplier learning and connectors belong to the selected workspace.</Text>
      {workspaces.map((space) => <TouchableOpacity key={space.id} style={styles.row}
        disabled={busy || space.id === user?.shop_id} onPress={() => void chooseWorkspace(space.id)}>
        <Text style={styles.label}>{space.name}</Text>
        <Text style={styles.note}>{space.id === user?.shop_id ? 'Current' : 'Switch'}</Text>
      </TouchableOpacity>)}
      {owner ? <>
        <TextInput style={styles.input} placeholder="New workspace name" placeholderTextColor={colors.textMuted}
          value={name} onChangeText={setName} maxLength={255} />
        <Button title="Create workspace" variant="secondary" disabled={busy || !name.trim() || org.workspaces_used >= org.workspaces_allowed}
          onPress={() => void run(async () => { await addWorkspace(name.trim()); setName(''); })} />
      </> : null}
    </Card>
    {owner ? <>
      <Card>
        <SectionTitle>Users</SectionTitle>
        {members.map((member) => <View key={member.id} style={styles.member}>
          <Text style={styles.label}>{member.email}</Text>
          <Text style={styles.note}>{member.role === 'owner' ? 'Admin' : 'Staff'} · {member.is_active ? 'Active' : 'Disabled'}</Text>
          <Text style={styles.note}>{member.workspace_ids.map((id) => workspaces.find((s) => s.id === id)?.name ?? id).join(', ')}</Text>
          {member.role === 'staff' ? <>
            <View style={styles.chips}>{workspaces.map((space) => {
              const chosen = member.workspace_ids.includes(space.id);
              const next = chosen ? member.workspace_ids.filter((id) => id !== space.id) : [...member.workspace_ids, space.id];
              return <TouchableOpacity key={space.id} style={[styles.chip, chosen && styles.chipActive]}
                disabled={busy || next.length === 0} onPress={() => void run(() => updateUser(member.id, { workspace_ids: next }))}>
                <Text style={styles.note}>{chosen ? '✓ ' : ''}{space.name}</Text>
              </TouchableOpacity>;
            })}</View>
            <Button title={member.is_active ? 'Disable user' : 'Reactivate user'} variant="secondary" disabled={busy}
              onPress={() => void run(() => updateUser(member.id, { is_active: !member.is_active }))} />
          </> : null}
        </View>)}
      </Card>
      <Card>
        <SectionTitle>Add a user</SectionTitle>
        <Text style={styles.note}>Set a temporary password and share it with the user privately. They can change it after signing in.</Text>
        <TextInput style={styles.input} placeholder="Email" autoCapitalize="none" keyboardType="email-address"
          placeholderTextColor={colors.textMuted} value={email} onChangeText={setEmail} />
        <TextInput style={styles.input} placeholder="Temporary password (8+ characters)" secureTextEntry
          placeholderTextColor={colors.textMuted} value={password} onChangeText={setPassword} />
        <View style={styles.chips}>{workspaces.map((space) => <TouchableOpacity key={space.id}
          style={[styles.chip, grants.includes(space.id) && styles.chipActive]} onPress={() => toggleGrant(space.id)}>
          <Text style={styles.note}>{grants.includes(space.id) ? '✓ ' : ''}{space.name}</Text>
        </TouchableOpacity>)}</View>
        <Button title="Add user" disabled={busy || org.seats_used >= org.seats_allowed || !email.trim() || password.length < 8 || !grants.length}
          onPress={() => void run(async () => { await addUser(email.trim(), password, grants); setEmail(''); setPassword(''); })} />
        {org.seats_used >= org.seats_allowed ? <Text style={styles.note}>All licensed seats are occupied. Contact your MediScan admin to upgrade.</Text> : null}
      </Card>
    </> : null}
  </ScrollView></Screen>;
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg, gap: spacing.md },
  title: { ...font.h2, color: colors.text },
  label: { ...font.h3, color: colors.text },
  note: { ...font.caption, color: colors.textSecondary, marginTop: spacing.xs },
  error: { ...font.body, color: colors.danger },
  row: { borderTopWidth: 1, borderTopColor: colors.border, paddingVertical: spacing.md,
    flexDirection: 'row', justifyContent: 'space-between' },
  member: { borderTopWidth: 1, borderTopColor: colors.border, paddingVertical: spacing.md, gap: spacing.sm },
  input: { minHeight: 44, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md,
    paddingHorizontal: spacing.md, ...font.body, color: colors.text, marginTop: spacing.md },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginVertical: spacing.md },
  chip: { borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border, padding: spacing.sm },
  chipActive: { backgroundColor: colors.primaryTint, borderColor: colors.primary },
});
