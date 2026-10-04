/**
 * Finding Explorer — paginated, filtered table of all analytical findings.
 *
 * Uses real server-side filters from GET /api/v1/findings. Maintains
 * neutral analytical terminology exactly as returned by the backend.
 */
import { useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { fetchFindings } from "../services/api";
import { useFetch } from "../hooks/useFetch";
import {
  LoadingState,
  ErrorState,
  EmptyState,
} from "../components/StateViews";
import {
  ConfidenceBadge,
  CategoryBadge,
} from "../components/Badge";
import { Pagination } from "../components/Pagination";
import type { FindingsParams } from "../services/api";
import "./shared.css";
import "./FindingExplorer.css";

const LIMIT = 25;

const CATEGORIES = [
  "EXECUTION_GAP",
  "MONITORING_GAP",
  "ANOMALY",
  "PEER_DEVIATION",
  "METRIC_RISK_DIVERGENCE",
  "WORKFLOW_PATTERN",
];

const ANALYTIC_IDS = [
  "EG-001",
  "NS-001",
  "AN-001",
  "PB-001",
  "MRD-001",
  "IF-REP-001",
  "IF-DEV-002",
  "IF-MEA-003",
];

export function FindingExplorer() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Derive filter state from URL search params so filters are shareable/bookmarkable.
  const entityId = searchParams.get("entity_id") ?? "";
  const analyticId = searchParams.get("analytic_id") ?? "";
  const category = searchParams.get("category") ?? "";
  const periodLabel = searchParams.get("period_label") ?? "";
  const offsetStr = searchParams.get("offset") ?? "0";
  const offset = Math.max(0, parseInt(offsetStr, 10) || 0);

  const setFilter = useCallback(
    (key: string, value: string) => {
      setSearchParams((p) => {
        const next = new URLSearchParams(p);
        if (value) next.set(key, value);
        else next.delete(key);
        next.delete("offset"); // reset pagination on filter change
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

  // Build params object for useFetch dependency comparison
  const params: FindingsParams = {
    entity_id: entityId || undefined,
    analytic_id: analyticId || undefined,
    category: category || undefined,
    period_label: periodLabel || undefined,
    limit: LIMIT,
    offset,
  };

  const paramsKey = JSON.stringify(params);

  const findings = useFetch(
    useCallback((signal) => fetchFindings(params, signal), [paramsKey]), // eslint-disable-line react-hooks/exhaustive-deps
    [paramsKey],
  );

  return (
    <div className="page">
      <h2 className="page-title">Finding Explorer</h2>
      <p className="page-desc">
        Browse all potential analytical signals. Filters use server-side
        querying. Results are ordered by finding key for stable pagination.
        An absence of findings reflects the current assessment state.
      </p>

      {/* Filters */}
      <div className="filters" role="search">
        <div className="filter-group">
          <label htmlFor="filter-entity">Entity</label>
          <input
            id="filter-entity"
            type="text"
            placeholder="ENT-01"
            value={entityId}
            onChange={(e) => setFilter("entity_id", e.target.value)}
          />
        </div>
        <div className="filter-group">
          <label htmlFor="filter-category">Category</label>
          <select
            id="filter-category"
            value={category}
            onChange={(e) => setFilter("category", e.target.value)}
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="filter-group">
          <label htmlFor="filter-analytic">Analytic</label>
          <select
            id="filter-analytic"
            value={analyticId}
            onChange={(e) => setFilter("analytic_id", e.target.value)}
          >
            <option value="">All analytics</option>
            {ANALYTIC_IDS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div className="filter-group">
          <label htmlFor="filter-period">Period (YYYY-MM)</label>
          <input
            id="filter-period"
            type="text"
            placeholder="2024-01"
            value={periodLabel}
            onChange={(e) => setFilter("period_label", e.target.value)}
          />
        </div>
        {(entityId || analyticId || category || periodLabel) && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setSearchParams({})}
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Results */}
      {findings.status === "loading" && <LoadingState label="Loading findings…" />}
      {findings.status === "error" && (
        <ErrorState
          message={findings.message}
          isConnectionError={findings.isConnectionError}
          onRetry={findings.reload}
        />
      )}
      {findings.status === "success" && findings.data.total === 0 && (
        <EmptyState
          title="No findings match the current filters"
          body="Try removing filters or running an assessment first. An empty result does not by itself indicate good operational health."
        />
      )}
      {findings.status === "success" && findings.data.total > 0 && (
        <>
          <p className="result-count">
            {findings.data.total.toLocaleString()} finding
            {findings.data.total !== 1 ? "s" : ""}
            {(entityId || analyticId || category || periodLabel) && " matching filters"}
          </p>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Category</th>
                  <th scope="col">Type / Finding</th>
                  <th scope="col">Entity</th>
                  <th scope="col">Period</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Evidence</th>
                  <th scope="col">
                    <span className="visually-hidden">Detail</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {findings.data.items.map((f) => (
                  <tr key={f.finding_key}>
                    <td>
                      <CategoryBadge category={f.category} />
                    </td>
                    <td className="finding-type-cell">
                      <span className="finding-type">
                        {f.finding_type.replace(/_/g, " ")}
                      </span>
                      <span className="finding-analytic">{f.analytic_id}</span>
                    </td>
                    <td>
                      <Link to={`/entities/${f.entity_id}`} className="link-btn">
                        {f.entity_id}
                      </Link>
                    </td>
                    <td className="mono">{f.period_label}</td>
                    <td>
                      <ConfidenceBadge confidence={f.confidence} />
                    </td>
                    <td>
                      {f.evidence_count > 0 ? (
                        <span className="evidence-count">{f.evidence_count}</span>
                      ) : (
                        <span className="evidence-none">—</span>
                      )}
                    </td>
                    <td>
                      <Link
                        to={`/findings/${encodeURIComponent(f.finding_key)}`}
                        className="btn btn--ghost"
                        style={{ fontSize: "var(--text-xs)" }}
                      >
                        Detail →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={findings.data.total}
            limit={LIMIT}
            offset={offset}
            onPage={setOffset}
          />
        </>
      )}
    </div>
  );
}
