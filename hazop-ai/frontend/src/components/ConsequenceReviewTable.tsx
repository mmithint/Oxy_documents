import { useState, useEffect, useRef } from "react";
import type { DeviationConsequences, OverpressureCalc } from "../types/hazop";
import { generateConsequences, approveConsequences } from "../services/api";

interface ConsequenceReviewTableProps {
  nodeId: string;
  onApproved: () => void;
  onBack: () => void;
}

type ProgressStep = {
  label: string;
  status: "pending" | "active" | "done" | "error";
};

type EditTarget = { devIdx: number; field: "intermediate" | "consequences"; itemIdx: number };

// Table-view inline edit state
type TableIntermEdit = { devIdx: number; value: string } | null;
type TableScenarioEdit = { devIdx: number; scenarioVal: string; consequencesVal: string } | null;

export default function ConsequenceReviewTable({
  nodeId,
  onApproved,
  onBack,
}: ConsequenceReviewTableProps) {
  const [deviationConsequences, setDeviationConsequences] = useState<DeviationConsequences[]>([]);
  const [loading, setLoading] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [smeName, setSmeName] = useState("");
  const [comments, setComments] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([]);

  // Card-view edit state
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null);
  const [editText, setEditText] = useState("");
  const [newItemText, setNewItemText] = useState<Record<string, string>>({});
  const [editingScenario, setEditingScenario] = useState<number | null>(null);
  const [editingCategory, setEditingCategory] = useState<number | null>(null);

  // View mode
  const [viewMode, setViewMode] = useState<"card" | "table">("card");

  // Table-view inline edit state
  const [tableIntermEdit, setTableIntermEdit] = useState<TableIntermEdit>(null);
  const [tableScenarioEdit, setTableScenarioEdit] = useState<TableScenarioEdit>(null);
  const [tableEditingCategory, setTableEditingCategory] = useState<number | null>(null);

  const hasTriggered = useRef(false);
  useEffect(() => {
    if (!hasTriggered.current && nodeId) {
      hasTriggered.current = true;
      handleGenerate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleGenerate = async () => {
    setLoading(true);
    setError("");
    setProgressSteps([
      { label: "Loading approved causes and equipment data from P&ID", status: "active" },
      { label: "Calculating overpressure ratios for pressure deviations", status: "pending" },
      { label: "Generating consequences using AI and knowledge documents", status: "pending" },
      { label: "Preparing consequences for SME review", status: "pending" },
    ]);

    try {
      const stepTimers = [
        setTimeout(() => {
          setProgressSteps((prev) => prev.map((s, i) =>
            i === 0 ? { ...s, status: "done" as const } :
            i === 1 ? { ...s, status: "active" as const } : s
          ));
        }, 800),
        setTimeout(() => {
          setProgressSteps((prev) => prev.map((s, i) =>
            i <= 1 ? { ...s, status: "done" as const } :
            i === 2 ? { ...s, status: "active" as const } : s
          ));
        }, 2000),
      ];

      const result = await generateConsequences(nodeId);
      stepTimers.forEach(clearTimeout);

      setProgressSteps([
        { label: "Loading approved causes and equipment data from P&ID", status: "done" },
        { label: "Calculating overpressure ratios for pressure deviations", status: "done" },
        { label: "Generating consequences using AI and knowledge documents", status: "done" },
        { label: "Preparing consequences for SME review", status: "active" },
      ]);
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((prev) => prev.map((s) => ({ ...s, status: "done" as const })));

      setDeviationConsequences(result.deviation_consequences);
      setGenerated(true);
    } catch (err: unknown) {
      let message = "Failed to generate consequences. Check if the backend is running.";
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
    } finally {
      setLoading(false);
    }
  };

  // ---- Card-view list editing helpers ----

  const handleDeleteItem = (devIdx: number, field: "intermediate" | "consequences", itemIdx: number) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      if (field === "intermediate") {
        dev.intermediate_consequences = dev.intermediate_consequences.filter((_, i) => i !== itemIdx);
      } else {
        dev.consequences = dev.consequences.filter((_, i) => i !== itemIdx);
      }
      updated[devIdx] = dev;
      return updated;
    });
  };

  const handleStartEdit = (devIdx: number, field: "intermediate" | "consequences", itemIdx: number) => {
    const dev = deviationConsequences[devIdx];
    const text = field === "intermediate"
      ? dev.intermediate_consequences[itemIdx]
      : dev.consequences[itemIdx];
    setEditTarget({ devIdx, field, itemIdx });
    setEditText(text);
  };

  const handleSaveEdit = () => {
    if (!editTarget || !editText.trim()) return;
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      const dev = { ...updated[editTarget.devIdx] };
      if (editTarget.field === "intermediate") {
        dev.intermediate_consequences = [...dev.intermediate_consequences];
        dev.intermediate_consequences[editTarget.itemIdx] = editText.trim();
      } else {
        dev.consequences = [...dev.consequences];
        dev.consequences[editTarget.itemIdx] = editText.trim();
      }
      updated[editTarget.devIdx] = dev;
      return updated;
    });
    setEditTarget(null);
    setEditText("");
  };

  const handleAddItem = (devIdx: number, field: "intermediate" | "consequences") => {
    const key = `${devIdx}-${field}`;
    const text = newItemText[key]?.trim();
    if (!text) return;
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      if (field === "intermediate") {
        dev.intermediate_consequences = [...dev.intermediate_consequences, text];
      } else {
        dev.consequences = [...dev.consequences, text];
      }
      updated[devIdx] = dev;
      return updated;
    });
    setNewItemText((prev) => ({ ...prev, [key]: "" }));
  };

  const handleScenarioChange = (devIdx: number, value: string) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], scenario_comments: value };
      return updated;
    });
  };

  const handleCategoryChange = (devIdx: number, value: string) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], consequence_category: value };
      return updated;
    });
    setEditingCategory(null);
    setTableEditingCategory(null);
  };

  // ---- Table-view edit helpers ----

  const openTableIntermEdit = (devIdx: number) => {
    const value = deviationConsequences[devIdx].intermediate_consequences.join("\n");
    setTableIntermEdit({ devIdx, value });
  };

  const saveTableIntermEdit = () => {
    if (!tableIntermEdit) return;
    const items = tableIntermEdit.value.split("\n").map((s) => s.trim()).filter(Boolean);
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      updated[tableIntermEdit.devIdx] = { ...updated[tableIntermEdit.devIdx], intermediate_consequences: items };
      return updated;
    });
    setTableIntermEdit(null);
  };

  const openTableScenarioEdit = (devIdx: number) => {
    const dev = deviationConsequences[devIdx];
    setTableScenarioEdit({
      devIdx,
      scenarioVal: dev.scenario_comments ?? "",
      consequencesVal: dev.consequences.join("\n"),
    });
  };

  const saveTableScenarioEdit = () => {
    if (!tableScenarioEdit) return;
    const consequences = tableScenarioEdit.consequencesVal
      .split("\n").map((s) => s.trim()).filter(Boolean);
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      updated[tableScenarioEdit.devIdx] = {
        ...updated[tableScenarioEdit.devIdx],
        scenario_comments: tableScenarioEdit.scenarioVal || null,
        consequences,
      };
      return updated;
    });
    setTableScenarioEdit(null);
  };

  // ---- Approve ----

  const handleApprove = async () => {
    if (!smeName.trim()) {
      setError("Please enter your name before approving.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await approveConsequences(nodeId, smeName.trim(), deviationConsequences, comments || undefined);
      onApproved();
    } catch (err: unknown) {
      let message = "Failed to approve consequences.";
      if (err && typeof err === "object" && "response" in err) {
        const axiosErr = err as { response?: { data?: { detail?: string } } };
        message = axiosErr.response?.data?.detail || message;
      }
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  // ---- Group by equipment (card view) ----
  const grouped = deviationConsequences.reduce<Record<string, { tag: string; items: { dev: DeviationConsequences; idx: number }[] }>>(
    (acc, dev, idx) => {
      const tag = dev.equipment_tag;
      if (!acc[tag]) acc[tag] = { tag, items: [] };
      acc[tag].items.push({ dev, idx });
      return acc;
    },
    {}
  );

  // ---- Render ----

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Consequence Review</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Review AI-generated consequences before generating the full HAZOP report. Edit any field inline.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* View toggle — only shown when data is loaded */}
          {generated && (
            <div className="flex items-center gap-1 bg-gray-100 rounded-md p-0.5">
              <button
                onClick={() => setViewMode("card")}
                title="Card view"
                className={`px-3 py-1 text-xs rounded ${viewMode === "card" ? "bg-white shadow text-gray-900 font-medium" : "text-gray-500 hover:text-gray-700"}`}
              >
                ▤ Card
              </button>
              <button
                onClick={() => setViewMode("table")}
                title="Table view"
                className={`px-3 py-1 text-xs rounded ${viewMode === "table" ? "bg-white shadow text-gray-900 font-medium" : "text-gray-500 hover:text-gray-700"}`}
              >
                ⊟ Table
              </button>
            </div>
          )}
          <button
            onClick={onBack}
            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
          >
            ← Back to Causes
          </button>
        </div>
      </div>

      {/* Progress Modal */}
      {loading && (
        <div className="bg-white rounded-lg border border-blue-200 p-5 space-y-3">
          <h3 className="text-sm font-semibold text-gray-800">Generating Consequences…</h3>
          <div className="space-y-2">
            {progressSteps.map((s, i) => (
              <div key={i} className="flex items-center gap-2.5">
                {s.status === "done" && (
                  <span className="w-4 h-4 rounded-full bg-green-500 flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0">✓</span>
                )}
                {s.status === "active" && (
                  <span className="w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin flex-shrink-0" />
                )}
                {s.status === "pending" && (
                  <span className="w-4 h-4 rounded-full border-2 border-gray-200 flex-shrink-0" />
                )}
                {s.status === "error" && (
                  <span className="w-4 h-4 rounded-full bg-red-500 flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0">✕</span>
                )}
                <span className={`text-xs ${s.status === "active" ? "text-blue-700 font-medium" : s.status === "done" ? "text-green-700" : s.status === "error" ? "text-red-600" : "text-gray-400"}`}>
                  {s.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-xs text-red-700">{error}</div>
      )}

      {/* ---- CARD VIEW ---- */}
      {generated && viewMode === "card" && (
        <div className="space-y-6">
          {Object.values(grouped).map(({ tag, items }) => (
            <div key={tag} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="bg-gray-50 border-b border-gray-200 px-4 py-2.5 flex items-center gap-2">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Equipment</span>
                <span className="text-sm font-bold text-gray-900">{tag}</span>
              </div>
              <div className="divide-y divide-gray-100">
                {items.map(({ dev, idx }) => (
                  <DeviationConsequenceCard
                    key={dev.deviation_id}
                    dev={dev}
                    devIdx={idx}
                    editTarget={editTarget}
                    editText={editText}
                    editingScenario={editingScenario}
                    editingCategory={editingCategory}
                    newItemText={newItemText}
                    onDeleteItem={handleDeleteItem}
                    onStartEdit={handleStartEdit}
                    onSaveEdit={handleSaveEdit}
                    onCancelEdit={() => { setEditTarget(null); setEditText(""); }}
                    onEditTextChange={setEditText}
                    onAddItem={handleAddItem}
                    onNewItemChange={(key, val) => setNewItemText((prev) => ({ ...prev, [key]: val }))}
                    onScenarioChange={handleScenarioChange}
                    onEditScenario={(i) => setEditingScenario(i)}
                    onSaveScenario={() => setEditingScenario(null)}
                    onCategoryChange={handleCategoryChange}
                    onEditCategory={(i) => setEditingCategory(i)}
                  />
                ))}
              </div>
            </div>
          ))}

          {/* Approval Section */}
          <ApprovalSection
            smeName={smeName}
            comments={comments}
            submitting={submitting}
            onSmeNameChange={setSmeName}
            onCommentsChange={setComments}
            onApprove={handleApprove}
            onBack={onBack}
          />
        </div>
      )}

      {/* ---- TABLE VIEW ---- */}
      {generated && viewMode === "table" && (
        <div className="space-y-4">
          <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs border-collapse">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-48">
                      Deviation
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-52">
                      Cause
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-32">
                      Drawing / Reference
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-60">
                      Intermediate Consequences
                      <span className="ml-1 text-gray-400 normal-case font-normal text-[10px]">(click to edit)</span>
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-28">
                      Category
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide w-72">
                      Scenario Comments / Final Impacts
                      <span className="ml-1 text-gray-400 normal-case font-normal text-[10px]">(click to edit)</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {deviationConsequences.map((dev, idx) => {
                    const catColors: Record<string, string> = {
                      PAF: "bg-red-100 text-red-700 border-red-300",
                      "PD/LOR": "bg-amber-100 text-amber-700 border-amber-300",
                      ECR: "bg-green-100 text-green-700 border-green-300",
                    };
                    const isEditingInterm = tableIntermEdit?.devIdx === idx;
                    const isEditingScenario = tableScenarioEdit?.devIdx === idx;
                    const isEditingCat = tableEditingCategory === idx;

                    return (
                      <tr key={dev.deviation_id} className="hover:bg-gray-50 align-top">
                        {/* Deviation */}
                        <td className="px-3 py-2.5 border-r border-gray-100">
                          <div className="flex flex-col gap-1">
                            <span className="font-mono text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded self-start">
                              {dev.guideword}
                            </span>
                            <span className="font-medium text-gray-900 leading-snug">{dev.deviation}</span>
                            <span className="text-gray-400 text-[10px]">{dev.equipment_tag}</span>
                            {dev.overpressure_calc?.exceeds_2x && (
                              <span className="text-[10px] bg-amber-100 text-amber-700 border border-amber-300 rounded px-1.5 py-0.5 self-start font-semibold">
                                ⚠ Vessel Rupture ({dev.overpressure_calc.ratio.toFixed(2)}×)
                              </span>
                            )}
                          </div>
                        </td>

                        {/* Causes */}
                        <td className="px-3 py-2.5 border-r border-gray-100">
                          <ul className="space-y-1">
                            {dev.causes.map((cause, ci) => (
                              <li key={ci} className="flex items-start gap-1.5 text-gray-700 leading-snug">
                                <span className="text-gray-300 flex-shrink-0 mt-0.5">•</span>
                                {cause}
                              </li>
                            ))}
                          </ul>
                        </td>

                        {/* Drawing / Reference */}
                        <td className="px-3 py-2.5 border-r border-gray-100">
                          {dev.drawing_references.length > 0 ? (
                            <span className="text-blue-700 font-medium">
                              {dev.drawing_references.join(", ")}
                            </span>
                          ) : (
                            <span className="text-gray-300 italic">—</span>
                          )}
                        </td>

                        {/* Intermediate Consequences — click to edit */}
                        <td
                          className="px-3 py-2.5 border-r border-gray-100 cursor-pointer"
                          onClick={() => { if (!isEditingInterm) openTableIntermEdit(idx); }}
                        >
                          {isEditingInterm ? (
                            <div className="space-y-1.5" onClick={(e) => e.stopPropagation()}>
                              <textarea
                                autoFocus
                                rows={5}
                                value={tableIntermEdit!.value}
                                onChange={(e) => setTableIntermEdit({ devIdx: idx, value: e.target.value })}
                                className="w-full px-2 py-1 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
                                placeholder="One item per line…"
                              />
                              <div className="flex gap-1">
                                <button
                                  onClick={saveTableIntermEdit}
                                  className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600"
                                >Save</button>
                                <button
                                  onClick={() => setTableIntermEdit(null)}
                                  className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300"
                                >Cancel</button>
                              </div>
                            </div>
                          ) : (
                            <div className="group relative">
                              {dev.intermediate_consequences.length > 0 ? (
                                <ul className="space-y-1">
                                  {dev.intermediate_consequences.map((item, ii) => (
                                    <li key={ii} className="flex items-start gap-1.5 text-gray-700 leading-snug">
                                      <span className="text-gray-300 flex-shrink-0 mt-0.5">•</span>
                                      {item}
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <span className="text-gray-300 italic">Click to add…</span>
                              )}
                              <span className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 text-[10px] text-blue-400 bg-white px-1 rounded border border-blue-200 transition-opacity">
                                Edit
                              </span>
                            </div>
                          )}
                        </td>

                        {/* Consequence Category — click to change */}
                        <td className="px-3 py-2.5 border-r border-gray-100">
                          {isEditingCat ? (
                            <div className="flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>
                              {["PAF", "PD/LOR", "ECR"].map((cat) => (
                                <button
                                  key={cat}
                                  onClick={() => handleCategoryChange(idx, cat)}
                                  className={`text-[11px] px-2 py-0.5 rounded border font-medium text-left ${catColors[cat] ?? ""} hover:opacity-80`}
                                >
                                  {cat}
                                </button>
                              ))}
                              <button
                                onClick={() => setTableEditingCategory(null)}
                                className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5"
                              >Cancel</button>
                            </div>
                          ) : (
                            <button
                              onClick={() => setTableEditingCategory(idx)}
                              title="Click to change"
                              className={`text-[11px] px-2 py-0.5 rounded border font-medium ${
                                dev.consequence_category
                                  ? (catColors[dev.consequence_category] ?? "bg-gray-100 text-gray-600 border-gray-300")
                                  : "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                              } hover:opacity-80`}
                            >
                              {dev.consequence_category ?? "Set…"}
                            </button>
                          )}
                          {dev.pec && (
                            <div className="mt-1.5">
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 border border-purple-300 font-medium">
                                PEC: {dev.pec}
                              </span>
                            </div>
                          )}
                        </td>

                        {/* Scenario Comments / Final Impacts — click to edit */}
                        <td
                          className="px-3 py-2.5 cursor-pointer"
                          onClick={() => { if (!isEditingScenario) openTableScenarioEdit(idx); }}
                        >
                          {isEditingScenario ? (
                            <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                              <div>
                                <div className="text-[10px] font-semibold text-gray-500 uppercase mb-1">Final Impacts (one per line)</div>
                                <textarea
                                  autoFocus
                                  rows={4}
                                  value={tableScenarioEdit!.consequencesVal}
                                  onChange={(e) => setTableScenarioEdit((prev) => prev ? { ...prev, consequencesVal: e.target.value } : null)}
                                  className="w-full px-2 py-1 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
                                  placeholder="One consequence per line…"
                                />
                              </div>
                              <div>
                                <div className="text-[10px] font-semibold text-gray-500 uppercase mb-1">Scenario Comments</div>
                                <textarea
                                  rows={3}
                                  value={tableScenarioEdit!.scenarioVal}
                                  onChange={(e) => setTableScenarioEdit((prev) => prev ? { ...prev, scenarioVal: e.target.value } : null)}
                                  className="w-full px-2 py-1 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
                                  placeholder="Narrative description…"
                                />
                              </div>
                              <div className="flex gap-1">
                                <button
                                  onClick={saveTableScenarioEdit}
                                  className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600"
                                >Save</button>
                                <button
                                  onClick={() => setTableScenarioEdit(null)}
                                  className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300"
                                >Cancel</button>
                              </div>
                            </div>
                          ) : (
                            <div className="group relative space-y-2">
                              {/* Final consequences */}
                              {dev.consequences.length > 0 && (
                                <ul className="space-y-1">
                                  {dev.consequences.map((c, ci) => (
                                    <li key={ci} className="flex items-start gap-1.5 text-gray-800 font-medium leading-snug">
                                      <span className="text-gray-400 flex-shrink-0 mt-0.5">▸</span>
                                      {c}
                                    </li>
                                  ))}
                                </ul>
                              )}
                              {/* Scenario comments */}
                              {dev.scenario_comments ? (
                                <p className="text-gray-500 italic leading-relaxed text-[11px] border-t border-gray-100 pt-1.5">
                                  {dev.scenario_comments}
                                </p>
                              ) : (
                                dev.consequences.length === 0 && (
                                  <span className="text-gray-300 italic">Click to add…</span>
                                )
                              )}
                              <span className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 text-[10px] text-blue-400 bg-white px-1 rounded border border-blue-200 transition-opacity">
                                Edit
                              </span>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Approval Section */}
          <ApprovalSection
            smeName={smeName}
            comments={comments}
            submitting={submitting}
            onSmeNameChange={setSmeName}
            onCommentsChange={setComments}
            onApprove={handleApprove}
            onBack={onBack}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ApprovalSection — shared between card and table views
// ---------------------------------------------------------------------------

function ApprovalSection({
  smeName, comments, submitting,
  onSmeNameChange, onCommentsChange, onApprove, onBack,
}: {
  smeName: string;
  comments: string;
  submitting: boolean;
  onSmeNameChange: (v: string) => void;
  onCommentsChange: (v: string) => void;
  onApprove: () => void;
  onBack: () => void;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-gray-900">SME Approval</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1">
            SME Name <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={smeName}
            onChange={(e) => onSmeNameChange(e.target.value)}
            placeholder="Enter your name"
            className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1">Comments (optional)</label>
          <input
            type="text"
            value={comments}
            onChange={(e) => onCommentsChange(e.target.value)}
            placeholder="Any notes for the record"
            className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={onApprove}
          disabled={!smeName.trim() || submitting}
          className="px-5 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {submitting ? "Approving…" : "Approve Consequences & Generate HAZOP"}
        </button>
        <button
          onClick={onBack}
          className="px-4 py-2 text-sm font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200"
        >
          ← Back
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OverpressureBadge
// ---------------------------------------------------------------------------

function OverpressureBadge({ calc }: { calc: OverpressureCalc }) {
  return (
    <div className={`rounded-md p-3 text-xs space-y-1 ${calc.exceeds_2x ? "bg-amber-50 border border-amber-300" : "bg-gray-50 border border-gray-200"}`}>
      <div className="flex items-center gap-2 font-semibold">
        <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold ${calc.exceeds_2x ? "bg-amber-500 text-white" : "bg-gray-400 text-white"}`}>
          {calc.exceeds_2x ? "⚠ VESSEL RUPTURE" : "OVERPRESSURE"}
        </span>
        <span className="text-gray-700">
          Max Credible: {calc.max_credible_pressure} PSIG ÷ Design: {calc.design_pressure} PSIG = {calc.ratio.toFixed(2)}×
        </span>
      </div>
      {calc.exceeds_2x && (
        <>
          <div className="text-amber-800 font-medium">
            Ratio {">"} 2.0 → {calc.assumed_leak_size} leak assumed
          </div>
          {calc.source && (
            <div className="text-gray-500 italic">Source: {calc.source}</div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EditableList — reusable inline-editable list (card view)
// ---------------------------------------------------------------------------

function EditableList({
  items,
  devIdx,
  field,
  editTarget,
  editText,
  newItemText,
  onDelete,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onEditTextChange,
  onAddItem,
  onNewItemChange,
}: {
  items: string[];
  devIdx: number;
  field: "intermediate" | "consequences";
  editTarget: EditTarget | null;
  editText: string;
  newItemText: Record<string, string>;
  onDelete: (devIdx: number, field: "intermediate" | "consequences", itemIdx: number) => void;
  onStartEdit: (devIdx: number, field: "intermediate" | "consequences", itemIdx: number) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onEditTextChange: (val: string) => void;
  onAddItem: (devIdx: number, field: "intermediate" | "consequences") => void;
  onNewItemChange: (key: string, val: string) => void;
}) {
  const newKey = `${devIdx}-${field}`;
  return (
    <div className="space-y-1">
      {items.map((item, itemIdx) => {
        const isEditing = editTarget?.devIdx === devIdx && editTarget.field === field && editTarget.itemIdx === itemIdx;
        return (
          <div key={itemIdx} className="group flex items-start gap-2">
            <span className="mt-1 text-gray-300 flex-shrink-0">•</span>
            {isEditing ? (
              <div className="flex-1 flex gap-1.5">
                <input
                  autoFocus
                  type="text"
                  value={editText}
                  onChange={(e) => onEditTextChange(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") onSaveEdit(); if (e.key === "Escape") onCancelEdit(); }}
                  className="flex-1 px-2 py-0.5 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                />
                <button onClick={onSaveEdit} className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600">Save</button>
                <button onClick={onCancelEdit} className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">Cancel</button>
              </div>
            ) : (
              <>
                <span className="flex-1 text-xs text-gray-700 leading-relaxed">{item}</span>
                <div className="opacity-0 group-hover:opacity-100 flex gap-1 flex-shrink-0 transition-opacity">
                  <button onClick={() => onStartEdit(devIdx, field, itemIdx)} className="text-[10px] text-blue-500 hover:text-blue-700">Edit</button>
                  <button onClick={() => onDelete(devIdx, field, itemIdx)} className="text-[10px] text-red-400 hover:text-red-600">×</button>
                </div>
              </>
            )}
          </div>
        );
      })}
      {/* Add new item */}
      <div className="flex gap-1.5 mt-1">
        <input
          type="text"
          placeholder="+ Add…"
          value={newItemText[newKey] ?? ""}
          onChange={(e) => onNewItemChange(newKey, e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") onAddItem(devIdx, field); }}
          className="flex-1 px-2 py-0.5 text-xs border border-dashed border-gray-300 rounded focus:outline-none focus:border-blue-300 placeholder-gray-300"
        />
        {newItemText[newKey]?.trim() && (
          <button onClick={() => onAddItem(devIdx, field)} className="text-[10px] px-2 py-0.5 bg-gray-100 text-gray-600 rounded hover:bg-gray-200">Add</button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeviationConsequenceCard (card view)
// ---------------------------------------------------------------------------

function DeviationConsequenceCard({
  dev, devIdx,
  editTarget, editText, editingScenario, editingCategory, newItemText,
  onDeleteItem, onStartEdit, onSaveEdit, onCancelEdit, onEditTextChange,
  onAddItem, onNewItemChange,
  onScenarioChange, onEditScenario, onSaveScenario,
  onCategoryChange, onEditCategory,
}: {
  dev: DeviationConsequences;
  devIdx: number;
  editTarget: EditTarget | null;
  editText: string;
  editingScenario: number | null;
  editingCategory: number | null;
  newItemText: Record<string, string>;
  onDeleteItem: (devIdx: number, field: "intermediate" | "consequences", itemIdx: number) => void;
  onStartEdit: (devIdx: number, field: "intermediate" | "consequences", itemIdx: number) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onEditTextChange: (val: string) => void;
  onAddItem: (devIdx: number, field: "intermediate" | "consequences") => void;
  onNewItemChange: (key: string, val: string) => void;
  onScenarioChange: (devIdx: number, val: string) => void;
  onEditScenario: (devIdx: number) => void;
  onSaveScenario: () => void;
  onCategoryChange: (devIdx: number, val: string) => void;
  onEditCategory: (devIdx: number) => void;
}) {
  const catColors: Record<string, string> = {
    PAF: "bg-red-100 text-red-700 border-red-300",
    "PD/LOR": "bg-amber-100 text-amber-700 border-amber-300",
    ECR: "bg-green-100 text-green-700 border-green-300",
  };

  return (
    <div className="p-4 space-y-4">
      {/* Deviation header */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded text-gray-600">{dev.guideword}</span>
        <span className="text-sm font-semibold text-gray-900">{dev.deviation}</span>
        {dev.consequence_category && (
          editingCategory === devIdx ? (
            <div className="flex gap-1">
              {["PAF", "PD/LOR", "ECR"].map((cat) => (
                <button key={cat} onClick={() => onCategoryChange(devIdx, cat)}
                  className={`text-xs px-2 py-0.5 rounded border ${catColors[cat] ?? ""} hover:opacity-80`}>
                  {cat}
                </button>
              ))}
            </div>
          ) : (
            <button onClick={() => onEditCategory(devIdx)}
              title="Click to change"
              className={`text-xs px-2 py-0.5 rounded border font-medium ${catColors[dev.consequence_category] ?? "bg-gray-100 text-gray-600 border-gray-300"} hover:opacity-80`}>
              {dev.consequence_category}
            </button>
          )
        )}
        {dev.pec && (
          <span className="text-xs px-2 py-0.5 rounded bg-purple-100 text-purple-700 border border-purple-300 font-medium">
            PEC: {dev.pec}
          </span>
        )}
        {dev.drawing_references.length > 0 && (
          <span className="text-xs text-gray-400 ml-auto">
            Ref: {dev.drawing_references.join(", ")}
          </span>
        )}
      </div>

      {/* Overpressure calculation badge */}
      {dev.overpressure_calc && (
        <OverpressureBadge calc={dev.overpressure_calc} />
      )}

      {/* Intermediate Consequences */}
      <div>
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
          Intermediate Consequences
        </div>
        <EditableList
          items={dev.intermediate_consequences}
          devIdx={devIdx}
          field="intermediate"
          editTarget={editTarget}
          editText={editText}
          newItemText={newItemText}
          onDelete={onDeleteItem}
          onStartEdit={onStartEdit}
          onSaveEdit={onSaveEdit}
          onCancelEdit={onCancelEdit}
          onEditTextChange={onEditTextChange}
          onAddItem={onAddItem}
          onNewItemChange={onNewItemChange}
        />
      </div>

      {/* Final Consequences */}
      <div>
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
          Final Consequences / Worst Credible Outcomes
          <span className="ml-1 font-normal normal-case text-gray-400">(no safeguards assumed)</span>
        </div>
        <EditableList
          items={dev.consequences}
          devIdx={devIdx}
          field="consequences"
          editTarget={editTarget}
          editText={editText}
          newItemText={newItemText}
          onDelete={onDeleteItem}
          onStartEdit={onStartEdit}
          onSaveEdit={onSaveEdit}
          onCancelEdit={onCancelEdit}
          onEditTextChange={onEditTextChange}
          onAddItem={onAddItem}
          onNewItemChange={onNewItemChange}
        />
      </div>

      {/* Scenario Comments */}
      <div>
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
          Scenario Comments / Final Impact Narrative
        </div>
        {editingScenario === devIdx ? (
          <div className="space-y-1">
            <textarea
              autoFocus
              rows={4}
              value={dev.scenario_comments ?? ""}
              onChange={(e) => onScenarioChange(devIdx, e.target.value)}
              className="w-full px-3 py-2 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
            />
            <button onClick={onSaveScenario} className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600">Done</button>
          </div>
        ) : (
          <div
            onClick={() => onEditScenario(devIdx)}
            className="text-xs text-gray-600 leading-relaxed p-2.5 bg-gray-50 rounded border border-dashed border-gray-200 cursor-pointer hover:border-blue-300 hover:bg-blue-50 transition-colors min-h-[48px]"
          >
            {dev.scenario_comments || <span className="text-gray-300 italic">Click to add scenario narrative…</span>}
          </div>
        )}
      </div>
    </div>
  );
}
