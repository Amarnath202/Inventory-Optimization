import { useCallback } from 'react';
import LazyPlot from '../components/LazyPlot';
import { getMetrics } from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatNumber } from '../utils/format';

const PLOT_CONFIG = { displayModeBar: false, responsive: true };
const PLOT_LAYOUT = {
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  font: { color: '#66778b', family: 'Inter, sans-serif', size: 10 },
  margin: { t: 20, b: 42, l: 54, r: 12 },
  xaxis: { gridcolor: '#edf1f4', linecolor: '#dce4e9' },
  yaxis: { gridcolor: '#edf1f4', linecolor: '#dce4e9' },
  barmode: 'group',
};

function MetricTable({ rows, title }) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return (
      <section className="card mb-20">
        <h2 className="card-title">{title}</h2>
        <EmptyState title="No metrics in this split" />
      </section>
    );
  }

  const metricKeys = ['mae', 'rmse', 'wape', 'smape'];
  return (
    <section className="card mb-20">
      <h2 className="card-title">{title}</h2>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>MAE</th>
              <th>RMSE</th>
              <th>WAPE</th>
              <th>sMAPE</th>
              <th>Rows evaluated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.model || 'model'}-${index}`}>
                <td className="text">{row.model || '—'}</td>
                {metricKeys.map((key) => (
                  <td key={key}>
                    {row[key] === null || row[key] === undefined
                      ? '—'
                      : `${formatNumber(Number(row[key]) * (key === 'wape' || key === 'smape' ? 100 : 1), 3)}${key === 'wape' || key === 'smape' ? '%' : ''}`}
                  </td>
                ))}
                <td>{formatNumber(row.n)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function buildErrorChart(rows, title) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const keys = [
    { key: 'mae', color: '#5586ad' },
    { key: 'rmse', color: '#399078' },
  ];
  return {
    data: keys.map(({ key, color }) => ({
      type: 'bar',
      name: key.toUpperCase(),
      x: rows.map((row) => row.model || '—'),
      y: rows.map((row) => row[key] === null || row[key] === undefined ? null : Number(row[key])),
      marker: { color },
      hovertemplate: `%{x}<br>${key.toUpperCase()}: %{y:.4f}<extra></extra>`,
    })),
    layout: {
      ...PLOT_LAYOUT,
      height: 245,
      title: { text: title, font: { size: 12, color: '#52657a' } },
      yaxis: { ...PLOT_LAYOUT.yaxis, title: 'Demand units' },
    },
  };
}

function buildWapeChart(rows, title) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return {
    data: [{
      type: 'bar',
      name: 'WAPE',
      x: rows.map((row) => row.model || '—'),
      y: rows.map((row) => row.wape === null || row.wape === undefined ? null : Number(row.wape) * 100),
      marker: { color: '#64998a' },
      hovertemplate: '%{x}<br>WAPE: %{y:.2f}%<extra></extra>',
    }],
    layout: {
      ...PLOT_LAYOUT,
      height: 220,
      title: { text: title, font: { size: 12, color: '#52657a' } },
      yaxis: { ...PLOT_LAYOUT.yaxis, title: 'WAPE (%)' },
    },
  };
}

export default function Metrics() {
  const requestMetrics = useCallback(async (signal) => {
    const response = await getMetrics({ signal });
    if (!response || typeof response !== 'object' || Array.isArray(response)) {
      throw new Error('The API returned an unexpected metrics response.');
    }
    return response;
  }, []);
  const { data: metrics, loading, error, reload: refreshMetrics } = useApiResource(requestMetrics);

  const validation = Array.isArray(metrics?.val) ? metrics.val : [];
  const test = Array.isArray(metrics?.test) ? metrics.test : [];
  const validationErrors = buildErrorChart(validation, 'Validation split: MAE and RMSE');
  const testErrors = buildErrorChart(test, 'Test split: MAE and RMSE');
  const validationWape = buildWapeChart(validation, 'Validation split: WAPE');
  const testWape = buildWapeChart(test, 'Test split: WAPE');

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Model evaluation</h1>
          <p>Backend-reported validation and test metrics for LightGBM and the moving-average baseline.</p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={refreshMetrics} disabled={loading}>
          Refresh metrics
        </button>
      </div>

      <div className="page-body">
        {loading ? (
          <LoadingState label="Loading evaluation metrics…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refreshMetrics} />
        ) : !metrics || (!validation.length && !test.length) ? (
          <EmptyState title="No evaluation metrics available">
            {metrics?.error || 'The metrics endpoint returned no validation or test results.'}
          </EmptyState>
        ) : (
          <>
            <div className="notice mb-20">
              Validation and test results are kept separate. MAE and RMSE are in demand units; WAPE and sMAPE are displayed as percentages. A missing metric remains unavailable rather than being plotted as zero.
            </div>
            <MetricTable rows={validation} title="Validation split" />
            <MetricTable rows={test} title="Test split" />

            {(validationErrors || testErrors) && (
              <div className="chart-grid">
                {validationErrors && <section className="card"><LazyPlot data={validationErrors.data} layout={validationErrors.layout} config={PLOT_CONFIG} style={{ width: '100%' }} /></section>}
                {testErrors && <section className="card"><LazyPlot data={testErrors.data} layout={testErrors.layout} config={PLOT_CONFIG} style={{ width: '100%' }} /></section>}
              </div>
            )}

            {(validationWape || testWape) && (
              <div className="chart-grid">
                {validationWape && <section className="card"><LazyPlot data={validationWape.data} layout={validationWape.layout} config={PLOT_CONFIG} style={{ width: '100%' }} /></section>}
                {testWape && <section className="card"><LazyPlot data={testWape.data} layout={testWape.layout} config={PLOT_CONFIG} style={{ width: '100%' }} /></section>}
              </div>
            )}

            <section className="card">
              <h2 className="card-title">Metric guide</h2>
              <div className="metric-guide">
                <div><strong>MAE</strong><p>Mean absolute error; the average absolute forecast error in demand units. Lower is better.</p></div>
                <div><strong>RMSE</strong><p>Root mean squared error; larger errors have a stronger effect. Lower is better.</p></div>
                <div><strong>WAPE</strong><p>Sum of absolute errors divided by total actual demand. It is undefined when total actual demand is zero.</p></div>
                <div><strong>sMAPE</strong><p>Symmetric mean absolute percentage error. The API's fractional value is shown as a percentage.</p></div>
              </div>
            </section>
          </>
        )}
      </div>
    </>
  );
}
