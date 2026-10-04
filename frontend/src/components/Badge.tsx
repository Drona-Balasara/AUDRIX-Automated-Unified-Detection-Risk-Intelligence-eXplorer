import "./Badge.css";

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info" | "muted";

interface BadgeProps {
  label: string;
  tone?: BadgeTone;
}

/**
 * Compact status/category badge. Communicates state through text AND color
 * (never color alone). Used for priority, confidence, status, category.
 */
export function Badge({ label, tone = "neutral" }: BadgeProps) {
  return (
    <span className={`badge badge--${tone}`}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Domain-specific badge helpers
// ---------------------------------------------------------------------------

const PRIORITY_TONE: Record<string, BadgeTone> = {
  CRITICAL: "danger",
  HIGH: "warning",
  MEDIUM: "info",
  LOW: "muted",
};

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <Badge
      label={priority}
      tone={PRIORITY_TONE[priority] ?? "neutral"}
    />
  );
}

const CONFIDENCE_TONE: Record<string, BadgeTone> = {
  HIGH: "success",
  MODERATE: "info",
  LOW: "muted",
  UNKNOWN: "neutral",
};

export function ConfidenceBadge({ confidence }: { confidence: string | null }) {
  const label = confidence ?? "—";
  return (
    <Badge
      label={label}
      tone={CONFIDENCE_TONE[label] ?? "neutral"}
    />
  );
}

const STATUS_TONE: Record<string, BadgeTone> = {
  OPEN: "info",
  IN_REVIEW: "warning",
  REVIEWED: "success",
  DISMISSED: "muted",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      label={status.replace("_", " ")}
      tone={STATUS_TONE[status] ?? "neutral"}
    />
  );
}

const CATEGORY_LABEL: Record<string, string> = {
  EXECUTION_GAP: "Execution Gap",
  MONITORING_GAP: "Monitoring Gap",
  ANOMALY: "Anomaly",
  PEER_DEVIATION: "Peer Deviation",
  METRIC_RISK_DIVERGENCE: "Metric Risk",
  WORKFLOW_PATTERN: "Workflow Pattern",
};

export function CategoryBadge({ category }: { category: string }) {
  return (
    <Badge
      label={CATEGORY_LABEL[category] ?? category}
      tone="neutral"
    />
  );
}
