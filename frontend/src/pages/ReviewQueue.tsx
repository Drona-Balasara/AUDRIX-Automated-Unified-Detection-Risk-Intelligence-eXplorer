/**
 * Supervisory Review Queue.
 *
 * Consumes GET /api/v1/queue and GET /api/v1/queue/summary.
 * Valid status transitions use PATCH /api/v1/queue/{queue_id}/status.
 * Backend validation is respected — 409 conflict responses are shown clearly.
 * Analytical findings and evidence are never mutated through this interface.
 */
import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { fetchQueue, fetchQueueSummary, transitionQueueStatus } from "../services/api";
import { useFetch } from "../hooks/useFetch";
import { ApiResponseError } from "../services/api";
import {
  LoadingState,
  ErrorState,
  EmptyState,
} from "../components/StateViews";
import {
  PriorityBadge,
  ConfidenceBadge,
  StatusBadge,
  CategoryBadge,
} from "../components/Badge";
import { MetricCard } from "../components/MetricCard";
import { Pagination } from "../components/Pagination";
import type { QueueItemResponse } from "../types/api";
import "./shared.css";
import "./ReviewQueue.css";

const LIMIT = 25;
const VALID_STATUSES = ["OPEN", "IN_REVIEW", "REVIEWED", "DISMISSED"];
const PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
const CATEGORIES = [
  "EXECUTION_GAP",
  "MONITORING_GAP",
  "ANOMALY",
  "PEER_DEVIATION",
  "METRIC_RISK_DIVERGENCE",
  "WORKFLOW_PATTERN",
];

// ---------------------------------------------------------------------------
// Inline transition form
// ---------------------------------------------------------------------------

interface TransitionFormProps {
  item: QueueItemResponse;
  onDone: (updated: QueueItemResponse) => void;
  onCancel: () => void;
}

function TransitionForm({ item, onDone, onCancel }: TransitionFormProps) {
  const [newStatus, setNewStatus] = useState("");
  const [reviewerRef, setReviewerRef] = useState("");
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "submitting" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!newStatus) return;
      setState("submitting");
      setErrorMsg("");
      try {
        const updated = await transitionQueueStatus(item.queue_id, {
          new_status: newStatus,
          reviewer_ref: reviewerRef || undefined,
          review_note: note || undefined,
        });
        onDone(updated);
      } catch (err: unknown) {
        if (err instanceof ApiResponseError && err.status === 409) {
          setErrorMsg(`Transition not allowed: ${err.detail}`);
        } else {
          setErrorMsg(err instanceof Error ? err.message : "An error occurred.");
        }
        setState("error");
      }
    },
    [item.queue_id, newStatus, reviewerRef, note, onDone],
  );

  return (
    <form className="transition-form" onSubmit={handleSubmit} aria-label="Update review status">
      <div className="transition-form__row">
        <label htmlFor={`status-select-${item.queue_id}`} className="visually-hidden">
          New status
        </label>
        <select
          id={`status-select-${item.queue_id}`}
          value={newStatus}
          onChange={(e) => setNewStatus(e.target.value)}
          required
          className="transition-select"
        >
          <option value="">— Select new status —</option>
          {VALID_STATUSES.filter((s) => s !== item.status).map((s) => (
            <option key={s} value={s}>
              {s.replace("_", " ")}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Reviewer (optional)"
          maxLength={128}
          value={reviewerRef}
          onChange={(e) => setReviewerRef(e.target.value)}
          className="transition-input"
          aria-label="Reviewer reference"
        />
      </div>
      <textarea
        placeholder="Review note (optional)"
        maxLength={4096}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className="transition-note"
        rows={2}
        aria-label="Review note"
      />
      {state === "error" && (
        <p className="transition-error" role="alert">
          {errorMsg}
        </p>
      )}
      <div className="transition-form__actions">
        <button
          type="submit"
          className="btn btn--primary"
          disabled={!newStatus || state === "submitting"}
        >
          {state === "submitting" ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn btn--secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Queue row
// ---------------------------------------------------------------------------

interface QueueRowProps {
  item: QueueItemResponse;
  onUpdate: (updated: QueueItemResponse) => void;
}

function QueueRow({ item, onUpdate }: QueueRowProps) {
  const [reviewing, setReviewing] = useState(false);

  const handleDone = useCallback(
    (updated: QueueItemResponse) => {
      setReviewing(false);
      onUpdate(updated);
    },
    [onUpdate],
  );

  return (
    <>
      <tr className={`queue-row queue-row--${item.status.toLowerCase()}`}>
        <td><PriorityBadge priority={item.priority} /></td>
        <td>
          <div className="queue-title">{item.title}</div>
          <div className="queue-meta">
            <CategoryBadge category={item.category} />
            <span className="mono" style={{ fontSize: "var(--text-xs)" }}>
              {item.analytic_id}
            </span>
          </div>
        </td>
        <td>
          <Link to={`/entities/${item.entity_id}`} className="link-btn">
            {item.entity_id}
          </Link>
        </td>
        <td className="mono">{item.period_label}</td>
        <td><ConfidenceBadge confidence={item.confidence} /></td>
        <td>{item.evidence_count > 0 ? item.evidence_count : "—"}</td>
        <td><StatusBadge status={item.status} /></td>
        <td>
          <div className="queue-row__actions">
            <Link
              to={`/findings/${encodeURIComponent(item.finding_key)}`}
              className="btn btn--ghost"
              style={{ fontSize: "var(--text-xs)" }}
            >
              Detail
            </Link>
            <button
              type="button"
              className="btn btn--secondary"
              style={{ fontSize: "var(--text-xs)", padding: "2px 8px" }}
              onClick={() => setReviewing((r) => !r)}
            >
              {reviewing ? "Cancel" : "Update"}
            </button>
          </div>
        </td>
      </tr>
      {reviewing && (
        <tr>
          <td colSpan={8} className="queue-row__form-cell">
            <TransitionForm
              item={item}
              onDone={handleDone}
              onCancel={() => setReviewing(false)}
            />
          </td>
        </tr>
      )}
      {item.review_note && (
        <tr>
          <td colSpan={8} className="queue-row__note-cell">
            <span className="queue-note-label">Note:</span> {item.review_note}
            {item.reviewer_ref && (
              <span className="queue-note-reviewer"> — {item.reviewer_ref}</span>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ReviewQueue() {
  const [searchParams, setSearchParams] = useSearchParams();

  const status = searchParams.get("status") ?? "";
  const priority = searchParams.get("priority") ?? "";
  const entityId = searchParams.get("entity_id") ?? "";
  const category = searchParams.get("category") ?? "";
  const offsetStr = searchParams.get("offset") ?? "0";
  const offset = Math.max(0, parseInt(offsetStr, 10) || 0);

  // Local override map for optimistic updates after transitions.
  const [overrides, setOverrides] = useState<Record<string, QueueItemResponse>>({});

  const [summaryNonce, setSummaryNonce] = useState(0);

  const setFilter = useCallback(
    (key: string, value: string) => {
      setSearchParams((p) => {
        const next = new URLSearchParams(p);
        if (value) next.set(key, value);
        else next.delete(key);
        next.delete("offset");
        return next;
      });
    },
    [setSearchParams],
  );

  const setOffset = useCallback(
    (newOffset: number) => {
      setSearchParams((p) => {
        const next = new URLSearchParams(p);
        if (newOffset > 0) next.set("offset", String(newOffset));
        else next.delete("offset");
        return next;
      });
    },
    [setSearchParams],
  );

  const paramsKey = JSON.stringify({ status, priority, entityId, category, offset });

  const queue = useFetch(
    useCallback(
      (signal) =>
        fetchQueue(
          {
            status: status || undefined,
            priority: priority || undefined,
            entity_id: entityId || undefined,
            category: category || undefined,
            limit: LIMIT,
            offset,
          },
          signal,
        ),
      [paramsKey], // eslint-disable-line react-hooks/exhaustive-deps
    ),
    [paramsKey],
  );

  const summary = useFetch(
    (signal) => fetchQueueSummary(signal),
    [summaryNonce],
  );

  const handleUpdate = useCallback((updated: QueueItemResponse) => {
    setOverrides((prev) => ({ ...prev, [updated.queue_id]: updated }));
    setSummaryNonce((n) => n + 1);
  }, []);

  return (
    <div className="page">
      <h2 className="page-title">Supervisory Review Queue</h2>
      <p className="page-desc">
        Prioritised queue of analytical findings for supervisory review. Status
        transitions are validated by the backend — invalid transitions are
        rejected with an explanation. Analytical evidence is never modified
        through this interface.
      </p>

      {/* Summary cards */}
      {summary.status === "success" && (
        <div className="metric-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
          <MetricCard label="Total" value={summary.data.total} />
          <MetricCard label="Open" value={summary.data.by_status["OPEN"] ?? 0} tone={summary.data.by_status["OPEN"] ? "warning" : "neutral"} />
          <MetricCard label="In review" value={summary.data.by_status["IN_REVIEW"] ?? 0} />
          <MetricCard label="Reviewed" value={summary.data.by_status["REVIEWED"] ?? 0} tone="success" />
          <MetricCard label="Dismissed" value={summary.data.by_status["DISMISSED"] ?? 0} />
          <MetricCard label="Critical" value={summary.data.by_priority["CRITICAL"] ?? 0} tone="danger" />
        </div>
      )}

      {/* Filters */}
      <div className="filters" role="search">
        <div className="filter-group">
          <label htmlFor="q-status">Status</label>
          <select id="q-status" value={status} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">All</option>
            {VALID_STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
        </div>
        <div className="filter-group">
          <label htmlFor="q-priority">Priority</label>
          <select id="q-priority" value={priority} onChange={(e) => setFilter("priority", e.target.value)}>
            <option value="">All</option>
            {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div className="filter-group">
          <label htmlFor="q-category">Category</label>
          <select id="q-category" value={category} onChange={(e) => setFilter("category", e.target.value)}>
            <option value="">All</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
          </select>
        </div>
        <div className="filter-group">
          <label htmlFor="q-entity">Entity</label>
          <input id="q-entity" type="text" placeholder="ENT-01" value={entityId} onChange={(e) => setFilter("entity_id", e.target.value)} />
        </div>
        {(status || priority || category || entityId) && (
          <button type="button" className="btn btn--ghost" onClick={() => setSearchParams({})}>
            Clear
          </button>
        )}
      </div>

      {/* Queue table */}
      {queue.status === "loading" && <LoadingState label="Loading queue…" />}
      {queue.status === "error" && (
        <ErrorState message={queue.message} isConnectionError={queue.isConnectionError} onRetry={queue.reload} />
      )}
      {queue.status === "success" && queue.data.total === 0 && (
        <EmptyState
          title="No queue items match the current filters"
          body="Run an assessment to populate the queue, or adjust the filters."
        />
      )}
      {queue.status === "success" && queue.data.total > 0 && (
        <>
          <p className="result-count">
            {queue.data.total.toLocaleString()} item{queue.data.total !== 1 ? "s" : ""}
          </p>
          <div className="data-table-wrap">
            <table className="data-table queue-table">
              <thead>
                <tr>
                  <th scope="col">Priority</th>
                  <th scope="col">Finding / Title</th>
                  <th scope="col">Entity</th>
                  <th scope="col">Period</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Evidence</th>
                  <th scope="col">Status</th>
                  <th scope="col"><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {queue.data.items.map((item) => (
                  <QueueRow
                    key={item.queue_id}
                    item={overrides[item.queue_id] ?? item}
                    onUpdate={handleUpdate}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={queue.data.total}
            limit={LIMIT}
            offset={offset}
            onPage={setOffset}
          />
        </>
      )}

      <p className="disclaimer" style={{ marginTop: "var(--space-5)" }}>
        Priority and confidence are distinct: priority indicates the attention a
        finding deserves, while confidence indicates data sufficiency. A
        LOW-confidence finding may still warrant HIGH priority.
      </p>
    </div>
  );
}
