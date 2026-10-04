/**
 * Entity Assessment — browse all entities and drill into one to see its
 * analytical findings grouped by category and period.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";

import { fetchEntities, fetchEntity, fetchFindings } from "../services/api";
import { useFetch } from "../hooks/useFetch";
import { LoadingState, ErrorState, EmptyState } from "../components/StateViews";
import { ConfidenceBadge, CategoryBadge } from "../components/Badge";
import { Pagination } from "../components/Pagination";
import type { EntityResponse } from "../types/api";
import "./shared.css";
import "./EntityAssessment.css";

// ---------------------------------------------------------------------------
// Entity list view
// ---------------------------------------------------------------------------

export function EntityListPage() {
  const navigate = useNavigate();
  const [offset, setOffset] = useState(0);
  const LIMIT = 20;

  const entities = useFetch(
    (signal) => fetchEntities({ limit: LIMIT, offset }, signal),
    [offset],
  );

  return (
    <div className="page">
      <h2 className="page-title">Entity Assessment</h2>
      <p className="page-desc">
        Select a SOC entity to view its analytical findings, priority
        distribution, confidence, and evidence availability across assessment
        periods.
      </p>

      {entities.status === "loading" && <LoadingState />}
      {entities.status === "error" && (
        <ErrorState
          message={entities.message}
          isConnectionError={entities.isConnectionError}
          onRetry={entities.reload}
        />
      )}
      {entities.status === "success" && entities.data.total === 0 && (
        <EmptyState
          title="No entities found"
          body="Import entity data through the ingestion pipeline before running an assessment."
        />
      )}
      {entities.status === "success" && entities.data.total > 0 && (
        <>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Entity ID</th>
                  <th scope="col">Name</th>
                  <th scope="col">Sector</th>
                  <th scope="col">Scale</th>
                  <th scope="col">Assets</th>
                  <th scope="col">Analysts</th>
                  <th scope="col">Period end</th>
                  <th scope="col"><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {entities.data.items.map((e: EntityResponse) => (
                  <tr key={e.entity_id}>
                    <td>
                      <code className="mono">{e.entity_id}</code>
                    </td>
                    <td>{e.name}</td>
                    <td>{e.sector}</td>
                    <td>{e.scale}</td>
                    <td>{e.asset_count_estimate}</td>
                    <td>{e.analyst_headcount}</td>
                    <td className="mono">
                      {new Date(e.data_period_end).toLocaleDateString()}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--secondary"
                        style={{ padding: "2px 10px", fontSize: "var(--text-xs)" }}
                        onClick={() => navigate(`/entities/${e.entity_id}`)}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={entities.data.total}
            limit={LIMIT}
            offset={offset}
            onPage={setOffset}
          />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entity detail view
// ---------------------------------------------------------------------------

export function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const [findingOffset, setFindingOffset] = useState(0);
  const FINDING_LIMIT = 25;

  const safeId = entityId ?? "";

  const entity = useFetch(
    useCallback((signal) => fetchEntity(safeId, signal), [safeId]),
    [],
  );

  const findings = useFetch(
    useCallback(
      (signal) =>
        fetchFindings(
          { entity_id: safeId, limit: FINDING_LIMIT, offset: findingOffset },
          signal,
        ),
      [safeId, findingOffset],
    ),
    [findingOffset],
  );

  // Reset offset when entity changes
  useEffect(() => setFindingOffset(0), [safeId]);

  if (!safeId) return <p>Entity not specified.</p>;

  return (
    <div className="page">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/entities">Entities</Link>
        <span aria-hidden="true"> / </span>
        <span aria-current="page">{safeId}</span>
      </nav>

      {/* Entity info */}
      {entity.status === "loading" && <LoadingState />}
      {entity.status === "error" && (
        <ErrorState
          message={entity.message}
          isConnectionError={entity.isConnectionError}
          onRetry={entity.reload}
        />
      )}
      {entity.status === "success" && (
        <div className="entity-header">
          <h2 className="page-title">{entity.data.name}</h2>
          <dl className="entity-meta">
            <div>
              <dt>ID</dt>
              <dd className="mono">{entity.data.entity_id}</dd>
            </div>
            <div>
              <dt>Sector</dt>
              <dd>{entity.data.sector}</dd>
            </div>
            <div>
              <dt>Scale</dt>
              <dd>{entity.data.scale}</dd>
            </div>
            <div>
              <dt>Assets</dt>
              <dd>{entity.data.asset_count_estimate}</dd>
            </div>
            <div>
              <dt>Analysts</dt>
              <dd>{entity.data.analyst_headcount}</dd>
            </div>
            <div>
              <dt>Data period</dt>
              <dd>
                {new Date(entity.data.data_period_start).toLocaleDateString()} –{" "}
                {new Date(entity.data.data_period_end).toLocaleDateString()}
              </dd>
            </div>
          </dl>
        </div>
      )}

      {/* Findings for this entity */}
      <section aria-labelledby="entity-findings-heading">
        <h3 id="entity-findings-heading" className="section-title">
          Analytical findings
        </h3>
        <p className="page-desc" style={{ marginBottom: "var(--space-4)" }}>
          Potential analytical signals identified for this entity. Use the
          Finding Explorer for full filtering and pagination. Findings are
          derived from the most recent assessment run.
        </p>

        {findings.status === "loading" && <LoadingState />}
        {findings.status === "error" && (
          <ErrorState
            message={findings.message}
            isConnectionError={findings.isConnectionError}
            onRetry={findings.reload}
          />
        )}
        {findings.status === "success" && findings.data.total === 0 && (
          <EmptyState
            title="No findings for this entity"
            body="Run an assessment or check the Finding Explorer. An absence of findings reflects the current assessment state; it does not confirm operational health."
          />
        )}
        {findings.status === "success" && findings.data.total > 0 && (
          <>
            <p className="result-count">
              {findings.data.total} finding{findings.data.total !== 1 ? "s" : ""}
            </p>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Category</th>
                    <th scope="col">Type</th>
                    <th scope="col">Period</th>
                    <th scope="col">Confidence</th>
                    <th scope="col">Evidence</th>
                    <th scope="col"><span className="visually-hidden">Detail</span></th>
                  </tr>
                </thead>
                <tbody>
                  {findings.data.items.map((f) => (
                    <tr key={f.finding_key}>
                      <td><CategoryBadge category={f.category} /></td>
                      <td style={{ fontSize: "var(--text-xs)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {f.finding_type}
                      </td>
                      <td className="mono">{f.period_label}</td>
                      <td><ConfidenceBadge confidence={f.confidence} /></td>
                      <td>{f.evidence_count > 0 ? f.evidence_count : "—"}</td>
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
              limit={FINDING_LIMIT}
              offset={findingOffset}
              onPage={setFindingOffset}
            />
          </>
        )}
      </section>
    </div>
  );
}
