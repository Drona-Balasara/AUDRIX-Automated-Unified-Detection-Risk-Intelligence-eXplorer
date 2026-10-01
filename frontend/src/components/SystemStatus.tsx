import "./SystemStatus.css";
import { StatusIndicator } from "./StatusIndicator";
import type { HealthState } from "../types/health";

const LABEL_BY_STATUS: Record<HealthState["status"], string> = {
  loading: "Checking…",
  connected: "Connected",
  unavailable: "Unavailable",
  error: "Error",
};

const DEFAULT_MESSAGE: Record<HealthState["status"], string> = {
  loading: "Contacting the backend service.",
  connected: "The backend service is reachable and responding.",
  unavailable: "The backend service is not responding.",
  error: "The backend returned an unexpected response.",
};

interface SystemStatusProps {
  state: HealthState;
  onRetry: () => void;
}

/**
 * System status panel: honestly reflects the live backend connection state and,
 * when connected, shows the health fields the backend actually reported. No
 * other product data is fabricated.
 */
export function SystemStatus({ state, onRetry }: SystemStatusProps) {
  const { status, data, message } = state;
  const canRetry = status === "unavailable" || status === "error";

  return (
    <section className="system-status" aria-labelledby="system-status-title">
      <div className="system-status__head">
        <h2 id="system-status-title" className="system-status__title">
          Backend connection
        </h2>
        <StatusIndicator status={status} label={LABEL_BY_STATUS[status]} />
      </div>

      <p className="system-status__message" role="status" aria-live="polite">
        {message ?? DEFAULT_MESSAGE[status]}
        {canRetry ? (
          <>
            {" "}
            <button type="button" className="system-status__retry" onClick={onRetry}>
              Retry
            </button>
          </>
        ) : null}
      </p>

      {status === "connected" && data ? (
        <dl className="system-status__details">
          <dt>Service</dt>
          <dd>{data.service}</dd>
          <dt>Version</dt>
          <dd>{data.version}</dd>
          <dt>Environment</dt>
          <dd>{data.environment}</dd>
          <dt>Database</dt>
          <dd>{data.database}</dd>
        </dl>
      ) : null}
    </section>
  );
}
