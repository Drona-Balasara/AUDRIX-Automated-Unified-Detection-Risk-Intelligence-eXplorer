import type { HealthResponse } from "../types/health";

/**
 * API service layer.
 *
 * The backend base URL comes from the environment (`VITE_API_BASE_URL`) with a
 * local-development fallback, so no production address is embedded in
 * components. All network access to the backend goes through this module.
 */

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1"
).replace(/\/$/, "");

/** Raised when the backend responds, but not in the way we expect. */
export class UnexpectedResponseError extends Error {}

/** Raised when the backend cannot be reached at all. */
export class ConnectionError extends Error {}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.service === "string" &&
    typeof v.status === "string" &&
    typeof v.version === "string" &&
    typeof v.environment === "string" &&
    typeof v.database === "string"
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
    // Network-level failure: backend not running, DNS, CORS, offline, etc.
    throw new ConnectionError("Could not reach the backend.");
  }

  if (!response.ok) {
    throw new UnexpectedResponseError(`Unexpected status ${response.status}.`);
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new UnexpectedResponseError("Response was not valid JSON.");
  }

  if (!isHealthResponse(body)) {
    throw new UnexpectedResponseError("Response did not match the expected shape.");
  }

  return body;
}
