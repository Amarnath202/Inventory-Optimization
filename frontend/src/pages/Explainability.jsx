import { useEffect, useRef, useState } from 'react';
import {
  errorMessage,
  getExcessStockExplanation,
  getExplainabilityHealth,
  getExplainabilityModels,
  getForecastExplanation,
  getInventoryExplanation,
  getProcurementExplanation,
} from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';

const EXPLANATION_TYPES = {
  forecast: { label: 'Forecast prediction', idLabel: 'Product ID', request: getForecastExplanation },
  inventory: { label: 'Inventory assessment', idLabel: 'Assessment ID', request: getInventoryExplanation },
  procurement: { label: 'Supplier recommendation', idLabel: 'Recommendation ID', request: getProcurementExplanation },
  excess: { label: 'Excess-stock assessment', idLabel: 'Assessment ID', request: getExcessStockExplanation },
};

function JsonValue({ value }) {
  if (value === null || typeof value !== 'object') {
    return <span className="json-scalar">{value === null ? 'null' : String(value)}</span>;
  }
  return <pre className="json-output">{JSON.stringify(value, null, 2)}</pre>;
}

function StatusSummary({ title, data, error }) {
  return (
    <section className="card">
      <h2 className="card-title">{title}</h2>
      {error ? <p className="status-error-text">{error}</p> : data === null ? <span className="inline-message">No response</span> : <JsonValue value={data} />}
    </section>
  );
}

export default function Explainability() {
  const [kind, setKind] = useState(() => {
    const queryKind = new URLSearchParams(window.location.search).get('kind');
    return EXPLANATION_TYPES[queryKind] ? queryKind : 'forecast';
  });
  const [identifier, setIdentifier] = useState(() => new URLSearchParams(window.location.search).get('id') || '');
  const [result, setResult] = useState(null);
  const [resultError, setResultError] = useState('');
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');
  const [models, setModels] = useState(null);
  const [modelsError, setModelsError] = useState('');
  const requestRef = useRef(null);
  const selectedType = EXPLANATION_TYPES[kind];

  useEffect(() => {
    let active = true;
    getExplainabilityHealth()
      .then((response) => { if (active) setHealth(response); })
      .catch((error) => { if (active) setHealthError(errorMessage(error)); });
    getExplainabilityModels()
      .then((response) => { if (active) setModels(response); })
      .catch((error) => { if (active) setModelsError(errorMessage(error)); });
    return () => {
      active = false;
      requestRef.current?.abort();
    };
  }, []);

  const runExplanation = async (event) => {
    event.preventDefault();
    const id = identifier.trim();
    if (!id) {
      setResultError(`Enter a ${selectedType.idLabel.toLowerCase()}.`);
      return;
    }

    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setResult(null);
    setResultError('');
    try {
      const response = await selectedType.request(id, { signal: controller.signal });
      if (response === undefined) throw new Error('The API returned an empty explanation response.');
      setResult(response);
    } catch (requestError) {
      if (!controller.signal.aborted) setResultError(errorMessage(requestError));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Explainability</h1>
          <p>Inspect explanations returned by the backend for supported forecast and persisted decision records.</p>
        </div>
      </div>

      <div className="page-body">
        <div className="notice mb-20">
          Explanations reflect the selected backend record. Forecast explanations depend on the trained model artifacts; inventory, procurement, and excess-stock explanations describe the recorded rule-based decision.
        </div>

        <div className="chart-grid mb-20">
          <StatusSummary title="Explainability service" data={health} error={healthError} />
          <StatusSummary title="Available model artifacts" data={models} error={modelsError} />
        </div>

        <section className="card mb-20">
          <h2 className="card-title">Request an explanation</h2>
          <form className="explanation-form" onSubmit={runExplanation}>
            <label className="filter-field">
              <span>Record type</span>
              <select value={kind} onChange={(event) => {
                requestRef.current?.abort();
                setLoading(false);
                setKind(event.target.value);
                setResult(null);
                setResultError('');
              }}>
                {Object.entries(EXPLANATION_TYPES).map(([key, type]) => <option key={key} value={key}>{type.label}</option>)}
              </select>
            </label>
            <label className="filter-field explanation-id" htmlFor="explanation-id">
              <span>{selectedType.idLabel}</span>
              <input id="explanation-id" value={identifier} onChange={(event) => setIdentifier(event.target.value)} required />
            </label>
            <button className="btn btn-primary" type="submit" disabled={loading || !identifier.trim()}>
              {loading ? 'Retrieving…' : 'Get explanation'}
            </button>
          </form>
          <p className="form-hint">Use a product ID for forecast explanations, or the saved record ID shown in the corresponding inventory, supplier, or excess-stock result.</p>
        </section>

        {loading ? (
          <LoadingState label="Retrieving backend explanation…" />
        ) : resultError ? (
          <ErrorState message={resultError} />
        ) : result !== null ? (
          <section className="card" aria-live="polite">
            <h2 className="card-title">Backend explanation response</h2>
            {result && typeof result === 'object' && !Array.isArray(result) ? (
              <dl className="explanation-fields">
                {Object.entries(result).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll('_', ' ')}</dt>
                    <dd><JsonValue value={value} /></dd>
                  </div>
                ))}
              </dl>
            ) : <JsonValue value={result} />}
          </section>
        ) : (
          <EmptyState title="No explanation selected">
            Choose a supported record type and enter its identifier to request an explanation.
          </EmptyState>
        )}
      </div>
    </>
  );
}
