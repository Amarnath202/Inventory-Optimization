export function LoadingState({ label = 'Loading data…' }) {
  return (
    <div className="loading-center" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="error-state" role="alert">
      <div>
        <strong>Could not load this information</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button className="btn btn-secondary" type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title = 'No data available', children }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}
