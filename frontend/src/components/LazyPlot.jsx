import { Suspense, lazy } from 'react';

const Plot = lazy(() => import('react-plotly.js'));

export default function LazyPlot(props) {
  return (
    <Suspense fallback={<div className="chart-loading" role="status">Loading chart…</div>}>
      <Plot {...props} />
    </Suspense>
  );
}
