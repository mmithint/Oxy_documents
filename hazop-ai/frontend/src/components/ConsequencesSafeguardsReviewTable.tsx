import { useState, useRef, useEffect } from "react";
import type { DeviationConsequences, DeviationSafeguards, InstrumentClassificationConfig, CategoryRow } from "../types/hazop";
import {
  generateConsequences,
  approveConsequences,
  generateSafeguards,
  approveSafeguards,
} from "../services/api";
import ConsequenceReviewTable from "./ConsequenceReviewTable";
import SafeguardsReviewTable from "./SafeguardsReviewTable";

interface Props {
  nodeId: string;
  selectedDeviationTypes: string[];
  onApproved: () => void;
  onBack: () => void;
  instrumentConfig?: InstrumentClassificationConfig;
  initialConsequences: DeviationConsequences[] | null;
  onConsequencesChange: (c: DeviationConsequences[]) => void;
  initialSafeguards: DeviationSafeguards[] | null;
  onSafeguardsChange: (s: DeviationSafeguards[]) => void;
}

type ProgressStep = { label: string; status: "pending" | "active" | "done" | "error" };
type GenPhase = "generating_consequences" | "saving_consequences" | "generating_safeguards" | "review" | "error";

// ---------------------------------------------------------------------------
// Helpers (mirrored from ConsequenceReviewTable)
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
      dev.category_rows && dev.category_rows.length > 0 ? dev.category_rows : initCategoryRows(dev),
  }));
}

// ---------------------------------------------------------------------------
// Progress steps labels
// ---------------------------------------------------------------------------

const CONSEQUENCE_STEPS = [
  "Loading approved causes and equipment data from P&ID",
  "Calculating overpressure ratios for pressure deviations",
  "Generating consequences using AI and knowledge documents",
  "Preparing consequences for review",
];

const SAFEGUARD_STEPS = [
  "Saving consequences to enable safeguard generation",
  "Matching safety devices from P&ID instruments",
  "Enriching safeguards using AI and knowledge documents (CME IDs, descriptions)",
  "Preparing safeguards for SME review",
];

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ConsequencesSafeguardsReviewTable({
  nodeId,
  selectedDeviationTypes,
  onApproved,
  onBack,
  instrumentConfig,
  initialConsequences,
  onConsequencesChange,
  initialSafeguards,
  onSafeguardsChange,
}: Props) {
  const [phase, setPhase] = useState<GenPhase>("generating_consequences");
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([]);
  const [genError, setGenError] = useState("");

  const [consequences, setConsequences] = useState<DeviationConsequences[]>([]);
  const [safeguards, setSafeguards] = useState<DeviationSafeguards[]>([]);

  const [activeTab, setActiveTab] = useState<"consequences" | "safeguards" | "combined">("consequences");

  const [smeName, setSmeName] = useState("");
  const [comments, setComments] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const hasTriggered = useRef(false);

  useEffect(() => {
    if (hasTriggered.current) return;
    hasTriggered.current = true;
    if (initialConsequences?.length && initialSafeguards?.length) {
      setConsequences(normalizeConsequences(initialConsequences));
      setSafeguards(initialSafeguards);
      setPhase("review");
    } else {
      runGeneration();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // Sequential generation
  // ---------------------------------------------------------------------------

  const advanceStep = (steps: ProgressStep[], activeIdx: number): ProgressStep[] =>
    steps.map((s, i) =>
      i < activeIdx
        ? { ...s, status: "done" }
        : i === activeIdx
        ? { ...s, status: "active" }
        : s
    );

  const runGeneration = async () => {
    const allSteps: ProgressStep[] = [
      ...CONSEQUENCE_STEPS.map((label, i) => ({
        label,
        status: (i === 0 ? "active" : "pending") as ProgressStep["status"],
      })),
      ...SAFEGUARD_STEPS.map((label) => ({
        label,
        status: "pending" as ProgressStep["status"],
      })),
    ];
    setProgressSteps(allSteps);
    setGenError("");

    try {
      // ---- Phase 1: Generate consequences ----
      setPhase("generating_consequences");

      const t1 = setTimeout(
        () => setProgressSteps((p) => advanceStep(p, 1)),
        800,
      );
      const t2 = setTimeout(
        () => setProgressSteps((p) => advanceStep(p, 2)),
        2000,
      );

      const consResult = await generateConsequences(nodeId, selectedDeviationTypes);
      clearTimeout(t1);
      clearTimeout(t2);

      const normalizedCons = normalizeConsequences(consResult.deviation_consequences);
      setConsequences(normalizedCons);
      onConsequencesChange(normalizedCons);

      setProgressSteps((p) => advanceStep(p, 3));
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((p) => advanceStep(p, 4)); // step 3 done, step 4 active (saving)

      // ---- Phase 2: Auto-save consequences so safeguards endpoint can read them ----
      setPhase("saving_consequences");
      await approveConsequences(nodeId, "_auto_pending_sme_review_", normalizedCons);

      // ---- Phase 3: Generate safeguards ----
      setPhase("generating_safeguards");
      setProgressSteps((p) => advanceStep(p, 5));

      const t3 = setTimeout(
        () => setProgressSteps((p) => advanceStep(p, 6)),
        1500,
      );

      const sgResult = await generateSafeguards(
        nodeId,
        selectedDeviationTypes,
        instrumentConfig?.safeguard_included_tags,
        instrumentConfig?.safeguard_excluded_tags,
      );
      clearTimeout(t3);

      setProgressSteps((p) => advanceStep(p, 7));
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((p) => p.map((s) => ({ ...s, status: "done" })));

      setSafeguards(sgResult.deviation_safeguards);
      onSafeguardsChange(sgResult.deviation_safeguards);

      setPhase("review");
    } catch (err: unknown) {
      let message = "Generation failed. Please go back and try again.";
      if (err && typeof err === "object" && "response" in err) {
        const axiosErr = err as { response?: { data?: { detail?: string }; status?: number } };
        message =
          axiosErr.response?.data?.detail ||
          `Server error (${axiosErr.response?.status || "unknown"})`;
      } else if (err instanceof Error) {
        message = err.message;
      }
      setGenError(message);
      setProgressSteps((p) =>
        p.map((s) => (s.status === "active" ? { ...s, status: "error" } : s))
      );
      setPhase("error");
    }
  };

  // ---------------------------------------------------------------------------
  // Final approval
  // ---------------------------------------------------------------------------

  const handleApprove = async () => {
    if (!smeName.trim()) {
      setSubmitError("Please enter your name before approving.");
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      await approveConsequences(nodeId, smeName.trim(), consequences, comments || undefined);
      await approveSafeguards(nodeId, smeName.trim(), safeguards, comments || undefined);
      onApproved();
    } catch (err: unknown) {
      let message = "Approval failed. Please try again.";
      if (err && typeof err === "object" && "response" in err) {
        const axiosErr = err as { response?: { data?: { detail?: string } } };
        message = axiosErr.response?.data?.detail || message;
      }
      setSubmitError(message);
    } finally {
      setSubmitting(false);
    }
  };

  // Excel export — combined sheet
  const handleDownloadExcel = async () => {
    const ExcelJS = (await import("exceljs")).default;
    const workbook = new ExcelJS.Workbook();
    const sheet = workbook.addWorksheet("Consequences & Safeguards");

    sheet.columns = [
      { header: "Equipment Tag", key: "eq", width: 15 },
      { header: "Deviation", key: "dev", width: 35 },
      { header: "Guideword", key: "gw", width: 12 },
      { header: "Parameter", key: "param", width: 15 },
      { header: "Causes", key: "causes", width: 50 },
      { header: "PAF Consequences", key: "paf", width: 50 },
      { header: "PD/LOR Consequences", key: "pdlor", width: 40 },
      { header: "ECR Consequences", key: "ecr", width: 40 },
      { header: "Current Risk", key: "risk", width: 14 },
      { header: "Safeguards / Mitigations", key: "safeguards", width: 60 },
      { header: "Probability", key: "prob", width: 13 },
      { header: "RL", key: "rl", width: 8 },
    ];

    const headerRow = sheet.getRow(1);
    headerRow.font = { bold: true };
    headerRow.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFE8F0FE" } };

    consequences.forEach((cons) => {
      const sg = safeguards.find((s) => s.deviation_id === cons.deviation_id);
      const pafRow = cons.category_rows?.find((r) => r.category === "PAF");
      const pdlorRow = cons.category_rows?.find((r) => r.category === "PD/LOR");
      const ecrRow = cons.category_rows?.find((r) => r.category === "ECR");
      const safeguardText = sg?.safeguards
        .map((s) => `${s.instrument_tag}: ${s.description} (${s.pr_classification})`)
        .join("\n") ?? "";

      sheet.addRow({
        eq: cons.equipment_tag,
        dev: cons.deviation,
        gw: cons.guideword,
        param: cons.parameter,
        causes: cons.causes.join("\n"),
        paf: pafRow?.consequences.join("\n") ?? "",
        pdlor: pdlorRow?.consequences.join("\n") ?? "",
        ecr: ecrRow?.consequences.join("\n") ?? "",
        risk: cons.current_risk ?? "",
        safeguards: safeguardText,
        prob: sg?.probability ?? "",
        rl: sg?.rl ?? "",
      });
    });

    sheet.eachRow((row) => { row.alignment = { vertical: "top", wrapText: true }; });

    const buffer = await workbook.xlsx.writeBuffer();
    const blob = new Blob([buffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "consequences_safeguards.xlsx";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const isGenerating = phase !== "review" && phase !== "error";
  const conssPhaseDone =
    phase === "saving_consequences" ||
    phase === "generating_safeguards" ||
    phase === "review";
  const sgPhaseActive = phase === "saving_consequences" || phase === "generating_safeguards";
  const sgPhaseDone = phase === "review";

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-900">
            Review Consequences &amp; Safeguards
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {isGenerating
              ? "AI is generating consequences and safeguards sequentially. This may take a moment…"
              : "Review and edit AI-generated consequences and safeguards, then approve to proceed."}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {phase === "review" && (
            <>
              <div className="flex items-center gap-0.5 bg-gray-100 rounded-md p-0.5">
                <button
                  onClick={() => setActiveTab("consequences")}
                  className={`px-3 py-1 text-xs rounded font-medium ${
                    activeTab === "consequences"
                      ? "bg-white shadow text-gray-900"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  Consequences
                </button>
                <button
                  onClick={() => setActiveTab("safeguards")}
                  className={`px-3 py-1 text-xs rounded font-medium ${
                    activeTab === "safeguards"
                      ? "bg-white shadow text-gray-900"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  Safeguards / Mitigations
                </button>
                <button
                  onClick={() => setActiveTab("combined")}
                  className={`px-3 py-1 text-xs rounded font-medium ${
                    activeTab === "combined"
                      ? "bg-white shadow text-gray-900"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  Combined Table
                </button>
              </div>
              <button
                onClick={handleDownloadExcel}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-green-700 bg-green-50 border border-green-200 rounded hover:bg-green-100 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Excel
              </button>
            </>
          )}
          <button
            onClick={onBack}
            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
          >
            ← Back to Causes
          </button>
        </div>
      </div>

      {/* Progress */}
      {isGenerating && (
        <div className="bg-white rounded-lg border border-blue-200 p-5 space-y-4">
          {/* Phase 1: Consequences */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <PhaseIndicator
                number="1"
                active={phase === "generating_consequences"}
                done={conssPhaseDone}
              />
              <span
                className={`text-sm font-semibold ${
                  phase === "generating_consequences" ? "text-gray-800" : "text-gray-500"
                }`}
              >
                Phase 1: Generating Consequences
              </span>
            </div>
            <div className="ml-7 space-y-2">
              {progressSteps.slice(0, 4).map((s, i) => (
                <ProgressRow key={i} step={s} />
              ))}
            </div>
          </div>

          {/* Phase 2: Safeguards */}
          <div className="border-t border-gray-100 pt-3">
            <div className="flex items-center gap-2 mb-2">
              <PhaseIndicator
                number="2"
                active={sgPhaseActive}
                done={sgPhaseDone}
              />
              <span
                className={`text-sm font-semibold ${sgPhaseActive ? "text-gray-800" : "text-gray-400"}`}
              >
                Phase 2: Generating Safeguards / Mitigations
              </span>
            </div>
            <div className="ml-7 space-y-2">
              {progressSteps.slice(4).map((s, i) => (
                <ProgressRow key={i} step={s} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Generation error */}
      {genError && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-xs text-red-700">
          {genError}
        </div>
      )}

      {/* Review — tabbed, embedded components (table-only, no approval/back) */}
      {phase === "review" && (
        <>
          {/* Consequences tab */}
          <div className={activeTab === "consequences" ? "" : "hidden"}>
            <ConsequenceReviewTable
              nodeId={nodeId}
              selectedDeviationTypes={selectedDeviationTypes}
              onApproved={() => {}}
              onBack={onBack}
              initialConsequences={consequences}
              onConsequencesChange={(c) => {
                setConsequences(c);
                onConsequencesChange(c);
              }}
              embedded={true}
            />
          </div>

          {/* Safeguards tab */}
          <div className={activeTab === "safeguards" ? "" : "hidden"}>
            <SafeguardsReviewTable
              nodeId={nodeId}
              selectedDeviationTypes={selectedDeviationTypes}
              onApproved={() => {}}
              onBack={onBack}
              instrumentConfig={instrumentConfig}
              initialSafeguards={safeguards}
              onSafeguardsChange={(s) => {
                setSafeguards(s);
                onSafeguardsChange(s);
              }}
              embedded={true}
            />
          </div>

          {/* Combined Table tab */}
          {activeTab === "combined" && (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                      {["Equipment", "Deviation", "Guideword", "Causes", "PAF Consequences", "PD/LOR", "ECR", "Risk", "Safeguards / Mitigations", "Prob", "RL"].map((h) => (
                        <th key={h} className="text-left px-3 py-2 font-semibold text-gray-700 border-r border-gray-100 whitespace-nowrap last:border-r-0">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {consequences.map((cons) => {
                      const sg = safeguards.find((s) => s.deviation_id === cons.deviation_id);
                      const pafRow = cons.category_rows?.find((r) => r.category === "PAF");
                      const pdlorRow = cons.category_rows?.find((r) => r.category === "PD/LOR");
                      const ecrRow = cons.category_rows?.find((r) => r.category === "ECR");

                      return (
                        <tr key={cons.deviation_id} className="hover:bg-gray-50 align-top">
                          <td className="px-3 py-2 font-mono text-gray-700 border-r border-gray-100 whitespace-nowrap">{cons.equipment_tag}</td>
                          <td className="px-3 py-2 text-gray-700 border-r border-gray-100 max-w-[180px]">{cons.deviation}</td>
                          <td className="px-3 py-2 border-r border-gray-100 whitespace-nowrap">
                            <span className="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded text-[11px]">{cons.guideword}</span>
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 max-w-[200px]">
                            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
                              {cons.causes.map((c, i) => <li key={i}>{c}</li>)}
                            </ul>
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 max-w-[220px]">
                            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
                              {(pafRow?.consequences ?? []).map((c, i) => <li key={i}>{c}</li>)}
                            </ul>
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 max-w-[180px]">
                            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
                              {(pdlorRow?.consequences ?? []).map((c, i) => <li key={i}>{c}</li>)}
                            </ul>
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 max-w-[160px]">
                            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
                              {(ecrRow?.consequences ?? []).map((c, i) => <li key={i}>{c}</li>)}
                            </ul>
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 whitespace-nowrap">
                            {cons.current_risk ? (
                              <span className="px-1.5 py-0.5 bg-amber-50 text-amber-700 rounded text-[11px] font-mono">{cons.current_risk}</span>
                            ) : <span className="text-gray-300">—</span>}
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 max-w-[240px]">
                            {sg?.safeguards && sg.safeguards.length > 0 ? (
                              <div className="space-y-1">
                                {sg.safeguards.map((s, i) => (
                                  <div key={i} className="flex items-start gap-1.5">
                                    <span className="font-mono text-purple-700 flex-shrink-0">{s.instrument_tag}</span>
                                    <span className="text-gray-600">{s.description}</span>
                                    <span className="ml-auto text-[10px] text-gray-400 flex-shrink-0">{s.pr_classification}</span>
                                  </div>
                                ))}
                              </div>
                            ) : <span className="text-gray-300">—</span>}
                          </td>
                          <td className="px-3 py-2 border-r border-gray-100 text-center whitespace-nowrap">
                            {sg?.probability ?? <span className="text-gray-300">—</span>}
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            {sg?.rl ? (
                              <span className="px-1.5 py-0.5 bg-gray-100 text-gray-700 rounded text-[11px] font-medium">{sg.rl}</span>
                            ) : <span className="text-gray-300">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Combined SME Approval */}
          <div className="bg-white rounded-lg border border-gray-200 p-5 space-y-4">
            <h3 className="text-sm font-semibold text-gray-900">
              SME Approval — Consequences &amp; Safeguards
            </h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  SME Name <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={smeName}
                  onChange={(e) => setSmeName(e.target.value)}
                  placeholder="Enter your name"
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Comments (optional)
                </label>
                <input
                  type="text"
                  value={comments}
                  onChange={(e) => setComments(e.target.value)}
                  placeholder="Any notes for the record"
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                />
              </div>
            </div>
            {submitError && <p className="text-xs text-red-600">{submitError}</p>}
            <div className="flex items-center gap-3">
              <button
                onClick={handleApprove}
                disabled={!smeName.trim() || submitting}
                className="px-5 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                {submitting
                  ? "Approving…"
                  : "Approve Consequences & Safeguards → Generate HAZOP"}
              </button>
              <button
                onClick={onBack}
                className="px-4 py-2 text-sm font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200"
              >
                ← Back
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function PhaseIndicator({
  number,
  active,
  done,
}: {
  number: string;
  active: boolean;
  done: boolean;
}) {
  const cls = done
    ? "bg-green-500"
    : active
    ? "bg-blue-500 animate-pulse"
    : "bg-gray-300";

  return (
    <div
      className={`w-5 h-5 rounded-full flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0 ${cls}`}
    >
      {done ? "✓" : number}
    </div>
  );
}

function ProgressRow({ step }: { step: ProgressStep }) {
  return (
    <div className="flex items-center gap-2.5">
      {step.status === "done" && (
        <span className="w-4 h-4 rounded-full bg-green-500 flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0">
          ✓
        </span>
      )}
      {step.status === "active" && (
        <span className="w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin flex-shrink-0" />
      )}
      {step.status === "pending" && (
        <span className="w-4 h-4 rounded-full border-2 border-gray-200 flex-shrink-0" />
      )}
      {step.status === "error" && (
        <span className="w-4 h-4 rounded-full bg-red-500 flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0">
          ✕
        </span>
      )}
      <span
        className={`text-xs ${
          step.status === "active"
            ? "text-blue-700 font-medium"
            : step.status === "done"
            ? "text-green-700"
            : step.status === "error"
            ? "text-red-600"
            : "text-gray-400"
        }`}
      >
        {step.label}
      </span>
    </div>
  );
}
