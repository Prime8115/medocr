import { useState, useEffect } from 'react';
import { Search, Filter, Eye, CheckCircle } from 'lucide-react';
import axios from 'axios';

const API_URL = 'http://127.0.0.1:8000/v1/documents';

export default function ReviewQueue() {
  const [documents, setDocuments] = useState<any[]>([]);

  // Since our simple MVP backend doesn't have a list endpoint yet, we mock the list
  useEffect(() => {
    setDocuments([
      { id: 'doc_1a2b3c', patient: 'Ramesh Kumar', date: '2026-07-10', status: 'processing', confidence: 0.88 },
      { id: 'doc_9f8e7d', patient: 'Anita Sharma', date: '2026-07-10', status: 'approved', confidence: 0.95 },
      { id: 'doc_5x6y7z', patient: 'Unknown', date: '2026-07-09', status: 'processing', confidence: 0.72 }
    ]);
  }, []);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
        <div>
          <h1>Review Queue</h1>
          <div className="text-muted">Manage and approve extracted prescriptions before sync.</div>
        </div>
        <div style={{ display: 'flex', gap: '16px' }}>
          <button className="btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Filter size={16} /> Filter
          </button>
        </div>
      </div>

      <div className="glass-card" style={{ padding: '0' }}>
        <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-glass)', display: 'flex', alignItems: 'center', gap: '16px' }}>
          <Search size={18} className="text-muted" />
          <input type="text" placeholder="Search by patient or document ID..." style={{ background: 'transparent', border: 'none', color: 'white', outline: 'none', flex: 1, fontSize: '15px' }} />
        </div>
        
        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-glass)', color: 'var(--text-secondary)' }}>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Document ID</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Patient Name</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Date Scanned</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Confidence</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Status</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {documents.map(doc => (
              <tr key={doc.id} style={{ borderBottom: '1px solid var(--border-glass)' }}>
                <td style={{ padding: '16px 24px', fontFamily: 'monospace' }}>{doc.id}</td>
                <td style={{ padding: '16px 24px' }}>{doc.patient}</td>
                <td style={{ padding: '16px 24px' }} className="text-muted">{doc.date}</td>
                <td style={{ padding: '16px 24px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <div style={{ width: '60px', height: '6px', background: 'rgba(255,255,255,0.1)', borderRadius: '3px', overflow: 'hidden' }}>
                      <div style={{ width: `${doc.confidence * 100}%`, height: '100%', background: doc.confidence > 0.9 ? 'var(--success)' : 'var(--warning)' }} />
                    </div>
                    <span className="text-muted" style={{ fontSize: '12px' }}>{Math.round(doc.confidence * 100)}%</span>
                  </div>
                </td>
                <td style={{ padding: '16px 24px' }}>
                  <span className={`badge ${doc.status === 'approved' ? 'badge-approved' : 'badge-processing'}`}>
                    {doc.status}
                  </span>
                </td>
                <td style={{ padding: '16px 24px' }}>
                  <button className="btn-secondary" style={{ padding: '6px 12px', fontSize: '13px' }}>
                    Review
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
