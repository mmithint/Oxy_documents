import type { ReviewStatus } from "../types/hazop";
import { STATUS_COLORS } from "../types/hazop";

interface StatusBadgeProps {
  status: ReviewStatus;
}

const STATUS_LABELS: Record<ReviewStatus, string> = {
  draft: "Draft",
  pending_review: "Pending Review",
  approved: "Approved",
  rejected: "Rejected",
  revision_requested: "Revision Requested",
};

export default function StatusBadge({ status }: StatusBadgeProps) {
  const colorClass = STATUS_COLORS[status] || "bg-gray-100 text-gray-700";
  const label = STATUS_LABELS[status] || status;

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${colorClass}`}>
      {label}
    </span>
  );
}
