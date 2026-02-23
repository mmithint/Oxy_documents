import { useState, useEffect, useRef } from "react";
import type { DeviationConsequences, OverpressureCalc, CategoryRow } from "../types/hazop";
import { generateConsequences, approveConsequences } from "../services/api";

interface ConsequenceReviewTableProps {
  nodeId: string;
  selectedDeviationTypes: string[];
  onApproved: () => void;
  onBack: () => void;
  initialConsequences: DeviationConsequences[] | null;
  onConsequencesChange: (consequences: DeviationConsequences[]) => void;
}

type ProgressStep = {
  label: string;
  status: "pending" | "active" | "done" | "error";
};

type EditTarget = { devIdx: number; field: "intermediate" | "consequences"; itemIdx: number };

// Table-view inline edit state
type TableIntermEdit = { devIdx: number; value: string } | null;
type TableCatRowEdit = { devIdx: number; rowIdx: number; scenarioVal: string; consequencesVal: string } | null;

// ---------------------------------------------------------------------------
// Helpers — initialize category rows from legacy flat data
// ---------------------------------------------------------------------------

function initCategoryRows(dev: DeviationConsequences): CategoryRow[] {
  return [
    {
      category: "PAF",
      consequences: dev.consequences ?? [],
      scenario_comments: dev.scenario_comments ?? null,
      current_risk: dev.current_risk ?? null,
      pec: dev.pec ?? null,
    },
    {
      category: "PD/LOR",
      consequences: ["Downtime of approximately X to X months"],
      scenario_comments: null,
      current_risk: null,
      pec: null,
    },
    {
      category: "ECR",
      consequences: [],
      scenario_comments: null,
      current_risk: null,
      pec: null,
    },
  ];
}

function normalizeConsequences(list: DeviationConsequences[]): DeviationConsequences[] {
  return list.map((dev) => ({
    ...dev,
    category_rows:
      dev.category_rows && dev.category_rows.length > 0
        ? dev.category_rows
        : initCategoryRows(dev),
  }));
}

export default function ConsequenceReviewTable({
  nodeId,
  selectedDeviationTypes,
  onApproved,
  onBack,
  initialConsequences,
  onConsequencesChange,
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

  // Table-view edit state
  const [tableIntermEdit, setTableIntermEdit] = useState<TableIntermEdit>(null);
  const [tableCatRowEdit, setTableCatRowEdit] = useState<TableCatRowEdit>(null);
  const [tableCatPecEdit, setTableCatPecEdit] = useState<{ devIdx: number; rowIdx: number } | null>(null);
  const [tableCatRiskEdit, setTableCatRiskEdit] = useState<{ devIdx: number; rowIdx: number } | null>(null);

  const hasTriggered = useRef(false);
  useEffect(() => {
    if (hasTriggered.current) return;
    hasTriggered.current = true;
    if (initialConsequences && initialConsequences.length > 0) {
      setDeviationConsequences(normalizeConsequences(initialConsequences));
      setGenerated(true);
    } else if (nodeId) {
      handleGenerate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync local edits back to parent cache
  useEffect(() => {
    if (generated && deviationConsequences.length > 0) {
      onConsequencesChange(deviationConsequences);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviationConsequences, generated]);

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

      const result = await generateConsequences(nodeId, selectedDeviationTypes);
      stepTimers.forEach(clearTimeout);

      setProgressSteps([
        { label: "Loading approved causes and equipment data from P&ID", status: "done" },
        { label: "Calculating overpressure ratios for pressure deviations", status: "done" },
        { label: "Generating consequences using AI and knowledge documents", status: "done" },
        { label: "Preparing consequences for SME review", status: "active" },
      ]);
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((prev) => prev.map((s) => ({ ...s, status: "done" as const })));

      setDeviationConsequences(normalizeConsequences(result.deviation_consequences));
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
  };

  const handlePecChange = (devIdx: number, value: string) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], pec: value };
      return updated;
    });
  };

  const handleCurrentRiskChange = (devIdx: number, value: string) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], current_risk: value };
      return updated;
    });
  };

  // ---- Table-view intermediate edit ----

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

  // ---- Table-view per-category row edit ----

  const openCatRowEdit = (devIdx: number, rowIdx: number) => {
    const row = (deviationConsequences[devIdx].category_rows ?? [])[rowIdx];
    setTableCatRowEdit({
      devIdx,
      rowIdx,
      scenarioVal: row?.scenario_comments ?? "",
      consequencesVal: (row?.consequences ?? []).join("\n"),
    });
  };

  const saveCatRowEdit = () => {
    if (!tableCatRowEdit) return;
    const { devIdx, rowIdx, scenarioVal, consequencesVal } = tableCatRowEdit;
    const consequences = consequencesVal.split("\n").map((s) => s.trim()).filter(Boolean);
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      const rows = [...(dev.category_rows ?? [])];
      rows[rowIdx] = { ...rows[rowIdx], consequences, scenario_comments: scenarioVal || null };
      dev.category_rows = rows;
      updated[devIdx] = dev;
      return updated;
    });
    setTableCatRowEdit(null);
  };

  const handleCatRowPecChange = (devIdx: number, rowIdx: number, value: string) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      const rows = [...(dev.category_rows ?? [])];
      rows[rowIdx] = { ...rows[rowIdx], pec: value };
      dev.category_rows = rows;
      updated[devIdx] = dev;
      return updated;
    });
    setTableCatPecEdit(null);
  };

  const handleCatRowRiskChange = (devIdx: number, rowIdx: number, value: string) => {
    setDeviationConsequences((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      const rows = [...(dev.category_rows ?? [])];
      rows[rowIdx] = { ...rows[rowIdx], current_risk: value };
      dev.category_rows = rows;
      updated[devIdx] = dev;
      return updated;
    });
    setTableCatRiskEdit(null);
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

  // ---- Category colors ----
  const catColors: Record<string, string> = {
    PAF: "bg-red-100 text-red-700 border-red-300",
    "PD/LOR": "bg-amber-100 text-amber-700 border-amber-300",
    ECR: "bg-green-100 text-green-700 border-green-300",
  };

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
                  <tr className="bg-gray-50 border-b-2 border-gray-300">
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-44">
                      Deviation
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-52">
                      Cause
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-32">
                      Drawings / References
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-56">
                      Intermediate Consequences
                      <span className="ml-1 text-gray-400 normal-case font-normal text-[10px]">(click to edit)</span>
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-24">
                      <span>Cons. Cat</span>
                      <div className="text-[9px] font-normal normal-case text-gray-400 leading-tight mt-0.5">
                        Defined in HSE Risk Assessment
                      </div>
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-72">
                      Scenario Comments / Final Impacts
                      <span className="ml-1 text-gray-400 normal-case font-normal text-[10px]">(click to edit)</span>
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-20">
                      PEC
                    </th>
                    <th className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap w-20">
                      Current Risk
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {deviationConsequences.flatMap((dev, devIdx) => {
                    const catRows = dev.category_rows ?? [];
                    const isEditingInterm = tableIntermEdit?.devIdx === devIdx;

                    return catRows.map((row, rowIdx) => {
                      const isFirst = rowIdx === 0;
                      const isLast = rowIdx === catRows.length - 1;
                      const isEditingCatRow = tableCatRowEdit?.devIdx === devIdx && tableCatRowEdit?.rowIdx === rowIdx;
                      const isEditingPec = tableCatPecEdit?.devIdx === devIdx && tableCatPecEdit?.rowIdx === rowIdx;
                      const isEditingRisk = tableCatRiskEdit?.devIdx === devIdx && tableCatRiskEdit?.rowIdx === rowIdx;

                      const riskColorCls = !row.current_risk
                        ? "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                        : row.current_risk.startsWith("E") || row.current_risk === "5"
                        ? "bg-red-200 text-red-900 border-red-400"
                        : row.current_risk.startsWith("D") || row.current_risk === "4"
                        ? "bg-red-100 text-red-700 border-red-300"
                        : row.current_risk.startsWith("C") || row.current_risk === "3"
                        ? "bg-amber-100 text-amber-700 border-amber-300"
                        : "bg-yellow-50 text-yellow-700 border-yellow-200";

                      return (
                        <tr
                          key={`${dev.deviation_id}-${rowIdx}`}
                          className={`align-top ${isLast ? "border-b-2 border-gray-300" : "border-b border-gray-100"}`}
                        >
                          {/* ---- Spanning cells (first row only) ---- */}
                          {isFirst && (
                            <>
                              {/* Deviation */}
                              <td
                                rowSpan={catRows.length}
                                className="px-3 py-2.5 border-r border-gray-200 align-top"
                              >
                                <div className="flex flex-col gap-1">
                                  <span className="font-mono text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded self-start">
                                    {dev.guideword}
                                  </span>
                                  <span className="font-medium text-gray-900 leading-snug">{dev.deviation}</span>
                                  <span className="text-gray-400 text-[10px]">{dev.equipment_tag}</span>
                                  {dev.overpressure_calc?.assumed_leak_size && (
                                    <span className={`text-[10px] rounded px-1.5 py-0.5 self-start font-semibold border ${
                                      dev.overpressure_calc.exceeds_2x
                                        ? "bg-amber-100 text-amber-700 border-amber-300"
                                        : "bg-yellow-50 text-yellow-700 border-yellow-300"
                                    }`}>
                                      {dev.overpressure_calc.exceeds_2x ? "⚠ " : ""}
                                      {dev.overpressure_calc.assumed_leak_size} leak
                                      {" "}({dev.overpressure_calc.ratio.toFixed(2)}×)
                                    </span>
                                  )}
                                </div>
                              </td>

                              {/* Cause */}
                              <td
                                rowSpan={catRows.length}
                                className="px-3 py-2.5 border-r border-gray-200 align-top"
                              >
                                <ul className="space-y-1">
                                  {dev.causes.map((cause, ci) => (
                                    <li key={ci} className="flex items-start gap-1.5 text-gray-700 leading-snug">
                                      <span className="text-gray-300 flex-shrink-0 mt-0.5">•</span>
                                      {cause}
                                    </li>
                                  ))}
                                </ul>
                                {/* P&ID cause instruments */}
                                {dev.pid_cause_instruments?.length > 0 && (
                                  <div className="mt-1.5 flex flex-wrap gap-1">
                                    {dev.pid_cause_instruments.map((inst) => (
                                      <span
                                        key={inst.tag}
                                        className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-indigo-50 border border-indigo-100 rounded text-[10px] text-indigo-700"
                                        title={inst.reason}
                                      >
                                        <span className="font-mono font-medium">{inst.tag}</span>
                                        <span className="text-indigo-400">{inst.instrument_type}</span>
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </td>

                              {/* Drawing / Reference */}
                              <td
                                rowSpan={catRows.length}
                                className="px-3 py-2.5 border-r border-gray-200 align-top"
                              >
                                {dev.drawing_references.length > 0 ? (
                                  <span className="text-blue-700 font-medium">
                                    {dev.drawing_references.join(", ")}
                                  </span>
                                ) : (
                                  <span className="text-gray-300 italic">—</span>
                                )}
                              </td>

                              {/* Intermediate Consequences */}
                              <td
                                rowSpan={catRows.length}
                                className="px-3 py-2.5 border-r border-gray-200 align-top cursor-pointer"
                                onClick={() => { if (!isEditingInterm) openTableIntermEdit(devIdx); }}
                              >
                                {isEditingInterm ? (
                                  <div className="space-y-1.5" onClick={(e) => e.stopPropagation()}>
                                    <textarea
                                      autoFocus
                                      rows={5}
                                      value={tableIntermEdit!.value}
                                      onChange={(e) => setTableIntermEdit({ devIdx, value: e.target.value })}
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
                            </>
                          )}

                          {/* ---- Per-category cells ---- */}

                          {/* Cons. Cat */}
                          <td className="px-3 py-2.5 border-r border-gray-200 align-top">
                            <span className={`text-[11px] px-2 py-0.5 rounded border font-semibold ${catColors[row.category] ?? "bg-gray-100 text-gray-600 border-gray-300"}`}>
                              {row.category}
                            </span>
                          </td>

                          {/* Scenario Comments / Final Impacts — stacked blocks */}
                          <td
                            className="border-r border-gray-200 align-top cursor-pointer"
                            onClick={() => { if (!isEditingCatRow) openCatRowEdit(devIdx, rowIdx); }}
                          >
                            {isEditingCatRow ? (
                              <div className="p-2 space-y-2" onClick={(e) => e.stopPropagation()}>
                                <div>
                                  <div className="text-[10px] font-semibold text-gray-500 uppercase mb-1">
                                    Final Impacts (one per line)
                                  </div>
                                  <textarea
                                    autoFocus
                                    rows={4}
                                    value={tableCatRowEdit!.consequencesVal}
                                    onChange={(e) => setTableCatRowEdit((prev) => prev ? { ...prev, consequencesVal: e.target.value } : null)}
                                    className="w-full px-2 py-1 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
                                    placeholder="One item per line…"
                                  />
                                </div>
                                <div>
                                  <div className="text-[10px] font-semibold text-gray-500 uppercase mb-1">
                                    Scenario Comments
                                  </div>
                                  <textarea
                                    rows={3}
                                    value={tableCatRowEdit!.scenarioVal}
                                    onChange={(e) => setTableCatRowEdit((prev) => prev ? { ...prev, scenarioVal: e.target.value } : null)}
                                    className="w-full px-2 py-1 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
                                    placeholder="Narrative description…"
                                  />
                                </div>
                                <div className="flex gap-1">
                                  <button
                                    onClick={saveCatRowEdit}
                                    className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600"
                                  >Save</button>
                                  <button
                                    onClick={() => setTableCatRowEdit(null)}
                                    className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300"
                                  >Cancel</button>
                                </div>
                              </div>
                            ) : (
                              <div className="group relative">
                                {/* Stacked blocks — one per consequence */}
                                {row.consequences.length > 0 || row.scenario_comments ? (
                                  <div className="divide-y divide-gray-100">
                                    {row.consequences.map((c, ci) => (
                                      <div key={ci} className="px-3 py-2 text-xs text-gray-800 leading-snug">
                                        {c}
                                      </div>
                                    ))}
                                    {row.scenario_comments && (
                                      <div className="px-3 py-2 text-xs text-gray-500 italic leading-snug">
                                        {row.scenario_comments}
                                      </div>
                                    )}
                                  </div>
                                ) : (
                                  <div className="px-3 py-2 text-gray-300 italic text-xs">
                                    Click to add…
                                  </div>
                                )}
                                <span className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 text-[10px] text-blue-400 bg-white px-1 rounded border border-blue-200 transition-opacity">
                                  Edit
                                </span>
                              </div>
                            )}
                          </td>

                          {/* PEC — only for PAF row */}
                          <td className="px-3 py-2.5 border-r border-gray-200 align-top">
                            {row.category === "PAF" ? (
                              isEditingPec ? (
                                <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
                                  {["PEC-1", "PEC-2", "PEC-3", "PEC-4"].map((p) => (
                                    <button
                                      key={p}
                                      onClick={() => handleCatRowPecChange(devIdx, rowIdx, p)}
                                      className="block w-full text-left text-[11px] px-2 py-0.5 rounded border border-purple-200 bg-purple-50 text-purple-700 hover:bg-purple-100 font-medium"
                                    >
                                      {p}
                                    </button>
                                  ))}
                                  <button
                                    onClick={() => setTableCatPecEdit(null)}
                                    className="text-[10px] text-gray-400 hover:text-gray-600"
                                  >Cancel</button>
                                </div>
                              ) : (
                                <button
                                  onClick={() => setTableCatPecEdit({ devIdx, rowIdx })}
                                  title="Click to change PEC"
                                  className={`text-[11px] px-2 py-0.5 rounded border font-medium hover:opacity-80 ${
                                    row.pec
                                      ? "bg-purple-100 text-purple-700 border-purple-300"
                                      : "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                                  }`}
                                >
                                  {row.pec ?? "Set…"}
                                </button>
                              )
                            ) : (
                              <span className="text-gray-300 text-[11px]">—</span>
                            )}
                          </td>

                          {/* Current Risk */}
                          <td className="px-3 py-2.5 align-top">
                            {isEditingRisk ? (
                              <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
                                {["C5", "D4", "D5", "E5", "B4", "C4", "4", "3", "2", "1"].map((r) => (
                                  <button
                                    key={r}
                                    onClick={() => handleCatRowRiskChange(devIdx, rowIdx, r)}
                                    className={`block w-full text-left text-[11px] px-2 py-0.5 rounded border font-medium hover:opacity-80 ${
                                      r.startsWith("E") || r === "5"
                                        ? "bg-red-100 text-red-700 border-red-300"
                                        : r.startsWith("D") || r === "4"
                                        ? "bg-red-50 text-red-600 border-red-200"
                                        : r.startsWith("C") || r === "3"
                                        ? "bg-amber-50 text-amber-700 border-amber-200"
                                        : "bg-yellow-50 text-yellow-700 border-yellow-200"
                                    }`}
                                  >
                                    {r}
                                  </button>
                                ))}
                                <button
                                  onClick={() => setTableCatRiskEdit(null)}
                                  className="text-[10px] text-gray-400 hover:text-gray-600"
                                >Cancel</button>
                              </div>
                            ) : (
                              <button
                                onClick={() => setTableCatRiskEdit({ devIdx, rowIdx })}
                                title="Click to change current risk"
                                className={`text-[11px] px-2 py-1 rounded border font-bold hover:opacity-80 ${riskColorCls}`}
                              >
                                {row.current_risk ?? "Set…"}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    });
                  })}
                </tbody>
              </table>
            </div>
          </div>

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
// ApprovalSection
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
          {submitting ? "Approving…" : "Approve Consequences & Review Safeguards"}
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
  const hasLeak = !!calc.assumed_leak_size;
  const containerCls = calc.exceeds_2x
    ? "bg-amber-50 border border-amber-300"
    : hasLeak
    ? "bg-yellow-50 border border-yellow-200"
    : "bg-gray-50 border border-gray-200";
  const labelCls = calc.exceeds_2x
    ? "bg-amber-500 text-white"
    : hasLeak
    ? "bg-yellow-400 text-yellow-900"
    : "bg-gray-400 text-white";
  const label = calc.exceeds_2x
    ? "⚠ VESSEL RUPTURE"
    : hasLeak
    ? "PRESSURIZED LEAK"
    : "OVERPRESSURE";

  return (
    <div className={`rounded-md p-3 text-xs space-y-1.5 ${containerCls}`}>
      <div className="flex items-center gap-2 font-semibold flex-wrap">
        <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold ${labelCls}`}>
          {label}
        </span>
        <span className="text-gray-700">
          {calc.max_credible_pressure} PSIG ÷ {calc.design_pressure} PSIG (design) = {calc.ratio.toFixed(2)}×
        </span>
      </div>
      {calc.significance && (
        <div className="text-gray-600">{calc.significance}</div>
      )}
      {calc.consequence_description && (
        <div className={`font-medium ${calc.exceeds_2x ? "text-amber-800" : "text-yellow-800"}`}>
          {calc.consequence_description}
          {calc.assumed_leak_size && (
            <span className="ml-2 font-bold">→ {calc.assumed_leak_size} assumed leak</span>
          )}
        </div>
      )}
      {calc.source && (
        <div className="text-gray-400 italic text-[10px]">Source: {calc.source}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EditableList — card view
// ---------------------------------------------------------------------------

function EditableList({
  items, devIdx, field,
  editTarget, editText, newItemText,
  onDelete, onStartEdit, onSaveEdit, onCancelEdit, onEditTextChange,
  onAddItem, onNewItemChange,
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
// DeviationConsequenceCard — card view
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
            {dev.pec}
          </span>
        )}
        {dev.current_risk && (
          <span className={`text-xs px-2 py-0.5 rounded border font-bold ${
            dev.current_risk.startsWith("E") ? "bg-red-200 text-red-900 border-red-400" :
            dev.current_risk.startsWith("D") ? "bg-red-100 text-red-700 border-red-300" :
            dev.current_risk.startsWith("C") ? "bg-amber-100 text-amber-700 border-amber-300" :
            "bg-yellow-50 text-yellow-700 border-yellow-200"
          }`}>
            {dev.current_risk}
          </span>
        )}
        {dev.drawing_references.length > 0 && (
          <span className="text-xs text-gray-400 ml-auto">
            Ref: {dev.drawing_references.join(", ")}
          </span>
        )}
      </div>

      {dev.overpressure_calc && <OverpressureBadge calc={dev.overpressure_calc} />}

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
