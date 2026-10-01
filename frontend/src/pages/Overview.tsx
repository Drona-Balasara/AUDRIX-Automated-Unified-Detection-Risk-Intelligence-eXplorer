import "./Overview.css";
import { SystemStatus } from "../components/SystemStatus";
import type { HealthState } from "../types/health";

interface OverviewProps {
  health: HealthState;
  onRetry: () => void;
}

/**
 * Phase 1 landing view. States plainly what the system currently is and shows
 * the live backend connection. It does not present any analytics, metrics, or
 * findings — those belong to later phases.
 */
export function Overview({ health, onRetry }: OverviewProps) {
  return (
    <div className="overview">
      <div className="overview__intro">
        <h2>Overview</h2>
        <p className="overview__lead">
          SAT-SA is an evidence-driven supervisory analytics platform for
          security operations. This build is the project foundation: the
          application shell, backend service, and system-status reporting.
          Analytical capabilities are not implemented yet.
        </p>
        <span className="overview__phase">Phase 1 · Project Foundation</span>
      </div>

      <SystemStatus state={health} onRetry={onRetry} />
    </div>
  );
}
