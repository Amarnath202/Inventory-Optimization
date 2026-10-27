import { useEffect, useState } from 'react';
import { errorMessage, getOrchestrationStatus, getProducts, runOrchestration } from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { formatDateTime } from '../utils/format';

function JsonPanel({ title, value }) {
  if (value === null || value === undefined) return null;
  return (
    <section className="card mb-20">
      <h2 className="card-title">{title}</h2>
      <pre className="json-output">{JSON.stringify(value, null, 2)}</pre>
    </section>
  );
}

export default function Decisions() {
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState('');
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState('');
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState('');
  const [result, setResult] = useState(null);

  useEffect(() => {
    let active = true;
    Promise.all([getOrchestrationStatus(), getProducts()])
      .then(([statusResponse, productResponse]) => {
        if (!statusResponse || !Array.isArray(productResponse)) throw new Error('The API returned an unexpected workflow response.');
        if (active) {
          setStatus(statusResponse);
          setProducts(productResponse);
          if (productResponse.length) setSelectedProduct(String(productResponse[0].product_id));
        }
      })
      .catch((requestError) => { if (active) setStatusError(errorMessage(requestError)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const executeWorkflow = async () => {
    if (!selectedProduct) return;
    const confirmed = window.confirm(
      `Run inventory assessment and conditional advisory routing for ${selectedProduct}? This writes assessment/recommendation records, but never changes stock or places an order.`,
    );
    if (!confirmed) return;
    setRunning(true);
    setRunError('');
    setResult(null);
    try {
      const response = await runOrchestration(selectedProduct, 'manual');
      if (!response || typeof response !== 'object' || !('manager_action_required' in response)) {
        throw new Error('The API returned an unexpected orchestration response.');
      }
      setResult(response);
    } catch (requestError) {
      setRunError(errorMessage(requestError));
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Decision workflow</h1>
          <p>Run the backend's Module 3 assessment and its conditional procurement or excess-stock handoff for one product.</p>
        </div>
      </div>

      <div className="page-body">
        <div className="notice warning mb-20">
          This workflow persists assessment/recommendation records and requires a configured database. It does not submit purchasing actions or modify stock. Historical demand and stock inputs may be stale.
        </div>

        {loading ? (
          <LoadingState label="Loading workflow capabilities…" />
        ) : statusError ? (
          <ErrorState message={statusError} />
        ) : (
          <>
            <section className="card mb-20">
              <h2 className="card-title">Backend workflow capabilities</h2>
              <div className="detail-grid">
                <div><span className="stat-label">Engine</span><strong>{status.orchestration_engine || '—'}</strong></div>
                <div><span className="stat-label">Results cached in process</span><strong>{status.active_runs_cached ?? '—'}</strong></div>
              </div>
              {Array.isArray(status.safety_guarantees) && (
                <ul className="safety-list">{status.safety_guarantees.map((item) => <li key={item}>{item}</li>)}</ul>
              )}
              {Array.isArray(status.known_limitations) && (
                <div className="notice warning mt-12">
                  <strong>Known backend limitations</strong>
                  <ul className="limitation-list">{status.known_limitations.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              )}
            </section>

            <section className="card mb-20">
              <h2 className="card-title">Run a single-product workflow</h2>
              <div className="assessment-controls">
                <label className="filter-field" htmlFor="workflow-product">
                  <span>Product from forecast catalog</span>
                  <select id="workflow-product" value={selectedProduct} onChange={(event) => setSelectedProduct(event.target.value)} disabled={!products.length}>
                    {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name || product.product_id} · {product.product_id}</option>)}
                  </select>
                </label>
                <button className="btn btn-primary" type="button" onClick={executeWorkflow} disabled={running || !selectedProduct}>
                  {running ? 'Running workflow…' : 'Run advisory workflow'}
                </button>
              </div>
              <p className="form-hint">Routing is determined by the backend. Procurement and excess-stock actions are mutually exclusive within one run; normal and insufficient-data outcomes stop without a downstream handoff.</p>
            </section>
          </>
        )}

        {runError && <ErrorState message={runError} />}
        {running && <LoadingState label="Running inventory assessment and conditional routing…" />}
        {result && (
          <section className="card mb-20" aria-live="polite">
            <div className="detail-heading">
              <div>
                <h2 className="detail-title">Workflow result · {result.product_id}</h2>
                <p className="detail-subtitle">{result.message || 'Backend workflow completed.'}</p>
              </div>
              <span className="badge badge-orange">{result.manager_action_required ? 'Manager action required' : 'Review required'}</span>
            </div>
            <div className="detail-grid mt-12">
              <div><span className="stat-label">Execution status</span><strong>{result.execution_status || '—'}</strong></div>
              <div><span className="stat-label">Routing decision</span><strong>{result.routing_decision || '—'}</strong></div>
              <div><span className="stat-label">Request ID</span><strong className="mono">{result.request_id || '—'}</strong></div>
              <div><span className="stat-label">Finalized</span><strong>{formatDateTime(result.finalized_at)}</strong></div>
            </div>
            {Array.isArray(result.errors) && result.errors.length > 0 && (
              <div className="notice error mt-12" role="alert">
                <strong>Backend reported workflow errors</strong>
                <ul className="limitation-list">{result.errors.map((item, index) => <li key={`${index}-${String(item)}`}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul>
              </div>
            )}
            {Array.isArray(result.data_notices) && result.data_notices.length > 0 && (
              <div className="notice warning mt-12">
                <strong>Data notices</strong>
                <ul className="limitation-list">{result.data_notices.map((item, index) => <li key={`${index}-${String(item)}`}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul>
              </div>
            )}
          </section>
        )}
        {result && <JsonPanel title="Inventory assessment summary" value={result.assessment_summary} />}
        {result && <JsonPanel title="Procurement result" value={result.procurement_result} />}
        {result && <JsonPanel title="Excess-stock result" value={result.excess_stock_result} />}

        {!loading && !statusError && !products.length && <EmptyState title="No products available for workflow">The forecast product endpoint returned no products. Generate or load forecast data first.</EmptyState>}
      </div>
    </>
  );
}
