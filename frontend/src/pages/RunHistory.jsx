// src/pages/RunHistory.jsx
import { useEffect, useState } from 'react';
import { getRuns, triggerForecast } from '../api';

function StatusPill({ status }) {
  return <span className={`status-pill status-${status}`}>{status}</span>;
}

export default function RunHistory() {
  const [runs,       setRuns]       = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [msg,        setMsg]        = useState('');

  const fetchRuns = () => {
    getRuns(50).then(setRuns).catch(console.error).finally(() => setLoading(false));
  };

  useEffect(() => { fetchRuns(); }, []);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      await triggerForecast('Manual catch-up trigger');
      setMsg('✅ Forecast queued. Refreshing…');
      setTimeout(() => { fetchRuns(); setMsg(''); }, 3000);
    } catch {
      setMsg('❌ Trigger failed.');
    } finally {
      setTriggering(false);
    }
  };

  const fmt = (ts) => ts ? ts.slice(0, 19).replace('T', ' ') : '—';

  return (
    <div>
      <div className="page-header">
        <div className="flex items-center gap-12">
          <div>
            <h1>Forecast Run History</h1>
            <p>All forecast executions. Each run has a unique UUID (forecast_run_id) separate from model_version.</p>
          </div>
          <div className="ml-auto flex gap-12 items-center">
            {msg && <span style={{ fontSize: 12, color: 'var(--accent-green)' }}>{msg}</span>}
            <button className="btn btn-secondary" onClick={fetchRuns}>↻ Refresh</button>
            <button className="btn btn-primary" onClick={handleTrigger} disabled={triggering}>
              {triggering ? '⏳ Queuing…' : '▶ Trigger Forecast'}
            </button>
          </div>
        </div>
      </div>

      <div className="page-body">
        <div style={{ background: 'rgba(251,146,60,0.08)', border: '1px solid rgba(251,146,60,0.2)', borderRadius: 10, padding: '12px 16px', marginBottom: 20, fontSize: 12.5, color: 'var(--accent-orange)' }}>
          <strong>Manual recovery note:</strong> Automatic catch-up after missed runs is not enabled.
          Use "Trigger Forecast" to manually generate forecasts. Retrain first (<code>python src/train.py</code>) if the model may be stale.
        </div>

        {loading ? (
          <div className="loading-center"><div className="spinner" /></div>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <h3>No forecast runs yet</h3>
            <p>Trigger a forecast run or wait for the weekly scheduler.</p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Model Version</th>
                  <th>Status</th>
                  <th>Triggered By</th>
                  <th>Forecast Origin</th>
                  <th>Target Range</th>
                  <th>Rows</th>
                  <th>Started At</th>
                  <th>Completed At</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.forecast_run_id}>
                    <td><span className="mono" title={r.forecast_run_id}>{String(r.forecast_run_id).slice(0, 14)}…</span></td>
                    <td><span className="mono">{r.model_version}</span></td>
                    <td><StatusPill status={r.status} /></td>
                    <td><span className="badge badge-blue">{r.triggered_by}</span></td>
                    <td className="mono">{r.forecast_origin}</td>
                    <td className="mono">{r.target_date_from} → {r.target_date_to}</td>
                    <td>{r.rows_written?.toLocaleString() ?? '—'}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{fmt(r.run_started_at)}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{fmt(r.run_completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
