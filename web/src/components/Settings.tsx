import { useState } from 'react';
import { Save, Link2 } from 'lucide-react';
import axios from 'axios';

const API_URL = 'http://127.0.0.1:8000/v1/webhooks';

export default function Settings() {
  const [webhookUrl, setWebhookUrl] = useState('https://api.customer-system.com/mediscan-ingest');
  const [saving, setSaving] = useState(false);

  const saveSettings = async () => {
    setSaving(true);
    try {
      await axios.post(API_URL, {
        url: webhookUrl,
        events: ['document.approved', 'document.sync_failed']
      });
      alert('Integration settings saved successfully!');
    } catch (e) {
      alert('Error saving settings.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ maxWidth: '800px' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1>Integration Settings</h1>
        <div className="text-muted">Configure where MediScan OCR Connect will push the approved structured data.</div>
      </div>

      <div className="glass-card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '24px' }}>
          <Link2 size={20} color="var(--primary-color)" /> Webhook Push Destination
        </h3>
        
        <div style={{ marginBottom: '20px' }}>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500, color: 'var(--text-secondary)' }}>Endpoint URL</label>
          <input 
            type="text" 
            className="input-field" 
            value={webhookUrl}
            onChange={e => setWebhookUrl(e.target.value)}
            placeholder="https://..."
          />
          <div className="text-muted" style={{ marginTop: '8px' }}>This URL will receive a POST request when a document is approved.</div>
        </div>

        <div style={{ marginBottom: '32px' }}>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500, color: 'var(--text-secondary)' }}>Subscribed Events</label>
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <span className="badge badge-approved">document.approved</span>
            <span className="badge badge-processing" style={{ background: 'rgba(239, 68, 68, 0.2)', color: 'var(--danger)' }}>document.sync_failed</span>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
          <button className="btn-secondary">Test Connection</button>
          <button className="btn-primary" onClick={saveSettings} disabled={saving} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Save size={16} /> {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}
