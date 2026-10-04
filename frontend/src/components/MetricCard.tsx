import "./MetricCard.css";

interface MetricCardProps {
  label: string;
  value: number | string;
  sub?: string;
  tone?: "neutral" | "warning" | "danger" | "success";
}

/**
 * Compact metric summary card. Used in Command Center to show analytical
 * counts derived directly from real API responses. Never shows fabricated data.
 */
export function MetricCard({ label, value, sub, tone = "neutral" }: MetricCardProps) {
  return (
    <div className={`metric-card metric-card--${tone}`}>
      <span className="metric-card__value">{value}</span>
      <span className="metric-card__label">{label}</span>
      {sub && <span className="metric-card__sub">{sub}</span>}
    </div>
  );
}
