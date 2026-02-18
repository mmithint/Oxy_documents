import { useState, useEffect } from "react";
import type { PIDNode, HAZOPReport } from "../types/hazop";
import UploadPanel from "../components/UploadPanel";
import EquipmentReviewTable from "../components/EquipmentReviewTable";
import DeviationSelector from "../components/DeviationSelector";
import CausesReviewTable from "../components/CausesReviewTable";
import HazopTable from "../components/HazopTable";
import ExtractionDetails from "../components/ExtractionDetails";
import { generateHAZOP, generateHAZOPQuick, getHAZOPByNode, checkBackendConnection } from "../services/api";

type WorkflowStep = "upload" | "validate" | "select_deviations" | "review_causes" | "generate" | "review";

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

  // Step 4: Causes approved → ready to generate
  const handleCausesApproved = () => {
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
              <div className="bg-white rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-900 mb-2">Selected Node</h3>
                <div className="space-y-1 text-xs">
                  <div><span className="text-gray-500">ID:</span> <span className="font-mono">{selectedNode.node_id}</span></div>
                  <div><span className="text-gray-500">Name:</span> {selectedNode.node_name}</div>
                  <div><span className="text-gray-500">Equipment:</span> {selectedNode.equipment.length}</div>
                  <div><span className="text-gray-500">Instruments:</span> {selectedNode.instruments.length}</div>
                  {selectedNode.upstream_pressure_psig != null && (
                    <div><span className="text-gray-500">Upstream P:</span> <span className="font-mono">{selectedNode.upstream_pressure_psig}</span> PSIG</div>
                  )}
                  {selectedNode.validated_by && (
                    <div className="pt-1 border-t border-gray-100">
                      <span className="text-green-600">Validated by {selectedNode.validated_by}</span>
                    </div>
                  )}
                </div>
              </div>
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

// --- Workflow Step Indicator ---

function WorkflowSteps({ currentStep }: { currentStep: WorkflowStep }) {
  const steps: { key: WorkflowStep; label: string }[] = [
    { key: "upload", label: "1. Upload P&ID" },
    { key: "validate", label: "2. Validate Equipment" },
    { key: "select_deviations", label: "3. Select Deviations" },
    { key: "review_causes", label: "4. Review Causes" },
    { key: "generate", label: "5. Generate HAZOP" },
    { key: "review", label: "6. SME Review" },
  ];

  const stepOrder: WorkflowStep[] = ["upload", "validate", "select_deviations", "review_causes", "generate", "review"];
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
