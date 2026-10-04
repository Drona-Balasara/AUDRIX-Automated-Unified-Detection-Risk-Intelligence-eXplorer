/**
 * Finding Detail / Evidence View.
 *
 * Shows the full analytical finding with its evidence and confidence from
 * GET /api/v1/findings/{finding_key}. Clearly distinguishes observed evidence
 * from derived analytical interpretation.
 */
import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchFinding } from "../services/api";
import { useFetch } from "../hooks/useFetch";
import { LoadingState, ErrorState } from "../components/StateViews";
import { ConfidenceBadge, CategoryBadge } from "../components/Badge";
import "./shared.css";
import "./FindingDetail.css";

const SOURCE_TYPE_LABEL: Record<string, string> = {
  ALERT: "Alert",
  INVESTIGATION: "Investigation",
  INVESTIGATION_ACTION: "Investigation Action",
  ESCALATION: "Escalation",
  REMEDIATION: "Remediation",
  TELEMETRY_RECORD: "Telemetry Record",
  PERFORMANCE_METRIC: "Performance Metric",
  ENTITY: "Entity",
  ASSET: "Asset",
  ENTITY_PERIOD_OBSERVATION: "Computed Observation",
  PEER_BASELINE: "Peer Baseline",
  ABSENCE: "Expected (Absent)",
};

export function FindingDetail() {
  const { "*": findingKey } = useParams();
  const safeKey = findingKey ?? "";

  const finding = useFetch(
    useCallback((signal) => fetchFinding(safeKey, signal), [safeKey]),
    [],
  );

  if (!safeKey) return <p>Finding key not specified.</p>;

  return (
    <div className="page">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/findings">Finding Explorer</Link>
        <span aria-hidden="true"> / </span>
        <span aria-current="page" className="breadcrumb__key">
          {safeKey.length > 60 ? `${safeKey.slice(0, 60)}…` : safeKey}
        </span>
      </nav>

      {finding.status === "loading" && <LoadingState label="Loading finding…" />}
      {finding.status === "error" && (
        <ErrorState
          message={finding.message}
          isConnectionError={finding.isConnectionError}
          onRetry={finding.reload}
        />
      )}

      {finding.status === "success" && (() => {
        const { finding: f, evidence: ev } = finding.data;
        return (
          <div className="detail-panel">
            {/* ── Finding identity ──────────────────────────────────── */}
            <div className="detail-section">
              <h2 className="detail-section__title">Finding</h2>
              <div className="finding-headline">
                <div className="finding-headline__badges">
                  <CategoryBadge category={f.category} />
                  {ev && <ConfidenceBadge confidence={ev.confidence} />}
                </div>
                <p className="finding-headline__summary">{f.summary}</p>
              </div>

              <div className="field-grid" style={{ marginTop: "var(--space-4)" }}>
                <div className="field-item">
                  <span className="field-item__label">Finding key</span>
                  <span className="field-item__value mono" style={{ wordBreak: "break-all", fontSize: "var(--text-xs)" }}>{f.finding_key}</span>
                </div>
                <div className="field-item">
                  <span className="field-item__label">Analytic</span>
                  <span className="field-item__value mono">{f.analytic_id}</span>
                </div>
                <div className="field-item">
                  <span className="field-item__label">Finding type</span>
                  <span className="field-item__value">{f.finding_type.replace(/_/g, " ")}</span>
                </div>
                <div className="field-item">
                  <span className="field-item__label">Entity</span>
                  <span className="field-item__value">
                    <Link to={`/entities/${f.entity_id}`} className="link-btn">
                      {f.entity_id}
                    </Link>
                  </span>
                </div>
                <div className="field-item">
                  <span className="field-item__label">Period</span>
                  <span className="field-item__value mono">{f.period_label}</span>
                </div>
                <div className="field-item">
                  <span className="field-item__label">Evidence count</span>
                  <span className="field-item__value">{f.evidence_count}</span>
                </div>
              </div>
            </div>

            {/* ── Analytical details ────────────────────────────────── */}
            {Object.keys(f.details).length > 0 && (
              <div className="detail-section">
                <h3 className="detail-section__title">Analytical details</h3>
                <p className="detail-note">
                  Analytical signal values derived from the assessment run. These
                  describe observed patterns in the data — not conclusions about
                  intent or cause.
                </p>
                <dl className="details-list">
                  {Object.entries(f.details).map(([k, v]) => (
                    <div key={k} className="details-list__row">
                      <dt className="details-list__key">{k.replace(/_/g, " ")}</dt>
                      <dd className="details-list__val mono">
                        {typeof v === "number" ? v.toFixed(4) : String(v)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}

            {/* ── Confidence ────────────────────────────────────────── */}
            {ev && (
              <div className="detail-section">
                <h3 className="detail-section__title">Confidence</h3>
                <div className="confidence-block">
                  <div className="confidence-block__level">
                    <ConfidenceBadge confidence={ev.confidence} />
                    <span className="confidence-block__note">{ev.confidence_note}</span>
                  </div>
                  {ev.confidence_factors.length > 0 && (
                    <ul className="confidence-factors">
                      {ev.confidence_factors.map((f) => (
                        <li key={f} className="confidence-factors__item">
                          {f.replace(/_/g, " ").toLowerCase()}
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="detail-note">
                    Confidence reflects data sufficiency, not risk or severity.
                    A LOW-confidence finding may still deserve supervisory
                    attention; a HIGH-confidence finding is not a verdict.
                  </p>
                </div>
              </div>
            )}

            {/* ── Evidence references ───────────────────────────────── */}
            {ev && ev.evidence_refs.length > 0 && (
              <div className="detail-section">
                <h3 className="detail-section__title">Evidence references</h3>
                <p className="detail-note">
                  Source records that support this finding. Observed records are
                  direct database rows; computed observations and baselines are
                  derived. Absence evidence indicates an expected record was not
                  found.
                </p>
                <ul className="evidence-list" aria-label="Evidence references">
                  {ev.evidence_refs.map((ref, i) => (
                    <li
                      key={`${ref.source_type}:${ref.source_id}:${i}`}
                      className={`evidence-item evidence-item--${
                        ref.source_type === "ABSENCE" ? "absence" :
                        ["ENTITY_PERIOD_OBSERVATION", "PEER_BASELINE"].includes(ref.source_type)
                          ? "derived"
                          : "direct"
                      }`}
                    >
                      <div className="evidence-item__header">
                        <span className="evidence-item__type">
                          {SOURCE_TYPE_LABEL[ref.source_type] ?? ref.source_type}
                        </span>
                        <span className="evidence-item__role">{ref.role.replace(/_/g, " ")}</span>
                        {ref.period_label && (
                          <span className="evidence-item__period mono">{ref.period_label}</span>
                        )}
                      </div>
                      <p className="evidence-item__reason">{ref.reason}</p>
                      <p className="evidence-item__id mono">{ref.source_id}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {!ev && (
              <p className="detail-note" style={{ marginTop: "var(--space-4)" }}>
                Evidence was not pre-computed for this finding in the current
                request. Re-run an assessment to populate the review queue with
                full evidence.
              </p>
            )}

            {/* ── Navigation ────────────────────────────────────────── */}
            <div className="finding-nav">
              <Link to="/findings" className="btn btn--secondary">
                ← Back to Finding Explorer
              </Link>
              <Link
                to={`/queue?entity_id=${encodeURIComponent(f.entity_id)}`}
                className="btn btn--secondary"
              >
                View entity in queue
              </Link>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
