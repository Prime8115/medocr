import { BrowserRouter as Router, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { Activity, Settings as SettingsIcon, FileText } from 'lucide-react';
import './index.css';

import ReviewQueue from './components/ReviewQueue';
import Settings from './components/Settings';

function App() {
  return (
    <Router>
      <div className="app-container">
        {/* Sidebar */}
        <aside className="sidebar glass-panel">
          <div style={{ padding: '0 16px', marginBottom: '24px' }}>
            <h2 style={{ margin: 0, color: 'var(--primary-color)' }}>MediScan<span style={{color: 'white'}}>OCR</span></h2>
            <div className="text-muted" style={{ fontSize: '12px' }}>Admin Console</div>
          </div>
          
          <NavLink to="/queue" className={({ isActive }) => isActive ? "sidebar-link active" : "sidebar-link"}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <FileText size={18} /> Review Queue
            </div>
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => isActive ? "sidebar-link active" : "sidebar-link"}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <SettingsIcon size={18} /> Integration Settings
            </div>
          </NavLink>
        </aside>

        {/* Main Content */}
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Navigate to="/queue" replace />} />
            <Route path="/queue" element={<ReviewQueue />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
