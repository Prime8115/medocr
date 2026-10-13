/**
 * The owner's consent for this shop's corrections to improve the reader for
 * every shop - collected privately, turned into general rules, and tested
 * against the private invoice set. Off until the owner turns it on.
 */
import { useEffect, useState } from 'react';

import { getShop, setShareCorrections } from '../api/auth';

export default function ShareCorrections() {
  const [share, setShare] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getShop()
      .then((s) => setShare(Boolean(s.share_corrections)))
      .catch(() => setError('Could not load this setting.'));
  }, []);

  async function toggle(next: boolean) {
    setError(null);
    try {
      setShare(await setShareCorrections(next));
    } catch {
      setError('Only the shop owner can change this.');
    }
  }

  if (share === null && !error) return null;
  return (
    <div className="glass-card" style={{ marginTop: 24 }}>
      <h3 style={{ marginTop: 0 }}>Help improve reading for every shop</h3>
      <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={Boolean(share)}
          onChange={(e) => toggle(e.target.checked)}
          aria-label="Share corrections"
        />
        <span>
          Use this shop's corrections to improve how every shop's bills are read. Corrections stay private;
          a pattern seen across several suppliers becomes a general rule.
        </span>
      </label>
      {error && <div style={{ color: 'var(--danger)', marginTop: 8 }}>{error}</div>}
    </div>
  );
}
