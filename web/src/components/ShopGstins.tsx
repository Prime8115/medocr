/**
 * The shop's own GSTIN.
 *
 * Every purchase bill names two businesses, and one of them is the shop. Told
 * which, the reader never has to guess who is the supplier and who the buyer -
 * suppliers print the two blocks side by side, under any label or none, and a
 * wrong guess files the purchase under the shop's own GSTIN. It is also learned
 * from bills the shop approves; stating it here makes it right from the first.
 */
import { useEffect, useState } from 'react';
import { Store } from 'lucide-react';

import { getShop, setShopGstins, type ShopProfile } from '../api/auth';

export default function ShopGstins() {
  const [shop, setShop] = useState<ShopProfile | null>(null);
  const [value, setValue] = useState('');
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  useEffect(() => {
    getShop()
      .then((s) => {
        setShop(s);
        setValue((s.stated.length ? s.stated : s.gstins).join(', '));
      })
      .catch(() => setShop(null));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const gstins = value.split(/[\s,]+/).map((g) => g.trim().toUpperCase()).filter(Boolean);
    try {
      const saved = await setShopGstins(gstins);
      setMsg({ text: `Saved: ${saved.join(', ') || 'none'}`, ok: true });
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMsg({ text: detail || 'Could not save.', ok: false });
    }
  }

  if (!shop) return null;
  const learnedOnly = shop.stated.length === 0 && shop.gstins.length > 0;

  return (
    <form onSubmit={save} className="glass-card" style={{ marginBottom: 24 }}>
      <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Store size={18} color="var(--primary-color)" /> Your shop's GSTIN
      </h3>
      <div className="text-muted" style={{ fontSize: 13, marginBottom: 12 }}>
        On every purchase bill the shop is the buyer. Telling us your GSTIN means the supplier and the buyer
        are never mixed up, whatever the supplier's layout.
        {learnedOnly && ' Learned from the bills you approved - confirm it below.'}
        {shop.gstins.length === 0 && ' Not set yet.'}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="27AAAAA0000A1Z5 (more than one: separate with commas)"
          style={{ flex: 1 }}
        />
        <button className="btn-primary" type="submit">Save</button>
      </div>
      {msg && (
        <div style={{ marginTop: 8, fontSize: 13, color: msg.ok ? 'var(--success)' : 'var(--danger)' }}>{msg.text}</div>
      )}
    </form>
  );
}
