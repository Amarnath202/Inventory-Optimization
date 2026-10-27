import { useCallback, useMemo, useState } from 'react';
import LazyPlot from '../components/LazyPlot';
import { errorMessage, getOverview, getTopDemand, triggerForecast } from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatDate, formatDateTime, formatNumber } from '../utils/format';

const PLOT_CONFIG = { displayModeBar: false, responsive: true };
const EMPTY_PRODUCTS = [];
const PLOT_LAYOUT = {
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  font: { color: '#66778b', family: 'Inter, sans-serif', size: 11 },
  margin: { t: 12, b: 42, l: 54, r: 16 },
  xaxis: { gridcolor: '#edf1f4', linecolor: '#dce4e9' },
  yaxis: { gridcolor: '#edf1f4', linecolor: '#dce4e9' },
};

function StatCard({ label, value, sub, color = 'blue' }) {
  return (
    <article className={`stat-card ${color}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </article>
  );
}

export default function Dashboard() {
  const [triggering, setTriggering] = useState(false);
  const [triggerMessage, setTriggerMessage] = useState('');

  const requestDashboard = useCallback(async (signal) => {
    const [overview, topData] = await Promise.all([
      getOverview({ signal }),
      getTopDemand(15, undefined, { signal }),
    ]);
    if (!overview || typeof overview !== 'object' || !Array.isArray(topData)) {
      throw new Error('The API returned an unexpected dashboard response.');
    }
    return { overview, topData };
  }, []);
  const { data: dashboard, loading, error, reload: refreshDashboard } = useApiResource(requestDashboard);
  const overview = dashboard?.overview;
  const topData = dashboard?.topData ?? EMPTY_PRODUCTS;

  const categoryData = useMemo(() => {
    const totals = new Map();
    topData.forEach((product) => {
      if (typeof product.category !== 'string' || !product.category.trim()) return;
      const value = Number(product.total_forecast);
      if (Number.isFinite(value)) totals.set(product.category, (totals.get(product.category) || 0) + value);
    });
    const entries = [...totals.entries()];
    if (!entries.length) return [];
    return [{
      type: 'pie',
      labels: entries.map(([category]) => category),
      values: entries.map(([, total]) => total),
      hole: 0.62,
      textinfo: 'label+percent',
      marker: { colors: ['#418c78', '#6f9cbb', '#a88ab9', '#d2a35f', '#7ba47d', '#a97971'] },
      hovertemplate: '<b>%{label}</b><br>Forecast: %{value:.1f} units<extra></extra>',
    }];
  }, [topData]);

  const forecastBars = useMemo(() => [{
    type: 'bar',
    orientation: 'h',
    x: topData.map((product) => Number(product.total_forecast) || 0),
    y: topData.map((product) => product.product_name || String(product.product_id)),
    marker: { color: '#4d947c' },
    hovertemplate: '<b>%{y}</b><br>Forecast: %{x:.1f} units<extra></extra>',
  }], [topData]);

  const handleTrigger = async () => {
    setTriggering(true);
    setTriggerMessage('');
    try {
      await triggerForecast('Manual trigger from dashboard');
      setTriggerMessage('Forecast request accepted. Check run history for completion.');
    } catch (requestError) {
      setTriggerMessage(errorMessage(requestError));
    } finally {
      setTriggering(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Demand forecast</h1>
          <p>Forecast summary from the latest completed backend run; the returned dates do not guarantee live input data.</p>
        </div>
        <button className="btn btn-primary" type="button" onClick={handleTrigger} disabled={triggering}>
          {triggering ? 'Submitting…' : 'Run forecast'}
        </button>
      </div>

      <div className="page-body">
        {triggerMessage && <div className={`notice ${triggering ? '' : triggerMessage.startsWith('Forecast request') ? 'success' : 'error'} mb-20`} role="status">{triggerMessage}</div>}
        {loading ? (
          <LoadingState label="Loading forecast overview…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refreshDashboard} />
        ) : !overview || overview.error ? (
          <EmptyState title="No completed forecast run">
            {overview?.error || 'A completed forecast is not available in the connected database.'}
          </EmptyState>
        ) : (
          <>
            <div className="notice warning mb-20">
              This view reflects the saved forecast run shown below. Confirm the forecast origin and source-data freshness before using recommendations operationally.
            </div>
            <div className="stats-grid">
              <StatCard label="Eligible products" value={formatNumber(overview.eligible_products)} sub="In the latest forecast run" />
              <StatCard label="Forecast horizon" value={overview.forecast_horizon_days ? `${formatNumber(overview.forecast_horizon_days)} days` : '—'} sub={`${formatDate(overview.target_date_from)} – ${formatDate(overview.target_date_to)}`} color="cyan" />
              <StatCard label="Forecast demand" value={formatNumber(overview.total_forecasted_demand)} sub="Total units across forecast horizon" color="green" />
              <StatCard
                label="Avg. daily demand"
                value={Number(overview.eligible_products) > 0 && Number(overview.forecast_horizon_days) > 0 && Number.isFinite(Number(overview.total_forecasted_demand))
                  ? formatNumber(Number(overview.total_forecasted_demand) / Number(overview.forecast_horizon_days) / Number(overview.eligible_products), 2)
                  : '—'}
                sub="Per product, calculated from run totals"
                color="purple"
              />
            </div>

            <div className="chart-grid">
              <section className="card">
                <h2 className="card-title">Top products by forecast demand</h2>
                {topData.length ? (
                  <LazyPlot
                    data={forecastBars}
                    layout={{
                      ...PLOT_LAYOUT,
                      height: 340,
                      margin: { t: 8, b: 40, l: 170, r: 20 },
                      xaxis: { ...PLOT_LAYOUT.xaxis, title: 'Forecast units (7-day)' },
                      yaxis: { ...PLOT_LAYOUT.yaxis, automargin: true },
                    }}
                    config={PLOT_CONFIG}
                    style={{ width: '100%' }}
                  />
                ) : <EmptyState title="No forecast products" />}
              </section>
              <section className="card">
                <h2 className="card-title">Category mix in top forecast products</h2>
                {categoryData.length ? (
                  <LazyPlot
                    data={categoryData}
                    layout={{
                      ...PLOT_LAYOUT,
                      height: 340,
                      margin: { t: 12, b: 12, l: 12, r: 12 },
                      showlegend: true,
                      legend: { orientation: 'h', y: -0.08, font: { size: 10 } },
                    }}
                    config={PLOT_CONFIG}
                    style={{ width: '100%' }}
                  />
                ) : <EmptyState title="Category data unavailable" />}
              </section>
            </div>

            <section className="card">
              <h2 className="card-title">Latest forecast run</h2>
              <div className="run-summary-grid">
                {[
                  ['Run ID', overview.forecast_run_id],
                  ['Model version', overview.model_version],
                  ['Rows written', formatNumber(overview.rows_written)],
                  ['Forecast origin', formatDate(overview.forecast_origin)],
                  ['Completed', formatDateTime(overview.run_completed_at)],
                  ['Demand scope', overview.demand_aggregation_scope],
                ].map(([label, value]) => (
                  <div key={label}>
                    <div className="stat-label">{label}</div>
                    <div className="summary-value">{value ?? '—'}</div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </>
  );
}
