import { useState } from "react";
import { approveDeviation, rejectDeviation, requestRevision } from "../services/api";
import type { Deviation } from "../types/hazop";

interface ApprovalControlsProps {
  deviation: Deviation;
  onStatusChanged: () => void;
}

export default function ApprovalControls({ deviation, onStatusChanged }: ApprovalControlsProps) {
  const [reviewerName, setReviewerName] = useState("");
  const [comments, setComments] = useState("");
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const handleAction = async (action: "approve" | "reject" | "revision") => {
    if (!reviewerName.trim()) return;
    setLoading(true);
    try {
      if (action === "approve") {
        await approveDeviation(deviation.deviation_id, reviewerName, comments || undefined);
      } else if (action === "reject") {
        await rejectDeviation(deviation.deviation_id, reviewerName, comments || undefined);
      } else {
        await requestRevision(deviation.deviation_id, reviewerName, comments || undefined);
      }
      onStatusChanged();
    } catch {
      // Error handling
    } finally {
      setLoading(false);
      setExpanded(false);
    }
  };

  if (deviation.status === "approved") {
    return (
      <div className="text-xs text-green-600">
        Approved by {deviation.reviewed_by}
      </div>
    );
  }

  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="text-xs text-blue-600 hover:text-blue-800 font-medium"
      >
        Review
      </button>
    );
  }

  return (
    <div className="space-y-2 min-w-[200px]">
      <input
        type="text"
        placeholder="Your name"
        value={reviewerName}
        onChange={(e) => setReviewerName(e.target.value)}
        className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
      />
      <textarea
        placeholder="Comments (optional)"
        value={comments}
        onChange={(e) => setComments(e.target.value)}
        rows={2}
        className="w-full px-2 py-1 text-xs border border-gray-300 rounded resize-none"
      />
      <div className="flex gap-1">
        <button
          onClick={() => handleAction("approve")}
          disabled={loading || !reviewerName.trim()}
          className="flex-1 px-2 py-1 text-xs font-medium text-white bg-green-600 rounded hover:bg-green-700 disabled:bg-gray-300"
        >
          Approve
        </button>
        <button
          onClick={() => handleAction("reject")}
          disabled={loading || !reviewerName.trim()}
          className="flex-1 px-2 py-1 text-xs font-medium text-white bg-red-600 rounded hover:bg-red-700 disabled:bg-gray-300"
        >
          Reject
        </button>
        <button
          onClick={() => handleAction("revision")}
          disabled={loading || !reviewerName.trim()}
          className="flex-1 px-2 py-1 text-xs font-medium text-white bg-amber-600 rounded hover:bg-amber-700 disabled:bg-gray-300"
        >
          Revise
        </button>
      </div>
      <button
        onClick={() => setExpanded(false)}
        className="w-full text-xs text-gray-400 hover:text-gray-600"
      >
        Cancel
      </button>
    </div>
  );
}
