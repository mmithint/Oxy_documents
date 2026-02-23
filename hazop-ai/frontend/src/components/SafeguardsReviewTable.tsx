import { useState, useRef, useEffect } from "react";
import type { DeviationSafeguards, SafeguardReview, InstrumentClassificationConfig } from "../types/hazop";
import { generateSafeguards, approveSafeguards } from "../services/api";

interface SafeguardsReviewTableProps {
  nodeId: string;
  selectedDeviationTypes: string[];
  onApproved: () => void;
  onBack: () => void;
  instrumentConfig?: InstrumentClassificationConfig;
  initialSafeguards: DeviationSafeguards[] | null;
  onSafeguardsChange: (safeguards: DeviationSafeguards[]) => void;
}

type ProgressStep = {
  label: string;
  status: "pending" | "active" | "done" | "error";
};

// Inline edit state for a single safeguard cell
type CellEdit = {
  devIdx: number;
  sgIdx: number;
  field: keyof SafeguardReview;
  value: string;
} | null;

// Table-view deviation-level inline edit state
type TableIntermEdit = { devIdx: number; value: string } | null;
type TableScenarioEdit = { devIdx: number; scenarioVal: string; consequencesVal: string } | null;

// Card-view list edit state
type CardListEdit = { devIdx: number; field: "intermediate"; itemIdx: number } | null;

const CAT_COLORS: Record<string, string> = {
  PAF: "bg-red-100 text-red-700 border-red-300",
  "PD/LOR": "bg-amber-100 text-amber-700 border-amber-300",
  ECR: "bg-green-100 text-green-700 border-green-300",
};

function riskBadgeCls(risk: string | null): string {
  if (!risk) return "bg-gray-50 text-gray-400 border-dashed border-gray-300";
  if (risk.startsWith("E")) return "bg-red-200 text-red-900 border-red-400";
  if (risk.startsWith("D")) return "bg-red-100 text-red-700 border-red-300";
  if (risk.startsWith("C")) return "bg-amber-100 text-amber-700 border-amber-300";
  return "bg-yellow-50 text-yellow-700 border-yellow-200";
}

function rlBadgeCls(rl: string | null): string {
  if (!rl) return "bg-gray-50 text-gray-400 border-dashed border-gray-300";
  if (rl === "E") return "bg-red-200 text-red-900 border-red-400";
  if (rl === "D") return "bg-orange-100 text-orange-700 border-orange-300";
  if (rl === "C") return "bg-yellow-100 text-yellow-700 border-yellow-300";
  if (rl === "B") return "bg-blue-100 text-blue-700 border-blue-300";
  return "bg-green-100 text-green-700 border-green-300"; // A
}

export default function SafeguardsReviewTable({
  nodeId,
  selectedDeviationTypes,
  onApproved,
  onBack,
  instrumentConfig,
  initialSafeguards,
  onSafeguardsChange,
}: SafeguardsReviewTableProps) {
  const [deviationSafeguards, setDeviationSafeguards] = useState<DeviationSafeguards[]>([]);
  const [loading, setLoading] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [smeName, setSmeName] = useState("");
  const [comments, setComments] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([]);

  // View toggle
  const [viewMode, setViewMode] = useState<"card" | "table">("card");

  // Safeguard cell edit (table + card)
  const [cellEdit, setCellEdit] = useState<CellEdit>(null);

  // Table-view deviation-level edit state
  const [tableIntermEdit, setTableIntermEdit] = useState<TableIntermEdit>(null);
  const [tableScenarioEdit, setTableScenarioEdit] = useState<TableScenarioEdit>(null);
  const [tableEditingCategory, setTableEditingCategory] = useState<number | null>(null);
  const [tableEditingPec, setTableEditingPec] = useState<number | null>(null);
  const [tableEditingCurrentRisk, setTableEditingCurrentRisk] = useState<number | null>(null);

  // Card-view list edit state
  const [cardListEdit, setCardListEdit] = useState<CardListEdit>(null);
  const [cardEditText, setCardEditText] = useState("");
  const [cardNewItem, setCardNewItem] = useState<Record<string, string>>({});
  const [cardEditingCategory, setCardEditingCategory] = useState<number | null>(null);
  const [cardEditingScenario, setCardEditingScenario] = useState<number | null>(null);
  const [cardEditingPec, setCardEditingPec] = useState<number | null>(null);
  const [cardEditingCurrentRisk, setCardEditingCurrentRisk] = useState<number | null>(null);
  const [cardEditingProbability, setCardEditingProbability] = useState<number | null>(null);
  const [cardEditingRl, setCardEditingRl] = useState<number | null>(null);

  const [tableEditingProbability, setTableEditingProbability] = useState<number | null>(null);
  const [tableEditingRl, setTableEditingRl] = useState<number | null>(null);

  const hasTriggered = useRef(false);
  useEffect(() => {
    if (hasTriggered.current) return;
    hasTriggered.current = true;
    if (initialSafeguards && initialSafeguards.length > 0) {
      setDeviationSafeguards(initialSafeguards);
      setGenerated(true);
    } else if (nodeId) {
      handleGenerate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync local edits back to parent cache
  useEffect(() => {
    if (generated && deviationSafeguards.length > 0) {
      onSafeguardsChange(deviationSafeguards);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviationSafeguards, generated]);

  // ---- Generate ----

  const handleGenerate = async () => {
    setLoading(true);
    setError("");
    setProgressSteps([
      { label: "Loading approved causes and consequences", status: "active" },
      { label: "Matching safety devices from P&ID instruments", status: "pending" },
      { label: "Enriching safeguards using AI and knowledge documents (CME IDs, descriptions)", status: "pending" },
      { label: "Preparing safeguards for SME review", status: "pending" },
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

      const result = await generateSafeguards(
        nodeId,
        selectedDeviationTypes,
        instrumentConfig?.safeguard_included_tags,
        instrumentConfig?.safeguard_excluded_tags,
      );
      stepTimers.forEach(clearTimeout);

      setProgressSteps([
        { label: "Loading approved causes and consequences", status: "done" },
        { label: "Matching safety devices from P&ID instruments", status: "done" },
        { label: "Enriching safeguards using AI and knowledge documents (CME IDs, descriptions)", status: "done" },
        { label: "Preparing safeguards for SME review", status: "active" },
      ]);
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((prev) => prev.map((s) => ({ ...s, status: "done" as const })));

      setDeviationSafeguards(result.deviation_safeguards);
      setGenerated(true);
    } catch (err: unknown) {
      let message = "Failed to generate safeguards. Check if the backend is running.";
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

  // ---- Safeguard cell edit (both views) ----

  const openCellEdit = (devIdx: number, sgIdx: number, field: keyof SafeguardReview) => {
    const sg = deviationSafeguards[devIdx].safeguards[sgIdx];
    setCellEdit({ devIdx, sgIdx, field, value: (sg[field] ?? "") as string });
  };

  const saveCellEdit = () => {
    if (!cellEdit) return;
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      const dev = { ...updated[cellEdit.devIdx] };
      const safeguards = [...dev.safeguards];
      safeguards[cellEdit.sgIdx] = { ...safeguards[cellEdit.sgIdx], [cellEdit.field]: cellEdit.value || null };
      dev.safeguards = safeguards;
      updated[cellEdit.devIdx] = dev;
      return updated;
    });
    setCellEdit(null);
  };

  const cancelCellEdit = () => setCellEdit(null);

  // ---- Add/Remove safeguard row ----

  const handleAddSafeguard = (devIdx: number) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      dev.safeguards = [
        ...dev.safeguards,
        {
          instrument_tag: "",
          description: "",
          pr_classification: "",
          mitigation_type: null,
          pid_reference: null,
          control_category: null,
          cme_name: null,
          cme_id: null,
        },
      ];
      updated[devIdx] = dev;
      return updated;
    });
  };

  const handleRemoveSafeguard = (devIdx: number, sgIdx: number) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      dev.safeguards = dev.safeguards.filter((_, i) => i !== sgIdx);
      updated[devIdx] = dev;
      return updated;
    });
  };

  // ---- Deviation-level field handlers (shared by card + table) ----

  const handleCategoryChange = (devIdx: number, value: string) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], consequence_category: value };
      return updated;
    });
    setCardEditingCategory(null);
    setTableEditingCategory(null);
  };

  const handlePecChange = (devIdx: number, value: string) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], pec: value };
      return updated;
    });
    setCardEditingPec(null);
    setTableEditingPec(null);
  };

  const handleCurrentRiskChange = (devIdx: number, value: string) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], current_risk: value };
      return updated;
    });
    setCardEditingCurrentRisk(null);
    setTableEditingCurrentRisk(null);
  };

  const handleProbabilityChange = (devIdx: number, value: number) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], probability: value };
      return updated;
    });
    setCardEditingProbability(null);
    setTableEditingProbability(null);
  };

  const handleRlChange = (devIdx: number, value: string) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], rl: value };
      return updated;
    });
    setCardEditingRl(null);
    setTableEditingRl(null);
  };

  const handleScenarioChange = (devIdx: number, value: string) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[devIdx] = { ...updated[devIdx], scenario_comments: value };
      return updated;
    });
  };

  // ---- Table-view intermediate consequences edit ----

  const openTableIntermEdit = (devIdx: number) => {
    const value = deviationSafeguards[devIdx].intermediate_consequences.join("\n");
    setTableIntermEdit({ devIdx, value });
  };

  const saveTableIntermEdit = () => {
    if (!tableIntermEdit) return;
    const items = tableIntermEdit.value.split("\n").map((s) => s.trim()).filter(Boolean);
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      updated[tableIntermEdit.devIdx] = { ...updated[tableIntermEdit.devIdx], intermediate_consequences: items };
      return updated;
    });
    setTableIntermEdit(null);
  };

  // ---- Table-view scenario/final impacts edit ----

  const openTableScenarioEdit = (devIdx: number) => {
    const dev = deviationSafeguards[devIdx];
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
    setDeviationSafeguards((prev) => {
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

  // ---- Card-view intermediate consequences list edit ----

  const handleCardDeleteItem = (devIdx: number, itemIdx: number) => {
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      dev.intermediate_consequences = dev.intermediate_consequences.filter((_, i) => i !== itemIdx);
      updated[devIdx] = dev;
      return updated;
    });
  };

  const handleCardStartEdit = (devIdx: number, itemIdx: number) => {
    const text = deviationSafeguards[devIdx].intermediate_consequences[itemIdx];
    setCardListEdit({ devIdx, field: "intermediate", itemIdx });
    setCardEditText(text);
  };

  const handleCardSaveEdit = () => {
    if (!cardListEdit || !cardEditText.trim()) return;
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      const dev = { ...updated[cardListEdit.devIdx] };
      const arr = [...dev.intermediate_consequences];
      arr[cardListEdit.itemIdx] = cardEditText.trim();
      dev.intermediate_consequences = arr;
      updated[cardListEdit.devIdx] = dev;
      return updated;
    });
    setCardListEdit(null);
    setCardEditText("");
  };

  const handleCardAddItem = (devIdx: number) => {
    const key = `${devIdx}-interm`;
    const text = cardNewItem[key]?.trim();
    if (!text) return;
    setDeviationSafeguards((prev) => {
      const updated = [...prev];
      const dev = { ...updated[devIdx] };
      dev.intermediate_consequences = [...dev.intermediate_consequences, text];
      updated[devIdx] = dev;
      return updated;
    });
    setCardNewItem((prev) => ({ ...prev, [key]: "" }));
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
      await approveSafeguards(nodeId, smeName.trim(), deviationSafeguards, comments || undefined);
      onApproved();
    } catch (err: unknown) {
      let message = "Failed to approve safeguards.";
      if (err && typeof err === "object" && "response" in err) {
        const axiosErr = err as { response?: { data?: { detail?: string } } };
        message = axiosErr.response?.data?.detail || message;
      }
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  // ---- Group by equipment (card view) — only deviations with approved consequences ----
  const grouped = deviationSafeguards.reduce<Record<string, { tag: string; items: { dev: DeviationSafeguards; idx: number }[] }>>(
    (acc, dev, idx) => {
      if (dev.consequences.length === 0) return acc;
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
          <h2 className="text-base font-semibold text-gray-900">Step 6: Review Safeguards / Mitigations</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Review AI-generated safety safeguards (CME/KME) for each deviation. Edit descriptions, tags, and classifications as needed.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {generated && (
            <>
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
              <span className="text-xs bg-green-100 text-green-700 px-2.5 py-1 rounded font-medium">
                {deviationSafeguards.filter(d => d.consequences.length > 0).length} deviations
              </span>
            </>
          )}
          <button
            onClick={onBack}
            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
          >
            ← Back to Consequences
          </button>
        </div>
      </div>

      {/* Progress Steps */}
      {loading && (
        <div className="bg-white rounded-lg border border-blue-200 p-5 space-y-3">
          <h3 className="text-sm font-semibold text-gray-800">Generating Safeguards…</h3>
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
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* ---- CARD VIEW ---- */}
      {generated && !loading && viewMode === "card" && (
        <div className="space-y-6">
          {Object.values(grouped).map(({ tag, items }) => (
            <div key={tag} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="bg-gray-50 border-b border-gray-200 px-4 py-2.5 flex items-center gap-2">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Equipment</span>
                <span className="text-sm font-bold text-gray-900">{tag}</span>
              </div>
              <div className="divide-y divide-gray-100">
                {items.map(({ dev, idx: devIdx }) => (
                  <div key={dev.deviation_id} className="p-4 space-y-4">
                    {/* Deviation header */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded text-gray-600">{dev.guideword}</span>
                      <span className="text-sm font-semibold text-gray-900">{dev.deviation}</span>
                      <span className="text-xs text-gray-400">{dev.equipment_tag}</span>

                      {/* Category badge / editor */}
                      {cardEditingCategory === devIdx ? (
                        <div className="flex gap-1">
                          {["PAF", "PD/LOR", "ECR"].map((cat) => (
                            <button key={cat} onClick={() => handleCategoryChange(devIdx, cat)}
                              className={`text-xs px-2 py-0.5 rounded border ${CAT_COLORS[cat] ?? ""} hover:opacity-80`}>
                              {cat}
                            </button>
                          ))}
                          <button onClick={() => setCardEditingCategory(null)} className="text-[10px] text-gray-400 hover:text-gray-600 ml-1">×</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setCardEditingCategory(devIdx)}
                          title="Click to change category"
                          className={`text-xs px-2 py-0.5 rounded border font-medium hover:opacity-80 ${
                            dev.consequence_category
                              ? (CAT_COLORS[dev.consequence_category] ?? "bg-gray-100 text-gray-600 border-gray-300")
                              : "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                          }`}>
                          {dev.consequence_category ?? "Category…"}
                        </button>
                      )}

                      {/* PEC badge / editor */}
                      {cardEditingPec === devIdx ? (
                        <div className="flex gap-1">
                          {["PEC-1", "PEC-2", "PEC-3", "PEC-4"].map((p) => (
                            <button key={p} onClick={() => handlePecChange(devIdx, p)}
                              className="text-[11px] px-2 py-0.5 rounded border border-purple-200 bg-purple-50 text-purple-700 hover:bg-purple-100 font-medium">
                              {p}
                            </button>
                          ))}
                          <button onClick={() => setCardEditingPec(null)} className="text-[10px] text-gray-400 hover:text-gray-600 ml-1">×</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setCardEditingPec(devIdx)}
                          title="Click to change PEC"
                          className={`text-xs px-2 py-0.5 rounded border font-medium hover:opacity-80 ${
                            dev.pec ? "bg-purple-100 text-purple-700 border-purple-300" : "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                          }`}>
                          {dev.pec ?? "PEC…"}
                        </button>
                      )}

                      {/* Current Risk badge / editor */}
                      {cardEditingCurrentRisk === devIdx ? (
                        <div className="flex gap-1 flex-wrap">
                          {["C5", "D4", "D5", "E5", "B4", "C4"].map((r) => (
                            <button key={r} onClick={() => handleCurrentRiskChange(devIdx, r)}
                              className={`text-[11px] px-2 py-0.5 rounded border font-medium hover:opacity-80 ${riskBadgeCls(r)}`}>
                              {r}
                            </button>
                          ))}
                          <button onClick={() => setCardEditingCurrentRisk(null)} className="text-[10px] text-gray-400 hover:text-gray-600 ml-1">×</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setCardEditingCurrentRisk(devIdx)}
                          title="Click to change current risk"
                          className={`text-xs px-2 py-0.5 rounded border font-bold hover:opacity-80 ${riskBadgeCls(dev.current_risk)}`}>
                          {dev.current_risk ?? "Risk…"}
                        </button>
                      )}

                      {/* Probability badge / editor */}
                      {cardEditingProbability === devIdx ? (
                        <div className="flex gap-1">
                          {[1, 2, 3, 4, 5].map((p) => (
                            <button key={p} onClick={() => handleProbabilityChange(devIdx, p)}
                              className="text-[11px] px-2 py-0.5 rounded border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 font-medium">
                              {p}
                            </button>
                          ))}
                          <button onClick={() => setCardEditingProbability(null)} className="text-[10px] text-gray-400 hover:text-gray-600 ml-1">×</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setCardEditingProbability(devIdx)}
                          title="Click to change probability"
                          className="text-xs px-2 py-0.5 rounded border border-indigo-200 bg-indigo-50 text-indigo-700 font-medium hover:opacity-80">
                          P={dev.probability ?? 4}
                        </button>
                      )}

                      {/* RL badge / editor */}
                      {cardEditingRl === devIdx ? (
                        <div className="flex gap-1">
                          {["A", "B", "C", "D", "E"].map((r) => (
                            <button key={r} onClick={() => handleRlChange(devIdx, r)}
                              className={`text-[11px] px-2 py-0.5 rounded border font-bold hover:opacity-80 ${rlBadgeCls(r)}`}>
                              {r}
                            </button>
                          ))}
                          <button onClick={() => setCardEditingRl(null)} className="text-[10px] text-gray-400 hover:text-gray-600 ml-1">×</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setCardEditingRl(devIdx)}
                          title="Click to change residual risk level"
                          className={`text-xs px-2 py-0.5 rounded border font-bold hover:opacity-80 ${rlBadgeCls(dev.rl ?? null)}`}>
                          {dev.rl ? `RL: ${dev.rl}` : "RL…"}
                        </button>
                      )}

                      {dev.drawing_references.length > 0 && (
                        <span className="text-xs text-gray-400 ml-auto">
                          Ref: {dev.drawing_references.join(", ")}
                        </span>
                      )}
                    </div>

                    {/* Intermediate Consequences */}
                    <div>
                      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                        Intermediate Consequences
                      </div>
                      <div className="space-y-1">
                        {dev.intermediate_consequences.map((item, itemIdx) => {
                          const isEditing = cardListEdit?.devIdx === devIdx && cardListEdit.itemIdx === itemIdx;
                          return (
                            <div key={itemIdx} className="group flex items-start gap-2">
                              <span className="mt-1 text-gray-300 flex-shrink-0">•</span>
                              {isEditing ? (
                                <div className="flex-1 flex gap-1.5">
                                  <input
                                    autoFocus
                                    type="text"
                                    value={cardEditText}
                                    onChange={(e) => setCardEditText(e.target.value)}
                                    onKeyDown={(e) => { if (e.key === "Enter") handleCardSaveEdit(); if (e.key === "Escape") { setCardListEdit(null); setCardEditText(""); } }}
                                    className="flex-1 px-2 py-0.5 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                                  />
                                  <button onClick={handleCardSaveEdit} className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600">Save</button>
                                  <button onClick={() => { setCardListEdit(null); setCardEditText(""); }} className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">Cancel</button>
                                </div>
                              ) : (
                                <>
                                  <span className="flex-1 text-xs text-gray-700 leading-relaxed">{item}</span>
                                  <div className="opacity-0 group-hover:opacity-100 flex gap-1 flex-shrink-0 transition-opacity">
                                    <button onClick={() => handleCardStartEdit(devIdx, itemIdx)} className="text-[10px] text-blue-500 hover:text-blue-700">Edit</button>
                                    <button onClick={() => handleCardDeleteItem(devIdx, itemIdx)} className="text-[10px] text-red-400 hover:text-red-600">×</button>
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
                            value={cardNewItem[`${devIdx}-interm`] ?? ""}
                            onChange={(e) => setCardNewItem((prev) => ({ ...prev, [`${devIdx}-interm`]: e.target.value }))}
                            onKeyDown={(e) => { if (e.key === "Enter") handleCardAddItem(devIdx); }}
                            className="flex-1 px-2 py-0.5 text-xs border border-dashed border-gray-300 rounded focus:outline-none focus:border-blue-300 placeholder-gray-300"
                          />
                          {cardNewItem[`${devIdx}-interm`]?.trim() && (
                            <button onClick={() => handleCardAddItem(devIdx)} className="text-[10px] px-2 py-0.5 bg-gray-100 text-gray-600 rounded hover:bg-gray-200">Add</button>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Scenario Comments */}
                    <div>
                      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                        Scenario Comments / Final Impact Narrative
                      </div>
                      {cardEditingScenario === devIdx ? (
                        <div className="space-y-1">
                          <textarea
                            autoFocus
                            rows={4}
                            value={dev.scenario_comments ?? ""}
                            onChange={(e) => handleScenarioChange(devIdx, e.target.value)}
                            className="w-full px-3 py-2 text-xs border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
                          />
                          <button onClick={() => setCardEditingScenario(null)} className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600">Done</button>
                        </div>
                      ) : (
                        <div
                          onClick={() => setCardEditingScenario(devIdx)}
                          className="text-xs text-gray-600 leading-relaxed p-2.5 bg-gray-50 rounded border border-dashed border-gray-200 cursor-pointer hover:border-blue-300 hover:bg-blue-50 transition-colors min-h-[48px]"
                        >
                          {dev.scenario_comments || <span className="text-gray-300 italic">Click to add scenario narrative…</span>}
                        </div>
                      )}
                    </div>

                    {/* Safeguards Sub-Table */}
                    <div>
                      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                        Safeguards / Mitigations
                      </div>
                      <div className="overflow-x-auto rounded border border-gray-100">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="bg-gray-50 text-left border-b border-gray-100">
                              <th className="px-3 py-2 font-semibold text-gray-600 w-8"></th>
                              <th className="px-3 py-2 font-semibold text-gray-600 min-w-[220px]">Mitigation (CME/KME)</th>
                              <th className="px-3 py-2 font-semibold text-gray-600 min-w-[160px]">CME Name</th>
                              <th className="px-3 py-2 font-semibold text-gray-600 w-28">Tag Number</th>
                              <th className="px-3 py-2 font-semibold text-gray-600 w-24">P&ID(CME)</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-50">
                            {dev.safeguards.length === 0 && (
                              <tr>
                                <td colSpan={5} className="px-3 py-3 text-gray-400 italic text-center">
                                  No safeguards detected — click + to add manually
                                </td>
                              </tr>
                            )}
                            {dev.safeguards.map((sg, sgIdx) => (
                              <tr key={sgIdx} className="hover:bg-gray-50 group">
                                <td className="px-2 py-1.5 text-center">
                                  <button
                                    onClick={() => handleRemoveSafeguard(devIdx, sgIdx)}
                                    className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-500 transition-opacity"
                                    title="Remove safeguard"
                                  >
                                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                                    </svg>
                                  </button>
                                </td>
                                <EditableCell
                                  value={sg.description}
                                  isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "description"}
                                  editValue={cellEdit?.value ?? ""}
                                  onOpen={() => openCellEdit(devIdx, sgIdx, "description")}
                                  onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                  onSave={saveCellEdit}
                                  onCancel={cancelCellEdit}
                                  multiline
                                  placeholder="Enter mitigation description…"
                                />
                                <EditableCell
                                  value={sg.cme_name}
                                  isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "cme_name"}
                                  editValue={cellEdit?.value ?? ""}
                                  onOpen={() => openCellEdit(devIdx, sgIdx, "cme_name")}
                                  onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                  onSave={saveCellEdit}
                                  onCancel={cancelCellEdit}
                                  placeholder="CME name…"
                                />
                                <EditableCell
                                  value={sg.instrument_tag}
                                  isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "instrument_tag"}
                                  editValue={cellEdit?.value ?? ""}
                                  onOpen={() => openCellEdit(devIdx, sgIdx, "instrument_tag")}
                                  onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                  onSave={saveCellEdit}
                                  onCancel={cancelCellEdit}
                                  mono
                                  placeholder="Tag…"
                                />
                                <EditableCell
                                  value={sg.pid_reference}
                                  isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "pid_reference"}
                                  editValue={cellEdit?.value ?? ""}
                                  onOpen={() => openCellEdit(devIdx, sgIdx, "pid_reference")}
                                  onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                  onSave={saveCellEdit}
                                  onCancel={cancelCellEdit}
                                  mono
                                  placeholder="Dwg ref…"
                                />
                              </tr>
                            ))}
                            <tr>
                              <td colSpan={5} className="px-3 py-1.5">
                                <button
                                  onClick={() => handleAddSafeguard(devIdx)}
                                  className="text-[11px] text-blue-500 hover:text-blue-700 font-medium flex items-center gap-1"
                                >
                                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                                  </svg>
                                  Add safeguard
                                </button>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <ApprovalSection
            smeName={smeName}
            comments={comments}
            submitting={submitting}
            error={error}
            onSmeNameChange={setSmeName}
            onCommentsChange={setComments}
            onApprove={handleApprove}
            onBack={onBack}
          />
        </div>
      )}

      {/* ---- TABLE VIEW ---- */}
      {generated && !loading && viewMode === "table" && (
        <div className="space-y-4">
          <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs border-collapse">
                <thead>
                  {/* Row 1 — main column headers */}
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-44 align-bottom">
                      Deviation
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-48 align-bottom">
                      Cause
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-28 align-bottom">
                      Drawing / Ref
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-52 align-bottom">
                      Intermediate Consequences
                      <span className="ml-1 text-gray-400 normal-case font-normal text-[10px]">(click)</span>
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-24 align-bottom">
                      Category
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-64 align-bottom">
                      Scenario / Final Impacts
                      <span className="ml-1 text-gray-400 normal-case font-normal text-[10px]">(click)</span>
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap border-r border-gray-200 w-20 align-bottom">
                      PEC
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-52 align-bottom">
                      Mitigation (CME/KME)
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 w-36 align-bottom">
                      CME Name
                    </th>
                    {/* Tags header spans two sub-columns */}
                    <th colSpan={2} className="px-3 py-1.5 text-center font-semibold text-gray-600 uppercase tracking-wide border-r border-gray-200 border-b border-gray-200">
                      Tags
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap w-24 align-bottom border-r border-gray-200">
                      Current Risk
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap w-20 align-bottom border-r border-gray-200">
                      Probability
                    </th>
                    <th rowSpan={2} className="px-3 py-2.5 text-left font-semibold text-gray-600 uppercase tracking-wide whitespace-nowrap w-16 align-bottom">
                      RL
                    </th>
                  </tr>
                  {/* Row 2 — Tags sub-headers */}
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="px-3 py-1.5 text-left font-semibold text-gray-500 whitespace-nowrap border-r border-gray-200 w-28">
                      Tag Number
                    </th>
                    <th className="px-3 py-1.5 text-left font-semibold text-gray-500 whitespace-nowrap border-r border-gray-200 w-24">
                      P&ID(CME)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {deviationSafeguards
                    .map((dev, idx) => ({ dev, idx }))
                    .filter(({ dev }) => dev.consequences.length > 0)
                    .map(({ dev, idx: devIdx }) => {
                    const rowCount = dev.safeguards.length || 1;
                    const isEditingInterm = tableIntermEdit?.devIdx === devIdx;
                    const isEditingScenario = tableScenarioEdit?.devIdx === devIdx;
                    const isEditingCat = tableEditingCategory === devIdx;
                    const isEditingPec = tableEditingPec === devIdx;
                    const isEditingCurrentRisk = tableEditingCurrentRisk === devIdx;

                    // Build safeguard rows — first row includes deviation-level cells (rowspan)
                    const safeguardRows = dev.safeguards.length > 0 ? dev.safeguards : [null];

                    return safeguardRows.map((sg, sgIdx) => {
                      const isFirstRow = sgIdx === 0;
                      const isLastRow = sgIdx === safeguardRows.length - 1;

                      return (
                        <tr key={`${dev.deviation_id}-${sgIdx}`} className="hover:bg-gray-50 align-top">
                          {/* ---- Deviation-level cells (rowspan, first row only) ---- */}
                          {isFirstRow && (
                            <>
                              {/* Deviation */}
                              <td rowSpan={rowCount} className="px-3 py-2.5 border-r border-gray-100 align-top">
                                <div className="flex flex-col gap-1">
                                  <span className="font-mono text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded self-start">{dev.guideword}</span>
                                  <span className="font-medium text-gray-900 leading-snug">{dev.deviation}</span>
                                  <span className="text-gray-400 text-[10px]">{dev.equipment_tag}</span>
                                </div>
                              </td>

                              {/* Causes */}
                              <td rowSpan={rowCount} className="px-3 py-2.5 border-r border-gray-100 align-top">
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
                              <td rowSpan={rowCount} className="px-3 py-2.5 border-r border-gray-100 align-top">
                                {dev.drawing_references.length > 0 ? (
                                  <span className="text-blue-700 font-medium">{dev.drawing_references.join(", ")}</span>
                                ) : (
                                  <span className="text-gray-300 italic">—</span>
                                )}
                              </td>

                              {/* Intermediate Consequences */}
                              <td
                                rowSpan={rowCount}
                                className="px-3 py-2.5 border-r border-gray-100 cursor-pointer align-top"
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
                                      <button onClick={saveTableIntermEdit} className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600">Save</button>
                                      <button onClick={() => setTableIntermEdit(null)} className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">Cancel</button>
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
                                    <span className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 text-[10px] text-blue-400 bg-white px-1 rounded border border-blue-200 transition-opacity">Edit</span>
                                  </div>
                                )}
                              </td>

                              {/* Category */}
                              <td rowSpan={rowCount} className="px-3 py-2.5 border-r border-gray-100 align-top">
                                {isEditingCat ? (
                                  <div className="flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>
                                    {["PAF", "PD/LOR", "ECR"].map((cat) => (
                                      <button
                                        key={cat}
                                        onClick={() => handleCategoryChange(devIdx, cat)}
                                        className={`text-[11px] px-2 py-0.5 rounded border font-medium text-left ${CAT_COLORS[cat] ?? ""} hover:opacity-80`}
                                      >
                                        {cat}
                                      </button>
                                    ))}
                                    <button onClick={() => setTableEditingCategory(null)} className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5">Cancel</button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => setTableEditingCategory(devIdx)}
                                    title="Click to change"
                                    className={`text-[11px] px-2 py-0.5 rounded border font-medium ${
                                      dev.consequence_category
                                        ? (CAT_COLORS[dev.consequence_category] ?? "bg-gray-100 text-gray-600 border-gray-300")
                                        : "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                                    } hover:opacity-80`}
                                  >
                                    {dev.consequence_category ?? "Set…"}
                                  </button>
                                )}
                              </td>

                              {/* Scenario / Final Impacts */}
                              <td
                                rowSpan={rowCount}
                                className="px-3 py-2.5 border-r border-gray-100 cursor-pointer align-top"
                                onClick={() => { if (!isEditingScenario) openTableScenarioEdit(devIdx); }}
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
                                      <button onClick={saveTableScenarioEdit} className="text-[10px] px-2 py-0.5 bg-blue-500 text-white rounded hover:bg-blue-600">Save</button>
                                      <button onClick={() => setTableScenarioEdit(null)} className="text-[10px] px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">Cancel</button>
                                    </div>
                                  </div>
                                ) : (
                                  <div className="group relative space-y-1">
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
                                    {dev.scenario_comments ? (
                                      <p className="text-gray-500 italic leading-relaxed text-[11px] border-t border-gray-100 pt-1">
                                        {dev.scenario_comments}
                                      </p>
                                    ) : (
                                      dev.consequences.length === 0 && (
                                        <span className="text-gray-300 italic">Click to add…</span>
                                      )
                                    )}
                                    <span className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 text-[10px] text-blue-400 bg-white px-1 rounded border border-blue-200 transition-opacity">Edit</span>
                                  </div>
                                )}
                              </td>

                              {/* PEC */}
                              <td rowSpan={rowCount} className="px-3 py-2.5 border-r border-gray-100 align-top">
                                {isEditingPec ? (
                                  <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
                                    {["PEC-1", "PEC-2", "PEC-3", "PEC-4"].map((p) => (
                                      <button
                                        key={p}
                                        onClick={() => handlePecChange(devIdx, p)}
                                        className="block w-full text-left text-[11px] px-2 py-0.5 rounded border border-purple-200 bg-purple-50 text-purple-700 hover:bg-purple-100 font-medium"
                                      >
                                        {p}
                                      </button>
                                    ))}
                                    <button onClick={() => setTableEditingPec(null)} className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5">Cancel</button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => setTableEditingPec(devIdx)}
                                    title="Click to change PEC"
                                    className={`text-[11px] px-2 py-0.5 rounded border font-medium hover:opacity-80 ${
                                      dev.pec
                                        ? "bg-purple-100 text-purple-700 border-purple-300"
                                        : "bg-gray-50 text-gray-400 border-dashed border-gray-300"
                                    }`}
                                  >
                                    {dev.pec ?? "Set…"}
                                  </button>
                                )}
                              </td>
                            </>
                          )}

                          {/* ---- Safeguard-level cells (repeat per safeguard row) ---- */}

                          {/* Mitigation (CME/KME) */}
                          {sg ? (
                            <EditableCell
                              value={sg.description}
                              isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "description"}
                              editValue={cellEdit?.value ?? ""}
                              onOpen={() => openCellEdit(devIdx, sgIdx, "description")}
                              onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                              onSave={saveCellEdit}
                              onCancel={cancelCellEdit}
                              multiline
                              placeholder="Enter mitigation…"
                            />
                          ) : (
                            <td className="px-3 py-2.5 border-r border-gray-100 text-gray-300 italic text-center" colSpan={5}>
                              No safeguards — click + to add
                            </td>
                          )}

                          {sg && (
                            <>
                              {/* CME Name */}
                              <EditableCell
                                value={sg.cme_name}
                                isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "cme_name"}
                                editValue={cellEdit?.value ?? ""}
                                onOpen={() => openCellEdit(devIdx, sgIdx, "cme_name")}
                                onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                onSave={saveCellEdit}
                                onCancel={cancelCellEdit}
                                placeholder="CME name…"
                              />
                              {/* Tag Number */}
                              <EditableCell
                                value={sg.instrument_tag}
                                isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "instrument_tag"}
                                editValue={cellEdit?.value ?? ""}
                                onOpen={() => openCellEdit(devIdx, sgIdx, "instrument_tag")}
                                onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                onSave={saveCellEdit}
                                onCancel={cancelCellEdit}
                                mono
                                placeholder="Tag…"
                              />
                              {/* P&ID(CME) */}
                              <EditableCell
                                value={sg.pid_reference}
                                isEditing={cellEdit?.devIdx === devIdx && cellEdit?.sgIdx === sgIdx && cellEdit?.field === "pid_reference"}
                                editValue={cellEdit?.value ?? ""}
                                onOpen={() => openCellEdit(devIdx, sgIdx, "pid_reference")}
                                onChangeEdit={(v) => setCellEdit((e) => e ? { ...e, value: v } : e)}
                                onSave={saveCellEdit}
                                onCancel={cancelCellEdit}
                                mono
                                placeholder="Dwg…"
                              />
                            </>
                          )}

                          {/* Current Risk (rowspan on first row) */}
                          {isFirstRow && (
                            <>
                              <td rowSpan={rowCount} className="px-3 py-2.5 align-top border-r border-gray-100">
                                {isEditingCurrentRisk ? (
                                  <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
                                    {["C5", "D4", "D5", "E5", "B4", "C4"].map((r) => (
                                      <button
                                        key={r}
                                        onClick={() => handleCurrentRiskChange(devIdx, r)}
                                        className={`block w-full text-left text-[11px] px-2 py-0.5 rounded border font-medium hover:opacity-80 ${riskBadgeCls(r)}`}
                                      >
                                        {r}
                                      </button>
                                    ))}
                                    <button onClick={() => setTableEditingCurrentRisk(null)} className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5">Cancel</button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => setTableEditingCurrentRisk(devIdx)}
                                    title="Click to change current risk"
                                    className={`text-[11px] px-2 py-1 rounded border font-bold hover:opacity-80 ${riskBadgeCls(dev.current_risk)}`}
                                  >
                                    {dev.current_risk ?? "Set…"}
                                  </button>
                                )}
                              </td>

                              {/* Probability (rowspan) */}
                              <td rowSpan={rowCount} className="px-3 py-2.5 align-top border-r border-gray-100">
                                {tableEditingProbability === devIdx ? (
                                  <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
                                    {[1, 2, 3, 4, 5].map((p) => (
                                      <button
                                        key={p}
                                        onClick={() => handleProbabilityChange(devIdx, p)}
                                        className="block w-full text-left text-[11px] px-2 py-0.5 rounded border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 font-medium"
                                      >
                                        {p}
                                      </button>
                                    ))}
                                    <button onClick={() => setTableEditingProbability(null)} className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5">Cancel</button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => setTableEditingProbability(devIdx)}
                                    title="Click to change probability"
                                    className="text-[11px] px-2 py-1 rounded border border-indigo-200 bg-indigo-50 text-indigo-700 font-bold hover:opacity-80"
                                  >
                                    {dev.probability ?? 4}
                                  </button>
                                )}
                              </td>

                              {/* RL (rowspan) */}
                              <td rowSpan={rowCount} className="px-3 py-2.5 align-top">
                                {tableEditingRl === devIdx ? (
                                  <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
                                    {["A", "B", "C", "D", "E"].map((r) => (
                                      <button
                                        key={r}
                                        onClick={() => handleRlChange(devIdx, r)}
                                        className={`block w-full text-left text-[11px] px-2 py-0.5 rounded border font-bold hover:opacity-80 ${rlBadgeCls(r)}`}
                                      >
                                        {r}
                                      </button>
                                    ))}
                                    <button onClick={() => setTableEditingRl(null)} className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5">Cancel</button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => setTableEditingRl(devIdx)}
                                    title="Click to change residual risk level"
                                    className={`text-[11px] px-2 py-1 rounded border font-bold hover:opacity-80 ${rlBadgeCls(dev.rl ?? null)}`}
                                  >
                                    {dev.rl ?? "Set…"}
                                  </button>
                                )}
                              </td>
                            </>
                          )}
                        </tr>
                      );
                    });
                  })}
                </tbody>
              </table>
            </div>

            {/* Add safeguard buttons below table — per deviation */}
            <div className="divide-y divide-gray-100 border-t border-gray-200">
              {deviationSafeguards
                .map((dev, idx) => ({ dev, idx }))
                .filter(({ dev }) => dev.consequences.length > 0)
                .map(({ dev, idx: devIdx }) => (
                <div key={dev.deviation_id} className="px-4 py-1.5 flex items-center gap-3">
                  <span className="text-[10px] font-mono text-gray-400 w-32 truncate">{dev.deviation}</span>
                  <button
                    onClick={() => handleAddSafeguard(devIdx)}
                    className="text-[11px] text-blue-500 hover:text-blue-700 font-medium flex items-center gap-1"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                    </svg>
                    Add safeguard
                  </button>
                </div>
              ))}
            </div>
          </div>

          <ApprovalSection
            smeName={smeName}
            comments={comments}
            submitting={submitting}
            error={error}
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
  smeName, comments, submitting, error,
  onSmeNameChange, onCommentsChange, onApprove, onBack,
}: {
  smeName: string;
  comments: string;
  submitting: boolean;
  error: string;
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
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center gap-3">
        <button
          onClick={onApprove}
          disabled={!smeName.trim() || submitting}
          className="px-5 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {submitting ? "Approving…" : "Approve Safeguards & Generate HAZOP"}
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
// EditableCell — click-to-edit table cell
// ---------------------------------------------------------------------------

function EditableCell({
  value,
  isEditing,
  editValue,
  onOpen,
  onChangeEdit,
  onSave,
  onCancel,
  multiline = false,
  mono = false,
  badge = false,
  badgeColor = "gray",
  placeholder = "",
}: {
  value: string | null | undefined;
  isEditing: boolean;
  editValue: string;
  onOpen: () => void;
  onChangeEdit: (v: string) => void;
  onSave: () => void;
  onCancel: () => void;
  multiline?: boolean;
  mono?: boolean;
  badge?: boolean;
  badgeColor?: "blue" | "gray";
  placeholder?: string;
}) {
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSave(); }
    if (e.key === "Escape") onCancel();
  };

  if (isEditing) {
    return (
      <td className="px-2 py-1 align-top border-r border-gray-100">
        {multiline ? (
          <textarea
            autoFocus
            rows={3}
            value={editValue}
            onChange={(e) => onChangeEdit(e.target.value)}
            onBlur={onSave}
            onKeyDown={handleKeyDown}
            className="w-full text-xs border border-blue-400 rounded px-2 py-1 focus:outline-none resize-none"
            placeholder={placeholder}
          />
        ) : (
          <input
            autoFocus
            type="text"
            value={editValue}
            onChange={(e) => onChangeEdit(e.target.value)}
            onBlur={onSave}
            onKeyDown={handleKeyDown}
            className={`w-full text-xs border border-blue-400 rounded px-2 py-1 focus:outline-none ${mono ? "font-mono" : ""}`}
            placeholder={placeholder}
          />
        )}
      </td>
    );
  }

  return (
    <td
      className="px-3 py-1.5 cursor-pointer hover:bg-blue-50 group align-top border-r border-gray-100"
      onClick={onOpen}
      title="Click to edit"
    >
      {value ? (
        badge ? (
          <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-semibold ${
            badgeColor === "blue" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600"
          }`}>
            {value}
          </span>
        ) : (
          <span className={`text-xs text-gray-700 leading-relaxed ${mono ? "font-mono" : ""}`}>
            {value}
          </span>
        )
      ) : (
        <span className="text-[11px] text-gray-300 italic group-hover:text-blue-300">
          {placeholder || "—"}
        </span>
      )}
    </td>
  );
}
