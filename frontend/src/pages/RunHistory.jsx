import { useCallback, useMemo, useState } from 'react';
import { errorMessage, getRuns, triggerForecast } from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatDate, formatDateTime, formatNumber } from '../utils/format';

const EMPTY_RUNS = [];

function StatusPill({ status }) {
  const normalized = String(status || 'unknown').toLowerCase().replaceAll('_', '-');
  return <span className={`status-pill status-${normalized}`}>{status || 'Unknown'}</span>;
}

export default function RunHistory() {
  const [search, setSearch] = useState('');
  const [triggering, setTriggering] = useState(false);
  const [message, setMessage] = useState('');

  const requestRuns = useCallback(async (signal) => {
    const response = await getRuns(100, { signal });
    if (!Array.isArray(response)) throw new Error('The API returned an unexpected forecast run list.');
    return response;
  }, []);
  const { data: runsData, loading, error, reload: refreshRuns } = useApiResource(requestRuns);
  const runs = runsData ?? EMPTY_RUNS;

  const filteredRuns = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return runs;
    return runs.filter((run) => [
      run.forecast_run_id,
      run.model_version,
      run.status,
      run.triggered_by,
      run.forecast_origin,
    ].some((value) => String(value ?? '').toLowerCase().includes(query)));
  }, [runs, search]);

  const handleTrigger = async () => {
    setTriggering(true);
    setMessage('');
    try {
      await triggerForecast('Manual request from forecast run history');
      setMessage('Forecast request accepted. Refresh the list to check run progress.');
    } catch (requestError) {
      setMessage(errorMessage(requestError));
    } finally {
      setTriggering(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Forecast run history</h1>
          <p>Execution records reported by the forecasting service. A queued request may take time to complete.</p>
        </div>
        <div className="flex items-center gap-12">
          <button className="btn btn-secondary" type="button" onClick={refreshRuns} disabled={loading}>
            Refresh
          </button>
          <button className="btn btn-primary" type="button" onClick={handleTrigger} disabled={triggering}>
            {triggering ? 'Submitting…' : 'Request forecast'}
          </button>
        </div>
      </div>

      <div className="page-body">
        {message && <div className={`notice ${message.startsWith('Forecast request') ? 'success' : 'error'} mb-20`} role="status">{message}</div>}
        <div className="notice warning mb-20">
          A manual forecast request runs the backend pipeline and writes forecast records. It does not retrain the model.
        </div>
        <label className="search-box" htmlFor="run-search">
          <span aria-hidden="true">⌕</span>
          <input
            id="run-search"
            type="search"
            placeholder="Search run ID, model version, status, or trigger…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <span className="inline-message">{filteredRuns.length} runs</span>
        </label>

        {loading ? (
          <LoadingState label="Loading forecast runs…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refreshRuns} />
        ) : filteredRuns.length === 0 ? (
          <EmptyState title={runs.length ? 'No matching runs' : 'No forecast runs'}>
            {runs.length ? 'Try a different search term.' : 'The API did not return any forecast runs.'}
          </EmptyState>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Model version</th>
                  <th>Status</th>
                  <th>Triggered by</th>
                  <th>Forecast origin</th>
                  <th>Target range</th>
                  <th>Rows</th>
                  <th>Started</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {filteredRuns.map((run) => (
                  <tr key={run.forecast_run_id}>
                    <td><span className="mono" title={run.forecast_run_id}>{String(run.forecast_run_id || '—').slice(0, 14)}{run.forecast_run_id ? '…' : ''}</span></td>
                    <td><span className="mono">{run.model_version || '—'}</span></td>
                    <td><StatusPill status={run.status} /></td>
                    <td>{run.triggered_by || '—'}</td>
                    <td>{formatDate(run.forecast_origin)}</td>
                    <td>{run.target_date_from || '—'}{run.target_date_to ? ` – ${run.target_date_to}` : ''}</td>
                    <td>{formatNumber(run.rows_written)}</td>
                    <td>{formatDateTime(run.run_started_at)}</td>
                    <td>{formatDateTime(run.run_completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
