/** Shape of the backend `GET /api/v1/health` response. */
export interface HealthResponse {
  service: string;
  status: "ok" | "degraded";
  version: string;
  environment: string;
  database: "ok" | "unavailable";
}

/**
 * Connection state for the backend health check.
 *
 * - `loading`      request in flight
 * - `connected`    backend reachable and responded
 * - `unavailable`  backend could not be reached (network/connection failure)
 * - `error`        reached the backend but the response was unexpected
 */
export type ConnectionStatus = "loading" | "connected" | "unavailable" | "error";

export interface HealthState {
  status: ConnectionStatus;
  data: HealthResponse | null;
  /** Safe, human-readable message for display. Never contains internals. */
  message: string | null;
}
