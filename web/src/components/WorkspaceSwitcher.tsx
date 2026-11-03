import { useState } from 'react';

import { switchWorkspace, type User } from '../api/auth';
import { reason } from '../api/org';

/**
 * The organisation and the workspace the user is in. With more than one
 * workspace to open, a pick switches - and every screen follows (it reloads).
 */
export default function WorkspaceSwitcher({ user, onSwitched }: { user: User; onSwitched?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spaces = user.workspaces ?? [];

  async function pick(id: string) {
    if (id === user.shop_id) return;
    setBusy(true);
    setError(null);
    try {
      await switchWorkspace(id);
      if (onSwitched) onSwitched();
      else window.location.reload();
    } catch (e) {
      setError(reason(e, 'Could not switch workspace.'));
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: '0 16px', marginBottom: 16 }}>
      {user.org_name && <div className="text-muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>{user.org_name}</div>}
      {spaces.length > 1 ? (
        <select className="field-input" aria-label="Workspace" value={user.shop_id} disabled={busy}
          onChange={(e) => pick(e.target.value)} style={{ width: '100%', marginTop: 4 }}>
          {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      ) : (
        <div style={{ fontSize: 14 }}>{user.workspace_name}</div>
      )}
      {error && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 4 }}>{error}</div>}
    </div>
  );
}
