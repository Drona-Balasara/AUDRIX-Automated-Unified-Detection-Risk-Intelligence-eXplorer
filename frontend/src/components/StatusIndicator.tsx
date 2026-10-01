import "./StatusIndicator.css";
import type { ConnectionStatus } from "../types/health";

type Tone = "neutral" | "success" | "warning" | "danger";

const TONE_BY_STATUS: Record<ConnectionStatus, Tone> = {
  loading: "neutral",
  connected: "success",
  unavailable: "danger",
  error: "warning",
};

interface StatusIndicatorProps {
  status: ConnectionStatus;
  label: string;
}

/**
 * A dot-plus-label status marker. Status is conveyed by the text label as well
 * as color, so meaning never depends on color alone. The dot is decorative
 * (aria-hidden); the label is the accessible content.
 */
export function StatusIndicator({ status, label }: StatusIndicatorProps) {
  const tone = TONE_BY_STATUS[status];
  return (
    <span className={`status-indicator status-indicator--${tone}`}>
      <span className="status-indicator__dot" aria-hidden="true" />
      <span className="status-indicator__label">{label}</span>
    </span>
  );
}
