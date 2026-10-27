import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../api';

export function useApiResource(loader) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  const reload = useCallback(() => {
    setLoading(true);
    setError('');
    setRevision((current) => current + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    loader(controller.signal)
      .then((response) => {
        if (active) setData(response);
      })
      .catch((requestError) => {
        if (active && !controller.signal.aborted) setError(errorMessage(requestError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [loader, revision]);

  return { data, loading, error, reload };
}
