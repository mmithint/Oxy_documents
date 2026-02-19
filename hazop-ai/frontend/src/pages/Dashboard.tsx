import { useState, useEffect } from "react";
import type { PIDNode, HAZOPReport, Instrument } from "../types/hazop";
import UploadPanel from "../components/UploadPanel";
import EquipmentReviewTable from "../components/EquipmentReviewTable";
import DeviationSelector from "../components/DeviationSelector";
import CausesReviewTable from "../components/CausesReviewTable";
import ConsequenceReviewTable from "../components/ConsequenceReviewTable";
import HazopTable from "../components/HazopTable";
import ExtractionDetails from "../components/ExtractionDetails";
import { generateHAZOP, generateHAZOPQuick, getHAZOPByNode, checkBackendConnection } from "../services/api";

// ---------------------------------------------------------------------------
// Instrument classification helper (mirrors EquipmentReviewTable logic)
// ---------------------------------------------------------------------------
const SAFETY_DEVICE_PREFIXES = new Set([
  "PSH", "PSL", "PSHH", "PSLL",
  "LSH", "LSL", "LSHH", "LSLL",
  "TSH", "TSL", "TSHH", "TSLL",
  "FSH", "FSL", "FSHH", "FSLL",
  "PSV", "PRV", "SV", "RV",
  "GD", "GDS", "FD",
  "SDV", "ESV", "BDV", "XV",
]);
const SAFETY_TYPE_KEYWORDS = [
  "safety valve", "switch high", "switch low",
  "gas detector", "fire detector", "deluge",
  "emergency shutdown", "shutdown valve", "blowdown valve", "relief valve",
];
function isSafetyDevice(inst: Instrument): boolean {
  const prefix = inst.tag.toUpperCase().match(/^([A-Z]+)/)?.[1] ?? "";
  if (SAFETY_DEVICE_PREFIXES.has(prefix)) return true;
  return SAFETY_TYPE_KEYWORDS.some((kw) => inst.instrument_type.toLowerCase().includes(kw));
}

type WorkflowStep = "upload" | "validate" | "select_deviations" | "review_causes" | "review_consequences" | "generate" | "review";

export default function Dashboard() {
  const [step, setStep] = useState<WorkflowStep>("upload");
  const [selectedNode, setSelectedNode] = useState<PIDNode | null>(null);
  const [report, setReport] = useState<HAZOPReport | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genMessage, setGenMessage] = useState("");
  const [backendConnected, setBackendConnected] = useState<boolean | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [ocrChunks, setOcrChunks] = useState<string[]>([]);
  const [llmRawOutput, setLlmRawOutput] = useState<Record<string, unknown> | null>(null);
  const [visionRawOutput, setVisionRawOutput] = useState<Record<string, unknown> | null>(null);
  const [mergeSummary, setMergeSummary] = useState<Record<string, unknown> | null>(null);
  const [selectedDeviationTypes, setSelectedDeviationTypes] = useState<string[] | null>(null);

  useEffect(() => {
    checkBackendConnection().then(({ ok, message }) => {
      setBackendConnected(ok);
      setBackendError(ok ? null : message ?? "Backend unreachable");
    });
  }, []);

  // Step 1: P&ID uploaded → nodes extracted
  const handleNodesExtracted = (nodes: PIDNode[]) => {
    if (nodes.length > 0) {
      setSelectedNode(nodes[0]);
      setStep("validate");
    }
  };

  const handleExtractionDetails = (
    chunks: string[],
    llmOutput: Record<string, unknown> | null,
    visionOutput: Record<string, unknown> | null,
    mergeSummaryData: Record<string, unknown> | null,
  ) => {
    setOcrChunks(chunks);
    setLlmRawOutput(llmOutput);
    setVisionRawOutput(visionOutput);
    setMergeSummary(mergeSummaryData);
  };

  // Step 2: Equipment validated → select deviations
  const handleEquipmentValidated = () => {
    setStep("select_deviations");
  };

  // Step 3: Deviations selected → review causes
  const handleDeviationsSelected = (types: string[]) => {
    setSelectedDeviationTypes(types);
    setStep("review_causes");
  };

  // Step 4: Causes approved → review consequences
  const handleCausesApproved = () => {
    setStep("review_consequences");
  };

  // Step 5: Consequences approved → ready to generate
  const handleConsequencesApproved = () => {
    setStep("generate");
  };

  const handleBackToValidation = () => {
    setStep("validate");
  };

  // Step 4: Generate HAZOP
  const handleGenerate = async (quick: boolean) => {
    if (!selectedNode) return;
    setGenerating(true);
    setGenMessage("");
    try {
      const result = quick
        ? await generateHAZOPQuick(selectedNode.node_id, selectedDeviationTypes ?? undefined)
        : await generateHAZOP(selectedNode.node_id, true, selectedDeviationTypes ?? undefined);
      setReport(result.report);
      setGenMessage(result.message);
      setStep("review");
    } catch {
      setGenMessage("Generation failed. Check if Azure services are configured.");
    } finally {
      setGenerating(false);
    }
  };

  // Refresh report after review actions
  const handleRefresh = async () => {
    if (!selectedNode) return;
    try {
      const updated = await getHAZOPByNode(selectedNode.node_id);
      setReport(updated);
    } catch {
      // Silent refresh failure
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-[1600px] mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-gray-900">HAZOP AI Assistant</h1>
              <p className="text-xs text-gray-500">AI-Assisted HAZOP Report Generation — Draft, Pending SME Validation</p>
            </div>
            <div className="flex items-center gap-3">
              {backendConnected === true && (
                <span className="px-2.5 py-1 rounded text-xs font-medium bg-green-100 text-green-700" title="Backend connected">
                  Backend connected
                </span>
              )}
              {backendConnected === false && (
                <span className="px-2.5 py-1 rounded text-xs font-medium bg-red-100 text-red-700" title={backendError ?? undefined}>
                  Backend disconnected
                </span>
              )}
              <WorkflowSteps currentStep={step} />
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-[1600px] mx-auto px-6 py-6">
        <div className="grid grid-cols-12 gap-6">
          {/* Left Panel: Upload & Node Info */}
          <div className="col-span-3 space-y-4">
            <UploadPanel onNodesExtracted={handleNodesExtracted} onExtractionDetails={handleExtractionDetails} />

            {/* Node Info Card */}
            {selectedNode && (
              <NodeInfoCard node={selectedNode} />
            )}

            {/* Generate Controls */}
            {step === "generate" && selectedNode && (
              <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                <h3 className="text-sm font-semibold text-gray-900">Generate HAZOP</h3>
                {selectedDeviationTypes && (
                  <p className="text-xs text-gray-500">
                    {selectedDeviationTypes.length} deviation types selected
                  </p>
                )}
                <button
                  onClick={() => handleGenerate(false)}
                  disabled={generating}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-300"
                >
                  {generating ? "Generating..." : "Full HAZOP (AI + Rules)"}
                </button>
                <button
                  onClick={() => handleGenerate(true)}
                  disabled={generating}
                  className="w-full px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded hover:bg-gray-200 disabled:bg-gray-50"
                >
                  Quick Draft (Rules Only)
                </button>
                {genMessage && (
                  <p className={`text-xs ${genMessage.includes("failed") ? "text-red-600" : "text-green-600"}`}>
                    {genMessage}
                  </p>
                )}
              </div>
            )}

            {/* Report Stats */}
            {report && (
              <div className="bg-white rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-900 mb-2">Report Summary</h3>
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Total Deviations</span>
                    <span className="font-medium">{report.deviations.length}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Approved</span>
                    <span className="font-medium text-green-600">
                      {report.deviations.filter((d) => d.status === "approved").length}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Pending</span>
                    <span className="font-medium text-amber-600">
                      {report.deviations.filter((d) => d.status !== "approved").length}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">High Risk (C+)</span>
                    <span className="font-medium text-red-600">
                      {report.deviations.filter((d) => {
                        const r = d.risk;
                        if (!r) return false;
                        return ["C", "D", "E"].some(
                          (lvl) =>
                            r.paf?.risk_level === lvl ||
                            r.pd_lor?.risk_level === lvl ||
                            r.ecr?.risk_level === lvl
                        );
                      }).length}
                    </span>
                  </div>

                  {/* Progress Bar */}
                  <div className="pt-2">
                    <div className="w-full bg-gray-200 rounded-full h-2">
                      <div
                        className="bg-green-500 h-2 rounded-full transition-all"
                        style={{
                          width: `${report.deviations.length > 0
                            ? (report.deviations.filter((d) => d.status === "approved").length / report.deviations.length) * 100
                            : 0}%`,
                        }}
                      />
                    </div>
                    <p className="text-[10px] text-gray-400 mt-1 text-center">
                      Review Progress
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Right Panel: Main Content Area */}
          <div className="col-span-9">
            {step === "upload" && (
              <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
                <div className="text-gray-400 text-4xl mb-4">P&ID</div>
                <h2 className="text-lg font-semibold text-gray-700">Upload a P&ID to begin</h2>
                <p className="text-sm text-gray-500 mt-2">
                  Upload a P&ID drawing (PDF or image) to extract equipment and instruments.
                </p>
              </div>
            )}

            {step === "validate" && selectedNode && (
              <>
                <ExtractionDetails
                  ocrChunks={ocrChunks}
                  llmRawOutput={llmRawOutput}
                  visionRawOutput={visionRawOutput}
                  mergeSummary={mergeSummary}
                />
                <EquipmentReviewTable
                  node={selectedNode}
                  onValidated={handleEquipmentValidated}
                />
              </>
            )}

            {step === "select_deviations" && selectedNode && (
              <DeviationSelector
                onSubmit={handleDeviationsSelected}
                onBack={handleBackToValidation}
              />
            )}

            {step === "review_causes" && selectedNode && selectedDeviationTypes && (
              <CausesReviewTable
                nodeId={selectedNode.node_id}
                selectedDeviationTypes={selectedDeviationTypes}
                onApproved={handleCausesApproved}
                onBack={() => setStep("select_deviations")}
              />
            )}

            {step === "review_consequences" && selectedNode && (
              <ConsequenceReviewTable
                nodeId={selectedNode.node_id}
                onApproved={handleConsequencesApproved}
                onBack={() => setStep("review_causes")}
              />
            )}

            {step === "generate" && (
              <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
                <div className="text-gray-400 text-4xl mb-4">HAZOP</div>
                <h2 className="text-lg font-semibold text-gray-700">Ready to Generate</h2>
                <p className="text-sm text-gray-500 mt-2">
                  Click "Full HAZOP" or "Quick Draft" in the left panel to generate the HAZOP report.
                </p>
              </div>
            )}

            {step === "review" && report && (
              <HazopTable report={report} onRefresh={handleRefresh} />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NodeInfoCard — Expandable node summary shown in the left sidebar
// ---------------------------------------------------------------------------

function CollapsibleSection({
  title,
  count,
  accentColor,
  children,
  defaultOpen = false,
}: {
  title: string;
  count: number;
  accentColor: string; // tailwind text color class for count badge
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border border-gray-100 rounded-md overflow-hidden">
      {/* Section header — always visible */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-700">{title}</span>
          <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${accentColor}`}>
            {count}
          </span>
        </div>
        <svg
          className={`w-3.5 h-3.5 text-gray-400 transition-transform flex-shrink-0 ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Collapsible body */}
      {open && (
        <div className="max-h-48 overflow-y-auto divide-y divide-gray-50">
          {children}
        </div>
      )}
    </div>
  );
}

function NodeInfoCard({ node }: { node: PIDNode }) {
  const instruments = node.instruments.filter((i) => !isSafetyDevice(i));
  const safetyDevices = node.instruments.filter(isSafetyDevice);

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      {/* Card header */}
      <div className="px-4 py-3 border-b border-gray-100">
        <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wide">Selected Node</h3>
        <p className="text-xs text-gray-500 mt-0.5 leading-snug">{node.node_name}</p>
        <p className="text-[11px] text-gray-400 font-mono">Node {node.node_id}</p>
      </div>

      {/* Expandable sections */}
      <div className="px-3 py-3 space-y-2">

        {/* Equipment */}
        <CollapsibleSection
          title="Equipment"
          count={node.equipment.length}
          accentColor="bg-blue-50 text-blue-700"
          defaultOpen={true}
        >
          {node.equipment.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-400 italic">No equipment</p>
          ) : (
            node.equipment.map((eq) => (
              <div key={eq.tag} className="flex items-center gap-2 px-3 py-1.5">
                <span className="font-mono text-xs text-blue-700 font-medium w-20 flex-shrink-0 truncate">
                  {eq.tag}
                </span>
                <span className="text-xs text-gray-600 truncate">{eq.equipment_type}</span>
                {eq.design_pressure != null && (
                  <span className="ml-auto text-[10px] text-gray-400 flex-shrink-0 font-mono">
                    {eq.design_pressure} PSIG
                  </span>
                )}
              </div>
            ))
          )}
        </CollapsibleSection>

        {/* Instruments */}
        <CollapsibleSection
          title="Instruments"
          count={instruments.length}
          accentColor="bg-purple-50 text-purple-700"
        >
          {instruments.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-400 italic">No instruments</p>
          ) : (
            instruments.map((inst) => (
              <div key={inst.tag} className="flex items-center gap-2 px-3 py-1.5">
                <span className="font-mono text-xs text-purple-700 font-medium w-20 flex-shrink-0 truncate">
                  {inst.tag}
                </span>
                <span className="text-xs text-gray-600 truncate">{inst.instrument_type}</span>
              </div>
            ))
          )}
        </CollapsibleSection>

        {/* Safety Devices */}
        <CollapsibleSection
          title="Safety Devices"
          count={safetyDevices.length}
          accentColor="bg-red-50 text-red-700"
        >
          {safetyDevices.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-400 italic">No safety devices</p>
          ) : (
            safetyDevices.map((sd) => (
              <div key={sd.tag} className="flex items-center gap-2 px-3 py-1.5">
                <span className="font-mono text-xs text-red-700 font-medium w-20 flex-shrink-0 truncate">
                  {sd.tag}
                </span>
                <span className="text-xs text-gray-600 truncate">{sd.instrument_type}</span>
              </div>
            ))
          )}
        </CollapsibleSection>

        {/* Maximum Upstream Pressure Source */}
        {node.upstream_pressure_psig != null && (
          <div className="px-3 py-2 bg-amber-50 border border-amber-100 rounded-md">
            <p className="text-[10px] font-semibold text-amber-700 uppercase tracking-wide mb-0.5">
              Max. Upstream Pressure Source
            </p>
            <p className="text-sm font-mono font-bold text-amber-900">
              {node.upstream_pressure_psig} <span className="text-xs font-normal text-amber-700">PSIG</span>
            </p>
          </div>
        )}

        {/* Validated by */}
        {node.validated_by && (
          <div className="flex items-center gap-1.5 px-1 pt-1 border-t border-gray-100">
            <svg className="w-3 h-3 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <span className="text-xs text-green-600">Validated by {node.validated_by}</span>
          </div>
        )}
      </div>
    </div>
  );
}


// --- Workflow Step Indicator ---

function WorkflowSteps({ currentStep }: { currentStep: WorkflowStep }) {
  const steps: { key: WorkflowStep; label: string }[] = [
    { key: "upload", label: "1. Upload P&ID" },
    { key: "validate", label: "2. Validate Equipment" },
    { key: "select_deviations", label: "3. Select Deviations" },
    { key: "review_causes", label: "4. Review Causes" },
    { key: "review_consequences", label: "5. Review Consequences" },
    { key: "generate", label: "6. Generate HAZOP" },
    { key: "review", label: "7. SME Review" },
  ];

  const stepOrder: WorkflowStep[] = ["upload", "validate", "select_deviations", "review_causes", "review_consequences", "generate", "review"];
  const currentIndex = stepOrder.indexOf(currentStep);

  return (
    <div className="flex items-center gap-1">
      {steps.map((s, i) => {
        const isActive = s.key === currentStep;
        const isCompleted = i < currentIndex;

        return (
          <div key={s.key} className="flex items-center">
            <span
              className={`px-2.5 py-1 rounded text-xs font-medium ${
                isActive
                  ? "bg-blue-100 text-blue-700"
                  : isCompleted
                  ? "bg-green-100 text-green-700"
                  : "bg-gray-100 text-gray-400"
              }`}
            >
              {s.label}
            </span>
            {i < steps.length - 1 && (
              <span className={`mx-1 text-xs ${isCompleted ? "text-green-400" : "text-gray-300"}`}>
                &rarr;
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
