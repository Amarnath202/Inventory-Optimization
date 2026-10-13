// src/App.jsx
import { useState } from 'react';
import Dashboard   from './pages/Dashboard';
import Products    from './pages/Products';
import RunHistory  from './pages/RunHistory';
import Metrics     from './pages/Metrics';
import './index.css';

const PAGES = [
  { id: 'dashboard', label: 'Dashboard',    icon: '📊' },
  { id: 'products',  label: 'Products',     icon: '📦' },
  { id: 'runs',      label: 'Run History',  icon: '🕐' },
  { id: 'metrics',   label: 'Evaluation',   icon: '📈' },
];

export default function App() {
  const [page, setPage] = useState('dashboard');

  const pageComponent = {
    dashboard: <Dashboard />,
    products:  <Products />,
    runs:      <RunHistory />,
    metrics:   <Metrics />,
  }[page];

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h2>Demand Forecast</h2>
          <p>Module 2 · Analytics</p>
        </div>

        <span className="nav-section-label">Navigation</span>
        {PAGES.map(p => (
          <button
            key={p.id}
            className={`nav-item${page === p.id ? ' active' : ''}`}
            onClick={() => setPage(p.id)}
          >
            <span className="nav-icon">{p.icon}</span>
            {p.label}
          </button>
        ))}

        <div style={{ marginTop: 'auto', padding: '20px', borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Inventory Optimization<br />
            Final Year Project · Module 2
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="main-content">
        {pageComponent}
      </main>
    </div>
  );
}
