/**
 * TypeScript types matching the Phase 11 Pydantic response contracts.
 *
 * These are deliberately kept as plain interfaces rather than classes so they
 * can be pattern-matched against API JSON without a parsing library.
 */

// ---------------------------------------------------------------------------
// Shared / pagination
// ---------------------------------------------------------------------------

export interface ApiError {
  detail: string;
}

// ---------------------------------------------------------------------------
// Entities
// ---------------------------------------------------------------------------

export interface EntityResponse {
  entity_id: string;
  name: string;
  sector: string;
  peer_group: string;
  scale: string;
  asset_count_estimate: number;
  analyst_headcount: number;
  data_period_start: string;
  data_period_end: string;
}

export interface EntityListResponse {
  total: number;
  limit: number;
  offset: number;
  items: EntityResponse[];
}

// ---------------------------------------------------------------------------
// Assessment
// ---------------------------------------------------------------------------

export interface AssessmentAnalyticSummary {
  analytic_id: string;
  finding_count: number;
  status: string;
}

export interface AssessmentRunResponse {
  entity_count: number;
  total_findings: number;
  analytics: AssessmentAnalyticSummary[];
  queue_items_total: number;
  queue_inserted: number;
  queue_unchanged: number;
  duration_ms: number;
}

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

export interface EvidenceRefResponse {
  source_type: string;
  source_id: string;
  role: string;
  period_label: string | null;
  reason: string;
}

export interface FindingEvidenceResponse {
  finding_key: string;
  analytic_id: string;
  confidence: string;
  confidence_note: string;
  confidence_factors: string[];
  evidence_count: number;
  evidence_refs: EvidenceRefResponse[];
}

export interface FindingResponse {
  finding_key: string;
  analytic_id: string;
  finding_type: string;
  category: string;
  entity_id: string;
  period_label: string;
  summary: string;
  confidence: string | null;
  evidence_count: number;
  details: Record<string, unknown>;
}

export interface FindingListResponse {
  total: number;
  limit: number;
  offset: number;
  items: FindingResponse[];
}

export interface FindingDetailResponse {
  finding: FindingResponse;
  evidence: FindingEvidenceResponse | null;
}

// ---------------------------------------------------------------------------
// Review queue
// ---------------------------------------------------------------------------

export interface QueueItemResponse {
  queue_id: string;
  finding_key: string;
  analytic_id: string;
  finding_type: string;
  entity_id: string;
  period_label: string;
  title: string;
  category: string;
  priority: string;
  confidence: string;
  evidence_count: number;
  status: string;
  created_at: string;
  updated_at: string;
  reviewed_at: string | null;
  reviewer_ref: string | null;
  review_note: string | null;
}

export interface QueueListResponse {
  total: number;
  limit: number;
  offset: number;
  items: QueueItemResponse[];
}

export interface QueueSummaryResponse {
  total: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  by_category: Record<string, number>;
}

export interface StatusTransitionRequest {
  new_status: string;
  reviewer_ref?: string;
  review_note?: string;
}
