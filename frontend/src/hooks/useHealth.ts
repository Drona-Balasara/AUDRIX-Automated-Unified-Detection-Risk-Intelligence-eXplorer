import { useCallback, useEffect, useState } from "react";

import {
  ConnectionError,
  fetchHealth,
  UnexpectedResponseError,
} from "../services/api";
import type { HealthState } from "../types/health";

const INITIAL_STATE: HealthState = {
  status: "loading",
  data: null,
  message: null,
};

/**
 * Fetches backend health on mount and exposes an honest state machine
 * (loading / connected / unavailable / error) plus a manual `refresh`.
 *
 * Error messages are deliberately generic and never surface backend internals.
 */
export function useHealth(): HealthState & { refresh: () => void } {
  const [state, setState] = useState<HealthState>(INITIAL_STATE);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => {
    setState(INITIAL_STATE);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    fetchHealth(controller.signal)
      .then((data) => {
        setState({ status: "connected", data, message: null });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ConnectionError) {
          setState({
            status: "unavailable",
            data: null,
            message: "The backend service is not responding.",
          });
        } else if (err instanceof UnexpectedResponseError) {
          setState({
            status: "error",
            data: null,
            message: "The backend returned an unexpected response.",
          });
        } else {
          setState({
            status: "error",
            data: null,
            message: "An unexpected error occurred.",
          });
        }
      });

    return () => controller.abort();
  }, [nonce]);

  return { ...state, refresh };
}
