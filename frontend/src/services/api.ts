/**
 * SAT-SA API service layer.
 *
 * All network access to the backend goes through this module. The backend
 * base URL comes from the environment (VITE_API_BASE_URL) with a local
 * development fallback — no production address is embedded in components.
 *
 * VITE_ variables are exposed to client-side bundle code after build, so
 * never place secrets or backend-only values in them.
 */

import type {
  AssessmentRunResponse,
  EntityListResponse,
  EntityResponse,
  FindingDetailResponse,
  FindingListResponse,
  QueueItemResponse,
  QueueListResponse,
  QueueSummaryResponse,
  StatusTransitionRequest,
} from "../types/api";
import type { HealthResponse } from "../types/health";

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1"
).replace(/\/$/, "");

// ---------------------------------------------------------------------------
// Error classes
// ---------------------------------------------------------------------------

/** Raised when the backend responds with an unexpected HTTP status. */
export class ApiResponseError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API error ${status}: ${detail}`);
    this.name = "ApiResponseError";
  }
}

/** Raised when the backend cannot be reached at all. */
export class ConnectionError extends Error {
  constructor() {
    super("Could not reach the backend.");
    this.name = "ConnectionError";
  }
}

/** Raised when the backend response does not match the expected shape. */
export class UnexpectedResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnexpectedResponseError";
  }
}

// ---------------------------------------------------------------------------
// Core fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json", ...options?.headers },
      ...options,
    });
  } catch {
    throw new ConnectionError();
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new UnexpectedResponseError("Response was not valid JSON.");
  }

  if (!response.ok) {
    const detail =
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as Record<string, unknown>).detail === "string"
        ? (body as Record<string, unknown>).detail as string
        : `HTTP ${response.status}`;
    throw new ApiResponseError(response.status, detail);
  }

  return body as T;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

function isHealthResponse(v: unknown): v is HealthResponse {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r.service === "string" &&
    typeof r.status === "string" &&
    typeof r.version === "string" &&
    typeof r.environment === "string" &&
    typeof r.database === "string"
  );
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/health`, {
      headers: { Accept: "application/json" },
      signal,
    });
  } catch {
    throw new ConnectionError();
  }
  if (!response.ok) throw new UnexpectedResponseError(`Unexpected status ${response.status}.`);
  let body: unknown;
  try { body = await response.json(); } catch { throw new UnexpectedResponseError("Response was not valid JSON."); }
  if (!isHealthResponse(body)) throw new UnexpectedResponseError("Response did not match the expected shape.");
  return body;
}

// ---------------------------------------------------------------------------
// Entities
// ---------------------------------------------------------------------------

export async function fetchEntities(
  params: { limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<EntityListResponse> {
  const q = new URLSearchParams();
  if (params.limit !== undefined) q.set("limit", String(params.limit));
  if (params.offset !== undefined) q.set("offset", String(params.offset));
  return apiFetch<EntityListResponse>(`/entities?${q}`, { signal });
}

export async function fetchEntity(
  entityId: string,
  signal?: AbortSignal,
): Promise<EntityResponse> {
  return apiFetch<EntityResponse>(`/entities/${encodeURIComponent(entityId)}`, { signal });
}

// ---------------------------------------------------------------------------
// Assessment
// ---------------------------------------------------------------------------

export async function runAssessment(
  signal?: AbortSignal,
): Promise<AssessmentRunResponse> {
  return apiFetch<AssessmentRunResponse>("/assessment/run", {
    method: "POST",
    signal,
  });
}

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

export interface FindingsParams {
  entity_id?: string;
  analytic_id?: string;
  category?: string;
  period_label?: string;
  include_evidence?: boolean;
  limit?: number;
  offset?: number;
}

export async function fetchFindings(
  params: FindingsParams,
  signal?: AbortSignal,
): Promise<FindingListResponse> {
  const q = new URLSearchParams();
  if (params.entity_id) q.set("entity_id", params.entity_id);
  if (params.analytic_id) q.set("analytic_id", params.analytic_id);
  if (params.category) q.set("category", params.category);
  if (params.period_label) q.set("period_label", params.period_label);
  if (params.include_evidence) q.set("include_evidence", "true");
  if (params.limit !== undefined) q.set("limit", String(params.limit));
  if (params.offset !== undefined) q.set("offset", String(params.offset));
  return apiFetch<FindingListResponse>(`/findings?${q}`, { signal });
}

export async function fetchFinding(
  findingKey: string,
  signal?: AbortSignal,
): Promise<FindingDetailResponse> {
  return apiFetch<FindingDetailResponse>(
    `/findings/${encodeURIComponent(findingKey)}`,
    { signal },
  );
}

// ---------------------------------------------------------------------------
// Review queue
// ---------------------------------------------------------------------------

export interface QueueParams {
  status?: string;
  priority?: string;
  entity_id?: string;
  category?: string;
  limit?: number;
  offset?: number;
}

export async function fetchQueue(
  params: QueueParams,
  signal?: AbortSignal,
): Promise<QueueListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.priority) q.set("priority", params.priority);
  if (params.entity_id) q.set("entity_id", params.entity_id);
  if (params.category) q.set("category", params.category);
  if (params.limit !== undefined) q.set("limit", String(params.limit));
  if (params.offset !== undefined) q.set("offset", String(params.offset));
  return apiFetch<QueueListResponse>(`/queue?${q}`, { signal });
}

export async function fetchQueueSummary(
  signal?: AbortSignal,
): Promise<QueueSummaryResponse> {
  return apiFetch<QueueSummaryResponse>("/queue/summary", { signal });
}

export async function fetchQueueItem(
  queueId: string,
  signal?: AbortSignal,
): Promise<QueueItemResponse> {
  return apiFetch<QueueItemResponse>(
    `/queue/${encodeURIComponent(queueId)}`,
    { signal },
  );
}

export async function transitionQueueStatus(
  queueId: string,
  body: StatusTransitionRequest,
  signal?: AbortSignal,
): Promise<QueueItemResponse> {
  return apiFetch<QueueItemResponse>(
    `/queue/${encodeURIComponent(queueId)}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
  );
}
