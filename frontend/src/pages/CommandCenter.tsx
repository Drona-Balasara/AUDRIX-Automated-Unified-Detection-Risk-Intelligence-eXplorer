/**
 * Command Center — the primary entry point for SAT-SA supervisors.
 *
 * Shows real assessment state from GET /api/v1/queue/summary and
 * GET /api/v1/findings (summary counts). All values come from the live backend.
 *
 * Provides drill-down paths to the Finding Explorer and Review Queue.
 * Does NOT claim that the system has made autonomous decisions.
 * Does NOT interpret an empty finding list as proof of good health.
 */

import { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { runAssessment, fetchQueueSummary, fetchFindings } from "../services/api";
import { useFetch } from "../hooks/useFetch";
import { MetricCard } from "../components/MetricCard";
import { LoadingState, ErrorState } from "../components/StateViews";
import "./CommandCenter.css";

const PRIORITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

export function CommandCenter() {
  const navigate = useNavigate();
  const [runState, setRunState] = useState<
    "idle" | "running" | "done" | "error"
  >("idle");
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [summaryNonce, setSummaryNonce] = useState(0);

  // Queue summary from the authoritative API endpoint.
  const summary = useFetch(
    (signal) => fetchQueueSummary(signal),
    [summaryNonce],
  );

  // High-level finding counts (all findings, limit=1 just to get total).
  const findingsMeta = useFetch(
    (signal) => fetchFindings({ limit: 1 }, signal),
    [summaryNonce],
  );

  const handleRunAssessment = useCallback(async () => {
    setRunState("running");
    setRunMessage(null);
    try {
      const result = await runAssessment();
      setRunMessage(
        `Assessment complete. ${result.total_findings} findings across ${result.entity_count} entities. ` +
          `Queue: ${result.queue_items_total} items (${result.queue_inserted} new).`,
      );
      setRunState("done");
      setSummaryNonce((n) => n + 1);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error.";
      setRunMessage(`Assessment failed: ${msg}`);
      setRunState("error");
    }
  }, []);

  const openItems =
    summary.status === "success"
      ? (summary.data.by_status["OPEN"] ?? 0)
      : null;

  const totalFindings =
    findingsMeta.status === "success" ? findingsMeta.data.total : null;

  return (
    <div className="command-center">
      <div className="command-center__header">
        <div>
          <h2 className="page-title">Command Center</h2>
          <p className="page-desc">
            Current assessment state across all SOC entities. Run an assessment
            to refresh findings and the review queue.
          </p>
        </div>
        <div className="command-center__actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleRunAssessment}
            disabled={runState === "running"}
          >
            {runState === "running" ? "Running…" : "Run Assessment"}
          </button>
        </div>
      </div>

      {runMessage && (
        <div
          className={`run-feedback run-feedback--${runState}`}
          role="status"
          aria-live="polite"
        >
          {runMessage}
        </div>
      )}

      {/* ── Summary metrics ─────────────────────────────────── */}
      <section aria-labelledby="summary-heading">
        <h3 id="summary-heading" className="section-title">
          Assessment summary
        </h3>

        {summary.status === "loading" && <LoadingState />}
        {summary.status === "error" && (
          <ErrorState
            message={summary.message}
            isConnectionError={summary.isConnectionError}
            onRetry={summary.reload}
          />
        )}

        {summary.status === "success" && (
          <>
            <div className="metric-grid">
              <MetricCard
                label="Total findings"
                value={totalFindings ?? "—"}
                sub="across all analytics"
              />
              <MetricCard
                label="Open review items"
                value={openItems ?? "—"}
                tone={openItems && openItems > 0 ? "warning" : "neutral"}
                sub="awaiting supervisory review"
              />
              <MetricCard
                label="In review"
                value={summary.data.by_status["IN_REVIEW"] ?? 0}
              />
              <MetricCard
                label="Reviewed"
                value={summary.data.by_status["REVIEWED"] ?? 0}
                tone="success"
              />
            </div>

            {/* Priority distribution — using server values directly */}
            <div className="priority-section">
              <h4 className="subsection-title">Queue by priority</h4>
              <dl className="priority-list">
                {PRIORITY_ORDER.map((p) => {
                  const count = summary.data.by_priority[p] ?? 0;
                  return (
                    <div key={p} className="priority-list__row">
                      <dt className={`priority-list__label priority-list__label--${p.toLowerCase()}`}>
                        {p}
                      </dt>
                      <dd className="priority-list__count">{count}</dd>
                      <dd className="priority-list__bar-wrap" aria-hidden="true">
                        <div
                          className={`priority-list__bar priority-list__bar--${p.toLowerCase()}`}
                          style={{
                            width: summary.data.total > 0
                              ? `${Math.round((count / summary.data.total) * 100)}%`
                              : "0%",
                          }}
                        />
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </div>

            {/* Category distribution */}
            <div className="category-section">
              <h4 className="subsection-title">Queue by category</h4>
              <dl className="category-list">
                {Object.entries(summary.data.by_category)
                  .filter(([, v]) => v > 0)
                  .sort(([, a], [, b]) => b - a)
                  .map(([cat, count]) => (
                    <div key={cat} className="category-list__row">
                      <dt className="category-list__label">
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => navigate(`/queue?category=${cat}`)}
                        >
                          {cat.replace(/_/g, " ")}
                        </button>
                      </dt>
                      <dd className="category-list__count">{count}</dd>
                    </div>
                  ))}
              </dl>
            </div>
          </>
        )}
      </section>

      {/* ── Navigation shortcuts ─────────────────────────────── */}
      <section className="shortcuts" aria-labelledby="shortcuts-heading">
        <h3 id="shortcuts-heading" className="section-title">
          Navigate
        </h3>
        <div className="shortcut-grid">
          <Link to="/findings" className="shortcut-card">
            <span className="shortcut-card__title">Finding Explorer</span>
            <span className="shortcut-card__desc">
              Browse, filter, and drill into all analytical findings with
              evidence and confidence.
            </span>
          </Link>
          <Link to="/queue" className="shortcut-card">
            <span className="shortcut-card__title">Review Queue</span>
            <span className="shortcut-card__desc">
              Work through the supervisory review queue. Transition status,
              add notes, track review progress.
            </span>
          </Link>
          <Link to="/entities" className="shortcut-card">
            <span className="shortcut-card__title">Entity Assessment</span>
            <span className="shortcut-card__desc">
              Select an entity to view its analytical profile, findings by
              period, and evidence availability.
            </span>
          </Link>
        </div>
      </section>

      <p className="disclaimer">
        AUDRIX identifies potential patterns in operational evidence for
        supervisory review. It does not make autonomous risk decisions,
        confirm misconduct, or replace human analytical judgment.
      </p>
    </div>
  );
}
