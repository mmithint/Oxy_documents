import type { RiskScore, RiskLevel } from "../types/hazop";
import { RISK_COLORS, RISK_LABELS } from "../types/hazop";

interface RiskBadgeProps {
  score: RiskScore | null;
  label: string;
}

export default function RiskBadge({ score, label }: RiskBadgeProps) {
  if (!score) {
    return (
      <span className="inline-flex items-center px-2 py-1 text-xs font-medium rounded bg-gray-50 text-gray-400 border border-gray-200">
        {label}: N/A
      </span>
    );
  }

  const level = score.risk_level as RiskLevel;
  const colorClass = RISK_COLORS[level] || "bg-gray-100 text-gray-700";
  const levelLabel = RISK_LABELS[level] || "Unknown";

  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 text-xs font-semibold rounded border ${colorClass}`}
      title={`${label}: C=${score.consequence} P=${score.probability} → ${level} (${levelLabel})`}
    >
      {label}: {level}
      <span className="ml-1 font-normal opacity-75">
        ({score.consequence}/{score.probability})
      </span>
    </span>
  );
}
