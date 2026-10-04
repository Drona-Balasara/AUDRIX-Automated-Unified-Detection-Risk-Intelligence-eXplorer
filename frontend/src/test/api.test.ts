/**
 * Tests for the typed API client layer (services/api.ts).
 *
 * Uses vitest + jsdom. The global fetch is mocked via vi.stubGlobal so no
 * real network requests are made.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchHealth,
  fetchEntities,
  fetchFindings,
  fetchQueue,
  fetchQueueSummary,
  transitionQueueStatus,
  ConnectionError,
  ApiResponseError,
  UnexpectedResponseError,
} from "../services/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    }),
  );
}

function mockFetchNetworkError() {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Network error")));
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── fetchHealth ──────────────────────────────────────────────────────────────

describe("fetchHealth", () => {
  it("returns health data on 200", async () => {
    const data = {
      service: "sat-sa-api",
      status: "ok",
      version: "0.1.0",
      environment: "test",
      database: "ok",
    };
    mockFetch(200, data);
    const result = await fetchHealth();
    expect(result.service).toBe("sat-sa-api");
    expect(result.status).toBe("ok");
  });

  it("throws ConnectionError when fetch rejects", async () => {
    mockFetchNetworkError();
    await expect(fetchHealth()).rejects.toBeInstanceOf(ConnectionError);
  });

  it("throws UnexpectedResponseError on non-200", async () => {
    mockFetch(500, { detail: "error" });
    await expect(fetchHealth()).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});

// ── fetchEntities ─────────────────────────────────────────────────────────────

describe("fetchEntities", () => {
  it("returns paginated entity list", async () => {
    const data = {
      total: 2,
      limit: 50,
      offset: 0,
      items: [
        {
          entity_id: "ENT-01",
          name: "Test SOC",
          sector: "FINANCE",
          peer_group: "FINANCE",
          scale: "MEDIUM",
          asset_count_estimate: 10,
          analyst_headcount: 5,
          data_period_start: "2024-01-01T00:00:00Z",
          data_period_end: "2024-07-01T00:00:00Z",
        },
      ],
    };
    mockFetch(200, data);
    const result = await fetchEntities({ limit: 50, offset: 0 });
    expect(result.total).toBe(2);
    expect(result.items[0].entity_id).toBe("ENT-01");
  });

  it("throws ConnectionError on network failure", async () => {
    mockFetchNetworkError();
    await expect(fetchEntities({})).rejects.toBeInstanceOf(ConnectionError);
  });
});

// ── fetchFindings ─────────────────────────────────────────────────────────────

describe("fetchFindings", () => {
  it("returns finding list", async () => {
    const data = {
      total: 5,
      limit: 25,
      offset: 0,
      items: [
        {
          finding_key: "EG-001:ENT-01:ALR-000001",
          analytic_id: "EG-001",
          finding_type: "CRITICAL_ALERT_NO_ESCALATION",
          category: "EXECUTION_GAP",
          entity_id: "ENT-01",
          period_label: "2024-01",
          summary: "Potential Escalation Gap",
          confidence: "MODERATE",
          evidence_count: 3,
          details: {},
        },
      ],
    };
    mockFetch(200, data);
    const result = await fetchFindings({ limit: 25 });
    expect(result.total).toBe(5);
    expect(result.items[0].analytic_id).toBe("EG-001");
  });

  it("throws ApiResponseError on 400", async () => {
    mockFetch(400, { detail: "invalid filter" });
    const err = await fetchFindings({ category: "BOGUS" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiResponseError);
    expect((err as ApiResponseError).status).toBe(400);
  });
});

// ── fetchQueue ─────────────────────────────────────────────────────────────────

describe("fetchQueue", () => {
  it("returns queue list", async () => {
    const data = {
      total: 3,
      limit: 25,
      offset: 0,
      items: [
        {
          queue_id: "abc123def456",
          finding_key: "EG-001:ENT-01:ALR-000001",
          analytic_id: "EG-001",
          finding_type: "CRITICAL_ALERT_NO_ESCALATION",
          entity_id: "ENT-01",
          period_label: "2024-01",
          title: "Potential Escalation Gap — ENT-01",
          category: "EXECUTION_GAP",
          priority: "CRITICAL",
          confidence: "MODERATE",
          evidence_count: 3,
          status: "OPEN",
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
          reviewed_at: null,
          reviewer_ref: null,
          review_note: null,
        },
      ],
    };
    mockFetch(200, data);
    const result = await fetchQueue({});
    expect(result.total).toBe(3);
    expect(result.items[0].priority).toBe("CRITICAL");
  });
});

// ── fetchQueueSummary ─────────────────────────────────────────────────────────

describe("fetchQueueSummary", () => {
  it("returns summary data", async () => {
    const data = {
      total: 100,
      by_status: { OPEN: 80, IN_REVIEW: 10, REVIEWED: 10, DISMISSED: 0 },
      by_priority: { CRITICAL: 20, HIGH: 30, MEDIUM: 25, LOW: 25 },
      by_category: { EXECUTION_GAP: 40, ANOMALY: 10, WORKFLOW_PATTERN: 50 },
    };
    mockFetch(200, data);
    const result = await fetchQueueSummary();
    expect(result.total).toBe(100);
    expect(result.by_status["OPEN"]).toBe(80);
  });
});

// ── transitionQueueStatus ─────────────────────────────────────────────────────

describe("transitionQueueStatus", () => {
  it("returns updated queue item on success", async () => {
    const data = {
      queue_id: "abc123",
      finding_key: "EG-001:ENT-01:ALR-000001",
      analytic_id: "EG-001",
      finding_type: "CRITICAL_ALERT_NO_ESCALATION",
      entity_id: "ENT-01",
      period_label: "2024-01",
      title: "Potential Escalation Gap — ENT-01",
      category: "EXECUTION_GAP",
      priority: "CRITICAL",
      confidence: "MODERATE",
      evidence_count: 3,
      status: "IN_REVIEW",
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T01:00:00Z",
      reviewed_at: null,
      reviewer_ref: "supervisor-1",
      review_note: null,
    };
    mockFetch(200, data);
    const result = await transitionQueueStatus("abc123", { new_status: "IN_REVIEW", reviewer_ref: "supervisor-1" });
    expect(result.status).toBe("IN_REVIEW");
    expect(result.reviewer_ref).toBe("supervisor-1");
  });

  it("throws ApiResponseError(409) on invalid transition", async () => {
    mockFetch(409, { detail: "Cannot transition from OPEN to OPEN." });
    const err = await transitionQueueStatus("abc123", { new_status: "OPEN" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiResponseError);
    expect((err as ApiResponseError).status).toBe(409);
    expect((err as ApiResponseError).detail).toContain("Cannot transition");
  });

  it("throws ApiResponseError(404) when item not found", async () => {
    mockFetch(404, { detail: "queue item not found" });
    const err = await transitionQueueStatus("badid", { new_status: "REVIEWED" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiResponseError);
    expect((err as ApiResponseError).status).toBe(404);
  });
});
