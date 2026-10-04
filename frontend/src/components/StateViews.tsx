/**
 * Loading, error, and empty states for API-backed screens.
 *
 * Every API-backed screen must show an honest, deliberate state rather than a
 * blank page. These components are used consistently across all pages.
 */
import "./StateViews.css";

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

interface LoadingProps {
  label?: string;
}

export function LoadingState({ label = "Loading…" }: LoadingProps) {
  return (
    <div className="state-view state-view--loading" role="status">
      <span className="state-view__spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

interface ErrorProps {
  message: string;
  isConnectionError?: boolean;
  onRetry?: () => void;
}

export function ErrorState({ message, isConnectionError, onRetry }: ErrorProps) {
  return (
    <div className="state-view state-view--error" role="alert">
      <p className="state-view__title">
        {isConnectionError ? "Backend not reachable" : "Something went wrong"}
      </p>
      <p className="state-view__body">{message}</p>
      {onRetry && (
        <button type="button" className="state-view__retry" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty
// ---------------------------------------------------------------------------

interface EmptyProps {
  title: string;
  body?: string;
}

export function EmptyState({ title, body }: EmptyProps) {
  return (
    <div className="state-view state-view--empty">
      <p className="state-view__title">{title}</p>
      {body && <p className="state-view__body">{body}</p>}
    </div>
  );
}
