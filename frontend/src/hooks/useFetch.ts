/**
 * Generic data-fetching hook.
 *
 * - Fires on mount and whenever `deps` change.
 * - Cancels in-flight requests via AbortController when the component
 *   unmounts or deps change (prevents stale state updates).
 * - Exposes `data`, `loading`, `error`, and a `reload` trigger.
 *
 * Use Effects only for external-system synchronization (React docs guidance);
 * this hook is the approved pattern for API data fetching in this codebase.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ConnectionError } from "../services/api";

export type FetchState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: T }
  | { status: "error"; message: string; isConnectionError: boolean };

export function useFetch<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  deps: any[],
): FetchState<T> & { reload: () => void } {
  const [state, setState] = useState<FetchState<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  // Keep a stable ref to the fetcher so it doesn't need to be in deps
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });

    fetcherRef.current(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setState({ status: "success", data });
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        const isConnectionError = err instanceof ConnectionError;
        const message =
          err instanceof Error ? err.message : "An unexpected error occurred.";
        setState({ status: "error", message, isConnectionError });
      },
    );

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, ...deps]);

  return { ...state, reload };
}
