import { useState, useEffect, useRef } from "react";
import type { DeviationCauses, LLMContextSummary } from "../types/hazop";
import { generateCauses, approveCauses } from "../services/api";

interface CausesReviewTableProps {
  nodeId: string;
  selectedDeviationTypes: string[];
  onApproved: () => void;
  onBack: () => void;
}

type ProgressStep = {
  label: string;
  status: "pending" | "active" | "done" | "error";
};

export default function CausesReviewTable({
  nodeId,
  selectedDeviationTypes,
  onApproved,
  onBack,
}: CausesReviewTableProps) {
  const [deviationCauses, setDeviationCauses] = useState<DeviationCauses[]>([]);
  const [llmContext, setLlmContext] = useState<LLMContextSummary | null>(null);
  const [contextExpanded, setContextExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [smeName, setSmeName] = useState("");
  const [comments, setComments] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [editingCause, setEditingCause] = useState<{ devIdx: number; causeIdx: number } | null>(null);
  const [editText, setEditText] = useState("");
  const [newCauseText, setNewCauseText] = useState<Record<number, string>>({});
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([]);

  // Auto-trigger generation on mount
  const hasTriggered = useRef(false);
  useEffect(() => {
    if (!hasTriggered.current && nodeId && selectedDeviationTypes.length > 0) {
      hasTriggered.current = true;
      handleGenerate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Generate causes
  const handleGenerate = async () => {
    setLoading(true);
    setError("");
    setProgressSteps([
      { label: "Loading validated equipment & instruments from P&ID", status: "active" },
      { label: "Mapping deviations to equipment (ontology rules)", status: "pending" },
      { label: "Generating causes using AI for each deviation — this takes time", status: "pending" },
      { label: "Preparing causes for SME review", status: "pending" },
    ]);

    try {
      // Step 1 → 2 transition quickly (these are fast server-side operations)
      const stepTimers = [
        setTimeout(() => {
          setProgressSteps((prev) => prev.map((s, i) =>
            i === 0 ? { ...s, status: "done" as const } :
            i === 1 ? { ...s, status: "active" as const } : s
          ));
        }, 800),
        // Step 2 → 3: ontology mapping is fast, LLM is the bottleneck
        setTimeout(() => {
          setProgressSteps((prev) => prev.map((s, i) =>
            i <= 1 ? { ...s, status: "done" as const } :
            i === 2 ? { ...s, status: "active" as const } : s
          ));
        }, 2000),
      ];

      const result = await generateCauses(nodeId, selectedDeviationTypes);

      // Clear timers if API finishes before them
      stepTimers.forEach(clearTimeout);

      // Mark steps 1-3 done, step 4 active
      setProgressSteps([
        { label: "Loading validated equipment & instruments from P&ID", status: "done" },
        { label: "Mapping deviations to equipment (ontology rules)", status: "done" },
        { label: "Generating causes using AI for each deviation", status: "done" },
        { label: "Preparing causes for SME review", status: "active" },
      ]);

      // Brief delay to show final step completing
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((prev) => prev.map((s) => ({ ...s, status: "done" as const })));

      setDeviationCauses(result.deviation_causes);
      setLlmContext(result.llm_context ?? null);
      setGenerated(true);
    } catch (err: unknown) {
      let message = "Failed to generate causes. Check if the backend is running.";
      if (err && typeof err === "object" && "response" in err) {
        const axiosErr = err as { response?: { data?: { detail?: string }; status?: number } };
        message = axiosErr.response?.data?.detail
          || `Server error (${axiosErr.response?.status || "unknown"})`;
      } else if (err instanceof Error) {
        message = err.message;
      }
      setError(message);
      setProgressSteps((prev) => prev.map((s) =>
        s.status === "active" ? { ...s, status: "error" as const } : s
      ));
      console.error("[CausesReviewTable] Generate causes error:", err);
    } finally {
      setLoading(false);
    }
  };

  // Delete a cause
  const handleDeleteCause = (devIdx: number, causeIdx: number) => {
    setDeviationCauses((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      dev.causes = dev.causes.filter((_, i) => i !== causeIdx);
      updated[devIdx] = dev;
      return updated;
    });
  };

  // Start editing a cause
  const handleStartEdit = (devIdx: number, causeIdx: number) => {
    setEditingCause({ devIdx, causeIdx });
    setEditText(deviationCauses[devIdx].causes[causeIdx]);
  };

  // Save edited cause
  const handleSaveEdit = () => {
    if (!editingCause || !editText.trim()) return;
    setDeviationCauses((prev) => {
      const updated = [...prev];
      const dev = { ...updated[editingCause.devIdx] };
      dev.causes = [...dev.causes];
      dev.causes[editingCause.causeIdx] = editText.trim();
      updated[editingCause.devIdx] = dev;
      return updated;
    });
    setEditingCause(null);
    setEditText("");
  };

  // Cancel editing
  const handleCancelEdit = () => {
    setEditingCause(null);
    setEditText("");
  };

  // Add a new cause
  const handleAddCause = (devIdx: number) => {
    const text = newCauseText[devIdx]?.trim();
    if (!text) return;
    setDeviationCauses((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      dev.causes = [...dev.causes, text];
      updated[devIdx] = dev;
      return updated;
    });
    setNewCauseText((prev) => ({ ...prev, [devIdx]: "" }));
  };

  // Submit approved causes
  const handleApprove = async () => {
    if (!smeName.trim()) {
      setError("Please enter your name to approve causes.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await approveCauses(nodeId, smeName.trim(), deviationCauses, comments || undefined);
      onApproved();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to save approved causes.";
      setError(message);
      console.error("[CausesReviewTable] Approve causes error:", err);
    } finally {
      setSubmitting(false);
    }
  };

  // Group deviations by equipment tag
  const groupedByEquipment: Record<string, { indices: number[] }> = {};
  deviationCauses.forEach((dc, idx) => {
    if (!groupedByEquipment[dc.equipment_tag]) {
      groupedByEquipment[dc.equipment_tag] = { indices: [] };
    }
    groupedByEquipment[dc.equipment_tag].indices.push(idx);
  });

  const isAllDone = progressSteps.length > 0 && progressSteps.every((s) => s.status === "done");
  const hasError = progressSteps.some((s) => s.status === "error");

  // ---- Progress Modal ----
  if (loading || (progressSteps.length > 0 && !generated && !hasError)) {
    return (
      <>
        {/* Progress Modal Overlay */}
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
            {/* Header */}
            <div className="px-5 py-4 border-b border-gray-200 bg-gray-50">
              <div className="flex items-center gap-3">
                {!isAllDone && !hasError && (
                  <span className="inline-block w-5 h-5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin flex-shrink-0" />
                )}
                <h3 className="text-sm font-semibold text-gray-900">Generating Causes for Review</h3>
              </div>
              <p className="text-xs text-gray-500 mt-1">
                Analyzing {selectedDeviationTypes.length} deviation types using extracted P&ID equipment data
              </p>
              {/* Overall progress bar */}
              <div className="mt-3 w-full bg-gray-200 rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all duration-700 ${hasError ? "bg-red-500" : "bg-blue-500"}`}
                  style={{
                    width: `${(progressSteps.filter((s) => s.status === "done").length / progressSteps.length) * 100}%`,
                  }}
                />
              </div>
            </div>

            {/* Steps list */}
            <div className="px-5 py-4 space-y-3">
              {progressSteps.map((step, idx) => (
                <div key={idx} className="flex items-start gap-3">
                  {/* Status icon */}
                  <div className="mt-0.5 flex-shrink-0">
                    {step.status === "pending" && (
                      <span className="inline-block w-4 h-4 rounded-full border-2 border-gray-300" />
                    )}
                    {step.status === "active" && (
                      <span className="inline-block w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
                    )}
                    {step.status === "done" && (
                      <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                    {step.status === "error" && (
                      <svg className="w-4 h-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    )}
                  </div>
                  {/* Step label */}
                  <span
                    className={`text-xs ${
                      step.status === "active" ? "text-blue-700 font-medium" :
                      step.status === "done" ? "text-green-700" :
                      step.status === "error" ? "text-red-600 font-medium" :
                      "text-gray-400"
                    }`}
                  >
                    {step.label}
                  </span>
                </div>
              ))}
            </div>

            {/* Error message */}
            {hasError && error && (
              <div className="px-5 pb-3">
                <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                  <p className="text-xs text-red-700">{error}</p>
                </div>
              </div>
            )}

            {/* Footer */}
            <div className="px-5 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-between">
              {!hasError ? (
                <p className="text-xs text-gray-500 animate-pulse">
                  This may take a minute... please wait
                </p>
              ) : (
                <p className="text-xs text-red-600 font-medium">
                  Generation failed
                </p>
              )}
              {hasError && (
                <div className="flex gap-2">
                  <button
                    onClick={onBack}
                    className="px-3 py-1.5 text-xs font-medium rounded bg-gray-200 text-gray-700 hover:bg-gray-300"
                  >
                    Go Back
                  </button>
                  <button
                    onClick={() => {
                      hasTriggered.current = false;
                      setProgressSteps([]);
                      setError("");
                      handleGenerate();
                    }}
                    className="px-3 py-1.5 text-xs font-medium rounded bg-blue-600 text-white hover:bg-blue-700"
                  >
                    Retry
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </>
    );
  }

  // ---- Main Causes Review Table ----
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Review Causes</h2>
            <p className="text-xs text-gray-500 mt-1">
              Review, edit, add, or remove causes for each deviation. Click on a cause to edit it.
            </p>
          </div>
          <div className="text-xs text-gray-500">
            {deviationCauses.length} deviations | {deviationCauses.reduce((sum, d) => sum + d.causes.length, 0)} total causes
          </div>
        </div>
      </div>

      {/* LLM Context Transparency Panel */}
      {llmContext && (
        <div className="bg-white rounded-lg border border-blue-100 overflow-hidden">
          {/* Collapsible header */}
          <button
            onClick={() => setContextExpanded((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-3 bg-blue-50 hover:bg-blue-100 transition-colors text-left"
          >
            <div className="flex items-center gap-2">
              <svg className="w-4 h-4 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="text-xs font-semibold text-blue-800">
                LLM Input Context — What was provided to AI for cause generation
              </span>
              <span className="text-xs text-blue-600 bg-blue-100 px-1.5 py-0.5 rounded">
                {llmContext.included_equipment.length} equipment&nbsp;·&nbsp;
                {llmContext.included_instruments.length} instrument{llmContext.included_instruments.length !== 1 ? "s" : ""}&nbsp;·&nbsp;
                {llmContext.excluded_instruments.length} excluded
              </span>
            </div>
            <svg
              className={`w-4 h-4 text-blue-500 transition-transform ${contextExpanded ? "rotate-180" : ""}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {contextExpanded && (
            <div className="px-4 py-4 grid grid-cols-1 gap-4 md:grid-cols-2">

              {/* PROVIDED to LLM */}
              <div>
                <h4 className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-2 flex items-center gap-1">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  Provided to LLM
                </h4>

                {/* Pressure values */}
                {(llmContext.upstream_pressure_psig != null) && (
                  <div className="mb-3">
                    <p className="text-[11px] font-medium text-gray-500 mb-1">Pressure Context</p>
                    <div className="space-y-1">
                      {llmContext.upstream_pressure_psig != null && (
                        <div className="flex items-center gap-2 px-2 py-1 bg-green-50 rounded text-xs">
                          <span className="font-mono text-green-800 font-medium">
                            {llmContext.upstream_pressure_psig} PSIG
                          </span>
                          <span className="text-green-600">Maximum Upstream Pressure Source (SME-entered)</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Equipment */}
                <div className="mb-3">
                  <p className="text-[11px] font-medium text-gray-500 mb-1">
                    Equipment ({llmContext.included_equipment.length})
                  </p>
                  <div className="space-y-1">
                    {llmContext.included_equipment.map((eq) => (
                      <div key={eq.tag} className="flex items-center gap-2 px-2 py-1 bg-green-50 rounded text-xs">
                        <span className="font-mono text-green-800 font-medium">{eq.tag}</span>
                        <span className="text-green-600">{eq.equipment_type}</span>
                        {eq.design_pressure != null && (
                          <span className="font-mono text-green-700 font-medium">{eq.design_pressure} PSIG</span>
                        )}
                        <span className="ml-auto text-[10px] text-green-500 italic">
                          {eq.design_pressure != null ? "design pressure included" : "context only"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Included instruments */}
                <div>
                  <p className="text-[11px] font-medium text-gray-500 mb-1">
                    Control Valves ({llmContext.included_instruments.length})
                  </p>
                  {llmContext.included_instruments.length === 0 ? (
                    <p className="text-xs text-gray-400 italic px-2">
                      No control valves detected — only equipment tags used for cause context.
                    </p>
                  ) : (
                    <div className="space-y-1">
                      {llmContext.included_instruments.map((item) => (
                        <div key={item.tag} className="px-2 py-1 bg-green-50 rounded text-xs">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-green-800 font-medium">{item.tag}</span>
                            <span className="text-green-600">{item.instrument_type}</span>
                          </div>
                          <p className="text-[10px] text-green-500 italic mt-0.5">{item.reason}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* NOT PROVIDED to LLM */}
              <div>
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  Not Provided to LLM
                </h4>
                <p className="text-[11px] text-gray-400 mb-2">
                  These are safeguards, transmitters, indicators, or gauges — not root causes of deviations.
                </p>
                {llmContext.excluded_instruments.length === 0 ? (
                  <p className="text-xs text-gray-400 italic px-2">All instruments were included.</p>
                ) : (
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {llmContext.excluded_instruments.map((item) => (
                      <div key={item.tag} className="px-2 py-1 bg-gray-50 border border-gray-100 rounded text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-gray-600 font-medium">{item.tag}</span>
                          <span className="text-gray-500">{item.instrument_type}</span>
                        </div>
                        <p className="text-[10px] text-gray-400 italic mt-0.5">{item.reason}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

            </div>
          )}
        </div>
      )}

      {/* Causes Table grouped by equipment */}
      {Object.entries(groupedByEquipment).map(([equipTag, { indices }]) => (
        <div key={equipTag} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          {/* Equipment Header */}
          <div className="bg-gray-50 px-4 py-2 border-b border-gray-200">
            <h3 className="text-sm font-semibold text-gray-800">{equipTag}</h3>
          </div>

          {/* Deviations for this equipment */}
          <div className="divide-y divide-gray-100">
            {indices.map((devIdx) => {
              const dc = deviationCauses[devIdx];
              return (
                <div key={dc.deviation_id} className="p-4">
                  {/* Deviation Name */}
                  <div className="flex items-center gap-2 mb-3">
                    <span className="px-2 py-0.5 text-xs font-medium bg-blue-50 text-blue-700 rounded">
                      {dc.guideword}
                    </span>
                    <span className="px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-700 rounded">
                      {dc.parameter}
                    </span>
                    <span className="text-sm font-medium text-gray-900">{dc.deviation}</span>
                    <span className="text-xs text-gray-400 ml-auto">
                      {dc.causes.length} cause{dc.causes.length !== 1 ? "s" : ""}
                    </span>
                  </div>

                  {/* Causes List */}
                  <div className="space-y-1.5">
                    {dc.causes.map((cause, causeIdx) => (
                      <div
                        key={causeIdx}
                        className="flex items-start gap-2 group"
                      >
                        {editingCause?.devIdx === devIdx && editingCause?.causeIdx === causeIdx ? (
                          // Editing mode
                          <div className="flex-1 flex gap-2">
                            <input
                              type="text"
                              value={editText}
                              onChange={(e) => setEditText(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleSaveEdit();
                                if (e.key === "Escape") handleCancelEdit();
                              }}
                              className="flex-1 text-xs px-2 py-1 border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                              autoFocus
                            />
                            <button
                              onClick={handleSaveEdit}
                              className="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-700"
                            >
                              Save
                            </button>
                            <button
                              onClick={handleCancelEdit}
                              className="text-xs px-2 py-1 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          // Display mode
                          <>
                            <span className="text-xs text-gray-400 mt-0.5 select-none">&#8226;</span>
                            <span
                              onClick={() => handleStartEdit(devIdx, causeIdx)}
                              className="flex-1 text-xs text-gray-700 cursor-pointer hover:bg-yellow-50 hover:text-gray-900 px-1 py-0.5 rounded transition-colors"
                              title="Click to edit"
                            >
                              {cause}
                            </span>
                            <button
                              onClick={() => handleDeleteCause(devIdx, causeIdx)}
                              className="opacity-0 group-hover:opacity-100 text-xs text-red-400 hover:text-red-600 px-1 transition-opacity"
                              title="Remove cause"
                            >
                              &times;
                            </button>
                          </>
                        )}
                      </div>
                    ))}

                    {/* Add new cause */}
                    <div className="flex gap-2 mt-2">
                      <input
                        type="text"
                        value={newCauseText[devIdx] || ""}
                        onChange={(e) =>
                          setNewCauseText((prev) => ({ ...prev, [devIdx]: e.target.value }))
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleAddCause(devIdx);
                        }}
                        placeholder="Add a new cause..."
                        className="flex-1 text-xs px-2 py-1 border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-300"
                      />
                      <button
                        onClick={() => handleAddCause(devIdx)}
                        disabled={!newCauseText[devIdx]?.trim()}
                        className="text-xs px-3 py-1 bg-green-50 text-green-700 rounded hover:bg-green-100 disabled:opacity-40 disabled:cursor-not-allowed font-medium"
                      >
                        + Add
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {/* Approval Section */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-semibold text-gray-900 mb-3">Approve Causes</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">SME Name *</label>
            <input
              type="text"
              value={smeName}
              onChange={(e) => setSmeName(e.target.value)}
              placeholder="Enter your name"
              className="w-full text-sm px-3 py-2 border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Comments (optional)</label>
            <input
              type="text"
              value={comments}
              onChange={(e) => setComments(e.target.value)}
              placeholder="Any comments on causes review"
              className="w-full text-sm px-3 py-2 border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>

        {error && <p className="text-xs text-red-600 mt-2">{error}</p>}

        <div className="flex justify-between items-center mt-4">
          <button
            onClick={onBack}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded hover:bg-gray-200"
          >
            Back to Deviations
          </button>
          <button
            onClick={handleApprove}
            disabled={submitting || !smeName.trim()}
            className="px-6 py-2 text-sm font-medium text-white bg-green-600 rounded hover:bg-green-700 disabled:bg-gray-300"
          >
            {submitting ? "Saving..." : "Approve Causes & Continue"}
          </button>
        </div>
      </div>
    </div>
  );
}
